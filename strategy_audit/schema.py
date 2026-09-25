"""Strict, closed strategy schema. User text only ever becomes one of these approved primitives.

Nothing here is executable: a StrategySpec is data. The engine maps each whitelisted indicator
name to a fixed pandas computation; unknown names, extra fields or out-of-range parameters are
rejected by validation (never eval'd, never guessed).
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import SCHEMA_VERSION

# indicator -> (needs period, min period, max period, value range or None, human label)
INDICATORS: dict[str, tuple[bool, int, int, Optional[tuple[float, float]], str]] = {
    "CLOSE": (False, 0, 0, (0.01, 1e6), "Close price"),
    "RSI": (True, 2, 100, (0.0, 100.0), "RSI"),
    "SMA": (True, 2, 250, (0.01, 1e6), "Simple moving average"),
    "EMA": (True, 2, 250, (0.01, 1e6), "Exponential moving average"),
    "RETURN": (True, 1, 250, (-0.95, 10.0), "Return over N sessions"),
    "REL_VOLUME": (True, 5, 100, (0.0, 50.0), "Volume vs prior N-session average"),
    "HIGH": (True, 5, 252, None, "Highest high of the prior N sessions"),
    "LOW": (True, 5, 252, None, "Lowest low of the prior N sessions"),
    "REL_STRENGTH": (True, 5, 250, (-5.0, 5.0), "N-session return minus SPY's"),
    "SPY_CLOSE": (False, 0, 0, None, "SPY close"),
    "SPY_SMA": (True, 2, 250, None, "SPY simple moving average"),
    "SPY_RETURN": (True, 1, 250, (-0.95, 10.0), "SPY return over N sessions"),
}
IndicatorName = Literal["CLOSE", "RSI", "SMA", "EMA", "RETURN", "REL_VOLUME", "HIGH", "LOW", "REL_STRENGTH",
                        "SPY_CLOSE", "SPY_SMA", "SPY_RETURN"]
Operator = Literal["<", "<=", ">", ">="]
PRICE_LIKE = {"CLOSE", "SMA", "EMA", "HIGH", "LOW"}
SPY_PRICE_LIKE = {"SPY_CLOSE", "SPY_SMA"}
_NAME_OK = re.compile(r"^[\w\s\-\(\)\.,&%/=+':]{1,80}$")      # no < > quotes: defence in depth


def _label(ind: str, period: Optional[int]) -> str:
    return f"{ind}({period})" if INDICATORS[ind][0] else ind


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    indicator: IndicatorName
    period: Optional[int] = None
    operator: Operator
    value: Optional[float] = None
    comparison_indicator: Optional[IndicatorName] = None
    comparison_period: Optional[int] = None
    comparison_multiplier: float = Field(1.0, ge=0.5, le=2.0)

    @model_validator(mode="after")
    def _check(self):
        needs, lo, hi, vrange, _ = INDICATORS[self.indicator]
        if needs and self.period is None:
            raise ValueError(f"{self.indicator} needs a period")
        if not needs and self.period is not None:
            raise ValueError(f"{self.indicator} takes no period")
        if needs and not lo <= self.period <= hi:
            raise ValueError(f"{self.indicator} period must be {lo}-{hi}")
        if (self.value is None) == (self.comparison_indicator is None):
            raise ValueError("give exactly one of: value, comparison_indicator")
        if self.value is not None:
            if vrange is None:
                raise ValueError(f"{self.indicator} must be compared with another indicator, not a number")
            if not vrange[0] <= self.value <= vrange[1]:
                raise ValueError(f"{self.indicator} value must be within {vrange}")
        if self.comparison_indicator is not None:
            cneeds, clo, chi, _, _ = INDICATORS[self.comparison_indicator]
            if cneeds and self.comparison_period is None:
                raise ValueError(f"{self.comparison_indicator} needs a comparison_period")
            if not cneeds and self.comparison_period is not None:
                raise ValueError(f"{self.comparison_indicator} takes no period")
            if cneeds and not clo <= self.comparison_period <= chi:
                raise ValueError(f"{self.comparison_indicator} period must be {clo}-{chi}")
            a = "stock" if self.indicator in PRICE_LIKE else "spy" if self.indicator in SPY_PRICE_LIKE else None
            b = ("stock" if self.comparison_indicator in PRICE_LIKE else
                 "spy" if self.comparison_indicator in SPY_PRICE_LIKE else None)
            if a is None or a != b:
                raise ValueError("indicator-vs-indicator comparisons are only allowed between price levels of the "
                                 "same instrument (e.g. CLOSE vs SMA, SPY_CLOSE vs SPY_SMA)")
        return self

    def describe(self) -> str:
        left = _label(self.indicator, self.period)
        if self.value is not None:
            v = self.value
            if self.indicator in ("RETURN", "REL_STRENGTH", "SPY_RETURN"):
                right = f"{v * 100:+.1f}%"
            elif self.indicator == "REL_VOLUME":
                right = f"{v:g}x"
            elif self.indicator == "CLOSE":
                right = f"${v:,.2f}"
            else:
                right = f"{v:g}"
        else:
            right = _label(self.comparison_indicator, self.comparison_period)
            if self.comparison_multiplier != 1.0:
                right = f"{self.comparison_multiplier:g} x {right}"
        return f"{left} {self.operator} {right}"


class Universe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["LARGE_CAP_93"]


class ExitRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["FIXED_HOLD"] = "FIXED_HOLD"
    trading_days: int = Field(..., ge=1, le=60)
    stop_loss_pct: Optional[float] = Field(None, ge=0.01, le=0.5)
    take_profit_pct: Optional[float] = Field(None, ge=0.01, le=2.0)

    def describe(self) -> str:
        s = (f"Sell at the close of the {self.trading_days}th trading session after entry "
             f"(entry day = session 1)")
        extra = []
        if self.stop_loss_pct:
            extra.append(f"earlier at the next open if a close is {self.stop_loss_pct * 100:g}%+ below entry")
        if self.take_profit_pct:
            extra.append(f"earlier at the next open if a close is {self.take_profit_pct * 100:g}%+ above entry")
        return s + ("; " + "; ".join(extra) if extra else "")


class Costs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commission_per_order: float = Field(0.0, ge=0, le=100)
    platform_fee_per_order: float = Field(0.99, ge=0, le=100)
    slippage_bps: float = Field(10.0, ge=0, le=200)
    regulatory_fee_bps_on_sells: float = Field(0.3, ge=0, le=50)


class Portfolio(BaseModel):
    model_config = ConfigDict(extra="forbid")
    starting_capital: float = Field(100_000.0, ge=1_000, le=10_000_000)
    max_positions: int = Field(10, ge=1, le=50)


class StrategySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec_version: Literal["1.0"] = SCHEMA_VERSION
    name: str = Field(..., min_length=1, max_length=80)
    universe: Universe
    direction: Literal["LONG"]
    entry_conditions: list[Condition] = Field(..., min_length=1, max_length=6)
    entry_execution: Literal["NEXT_OPEN"]
    exit: ExitRule
    benchmark: Literal["SPY"] = "SPY"
    costs: Costs = Costs()
    portfolio: Portfolio = Portfolio()

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not _NAME_OK.match(v):
            raise ValueError("name may contain letters, digits, spaces and basic punctuation only")
        return v

    def canonical(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def spec_hash(self) -> str:
        return hashlib.sha256(self.canonical().encode()).hexdigest()[:16]

    def rules_hash(self) -> str:
        """Hash of the trading RULES only (not name/costs/portfolio): identifies 'the same strategy'."""
        d = self.model_dump(mode="json")
        core = {k: d[k] for k in ("universe", "direction", "entry_conditions", "entry_execution", "exit")}
        return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()[:16]
