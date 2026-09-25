# Strategy 3: Abnormal repricing — continuation or reversal?

## VERDICT: **WEAK / INCONCLUSIVE EFFECT**

*Backtest only. No live trading. Not a recommendation to trade real money.*

## Setup

- Events: every day with |daily return| >= 4% AND relative volume >= 2.0x (volume vs the previous 20 days, event day excluded), 2019-01-01 .. 2024-09-30.
- Universe: the existing 93 large US stocks. **SURVIVORSHIP BIAS:** these are today's large, successful companies, so historical results are likely biased upward. No historically valid broader universe is available in the current data infrastructure without new data; none was used.
- Prices: Moomoo forward-adjusted daily bars (share counts in the $ tests use real unadjusted prices).
- Timing: signal known at Day T close; entry at T+1 OPEN; forward_h = Close[T+h] / Open[T+1] - 1.
- In-sample (research): 2019-2022. Out-of-sample (untouched validation): 2023 - Sep 2024.
- Random baseline: for every event, 20 random NON-event days of the same stock, same year, same SPY regime (seed 42); same entry/exit rule.
- Hypotheses tested: **34 event sets x 6 horizons = 204 tests.** At a 5% false-positive rate about 10 would look 'significant' by chance alone.
- Selection rule, fixed before results: in-sample n >= 30 and the 95% CI of (continuation vs matched random) excludes 0; the strongest horizon per hypothesis is frozen and tested once out-of-sample.

## 1. Events

**2541 abnormal events** (1312 positive shocks, 1229 negative shocks) across 93 stocks. In-sample 1745, out-of-sample 796.

Event types: GAP_AND_GO 953, GAP_DOWN_AND_GO 908, GAP_AND_FADE 200, INTRADAY_SELLOFF 176, INTRADAY_BREAKOUT 159, GAP_DOWN_AND_RECOVER 144, MIXED 1

## 2. Positive shocks: raw forward returns (full period, entry next open)

'mean' > 0 = price kept rising. 'continuation vs random' > 0 = rose MORE than the same stock did on comparable random days.

| h (days) | n | mean | median | win% | std | 95% CI (mean) | SPY same window | excess vs SPY | random entries | continuation vs random | 95% CI |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1312 | +0.04% | +0.00% | +50.00% | +3.71% | -0.19% .. +0.28% | +0.02% | +0.02% | +0.07% | -0.02% | -0.25% .. +0.21% |
| 2 | 1312 | +0.08% | -0.19% | +48.17% | +5.39% | -0.27% .. +0.44% | +0.11% | -0.03% | +0.22% | -0.13% | -0.48% .. +0.22% |
| 3 | 1312 | -0.00% | -0.05% | +49.16% | +6.47% | -0.45% .. +0.43% | +0.12% | -0.12% | +0.37% | -0.37% | -0.84% .. +0.06% |
| 5 | 1312 | +0.11% | +0.37% | +52.59% | +7.84% | -0.45% .. +0.66% | +0.23% | -0.12% | +0.72% | -0.61% | -1.16% .. -0.09% |
| 10 | 1312 | +1.26% | +1.11% | +55.64% | +10.66% | +0.56% .. +1.97% | +0.61% | +0.65% | +1.56% | -0.31% | -1.01% .. +0.36% |
| 20 | 1312 | +3.02% | +1.90% | +57.39% | +16.75% | +1.81% .. +4.19% | +1.38% | +1.63% | +3.38% | -0.36% | -1.57% .. +0.80% |

## 3. Negative shocks: raw forward returns (full period)

'mean' < 0 = price kept falling. 'continuation vs random' > 0 = fell MORE than on comparable random days (continuation); < 0 = rebounded relative to random (reversal).

| h (days) | n | mean | median | win% | std | 95% CI (mean) | SPY same window | excess vs SPY | random entries | continuation vs random | 95% CI |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1229 | +0.02% | +0.04% | +50.53% | +3.77% | -0.30% .. +0.33% | +0.14% | -0.12% | +0.01% | -0.01% | -0.32% .. +0.31% |
| 2 | 1229 | -0.27% | -0.19% | +48.17% | +6.10% | -0.95% .. +0.42% | -0.14% | -0.13% | +0.10% | +0.37% | -0.32% .. +1.08% |
| 3 | 1229 | -0.14% | -0.12% | +48.66% | +7.04% | -0.82% .. +0.56% | -0.08% | -0.05% | +0.19% | +0.33% | -0.36% .. +1.04% |
| 5 | 1229 | +0.17% | -0.10% | +49.31% | +9.28% | -0.77% .. +1.10% | +0.05% | +0.13% | +0.38% | +0.20% | -0.74% .. +1.16% |
| 10 | 1229 | +0.16% | +0.36% | +51.18% | +11.93% | -1.08% .. +1.28% | +0.15% | +0.00% | +0.81% | +0.65% | -0.53% .. +1.93% |
| 20 | 1229 | +1.50% | +1.35% | +54.84% | +15.47% | +0.18% .. +2.84% | +1.13% | +0.38% | +1.83% | +0.33% | -1.07% .. +1.79% |

## 4. Predefined subgroups and combinations (IN-SAMPLE 2019-2022, continuation vs random)

Each cell: mean continuation vs matched random entries; * = 95% CI excludes 0 (n >= 30). Positive = continuation, negative = reversal.

| hypothesis | n | 1d | 2d | 3d | 5d | 10d | 20d |
|---|---|---|---|---|---|---|---|
| POS baseline | 899 | +0.03% | -0.11% | -0.39% | -0.68%* | -0.73% | -1.00% |
| POS | rel volume 2-3x | 528 | +0.06% | +0.02% | -0.37% | -0.80% | -0.72% | -0.58% |
| POS | rel volume >=3x | 371 | -0.01% | -0.29% | -0.42% | -0.51% | -0.75% | -1.59% |
| POS | move 4-6% | 271 | -0.18% | -0.56% | -0.52% | -0.66% | -0.83% | -1.34% |
| POS | move 6-10% | 327 | -0.25% | +0.11% | -0.30% | -0.52% | -0.45% | +0.33% |
| POS | move >=10% | 301 | +0.54% | +0.06% | -0.38% | -0.88% | -0.94% | -2.13% |
| POS | GAP_AND_GO | 645 | +0.01% | -0.15% | -0.39% | -0.97%* | -1.02%* | -0.85% |
| POS | GAP_AND_FADE | 132 | -0.08% | -0.29% | -1.11% | -0.68% | -0.46% | -2.12% |
| POS | INTRADAY_MOVE | 122 | +0.27% | +0.31% | +0.36% | +0.87% | +0.51% | -0.57% |
| POS | close > SMA50 | 719 | +0.08% | -0.06% | -0.26% | -0.45% | -0.84% | -1.41% |
| POS | close <= SMA50 | 180 | -0.14% | -0.32% | -0.92% | -1.59% | -0.27% | +0.65% |
| POS | SPY > SMA200 | 650 | -0.07% | -0.29% | -0.50% | -0.53% | -0.41% | -0.74% |
| POS | SPY <= SMA200 | 249 | +0.29% | +0.35% | -0.12% | -1.07% | -1.55% | -1.67% |
| POS | 60d RS > 0 | 465 | +0.09% | -0.16% | -0.36% | -0.57% | -1.58%* | -2.07%* |
| POS | 60d RS <= 0 | 407 | -0.09% | -0.05% | -0.36% | -0.66% | +0.09% | +0.13% |
| NEG baseline | 846 | +0.01% | +0.59% | +0.69% | +0.42% | +1.22% | +1.39% |
| NEG | rel volume 2-3x | 503 | -0.15% | +0.52% | +0.60% | +0.49% | +1.96% | +2.04% |
| NEG | rel volume >=3x | 343 | +0.25% | +0.68% | +0.83% | +0.32% | +0.15% | +0.44% |
| NEG | move 4-6% | 336 | +0.13% | +0.33% | +0.62% | +0.52% | +2.19%* | +2.47%* |
| NEG | move 6-10% | 317 | -0.08% | +0.48% | +0.42% | +0.36% | +1.09% | +1.23% |
| NEG | move >=10% | 193 | -0.04% | +1.22% | +1.27% | +0.35% | -0.25% | -0.21% |
| NEG | GAP_AND_GO | 634 | -0.01% | +0.63% | +0.68% | +0.47% | +1.02% | +0.62% |
| NEG | GAP_AND_FADE | 87 | -0.22% | +0.85% | +1.64% | +0.81% | +3.02%* | +2.43% |
| NEG | INTRADAY_MOVE | 124 | +0.27% | +0.16% | +0.06% | -0.11% | +0.96% | +4.62%* |
| NEG | close > SMA50 | 163 | -0.25% | -0.16% | -0.24% | +0.46% | +2.14%* | +2.63%* |
| NEG | close <= SMA50 | 683 | +0.07% | +0.77% | +0.91% | +0.41% | +1.00% | +1.10% |
| NEG | SPY > SMA200 | 520 | +0.36% | +0.46% | +0.49% | +0.38% | +0.94% | +1.25% |
| NEG | SPY <= SMA200 | 326 | -0.55% | +0.80% | +1.01% | +0.48% | +1.67% | +1.62% |
| NEG | 60d RS > 0 | 388 | -0.21% | +0.14% | -0.00% | +0.13% | +1.30% | +1.64%* |
| NEG | 60d RS <= 0 | 448 | +0.18% | +0.92% | +1.23% | +0.54% | +1.25% | +1.03% |
| CONTINUATION A | 352 | +0.14% | -0.11% | -0.22% | -0.82% | -1.78%* | -2.23%* |
| CONTINUATION B | 321 | +0.14% | -0.01% | -0.07% | -0.32% | -0.69% | -1.78% |
| REVERSAL A | 40 | +0.75% | +0.68% | -0.81% | -1.10% | -0.51% | -2.85% |
| NEGATIVE CONTINUATION | 662 | +0.16% | +0.65% | +0.81% | +0.21% | +0.89% | +0.91% |

## 5. Frozen in-sample selection

| hypothesis | h | n | effect | IS vs random | 95% CI | t | long-tradable |
|---|---|---|---|---|---|---|---|
| CONTINUATION A | 10 | 352 | REVERSAL | -1.78% | -2.84% .. -0.61% | -3.47 | False |
| NEG | move 4-6% | 10 | 336 | CONTINUATION | +2.19% | +0.36% .. +4.08% | 3.36 | False |
| POS | 60d RS > 0 | 10 | 465 | REVERSAL | -1.58% | -2.63% .. -0.47% | -3.30 | False |
| POS | GAP_AND_GO | 5 | 645 | REVERSAL | -0.97% | -1.73% .. -0.25% | -3.12 | False |
| NEG | INTRADAY_MOVE | 20 | 124 | CONTINUATION | +4.62% | +0.95% .. +9.05% | 2.79 | False |
| NEG | close > SMA50 | 10 | 163 | CONTINUATION | +2.14% | +0.48% .. +3.73% | 2.59 | False |
| NEG | 60d RS > 0 | 20 | 388 | CONTINUATION | +1.64% | +0.08% .. +3.12% | 2.52 | False |
| NEG | GAP_AND_FADE | 10 | 87 | CONTINUATION | +3.02% | +0.08% .. +6.74% | 2.47 | False |
| POS baseline | 5 | 899 | REVERSAL | -0.68% | -1.40% .. -0.01% | -2.46 | False |

9 of 34 hypotheses selected (with ~10 chance false positives expected across 204 tests).

## 6. Out-of-sample, robustness, costs (frozen variants + both baselines)

### POS baseline — REVERSAL at 5d — FROZEN (passed in-sample rule)

- Trade = SHORT (research only, not executable in the $300 account); return per event = -forward return.
- In-sample vs random: n=899, mean +0.68% [+0.01% .. +1.40%]
- **Out-of-sample 2023-Sep 2024 vs random: n=413, mean +0.46% [-0.41% .. +1.35%] -> same sign, NOT significant**
- OOS trade return -0.35% per event; vs SPY same windows +0.12%
- Best ticker US.SOFI = -108% of summed returns; top 3 ['US.SOFI', 'US.MRNA', 'US.DAL'] = -252%; top 5 = -355%. Without US.SOFI: n=1287, mean -0.24% [-0.79% .. +0.34%]
- Years with positive mean trade return: 3 of 6

| outlier test (full period) | n | mean trade return | 95% CI | win% |
|---|---|---|---|---|
| all events | 1312 | -0.11% | -0.66% .. +0.45% | +47.33% |
| excl. best 1 | 1311 | -0.14% | -0.68% .. +0.42% | +47.29% |
| excl. best 5 | 1307 | -0.23% | -0.73% .. +0.24% | +47.13% |
| excl. best 10 | 1302 | -0.33% | -0.81% .. +0.13% | +46.93% |
| winsorized 1/99% | 1312 | -0.11% | -0.62% .. +0.44% | +47.33% |

| year | n | mean trade return | vs random | win% |
|---|---|---|---|---|
| 2019 | 154 | -0.32% | +0.64% | +50.65% |
| 2020 | 292 | +0.21% | +1.91% | +45.89% |
| 2021 | 222 | -0.38% | -0.05% | +42.34% |
| 2022 | 231 | +0.31% | -0.15% | +48.05% |
| 2023 | 246 | -0.66% | +0.34% | +47.15% |
| 2024 | 167 | +0.10% | +0.63% | +52.69% |

### NEG baseline — CONTINUATION at 10d — not selected (strongest in-sample horizon shown for reference)

- Trade = SHORT (research only, not executable in the $300 account); return per event = -forward return.
- In-sample vs random: n=846, mean +1.22% [-0.40% .. +2.97%]
- **Out-of-sample 2023-Sep 2024 vs random: n=383, mean -0.62% [-1.94% .. +0.69%] -> DIES (sign flips or zero)**
- OOS trade return -1.52% per event; vs SPY same windows -0.09%
- Best ticker US.OXY = -131% of summed returns; top 3 ['US.OXY', 'US.DAL', 'US.AXP'] = -252%; top 5 = -348%. Without US.OXY: n=1218, mean -0.37% [-1.43% .. +0.70%]
- Years with positive mean trade return: 1 of 6

| outlier test (full period) | n | mean trade return | 95% CI | win% |
|---|---|---|---|---|
| all events | 1229 | -0.16% | -1.28% .. +1.08% | +48.74% |
| excl. best 1 | 1228 | -0.21% | -1.33% .. +1.04% | +48.70% |
| excl. best 5 | 1224 | -0.40% | -1.43% .. +0.71% | +48.53% |
| excl. best 10 | 1219 | -0.58% | -1.55% .. +0.50% | +48.32% |
| winsorized 1/99% | 1229 | -0.18% | -1.22% .. +0.97% | +48.74% |

| year | n | mean trade return | vs random | win% |
|---|---|---|---|---|
| 2019 | 160 | -0.47% | +0.89% | +45.62% |
| 2020 | 244 | +2.52% | +5.21% | +54.10% |
| 2021 | 199 | -0.29% | +0.25% | +47.74% |
| 2022 | 243 | -0.38% | -1.77% | +51.85% |
| 2023 | 197 | -1.13% | +0.03% | +48.22% |
| 2024 | 186 | -1.93% | -1.30% | +41.94% |

### CONTINUATION A — REVERSAL at 10d — FROZEN (passed in-sample rule)

- Trade = SHORT (research only, not executable in the $300 account); return per event = -forward return.
- In-sample vs random: n=352, mean +1.78% [+0.61% .. +2.84%]
- **Out-of-sample 2023-Sep 2024 vs random: n=167, mean +0.73% [-0.89% .. +2.42%] -> same sign, NOT significant**
- OOS trade return -1.47% per event; vs SPY same windows -0.12%
- Best ticker US.ABNB = -14% of summed returns; top 3 ['US.ABNB', 'US.ROKU', 'US.DASH'] = -33%; top 5 = -44%. Without US.ABNB: n=512, mean -1.03% [-1.90% .. -0.15%]
- Years with positive mean trade return: 1 of 6

| outlier test (full period) | n | mean trade return | 95% CI | win% |
|---|---|---|---|---|
| all events | 519 | -0.89% | -1.78% .. -0.02% | +45.09% |
| excl. best 1 | 518 | -0.96% | -1.83% .. -0.03% | +44.98% |
| excl. best 5 | 514 | -1.17% | -1.99% .. -0.34% | +44.55% |
| excl. best 10 | 509 | -1.39% | -2.25% .. -0.55% | +44.01% |
| winsorized 1/99% | 519 | -0.89% | -1.77% .. -0.03% | +45.09% |

| year | n | mean trade return | vs random | win% |
|---|---|---|---|---|
| 2019 | 72 | -1.77% | +1.22% | +40.28% |
| 2020 | 128 | -1.04% | +2.96% | +47.66% |
| 2021 | 97 | -0.14% | +0.68% | +48.45% |
| 2022 | 55 | +1.05% | +1.70% | +47.27% |
| 2023 | 102 | -1.57% | +0.83% | +43.14% |
| 2024 | 65 | -1.31% | +0.56% | +41.54% |

### NEG | move 4-6% — CONTINUATION at 10d — FROZEN (passed in-sample rule)

- Trade = SHORT (research only, not executable in the $300 account); return per event = -forward return.
- In-sample vs random: n=336, mean +2.19% [+0.36% .. +4.08%]
- **Out-of-sample 2023-Sep 2024 vs random: n=139, mean -0.44% [-2.02% .. +0.98%] -> DIES (sign flips or zero)**
- OOS trade return -1.32% per event; vs SPY same windows -0.02%
- Best ticker US.AXP = +35% of summed returns; top 3 ['US.AXP', 'US.BAC', 'US.OXY'] = +102%; top 5 = +163%. Without US.AXP: n=466, mean +0.33% [-0.94% .. +1.60%]
- Years with positive mean trade return: 2 of 6

| outlier test (full period) | n | mean trade return | 95% CI | win% |
|---|---|---|---|---|
| all events | 475 | +0.50% | -0.86% .. +2.01% | +48.63% |
| excl. best 1 | 474 | +0.37% | -0.99% .. +1.87% | +48.52% |
| excl. best 5 | 470 | +0.02% | -1.19% .. +1.18% | +48.09% |
| excl. best 10 | 465 | -0.27% | -1.34% .. +0.81% | +47.53% |
| winsorized 1/99% | 475 | +0.51% | -0.76% .. +1.91% | +48.63% |

| year | n | mean trade return | vs random | win% |
|---|---|---|---|---|
| 2019 | 75 | -1.28% | -0.17% | +42.67% |
| 2020 | 100 | +5.01% | +7.07% | +60.00% |
| 2021 | 85 | -0.42% | +0.26% | +47.06% |
| 2022 | 76 | +0.68% | +0.25% | +51.32% |
| 2023 | 86 | -1.28% | -0.15% | +45.35% |
| 2024 | 53 | -1.37% | -0.89% | +39.62% |

### POS | 60d RS > 0 — REVERSAL at 10d — FROZEN (passed in-sample rule)

- Trade = SHORT (research only, not executable in the $300 account); return per event = -forward return.
- In-sample vs random: n=465, mean +1.58% [+0.47% .. +2.63%]
- **Out-of-sample 2023-Sep 2024 vs random: n=228, mean +0.67% [-0.75% .. +2.10%] -> same sign, NOT significant**
- OOS trade return -1.55% per event; vs SPY same windows -0.22%
- Best ticker US.ABNB = -8% of summed returns; top 3 ['US.ABNB', 'US.DELL', 'US.ROKU'] = -21%; top 5 = -30%. Without US.ABNB: n=685, mean -1.28% [-2.10% .. -0.45%]
- Years with positive mean trade return: 0 of 6

| outlier test (full period) | n | mean trade return | 95% CI | win% |
|---|---|---|---|---|
| all events | 693 | -1.17% | -2.01% .. -0.30% | +44.01% |
| excl. best 1 | 692 | -1.22% | -2.05% .. -0.33% | +43.93% |
| excl. best 5 | 688 | -1.39% | -2.20% .. -0.57% | +43.60% |
| excl. best 10 | 683 | -1.58% | -2.36% .. -0.77% | +43.19% |
| winsorized 1/99% | 693 | -1.15% | -1.95% .. -0.31% | +44.01% |

| year | n | mean trade return | vs random | win% |
|---|---|---|---|---|
| 2019 | 87 | -1.40% | +1.53% | +40.23% |
| 2020 | 171 | -1.62% | +2.78% | +44.44% |
| 2021 | 126 | -0.43% | +0.55% | +46.83% |
| 2022 | 81 | -0.02% | +0.70% | +44.44% |
| 2023 | 137 | -1.35% | +1.07% | +43.07% |
| 2024 | 91 | -1.86% | +0.05% | +43.96% |

### POS | GAP_AND_GO — REVERSAL at 5d — FROZEN (passed in-sample rule)

- Trade = SHORT (research only, not executable in the $300 account); return per event = -forward return.
- In-sample vs random: n=645, mean +0.97% [+0.25% .. +1.73%]
- **Out-of-sample 2023-Sep 2024 vs random: n=308, mean +0.55% [-0.39% .. +1.53%] -> same sign, NOT significant**
- OOS trade return -0.29% per event; vs SPY same windows +0.15%
- Best ticker US.SOFI = +154% of summed returns; top 3 ['US.SOFI', 'US.MRNA', 'US.ABNB'] = +394%; top 5 = +556%. Without US.SOFI: n=933, mean -0.04% [-0.60% .. +0.50%]
- Years with positive mean trade return: 3 of 6

| outlier test (full period) | n | mean trade return | 95% CI | win% |
|---|---|---|---|---|
| all events | 953 | +0.07% | -0.53% .. +0.67% | +48.27% |
| excl. best 1 | 952 | +0.03% | -0.55% .. +0.63% | +48.21% |
| excl. best 5 | 948 | -0.08% | -0.57% .. +0.50% | +48.00% |
| excl. best 10 | 943 | -0.20% | -0.70% .. +0.31% | +47.72% |
| winsorized 1/99% | 953 | +0.08% | -0.50% .. +0.68% | +48.27% |

**FLAG: OUTLIER DEPENDENCE** (positive overall, not positive without the best 5 events).

| year | n | mean trade return | vs random | win% |
|---|---|---|---|---|
| 2019 | 127 | -0.55% | +0.44% | +46.46% |
| 2020 | 213 | +0.22% | +1.82% | +46.95% |
| 2021 | 160 | -0.19% | +0.08% | +45.00% |
| 2022 | 145 | +1.43% | +1.18% | +51.72% |
| 2023 | 191 | -0.71% | +0.33% | +46.60% |
| 2024 | 117 | +0.39% | +0.90% | +55.56% |

### NEG | INTRADAY_MOVE — CONTINUATION at 20d — FROZEN (passed in-sample rule)

- Trade = SHORT (research only, not executable in the $300 account); return per event = -forward return.
- In-sample vs random: n=124, mean +4.62% [+0.95% .. +9.05%]
- **Out-of-sample 2023-Sep 2024 vs random: n=52, mean +1.32% [-2.49% .. +4.94%] -> same sign, NOT significant**
- OOS trade return -1.85% per event; vs SPY same windows -1.10%
- Best ticker US.OXY = +122% of summed returns; top 3 ['US.OXY', 'US.ROKU', 'US.C'] = +216%; top 5 = +289%. Without US.OXY: n=174, mean -0.14% [-3.04% .. +2.56%]
- Years with positive mean trade return: 3 of 6

| outlier test (full period) | n | mean trade return | 95% CI | win% |
|---|---|---|---|---|
| all events | 176 | +0.62% | -2.64% .. +3.92% | +48.30% |
| excl. best 1 | 175 | +0.22% | -2.70% .. +3.09% | +48.00% |
| excl. best 5 | 171 | -0.96% | -3.44% .. +1.37% | +46.78% |
| excl. best 10 | 166 | -1.86% | -4.28% .. +0.30% | +45.18% |
| winsorized 1/99% | 176 | +0.72% | -2.18% .. +3.81% | +48.30% |

**FLAG: OUTLIER DEPENDENCE** (positive overall, not positive without the best 5 events).

| year | n | mean trade return | vs random | win% |
|---|---|---|---|---|
| 2019 | 21 | -3.35% | +0.33% | +38.10% |
| 2020 | 37 | +3.72% | +13.05% | +59.46% |
| 2021 | 38 | +1.58% | +2.20% | +44.74% |
| 2022 | 28 | +2.79% | +0.01% | +53.57% |
| 2023 | 26 | -2.18% | +0.77% | +50.00% |
| 2024 | 26 | -1.53% | +1.86% | +38.46% |

### NEG | close > SMA50 — CONTINUATION at 10d — FROZEN (passed in-sample rule)

- Trade = SHORT (research only, not executable in the $300 account); return per event = -forward return.
- In-sample vs random: n=163, mean +2.14% [+0.48% .. +3.73%]
- **Out-of-sample 2023-Sep 2024 vs random: n=74, mean +0.91% [-0.97% .. +2.68%] -> same sign, NOT significant**
- OOS trade return -0.94% per event; vs SPY same windows +0.25%
- Best ticker US.ROKU = -47% of summed returns; top 3 ['US.ROKU', 'US.XYZ', 'US.HOOD'] = -77%; top 5 = -101%. Without US.ROKU: n=227, mean -0.99% [-2.24% .. +0.25%]
- Years with positive mean trade return: 3 of 6

| outlier test (full period) | n | mean trade return | 95% CI | win% |
|---|---|---|---|---|
| all events | 237 | -0.65% | -1.99% .. +0.68% | +48.10% |
| excl. best 1 | 236 | -0.77% | -1.97% .. +0.44% | +47.88% |
| excl. best 5 | 232 | -1.15% | -2.48% .. +0.13% | +46.98% |
| excl. best 10 | 227 | -1.55% | -2.73% .. -0.41% | +45.81% |
| winsorized 1/99% | 237 | -0.55% | -1.79% .. +0.68% | +48.10% |

| year | n | mean trade return | vs random | win% |
|---|---|---|---|---|
| 2019 | 30 | +0.38% | +2.58% | +43.33% |
| 2020 | 67 | -1.69% | +2.89% | +46.27% |
| 2021 | 45 | -0.52% | +0.34% | +46.67% |
| 2022 | 21 | +1.99% | +2.99% | +66.67% |
| 2023 | 34 | +0.08% | +2.15% | +52.94% |
| 2024 | 40 | -1.81% | -0.14% | +42.50% |

### NEG | 60d RS > 0 — CONTINUATION at 20d — FROZEN (passed in-sample rule)

- Trade = SHORT (research only, not executable in the $300 account); return per event = -forward return.
- In-sample vs random: n=388, mean +1.64% [+0.08% .. +3.12%]
- **Out-of-sample 2023-Sep 2024 vs random: n=203, mean -0.18% [-1.90% .. +1.62%] -> DIES (sign flips or zero)**
- OOS trade return -3.42% per event; vs SPY same windows -0.83%
- Best ticker US.PLTR = -4% of summed returns; top 3 ['US.PLTR', 'US.MA', 'US.MS'] = -10%; top 5 = -15%. Without US.PLTR: n=585, mean -2.46% [-3.63% .. -1.19%]
- Years with positive mean trade return: 2 of 6

| outlier test (full period) | n | mean trade return | 95% CI | win% |
|---|---|---|---|---|
| all events | 591 | -2.33% | -3.44% .. -1.06% | +42.13% |
| excl. best 1 | 590 | -2.41% | -3.52% .. -1.20% | +42.03% |
| excl. best 5 | 586 | -2.67% | -3.73% .. -1.52% | +41.64% |
| excl. best 10 | 581 | -2.93% | -4.01% .. -1.79% | +41.14% |
| winsorized 1/99% | 591 | -2.29% | -3.36% .. -1.09% | +42.13% |

| year | n | mean trade return | vs random | win% |
|---|---|---|---|---|
| 2019 | 88 | -3.91% | -0.35% | +35.23% |
| 2020 | 121 | -3.33% | +3.91% | +38.84% |
| 2021 | 94 | +0.04% | +1.02% | +46.81% |
| 2022 | 85 | +0.71% | +1.12% | +52.94% |
| 2023 | 112 | -4.08% | -0.12% | +41.07% |
| 2024 | 91 | -2.62% | -0.25% | +39.56% |

### NEG | GAP_AND_FADE — CONTINUATION at 10d — FROZEN (passed in-sample rule)

- Trade = SHORT (research only, not executable in the $300 account); return per event = -forward return.
- In-sample vs random: n=87, mean +3.02% [+0.08% .. +6.74%]
- **Out-of-sample 2023-Sep 2024 vs random: n=57, mean -2.07% [-4.45% .. +0.48%] -> DIES (sign flips or zero)**
- OOS trade return -2.86% per event; vs SPY same windows -0.73%
- Best ticker US.DAL = +158% of summed returns; top 3 ['US.DAL', 'US.UBER', 'US.XOM'] = +338%; top 5 = +494%. Without US.DAL: n=141, mean -0.16% [-2.10% .. +1.76%]
- Years with positive mean trade return: 3 of 6

| outlier test (full period) | n | mean trade return | 95% CI | win% |
|---|---|---|---|---|
| all events | 144 | +0.27% | -2.00% .. +2.55% | +49.31% |
| excl. best 1 | 143 | -0.09% | -2.13% .. +1.82% | +48.95% |
| excl. best 5 | 139 | -0.78% | -2.69% .. +0.90% | +47.48% |
| excl. best 10 | 134 | -1.45% | -3.25% .. +0.18% | +45.52% |
| winsorized 1/99% | 144 | +0.17% | -1.99% .. +2.20% | +49.31% |

**FLAG: OUTLIER DEPENDENCE** (positive overall, not positive without the best 5 events).

| year | n | mean trade return | vs random | win% |
|---|---|---|---|---|
| 2019 | 20 | -0.26% | +1.07% | +45.00% |
| 2020 | 24 | +5.34% | +9.21% | +45.83% |
| 2021 | 15 | +2.89% | +2.73% | +73.33% |
| 2022 | 28 | +1.27% | -0.74% | +57.14% |
| 2023 | 29 | -4.02% | -2.78% | +41.38% |
| 2024 | 28 | -1.66% | -1.34% | +42.86% |

## 7. $300 test (and larger accounts, same rules)

Strategy simulated: NO long-tradable variant passed the in-sample rule; pre-declared fallback shown for completeness: buy every POSITIVE_SHOCK, 5-day hold. Whole shares at real prices (fractional shares were rejected by Moomoo's paper API), no leverage, max 50% of equity per position, fees $0.99 per order + slippage.

| start $ | signals | trades | skipped | fees $ | avg idle cash | ending $ | total | CAGR | max DD | SPY same period |
|---|---|---|---|---|---|---|---|---|---|---|
| 300 | 1312 | 133 | 1179 | 263.34 | +85.89% | 12.19 | -95.78% | -42.44% | -95.95% | +142.22% |
| 1000 | 1312 | 404 | 908 | 799.92 | +60.50% | 36.10 | -96.26% | -43.63% | -97.05% | +142.22% |
| 3000 | 1312 | 642 | 670 | 1271.16 | +41.02% | 1023.32 | -64.64% | -16.59% | -74.44% | +142.22% |
| 10000 | 1312 | 662 | 650 | 1310.76 | +39.08% | 7073.52 | -26.71% | -5.28% | -60.84% | +142.22% |

Period: 2019-01-08 .. 2024-10-03.

Per-trade costs for this simulated (long) variant, full period:

| position $ | gross | fees | slippage+reg | NET |
|---|---|---|---|---|
| 150 | +0.11% | +1.32% | +0.20% | -1.41% |
| 500 | +0.11% | +0.40% | +0.20% | -0.49% |
| 1000 | +0.11% | +0.20% | +0.20% | -0.29% |
| 3000 | +0.11% | +0.07% | +0.20% | -0.16% |

## 8. Answers

1. **Events:** 2541 (1312 positive, 1229 negative).
2. **Positive shocks** (mean raw forward return; vs matched random, * = CI excludes 0): 1d +0.04% (vs random -0.02%); 2d +0.08% (vs random -0.13%); 3d -0.00% (vs random -0.37%); 5d +0.11% (vs random -0.61%*); 10d +1.26% (vs random -0.31%); 20d +3.02% (vs random -0.36%).
3. **Negative shocks:** 1d +0.02% (vs random -0.01%); 2d -0.27% (vs random +0.37%); 3d -0.14% (vs random +0.33%); 5d +0.17% (vs random +0.20%); 10d +0.16% (vs random +0.65%); 20d +1.50% (vs random +0.33%). (vs random > 0 = kept falling more than normal; < 0 = rebounded.)
4. **Strongest horizon:** POS baseline: 5d (reversal); NEG baseline: 10d (continuation).
5. **Abnormal volume:** No subgroup in this group passed the in-sample rule at any horizon.
6. **Gap behaviour:** In-sample significant: POS | GAP_AND_GO 5d -0.97%; POS | GAP_AND_GO 10d -1.02%; NEG | GAP_AND_FADE 10d +3.02%; NEG | INTRADAY_MOVE 20d +4.62%
7. **Prior relative strength:** In-sample significant: POS | 60d RS > 0 10d -1.58%; POS | 60d RS > 0 20d -2.07%; NEG | 60d RS > 0 20d +1.64%
8. **Trend alignment:** In-sample significant: NEG | close > SMA50 10d +2.14%; NEG | close > SMA50 20d +2.63%
9. **Market regime:** No subgroup in this group passed the in-sample rule at any horizon.
10. **vs random entries:** 14 of 204 in-sample tests significant (~10 expected by chance); frozen hypotheses: 9.
11. **vs SPY (same windows):** see 'excess vs SPY' columns in sections 2-3 and each variant's OOS line.
12. **Out-of-sample 2023-Sep 2024:** POS baseline: same sign, not significant; CONTINUATION A: same sign, not significant; NEG | move 4-6%: DIES; POS | 60d RS > 0: same sign, not significant; POS | GAP_AND_GO: same sign, not significant; NEG | INTRADAY_MOVE: same sign, not significant; NEG | close > SMA50: same sign, not significant; NEG | 60d RS > 0: DIES; NEG | GAP_AND_FADE: DIES.
13. **Without best events:** POS baseline excl. best 5 = -0.23%; NEG baseline excl. best 5 = -0.40%; CONTINUATION A excl. best 5 = -1.17%; NEG | move 4-6% excl. best 5 = +0.02%; POS | 60d RS > 0 excl. best 5 = -1.39%; POS | GAP_AND_GO excl. best 5 = -0.08%; NEG | INTRADAY_MOVE excl. best 5 = -0.96%; NEG | close > SMA50 excl. best 5 = -1.15%; NEG | 60d RS > 0 excl. best 5 = -2.67%; NEG | GAP_AND_FADE excl. best 5 = -0.78%.
14. **Without best ticker:** POS baseline without US.SOFI = -0.24%; NEG baseline without US.OXY = -0.37%; CONTINUATION A without US.ABNB = -1.03%; NEG | move 4-6% without US.AXP = +0.33%; POS | 60d RS > 0 without US.ABNB = -1.28%; POS | GAP_AND_GO without US.SOFI = -0.04%; NEG | INTRADAY_MOVE without US.OXY = -0.14%; NEG | close > SMA50 without US.ROKU = -0.99%; NEG | 60d RS > 0 without US.PLTR = -2.46%; NEG | GAP_AND_FADE without US.DAL = -0.16%.
15. **After costs:** no long-tradable variant passed; for the long fallback (buy every POSITIVE_SHOCK, 5-day hold): gross +0.11%, net $150 -1.41%, $500 -0.49%, $1000 -0.29%, $3000 -0.16%.
16. **$300 enough?** $300 -> $12.19 (-95.78%) vs SPY +142.22%; 1179 of 1312 signals skipped; fees $263.34.

## Limitations

- Survivorship bias (today's 93 large stocks); in-sample tuning risk; overlapping events of the same stock are not independent (the bootstrap clusters by date only, so CIs are, if anything, too narrow).
- Short-side results (negative continuation / positive reversal) are research only: borrow costs, short availability and margin are not modelled.
- Daily bars only: no intraday execution realism beyond next-open entry and 10 bps slippage per side.

Files: outputs/strategy3/ (abnormal_events.csv, hypotheses_*.csv, frozen_in_sample_selection.csv, account_simulation.csv).