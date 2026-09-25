"""Technical indicators. Every 'prior'/'average' series EXCLUDES the current bar
(shift(1)) so a bar never references itself: no self-reference or lookahead.
"""
from __future__ import annotations

from datetime import datetime, time

import numpy as np
import pandas as pd


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI. First `period` values are NaN."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    out = 100 - 100 / (1 + rs)
    out = out.where(avg_loss != 0, 100.0)
    out = out.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)
    out.iloc[:period] = np.nan
    return out


def prior_avg_volume(volume: pd.Series, window: int = 20) -> pd.Series:
    """Mean volume of the `window` bars BEFORE each bar (current bar excluded)."""
    return volume.shift(1).rolling(window, min_periods=window).mean()


def relative_volume(volume: pd.Series, window: int = 20) -> pd.Series:
    return volume / prior_avg_volume(volume, window)


def prior_high(high: pd.Series, n: int) -> pd.Series:
    """Highest high of the previous n bars, current bar EXCLUDED."""
    return high.shift(1).rolling(n, min_periods=n).max()


def prior_low(low: pd.Series, n: int) -> pd.Series:
    return low.shift(1).rolling(n, min_periods=n).min()


def consolidation_range(high: pd.Series, low: pd.Series, n: int) -> pd.DataFrame:
    """Range of the previous n bars (current excluded): top, bottom, width %."""
    top = prior_high(high, n)
    bot = prior_low(low, n)
    return pd.DataFrame({"top": top, "bottom": bot, "width_pct": (top / bot - 1) * 100})


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def same_time_relative_volume(intraday: pd.DataFrame, now_et: datetime, min_days: int = 5) -> tuple[float | None, int]:
    """Today's cumulative volume up to now vs. the average cumulative volume at the
    SAME clock time on previous sessions. Regular session only.

    intraday: columns ts_et (tz-aware ET), volume. Returns (rvol, n_prior_days);
    rvol is None if fewer than `min_days` comparable prior sessions exist.
    """
    if intraday is None or intraday.empty:
        return None, 0
    df = intraday.copy()
    df["d"] = df["ts_et"].dt.date
    df["t"] = df["ts_et"].dt.time
    df = df[(df["t"] > time(9, 30)) & (df["t"] <= time(16, 0))]
    cutoff = now_et.time()
    today = now_et.date()
    upto = df[df["t"] <= cutoff]
    by_day = upto.groupby("d")["volume"].sum()
    if today not in by_day.index:
        return None, 0
    prior = by_day.drop(today)
    prior = prior[prior.index < today].tail(20)
    if len(prior) < min_days or prior.mean() <= 0:
        return None, len(prior)
    return float(by_day[today] / prior.mean()), len(prior)


def add_daily_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Attach the indicator columns used by signals. Input: date-indexed OHLCV."""
    s = cfg["signals"]
    out = df.copy()
    vw = s["volume_anomaly"]["avg_volume_window"]
    out["avg_vol_prior"] = prior_avg_volume(out["volume"], vw)
    out["rvol"] = out["volume"] / out["avg_vol_prior"]
    out["rsi"] = rsi(out["close"], s["rsi_period"])
    out["ret_1d_pct"] = out["close"].pct_change() * 100
    out["sma5_prior"] = out["close"].shift(1).rolling(5, min_periods=5).mean()
    out["dollar_vol_avg"] = (out["close"] * out["volume"]).shift(1).rolling(vw, min_periods=vw).mean()
    mb = s["momentum_breakout"]
    lbs = sorted(set(mb["lookbacks"]) | {20, 50})
    for n in lbs:
        out[f"prior_high_{n}"] = prior_high(out["high"], n)
        out[f"prior_low_{n}"] = prior_low(out["low"], n)
    cr = consolidation_range(out["high"], out["low"], mb["consolidation_days"])
    out["cons_top"], out["cons_bottom"], out["cons_width_pct"] = cr["top"], cr["bottom"], cr["width_pct"]
    return out
