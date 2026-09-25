# Strategy Audit Platform

**Think your trading strategy works? Try to prove it doesn't.**

A local prototype of a trading-strategy *validation* platform. You describe a strategy in plain English. The
platform turns it into explicit, validated rules, backtests them deterministically, and then deliberately looks
for reasons the backtest might be misleading.

> Historical research only. Backtests do not predict future performance and are not investment recommendations.
> This project does not claim that any strategy is profitable.

---

## The problem

AI makes it trivially easy to *generate* trading strategies and very hard to *validate* them. A backtest that
looks great usually hides one of a handful of traps: lookahead, survivorship bias, costs, overfitting through
many tries, a single lucky year or stock, or an "edge" that is really just the market going up. Retail tools
show the flattering equity curve and stop there.

## The solution: auditing, not generating

```
plain English ──► strict strategy schema ──► user confirms ──► deterministic backtest
                  (parser: interpretation      (nothing runs      (Python/pandas only)
                   only, never guesses)          unconfirmed)             │
                                                                          ▼
     report ◄── evidence assessment ◄── robustness audit ("TRY TO BREAK MY STRATEGY")
```

* **AI/parser = interpretation and explanation. Python engine = every number.** The V0 parser is deterministic
  pattern matching (no LLM is required). Explanations are assembled from computed fields, and each sentence
  records which result fields it quotes.
* **Nothing ambiguous is silently assumed.** "Strong stock", "moving average" without simple or exponential,
  missing periods, or "S&P 500" when that universe is not in the dataset: each becomes a question with explicit
  options. Unsupported requests (shorting, options, crypto, intraday, fundamentals) are rejected. Unrecognised
  phrases are shown back to the user.

## The audit (the product)

| Test | What it tries to find |
|---|---|
| Out-of-sample | The last 30% of history is **sealed**. It can be revealed once per strategy family; the reveal is recorded permanently |
| Parameter sensitivity | Whether nearby parameter values also work (one-at-a-time neighbours plus a 3×3 heatmap; a stability check, not a search) |
| Transaction costs | Whether the edge survives double fees, triple slippage, and worse |
| Market regimes | Whether profits come only from bull (SPY > 200-day average) or bear markets |
| Year-by-year | Whether one year produced most of the profit |
| Outlier dependence | Whether the result survives removing the best 1 / 5 / 10 trades, and winsorising |
| Stock concentration | Whether one ticker carries the result (and what happens without it) |
| Random-entry baseline | Whether the strategy beats entering the *same stocks* on random days (same year and regime; fixed seed) |
| Statistical confidence | A bootstrap CI of the average trade, resampling trading **dates** (clustered), with a small-sample flag |
| Lookahead audit | Recomputes indicators on truncated history, re-derives signals from past data only, and checks next-open fills and SPY date alignment |
| Multiple testing | Counts related versions tested on the same data (a research warning, not enforcement) |
| Survivorship bias | Always shown when the universe is not point-in-time |

Results are **PASS / MIXED / FAIL** labels, each with an icon and a sentence (never colour alone, never a 0–100
score). The final **evidence assessment** is one of: *No evidence of historical edge · Weak / inconclusive ·
Promising but unstable · Relatively robust*. It never says buy, sell, safe or recommended. Rules are fixed in
`assess.py`.

## Quantitative safeguards

* **Lookahead:** indicators on day T use bars ≤ T ("prior N-day" highs and volume exclude T). A signal is known at
  T's close; the earliest fill is the **T+1 open** on the US exchange calendar. The automated audit verifies this
  on every run.
* **Survivorship bias:** the current universe is 93 of *today's* large US stocks. Every result carries the warning.
* **Costs:** per-order fees plus slippage in bps on both sides plus a regulatory fee on sells, stress-tested.
* **Out-of-sample:** chronological 70/30 split, never shuffled; sealed until a one-time reveal. It reduces
  hindsight but does not remove it (you may already know what happened in those years).
* **Multiple testing:** strategy families and versions, with parameter diffs, results viewed and validation viewed.
* **Random baselines:** tests whether the entry rule adds anything beyond these stocks' normal drift.
* **Missing data:** a missing candle voids a trade; it is never filled in.
* **Reproducibility:** each backtest stores the engine version, methodology version, dataset version, spec hash,
  settings and timestamp. The same inputs give the same numbers (tested).

## Architecture

```
strategy_audit/
  schema.py          strict pydantic StrategySpec (closed set of indicators/operators; no eval)
  parser.py          plain English -> draft + issues/options; whitelisted patch operations
  quant/data.py      MarketDataProvider -> MoomooCacheProvider | FutureCommercialProvider | MockProvider
  quant/engine.py    indicators, signals, trade ledger, portfolio simulation, metrics
  quant/robustness.py  every audit test (thresholds fixed in code)
  quant/lookahead.py   automated lookahead audit
  assess.py          audit table, evidence assessment, traceable explanation, limitations
  service.py         lineage (families/versions), backtests, audits, sealed validation, reports
  db.py              SQLite (families, versions, backtests, audits, validation_reveals, submissions, events)
  api.py             FastAPI JSON API + static frontend
  static/            vanilla JS single-page app, hand-drawn SVG charts (no build step)
tests/test_audit_platform.py   31 tests (synthetic data)
```

It reuses the existing project's US trading calendar, RSI, cluster bootstrap and cached Moomoo data (see
`docs/PHASE_A_AUDIT.md`). There are no microservices and no build step: one Python process.

## Run it

```bat
0_FIRST_TIME_SETUP.bat          (once per computer, from the project root)
START_AUDIT_PLATFORM.bat        -> http://127.0.0.1:8765
```

or `.venv\Scripts\python -m strategy_audit.run`. Tests: `.venv\Scripts\python -m pytest tests/test_audit_platform.py`.

* **Demo (2–3 minutes):** open `http://127.0.0.1:8765/#/demo` → Interpret → answer the two questions →
  Confirm → results → **TRY TO BREAK MY STRATEGY** → reveal validation → report.
* **Admin (Stage-1 research):** `/#/admin`. On first start a token is generated into
  `config/audit_platform.local.env` (git-ignored) and printed once.
* The server binds to 127.0.0.1 only.

## Demo result (real data, not illustrative)

*"Buy S&P 500 stocks when RSI(14) < 30, above the 200-day average, down ≥ 5% over 5 days; next open; hold 10 days."*
S&P 500 history isn't in the dataset, so the platform asks the user to pick the available universe (93 large
caps). Development period 2019-01-02 → 2024-05-24:

* 135 portfolio trades, 64% win rate, +2.5% average trade: looks good.
* Portfolio **+37.5% vs SPY +130.3%** (invested only 9% of the time).
* Audit: the average trade's 95% CI includes zero; it doesn't beat random entries in the same stocks
  (+0.72%, CI −1.85% … +3.22%); 80%+ of profit comes from one regime; 3 stocks produce 56% of profit.
* Sealed validation (2024-05-28 → 2026-09-23): the average trade fell 72% (+1.72% → +0.48%), and the portfolio
  made −1.2% vs SPY +49%.
* **Evidence: WEAK / INCONCLUSIVE HISTORICAL EVIDENCE.** Loosening RSI to 35 (version 2) gives *No evidence of
  historical edge*, and the lineage page flags **multiple-testing risk**.

## Origin

This grew out of testing my own trading ideas on a small paper account. Seemingly reasonable strategies
(post-earnings drift, swing momentum, abnormal-volume continuation) looked fine at first and failed once realistic
costs, whole-share constraints, random baselines and out-of-sample checks were applied (see
`../WHY_IT_DOESNT_MAKE_MONEY.md`, `../STRATEGY_3_ABNORMAL_REPRICING_RESULTS.md`). The useful part turned out to be
the *checking*, and that is what this platform packages.

## Known limitations

* Historical performance does not predict future returns. Backtests are sensitive to data quality and assumptions.
* Out-of-sample testing reduces but does not eliminate hindsight bias.
* Research lineage cannot detect related strategies across browsers, users or accounts (renaming is only
  partly caught, via the rule signature within the same browser).
* The dataset is survivorship-biased (93 current large caps; no delisted names; no point-in-time S&P 500).
* Cost estimates may differ from real execution. Daily bars only; no intraday execution modelling.
* The V0 parser understands a fixed vocabulary; anything else is reported as not understood, never forced.
* Share links only work on the machine running the prototype. There are no accounts and no payments (by design).
* **Data licensing is unresolved: local prototype only.** See `docs/DATA_LICENSING.md`.

## Product analytics (privacy-light)

Anonymous events only (a random browser id; no names or emails): strategy submitted / testable / interpretation
confirmed / backtest completed / audit launched / report viewed / second strategy / referral source. The admin
page shows the funnel and Stage-1 tracking (testable, delivered, returned unprompted, referred, willing to pay).
Compliments are not demand.
