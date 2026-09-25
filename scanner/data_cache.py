"""On-disk caches so static/historical data is not re-downloaded (API quota)."""
from __future__ import annotations

import json
import logging
import time
from datetime import date
from pathlib import Path

import pandas as pd

from .config import resolve

log = logging.getLogger(__name__)


def _safe(code: str) -> str:
    return code.replace(".", "_").replace("/", "_")


class DailyBarCache:
    """Daily OHLCV per symbol: CSV + small JSON meta (requested_start, fetched_at)."""

    def __init__(self, root: str | Path = "data/cache/daily", autype: str = "QFQ"):
        self.root = resolve(str(root))
        self.root.mkdir(parents=True, exist_ok=True)
        self.autype = autype

    def _paths(self, code: str) -> tuple[Path, Path]:
        stem = f"{_safe(code)}_{self.autype}"
        return self.root / f"{stem}.csv", self.root / f"{stem}.meta.json"

    def load(self, code: str) -> tuple[pd.DataFrame | None, dict]:
        csv, meta = self._paths(code)
        if not csv.exists():
            return None, {}
        df = pd.read_csv(csv, parse_dates=["date"])
        df["date"] = df["date"].dt.date
        df = df.set_index("date").sort_index()
        m = json.loads(meta.read_text()) if meta.exists() else {}
        return df, m

    def save(self, code: str, df: pd.DataFrame, requested_start: date) -> None:
        csv, meta = self._paths(code)
        out = df.copy()
        out.index.name = "date"
        out.to_csv(csv)
        meta.write_text(json.dumps({
            "code": code, "autype": self.autype,
            "requested_start": requested_start.isoformat(),
            "first": str(df.index[0]) if len(df) else None,
            "last": str(df.index[-1]) if len(df) else None,
            "fetched_at_epoch": time.time(),
        }, indent=1))


class JsonCache:
    """Generic keyed JSON/CSV cache with max-age."""

    def __init__(self, root: str | Path):
        self.root = resolve(str(root))
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key: str, ext: str = "csv") -> Path:
        return self.root / f"{_safe(key)}.{ext}"

    def fresh(self, key: str, max_age_s: float | None, ext: str = "csv") -> bool:
        p = self.path(key, ext)
        if not p.exists():
            return False
        return max_age_s is None or (time.time() - p.stat().st_mtime) < max_age_s

    def read_df(self, key: str) -> pd.DataFrame:
        log.info("cache hit: %s", self.path(key))
        return pd.read_csv(self.path(key))

    def write_df(self, key: str, df: pd.DataFrame) -> None:
        df.to_csv(self.path(key), index=False)
