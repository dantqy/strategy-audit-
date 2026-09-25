"""MOCK tests: Strategy 3 feature timing (no lookahead), classification, random baseline, robustness maths."""
import unittest
from datetime import date

import numpy as np
import pandas as pd

from fixtures import bars
from strategy3 import study as S
from strategy3.features import HORIZONS, build_events, classify, forward_frame, spy_features, stock_features


def shock(b, i, ret=0.08, vol_mult=3.0, gap=0.05):
    """Plant a shock on bar i: open gaps by `gap`, close = prev close * (1+ret), volume x vol_mult."""
    b = b.copy()
    pc = b.iloc[i - 1]["close"]
    o, c = pc * (1 + gap), pc * (1 + ret)
    b.iloc[i, b.columns.get_loc("open")] = o
    b.iloc[i, b.columns.get_loc("close")] = c
    b.iloc[i, b.columns.get_loc("high")] = max(o, c) * 1.001
    b.iloc[i, b.columns.get_loc("low")] = min(o, c) * 0.999
    b.iloc[i, b.columns.get_loc("volume")] = b.iloc[i - 20:i]["volume"].mean() * vol_mult
    return b


class TestTiming(unittest.TestCase):
    def setUp(self):
        self.spy = bars(date(2018, 1, 2), 700, price=300, seed=99)
        self.b = shock(bars(date(2018, 1, 2), 700, seed=1), 400)
        self.f = stock_features(self.b, spy_features(self.spy))
        self.T = self.b.index[400]

    def test_relative_volume_excludes_event_day(self):
        prev20 = self.b.iloc[380:400]["volume"].mean()
        self.assertAlmostEqual(self.f.loc[self.T, "avg_volume_prev20"], prev20)
        self.assertAlmostEqual(self.f.loc[self.T, "relative_volume"], 3.0, places=6)

    def test_baselines_use_only_prior_bars(self):
        """Changing the event day's own bar must not change its 'normal' volatility / ATR / prior returns."""
        b2 = self.b.copy()
        b2.iloc[400, b2.columns.get_loc("high")] *= 1.5
        f2 = stock_features(b2, spy_features(self.spy))
        for col in ("atr14_prev", "vol20_prev", "avg_volume_prev20", "return_20d_prior", "return_60d_prior"):
            self.assertAlmostEqual(self.f.loc[self.T, col], f2.loc[self.T, col], msg=col)

    def test_future_bars_do_not_change_event_features(self):
        b2 = self.b.copy()
        b2.iloc[401:] = b2.iloc[401:] * 1.7
        f2 = stock_features(b2, spy_features(self.spy))
        cols = [c for c in self.f.columns if not c.startswith(("forward_", "exit_date_", "entry_"))]
        pd.testing.assert_series_equal(self.f.loc[self.T, cols], f2.loc[self.T, cols], check_names=False)

    def test_forward_returns_from_next_open(self):
        for h in HORIZONS:
            exp = self.b.iloc[400 + h]["close"] / self.b.iloc[401]["open"] - 1
            self.assertAlmostEqual(self.f.loc[self.T, f"forward_{h}d"], exp)
        self.assertEqual(self.f.loc[self.T, "entry_date"], self.b.index[401])

    def test_missing_candle_makes_forward_nan_not_filled(self):
        b = self.b.drop(self.b.index[403])
        fw = forward_frame(b)
        T = self.b.index[400]
        self.assertFalse(np.isnan(fw.loc[T, "forward_2d"]))
        for h in (3, 5, 10, 20):
            self.assertTrue(np.isnan(fw.loc[T, f"forward_{h}d"]), h)

    def test_event_detected_with_spy_excess(self):
        ev, _ = build_events({"US.X": self.b}, self.spy, start=date(2018, 1, 1), end=date(2030, 1, 1))
        e = ev[ev["event_date"] == self.T].iloc[0]
        self.assertEqual(e["event_direction"], "POSITIVE_SHOCK")
        self.assertEqual(e["event_type"], "GAP_AND_GO")
        self.assertAlmostEqual(e["excess_forward_5d"], e["forward_5d"] - e["SPY_forward_5d"])


class TestClassify(unittest.TestCase):
    def test_gap_classes_both_directions(self):
        rows = pd.DataFrame([
            {"daily_return": 0.06, "gap_return": 0.03, "intraday_return": 0.03},    # gap & go
            {"daily_return": 0.05, "gap_return": 0.08, "intraday_return": -0.03},   # gap & fade
            {"daily_return": 0.05, "gap_return": -0.01, "intraday_return": 0.06},   # intraday
            {"daily_return": -0.06, "gap_return": -0.03, "intraday_return": -0.03},  # gap down & go
            {"daily_return": -0.05, "gap_return": -0.08, "intraday_return": 0.03},  # gap down & recover
            {"daily_return": -0.05, "gap_return": 0.01, "intraday_return": -0.06}])  # intraday selloff
        for k in ("close", "SMA50", "SMA200", "relative_strength_60d"):
            rows[k] = 1.0
        rows["event_date"] = pd.Timestamp("2020-01-02").date()
        c = classify(rows)
        self.assertEqual(list(c["event_type"]), ["GAP_AND_GO", "GAP_AND_FADE", "INTRADAY_BREAKOUT", "GAP_DOWN_AND_GO",
                                                 "GAP_DOWN_AND_RECOVER", "INTRADAY_SELLOFF"])
        self.assertEqual(list(c["gap_class"]), ["GAP_AND_GO", "GAP_AND_FADE", "INTRADAY_MOVE"] * 2)


class TestStudy(unittest.TestCase):
    def test_hypothesis_count_is_fixed(self):
        self.assertEqual(len(S.hypotheses()), 34)

    def test_random_baseline_matches_stock_year_and_excludes_event_days(self):
        spy = bars(date(2018, 1, 2), 700, price=300, seed=99)
        b = shock(bars(date(2018, 1, 2), 700, seed=2), 400)
        ev, feats = build_events({"US.X": b}, spy, start=date(2019, 1, 1), end=date(2020, 6, 30))
        ev = S.add_random_baseline(ev, feats, date(2019, 1, 1), date(2020, 6, 30))
        self.assertTrue(ev["random_forward_5d"].notna().all())
        self.assertTrue((ev["random_pool_size"] > 20).all())

    def test_trade_return_signs(self):
        ev = pd.DataFrame({"event_direction": ["POSITIVE_SHOCK", "NEGATIVE_SHOCK"], "forward_5d": [0.02, -0.03],
                           "random_forward_5d": [0.01, 0.0], "event_date": [date(2020, 1, 2)] * 2})
        allm = lambda e: e["event_direction"].notna()   # noqa: E731
        cont = S.trade_returns(ev, allm, 5, "CONTINUATION")
        self.assertEqual(list(cont["trade_ret"].round(6)), [0.02, 0.03])        # neg shock continuing = short gain
        rev = S.trade_returns(ev, allm, 5, "REVERSAL")
        self.assertEqual(list(rev["trade_ret"].round(6)), [-0.02, -0.03])
        self.assertEqual(list(cont["trade_vs_random"].round(6)), [0.01, 0.03])

    def test_outlier_table_drops_best(self):
        e = pd.DataFrame({"trade_ret": [0.5] + [0.0] * 40, "event_date": pd.date_range("2020-01-01", periods=41).date})
        t = S.outlier_table(e).set_index("variant")
        self.assertGreater(t.loc["all events", "mean"], 0.01)
        self.assertAlmostEqual(t.loc["excl. best 1", "mean"], 0.0)

    def test_selection_freezes_one_horizon_per_hypothesis(self):
        t = pd.DataFrame({"hypothesis": ["H", "H", "G"], "direction": ["POSITIVE_SHOCK"] * 2 + ["NEGATIVE_SHOCK"],
                          "horizon": [5, 10, 3], "significant": [True, True, True], "vs_random": [0.01, 0.02, 0.01],
                          "vs_random_t": [2.1, 3.5, 2.5]})
        f = S.select_in_sample(t).set_index("hypothesis")
        self.assertEqual(int(f.loc["H", "horizon"]), 10)
        self.assertEqual(f.loc["H", "effect"], "CONTINUATION")
        self.assertTrue(f.loc["H", "long_tradable"])
        self.assertFalse(f.loc["G", "long_tradable"])       # negative-shock continuation = short


if __name__ == "__main__":
    unittest.main()
