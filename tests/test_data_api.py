"""MOCK tests: data quality, retries, pagination, caching, earnings parsing."""
import os
import tempfile
import unittest
from datetime import date, datetime

import pandas as pd

from fixtures import bars, cfg, fake_moomoo_module
from scanner import market_calendar as mc
from scanner.data_cache import DailyBarCache, JsonCache
from scanner.data_quality import normalize_daily
from scanner.earnings import load_events, parse_moomoo_calendar, reaction_metrics
from scanner.moomoo_client import (MoomooAPIError, MoomooClient, RateLimiter, call_with_retry,
                                   suspected_row_cap)
from scanner.signals import earnings_drift


def raw_kline(days, px=10.0):
    return pd.DataFrame({"code": "US.T", "time_key": [f"{d} 00:00:00" for d in days],
                         "open": px, "high": px + 1, "low": px - 1, "close": px, "volume": 1000})


class TestDataQuality(unittest.TestCase):
    def test_duplicates_dropped(self):
        df, rep = normalize_daily(raw_kline(["2026-09-21", "2026-09-21", "2026-09-22"]))
        self.assertEqual(len(df), 2)
        self.assertEqual(rep.duplicates_dropped, 1)

    def test_missing_candles_reported_not_filled(self):
        df, rep = normalize_daily(raw_kline(["2026-09-21", "2026-09-23"]))
        self.assertEqual(len(df), 2)
        self.assertEqual(rep.missing_sessions, [date(2026, 9, 22)])

    def test_weekend_rows_dropped(self):
        df, rep = normalize_daily(raw_kline(["2026-09-25", "2026-09-26", "2026-09-28"]))
        self.assertEqual(rep.non_trading_rows_dropped, 1)
        self.assertEqual(rep.missing_sessions, [])

    def test_empty_response(self):
        df, rep = normalize_daily(pd.DataFrame())
        self.assertTrue(df.empty)
        df2, _ = normalize_daily(None)
        self.assertTrue(df2.empty)

    def test_bad_rows_dropped(self):
        r = raw_kline(["2026-09-21", "2026-09-22"])
        r.loc[1, "close"] = None
        df, rep = normalize_daily(r)
        self.assertEqual(rep.bad_rows_dropped, 1)


class TestRetry(unittest.TestCase):
    def test_bounded_retries(self):
        calls = []

        def f():
            calls.append(1)
            return (-1, "request too frequent")
        with self.assertRaises(MoomooAPIError):
            call_with_retry(f, max_retries=3, sleep=lambda s: None)
        self.assertEqual(len(calls), 4)

    def test_fatal_not_retried(self):
        calls = []

        def f():
            calls.append(1)
            return (-1, "historical kline quota exceeded")
        with self.assertRaises(MoomooAPIError):
            call_with_retry(f, max_retries=5, sleep=lambda s: None)
        self.assertEqual(len(calls), 1)

    def test_recovers_after_disconnect(self):
        seq = iter([ConnectionError("disconnect"), (-1, "network timeout"), (0, "ok")])

        def f():
            x = next(seq)
            if isinstance(x, Exception):
                raise x
            return x
        self.assertEqual(call_with_retry(f, sleep=lambda s: None), (0, "ok"))

    def test_rate_limiter_sleeps(self):
        t = [0.0]
        slept = []
        rl = RateLimiter(2, 30, clock=lambda: t[0], sleep=lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)))
        for _ in range(3):
            rl.wait()
        self.assertEqual(len(slept), 1)


class FakeQuoteCtx:
    """Pages a kline response in two pages; counts calls."""

    def __init__(self, df):
        self.df, self.calls = df, 0

    def request_history_kline(self, code, start, end, ktype, autype, max_count, page_req_key):
        self.calls += 1
        d = self.df[(self.df["time_key"] >= start) & (self.df["time_key"] <= end + " 23:59:59")]
        half = len(d) // 2
        if page_req_key is None and half:
            return 0, d.iloc[:half], "PAGE2"
        return 0, d.iloc[half:] if page_req_key else d, None


class TestClient(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        b = bars(date(2026, 1, 2), 60)
        self.raw = b.reset_index().assign(time_key=lambda x: x["date"].astype(str) + " 00:00:00", code="US.T")
        self.ctx = FakeQuoteCtx(self.raw)
        self.client = MoomooClient(cfg(), quote_ctx=self.ctx, sdk=fake_moomoo_module())
        self.client.daily_cache = DailyBarCache(os.path.join(self.tmp, "daily"))
        self.client.limiter = RateLimiter(10_000)

    def test_pagination_and_cache(self):
        s, e = date(2026, 1, 2), self.raw["date"].iloc[-1]
        df = self.client.daily_bars("US.T", s, e)
        self.assertEqual(self.ctx.calls, 2)            # two pages
        self.assertEqual(len(df), 60)
        df2 = self.client.daily_bars("US.T", s, e)      # served from cache
        self.assertEqual(self.ctx.calls, 2)
        self.assertEqual(self.client.cache_hits, 1)
        pd.testing.assert_frame_equal(df, df2, check_freq=False, check_exact=False)


class FakeEarningsCtx:
    """get_earnings_calendar stub: `rows_per_day(date) -> int` rows for each queried day."""

    def __init__(self, rows_per_day):
        self.rows_per_day, self.calls = rows_per_day, []

    def get_earnings_calendar(self, market, sort_type=None, begin_date=None, end_date=None, filter_list=None):
        self.calls.append({"begin": begin_date, "end": end_date, "sort_type": sort_type})
        n = self.rows_per_day(date.fromisoformat(begin_date))
        return 0, pd.DataFrame([{"security": f"US.X{i}", "earnings_date": begin_date,
                                 "earnings_timestamp": None, "pub_type": "AFTER"} for i in range(n)])


class TestEarningsCalendarClient(unittest.TestCase):
    def _client(self, ctx):
        sdk = fake_moomoo_module()
        sdk.EarningsCalendarSortType = type("S", (), {"MARKET_CAP": "MARKET_CAP"})
        c = MoomooClient(cfg(), quote_ctx=ctx, sdk=sdk)
        c.earn_cache = JsonCache(os.path.join(tempfile.mkdtemp(), "earn"))
        c.limiter = RateLimiter(10_000)
        return c

    def test_one_request_per_day_sorted_by_market_cap_and_cached(self):
        ctx = FakeEarningsCtx(lambda d: 3)
        c = self._client(ctx)
        df = c.earnings_calendar(date(2025, 3, 3), date(2025, 3, 16))
        self.assertEqual(len(ctx.calls), 14)
        self.assertTrue(all(x["begin"] == x["end"] for x in ctx.calls))
        self.assertTrue(all(x["sort_type"] == "MARKET_CAP" for x in ctx.calls))
        self.assertEqual(len(df), 42)
        c.earnings_calendar(date(2025, 3, 3), date(2025, 3, 16))   # old days: permanent cache
        self.assertEqual(len(ctx.calls), 14)

    def test_row_cap_plateau_warns(self):
        ctx = FakeEarningsCtx(lambda d: 100 if d.day in (4, 5, 6) else d.day)
        c = self._client(ctx)
        with self.assertLogs("scanner.moomoo_client", level="WARNING") as lg:
            c.earnings_calendar(date(2025, 3, 3), date(2025, 3, 9))
        self.assertTrue(any("row cap" in m for m in lg.output))

    def test_suspected_row_cap(self):
        self.assertIsNone(suspected_row_cap({1: 30, 2: 41, 3: 55, 4: 12}))
        self.assertEqual(suspected_row_cap({1: 50, 2: 50, 3: 50, 4: 12}), 50)
        self.assertIsNone(suspected_row_cap({1: 5, 2: 5, 3: 5}))          # quiet days, not a cap
        self.assertIsNone(suspected_row_cap({}))


class TestEarnings(unittest.TestCase):
    def test_parse_moomoo_calendar_timing(self):
        ts_after = datetime(2026, 9, 21, 16, 5, tzinfo=mc.ET).timestamp()
        ts_before = datetime(2026, 9, 22, 7, 0, tzinfo=mc.ET).timestamp()
        df = pd.DataFrame([
            {"security": "US.AAA", "earnings_date": "2026-09-21", "earnings_timestamp": ts_after, "pub_type": "BEFORE"},
            {"security": "US.BBB", "earnings_date": "2026-09-22", "earnings_timestamp": ts_before, "pub_type": ""},
            {"security": "CCC", "earnings_date": "2026-09-22", "earnings_timestamp": None, "pub_type": "AFTER"},
            {"security": "US.DDD", "earnings_date": "2026-09-22", "earnings_timestamp": None, "pub_type": "weird"},
        ])
        ev = {e.code: e for e in parse_moomoo_calendar(df)}
        self.assertEqual(ev["US.AAA"].timing, "BEFORE")     # pub_type wins: live timestamps are approximate
        self.assertEqual(ev["US.BBB"].timing, "BEFORE")     # no pub_type: a real timestamp is the fallback
        self.assertEqual(ev["US.CCC"].timing, "AFTER")
        self.assertEqual(ev["US.DDD"].timing, "UNKNOWN")

    def test_regular_pub_type_is_unknown_not_during(self):
        """SDK proto: EarningsCalendarPubType_Regular = '盘中(未识别出时段)' = time slot NOT identified."""
        df = pd.DataFrame([{"security": "US.AAA", "earnings_date": "2026-09-22",
                            "earnings_timestamp": None, "pub_type": "REGULAR"}])
        self.assertEqual(parse_moomoo_calendar(df)[0].timing, "UNKNOWN")

    def test_live_rows_2026_09_24(self):
        """Rows as returned by LIVE OpenD on 2026-09-24 (earnings_timestamp in epoch seconds)."""
        df = pd.DataFrame([
            # BEFORE with the 09:30 placeholder: must stay BEFORE, not DURING
            {"security": "US.UNH", "earnings_date": "2025-07-29", "earnings_timestamp": 1753795800.0, "pub_type": "BEFORE"},
            {"security": "US.KBH", "earnings_date": "2026-09-22", "earnings_timestamp": 1790107200.0, "pub_type": "AFTER"},
            # REGULAR with wrong clock times (CVX/TXN actually report outside market hours)
            {"security": "US.CVX", "earnings_date": "2025-08-01", "earnings_timestamp": 1754060400.0, "pub_type": "REGULAR"},
            {"security": "US.TXN", "earnings_date": "2019-10-22", "earnings_timestamp": 1571770860.0, "pub_type": "REGULAR"},
        ])
        ev = {e.code: e for e in parse_moomoo_calendar(df)}
        self.assertEqual((ev["US.UNH"].timing, ev["US.UNH"].event_date), ("BEFORE", date(2025, 7, 29)))
        self.assertEqual((ev["US.KBH"].timing, ev["US.KBH"].reaction_session()), ("AFTER", date(2026, 9, 23)))
        self.assertEqual(ev["US.CVX"].timing, "UNKNOWN")
        self.assertEqual(ev["US.TXN"].timing, "UNKNOWN")
        self.assertIsNone(ev["US.CVX"].reaction_session())   # excluded under the default policy

    def test_earnings_date_kept_when_timestamp_date_differs(self):
        ts = datetime(2026, 9, 21, 16, 5, tzinfo=mc.ET).timestamp()
        df = pd.DataFrame([{"security": "US.AAA", "earnings_date": "2026-09-22",
                            "earnings_timestamp": ts, "pub_type": "AFTER"}])
        with self.assertLogs("scanner.earnings", level="WARNING"):
            e = parse_moomoo_calendar(df)[0]
        self.assertEqual(e.event_date, date(2026, 9, 22))

    def test_midnight_placeholder_timestamps_ignored(self):
        """00:00 Beijing (= 12:00 ET prev day) or 00:00 ET carry no time: fall back to pub_type/date."""
        from zoneinfo import ZoneInfo
        cn_midnight = datetime(2026, 9, 22, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        et_midnight = datetime(2026, 9, 22, 0, 0, tzinfo=mc.ET).timestamp()
        df = pd.DataFrame([
            {"security": "US.AAA", "earnings_date": "2026-09-22", "earnings_timestamp": cn_midnight, "pub_type": "AFTER"},
            {"security": "US.BBB", "earnings_date": "2026-09-22", "earnings_timestamp": et_midnight, "pub_type": "BEFORE"},
        ])
        ev = {e.code: e for e in parse_moomoo_calendar(df)}
        self.assertEqual((ev["US.AAA"].event_date, ev["US.AAA"].timing), (date(2026, 9, 22), "AFTER"))
        self.assertEqual((ev["US.BBB"].event_date, ev["US.BBB"].timing), (date(2026, 9, 22), "BEFORE"))

    def test_reaction_metrics_and_threshold(self):
        b = bars(date(2026, 1, 2), 40)
        R = b.index[30]
        b.loc[R, "close"] = b.iloc[29]["close"] * 1.06
        b.loc[R, "volume"] = b.iloc[10:30]["volume"].mean() * 2.5
        m = reaction_metrics(b, R)
        self.assertAlmostEqual(m["reaction_move_pct"], 6.0, places=6)
        self.assertAlmostEqual(m["relative_volume"], 2.5, places=6)
        c = cfg()
        self.assertTrue(earnings_drift(m, c).triggered)
        self.assertEqual(earnings_drift(m, c).direction, "BULLISH")
        m2 = {**m, "relative_volume": 1.9}
        self.assertFalse(earnings_drift(m2, c).triggered)

    def test_csv_provider(self):
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "e.csv")
        pd.DataFrame([{"code": "AAPL", "date": "2026-07-30", "timing": "AMC"}]).to_csv(p, index=False)
        c = cfg()
        c["earnings"]["providers"] = ["csv"]
        c["earnings"]["csv_path"] = p
        ev = load_events(c, date(2026, 7, 1), date(2026, 8, 30))
        self.assertEqual(ev.iloc[0]["code"], "US.AAPL")
        self.assertEqual(ev.iloc[0]["timing"], "AFTER")
        self.assertEqual(ev.iloc[0]["reaction_session"], date(2026, 7, 31))


if __name__ == "__main__":
    unittest.main()
