# Standing rules for any coding agent working in this repo

1. **Paper only.** The only `place_order()` call lives in `scanner/paper_execution.py` with a literal `trd_env=TrdEnv.SIMULATE`. Do not add a live mode, an environment parameter, or a config switch. Keep `tests/test_execution_safety.py` passing.
2. **A human approves every order.** The scanner never submits anything. The one exception is a swing exit. A swing BUY ticket may print a stop and a time exit with "Auto exits: ON". Approving that ticket approves those two SELLs, which the bot then sends through `preapproved_exit_approval()`. That function can never approve a BUY, and never a position without such an approved plan. Do not widen it.
3. **No lookahead.** A signal confirmed at R's close is entered at the next session's open. Rolling statistics exclude the current bar. Use the US exchange calendar and never the local date.
4. **Out-of-sample stays locked.** Tune on in-sample only. Run `--final-oos` once per final configuration. Never tune after looking at OOS.
5. **Log every experiment** in `outputs/backtests/experiment_log.csv`. Prefer a broad, stable region of parameters over one best cell.
6. **Be skeptical.** A Sharpe above 3, a win rate near 100% or a tiny drawdown means investigate for a bug first.
7. **Run it.** Run `pytest` before calling any change done. Mock tests are not live checks: say which kind you ran.
8. **Respect the API budget.** Stay under 100 distinct stocks per 7 days and 60 requests per 30 s. Cache everything reusable.
9. **Never fabricate data.** Missing candles are reported, never filled. Don't swap in fake data to make a test pass.
10. **Never optimise for a daily or weekly dollar target.**
