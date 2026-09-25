"""Whole-market swing screener (UNTESTED idea list; never places orders).

    python scripts/run_swing_scan.py

Best times (Singapore, US summer time): during US hours 21:30-04:00 for live movers,
after the close (04:00+) for the day's final movers, or 16:00-21:30 for pre-market gaps.
Filters live in config/settings.yaml under `swing:`.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from scanner import market_calendar as mc  # noqa: E402
from scanner.config import load_config  # noqa: E402
from scanner.logsetup import setup_logging  # noqa: E402
from scanner.moomoo_client import MoomooClient, OpenDUnavailable  # noqa: E402
from scanner.swing import format_swing, run_swing_scan  # noqa: E402


def main() -> int:
    setup_logging("swing_scan")
    cfg = load_config()
    now = mc.now_utc()
    print("Scanning the whole US market (about 1-2 minutes)...")
    try:
        with MoomooClient(cfg) as client:
            df, meta = run_swing_scan(cfg, client, now)
    except OpenDUnavailable as e:
        print(e)
        return 2
    print(format_swing(df, now, meta["market_phase"], meta["scanned"]))
    print("\nSaved in outputs\\swing\\. No orders were placed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
