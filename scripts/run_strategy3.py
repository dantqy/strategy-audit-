"""Strategy 3 research run: abnormal repricing -> continuation or reversal? (BACKTEST ONLY)

    python scripts/run_strategy3.py

Uses cached Moomoo daily bars only (no API calls, no orders). Writes
outputs/strategy3/abnormal_events.csv, supporting tables, and
STRATEGY_3_ABNORMAL_REPRICING_RESULTS.md in the project root.
"""
from __future__ import annotations

import copy
import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from backtest.engine import simulate_account  # noqa: E402
from backtest.metrics import equity_stats  # noqa: E402
from backtest.validation import append_experiment_log  # noqa: E402
from scanner.config import load_config, resolve  # noqa: E402
from scanner.data_cache import DailyBarCache  # noqa: E402
from scanner.universe import load_universe  # noqa: E402
from strategy3 import study as S  # noqa: E402
from strategy3.features import EVENT_END, EVENT_START, HORIZONS, IS_END, build_events  # noqa: E402

OUT = resolve("outputs/strategy3")
REPORT = resolve("STRATEGY_3_ABNORMAL_REPRICING_RESULTS.md")
DATASET_COLS = ["ticker", "event_date", "open", "high", "low", "close", "volume", "daily_return",
                "market_adjusted_return", "gap_return", "intraday_return", "relative_volume", "normalized_move",
                "ATR_move", "SMA20", "SMA50", "SMA200", "return_20d_prior", "return_60d_prior",
                "relative_strength_20d", "relative_strength_60d", "event_direction", "event_type", "gap_class",
                "trend_SMA50", "trend_SMA200", "market_regime", "sample", "entry_date", "entry_open"] + \
    [f"forward_{h}d" for h in HORIZONS] + [f"SPY_forward_{h}d" for h in HORIZONS] + \
    [f"excess_forward_{h}d" for h in HORIZONS] + [f"random_forward_{h}d" for h in HORIZONS] + \
    [f"exit_date_{h}d" for h in HORIZONS]


def pct(x, d=2):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:+.{d}f}%"


def md_table(df: pd.DataFrame, cols: list[tuple[str, str, str]]) -> list[str]:
    """cols: (column, header, kind) kind in pct|int|str|ci|f2"""
    head = "| " + " | ".join(h for _, h, _ in cols) + " |"
    lines = [head, "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        cells = []
        for c, _, k in cols:
            v = r[c] if c in r else None
            if k == "pct":
                cells.append(pct(v))
            elif k == "int":
                cells.append("" if v is None or v != v else f"{int(v)}")
            elif k == "f2":
                cells.append("n/a" if v is None or v != v else f"{v:.2f}")
            elif k == "ci":
                lo, hi = r[c[0]], r[c[1]]
                cells.append(f"{pct(lo)} .. {pct(hi)}")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def load_prices(cfg):
    adj, raw = DailyBarCache(resolve("data/cache/daily"), "QFQ"), DailyBarCache(resolve("data/cache/daily"), "NONE")
    codes = load_universe(cfg)
    prices = {c: adj.load(c)[0] for c in codes if adj.load(c)[0] is not None}
    raws = {c: raw.load(c)[0] for c in codes if raw.load(c)[0] is not None}
    spy = adj.load(cfg["universe"]["benchmark"])[0]
    return prices, raws, spy


def account_runs(ev, variant, prices, raws, spy, cfg):
    """Long-only whole-share account on the chosen variant; SAME rules for every starting capital."""
    e = variant["events"]
    h = variant["horizon"]
    sig = pd.DataFrame({"code": e["ticker"], "direction": "BULLISH", "entry_date": e["entry_date"],
                        f"exit_date_{h}": e[f"exit_date_{h}d"], "entry_open": e["entry_open"],
                        "reaction_move_pct": e["daily_return"] * 100, "bucket": variant["name"]})
    rows = []
    for cash in (300, 1000, 3000, 10000):
        c = copy.deepcopy(cfg)
        c["backtest"]["exit_at"] = "close"
        c["account"].update(starting_cash=float(cash), max_position_pct=0.5)
        curve, trades, info = simulate_account(sig, prices, c, h, with_costs=True, raw_prices=raws)
        st = equity_stats(curve["equity"])
        spy_w = spy.loc[curve.index[0]:curve.index[-1], "close"]
        spy_ret = float(spy_w.iloc[-1] / spy_w.iloc[0] - 1)
        rows.append({"start_cash": cash, "signals": len(sig), "executed": len(trades),
                     "skipped": info["not_executable"], "skipped_too_expensive": info["skipped_share_too_expensive"],
                     "skipped_no_cash": info["skipped_no_free_cash"],
                     "fees_usd": len(trades) * 2 * (cfg["costs"]["platform_fee_per_order"] + cfg["costs"]["commission_per_order"]),
                     "avg_idle_cash_pct": float((curve["cash"] / curve["equity"]).mean()),
                     "ending_capital": float(curve["equity"].iloc[-1]), "total_return": st["total_return"],
                     "cagr": st["annualized_return"], "max_drawdown": st["max_drawdown"],
                     "spy_total_return": spy_ret, "spy_ending": cash * (1 + spy_ret),
                     "period": f"{curve.index[0]} .. {curve.index[-1]}"})
    return pd.DataFrame(rows)


def main() -> int:
    cfg = load_config()
    OUT.mkdir(parents=True, exist_ok=True)
    prices, raws, spy = load_prices(cfg)
    first = min(df.index[0] for df in prices.values())
    print(f"Loaded {len(prices)} stocks (+SPY from {spy.index[0]}); earliest stock bar {first}")
    if spy.index[0] > date(2018, 3, 20):
        print("WARNING: SPY history starts too late for SMA200 at 2019-01-02; early regime labels will be missing.")

    ev, feats = build_events(prices, spy)
    ev = S.add_random_baseline(ev, feats, EVENT_START, EVENT_END)
    ev[[c for c in DATASET_COLS if c in ev.columns]].to_csv(OUT / "abnormal_events.csv", index=False)
    n_pos, n_neg = (ev["event_direction"] == "POSITIVE_SHOCK").sum(), (ev["event_direction"] == "NEGATIVE_SHOCK").sum()
    print(f"Events {EVENT_START}..{EVENT_END}: {len(ev)} (positive {n_pos}, negative {n_neg}); "
          f"in-sample {int((ev['sample'] == 'IN_SAMPLE').sum())}, out-of-sample {int((ev['sample'] == 'OUT_OF_SAMPLE').sum())}")

    is_ev = ev[ev["sample"] == "IN_SAMPLE"]
    full_tab = S.all_hypotheses_table(ev)
    is_tab = S.all_hypotheses_table(is_ev)
    full_tab.to_csv(OUT / "hypotheses_full_period.csv", index=False)
    is_tab.to_csv(OUT / "hypotheses_in_sample.csv", index=False)
    n_hyp = len(S.hypotheses())
    n_tests = n_hyp * len(HORIZONS)

    # ---- pre-registered selection on 2019-2022, then FROZEN
    frozen = S.select_in_sample(is_tab)
    frozen.to_csv(OUT / "frozen_in_sample_selection.csv", index=False)
    Hmap = {H["name"]: H for H in S.hypotheses()}

    def strongest_is(name):
        t = is_tab[is_tab["hypothesis"] == name].copy()
        t["abs_t"] = t["vs_random_t"].abs()
        r = t.sort_values("abs_t", ascending=False).iloc[0]
        return int(r["horizon"]), ("CONTINUATION" if r["vs_random"] > 0 else "REVERSAL")

    studied = []
    for name in ["POS baseline", "NEG baseline"] + [n for n in frozen["hypothesis"] if n not in ("POS baseline", "NEG baseline")]:
        H = Hmap[name]
        if name in set(frozen["hypothesis"]):
            fr = frozen[frozen["hypothesis"] == name].iloc[0]
            h, eff, sel = int(fr["horizon"]), fr["effect"], True
        else:
            (h, eff), sel = strongest_is(name), False
        long_ok = (H["direction"] == "POSITIVE_SHOCK") == (eff == "CONTINUATION")
        e_all = S.trade_returns(ev, H["mask"], h, eff)
        e_is, e_oos = e_all[e_all["sample"] == "IN_SAMPLE"], e_all[e_all["sample"] == "OUT_OF_SAMPLE"]
        st_is = S._st(e_is["trade_vs_random"], e_is["event_date"], 1000)   # same bootstrap as the selection table
        st_oos = S._st(e_oos["trade_vs_random"], e_oos["event_date"], 2000)
        tr_oos = S._st(e_oos["trade_ret"], e_oos["event_date"], 2000)
        by_stock, sinfo = S.stock_table(e_all)
        v = {"name": name, "direction": H["direction"], "horizon": h, "effect": eff, "selected": sel,
             "long_tradable": long_ok, "events": e_all, "is": st_is, "oos": st_oos, "oos_trade": tr_oos,
             "oos_spy_excess": e_oos[f"excess_forward_{h}d"].mean() * (1 if long_ok else -1) if len(e_oos) else np.nan,
             "outliers": S.outlier_table(e_all), "stock": sinfo, "by_stock": by_stock, "years": S.year_table(e_all),
             "costs": S.cost_table(e_all, cfg) if long_ok else None}
        oos_same = st_oos.get("mean", 0) * st_is.get("mean", 0) > 0 if st_oos["n"] else False
        v["oos_survives"] = bool(oos_same and st_oos.get("significant_5pct"))
        v["oos_same_sign"] = bool(oos_same)
        ex5 = v["outliers"].set_index("variant").loc["excl. best 5", "mean"]
        yrs = v["years"]
        v["robust"] = {"excl_best5_positive": bool(ex5 > 0),
                       "without_best_ticker_positive": bool((sinfo["without_best_mean"] or 0) > 0),
                       "years_positive": int((yrs["mean_trade_ret"] > 0).sum()), "years_total": len(yrs),
                       "net_1000_positive": bool(long_ok and v["costs"].set_index("position_usd").loc[1000, "net_expectancy"] > 0)}
        studied.append(v)

    # ---- pre-registered verdict
    sel_v = [v for v in studied if v["selected"]]
    surv = [v for v in sel_v if v["oos_survives"]]
    robust = [v for v in surv if v["robust"]["excl_best5_positive"] and v["robust"]["without_best_ticker_positive"]
              and v["robust"]["years_positive"] >= 4 and v["robust"]["net_1000_positive"]]
    if not sel_v:
        verdict = "NO EVIDENCE OF EDGE"
    elif robust:
        verdict = "ROBUST HISTORICAL EFFECT — FORWARD PAPER TEST NEXT"
    elif surv:
        verdict = "PROMISING EFFECT — NEEDS FURTHER VALIDATION"
    elif any(v["oos_same_sign"] for v in sel_v):
        verdict = "WEAK / INCONCLUSIVE EFFECT"
    else:
        verdict = "NO EVIDENCE OF EDGE"

    # ---- $300 test last: best long-tradable frozen variant, else the pre-declared fallback
    longs = [v for v in sel_v if v["long_tradable"]]
    if longs:
        acct_v = max(longs, key=lambda v: abs(v["is"].get("mean", 0)) / max(v["is"].get("std", 1) / math.sqrt(max(v["is"]["n"], 1)), 1e-9))
        acct_note = f"frozen in-sample variant '{acct_v['name']}' ({acct_v['effect']}, {acct_v['horizon']}d hold)"
    else:
        b = next(v for v in studied if v["name"] == "POS baseline")
        acct_v = {**b, "horizon": 5, "events": S.trade_returns(ev, Hmap["POS baseline"]["mask"], 5, "CONTINUATION")}
        acct_note = ("NO long-tradable variant passed the in-sample rule; pre-declared fallback shown for "
                     "completeness: buy every POSITIVE_SHOCK, 5-day hold")
    acct = account_runs(ev, acct_v, prices, raws, spy, cfg)
    acct.to_csv(OUT / "account_simulation.csv", index=False)
    acct_costs = S.cost_table(acct_v["events"], cfg)
    acct_costs.to_csv(OUT / "account_variant_costs.csv", index=False)

    # ---- experiment log (every hypothesis x horizon, in-sample)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log = [{"timestamp_utc": ts, "run_id": "strategy3_" + ts[:10], "segment": "S3_IN_SAMPLE",
            "params": json.dumps({"hypothesis": r["hypothesis"], "horizon": int(r["horizon"])}),
            "sample_start": str(EVENT_START), "sample_end": str(IS_END), "direction": r["direction"],
            "bucket": r["group"], "horizon": int(r["horizon"]), "trade_count": int(r["n"]),
            "mean_net_return": r["vs_random"], "notes": "vs matched random; " + ("SIGNIFICANT" if r["significant"] else "")}
           for _, r in is_tab.iterrows()]
    append_experiment_log(log)

    write_report(ev, full_tab, is_tab, frozen, studied, verdict, acct, acct_note, n_hyp, n_tests, spy, acct_costs)
    print(f"\nVERDICT: {verdict}")
    print(f"Report: {REPORT}\nDataset: {OUT / 'abnormal_events.csv'}")
    return 0


def write_report(ev, full_tab, is_tab, frozen, studied, verdict, acct, acct_note, n_hyp, n_tests, spy, acct_costs):
    L = ["# Strategy 3: Abnormal repricing — continuation or reversal?", "",
         f"## VERDICT: **{verdict}**", "",
         "*Backtest only. No live trading. Not a recommendation to trade real money.*", "",
         "## Setup", "",
         f"- Events: every day with |daily return| >= 4% AND relative volume >= 2.0x (volume vs the previous "
         f"20 days, event day excluded), {EVENT_START} .. {EVENT_END}.",
         "- Universe: the existing 93 large US stocks. **SURVIVORSHIP BIAS:** these are today's large, successful "
         "companies, so historical results are likely biased upward. No historically valid broader universe is "
         "available in the current data infrastructure without new data; none was used.",
         "- Prices: Moomoo forward-adjusted daily bars (share counts in the $ tests use real unadjusted prices).",
         "- Timing: signal known at Day T close; entry at T+1 OPEN; forward_h = Close[T+h] / Open[T+1] - 1.",
         "- In-sample (research): 2019-2022. Out-of-sample (untouched validation): 2023 - Sep 2024.",
         f"- Random baseline: for every event, {S.N_RANDOM} random NON-event days of the same stock, same year, "
         f"same SPY regime (seed {S.SEED}); same entry/exit rule.",
         f"- Hypotheses tested: **{n_hyp} event sets x {len(HORIZONS)} horizons = {n_tests} tests.** "
         f"At a 5% false-positive rate about {n_tests * 0.05:.0f} would look 'significant' by chance alone.",
         "- Selection rule, fixed before results: in-sample n >= 30 and the 95% CI of (continuation vs matched "
         "random) excludes 0; the strongest horizon per hypothesis is frozen and tested once out-of-sample.", ""]

    def base(name):
        t = full_tab[full_tab["hypothesis"] == name]
        return md_table(t, [("horizon", "h (days)", "int"), ("n", "n", "int"), ("mean", "mean", "pct"),
                            ("median", "median", "pct"), ("win_rate", "win%", "pct"), ("std", "std", "pct"),
                            (("ci_low", "ci_high"), "95% CI (mean)", "ci"), ("spy_mean", "SPY same window", "pct"),
                            ("excess_vs_spy", "excess vs SPY", "pct"), ("random_mean", "random entries", "pct"),
                            ("vs_random", "continuation vs random", "pct"),
                            (("vs_random_ci_low", "vs_random_ci_high"), "95% CI", "ci")])
    npos, nneg = (ev["event_direction"] == "POSITIVE_SHOCK").sum(), (ev["event_direction"] == "NEGATIVE_SHOCK").sum()
    L += ["## 1. Events", "",
          f"**{len(ev)} abnormal events** ({npos} positive shocks, {nneg} negative shocks) across "
          f"{ev['ticker'].nunique()} stocks. In-sample {int((ev['sample'] == 'IN_SAMPLE').sum())}, out-of-sample "
          f"{int((ev['sample'] == 'OUT_OF_SAMPLE').sum())}.", "",
          "Event types: " + ", ".join(f"{k} {v}" for k, v in ev["event_type"].value_counts().items()), "",
          "## 2. Positive shocks: raw forward returns (full period, entry next open)", "",
          "'mean' > 0 = price kept rising. 'continuation vs random' > 0 = rose MORE than the same stock did on "
          "comparable random days.", ""] + base("POS baseline") + [
          "", "## 3. Negative shocks: raw forward returns (full period)", "",
          "'mean' < 0 = price kept falling. 'continuation vs random' > 0 = fell MORE than on comparable random "
          "days (continuation); < 0 = rebounded relative to random (reversal).", ""] + base("NEG baseline")

    L += ["", "## 4. Predefined subgroups and combinations (IN-SAMPLE 2019-2022, continuation vs random)", "",
          "Each cell: mean continuation vs matched random entries; * = 95% CI excludes 0 (n >= 30). "
          "Positive = continuation, negative = reversal.", ""]
    piv = is_tab.assign(cell=[("n/a" if v != v else f"{v * 100:+.2f}%" + ("*" if s else "")) for v, s in
                              zip(is_tab["vs_random"], is_tab["significant"])])
    order = [H["name"] for H in S.hypotheses()]
    wide = piv.pivot(index="hypothesis", columns="horizon", values="cell").reindex(order)
    nn = is_tab[is_tab["horizon"] == 1].set_index("hypothesis")["n"].reindex(order)
    L += ["| hypothesis | n | " + " | ".join(f"{h}d" for h in HORIZONS) + " |", "|" + "---|" * (len(HORIZONS) + 2)]
    for hname, r in wide.iterrows():
        L.append(f"| {hname} | {int(nn[hname])} | " + " | ".join(r[h] for h in HORIZONS) + " |")

    L += ["", "## 5. Frozen in-sample selection", ""]
    if frozen.empty:
        L += ["**No hypothesis passed the pre-registered in-sample rule.** Nothing was frozen.", ""]
    else:
        L += md_table(frozen, [("hypothesis", "hypothesis", "str"), ("horizon", "h", "int"), ("n", "n", "int"),
                               ("effect", "effect", "str"), ("vs_random", "IS vs random", "pct"),
                               (("vs_random_ci_low", "vs_random_ci_high"), "95% CI", "ci"),
                               ("vs_random_t", "t", "f2"), ("long_tradable", "long-tradable", "str")])
        L += ["", f"{len(frozen)} of {n_hyp} hypotheses selected (with ~{n_tests * 0.05:.0f} chance false "
                  f"positives expected across {n_tests} tests).", ""]

    L += ["## 6. Out-of-sample, robustness, costs (frozen variants + both baselines)", ""]
    for v in studied:
        s_is, s_oos, rb, si = v["is"], v["oos"], v["robust"], v["stock"]
        tag = "FROZEN (passed in-sample rule)" if v["selected"] else "not selected (strongest in-sample horizon shown for reference)"
        L += [f"### {v['name']} — {v['effect']} at {v['horizon']}d — {tag}", "",
              f"- Trade = {'long' if v['long_tradable'] else 'SHORT (research only, not executable in the $300 account)'}; "
              f"return per event = {'+' if v['long_tradable'] else '-'}forward return.",
              f"- In-sample vs random: n={s_is['n']}, mean {pct(s_is.get('mean'))} "
              f"[{pct(s_is.get('mean_ci95_low'))} .. {pct(s_is.get('mean_ci95_high'))}]",
              f"- **Out-of-sample 2023-Sep 2024 vs random: n={s_oos['n']}, mean {pct(s_oos.get('mean'))} "
              f"[{pct(s_oos.get('mean_ci95_low'))} .. {pct(s_oos.get('mean_ci95_high'))}] -> "
              f"{'SURVIVES' if v['oos_survives'] else ('same sign, NOT significant' if v['oos_same_sign'] else 'DIES (sign flips or zero)')}**",
              f"- OOS trade return {pct(v['oos_trade'].get('mean'))} per event; vs SPY same windows {pct(v['oos_spy_excess'])}",
              f"- Best ticker {si['best_ticker']} = {pct(si['best_share_of_sum'], 0)} of summed returns; top 3 "
              f"{si['top3']} = {pct(si['top3_share'], 0)}; top 5 = {pct(si['top5_share'], 0)}. Without "
              f"{si['best_ticker']}: n={si['without_best_n']}, mean {pct(si['without_best_mean'])} "
              f"[{pct(si['without_best_ci'][0])} .. {pct(si['without_best_ci'][1])}]",
              f"- Years with positive mean trade return: {rb['years_positive']} of {rb['years_total']}", ""]
        L += md_table(v["outliers"], [("variant", "outlier test (full period)", "str"), ("n", "n", "int"),
                                      ("mean", "mean trade return", "pct"), (("ci_low", "ci_high"), "95% CI", "ci"),
                                      ("win_rate", "win%", "pct")])
        flag = v["outliers"].set_index("variant").loc["excl. best 5", "mean"] <= 0 < v["outliers"].iloc[0]["mean"]
        if flag:
            L += ["", "**FLAG: OUTLIER DEPENDENCE** (positive overall, not positive without the best 5 events)."]
        L += [""] + md_table(v["years"], [("year", "year", "int"), ("n", "n", "int"),
                                          ("mean_trade_ret", "mean trade return", "pct"),
                                          ("mean_vs_random", "vs random", "pct"), ("win_rate", "win%", "pct")])
        if v["costs"] is not None:
            L += ["", "Costs (long, round trip):", ""] + md_table(
                v["costs"], [("position_usd", "position $", "int"), ("gross_expectancy", "gross", "pct"),
                             ("fees", "fees", "pct"), ("slippage_and_reg", "slippage+reg", "pct"),
                             ("net_expectancy", "NET", "pct")])
        L += [""]

    L += ["## 7. $300 test (and larger accounts, same rules)", "", f"Strategy simulated: {acct_note}. "
          "Whole shares at real prices (fractional shares were rejected by Moomoo's paper API), no leverage, "
          "max 50% of equity per position, fees $0.99 per order + slippage.", ""]
    L += md_table(acct, [("start_cash", "start $", "int"), ("signals", "signals", "int"), ("executed", "trades", "int"),
                         ("skipped", "skipped", "int"), ("fees_usd", "fees $", "f2"), ("avg_idle_cash_pct", "avg idle cash", "pct"),
                         ("ending_capital", "ending $", "f2"), ("total_return", "total", "pct"), ("cagr", "CAGR", "pct"),
                         ("max_drawdown", "max DD", "pct"), ("spy_total_return", "SPY same period", "pct")])
    L += ["", f"Period: {acct['period'].iloc[0]}.", "", "Per-trade costs for this simulated (long) variant, full period:", ""]
    L += md_table(acct_costs, [("position_usd", "position $", "int"), ("gross_expectancy", "gross", "pct"),
                               ("fees", "fees", "pct"), ("slippage_and_reg", "slippage+reg", "pct"),
                               ("net_expectancy", "NET", "pct")]) + [""]

    # ---- the 16 answers (numbers filled in; wording is mechanical)
    pb = full_tab[full_tab["hypothesis"] == "POS baseline"].set_index("horizon")
    nb = full_tab[full_tab["hypothesis"] == "NEG baseline"].set_index("horizon")

    def dir_text(tb, pos):
        parts = []
        for h in HORIZONS:
            m, vr = tb.loc[h, "mean"], tb.loc[h, "vs_random"]
            parts.append(f"{h}d {pct(m)} (vs random {pct(vr)}{'*' if tb.loc[h, 'significant'] else ''})")
        return "; ".join(parts)

    def grp_answer(group):
        g = is_tab[(is_tab["group"] == group) & is_tab["significant"]]
        if g.empty:
            return "No subgroup in this group passed the in-sample rule at any horizon."
        return "In-sample significant: " + "; ".join(f"{r.hypothesis} {int(r.horizon)}d {pct(r.vs_random)}"
                                                     for r in g.itertuples())
    base_v = {v["name"]: v for v in studied}
    L += ["## 8. Answers", "",
          f"1. **Events:** {len(ev)} ({npos} positive, {nneg} negative).",
          f"2. **Positive shocks** (mean raw forward return; vs matched random, * = CI excludes 0): {dir_text(pb, True)}.",
          f"3. **Negative shocks:** {dir_text(nb, False)}. (vs random > 0 = kept falling more than normal; < 0 = rebounded.)",
          "4. **Strongest horizon:** " + "; ".join(
              f"{n}: {base_v[n]['horizon']}d ({base_v[n]['effect'].lower()})" for n in ("POS baseline", "NEG baseline")) + ".",
          f"5. **Abnormal volume:** {grp_answer('A volume')}",
          f"6. **Gap behaviour:** {grp_answer('C gap')}",
          f"7. **Prior relative strength:** {grp_answer('F rel. strength')}",
          f"8. **Trend alignment:** {grp_answer('D trend')}",
          f"9. **Market regime:** {grp_answer('E regime')}",
          f"10. **vs random entries:** {int(is_tab['significant'].sum())} of {n_tests} in-sample tests significant "
          f"(~{n_tests * 0.05:.0f} expected by chance); frozen hypotheses: {len(frozen)}.",
          "11. **vs SPY (same windows):** see 'excess vs SPY' columns in sections 2-3 and each variant's OOS line.",
          "12. **Out-of-sample 2023-Sep 2024:** " + ("; ".join(
              f"{v['name']}: {'SURVIVES' if v['oos_survives'] else ('same sign, not significant' if v['oos_same_sign'] else 'DIES')}"
              for v in studied if v["selected"]) or "nothing was frozen, so nothing to validate") + ".",
          "13. **Without best events:** " + "; ".join(
              f"{v['name']} excl. best 5 = {pct(v['outliers'].set_index('variant').loc['excl. best 5', 'mean'])}"
              for v in studied) + ".",
          "14. **Without best ticker:** " + "; ".join(
              f"{v['name']} without {v['stock']['best_ticker']} = {pct(v['stock']['without_best_mean'])}" for v in studied) + ".",
          "15. **After costs:** " + ("; ".join(
              f"{v['name']} net at $150 {pct(v['costs'].iloc[0]['net_expectancy'])}, at $1,000 "
              f"{pct(v['costs'].set_index('position_usd').loc[1000, 'net_expectancy'])}"
              for v in studied if v["costs"] is not None) or "no long-tradable variant passed; for the long "
              f"fallback ({acct_note.split(': ')[-1]}): gross {pct(acct_costs.iloc[0]['gross_expectancy'])}, net "
              + ", ".join(f"${int(r.position_usd)} {pct(r.net_expectancy)}" for r in acct_costs.itertuples())) + ".",
          f"16. **$300 enough?** $300 -> ${acct.iloc[0]['ending_capital']:,.2f} ({pct(acct.iloc[0]['total_return'])}) "
          f"vs SPY {pct(acct.iloc[0]['spy_total_return'])}; {int(acct.iloc[0]['skipped'])} of "
          f"{int(acct.iloc[0]['signals'])} signals skipped; fees ${acct.iloc[0]['fees_usd']:,.2f}.", "",
          "## Limitations", "",
          "- Survivorship bias (today's 93 large stocks); in-sample tuning risk; overlapping events of the same stock "
          "are not independent (the bootstrap clusters by date only, so CIs are, if anything, too narrow).",
          "- Short-side results (negative continuation / positive reversal) are research only: borrow costs, "
          "short availability and margin are not modelled.",
          "- Daily bars only: no intraday execution realism beyond next-open entry and 10 bps slippage per side.", "",
          "Files: outputs/strategy3/ (abnormal_events.csv, hypotheses_*.csv, frozen_in_sample_selection.csv, "
          "account_simulation.csv)."]
    REPORT.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
