"""One-time Telegram setup: connect YOUR bot to YOUR Telegram account.

    python scripts/telegram_setup.py

You type the bot token yourself (hidden while typing). It is saved only in
config/telegram.local.yaml on this laptop (git-ignored) and never printed.
"""
from __future__ import annotations

import getpass
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from scanner.telegram import Telegram, TelegramError, save_config  # noqa: E402

STEPS = """
TELEGRAM SETUP (one time, ~2 minutes)
=====================================
On your phone, in Telegram:
  1. Search for  @BotFather  (blue tick) and open it.
  2. Send:  /newbot
  3. Give it any name, e.g.  My Paper Scanner
  4. Give it a username ending in 'bot', e.g.  jo_paper_scanner_bot
  5. BotFather replies with a TOKEN that looks like  1234567890:AAH...xyz
     Copy it. Treat it like a password: anyone with it can control the bot.
"""


def main() -> int:
    print(STEPS)
    token = getpass.getpass("Paste the token here and press Enter (it stays hidden while you paste): ").strip()
    try:
        tg = Telegram(token)
        me = tg.me()
    except TelegramError as e:
        print(f"\nThat token did not work ({e}). Check you copied all of it, then run setup again.")
        return 1
    name = me.get("username", "your bot")
    print(f"\nToken OK: your bot is @{name}")
    print(f"\nNow in Telegram: search for @{name}, open it, and press START (or send 'hi').")
    print("Waiting up to 3 minutes for your message...")
    tg._get_updates(0)          # skip anything older than now
    deadline = time.monotonic() + 180
    chat = None
    while time.monotonic() < deadline and chat is None:
        try:
            for u in tg._get_updates(20):
                m = u.get("message") or {}
                c = m.get("chat") or {}
                if c.get("type") == "private":
                    chat = c
                    break
        except TelegramError as e:
            print(f"  (network hiccup: {e}; retrying)")
            time.sleep(3)
    if chat is None:
        print("\nNo message arrived. Run setup again and press START in the bot chat.")
        return 1
    who = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("username", "?")
    ans = input(f"\nGot a message from '{who}'. Is that you? Type y and press Enter: ").strip().lower()
    if ans != "y":
        print("Not saved. Run setup again.")
        return 1
    save_config(token, chat["id"])
    tg.chat_id = chat["id"]
    tg.send("Connected. This bot will send you paper-trading scans and order tickets.\n"
            "Nothing is ever sent to Moomoo unless you reply YES + the ticket's code.\n"
            "Start the bot on your laptop with 9_TELEGRAM_BOT.bat, then send /help here.")
    print("\nSaved. Check Telegram for a 'Connected' message.")
    print("Next: double-click 9_TELEGRAM_BOT.bat and keep that window open.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
