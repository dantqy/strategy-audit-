"""MOCK tests: US trading calendar, timezones, earnings timing -> reaction session."""
import unittest
from datetime import date, datetime

from fixtures import ROOT  # noqa: F401  (sets sys.path)
from scanner import market_calendar as mc
from scanner.earnings import EarningsEvent

SGT = __import__("zoneinfo").ZoneInfo("Asia/Singapore")


class TestCalendar(unittest.TestCase):
    def test_holidays(self):
        for d in [date(2025, 7, 4), date(2026, 4, 3), date(2025, 1, 9), date(2026, 6, 19),
                  date(2027, 6, 18), date(2026, 11, 26), date(2026, 12, 25), date(2026, 1, 19),
                  date(2022, 12, 26), date(2021, 12, 24)]:
            self.assertFalse(mc.is_trading_day(d), d)

    def test_saturday_new_year_no_friday_closure(self):
        self.assertTrue(mc.is_trading_day(date(2021, 12, 31)))

    def test_weekend(self):
        self.assertFalse(mc.is_trading_day(date(2026, 9, 26)))
        self.assertEqual(mc.next_trading_day(date(2026, 9, 25)), date(2026, 9, 28))
        self.assertEqual(mc.prev_trading_day(date(2026, 9, 28)), date(2026, 9, 25))

    def test_early_close(self):
        o, c = mc.session_bounds(date(2026, 11, 27))
        self.assertEqual(c.hour, 13)


class TestReactionSession(unittest.TestCase):
    def test_after_market_monday_reacts_tuesday(self):
        self.assertEqual(mc.session_for_event(date(2026, 9, 21), "AFTER"), date(2026, 9, 22))

    def test_pre_market_tuesday_reacts_tuesday(self):
        self.assertEqual(mc.session_for_event(date(2026, 9, 22), "BEFORE"), date(2026, 9, 22))

    def test_after_market_friday_reacts_monday(self):
        self.assertEqual(mc.session_for_event(date(2026, 9, 25), "AFTER"), date(2026, 9, 28))

    def test_weekend_announcement(self):
        self.assertEqual(mc.session_for_event(date(2026, 9, 26), "BEFORE"), date(2026, 9, 28))

    def test_after_market_before_holiday(self):
        # Thu 2 Jul 2026 after close; Fri 3 Jul 2026 = Independence Day observed -> Mon 6 Jul
        self.assertEqual(mc.session_for_event(date(2026, 7, 2), "AFTER"), date(2026, 7, 6))

    def test_pre_market_on_holiday(self):
        self.assertEqual(mc.session_for_event(date(2026, 4, 3), "BEFORE"), date(2026, 4, 6))

    def test_unknown_is_not_guessed(self):
        self.assertIsNone(mc.session_for_event(date(2026, 9, 22), "UNKNOWN"))
        ev = EarningsEvent("US.X", date(2026, 9, 22), "UNKNOWN", "t")
        self.assertIsNone(ev.reaction_session("exclude"))
        self.assertEqual(ev.reaction_session("assume_after"), date(2026, 9, 23))

    def test_earliest_entry_is_after_reaction(self):
        ev = EarningsEvent("US.X", date(2026, 9, 21), "AFTER", "t")
        self.assertEqual(ev.reaction_session(), date(2026, 9, 22))
        self.assertEqual(ev.earliest_entry_session(), date(2026, 9, 23))


class TestSessionsUsingSingaporeClock(unittest.TestCase):
    """'Today' must be judged by the US exchange calendar, not Singapore's date."""

    def test_sgt_tuesday_morning_is_us_monday_evening(self):
        now = datetime(2026, 9, 22, 8, 0, tzinfo=SGT)   # = Mon 21 Sep 20:00 ET
        self.assertEqual(mc.market_phase(now), "POST_MARKET")
        self.assertEqual(mc.last_completed_session(now), date(2026, 9, 21))

    def test_sgt_late_night_is_us_intraday(self):
        now = datetime(2026, 9, 22, 23, 0, tzinfo=SGT)  # = Tue 22 Sep 11:00 ET
        self.assertEqual(mc.market_phase(now), "OPEN")
        self.assertEqual(mc.last_completed_session(now), date(2026, 9, 21))

    def test_sgt_saturday_morning(self):
        now = datetime(2026, 9, 26, 9, 0, tzinfo=SGT)   # = Fri 25 Sep 21:00 ET
        self.assertEqual(mc.last_completed_session(now), date(2026, 9, 25))

    def test_us_holiday(self):
        now = datetime(2026, 7, 3, 12, 0, tzinfo=mc.ET)
        self.assertEqual(mc.market_phase(now), "CLOSED")
        self.assertEqual(mc.last_completed_session(now), date(2026, 7, 2))

    def test_naive_datetime_rejected(self):
        with self.assertRaises(ValueError):
            mc.to_et(datetime(2026, 9, 22, 10, 0))

    def test_previous_one_and_two_sessions(self):
        now = datetime(2026, 9, 28, 20, 0, tzinfo=mc.ET)  # Monday evening
        last = mc.last_completed_session(now)
        self.assertEqual(last, date(2026, 9, 28))
        self.assertEqual(mc.prev_trading_day(last), date(2026, 9, 25))  # weekend skipped


if __name__ == "__main__":
    unittest.main()
