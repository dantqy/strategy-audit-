"""Investment-plan simulator: monthly investing (DCA), optionally buying extra after drops from the 52-week high.

This answers a different question from the trade backtester. There are no exits and no win rate; the test is
whether the plan ended with more money (per dollar put in) than plain monthly investing in the same asset.

Timing (no lookahead):
* Monthly buy: at the OPEN of the first trading session of each month (a calendar rule, known in advance).
* 52-week high: the highest CLOSE of the last 252 sessions including today, known at today's close.
* Dip trigger: drawdown = 1 - close / 52-week high, checked at the close; the buy fills at the NEXT session's open.
* A tier fires once, then re-arms only after the price closes at a new 52-week high.
Money: fractional units, a fee per order and slippage on every buy. Prices are split/dividend-adjusted, so
dividends are treated as reinvested (approximately). No taxes, no currency conversion.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Literal, Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

HIGH_WINDOW = 252
MIN_YEARS_FOR_START = 3          # start-year robustness: each start needs at least this many years of data
_NAME_OK = re.compile(r"^[\w\s\-\(\)\.,&%/=+':]{1,80}$")


class Tier(BaseModel):
    model_config = ConfigDict(extra="forbid")
    drawdown: float = Field(..., ge=0.03, le=0.6)       # 0.10 = 10% below the 52-week high
    deploy: float = Field(1.0, ge=0.05, le=1.0)         # RESERVE: share of the cash pile to invest


class DipRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tiers: list[Tier] = Field(..., min_length=1, max_length=5)
    funding: Literal["RESERVE", "EXTRA"]
    reserve_share: Optional[float] = Field(None, ge=0.05, le=0.9)   # RESERVE: part of each monthly amount kept as cash
    extra_amount: Optional[float] = Field(None, ge=1, le=1_000_000)  # EXTRA: new money added at each dip buy
    cash_rate: float = Field(0.0, ge=0.0, le=0.1)                   # RESERVE: yearly interest on the cash pile

    @model_validator(mode="after")
    def _check(self):
        dd = [t.drawdown for t in self.tiers]
        if dd != sorted(dd) or len(set(dd)) != len(dd):
            raise ValueError("dip levels must be different and in increasing order")
        if self.funding == "RESERVE" and self.reserve_share is None:
            raise ValueError("RESERVE funding needs reserve_share")
        if self.funding == "EXTRA" and self.extra_amount is None:
            raise ValueError("EXTRA funding needs extra_amount")
        return self


class PlanSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = "My investment plan"
    asset: str = Field(..., pattern=r"^[A-Z]{2,4}\.[A-Z0-9.\-]{1,10}$")
    monthly_amount: float = Field(..., ge=10, le=100_000)
    start_month: Optional[str] = Field(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")   # None = earliest possible
    dip: Optional[DipRule] = None
    fee_per_order: float = Field(0.99, ge=0, le=20)
    slippage_bps: float = Field(10.0, ge=0, le=100)

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        v = v.strip()
        if not _NAME_OK.match(v):
            raise ValueError("name: letters, digits and basic punctuation only (max 80)")
        return v


# ------------------------------------------------------------------ core
def earliest_start(bars: pd.DataFrame) -> date:
    """First session with a full 52-week history."""
    return bars.index[HIGH_WINDOW - 1]


def _irr(flows: list[tuple[date, float]]) -> Optional[float]:
    """Money-weighted yearly return. flows: (date, amount), money in < 0, final value > 0."""
    if len(flows) < 2:
        return None
    t0 = flows[0][0]
    ts = np.array([(d - t0).days / 365.25 for d, _ in flows])
    a = np.array([x for _, x in flows])
    if ts[-1] <= 0:
        return None
    f = lambda r: float((a / (1 + r) ** ts).sum())
    lo, hi = -0.99, 10.0
    if f(lo) * f(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) > 0 else (lo, mid)
    return (lo + hi) / 2


def simulate(bars: pd.DataFrame, monthly: float, start: date, dip: Optional[DipRule],
             fee: float, slip_bps: float) -> dict:
    """One run from `start` to the last bar. dip=None is plain monthly investing."""
    idx = [d for d in bars.index if d >= start]
    op = bars["open"].to_numpy(float)
    cl = bars["close"].to_numpy(float)
    hi52 = bars["close"].rolling(HIGH_WINDOW, min_periods=HIGH_WINDOW).max().to_numpy(float)
    pos = {d: i for i, d in enumerate(bars.index)}
    slip = slip_bps / 1e4
    units, cash, fees = 0.0, 0.0, 0.0
    flows: list[tuple[date, float]] = []
    buys, curve = [], []
    tiers = dip.tiers if dip else []
    armed = [True] * len(tiers)
    pending: list[tuple[int, float, date]] = []          # (tier index, drawdown at signal, signal date)
    last_month, prev_day = None, None
    reserve = dip.reserve_share if (dip and dip.funding == "RESERVE") else 0.0

    def buy(amount: float, i: int) -> float:
        nonlocal units, fees, cash
        if amount <= fee:                                # not worth an order: keep it as cash, never lose it
            cash += amount
            return 0.0
        fees += fee
        u = (amount - fee) / (op[i] * (1 + slip))
        units += u
        return u

    for d in idx:
        i = pos[d]
        if prev_day is not None and cash > 0 and dip and dip.cash_rate:
            cash *= (1 + dip.cash_rate) ** ((d - prev_day).days / 365.25)
        prev_day = d
        # 1) orders decided at yesterday's close fill at today's open
        for k, dd, sig in pending:
            if dip.funding == "RESERVE":
                amt = cash * tiers[k].deploy
                if amt <= fee:
                    continue
                cash -= amt
            else:
                amt = dip.extra_amount
                flows.append((d, -amt))
            u = buy(amt, i)
            buys.append({"signal_date": sig, "date": d, "tier": tiers[k].drawdown, "drawdown": dd,
                         "amount": amt, "price": op[i], "units": u})
        pending = []
        # 2) the monthly contribution at the first session of each month
        if (d.year, d.month) != last_month:
            last_month = (d.year, d.month)
            flows.append((d, -monthly))
            buy(monthly * (1 - reserve), i)
            cash += monthly * reserve
        # 3) at the close: re-arm on a new 52-week high, queue dip buys for tomorrow's open
        if not np.isnan(hi52[i]):
            if cl[i] >= hi52[i] - 1e-12:
                armed = [True] * len(tiers)
            dd = 1 - cl[i] / hi52[i]
            for k, t in enumerate(tiers):
                if armed[k] and dd >= t.drawdown - 1e-12:
                    armed[k] = False
                    pending.append((k, dd, d))
        curve.append((d, units * cl[i] + cash, -sum(x for _, x in flows), cash))
    end = idx[-1]
    value = units * cl[pos[end]] + cash
    put_in = -sum(x for _, x in flows)
    vals = np.array([c[1] for c in curve])
    peak = np.maximum.accumulate(vals)
    worst = float(((vals - peak) / np.where(peak > 0, peak, 1)).min()) if len(vals) else 0.0
    return {"start": idx[0], "end": end, "put_in": put_in, "final_value": value, "profit": value - put_in,
            "irr": _irr(flows + [(end, value)]), "fees": fees, "cash_end": cash,
            "avg_cash_share": float(np.mean([c[3] / c[1] for c in curve if c[1] > 0])) if curve else 0.0,
            "worst_drop": worst, "buys": buys, "curve": curve, "months": len({(d.year, d.month) for d, *_ in curve})}


def _month_start(bars: pd.DataFrame, ym: Optional[str]) -> date:
    first = earliest_start(bars)
    if not ym:
        return first
    y, m = map(int, ym.split("-"))
    d = date(y, m, 1)
    if d < date(first.year, first.month, 1):
        raise ValueError(f"start_month: earliest possible start is {first.year}-{first.month:02d} "
                         f"(the 52-week high needs a year of history)")
    later = [x for x in bars.index if x >= d]
    if len(later) < 60:
        raise ValueError("start_month: too close to the end of the data")
    return max(later[0], first)


def _row(r: dict) -> dict:
    return {k: r[k] for k in ("start", "end", "put_in", "final_value", "profit", "irr", "fees", "cash_end",
                              "avg_cash_share", "worst_drop", "months")}


def run_plan(spec: PlanSpec, bars: pd.DataFrame) -> dict:
    """The plan, plain monthly investing (same monthly amount), a chart and a start-year robustness table."""
    start = _month_start(bars, spec.start_month)
    plain = simulate(bars, spec.monthly_amount, start, None, spec.fee_per_order, spec.slippage_bps)
    out = {"plain": _row(plain), "plan": None, "dip_buys": [], "by_start_year": [], "chart": None, "verdict": None}
    step = max(1, len(plain["curve"]) // 400)
    chart = {"dates": [str(c[0]) for c in plain["curve"][::step]],
             "plain_value": [round(c[1], 2) for c in plain["curve"][::step]],
             "plain_in": [round(c[2], 2) for c in plain["curve"][::step]]}
    if spec.dip:
        plan = simulate(bars, spec.monthly_amount, start, spec.dip, spec.fee_per_order, spec.slippage_bps)
        out["plan"] = _row(plan)
        out["dip_buys"] = [{**b, "signal_date": str(b["signal_date"]), "date": str(b["date"])} for b in plan["buys"]]
        chart.update(plan_value=[round(c[1], 2) for c in plan["curve"][::step]],
                     plan_in=[round(c[2], 2) for c in plan["curve"][::step]])
        # robustness: the same comparison from every January that leaves >= MIN_YEARS_FOR_START years of data
        first, last = earliest_start(bars), bars.index[-1]
        for y in range(first.year, last.year + 1):
            s = max(first, next(d for d in bars.index if d >= date(y, 1, 1)))
            if (last - s).days < MIN_YEARS_FOR_START * 365:
                continue
            a = simulate(bars, spec.monthly_amount, s, None, spec.fee_per_order, spec.slippage_bps)
            b = simulate(bars, spec.monthly_amount, s, spec.dip, spec.fee_per_order, spec.slippage_bps)
            out["by_start_year"].append({"start": str(s), "plain_irr": a["irr"], "plan_irr": b["irr"],
                                         "plain_value": a["final_value"], "plan_value": b["final_value"],
                                         "plain_in": a["put_in"], "plan_in": b["put_in"], "dip_buys": len(b["buys"])})
        out["verdict"] = verdict(spec, out)
    out["chart"] = chart
    return out


def verdict(spec: PlanSpec, out: dict) -> dict:
    """Plain-language conclusion computed from the numbers above (no free-text model involved)."""
    rows = out["by_start_year"]
    same_money = spec.dip.funding == "RESERVE"
    TOL = 0.001                                   # 0.1 percentage point per year counts as a tie
    ahead = sum(1 for r in rows if r["plan_irr"] is not None and r["plain_irr"] is not None
                and r["plan_irr"] > r["plain_irr"] + TOL)
    behind = sum(1 for r in rows if r["plan_irr"] is not None and r["plain_irr"] is not None
                 and r["plan_irr"] < r["plain_irr"] - TOL)
    n = len(rows)
    if n == 0:
        label = "NOT ENOUGH HISTORY"
    elif ahead >= 0.75 * n:
        label = "DIP-BUYING CAME OUT AHEAD"
    elif behind >= 0.75 * n:
        label = "PLAIN MONTHLY INVESTING CAME OUT AHEAD"
    else:
        label = "MIXED: DEPENDS ON THE START DATE"
    diffs = [r["plan_irr"] - r["plain_irr"] for r in rows if r["plan_irr"] is not None and r["plain_irr"] is not None]
    median_diff = float(np.median(diffs)) if diffs else None
    if median_diff is not None and label.endswith("AHEAD") and abs(median_diff) < 0.005:
        label += " (BY A SMALL MARGIN)"
    notes = []
    if len(out["dip_buys"]) < 5:
        notes.append(f"Only {len(out['dip_buys'])} dip buys happened, so this rests on a handful of market drops.")
    if same_money and out["plan"]["cash_end"] > spec.monthly_amount:
        notes.append(f"${out['plan']['cash_end']:,.0f} was still waiting in cash at the end.")
    if not same_money:
        notes.append("The dip plan puts in more money than plain investing, so its final value is naturally higher. "
                     "The fair comparison is the yearly return on money put in.")
    return {"label": label, "ahead": ahead, "behind": behind, "ties": n - ahead - behind, "n": n,
            "median_irr_diff": median_diff, "same_money": same_money, "notes": notes}
