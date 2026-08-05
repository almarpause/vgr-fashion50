"""Shared test fixtures — fully offline, deterministic (no network)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import config  # noqa: E402
from engine.config import Constituent, Settings  # noqa: E402
from engine.datafetch import Quote, StaticDataProvider  # noqa: E402
from engine.fx import StaticFxProvider  # noqa: E402
from engine.state import EngineState  # noqa: E402
from engine.workbook import WorkbookManager  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_alerts(tmp_path, monkeypatch):
    """Keep alert logging inside the tmp dir during tests."""
    monkeypatch.setattr(config, "ALERTS_LOG",
                        str(tmp_path / "alerts.log"), raising=False)
    yield


@pytest.fixture
def settings():
    return Settings()


def make_quote(ticker, price, currency, shares, float_shares=None):
    return Quote(ticker=ticker, price=price, currency=currency,
                 shares_outstanding=shares, float_shares=float_shares,
                 source="static", status="OK")


@pytest.fixture
def three_stock_universe():
    """A synthetic 3-stock universe with static quotes + FX."""
    consts = [
        Constituent("Alpha", "ALPHA", "ALPHA:X", "USD", "Fashion", "USA"),
        Constituent("Beta", "BETA.PA", "BETA:X", "EUR", "Luxury", "France"),
        Constituent("Gamma", "GAMMA.L", "GAMMA:X", "GBP", "Sportswear", "UK"),
    ]
    quotes = {
        "ALPHA": make_quote("ALPHA", 100.0, "USD", 1_000_000, 900_000),
        "BETA.PA": make_quote("BETA.PA", 50.0, "EUR", 2_000_000, 2_000_000),
        "GAMMA.L": make_quote("GAMMA.L", 20.0, "GBP", 5_000_000, 4_000_000),
    }
    data = StaticDataProvider(quotes)
    fx = StaticFxProvider({"USD": 1.0, "EUR": 1.10, "GBP": 1.25})
    return consts, data, fx


@pytest.fixture
def wbm(tmp_path):
    return WorkbookManager(str(tmp_path / "TestBook.xlsx"))


@pytest.fixture
def fresh_state():
    return EngineState()
