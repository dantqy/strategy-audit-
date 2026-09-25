"""Transparent additive score. Every point is itemised in `score_breakdown`."""
from __future__ import annotations

from .signals import BEARISH, BULLISH, EARNINGS_DRIFT, MOMENTUM_BREAKOUT, VOLUME_ANOMALY


def score_candidate(c: dict, cfg: dict) -> tuple[int, list[tuple[str, int]]]:
    w, pen = cfg["ranking"]["weights"], cfg["ranking"]["penalties"]
    items: list[tuple[str, int]] = []
    sig = c["signals_triggered"]
    if EARNINGS_DRIFT in sig:
        items.append(("earnings drift", w["earnings_drift"]))
    if VOLUME_ANOMALY in sig:
        items.append(("volume anomaly", w["volume_anomaly"]))
    if MOMENTUM_BREAKOUT in sig:
        items.append(("momentum breakout", w["momentum_breakout"]))
    if len(sig) >= 2 and c["signal_direction"] in (BULLISH, BEARISH):
        items.append(("multiple confirmation (same direction)", w["multi_confirmation"]))
    r, lvl = c.get("rsi_14"), pen["rsi_extreme_level"]
    if r is not None and ((c["signal_direction"] == BULLISH and r >= lvl) or
                          (c["signal_direction"] == BEARISH and r <= 100 - lvl)):
        items.append((f"extreme RSI ({r:.0f})", pen["rsi_extreme"]))
    if c.get("liquidity_ok") is False:
        items.append(("poor liquidity", pen["poor_liquidity"]))
    if c.get("data_freshness") in ("LAST_CLOSE", "STALE"):
        items.append((f"stale data ({c['data_freshness']})", pen["stale_data"]))
    if c.get("rvol_method", "").startswith("APPROX"):
        items.append(("approximate intraday volume", pen["approximate_volume"]))
    return sum(p for _, p in items), items


def rank(candidates: list[dict], cfg: dict) -> list[dict]:
    for c in candidates:
        c["score"], c["score_breakdown"] = score_candidate(c, cfg)
    return sorted(candidates, key=lambda c: (-c["score"], -abs(c.get("reaction_move_pct") or 0), c["ticker"]))
