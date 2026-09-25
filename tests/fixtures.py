"""MOCK fixtures for unit tests. Nothing here touches the real Moomoo API.

Synthetic data is used ONLY to test mechanics (timing, maths, safety). It is
never used to make claims about strategy performance.
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
from datetime import date
from enum import Enum

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scanner import market_calendar as mc  # noqa: E402
from scanner.config import load_config  # noqa: E402


def cfg(**over) -> dict:
    c = load_config()
    c["api"]["backoff_base_seconds"] = 0.0
    tmp = tempfile.mkdtemp()
    c["account"]["ledger_path"] = os.path.join(tmp, "ledger.json")
    return c


def bars(start: date, n: int, price: float = 100.0, vol: float = 1_000_000, seed: int = 0,
         drift: float = 0.0, sigma: float = 0.01) -> pd.DataFrame:
    """Random-walk daily OHLCV on real US trading days."""
    rng = np.random.default_rng(seed)
    days, d = [], start
    while len(days) < n:
        if mc.is_trading_day(d):
            days.append(d)
        d = date.fromordinal(d.toordinal() + 1)
    rets = rng.normal(drift, sigma, n)
    close = price * np.cumprod(1 + rets)
    opn = np.r_[price, close[:-1]] * (1 + rng.normal(0, sigma / 4, n))
    high = np.maximum(opn, close) * (1 + np.abs(rng.normal(0, sigma / 3, n)))
    low = np.minimum(opn, close) * (1 - np.abs(rng.normal(0, sigma / 3, n)))
    v = vol * np.exp(rng.normal(0, 0.1, n))
    return pd.DataFrame({"open": opn, "high": high, "low": low, "close": close, "volume": v},
                        index=pd.Index(days, name="date"))


# ------------------------------------------------------------ fake moomoo SDK
class _TrdEnv(Enum):
    SIMULATE = "SIMULATE"


class _TrdSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class _OrderType(Enum):
    NORMAL = "NORMAL"


class _TIF(Enum):
    DAY = "DAY"


def fake_moomoo_module() -> types.ModuleType:
    m = types.ModuleType("moomoo")
    m.RET_OK, m.RET_ERROR = 0, -1
    m.TrdEnv, m.TrdSide, m.OrderType, m.TimeInForce = _TrdEnv, _TrdSide, _OrderType, _TIF
    m.KLType = types.SimpleNamespace(K_DAY="K_DAY", K_1M="K_1M")
    m.AuType = types.SimpleNamespace(QFQ="qfq", NONE="none")
    m.Market = types.SimpleNamespace(US="US")
    m.SecurityType = types.SimpleNamespace(STOCK="STOCK")
    return m


class FakeTradeCtx:
    """Records place_order kwargs; returns a SIMULATE account.

    `orders` maps order_id -> dict(order_status, dealt_qty, dealt_avg_price) and
    is what the order-status queries report. `history_orders` is the same for
    orders only visible through history_order_list_query (previous days).
    """

    def __init__(self):
        self.calls = []
        self.query_calls = []
        self.orders: dict[str, dict] = {}
        self.history_orders: dict[str, dict] = {}

    def _frame(self, orders):
        return pd.DataFrame([{"order_id": k, **v} for k, v in orders.items()],
                            columns=["order_id", "order_status", "dealt_qty", "dealt_avg_price"])

    def order_list_query(self, **kw):
        self.query_calls.append(("order_list_query", kw))
        return 0, self._frame({k: v for k, v in self.orders.items() if k == kw.get("order_id")})

    def history_order_list_query(self, **kw):
        self.query_calls.append(("history_order_list_query", kw))
        return 0, self._frame(self.history_orders)

    def get_acc_list(self):
        return 0, pd.DataFrame([{"acc_id": 111, "trd_env": "SIMULATE", "trdmarket_auth": "['US']",
                                 "sim_acc_type": "STOCK"}])

    def place_order(self, **kw):
        self.calls.append(kw)
        return 0, pd.DataFrame([{"order_id": "OID1"}])

    def close(self):
        pass

    def accinfo_query(self, **kw):
        return 0, pd.DataFrame([{"cash": 1_000_000.0}])


class use_fake_moomoo:
    """Context manager: temporarily replace sys.modules['moomoo'] with the fake."""

    def __enter__(self):
        self.prev = sys.modules.get("moomoo")
        sys.modules["moomoo"] = fake_moomoo_module()
        return sys.modules["moomoo"]

    def __exit__(self, *exc):
        if self.prev is None:
            sys.modules.pop("moomoo", None)
        else:
            sys.modules["moomoo"] = self.prev
