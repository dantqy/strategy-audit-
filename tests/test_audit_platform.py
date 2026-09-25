"""MOCK tests for the Strategy Audit Platform (synthetic MockProvider data; no Moomoo cache or network).

Covers: parsing, schema validation, backtest timing/costs/benchmark, lookahead, robustness tests,
reproducibility, research lineage + sealed validation, and API behaviour.
"""
import os
import tempfile
import unittest

import pandas as pd
from fastapi.testclient import TestClient

from scanner import market_calendar as mc
from strategy_audit import api as API
from strategy_audit import assess as A
from strategy_audit.db import DB
from strategy_audit.parser import apply_patches, interpret, validate_draft
from strategy_audit.quant import robustness as R
from strategy_audit.quant.data import MockProvider
from strategy_audit.quant.engine import Market, generate_trades, indicator_series, net_return, run_backtest
from strategy_audit.quant.lookahead import lookahead_audit
from strategy_audit.schema import StrategySpec
from strategy_audit.service import AuditService, NotFound

BASE = {"name": "Dip in uptrend", "universe": {"type": "LARGE_CAP_93"}, "direction": "LONG",
        "entry_conditions": [{"indicator": "RETURN", "period": 5, "operator": "<=", "value": -0.04},
                             {"indicator": "CLOSE", "operator": ">", "comparison_indicator": "SMA", "comparison_period": 50}],
        "entry_execution": "NEXT_OPEN", "exit": {"type": "FIXED_HOLD", "trading_days": 5}}


def spec(**over) -> StrategySpec:
    d = {**BASE, **over}
    return StrategySpec.model_validate(d)


MOCK = MockProvider(n_symbols=10, seed=11)
MKT = Market(MOCK, "LARGE_CAP_93")


# ------------------------------------------------------------------ parsing
class TestParser(unittest.TestCase):
    def test_supported_strategy_becomes_schema(self):
        r = interpret("Buy large-cap stocks when RSI(14) < 30 and close > SMA(200). Enter next open, hold 10 days, stop loss 8%.")
        self.assertEqual(r["status"], "ok")
        s = StrategySpec.model_validate(r["spec"])
        self.assertEqual([c.describe() for c in s.entry_conditions], ["RSI(14) < 30", "CLOSE > SMA(200)"])
        self.assertEqual((s.exit.trading_days, s.exit.stop_loss_pct), (10, 0.08))

    def test_ambiguous_requires_clarification_not_assumption(self):
        r = interpret("Buy strong stocks when above the 200-day moving average. Next open, hold 5 days, large cap.")
        self.assertEqual(r["status"], "needs_clarification")
        kinds = {i["message"].split('"')[1] for i in r["issues"] if i["kind"] == "ambiguous" and '"' in i["message"]}
        self.assertIn("Strong stock", kinds)
        self.assertIn("200-day moving average", kinds)
        self.assertIsNone(r["spec"])                              # nothing runs until the user chooses

    def test_unsupported_rejected(self):
        for t in ("Short TSLA when RSI above 70", "Buy bitcoin on Mondays", "Buy calls when volume spikes",
                  "Day trade SPY on 5-minute bars", "Buy undervalued stocks with low P/E"):
            self.assertEqual(interpret(t)["status"], "unsupported", t)

    def test_unknown_phrase_reported_not_dropped(self):
        r = interpret("Buy large cap when RSI(14) < 25 and the moon is full. Next open, hold 5 days.")
        self.assertTrue(any("moon" in u for u in r["unmatched"]))
        self.assertEqual(r["status"], "needs_clarification")

    def test_missing_period_asks(self):
        r = interpret("Buy large cap when RSI below 30. next open. hold 5 days.")
        self.assertTrue(any(i["kind"] == "missing" and "RSI period" in i["message"] for i in r["issues"]))

    def test_patches_are_whitelisted_and_revalidated(self):
        with self.assertRaises(ValueError):
            apply_patches({}, [{"op": "exec", "value": "import os"}])
        d = apply_patches({**BASE, "entry_conditions": []}, [{"op": "add_condition", "value": {"indicator": "RSI", "period": 500,
                                                                                               "operator": "<", "value": 30}}])
        spec_, errs = validate_draft(d)
        self.assertIsNone(spec_)
        self.assertTrue(errs)


# ------------------------------------------------------------------ schema
class TestSchema(unittest.TestCase):
    def test_invalid_specs_rejected(self):
        bad = [{"direction": "SHORT"}, {"entry_execution": "SAME_CLOSE"}, {"universe": {"type": "SP500"}},
               {"entry_conditions": []}, {"exit": {"type": "FIXED_HOLD", "trading_days": 0}}, {"leverage": 2},
               {"entry_conditions": [{"indicator": "RSI", "operator": "<", "value": 30}]},                   # no period
               {"entry_conditions": [{"indicator": "RSI", "period": 14, "operator": "<", "comparison_indicator": "SMA",
                                      "comparison_period": 50}]},                                            # RSI vs price
               {"entry_conditions": [{"indicator": "EVAL", "operator": "<", "value": 1}]},
               {"name": "<script>alert(1)</script>"}]
        for over in bad:
            with self.assertRaises(Exception, msg=str(over)):
                spec(**over)

    def test_hash_stable(self):
        self.assertEqual(spec().spec_hash(), spec().spec_hash())
        self.assertNotEqual(spec().spec_hash(), spec(exit={"type": "FIXED_HOLD", "trading_days": 6}).spec_hash())


# ------------------------------------------------------------------ backtest
class TestBacktest(unittest.TestCase):
    def test_next_open_entry_and_holding_period(self):
        s0, e0 = MKT.period("development")
        led, _ = generate_trades(spec(), MKT, s0, e0)
        self.assertGreater(len(led), 20)
        for r in led.itertuples():
            self.assertEqual(r.entry_date, mc.next_trading_day(r.signal_date))
            b = MOCK.bars(r.ticker)
            self.assertAlmostEqual(r.entry_price, b.loc[r.entry_date, "open"])
            self.assertEqual(len(mc.trading_days(r.entry_date, r.exit_date)), 5)
            self.assertAlmostEqual(r.exit_price, b.loc[r.exit_date, "close"])
            self.assertLessEqual(r.exit_date, e0)

    def test_fees_and_slippage(self):
        c = spec().costs
        self.assertAlmostEqual(net_return(0.0, c, 10_000), (1 - 0.001) / (1 + 0.001) - 1 - 2 * 0.99 / 10_000 - 0.3e-4)
        self.assertLess(net_return(0.02, c, 10_000), 0.02)

    def test_stop_loss_exits_next_open(self):
        s0, e0 = MKT.period("development")
        led, _ = generate_trades(spec(exit={"type": "FIXED_HOLD", "trading_days": 20, "stop_loss_pct": 0.03}), MKT, s0, e0)
        stops = led[led["exit_reason"] == "STOP"]
        self.assertGreater(len(stops), 0)
        for r in stops.itertuples():
            b = MOCK.bars(r.ticker)
            prev = mc.prev_trading_day(r.exit_date)
            self.assertLessEqual(b.loc[prev, "close"], r.entry_price * 0.97 + 1e-9)     # triggered on a close...
            self.assertAlmostEqual(r.exit_price, b.loc[r.exit_date, "open"])             # ...filled next open

    def test_missing_candle_voids_trade(self):
        class Gappy(MockProvider):
            def bars(self, code):
                b = super().bars(code)
                return b.drop(b.index[400]) if code == "MOCK.S00" else b
        g = Gappy(n_symbols=10, seed=11)
        m = Market(g, "LARGE_CAP_93")
        led, stats = generate_trades(spec(), m, *m.period("development"))
        gap = MOCK.bars("MOCK.S00").index[400]
        s00 = led[led["ticker"] == "MOCK.S00"]
        self.assertFalse(((s00["entry_date"] <= gap) & (s00["exit_date"] >= gap)).any())

    def test_benchmark_alignment(self):
        b = MOCK.bars("MOCK.S03")
        s = indicator_series("SPY_CLOSE", None, b, MOCK.benchmark())
        for d in b.index[250:260]:
            self.assertAlmostEqual(s.loc[d], MOCK.benchmark().loc[d, "close"])

    def test_reproducible(self):
        a, b = run_backtest(spec(), MKT), run_backtest(spec(), MKT)
        self.assertEqual(a["strategy"], b["strategy"])
        self.assertEqual(a["charts"]["strategy"], b["charts"]["strategy"])
        for k in ("engine_version", "dataset_version", "spec_hash", "settings"):
            self.assertIn(k, a["meta"])

    def test_portfolio_capacity_and_no_leverage(self):
        r = run_backtest(spec(portfolio={"starting_capital": 10_000, "max_positions": 2}), MKT)
        self.assertLessEqual(r["counts"]["executed_in_portfolio"], r["counts"]["trades"])
        self.assertGreater(r["counts"]["skipped_portfolio_capacity"], 0)
        self.assertLessEqual(r["strategy"]["exposure"], 1.0 + 1e-9)

    def test_lookahead_audit_passes_on_engine(self):
        r = run_backtest(spec(), MKT)
        la = lookahead_audit(spec(), MKT, r["_ledger"])
        self.assertEqual(la["status"], "PASS", la)


# ------------------------------------------------------------------ robustness
class TestRobustness(unittest.TestCase):
    def setUp(self):
        self.s0, self.e0 = MKT.period("development")
        self.led, _ = generate_trades(spec(), MKT, self.s0, self.e0)

    def test_oos_split_is_chronological(self):
        t0, dev_end, v0, last = MOCK.split()
        self.assertLess(dev_end, v0)
        self.assertEqual(v0, mc.next_trading_day(dev_end))
        n = len(MOCK.sessions())
        self.assertAlmostEqual(len([d for d in MOCK.sessions() if d <= dev_end]) / n, 0.7, delta=0.01)
        sealed = R.out_of_sample(self.led, None, revealed=False)
        self.assertEqual(sealed["status"], "SEALED")

    def test_sensitivity_is_small_and_neighbouring(self):
        out = R.sensitivity(spec(), MKT, self.s0, self.e0, self.led)
        self.assertLessEqual(len(out["variants"]), 17)
        labels = {v["parameter"] for v in out["variants"]}
        self.assertTrue({"RETURN threshold", "SMA period", "Holding days"} <= labels)
        self.assertLessEqual(len(out["grid"]), 9)

    def test_outlier_removal(self):
        led = pd.DataFrame({"net_return": [0.5] + [-0.001] * 40, "entry_date": pd.date_range("2020-01-01", periods=41).date,
                            "ticker": "X", "regime": "BULL", "year": 2020})
        out = R.outlier_test(led)
        self.assertEqual(out["status"], "FAIL")
        self.assertEqual(out["dependence"], "HIGH")

    def test_regime_classification(self):
        spy = MOCK.benchmark()
        reg = MKT.regime
        d = spy.index[300]
        exp = "BULL" if spy["close"].iloc[300] > spy["close"].iloc[101:301].mean() else "BEAR"
        self.assertEqual(reg.loc[d], exp)
        self.assertIsNone(reg.iloc[50])                       # not enough history -> unknown, not guessed

    def test_random_baseline_reproducible(self):
        a = R.random_baseline(self.led, spec(), MKT, self.s0, self.e0, seed=42)
        b = R.random_baseline(self.led, spec(), MKT, self.s0, self.e0, seed=42)
        c = R.random_baseline(self.led, spec(), MKT, self.s0, self.e0, seed=7)
        self.assertEqual(a["random_mean"], b["random_mean"])
        self.assertNotEqual(a["random_mean"], c["random_mean"])

    def test_bootstrap_reproducible_and_small_sample_flag(self):
        a, b = R.statistical_confidence(self.led), R.statistical_confidence(self.led)
        self.assertEqual((a["ci_low"], a["ci_high"]), (b["ci_low"], b["ci_high"]))
        small = R.statistical_confidence(self.led.head(10))
        self.assertTrue(small["small_sample"])
        self.assertNotEqual(small["status"], "PASS")

    def test_cost_stress_flags_fragile_edge(self):
        led = pd.DataFrame({"gross_return": [0.004] * 50})
        out = R.cost_stress(led, spec())
        self.assertIn(out["status"], ("MIXED", "FAIL"))

    def test_evidence_never_recommends(self):
        fake = {k: {"status": "PASS", "summary": ""} for k, _ in A.TESTS}
        fake["stats"] = {"status": "PASS", "mean": 0.01, "n": 100}
        fake["oos"]["status"] = "PASS"
        lvl = A.evidence(fake, revealed=True)["level"]
        self.assertEqual(lvl, A.ROBUST)
        self.assertEqual(A.evidence(fake, revealed=False)["level"], A.PROMISING)   # sealed data caps the verdict
        for word in ("BUY", "SELL", "SAFE", "GUARANTEED", "RECOMMENDED"):
            for l in (A.NO_EDGE, A.WEAK, A.PROMISING, A.ROBUST):
                self.assertNotIn(word, l)


# ------------------------------------------------------------------ lineage & sealed validation
class TestLineage(unittest.TestCase):
    def setUp(self):
        self.svc = AuditService(DB(os.path.join(tempfile.mkdtemp(), "t.sqlite3")), MockProvider(n_symbols=10, seed=11))

    def test_versions_reveal_and_multiple_testing(self):
        v1 = self.svc.create_version(BASE, "anon1")
        self.assertEqual(v1["version_no"], 1)
        again = self.svc.create_version(BASE, "anon1")
        self.assertTrue(again["reused"])                                   # same spec: no version inflation
        bt1 = self.svc.backtest(v1["version_id"])
        v2 = self.svc.create_version({**BASE, "exit": {"type": "FIXED_HOLD", "trading_days": 7}}, "anon1", v1["family_id"])
        self.assertEqual(v2["version_no"], 2)
        self.svc.backtest(v2["version_id"])
        lin = self.svc.lineage(v1["family_id"])
        self.assertEqual([v["version_no"] for v in lin["versions"]], [1, 2])
        self.assertIn("exit.trading_days: 5 -> 7", lin["versions"][1]["changes"])
        self.assertEqual(lin["multiple_testing"]["status"], "MODERATE")
        au = self.svc.audit(bt1["backtest_id"])
        self.assertEqual(au["tests"]["oos"]["status"], "SEALED")
        with self.assertRaises(ValueError):
            self.svc.reveal(v1["family_id"], v1["version_id"], confirm=False)
        r = self.svc.reveal(v1["family_id"], v1["version_id"], confirm=True)
        self.assertTrue(r["first_reveal"])
        self.assertNotEqual(r["audit"]["tests"]["oos"]["status"], "SEALED")
        lin = self.svc.lineage(v1["family_id"])
        self.assertTrue(lin["validation_revealed"])
        self.assertTrue(lin["versions"][0]["validation_viewed"])
        # auditing v2 afterwards shows validation, flagged as contaminated, and RECORDS the view
        au2 = self.svc.audit(self.svc.backtest(v2["version_id"])["backtest_id"])
        self.assertTrue(au2["tests"]["oos"].get("contaminated"))
        self.assertTrue(self.svc.lineage(v1["family_id"])["versions"][1]["validation_viewed"])
        self.assertFalse(self.svc.reveal(v1["family_id"], v1["version_id"], True)["first_reveal"])

    def test_related_signature_detected_same_browser_only(self):
        a = self.svc.create_version(BASE, "anonA")
        b = self.svc.create_version({**BASE, "name": "renamed", "exit": {"type": "FIXED_HOLD", "trading_days": 8}}, "anonA")
        c = self.svc.create_version(BASE, "anonB")
        self.assertEqual(a["family_id"], b["family_id"])
        self.assertTrue(b["related_detected"])
        self.assertNotEqual(a["family_id"], c["family_id"])           # other browsers are not linked (documented)

    def test_not_found(self):
        with self.assertRaises(NotFound):
            self.svc.backtest("ver_nope")


# ------------------------------------------------------------------ API
class TestAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prev = API._svc
        API._svc = AuditService(DB(os.path.join(tempfile.mkdtemp(), "api.sqlite3")), MockProvider(n_symbols=10, seed=11))
        cls.c = TestClient(API.app)

    @classmethod
    def tearDownClass(cls):
        API._svc = cls.prev

    def test_flow_and_errors(self):
        c = self.c
        self.assertTrue(c.get("/api/dataset").json()["synthetic"])          # mock data is labelled as such
        self.assertEqual(c.post("/api/versions", json={"spec": {**BASE, "direction": "SHORT"}}).status_code, 422)
        self.assertEqual(c.post("/api/interpret", json={"text": ""}).status_code, 422)
        self.assertEqual(c.post("/api/interpret", json={"text": "x" * 5000}).status_code, 422)
        self.assertEqual(c.post("/api/backtests", json={"version_id": "ver_missing"}).status_code, 404)
        self.assertEqual(c.get("/api/audits/aud_missing").status_code, 404)
        v = c.post("/api/versions", json={"spec": BASE, "anonymous_user_id": "u"}).json()
        bt = c.post("/api/backtests", json={"version_id": v["version_id"]}).json()
        self.assertGreater(bt["counts"]["trades"], 0)
        self.assertNotIn("_ledger", bt)
        au = c.post("/api/audits", json={"backtest_id": bt["backtest_id"]}).json()
        self.assertEqual(len(au["table"]), len(A.TESTS))
        self.assertEqual(c.get(f"/api/reports/{au['audit_id']}").status_code, 200)

    def test_backtest_error_is_reported_not_faked(self):
        orig = API._svc.backtest
        API._svc.backtest = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("data file missing"))
        try:
            r = self.c.post("/api/backtests", json={"version_id": "x"})
            self.assertEqual(r.status_code, 500)
            self.assertIn("data file missing", r.json()["detail"])
        finally:
            API._svc.backtest = orig

    def test_admin_requires_token(self):
        os.environ.pop("ADMIN_TOKEN", None)
        self.assertEqual(self.c.get("/api/admin/submissions").status_code, 403)
        os.environ["ADMIN_TOKEN"] = "t0k"
        try:
            self.assertEqual(self.c.get("/api/admin/submissions", headers={"X-Admin-Token": "bad"}).status_code, 401)
            s = self.c.post("/api/submissions", json={"strategy_name": "x", "description": "y", "belief_1_5": 3}).json()
            ok = self.c.patch(f"/api/admin/submissions/{s['submission_id']}", json={"testable_yn": "Y"},
                              headers={"X-Admin-Token": "t0k"})
            self.assertEqual(ok.json()["updated"], 1)
            bad = self.c.patch(f"/api/admin/submissions/{s['submission_id']}", json={"testable_yn": "DROP TABLE"},
                               headers={"X-Admin-Token": "t0k"})
            self.assertEqual(bad.status_code, 422)
        finally:
            os.environ.pop("ADMIN_TOKEN", None)

    def test_events_whitelisted(self):
        self.assertEqual(self.c.post("/api/events", json={"name": "report_viewed"}).status_code, 200)
        self.assertEqual(self.c.post("/api/events", json={"name": "keystrokes"}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
