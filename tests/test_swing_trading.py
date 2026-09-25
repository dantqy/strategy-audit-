"""MOCK tests: swing paper trading (pre-approved exits, buy checks, auto exits, idea scorecard)."""
import json
import os
import tempfile
import unittest
from datetime import datetime

import pandas as pd

from fixtures import FakeTradeCtx, cfg, use_fake_moomoo
from scanner import market_calendar as mc
from scanner import paper_execution as pe
from scanner import swing_trading as st
from scanner.risk import PaperLedger

OPEN_NOW = datetime(2026, 9, 24, 12, 0, tzinfo=mc.ET)


def et(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=mc.ET)


class FakeClient:
    """Context-manager stand-in for MoomooClient: snapshot() serves the given rows."""

    def __init__(self, rows):
        self.rows = rows

    def __call__(self, _cfg):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def snapshot(self, codes):
        return pd.DataFrame([r for r in self.rows if r["code"] in codes])


class SwingTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.c = cfg()
        self.c["swing"]["ledger_path"] = os.path.join(self.tmp, "swing_ledger.json")
        self._saved = (st.IDEAS, st.TRACK, st.latest_swing_scan)
        st.IDEAS = os.path.join(self.tmp, "ideas.csv")
        st.TRACK = os.path.join(self.tmp, "track.csv")
        self.scan = None
        st.latest_swing_scan = lambda: self.scan
        self.said = []

    def tearDown(self):
        st.IDEAS, st.TRACK, st.latest_swing_scan = self._saved

    def say(self, m):
        self.said.append(m)

    def make_scan(self, when=OPEN_NOW, price=20.0, stop=18.5, code="US.ABC"):
        meta = {"scan_time": mc.timestamps(when)}
        cand = {"rank": 1, "code": code, "price": price, "stop": stop, "move_pct": 7.0, "rel_vol": 3.0,
                "tags": "near 52-week high"}
        self.scan = {**meta, "candidates": [cand]}
        st.record_ideas(pd.DataFrame([cand]), meta)


def approve_yes(t):
    return pe.request_human_approval(t, input_fn=lambda _: "YES", print_fn=lambda _: None)


class TestPreapproval(SwingTestBase):
    def ledger_with(self, plan):
        led = st.swing_ledger(self.c)
        led.record_fill("US.ABC", "BUY", 10, 20.0, 1.0, "b1")
        if plan is not None:
            led.state["positions"]["US.ABC"]["plan"] = plan
        return led

    def sell(self, qty=10, side="SELL"):
        return pe.OrderTicket(code="US.ABC", side=side, qty=qty, limit_price=19.0, reason="auto", signals=(),
                              remaining_cash_after=0)

    def test_needs_approved_auto_exit_plan(self):
        good = {"kind": "swing", "auto_exit": True, "approved_fingerprint": "abc"}
        self.assertIsInstance(pe.preapproved_exit_approval(self.sell(), self.ledger_with(good)), pe.Approval)
        for bad in (None, {**good, "auto_exit": False}, {k: v for k, v in good.items() if k != "approved_fingerprint"}):
            with self.assertRaises(pe.PaperOrderRejected):
                pe.preapproved_exit_approval(self.sell(), self.ledger_with(bad))

    def test_never_approves_a_buy_or_oversell(self):
        good = {"kind": "swing", "auto_exit": True, "approved_fingerprint": "abc"}
        with self.assertRaises(pe.PaperOrderRejected):
            pe.preapproved_exit_approval(self.sell(side="BUY"), self.ledger_with(good))
        with self.assertRaises(pe.PaperOrderRejected):
            pe.preapproved_exit_approval(self.sell(qty=11), self.ledger_with(good))


class TestSwingBuy(SwingTestBase):
    def buy(self, live_price, now=OPEN_NOW, approve=approve_yes, which="1"):
        ctx = FakeTradeCtx()
        client = FakeClient([{"code": "US.ABC", "last_price": live_price, "pre_price": 0.0}])
        with use_fake_moomoo():
            rc = st.run_swing_buy(self.c, which, self.say, approve, now=now, client_factory=client,
                                  trade_ctx_factory=lambda _c: ctx)
        return rc, ctx

    def test_happy_path_stores_preapproved_plan_and_marks_taken(self):
        self.make_scan()
        rc, ctx = self.buy(20.1)
        self.assertEqual(rc, 0)
        call = ctx.calls[0]
        self.assertEqual(call["trd_env"].name, "SIMULATE")
        self.assertEqual(call["qty"], int((300 - 0.99) // (round(20.1 * 1.01, 2) * 1.001)))
        led = st.swing_ledger(self.c)
        plan = led.pending["OID1"]["plan"]
        self.assertEqual((plan["kind"], plan["auto_exit"], plan["stop_price"]), ("swing", True, 18.5))
        self.assertTrue(plan["approved_fingerprint"])
        self.assertEqual(plan["planned_exit"], "2026-10-01")          # open after 5 sessions (Thu..Wed)
        ideas = pd.read_csv(st.IDEAS)
        self.assertEqual(int(ideas.loc[ideas["code"] == "US.ABC", "taken"].iloc[0]), 1)
        self.assertEqual(PaperLedger(st.swing_cfg(self.c)).cash, 300)   # separate $300 swing ledger

    def test_refusals(self):
        self.make_scan()
        for price, reason in ((21.0, "chase limit"), (18.4, "below the idea's stop")):
            self.said.clear()
            rc, ctx = self.buy(price)
            self.assertEqual(rc, 1)
            self.assertEqual(ctx.calls, [])
            self.assertTrue(any(reason in m for m in self.said), self.said)
        self.said.clear()
        rc, _ = self.buy(20.1, now=datetime(2026, 9, 24, 14, 0, tzinfo=mc.ET))   # 2h after the scan
        self.assertTrue(any("min old" in m for m in self.said))
        self.said.clear()
        rc, _ = self.buy(20.1, now=datetime(2026, 9, 26, 12, 0, tzinfo=mc.ET))   # Saturday
        self.assertTrue(any("CLOSED" in m for m in self.said))

    def test_declined_ticket_sends_nothing(self):
        self.make_scan()
        rc, ctx = self.buy(20.1, approve=lambda t: None)
        self.assertEqual(ctx.calls, [])
        ideas = pd.read_csv(st.IDEAS)
        self.assertEqual(int(ideas["taken"].iloc[0]), 0)


class TestAutoExits(SwingTestBase):
    def held(self, plan_over=None):
        led = st.swing_ledger(self.c)
        led.record_fill("US.ABC", "BUY", 10, 20.0, 1.0, "b1")
        plan = {"kind": "swing", "auto_exit": True, "approved_fingerprint": "x", "stop_price": 18.5,
                "planned_exit": "2026-10-01", "exit_at": "next_open", "horizon": 5, "entry_session": "2026-09-24"}
        plan.update(plan_over or {})
        led.state["positions"]["US.ABC"]["plan"] = plan
        led.save()
        return led

    def test_decide(self):
        led = self.held()
        snap = {"US.ABC": {"last_price": 18.4}}
        self.assertEqual(st.decide_exits(led, snap, OPEN_NOW)[0]["reason"][:4], "stop")
        self.assertEqual(st.decide_exits(led, {"US.ABC": {"last_price": 19.0}}, OPEN_NOW), [])
        # stop is not acted on outside US hours (pre-market prints are thin)
        self.assertEqual(st.decide_exits(led, snap, et(2026, 9, 25, 8, 0)), [])
        # time exit: after the 5th session closes, before the 6th opens -> sell pre-market
        due = st.decide_exits(led, {"US.ABC": {"pre_price": 21.0, "last_price": 21.0}}, et(2026, 10, 1, 8, 0))
        self.assertIn("time exit", due[0]["reason"])
        # not an auto-exit position -> never touched
        self.c["swing"]["ledger_path"] = os.path.join(self.tmp, "swing_ledger2.json")
        led2 = self.held({"auto_exit": False})
        self.assertEqual(st.decide_exits(led2, snap, OPEN_NOW), [])

    def test_run_auto_exits_sells_once(self):
        self.held()
        ctx = FakeTradeCtx()
        client = FakeClient([{"code": "US.ABC", "last_price": 18.4}])
        with use_fake_moomoo():
            n = st.run_auto_exits(self.c, self.say, OPEN_NOW, client_factory=client, trade_ctx_factory=lambda _c: ctx)
            self.assertEqual(n, 1)
            self.assertEqual(ctx.calls[0]["trd_side"].name, "SELL")
            self.assertEqual(ctx.calls[0]["trd_env"].name, "SIMULATE")
            self.assertTrue(any("AUTO SELL" in m for m in self.said))
            # the pending SELL blocks a second auto sell
            n2 = st.run_auto_exits(self.c, self.say, OPEN_NOW, client_factory=client, trade_ctx_factory=lambda _c: ctx)
            self.assertEqual(n2, 0)
            self.assertEqual(len(ctx.calls), 1)


class TestPendingFillIsWatched(SwingTestBase):
    def test_filled_but_pending_buy_gets_booked_and_its_stop_checked(self):
        """Live bug 2026-09-24: PRGO filled after /sbuy stopped waiting; the stop was not watched."""
        led = st.swing_ledger(self.c)
        plan = {"kind": "swing", "auto_exit": True, "approved_fingerprint": "x", "stop_price": 14.0,
                "planned_exit": "2026-10-01", "exit_at": "next_open", "horizon": 5, "entry_session": "2026-09-24"}
        led.add_pending("OIDB", "US.PRGO", "BUY", 19, 15.03, 1.02, 111, plan=plan)
        led.save()
        ctx = FakeTradeCtx()
        ctx.orders["OIDB"] = {"order_status": "FILLED_ALL", "dealt_qty": 19, "dealt_avg_price": 15.03}
        client = FakeClient([{"code": "US.PRGO", "last_price": 13.9}])       # already below the stop
        with use_fake_moomoo():
            n = st.run_auto_exits(self.c, self.say, OPEN_NOW, client_factory=client, trade_ctx_factory=lambda _c: ctx)
        self.assertTrue(any("Fill confirmed" in m for m in self.said), self.said)
        self.assertEqual(n, 1)                                                  # and the stop acted
        self.assertEqual(ctx.calls[0]["trd_side"].name, "SELL")


class TestScorecard(SwingTestBase):
    def test_outcomes(self):
        ideas = pd.DataFrame([
            {"idea_date": "2026-09-01", "code": "US.STOP", "price": 20.0, "stop": 18.5, "taken": 1},
            {"idea_date": "2026-09-01", "code": "US.GAP", "price": 20.0, "stop": 18.5, "taken": 0},
            {"idea_date": "2026-09-01", "code": "US.HOLD", "price": 20.0, "stop": 18.5, "taken": 0},
            {"idea_date": "2026-09-01", "code": "US.NEW", "price": 20.0, "stop": 18.5, "taken": 0}])
        days = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-08", "2026-09-09"]
        rows = []
        for k, d in enumerate(days):
            rows.append({"date": d, "code": "US.HOLD", "open": 20, "high": 22, "low": 19.0 if k else 10.0,
                         "close": 20 + k})                                   # day-0 low 10 must NOT stop it
            rows.append({"date": d, "code": "US.STOP", "open": 20, "high": 20.5, "low": 18.0 if k == 2 else 19.5,
                         "close": 19.8})
            rows.append({"date": d, "code": "US.GAP", "open": 16.0 if k == 1 else 20, "high": 20, "low": 15.5,
                         "close": 16})
        rows.append({"date": "2026-09-02", "code": "US.NEW", "open": 20, "high": 21, "low": 19.5, "close": 20.5})
        oc = st.idea_outcomes(ideas, pd.DataFrame(rows), self.c).set_index("code")
        fee = 2 * 0.99 / 300
        self.assertEqual(oc.at["US.STOP", "status"], "stopped")
        self.assertAlmostEqual(oc.at["US.STOP", "ret_net"], 18.5 / 20 - 1 - fee)
        self.assertAlmostEqual(oc.at["US.GAP", "ret_net"], 16.0 / 20 - 1 - fee)     # gap below stop: open fill
        self.assertEqual(oc.at["US.HOLD", "status"], "held to time exit")
        self.assertAlmostEqual(oc.at["US.HOLD", "ret_net"], 25 / 20 - 1 - fee)        # close of 5th session after
        self.assertTrue(pd.isna(oc.at["US.NEW", "ret_net"]))                           # still running

    def test_record_ideas_dedupes_per_day(self):
        meta = {"scan_time": mc.timestamps(OPEN_NOW)}
        df = pd.DataFrame([{"code": "US.A", "price": 10, "stop": 9.2, "move_pct": 5, "rel_vol": 2.5, "tags": "",
                            "score": 4}])
        st.record_ideas(df, meta)
        st.record_ideas(df.assign(price=11), meta)                  # same day again: first sighting kept
        ideas = pd.read_csv(st.IDEAS)
        self.assertEqual(len(ideas), 1)
        self.assertEqual(float(ideas["price"].iloc[0]), 10.0)

    def test_stats_text_runs_empty_and_with_data(self):
        self.assertIn("No swing ideas", st.stats_text(self.c))
        self.make_scan()
        self.assertIn("too few to judge", st.stats_text(self.c))


class TestTicketShowsExitPlan(unittest.TestCase):
    def test_render(self):
        t = pe.OrderTicket(code="US.ABC", side="BUY", qty=14, limit_price=20.3, reason="r", signals=(),
                           remaining_cash_after=12, entry_session="2026-09-24", planned_exit="2026-10-01",
                           horizon=5, exit_at="next_open", kind="swing", stop_price=18.5, auto_exit=True)
        txt = t.render()
        self.assertIn("Stop-loss            : $18.50", txt)
        self.assertIn("Time exit            : open of 2026-10-01", txt)
        self.assertIn("approving this ticket approves those two sells too", txt)
        self.assertNotEqual(t.fingerprint(), pe.OrderTicket(**{**t.__dict__, "stop_price": 17.0}).fingerprint())


if __name__ == "__main__":
    unittest.main()
