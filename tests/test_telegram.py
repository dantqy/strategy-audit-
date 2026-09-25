"""MOCK tests: Telegram approval and bot routing. No network: a fake Bot API is used."""
import sys
import unittest
from datetime import datetime
from pathlib import Path

from fixtures import ROOT, cfg
from scanner import market_calendar as mc
from scanner import paper_execution as pe
from scanner.risk import PaperLedger
from scanner.telegram import Telegram, TelegramError, approval_code, telegram_approval

sys.path.insert(0, str(Path(ROOT) / "scripts"))
import telegram_bot  # noqa: E402

TOKEN = "123456:SECRETSECRET"
ME, STRANGER = 111, 999


class FakeBotAPI:
    """Mimics api.telegram.org. `inbox` holds (chat_id, text) the user 'sends'; `on_send` can react."""

    def __init__(self):
        self.inbox, self.sent, self.next_id, self.on_send = [], [], 1, None

    def user_says(self, text, chat=ME):
        self.inbox.append({"update_id": self.next_id, "message": {"chat": {"id": chat, "type": "private"},
                                                                  "text": text}})
        self.next_id += 1

    def __call__(self, url, payload, timeout):
        method = url.rsplit("/", 1)[1]
        if method == "sendMessage":
            self.sent.append(payload["text"])
            if self.on_send:
                self.on_send(payload["text"])
            return {"ok": True, "result": {}}
        if method == "getUpdates":
            ups = [u for u in self.inbox if u["update_id"] >= payload["offset"]]
            return {"ok": True, "result": ups}
        if method == "getMe":
            return {"ok": False, "description": "Unauthorized"}
        return {"ok": False, "description": "unknown"}


def ticket(**kw):
    base = dict(code="US.ABC", side="BUY", qty=2, limit_price=50.0, reason="t", signals=(),
                remaining_cash_after=199.0)
    return pe.OrderTicket(**{**base, **kw})


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 30          # every poll "takes" 30 s
        return self.t


class TestApproval(unittest.TestCase):
    def setUp(self):
        self.api = FakeBotAPI()
        self.tg = Telegram(TOKEN, ME, http=self.api)
        self.t = ticket()
        self.code = approval_code(self.t)

    def reply_after_ticket(self, text, chat=ME):
        """The user answers only once the question has been asked."""
        def react(msg):
            if msg.startswith("To send this PAPER order"):
                self.api.user_says(text, chat)
                self.api.on_send = None
        self.api.on_send = react

    def test_exact_code_approves_this_ticket(self):
        self.reply_after_ticket(f"yes {self.code.lower()}")
        a = telegram_approval(self.tg, self.t, clock=FakeClock())
        self.assertIsInstance(a, pe.Approval)
        self.assertEqual(a.fingerprint, self.t.fingerprint())
        self.assertTrue(any("PAPER ORDER" in m for m in self.api.sent))     # the full ticket was shown

    def test_bare_yes_cancels(self):
        self.reply_after_ticket("YES")
        self.assertIsNone(telegram_approval(self.tg, self.t, clock=FakeClock()))
        self.assertIn("NO ORDER", self.api.sent[-1])

    def test_code_of_another_ticket_cancels(self):
        self.reply_after_ticket(f"YES {approval_code(ticket(qty=3))}")
        self.assertIsNone(telegram_approval(self.tg, self.t, clock=FakeClock()))

    def test_stranger_cannot_approve(self):
        self.reply_after_ticket(f"YES {self.code}", chat=STRANGER)
        self.assertIsNone(telegram_approval(self.tg, self.t, timeout_s=120, clock=FakeClock()))
        self.assertIn("No reply in time", self.api.sent[-2])

    def test_old_message_cannot_approve(self):
        self.api.user_says(f"YES {self.code}")          # sitting there BEFORE the ticket was sent
        self.assertIsNone(telegram_approval(self.tg, self.t, timeout_s=120, clock=FakeClock()))

    def test_timeout(self):
        self.assertIsNone(telegram_approval(self.tg, self.t, timeout_s=120, clock=FakeClock()))


class TestClient(unittest.TestCase):
    def test_errors_never_contain_token(self):
        tg = Telegram(TOKEN, ME, http=FakeBotAPI())
        with self.assertRaises(TelegramError) as cm:
            tg.me()
        self.assertNotIn("SECRET", str(cm.exception))

    def test_bad_token_rejected(self):
        with self.assertRaises(TelegramError):
            Telegram("not-a-token")

    def test_long_message_split(self):
        api = FakeBotAPI()
        Telegram(TOKEN, ME, http=api).send("x" * 9000)
        self.assertEqual(len(api.sent), 3)


class TestBotRouting(unittest.TestCase):
    def setUp(self):
        self.api = FakeBotAPI()
        self.bot = telegram_bot.Bot(cfg(), Telegram(TOKEN, ME, http=self.api))
        self.calls = []
        self.bot.do_order = lambda side, t: self.calls.append((side, t))
        self.bot.do_scan = lambda: self.calls.append(("scan",))
        self.bot.do_swing = lambda: self.calls.append(("swing",))

    def test_commands(self):
        self.bot.handle("/buy nvda")
        self.bot.handle("/sell@my_bot AAPL")
        self.bot.handle("/scan")
        self.bot.handle("/swing")
        self.assertEqual(self.calls, [("BUY", "nvda"), ("SELL", "AAPL"), ("scan",), ("swing",)])

    def test_buy_without_ticker_asks(self):
        self.bot.handle("/buy")
        self.assertEqual(self.calls, [])
        self.assertIn("Which stock", self.api.sent[-1])

    def test_yes_with_nothing_waiting_does_nothing(self):
        self.bot.handle("YES ABC123")
        self.assertEqual(self.calls, [])
        self.assertIn("No ticket is waiting", self.api.sent[-1])

    def test_status(self):
        self.bot.handle("/status")
        self.assertIn("EARNINGS account", self.api.sent[-1])
        self.assertIn("SWING account", self.api.sent[-1])
        self.assertIn("Paper cash $300.00", self.api.sent[-1])


class TestScanMessage(unittest.TestCase):
    def test_buyable_marking(self):
        c = cfg()
        led = PaperLedger(c)
        base = {"signals_triggered": ["EARNINGS_DRIFT"], "score": 3, "bar_date": "2026-09-24",
                "reaction_session": "2026-09-24"}
        res = {"scan_time": {"us_eastern": "2026-09-24T16:30:00-04:00"}, "market_phase": "POST_MARKET",
               "last_completed_session": "2026-09-24", "errors": [], "candidates": [
                   {**base, "ticker": "US.OK", "current_price": 40.0, "signal_direction": "BULLISH",
                    "data_freshness": "LAST_COMPLETED_BAR"},
                   {**base, "ticker": "US.BIG", "current_price": 765.0, "signal_direction": "BULLISH",
                    "data_freshness": "LAST_COMPLETED_BAR"},
                   {**base, "ticker": "US.DN", "current_price": 30.0, "signal_direction": "BEARISH",
                    "data_freshness": "LAST_COMPLETED_BAR"},
                   {**base, "ticker": "US.LIVE", "current_price": 30.0, "signal_direction": "BULLISH",
                    "data_freshness": "LIVE_INTRADAY"},
                   {**base, "ticker": "US.VOL", "current_price": 30.0, "signal_direction": "BULLISH",
                    "data_freshness": "LAST_COMPLETED_BAR", "signals_triggered": ["VOLUME_ANOMALY"]},
                   {**base, "ticker": "US.OLD", "current_price": 30.0, "signal_direction": "BULLISH",
                    "data_freshness": "LAST_COMPLETED_BAR", "reaction_session": "2026-09-23"}]}
        now = datetime(2026, 9, 24, 20, 0, tzinfo=mc.ET)
        msg = telegram_bot.scan_message(res, c, led, now=now)
        self.assertIn("-> /buy OK", msg)
        self.assertIn("budget", msg.split("US.BIG")[1].split("US.DN")[0])
        self.assertIn("long-only", msg.split("US.DN")[1])
        self.assertIn("intraday", msg.split("US.LIVE")[1].split("US.VOL")[0])
        self.assertIn("no backtested edge", msg.split("US.VOL")[1].split("US.OLD")[0])
        self.assertIn("entry timing passed", msg.split("US.OLD")[1])


if __name__ == "__main__":
    unittest.main()
