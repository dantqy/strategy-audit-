"""Normalisation and quality checks for daily OHLCV bars.

Never fabricates data: missing candles are REPORTED, not filled.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from . import market_calendar as mc

log = logging.getLogger(__name__)

OHLCV = ["open", "high", "low", "close", "volume"]


@dataclass
class QualityReport:
    code: str
    rows_in: int = 0
    duplicates_dropped: int = 0
    non_trading_rows_dropped: int = 0
    bad_rows_dropped: int = 0
    missing_sessions: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing_sessions

    def summary(self) -> str:
        return (f"{self.code}: rows_in={self.rows_in} dup={self.duplicates_dropped} "
                f"non_trading={self.non_trading_rows_dropped} bad={self.bad_rows_dropped} "
                f"missing_sessions={len(self.missing_sessions)}")


def normalize_daily(raw: pd.DataFrame, code: str = "?") -> tuple[pd.DataFrame, QualityReport]:
    """Moomoo kline DataFrame -> date-indexed OHLCV frame (ET session dates).

    Moomoo's `time_key` for US daily bars is 'yyyy-MM-dd HH:mm:ss' in US Eastern;
    the date part is the session date.
    """
    rep = QualityReport(code=code, rows_in=0 if raw is None else len(raw))
    if raw is None or raw.empty:
        return pd.DataFrame(columns=OHLCV), rep
    df = raw.copy()
    if "time_key" in df.columns:
        df["date"] = pd.to_datetime(df["time_key"]).dt.date
    elif "date" not in df.columns:
        raise ValueError("kline frame has neither 'time_key' nor 'date'")
    else:
        df["date"] = pd.to_datetime(df["date"]).dt.date
    missing_cols = [c for c in OHLCV if c not in df.columns]
    if missing_cols:
        raise ValueError(f"{code}: kline missing columns {missing_cols}")
    df = df[["date"] + OHLCV]
    for c in OHLCV:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    before = len(df)
    df = df.drop_duplicates(subset="date", keep="last")
    rep.duplicates_dropped = before - len(df)

    before = len(df)
    df = df[df["date"].map(mc.is_trading_day)]
    rep.non_trading_rows_dropped = before - len(df)

    before = len(df)
    bad = (df[["open", "high", "low", "close"]].isna().any(axis=1)
           | (df["close"] <= 0) | (df["high"] < df["low"]) | df["volume"].isna() | (df["volume"] < 0))
    df = df[~bad]
    rep.bad_rows_dropped = before - len(df)

    df = df.sort_values("date").set_index("date")
    if len(df) >= 2:
        expected = mc.trading_days(df.index[0], df.index[-1])
        have = set(df.index)
        rep.missing_sessions = [d for d in expected if d not in have]
    if rep.duplicates_dropped or rep.non_trading_rows_dropped or rep.bad_rows_dropped or rep.missing_sessions:
        log.warning("Data quality: %s", rep.summary())
    return df, rep


def bar_position(df: pd.DataFrame, d: date) -> int | None:
    """Integer position of session d in df, or None if the bar is missing."""
    try:
        return df.index.get_loc(d)
    except KeyError:
        return None
