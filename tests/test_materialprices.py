"""Admin → AI Prompts & Params → Pricing · materials: one versioned table for density, price $/g,
cost $/g and the website's fixed price."""

import pytest

from tests.conftest import Harness

AdminKey = "materials-admin-key"
Admin = {"Authorization": f"Bearer {AdminKey}"}


@pytest.fixture
async def HM(tmp_path):
    Obj = Harness(tmp_path, AdminKey=AdminKey)
    yield Obj
    await Obj.Close()


async def test_seeded_table_matches_the_business_table(HM):
    R = await HM.Client.get("/api/admin/material-prices", headers=Admin)
    assert R.status_code == 200
    Rows = {X["id"]: X for X in R.json()["rows"]}
    assert (Rows["silver"]["density_g_cm3"], Rows["silver"]["price_per_g"], Rows["silver"]["cost_per_g"],
            Rows["silver"]["fixed_price"]) == (10.36, 35, 16, 200)
    assert (Rows["stainless_steel"]["price_per_g"], Rows["stainless_steel"]["cost_per_g"], Rows["stainless_steel"]["fixed_price"]) == (25, 11, 90)
    assert Rows["gold_18k_yellow"]["price_per_g"] == 400 and Rows["gold_18k_yellow"]["fixed_price"] is None   # NA
    assert Rows["gold_14k_rose"]["cost_per_g"] == 135 and Rows["gold_14k_rose"]["density_g_cm3"] == 13.7
    assert (await HM.Client.get("/api/admin/material-prices")).status_code == 403


async def test_fixed_price_is_the_website_price_and_edits_are_versioned(HM):
    Q = (await HM.Client.get("/api/quote", params={"material_id": "silver"})).json()
    assert Q["unit_price"] == 200 and Q["pricing_status"] == "available" and Q["pricing_version"].startswith("materials-v")
    Doc = (await HM.Client.get("/api/admin/material-prices", headers=Admin)).json()
    Mats = {X["id"]: {K: X[K] for K in ("density_g_cm3", "price_per_g", "cost_per_g", "fixed_price")} for X in Doc["rows"]}
    Mats["silver"]["fixed_price"] = 210
    Mats["silver"]["density_g_cm3"] = 10.4
    R = await HM.Client.put("/api/admin/material-prices", json={"materials": Mats, "note": "silver up"}, headers=Admin)
    assert R.status_code == 200 and R.json()["version"] != Doc["version"] and R.json()["update_note"] == "silver up"
    Q = (await HM.Client.get("/api/quote", params={"material_id": "silver"})).json()
    assert Q["unit_price"] == 210 and Q["estimated_weight_g"] == 10.4                  # density applies too
    assert HM.Ctx.Catalog.Get("silver").DensityGCm3 == 10.4
    Lux = (await HM.Client.get("/api/quote", params={"material_id": "gold_18k_yellow"})).json()
    assert Lux["pricing_status"] == "unavailable" and Lux["unit_price"] is None        # NA stays unavailable
    Mats["silver"]["cost_per_g"] = -1
    Bad = await HM.Client.put("/api/admin/material-prices", json={"materials": Mats}, headers=Admin)
    assert Bad.status_code == 400 and "greater than 0" in Bad.json()["error"]["message"]
