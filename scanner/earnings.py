"""Earnings events: sourcing, timing classification and reaction-session mapping.

Timing vocabulary: BEFORE (pre-market), DURING (regular hours), AFTER (post-market),
UNKNOWN. The *reaction session* is the first regular session that could reflect
the news (see market_calendar.session_for_event).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

from . import market_calendar as mc
from .config import resolve

log = logging.getLogger(__name__)

TIMINGS = ("BEFORE", "DURING", "AFTER", "UNKNOWN")
_ALIASES = {
    "BEFORE": "BEFORE", "BMO": "BEFORE", "PRE": "BEFORE", "PRE_MARKET": "BEFORE", "PREMARKET": "BEFORE",
    "TIME-PRE-MARKET": "BEFORE",
    "AFTER": "AFTER", "AMC": "AFTER", "POST": "AFTER", "AFTER_MARKET": "AFTER", "AFTERHOURS": "AFTER",
    "TIME-AFTER-HOURS": "AFTER",
    "DURING": "DURING", "DMT": "DURING", "INTRADAY": "DURING",
    # Moomoo's EarningsCalendarPubType_Regular is documented in the SDK proto as
    # "盘中(未识别出时段)" = "time slot NOT identified". It is not a confirmed
    # during-market report, so it must not be mapped to DURING.
    "REGULAR": "UNKNOWN",
}

# Asia/Shanghai: a timestamp at exactly 00:00 here (or 00:00 ET) is treated as a
# date-only placeholder, not a real release time. Live data showed REGULAR rows
# at 00:00 ET (MRK, INTC 2019); the Beijing check is kept as a guard.
_CN = ZoneInfo("Asia/Shanghai")


def normalize_timing(raw) -> str:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return "UNKNOWN"
    s = str(raw).strip().upper()
    # Moomoo enums may stringify as 'EarningsPubType.BEFORE' etc.
    s = s.split(".")[-1]
    return _ALIASES.get(s, "UNKNOWN")


def timing_from_et_time(t: time) -> str | None:
    """Derive timing from an ET clock time; None when time carries no info (00:00)."""
    if t == time(0, 0):
        return None
    if t < mc.REGULAR_OPEN:
        return "BEFORE"
    if t >= mc.REGULAR_CLOSE:
        return "AFTER"
    return "DURING"


@dataclass(frozen=True)
class EarningsEvent:
    code: str
    event_date: date
    timing: str
    source: str
    raw_timing: str = ""

    def reaction_session(self, unknown_policy: str = "exclude") -> date | None:
        timing = self.timing
        if timing == "UNKNOWN" and unknown_policy == "assume_after":
            timing = "AFTER"
        return mc.session_for_event(self.event_date, timing)

    def earliest_entry_session(self, unknown_policy: str = "exclude") -> date | None:
        """Signal is confirmed at the reaction-session CLOSE -> earliest fill is the next session's open."""
        r = self.reaction_session(unknown_policy)
        return mc.next_trading_day(r) if r else None


# ------------------------------------------------------------------ providers
def parse_moomoo_calendar(df: pd.DataFrame) -> list[EarningsEvent]:
    """Convert a Moomoo get_earnings_calendar frame into events.

    Verified against LIVE OpenD output (2026-09-24, windows in 2019, 2025, 2026):
    * earnings_date is the US/Eastern date: it matched the ET date of
      earnings_timestamp on every row checked.
    * pub_type is the reliable timing field. The timestamp is only approximate
      and uses placeholders: BEFORE rows often say exactly 09:30 ET, which would
      misread as DURING; AAPL/MSFT AFTER rows say 17:00.
    * REGULAR ("time slot not identified") timestamps are wrong or empty:
      CVX 11:00 and TXN 15:01 (both actually report outside market hours),
      MRK/INTC 00:00. REGULAR therefore becomes UNKNOWN and the timestamp is
      not used for it.
    The timestamp is used only when pub_type is missing, and only if it carries
    a non-placeholder time. If earnings_date and a real timestamp's ET date
    disagree, earnings_date is kept (it is ET) and the disagreement is logged.
    """
    events = []
    if df is None or df.empty:
        return events
    code_col = "security" if "security" in df.columns else "code"
    date_disagreements = 0
    for _, r in df.iterrows():
        code = str(r.get(code_col, "")).strip()
        if not code:
            continue
        if not code.startswith("US."):
            code = f"US.{code}"
        ed = pd.to_datetime(r.get("earnings_date"), errors="coerce")
        raw = r.get("pub_type")
        raw = "" if raw is None or (isinstance(raw, float) and pd.isna(raw)) else str(raw).strip()
        timing = normalize_timing(raw)
        dt_et = _real_release_time_et(r.get("earnings_timestamp"))
        if dt_et is not None:
            if pd.isna(ed):
                ed = pd.Timestamp(dt_et.date())
            elif ed.date() != dt_et.date():
                date_disagreements += 1
            if raw.split(".")[-1].upper() in ("", "N/A", "UNKNOWN"):   # no pub_type at all: timestamp is all we have
                timing = timing_from_et_time(dt_et.time().replace(second=0, microsecond=0)) or "UNKNOWN"
        if pd.isna(ed):
            continue
        events.append(EarningsEvent(code, ed.date(), timing, "moomoo", raw))
    if date_disagreements:
        log.warning("earnings_calendar: %d rows had earnings_date != ET date of earnings_timestamp; "
                    "kept earnings_date (verified to be US/Eastern)", date_disagreements)
    return events


def _real_release_time_et(ts) -> datetime | None:
    """ET datetime of a timestamp that carries a real clock time, else None."""
    if ts is None or (isinstance(ts, float) and pd.isna(ts)) or str(ts).strip() in ("", "0", "N/A"):
        return None
    try:
        tsf = float(ts)
        if tsf <= 0:
            return None
        dt_utc = datetime.fromtimestamp(tsf, tz=mc.UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    dt_et = dt_utc.astimezone(mc.ET)
    for local in (dt_et, dt_utc.astimezone(_CN)):
        if local.time().replace(second=0, microsecond=0) == time(0, 0):
            return None
    # 09:30 / 16:00 ET exactly are session-boundary placeholders in live data, not release times
    if dt_et.time().replace(second=0, microsecond=0) in (mc.REGULAR_OPEN, mc.REGULAR_CLOSE):
        return None
    return dt_et


def load_csv_events(path: str) -> list[EarningsEvent]:
    """CSV fallback: columns code,date,timing (timing in BEFORE/AFTER/DURING/UNKNOWN or BMO/AMC)."""
    p = resolve(path)
    if not p.exists():
        return []
    df = pd.read_csv(p)
    out = []
    for _, r in df.iterrows():
        code = str(r["code"]).strip()
        code = code if code.startswith("US.") else f"US.{code}"
        out.append(EarningsEvent(code, pd.to_datetime(r["date"]).date(),
                                 normalize_timing(r.get("timing")), "csv", str(r.get("timing", ""))))
    return out


def load_events(cfg: dict, start: date, end: date, codes: set[str] | None = None,
                client=None) -> pd.DataFrame:
    """Merge events from configured providers (first provider wins on duplicates)."""
    ecfg = cfg["earnings"]
    seen: dict[tuple, EarningsEvent] = {}
    for prov in ecfg["providers"]:
        evs: list[EarningsEvent] = []
        try:
            if prov == "moomoo" and client is not None:
                evs = parse_moomoo_calendar(client.earnings_calendar(start, end))
            elif prov == "csv":
                evs = load_csv_events(ecfg["csv_path"])
        except Exception as e:  # provider failure must not be silent
            log.error("earnings provider '%s' failed: %s", prov, e)
            continue
        n = 0
        for ev in evs:
            if not (start <= ev.event_date <= end):
                continue
            if codes is not None and ev.code not in codes:
                continue
            k = (ev.code, ev.event_date)
            if k not in seen:
                seen[k] = ev
                n += 1
        log.info("earnings provider '%s': %d events used", prov, n)
    rows = []
    policy = ecfg.get("unknown_timing_policy", "exclude")
    for ev in seen.values():
        rows.append({"code": ev.code, "event_date": ev.event_date, "timing": ev.timing,
                     "raw_timing": ev.raw_timing, "source": ev.source,
                     "reaction_session": ev.reaction_session(policy)})
    df = pd.DataFrame(rows, columns=["code", "event_date", "timing", "raw_timing", "source", "reaction_session"])
    if len(df):
        counts = df["timing"].value_counts().to_dict()
        log.info("earnings timing mix: %s", counts)
    return df


# --------------------------------------------------------- reaction metrics
def reaction_metrics(bars: pd.DataFrame, reaction_session: date, avg_window: int = 20) -> dict | None:
    """Price/volume reaction on the reaction session using ONLY bars <= that session.

    reaction_move_pct = close[R] / close[R-1] - 1   (prior close -> reaction close)
    gap_pct           = open[R]  / close[R-1] - 1
    avg_volume        = mean(volume over the `avg_window` bars BEFORE R)  (R excluded)
    Returns None if the reaction bar or its predecessor is missing, or the
    previous bar is not the immediately preceding trading day (data gap).
    """
    if reaction_session not in bars.index:
        return None
    i = bars.index.get_loc(reaction_session)
    if i < 1:
        return None
    if bars.index[i - 1] != mc.prev_trading_day(reaction_session):
        log.warning("gap before reaction session %s: previous bar is %s", reaction_session, bars.index[i - 1])
        return None
    hist = bars.iloc[max(0, i - avg_window):i]
    if len(hist) < avg_window:
        return None
    r, p = bars.iloc[i], bars.iloc[i - 1]
    avg_vol = float(hist["volume"].mean())
    return {
        "reaction_session": reaction_session,
        "reaction_move_pct": (r["close"] / p["close"] - 1) * 100,
        "gap_pct": (r["open"] / p["close"] - 1) * 100,
        "reaction_volume": float(r["volume"]),
        "avg_20d_volume": avg_vol,
        "relative_volume": float(r["volume"]) / avg_vol if avg_vol > 0 else float("nan"),
    }
