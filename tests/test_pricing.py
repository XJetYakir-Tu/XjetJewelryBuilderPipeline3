"""Fixed-volume fashion pricing, luxury unavailability, profile validation, CPP parity."""

import pytest

from p3.config import LoadCatalog
from p3.pricing import cpp
from p3.pricing.service import PricingService
from tests.conftest import DevProfile, ShippedProfile

Catalog = LoadCatalog()

# Reference values produced by Pipeline 2's browser calculator (app/static/js/xjet-calc.js
# calculatePart, revision 1e871734) run read-only under Node on 2026-09-29, volume 1.0 cm³.
P2BrowserPrices = {
    ("Stainless Steel", 22, 22, 8): 112.86502993248342,
    ("Stainless Steel", 18, 20, 3): 85.12124078687968,
    ("Sterling Silver 925", 22, 22, 8): 271.799265813999,
    ("Sterling Silver 925", 18, 20, 3): 240.66206325030356,
    ("14K Gold Vermeil", 22, 22, 8): 297.68491017723704,
    ("14K Gold Vermeil", 18, 20, 3): 263.58225975033247,
}


@pytest.mark.parametrize("Key,Expected", list(P2BrowserPrices.items()))
def test_cpp_port_matches_pipeline2_browser_calculator(Key, Expected):
    Metal, X, Y, Z = Key
    assert cpp.CalculateCost(Metal, X, Y, Z, 1.0)["price_usd"] == pytest.approx(Expected, abs=1e-9)


def test_cpp_price_depends_on_reference_dimensions():
    # The reason fixed volume alone does not define a price (spec section 6).
    A = cpp.CalculateCost("Sterling Silver 925", 22, 22, 8, 1.0)["price_usd"]
    B = cpp.CalculateCost("Sterling Silver 925", 18, 20, 3, 1.0)["price_usd"]
    assert A != pytest.approx(B)


def test_shipped_profile_makes_fashion_unavailable_not_zero():
    Svc = PricingService(Catalog, ShippedProfile, AllowUnapproved=False)
    for Mid in ("stainless_steel", "silver", "vermeil"):
        Q = Svc.QuoteFor(Mid)
        assert Q.pricing_status == "unavailable" and Q.unit_price is None
    # Even when unapproved profiles are allowed, "unconfigured" never yields a number.
    Q = PricingService(Catalog, ShippedProfile, AllowUnapproved=True).QuoteFor("silver")
    assert Q.pricing_status == "unavailable" and Q.unavailable_reason == "pricing_not_configured"


def test_unapproved_profile_requires_explicit_opt_in():
    assert PricingService(Catalog, DevProfile, AllowUnapproved=False).QuoteFor("silver").unavailable_reason \
        == "pricing_profile_unapproved"
    Q = PricingService(Catalog, DevProfile, AllowUnapproved=True).QuoteFor("silver")
    assert Q.pricing_status == "available" and Q.unit_price == pytest.approx(271.80)
    assert Q.assumed_volume_cm3 == 1.0 and Q.currency == "USD" and not Q.profile_approved
    assert Q.estimated_weight_g == pytest.approx(10.36)
    assert any("Unapproved" in N for N in Q.notes)


def test_luxury_is_never_priced():
    Svc = PricingService(Catalog, DevProfile, AllowUnapproved=True)
    for Mid in [M.Id for M in Catalog.Materials.values() if M.Group == "luxury"]:
        Q = Svc.QuoteFor(Mid)
        assert Q.pricing_status == "unavailable" and Q.unit_price is None
        assert Q.unavailable_reason == "luxury_pricing_unavailable"


def _Profile(**Materials):
    return {"version": "t1", "approved": True, "currency": "USD", "assumed_volume_cm3": 1.0,
            "materials": Materials}


def test_fixed_price_profile_and_placeholder_rejection(WriteProfile):
    Svc = PricingService(Catalog, WriteProfile(_Profile(silver={"method": "fixed", "unit_price": 129.0},
                                                        stainless_steel={"method": "fixed", "unit_price": 0})),
                         AllowUnapproved=False)
    assert Svc.QuoteFor("silver").unit_price == 129.0 and Svc.QuoteFor("silver").profile_approved
    Zero = Svc.QuoteFor("stainless_steel")
    assert Zero.pricing_status == "unavailable" and Zero.unit_price is None


def test_approved_vermeil_must_state_plating(WriteProfile):
    Missing = PricingService(Catalog, WriteProfile(_Profile(vermeil={"method": "fixed", "unit_price": 100})), False)
    assert Missing.QuoteFor("vermeil").unavailable_reason == "plating_cost_not_configured"
    With = PricingService(Catalog, WriteProfile(_Profile(vermeil={"method": "fixed", "unit_price": 100,
                                                                  "plating_cost_usd": 25})), False)
    assert With.QuoteFor("vermeil").unit_price == 125.0


@pytest.mark.parametrize("Bad", [
    {"version": "x", "approved": True, "assumed_volume_cm3": 2.0, "materials": {}},
    {"approved": True, "assumed_volume_cm3": 1.0, "materials": {}},
])
def test_invalid_profile_is_unavailable_not_crash(WriteProfile, Bad):
    Q = PricingService(Catalog, WriteProfile(Bad), True).QuoteFor("silver")
    assert Q.pricing_status == "unavailable" and Q.unavailable_reason == "pricing_profile_invalid"


def test_cpp_reference_without_dimensions_is_unavailable(WriteProfile):
    Svc = PricingService(Catalog, WriteProfile(_Profile(silver={"method": "cpp_reference"})), False)
    assert Svc.QuoteFor("silver").unavailable_reason == "pricing_profile_invalid"
