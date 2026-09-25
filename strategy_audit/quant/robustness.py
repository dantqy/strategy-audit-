"""Robustness audit: tries to find evidence AGAINST a backtest. Every threshold is fixed here, in code,
before any user strategy is seen; statuses are PASS / MIXED / FAIL (never a 0-100 score).

All per-trade statistics use the TRADE LEDGER (every rule-generated trade, net of costs at the
portfolio's position size), so they do not depend on which trades a capacity-limited portfolio took.
Confidence intervals: bootstrap of the mean, resampling entry DATES (clusters), fixed seed.
"""
from __future__ import annotations

import copy
import math
from datetime import date

import numpy as np
import pandas as pd

from backtest.metrics import trade_stats

from ..schema import StrategySpec
from .engine import Market, generate_trades, net_return, signal_mask, simulate_exit

SEED = 42
N_BOOT = 2000
N_RANDOM = 20
MIN_N = 30

PASS, MIXED, FAIL, SEALED, NA = "PASS", "MIXED", "FAIL", "SEALED", "N/A"


def _nan(x):
    return None if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else x


def mean_stats(returns: pd.Series, dates: pd.Series) -> dict:
    r = pd.Series(returns).reset_index(drop=True)
    d = pd.Series(dates).reset_index(drop=True)
    s = trade_stats(r, d, MIN_N, N_BOOT, SEED)
    return {"n": s["n"], "mean": _nan(s.get("mean")), "median": _nan(s.get("median")),
            "win_rate": _nan(s.get("win_rate")), "ci_low": _nan(s.get("mean_ci95_low")),
            "ci_high": _nan(s.get("mean_ci95_high")), "small_sample": s["n"] < MIN_N}


# ------------------------------------------------------------- statistics
def statistical_confidence(led: pd.DataFrame) -> dict:
    s = mean_stats(led["net_return"], led["entry_date"])
    if not s["n"] or (s["mean"] or 0) <= 0:
        st, why = FAIL, "Average trade after costs is not positive."
    elif s["small_sample"]:
        st, why = MIXED, f"Only {s['n']} trades: too few to rule out luck."
    elif (s["ci_low"] or 0) > 0:
        st, why = PASS, "The 95% confidence interval of the average trade excludes zero."
    else:
        st, why = MIXED, "The 95% confidence interval of the average trade includes zero."
    return {"status": st, "summary": why, **s}


# ---------------------------------------------------------------- costs
def cost_stress(led: pd.DataFrame, spec: StrategySpec) -> dict:
    notional = spec.portfolio.starting_capital / spec.portfolio.max_positions
    c = spec.costs
    scen = [("Baseline", 1, 1), ("Fees x2", 2, 1), ("Slippage x3", 1, 3), ("Fees x2 + slippage x3", 2, 3),
            ("Severe: fees x3 + slippage x5", 3, 5)]
    g = led["gross_return"]
    rows = []
    for label, fm, sm in scen:
        cc = c.model_copy(update={"commission_per_order": c.commission_per_order * fm,
                                  "platform_fee_per_order": c.platform_fee_per_order * fm,
                                  "slippage_bps": c.slippage_bps * sm})
        net = g.apply(lambda x: net_return(x, cc, notional)) if len(g) else g
        rows.append({"scenario": label, "gross_expectancy": _nan(float(g.mean())) if len(g) else None,
                     "net_expectancy": _nan(float(net.mean())) if len(g) else None})
    base, stress = rows[0]["net_expectancy"] or 0, rows[3]["net_expectancy"] or 0
    if base <= 0:
        st, why = FAIL, "Not profitable after baseline costs."
    elif stress > 0:
        st, why = PASS, "Still positive with double fees and triple slippage."
    else:
        st, why = MIXED, "Positive at baseline costs, but the edge disappears with modestly worse execution."
    for r in rows:
        r["degradation"] = _nan((1 - r["net_expectancy"] / base) if base and r["net_expectancy"] is not None else None)
    return {"status": st, "summary": why, "scenarios": rows}


# -------------------------------------------------------------- regimes
def regime_test(led: pd.DataFrame) -> dict:
    total = float(led["net_return"].sum()) if len(led) else 0.0
    rows = []
    for reg in ("BULL", "BEAR"):
        g = led[led["regime"] == reg]
        rows.append({"regime": reg, "label": "SPY above its 200-day average" if reg == "BULL" else
                     "SPY at/below its 200-day average", "n": int(len(g)),
                     "mean": _nan(float(g["net_return"].mean())) if len(g) else None,
                     "sum": float(g["net_return"].sum()), "share_of_total": _nan(g["net_return"].sum() / total) if total else None})
    b, e = rows
    if total <= 0:
        st, why = FAIL, "Not profitable overall."
    elif min(b["n"], e["n"]) < 10:
        thin = b if b["n"] < 10 else e
        st, why = MIXED, f"Only {thin['n']} trades in {thin['regime']} markets: cannot judge that regime."
    elif (b["mean"] or 0) > 0 and (e["mean"] or 0) > 0 and max(b["share_of_total"], e["share_of_total"]) < 0.8:
        st, why = PASS, "Positive in both bull and bear regimes."
    elif (b["mean"] or 0) <= 0 or (e["mean"] or 0) <= 0:
        bad = b if (b["mean"] or 0) <= 0 else e
        st, why = FAIL, f"Loses money in {bad['regime']} markets; profits come from one regime."
    else:
        st, why = MIXED, "Positive in both regimes, but over 80% of profit comes from one."
    return {"status": st, "summary": why, "regimes": rows}


# ---------------------------------------------------------------- years
def year_test(led: pd.DataFrame) -> dict:
    total = float(led["net_return"].sum()) if len(led) else 0.0
    rows = [{"year": int(y), "n": int(len(g)), "mean": float(g["net_return"].mean()), "sum": float(g["net_return"].sum())}
            for y, g in led.groupby("year")]
    if total <= 0 or not rows:
        return {"status": FAIL, "summary": "Not profitable overall.", "years": rows}
    best = max(rows, key=lambda r: r["sum"])
    share = best["sum"] / total
    without = total - best["sum"]
    if without <= 0:
        st, why = FAIL, f"Without {best['year']}, the strategy is not profitable."
    elif share > 0.5:
        st, why = MIXED, f"{best['year']} alone produced {share:.0%} of all profit."
    else:
        st, why = PASS, "Profit is spread across years."
    return {"status": st, "summary": why, "years": rows, "best_year": best["year"], "best_year_share": share}


# -------------------------------------------------------------- outliers
def outlier_test(led: pd.DataFrame) -> dict:
    r = led.sort_values("net_return", ascending=False)
    lo, hi = (r["net_return"].quantile([0.01, 0.99]) if len(r) else (0, 0))
    rows = []
    for label, g in (("All trades", r), ("Without best trade", r.iloc[1:]), ("Without best 5", r.iloc[5:]),
                     ("Without best 10", r.iloc[10:]),
                     ("Winsorized 1%/99%", r.assign(net_return=r["net_return"].clip(lo, hi)))):
        rows.append({"variant": label, "n": int(len(g)), "mean": _nan(float(g["net_return"].mean())) if len(g) else None})
    m = [x["mean"] if x["mean"] is not None else -1 for x in rows]
    if m[0] <= 0:
        st, why, level = FAIL, "Not profitable even with its best trades.", "N/A"
    elif m[2] <= 0:
        st, why, level = FAIL, "Profit disappears without the 5 best trades.", "HIGH"
    elif m[3] <= 0:
        st, why, level = MIXED, "Profit disappears without the 10 best trades.", "MODERATE"
    else:
        st, why, level = PASS, "Still profitable without the 10 best trades.", "LOW"
    return {"status": st, "summary": why, "dependence": level, "variants": rows}


# --------------------------------------------------------- concentration
def concentration_test(led: pd.DataFrame) -> dict:
    total = float(led["net_return"].sum()) if len(led) else 0.0
    by = led.groupby("ticker")["net_return"].agg(["count", "sum", "mean"]).sort_values("sum", ascending=False)
    if total <= 0 or by.empty:
        return {"status": FAIL, "summary": "Not profitable overall.", "top": []}
    best = by.index[0]
    rest = led[led["ticker"] != best]
    wo = float(rest["net_return"].mean()) if len(rest) else float("nan")
    s1, s3, s5 = (by["sum"].iloc[:k].sum() / total for k in (1, 3, 5))
    if not wo > 0:
        st, why = FAIL, f"Without {best}, the average trade is not positive."
    elif s1 > 0.25 or s3 > 0.5:
        st, why = MIXED, f"Profit is concentrated: {best} = {s1:.0%}, top 3 = {s3:.0%} of the total."
    else:
        st, why = PASS, "Profit is spread across many stocks."
    top = [{"ticker": t, "trades": int(r["count"]), "sum": float(r["sum"]), "share": float(r["sum"] / total)}
           for t, r in by.head(5).iterrows()]
    return {"status": st, "summary": why, "top": top, "best_ticker": best, "top1_share": s1, "top3_share": s3,
            "top5_share": s5, "mean_without_best_ticker": _nan(wo), "tickers_traded": int(len(by))}


# --------------------------------------------------------- random entries
def random_baseline(led: pd.DataFrame, spec: StrategySpec, mkt: Market, start: date, end: date,
                    k: int = N_RANDOM, seed: int = SEED) -> dict:
    """For each trade: k random NON-signal days of the SAME stock, SAME year, SAME SPY regime, traded with the
    same entry/exit rules and costs. Compares strategy expectancy with what those stocks did anyway."""
    if led.empty:
        return {"status": FAIL, "summary": "No trades.", "strategy_mean": None, "random_mean": None}
    rng = np.random.default_rng(seed)
    notional = spec.portfolio.starting_capital / spec.portfolio.max_positions
    pools: dict[str, dict] = {}
    memo: dict[tuple, float | None] = {}
    for code in sorted(set(led["ticker"])):
        a, b = mkt.arr[code], mkt.bars[code]
        sig = signal_mask(spec, b, mkt.spy).to_numpy()
        pool: dict[tuple, list[int]] = {}
        for i, d in enumerate(a.dates):
            if start <= d <= end and not sig[i]:
                pool.setdefault((d.year, mkt.regime.get(d)), []).append(i)
        pools[code] = pool
    rand_means = []
    for code, dt, reg in zip(led["ticker"], led["signal_date"], led["regime"]):
        cand = pools[code].get((dt.year, reg)) or []
        vals = []
        for i in (rng.choice(cand, size=min(k * 3, len(cand)), replace=len(cand) < k * 3) if cand else []):
            key = (code, int(i))
            if key not in memo:
                t = simulate_exit(mkt.arr[code], int(i), spec.exit)
                memo[key] = None if (t is None or t[2] > end) else net_return(t[3] / t[1] - 1, spec.costs, notional)
            if memo[key] is not None:
                vals.append(memo[key])
            if len(vals) >= k:
                break
        rand_means.append(np.mean(vals) if vals else np.nan)
    diff = led["net_return"].to_numpy() - np.array(rand_means)
    ok = ~np.isnan(diff)
    s = mean_stats(pd.Series(diff[ok]), led["entry_date"][ok])
    if (s["mean"] or 0) <= 0:
        st, why = FAIL, "No better than entering the same stocks on random days."
    elif (s["ci_low"] or 0) > 0:
        st, why = PASS, "Beats random entries in the same stocks, and the difference's 95% CI excludes zero."
    else:
        st, why = MIXED, "Beats random entries on average, but the difference could be chance."
    return {"status": st, "summary": why, "strategy_mean": _nan(float(led["net_return"][ok].mean())),
            "random_mean": _nan(float(np.nanmean(rand_means))), "difference": s, "samples_per_trade": k,
            "seed": seed, "matched_on": ["same stock", "same calendar year", "same SPY regime (above/below 200-day)"]}


# ------------------------------------------------------ parameter sensitivity
def _neighbors(spec: StrategySpec) -> list[tuple[str, str, dict]]:
    """(parameter label, value label, patch) one-at-a-time neighbours. Stability test, NOT optimisation."""
    out = []
    d = spec.model_dump(mode="json")
    for ci, c in enumerate(d["entry_conditions"]):
        ind = c["indicator"]
        if c.get("value") is not None:
            v = c["value"]
            if ind == "RSI":
                vals = [max(1.0, v - 5), min(99.0, v + 5)]
            elif ind in ("RETURN", "REL_STRENGTH", "SPY_RETURN"):
                step = max(0.01, abs(v) * 0.2)
                vals = [round(v - step, 4), round(v + step, 4)]
            elif ind == "REL_VOLUME":
                vals = [round(v * 0.8, 2), round(v * 1.2, 2)]
            else:
                vals = [round(v * 0.9, 2), round(v * 1.1, 2)]
            for nv in vals:
                out.append((f"{ind} threshold", f"{nv:g}", {"cond": ci, "field": "value", "new": nv}))
        for fld in ("period", "comparison_period"):
            p = c.get(fld)
            if p:
                name = c["indicator"] if fld == "period" else c["comparison_indicator"]
                for np_ in sorted({max(2, round(p * 0.75)), round(p * 1.25)} - {p}):
                    out.append((f"{name} period", str(np_), {"cond": ci, "field": fld, "new": int(np_)}))
    h = d["exit"]["trading_days"]
    for nh in sorted({max(1, round(h * 0.5)), min(60, round(h * 1.5))} - {h}):
        out.append(("Holding days", str(nh), {"exit": nh}))
    return out[:16]


def _patched(spec: StrategySpec, patch: dict) -> StrategySpec | None:
    d = copy.deepcopy(spec.model_dump(mode="json"))
    if "exit" in patch:
        d["exit"]["trading_days"] = patch["exit"]
    else:
        d["entry_conditions"][patch["cond"]][patch["field"]] = patch["new"]
    try:
        return StrategySpec.model_validate(d)
    except Exception:
        return None                      # neighbour outside the allowed range: skipped, not forced


def sensitivity(spec: StrategySpec, mkt: Market, start: date, end: date, base_led: pd.DataFrame) -> dict:
    base = float(base_led["net_return"].mean()) if len(base_led) else float("nan")
    rows = [{"parameter": "(original)", "value": "original", "n": int(len(base_led)), "mean": _nan(base)}]
    for label, vlabel, patch in _neighbors(spec):
        s2 = _patched(spec, patch)
        if s2 is None:
            continue
        led, _ = generate_trades(s2, mkt, start, end)
        rows.append({"parameter": label, "value": vlabel, "n": int(len(led)),
                     "mean": _nan(float(led["net_return"].mean())) if len(led) else None})
    # small 3x3 grid: first numeric threshold x holding days (a heatmap, still <= 9 cells)
    grid = []
    first = next(((ci, c) for ci, c in enumerate(spec.entry_conditions) if c.value is not None), None)
    if first:
        ci, c = first
        thr = sorted({x for x in [p["new"] for lbl, _, p in _neighbors(spec) if p.get("cond") == ci and p.get("field") == "value"]}
                     | {c.value})
        h = spec.exit.trading_days
        holds = sorted({max(1, round(h * 0.5)), h, min(60, round(h * 1.5))})
        for tv in thr:
            for hv in holds:
                d = copy.deepcopy(spec.model_dump(mode="json"))
                d["entry_conditions"][ci]["value"] = tv
                d["exit"]["trading_days"] = hv
                try:
                    s2 = StrategySpec.model_validate(d)
                except Exception:
                    continue
                led, _ = generate_trades(s2, mkt, start, end)
                grid.append({"threshold": tv, "hold": hv, "n": int(len(led)),
                             "mean": _nan(float(led["net_return"].mean())) if len(led) else None})
        grid_meta = {"threshold_label": c.describe().split(" ")[0], "thresholds": thr, "holds": holds}
    else:
        grid_meta = None
    neigh = [r for r in rows[1:] if r["mean"] is not None]
    pos = sum(1 for r in neigh if r["mean"] > 0)
    share = pos / len(neigh) if neigh else float("nan")
    if not base > 0:
        st, why = FAIL, "The original parameters are not profitable, so there is no edge to be stable."
    elif not neigh:
        st, why = MIXED, "No neighbouring parameters could be tested."
    elif share >= 0.8:
        st, why = PASS, f"{pos} of {len(neigh)} nearby parameter settings are also profitable."
    elif share >= 0.5:
        st, why = MIXED, f"Only {pos} of {len(neigh)} nearby parameter settings are profitable."
    else:
        st, why = FAIL, f"Only {pos} of {len(neigh)} nearby settings are profitable: the result depends on exact parameters."
    return {"status": st, "summary": why, "variants": rows, "grid": grid, "grid_meta": grid_meta,
            "neighbors_tested": len(neigh), "neighbors_positive": pos}


# ----------------------------------------------------------- out-of-sample
def out_of_sample(dev_led: pd.DataFrame, val_led: pd.DataFrame | None, revealed: bool) -> dict:
    dev = mean_stats(dev_led["net_return"], dev_led["entry_date"])
    if not revealed or val_led is None:
        return {"status": SEALED, "summary": "Validation period is sealed. Reveal it once to run this test.",
                "development": dev, "validation": None, "degradation": None}
    val = mean_stats(val_led["net_return"], val_led["entry_date"])
    dm, vm = dev["mean"] or 0, val["mean"]
    deg = _nan(1 - vm / dm) if dm and vm is not None else None
    if vm is None or not val["n"]:
        st, why = FAIL, "No trades in the validation period."
    elif vm <= 0:
        st, why = FAIL, "Not profitable in the unseen validation period."
    elif dm > 0 and vm < 0.5 * dm:
        st, why = MIXED, f"Still positive out-of-sample, but the average trade fell {deg:.0%}."
    elif (val["ci_low"] or 0) <= 0:
        st, why = MIXED, "Positive out-of-sample, but the validation confidence interval includes zero."
    else:
        st, why = PASS, "Holds up in the unseen validation period."
    return {"status": st, "summary": why, "development": dev, "validation": val, "degradation": deg}
