"""One-off test: does Moomoo's PAPER (SIMULATE) account accept fractional US shares?

    python scripts/fractional_test.py            # 0.1 share of US.AAPL
    python scripts/fractional_test.py US.MSFT 0.05

Places ONE small paper BUY (you must type YES), reports whether it filled, and
offers to sell it back. Uses its own throwaway ledger
(outputs/fractional_test_ledger.json), so the $300 research ledger is untouched.
Run it while the US market is open (21:30-04:00 SGT in US summer time).
The result is saved to outputs/logs/fractional_test_result.json; set
account.fractional_shares_confirmed: true in config/settings.yaml only if it
says SUPPORTED.
"""
from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timezone
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
from scanner.paper_execution import (OrderTicket, PaperOrderRejected, find_simulate_account,  # noqa: E402
                                     open_trade_context, query_order, request_human_approval,
                                     submit_paper_order, wait_for_order)
from scanner.risk import PaperLedger  # noqa: E402

TEST_LEDGER = "outputs/fractional_test_ledger.json"
RESULT = "outputs/logs/fractional_test_result.json"


def one_order(tctx, ledger, cfg, code, side, qty, price, reason) -> dict:
    t = OrderTicket(code=code, side=side, qty=qty, limit_price=price, reason=reason, signals=(),
                    remaining_cash_after=round(ledger.available_cash - qty * price, 2))
    approval = request_human_approval(t)
    if approval is None:
        return {"status": "NOT_APPROVED"}
    try:
        res = submit_paper_order(tctx, t, approval, ledger, cfg)
    except PaperOrderRejected as e:
        return {"status": "REJECTED", "message": str(e)}
    oid = res["order_id"]
    print(f"Submitted order {oid}; waiting up to 30 s for the paper fill...")
    wait_for_order(tctx, ledger, oid, timeout_s=30)
    pend = ledger.pending.get(oid)
    q = query_order(tctx, oid, code, res["acc_id"],
                    pend["submitted_utc"] if pend else datetime.now(timezone.utc).isoformat())
    return {"status": (q or {}).get("status", "NOT_FOUND"), "order_id": oid,
            "dealt_qty": (q or {}).get("dealt_qty"), "dealt_avg_price": (q or {}).get("dealt_avg_price")}


def main() -> int:
    setup_logging("fractional_test")
    code = sys.argv[1] if len(sys.argv) > 1 else "US.AAPL"
    code = code if code.startswith("US.") else f"US.{code}"
    qty = float(sys.argv[2]) if len(sys.argv) > 2 else 0.1
    now = mc.now_utc()
    if mc.market_phase(now) != "OPEN":
        print(f"The US market is {mc.market_phase(now)}. Run this while it is OPEN "
              "(21:30-04:00 SGT in US summer time, 22:30-05:00 in winter).")
        return 1
    cfg = copy.deepcopy(load_config())
    cfg["account"]["fractional_shares_confirmed"] = True    # THIS TEST ONLY: lets a fractional ticket through
    ledger = PaperLedger(cfg, path=TEST_LEDGER)
    if not resolve(TEST_LEDGER).exists():
        ledger.state["cash"] = ledger.state["starting_cash"] = 100_000.0   # throwaway test ledger
    try:
        with MoomooClient(cfg) as client:
            last = float(client.snapshot([code]).iloc[0]["last_price"])
    except OpenDUnavailable as e:
        print(e)
        return 2
    print("=" * 60)
    print(f"FRACTIONAL-SHARE TEST: paper BUY of {qty:g} share of {code} (~${qty * last:,.2f} of FAKE money).")
    print("This does not touch your $300 research ledger.")
    print("=" * 60)
    tctx = open_trade_context(cfg)
    out = {"ts_utc": now.isoformat(timespec="seconds"), "code": code, "qty": qty}
    try:
        find_simulate_account(tctx)
        buy = one_order(tctx, ledger, cfg, code, "BUY", qty, round(last * 1.01, 2),
                        "FRACTIONAL-SHARE TEST (paper). Limit 1% above last so it fills now.")
        out["buy"] = buy
        filled = buy.get("status") == "FILLED_ALL" and abs((buy.get("dealt_qty") or 0) - qty) < 1e-6
        if buy["status"] == "NOT_APPROVED":
            verdict = "NOT_TESTED"
        elif filled:
            verdict = "SUPPORTED"
        elif buy["status"] in ("REJECTED", "FAILED", "SUBMIT_FAILED", "DISABLED"):
            verdict = "NOT_SUPPORTED"
        else:
            verdict = "UNCLEAR"
        out["verdict"] = verdict
        print(f"\nBUY result: {buy}")
        print(f"VERDICT: fractional shares in the PAPER account = {verdict}")
        if verdict == "SUPPORTED":
            print("\nNow selling the test share back so the paper account is tidy (type YES again).")
            out["sell"] = one_order(tctx, ledger, cfg, code, "SELL", qty, round(last * 0.99, 2),
                                    "FRACTIONAL-SHARE TEST cleanup: sell the test share back.")
            print(f"SELL result: {out['sell']}")
        elif verdict == "UNCLEAR":
            print("The order is still working or its status is unusual. Check Paper Trading in the Moomoo app, "
                  "and tell Claude what you see.")
    finally:
        tctx.close()
    p = resolve(RESULT)
    p.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nSaved: {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
