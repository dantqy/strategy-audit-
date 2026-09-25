"""Automated lookahead audit. PASS only if every check passes; otherwise WARNING with the failing checks.

Checks run against the actual data and the actual trades of this backtest (deterministic samples):
1. Truncation: every indicator the strategy uses has the same value on day d whether computed from the
   full history or from history truncated at d (future bars cannot leak into rolling windows).
2. Signal reproducibility: for sampled trades, the entry signal is TRUE when recomputed from data up to
   the signal day only (stock and SPY both truncated).
3. Execution timing: every entry is the OPEN of the next US session after the signal.
4. Holding period: fixed-hold exits occur on the planned session; no exit precedes its entry.
5. Benchmark alignment: SPY values used by the strategy come from the same calendar date.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from scanner import market_calendar as mc

from ..schema import StrategySpec
from .engine import Market, indicator_series, signal_mask


def _same(a, b) -> bool:
    if pd.isna(a) and pd.isna(b):
        return True
    return bool(np.isclose(a, b, rtol=1e-9, atol=1e-12))


def lookahead_audit(spec: StrategySpec, mkt: Market, led: pd.DataFrame) -> dict:
    checks = []
    rng = np.random.default_rng(123)
    codes = sorted(mkt.arr)[:: max(1, len(mkt.arr) // 6)][:6]
    inds = set()
    for c in spec.entry_conditions:
        inds.add((c.indicator, c.period))
        if c.comparison_indicator:
            inds.add((c.comparison_indicator, c.comparison_period))

    # 1 truncation
    bad = []
    for code in codes:
        b = mkt.bars[code]
        idx = [d for d in b.index if d >= mkt.test_start]
        for d in [idx[int(x)] for x in rng.integers(0, len(idx), 5)]:
            bt, st = b.loc[:d], mkt.spy.loc[:d]
            for ind, p in inds:
                full = indicator_series(ind, p, b, mkt.spy).loc[d]
                trunc = indicator_series(ind, p, bt, st).loc[d]
                if not _same(full, trunc):
                    bad.append(f"{code} {ind}({p}) on {d}")
    checks.append({"check": "Indicators use only data available at the signal close",
                   "passed": not bad, "detail": f"{len(codes) * 5 * len(inds)} indicator values recomputed on truncated history"
                   + (f"; mismatches: {bad[:3]}" if bad else "")})

    # 2 signal reproducibility from past data only
    sample = led.sample(min(20, len(led)), random_state=7) if len(led) else led
    bad = []
    for _, t in sample.iterrows():
        b = mkt.bars[t["ticker"]].loc[:t["signal_date"]]
        s = mkt.spy.loc[:t["signal_date"]]
        if not bool(signal_mask(spec, b, s).iloc[-1]):
            bad.append(f"{t['ticker']} {t['signal_date']}")
    checks.append({"check": "Each signal is reproducible from data up to the signal day only",
                   "passed": not bad, "detail": f"{len(sample)} trades re-derived" + (f"; failed: {bad[:3]}" if bad else "")})

    # 3 execution timing
    bad = [f"{r.ticker} {r.signal_date}" for r in led.itertuples()
           if not (r.entry_date > r.signal_date and r.entry_date == mc.next_trading_day(r.signal_date))]
    checks.append({"check": "Every entry is at the next session's open after the signal",
                   "passed": not bad, "detail": f"{len(led)} trades checked" + (f"; failed: {bad[:3]}" if bad else "")})

    # 4 holding period
    h = spec.exit.trading_days
    bad = [f"{r.ticker} {r.entry_date}" for r in led.itertuples()
           if r.exit_date < r.entry_date or (r.exit_reason == "HOLD" and r.sessions_held != h)
           or (r.exit_reason == "HOLD" and len(mc.trading_days(r.entry_date, r.exit_date)) != h)]
    checks.append({"check": f"Fixed-hold exits occur on the {h}th session (entry day = 1)",
                   "passed": not bad, "detail": f"{len(led)} trades checked" + (f"; failed: {bad[:3]}" if bad else "")})

    # 5 benchmark alignment
    bad = []
    b = mkt.bars[codes[0]]
    s_al = indicator_series("SPY_CLOSE", None, b, mkt.spy)
    for d in [b.index[int(x)] for x in rng.integers(200, len(b.index), 10)]:
        if d in mkt.spy.index and not _same(s_al.loc[d], mkt.spy.loc[d, "close"]):
            bad.append(str(d))
    checks.append({"check": "Benchmark (SPY) data is aligned by calendar date", "passed": not bad,
                   "detail": "10 dates compared" + (f"; misaligned: {bad[:3]}" if bad else "")})

    ok = all(c["passed"] for c in checks)
    return {"status": "PASS" if ok else "WARNING",
            "summary": "All automated lookahead checks passed." if ok else
            "Some automated lookahead checks failed: results may use information not available at the time.",
            "checks": checks}
