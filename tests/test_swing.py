"""MOCK tests: swing screener filters, plan maths and phase handling on synthetic snapshot rows."""
import unittest
from datetime import datetime

import pandas as pd

from fixtures import cfg
from scanner import market_calendar as mc
from scanner.swing import format_swing, screen

OPEN_NOW = datetime(2026, 9, 24, 12, 0, tzinfo=mc.ET)      # regular session
PRE_NOW = datetime(2026, 9, 24, 8, 0, tzinfo=mc.ET)        # pre-market


def row(code, last=20.0, prev=18.0, low=18.5, high=20.2, rv=3.0, cap=2e9, turnover=60e6, **kw):
    r = {"code": code, "name": code, "last_price": last, "prev_close_price": prev, "open_price": prev,
         "high_price": high, "low_price": low, "volume": 1e6, "turnover": turnover, "volume_ratio": rv,
         "highest52weeks_price": 40.0, "lowest52weeks_price": 10.0, "pre_price": 0.0, "pre_turnover": 0.0,
         "total_market_val": cap}
    r.update(kw)
    return r


class TestScreen(unittest.TestCase):
    def setUp(self):
        self.c = cfg()

    def run_rows(self, rows, now=OPEN_NOW, earnings=None):
        return screen(pd.DataFrame(rows), self.c, now, earnings)

    def test_filters(self):
        rows = [row("US.OK"),
                row("US.PENNY", last=2.0, prev=1.8, low=1.8, high=2.1),     # below min price
                row("US.PRICEY", last=400, prev=370, low=372, high=401),    # above max price
                row("US.TINY", cap=1e8),                                    # micro-cap
                row("US.QUIET", rv=1.2),                                    # normal volume
                row("US.FLAT", last=18.3),                                  # moved < 4%
                row("US.DOWN", last=16.0, low=15.8, high=18.1),             # falling: long-only hides it
                row("US.ILLIQ", turnover=1e6)]                              # thin trading
        df = self.run_rows(rows)
        self.assertEqual(list(df["code"]), ["US.OK"])

    def test_down_moves_shown_when_not_long_only(self):
        self.c["swing"]["long_only"] = False
        df = self.run_rows([row("US.DOWN", last=16.0, low=15.8, high=18.1)])
        self.assertEqual(list(df["code"]), ["US.DOWN"])

    def test_plan_maths(self):
        r = self.run_rows([row("US.OK", last=20.0, low=19.0)]).iloc[0]
        self.assertEqual(r["shares"], 15)                        # floor(300 / 20)
        self.assertAlmostEqual(r["stop"], 19.0)                   # today's low (5% below)
        self.assertAlmostEqual(r["risk_pct"], 5.0)
        self.assertAlmostEqual(r["risk_usd"], 15.0)
        self.assertAlmostEqual(r["fee_pct_round_trip"], 2 * 0.99 / 300 * 100)

    def test_stop_never_wider_than_max(self):
        r = self.run_rows([row("US.OK", last=20.0, low=15.0)]).iloc[0]
        self.assertAlmostEqual(r["stop"], 20.0 * 0.92)            # capped at -8%
        self.assertAlmostEqual(r["risk_pct"], 8.0)

    def test_tags_and_ranking(self):
        rows = [row("US.PLAIN"), row("US.EARN"), row("US.HIGH", last=39.5, prev=36.0, low=36.5, high=39.6)]
        df = self.run_rows(rows, earnings={"US.EARN": "2026-09-23 AFTER"})
        self.assertIn("earnings 2026-09-23 AFTER", df.set_index("code").at["US.EARN", "tags"])
        self.assertIn("near 52-week high", df.set_index("code").at["US.HIGH", "tags"])
        self.assertEqual(df.iloc[-1]["code"], "US.PLAIN")          # untagged ranks last

    def test_pre_market_uses_gap_and_needs_real_premarket_trading(self):
        rows = [row("US.GAP", last=18.0, prev=18.0, pre_price=19.5, pre_turnover=5e6, cap=3e9),
                row("US.NOTRADE", last=18.0, prev=18.0, pre_price=19.5, pre_turnover=1e4, cap=3e9),
                row("US.SMALL", last=18.0, prev=18.0, pre_price=19.5, pre_turnover=5e6, cap=5e8)]
        df = self.run_rows(rows, now=PRE_NOW)
        self.assertEqual(list(df["code"]), ["US.GAP"])
        r = df.iloc[0]
        self.assertAlmostEqual(r["move_pct"], (19.5 / 18 - 1) * 100)
        self.assertIn("pre-market gap", r["tags"])
        self.assertTrue(pd.isna(r["rel_vol"]))

    def test_format(self):
        df = self.run_rows([row("US.OK")])
        txt = format_swing(df, OPEN_NOW, "OPEN", 6000)
        self.assertIn("UNTESTED", txt)
        self.assertIn("OK $20.00", txt)
        self.assertIn("Nothing passed", format_swing(pd.DataFrame(), OPEN_NOW, "OPEN", 6000))


if __name__ == "__main__":
    unittest.main()
