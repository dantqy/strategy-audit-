"""Strategy 3 statistics: continuation vs reversal, subgroups, random baseline, robustness, costs, $300 test.

Conventions
-----------
sign        +1 for POSITIVE_SHOCK, -1 for NEGATIVE_SHOCK.
forward_h   raw stock return from the T+1 open (positive = price went up).
cont_h      sign * forward_h: > 0 means the shock CONTINUED, < 0 means it REVERSED.
vs_random   sign * (forward_h - matched random forward_h): continuation BEYOND what the same
            stock did on comparable random days (same stock, same year, same SPY regime).

Pre-registered selection rule (fixed before looking at any result)
------------------------------------------------------------------
A hypothesis x horizon is "IN-SAMPLE SIGNIFICANT" when, on 2019-2022 events only,
n >= 30 AND the 95% cluster-bootstrap CI (clusters = event dates) of vs_random excludes 0.
Its effect is CONTINUATION if the mean vs_random > 0, else REVERSAL. For each significant
hypothesis the single horizon with the largest |mean / standard error| is FROZEN, and only
that (hypothesis, horizon, effect) is evaluated out-of-sample (2023 - Sep 2024).
OOS "survives" = same sign AND CI excludes 0.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from backtest.metrics import trade_stats

from .features import HORIZONS

N_RANDOM, SEED = 20, 42
MIN_N = 30


# ----------------------------------------------------------------- hypotheses
def _sign(ev: pd.DataFrame) -> np.ndarray:
    return np.where(ev["event_direction"] == "POSITIVE_SHOCK", 1.0, -1.0)


def hypotheses() -> list[dict]:
    """The complete, fixed list of hypotheses tested (34 event sets). Nothing is added after results."""
    H = []
    ar = lambda e: e["daily_return"].abs()   # noqa: E731
    subgroups = [
        ("A volume", "rel volume 2-3x", lambda e: (e["relative_volume"] >= 2) & (e["relative_volume"] < 3)),
        ("A volume", "rel volume >=3x", lambda e: e["relative_volume"] >= 3),
        ("B size", "move 4-6%", lambda e: (ar(e) >= 0.04) & (ar(e) < 0.06)),
        ("B size", "move 6-10%", lambda e: (ar(e) >= 0.06) & (ar(e) < 0.10)),
        ("B size", "move >=10%", lambda e: ar(e) >= 0.10),
        ("C gap", "GAP_AND_GO", lambda e: e["gap_class"] == "GAP_AND_GO"),
        ("C gap", "GAP_AND_FADE", lambda e: e["gap_class"] == "GAP_AND_FADE"),
        ("C gap", "INTRADAY_MOVE", lambda e: e["gap_class"] == "INTRADAY_MOVE"),
        ("D trend", "close > SMA50", lambda e: e["trend_SMA50"] == "ABOVE_SMA50"),
        ("D trend", "close <= SMA50", lambda e: e["trend_SMA50"] == "BELOW_SMA50"),
        ("E regime", "SPY > SMA200", lambda e: e["market_regime"] == "SPY_ABOVE_SMA200"),
        ("E regime", "SPY <= SMA200", lambda e: e["market_regime"] == "SPY_BELOW_SMA200"),
        ("F rel. strength", "60d RS > 0", lambda e: e["relative_strength_60d"] > 0),
        ("F rel. strength", "60d RS <= 0", lambda e: e["relative_strength_60d"] <= 0),
    ]
    for d, tag in (("POSITIVE_SHOCK", "POS"), ("NEGATIVE_SHOCK", "NEG")):
        H.append({"name": f"{tag} baseline", "group": "baseline", "direction": d,
                  "mask": lambda e, d=d: e["event_direction"] == d})
        for g, label, fn in subgroups:
            H.append({"name": f"{tag} | {label}", "group": g, "direction": d,
                      "mask": lambda e, d=d, fn=fn: (e["event_direction"] == d) & fn(e)})
    P, N = "POSITIVE_SHOCK", "NEGATIVE_SHOCK"
    H += [
        {"name": "CONTINUATION A", "group": "combination", "direction": P,
         "mask": lambda e: (e["event_direction"] == P) & (e["relative_volume"] >= 2)
         & (e["market_adjusted_return"] > 0) & (e["gap_class"] == "GAP_AND_GO") & (e["relative_strength_60d"] > 0)},
        {"name": "CONTINUATION B", "group": "combination", "direction": P,
         "mask": lambda e: (e["event_direction"] == P) & (e["relative_volume"] >= 3) & (e["close"] > e["SMA50"])},
        {"name": "REVERSAL A", "group": "combination", "direction": P,
         "mask": lambda e: (e["event_direction"] == P) & (ar(e) >= 0.10) & (e["gap_class"] == "GAP_AND_FADE")},
        {"name": "NEGATIVE CONTINUATION", "group": "combination", "direction": N,
         "mask": lambda e: (e["event_direction"] == N) & (e["relative_volume"] >= 2)
         & (e["market_adjusted_return"] < 0) & (e["close"] < e["SMA50"])},
    ]
    return H


# ------------------------------------------------------------- random baseline
def add_random_baseline(ev: pd.DataFrame, feats: dict[str, pd.DataFrame], start, end,
                        k: int = N_RANDOM, seed: int = SEED) -> pd.DataFrame:
    """Matched random entries: for each event, k random NON-event days of the SAME stock in the SAME
    year and SAME SPY regime (falls back to same stock+year if that pool is small). Adds
    random_forward_{h}d = mean forward return of those days (same entry/exit rule)."""
    rng = np.random.default_rng(seed)
    ev = ev.copy()
    event_days = set(zip(ev["ticker"], ev["event_date"]))
    pools = {}
    for code, f in feats.items():
        g = f[(f.index >= start) & (f.index <= end) & f["entry_open"].notna() & f["market_regime"].notna()]
        g = g[[(code, d) not in event_days for d in g.index]]
        yr = pd.Series([d.year for d in g.index], index=g.index)
        pools[code] = (g, yr)
    cols = [f"forward_{h}d" for h in HORIZONS]
    out = np.full((len(ev), len(HORIZONS)), np.nan)
    used = np.zeros(len(ev), int)
    for n, (code, d, reg) in enumerate(zip(ev["ticker"], ev["event_date"], ev["market_regime"])):
        g, yr = pools[code]
        p = g[(yr == d.year).to_numpy() & (g["market_regime"] == reg).to_numpy()]
        if len(p) < k:
            p = g[(yr == d.year).to_numpy()]
        if not len(p):
            continue
        pick = p.iloc[rng.integers(0, len(p), k)][cols].to_numpy(float)
        out[n] = np.nanmean(pick, axis=0)
        used[n] = len(p)
    for j, h in enumerate(HORIZONS):
        ev[f"random_forward_{h}d"] = out[:, j]
    ev["random_pool_size"] = used
    return ev


# ------------------------------------------------------------------- stats
def _st(values, dates, n_boot=1000) -> dict:
    return trade_stats(pd.Series(values).reset_index(drop=True), pd.Series(dates).reset_index(drop=True),
                       MIN_N, n_boot, SEED)


def horizon_table(ev: pd.DataFrame, mask, n_boot: int = 1000) -> pd.DataFrame:
    """Per horizon: raw forward stats, SPY-excess, random mean, and continuation-vs-random stats."""
    e = ev[mask(ev)] if callable(mask) else ev[mask]
    rows = []
    s = _sign(e)
    for h in HORIZONS:
        f = e[f"forward_{h}d"]
        ok = f.notna()
        raw = _st(f[ok], e.loc[ok, "event_date"], n_boot)
        vr = s[ok.to_numpy()] * (f[ok] - e.loc[ok, f"random_forward_{h}d"])
        vs = _st(vr, e.loc[ok, "event_date"], n_boot)
        se = vs.get("std", float("nan")) / math.sqrt(vs["n"]) if vs["n"] > 1 else float("nan")
        rows.append({"horizon": h, "n": raw["n"], "mean": raw.get("mean"), "median": raw.get("median"),
                     "win_rate": raw.get("win_rate"), "std": raw.get("std"),
                     "ci_low": raw.get("mean_ci95_low"), "ci_high": raw.get("mean_ci95_high"),
                     "spy_mean": e.loc[ok, f"SPY_forward_{h}d"].mean(),
                     "excess_vs_spy": e.loc[ok, f"excess_forward_{h}d"].mean(),
                     "random_mean": e.loc[ok, f"random_forward_{h}d"].mean(),
                     "vs_random": vs.get("mean"), "vs_random_ci_low": vs.get("mean_ci95_low"),
                     "vs_random_ci_high": vs.get("mean_ci95_high"),
                     "vs_random_t": (vs.get("mean") / se) if se and se == se and se > 0 else float("nan"),
                     "significant": bool(raw["n"] >= MIN_N and vs.get("significant_5pct", False))})
    return pd.DataFrame(rows)


def all_hypotheses_table(ev: pd.DataFrame, n_boot: int = 1000) -> pd.DataFrame:
    out = []
    for H in hypotheses():
        t = horizon_table(ev, H["mask"], n_boot)
        t.insert(0, "direction", H["direction"])
        t.insert(0, "group", H["group"])
        t.insert(0, "hypothesis", H["name"])
        out.append(t)
    return pd.concat(out, ignore_index=True)


def select_in_sample(is_table: pd.DataFrame) -> pd.DataFrame:
    """Apply the pre-registered rule: one frozen (hypothesis, horizon, effect) per significant hypothesis."""
    sig = is_table[is_table["significant"]].copy()
    if sig.empty:
        return sig.assign(effect=[])
    sig["abs_t"] = sig["vs_random_t"].abs()
    best = sig.sort_values("abs_t", ascending=False).groupby("hypothesis", sort=False).head(1)
    best["effect"] = np.where(best["vs_random"] > 0, "CONTINUATION", "REVERSAL")
    best["long_tradable"] = ((best["direction"] == "POSITIVE_SHOCK") & (best["effect"] == "CONTINUATION")) | \
                            ((best["direction"] == "NEGATIVE_SHOCK") & (best["effect"] == "REVERSAL"))
    return best.sort_values("abs_t", ascending=False).reset_index(drop=True)


# -------------------------------------------------------------- robustness
def trade_returns(ev: pd.DataFrame, mask, h: int, effect: str) -> pd.DataFrame:
    """Return per event of TRADING the effect: continuation -> in the shock's direction, reversal -> against.
    (For a positive shock + continuation this is simply the long return.)"""
    e = ev[mask(ev)].copy()
    e = e[e[f"forward_{h}d"].notna()]
    trade_sign = _sign(e) * (1.0 if effect == "CONTINUATION" else -1.0)
    e["trade_ret"] = trade_sign * e[f"forward_{h}d"]
    e["trade_vs_random"] = trade_sign * (e[f"forward_{h}d"] - e[f"random_forward_{h}d"])
    return e


def outlier_table(e: pd.DataFrame) -> pd.DataFrame:
    r = e.sort_values("trade_ret", ascending=False)
    lo, hi = r["trade_ret"].quantile([0.01, 0.99])
    variants = [("all events", r), ("excl. best 1", r.iloc[1:]), ("excl. best 5", r.iloc[5:]),
                ("excl. best 10", r.iloc[10:]), ("winsorized 1/99%", r.assign(trade_ret=r["trade_ret"].clip(lo, hi)))]
    rows = []
    for label, g in variants:
        s = _st(g["trade_ret"], g["event_date"])
        rows.append({"variant": label, "n": s["n"], "mean": s.get("mean"), "ci_low": s.get("mean_ci95_low"),
                     "ci_high": s.get("mean_ci95_high"), "win_rate": s.get("win_rate")})
    return pd.DataFrame(rows)


def stock_table(e: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    by = e.groupby("ticker")["trade_ret"].agg(["count", "mean", "sum"]).sort_values("sum", ascending=False)
    total = e["trade_ret"].sum()
    best = by.index[0]
    rest = e[e["ticker"] != best]
    s = _st(rest["trade_ret"], rest["event_date"])
    info = {"total_sum": total, "best_ticker": best, "best_share_of_sum": by["sum"].iloc[0] / total if total else np.nan,
            "top3": list(by.index[:3]), "top3_share": by["sum"].iloc[:3].sum() / total if total else np.nan,
            "top5": list(by.index[:5]), "top5_share": by["sum"].iloc[:5].sum() / total if total else np.nan,
            "without_best_n": s["n"], "without_best_mean": s.get("mean"),
            "without_best_ci": (s.get("mean_ci95_low"), s.get("mean_ci95_high"))}
    return by, info


def year_table(e: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for y, g in e.groupby("year"):
        rows.append({"year": int(y), "n": len(g), "mean_trade_ret": g["trade_ret"].mean(),
                     "mean_vs_random": g["trade_vs_random"].mean(), "win_rate": (g["trade_ret"] > 0).mean()})
    return pd.DataFrame(rows)


def cost_table(e: pd.DataFrame, cfg: dict, sizes=(150, 500, 1000, 3000)) -> pd.DataFrame:
    """Long-only round trip: fixed platform/commission fees plus slippage per side on the notional."""
    c = cfg["costs"]
    gross = e["trade_ret"].mean()
    slip = 2 * c["slippage_bps"] / 1e4 + c["regulatory_fee_bps_on_sells"] / 1e4
    rows = []
    for s in sizes:
        fee = 2 * (c["commission_per_order"] + c["platform_fee_per_order"]) / s
        rows.append({"position_usd": s, "gross_expectancy": gross, "fees": fee, "slippage_and_reg": slip,
                     "net_expectancy": gross - fee - slip})
    return pd.DataFrame(rows)
