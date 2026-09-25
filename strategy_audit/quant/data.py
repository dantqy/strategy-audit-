"""Market data behind one interface, so a licensed vendor can replace Moomoo without touching the engine.

    MarketDataProvider
    ├── MoomooCacheProvider       local read-only cache from a personal Moomoo account (PROTOTYPE ONLY)
    ├── FutureCommercialProvider  placeholder for a licensed EOD vendor
    └── MockProvider              synthetic, deterministic, clearly labelled (tests / offline demo)

See docs/DATA_LICENSING.md: redistribution rights for Moomoo-derived data are unresolved.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from scanner import market_calendar as mc

ROOT = Path(__file__).resolve().parents[2]
TEST_START = date(2019, 1, 2)          # first tested session; earlier bars are indicator warm-up only
DEV_FRACTION = 0.70                    # first 70% of sessions = development, last 30% = sealed validation


@dataclass(frozen=True)
class DatasetInfo:
    provider: str
    label: str
    universe_name: str
    n_symbols: int
    first_bar: str
    last_bar: str
    test_start: str
    dev_end: str
    validation_start: str
    dataset_version: str
    survivorship_biased: bool
    synthetic: bool
    license_status: str
    notes: str


class MarketDataProvider:
    """Interface. Bars are split/dividend-adjusted daily OHLCV indexed by US session date (datetime.date)."""
    name = "abstract"
    synthetic = False
    survivorship_biased = True
    license_status = "unknown"

    def universe(self, universe_type: str) -> list[str]:
        raise NotImplementedError

    def bars(self, code: str) -> pd.DataFrame:
        raise NotImplementedError

    def benchmark(self) -> pd.DataFrame:
        raise NotImplementedError

    def dataset_version(self) -> str:
        raise NotImplementedError

    # ---- shared helpers
    def all_bars(self, universe_type: str) -> dict[str, pd.DataFrame]:
        return {c: self.bars(c) for c in self.universe(universe_type)}

    def sessions(self) -> list[date]:
        spy = self.benchmark()
        return [d for d in spy.index if d >= TEST_START]

    def split(self) -> tuple[date, date, date, date]:
        """(test_start, dev_end, validation_start, last_session): chronological 70/30 split, never shuffled."""
        s = self.sessions()
        cut = int(len(s) * DEV_FRACTION)
        return s[0], s[cut - 1], s[cut], s[-1]

    def info(self, universe_type: str = "LARGE_CAP_93") -> DatasetInfo:
        u = self.universe(universe_type)
        spy = self.benchmark()
        t0, dev_end, v0, last = self.split()
        return DatasetInfo(provider=self.name, label=self.label(), universe_name=universe_type, n_symbols=len(u),
                           first_bar=str(spy.index[0]), last_bar=str(spy.index[-1]), test_start=str(t0),
                           dev_end=str(dev_end), validation_start=str(v0), dataset_version=self.dataset_version(),
                           survivorship_biased=self.survivorship_biased, synthetic=self.synthetic,
                           license_status=self.license_status, notes=self.notes())

    def label(self) -> str:
        return self.name

    def notes(self) -> str:
        return ""


class MoomooCacheProvider(MarketDataProvider):
    """Reads the local Moomoo cache (data/cache/daily/*_QFQ.csv). Never calls the Moomoo API."""
    name = "moomoo_cache"
    license_status = "PERSONAL USE: redistribution rights unresolved (prototype only)"

    def __init__(self, root: Path | None = None):
        self.dir = (root or ROOT) / "data" / "cache" / "daily"
        self.universe_csv = (root or ROOT) / "data" / "universe.csv"

    def label(self) -> str:
        return "Moomoo OpenAPI (personal account), local cache"

    def notes(self) -> str:
        return ("93 of today's large US stocks. No point-in-time index membership and no delisted "
                "securities: results may be biased upward (survivorship bias).")

    def universe(self, universe_type: str) -> list[str]:
        if universe_type != "LARGE_CAP_93":
            raise ValueError(f"universe {universe_type!r} is not available from {self.name}")
        codes = pd.read_csv(self.universe_csv)["code"].astype(str).str.strip()
        return [c for c in codes if (self.dir / f"{c.replace('.', '_')}_QFQ.csv").exists()]

    @lru_cache(maxsize=256)
    def bars(self, code: str) -> pd.DataFrame:
        p = self.dir / f"{code.replace('.', '_')}_QFQ.csv"
        df = pd.read_csv(p, parse_dates=["date"])
        df["date"] = df["date"].dt.date
        return df.set_index("date").sort_index()[["open", "high", "low", "close", "volume"]].astype(float)

    def benchmark(self) -> pd.DataFrame:
        return self.bars("US.SPY")

    @lru_cache(maxsize=1)
    def dataset_version(self) -> str:
        parts = []
        for c in sorted(self.universe("LARGE_CAP_93") + ["US.SPY"]):
            m = self.dir / f"{c.replace('.', '_')}_QFQ.meta.json"
            meta = json.loads(m.read_text()) if m.exists() else {}
            parts.append(f"{c}:{meta.get('first')}:{meta.get('last')}:{len(self.bars(c))}")
        return "moomoo-qfq-" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]


class FutureCommercialProvider(MarketDataProvider):
    """Placeholder for a properly licensed end-of-day vendor (point-in-time membership + delisted names)."""
    name = "licensed_vendor"
    license_status = "not configured"

    _MSG = "Configure a licensed data vendor first (see docs/DATA_LICENSING.md)"

    def universe(self, universe_type):
        raise NotImplementedError(self._MSG)

    def bars(self, code):
        raise NotImplementedError(self._MSG)

    def benchmark(self):
        raise NotImplementedError(self._MSG)

    def dataset_version(self):
        raise NotImplementedError(self._MSG)


class MockProvider(MarketDataProvider):
    """SYNTHETIC random-walk data on real US trading days. Deterministic from a seed. NOT REAL MARKET DATA."""
    name = "mock"
    synthetic = True
    survivorship_biased = False
    license_status = "synthetic (no restrictions)"

    def __init__(self, n_symbols: int = 12, start: date = date(2017, 12, 1), end: date = date(2024, 12, 31),
                 seed: int = 7, drift: float = 0.0003, vol: float = 0.018):
        self.days = mc.trading_days(start, end)
        self.codes = [f"MOCK.S{k:02d}" for k in range(n_symbols)]
        self.seed, self.drift, self.vol = seed, drift, vol
        self._cache: dict[str, pd.DataFrame] = {}

    def label(self) -> str:
        return "SYNTHETIC DEMO DATA — NOT REAL MARKET DATA"

    def notes(self) -> str:
        return "Random-walk prices generated from a fixed seed. For tests and offline demos only."

    def universe(self, universe_type: str) -> list[str]:
        return list(self.codes)

    def _gen(self, code: str, k: int) -> pd.DataFrame:
        rng = np.random.default_rng(self.seed * 1000 + k)
        n = len(self.days)
        r = rng.normal(self.drift, self.vol, n)
        close = 50 * np.cumprod(1 + r)
        opn = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, self.vol / 4, n))
        high = np.maximum(opn, close) * (1 + np.abs(rng.normal(0, self.vol / 3, n)))
        low = np.minimum(opn, close) * (1 - np.abs(rng.normal(0, self.vol / 3, n)))
        vol = 1e6 * np.exp(rng.normal(0, 0.3, n))
        return pd.DataFrame({"open": opn, "high": high, "low": low, "close": close, "volume": vol},
                            index=pd.Index(self.days, name="date"))

    def bars(self, code: str) -> pd.DataFrame:
        if code not in self._cache:
            k = 999 if code == "MOCK.SPY" else self.codes.index(code)
            self._cache[code] = self._gen(code, k)
        return self._cache[code]

    def benchmark(self) -> pd.DataFrame:
        return self.bars("MOCK.SPY")

    def dataset_version(self) -> str:
        return f"mock-seed{self.seed}-n{len(self.codes)}-{self.days[0]}-{self.days[-1]}"


_DEFAULT: MarketDataProvider | None = None


def default_provider() -> MarketDataProvider:
    """Moomoo cache if present, else the clearly-labelled mock (never silently: .synthetic is exposed)."""
    global _DEFAULT
    if _DEFAULT is None:
        p = MoomooCacheProvider()
        _DEFAULT = p if (p.dir / "US_SPY_QFQ.csv").exists() else MockProvider()
    return _DEFAULT
