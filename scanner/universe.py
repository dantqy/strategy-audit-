"""Replaceable universe module.

Default: data/universe.csv (93 liquid US names + benchmark). Kept under ~100
symbols because Moomoo's historical-candle quota for accounts < HKD 10k is 100
distinct stocks per rolling 7 days (re-requests of the same stock are free).

NOTE (survivorship bias): a universe of *today's* liquid names excludes stocks
that were delisted or fell out of favour during the backtest period. Backtest
reports flag this.
"""
from __future__ import annotations

import logging

import pandas as pd

from .config import resolve

log = logging.getLogger(__name__)


def load_universe(cfg: dict) -> list[str]:
    u = cfg["universe"]
    if u["source"] != "csv":
        raise ValueError(f"universe source '{u['source']}' not implemented; use 'csv'")
    df = pd.read_csv(resolve(u["csv_path"]))
    codes = [c.strip() for c in df["code"].astype(str) if c.strip() and not c.startswith("#")]
    codes = [c if c.startswith("US.") else f"US.{c}" for c in codes]
    dupes = len(codes) - len(set(codes))
    if dupes:
        log.warning("universe.csv has %d duplicate codes (dropped)", dupes)
    return list(dict.fromkeys(codes))


def liquidity_ok(last_price: float | None, avg_dollar_volume: float | None, cfg: dict) -> bool | None:
    """True/False when data exists; None when it cannot be judged."""
    if last_price is None or avg_dollar_volume is None or pd.isna(last_price) or pd.isna(avg_dollar_volume):
        return None
    u = cfg["universe"]
    return bool(last_price >= u["min_price"] and avg_dollar_volume >= u["min_avg_dollar_volume"])
