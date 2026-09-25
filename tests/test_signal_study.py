"""MOCK tests: standalone VOLUME_ANOMALY / MOMENTUM_BREAKOUT study on SYNTHETIC data.

Synthetic results here prove the machinery, not the strategy.
"""
import unittest
from datetime import date

import numpy as np
import pandas as pd

from fixtures import bars, cfg
from backtest.signal_study import (build_signal_table, non_overlapping, study_report,
                                   vectorized_signals)
from scanner import market_calendar as mc
from scanner.indicators import add_daily_indicators
from scanner.signals import MOMENTUM_BREAKOUT, VOLUME_ANOMALY, momentum_breakout, volume_anomaly


def spiky_bars(n=400, seed=0):
    """Random walk with frequent volume spikes and big moves so both signals fire often."""
    b = bars(date(2022, 1, 3), n, seed=seed, sigma=0.02)
    rng = np.random.default_rng(seed + 100)
    for i in rng.choice(np.arange(30, n), n // 8, replace=False):
        b.iloc[i, b.columns.get_loc("volume")] *= rng.uniform(1.6, 4.0)
        mv = rng.choice([-1, 1]) * rng.uniform(0.03, 0.09)
        c = b.iloc[i - 1]["close"] * (1 + mv)
        b.iloc[i, b.columns.get_loc("close")] = c
        b.iloc[i, b.columns.get_loc("high")] = max(b.iloc[i]["high"], c * 1.002)
        b.iloc[i, b.columns.get_loc("low")] = min(b.iloc[i]["low"], c * 0.998)
    return b


class TestVectorizedMatchesScanner(unittest.TestCase):
    def test_bar_by_bar_equivalence(self):
        c = cfg()
        fired = {VOLUME_ANOMALY: 0, MOMENTUM_BREAKOUT: 0}
        for seed in range(4):
            ind = add_daily_indicators(spiky_bars(seed=seed), c)
            vec = vectorized_signals(ind, c)
            for i in range(len(ind)):
                va, mb = volume_anomaly(ind, i, c), momentum_breakout(ind, i, c)
                exp_va = va.direction if va.triggered else None
                exp_mb = mb.direction if mb.triggered else None
                self.assertEqual(vec[VOLUME_ANOMALY].iloc[i], exp_va, f"seed {seed} bar {i} VA")
                self.assertEqual(vec[MOMENTUM_BREAKOUT].iloc[i], exp_mb, f"seed {seed} bar {i} MB")
                fired[VOLUME_ANOMALY] += exp_va is not None
                fired[MOMENTUM_BREAKOUT] += exp_mb is not None
        self.assertGreater(fired[VOLUME_ANOMALY], 50)       # the test actually exercised both signals
        self.assertGreater(fired[MOMENTUM_BREAKOUT], 50)

    def test_no_lookahead(self):
        """Changing bars AFTER S must not change the signal on S."""
        c = cfg()
        b = spiky_bars(seed=7)
        base = vectorized_signals(add_daily_indicators(b, c), c)
        k = 250
        b2 = b.copy()
        b2.iloc[k + 1:] = b2.iloc[k + 1:] * 3.0
        after = vectorized_signals(add_daily_indicators(b2, c), c)
        pd.testing.assert_frame_equal(base.iloc[:k + 1], after.iloc[:k + 1])


class TestSignalTable(unittest.TestCase):
    def setUp(self):
        self.c = cfg()
        self.b = spiky_bars(seed=3)
        self.prices = {"US.A": self.b}

    def test_entry_next_open_exit_close_and_costs(self):
        self.c["backtest"]["exit_at"] = "close"
        t, _ = build_signal_table(self.prices, pd.DataFrame(), self.c)
        self.assertGreater(len(t), 20)
        r = t.iloc[len(t) // 2]
        i = self.b.index.get_loc(r["signal_date"])
        self.assertEqual(r["entry_date"], self.b.index[i + 1])
        self.assertEqual(r["entry_date"], mc.next_trading_day(r["signal_date"]))
        self.assertAlmostEqual(r["ret_5"], self.b.iloc[i + 5]["close"] / self.b.iloc[i + 1]["open"] - 1)
        self.assertLess(r["net_ret_5"], r["ret_5"])

    def test_next_open_exit(self):
        self.c["backtest"]["exit_at"] = "next_open"
        t, _ = build_signal_table(self.prices, pd.DataFrame(), self.c)
        r = t.iloc[len(t) // 2]
        i = self.b.index.get_loc(r["signal_date"])
        self.assertEqual(r["exit_date_5"], self.b.index[i + 6])
        self.assertAlmostEqual(r["ret_5"], self.b.iloc[i + 6]["open"] / self.b.iloc[i + 1]["open"] - 1)

    def test_data_gap_makes_horizon_nan_not_wrong(self):
        b = self.b.drop(self.b.index[300])                  # missing candle inside some holding windows
        t, _ = build_signal_table({"US.A": b}, pd.DataFrame(), self.c)
        gap = self.b.index[300]
        for _, r in t.iterrows():
            for h in self.c["backtest"]["horizons"]:
                x = r[f"exit_date_{h}"]
                if x is not None and not pd.isna(x):
                    self.assertFalse(r["entry_date"] <= gap <= x, f"window over the gap for h={h}")

    def test_recent_signal_keeps_short_horizons(self):
        t, _ = build_signal_table(self.prices, pd.DataFrame(), self.c)
        late = t[t["signal_date"] >= self.b.index[-10]]
        if len(late):
            self.assertTrue(late["exit_date_60"].isna().all())

    def test_earnings_window_excluded(self):
        t, _ = build_signal_table(self.prices, pd.DataFrame(), self.c)
        S = t.iloc[0]["signal_date"]
        ev = pd.DataFrame([{"code": "US.A", "event_date": mc.prev_trading_day(S), "timing": "AFTER",
                            "reaction_session": S}])
        t2, stats = build_signal_table(self.prices, ev, self.c)
        self.assertNotIn(S, set(t2["signal_date"]))
        self.assertGreaterEqual(stats["excluded_earnings_window"], 1)

    def test_start_date_respected(self):
        start = self.b.index[200]
        t, _ = build_signal_table(self.prices, pd.DataFrame(), self.c, start=start)
        self.assertTrue((t["signal_date"] >= start).all())


class TestOverlap(unittest.TestCase):
    def test_non_overlapping(self):
        d = mc.trading_days(date(2024, 1, 2), date(2024, 3, 1))
        rows = []
        for k in (0, 1, 2, 10):                             # signals on days 0,1,2 overlap at h=5; day 10 doesn't
            rows.append({"code": "US.A", "signal": VOLUME_ANOMALY, "signal_date": d[k], "entry_date": d[k + 1],
                         "exit_date_5": d[k + 5]})
        rows.append({"code": "US.B", "signal": VOLUME_ANOMALY, "signal_date": d[1], "entry_date": d[2],
                     "exit_date_5": d[6]})                  # other symbol: independent
        t = pd.DataFrame(rows)
        kept = non_overlapping(t, 5)
        self.assertEqual(list(kept[kept["code"] == "US.A"]["signal_date"]), [d[0], d[10]])
        self.assertEqual(len(kept[kept["code"] == "US.B"]), 1)

    def test_report_random_walk_shows_no_edge(self):
        c = cfg()
        prices = {f"US.S{k}": spiky_bars(n=500, seed=20 + k) for k in range(6)}
        t, _ = build_signal_table(prices, pd.DataFrame(), c)
        rep = study_report(t, c, n_boot=300)
        g = rep[(rep["measure"] == "continuation_gross") & (rep["horizon"] == 1) & (rep["n"] > 30)]
        self.assertGreater(len(g), 0)
        self.assertTrue((g["mean"].abs() < 0.01).all())


if __name__ == "__main__":
    unittest.main()
