"""Backtest the earnings-reaction hypothesis (LIVE API for data; cached afterwards).

    python scripts/run_backtest.py                 # in-sample + sensitivity; OOS stays locked
    python scripts/run_backtest.py --final-oos     # also evaluate the held-out period (logged)
    python scripts/run_backtest.py --universe data/universe_midcap.csv --slippage-bps 30
                                                   # another stock list / cost assumption (logged in notes)
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from backtest.runner import run_backtest  # noqa: E402
from scanner import market_calendar as mc  # noqa: E402
from scanner.config import load_config, resolve  # noqa: E402
from scanner.earnings import load_events  # noqa: E402
from scanner.logsetup import setup_logging  # noqa: E402
from scanner.moomoo_client import MoomooAPIError, MoomooClient, OpenDUnavailable  # noqa: E402
from scanner.universe import load_universe  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--final-oos", action="store_true", help="evaluate the locked out-of-sample period (logged)")
    ap.add_argument("--notes", default="", help="free-text note stored in experiment_log.csv")
    ap.add_argument("--max-symbols", type=int, default=None, help="limit universe (quick test)")
    ap.add_argument("--universe", default=None, help="CSV of codes to test instead of universe.csv_path")
    ap.add_argument("--slippage-bps", type=float, default=None, help="override costs.slippage_bps (per side)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logfile = setup_logging("backtest", args.verbose)
    cfg = load_config()
    if args.universe:
        cfg["universe"]["csv_path"] = args.universe
        args.notes = f"universe={args.universe}; {args.notes}"
    if args.slippage_bps is not None:
        cfg["costs"]["slippage_bps"] = args.slippage_bps
        args.notes = f"slippage_bps={args.slippage_bps}; {args.notes}"
    h = cfg["history"]
    start = date.fromisoformat(h["backtest_start"])
    end = date.fromisoformat(h["backtest_end"]) if h.get("backtest_end") else mc.last_completed_session(mc.now_utc())
    codes = load_universe(cfg)[: args.max_symbols]
    bench = cfg["universe"]["benchmark"]
    data_start = start - timedelta(days=120)  # warm-up for 50-day highs / RSI / 20d volume
    try:
        with MoomooClient(cfg) as client:
            try:
                q = client.history_quota()
                print(f"History quota: used {q['used']}, remaining {q['remaining']} (distinct stocks / 7 days)")
                if q["remaining"] < len(codes) + 1:
                    print("WARNING: universe may exceed remaining quota; already-requested stocks are free.")
            except MoomooAPIError as e:
                print(f"(quota check failed: {e})")
            prices, failed = {}, []
            for n, code in enumerate(codes, 1):
                try:
                    df = client.daily_bars(code, data_start, end)
                    if len(df):
                        prices[code] = df
                    else:
                        failed.append(code)
                except MoomooAPIError as e:
                    failed.append(f"{code} ({e})")
                if n % 10 == 0:
                    print(f"  prices {n}/{len(codes)} (cache hits {client.cache_hits}, fetches {client.api_fetches})")
            spy = client.daily_bars(bench, data_start, end)
            # As-traded prices, so the $300 account buys whole shares at what they really cost
            # then (split-adjusted history makes pre-split shares look cheap). Same stocks: no extra quota.
            raw_prices = {}
            for code in prices:
                try:
                    r = client.raw_daily_bars(code, data_start, end)
                    if len(r):
                        raw_prices[code] = r
                except MoomooAPIError as e:
                    print(f"  unadjusted prices unavailable for {code}: {e}")
            print(f"Unadjusted prices: {len(raw_prices)}/{len(prices)} symbols")
            print(f"Loading earnings events {start}..{end} (weekly requests, cached)...")
            events = load_events(cfg, start, end, set(prices), client=client)
    except OpenDUnavailable as e:
        print(e)
        return 2
    if failed:
        print(f"Price data unavailable for {len(failed)}: {failed[:10]}")
    print(f"Symbols with data: {len(prices)} | earnings events: {len(events)}")
    if events.empty:
        print("No earnings events. Moomoo history may be unavailable: add data/earnings_calendar.csv (code,date,timing).")
        return 1
    res = run_backtest(prices, spy, events, cfg, resolve("outputs/backtests"), final_oos=args.final_oos,
                       notes=args.notes, raw_prices=raw_prices)
    print(Path(res["summary_path"]).read_text(encoding="utf-8"))
    print(f"\nReports: {res['out_dir']}\nExperiment log: outputs/backtests/experiment_log.csv\nLog: {logfile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
