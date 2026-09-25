"""Paper-order workflows shared by the console script and the Telegram bot.

Every function takes `say` (how to show a message) and, for orders, `approve`
(ticket -> Approval | None). The console passes print / request_human_approval;
the bot passes Telegram equivalents. Either way the only way to an order is:
all checks pass -> ticket shown -> human approval -> submit_paper_order
(TrdEnv.SIMULATE, hard-wired in paper_execution.py).
"""
from __future__ import annotations

import json
from typing import Callable

from . import market_calendar as mc
from .config import resolve
from .moomoo_client import MoomooClient, OpenDUnavailable
from .paper_execution import (Approval, OrderTicket, PaperOrderRejected, find_simulate_account,
                              open_trade_context, simulate_cash, submit_paper_order, sync_pending_orders,
                              wait_for_order)
from .risk import PaperLedger, estimated_costs, size_position
from .trade_plan import check_entry_window, entry_limit, exit_status, planned_exit_session

Say = Callable[[str], None]
Approve = Callable[[OrderTicket], "Approval | None"]


def latest_scan() -> dict | None:
    files = sorted(resolve("outputs/scans").glob("scan_*.json"))
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def _exit_word(plan: dict) -> str:
    return "open" if plan.get("exit_at") == "next_open" else "close"


def format_sync(results: list[dict]) -> list[str]:
    out = []
    for r in results:
        b = r.get("booked")
        what = (f"booked {b['qty']:g} @ ${b['price']:,.2f}" if b else
                "not found at broker" if r["status"] == "NOT_FOUND" else "no fill booked")
        out.append(f"order {r['order_id']} {r['side']} {r['code']}: {r['status']} -> {what}")
    return out


def exits_report(ledger: PaperLedger, now=None) -> tuple[list[str], list[str]]:
    """(lines, codes whose exit is DUE_NOW/DUE_TODAY/OVERDUE). Local ledger only, no API."""
    now = now or mc.now_utc()
    pos = ledger.state["positions"]
    if not pos:
        return ["No paper positions."], []
    lines = [f"US/Eastern now: {mc.to_et(now):%Y-%m-%d %H:%M} | market {mc.market_phase(now)}"]
    due = []
    for code, p in sorted(pos.items()):
        plan = p.get("plan")
        st = exit_status(plan, now)
        if st in ("DUE_NOW", "DUE_TODAY", "OVERDUE"):
            due.append(code)
        when = f"sell at the {_exit_word(plan)} of {plan['planned_exit']}" if plan else "no plan recorded"
        lines.append(f"{code} qty {p['qty']:g} @ ${p['avg_price']:,.2f} | {st} | {when}")
    return lines, due


def run_sync(cfg: dict, ledger: PaperLedger, say: Say) -> int:
    if not ledger.pending:
        say("No pending paper orders.")
        return 0
    tctx = open_trade_context(cfg)
    try:
        lines = format_sync(sync_pending_orders(tctx, ledger))
    finally:
        tctx.close()
    say("\n".join(lines + [f"Ledger cash ${ledger.cash:,.2f} | available ${ledger.available_cash:,.2f} | "
                           f"still pending: {len(ledger.pending)}"]))
    return 0


def run_order(cfg: dict, ledger: PaperLedger, code: str, side: str, say: Say, approve: Approve,
              qty: float | None = None, limit: float | None = None, now=None) -> int:
    """Check -> ticket -> approval -> submit ONE paper order. Returns a process-style exit code."""
    code = code.upper()
    code = code if code.startswith("US.") else f"US.{code}"
    side = side.upper()
    ex = cfg["execution"]
    now = now or mc.now_utc()

    cand, plan, scan = None, {}, latest_scan()
    if scan:
        cand = next((c for c in scan["candidates"] if c["ticker"] == code), None)
    if side == "BUY":
        if cand is None:
            say(f"{code} is not in the latest scan. Paper BUYs must come from scanner candidates. NO ORDER.")
            return 1
        if cand["signal_direction"] != "BULLISH":
            say(f"{code} direction is {cand['signal_direction']}. The $300 account is long-only. NO ORDER.")
            return 1
        if ledger.has_exposure(code):
            say(f"{code} is already held or has a pending order. The backtest never adds to a position. NO ORDER.")
            return 1
        chk = check_entry_window(cand, scan, now, ex.get("buy_signals"))
        if not chk.ok:
            say(f"NOT LIKE THE BACKTEST: {chk.reason} NO ORDER.")
            return 1
        h = int(ex.get("hold_sessions") or cfg["backtest"]["primary_horizon"])
        exit_at = cfg["backtest"].get("exit_at", "close")
        plan = {"entry_session": str(chk.entry_session), "horizon": h, "exit_at": exit_at,
                "planned_exit": str(planned_exit_session(chk.entry_session, h, exit_at))}
    try:
        with MoomooClient(cfg) as client:
            snap = client.snapshot([code])
    except OpenDUnavailable as e:
        say(str(e))
        return 2
    row = snap.iloc[0] if len(snap) else {}
    last = float(row["last_price"]) if len(snap) else None
    notes = []
    if side == "BUY":
        price = limit or entry_limit(float(cand["current_price"]), float(ex["entry_limit_cap_pct"]), "BUY")
        pre = row.get("pre_price") if len(snap) else None
        if pre and float(pre) > price:
            notes.append(f"WARNING: pre-market price ${float(pre):,.2f} is above the ${price:,.2f} limit: "
                         "this order will likely NOT fill at the open.")
    else:
        price = limit or (entry_limit(last, float(ex["exit_limit_floor_pct"]), "SELL") if last else None)
    if price is None:
        say("No price available. NO ORDER.")
        return 1

    tctx = open_trade_context(cfg)
    try:
        synced = format_sync(sync_pending_orders(tctx, ledger))
        if synced:
            say("\n".join(["Updated earlier orders first:"] + synced))
        acc = find_simulate_account(tctx)
        broker_cash = simulate_cash(tctx, acc)
        avail = ledger.available_cash
        cash = avail if broker_cash is None else min(avail, broker_cash)
        if side == "BUY":
            size = size_position(price, cash, ledger.equity(), cfg)
            if size.status != "OK":
                say(f"{size.status}: {size.note}. NO ORDER.")
                return 1
            q = qty or size.qty
            remaining = avail - q * price - estimated_costs(q * price, "BUY", cfg)
        else:
            q = qty or ledger.sellable_qty(code)
            if q <= 0:
                say(f"No {code} to sell in the paper ledger. NO ORDER.")
                return 1
            remaining = avail + q * price - estimated_costs(q * price, "SELL", cfg)
            p = ledger.position_plan(code)
            if exit_status(p, now) == "UPCOMING":
                notes.append(f"NOTE: planned exit is the {_exit_word(p)} of {p['planned_exit']}. "
                             "Selling earlier departs from the backtested holding period.")
        buy = side == "BUY"
        ticket = OrderTicket(code=code, side=side, qty=q, limit_price=price,
                             reason=cand.get("reason", "") if buy else "close paper position",
                             signals=tuple(cand.get("signals_triggered", [])) if buy else (),
                             remaining_cash_after=round(remaining, 2),
                             entry_session=plan.get("entry_session", ""),
                             planned_exit=plan.get("planned_exit", ""), horizon=plan.get("horizon", 0),
                             exit_at=plan.get("exit_at", ""))
        say("\n".join(notes + [f"Paper ledger cash ${ledger.cash:,.2f} (available ${avail:,.2f}) | "
                               f"SIMULATE acc {acc}"]))
        approval = approve(ticket)
        if approval is None:
            return 0
        if buy:   # approval may arrive later (e.g. by phone): the timing rule must still hold NOW
            chk = check_entry_window(cand, scan, mc.now_utc(), ex.get("buy_signals"))
            if not chk.ok:
                say(f"Approved too late: {chk.reason} NO ORDER.")
                return 1
        res = submit_paper_order(tctx, ticket, approval, ledger, cfg)
        say(f"PAPER order submitted: {side} {res['qty']:g} {code} "
            f"limit ${res['price']:,.2f} (order {res['order_id']}). Waiting for the broker...")
        s = wait_for_order(tctx, ledger, res["order_id"])
        if res["order_id"] in ledger.pending:
            say(f"Still working (status {(s or {}).get('status', '?')}). It usually fills at the US open. "
                "Check later with sync.")
        elif s:
            say("\n".join(format_sync([s])))
    except PaperOrderRejected as e:
        say(f"REJECTED: {e}")
        return 1
    finally:
        tctx.close()
    return 0
