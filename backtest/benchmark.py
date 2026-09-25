"""Benchmarks: SPY over identical holding windows, beta/correlation, and the
unconditional universe baseline. Answers: is this more than market exposure?"""
from __future__ import annotations

import numpy as np
import pandas as pd


def spy_comparison(signals: pd.DataFrame, horizon: int) -> dict:
    r, s = signals.get(f"ret_{horizon}"), signals.get(f"spy_ret_{horizon}")
    if r is None or s is None:
        return {}
    d = pd.DataFrame({"r": r, "s": s}).dropna()
    if len(d) < 3:
        return {"n": len(d)}
    var = d["s"].var(ddof=1)
    beta = float(d["r"].cov(d["s"]) / var) if var > 0 else float("nan")
    alpha = float(d["r"].mean() - beta * d["s"].mean()) if beta == beta else float("nan")
    return {"n": int(len(d)), "mean_trade_ret": float(d["r"].mean()), "mean_spy_same_window": float(d["s"].mean()),
            "mean_excess_vs_spy": float((d["r"] - d["s"]).mean()), "beta_to_spy": beta,
            "corr_to_spy": float(d["r"].corr(d["s"])), "alpha_per_trade": alpha}


def portfolio_vs_spy(curve: pd.Series, spy: pd.DataFrame) -> dict:
    if curve is None or len(curve) < 20 or spy is None or spy.empty:
        return {}
    pr = curve.pct_change()
    sr = spy["close"].pct_change()
    d = pd.DataFrame({"p": pr, "s": sr}).dropna()
    d = d[d["p"] != 0]  # days with no exposure carry no information about beta
    if len(d) < 20:
        return {"n_days_exposed": len(d)}
    beta = float(d["p"].cov(d["s"]) / d["s"].var(ddof=1))
    spy_win = spy.loc[curve.index[0]:curve.index[-1], "close"]
    return {"n_days_exposed": int(len(d)), "beta_daily": beta, "corr_daily": float(d["p"].corr(d["s"])),
            "spy_total_return_same_period": float(spy_win.iloc[-1] / spy_win.iloc[0] - 1) if len(spy_win) > 1 else None}


def market_exposure_verdict(cmp: dict) -> str:
    if not cmp or "beta_to_spy" not in cmp:
        return "insufficient data for SPY comparison"
    b, ex = cmp["beta_to_spy"], cmp["mean_excess_vs_spy"]
    if b != b:
        return "beta undefined"
    if abs(ex) < 0.001 and b > 0.8:
        return "Behaves like (levered) market exposure: little excess over SPY."
    return f"beta {b:.2f}; mean excess vs SPY per trade {ex*100:+.2f}%"
