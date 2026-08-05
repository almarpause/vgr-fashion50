"""Configuration: paths, thresholds, constituent universe.

Everything here is data / tunables.  No network, no side effects on import.
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONSTITUENTS_CSV = os.path.join(PROJECT_DIR, "constituents.csv")
WORKBOOK_PATH = os.path.join(PROJECT_DIR, "Fashion50_Index.xlsx")
STATE_PATH = os.path.join(PROJECT_DIR, "state.json")
ALERTS_LOG = os.path.join(PROJECT_DIR, "logs", "alerts.log")
AUDIT_DIR = os.path.join(PROJECT_DIR, "audit")

# --------------------------------------------------------------------------- #
# Index construction parameters
# --------------------------------------------------------------------------- #
BASE_INDEX_LEVEL = 1000.0            # index == 1000.00 on the base date
WEIGHT_CAP = 0.10                    # single-name cap (10%); None or <=0 disables
WEIGHT_CAP_ENABLED = False           # capping OFF: pure float-adjusted weights
#                                      (flip to True to reinstate the 10% cap)

# Anomaly-guard thresholds (Weekly job).  Fractions, not percents.
SHARES_CHANGE_THRESHOLD = 0.15      # shares outstanding moved >15% vs last run
CAP_MOVE_THRESHOLD = 0.25           # a name's cap_usd moved >25% overnight
FX_OUTLIER_THRESHOLD = 0.15         # an FX rate moved >15% vs last run

# Annual reconstitution buffer ranks (40/60 rule) to minimise churn.
ADD_RANK = 40                       # newcomer added only if it rises above ~40
DROP_RANK = 60                      # member dropped only if it falls below ~60

# Pre-flight / early-warning layer (proactive, runs before the weekly job).
SHARES_DRIFT_EARLY = 0.03           # flag a >3% share-count drift EARLY (the
#                                     reactive anomaly guard only trips at 15%)
WATCH_HORIZON_DAYS = 21             # look this many days ahead for events
DROP_ZONE_RANK = 45                 # a member ranked worse than this is "near
#                                     the drop zone" — a reconstitution warning
NEWS_KEYWORDS = ("merger", "acquisition", "acquire", "delist", "delisting",
                 "spin-off", "spinoff", "buyback", "buy-back", "rights issue",
                 "stock split", "take-private", "tender offer")

# Numerical tolerance used across verification / continuity checks.
TOL = 1e-6

# Family / founder-controlled names whose real free float is low; flag these
# for manual float review even when Yahoo reports a floatShares value.
FAMILY_CONTROLLED = {
    "MC.PA",    # LVMH (Arnault family)
    "RMS.PA",   # Hermes (Hermes family)
    "ITX.MC",   # Inditex (Ortega)
    "EL.PA",    # EssilorLuxottica (Del Vecchio)
    "CDI.PA",   # Christian Dior SE (Arnault)
    "KER.PA",   # Kering (Pinault)
    "PAGEIND.NS",  # Page Industries (promoter-held)
    "9983.T",   # Fast Retailing (Yanai)
}


@dataclass(frozen=True)
class Constituent:
    name: str
    yahoo_ticker: str
    google_ticker: str
    trading_currency: str
    segment: str
    country_hq: str
    ir_url: str = ""


def load_constituents(path: str = CONSTITUENTS_CSV) -> list[Constituent]:
    """Read the seed constituent universe from CSV."""
    out: list[Constituent] = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            out.append(
                Constituent(
                    name=row["name"].strip(),
                    yahoo_ticker=row["yahoo_ticker"].strip(),
                    google_ticker=row["google_ticker"].strip(),
                    trading_currency=row["trading_currency"].strip(),
                    segment=row["segment"].strip(),
                    country_hq=row["country_hq"].strip(),
                    ir_url=(row.get("ir_url") or "").strip(),
                )
            )
    return out


@dataclass
class Settings:
    """Runtime knobs, overridable for tests."""
    weight_cap: float | None = WEIGHT_CAP
    weight_cap_enabled: bool = WEIGHT_CAP_ENABLED
    base_index_level: float = BASE_INDEX_LEVEL
    shares_change_threshold: float = SHARES_CHANGE_THRESHOLD
    cap_move_threshold: float = CAP_MOVE_THRESHOLD
    fx_outlier_threshold: float = FX_OUTLIER_THRESHOLD
    add_rank: int = ADD_RANK
    drop_rank: int = DROP_RANK
    shares_drift_early: float = SHARES_DRIFT_EARLY
    watch_horizon_days: int = WATCH_HORIZON_DAYS
    drop_zone_rank: int = DROP_ZONE_RANK
    tol: float = TOL
    family_controlled: frozenset[str] = field(
        default_factory=lambda: frozenset(FAMILY_CONTROLLED)
    )

    @property
    def effective_cap(self) -> float | None:
        if not self.weight_cap_enabled:
            return None
        if self.weight_cap is None or self.weight_cap <= 0:
            return None
        return self.weight_cap


DEFAULT_SETTINGS = Settings()
