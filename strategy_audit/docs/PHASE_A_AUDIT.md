# Phase A: audit of the existing `moomoo_scanner` project

Inspected 2026-09-25 before building the Strategy Audit Platform.

## Reusable, and reused

| Component | Where | Why it can be trusted | Used by the platform as |
|---|---|---|---|
| US trading calendar (holidays, early closes, next/prev session) | `scanner/market_calendar.py` | Rule-based, unit-tested (`tests/test_calendar_timing.py`) | Contiguity checks: entry = the *next session*, and missing sessions are detected, never filled |
| RSI (Wilder), SMA, prior high/low excluding the current bar | `scanner/indicators.py` | Tested against a reference Wilder RSI; lookahead tests | Indicator primitives |
| Cluster bootstrap (resampling by date) and trade stats | `backtest/metrics.py` | Used and cross-checked in Strategies 1–3 | Bootstrap CI of mean trade return |
| Daily bar cache: forward-adjusted (QFQ) + unadjusted (NONE) CSVs | `scanner/data_cache.py`, `data/cache/daily/` | Written by the Moomoo client with a data-quality report; missing candles are never filled | `MoomooCacheProvider` (read-only, no API calls) |
| Cost model ($0.99/order + slippage bps + regulatory bps on sells) | `config/settings.yaml` `costs:` | Moomoo SG fee schedule | Default cost settings |
| Research discipline (pre-registered rules, OOS lock, random baselines, outlier and stock-dependence checks) | `backtest/`, `strategy3/` | Produced honest negative results for Strategies 1 and 3 | Design of the robustness engine |

## Not reused as-is (strategy-specific)

- `backtest/engine.py` is hard-wired to earnings events (`build_reaction_table`) and to the $300 account. The platform needs a *generic* engine driven by a structured spec, so `strategy_audit/quant/engine.py` is new. It reuses the calendar and indicators above.
- `strategy3/` implements a single research question. Its checks (outliers, ticker removal, matched random baseline) are generalised in `strategy_audit/quant/robustness.py`.
- The scanner, Telegram bot and paper trading are execution tooling. They are out of scope and untouched (the spec says no execution).

## Technical debt and bugs found

1. **`backtest/metrics.trade_stats` index alignment:** it aligns `dates` to `returns` by index label. Callers must pass Series with matching default indexes, or the clusters silently mismatch. The platform always resets indexes before calling it (a test covers this).
2. **`equity_stats` Sharpe counts idle days:** days with no exposure produce 0 returns, which lowers volatility and changes Sharpe. The platform reports exposure next to Sharpe so this is visible.
3. **`equity_stats` returns `annualized_return=None` below 0.5 years:** handled by the UI ("n/a").
4. **The universe is survivorship-biased:** 93 of *today's* large US stocks. There's no point-in-time membership or delisted names, and no S&P 500 constituent history exists in the data. The platform shows this on every result and asks the user to choose the available universe when they ask for "S&P 500".
5. **Data rights:** the cache was downloaded with a personal Moomoo OpenAPI account. Redistribution rights are unknown, so the platform keeps the data behind a provider interface and is local-only (see `DATA_LICENSING.md`).
6. **Windows Git Bash heredocs** stripped backslashes during earlier edits (fixed at the time). New code is written with file tools only.
7. `backtest/runner.py`: one f-string without placeholders (cosmetic). The experiment log holds duplicate rows from re-runs (by design: every run is logged).
8. **No git repository:** version history exists only in the experiment logs. Recommended: `git init` before sharing the project.

## Data available to the platform

- 93 US large-cap stocks + SPY, daily OHLCV, forward-adjusted, **2017-12-01 → 2026-09-23** (indicator warm-up included). From Moomoo OpenAPI via the local cache.
- Tested period: 2019-01-02 → latest cached session. The first 70% of sessions are the development period and the last 30% are **sealed validation**.
