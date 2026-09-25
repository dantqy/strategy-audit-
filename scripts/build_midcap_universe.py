"""Build a rule-based MID-CAP universe (read-only; uses snapshots, not history quota).

    python scripts/build_midcap_universe.py            # writes data/universe_midcap.csv

Rule, fixed in advance and NOT based on past performance or today's share price
(filtering on today's price would smuggle hindsight in: stocks cheap today are
often the ones that crashed):
  * NYSE or Nasdaq common stock (no OTC/pink sheets)
  * market cap USD 2-20 bn
  * listed on or before 2018-12-31 when the listing date is known. Moomoo
    reports 1970-01-01 when it does NOT know the date (e.g. IONQ, a 2021 SPAC);
    those are kept as 'unknown' and simply contribute fewer backtest years.
  * estimated average daily turnover >= USD 20 m
    (today's turnover / Moomoo's time-normalised 5-day volume_ratio / session
    fraction elapsed; best run after the close, when the fraction is 1)
  * not already in data/universe.csv
  * a RANDOM sample of N (default 90, fixed seed) of everything that passes.
    Not the N most-traded: today's most-traded names are often the recent big
    winners (hindsight again). History quota is 100 distinct stocks per 7 days.

Known bias: this is TODAY's mid-cap list, so companies that shrank below $2 bn or
were delisted since 2019 are missing (survivorship). Affordability is judged in
the backtest at the real price on each signal day, never by today's price.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

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
from scanner.universe import load_universe  # noqa: E402

MIN_CAP, MAX_CAP = 2e9, 20e9
MIN_TURNOVER = 20e6
LISTED_BY = date(2018, 12, 31)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=90)
    ap.add_argument("--out", default="data/universe_midcap.csv")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    setup_logging("build_midcap")
    cfg = load_config()
    existing = set(load_universe(cfg))
    now = mc.now_utc()
    frac = mc.session_elapsed_fraction(now) or 1.0
    try:
        with MoomooClient(cfg) as c:
            sdk = c.sdk
            _, info = c._call("get_stock_basicinfo", sdk.Market.US, sdk.SecurityType.STOCK)[:2]
            info = info[info["exchange_type"].isin(["US_NYSE", "US_NASDAQ"])]
            info = info[info["code"].str.fullmatch(r"US\.[A-Z]{1,5}")]        # plain tickers only
            print(f"NYSE/Nasdaq common tickers: {len(info)}; fetching snapshots...")
            snap = c.snapshot(list(info["code"]))
    except OpenDUnavailable as e:
        print(e)
        return 2
    s = snap.copy()
    for col in ("total_market_val", "turnover", "volume_ratio", "last_price"):
        s[col] = pd.to_numeric(s[col], errors="coerce")
    s["listing_date"] = pd.to_datetime(s["listing_date"], errors="coerce").dt.date
    s.loc[s["listing_date"] == date(1970, 1, 1), "listing_date"] = None      # Moomoo's 'unknown' placeholder
    s["est_avg_turnover"] = s["turnover"] / s["volume_ratio"].where(s["volume_ratio"] > 0) / frac
    steps = [("market cap 2-20bn", s["total_market_val"].between(MIN_CAP, MAX_CAP)),
             (f"listed by {LISTED_BY} (or unknown)",
              s["listing_date"].isna() | s["listing_date"].map(lambda d: d is not None and d <= LISTED_BY)),
             ("est. avg turnover >= $20m", s["est_avg_turnover"] >= MIN_TURNOVER),
             ("not in current universe", ~s["code"].isin(existing))]
    keep = pd.Series(True, index=s.index)
    for label, m in steps:
        keep &= m.fillna(False)
        print(f"  after {label:36s}: {int(keep.sum())}")
    eligible = s[keep]
    pick = eligible.sample(min(args.n, len(eligible)), random_state=args.seed).sort_values("code")
    print(f"  random sample (seed {args.seed})              : {len(pick)} of {len(eligible)} eligible")
    out = pd.DataFrame({"code": pick["code"], "name": pick["name"],
                        "market_cap_bn": (pick["total_market_val"] / 1e9).round(2),
                        "price_when_built": pick["last_price"].round(2),
                        "est_avg_turnover_m": (pick["est_avg_turnover"] / 1e6).round(1),
                        "listing_date": pick["listing_date"].map(lambda d: d or "unknown")})
    p = resolve(args.out)
    out.to_csv(p, index=False)
    budget = cfg["account"]["starting_cash"] * cfg["account"]["max_position_pct"]
    print(f"\nSaved {len(out)} names to {p}  (built {mc.to_et(now):%Y-%m-%d %H:%M} ET, session fraction {frac:.2f})")
    print(f"Price today <= ${budget / 1.05:,.0f} (one share fits a ${budget:,.0f} position): "
          f"{int((out['price_when_built'] <= budget / 1.05).sum())} of {len(out)}  "
          "(information only: NOT used to choose the list)")
    print(out.head(15).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
