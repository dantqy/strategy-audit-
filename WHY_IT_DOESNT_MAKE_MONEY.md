# Why the original strategy doesn't make money

**The original plan:** when a big US stock jumps or drops **5%+ after earnings on 2x+ normal volume**, buy at the next open, hold **1–3 days**, using a **$300** account ($150 per trade).

**Tested on:** real Moomoo data, 93 big US stocks, **Jan 2019 – Sep 2024** (in-sample; the last ~2 years are locked and unused).

## 1. Fees eat the whole edge
Moomoo SG charges **$0.99 per order**. On a $150 trade that's $1.98 per round trip, about **1.3%**, plus about 0.2% slippage, so **~1.5% per trade**.
The signal does work a little: stocks kept drifting **+0.4–0.5% the next day**. But +0.5% − 1.5% = **−1.1% per trade**. The 1-day version turned **$300 into about $100 (−66%)**.

## 2. The real edge is slow
The drift after earnings plays out over **weeks, not days**:

| Hold | Avg per trade after fees |
|---|---|
| 1–5 days | −1.1% to 0% |
| 20 days | **+2.4%** (held up across all 27 settings tested) |
| 60 days | +7.2% |

So the idea isn't wrong. The **1–3 day holding period** was the problem.

## 3. $300 is too small to capture it
- **Whole shares only:** Moomoo's paper API rejected fractional shares ("Invalid quantity"). Any stock over ~$140 can't be bought with $150, and that rules out most of the big names.
- **Only 2 slots:** with 20-day holds, both $150 slots stay busy.
- Result: **200 of 272 signals skipped**. The account made **+35%** over ~5.7 years (~5%/year), and most of that came from **one lucky Super Micro trade (+76%)**.
- Fees over the period: **~$143, nearly half the starting money.**

## 4. Idle cash loses to the market
Money waits in cash between signals. Over the same period, simply holding **SPY (the S&P 500) made +140%**. Even with more money the strategy trailed:

| Starting money | Strategy | SPY |
|---|---|---|
| $300 | −28% to +35% | +140% |
| $3,000 | +56% | +140% |
| $10,000 | +70% | +140% |

## 5. The other signals had no edge
"Volume spike" and "breakout" signals did **no better than buying the same stocks on random days**.

## 6. Reality would be worse, not better
- **Survivorship bias:** the stock list is *today's* winners, which flatters every result.
- **In-sample only:** the settings were chosen on this data.
- **~23% of earnings events were skipped:** Moomoo didn't know whether they were released before or after market hours.

## Bottom line
The earnings signal is real, but it's **small and slow**. With **$300, $0.99 fees and whole shares**, costs and idle cash swamp it, and a plain index fund did far better. It could only become worthwhile with **much more capital (≈$3k+), lower fees, and idle money kept invested** instead of sitting in cash. Even then it didn't beat SPY in 2019–2024.

*Not financial advice; historical, in-sample results. Full details: `outputs/backtests/`.*
