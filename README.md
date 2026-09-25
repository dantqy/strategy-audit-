# Moomoo Paper-Trading Research Scanner

> **Strategy Audit Platform (portfolio project):** a local web app that turns plain-English strategies into validated rules, backtests them and tries to break them. See [strategy_audit/README.md](strategy_audit/README.md) and run `START_AUDIT_PLATFORM.bat`.

> **New here / not a coder?** Read [HOW_TO_USE.md](HOW_TO_USE.md) and use the numbered `.bat` shortcut files. This README is the technical reference.

This project tests one research question:

> After a US stock has a **≥5% earnings reaction on ≥2× normal volume**, does the move **continue or reverse** over the next 1–3 trading sessions?

It is a scanner plus an honest backtest. **PAPER TRADING ONLY.** Nothing in it places live orders.

```
SCAN → RANK → DISPLAY → HUMAN APPROVAL (type YES) → PAPER ORDER (TrdEnv.SIMULATE)
```

## Safety design

- `scanner/paper_execution.py` is the **only** code that calls `place_order()`. It passes `trd_env=TrdEnv.SIMULATE` literally.
  - The environment is not a parameter, and it is not in the config.
  - Moomoo's own `place_order()` defaults to the LIVE environment when `trd_env` is left out. That is why the environment is always passed explicitly.
- Before it sends anything, it checks that the target account is a SIMULATE account.
- Every order needs you to type exactly `YES`. The approval only works for that exact ticket: if the ticker, quantity or price changes, it has to be approved again.
- Tests fail if any of these happen:
  - `TrdEnv.REAL` or a bare `REAL` identifier appears in non-test code.
  - `place_order` is called anywhere else.
  - A config key such as `live` or `trd_env` is added.
- The scanner never calls the execution module.

## Setup (Windows, on the machine running OpenD)

```powershell
cd moomoo_scanner
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pytest                                   # 69 MOCK unit tests, no OpenD needed
```

Next, start **moomoo OpenD**, log in, and leave it on `127.0.0.1:11111`. Then run these in order:

```powershell
python scripts\check_moomoo_connection.py   # Phase A: LIVE sanity check (read-only)
python scripts\run_scanner.py               # scan, rank, save CSV + JSON
python scripts\run_backtest.py              # in-sample + sensitivity (OOS stays locked)
python scripts\run_backtest.py --final-oos  # once, when you're done choosing parameters
python scripts\paper_order.py --ticker US.XYZ   # human-approved paper order
```

## Pinned definitions

| Item | Definition |
|---|---|
| Reaction session R | BEFORE (pre-market) on day D → D. DURING → D. AFTER (post-market) on D → next trading day. Weekend or holiday → next trading day. UNKNOWN → excluded by default (`unknown_timing_policy`). |
| reaction_move_pct | `close[R] / close[R-1] − 1` |
| gap_pct | `open[R] / close[R-1] − 1` (recorded, not used for the threshold) |
| avg_20d_volume | Mean volume of the 20 bars **before** R (R excluded) |
| Prior N-day high | `high.shift(1).rolling(N).max()`, so the current bar is never included |
| Entry | Open of the session **after** R. The signal is only known at R's close. |
| Exit, horizon h | Close of the h-th session starting from the entry day (h=1 means the entry day's close) |
| Direction | BULLISH / BEARISH from the reaction sign. BEARISH is **research only**: the $300 account is long-only. |
| VOLUME_ANOMALY | rvol ≥ threshold **and** directional price confirmation: return sign, close in the matching half of the bar's range, and close on the matching side of the prior 5-day SMA |
| MOMENTUM_BREAKOUT | close > prior 20/50-day high, or a break out of a tight 10-day range; rvol ≥ 1.5; RSI(14) < 80 (a filter, not a trigger). A mirrored breakdown is tagged BEARISH. |

Note on buckets: an earnings signal already requires ≥2× volume. So in the backtest, "earnings + volume" really means "earnings + directional price confirmation".

## Data freshness labels

- `LIVE_INTRADAY`: the market is open and the quote is less than 5 minutes old. Today's partial bar is built from the snapshot.
- `LAST_COMPLETED_BAR`: the market is closed and the newest bar is the most recent completed session.
- `LAST_CLOSE`: the market is open but there is no fresh quote. Penalised in the score.
- `STALE`: the newest bar is older than the last completed session. Penalised.

Intraday relative volume is **never** today's partial volume divided by a full-day average. Methods, in order of preference:

1. Same-time-of-day comparison from 1-minute history
2. Moomoo's `volume_ratio`
3. `APPROX_elapsed_fraction`, which is labelled as approximate and penalised

## Timezones

All session logic uses the US exchange calendar (`scanner/market_calendar.py`: rule-based NYSE holidays, known one-off closures, and early closes). Your Singapore clock date is never used to decide what "today" is.

Scans record UTC, US/Eastern and local-machine timestamps as ISO-8601 with offsets. Moomoo's `time_key` and `update_time` for US stocks are documented as US/Eastern.

## API budget

- **Historical-candle quota:** 100 distinct stocks per rolling 7 days for accounts under HKD 10k. Re-requesting the same stock is free. The default universe (93 names + SPY) fits under that.
- **Rate limits:** 60 requests per 30 s. The client throttles itself to 50.
- **Caching:** daily bars are cached in `data/cache/daily/`, and only the missing tail is fetched. If forward-adjusted prices change (after a split or dividend), the full history is refetched. Earnings days are cached permanently once they are more than 3 days in the past.

## Earnings data

The primary source is Moomoo `get_earnings_calendar`, queried **one day per request**, sorted by market cap. The response has no paging or total count, so a server-side row cap would be silent. Single-day queries keep responses small, market-cap sorting keeps the large names in this universe if a cap exists, and a warning fires if several days return the same maximum row count. The first backtest run from 2019 is about 2,800 requests (~28 min); after that it is cached.

Verified on LIVE OpenD (2026-09-24): history goes back to at least 2019 with full mega-cap coverage (1,100+ rows per fortnight in earnings season, no sign of a row cap).

- **Timing comes from `pub_type`.** `earnings_timestamp` is approximate and uses placeholders (BEFORE rows often read exactly 09:30 ET; AAPL/MSFT AFTER rows read 17:00). It is used only when `pub_type` is missing.
- **`REGULAR` = UNKNOWN.** The SDK proto calls it "time slot not identified", and its timestamps are wrong in live data (CVX 11:00 and TXN 15:01, both actually outside market hours; MRK/INTC 00:00). With the default `unknown_timing_policy: exclude`, these events are skipped (roughly 10% of rows).
- **`earnings_date` is the US/Eastern date** (it matched the timestamp's ET date on every row checked).
- If Moomoo does not return historical weeks, fill `data/earnings_calendar.csv` (`code,date,timing`; see the `.example`).

## Costs

The config defaults are the Moomoo SG US fee structure, which you should verify yourself: commission $0, platform fee **$0.99 per order**, 10 bps slippage per side, and about 0.3 bps regulatory fee on sells.

At a $150 position, the platform fee alone is about **1.3% per round trip**. That is far bigger than most short-term edges. The backtest reports gross and net results side by side so you can see this directly.

## Backtest scope

- **Horizons:** 1, 2, 3, 5, 10, 20 and 60 sessions. At a $150 position, round-trip costs are about 1.5%, which 1–3 day holds almost never recover; post-earnings drift is documented over weeks. An event too recent for a long horizon is kept for the shorter ones (that horizon is NaN, not dropped). `primary_horizon` (default 1) drives the $300 account simulation and the sensitivity grid; choose it from in-sample results only.
- **Standalone signals:** the scanner flags VOLUME_ANOMALY and MOMENTUM_BREAKOUT on any day, so `backtest/signal_study.py` measures them on their own, on non-earnings days (announcement day, next session and reaction session excluded), with the same entry/exit rules. Trades of the same symbol+signal never overlap within a horizon, so n is not inflated by consecutive re-triggers. They use the same in-sample/OOS cut date as the earnings study and stay locked until `--final-oos`. Exclusion is only as good as the earnings calendar: missed earnings days leak in.

## Paper trading workflow (matches the backtest)

You place every order yourself; nothing trades automatically.

1. **Scan after the US close** (any time from 04:00 to 21:30 SGT in US summer time, 05:00 to 22:30 in winter): `python scripts\run_scanner.py`. Only candidates marked `LAST_COMPLETED_BAR` can be bought; intraday hits are refused.
2. **Buy before the next open:** `python scripts\paper_order.py --ticker US.XYZ`. It submits a DAY limit at the signal close + `execution.entry_limit_cap_pct` (default 5%), so it fills at the open unless the stock gaps up more than that. The ticket shows the entry session and the planned exit, and both are part of what you approve.
3. **Book fills:** `python scripts\paper_order.py --sync`.
4. **Exit:** `python scripts\paper_order.py --exits` lists positions as UPCOMING / DUE_TODAY / OVERDUE; sell with `--side SELL`. Holding period = `execution.hold_sessions`, or `backtest.primary_horizon` if unset.

Refused on purpose: intraday candidates, stale scans (a newer session has closed), orders after the entry session opened, and adding to a position you already hold or have pending.

## Outputs

- `outputs/scans/scan_<ET time>.json|csv`: candidates, signals, the reason, and a score breakdown
- `outputs/backtests/<run_id>/`:
  - `summary.md`, `in_sample_report.md`, `out_of_sample_report.md`
  - `signals_*.csv` (the trade/signal log), `reaction_table_all_events.csv`
  - `equity_curve_*.csv|png`, `account_trades_*.csv`
  - `sensitivity_in_sample.csv`, `sensitivity_report.md`
  - `standalone_signals_all.csv`, `standalone_report_in_sample.csv` (and `_out_of_sample` after `--final-oos`)
- `outputs/backtests/experiment_log.csv`: every run and every sensitivity cell
- `outputs/backtests/oos_access_log.csv`: every out-of-sample evaluation. You get a warning if OOS was already viewed under different settings.
- `outputs/paper_ledger.json`: the local $300 account. Moomoo's SIMULATE balance is much larger, so orders are sized against the smaller of the two. Submitted orders sit under `pending` (cash or shares reserved) until the broker reports a final status; only the broker's filled quantity and average price are booked. Run `python scripts/paper_order.py --sync` to book fills of orders that were still working.

## Known limitations

- **Survivorship bias:** the universe is today's liquid names.
- **Fractional shares:** not confirmed for SIMULATE, so sizing uses whole shares. Stocks you can't afford are marked `NOT_EXECUTABLE_WITH_CURRENT_PAPER_CAPITAL`.
- **Paper fills:** Moomoo has no deals query for paper trading, so fills are read from the SIMULATE order list (`order_status`, `dealt_qty`, `dealt_avg_price`). `check_moomoo_connection.py` checks these fields exist; this is not yet verified live.
- **Test types:** tests are **MOCK**. `check_moomoo_connection.py` is the **LIVE** check.

If any result shows a Sharpe above 3, a win rate near 100% or almost no drawdown, treat it as a bug until proven otherwise. The backtest flags these automatically.
