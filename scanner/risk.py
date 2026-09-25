"""Position sizing for the $300 paper account and a local paper ledger.

Moomoo's SIMULATE account has its own (large) virtual balance, so the $300
research account is tracked locally in outputs/paper_ledger.json. Orders are
sized against min(ledger cash, broker simulated cash). No leverage.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import resolve

NOT_EXECUTABLE = "NOT_EXECUTABLE_WITH_CURRENT_PAPER_CAPITAL"


@dataclass
class SizeResult:
    qty: float
    est_price: float
    notional: float
    status: str          # OK | NOT_EXECUTABLE_WITH_CURRENT_PAPER_CAPITAL | INVALID_PRICE
    note: str = ""


def size_position(est_price: float, cash: float, equity: float, cfg: dict) -> SizeResult:
    a = cfg["account"]
    if est_price is None or not math.isfinite(est_price) or est_price <= 0:
        return SizeResult(0, est_price or 0.0, 0.0, "INVALID_PRICE", "no valid price")
    budget = min(cash, equity * float(a["max_position_pct"]))
    if budget <= 0:
        return SizeResult(0, est_price, 0.0, NOT_EXECUTABLE, "no available cash")
    if a.get("fractional_shares_confirmed"):
        qty = math.floor(budget / est_price * 1000) / 1000  # moomoo: 3-dp qty precision
    else:
        qty = math.floor(budget / est_price)
    if qty <= 0:
        return SizeResult(0, est_price, 0.0, NOT_EXECUTABLE,
                          f"1 share costs ${est_price:,.2f} > position budget ${budget:,.2f} "
                          "(whole shares only; fractional not confirmed for SIMULATE)")
    return SizeResult(qty, est_price, round(qty * est_price, 2), "OK")


def estimated_costs(notional: float, side: str, cfg: dict) -> float:
    c = cfg["costs"]
    cost = notional * c["slippage_bps"] / 1e4 + c["commission_per_order"] + c["platform_fee_per_order"]
    if side.upper() == "SELL":
        cost += notional * c["regulatory_fee_bps_on_sells"] / 1e4
    return round(cost, 4)


class PaperLedger:
    """Local record of the $300 research account (cash + positions + history).

    A submitted order is NOT a fill. It sits in `pending` (reserving cash for a
    BUY, or shares for a SELL) until the broker reports its final status; only
    the quantity and average price the broker actually filled are booked.
    """

    def __init__(self, cfg: dict, path: str | None = None):
        self.cfg = cfg
        self.path = resolve(path or cfg["account"]["ledger_path"])
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
        else:
            self.state = {"starting_cash": cfg["account"]["starting_cash"],
                          "cash": cfg["account"]["starting_cash"], "positions": {}, "history": []}
        self.state.setdefault("pending", {})

    @property
    def cash(self) -> float:
        return float(self.state["cash"])

    @property
    def pending(self) -> dict:
        return self.state["pending"]

    @property
    def available_cash(self) -> float:
        """Cash minus what open BUY orders could still consume (limit price + est. costs)."""
        reserved = sum(o["qty"] * o["limit_price"] + o["est_fees"]
                       for o in self.pending.values() if o["side"] == "BUY")
        return self.cash - reserved

    def sellable_qty(self, code: str) -> float:
        held = self.position_qty(code)
        return held - sum(o["qty"] for o in self.pending.values() if o["side"] == "SELL" and o["code"] == code)

    def add_pending(self, order_id: str, code: str, side: str, qty: float, limit_price: float,
                    est_fees: float, acc_id: int, plan: dict | None = None) -> None:
        if order_id in self.pending:
            raise ValueError(f"ledger: order {order_id} already pending")
        self.pending[order_id] = {"code": code, "side": side.upper(), "qty": qty, "limit_price": limit_price,
                                  "est_fees": est_fees, "acc_id": acc_id, "plan": plan,
                                  "submitted_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    def resolve_pending(self, order_id: str, filled_qty: float, avg_price: float, status: str) -> dict:
        """Close out a pending order with the broker's FINAL fill. Idempotent per order_id."""
        o = self.pending.pop(order_id)
        booked = None
        if filled_qty > 0:
            fees = estimated_costs(filled_qty * avg_price, o["side"], self.cfg)
            self.record_fill(o["code"], o["side"], filled_qty, avg_price, fees, order_id,
                             note=f"broker fill, final status {status}")
            if o["side"] == "BUY" and o.get("plan") and o["code"] in self.state["positions"]:
                self.state["positions"][o["code"]]["plan"] = o["plan"]
            booked = {"qty": filled_qty, "price": avg_price, "fees_est": fees}
        else:
            self.state["history"].append({"ts_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                          "code": o["code"], "side": o["side"], "qty": 0, "price": None,
                                          "fees_est": 0.0, "order_id": order_id,
                                          "note": f"no fill, final status {status}"})
        return {"order_id": order_id, "code": o["code"], "side": o["side"], "status": status, "booked": booked}

    def equity(self, marks: dict[str, float] | None = None) -> float:
        marks = marks or {}
        pos_val = sum(p["qty"] * marks.get(code, p["avg_price"]) for code, p in self.state["positions"].items())
        return self.cash + pos_val

    def has_exposure(self, code: str) -> bool:
        """Held, or an order for it is still pending (the backtest never adds to an open position)."""
        return self.position_qty(code) > 0 or any(o["code"] == code for o in self.pending.values())

    def position_plan(self, code: str) -> dict | None:
        return self.state["positions"].get(code, {}).get("plan")

    def position_qty(self, code: str) -> float:
        return float(self.state["positions"].get(code, {}).get("qty", 0))

    def record_fill(self, code: str, side: str, qty: float, price: float, fees: float, order_id: str,
                    note: str = "broker fill") -> None:
        side = side.upper()
        pos = self.state["positions"].get(code, {"qty": 0.0, "avg_price": 0.0})
        if side == "BUY":
            cost = qty * price + fees
            if cost > self.cash + 1e-9:
                raise ValueError("ledger: insufficient paper cash")
            new_qty = pos["qty"] + qty
            pos["avg_price"] = (pos["qty"] * pos["avg_price"] + qty * price) / new_qty
            pos["qty"] = new_qty
            self.state["cash"] = self.cash - cost
        else:
            if qty > pos["qty"] + 1e-9:
                raise ValueError("ledger: cannot sell more than held (no shorting)")
            pos["qty"] -= qty
            self.state["cash"] = self.cash + qty * price - fees
        if pos["qty"] <= 1e-9:
            self.state["positions"].pop(code, None)
        else:
            self.state["positions"][code] = pos
        self.state["history"].append({"ts_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                      "code": code, "side": side, "qty": qty, "price": price,
                                      "fees_est": fees, "order_id": order_id, "note": note})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, indent=2))
