"""Abnormal-move event dataset for Strategy 3.

Timing contract (no lookahead)
------------------------------
* Everything describing the event uses bars <= T (the event day). Averages that
  define "normal" (volume, volatility, ATR) use bars <= T-1 only, so the event
  day never inflates its own baseline.
* The event is known at T's close. Forward returns start at the T+1 OPEN:
      forward_h = Close[T+h] / Open[T+1] - 1        (h = 1 -> T+1's close)
  and are NaN when a candle inside the window is missing (never filled).
* Prior-trend features (20d/60d returns, relative strength) end at T-1, so the
  event day's own move is not counted as "prior" strength.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from scanner import market_calendar as mc

EVENT_START, EVENT_END = date(2019, 1, 1), date(2024, 9, 30)
IS_END = date(2022, 12, 31)                   # in-sample 2019-2022, out-of-sample 2023 - Sep 2024
HORIZONS = (1, 2, 3, 5, 10, 20)
MIN_ABS_RETURN, MIN_REL_VOLUME = 0.04, 2.0


def _ordinals(index) -> np.ndarray:
    cal = {d: k for k, d in enumerate(mc.trading_days(index[0], mc.next_trading_day(index[-1])))}
    return np.array([cal[d] for d in index])


def forward_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Per date: entry (next open), exit dates and forward returns for every horizon. NaN across gaps."""
    o, c = df["open"].to_numpy(float), df["close"].to_numpy(float)
    ordn, n, idx = _ordinals(list(df.index)), len(df), list(df.index)
    out = {"entry_date": [None] * n, "entry_open": np.full(n, np.nan)}
    ok_entry = np.zeros(n, bool)
    ok_entry[:-1] = ordn[1:] - ordn[:-1] == 1
    for i in np.flatnonzero(ok_entry):
        out["entry_date"][i] = idx[i + 1]
        out["entry_open"][i] = o[i + 1]
    for h in HORIZONS:
        fwd, ex = np.full(n, np.nan), [None] * n
        for i in np.flatnonzero(ok_entry):
            j = i + h
            if j < n and ordn[j] - ordn[i] == h:
                fwd[i] = c[j] / o[i + 1] - 1
                ex[i] = idx[j]
        out[f"forward_{h}d"], out[f"exit_date_{h}d"] = fwd, ex
    return pd.DataFrame(out, index=df.index)


def spy_features(spy: pd.DataFrame) -> pd.DataFrame:
    c = spy["close"]
    f = pd.DataFrame(index=spy.index)
    f["spy_return"] = c / c.shift(1) - 1
    f["spy_sma200"] = c.rolling(200, min_periods=200).mean()
    f["market_regime"] = np.where(f["spy_sma200"].isna(), None,
                                  np.where(c > f["spy_sma200"], "SPY_ABOVE_SMA200", "SPY_BELOW_SMA200"))
    f["spy_ret_20d_prior"] = c.shift(1) / c.shift(21) - 1
    f["spy_ret_60d_prior"] = c.shift(1) / c.shift(61) - 1
    return f


def stock_features(df: pd.DataFrame, spyf: pd.DataFrame) -> pd.DataFrame:
    """All per-day analysis variables for one stock (adjusted OHLCV, date index)."""
    o, h, l, c, v = (df[k].astype(float) for k in ("open", "high", "low", "close", "volume"))
    pc = c.shift(1)
    f = pd.DataFrame(index=df.index)
    f["open"], f["high"], f["low"], f["close"], f["volume"] = o, h, l, c, v
    f["daily_return"] = c / pc - 1
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    f["atr14_prev"] = tr.rolling(14, min_periods=14).mean().shift(1)
    f["vol20_prev"] = f["daily_return"].rolling(20, min_periods=20).std().shift(1)
    f["avg_volume_prev20"] = v.rolling(20, min_periods=20).mean().shift(1)
    f["relative_volume"] = v / f["avg_volume_prev20"]
    f["normalized_move"] = f["daily_return"].abs() / f["vol20_prev"]
    f["ATR_move"] = (c - pc).abs() / f["atr14_prev"]
    f["gap_return"] = o / pc - 1
    f["intraday_return"] = c / o - 1
    for n in (20, 50, 200):
        f[f"SMA{n}"] = c.rolling(n, min_periods=n).mean()
        f[f"close_to_SMA{n}"] = c / f[f"SMA{n}"]
    f["return_20d_prior"] = pc / c.shift(21) - 1
    f["return_60d_prior"] = pc / c.shift(61) - 1
    s = spyf.reindex(df.index)
    f["spy_return"] = s["spy_return"]
    f["market_adjusted_return"] = f["daily_return"] - f["spy_return"]
    f["relative_strength_20d"] = f["return_20d_prior"] - s["spy_ret_20d_prior"]
    f["relative_strength_60d"] = f["return_60d_prior"] - s["spy_ret_60d_prior"]
    f["market_regime"] = s["market_regime"]
    return f.join(forward_frame(df))


def classify(ev: pd.DataFrame) -> pd.DataFrame:
    """Direction, gap/intraday event type, and trend / strength labels (recorded, not filtered)."""
    ev = ev.copy()
    pos = ev["daily_return"] > 0
    ev["event_direction"] = np.where(pos, "POSITIVE_SHOCK", "NEGATIVE_SHOCK")
    g, i = ev["gap_return"], ev["intraday_return"]
    sgn = np.where(pos, 1.0, -1.0)
    g_dir, i_dir = g * sgn, i * sgn            # both measured in the direction of the shock
    ev["event_type"] = np.select(
        [(g_dir > 0) & (i_dir > 0), (g_dir > 0) & (i_dir < 0), (g_dir <= 0) & (i_dir > 0)],
        [np.where(pos, "GAP_AND_GO", "GAP_DOWN_AND_GO"), np.where(pos, "GAP_AND_FADE", "GAP_DOWN_AND_RECOVER"),
         np.where(pos, "INTRADAY_BREAKOUT", "INTRADAY_SELLOFF")], "MIXED")
    ev["gap_class"] = np.select([(g_dir > 0) & (i_dir > 0), (g_dir > 0) & (i_dir < 0), (g_dir <= 0) & (i_dir > 0)],
                                ["GAP_AND_GO", "GAP_AND_FADE", "INTRADAY_MOVE"], "MIXED")
    ev["trend_SMA50"] = np.where(ev["close"] > ev["SMA50"], "ABOVE_SMA50", "BELOW_SMA50")
    ev["trend_SMA200"] = np.where(ev["SMA200"].isna(), None,
                                  np.where(ev["close"] > ev["SMA200"], "ABOVE_SMA200", "BELOW_SMA200"))
    ev["rs60_sign"] = np.where(ev["relative_strength_60d"] > 0, "POSITIVE_RS60", "NEGATIVE_RS60")
    ev["sample"] = np.where(pd.to_datetime(ev["event_date"]).dt.date <= IS_END, "IN_SAMPLE", "OUT_OF_SAMPLE")
    ev["year"] = pd.to_datetime(ev["event_date"]).dt.year
    return ev


def build_events(prices: dict[str, pd.DataFrame], spy: pd.DataFrame,
                 start: date = EVENT_START, end: date = EVENT_END) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """(events, per-stock feature frames). Events: |return| >= 4% AND relative volume >= 2.0 in [start, end]."""
    spyf = spy_features(spy)
    feats, rows = {}, []
    for code, df in prices.items():
        if len(df) < 70:
            continue
        f = stock_features(df, spyf)
        feats[code] = f
        m = ((f["daily_return"].abs() >= MIN_ABS_RETURN) & (f["relative_volume"] >= MIN_REL_VOLUME)
             & (f.index >= start) & (f.index <= end) & f["entry_open"].notna())
        e = f[m].copy()
        e.insert(0, "event_date", e.index)
        e.insert(0, "ticker", code)
        rows.append(e)
    ev = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    if len(ev):
        ev = classify(ev)
        # SPY over the identical holding window (entry at SPY's T+1 open, exit at SPY's T+h close)
        for h in HORIZONS:
            ev[f"SPY_forward_{h}d"] = [
                (spy.loc[x, "close"] / spy.loc[e_, "open"] - 1) if (e_ in spy.index and x in spy.index) else np.nan
                for e_, x in zip(ev["entry_date"], ev[f"exit_date_{h}d"])]
            ev[f"excess_forward_{h}d"] = ev[f"forward_{h}d"] - ev[f"SPY_forward_{h}d"]
        ev = ev.sort_values(["event_date", "ticker"]).reset_index(drop=True)
    return ev, feats
