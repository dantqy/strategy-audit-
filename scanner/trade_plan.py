"""Paper-trade plan rules that make paper trading match the backtest.

The backtest (backtest/engine.py, backtest/signal_study.py) assumes:
* the signal is confirmed at the CLOSE of bar R (a completed session),
* entry at the OPEN of the next session E,
* exit after `h` sessions: the close of the h-th session starting at E, or
  (backtest.exit_at = next_open) the OPEN of the session after it.

So a paper BUY is only allowed when:
* the candidate came from a scan of COMPLETED bars (not a partial intraday bar),
* no newer session has closed since that scan (the signal is still the latest),
* the entry session E has not opened yet (the order can still fill at E's open).

Entry uses a DAY limit order submitted before the open. Its limit sits `cap_pct`
above the reference close, so it fills at the open unless the stock gaps up by
more than the cap. That keeps a large gap from being chased at any price, and
such a miss is reported, not hidden.

All functions are pure (no API calls) so they are unit-tested directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from . import market_calendar as mc


@dataclass(frozen=True)
class EntryCheck:
    ok: bool
    reason: str
    entry_session: date | None = None


def check_entry_window(cand: dict, scan: dict, now: datetime,
                       buy_signals: tuple | list | None = None) -> EntryCheck:
    """May a paper BUY for this scan candidate be submitted now?

    `buy_signals`: signals allowed to start a BUY (config execution.buy_signals);
    None allows any. An EARNINGS_DRIFT buy additionally needs the reaction session
    to be the scan's bar: the backtest enters at the open right after the reaction,
    while the scanner also lists reactions from the session before.
    """
    if buy_signals is not None:
        allowed = [s for s in cand.get("signals_triggered", []) if s in buy_signals]
        if not allowed:
            return EntryCheck(False, f"signals {cand.get('signals_triggered')} are information only; only "
                                     f"{list(buy_signals)} were backtested with an edge.")
        if allowed == ["EARNINGS_DRIFT"]:     # earnings is the only reason to buy: its timing must match
            if str(cand.get("reaction_session")) != str(cand.get("bar_date")):
                return EntryCheck(False, f"the earnings reaction was on {cand.get('reaction_session')}, not the "
                                         f"latest session {cand.get('bar_date')}: the backtest bought at the open "
                                         "right after the reaction, which has already passed.")
    if cand.get("data_freshness") != "LAST_COMPLETED_BAR":
        return EntryCheck(False, f"candidate data is {cand.get('data_freshness')}, not LAST_COMPLETED_BAR. "
                                 "The backtest confirms signals at the close: re-run the scanner any time between "
                                 "the US close and the next open (Singapore: 04:00-21:30 SGT during US summer time, "
                                 "05:00-22:30 SGT in winter).")
    R = date.fromisoformat(str(cand["bar_date"]))
    if str(R) != str(scan.get("last_completed_session")):
        return EntryCheck(False, f"candidate bar {R} is not the scan's last completed session "
                                 f"{scan.get('last_completed_session')}.")
    last_done = mc.last_completed_session(now)
    if last_done != R:
        return EntryCheck(False, f"scan is stale: signal bar {R}, but session {last_done} has closed since. "
                                 "Re-run the scanner.")
    E = mc.next_trading_day(R)
    open_E, _ = mc.session_bounds(E)
    if mc.to_et(now) >= open_E:
        return EntryCheck(False, f"entry session {E} already opened at {open_E:%H:%M} ET: the backtest's "
                                 "next-open entry is no longer possible for this signal.")
    return EntryCheck(True, f"signal confirmed at close of {R}; order fills at the open of {E}", E)


def entry_limit(ref_price: float, cap_pct: float, side: str) -> float:
    """Marketable limit for an at-the-open fill: BUY up to +cap%, SELL down to -cap%."""
    k = 1 + cap_pct / 100 if side.upper() == "BUY" else 1 - cap_pct / 100
    p = ref_price * k
    return round(p, 2) if p >= 1 else round(p, 4)


def planned_exit_session(entry_session: date, horizon: int, exit_at: str = "close") -> date:
    """Session of the backtest exit: the h-th session starting at entry (its CLOSE), or for
    exit_at='next_open' the session after that (its OPEN)."""
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    d = entry_session
    for _ in range(horizon - 1):
        d = mc.next_trading_day(d)
    return mc.next_trading_day(d) if exit_at == "next_open" else d


def exit_status(plan: dict | None, now: datetime) -> str:
    """Where a held position stands against its planned exit.

    exit_at=close:     UPCOMING -> DUE_TODAY (sell before X's close) -> OVERDUE
    exit_at=next_open: UPCOMING -> DUE_NOW (the day before closed: place the sell
                       before X's open) -> OVERDUE (X already opened)
    """
    if not plan or not plan.get("planned_exit"):
        return "NO_PLAN"
    X = date.fromisoformat(plan["planned_exit"])
    if plan.get("exit_at") == "next_open":
        if mc.to_et(now) >= mc.session_bounds(X)[0]:
            return "OVERDUE"
        return "DUE_NOW" if mc.last_completed_session(now) >= mc.prev_trading_day(X) else "UPCOMING"
    today = mc.to_et(now).date()
    last_done = mc.last_completed_session(now)
    if last_done >= X:
        return "OVERDUE"
    if today == X:
        return "DUE_TODAY"
    return "UPCOMING"
