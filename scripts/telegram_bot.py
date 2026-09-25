"""Telegram control for the PAPER scanner. Keep this window (and OpenD) running.

    python scripts/telegram_bot.py

Commands (send them to your bot in Telegram):
    /scan          run the scanner now
    /swing         whole-market swing ideas (untested; nothing is ordered)
    /buy XYZ       paper BUY ticket for XYZ (reply YES <code> to send)
    /sell XYZ      paper SELL ticket for XYZ (reply YES <code> to send)
    /sync          book fills of pending paper orders
    /exits         positions and their planned exit
    /status        both paper accounts (earnings + swing): cash, positions, pending
    /sbuy 2        SWING paper buy of idea #2 from the latest /swing list (or /sbuy TICKER)
    /ssell XYZ     SWING paper sell (asks YES <code>)
    /sstatus       swing paper account
    /sstats        swing scorecard: all ideas vs the ones you took vs skipped
    /help          this list

Automatic (US trading days, times in US/Eastern):
    ~10:15 ET (22:15 SGT summer / 23:15 winter): swing ideas -> reply /sbuy N
    during US hours, every 2 min: swing stops checked; pre-approved auto-sell if hit
    before the open: swing time exits (sold at the open)
    ~16:20 ET (04:20 SGT summer / 05:20 winter): scan, positions due to sell, swing tracking
    ~09:50 ET (21:50 SGT summer / 22:50 winter): book fills from the open

The bot never BUYS without your "YES <code>" reply. The only orders it sends on
its own are swing exits (stop / time exit) that were printed on a ticket you
approved. It only listens to the chat saved by telegram_setup.py.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from datetime import time as dtime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from scanner import market_calendar as mc  # noqa: E402
from scanner.config import load_config, resolve  # noqa: E402
from scanner.logsetup import setup_logging  # noqa: E402
from scanner.moomoo_client import MoomooClient, OpenDUnavailable  # noqa: E402
from scanner.order_flow import exits_report, run_order, run_sync  # noqa: E402
from scanner.risk import PaperLedger  # noqa: E402
from scanner.scanner import run_scan, save_scan  # noqa: E402
from scanner.swing import format_swing, run_swing_scan  # noqa: E402
from scanner.swing_trading import (run_auto_exits, run_swing_buy, stats_text, swing_cfg,  # noqa: E402
                                   swing_ledger, update_tracking)
from scanner.trade_plan import check_entry_window  # noqa: E402
from scanner.telegram import Telegram, TelegramError, load_config as load_tg, telegram_approval  # noqa: E402

log = logging.getLogger("telegram_bot")
STATE_PATH = "outputs/telegram_bot_state.json"
AUTO_SYNC_AT = dtime(9, 50)      # ET, after the open
HELP = __doc__.split("Commands (send them to your bot in Telegram):")[1].split("The bot never")[0].strip()


def scan_message(result: dict, cfg: dict, ledger: PaperLedger, now=None) -> str:
    """Short phone-friendly summary; marks which candidates can actually be bought."""
    now = now or mc.now_utc()
    budget = ledger.equity() * float(cfg["account"]["max_position_pct"])
    cap = 1 + float(cfg["execution"]["entry_limit_cap_pct"]) / 100
    frac = bool(cfg["account"].get("fractional_shares_confirmed"))
    head = (f"SCAN {result['scan_time']['us_eastern'][:16]} ET | market {result['market_phase']} | "
            f"last session {result['last_completed_session']}")
    lines = [head, "Research only. Only buy signals the backtest supports."]
    if not result["candidates"]:
        lines.append("No candidates today.")
    for n, c in enumerate(result["candidates"][:10], 1):
        px = c["current_price"] or 0
        chk = check_entry_window(c, result, now, cfg["execution"].get("buy_signals"))
        affordable = px > 0 and (frac or px * cap <= budget)
        if c["signal_direction"] != "BULLISH":
            why = "(long-only: not buyable)"
        elif not chk.ok:
            why = "(info only: " + ("intraday" if c["data_freshness"] != "LAST_COMPLETED_BAR" else
                                    "no backtested edge" if "information only" in chk.reason else
                                    "entry timing passed") + ")"
        elif not affordable:
            why = f"(1 share > ${budget:,.0f} budget)"
        else:
            why = "-> /buy " + c["ticker"].replace("US.", "")
        sig = ", ".join(s.replace("_", " ").title() for s in c["signals_triggered"])
        lines.append(f"\n{n}. {c['ticker']} ${px:,.2f} {c['signal_direction']} score {c['score']}\n"
                     f"   {sig}\n   {why}")
    if len(result["candidates"]) > 10:
        lines.append(f"\n...and {len(result['candidates']) - 10} more (see outputs/scans).")
    if result["errors"]:
        lines.append(f"\n{len(result['errors'])} symbol error(s); see the log on the laptop.")
    return "\n".join(lines)


def status_message(ledger: PaperLedger) -> str:
    lines = [f"Paper cash ${ledger.cash:,.2f} | available ${ledger.available_cash:,.2f} | "
             f"equity at cost ${ledger.equity():,.2f}"]
    for code, p in sorted(ledger.state["positions"].items()):
        lines.append(f"HOLD {code} {p['qty']:g} @ ${p['avg_price']:,.2f}")
    for oid, o in ledger.pending.items():
        lines.append(f"PENDING {o['side']} {o['code']} {o['qty']:g} limit ${o['limit_price']:,.2f} (order {oid})")
    return "\n".join(lines)


class Bot:
    def __init__(self, cfg: dict, tg: Telegram):
        self.cfg, self.tg = cfg, tg
        p = resolve(STATE_PATH)
        self.state = json.loads(p.read_text()) if p.exists() else {}
        self._last_exit_check = 0.0

    def _save_state(self) -> None:
        resolve(STATE_PATH).write_text(json.dumps(self.state))

    def ledger(self) -> PaperLedger:
        return PaperLedger(self.cfg)     # re-read each time: the console script may have changed it

    def say(self, text: str) -> None:
        self.tg.send(text)

    # ------------------------------------------------------------- commands
    def do_scan(self) -> None:
        self.say("Scanning 93 stocks... (about a minute)")
        try:
            with MoomooClient(self.cfg) as client:
                result = run_scan(self.cfg, client)
        except OpenDUnavailable:
            self.say("OpenD is not reachable. Open OpenD on the laptop and log in.")
            return
        save_scan(result)
        self.say(scan_message(result, self.cfg, self.ledger()))

    def do_swing(self, auto: bool = False) -> None:
        self.say(("Daily swing ideas. " if auto else "") + "Scanning the whole US market... (1-2 minutes)")
        now = mc.now_utc()
        try:
            with MoomooClient(self.cfg) as client:
                df, meta = run_swing_scan(self.cfg, client, now)
        except OpenDUnavailable:
            self.say("OpenD is not reachable. Open OpenD on the laptop and log in.")
            return
        n = int(self.cfg["swing"]["offer_top_n"]) if auto else len(df)
        self.say(format_swing(df.head(n), now, meta["market_phase"], meta["scanned"]) +
                 ("\n\nTo paper-buy one: /sbuy <number>. Ignore to skip; skipped ideas are tracked too."
                  if len(df) else ""))

    def do_exits(self, only_due: bool = False) -> None:
        lines, due = exits_report(self.ledger())
        if only_due and not due:
            return
        tail = ("\nDUE_NOW = send /sell before the US open. OVERDUE = sell now."
                if due else "")
        self.say("\n".join(lines) + tail)

    def do_order(self, side: str, ticker: str) -> None:
        run_order(self.cfg, self.ledger(), ticker, side, say=self.say,
                  approve=lambda t: telegram_approval(self.tg, t))

    def handle(self, text: str) -> None:
        parts = text.split()
        cmd = parts[0].lower().split("@")[0] if parts else ""
        arg = parts[1] if len(parts) > 1 else ""
        if cmd in ("/start", "/help"):
            self.say("Commands:\n" + HELP)
        elif cmd == "/scan":
            self.do_scan()
        elif cmd == "/swing":
            self.do_swing()
        elif cmd == "/sbuy":
            if not arg:
                self.say("Which idea? e.g. /sbuy 2 (number from the latest /swing list) or /sbuy QMCO")
                return
            run_swing_buy(self.cfg, arg, say=self.say, approve=lambda t: telegram_approval(self.tg, t))
        elif cmd == "/ssell":
            if not arg:
                self.say("Which stock? e.g. /ssell QMCO")
                return
            run_order(swing_cfg(self.cfg), swing_ledger(self.cfg), arg, "SELL", say=self.say,
                      approve=lambda t: telegram_approval(self.tg, t))
        elif cmd == "/sstatus":
            self.say("SWING account\n" + status_message(swing_ledger(self.cfg)))
        elif cmd == "/sstats":
            self.say(stats_text(self.cfg))
        elif cmd in ("/buy", "/sell"):
            if not arg:
                self.say(f"Which stock? e.g. {cmd} AAPL")
                return
            self.do_order(cmd[1:].upper(), arg)
        elif cmd == "/sync":
            run_sync(self.cfg, self.ledger(), self.say)
        elif cmd == "/exits":
            self.do_exits()
        elif cmd == "/status":
            self.say("EARNINGS account (/buy, /sell)\n" + status_message(self.ledger()) +
                     "\n\nSWING account (/sbuy, /ssell)\n" + status_message(swing_ledger(self.cfg)))
        elif cmd == "yes":
            self.say("No ticket is waiting for approval. Start one with /buy XYZ or /sell XYZ.")
        else:
            self.say("I didn't understand that. Send /help for the commands.")

    # ------------------------------------------------------------ schedule
    def scheduled(self) -> None:
        now = mc.now_utc()
        et = mc.to_et(now)
        d = et.date()
        if not mc.is_trading_day(d):
            return
        sw, phase = self.cfg["swing"], mc.market_phase(now)
        if (phase == "OPEN" and et.time() >= dtime.fromisoformat(sw["auto_scan_et"])
                and self.state.get("auto_swing") != str(d)):
            self.state["auto_swing"] = str(d)
            self._save_state()
            self.do_swing(auto=True)
        if phase in ("PRE_MARKET", "OPEN") and time.monotonic() - self._last_exit_check >= sw["stop_check_seconds"]:
            self._last_exit_check = time.monotonic()
            try:
                run_auto_exits(self.cfg, self.say, now)
            except OpenDUnavailable:
                log.warning("auto exits: OpenD unavailable")
        close_dt = mc.session_bounds(d)[1]
        close_t = close_dt.time()
        scan_at = (close_dt + timedelta(minutes=20)).time()     # 16:20, or 13:20 on early-close days
        if et.time() >= scan_at and self.state.get("auto_scan") != str(d):
            self.state["auto_scan"] = str(d)
            self._save_state()
            self.say("Daily post-close check:")
            self.do_scan()
            self.do_exits(only_due=True)
            sl = swing_ledger(self.cfg)
            if sl.pending:
                run_sync(swing_cfg(self.cfg), sl, self.say)
            try:
                with MoomooClient(self.cfg) as client:
                    update_tracking(client, now)
            except OpenDUnavailable:
                log.warning("swing tracking: OpenD unavailable")
            self.say(stats_text(self.cfg))
        if AUTO_SYNC_AT <= et.time() < close_t and self.state.get("auto_sync") != str(d):
            self.state["auto_sync"] = str(d)
            self._save_state()
            if self.ledger().pending:
                run_sync(self.cfg, self.ledger(), self.say)
            if swing_ledger(self.cfg).pending:
                run_sync(swing_cfg(self.cfg), swing_ledger(self.cfg), self.say)

    def run(self) -> None:
        self.tg.drain()
        self.say("Bot is running on the laptop. Send /help for commands.")
        print("Telegram bot running. Keep this window and OpenD open. Press Ctrl+C to stop.")
        while True:
            try:
                for msg in self.tg.messages(wait_s=25):
                    try:
                        self.handle(msg)
                    except Exception as e:           # one bad command must not kill the bot
                        log.exception("command failed: %s", msg)
                        self.say(f"Something went wrong: {type(e).__name__}: {e}")
                self.scheduled()
            except TelegramError as e:
                log.warning("telegram: %s", e)
                time.sleep(10)
            except KeyboardInterrupt:
                raise
            except Exception:
                log.exception("bot loop error")
                time.sleep(10)


def main() -> int:
    setup_logging("telegram_bot")
    conf = load_tg()
    if not conf:
        print("Telegram is not set up yet. Double-click 8_TELEGRAM_SETUP.bat first.")
        return 1
    tg = Telegram(conf["token"], conf["chat_id"])
    try:
        Bot(load_config(), tg).run()
    except KeyboardInterrupt:
        try:
            tg.send("Bot stopped on the laptop.")
        except TelegramError:
            pass
        print("Stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
