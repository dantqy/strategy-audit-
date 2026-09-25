"""Event-study backtest of the earnings-reaction hypothesis + a $300 account simulation.

Timing contract (NO LOOKAHEAD)
------------------------------
* Reaction session R = first regular session that can reflect the announcement
  (AFTER-close on day D -> R = next session; BEFORE-open on D -> R = D).
* The signal uses only bars <= R and is confirmed at R's CLOSE.
* Earliest entry = OPEN of the next trading session after R (E = R+1).
* Exit for horizon h (backtest.exit_at):
    close     = CLOSE of the h-th session starting at E (h=1: E's close)
    next_open = OPEN of the session AFTER that (h=1: the open after E). This is
                the executable choice from Singapore, where the US close is 04:00-05:00.
* Rolling highs/averages exclude the current bar (see indicators.py).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from scanner import market_calendar as mc
from scanner.earnings import reaction_metrics
from scanner.indicators import add_daily_indicators
from scanner.signals import BEARISH, BULLISH, NEUTRAL, directional_confirmation

log = logging.getLogger(__name__)

BUCKETS = ["earnings_only", "earnings_volume", "earnings_breakout", "earnings_volume_breakout"]


@dataclass
class Params:
    min_abs_move_pct: float = 5.0
    min_relative_volume: float = 2.0
    breakout_lookback: int = 20

    @classmethod
    def from_cfg(cls, cfg: dict) -> "Params":
        e = cfg["signals"]["earnings_drift"]
        return cls(e["min_abs_move_pct"], e["min_relative_volume"],
                   cfg["signals"]["momentum_breakout"]["lookbacks"][0])


def exit_rule(cfg: dict) -> tuple[int, str]:
    """(extra sessions after the h-th session, price field) for backtest.exit_at."""
    mode = cfg["backtest"].get("exit_at", "close")
    if mode == "close":
        return 0, "close"
    if mode == "next_open":
        return 1, "open"
    raise ValueError(f"backtest.exit_at must be 'close' or 'next_open', not {mode!r}")


def cost_adjusted(gross: float, cfg: dict) -> float:
    """Net return of a long round trip at the configured notional."""
    c = cfg["costs"]
    slip = c["slippage_bps"] / 1e4
    notional = cfg["backtest"]["trade_notional_for_costs"]
    fixed = 2 * (c["commission_per_order"] + c["platform_fee_per_order"]) / notional
    reg = c["regulatory_fee_bps_on_sells"] / 1e4
    return (1 + gross) * (1 - slip) / (1 + slip) - 1 - fixed - reg


def build_reaction_table(prices: dict[str, pd.DataFrame], events: pd.DataFrame, cfg: dict,
                         spy: pd.DataFrame | None = None) -> tuple[pd.DataFrame, dict]:
    """One row per earnings event with a measurable reaction (NO thresholds applied yet).

    Threshold-independent, so parameter sensitivity runs re-filter this table
    instead of recomputing. Returns (table, skip_counts).
    """
    horizons = cfg["backtest"]["horizons"]
    policy = cfg["earnings"].get("unknown_timing_policy", "exclude")
    mb = cfg["signals"]["momentum_breakout"]
    lookbacks = sorted(set(mb["lookbacks"]) | set(cfg["backtest"]["sensitivity"]["breakout_lookback"]))
    cfg_ind = {**cfg, "signals": {**cfg["signals"], "momentum_breakout": {**mb, "lookbacks": lookbacks}}}
    ind_cache = {c: add_daily_indicators(df, cfg_ind) for c, df in prices.items() if len(df)}
    skips = {"unknown_timing": 0, "no_price_data": 0, "reaction_bar_missing_or_short_history": 0,
             "entry_bar_missing": 0, "exit_bar_missing": 0}
    partial = {h: 0 for h in horizons}   # events kept but with no exit bar for horizon h (recent / gap)
    off, exit_field = exit_rule(cfg)
    rows = []
    for _, ev in events.iterrows():
        code, R = ev["code"], ev["reaction_session"]
        if R is None or (isinstance(R, float) and math.isnan(R)) or pd.isna(R):
            if ev["timing"] == "UNKNOWN" and policy == "exclude":
                skips["unknown_timing"] += 1
                continue
            R = mc.session_for_event(ev["event_date"], "AFTER" if ev["timing"] == "UNKNOWN" else ev["timing"])
        ind = ind_cache.get(code)
        if ind is None:
            skips["no_price_data"] += 1
            continue
        m = reaction_metrics(ind, R, cfg["signals"]["volume_anomaly"]["avg_volume_window"])
        if m is None:
            skips["reaction_bar_missing_or_short_history"] += 1
            continue
        i = ind.index.get_loc(R)
        E = mc.next_trading_day(R)
        if i + 1 >= len(ind) or ind.index[i + 1] != E:
            skips["entry_bar_missing"] += 1
            continue
        # hard lookahead guards
        assert E > R, "entry must be strictly after the reaction session"
        assert E > ev["event_date"], "entry must be after the announcement date"
        row_R = ind.iloc[i]
        direction = BULLISH if m["reaction_move_pct"] > 0 else BEARISH
        conf = directional_confirmation(row_R)
        rsi_R = row_R["rsi"]
        rec = {"code": code, "event_date": ev["event_date"], "timing": ev["timing"], "reaction_session": R,
               "entry_date": E, "direction": direction,
               "reaction_move_pct": m["reaction_move_pct"], "gap_pct": m["gap_pct"],
               "relative_volume": m["relative_volume"], "rsi_14": rsi_R,
               "price_confirmation": conf, "entry_open": float(ind.iloc[i + 1]["open"])}
        for n in lookbacks:
            ph, pl = row_R.get(f"prior_high_{n}"), row_R.get(f"prior_low_{n}")
            if direction == BULLISH:
                rec[f"breakout_{n}"] = bool(pd.notna(ph) and row_R["close"] > ph and not (pd.notna(rsi_R) and rsi_R >= mb["rsi_max_bullish"]))
            else:
                rec[f"breakout_{n}"] = bool(pd.notna(pl) and row_R["close"] < pl and not (pd.notna(rsi_R) and rsi_R <= 100 - mb["rsi_max_bullish"]))
        n_ok = 0
        for h in horizons:
            j = i + h + off
            expected = E
            for _ in range(h - 1 + off):
                expected = mc.next_trading_day(expected)
            if j >= len(ind) or ind.index[j] != expected:
                # too recent, or a data gap inside the holding window: this horizon only is unmeasurable
                rec[f"exit_date_{h}"] = None
                rec[f"ret_{h}"] = rec[f"net_ret_{h}"] = rec[f"spy_ret_{h}"] = np.nan
                partial[h] += 1
                continue
            n_ok += 1
            exit_px = float(ind.iloc[j][exit_field])
            g = exit_px / rec["entry_open"] - 1
            rec[f"exit_date_{h}"] = ind.index[j]
            rec[f"ret_{h}"] = g
            rec[f"net_ret_{h}"] = cost_adjusted(g, cfg)
            if spy is not None and E in spy.index and ind.index[j] in spy.index:
                rec[f"spy_ret_{h}"] = float(spy.loc[ind.index[j], exit_field] / spy.loc[E, "open"] - 1)
            else:
                rec[f"spy_ret_{h}"] = np.nan
        if not n_ok:
            skips["exit_bar_missing"] += 1
            continue
        rows.append(rec)
    t = pd.DataFrame(rows)
    if len(t):
        t = t.sort_values(["reaction_session", "code"]).reset_index(drop=True)
        dup = t.duplicated(["code", "reaction_session"]).sum()
        if dup:
            log.warning("dropping %d duplicate (code, reaction_session) events", dup)
            t = t.drop_duplicates(["code", "reaction_session"])
    skips["kept_but_horizon_unmeasurable"] = {h: n for h, n in partial.items() if n}
    return t, skips


def apply_params(table: pd.DataFrame, p: Params, cfg: dict) -> pd.DataFrame:
    """Filter the reaction table with thresholds and assign signal buckets."""
    if table.empty:
        return table.assign(qualified=[], volume_anomaly=[], breakout=[], bucket=[])
    va_thr = cfg["signals"]["volume_anomaly"]["min_relative_volume"]
    t = table.copy()
    t["qualified"] = (t["reaction_move_pct"].abs() >= p.min_abs_move_pct) & (t["relative_volume"] >= p.min_relative_volume)
    # VOLUME_ANOMALY on the reaction bar: rvol threshold AND price confirmation in the move's direction
    t["volume_anomaly"] = (t["relative_volume"] >= va_thr) & (t["price_confirmation"] == t["direction"])
    bcol = f"breakout_{p.breakout_lookback}"
    t["breakout"] = t[bcol] & (t["relative_volume"] >= cfg["signals"]["momentum_breakout"]["min_relative_volume"])
    t = t[t["qualified"]].copy()
    t["bucket"] = np.select(
        [t["volume_anomaly"] & t["breakout"], t["volume_anomaly"], t["breakout"]],
        ["earnings_volume_breakout", "earnings_volume", "earnings_breakout"], default="earnings_only")
    return t


def unconditional_baseline(prices: dict[str, pd.DataFrame], horizons: list[int],
                           start=None, end=None, exit_at: str = "close") -> dict:
    """Mean next-open -> exit return over ALL stock-days in the universe (same exit rule as the signals)."""
    out = {}
    for h in horizons:
        vals = []
        for df in prices.values():
            if len(df) < h + 2:
                continue
            d = df.loc[start:end] if start is not None else df
            x = d["close"].shift(-h) if exit_at == "close" else d["open"].shift(-(h + 1))
            r = x / d["open"].shift(-1) - 1
            vals.append(r.dropna().values)
        allv = np.concatenate(vals) if vals else np.array([])
        out[h] = {"n": int(allv.size), "mean": float(allv.mean()) if allv.size else float("nan"),
                  "median": float(np.median(allv)) if allv.size else float("nan"),
                  "win_rate": float((allv > 0).mean()) if allv.size else float("nan")}
    return out


def simulate_account(signals: pd.DataFrame, prices: dict[str, pd.DataFrame], cfg: dict,
                     horizon: int, with_costs: bool = True,
                     raw_prices: dict[str, pd.DataFrame] | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """$300 long-only account: BULLISH qualified signals, whole shares, no leverage.

    Entry at E open; exit per backtest.exit_at (close of the horizon-th session,
    or the next open). Entries on a day are sized before that day's exits are
    booked, so exit cash is never recycled into entries at the same open.

    Share counts: `prices` are forward-adjusted, which rewrites pre-split history
    (CMG traded at $3,214 on 2024-06-25; adjusted history shows ~$64). With
    `raw_prices` (as-traded bars) the number of whole shares is decided at the
    REAL entry price; the position is then valued with adjusted prices so splits
    are not fake moves. Without raw prices, sizing falls back to adjusted prices
    and the result overstates what a small account could buy.
    """
    a, c = cfg["account"], cfg["costs"]
    _, exit_field = exit_rule(cfg)
    slip = c["slippage_bps"] / 1e4 if with_costs else 0.0
    fee = (c["commission_per_order"] + c["platform_fee_per_order"]) if with_costs else 0.0
    reg = c["regulatory_fee_bps_on_sells"] / 1e4 if with_costs else 0.0
    frac = bool(a.get("fractional_shares_confirmed"))
    cash, open_pos, trades = float(a["starting_cash"]), [], []
    not_exec = too_expensive = no_cash = sized_real = sized_adjusted = 0
    longs = signals[(signals["direction"] == BULLISH) & signals[f"exit_date_{horizon}"].notna()].copy()
    if longs.empty:
        return pd.DataFrame(columns=["equity"]), pd.DataFrame(), {"not_executable": 0, "sizing": "n/a"}
    longs["abs_move"] = longs["reaction_move_pct"].abs()
    by_entry = {d: g.sort_values("abs_move", ascending=False) for d, g in longs.groupby("entry_date")}
    first = min(by_entry)
    last = max(longs[f"exit_date_{horizon}"])
    days = mc.trading_days(first, last)
    curve = []
    last_close: dict[str, float] = {}
    for d in days:
        equity_open = cash + sum(p["qty"] * last_close.get(p["code"], p["entry"]) for p in open_pos)
        for _, s in by_entry.get(d, pd.DataFrame()).iterrows():
            px = s["entry_open"] * (1 + slip)                     # adjusted units
            raw = raw_prices.get(s["code"]) if raw_prices else None
            real_open = float(raw.loc[d, "open"]) if raw is not None and d in raw.index else None
            real_px = real_open * (1 + slip) if real_open else px
            target = equity_open * float(a["max_position_pct"])
            budget = min(cash - fee, target)
            shares = (math.floor(budget / real_px * 1000) / 1000) if frac else math.floor(budget / real_px)
            if shares <= 0:
                not_exec += 1
                if real_px > target:
                    too_expensive += 1
                else:
                    no_cash += 1
                continue
            if real_open:
                sized_real += 1
            else:
                sized_adjusted += 1
            cost = shares * real_px
            qty = cost / px                                       # adjusted-unit quantity with the same value
            cash -= cost + fee
            open_pos.append({"code": s["code"], "qty": qty, "shares": shares, "entry": px,
                             "entry_real": real_px, "entry_date": d,
                             "exit_date": s[f"exit_date_{horizon}"], "signal_bucket": s.get("bucket", "")})
        for p in open_pos:
            df = prices[p["code"]]
            if d in df.index:
                last_close[p["code"]] = float(df.loc[d, "close"])
        still = []
        for p in open_pos:
            if p["exit_date"] == d:
                px = prices[p["code"]].loc[d, exit_field] * (1 - slip)
                proceeds = p["qty"] * px
                cash += proceeds - fee - proceeds * reg
                trades.append({**p, "exit": px, "pnl": proceeds - fee - proceeds * reg - p["qty"] * p["entry"] - fee,
                               "ret": px / p["entry"] - 1})
            else:
                still.append(p)
        open_pos = still
        eq = cash + sum(p["qty"] * last_close.get(p["code"], p["entry"]) for p in open_pos)
        curve.append({"date": d, "equity": eq, "cash": cash, "open_positions": len(open_pos)})
    sizing = ("real (unadjusted) prices" if sized_real and not sized_adjusted else
              "ADJUSTED prices (overstates affordability)" if sized_adjusted and not sized_real else
              f"real prices for {sized_real}, adjusted for {sized_adjusted}")
    return pd.DataFrame(curve).set_index("date"), pd.DataFrame(trades), {
        "not_executable": not_exec, "skipped_share_too_expensive": too_expensive,
        "skipped_no_free_cash": no_cash, "sizing": sizing}
