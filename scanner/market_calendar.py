"""US equity (NYSE/Nasdaq) trading calendar and timezone helpers.

Self-contained, rule-based: no third-party calendar dependency. All "is this a
trading session?" decisions are made in US/Eastern using this calendar, never
by the local machine's (e.g. Singapore) calendar date.

Covers standard NYSE holidays plus known one-off closures since 2004. Early
closes (13:00 ET) are modelled for intraday elapsed-time calculations.
Extend SPECIAL_CLOSURES if the exchange announces a new one-off closure.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = timezone.utc

REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)

SPECIAL_CLOSURES = {
    date(2004, 6, 11),   # Reagan national day of mourning
    date(2007, 1, 2),    # Ford national day of mourning
    date(2012, 10, 29),  # Hurricane Sandy
    date(2012, 10, 30),  # Hurricane Sandy
    date(2018, 12, 5),   # G.H.W. Bush national day of mourning
    date(2025, 1, 9),    # Carter national day of mourning
}


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date, saturday_to_friday: bool = True) -> date | None:
    if d.weekday() == 5:
        return d - timedelta(days=1) if saturday_to_friday else None
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=None)
def holidays(year: int) -> frozenset:
    h = set()
    # New Year's Day: NYSE does not close the preceding Friday if Jan 1 is a Saturday.
    ny = _observed(date(year, 1, 1), saturday_to_friday=False)
    if ny:
        h.add(ny)
    h.add(_nth_weekday(year, 1, 0, 3))    # MLK Day
    h.add(_nth_weekday(year, 2, 0, 3))    # Washington's Birthday
    h.add(_easter(year) - timedelta(days=2))  # Good Friday
    h.add(_last_weekday(year, 5, 0))      # Memorial Day
    if year >= 2022:
        h.add(_observed(date(year, 6, 19)))  # Juneteenth
    h.add(_observed(date(year, 7, 4)))    # Independence Day
    h.add(_nth_weekday(year, 9, 0, 1))    # Labor Day
    h.add(_nth_weekday(year, 11, 3, 4))   # Thanksgiving
    h.add(_observed(date(year, 12, 25)))  # Christmas
    h |= {d for d in SPECIAL_CLOSURES if d.year == year}
    return frozenset(h)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


def next_trading_day(d: date) -> date:
    n = d + timedelta(days=1)
    while not is_trading_day(n):
        n += timedelta(days=1)
    return n


def prev_trading_day(d: date) -> date:
    p = d - timedelta(days=1)
    while not is_trading_day(p):
        p -= timedelta(days=1)
    return p


def trading_days(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def is_early_close(d: date) -> bool:
    if not is_trading_day(d):
        return False
    thanksgiving = _nth_weekday(d.year, 11, 3, 4)
    if d == thanksgiving + timedelta(days=1):
        return True
    if d.month == 12 and d.day == 24:
        return True
    if d.month == 7 and d.day == 3:
        return True
    return False


def session_bounds(d: date) -> tuple[datetime, datetime]:
    """Regular-session open/close for trading day d, as tz-aware ET datetimes."""
    if not is_trading_day(d):
        raise ValueError(f"{d} is not a US trading day")
    close_t = EARLY_CLOSE if is_early_close(d) else REGULAR_CLOSE
    return (datetime.combine(d, REGULAR_OPEN, ET), datetime.combine(d, close_t, ET))


def now_utc() -> datetime:
    return datetime.now(UTC)


def to_et(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime: refuse to guess its timezone")
    return dt.astimezone(ET)


def market_phase(now: datetime) -> str:
    """PRE_MARKET / OPEN / POST_MARKET / CLOSED (non-trading day), judged in ET."""
    et = to_et(now)
    d = et.date()
    if not is_trading_day(d):
        return "CLOSED"
    o, c = session_bounds(d)
    if et < o:
        return "PRE_MARKET"
    if et < c:
        return "OPEN"
    return "POST_MARKET"


def last_completed_session(now: datetime) -> date:
    """Most recent regular session whose close is at or before `now`."""
    et = to_et(now)
    d = et.date()
    if is_trading_day(d) and et >= session_bounds(d)[1]:
        return d
    return prev_trading_day(d)


def session_elapsed_fraction(now: datetime) -> float | None:
    """Fraction of today's regular session elapsed, or None if market not open."""
    et = to_et(now)
    if market_phase(now) != "OPEN":
        return None
    o, c = session_bounds(et.date())
    return max(0.0, min(1.0, (et - o) / (c - o)))


def session_for_event(event_date: date, timing: str) -> date | None:
    """Map an earnings announcement to the regular session that first reflects it.

    BEFORE (pre-market) on a trading day      -> same day
    DURING (regular hours) on a trading day   -> same day (partially pre-info!)
    AFTER  (after close) on a trading day     -> next trading day
    Any timing on a weekend/holiday           -> next trading day
    UNKNOWN                                   -> None (caller decides policy)
    """
    timing = (timing or "UNKNOWN").upper()
    if not is_trading_day(event_date):
        return next_trading_day(event_date) if timing != "UNKNOWN" else None
    if timing in ("BEFORE", "DURING"):
        return event_date
    if timing == "AFTER":
        return next_trading_day(event_date)
    return None


def timestamps(now: datetime | None = None) -> dict:
    """The three clocks, unambiguous ISO-8601 with offsets."""
    now = now or now_utc()
    return {
        "utc": now.astimezone(UTC).isoformat(timespec="seconds"),
        "us_eastern": now.astimezone(ET).isoformat(timespec="seconds"),
        "local_machine": now.astimezone().isoformat(timespec="seconds"),
    }
