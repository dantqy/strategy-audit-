"""MOCK tests: paper-execution safety. No real OpenD / broker involved."""
import ast
import inspect
import io
import tokenize
import unittest
from pathlib import Path

from fixtures import ROOT, FakeTradeCtx, cfg, use_fake_moomoo
from scanner import paper_execution as pe
from scanner.risk import NOT_EXECUTABLE, PaperLedger, size_position

CODE_DIRS = ["scanner", "backtest", "scripts"]


def executable_py_files():
    for d in CODE_DIRS:
        yield from (Path(ROOT) / d).rglob("*.py")


def ticket(**kw):
    base = dict(code="US.ABC", side="BUY", qty=2, limit_price=50.0, reason="test",
                signals=("EARNINGS_DRIFT",), remaining_cash_after=199.0)
    base.update(kw)
    return pe.OrderTicket(**base)


def approve(t):
    return pe.request_human_approval(t, input_fn=lambda _: "YES", print_fn=lambda _: None)


class TestNoRealEnvironment(unittest.TestCase):
    def test_no_REAL_token_in_executable_code(self):
        """Fails if TrdEnv.REAL (or any bare REAL identifier) appears in non-test code."""
        offenders = []
        for f in executable_py_files():
            src = f.read_text(encoding="utf-8")
            if "TrdEnv.REAL" in src:
                offenders.append(f"{f}: literal TrdEnv.REAL")
            for tok in tokenize.generate_tokens(io.StringIO(src).readline):
                if tok.type == tokenize.NAME and tok.string == "REAL":
                    offenders.append(f"{f}:{tok.start[0]} identifier REAL")
        self.assertEqual(offenders, [])

    def test_place_order_only_called_in_paper_execution(self):
        callers = []
        for f in executable_py_files():
            tree = ast.parse(f.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "place_order":
                    callers.append(f.name)
        self.assertEqual(set(callers), {"paper_execution.py"})
        self.assertEqual(len(callers), 1)

    def test_every_place_order_passes_trdenv_simulate_literally(self):
        tree = ast.parse((Path(ROOT) / "scanner" / "paper_execution.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "place_order":
                kw = {k.arg: k.value for k in node.keywords}
                self.assertIn("trd_env", kw)
                self.assertEqual(ast.unparse(kw["trd_env"]), "TrdEnv.SIMULATE")

    def test_every_trade_query_passes_trdenv_simulate_literally(self):
        """moomoo's trade QUERIES also default to the live environment: every call must pin SIMULATE."""
        env_calls = {"place_order", "order_list_query", "history_order_list_query", "deal_list_query",
                     "history_deal_list_query", "accinfo_query", "position_list_query", "modify_order",
                     "cancel_all_order", "acctradinginfo_query"}
        seen = []
        for f in executable_py_files():
            tree = ast.parse(f.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in env_calls:
                    kw = {k.arg: k.value for k in node.keywords}
                    seen.append(node.func.attr)
                    self.assertIn("trd_env", kw, f"{f.name}:{node.lineno} {node.func.attr} without trd_env")
                    self.assertEqual(ast.unparse(kw["trd_env"]), "TrdEnv.SIMULATE", f"{f.name}:{node.lineno}")
        self.assertTrue({"place_order", "order_list_query", "history_order_list_query"} <= set(seen))

    def test_submit_signature_has_no_env_or_live_switch(self):
        params = set(inspect.signature(pe.submit_paper_order).parameters)
        for bad in ("trd_env", "live", "env", "real", "mode"):
            self.assertNotIn(bad, params)

    def test_config_cannot_add_live_switch(self):
        from scanner.config import _check_forbidden
        for bad in ({"live": True}, {"account": {"trd_env": "X"}}, {"x": {"live_trading": 1}}):
            with self.assertRaises(ValueError):
                _check_forbidden(bad)


class TestSubmit(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg()
        self.ledger = PaperLedger(self.cfg)

    def test_simulate_forced_at_runtime(self):
        with use_fake_moomoo() as m:
            ctx = FakeTradeCtx()
            t = ticket()
            res = pe.submit_paper_order(ctx, t, approve(t), self.ledger, self.cfg)
            self.assertEqual(ctx.calls[0]["trd_env"], m.TrdEnv.SIMULATE)
            self.assertEqual(ctx.calls[0]["acc_id"], 111)
            self.assertEqual(res["env"], "SIMULATE")
            # submitted != filled: cash is reserved, not spent, and no position exists yet
            self.assertEqual(self.ledger.cash, 300)
            self.assertLess(self.ledger.available_cash, 300)
            self.assertIn("OID1", self.ledger.pending)
            self.assertEqual(self.ledger.position_qty("US.ABC"), 0)

    def test_human_approval_required(self):
        with use_fake_moomoo():
            ctx = FakeTradeCtx()
            with self.assertRaises(pe.PaperOrderRejected):
                pe.submit_paper_order(ctx, ticket(), None, self.ledger, self.cfg)
            self.assertEqual(ctx.calls, [])

    def test_only_exact_YES_approves(self):
        for ans in ("yes", "y", "", "YES please", "no"):
            self.assertIsNone(pe.request_human_approval(ticket(), input_fn=lambda _, a=ans: a, print_fn=lambda _: None))

    def test_approval_bound_to_exact_ticket(self):
        with use_fake_moomoo():
            ctx = FakeTradeCtx()
            a = approve(ticket(qty=1))
            with self.assertRaises(pe.PaperOrderRejected):
                pe.submit_paper_order(ctx, ticket(qty=2), a, self.ledger, self.cfg)
            self.assertEqual(ctx.calls, [])

    def test_forged_approval_rejected(self):
        with self.assertRaises(pe.PaperOrderRejected):
            pe.Approval(ticket().fingerprint(), object())

    def test_insufficient_cash(self):
        with use_fake_moomoo():
            ctx = FakeTradeCtx()
            t = ticket(qty=10, limit_price=50.0)  # $500 > $300
            with self.assertRaises(pe.PaperOrderRejected):
                pe.submit_paper_order(ctx, t, approve(t), self.ledger, self.cfg)
            self.assertEqual(ctx.calls, [])

    def test_invalid_quantities(self):
        with use_fake_moomoo():
            for q in (0, -1, 1.5, float("nan")):
                ctx = FakeTradeCtx()
                t = ticket(qty=q)
                with self.assertRaises(pe.PaperOrderRejected, msg=str(q)):
                    pe.submit_paper_order(ctx, t, approve(t), self.ledger, self.cfg)
                self.assertEqual(ctx.calls, [])

    def test_no_shorting(self):
        with use_fake_moomoo():
            ctx = FakeTradeCtx()
            t = ticket(side="SELL", qty=1)
            with self.assertRaises(pe.PaperOrderRejected):
                pe.submit_paper_order(ctx, t, approve(t), self.ledger, self.cfg)

    def test_rejects_when_no_simulate_account(self):
        class RealOnly(FakeTradeCtx):
            def get_acc_list(self):
                import pandas as pd
                return 0, pd.DataFrame([{"acc_id": 9, "trd_env": "LIVE_ACCOUNT", "trdmarket_auth": "['US']"}])
        with use_fake_moomoo():
            ctx = RealOnly()
            t = ticket()
            with self.assertRaises(pe.PaperOrderRejected):
                pe.submit_paper_order(ctx, t, approve(t), self.ledger, self.cfg)
            self.assertEqual(ctx.calls, [])


class TestFillSync(unittest.TestCase):
    """The ledger books what the broker FILLED, not what was submitted."""

    def setUp(self):
        self.cfg = cfg()
        self.ledger = PaperLedger(self.cfg)

    def _submit(self, ctx, **kw):
        t = ticket(**kw)
        return pe.submit_paper_order(ctx, t, approve(t), self.ledger, self.cfg)

    def test_fill_booked_at_broker_price_not_limit(self):
        with use_fake_moomoo():
            ctx = FakeTradeCtx()
            self._submit(ctx)                               # 2 @ limit 50
            ctx.orders["OID1"] = {"order_status": "FILLED_ALL", "dealt_qty": 2, "dealt_avg_price": 49.5}
            res = pe.sync_pending_orders(ctx, self.ledger)
            self.assertEqual(res[0]["booked"]["price"], 49.5)
            self.assertEqual(self.ledger.position_qty("US.ABC"), 2)
            self.assertEqual(self.ledger.pending, {})
            self.assertAlmostEqual(self.ledger.cash, 300 - 99 - res[0]["booked"]["fees_est"])
            for name, kw in ctx.query_calls:
                self.assertEqual(kw["trd_env"].name, "SIMULATE", name)

    def test_unfilled_order_releases_reservation(self):
        with use_fake_moomoo():
            ctx = FakeTradeCtx()
            self._submit(ctx)
            ctx.orders["OID1"] = {"order_status": "CANCELLED_ALL", "dealt_qty": 0, "dealt_avg_price": 0}
            pe.sync_pending_orders(ctx, self.ledger)
            self.assertEqual(self.ledger.cash, 300)
            self.assertEqual(self.ledger.available_cash, 300)
            self.assertEqual(self.ledger.position_qty("US.ABC"), 0)
            self.assertIn("no fill", self.ledger.state["history"][-1]["note"])

    def test_working_order_stays_pending_then_partial_fill_booked(self):
        with use_fake_moomoo():
            ctx = FakeTradeCtx()
            self._submit(ctx)
            ctx.orders["OID1"] = {"order_status": "FILLED_PART", "dealt_qty": 1, "dealt_avg_price": 50}
            pe.sync_pending_orders(ctx, self.ledger)
            self.assertIn("OID1", self.ledger.pending)        # not final yet: nothing booked
            self.assertEqual(self.ledger.position_qty("US.ABC"), 0)
            ctx.orders["OID1"] = {"order_status": "CANCELLED_PART", "dealt_qty": 1, "dealt_avg_price": 50}
            pe.sync_pending_orders(ctx, self.ledger)
            self.assertEqual(self.ledger.position_qty("US.ABC"), 1)
            pe.sync_pending_orders(ctx, self.ledger)          # idempotent: no double booking
            self.assertEqual(self.ledger.position_qty("US.ABC"), 1)

    def test_rolled_back_fill_books_nothing(self):
        with use_fake_moomoo():
            ctx = FakeTradeCtx()
            self._submit(ctx)
            ctx.orders["OID1"] = {"order_status": "FILL_CANCELLED", "dealt_qty": 2, "dealt_avg_price": 50}
            pe.sync_pending_orders(ctx, self.ledger)
            self.assertEqual(self.ledger.position_qty("US.ABC"), 0)
            self.assertEqual(self.ledger.cash, 300)

    def test_previous_day_order_found_via_history(self):
        with use_fake_moomoo():
            ctx = FakeTradeCtx()
            self._submit(ctx)
            ctx.history_orders["OID1"] = {"order_status": "FILLED_ALL", "dealt_qty": 2, "dealt_avg_price": 50}
            pe.sync_pending_orders(ctx, self.ledger)
            self.assertEqual(self.ledger.position_qty("US.ABC"), 2)
            self.assertEqual([n for n, _ in ctx.query_calls], ["order_list_query", "history_order_list_query"])

    def test_not_found_left_pending(self):
        with use_fake_moomoo():
            ctx = FakeTradeCtx()
            self._submit(ctx)
            res = pe.sync_pending_orders(ctx, self.ledger)
            self.assertEqual(res[0]["status"], "NOT_FOUND")
            self.assertIn("OID1", self.ledger.pending)

    def test_pending_buy_reservation_blocks_overspending(self):
        with use_fake_moomoo():
            ctx = FakeTradeCtx()
            self._submit(ctx, qty=2, limit_price=100.0)       # reserves ~$202 of $300
            t2 = ticket(qty=1, limit_price=150.0)
            with self.assertRaises(pe.PaperOrderRejected):
                pe.submit_paper_order(ctx, t2, approve(t2), self.ledger, self.cfg)

    def test_pending_sell_blocks_double_sell(self):
        with use_fake_moomoo():
            self.ledger.record_fill("US.ABC", "BUY", 2, 50.0, 1.0, "seed")
            ctx = FakeTradeCtx()
            self._submit(ctx, side="SELL", qty=2)
            t2 = ticket(side="SELL", qty=1)
            with self.assertRaises(pe.PaperOrderRejected):
                pe.submit_paper_order(ctx, t2, approve(t2), self.ledger, self.cfg)

    def test_wait_for_order_polls_until_final(self):
        with use_fake_moomoo():
            ctx = FakeTradeCtx()
            self._submit(ctx)
            ctx.orders["OID1"] = {"order_status": "SUBMITTED", "dealt_qty": 0, "dealt_avg_price": 0}

            def fill_on_sleep(_):
                ctx.orders["OID1"] = {"order_status": "FILLED_ALL", "dealt_qty": 2, "dealt_avg_price": 50}
            s = pe.wait_for_order(ctx, self.ledger, "OID1", timeout_s=5, sleep=fill_on_sleep)
            self.assertEqual(s["status"], "FILLED_ALL")
            self.assertEqual(self.ledger.position_qty("US.ABC"), 2)


class TestSizing(unittest.TestCase):
    def test_whole_shares(self):
        r = size_position(47.3, 300, 300, cfg())
        self.assertEqual(r.qty, 3)          # budget $150
        self.assertEqual(r.status, "OK")

    def test_not_executable_with_current_capital(self):
        r = size_position(900.0, 300, 300, cfg())
        self.assertEqual(r.status, NOT_EXECUTABLE)
        self.assertEqual(r.qty, 0)

    def test_fractional_only_if_confirmed(self):
        c = cfg()
        c["account"]["fractional_shares_confirmed"] = True
        r = size_position(900.0, 300, 300, c)
        self.assertAlmostEqual(r.qty, 0.166)


if __name__ == "__main__":
    unittest.main()
