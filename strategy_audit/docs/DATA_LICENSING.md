# Data licensing: what must be resolved before any public or commercial use

**Current status: LOCAL PROTOTYPE ONLY.** Nothing in this repository should be deployed publicly or monetised
until the questions below are answered in writing by the data provider(s).

## Current provider

| | |
|---|---|
| Provider | `MoomooCacheProvider` (`strategy_audit/quant/data.py`) |
| Source | Moomoo OpenAPI (OpenD), personal retail account, Singapore (FUTUSG) |
| How obtained | `request_history_kline` via the official SDK, cached locally to `data/cache/daily/` |
| Access pattern in the platform | **Read-only from the local cache. The web app makes no live API calls.** |
| Rights to redistribute raw or derived data | **UNKNOWN: assume NO** |

## Questions for the provider (Moomoo / Futu) or a replacement vendor

1. Does the OpenAPI / retail account agreement allow **displaying** historical prices, or anything derived from them (indicators, backtest equity curves, trade lists), to **other people**?
2. Does it allow **commercial** use (a paid or freemium product)?
3. Are **derived analytics** (aggregate statistics, charts) treated differently from raw prices?
4. Are there exchange-level fees for redistributing **end-of-day** US equity data (e.g., non-display vs display use)?
5. Attribution requirements, caching limits, retention limits?
6. Does the account's historical-quota tier (100 stocks per 7 days) restrict automated bulk use?

## Architecture decision

All market data goes through `MarketDataProvider`:

```
MarketDataProvider (interface)
├── MoomooCacheProvider        personal, local cache, rights unresolved (prototype only)
├── FutureCommercialProvider   placeholder for a properly licensed EOD vendor
└── MockProvider               synthetic, deterministic, clearly labelled; tests and offline demo only
```

Swapping in a licensed vendor means implementing `FutureCommercialProvider` (bars, universe, benchmark,
dataset version, survivorship flag). Nothing else in the engine, API or UI needs to change.

## Requirements for a replacement provider

- Split/dividend-adjusted daily OHLCV for US equities, plus unadjusted prices if share counts matter.
- **Point-in-time index membership and delisted securities.** This is required to remove the survivorship-bias warning.
- Written permission for display, derived-data use and commercial use.

## Explicitly not done

- No scraping; no workarounds for provider restrictions.
- No public deployment; no hosting of the cache.
- No live data calls from the web application.
