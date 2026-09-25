"""Human-approved PAPER order (TrdEnv.SIMULATE only). There is no live mode.

    python scripts/paper_order.py --ticker US.XYZ              # BUY sized from latest scan
    python scripts/paper_order.py --ticker US.XYZ --side SELL  # close paper-ledger position
    python scripts/paper_order.py --sync                       # book fills of pending orders; no new order
    python scripts/paper_order.py --exits                      # positions due to be sold (planned exits)

BUYs follow the backtest: the candidate must come from a scan of COMPLETED bars
(run the scanner after the US close), and the order is submitted before the next
open as a DAY limit capped at execution.entry_limit_cap_pct above the signal close.

You must type YES at the prompt. Anything else = NO ORDER.
The same workflow is available from Telegram: scripts/telegram_bot.py.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from scanner.config import load_config  # noqa: E402
from scanner.logsetup import setup_logging  # noqa: E402
from scanner.order_flow import exits_report, run_order, run_sync  # noqa: E402
from scanner.paper_execution import request_human_approval  # noqa: E402
from scanner.risk import PaperLedger  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker")
    ap.add_argument("--sync", action="store_true", help="only book fills of pending paper orders")
    ap.add_argument("--exits", action="store_true", help="list positions and their planned exit sessions")
    ap.add_argument("--side", choices=["BUY", "SELL"], default="BUY")
    ap.add_argument("--qty", type=float, default=None, help="override quantity (still validated)")
    ap.add_argument("--limit", type=float, default=None,
                    help="limit price (default BUY: signal close + cap%%; SELL: last price - floor%%)")
    args = ap.parse_args()
    if not (args.sync or args.exits) and not args.ticker:
        ap.error("--ticker is required unless --sync or --exits")
    setup_logging("paper_order")
    cfg = load_config()
    ledger = PaperLedger(cfg)
    if args.sync:
        return run_sync(cfg, ledger, print)
    if args.exits:
        lines, _ = exits_report(ledger)
        print("\n".join(lines))
        print("DUE_NOW = place the sell tonight, before the US open. OVERDUE = the planned exit has passed: sell now.")
        print("To sell:  python scripts/paper_order.py --ticker US.XYZ --side SELL")
        return 0
    return run_order(cfg, ledger, args.ticker, args.side, say=print, approve=request_human_approval,
                     qty=args.qty, limit=args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
