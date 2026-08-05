"""Tests for FX resolution and data normalization (offline)."""
import math

import pytest

from engine.datafetch import Quote, StaticDataProvider, normalize_currency
from engine.fx import StaticFxProvider


def test_normalize_pence_to_gbp():
    price, ccy = normalize_currency(14675.0, "GBp")
    assert ccy == "GBP"
    assert price == pytest.approx(146.75)


def test_normalize_cents_to_zar():
    price, ccy = normalize_currency(2168.0, "ZAc")
    assert ccy == "ZAR"
    assert price == pytest.approx(21.68)


def test_normalize_passthrough():
    price, ccy = normalize_currency(100.0, "usd")
    assert ccy == "USD"
    assert price == 100.0


def test_static_fx_identity_and_missing():
    fx = StaticFxProvider({"EUR": 1.10})
    assert fx.get("USD").rate_to_usd == 1.0
    assert fx.get("USD").source == "identity"
    eur = fx.get("EUR")
    assert eur.rate_to_usd == pytest.approx(1.10)
    missing = fx.get("XYZ")
    assert missing.status == "MISSING"
    assert math.isnan(missing.rate_to_usd)


def test_quote_ok_property():
    good = Quote("A", price=10.0, currency="USD", shares_outstanding=1000)
    assert good.ok
    no_price = Quote("A", price=None, currency="USD", shares_outstanding=1000)
    assert not no_price.ok
    no_shares = Quote("A", price=10.0, currency="USD", shares_outstanding=None)
    assert not no_shares.ok


def test_static_data_provider_missing():
    dp = StaticDataProvider({})
    q = dp.get_quote("NOPE")
    assert q.status == "MISSING"
    assert not q.ok
