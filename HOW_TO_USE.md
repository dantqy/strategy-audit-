# How to use the Moomoo paper scanner

A plain-English guide. You don't need to know how to code, and you don't need to have used Moomoo before.

---

## 1. What this is, in one paragraph

Every day the **scanner** looks at 93 big US companies (Apple, Nvidia, Tesla…) and flags the ones that did something unusual, like jumping after an earnings report on very heavy trading. The **backtest** checks whether buying those flagged stocks would have made money over the last ~7 years, after fees. If it would have, you **practise** with **paper trading**, which uses Moomoo's fake money, to see if it works in real life. Real money only comes much later, if ever.

### What it will NOT do

- **It never trades on its own.** Every order shows you a ticket, and nothing happens unless you type `YES`.
- **It never touches your real money.** Every order goes to Moomoo's *paper* (practice) account. That's locked in the code, and a safety test fails if anything tries otherwise.
- **It doesn't promise profit.** The backtest is there to tell you honestly whether the idea works. "It doesn't work" is a useful answer: it saves you your SGD 300.

---

## 2. Words you'll see

| Word | What it means |
|---|---|
| **Paper trading** | Practice trading with fake money inside Moomoo. Real prices, fake cash. |
| **OpenD** | A small Moomoo program on your laptop. The scripts talk to Moomoo *through* it. **It must be open and logged in whenever you use anything here.** |
| **Ticker** | A stock's short code: `AAPL` = Apple, `NVDA` = Nvidia. The scripts write them as `US.AAPL`. |
| **Scanner / scan** | The daily search for unusual stocks. It only looks; it never buys. |
| **Signal** | The reason a stock was flagged: *Earnings Drift* (big move after earnings), *Volume Anomaly* (far more trading than normal), *Momentum Breakout* (price broke above/below its recent range). |
| **BULLISH / BEARISH** | Flagged going up / going down. Your account only **buys** (it can't bet on falls), so only BULLISH stocks can be bought. |
| **Open / close** | The US stock market opens 9:30am and closes 4:00pm **New York time**. See the Singapore times below. |
| **Session** | One trading day. Weekends and US holidays don't count. |
| **Backtest** | Replaying history to see how the rules *would have* done. |
| **Net** | After fees. **This is the number that matters.** "Gross" is before fees. |
| **Fill / filled** | Your order actually got executed. A submitted order is not a filled order. |

---

## 3. US market hours in Singapore time

The US changes its clocks, so the Singapore times shift by an hour:

| | US open | US close |
|---|---|---|
| **Until 1 Nov 2026** (US summer time) | **21:30** SGT | **04:00** SGT (next morning) |
| **From 2 Nov 2026** (US winter time) | **22:30** SGT | **05:00** SGT (next morning) |

The whole routine is designed so you **never need to be awake at 4am**. You place orders in the evening before the US opens, and they fill at the open.

---

## 4. The folder and the shortcut buttons

Everything lives in **File Explorer → Downloads → moomoo_scanner**.

Instead of typing commands, **double-click** these files. Each opens a black window, does one job, and waits for you to press a key when you've finished reading:

| File | What it does | Sends an order? |
|---|---|---|
| `0_FIRST_TIME_SETUP.bat` | Installs everything. **Already done on this laptop**; only needed on a new computer. | No |
| `1_CHECK_CONNECTION.bat` | Checks OpenD is running and logged in. Every line at the bottom should say `PASS`. | No |
| `2_SCAN.bat` | Finds today's flagged stocks. | No |
| `2B_SWING_SCAN.bat` | **Swing ideas:** today's big movers on heavy volume across the *whole* US market, with a simple plan for each. Untested. See section 6c. | No |
| `3_BUY.bat` | Asks for a ticker, shows a paper-buy ticket, and waits for `YES`. | Only if you type YES |
| `4_CHECK_FILLS.bat` | Asks Moomoo whether your paper orders were filled, and records them. | No |
| `5_WHAT_TO_SELL.bat` | Lists what you hold and when each should be sold. | No |
| `6_SELL.bat` | Asks for a ticker, shows a paper-sell ticket, and waits for `YES`. | Only if you type YES |
| `7_RUN_BACKTEST.bat` | Re-tests the strategy on history (~30 min the first time, fast after). | No |
| `FRACTIONAL_TEST.bat` | One-time test: can the paper account buy part of a share? Places one 0.1-share paper order (fake money). Run while the US market is open. | Only if you type YES |
| `8_TELEGRAM_SETUP.bat` | One-time: connects your own Telegram bot (see section 6b). | No |
| `9_TELEGRAM_BOT.bat` | Runs the bot so you can scan and approve orders from your phone. Keep it open. | Only after you reply `YES <code>` |

> Windows may show a blue "Windows protected your PC" box the first time. Click **More info → Run anyway**. These are plain text files; right-click → *Edit* shows exactly what each one runs.

---

## 5. Before anything: start OpenD

1. Open **OpenD** from the Start menu and log in (same Moomoo ID as the app).
2. Leave it open. **If OpenD is closed, nothing works.** You'll see `OpenD is not reachable`.
3. The first time each day, double-click `1_CHECK_CONNECTION.bat` and check everything says `PASS`.

You *can* also open the normal Moomoo app to look around; it doesn't disturb OpenD. Your practice trades appear under **Paper Trading** in the app, not in your real portfolio.

---

## 6. The daily routine

> **Don't start paper trading until the backtest says a signal is worth trading** (see section 8). Until then, just run the scan to get familiar with it.

### Step 1: Scan (any time after the US close, before the next open)

Between **04:00 and 21:30 SGT** (winter: 05:00–22:30), double-click **`2_SCAN.bat`**.

You'll get a ranked list like this:

```
1. US.XYZ  Example Corp
   Score: 5   (earnings drift +3, volume anomaly +2)
   Direction: BULLISH
   Signals:  ✓ Earnings Drift  ✓ Volume Anomaly
   Price: $48.20  (bar 2026-09-23)
   Data status: LAST_COMPLETED_BAR
   Reason: AFTER earnings on 2026-09-22: +7.4% on 3.1x volume ...
```

What to look at:
- **Only Earnings Drift signals can be bought.** Volume Anomaly and Momentum Breakout on their own showed no edge in the backtest, so they're shown for information only.
- The earnings reaction must be from the **latest** US trading day. A reaction from the day before is shown, but it's too late to buy the way the backtest did.
- **Direction** must be **BULLISH** to buy.
- **Data status** must be **LAST_COMPLETED_BAR**. If you scan while the US market is open, you'll see `LIVE_INTRADAY`. Those are for looking only and **can't be bought**, because the backtest only tested finished days.
- **Price**: one position is about **$150**, and only whole shares can be bought. A $400 stock can't be bought with $150, and the script will tell you so.
- **No candidates** is a normal answer on quiet days.

### Step 2: Buy (before the US open, 21:30 SGT)

Double-click **`3_BUY.bat`**, type the ticker (e.g. `XYZ`), and press Enter. You'll see a ticket:

```
PAPER ORDER (TrdEnv.SIMULATE) - REVIEW CAREFULLY
Ticker               : US.XYZ
Side                 : BUY
Quantity             : 2
Estimated price      : $50.61 (limit)
Entry                : open of 2026-09-24 (DAY limit; no fill if it gaps above the limit)
Planned exit         : open of 2026-09-25 (hold 1 session(s), as backtested)
Type YES to submit this PAPER order (anything else cancels):
```

Check that the ticker and quantity look right and the first line says `TrdEnv.SIMULATE` (paper). Type **`YES`** in capitals and press Enter to send it. Anything else cancels.

**What the price means:** the order is allowed to pay up to 5% above yesterday's close. It fills when the US market opens. If the stock jumps more than 5% overnight, it **won't fill**. That's on purpose, so you never chase a spike.

### Step 3: Check the fill (after the open, or the next day)

Double-click **`4_CHECK_FILLS.bat`**. It shows what actually happened:
- `FILLED_ALL -> booked 2 @ $49.80`: you own it at that price.
- `CANCELLED_ALL -> no fill booked`: it didn't fill (usually a big gap up). Your cash is released.
- `SUBMITTED` / still working: check again later.

### Step 4: Sell on the planned day

Double-click **`5_WHAT_TO_SELL.bat`**:

```
US.XYZ   qty 2 @ $49.80 | DUE_NOW   | sell at the open of 2026-09-25
```

- **UPCOMING**: not yet. Hold.
- **DUE_NOW**: place the sell **this evening, before the US open**. Double-click **`6_SELL.bat`**, type the ticker, check the ticket, and type `YES`. It sells at the open.
- **OVERDUE**: the planned exit has passed. Sell as soon as you can.

Then run `4_CHECK_FILLS.bat` after the open to record the sale.

### A full example, in Singapore time (holding 1 day)

| When (SGT) | What happens |
|---|---|
| Wed night | US trading day. XYZ jumps after earnings. |
| **Thu 04:00** | US market closes. The signal is now confirmed. |
| **Thu, any time before 21:30** | You run `2_SCAN.bat`, then `3_BUY.bat`, and type YES. |
| Thu 21:30 | US opens and your buy fills. |
| **Fri, any time before 21:30** | `4_CHECK_FILLS.bat`, `5_WHAT_TO_SELL.bat` (says DUE_NOW), then `6_SELL.bat` and type YES. |
| Fri 21:30 | US opens and your sell fills. |
| Sat | `4_CHECK_FILLS.bat` to record the sale. |

**The real setting is now 20 trading days (about 4 weeks), not 1.** The backtest showed 1–5 day holds lose money after fees, while 20 days had an edge in the tested history. So in practice the "sell" step comes about four weeks after the buy. `5_WHAT_TO_SELL.bat` (or `/exits` on Telegram) tells you the exact date, and the ticket shows it before you approve.

---

## 6b. Doing it all from your phone (Telegram)

Instead of sitting at the laptop, you can get the scan and the order tickets on Telegram and approve them from your phone.

**What must stay on:** the laptop, **OpenD** (logged in), and the black **bot window**. Telegram only carries messages; the orders still go from your laptop. Set your laptop so it doesn't go to sleep while plugged in: Windows Settings → System → Power → *When plugged in, put my device to sleep after* → **Never**.

### One-time setup (~2 minutes)

1. Double-click **`8_TELEGRAM_SETUP.bat`** and follow the steps on screen:
   - In Telegram, open **@BotFather** (blue tick), send `/newbot`, and choose a name and a username ending in `bot`.
   - BotFather gives you a **token** (like `1234567890:AAH...`). **Treat it like a password.**
   - Paste it into the setup window. It stays hidden and is saved only on your laptop.
   - Open your new bot in Telegram and press **START**. The setup window asks "Is that you?": type `y`.
2. You'll get a **"Connected"** message in Telegram.

### Every day

1. Make sure OpenD is open and logged in.
2. Double-click **`9_TELEGRAM_BOT.bat`** and leave the window open.

### What the bot does by itself

| When (SGT, US summer time) | Message you get |
|---|---|
| ~04:20 (after the US close) | Today's scan, with `-> /buy XYZ` next to anything you're allowed to buy, plus any positions due to sell |
| ~21:50 (after the US open) | Whether your pending orders filled |

In US winter time (from 2 Nov), add one hour.

### Commands you can send the bot

| Send | What happens |
|---|---|
| `/scan` | Runs the scan now |
| `/swing` | Whole-market swing ideas (untested) |
| `/sbuy 2` | Paper-buy swing idea #2 (ticket, then `YES <code>`) |
| `/ssell XYZ` | Sell a swing position early |
| `/sstatus` | Swing paper account |
| `/sstats` | Swing scorecard: all ideas vs your picks vs skipped |
| `/buy XYZ` | Shows a paper-buy ticket |
| `/sell XYZ` | Shows a paper-sell ticket |
| `/sync` | Checks whether orders filled |
| `/exits` | What you hold and when to sell |
| `/status` | Paper cash, positions, pending orders |
| `/help` | This list |

### Approving an order from your phone

After `/buy XYZ` the bot sends the full ticket, then:

```
To send this PAPER order reply exactly:
YES 7F3A2C

Anything else cancels. Expires in 15 min.
```

Reply **`YES 7F3A2C`**, with the code from *that* message. The code changes with every ticket, so:
- a plain `YES` **cancels**;
- an old `YES` from an earlier ticket **cancels**;
- messages from anyone other than you are **ignored**;
- no reply within 15 minutes **cancels**;
- if you approve a buy after the US market has already opened, it's **refused**, same as on the laptop.

All the rules from section 7 apply exactly the same on Telegram.

---

## 6c. Swing ideas (untested, high risk)

`2B_SWING_SCAN.bat` (or `/swing` on Telegram) searches **all ~6,200 NYSE/Nasdaq stocks** and lists ones that are:
- up **4%+ today** (or gapping up 4%+ before the open),
- trading at **2x+ their normal volume**,
- priced **$3–$150** (so $300 buys at least 2 whole shares),
- not tiny companies (**$300M+** market value) and actively traded.

Each idea comes with a simple plan:

```
1. QMCO $28.03 +7.9% vol 2.7x cap $1.1B
   holding near day high
   plan: 10 sh ($280), stop $25.79 (-8.0%, $22 at risk), fees 0.7% round trip
```

- **10 sh ($280)**: whole shares $300 buys.
- **stop**: a price where you'd sell to cut the loss. It's today's low, but never more than 8% below the price.
- **$22 at risk**: roughly what you'd lose if the stop is hit (it can be worse if the stock gaps down overnight).
- **fees**: the two $0.99 platform fees as a % of the trade.

Tags like *earnings*, *near 52-week high* and *holding near day high* are extra context.

**Important:** unlike the earnings strategy, **nothing here has been backtested**. It shows what's moving, not what will keep moving. The scripts will not paper-buy these (paper buys stay limited to the tested earnings signals). If you trade them, you do it yourself in the Moomoo app, with your own judgement and money you can afford to lose. Filters live under `swing:` in `config\settings.yaml`.

**Best times (SGT, US summer time):** 21:30–04:00 for live movers, just after 04:00 for the day's final list, or 16:00–21:30 for pre-market gaps. Add one hour from 2 Nov.

---

## 6d. Paper swing trading from Telegram (auto-tracked)

A separate **$300 paper account** just for swing trades, so you can see if *you* can make the swing ideas pay, with fake money.

**Every US trading day:**
1. **~22:15 SGT** (23:15 from 2 Nov) the bot sends the top 5 swing ideas. You can also send `/swing` any time during US hours.
2. To take one, reply **`/sbuy 2`** (idea number) or `/sbuy QMCO`. The bot sends a ticket:
   ```
   Quantity             : 10
   Estimated price      : $28.68 (limit)
   Stop-loss            : $26.13  (bot SELLS automatically if the price trades at/below it in US hours)
   Time exit            : open of 2026-10-01 (bot SELLS automatically after 5 session(s))
   Auto exits           : ON - approving this ticket approves those two sells too
   ```
3. Reply **`YES <code>`** to buy, or anything else to skip.
4. **That's it.** The bot checks your stop every 2 minutes during US hours and sells (paper) if it's hit, even at 3am while you sleep. If the stop isn't hit, it sells at the open after 5 trading days. You get a Telegram message whenever it does.

**Refused on purpose:** the price already ran 3%+ past the idea, it's already below the stop, the idea list is older than 90 minutes (send `/swing` again), or you already hold that stock.

**Your scorecard, `/sstats`** (also sent every morning ~04:20 SGT). Every idea is tracked daily, **taken or not**:
- **All ideas:** how the screener itself did (entry at the idea price, the stop, 5-day exit, after fees).
- **You took vs you skipped:** whether your choices beat the ideas you passed on.
- **Your swing account:** actual paper cash and positions vs the $300 start.

Judge it after **20+ finished ideas**, not after one good or bad week.

Other commands: `/sstatus` (swing account), `/ssell XYZ` (sell early; asks for `YES <code>`).

**Real money:** the bot only ever trades paper. If the scorecard looks good after a few weeks and you decide to trade real money, you'd do that yourself in the Moomoo app.

---

## 7. Things the scripts refuse to do (and why)

| Message | Meaning | What to do |
|---|---|---|
| `OpenD is not reachable` | OpenD is closed or logged out. | Open OpenD, log in, and try again. |
| `NOT LIKE THE BACKTEST: candidate data is LIVE_INTRADAY` | You scanned while the US market was open. | Scan again after the US close (04:00 SGT). |
| `NOT LIKE THE BACKTEST: ... scan is stale` | Another US day has finished since your scan. | Run `2_SCAN.bat` again. |
| `NOT LIKE THE BACKTEST: entry session ... already opened` | You're too late: the US market already opened. | Skip this one. Chasing it isn't what was tested. |
| `NOT LIKE THE BACKTEST: signals [...] are information only` | Volume/breakout signals had no backtested edge. | Only buy Earnings Drift signals. |
| `NOT LIKE THE BACKTEST: the earnings reaction was on ...` | The reaction happened a day earlier; the backtest bought right after it. | Skip it. |
| `NOT_EXECUTABLE_WITH_CURRENT_PAPER_CAPITAL` | One share costs more than your ~$150 position. | Skip it. |
| `... is not in the latest scan` | You typed a ticker the scanner didn't flag. | Only buy what the scan lists. |
| `direction is BEARISH` | Flagged going down; you can only buy. | Skip it. |
| `already held or has a pending order` | You already own it or have an order in. | Nothing. The strategy never doubles up. |
| `insufficient paper cash` | Not enough practice money left. | Wait for a position to be sold. |

These rules exist so your practice trades copy the backtest exactly. Otherwise practice results would tell you nothing about the backtest.

---

## 8. Reading the backtest results

Double-click `7_RUN_BACKTEST.bat` (or ask Claude to run it). The report is saved in `outputs\backtests\<date>\summary.md`.

What to look for, in plain terms:

- **"mean net"** = the average profit per trade **after fees**. It needs to be **above 0**. Fees are about 1.5% per round trip on a $150 position, which is a big hurdle.
- **"95% CI"** = the range the true average probably lies in. If it goes from negative to positive (e.g. `-0.8% .. +1.1%`), **we can't tell whether it works**. You want the whole range above 0.
- **"n"** = the number of trades. `LOW SAMPLE SIZE` means too few to trust.
- **Consistency beats one great number.** A real edge looks decent across several holding periods and nearby settings. One amazing cell surrounded by losers is usually luck.
- **Too good to be true?** The report flags things like a >80% win rate or a tiny drawdown. Those usually mean a bug, not a gold mine.

### The one-time "final exam"

The last ~30% of history (roughly mid-2024 to now) is **locked** and not used while choosing settings. Once the settings are final, it's opened **once** with `run_backtest.py --final-oos`. If the edge holds on data it has never seen, that's real evidence. Opening it repeatedly and re-tweaking ruins it, and the tool warns you if you do.

---

## 9. Rules to protect yourself

1. **Keep OpenD open** while anything is running.
2. **Don't paper trade signals the backtest didn't support.**
3. **Paper trade for a few weeks** and compare with the backtest before even thinking about real money.
4. **Real money is a separate decision.** Your account is in SGD, so US stocks also cost a currency conversion, which paper trading ignores. Talk it through first; this project has no real-money mode on purpose.
5. **Data limit:** Moomoo lets this account download history for about 100 different stocks per week. The 93 stocks + SPY already use ~94, so don't add tickers to `data\universe.csv` without checking.
6. **Fix your PC clock:** Windows Settings → Time & language → Date & time → **Sync now**. It was 77 seconds slow.

---

## 10. Where things are saved

| What | Where |
|---|---|
| Scan results | `outputs\scans\` |
| Backtest reports | `outputs\backtests\<date>\summary.md` |
| Your practice account (cash, positions, history) | `outputs\paper_ledger.json` |
| Settings | `config\settings.yaml` (ask before changing anything) |
| Logs, if something goes wrong | `outputs\logs\` |
| Telegram bot token (private: never share or upload) | `config\telegram.local.yaml` |

Stuck? Copy the whole black window's text and paste it to Claude.
