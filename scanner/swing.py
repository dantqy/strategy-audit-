"""Whole-market SWING screener: today's big movers on heavy volume, from live snapshots.

UNTESTED IDEA LIST. Unlike the earnings scanner there is no backtest behind this;
it finds volatile, liquid, affordable stocks that are moving now and attaches a
simple, transparent plan (whole shares for the budget, a stop, the $ at risk, the
fee cost). It never places orders.

Uses get_market_snapshot (plus the cached earnings calendar), which does NOT count
against the 100-stocks-per-week historical-candle quota.

Market phase decides what "move" means:
  PRE_MARKET          gap = pre-market price vs previous close (volume ratio n/a)
  OPEN / after close  move = last price vs previous close; relative volume = Moomoo
                      volume_ratio (today vs 5-day average at the same time of day)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

import numpy as np
import pandas as pd

from . import market_calendar as mc
from .config import resolve

log = logging.getLogger(__name__)

SNAP_COLS = ("last_price", "prev_close_price", "open_price", "high_price", "low_price", "volume", "turnover",
             "volume_ratio", "highest52weeks_price", "lowest52weeks_price", "pre_price", "pre_turnover",
             "total_market_val")


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce") if col in df.columns else pd.Series(np.nan, index=df.index)


def screen(snap: pd.DataFrame, cfg: dict, now: datetime, earnings: dict | None = None) -> pd.DataFrame:
    """Rank today's swing candidates. Pure: all inputs passed in.

    snap: Moomoo snapshot rows (one per stock). earnings: code -> "2026-09-24 AFTER" for
    recent reports. Returns one row per candidate, best first.
    """
    sw, fee = cfg["swing"], float(cfg["costs"]["platform_fee_per_order"])
    earnings = earnings or {}
    if snap is None or snap.empty:
        return pd.DataFrame()
    s = snap.copy()
    n = {c: _num(s, c) for c in SNAP_COLS}
    phase = mc.market_phase(now)
    pre = phase == "PRE_MARKET"
    ref = n["pre_price"].where(n["pre_price"] > 0) if pre else n["last_price"]
    prev = n["prev_close_price"]
    s["price"] = ref
    s["move_pct"] = (ref / prev - 1) * 100
    s["rel_vol"] = np.nan if pre else n["volume_ratio"]
    frac = (mc.session_elapsed_fraction(now) or 1.0) if phase == "OPEN" else 1.0
    s["est_avg_turnover"] = n["turnover"] / n["volume_ratio"].where(n["volume_ratio"] > 0) / frac
    s["mkt_cap"] = n["total_market_val"]

    ok = ref.between(sw["min_price"], sw["max_price"]) & (s["move_pct"].abs() >= sw["min_move_pct"])
    if pre:     # no regular-session volume yet: demand a bigger company and real pre-market trading
        ok &= (s["mkt_cap"] >= max(sw["min_market_cap"], 1e9)) & (n["pre_turnover"] >= 1e6)
    else:
        ok &= (s["mkt_cap"] >= sw["min_market_cap"]) & (s["est_avg_turnover"] >= sw["min_avg_turnover"]) \
            & (s["rel_vol"] >= sw["min_relative_volume"])
    if sw.get("long_only", True):
        ok &= s["move_pct"] > 0
    s = s[ok.fillna(False)].copy()
    if s.empty:
        return pd.DataFrame()
    n = {c: v.loc[s.index] for c, v in n.items()}

    up = s["move_pct"] > 0
    hi52 = n["highest52weeks_price"]
    rng = (n["high_price"] - n["low_price"]).where(lambda x: x > 0)
    s["range_pct"] = rng / prev.loc[s.index] * 100
    near_high = up & (s["price"] >= 0.97 * hi52)
    strong = up & (((s["price"] - n["low_price"]) / rng) >= 0.7) & (not pre)
    s["earnings"] = s["code"].map(earnings).fillna("")

    def tags(i):
        t = []
        if s.at[i, "earnings"]:
            t.append(f"earnings {s.at[i, 'earnings']}")
        if near_high.get(i, False):
            t.append("near 52-week high")
        if strong.get(i, False):
            t.append("holding near day high" if phase == "OPEN" else "closed near day high")
        if pre:
            t.append("pre-market gap")
        return ", ".join(t)
    s["tags"] = [tags(i) for i in s.index]

    # plan: all-in whole shares; stop at today's low but never wider than max_stop_pct
    budget, max_stop = float(sw["budget_usd"]), float(sw["max_stop_pct"]) / 100
    s["shares"] = np.floor(budget / s["price"]).astype(int)
    floor_stop = s["price"] * (1 - max_stop)
    low = n["low_price"] if not pre else pd.Series(np.nan, index=s.index)
    s["stop"] = np.where(up, np.fmax(low.fillna(0), floor_stop), np.nan)
    s["stop"] = s["stop"].where(s["stop"] < s["price"], floor_stop)
    s["risk_pct"] = (s["price"] - s["stop"]) / s["price"] * 100
    s["risk_usd"] = s["shares"] * (s["price"] - s["stop"])
    s["fee_pct_round_trip"] = 2 * fee / (s["shares"] * s["price"]) * 100

    # transparent ordering score (NOT a probability, NOT backtested)
    rv = s["rel_vol"].fillna(sw["min_relative_volume"]).clip(upper=10)
    s["score"] = (rv + s["move_pct"].abs().clip(upper=30) / 3 + 3 * (s["earnings"] != "")
                  + 2 * near_high.astype(int) + 1 * strong.astype(int)).round(1)
    cols = ["code", "name", "price", "move_pct", "rel_vol", "range_pct", "mkt_cap", "est_avg_turnover", "tags",
            "shares", "stop", "risk_pct", "risk_usd", "fee_pct_round_trip", "score"]
    if "name" not in s.columns:
        s["name"] = ""
    return s.sort_values(["score", "move_pct"], ascending=False)[cols].head(int(sw["top_n"])).reset_index(drop=True)


def format_swing(df: pd.DataFrame, now: datetime, phase: str, scanned: int) -> str:
    head = [f"SWING IDEAS {mc.to_et(now):%Y-%m-%d %H:%M} ET | market {phase} | scanned {scanned} stocks",
            "UNTESTED: no backtest behind these. High risk. You decide; nothing is ordered.", ""]
    if df.empty:
        return "\n".join(head + ["Nothing passed the filters right now."])
    out = []
    for k, r in df.iterrows():
        rv = "n/a" if pd.isna(r["rel_vol"]) else f"{r['rel_vol']:.1f}x"
        out.append(
            f"{k + 1}. {r['code'].replace('US.', '')} ${r['price']:,.2f} {r['move_pct']:+.1f}% vol {rv} "
            f"cap ${r['mkt_cap'] / 1e9:,.1f}B" + (f"\n   {r['tags']}" if r["tags"] else "") +
            f"\n   plan: {r['shares']} sh (${r['shares'] * r['price']:,.0f}), stop ${r['stop']:,.2f} "
            f"(-{r['risk_pct']:.1f}%, ${r['risk_usd']:,.0f} at risk), fees {r['fee_pct_round_trip']:.1f}% round trip")
    return "\n".join(head + out)


def recent_earnings(client, cfg: dict, now: datetime) -> dict:
    """code -> 'YYYY-MM-DD TIMING' for reports in the last few days (cached calendar)."""
    from .earnings import load_events
    today = mc.to_et(now).date()
    start = mc.prev_trading_day(mc.prev_trading_day(mc.last_completed_session(now)))
    try:
        ev = load_events({**cfg, "earnings": {**cfg["earnings"], "providers": ["moomoo"]}},
                         start, today, None, client=client)
    except Exception as e:           # tags are a bonus; never fail the screen over them
        log.warning("earnings tags unavailable: %s", e)
        return {}
    ev = ev.sort_values("event_date")
    return {r["code"]: f"{r['event_date']} {r['timing']}" for _, r in ev.iterrows()}


def run_swing_scan(cfg: dict, client, now: datetime | None = None) -> tuple[pd.DataFrame, dict]:
    """Snapshot every NYSE/Nasdaq common stock, screen, save. Returns (candidates, meta)."""
    now = now or mc.now_utc()
    info = client.us_stock_names()
    if "exchange_type" in info.columns:
        info = info[info["exchange_type"].isin(["US_NYSE", "US_NASDAQ"])]
    codes = [c for c in info["code"] if isinstance(c, str) and c[3:].isalpha() and c[3:].isupper()
             and 1 <= len(c) - 3 <= 5]
    snap = client.snapshot(codes)
    if "name" not in snap.columns and "name" in info.columns:
        snap = snap.merge(info[["code", "name"]], on="code", how="left")
    df = screen(snap, cfg, now, recent_earnings(client, cfg, now))
    if len(df):
        df.insert(0, "rank", range(1, len(df) + 1))
    phase = mc.market_phase(now)
    meta = {"scan_time": mc.timestamps(now), "market_phase": phase, "scanned": len(snap),
            "untested": True, "filters": cfg["swing"]}
    d = resolve("outputs/swing")
    d.mkdir(parents=True, exist_ok=True)
    stamp = mc.to_et(now).strftime("%Y%m%dT%H%M%S")
    df.to_csv(d / f"swing_{stamp}ET.csv", index=False)
    (d / f"swing_{stamp}ET.json").write_text(json.dumps({**meta, "candidates": df.to_dict("records")},
                                                        indent=2, default=str))
    from .swing_trading import record_ideas       # every idea is tracked, taken or not
    record_ideas(df, meta)
    return df, meta
