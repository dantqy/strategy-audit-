"""MOCK tests: scanner classification/ranking and backtest mechanics on SYNTHETIC data.

Synthetic results here prove the machinery, not the strategy.
"""
import tempfile
import unittest
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from fixtures import bars, cfg
from backtest.engine import Params, apply_params, build_reaction_table, cost_adjusted, simulate_account
from backtest.metrics import trade_stats
from backtest.runner import run_backtest
from backtest.validation import time_split
from scanner import market_calendar as mc
from scanner.ranking import rank
from scanner.scanner import evaluate_symbol, format_terminal


def plant_reaction(b, R, move=0.07, rvol=3.0, then=0.0):
    b = b.copy()
    i = b.index.get_loc(R)
    prev = b.iloc[i - 1]["close"]
    b.iloc[i, b.columns.get_loc("close")] = prev * (1 + move)
    b.iloc[i, b.columns.get_loc("high")] = max(b.iloc[i]["high"], prev * (1 + move) * 1.001)
    b.iloc[i, b.columns.get_loc("low")] = min(b.iloc[i]["low"], prev * (1 + move * 0.2))
    b.iloc[i, b.columns.get_loc("volume")] = b.iloc[i - 20:i]["volume"].mean() * rvol
    if then:
        for k in (1, 2, 3):
            j = i + k
            if j < len(b):
                o = b.iloc[j - 1]["close"]
                b.iloc[j, b.columns.get_loc("open")] = o
                b.iloc[j, b.columns.get_loc("close")] = o * (1 + then)
                b.iloc[j, b.columns.get_loc("high")] = max(o, o * (1 + then)) * 1.001
                b.iloc[j, b.columns.get_loc("low")] = min(o, o * (1 + then)) * 0.999
    return b


class TestScanner(unittest.TestCase):
    def test_post_market_scan_finds_after_close_earnings(self):
        c = cfg()
        b = bars(date(2026, 4, 1), 120, seed=5)
        R = b.index[-1]                         # reaction session = last completed session
        D = mc.prev_trading_day(R)              # announced after close the day before
        b = plant_reaction(b, R, move=0.08, rvol=3.2)
        now = datetime.combine(R, datetime.min.time(), mc.ET).replace(hour=20)  # 8pm ET, post-market
        ev = pd.DataFrame([{"code": "US.T", "event_date": D, "timing": "AFTER", "reaction_session": R}])
        cand = evaluate_symbol("US.T", b, c, now, ev)
        self.assertIsNotNone(cand)
        self.assertIn("EARNINGS_DRIFT", cand["signals_triggered"])
        self.assertEqual(cand["data_freshness"], "LAST_COMPLETED_BAR")
        self.assertEqual(cand["signal_direction"], "BULLISH")
        self.assertAlmostEqual(cand["reaction_move_pct"], 8.0, places=1)
        out = rank([cand], c)
        self.assertGreaterEqual(out[0]["score"], 3)
        self.assertIn("US.T", format_terminal({"scan_time": mc.timestamps(now), "market_phase": "POST_MARKET",
                                                "last_completed_session": str(R), "universe_size": 1,
                                                "earnings_events_in_window": 1, "errors": [], "candidates": out}))

    def test_event_older_than_two_sessions_ignored(self):
        c = cfg()
        b = bars(date(2026, 4, 1), 120, seed=5)
        R = b.index[-4]
        b = plant_reaction(b, R)
        now = datetime.combine(b.index[-1], datetime.min.time(), mc.ET).replace(hour=20)
        ev = pd.DataFrame([{"code": "US.T", "event_date": R, "timing": "BEFORE", "reaction_session": R}])
        cand = evaluate_symbol("US.T", b, c, now, ev)
        self.assertTrue(cand is None or "EARNINGS_DRIFT" not in cand["signals_triggered"])

    def test_live_intraday_uses_time_normalised_volume(self):
        c = cfg()
        b = bars(date(2026, 4, 1), 120, seed=7)
        today = mc.next_trading_day(b.index[-1])
        now = datetime.combine(today, datetime.min.time(), mc.ET).replace(hour=11)
        last = b.iloc[-1]["close"]
        snap = pd.Series({"code": "US.T", "name": "Test Co", "update_time_et": pd.Timestamp(now - timedelta(minutes=1)),
                          "open_price": last * 1.01, "high_price": last * 1.09, "low_price": last * 1.005,
                          "last_price": last * 1.085, "volume": b["volume"].mean() * 1.2, "prev_close_price": last})
        cand = evaluate_symbol("US.T", b, c, now, pd.DataFrame(columns=["code", "reaction_session"]), snap)
        self.assertIsNotNone(cand)
        self.assertEqual(cand["data_freshness"], "LIVE_INTRADAY")
        self.assertTrue(cand["rvol_method"].startswith("APPROX"))   # labelled, not presented as exact
        # 1.2x full-day volume by 11:00 is ~5x the time-normalised pace
        self.assertGreater(cand["relative_volume"], 3.0)

    def test_stale_quote_flagged(self):
        c = cfg()
        b = bars(date(2026, 4, 1), 120, seed=7)
        b = plant_reaction(b, b.index[-1], move=0.06, rvol=3)
        today = mc.next_trading_day(b.index[-1])
        now = datetime.combine(today, datetime.min.time(), mc.ET).replace(hour=11)
        snap = pd.Series({"update_time_et": pd.Timestamp(now - timedelta(hours=2)), "last_price": 1.0})
        ev = pd.DataFrame([{"code": "US.T", "event_date": b.index[-1], "timing": "BEFORE", "reaction_session": b.index[-1]}])
        cand = evaluate_symbol("US.T", b, c, now, ev, snap)
        self.assertEqual(cand["data_freshness"], "LAST_CLOSE")
        self.assertTrue(any("stale" in l for l, _ in rank([cand], c)[0]["score_breakdown"]))


def synthetic_universe(n_stocks=30, n_days=900, n_events=8, cont=0.0, seed=0, noise_spikes=0):
    """`noise_spikes`: extra non-earnings volume/price shocks per stock (standalone-signal fodder)."""
    prices, events = {}, []
    rng = np.random.default_rng(seed)
    for k in range(n_stocks):
        b = bars(date(2021, 1, 4), n_days, price=float(rng.uniform(20, 120)), seed=seed * 1000 + k)
        for i in rng.choice(np.arange(60, n_days - 5), noise_spikes, replace=False):
            b = plant_reaction(b, b.index[i], move=float(rng.choice([-1, 1]) * rng.uniform(0.03, 0.06)),
                               rvol=float(rng.uniform(2.2, 3.5)))
        idxs = sorted(rng.choice(np.arange(60, n_days - 5), n_events, replace=False))
        for i in idxs:
            R = b.index[i]
            mv = float(rng.choice([-1, 1]) * rng.uniform(0.055, 0.12))
            b = plant_reaction(b, R, move=mv, rvol=float(rng.uniform(2.2, 4)), then=np.sign(mv) * cont)
            events.append({"code": f"US.S{k}", "event_date": R, "timing": "BEFORE", "reaction_session": R})
        prices[f"US.S{k}"] = b
    spy = bars(date(2021, 1, 4), n_days, price=400, seed=999)
    return prices, spy, pd.DataFrame(events)


class TestBacktest(unittest.TestCase):
    def test_planted_continuation_is_recovered(self):
        c = cfg()
        prices, spy, ev = synthetic_universe(cont=0.01, seed=1)
        t, _ = build_reaction_table(prices, ev, c, spy)
        sig = apply_params(t, Params(5, 2, 20), c)
        cont = np.where(sig["direction"] == "BULLISH", 1, -1) * sig["ret_1"]
        st = trade_stats(cont, sig["reaction_session"])
        self.assertGreater(st["n"], 100)
        self.assertGreater(st["mean_ci95_low"], 0)

    def test_random_walk_shows_no_edge(self):
        c = cfg()
        prices, spy, ev = synthetic_universe(cont=0.0, seed=2)
        t, _ = build_reaction_table(prices, ev, c, spy)
        sig = apply_params(t, Params(5, 2, 20), c)
        st = trade_stats(np.where(sig["direction"] == "BULLISH", 1, -1) * sig["ret_1"], sig["reaction_session"])
        self.assertLess(abs(st["mean"]), 0.004)
        self.assertLess(st["win_rate"], 0.62)

    def test_costs_reduce_returns(self):
        c = cfg()
        self.assertLess(cost_adjusted(0.01, c), 0.01)
        self.assertLess(cost_adjusted(0.0, c), -0.01)   # $0.99 x2 on $150 ~ 1.3%: costs matter at $300

    def test_time_split_is_chronological(self):
        c = cfg()
        prices, spy, ev = synthetic_universe(n_stocks=5, seed=3)
        t, _ = build_reaction_table(prices, ev, c, spy)
        a, b, cut = time_split(t, 0.7)
        self.assertLess(a["reaction_session"].max(), b["reaction_session"].min())
        self.assertAlmostEqual(len(a) / len(t), 0.7, delta=0.1)

    def test_account_whole_shares_and_not_executable(self):
        c = cfg()
        prices, spy, ev = synthetic_universe(n_stocks=3, seed=4)
        prices["US.S0"] = prices["US.S0"] * [10, 10, 10, 10, 1]   # ~$200-1200/share names
        t, _ = build_reaction_table(prices, ev, c, spy)
        sig = apply_params(t, Params(5, 2, 20), c)
        curve, trades, info = simulate_account(sig, prices, c, 1)
        self.assertTrue(len(curve) > 0)
        if len(trades):
            self.assertTrue((trades["qty"] == trades["qty"].round()).all())
        self.assertGreaterEqual(info["not_executable"], 0)
        self.assertTrue((curve["cash"] >= -1e-6).all())       # no leverage

    def test_account_sizes_with_real_prices_not_split_adjusted(self):
        """CMG case: adjusted history shows ~$60, but a share really cost ~$3,000 before the split."""
        c = cfg()
        prices, spy, ev = synthetic_universe(n_stocks=3, seed=4)
        t, _ = build_reaction_table(prices, ev, c, spy)
        sig = apply_params(t, Params(5, 2, 20), c)
        sig = sig.assign(direction="BULLISH")
        pre_split = {k: v * 50 for k, v in prices.items()}           # real prices 50x the adjusted ones
        _, trades, info = simulate_account(sig, prices, c, 1, raw_prices=pre_split)
        self.assertEqual(len(trades), 0)
        self.assertEqual(info["skipped_share_too_expensive"], info["not_executable"])
        self.assertGreater(info["not_executable"], 0)
        _, tr_adj, info_adj = simulate_account(sig, prices, c, 1)    # adjusted sizing would have "bought" them
        self.assertGreater(len(tr_adj), 0)
        self.assertIn("ADJUSTED", info_adj["sizing"])
        # affordable real price: share count from the REAL price, P&L identical in value terms
        half = {k: v * 0.5 for k, v in prices.items()}               # real = half the adjusted price
        _, tr_half, info_half = simulate_account(sig, prices, c, 1, raw_prices=half)
        self.assertIn("real", info_half["sizing"])
        r = tr_half.iloc[0]
        self.assertAlmostEqual(r["shares"] * r["entry_real"], r["qty"] * r["entry"], places=6)
        self.assertEqual(r["shares"], int(r["shares"]))

    def test_runner_end_to_end_and_oos_lock(self):
        c = cfg()
        prices, spy, ev = synthetic_universe(n_stocks=8, seed=5, cont=0.0, noise_spikes=25)
        tmp = tempfile.mkdtemp()
        import backtest.validation as v
        v.EXPERIMENT_LOG = f"{tmp}/experiment_log.csv"
        v.OOS_LOG = f"{tmp}/oos.csv"
        import backtest.runner as r
        r.append_experiment_log = v.append_experiment_log
        res = run_backtest(prices, spy, ev, c, tmp, final_oos=False, notes="synthetic MOCK")
        with open(res["summary_path"], encoding="utf-8") as f:
            text = f.read()
        self.assertIn("LOCKED", text)
        self.assertIn("IN SAMPLE", text)
        self.assertIn("STANDALONE SIGNALS (IN SAMPLE)", text)
        self.assertNotIn("STANDALONE SIGNALS (OUT OF SAMPLE)", text)   # OOS stays locked for standalone too
        log = pd.read_csv(v.EXPERIMENT_LOG)
        self.assertGreaterEqual(len(log), 27)                # every sensitivity cell logged
        res2 = run_backtest(prices, spy, ev, c, tmp, final_oos=True)
        with open(res2["summary_path"], encoding="utf-8") as f:
            text2 = f.read()
        self.assertIn("OUT OF SAMPLE", text2)
        self.assertIn("STANDALONE SIGNALS (OUT OF SAMPLE)", text2)
        st = pd.read_csv(f"{res2['out_dir']}/standalone_signals_all.csv", parse_dates=["signal_date"])
        cut = pd.Timestamp(res2["split_date"])
        is_rep = pd.read_csv(f"{res2['out_dir']}/standalone_report_in_sample.csv")
        oos_rep = pd.read_csv(f"{res2['out_dir']}/standalone_report_out_of_sample.csv")
        self.assertGreater(is_rep["n"].sum(), 0)
        self.assertGreater(oos_rep["n"].sum(), 0)
        self.assertTrue((st["signal_date"] < cut).any() and (st["signal_date"] >= cut).any())
        self.assertNotEqual(res["out_dir"], res2["out_dir"])
        c2 = cfg()
        c2["signals"]["earnings_drift"]["min_abs_move_pct"] = 6.0
        res3 = run_backtest(prices, spy, ev, c2, tmp, final_oos=True)
        self.assertTrue(any("DIFFERENT settings" in w for w in res3["oos_warnings"]))


if __name__ == "__main__":
    unittest.main()
