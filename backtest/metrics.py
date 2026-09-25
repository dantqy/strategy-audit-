"""Performance statistics with honest uncertainty.

Per-trade statistics use a CLUSTER bootstrap by reaction date: earnings events
bunch on the same days and are correlated, so resampling single trades would
overstate precision.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

try:
    from scipy import stats as _st
except ImportError:  # pragma: no cover
    _st = None


def cluster_bootstrap_mean_ci(values: np.ndarray, clusters: np.ndarray | None, n_boot: int = 2000,
                              seed: int = 42, alpha: float = 0.05) -> tuple[float, float, float]:
    """(ci_low, ci_high, p_two_sided_approx) for the mean; clusters = e.g. dates."""
    values = np.asarray(values, float)
    if values.size < 2:
        return (float("nan"), float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    if clusters is None:
        clusters = np.arange(values.size)
    keys, inv = np.unique(np.asarray(clusters, dtype=str), return_inverse=True)
    sums = np.bincount(inv, weights=values)
    cnts = np.bincount(inv)
    k = len(keys)
    idx = rng.integers(0, k, size=(n_boot, k))
    means = sums[idx].sum(1) / cnts[idx].sum(1)
    lo, hi = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    # bootstrap p-value for H0: mean = 0 (shifted distribution)
    centered = means - means.mean()
    p = float(np.mean(np.abs(centered) >= abs(values.mean())))
    return float(lo), float(hi), p


def trade_stats(returns, dates=None, min_n: int = 30, n_boot: int = 2000, seed: int = 42) -> dict:
    r = np.asarray(pd.Series(returns).dropna(), float)
    n = int(r.size)
    out = {"n": n, "low_sample_size": n < min_n}
    if n == 0:
        return out
    wins, losses = r[r > 0], r[r <= 0]
    gross_win, gross_loss = wins.sum(), -losses.sum()
    out.update({
        "win_rate": float((r > 0).mean()),
        "avg_winner": float(wins.mean()) if wins.size else float("nan"),
        "avg_loser": float(losses.mean()) if losses.size else float("nan"),
        "profit_factor": float(gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "mean": float(r.mean()), "median": float(np.median(r)),
        "std": float(r.std(ddof=1)) if n > 1 else float("nan"),
        "sum_of_returns": float(r.sum()),
        "compounded_if_sequential": float(np.prod(1 + r) - 1),
        "expectancy": float(r.mean()),
    })
    if n > 1 and out["std"] > 0:
        out["t_stat"] = float(out["mean"] / (out["std"] / math.sqrt(n)))
        out["t_test_p"] = float(_st.ttest_1samp(r, 0.0).pvalue) if _st else float("nan")
    d = None if dates is None else np.asarray(pd.Series(dates).loc[pd.Series(returns).dropna().index], dtype=str)
    lo, hi, p = cluster_bootstrap_mean_ci(r, d, n_boot=n_boot, seed=seed)
    out.update({"mean_ci95_low": lo, "mean_ci95_high": hi, "bootstrap_p": p,
                "significant_5pct": bool(lo > 0 or hi < 0) if not math.isnan(lo) else False})
    return out


def equity_stats(curve: pd.Series) -> dict:
    curve = curve.dropna()
    if len(curve) < 2:
        return {"n_days": len(curve)}
    rets = curve.pct_change().dropna()
    total = curve.iloc[-1] / curve.iloc[0] - 1
    years = len(rets) / 252
    peak = curve.cummax()
    dd = (curve / peak - 1).min()
    sharpe = float(rets.mean() / rets.std(ddof=1) * math.sqrt(252)) if rets.std(ddof=1) > 0 else float("nan")
    return {"n_days": int(len(curve)), "start_equity": float(curve.iloc[0]), "end_equity": float(curve.iloc[-1]),
            "total_return": float(total),
            "annualized_return": float((1 + total) ** (1 / years) - 1) if years >= 0.5 and total > -1 else None,
            "annualized_vol": float(rets.std(ddof=1) * math.sqrt(252)),
            "sharpe": sharpe, "max_drawdown": float(dd)}


def skepticism_flags(ts: dict, eq: dict | None = None) -> list[str]:
    flags = []
    if ts.get("n", 0) and ts.get("low_sample_size"):
        flags.append(f"LOW SAMPLE SIZE (n={ts['n']}): conclusions unreliable")
    if ts.get("n", 0) >= 20 and ts.get("win_rate", 0) > 0.8:
        flags.append("SUSPICIOUS: win rate > 80% - check lookahead/fills/duplicates")
    if eq:
        if eq.get("sharpe") and eq["sharpe"] == eq["sharpe"] and eq["sharpe"] > 3:
            flags.append("SUSPICIOUS: Sharpe > 3 - assume a bug until proven otherwise")
        if eq.get("max_drawdown") is not None and eq.get("n_days", 0) > 60 and eq["max_drawdown"] > -0.02:
            flags.append("SUSPICIOUS: almost no drawdown")
        if eq.get("total_return", 0) > 5:
            flags.append("SUSPICIOUS: >500% total return")
    return flags
