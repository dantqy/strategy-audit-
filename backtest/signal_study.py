"""Standalone event study of the scanner's non-earnings signals.

The scanner flags VOLUME_ANOMALY and MOMENTUM_BREAKOUT on any day, but the
earnings backtest only measures them as add-ons to earnings reactions. This
module measures them on their own, on NON-earnings days, with the same timing
contract as engine.py:

* Signal on bar S uses only bars <= S (indicators exclude the current bar).
* Entry = OPEN of the next session (S+1). Exit for horizon h per
  backtest.exit_at (see engine.exit_rule). Data gaps make that horizon NaN.
* Overlap: consecutive days often re-trigger the same signal. Counting every
  day would inflate n with near-duplicate trades, so per horizon a new trade of
  the same symbol+signal is only taken after the previous one has exited.

Signals are computed vectorised; tests assert they match scanner/signals.py
bar by bar.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from scanner import market_calendar as mc
from scanner.indicators import add_daily_indicators
from scanner.signals import BEARISH, BULLISH, MOMENTUM_BREAKOUT, VOLUME_ANOMALY

from .engine import cost_adjusted, exit_rule
from .metrics import trade_stats

log = logging.getLogger(__name__)

SIGNALS = (VOLUME_ANOMALY, MOMENTUM_BREAKOUT)


def vectorized_signals(ind: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Direction of each signal per bar (BULLISH / BEARISH / None).

    Mirrors signals.volume_anomaly and signals.momentum_breakout with the
    bar's own daily rvol (rvol vs the prior 20 sessions).
    """
    s = cfg["signals"]
    va, mb = s["volume_anomaly"], s["momentum_breakout"]
    c, rv, rsi = ind["close"], ind["rvol"], ind["rsi"]

    mid = (ind["high"] + ind["low"]) / 2
    ret, sma5 = ind["ret_1d_pct"], ind["sma5_prior"]
    fin = ret.notna() & sma5.notna()
    conf_bull = fin & (ret > 0) & (c >= mid) & (c > sma5)
    conf_bear = fin & (ret < 0) & (c <= mid) & (c < sma5)
    va_ok = rv.notna() & (rv >= va["min_relative_volume"])
    va_dir = _directions(va_ok & conf_bull, va_ok & conf_bear)

    mb_ok = rv.notna() & (rv >= mb["min_relative_volume"])
    up = pd.Series(False, index=ind.index)
    dn = pd.Series(False, index=ind.index)
    for n in mb["lookbacks"]:
        up |= ind[f"prior_high_{n}"].notna() & (c > ind[f"prior_high_{n}"])
        dn |= ind[f"prior_low_{n}"].notna() & (c < ind[f"prior_low_{n}"])
    cons_ok = ind["cons_width_pct"].notna() & (ind["cons_width_pct"] <= mb["consolidation_max_range_pct"])
    bull_cond = up | (cons_ok & (c > ind["cons_top"]))
    bear_cond = dn | (cons_ok & (c < ind["cons_bottom"]))
    bull_block = rsi.notna() & (rsi >= mb["rsi_max_bullish"])
    bear_block = rsi.notna() & (rsi <= 100 - mb["rsi_max_bullish"])
    # signals.py: a bullish breakout blocked by RSI does NOT fall through to the bearish check
    mb_dir = _directions(mb_ok & bull_cond & ~bull_block, mb_ok & ~bull_cond & bear_cond & ~bear_block)
    # explicit object dtype: pandas 3 would otherwise infer a string dtype and turn None into NaN
    return pd.DataFrame({VOLUME_ANOMALY: pd.Series(va_dir, index=ind.index, dtype=object),
                         MOMENTUM_BREAKOUT: pd.Series(mb_dir, index=ind.index, dtype=object)})


def _directions(bull: pd.Series, bear: pd.Series) -> np.ndarray:
    out = np.full(len(bull), None, dtype=object)
    out[bull.to_numpy()] = BULLISH
    out[bear.to_numpy()] = BEARISH
    return out


def earnings_exclusion_dates(events: pd.DataFrame) -> dict[str, set]:
    """Per code: announcement day, its next session, and the reaction session (if known)."""
    out: dict[str, set] = {}
    if events is None or events.empty:
        return out
    for _, e in events.iterrows():
        d = out.setdefault(e["code"], set())
        ed = e["event_date"]
        d.add(ed)
        d.add(mc.next_trading_day(ed))
        R = e.get("reaction_session")
        if R is not None and not pd.isna(R):
            d.add(R)
    return out


def build_signal_table(prices: dict[str, pd.DataFrame], events: pd.DataFrame, cfg: dict,
                       spy: pd.DataFrame | None = None, start=None) -> tuple[pd.DataFrame, dict]:
    """One row per (code, signal day, signal) with a direction; returns per horizon.

    `start`: first allowed signal date (bars before it are indicator warm-up only).
    """
    horizons = cfg["backtest"]["horizons"]
    st = cfg["backtest"].get("standalone", {})
    excl = earnings_exclusion_dates(events) if st.get("exclude_earnings_window", True) else {}
    stats = {"signal_days": 0, "excluded_earnings_window": 0, "no_entry_bar": 0}
    off, exit_field = exit_rule(cfg)
    frames = []
    for code, df in prices.items():
        if len(df) < 60:
            continue
        ind = add_daily_indicators(df, cfg)
        sig = vectorized_signals(ind, cfg)
        dates = list(ind.index)
        # trading-day ordinal of every bar, to detect gaps inside a holding window
        cal = {d: k for k, d in enumerate(mc.trading_days(dates[0], mc.next_trading_day(dates[-1])))}
        ordn = np.array([cal[d] for d in dates])
        opn, xpx = ind["open"].to_numpy(), ind[exit_field].to_numpy()
        n = len(dates)
        for name in SIGNALS:
            idx = np.flatnonzero(pd.notna(sig[name].to_numpy()))
            for i in idx:
                S = dates[i]
                if start is not None and S < start:
                    continue
                stats["signal_days"] += 1
                if S in excl.get(code, ()):
                    stats["excluded_earnings_window"] += 1
                    continue
                if i + 1 >= n or ordn[i + 1] != ordn[i] + 1:
                    stats["no_entry_bar"] += 1
                    continue
                E = dates[i + 1]
                assert E > S, "entry must be strictly after the signal bar"
                rec = {"code": code, "signal": name, "signal_date": S, "entry_date": E,
                       "direction": sig[name].iloc[i], "relative_volume": float(ind["rvol"].iloc[i]),
                       "rsi_14": float(ind["rsi"].iloc[i]) if pd.notna(ind["rsi"].iloc[i]) else np.nan,
                       "entry_open": float(opn[i + 1])}
                for h in horizons:
                    j = i + h + off
                    if j < n and ordn[j] == ordn[i + 1] + h - 1 + off:
                        g = xpx[j] / opn[i + 1] - 1
                        X = dates[j]
                        rec[f"exit_date_{h}"], rec[f"ret_{h}"], rec[f"net_ret_{h}"] = X, g, cost_adjusted(g, cfg)
                        rec[f"spy_ret_{h}"] = (float(spy.loc[X, exit_field] / spy.loc[E, "open"] - 1)
                                               if spy is not None and E in spy.index and X in spy.index else np.nan)
                    else:
                        rec[f"exit_date_{h}"] = None
                        rec[f"ret_{h}"] = rec[f"net_ret_{h}"] = rec[f"spy_ret_{h}"] = np.nan
                frames.append(rec)
    t = pd.DataFrame(frames)
    if len(t):
        t = t.sort_values(["signal_date", "code", "signal"]).reset_index(drop=True)
    return t, stats


def non_overlapping(t: pd.DataFrame, h: int) -> pd.DataFrame:
    """Per code+signal, keep a trade only if it enters after the previous kept trade exited (horizon h)."""
    t = t[t[f"exit_date_{h}"].notna()]
    if t.empty:
        return t
    keep = []
    for _, g in t.sort_values("entry_date").groupby(["code", "signal"], sort=False):
        last_exit = None
        for ix, r in g.iterrows():
            if last_exit is None or r["entry_date"] > last_exit:
                keep.append(ix)
                last_exit = r[f"exit_date_{h}"]
    return t.loc[sorted(keep)]


def study_report(t: pd.DataFrame, cfg: dict, n_boot: int | None = None) -> pd.DataFrame:
    """Long-form: signal x direction x horizon x measure, on non-overlapping trades."""
    bt = cfg["backtest"]
    n_boot = n_boot or bt["bootstrap_resamples"]
    rows = []
    if t.empty:
        return pd.DataFrame()
    for h in bt["horizons"]:
        nov = non_overlapping(t, h)
        for name in SIGNALS:
            for direction in (BULLISH, BEARISH):
                sub = nov[(nov["signal"] == name) & (nov["direction"] == direction)] if len(nov) else nov
                sign = 1.0 if direction == BULLISH else -1.0
                excess = (sub[f"ret_{h}"] - sub[f"spy_ret_{h}"]) if len(sub) else pd.Series(dtype=float)
                for mname, series in (("long_gross", sub.get(f"ret_{h}", pd.Series(dtype=float))),
                                      ("long_net", sub.get(f"net_ret_{h}", pd.Series(dtype=float))),
                                      ("continuation_gross", sub.get(f"ret_{h}", pd.Series(dtype=float)) * sign),
                                      ("long_excess_vs_spy", excess)):
                    s = trade_stats(series, sub["signal_date"] if len(sub) else None,
                                    bt["min_sample_size"], n_boot, bt["random_seed"])
                    rows.append({"signal": name, "direction": direction, "horizon": h, "measure": mname, **s})
    return pd.DataFrame(rows)


def format_study(rep: pd.DataFrame, cfg: dict) -> list[str]:
    if rep.empty:
        return ["(no standalone signals)"]
    lines = ["| signal | direction | horizon | n | win% (net) | mean net (long) | 95% CI net | "
             "mean excess vs SPY | continuation gross | flag |",
             "|---|---|---|---|---|---|---|---|---|---|"]

    def pct(x):
        return "n/a" if x is None or x != x else f"{x*100:+.2f}%"
    for (sig, d, h), g in rep.groupby(["signal", "direction", "horizon"], sort=False):
        m = {r["measure"]: r for _, r in g.iterrows()}
        net, exc, cont = m["long_net"], m["long_excess_vs_spy"], m["continuation_gross"]
        if not net["n"]:
            continue
        lines.append(f"| {sig} | {d} | {h} | {net['n']} | {net.get('win_rate', float('nan'))*100:.0f}% | "
                     f"{pct(net.get('mean'))} | {pct(net.get('mean_ci95_low'))} .. {pct(net.get('mean_ci95_high'))} | "
                     f"{pct(exc.get('mean'))} | {pct(cont.get('mean'))} | "
                     f"{'LOW SAMPLE SIZE' if net['low_sample_size'] else ''} |")
    return lines
