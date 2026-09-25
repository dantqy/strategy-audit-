"""PHASE A - Environment + OpenD connectivity + data sanity check (LIVE API).

    python scripts/check_moomoo_connection.py

Read-only: fetches quotes/history and lists paper accounts. Places NO orders.
"""
from __future__ import annotations

import json
import platform
import sys
from datetime import timedelta
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
from scanner.moomoo_client import (OPEND_DOWN_MSG, MoomooAPIError, MoomooClient,  # noqa: E402
                                   OpenDUnavailable, port_open)

TEST_CODES = ["US.AAPL", "US.NVDA", "US.MSFT"]


def main() -> int:
    logfile = setup_logging("connection_check")
    cfg = load_config()
    checks: dict[str, str] = {}
    print("=" * 64)
    print("MOOMOO OpenD SANITY CHECK (read-only, no orders)")
    print("=" * 64)
    print(f"OS              : {platform.platform()}")
    print(f"Python          : {sys.version.split()[0]} ({sys.executable})")
    print(f"In virtualenv   : {sys.prefix != getattr(sys, 'base_prefix', sys.prefix)}")
    try:
        import moomoo
        ver = getattr(moomoo, "__version__", None)
        if ver is None:
            from importlib.metadata import version
            ver = version("moomoo-api")
        print(f"moomoo SDK      : {ver}")
        checks["sdk_import"] = "PASS"
    except Exception as e:
        print(f"moomoo SDK      : NOT IMPORTABLE ({e}).  Fix: pip install -r requirements.txt")
        checks["sdk_import"] = "FAIL"
        return _finish(checks, logfile, 3)

    ts = mc.timestamps()
    now = mc.now_utc()
    print("\nClocks (ISO-8601 with offsets):")
    for k, v in ts.items():
        print(f"  {k:14s}: {v}")
    print(f"  US market phase: {mc.market_phase(now)}  | last completed US session: {mc.last_completed_session(now)}")

    host, port = cfg["opend"]["host"], cfg["opend"]["port"]
    reachable = port_open(host, port)
    print(f"\nOpenD {host}:{port} TCP : {'OPEN' if reachable else 'CLOSED'}")
    if not reachable:
        print("\n" + OPEND_DOWN_MSG)
        checks["opend_port"] = "FAIL"
        return _finish(checks, logfile, 2)
    checks["opend_port"] = "PASS"

    client = MoomooClient(cfg)
    try:
        client.connect()
        st = client.global_state()
        print(f"Global state    : {json.dumps(st, default=str)}")
        checks["opend_login"] = "PASS"
        feats = client.probe_features()
        print("\nSDK features:")
        for k, v in feats.items():
            print(f"  {'yes' if v else 'NO ':3s}  {k}")
        try:
            q = client.history_quota()
            print(f"\nHistorical-candle quota (distinct stocks / rolling 7 days): used={q['used']} remaining={q['remaining']}")
            checks["kl_quota"] = "PASS"
        except MoomooAPIError as e:
            print(f"\nQuota query failed: {e}")
            checks["kl_quota"] = "WARN"

        end = mc.last_completed_session(now)
        start = end - timedelta(days=45)
        print(f"\nDaily OHLCV (forward-adjusted {cfg['history']['autype']}), {start}..{end}. "
              "time_key dates = US/Eastern session dates.")
        for code in TEST_CODES:
            try:
                df = client.daily_bars(code, start, end, use_cache=False)
                if df.empty:
                    print(f"  {code}: EMPTY response")
                    checks[f"kline_{code}"] = "FAIL"
                    continue
                print(f"\n  {code}: {len(df)} bars, newest {df.index[-1]} (expected {end})")
                print(df.tail(3).to_string())
                checks[f"kline_{code}"] = "PASS" if df.index[-1] == end else "WARN (newest bar != last session)"
            except MoomooAPIError as e:
                print(f"  {code}: API ERROR {e}")
                checks[f"kline_{code}"] = "FAIL"

        print("\nSnapshot (update_time documented as US/Eastern for US stocks):")
        try:
            snap = client.snapshot(TEST_CODES)
            cols = [c for c in ("code", "name", "update_time", "last_price", "prev_close_price", "volume",
                                "volume_ratio", "pre_price", "after_price") if c in snap.columns]
            print(snap[cols].to_string(index=False))
            missing = [c for c in ("volume_ratio", "name") if c not in snap.columns]
            if missing:
                print(f"  NOTE: snapshot lacks {missing}; scanner falls back accordingly.")
            ages = [(now - t.to_pydatetime()).total_seconds() / 60 for t in snap["update_time_et"].dropna()]
            if ages:
                print(f"  quote age (minutes): {', '.join(f'{a:.1f}' for a in ages)}")
            checks["snapshot"] = "PASS"
        except MoomooAPIError as e:
            print(f"  snapshot failed: {e}")
            checks["snapshot"] = "FAIL"

        print("\nEarnings calendar (get_earnings_calendar):")
        today = now.astimezone(mc.ET).date()
        for label, b in (("this week", today - timedelta(days=3)), ("1 year ago", today - timedelta(days=365))):
            try:
                ec = client.earnings_calendar(b, b + timedelta(days=6))
                print(f"  {label} ({b}..{b + timedelta(days=6)}): {len(ec)} rows")
                if len(ec):
                    show = [c for c in ("security", "name", "earnings_date", "earnings_timestamp", "pub_type") if c in ec.columns]
                    print(ec[show].head(5).to_string(index=False))
                    if "pub_type" in ec.columns:
                        print(f"  pub_type values: {ec['pub_type'].astype(str).value_counts().to_dict()}")
                    print(f"  rows per day: {getattr(client, 'last_earnings_day_counts', {})}")
                    _print_timestamp_check(ec)
                checks[f"earnings_{label}"] = "PASS" if len(ec) else "WARN (0 rows)"
            except MoomooAPIError as e:
                print(f"  {label}: {e}")
                checks[f"earnings_{label}"] = "FAIL"
        if checks.get("earnings_1 year ago", "").startswith(("WARN", "FAIL")):
            print("  -> Historical earnings not available from Moomoo: supply data/earnings_calendar.csv for backtests.")
    except OpenDUnavailable as e:
        print(f"\n{e}")
        checks["opend_login"] = "FAIL"
        return _finish(checks, logfile, 2)
    finally:
        client.close()

    print("\nPaper account probe (read-only):")
    try:
        from scanner.paper_execution import find_simulate_account, open_trade_context, probe_order_fields
        tctx = open_trade_context(cfg)
        try:
            acc = find_simulate_account(tctx)
            print(f"  US SIMULATE account found: acc_id={acc}")
            checks["paper_account"] = "PASS"
            try:
                ok, cols = probe_order_fields(tctx, acc)
                print(f"  SIMULATE order query columns: {cols}")
                checks["paper_order_fill_fields"] = "PASS" if ok else "FAIL"
                if not ok:
                    print("  -> order_status/dealt_qty/dealt_avg_price missing: fill sync cannot book fills.")
            except Exception as e:
                print(f"  SIMULATE order query failed: {e}")
                checks["paper_order_fill_fields"] = "FAIL"
        finally:
            tctx.close()
    except Exception as e:
        print(f"  paper account probe failed: {e}")
        checks["paper_account"] = "WARN"
    print("  Fractional shares in SIMULATE: UNCONFIRMED (not documented). Sizing uses whole shares.")
    return _finish(checks, logfile, 0 if all(v == "PASS" for v in checks.values()) else 1)


def _print_timestamp_check(ec) -> None:
    """Show earnings_date next to the timestamp read in ET and in Beijing, to settle which timezone
    earnings_date uses, and how timing is classified per pub_type."""
    import pandas as pd
    from zoneinfo import ZoneInfo
    from scanner.earnings import parse_moomoo_calendar
    if "earnings_timestamp" not in ec.columns:
        print("  (no earnings_timestamp column)")
        return
    ts = pd.to_numeric(ec["earnings_timestamp"], errors="coerce")
    has = ec[ts > 0].head(8)
    if has.empty:
        print("  earnings_timestamp: empty on every row -> timing comes from pub_type only")
    for _, r in has.iterrows():
        t = pd.Timestamp(float(r["earnings_timestamp"]), unit="s", tz="UTC")
        print(f"  {r['security']:10s} earnings_date={r['earnings_date']}  ET={t.tz_convert(mc.ET):%Y-%m-%d %H:%M}  "
              f"Beijing={t.tz_convert(ZoneInfo('Asia/Shanghai')):%Y-%m-%d %H:%M}  pub_type={r.get('pub_type')}")
    evs = parse_moomoo_calendar(ec)
    mix = pd.Series([(e.raw_timing, e.timing) for e in evs]).value_counts().to_dict() if evs else {}
    print(f"  (pub_type -> classified timing): {mix}")


def _finish(checks: dict, logfile: str, code: int) -> int:
    print("\n" + "-" * 64)
    for k, v in checks.items():
        print(f"  {v:6s}  {k}")
    print(f"\nLog: {logfile}")
    out = resolve("outputs/logs") / "connection_check_latest.json"
    out.write_text(json.dumps({"checks": checks, "exit_code": code, "clocks": mc.timestamps()}, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
