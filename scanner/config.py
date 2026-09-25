"""Configuration loading. Paths are resolved relative to the project root."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"

_FORBIDDEN_KEYS = {"live", "trd_env", "real", "live_trading", "environment"}


def _check_forbidden(node: Any, path: str = "") -> None:
    """Refuse any config that tries to introduce a trading-environment switch."""
    if isinstance(node, dict):
        for k, v in node.items():
            if str(k).lower() in _FORBIDDEN_KEYS:
                raise ValueError(
                    f"Config key '{path}{k}' is not allowed: this project has no "
                    "live/real trading mode and no environment switch."
                )
            _check_forbidden(v, f"{path}{k}.")


def load_config(path: str | Path | None = None) -> dict:
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(p, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    _check_forbidden(cfg)
    return cfg


def resolve(path_str: str) -> Path:
    p = Path(path_str)
    return p if p.is_absolute() else PROJECT_ROOT / p


def override(cfg: dict, dotted: str, value: Any) -> dict:
    """Return a deep copy of cfg with one dotted key replaced (for sensitivity runs)."""
    out = copy.deepcopy(cfg)
    node = out
    keys = dotted.split(".")
    for k in keys[:-1]:
        node = node[k]
    node[keys[-1]] = value
    return out
