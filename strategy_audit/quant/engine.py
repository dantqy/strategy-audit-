"""Deterministic, spec-driven daily backtest engine.

Timing contract (enforced here and verified by quant/lookahead.py):
* Indicators on day T use bars <= T only ("prior N-session" highs/lows/volume exclude T itself).
* A signal is observable at T's CLOSE; the earliest fill is the OPEN of the next US session (T+1).
* FIXED_HOLD h: sell at the CLOSE of the h-th session counting the entry day as session 1.
  Stop-loss / take-profit are evaluated on each session's CLOSE and filled at the NEXT OPEN.
* A missing candle anywhere in the entry/holding window voids that trade (never filled).
* One position per stock at a time; a trade belongs to the period of its signal date and must
  complete inside that period.

Two layers of output:
* TRADE LEDGER: every trade the rules generate (capacity-independent) -> robustness statistics.
* PORTFOLIO: equal-weight, max N concurrent positions, fees + slippage -> equity curve metrics.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from scanner import market_calendar as mc
from scanner.indicators import rsi as _rsi

from .. import ENGINE_VERSION, METHODOLOGY_VERSION
from ..schema import Condition, StrategySpec
from .data import MarketDataProvider


# ------------------------------------------------------------------ indicators
def _spy_close(spy: pd.DataFrame) -> pd.Series:
    return spy["close"]


def indicator_series(ind: str, period: int | None, bars: pd.DataFrame, spy: pd.DataFrame) -> pd.Series:
    """Value of a whitelisted indicator at each session's close, on the stock's own dates."""
    c, h, lo, v = bars["close"], bars["high"], bars["low"], bars["volume"]
    p = period
    if ind == "CLOSE":
        return c
    if ind == "RSI":
        return _rsi(c, p)
    if ind == "SMA":
        return c.rolling(p, min_periods=p).mean()
    if ind == "EMA":
        return c.ewm(span=p, adjust=False, min_periods=p).mean()
    if ind == "RETURN":
        return c / c.shift(p) - 1
    if ind == "REL_VOLUME":
        return v / v.shift(1).rolling(p, min_periods=p).mean()
    if ind == "HIGH":
        return h.shift(1).rolling(p, min_periods=p).max()
    if ind == "LOW":
        return lo.shift(1).rolling(p, min_periods=p).min()
    # SPY-based: computed on SPY's OWN sessions, then aligned by exact date (no shifting)
    sc = _spy_close(spy)
    if ind == "SPY_CLOSE":
        return sc.reindex(bars.index)
    if ind == "SPY_SMA":
        return sc.rolling(p, min_periods=p).mean().reindex(bars.index)
    if ind == "SPY_RETURN":
        return (sc / sc.shift(p) - 1).reindex(bars.index)
    if ind == "REL_STRENGTH":
        return (c / c.shift(p) - 1) - (sc / sc.shift(p) - 1).reindex(bars.index)
    raise ValueError(f"unsupported indicator {ind}")


_OPS = {"<": np.less, "<=": np.less_equal, ">": np.greater, ">=": np.greater_equal}


def condition_mask(cond: Condition, bars: pd.DataFrame, spy: pd.DataFrame) -> pd.Series:
    left = indicator_series(cond.indicator, cond.period, bars, spy)
    if cond.value is not None:
        right = pd.Series(cond.value, index=bars.index)
    else:
        right = indicator_series(cond.comparison_indicator, cond.comparison_period, bars, spy) * cond.comparison_multiplier
    ok = left.notna() & right.notna()
    return pd.Series(_OPS[cond.operator](left.to_numpy(float), right.to_numpy(float)), index=bars.index) & ok


def signal_mask(spec: StrategySpec, bars: pd.DataFrame, spy: pd.DataFrame) -> pd.Series:
    m = pd.Series(True, index=bars.index)
    for cond in spec.entry_conditions:
        m &= condition_mask(cond, bars, spy)
    return m


def regime_series(spy: pd.DataFrame) -> pd.Series:
    c = _spy_close(spy)
    sma = c.rolling(200, min_periods=200).mean()
    lab = [None if pd.isna(s) else ("BULL" if cv > s else "BEAR") for cv, s in zip(c.to_numpy(), sma.to_numpy())]
    return pd.Series(lab, index=spy.index, dtype=object)          # None (not NaN) = unknown: NaN != NaN breaks matching


# ---------------------------------------------------------------------- trades
class _Arr:
    """Numpy view of one stock's bars plus trading-day ordinals (to detect missing sessions)."""

    def __init__(self, bars: pd.DataFrame):
        self.dates = list(bars.index)
        cal = {d: k for k, d in enumerate(mc.trading_days(self.dates[0], mc.next_trading_day(self.dates[-1])))}
        self.ordn = np.array([cal[d] for d in self.dates])
        self.o = bars["open"].to_numpy(float)
        self.c = bars["close"].to_numpy(float)
        self.n = len(self.dates)
        self.pos = {d: i for i, d in enumerate(self.dates)}


def simulate_exit(a: _Arr, i_sig: int, exit_rule) -> tuple | None:
    """(entry_date, entry_px, exit_date, exit_px, reason, sessions) or None if any needed bar is missing."""
    e = i_sig + 1
    if e >= a.n or a.ordn[e] != a.ordn[i_sig] + 1:
        return None
    entry = a.o[e]
    last = e + exit_rule.trading_days - 1
    sl, tp = exit_rule.stop_loss_pct, exit_rule.take_profit_pct
    for k in range(e, last + 1):
        if k >= a.n or a.ordn[k] != a.ordn[e] + (k - e):
            return None
        if k == last:
            return a.dates[e], entry, a.dates[k], a.c[k], "HOLD", k - e + 1
        hit = ("STOP" if sl and a.c[k] <= entry * (1 - sl) else
               "TAKE_PROFIT" if tp and a.c[k] >= entry * (1 + tp) else None)
        if hit:
            x = k + 1
            if x >= a.n or a.ordn[x] != a.ordn[k] + 1:
                return None
            return a.dates[e], entry, a.dates[x], a.o[x], hit, x - e + 1
    return None


def net_return(gross: float, costs, notional: float) -> float:
    """Per-trade net return: slippage both sides, fixed fees on buy+sell, regulatory bps on the sell."""
    s = costs.slippage_bps / 1e4
    fixed = 2 * (costs.commission_per_order + costs.platform_fee_per_order) / notional
    return (1 + gross) * (1 - s) / (1 + s) - 1 - fixed - costs.regulatory_fee_bps_on_sells / 1e4


class Market:
    """Everything the engine needs for one universe, computed once and reused by all re-runs."""

    def __init__(self, provider: MarketDataProvider, universe_type: str):
        self.provider = provider
        self.bars = provider.all_bars(universe_type)
        self.spy = provider.benchmark()
        self.arr = {c: _Arr(b) for c, b in self.bars.items() if len(b) > 30}
        self.regime = regime_series(self.spy)
        self.test_start, self.dev_end, self.val_start, self.last = provider.split()

    def period(self, name: str) -> tuple[date, date]:
        return {"development": (self.test_start, self.dev_end), "validation": (self.val_start, self.last),
                "full": (self.test_start, self.last)}[name]


def generate_trades(spec: StrategySpec, mkt: Market, start: date, end: date) -> tuple[pd.DataFrame, dict]:
    """Trade ledger for every signal in [start, end] whose trade completes by `end`."""
    notional = spec.portfolio.starting_capital / spec.portfolio.max_positions
    rows, stats = [], {"signals": 0, "skipped_in_position": 0, "skipped_missing_data": 0, "skipped_incomplete": 0}
    for code in sorted(mkt.arr):
        a, b = mkt.arr[code], mkt.bars[code]
        m = signal_mask(spec, b, mkt.spy).to_numpy()
        busy_until = None
        for i in np.flatnonzero(m):
            d = a.dates[i]
            if d < start or d > end:
                continue
            stats["signals"] += 1
            if busy_until is not None and d < busy_until:
                stats["skipped_in_position"] += 1
                continue
            t = simulate_exit(a, i, spec.exit)
            if t is None:
                stats["skipped_missing_data"] += 1
                continue
            if t[2] > end:
                stats["skipped_incomplete"] += 1
                continue
            busy_until = t[2]
            g = t[3] / t[1] - 1
            rows.append({"ticker": code, "signal_date": d, "entry_date": t[0], "entry_price": t[1],
                         "exit_date": t[2], "exit_price": t[3], "exit_reason": t[4], "sessions_held": t[5],
                         "gross_return": g, "net_return": net_return(g, spec.costs, notional),
                         "regime": mkt.regime.get(d), "year": t[0].year})
    cols = ["ticker", "signal_date", "entry_date", "entry_price", "exit_date", "exit_price", "exit_reason",
            "sessions_held", "gross_return", "net_return", "regime", "year"]
    df = pd.DataFrame(rows, columns=cols)
    if len(df):
        df = df.sort_values(["entry_date", "ticker"]).reset_index(drop=True)
    stats["trades"] = len(df)
    return df, stats


# ------------------------------------------------------------------- portfolio
def simulate_portfolio(trades: pd.DataFrame, spec: StrategySpec, mkt: Market, start: date, end: date) -> dict:
    """Equal-weight, max N positions, value-based (fractional) sizing, fees + slippage. Deterministic
    ordering: entries on the same day are taken alphabetically by ticker until slots run out."""
    K, maxp, c = spec.portfolio.starting_capital, spec.portfolio.max_positions, spec.costs
    fee, s, reg = c.commission_per_order + c.platform_fee_per_order, c.slippage_bps / 1e4, c.regulatory_fee_bps_on_sells / 1e4
    days = [d for d in mkt.spy.index if start <= d <= end]
    by_entry = {d: g.sort_values("ticker") for d, g in trades.groupby("entry_date")} if len(trades) else {}
    cash, pos, done = K, [], []
    eq_prev, curve = K, []
    skipped_capacity = fees_paid = slip_paid = 0.0
    last_close: dict[str, float] = {}
    for d in days:
        still = []
        for p in pos:                               # stop / take-profit exits fill at today's OPEN
            if p["exit_date"] == d and p["exit_reason"] != "HOLD":
                cash += _close_position(p, done, fee, s, reg)
                fees_paid += fee
                slip_paid += p["shares"] * p["exit_price"] * s
            else:
                still.append(p)
        pos = still
        for _, t in by_entry.get(d, pd.DataFrame()).iterrows():
            if len(pos) >= maxp:
                skipped_capacity += 1
                continue
            alloc = min(cash, eq_prev / maxp)
            if alloc <= 2 * fee + 1:
                skipped_capacity += 1
                continue
            px = t["entry_price"] * (1 + s)
            shares = (alloc - fee) / px
            cash -= shares * px + fee
            fees_paid += fee
            slip_paid += shares * t["entry_price"] * s
            pos.append({**t.to_dict(), "shares": shares, "cost": shares * px + fee})
        still = []
        for p in pos:                               # fixed-hold exits fill at today's CLOSE
            if p["exit_date"] == d and p["exit_reason"] == "HOLD":
                cash += _close_position(p, done, fee, s, reg)
                fees_paid += fee
                slip_paid += p["shares"] * p["exit_price"] * s
            else:
                still.append(p)
        pos = still
        mv = 0.0
        for p in pos:
            px = mkt.bars[p["ticker"]]["close"].get(d)
            if px is not None and not math.isnan(px):
                last_close[p["ticker"]] = px
            mv += p["shares"] * last_close.get(p["ticker"], p["entry_price"])
        eq = cash + mv
        curve.append({"date": d, "equity": eq, "invested": mv, "positions": len(pos)})
        eq_prev = eq
    cur = pd.DataFrame(curve).set_index("date") if curve else pd.DataFrame(columns=["equity", "invested", "positions"])
    return {"curve": cur, "executed": pd.DataFrame(done), "skipped_capacity": int(skipped_capacity),
            "fees_paid": fees_paid, "slippage_paid": slip_paid}


def _close_position(p, done, fee, s, reg) -> float:
    """Record the closed trade; return the cash it releases."""
    px = p["exit_price"] * (1 - s)
    proceeds = p["shares"] * px
    net = proceeds - fee - proceeds * reg
    done.append({**{k: p[k] for k in ("ticker", "signal_date", "entry_date", "exit_date", "exit_reason",
                                      "sessions_held", "regime", "year")},
                 "pnl": net - p["cost"], "ret": net / p["cost"] - 1})
    return net


# --------------------------------------------------------------------- metrics
def _dd(eq: pd.Series) -> pd.Series:
    return eq / eq.cummax() - 1


def equity_metrics(eq: pd.Series) -> dict:
    if len(eq) < 2:
        return {"total_return": float("nan"), "cagr": float("nan"), "sharpe": float("nan"),
                "sortino": float("nan"), "max_drawdown": float("nan")}
    r = eq.pct_change().dropna()
    years = len(r) / 252
    total = eq.iloc[-1] / eq.iloc[0] - 1
    downside = math.sqrt(float((np.minimum(r, 0) ** 2).mean()))
    sd = float(r.std(ddof=1))
    return {"total_return": float(total),
            "cagr": float((1 + total) ** (1 / years) - 1) if years > 0 and total > -1 else float("nan"),
            "sharpe": float(r.mean() / sd * math.sqrt(252)) if sd > 0 else float("nan"),
            "sortino": float(r.mean() / downside * math.sqrt(252)) if downside > 0 else float("nan"),
            "max_drawdown": float(_dd(eq).min())}


def trade_metrics(ex: pd.DataFrame) -> dict:
    if ex.empty:
        return {"trades": 0}
    r, pnl = ex["ret"], ex["pnl"]
    wins, losses = r[r > 0], r[r <= 0]
    gl = -pnl[pnl <= 0].sum()
    return {"trades": int(len(ex)), "win_rate": float((r > 0).mean()), "avg_trade": float(r.mean()),
            "median_trade": float(r.median()), "avg_winner": float(wins.mean()) if len(wins) else float("nan"),
            "avg_loser": float(losses.mean()) if len(losses) else float("nan"),
            "profit_factor": float(pnl[pnl > 0].sum() / gl) if gl > 0 else float("inf"),
            "avg_holding_sessions": float(ex["sessions_held"].mean())}


def annual_returns(eq: pd.Series) -> dict[int, float]:
    s = pd.Series(eq.values, index=pd.to_datetime(eq.index))
    out, prev = {}, s.iloc[0]
    for y, g in s.groupby(s.index.year):
        out[int(y)] = float(g.iloc[-1] / prev - 1)
        prev = g.iloc[-1]
    return out


def run_backtest(spec: StrategySpec, mkt: Market, period: str = "development") -> dict:
    """Full deterministic backtest for one period. Returns JSON-ready results + the trade ledger."""
    start, end = mkt.period(period)
    trades, tstats = generate_trades(spec, mkt, start, end)
    port = simulate_portfolio(trades, spec, mkt, start, end)
    cur = port["curve"]
    K = spec.portfolio.starting_capital
    spy_c = mkt.spy["close"].loc[start:end]
    spy_eq = K * spy_c / spy_c.iloc[0]
    eq = cur["equity"] if len(cur) else pd.Series([K], index=[start])
    strat = equity_metrics(eq)
    strat.update(trade_metrics(port["executed"]))
    strat["exposure"] = float((cur["invested"] / cur["equity"]).mean()) if len(cur) else 0.0
    strat["estimated_costs_usd"] = port["fees_paid"] + port["slippage_paid"]
    strat["estimated_costs_pct_of_capital"] = strat["estimated_costs_usd"] / K
    bench = equity_metrics(spy_eq)
    ann_s, ann_b = annual_returns(eq), annual_returns(spy_eq)
    dd = _dd(eq)
    ds = max(1, len(eq) // 400)                  # thin chart series to <= ~400 points (display only)
    charts = {"dates": [str(d) for d in eq.index[::ds]], "strategy": [round(float(x), 2) for x in eq.values[::ds]],
              "spy": [round(float(x), 2) for x in spy_eq.reindex(eq.index).ffill().values[::ds]],
              "drawdown": [round(float(x), 5) for x in dd.values[::ds]],
              "annual": [{"year": y, "strategy": ann_s[y], "spy": ann_b.get(y)} for y in sorted(ann_s)]}
    ledger = trades
    return {
        "meta": {"engine_version": ENGINE_VERSION, "methodology_version": METHODOLOGY_VERSION,
                 "dataset_version": mkt.provider.dataset_version(), "provider": mkt.provider.label(),
                 "synthetic_data": mkt.provider.synthetic, "survivorship_biased": mkt.provider.survivorship_biased,
                 "spec_hash": spec.spec_hash(), "rules_hash": spec.rules_hash(), "period": period,
                 "start": str(start), "end": str(end), "universe_size": len(mkt.arr),
                 "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "settings": {"costs": spec.costs.model_dump(), "portfolio": spec.portfolio.model_dump()}},
        "counts": {**tstats, "executed_in_portfolio": int(len(port["executed"])),
                   "skipped_portfolio_capacity": port["skipped_capacity"]},
        "strategy": strat, "benchmark": bench, "charts": charts,
        "ledger_summary": {"n": int(len(ledger)), "mean_net": float(ledger["net_return"].mean()) if len(ledger) else None,
                           "mean_gross": float(ledger["gross_return"].mean()) if len(ledger) else None},
        "_ledger": ledger,
    }
