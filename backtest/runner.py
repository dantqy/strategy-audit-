"""Orchestrates a full backtest run from already-loaded data (no API calls here).
Kept data-source-agnostic so it is unit-testable with synthetic fixtures."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from scanner.signals import BULLISH

from .benchmark import market_exposure_verdict, portfolio_vs_spy, spy_comparison
from .engine import Params, apply_params, build_reaction_table, simulate_account, unconditional_baseline
from .signal_study import build_signal_table, format_study, study_report
from .metrics import equity_stats, skepticism_flags, trade_stats
from .validation import (append_experiment_log, record_oos_access, segment_report, sensitivity_grid,
                         stability_summary, time_split)

log = logging.getLogger(__name__)


def _pct(x):
    return "n/a" if x is None or x != x else f"{x*100:+.2f}%"


def _fmt_seg(rep: pd.DataFrame, measure: str, h: int) -> list[str]:
    if rep.empty:
        return ["(no signals)"]
    r = rep[(rep["measure"] == measure) & (rep["horizon"] == h)]
    lines = [f"| direction | bucket | n | win% | mean | median | 95% CI (cluster bootstrap) | PF | flag |",
             "|---|---|---|---|---|---|---|---|---|"]
    for _, x in r.iterrows():
        if x["n"] == 0:
            continue
        lines.append(f"| {x['direction']} | {x['bucket']} | {x['n']} | {x.get('win_rate', float('nan'))*100:.0f}% | "
                     f"{_pct(x.get('mean'))} | {_pct(x.get('median'))} | {_pct(x.get('mean_ci95_low'))} .. "
                     f"{_pct(x.get('mean_ci95_high'))} | {x.get('profit_factor', float('nan')):.2f} | "
                     f"{'LOW SAMPLE SIZE' if x['low_sample_size'] else ''} |")
    return lines


def _plot_curves(curves: dict, path: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    fig, ax = plt.subplots(figsize=(9, 4.5))
    for label, c in curves.items():
        if c is not None and len(c):
            ax.plot(pd.to_datetime(c.index), c.values, label=label, linewidth=1.4)
    ax.set_title("$300 paper account - long-only BULLISH earnings signals")
    ax.set_ylabel("equity (USD)")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _affordability_lines(sig: pd.DataFrame, raw_prices: dict | None, cfg: dict) -> list[str]:
    """Per-trade BULLISH results split by whether ONE share was affordable at the REAL entry price."""
    if not raw_prices or sig.empty:
        return []
    a = cfg["account"]
    budget = float(a["starting_cash"]) * float(a["max_position_pct"])
    b = sig[sig["direction"] == BULLISH].copy()
    if b.empty:
        return []
    b["real_open"] = [float(raw_prices[c].loc[d, "open"]) if c in raw_prices and d in raw_prices[c].index else float("nan")
                      for c, d in zip(b["code"], b["entry_date"])]
    b = b[b["real_open"].notna()]
    b["affordable"] = b["real_open"] * (1 + cfg["costs"]["slippage_bps"] / 1e4) <= budget
    lines = [f"### BULLISH trades split by real share price at entry (1 share <= ${budget:,.0f} position)", "",
             "| horizon | group | n | mean net | 95% CI | win% |", "|---|---|---|---|---|---|"]
    bt = cfg["backtest"]
    for h in bt["horizons"]:
        for lab, g in (("affordable", b[b["affordable"]]), ("too expensive", b[~b["affordable"]])):
            st = trade_stats(g[f"net_ret_{h}"], g["reaction_session"], bt["min_sample_size"], 1000, bt["random_seed"])
            if not st["n"]:
                continue
            lines.append(f"| {h} | {lab} | {st['n']} | {_pct(st['mean'])} | {_pct(st.get('mean_ci95_low'))} .. "
                         f"{_pct(st.get('mean_ci95_high'))} | {st['win_rate']*100:.0f}% |")
    return lines + [""]


def _segment_block(name: str, table: pd.DataFrame, prices, spy, cfg, p: Params, out: Path, run_id: str,
                   raw_prices: dict | None = None) -> tuple[list[str], list[dict]]:
    bt = cfg["backtest"]
    h = bt["primary_horizon"]
    sig = apply_params(table, p, cfg)
    sig.to_csv(out / f"signals_{name}.csv", index=False)
    rep = segment_report(sig, cfg)
    rep.to_csv(out / f"report_{name}.csv", index=False)
    lines = [f"## {name.upper().replace('_', ' ')}", ""]
    if table.empty:
        return lines + ["No events in this segment."], []
    start, end = table["reaction_session"].min(), table["reaction_session"].max()
    lines.append(f"Sample: {start} .. {end} | measurable earnings reactions: {len(table)} | qualified signals: {len(sig)}")
    lines.append("")
    base = unconditional_baseline(prices, bt["horizons"], start, end, bt.get("exit_at", "close"))
    for hh in bt["horizons"]:
        exit_txt = f"close of session {hh}" if bt.get("exit_at", "close") == "close" else f"open after session {hh}"
        lines += [f"### Horizon {hh} session(s): entry next open -> {exit_txt}", "",
                  "**Long, net of assumed costs**", ""] + _fmt_seg(rep, "long_net", hh)
        lines += ["", "**Long, gross**", ""] + _fmt_seg(rep, "long_gross", hh)
        lines += ["", "**Continuation (BEARISH sign-flipped = short; research only), gross**", ""] + _fmt_seg(rep, "continuation_gross", hh)
        b = base.get(hh, {})
        lines += ["", f"Unconditional baseline (all universe stock-days, same period): n={b.get('n')} "
                      f"mean {_pct(b.get('mean'))}, win {b.get('win_rate', float('nan'))*100:.0f}%", ""]
        cmp = spy_comparison(sig[sig["direction"] == BULLISH], hh) if len(sig) else {}
        if cmp:
            lines.append(f"BULLISH vs SPY (same windows): mean {_pct(cmp.get('mean_trade_ret'))}, "
                         f"SPY {_pct(cmp.get('mean_spy_same_window'))}, excess {_pct(cmp.get('mean_excess_vs_spy'))}, "
                         f"beta {cmp.get('beta_to_spy', float('nan')):.2f}, corr {cmp.get('corr_to_spy', float('nan')):.2f} -> "
                         f"{market_exposure_verdict(cmp)}")
            lines.append("")
    # $300 account simulation
    curves, eqs = {}, {}
    for label, costs in (("gross", False), ("net", True)):
        curve, trades, info = (simulate_account(sig, prices, cfg, h, with_costs=costs, raw_prices=raw_prices)
                               if len(sig) else (pd.DataFrame(), pd.DataFrame(), {"not_executable": 0}))
        if len(curve):
            curve.to_csv(out / f"equity_curve_{name}_{label}.csv")
            trades.to_csv(out / f"account_trades_{name}_{label}.csv", index=False)
            curves[f"{name} {label}"] = curve["equity"]
            eqs[label] = {**equity_stats(curve["equity"]), "trades": len(trades), **info,
                          **portfolio_vs_spy(curve["equity"], spy)}
    _plot_curves(curves, out / f"equity_curve_{name}.png")
    lines += _affordability_lines(sig, raw_prices, cfg)
    lines += ["### $300 account simulation (long-only BULLISH, whole shares, no leverage, "
              f"horizon {h})", "",
              f"Share counts sized with {eqs.get('net', {}).get('sizing', 'n/a')}.", "",
              "| | gross | net |", "|---|---|---|"]
    keys = ["trades", "not_executable", "skipped_share_too_expensive", "skipped_no_free_cash", "total_return",
            "annualized_return", "sharpe", "max_drawdown", "beta_daily", "spy_total_return_same_period"]
    for k in keys:
        def f(v):
            if v is None:
                return "n/a"
            if isinstance(v, float) and k in ("total_return", "annualized_return", "max_drawdown", "spy_total_return_same_period"):
                return _pct(v)
            return f"{v:.2f}" if isinstance(v, float) else str(v)
        lines.append(f"| {k} | {f(eqs.get('gross', {}).get(k))} | {f(eqs.get('net', {}).get(k))} |")
    all_q = rep[(rep["direction"] == "ALL") & (rep["bucket"] == "all_qualified") & (rep["horizon"] == h) & (rep["measure"] == "long_net")]
    ts_main = all_q.iloc[0].to_dict() if len(all_q) else {"n": 0}
    flags = skepticism_flags(ts_main, eqs.get("net"))
    if flags:
        lines += ["", "**Flags:**"] + [f"- {x}" for x in flags]
    logrows = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for direction in ("ALL", BULLISH, "BEARISH"):
        r = rep[(rep["direction"] == direction) & (rep["bucket"] == "all_qualified") & (rep["horizon"] == h) & (rep["measure"] == "long_net")]
        if len(r):
            x = r.iloc[0]
            logrows.append({"timestamp_utc": now, "run_id": run_id, "segment": name.upper(),
                            "params": json.dumps(p.__dict__), "sample_start": str(start), "sample_end": str(end),
                            "direction": direction, "bucket": "all_qualified", "horizon": h,
                            "trade_count": x["n"], "mean_net_return": x.get("mean"), "median_net_return": x.get("median"),
                            "win_rate_net": x.get("win_rate"), "sum_net_return": x.get("sum_of_returns"),
                            "portfolio_sharpe": eqs.get("net", {}).get("sharpe"),
                            "portfolio_max_drawdown": eqs.get("net", {}).get("max_drawdown"),
                            "notes": "; ".join(flags)})
    return lines, logrows


def _standalone_block(name: str, t: pd.DataFrame, cfg: dict, out: Path, run_id: str) -> tuple[list[str], list[dict]]:
    """Report + experiment-log rows for the non-earnings VOLUME_ANOMALY / MOMENTUM_BREAKOUT study."""
    h = cfg["backtest"]["primary_horizon"]
    title = f"## STANDALONE SIGNALS ({name.upper().replace('_', ' ')}): non-earnings days"
    if t.empty:
        return [title, "", "No standalone signals in this segment."], []
    rep = study_report(t, cfg)
    rep.to_csv(out / f"standalone_report_{name}.csv", index=False)
    lines = [title, "",
             f"Sample: {t['signal_date'].min()} .. {t['signal_date'].max()} | signal rows: {len(t)} "
             "(n below is after removing overlapping trades per symbol+signal+horizon)",
             "Long = buy at next open. BEARISH rows therefore measure buying after a bearish signal; "
             "'continuation gross' flips the sign (short; research only). Excess vs SPY is gross.", ""]
    lines += format_study(rep, cfg)
    logrows = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    r = rep[(rep["horizon"] == h) & (rep["measure"] == "long_net")]
    for _, x in r.iterrows():
        logrows.append({"timestamp_utc": now, "run_id": run_id, "segment": f"{name.upper()}_STANDALONE",
                        "params": json.dumps({"signal": x["signal"], **{k: cfg["signals"][k] for k in
                                              ("volume_anomaly", "momentum_breakout")}}),
                        "sample_start": str(t["signal_date"].min()), "sample_end": str(t["signal_date"].max()),
                        "direction": x["direction"], "bucket": x["signal"], "horizon": h,
                        "trade_count": x["n"], "mean_net_return": x.get("mean"), "median_net_return": x.get("median"),
                        "win_rate_net": x.get("win_rate"), "sum_net_return": x.get("sum_of_returns"),
                        "notes": "LOW SAMPLE SIZE" if x["low_sample_size"] else ""})
    return lines, logrows


def run_backtest(prices: dict, spy: pd.DataFrame | None, events: pd.DataFrame, cfg: dict,
                 out_root: str | Path, final_oos: bool = False, notes: str = "",
                 raw_prices: dict | None = None) -> dict:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out, k = Path(out_root) / run_id, 1
    while out.exists():
        k += 1
        out = Path(out_root) / f"{run_id}_{k}"
    run_id = out.name
    out.mkdir(parents=True, exist_ok=True)
    p = Params.from_cfg(cfg)
    table, skips = build_reaction_table(prices, events, cfg, spy)
    table.to_csv(out / "reaction_table_all_events.csv", index=False)
    is_t, oos_t, cut = time_split(table, cfg["backtest"]["in_sample_fraction"])
    lines = [f"# Earnings-reaction backtest {run_id}", "",
             f"Hypothesis: after a >= {p.min_abs_move_pct}% earnings reaction on >= {p.min_relative_volume}x volume, "
             "is there continuation or reversal over the next 1-3 sessions?", "",
             f"Params: {p.__dict__} | events input: {len(events)} | measurable reactions: {len(table)} | skipped: {skips}",
             f"Time split: in-sample < {cut} <= out-of-sample (chronological, not shuffled)",
             "Costs: " + json.dumps(cfg["costs"]),
             f"Exit rule: {cfg['backtest'].get('exit_at', 'close')} "
             "(close = close of the h-th session; next_open = open of the session after it)",
             "", "**Caveats:** universe = today's liquid names (SURVIVORSHIP BIAS: likely flatters results); "
             "earnings timing from provider (errors shift the reaction session); bearish results are research-only "
             "(no shorting in the $300 account).", ""]
    seg_lines, logrows = _segment_block("in_sample", is_t, prices, spy, cfg, p, out, run_id, raw_prices)
    lines += seg_lines
    grid = sensitivity_grid(is_t, cfg, run_id) if len(is_t) else pd.DataFrame()
    grid.to_csv(out / "sensitivity_in_sample.csv", index=False)
    sens_lines = ["# Parameter sensitivity (IN-SAMPLE only)", "",
                  f"Horizon {cfg['backtest']['primary_horizon']}, net of costs. We want a broad stable region, not one magic cell.",
                  "", "```"] + stability_summary(grid) + ["```"]
    (out / "sensitivity_report.md").write_text("\n".join(sens_lines), encoding="utf-8")
    lines += ["", *sens_lines[2:]]
    result = {"run_id": run_id, "out_dir": str(out), "n_events": len(events), "n_reactions": len(table),
              "split_date": str(cut), "skips": skips}

    st_is = st_oos = None
    if cfg["backtest"].get("standalone", {}).get("enabled", True):
        sig_start = date.fromisoformat(cfg["history"]["backtest_start"])
        sig_t, sig_stats = build_signal_table(prices, events, cfg, spy, start=sig_start)
        sig_t.to_csv(out / "standalone_signals_all.csv", index=False)
        if cut is not None:     # same cut date as the earnings study: one OOS period for everything
            if len(sig_t):
                st_is, st_oos = sig_t[sig_t["signal_date"] < cut], sig_t[sig_t["signal_date"] >= cut]
            else:
                st_is, st_oos = sig_t, sig_t
        else:
            st_is, st_oos, _ = time_split(sig_t, cfg["backtest"]["in_sample_fraction"], col="signal_date")
        st_lines, st_log = _standalone_block("in_sample", st_is, cfg, out, run_id)
        st_lines.insert(2, f"Signal days: {sig_stats} | earnings-window exclusion relies on the earnings "
                           "calendar being complete; missed earnings days leak in here.")
        lines += ["", *st_lines]
        logrows += st_log
        result["standalone_stats"] = sig_stats
    if final_oos:
        warns = record_oos_access(cfg, p, run_id)
        oos_lines, oos_log = _segment_block("out_of_sample", oos_t, prices, spy, cfg, p, out, run_id, raw_prices)
        if st_oos is not None:
            so_lines, so_log = _standalone_block("out_of_sample", st_oos, cfg, out, run_id)
            oos_lines += ["", *so_lines]
            oos_log += so_log
        (out / "out_of_sample_report.md").write_text("\n".join(["# OUT-OF-SAMPLE", ""] + [f"> {w}" for w in warns] + oos_lines), encoding="utf-8")
        lines += ["", *[f"> {w}" for w in warns], *oos_lines]
        logrows += oos_log
        result["oos_warnings"] = warns
    else:
        lines += ["", "## OUT OF SAMPLE", "",
                  f"LOCKED. {len(oos_t)} earnings events"
                  + (f" and {len(st_oos)} standalone signal rows" if st_oos is not None else "")
                  + " held out. Run with --final-oos ONCE, after you have finished "
                  "choosing parameters on in-sample data. Every OOS run is logged in outputs/backtests/oos_access_log.csv."]
    for r in logrows:
        r["notes"] = (r.get("notes") or "") + (f" | {notes}" if notes else "")
    append_experiment_log(logrows)
    (out / "in_sample_report.md").write_text("\n".join(seg_lines), encoding="utf-8")
    (out / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    result["summary_path"] = str(out / "summary.md")
    return result
