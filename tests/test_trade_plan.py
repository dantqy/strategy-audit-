"""MOCK tests: paper-trade plan rules match the backtest's timing (signal at close, entry next open, exit after h)."""
import unittest
from datetime import date, datetime

from fixtures import FakeTradeCtx, cfg, use_fake_moomoo
from scanner import market_calendar as mc
from scanner import paper_execution as pe
from scanner.risk import PaperLedger
from scanner.trade_plan import check_entry_window, entry_limit, exit_status, planned_exit_session


def et(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=mc.ET)


R = date(2026, 9, 23)   # Wed: signal bar
E = date(2026, 9, 24)   # Thu: entry session


def cand(freshness="LAST_COMPLETED_BAR", bar=R, signals=("EARNINGS_DRIFT",), reaction=None):
    return {"ticker": "US.ABC", "data_freshness": freshness, "bar_date": str(bar), "current_price": 100.0,
            "signals_triggered": list(signals), "reaction_session": str(reaction or bar)}


SCAN = {"last_completed_session": str(R)}


class TestEntryWindow(unittest.TestCase):
    def test_post_close_scan_before_next_open_ok(self):
        for now in (et(2026, 9, 23, 16, 30), et(2026, 9, 24, 3, 0), et(2026, 9, 24, 9, 29)):
            c = check_entry_window(cand(), SCAN, now)
            self.assertTrue(c.ok, now)
            self.assertEqual(c.entry_session, E)

    def test_intraday_candidate_refused(self):
        """Today's real case: META flagged from a partial bar at 10:45 ET."""
        c = check_entry_window(cand("LIVE_INTRADAY", E), {"last_completed_session": str(R)}, et(2026, 9, 24, 10, 45))
        self.assertFalse(c.ok)
        self.assertIn("LAST_COMPLETED_BAR", c.reason)

    def test_after_entry_open_refused(self):
        c = check_entry_window(cand(), SCAN, et(2026, 9, 24, 9, 31))
        self.assertFalse(c.ok)
        self.assertIn("already opened", c.reason)

    def test_stale_scan_refused(self):
        c = check_entry_window(cand(), SCAN, et(2026, 9, 24, 16, 30))   # Thu closed since the scan
        self.assertFalse(c.ok)
        self.assertIn("stale", c.reason)

    def test_only_backtested_signals_may_buy(self):
        ok = check_entry_window(cand(), SCAN, et(2026, 9, 23, 20, 0), ["EARNINGS_DRIFT"])
        self.assertTrue(ok.ok)
        vol = check_entry_window(cand(signals=("VOLUME_ANOMALY", "MOMENTUM_BREAKOUT")), SCAN,
                                 et(2026, 9, 23, 20, 0), ["EARNINGS_DRIFT"])
        self.assertFalse(vol.ok)
        self.assertIn("information only", vol.reason)

    def test_earnings_reaction_must_be_latest_session(self):
        """Scanner lists reactions from 2 sessions; the backtest only bought right after the reaction."""
        old = check_entry_window(cand(reaction=date(2026, 9, 22)), SCAN, et(2026, 9, 23, 20, 0), ["EARNINGS_DRIFT"])
        self.assertFalse(old.ok)
        self.assertIn("already passed", old.reason)

    def test_friday_signal_enters_monday(self):
        fri = date(2026, 9, 25)
        c = check_entry_window(cand(bar=fri), {"last_completed_session": str(fri)}, et(2026, 9, 27, 12, 0))
        self.assertTrue(c.ok)
        self.assertEqual(c.entry_session, date(2026, 9, 28))


class TestLimitsAndExits(unittest.TestCase):
    def test_entry_limit(self):
        self.assertEqual(entry_limit(100.0, 5.0, "BUY"), 105.0)
        self.assertEqual(entry_limit(100.0, 5.0, "SELL"), 95.0)

    def test_planned_exit_matches_backtest_horizon(self):
        self.assertEqual(planned_exit_session(E, 1), E)                       # h=1: entry day's close
        self.assertEqual(planned_exit_session(E, 3), date(2026, 9, 28))       # Thu, Fri, Mon
        self.assertEqual(planned_exit_session(date(2026, 11, 25), 2), date(2026, 11, 27))  # skips Thanksgiving

    def test_planned_exit_next_open(self):
        self.assertEqual(planned_exit_session(E, 1, "next_open"), date(2026, 9, 25))   # open after entry day
        self.assertEqual(planned_exit_session(E, 3, "next_open"), date(2026, 9, 29))   # Thu,Fri,Mon -> Tue open

    def test_exit_status_next_open(self):
        plan = {"planned_exit": "2026-09-29", "exit_at": "next_open"}
        self.assertEqual(exit_status(plan, et(2026, 9, 28, 12)), "UPCOMING")    # Mon session still running
        self.assertEqual(exit_status(plan, et(2026, 9, 28, 16, 30)), "DUE_NOW")  # Mon closed: place sell for Tue open
        self.assertEqual(exit_status(plan, et(2026, 9, 29, 9, 0)), "DUE_NOW")
        self.assertEqual(exit_status(plan, et(2026, 9, 29, 9, 31)), "OVERDUE")

    def test_exit_status(self):
        plan = {"planned_exit": "2026-09-28"}
        self.assertEqual(exit_status(plan, et(2026, 9, 25, 12)), "UPCOMING")
        self.assertEqual(exit_status(plan, et(2026, 9, 28, 10)), "DUE_TODAY")
        self.assertEqual(exit_status(plan, et(2026, 9, 28, 16, 30)), "OVERDUE")
        self.assertEqual(exit_status(None, et(2026, 9, 28, 10)), "NO_PLAN")


class TestPlanFlowsIntoLedger(unittest.TestCase):
    def test_plan_on_ticket_is_shown_approved_and_stored_on_fill(self):
        c = cfg()
        ledger = PaperLedger(c)
        t = pe.OrderTicket(code="US.ABC", side="BUY", qty=1, limit_price=105.0, reason="r", signals=(),
                           remaining_cash_after=190.0, entry_session=str(E), planned_exit="2026-09-28", horizon=3)
        self.assertIn("Planned exit         : close of 2026-09-28", t.render())
        t_open = pe.OrderTicket(**{**t.__dict__, "exit_at": "next_open"})
        self.assertIn("open of 2026-09-28", t_open.render())
        self.assertEqual(t_open.plan()["exit_at"], "next_open")
        t2 = pe.OrderTicket(**{**t.__dict__, "planned_exit": "2026-09-30"})
        self.assertNotEqual(t.fingerprint(), t2.fingerprint())                # the plan is part of what was approved
        with use_fake_moomoo():
            ctx = FakeTradeCtx()
            a = pe.request_human_approval(t, input_fn=lambda _: "YES", print_fn=lambda _: None)
            pe.submit_paper_order(ctx, t, a, ledger, c)
            self.assertTrue(ledger.has_exposure("US.ABC"))                     # pending counts as exposure
            ctx.orders["OID1"] = {"order_status": "FILLED_ALL", "dealt_qty": 1, "dealt_avg_price": 101.0}
            pe.sync_pending_orders(ctx, ledger)
        self.assertEqual(ledger.position_plan("US.ABC")["planned_exit"], "2026-09-28")


if __name__ == "__main__":
    unittest.main()
