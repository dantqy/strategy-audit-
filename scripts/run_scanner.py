"""SCAN -> RANK -> DISPLAY (LIVE API). Never places orders.

    python scripts/run_scanner.py [-v]
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
from scanner.moomoo_client import MoomooClient, OpenDUnavailable  # noqa: E402
from scanner.scanner import format_terminal, run_scan, save_scan  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logfile = setup_logging("scanner", args.verbose)
    cfg = load_config()
    try:
        with MoomooClient(cfg) as client:
            result = run_scan(cfg, client)
    except OpenDUnavailable as e:
        print(e)
        return 2
    print(format_terminal(result))
    j, c = save_scan(result)
    print(f"\nSaved: {j}\n       {c}\nLog:   {logfile}")
    print(f"Cache hits: {result['cache_hits']} | API history fetches: {result['api_fetches']}")
    print("\nNo orders were placed. To paper-trade a candidate: python scripts/paper_order.py --ticker US.XYZ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
