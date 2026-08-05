"""Persistent engine state (source of truth for the divisor & base).

Stored as JSON next to the workbook so any index level can be reproduced.
The workbook is the human-facing artifact; ``state.json`` is the machine
source of truth for divisor, base caps and last-run values used by the
anomaly guard.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

from . import config


@dataclass
class EngineState:
    base_date: str | None = None
    base_divisor: float | None = None
    current_divisor: float | None = None
    base_index_level: float = config.BASE_INDEX_LEVEL
    base_total_cap_usd: float | None = None
    # current membership as yahoo tickers
    constituents: list[str] = field(default_factory=list)
    # last successful per-ticker values (for anomaly detection)
    last_shares: dict[str, float] = field(default_factory=dict)
    last_float_factor: dict[str, float] = field(default_factory=dict)
    last_caps_usd: dict[str, float] = field(default_factory=dict)
    last_fx: dict[str, float] = field(default_factory=dict)
    last_index_level: float | None = None
    last_run_date: str | None = None

    @property
    def initialized(self) -> bool:
        return self.base_divisor is not None and self.current_divisor is not None

    def to_json(self) -> dict:
        return asdict(self)


def load_state(path: str = config.STATE_PATH) -> EngineState:
    if not os.path.exists(path):
        return EngineState()
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    known = EngineState().to_json().keys()
    return EngineState(**{k: v for k, v in data.items() if k in known})


def save_state(state: EngineState, path: str = config.STATE_PATH) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state.to_json(), fh, indent=2, sort_keys=True)
