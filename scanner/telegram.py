"""Minimal Telegram Bot API client + phone approval for paper orders.

Security model
--------------
* The bot token and your chat id live in config/telegram.local.yaml, which the
  setup script writes from what YOU type. It is git-ignored and never printed.
* Only messages from the configured chat id are read; everything else is ignored.
* Approving a ticket needs "YES <code>", where <code> comes from that exact
  ticket's fingerprint. A bare "YES", or a code from another ticket, cancels.
  The reply must arrive after the ticket was sent and before the timeout.
* The Approval object is still minted only by paper_execution.request_human_approval;
  this module just supplies its input/print functions.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Callable

import yaml

from .config import resolve
from .paper_execution import Approval, OrderTicket, request_human_approval

log = logging.getLogger(__name__)

CONFIG_PATH = "config/telegram.local.yaml"
MAX_LEN = 4000   # Telegram's hard limit is 4096 characters per message


class TelegramError(RuntimeError):
    pass


def _http_post(url: str, payload: dict, timeout: float) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            raise TelegramError(f"HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TelegramError(f"network: {e}") from None


class Telegram:
    def __init__(self, token: str, chat_id: int | None = None, http: Callable = _http_post):
        if not token or ":" not in token:
            raise TelegramError("that does not look like a bot token (expected '123456:ABC...')")
        self._base = f"https://api.telegram.org/bot{token}/"
        self.chat_id = int(chat_id) if chat_id is not None else None
        self._http = http
        self.offset = 0

    def call(self, method: str, timeout: float = 20, **params) -> dict | list:
        res = self._http(self._base + method, params, timeout)
        if not res.get("ok"):
            # never include the URL (it contains the token) in errors
            raise TelegramError(f"{method}: {res.get('description', 'failed')}")
        return res["result"]

    def me(self) -> dict:
        return self.call("getMe")

    def send(self, text: str) -> None:
        if self.chat_id is None:
            raise TelegramError("no chat id configured")
        text = text or "(empty)"
        for i in range(0, len(text), MAX_LEN):
            self.call("sendMessage", chat_id=self.chat_id, text=text[i:i + MAX_LEN],
                      disable_web_page_preview=True)

    def _get_updates(self, wait_s: int) -> list[dict]:
        ups = self._http(self._base + "getUpdates",
                         {"offset": self.offset, "timeout": wait_s, "allowed_updates": ["message"]},
                         wait_s + 10)
        if not ups.get("ok"):
            raise TelegramError(f"getUpdates: {ups.get('description', 'failed')}")
        ups = ups["result"]
        if ups:
            self.offset = ups[-1]["update_id"] + 1
        return ups

    def messages(self, wait_s: int = 0) -> list[str]:
        """Text messages from the configured chat only (others are logged and dropped)."""
        out = []
        for u in self._get_updates(wait_s):
            m = u.get("message") or {}
            cid = (m.get("chat") or {}).get("id")
            if cid != self.chat_id:
                log.warning("ignored a Telegram message from an unknown chat")
                continue
            if isinstance(m.get("text"), str):
                out.append(m["text"].strip())
        return out

    def drain(self) -> None:
        """Skip anything already waiting, so an old message can never answer a new question."""
        while self._get_updates(0):
            pass


def load_config() -> dict | None:
    p = resolve(CONFIG_PATH)
    if not p.exists():
        return None
    return yaml.safe_load(p.read_text(encoding="utf-8")) or None


def save_config(token: str, chat_id: int) -> None:
    p = resolve(CONFIG_PATH)
    p.write_text(yaml.safe_dump({"token": token, "chat_id": int(chat_id)}), encoding="utf-8")


def approval_code(ticket: OrderTicket) -> str:
    return ticket.fingerprint()[:6].upper()


def telegram_approval(tg: Telegram, ticket: OrderTicket, timeout_s: float = 900,
                      clock=time.monotonic) -> Approval | None:
    """Send the ticket; return an Approval only for the exact reply 'YES <code>' in time."""
    code = approval_code(ticket)
    tg.drain()

    def show(text: str) -> None:
        tg.send(text)

    def ask(_prompt: str) -> str:
        tg.send(f"To send this PAPER order reply exactly:\nYES {code}\n\n"
                f"Anything else cancels. Expires in {int(timeout_s // 60)} min.")
        deadline = clock() + timeout_s
        while clock() < deadline:
            for msg in tg.messages(wait_s=int(max(1, min(25, deadline - clock())))):
                if msg.upper().split() == ["YES", code]:
                    return "YES"
                return ""            # any other reply cancels
        tg.send("No reply in time. NO ORDER was sent.")
        return ""

    return request_human_approval(ticket, input_fn=ask, print_fn=show)
