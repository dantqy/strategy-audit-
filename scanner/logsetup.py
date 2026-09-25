"""Logging to console + outputs/logs/<name>_<utc>.log."""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from .config import resolve


def setup_logging(name: str, verbose: bool = False) -> str:
    d = resolve("outputs/logs")
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    ch = logging.StreamHandler(sys.stderr)
    ch.setFormatter(fmt)
    ch.setLevel(logging.INFO if verbose else logging.WARNING)
    root.addHandler(fh)
    root.addHandler(ch)
    return str(path)
