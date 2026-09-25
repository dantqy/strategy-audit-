"""SCAN -> RANK -> DISPLAY. Never places orders.

Data freshness labels
---------------------
LIVE_INTRADAY       market open, snapshot quote updated within N minutes; today's
                    partial bar is built from the snapshot.
LAST_COMPLETED_BAR  market not in regular session; newest daily bar is the most
                    recent completed session.
LAST_CLOSE          market open but no fresh quote: falling back to last close (penalised).
STALE               newest bar is older than the last completed session (penalised).
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from . import market_calendar as mc
from .config import resolve
from .earnings import load_events, reaction_metrics
from .indicators import add_daily_indicators
from .ranking import rank
from .signals import (EARNINGS_DRIFT, MOMENTUM_BREAKOUT, VOLUME_ANOMALY, combine_direction,
                      earnings_drift, momentum_breakout, volume_anomaly)
from .universe import liquidity_ok, load_universe

log = logging.getLogger(__name__)


def _num(x):
    try:
        v = float(x)
        return v if np.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def live_bar_from_snapshot(snap_row: pd.Series, now: datetime, max_age_min: float) -> dict | None:
    """Build today's partial bar from a snapshot row if its quote is fresh."""
    ts = snap_row.get("update_time_et")
    if ts is None or pd.isna(ts):
        return None
    age_min = (now - ts.to_pydatetime()).total_seconds() / 60
    if age_min > max_age_min or ts.date() != mc.to_et(now).date():
        return None
    vals = {k: _num(snap_row.get(s)) for k, s in
            (("open", "open_price"), ("high", "high_price"), ("low", "low_price"),
             ("close", "last_price"), ("volume", "volume"))}
    if any(v is None or v <= 0 for k, v in vals.items() if k != "volume") or vals["volume"] is None:
        return None
    vals["quote_age_min"] = round(age_min, 1)
    return vals


def evaluate_symbol(code: str, bars: pd.DataFrame, cfg: dict, now: datetime,
                    events: pd.DataFrame, snap_row: pd.Series | None = None,
                    intraday_rvol_fn=None) -> dict | None:
    """Evaluate one symbol. Pure given its inputs (unit-testable)."""
    phase = mc.market_phase(now)
    last_done = mc.last_completed_session(now)
    today = mc.to_et(now).date()
    completed = bars.loc[:last_done]
    if completed.empty:
        return None

    freshness, live = None, None
    if phase == "OPEN":
        live = live_bar_from_snapshot(snap_row, now, cfg["freshness"]["live_quote_max_age_minutes"]) \
            if snap_row is not None else None
        freshness = "LIVE_INTRADAY" if live else "LAST_CLOSE"
    else:
        freshness = "LAST_COMPLETED_BAR" if completed.index[-1] == last_done else "STALE"

    df = completed.copy()
    if live:
        df.loc[today, ["open", "high", "low", "close", "volume"]] = [live[k] for k in ("open", "high", "low", "close", "volume")]
    ind = add_daily_indicators(df, cfg)
    i = len(ind) - 1
    row = ind.iloc[i]

    # --- relative volume for the current bar -------------------------------
    rvol, rvol_method = _num(row["rvol"]), "daily_vs_prior_20d"
    if live:
        rvol, rvol_method = None, None
        if intraday_rvol_fn is not None:
            rv, n = intraday_rvol_fn(code)
            if rv is not None:
                rvol, rvol_method = rv, f"same_time_of_day_vs_{n}d"
        if rvol is None and snap_row is not None and _num(snap_row.get("volume_ratio")):
            rvol, rvol_method = _num(snap_row.get("volume_ratio")), "moomoo_volume_ratio_time_normalized_5d"
        if rvol is None:
            frac = mc.session_elapsed_fraction(now) or 1.0
            avg = _num(row["avg_vol_prior"])
            rvol = live["volume"] / (avg * frac) if avg and frac > 0 else None
            rvol_method = "APPROX_elapsed_fraction_of_20d_avg"

    # --- earnings ------------------------------------------------------------
    n_back = cfg["earnings"]["lookback_sessions"]
    window, d = [], last_done
    for _ in range(n_back):
        window.append(d)
        d = mc.prev_trading_day(d)
    if live:
        window.insert(0, today)
    ev = events[(events["code"] == code) & events["reaction_session"].isin(window)] if len(events) else events
    e_res, e_info = None, {}
    if len(ev):
        e = ev.sort_values("reaction_session").iloc[-1]
        R = e["reaction_session"]
        if live and R == today:
            # full-day volume comparison would be invalid intraday: use the
            # time-normalised rvol computed above instead.
            base = reaction_metrics(df, R) or {}
            if base:
                base["relative_volume"] = rvol
                base["rvol_method"] = rvol_method
            m = base or None
        else:
            m = reaction_metrics(completed, R)
            if m:
                m["rvol_method"] = "reaction_day_vs_prior_20d"
        e_res = earnings_drift(m, cfg)
        e_info = {"earnings_date": str(e["event_date"]), "earnings_timing": e["timing"],
                  "reaction_session": str(R), **({k: m[k] for k in ("reaction_move_pct", "gap_pct")} if m else {}),
                  "reaction_relative_volume": m["relative_volume"] if m else None}

    v_res = volume_anomaly(ind, i, cfg, rvol=rvol, rvol_method=rvol_method)
    b_res = momentum_breakout(ind, i, cfg, rvol=rvol)
    results = [r for r in (e_res, v_res, b_res) if r is not None]
    trig = [r for r in results if r.triggered]
    if not trig:
        return None

    names = [r.name for r in trig]
    direction = combine_direction(trig)
    liq = liquidity_ok(_num(row["close"]), _num(row["dollar_vol_avg"]), cfg)
    name = str(snap_row.get("name")) if snap_row is not None and "name" in snap_row else ""
    reason_bits = []
    if EARNINGS_DRIFT in names:
        reason_bits.append(f"{e_info.get('earnings_timing')} earnings on {e_info.get('earnings_date')}: "
                           f"{e_info.get('reaction_move_pct', 0):+.1f}% on {e_info.get('reaction_relative_volume') or 0:.1f}x volume "
                           f"(reaction session {e_info.get('reaction_session')})")
    if VOLUME_ANOMALY in names:
        reason_bits.append(f"abnormal volume {rvol:.1f}x with {v_res.direction.lower()} price confirmation")
    if MOMENTUM_BREAKOUT in names:
        reason_bits.append(f"broke {b_res.detail.get('breakout_type')} on {rvol:.1f}x volume")
    return {
        "ticker": code,
        "company": name,
        "current_price": _num(row["close"]),
        "signal_direction": direction,
        "signals_triggered": names,
        "earnings_date": e_info.get("earnings_date"),
        "earnings_timing": e_info.get("earnings_timing"),
        "reaction_session": e_info.get("reaction_session"),
        "reaction_move_pct": None if e_info.get("reaction_move_pct") is None else round(e_info["reaction_move_pct"], 2),
        "relative_volume": None if rvol is None else round(rvol, 2),
        "rvol_method": rvol_method or "",
        "breakout_type": b_res.detail.get("breakout_type") if b_res.triggered else None,
        "rsi_14": None if _num(row["rsi"]) is None else round(float(row["rsi"]), 1),
        "data_freshness": freshness,
        "bar_date": str(ind.index[i]),
        "liquidity_ok": liq,
        "reason": "; ".join(reason_bits),
    }


def run_scan(cfg: dict, client, now: datetime | None = None) -> dict:
    now = now or mc.now_utc()
    codes = load_universe(cfg)
    last_done = mc.last_completed_session(now)
    phase = mc.market_phase(now)
    start = last_done - timedelta(days=int(cfg["history"]["lookback_days_scanner"]))

    snap = client.snapshot(codes)
    snap_by = {r["code"]: r for _, r in snap.iterrows()} if len(snap) else {}
    ev_start = last_done - timedelta(days=10)
    events = load_events(cfg, ev_start, mc.to_et(now).date(), set(codes), client=client)

    def intraday_fn(code):
        from .indicators import same_time_relative_volume
        try:
            return same_time_relative_volume(client.intraday_bars(code, days=10), mc.to_et(now))
        except Exception as e:
            log.warning("intraday rvol failed for %s: %s", code, e)
            return None, 0

    cands, errors = [], []
    for code in codes:
        try:
            bars = client.daily_bars(code, start, last_done)
            if bars.empty:
                errors.append(f"{code}: no daily bars")
                continue
            snap_row = snap_by.get(code)
            # same-time-of-day intraday rvol costs requests: only for plausible movers
            use_intraday = None
            if phase == "OPEN" and snap_row is not None:
                chg = abs((_num(snap_row.get("last_price")) or 0) / (_num(snap_row.get("prev_close_price")) or 1) - 1)
                if chg >= 0.03:
                    use_intraday = intraday_fn
            c = evaluate_symbol(code, bars, cfg, now, events, snap_row, use_intraday)
            if c:
                cands.append(c)
        except Exception as e:
            log.exception("scan failed for %s", code)
            errors.append(f"{code}: {type(e).__name__}: {e}")
    ranked = rank(cands, cfg)
    return {
        "scan_time": mc.timestamps(now), "market_phase": phase, "last_completed_session": str(last_done),
        "universe_size": len(codes), "earnings_events_in_window": int(len(events)),
        "cache_hits": client.cache_hits, "api_fetches": client.api_fetches,
        "errors": errors, "candidates": ranked,
    }


def format_terminal(result: dict) -> str:
    ts = result["scan_time"]
    lines = ["=" * 59, "MOOMOO PAPER SCANNER  (research only - no orders placed)",
             f"{ts['us_eastern']} ET | {ts['utc']} UTC | local {ts['local_machine']}",
             f"Market phase: {result['market_phase']} | last completed session: {result['last_completed_session']}",
             f"Universe: {result['universe_size']} | earnings events in window: {result['earnings_events_in_window']}",
             "=" * 59]
    if not result["candidates"]:
        lines.append("No candidates met any signal criteria.")
    labels = {EARNINGS_DRIFT: "Earnings Drift", VOLUME_ANOMALY: "Volume Anomaly", MOMENTUM_BREAKOUT: "Momentum Breakout"}
    for n, c in enumerate(result["candidates"], 1):
        lines += ["", f"{n}. {c['ticker']}  {c['company']}".rstrip(),
                  f"   Score: {c['score']}   ({', '.join(f'{l} {p:+d}' for l, p in c['score_breakdown'])})",
                  f"   Direction: {c['signal_direction']}", "", "   Signals:"]
        lines += [f"   ✓ {labels[s]}" for s in c["signals_triggered"]]
        lines.append("")
        if c["reaction_move_pct"] is not None:
            lines.append(f"   Earnings reaction: {c['reaction_move_pct']:+.1f}%  ({c['earnings_timing']} {c['earnings_date']}, "
                         f"reaction session {c['reaction_session']})")
        if c["relative_volume"] is not None:
            lines.append(f"   Relative volume: {c['relative_volume']:.1f}x  [{c['rvol_method']}]")
        if c["rsi_14"] is not None:
            lines.append(f"   RSI(14): {c['rsi_14']:.1f}")
        if c["breakout_type"]:
            lines.append(f"   Breakout: {c['breakout_type']}")
        lines += [f"   Price: ${c['current_price']:,.2f}  (bar {c['bar_date']})",
                  f"   Data status: {c['data_freshness']}", "   Reason:", f"   {c['reason']}"]
    if result["errors"]:
        lines += ["", f"Errors ({len(result['errors'])}):"] + [f"  - {e}" for e in result["errors"][:20]]
    return "\n".join(lines)


def save_scan(result: dict, out_dir: str = "outputs/scans") -> tuple[str, str]:
    d = resolve(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    stamp = result["scan_time"]["us_eastern"][:19].replace(":", "").replace("-", "")
    base = d / f"scan_{stamp}ET"
    with open(f"{base}.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str)
    rows = [{**{k: v for k, v in c.items() if k != "score_breakdown"},
             "signals_triggered": "|".join(c["signals_triggered"]),
             "score_breakdown": "; ".join(f"{l} {p:+d}" for l, p in c["score_breakdown"]),
             "scan_time_et": result["scan_time"]["us_eastern"], "scan_time_utc": result["scan_time"]["utc"]}
            for c in result["candidates"]]
    pd.DataFrame(rows).to_csv(f"{base}.csv", index=False)
    return f"{base}.json", f"{base}.csv"
