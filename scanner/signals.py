"""Signal definitions. Each takes an indicator-enriched daily frame (see
indicators.add_daily_indicators) and a bar position `i`, using ONLY rows <= i.

Signals are scanner CLASSIFICATIONS, not permission to trade either direction.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

EARNINGS_DRIFT = "EARNINGS_DRIFT"
VOLUME_ANOMALY = "VOLUME_ANOMALY"
MOMENTUM_BREAKOUT = "MOMENTUM_BREAKOUT"
BULLISH, BEARISH, NEUTRAL, MIXED = "BULLISH", "BEARISH", "NEUTRAL", "MIXED"


@dataclass
class SignalResult:
    name: str
    triggered: bool
    direction: str = NEUTRAL
    detail: dict = field(default_factory=dict)


def _finite(x) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x)) and not pd.isna(x)


def earnings_drift(metrics: dict | None, cfg: dict, min_move: float | None = None,
                   min_rvol: float | None = None) -> SignalResult:
    p = cfg["signals"]["earnings_drift"]
    min_move = p["min_abs_move_pct"] if min_move is None else min_move
    min_rvol = p["min_relative_volume"] if min_rvol is None else min_rvol
    if not metrics or not _finite(metrics.get("relative_volume")):
        return SignalResult(EARNINGS_DRIFT, False, detail={"reason": "no reaction metrics"})
    mv, rv = metrics["reaction_move_pct"], metrics["relative_volume"]
    ok = abs(mv) >= min_move and rv >= min_rvol
    return SignalResult(EARNINGS_DRIFT, ok, BULLISH if mv > 0 else BEARISH, dict(metrics))


def directional_confirmation(row: pd.Series) -> str:
    """Directional price behaviour on the bar (volume alone is not enough).

    BULLISH: positive return, close in upper half of bar range, close above prior 5-day SMA.
    BEARISH: the mirror image. Otherwise NEUTRAL.
    """
    rng_mid = (row["high"] + row["low"]) / 2
    ret, sma5 = row.get("ret_1d_pct"), row.get("sma5_prior")
    if not (_finite(ret) and _finite(sma5)):
        return NEUTRAL
    if ret > 0 and row["close"] >= rng_mid and row["close"] > sma5:
        return BULLISH
    if ret < 0 and row["close"] <= rng_mid and row["close"] < sma5:
        return BEARISH
    return NEUTRAL


def volume_anomaly(df: pd.DataFrame, i: int, cfg: dict, rvol: float | None = None,
                   rvol_method: str = "daily_vs_prior_20d") -> SignalResult:
    p = cfg["signals"]["volume_anomaly"]
    row = df.iloc[i]
    rv = row["rvol"] if rvol is None else rvol
    if not _finite(rv):
        return SignalResult(VOLUME_ANOMALY, False, detail={"reason": "rvol unavailable"})
    direction = directional_confirmation(row)
    ok = rv >= p["min_relative_volume"] and direction != NEUTRAL
    return SignalResult(VOLUME_ANOMALY, ok, direction,
                        {"relative_volume": float(rv), "rvol_method": rvol_method, "confirmation": direction})


def momentum_breakout(df: pd.DataFrame, i: int, cfg: dict, rvol: float | None = None,
                      lookbacks: list[int] | None = None) -> SignalResult:
    p = cfg["signals"]["momentum_breakout"]
    lookbacks = lookbacks or p["lookbacks"]
    row = df.iloc[i]
    rv = row["rvol"] if rvol is None else rvol
    price, r = row["close"], row.get("rsi")
    detail = {"relative_volume": None if not _finite(rv) else float(rv), "rsi_14": None if not _finite(r) else float(r)}
    if not _finite(rv) or rv < p["min_relative_volume"]:
        return SignalResult(MOMENTUM_BREAKOUT, False, detail={**detail, "reason": "no volume confirmation"})

    up = [n for n in lookbacks if _finite(row.get(f"prior_high_{n}")) and price > row[f"prior_high_{n}"]]
    dn = [n for n in lookbacks if _finite(row.get(f"prior_low_{n}")) and price < row[f"prior_low_{n}"]]
    cons_ok = _finite(row.get("cons_width_pct")) and row["cons_width_pct"] <= p["consolidation_max_range_pct"]

    if up or (cons_ok and price > row["cons_top"]):
        if _finite(r) and r >= p["rsi_max_bullish"]:
            return SignalResult(MOMENTUM_BREAKOUT, False, BULLISH, {**detail, "reason": f"RSI {r:.1f} >= filter"})
        btype = f"{max(up)}-day high" if up else f"{p['consolidation_days']}-day consolidation"
        return SignalResult(MOMENTUM_BREAKOUT, True, BULLISH, {**detail, "breakout_type": btype})
    if dn or (cons_ok and price < row["cons_bottom"]):
        if _finite(r) and r <= 100 - p["rsi_max_bullish"]:
            return SignalResult(MOMENTUM_BREAKOUT, False, BEARISH, {**detail, "reason": f"RSI {r:.1f} <= filter"})
        btype = f"{max(dn)}-day low" if dn else f"{p['consolidation_days']}-day consolidation breakdown"
        return SignalResult(MOMENTUM_BREAKOUT, True, BEARISH, {**detail, "breakout_type": btype})
    return SignalResult(MOMENTUM_BREAKOUT, False, detail={**detail, "reason": "no breakout"})


def combine_direction(results: list[SignalResult]) -> str:
    dirs = {r.direction for r in results if r.triggered and r.direction in (BULLISH, BEARISH)}
    if not dirs:
        return NEUTRAL
    return dirs.pop() if len(dirs) == 1 else MIXED
