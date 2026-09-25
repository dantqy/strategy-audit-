"""Time-based split, out-of-sample lock, sensitivity grid and experiment log."""
from __future__ import annotations

import csv
import hashlib
import itertools
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from scanner.config import resolve
from scanner.signals import BEARISH, BULLISH

from .engine import BUCKETS, Params, apply_params
from .metrics import trade_stats

EXPERIMENT_LOG = "outputs/backtests/experiment_log.csv"
OOS_LOG = "outputs/backtests/oos_access_log.csv"
LOG_FIELDS = ["timestamp_utc", "run_id", "segment", "params", "sample_start", "sample_end",
              "direction", "bucket", "horizon", "trade_count", "mean_net_return", "median_net_return",
              "win_rate_net", "sum_net_return", "portfolio_sharpe", "portfolio_max_drawdown", "notes"]


def time_split(table: pd.DataFrame, in_sample_fraction: float,
               col: str = "reaction_session") -> tuple[pd.DataFrame, pd.DataFrame, object]:
    """Chronological split on `col`. Never shuffles. Same-day events stay together."""
    if table.empty:
        return table, table, None
    dates = np.array(sorted(table[col].unique()))
    cut = dates[min(len(dates) - 1, int(len(dates) * in_sample_fraction))]
    return table[table[col] < cut], table[table[col] >= cut], cut


def params_hash(cfg: dict, p: Params) -> str:
    blob = json.dumps({"params": p.__dict__, "signals": cfg["signals"], "costs": cfg["costs"],
                       "backtest": {k: v for k, v in cfg["backtest"].items() if k != "sensitivity"}},
                      sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def record_oos_access(cfg: dict, p: Params, run_id: str) -> list[str]:
    """Log every OOS evaluation. Warn if OOS was already viewed under other settings."""
    path = resolve(OOS_LOG)
    path.parent.mkdir(parents=True, exist_ok=True)
    h = params_hash(cfg, p)
    prior = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["timestamp_utc", "run_id", "config_hash"])
    warnings = []
    if len(prior):
        others = prior[prior["config_hash"] != h]
        warnings.append(f"Out-of-sample has been evaluated {len(prior)} time(s) before.")
        if len(others):
            warnings.append("WARNING: OOS was previously evaluated with DIFFERENT settings. "
                            "Treat this OOS result as contaminated (parameters may be tuned to it).")
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if not len(prior):
            w.writerow(["timestamp_utc", "run_id", "config_hash"])
        w.writerow([datetime.now(timezone.utc).isoformat(timespec="seconds"), run_id, h])
    return warnings


def append_experiment_log(rows: list[dict]) -> None:
    path = resolve(EXPERIMENT_LOG)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        for r in rows:
            w.writerow(r)


def segment_report(sig: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Long-form table: direction x bucket x horizon x measure."""
    bt = cfg["backtest"]
    out = []
    if sig.empty:
        return pd.DataFrame()
    for direction in (BULLISH, BEARISH, "ALL"):
        dsub = sig if direction == "ALL" else sig[sig["direction"] == direction]
        for bucket in ["all_qualified"] + BUCKETS:
            sub = dsub if bucket == "all_qualified" else dsub[dsub["bucket"] == bucket]
            for h in bt["horizons"]:
                sign = np.where(sub["direction"] == BULLISH, 1.0, -1.0) if len(sub) else np.array([])
                measures = {
                    "long_gross": sub.get(f"ret_{h}", pd.Series(dtype=float)),
                    "long_net": sub.get(f"net_ret_{h}", pd.Series(dtype=float)),
                    # continuation = return in the direction of the reaction (BEARISH => short; research only)
                    "continuation_gross": sub[f"ret_{h}"] * sign if len(sub) else pd.Series(dtype=float),
                }
                for mname, series in measures.items():
                    st = trade_stats(series, sub["reaction_session"] if len(sub) else None,
                                     bt["min_sample_size"], bt["bootstrap_resamples"], bt["random_seed"])
                    out.append({"direction": direction, "bucket": bucket, "horizon": h, "measure": mname, **st})
    return pd.DataFrame(out)


def sensitivity_grid(table_is: pd.DataFrame, cfg: dict, run_id: str) -> pd.DataFrame:
    """Neighbouring-parameter grid on IN-SAMPLE data only. Every cell is logged."""
    g, bt = cfg["backtest"]["sensitivity"], cfg["backtest"]
    h = bt["primary_horizon"]
    rows, logrows = [], []
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for mv, rv, lb in itertools.product(g["min_abs_move_pct"], g["min_relative_volume"], g["breakout_lookback"]):
        p = Params(mv, rv, lb)
        sig = apply_params(table_is, p, cfg)
        for direction in (BULLISH, BEARISH):
            for bucket in ("all_qualified", "earnings_breakout", "earnings_volume_breakout"):
                sub = sig[sig["direction"] == direction] if len(sig) else sig
                if bucket != "all_qualified" and len(sub):
                    sub = sub[sub["bucket"] == bucket]
                st = trade_stats(sub[f"net_ret_{h}"] if len(sub) else [], sub["reaction_session"] if len(sub) else None,
                                 bt["min_sample_size"], 500, bt["random_seed"])
                row = {"min_abs_move_pct": mv, "min_relative_volume": rv, "breakout_lookback": lb,
                       "direction": direction, "bucket": bucket, "horizon": h, **st}
                rows.append(row)
                logrows.append({"timestamp_utc": ts, "run_id": run_id, "segment": "IN_SAMPLE_SENSITIVITY",
                                "params": json.dumps(p.__dict__),
                                "sample_start": str(table_is["reaction_session"].min()) if len(table_is) else "",
                                "sample_end": str(table_is["reaction_session"].max()) if len(table_is) else "",
                                "direction": direction, "bucket": bucket, "horizon": h,
                                "trade_count": st["n"], "mean_net_return": st.get("mean"),
                                "median_net_return": st.get("median"), "win_rate_net": st.get("win_rate"),
                                "sum_net_return": st.get("sum_of_returns"), "notes": "grid cell"})
    append_experiment_log(logrows)
    return pd.DataFrame(rows)


def stability_summary(grid: pd.DataFrame) -> list[str]:
    lines = []
    if grid.empty:
        return ["No sensitivity results."]
    for (direction, bucket), g in grid.groupby(["direction", "bucket"]):
        g = g[g["n"] > 0]
        if g.empty:
            continue
        pos = (g["mean"] > 0).mean()
        sig = g.get("significant_5pct", pd.Series(False)).fillna(False).mean()
        lines.append(f"{direction:8s} {bucket:26s} cells={len(g):2d} n_range={int(g['n'].min())}-{int(g['n'].max())} "
                     f"mean_net range {g['mean'].min()*100:+.2f}%..{g['mean'].max()*100:+.2f}% | "
                     f"positive in {pos:.0%} of cells | CI excludes 0 in {sig:.0%}")
    return lines
