"""PAPER swing trading on the swing screener's ideas, plus automatic idea tracking.

UNTESTED STRATEGY: this exists so the user can find out, with fake money, whether
they can make the swing ideas pay. Separate $300 ledger (swing.ledger_path).

Flow
----
1. A swing scan's ideas are logged to outputs/swing_ideas.csv (numbered per scan).
2. /sbuy <n|TICKER>: checks -> ticket with entry, stop and time exit -> human
   approves with YES <code> -> SIMULATE order. With swing.auto_exit the approved
   ticket also approves the two exits below.
3. The bot sells automatically (paper) when (a) the price trades at/below the
   stop during US hours, or (b) the time exit is due (at the next open after
   max_hold_sessions). Each auto action is announced on Telegram.
4. Every day after the close, all ideas of the last weeks are price-tracked from
   snapshots (no history quota) -> /sstats compares: all ideas, the ones you took,
   the ones you skipped, and your actual swing-account P&L.
"""
from __future__ import annotations

import copy
import json
import logging
from datetime import datetime, timedelta

import pandas as pd

from . import market_calendar as mc
from .config import resolve
from .moomoo_client import MoomooClient, OpenDUnavailable
from .order_flow import Approve, Say, format_sync
from .paper_execution import (OrderTicket, PaperOrderRejected, find_simulate_account, open_trade_context,
                              preapproved_exit_approval, simulate_cash, submit_paper_order, sync_pending_orders,
                              wait_for_order)
from .risk import PaperLedger, estimated_costs
from .trade_plan import exit_status, planned_exit_session

log = logging.getLogger(__name__)
IDEAS = "outputs/swing_ideas.csv"
TRACK = "outputs/swing_tracking.csv"
IDEA_COLS = ["idea_date", "idea_time_et", "rank", "code", "price", "stop", "move_pct", "rel_vol", "tags", "score",
             "taken"]


# ------------------------------------------------------------------ ledger
def swing_cfg(cfg: dict) -> dict:
    """cfg whose 'account' is the separate $300 swing paper account (one all-in position)."""
    c = copy.deepcopy(cfg)
    sw = c["swing"]
    c["account"].update(starting_cash=float(sw["starting_cash"]), ledger_path=sw["ledger_path"],
                        max_position_pct=1.0)
    return c


def swing_ledger(cfg: dict) -> PaperLedger:
    return PaperLedger(swing_cfg(cfg))


# ------------------------------------------------------------------- ideas
def record_ideas(df: pd.DataFrame, meta: dict) -> pd.DataFrame:
    """Append this scan's ideas (ranked 1..n). One row per (day, code): the first sighting wins."""
    p = resolve(IDEAS)
    old = pd.read_csv(p, dtype={"taken": "Int64"}) if p.exists() else pd.DataFrame(columns=IDEA_COLS)
    if df is None or df.empty:
        return old
    et = datetime.fromisoformat(meta["scan_time"]["us_eastern"])
    def col(name, nd=None):
        v = df[name] if name in df.columns else pd.Series([None] * len(df), index=df.index)
        return pd.to_numeric(v, errors="coerce").round(nd) if nd is not None else v
    new = pd.DataFrame({"idea_date": str(et.date()), "idea_time_et": et.strftime("%H:%M"),
                        "rank": range(1, len(df) + 1), "code": df["code"].values, "price": col("price", 4).values,
                        "stop": col("stop", 4).values, "move_pct": col("move_pct", 2).values,
                        "rel_vol": col("rel_vol", 2).values, "tags": col("tags").fillna("").values,
                        "score": col("score").values, "taken": 0})
    seen = set(zip(old["idea_date"].astype(str), old["code"]))
    new = new[[(d, c) not in seen for d, c in zip(new["idea_date"], new["code"])]]
    out = pd.concat([old, new], ignore_index=True)
    out.to_csv(p, index=False)
    return out


def latest_swing_scan() -> dict | None:
    files = sorted(resolve("outputs/swing").glob("swing_*.json"))
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def _mark_taken(code: str, day: str) -> None:
    p = resolve(IDEAS)
    if not p.exists():
        return
    d = pd.read_csv(p)
    d.loc[(d["code"] == code) & (d["idea_date"].astype(str) == day), "taken"] = 1
    d.to_csv(p, index=False)


# --------------------------------------------------------------------- buy
def resolve_idea(scan: dict, which: str) -> dict | None:
    cands = scan.get("candidates", [])
    w = which.strip().upper()
    if w.isdigit():
        k = int(w) - 1
        return cands[k] if 0 <= k < len(cands) else None
    code = w if w.startswith("US.") else f"US.{w}"
    return next((c for c in cands if c["code"] == code), None)


def run_swing_buy(cfg: dict, which: str, say: Say, approve: Approve, now: datetime | None = None,
                  client_factory=MoomooClient, trade_ctx_factory=open_trade_context) -> int:
    """Checks -> swing ticket -> human approval -> PAPER order. Returns an exit code."""
    sw = cfg["swing"]
    scfg = swing_cfg(cfg)
    ledger = PaperLedger(scfg)
    now = now or mc.now_utc()
    phase = mc.market_phase(now)
    if phase not in ("PRE_MARKET", "OPEN"):
        say(f"US market is {phase}. Swing buys only before the open or during US hours "
            "(SGT: 16:00-04:00 in US summer time). NO ORDER.")
        return 1
    scan = latest_swing_scan()
    if not scan:
        say("No swing scan yet. Send /swing first. NO ORDER.")
        return 1
    age = (now - datetime.fromisoformat(scan["scan_time"]["utc"])).total_seconds() / 60
    if age > sw["idea_max_age_minutes"]:
        say(f"The last swing scan is {age:.0f} min old (limit {sw['idea_max_age_minutes']}). Send /swing again. NO ORDER.")
        return 1
    idea = resolve_idea(scan, which)
    if idea is None:
        say(f"'{which}' is not in the latest swing list. Use its number or ticker from that list. NO ORDER.")
        return 1
    code = idea["code"]
    if ledger.has_exposure(code):
        say(f"{code} is already held or has a pending order in the swing account. NO ORDER.")
        return 1
    try:
        with client_factory(cfg) as client:
            snap = client.snapshot([code])
    except OpenDUnavailable as e:
        say(str(e))
        return 2
    row = snap.iloc[0] if len(snap) else {}
    cur = float(row.get("pre_price") or 0) if phase == "PRE_MARKET" else float(row.get("last_price") or 0)
    if cur <= 0:
        say("No live price. NO ORDER.")
        return 1
    stop = float(idea["stop"])
    if cur <= stop:
        say(f"{code} is already at/below the idea's stop (${cur:,.2f} <= ${stop:,.2f}). NO ORDER.")
        return 1
    if cur > float(idea["price"]) * (1 + sw["max_chase_pct"] / 100):
        say(f"{code} ran from ${float(idea['price']):,.2f} to ${cur:,.2f} (>{sw['max_chase_pct']}% chase limit). NO ORDER.")
        return 1
    limit = round(cur * (1 + sw["entry_limit_pct"] / 100), 2)
    entry = mc.to_et(now).date()
    exit_day = planned_exit_session(entry, int(sw["max_hold_sessions"]), "next_open")

    tctx = trade_ctx_factory(scfg)
    try:
        for line in format_sync(sync_pending_orders(tctx, ledger)):
            say(line)
        acc = find_simulate_account(tctx)
        broker = simulate_cash(tctx, acc)
        avail = ledger.available_cash if broker is None else min(ledger.available_cash, broker)
        c = scfg["costs"]
        fixed = c["commission_per_order"] + c["platform_fee_per_order"]
        qty = int((avail - fixed) // (limit * (1 + c["slippage_bps"] / 1e4)))
        if qty <= 0:
            say(f"Swing account has ${avail:,.2f} available: not enough for 1 share at ${limit:,.2f}. NO ORDER.")
            return 1
        ticket = OrderTicket(
            code=code, side="BUY", qty=qty, limit_price=limit,
            reason=f"SWING idea #{idea.get('rank', '?')}: {float(idea['move_pct']):+.1f}% on "
                   + (f"{float(idea['rel_vol']):.1f}x volume. " if pd.notna(idea.get("rel_vol")) else "pre-market gap. ")
                   + str(idea.get("tags") or ""),
            signals=("SWING_UNTESTED",),
            remaining_cash_after=round(avail - qty * limit - estimated_costs(qty * limit, "BUY", scfg), 2),
            entry_session=str(entry), planned_exit=str(exit_day), horizon=int(sw["max_hold_sessions"]),
            exit_at="next_open", kind="swing", stop_price=round(stop, 2), auto_exit=bool(sw.get("auto_exit", True)))
        say(f"Swing account: cash ${ledger.cash:,.2f}, available ${avail:,.2f}. Risk if stopped: about "
            f"${qty * (limit - stop):,.0f} + fees.")
        approval = approve(ticket)
        if approval is None:
            return 0
        res = submit_paper_order(tctx, ticket, approval, ledger, scfg)
        _mark_taken(code, str(scan["scan_time"]["us_eastern"])[:10])
        say(f"PAPER swing BUY sent: {qty} {code} limit ${limit:,.2f} (order {res['order_id']}).")
        s = wait_for_order(tctx, ledger, res["order_id"])
        say("Still working; it usually fills within seconds in US hours, or at the open."
            if res["order_id"] in ledger.pending else "\n".join(format_sync([s] if s else [])))
    except PaperOrderRejected as e:
        say(f"REJECTED: {e}")
        return 1
    finally:
        tctx.close()
    return 0


# -------------------------------------------------------------- auto exits
def decide_exits(ledger: PaperLedger, snap_by_code: dict, now: datetime) -> list[dict]:
    """Which swing positions must be sold NOW, and why. Pure (no API)."""
    phase = mc.market_phase(now)
    out = []
    pending_sells = {o["code"] for o in ledger.pending.values() if o["side"] == "SELL"}
    for code, p in ledger.state["positions"].items():
        plan = p.get("plan") or {}
        if plan.get("kind") != "swing" or not plan.get("auto_exit") or code in pending_sells:
            continue
        row = snap_by_code.get(code) or {}
        last = float(row.get("last_price") or 0)
        if phase == "OPEN" and 0 < last <= float(plan.get("stop_price") or 0):
            out.append({"code": code, "reason": f"stop ${plan['stop_price']:,.2f} hit (last ${last:,.2f})",
                        "ref": last, "phase": phase})
            continue
        st = exit_status(plan, now)
        if phase in ("PRE_MARKET", "OPEN") and st in ("DUE_NOW", "OVERDUE"):
            ref = float(row.get("pre_price") or 0) if phase == "PRE_MARKET" else last
            ref = ref or float(row.get("prev_close_price") or 0) or float(p["avg_price"])
            out.append({"code": code, "reason": f"time exit ({plan['horizon']} sessions held)",
                        "ref": ref, "phase": phase})
    return out


def run_auto_exits(cfg: dict, say: Say, now: datetime | None = None, client_factory=MoomooClient,
                   trade_ctx_factory=open_trade_context) -> int:
    """Check swing positions and place pre-approved exit SELLs. Returns the number of sells sent."""
    scfg = swing_cfg(cfg)
    ledger = PaperLedger(scfg)
    if ledger.pending:
        # A BUY that filled after /sbuy stopped waiting is still 'pending' here. Book it first,
        # otherwise its stop would not be watched until the post-close sync.
        tctx = trade_ctx_factory(scfg)
        try:
            for line in format_sync(sync_pending_orders(tctx, ledger)):
                if "booked" in line:
                    say("Fill confirmed: " + line)
        finally:
            tctx.close()
    held = [c for c, p in ledger.state["positions"].items() if (p.get("plan") or {}).get("kind") == "swing"]
    if not held:
        return 0
    now = now or mc.now_utc()
    with client_factory(cfg) as client:
        snap = client.snapshot(held)
    by = {r["code"]: r.to_dict() for _, r in snap.iterrows()} if len(snap) else {}
    todo = decide_exits(ledger, by, now)
    if not todo:
        return 0
    sent = 0
    tctx = trade_ctx_factory(scfg)
    try:
        sync_pending_orders(tctx, ledger)
        for x in todo:
            qty = ledger.sellable_qty(x["code"])
            if qty <= 0:
                continue
            floor = 0.98 if x["phase"] == "OPEN" else 1 - cfg["execution"]["exit_limit_floor_pct"] / 100
            t = OrderTicket(code=x["code"], side="SELL", qty=qty, limit_price=round(x["ref"] * floor, 2),
                            reason=f"AUTO EXIT (pre-approved): {x['reason']}", signals=(),
                            remaining_cash_after=round(ledger.available_cash + qty * x["ref"], 2))
            try:
                res = submit_paper_order(tctx, t, preapproved_exit_approval(t, ledger), ledger, scfg)
                sent += 1
                say(f"AUTO SELL (paper) {x['code']} x{qty:g}: {x['reason']}. Limit ${t.limit_price:,.2f}, "
                    f"order {res['order_id']}.")
                wait_for_order(tctx, ledger, res["order_id"], timeout_s=10)
            except PaperOrderRejected as e:
                say(f"Auto exit for {x['code']} FAILED: {e}. Sell it yourself with /ssell {x['code'][3:]}.")
    finally:
        tctx.close()
    return sent


# ---------------------------------------------------------------- tracking
def update_tracking(client, now: datetime | None = None, days_back: int = 45) -> int:
    """After the close: record today's OHLC (from snapshots) for every recent idea. Returns rows added."""
    now = now or mc.now_utc()
    p = resolve(IDEAS)
    if not p.exists():
        return 0
    ideas = pd.read_csv(p)
    last_done = mc.last_completed_session(now)
    recent = ideas[pd.to_datetime(ideas["idea_date"]).dt.date >= last_done - timedelta(days=days_back)]
    codes = sorted(set(recent["code"]))
    if not codes:
        return 0
    tp = resolve(TRACK)
    track = pd.read_csv(tp) if tp.exists() else pd.DataFrame(columns=["date", "code", "open", "high", "low", "close"])
    have = set(zip(track["date"].astype(str), track["code"]))
    snap = client.snapshot(codes)
    rows = []
    for _, r in snap.iterrows():
        if (str(last_done), r["code"]) in have:
            continue
        ut = pd.to_datetime(r.get("update_time"), errors="coerce")
        if pd.isna(ut) or ut.date() != last_done:      # stale quote (halted etc.): don't invent a bar
            continue
        rows.append({"date": str(last_done), "code": r["code"], "open": r["open_price"], "high": r["high_price"],
                     "low": r["low_price"], "close": r["last_price"]})
    if rows:
        pd.concat([track, pd.DataFrame(rows)], ignore_index=True).to_csv(tp, index=False)
    return len(rows)


def idea_outcomes(ideas: pd.DataFrame, track: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Follow each idea's plan on tracked daily bars: stop (or gap below it) vs exit at the max-hold close.

    Entry is assumed at the idea price (real fills differ). Bars strictly after the
    idea day are used, so the idea day's earlier low cannot fake a stop-out.
    """
    sw = cfg["swing"]
    h = int(sw["max_hold_sessions"])
    fee_pct = 2 * float(cfg["costs"]["platform_fee_per_order"]) / float(sw["starting_cash"])
    out = []
    if ideas.empty:
        return pd.DataFrame()
    t = track.copy()
    t["date"] = t["date"].astype(str)
    for _, i in ideas.iterrows():
        bars = t[(t["code"] == i["code"]) & (t["date"] > str(i["idea_date"]))].sort_values("date").head(h)
        res, how = None, "open"
        for _, b in bars.iterrows():
            if b["low"] <= i["stop"]:
                px = min(b["open"], i["stop"])            # gap below the stop fills at the open
                res, how = px / i["price"] - 1, "stopped"
                break
        if res is None and len(bars) >= h:
            res, how = bars.iloc[-1]["close"] / i["price"] - 1, "held to time exit"
        out.append({"idea_date": i["idea_date"], "code": i["code"], "taken": int(i.get("taken") or 0),
                    "status": how, "ret_net": None if res is None else res - fee_pct, "days_tracked": len(bars)})
    return pd.DataFrame(out)


def stats_text(cfg: dict) -> str:
    p, tp = resolve(IDEAS), resolve(TRACK)
    if not p.exists():
        return "No swing ideas recorded yet. They are recorded every time a swing scan runs."
    ideas = pd.read_csv(p)
    track = pd.read_csv(tp) if tp.exists() else pd.DataFrame(columns=["date", "code", "open", "high", "low", "close"])
    oc = idea_outcomes(ideas, track, cfg)
    done = oc[oc["ret_net"].notna()] if len(oc) else oc
    lines = [f"SWING SCORECARD (paper; plan = idea price, stop, max {cfg['swing']['max_hold_sessions']} sessions; "
             "net of fees)", f"Ideas recorded: {len(ideas)} | finished: {len(done)} | still running: {len(oc) - len(done)}"]

    def grp(label, g):
        if g.empty:
            return f"{label}: none finished yet"
        return (f"{label}: n={len(g)}, avg {g['ret_net'].mean() * 100:+.1f}%, win {(g['ret_net'] > 0).mean() * 100:.0f}%, "
                f"stopped {(g['status'] == 'stopped').mean() * 100:.0f}%")
    lines += [grp("All ideas", done), grp("You took", done[done["taken"] == 1]),
              grp("You skipped", done[done["taken"] != 1])]
    led = swing_ledger(cfg)
    start = float(cfg["swing"]["starting_cash"])
    pos = ", ".join(f"{c} x{p['qty']:g} @ ${p['avg_price']:,.2f}" for c, p in led.state["positions"].items()) or "none"
    lines += [f"Your swing paper account: cash ${led.cash:,.2f} | positions: {pos} | "
              f"at cost ${led.equity():,.2f} vs start ${start:,.0f} ({(led.equity() / start - 1) * 100:+.1f}%)"]
    if len(done) < 20:
        lines.append("Under 20 finished ideas: too few to judge. Keep going.")
    return "\n".join(lines)
