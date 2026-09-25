"""MOCK tests: indicators and lookahead guards."""
import unittest
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from fixtures import bars, cfg
from backtest.engine import build_reaction_table
from scanner import market_calendar as mc
from scanner import indicators as ind


def wilder_rsi_last(close, n=14):
    d = np.diff(np.asarray(close, float))
    g, l = np.clip(d, 0, None), np.clip(-d, 0, None)
    ag, al = g[:n].mean(), l[:n].mean()
    for i in range(n, len(d)):
        ag = (ag * (n - 1) + g[i]) / n
        al = (al * (n - 1) + l[i]) / n
    return 100 - 100 / (1 + ag / al)


class TestIndicators(unittest.TestCase):
    def test_rsi_bounds_and_trend(self):
        up = pd.Series(np.arange(1, 40, dtype=float))
        self.assertAlmostEqual(ind.rsi(up).iloc[-1], 100.0)
        down = pd.Series(np.arange(40, 1, -1, dtype=float))
        self.assertAlmostEqual(ind.rsi(down).iloc[-1], 0.0)
        flat = pd.Series([10.0] * 30)
        self.assertAlmostEqual(ind.rsi(flat).iloc[-1], 50.0)

    def test_rsi_converges_to_wilder(self):
        # ewm seeding differs from the SMA seed only early on; after ~100 bars they agree
        c = bars(date(2024, 1, 2), 300, seed=3)["close"]
        ours = ind.rsi(c).iloc[-1]
        ref = wilder_rsi_last(c.values)
        self.assertAlmostEqual(ours, ref, places=3)

    def test_relative_volume_excludes_current_bar(self):
        v = pd.Series([100.0] * 20 + [300.0])
        self.assertAlmostEqual(ind.relative_volume(v, 20).iloc[-1], 3.0)

    def test_prior_high_excludes_current_bar_20(self):
        h = pd.Series(list(range(1, 21)) + [100.0])
        ph = ind.prior_high(h, 20)
        self.assertEqual(ph.iloc[-1], 20.0)          # not 100 (the current bar)
        self.assertTrue(100.0 > ph.iloc[-1])         # so a breakout is detectable

    def test_prior_high_50(self):
        h = pd.Series([10.0] * 49 + [50.0] + [40.0])
        self.assertEqual(ind.prior_high(h, 50).iloc[-1], 50.0)
        self.assertTrue(np.isnan(ind.prior_high(h, 50).iloc[48]))

    def test_same_time_rvol(self):
        rows = []
        base = date(2026, 9, 14)
        days = [d for d in (base + timedelta(i) for i in range(12)) if mc.is_trading_day(d)][:6]
        for k, d in enumerate(days):
            for m in range(1, 61):  # first hour only
                ts = datetime(d.year, d.month, d.day, 9, 30, tzinfo=mc.ET) + timedelta(minutes=m)
                rows.append({"ts_et": ts, "volume": 300.0 if k == len(days) - 1 else 100.0})
        df = pd.DataFrame(rows)
        now = datetime(days[-1].year, days[-1].month, days[-1].day, 10, 0, tzinfo=mc.ET)
        rv, n = ind.same_time_relative_volume(df, now)
        self.assertEqual(n, 5)
        self.assertAlmostEqual(rv, 3.0)


class TestLookahead(unittest.TestCase):
    def setUp(self):
        self.c = cfg()
        self.b = bars(date(2025, 1, 2), 120, seed=1)

    def _event(self, d, timing):
        return pd.DataFrame([{"code": "US.T", "event_date": d, "timing": timing,
                              "reaction_session": mc.session_for_event(d, timing)}])

    def test_next_bar_entry(self):
        R = self.b.index[80]
        c = {**self.c, "backtest": {**self.c["backtest"], "exit_at": "close"}}
        t, _ = build_reaction_table({"US.T": self.b}, self._event(R, "BEFORE"), c)
        row = t.iloc[0]
        self.assertEqual(row["reaction_session"], R)
        self.assertEqual(row["entry_date"], self.b.index[81])
        self.assertAlmostEqual(row["entry_open"], self.b.iloc[81]["open"])
        self.assertAlmostEqual(row["ret_1"], self.b.iloc[81]["close"] / self.b.iloc[81]["open"] - 1)

    def test_next_open_exit(self):
        """exit_at=next_open: h=1 exits at the OPEN of the session after entry; h=3 at the open after 3 sessions."""
        R = self.b.index[80]
        c = {**self.c, "backtest": {**self.c["backtest"], "exit_at": "next_open"}}
        row = build_reaction_table({"US.T": self.b}, self._event(R, "BEFORE"), c)[0].iloc[0]
        entry = self.b.iloc[81]["open"]
        self.assertEqual(row["exit_date_1"], self.b.index[82])
        self.assertAlmostEqual(row["ret_1"], self.b.iloc[82]["open"] / entry - 1)
        self.assertEqual(row["exit_date_3"], self.b.index[84])
        self.assertAlmostEqual(row["ret_3"], self.b.iloc[84]["open"] / entry - 1)

    def test_after_market_move_measured_on_next_session(self):
        b = self.b.copy()
        D = b.index[80]
        # Big move on D itself (BEFORE the after-close news) must NOT be attributed to the earnings
        b.loc[D, "close"] = b.iloc[79]["close"] * 1.10
        b.loc[D, "high"] = b.loc[D, "close"]
        t, _ = build_reaction_table({"US.T": b}, self._event(D, "AFTER"), self.c)
        row = t.iloc[0]
        self.assertEqual(row["reaction_session"], b.index[81])
        self.assertEqual(row["entry_date"], b.index[82])
        expected = (b.iloc[81]["close"] / b.iloc[80]["close"] - 1) * 100
        self.assertAlmostEqual(row["reaction_move_pct"], expected)

    def test_post_market_earnings_not_tradable_before_publication(self):
        D = self.b.index[80]
        t, _ = build_reaction_table({"US.T": self.b}, self._event(D, "AFTER"), self.c)
        self.assertGreater(t.iloc[0]["entry_date"], D)
        self.assertGreater(t.iloc[0]["entry_date"], t.iloc[0]["reaction_session"])

    def test_breakout_flag_excludes_reaction_bar_itself(self):
        b = self.b.copy()
        R = b.index[80]
        b.loc[R, "high"] = b.iloc[:81]["high"].max() * 1.5   # huge high but close below prior high
        b.loc[R, "close"] = b.iloc[60:80]["high"].max() * 0.99
        t, _ = build_reaction_table({"US.T": b}, self._event(R, "BEFORE"), self.c)
        self.assertFalse(t.iloc[0]["breakout_20"])

    def test_missing_entry_bar_skipped_not_filled(self):
        b = self.b.drop(self.b.index[81])
        R = self.b.index[80]
        t, skips = build_reaction_table({"US.T": b}, self._event(R, "BEFORE"), self.c)
        self.assertTrue(t.empty)
        self.assertEqual(skips["entry_bar_missing"], 1)


if __name__ == "__main__":
    unittest.main()
