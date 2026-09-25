"""THE ONLY order-placement code in this repository. Paper trading only.

Safety design
-------------
* Every order uses trd_env=TrdEnv.SIMULATE, written literally below. The
  environment is not a parameter, not configurable, and there is no live mode.
* Moomoo's own place_order() DEFAULTS to the live environment if trd_env is
  omitted, which is why the environment is always passed explicitly here and why
  tests forbid any other module from calling place_order().
* The target account is double-checked to be a SIMULATE account before sending.
* A human must approve each order interactively (typing YES). Scanner code
  never calls this module.
* Every broker QUERY (order status, account info) also passes
  trd_env=TrdEnv.SIMULATE literally: moomoo's query functions default to the
  live environment too.
* A submitted order is booked as PENDING, never as a fill. sync_pending_orders()
  books only what the broker reports as actually filled.
* Pre-approved exits (swing trades): a BUY ticket may carry a stop and a time
  exit with auto_exit=True. The human sees both on the ticket and approves them
  with the buy. Later SELLs of that position may then use
  preapproved_exit_approval(), which only works for a SELL of a position whose
  stored plan came from such an approved ticket. It can never approve a BUY.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Callable

from .risk import PaperLedger, estimated_costs

log = logging.getLogger(__name__)

PAPER_ENV_NAME = "SIMULATE"
# Final order states (moomoo OrderStatus). Anything else may still fill.
TERMINAL_STATUSES = {"FILLED_ALL", "CANCELLED_PART", "CANCELLED_ALL", "FAILED", "SUBMIT_FAILED",
                     "DISABLED", "DELETED", "FILL_CANCELLED"}
_APPROVAL_KEY = object()   # only request_human_approval() can mint an Approval


class PaperOrderRejected(RuntimeError):
    pass


@dataclass(frozen=True)
class OrderTicket:
    code: str
    side: str            # BUY | SELL
    qty: float
    limit_price: float
    reason: str
    signals: tuple
    remaining_cash_after: float
    entry_session: str = ""      # BUY: session whose OPEN the order is meant to fill at
    planned_exit: str = ""       # BUY: session whose CLOSE is the backtest exit
    horizon: int = 0             # BUY: sessions held
    exit_at: str = ""            # BUY: 'close' (of planned_exit) or 'next_open' (open of planned_exit)
    kind: str = ""               # "swing" for swing trades ("" = earnings strategy)
    stop_price: float = 0.0      # BUY (swing): stop-loss level
    auto_exit: bool = False      # BUY (swing): the bot may SELL at the stop / time exit without asking again

    def plan(self) -> dict | None:
        if self.side != "BUY" or not self.planned_exit:
            return None
        p = {"entry_session": self.entry_session, "planned_exit": self.planned_exit, "horizon": self.horizon,
             "exit_at": self.exit_at or "close"}
        if self.kind:
            p.update(kind=self.kind, stop_price=self.stop_price, auto_exit=self.auto_exit)
        return p

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, default=str).encode()).hexdigest()

    def render(self) -> str:
        return "\n".join([
            "=" * 60,
            "PAPER ORDER (TrdEnv.SIMULATE) - REVIEW CAREFULLY",
            "=" * 60,
            f"Ticker               : {self.code}",
            f"Side                 : {self.side}",
            f"Quantity             : {self.qty:g}",
            f"Estimated price      : ${self.limit_price:,.2f} (limit)",
            f"Estimated notional   : ${self.qty * self.limit_price:,.2f}",
            f"Reason               : {self.reason}",
            f"Signals              : {', '.join(self.signals) or '-'}",
            f"Remaining sim. cash  : ${self.remaining_cash_after:,.2f}",
        ] + ([f"Stop-loss            : ${self.stop_price:,.2f}  (bot SELLS automatically if the price trades at/below it in US hours)",
              f"Time exit            : open of {self.planned_exit} (bot SELLS automatically after {self.horizon} session(s))",
              "Auto exits           : " + ("ON - approving this ticket approves those two sells too" if self.auto_exit
                                          else "OFF - the bot will ask you first")]
             if self.kind == "swing" else []) + ([
              f"Entry                : open of {self.entry_session} (DAY limit; no fill if it gaps above the limit)",
              f"Planned exit         : {'open' if self.exit_at == 'next_open' else 'close'} of {self.planned_exit} "
              f"(hold {self.horizon} session(s), as backtested)"]
             if self.planned_exit and self.kind != "swing" else []) + ["=" * 60])


class Approval:
    def __init__(self, fingerprint: str, _key: object):
        if _key is not _APPROVAL_KEY:
            raise PaperOrderRejected("Approvals can only be created by request_human_approval().")
        self.fingerprint = fingerprint


def request_human_approval(ticket: OrderTicket, input_fn: Callable[[str], str] = input,
                           print_fn: Callable[[str], None] = print) -> Approval | None:
    """Show the ticket; return an Approval only if the human types exactly YES."""
    print_fn(ticket.render())
    try:
        answer = input_fn("Type YES to submit this PAPER order (anything else cancels): ")
    except EOFError:
        answer = ""
    if answer.strip() != "YES":
        print_fn("Not approved. NO ORDER was sent.")
        return None
    return Approval(ticket.fingerprint(), _APPROVAL_KEY)


def preapproved_exit_approval(ticket: OrderTicket, ledger: PaperLedger) -> Approval:
    """Approval for an automatic SELL that the human already approved as part of the BUY ticket.

    Only a SELL, only of a position whose stored plan has auto_exit=True and came
    from a human-approved ticket, and never for more than is held and not already
    being sold. Everything else raises.
    """
    if ticket.side != "SELL":
        raise PaperOrderRejected("pre-approval only ever covers SELLs")
    plan = ledger.position_plan(ticket.code)
    if not plan or not plan.get("auto_exit") or not plan.get("approved_fingerprint"):
        raise PaperOrderRejected(f"{ticket.code}: no human-approved auto-exit plan. Ask for approval instead.")
    if ticket.qty > ledger.sellable_qty(ticket.code) + 1e-9:
        raise PaperOrderRejected(f"{ticket.code}: sell qty exceeds sellable position")
    return Approval(ticket.fingerprint(), _APPROVAL_KEY)


def validate_ticket(ticket: OrderTicket, ledger: PaperLedger, cfg: dict) -> None:
    if ticket.side not in ("BUY", "SELL"):
        raise PaperOrderRejected(f"invalid side {ticket.side!r}")
    if not (isinstance(ticket.qty, (int, float)) and math.isfinite(ticket.qty) and ticket.qty > 0):
        raise PaperOrderRejected(f"invalid quantity {ticket.qty!r}")
    if not cfg["account"].get("fractional_shares_confirmed") and ticket.qty != int(ticket.qty):
        raise PaperOrderRejected("fractional quantity but fractional shares are not confirmed for SIMULATE")
    if not (math.isfinite(ticket.limit_price) and ticket.limit_price > 0):
        raise PaperOrderRejected(f"invalid price {ticket.limit_price!r}")
    notional = ticket.qty * ticket.limit_price
    if ticket.side == "BUY":
        need = notional + estimated_costs(notional, "BUY", cfg)
        have = ledger.available_cash
        if need > have + 1e-9:
            raise PaperOrderRejected(f"insufficient paper cash: need ${need:,.2f}, available ${have:,.2f} "
                                     f"(cash ${ledger.cash:,.2f} minus open BUY orders)")
    else:
        held = ledger.sellable_qty(ticket.code)
        if ticket.qty > held + 1e-9:
            raise PaperOrderRejected(f"cannot sell {ticket.qty:g}; paper ledger has {held:g} sellable "
                                     "(held minus open SELL orders; no shorting)")


def _round_price(p: float) -> float:
    return round(p, 2) if p >= 1 else round(p, 4)


def open_trade_context(cfg: dict):
    from moomoo import OpenSecTradeContext, SecurityFirm, TrdMarket
    o = cfg["opend"]
    return OpenSecTradeContext(filter_trdmarket=TrdMarket.US, host=o["host"], port=int(o["port"]),
                               security_firm=getattr(SecurityFirm, o.get("security_firm", "NONE")))


def find_simulate_account(trade_ctx) -> int:
    from moomoo import RET_OK
    ret, accs = trade_ctx.get_acc_list()
    if ret != RET_OK:
        raise PaperOrderRejected(f"get_acc_list failed: {accs}")
    sim = accs[accs["trd_env"].astype(str).str.upper().str.endswith(PAPER_ENV_NAME)]
    if "trdmarket_auth" in sim.columns:
        sim = sim[sim["trdmarket_auth"].astype(str).str.contains("US")]
    if "sim_acc_type" in sim.columns:
        stock = sim[sim["sim_acc_type"].astype(str).str.contains("STOCK")]
        sim = stock if len(stock) else sim
    if sim.empty:
        raise PaperOrderRejected("No US SIMULATE (paper) account found in OpenD.")
    return int(sim.iloc[0]["acc_id"])


def simulate_cash(trade_ctx, acc_id: int) -> float | None:
    from moomoo import RET_OK, TrdEnv
    ret, info = trade_ctx.accinfo_query(trd_env=TrdEnv.SIMULATE, acc_id=acc_id)
    if ret != RET_OK or info is None or len(info) == 0:
        return None
    for col in ("cash", "us_cash", "power"):
        if col in info.columns:
            try:
                return float(info.iloc[0][col])
            except (TypeError, ValueError):
                continue
    return None


def probe_order_fields(trade_ctx, acc_id: int) -> tuple[bool, list[str]]:
    """READ-ONLY: do SIMULATE order queries work and expose the fill fields sync relies on?"""
    from moomoo import RET_OK, TrdEnv

    start = (date.today() - timedelta(days=30)).isoformat()
    ret, data = trade_ctx.history_order_list_query(start=start, trd_env=TrdEnv.SIMULATE, acc_id=acc_id)
    if ret != RET_OK:
        raise PaperOrderRejected(f"history_order_list_query failed: {data}")
    cols = list(getattr(data, "columns", []))
    return all(c in cols for c in ("order_id", "order_status", "dealt_qty", "dealt_avg_price")), cols


def submit_paper_order(trade_ctx, ticket: OrderTicket, approval: Approval | None,
                       ledger: PaperLedger, cfg: dict) -> dict:
    """Submit ONE human-approved paper order. Environment is fixed to SIMULATE."""
    from moomoo import RET_OK, OrderType, TimeInForce, TrdEnv, TrdSide

    if approval is None or not isinstance(approval, Approval):
        raise PaperOrderRejected("Human approval required. NO ORDER.")
    if approval.fingerprint != ticket.fingerprint():
        raise PaperOrderRejected("Approval does not match this exact ticket. NO ORDER.")
    validate_ticket(ticket, ledger, cfg)

    acc_id = find_simulate_account(trade_ctx)
    price = _round_price(ticket.limit_price)
    ret, data = trade_ctx.place_order(
        price=price,
        qty=ticket.qty,
        code=ticket.code,
        trd_side=TrdSide.BUY if ticket.side == "BUY" else TrdSide.SELL,
        order_type=OrderType.NORMAL,          # limit order
        trd_env=TrdEnv.SIMULATE,              # PAPER ONLY - never parameterised
        acc_id=acc_id,
        time_in_force=TimeInForce.DAY,
        remark="paper-scanner",
    )
    if ret != RET_OK:
        raise PaperOrderRejected(f"moomoo rejected paper order: {data}")
    order_id = str(data.iloc[0]["order_id"]) if hasattr(data, "iloc") and len(data) else ""
    if not order_id:
        raise PaperOrderRejected("moomoo accepted the order but returned no order_id; check the moomoo app. "
                                 "Ledger NOT changed.")
    fees = estimated_costs(ticket.qty * price, ticket.side, cfg)
    plan = ticket.plan()
    if plan is not None:
        plan["approved_fingerprint"] = approval.fingerprint     # proof the plan was on a human-approved ticket
    ledger.add_pending(order_id, ticket.code, ticket.side, ticket.qty, price, fees, acc_id, plan=plan)
    ledger.save()
    return {"order_id": order_id, "acc_id": acc_id, "env": PAPER_ENV_NAME, "price": price, "qty": ticket.qty,
            "ledger": "PENDING until the broker reports a fill (run sync)"}


def _status_str(x) -> str:
    return str(x).split(".")[-1].strip().upper()


def query_order(trade_ctx, order_id: str, code: str, acc_id: int, submitted_utc: str) -> dict | None:
    """Current state of one SIMULATE order: {status, dealt_qty, dealt_avg_price}, or None if not found."""
    from moomoo import RET_OK, TrdEnv

    ret, data = trade_ctx.order_list_query(order_id=order_id, trd_env=TrdEnv.SIMULATE, acc_id=acc_id,
                                           refresh_cache=True)
    rows = data[data["order_id"].astype(str) == order_id] if ret == RET_OK and hasattr(data, "empty") and len(data) else None
    if rows is None or rows.empty:
        # order_list_query only covers today's orders; older ones need the history query.
        # submitted_utc's date can be a day AFTER the US/Eastern order date: start a day earlier.
        start = (date.fromisoformat(submitted_utc[:10]) - timedelta(days=1)).isoformat()
        ret, data = trade_ctx.history_order_list_query(code=code, start=start, trd_env=TrdEnv.SIMULATE,
                                                       acc_id=acc_id)
        if ret != RET_OK or not hasattr(data, "empty") or data.empty:
            return None
        rows = data[data["order_id"].astype(str) == order_id]
        if rows.empty:
            return None
    r = rows.iloc[0]
    status = _status_str(r.get("order_status"))
    qty = float(r.get("dealt_qty") or 0)
    px = float(r.get("dealt_avg_price") or 0)
    if status == "FILL_CANCELLED":   # the broker rolled the fill back: nothing was bought or sold
        qty, px = 0.0, 0.0
    return {"status": status, "dealt_qty": qty, "dealt_avg_price": px}


def sync_pending_orders(trade_ctx, ledger: PaperLedger) -> list[dict]:
    """Book the final result of every pending order that has reached a terminal state.

    Orders still working (SUBMITTED, FILLED_PART, ...) stay pending. Returns one
    summary per pending order. Saves the ledger if anything changed.
    """
    out, changed = [], False
    for oid, o in list(ledger.pending.items()):
        q = query_order(trade_ctx, oid, o["code"], o["acc_id"], o["submitted_utc"])
        if q is None:
            log.warning("order %s not found in SIMULATE order lists; left pending", oid)
            out.append({"order_id": oid, "code": o["code"], "side": o["side"], "status": "NOT_FOUND", "booked": None})
            continue
        if q["status"] in TERMINAL_STATUSES:
            if q["dealt_qty"] > 0 and not q["dealt_avg_price"] > 0:
                log.error("order %s: filled qty %s but no avg price; left pending", oid, q["dealt_qty"])
                out.append({"order_id": oid, "code": o["code"], "side": o["side"], "status": q["status"], "booked": None})
                continue
            out.append(ledger.resolve_pending(oid, q["dealt_qty"], q["dealt_avg_price"], q["status"]))
            changed = True
        else:
            out.append({"order_id": oid, "code": o["code"], "side": o["side"], "status": q["status"],
                        "booked": None, "dealt_so_far": q["dealt_qty"]})
    if changed:
        ledger.save()
    return out


def wait_for_order(trade_ctx, ledger: PaperLedger, order_id: str, timeout_s: float = 15.0,
                   poll_s: float = 2.0, sleep=time.sleep) -> dict | None:
    """Poll until `order_id` is no longer pending or the timeout passes. Returns its last summary."""
    deadline = time.monotonic() + timeout_s
    last = None
    while True:
        for s in sync_pending_orders(trade_ctx, ledger):
            if s["order_id"] == order_id:
                last = s
        if order_id not in ledger.pending or time.monotonic() >= deadline:
            return last
        sleep(poll_s)
