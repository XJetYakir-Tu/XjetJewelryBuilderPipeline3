"""Sessions (one design journey each), stage tracking, admin Generate 3D, ring geometry."""

import io
import json
import math

import numpy as np
import pytest

from p3.geometry import MeasureRing, UsSizeToInnerDiameterMm
from p3.providers import endpoints
from tests.conftest import Harness, MakeLive

AdminKey = "sessions-admin-key"
Admin = {"Authorization": f"Bearer {AdminKey}"}


@pytest.fixture
async def HS(tmp_path):
    Obj = Harness(tmp_path, AdminKey=AdminKey)
    yield Obj
    await Obj.Close()


async def _Session(H, DesignId):
    return (await H.Client.get(f"/api/admin/sessions/{DesignId}", headers=Admin)).json()


async def _Journey(H, Refine=True, Bag=True):
    Batch = await H.NewDesign("Signet ring with a hexagon face")
    Did, Sel = Batch["design_id"], Batch["candidates"][0]
    await H.Client.put(f"/api/designs/{Did}/selection", json={"candidate_id": Sel["id"]})
    if Refine:
        R = await H.Client.post(f"/api/designs/{Did}/batches", json={"parent_candidate_id": Sel["id"], "instruction": "Thinner band"})
        assert R.status_code == 200
        await H.Idle()
    Cust = await H.Proceed(Did, Sel["id"])
    await H.Idle()
    await H.Client.patch(f"/api/customizations/{Cust['id']}", json={"material_id": "silver"})
    await H.Client.patch(f"/api/customizations/{Cust['id']}", json={"ring_size": 7})
    if Bag:
        R = await H.Client.post("/api/bag", json={"customization_id": Cust["id"]})
        assert R.status_code == 200, R.text
    return Did, Sel, Cust


async def test_full_journey_is_tracked_with_stage_times(HDevPricing):
    H = HDevPricing
    Did, _, _ = await _Journey(H)
    await H.Client.post("/api/events", json={"kind": "bag_viewed"})
    await H.Client.post("/api/events", json={"kind": "checkout_clicked"})
    S = H.App.state.Ctx
    from p3 import sessions as Sessions
    X = Sessions.Summaries(S, [Did])[0]
    assert X["path"] == "Generated → Refined → Customize → Bag → Checkout Clicked"
    assert X["stage_reached"] == "checkout_clicked" and X["add_to_bag"] and X["checkout_clicked"]
    assert all(X["stage_times"][K] for K in ("started", "generated", "customize", "bag", "checkout_clicked"))
    assert X["stage_times"]["started"] <= X["stage_times"]["generated"] <= X["stage_times"]["customize"] <= X["stage_times"]["bag"]
    assert (X["generations"], X["refinements"], X["ring_size"], X["material_id"], X["material_chosen"]) == (1, 1, 7.0, "silver", True)
    assert X["fixed_price"]["source"] == "bag_snapshot" and X["fixed_price"]["unit_price"] is not None
    assert X["state"] == "active" and X["three_d_status"] is None


async def test_session_that_stops_before_bag_and_ends_on_new_design(HS):
    H = HS
    Did, _, _ = await _Journey(H, Refine=False, Bag=False)
    D = await _Session(H, Did)
    assert D["session"]["path"] == "Generated → Customize" and D["session"]["state"] == "active"
    await H.NewDesign("A second, different ring")                       # another New Design ends it
    D = await _Session(H, Did)
    assert D["session"]["state"] == "ended" and D["session"]["end_reason"] == "new_design"
    assert D["session"]["path"] == "Generated → Customize → stopped" and not D["session"]["add_to_bag"]
    Kinds = [E["kind"] for E in D["timeline"]]
    assert Kinds[0] == "started" and "generated" in Kinds and "customize_opened" in Kinds
    assert [C["ring_size"] for C in D["choices"] if "ring_size" in C][-1] == 7.0         # choice history kept
    assert D["choices"][0]["kind"] == "customize_opened" and "unit_price" in D["choices"][0]   # fixed price shown


async def test_idle_session_ends_and_client_events_are_validated(HS):
    H = HS
    Batch = await H.NewDesign("Plain band")
    Did = Batch["design_id"]
    Old = "2020-01-01T00:00:00.000+00:00"
    for T in ("designs", "candidates", "batches"):
        H.Ctx.Db.Execute(f"UPDATE {T} SET created_at = ?" + (", updated_at = ?" if T != "batches" else ""),
                         (Old, Old) if T != "batches" else (Old,))
    S = (await _Session(H, Did))["session"]
    assert S["state"] == "ended" and S["end_reason"] == "idle" and S["path"] == "Generated → stopped"
    assert (await H.Client.post("/api/events", json={"kind": "hack"})).status_code == 400
    assert (await H.Client.post("/api/events", json={"kind": "design_opened", "design_id": "dsg_nope"})).status_code == 404
    assert (await H.Client.post("/api/events", json={"kind": "new_design_clicked"})).json()["ok"]
    assert (await H.Client.post("/api/events", json={"kind": "design_opened", "design_id": Did})).json()["ok"]
    Dash = (await H.Client.get("/api/admin/dashboard", headers=Admin)).json()
    assert Dash["sessions"] == 0 and Dash["new_design_clicks"] == 0 and Dash["mock_excluded"]   # mock mode: not counted
    MakeLive(H)
    H.Ctx.Db.Execute("UPDATE session_events SET data_json = json_set(data_json, '$.ai_mode', 'live')")
    Dash = (await H.Client.get("/api/admin/dashboard", headers=Admin)).json()
    assert Dash["sessions"] == 1 and Dash["new_design_clicks"] == 1
    assert [F["sessions"] for F in Dash["funnel"]] == [1, 1, 0, 0, 0]
    assert (await H.Client.get("/api/admin/dashboard")).status_code == 403


async def test_3d_never_runs_automatically_and_uses_default_size_10(HS):
    H = HS
    Batch = await H.NewDesign("Plain band")
    Did = Batch["design_id"]
    Sel = Batch["candidates"][0]
    Cust = await H.Proceed(Did, Sel["id"])                              # customer reaches Customize, no size
    await H.Idle()
    assert H.Ctx.Db.One("SELECT COUNT(*) AS n FROM meshes")["n"] == 0    # nothing automatic
    assert (await H.Client.post(f"/api/admin/sessions/{Did}/3d", json={})).status_code == 403   # customer can't
    R = await H.Client.post(f"/api/admin/sessions/{Did}/3d", json={}, headers=Admin)
    assert R.status_code == 200, R.text
    T = R.json()
    assert (T["customer_size"], T["production_size"], T["size_source"]) == (None, 10.0, "default")
    assert T["material_source"] == "default" and T["status"] == "generating"
    await H.Idle()
    T = (await _Session(H, Did))["three_d"][0]
    assert T["status"] == "measured", T
    Prod = T["geometry"]["production"]
    assert math.isclose(Prod["inner_diameter_mm"], UsSizeToInnerDiameterMm(10), rel_tol=0.01)
    assert Prod["watertight"] and Prod["volume_mm3"] > 0 and Prod["surface_area_mm2"] > 0
    assert Prod["size_z_mm"] < Prod["size_x_mm"]                         # ring axis aligned to Z
    Price = T["price"]
    assert math.isclose(Price["weight_g"], Prod["volume_mm3"] / 1000 * Price["density_g_cm3"], rel_tol=1e-3)
    assert Price["status"] == "cost_model_not_configured" and Price["production_cost"] is None
    assert Price["calculated_price"] is None
    # STL of the scaled geometry downloads (admin only).
    assert (await H.Client.get(f"/api/admin/3d/{T['id']}/stl/production")).status_code == 403
    Stl = await H.Client.get(f"/api/admin/3d/{T['id']}/stl/production", headers=Admin)
    assert Stl.status_code == 200 and len(Stl.content) > 1000
    # The customer's price is untouched by 3D.
    assert (await H.Client.get(f"/api/customizations/{Cust['id']}")).json()["quote"] == Cust["quote"]


async def test_customer_size_admin_override_and_raw_mesh_reuse(HS):
    H = HS
    Did, Sel, Cust = await _Journey(H, Refine=False, Bag=False)            # customer chose US 7, silver
    T1 = (await H.Client.post(f"/api/admin/sessions/{Did}/3d", json={}, headers=Admin)).json()
    assert (T1["customer_size"], T1["production_size"], T1["size_source"]) == (7.0, 7.0, "customer")
    assert (T1["material_id"], T1["material_source"]) == ("silver", "customer")
    await H.Idle()
    Subs = len([S for S in H.Provider.Submissions if S[0] == endpoints.Mesh])
    assert Subs == 1
    T2 = (await H.Client.post(f"/api/admin/sessions/{Did}/3d", json={"production_size": 9, "material_id": "stainless_steel"},
                              headers=Admin)).json()
    assert (T2["customer_size"], T2["production_size"], T2["size_source"]) == (7.0, 9.0, "admin_override")
    assert T2["material_source"] == "admin_override" and T2["mesh_id"] == T1["mesh_id"]
    await H.Idle()
    assert len([S for S in H.Provider.Submissions if S[0] == endpoints.Mesh]) == Subs   # no new paid Hi3D call
    D = await _Session(H, Did)
    A, B = D["three_d"][1], D["three_d"][0]                               # newest first
    assert A["geometry"]["production"]["inner_diameter_mm"] < B["geometry"]["production"]["inner_diameter_mm"]
    assert D["session"]["three_d_status"] == "measured"
    assert (await H.Client.post(f"/api/admin/sessions/{Did}/3d", json={"production_size": 99}, headers=Admin)).status_code == 400
    Kinds = [E["kind"] for E in D["timeline"]]
    assert Kinds.count("admin_3d_requested") == 2 and Kinds.count("admin_3d_measured") == 2


def test_geometry_matches_an_ideal_ring_in_any_orientation():
    import trimesh
    M = trimesh.creation.torus(major_radius=9.0, minor_radius=1.5, major_sections=128, minor_sections=64)
    M.apply_transform(trimesh.transformations.rotation_matrix(0.7, [1, 0.3, 0.2]))
    M.apply_scale(0.037)                                                  # arbitrary units, like Hi3D output
    Buf = io.BytesIO()
    M.export(Buf, file_type="stl")
    Target = UsSizeToInnerDiameterMm(10)
    G = MeasureRing(Buf.getvalue(), "stl", Target)
    assert G.status == "measured" and not G.problems
    S = Target / 15.0                                                     # torus bore is 2*(9-1.5) = 15
    R, r = 9 * S, 1.5 * S
    P = G.production
    assert math.isclose(P.inner_diameter_mm, Target, rel_tol=0.002)
    assert math.isclose(P.volume_mm3, 2 * math.pi ** 2 * R * r * r, rel_tol=0.01)
    assert math.isclose(P.surface_area_mm2, 4 * math.pi ** 2 * R * r, rel_tol=0.01)
    assert math.isclose(P.size_x_mm, 2 * (R + r), rel_tol=0.01) and math.isclose(P.size_z_mm, 2 * r, rel_tol=0.03)


def test_geometry_without_a_bore_needs_review():
    import trimesh
    Buf = io.BytesIO()
    trimesh.creation.box(extents=(10, 10, 2)).export(Buf, file_type="stl")
    G = MeasureRing(Buf.getvalue(), "stl", UsSizeToInnerDiameterMm(7))
    assert G.status == "needs_review" and G.production is None and "bore" in G.problems[0]


def test_us_size_table():
    assert UsSizeToInnerDiameterMm(10) == pytest.approx(19.758, abs=0.01)
    assert UsSizeToInnerDiameterMm(7) == pytest.approx(17.32, abs=0.01)


async def test_session_pipeline_cost_duration_and_flags(HS):
    H = HS
    Did, Sel, Cust = await _Journey(H, Refine=False, Bag=False)
    await H.Client.post(f"/api/admin/sessions/{Did}/3d", json={}, headers=Admin)
    await H.Idle()
    await H.Client.post(f"/api/admin/sessions/{Did}/3d", json={"production_size": 9}, headers=Admin)   # re-measure only
    await H.Idle()
    D = await _Session(H, Did)
    assert D["cost"]["session"] == 0.0                                  # mock requests are free
    assert {S["kind"] for S in D["pipeline"]["steps"]} == {"design", "movie", "3d"}
    assert all(S["duration_s"] is not None and S["duration_s"] >= 0 for S in D["pipeline"]["steps"])
    assert D["session"]["mock"] and D["cost"]["user_sessions"] == 0                 # mock: not in user totals
    # Price the same requests as if they had been live (fal) submissions.
    MakeLive(H)
    D = await _Session(H, Did)
    Steps = {(S["kind"], S["detail"].startswith("reused")): S for S in D["pipeline"]["steps"]}
    assert Steps[("design", False)]["cost"] == pytest.approx(0.60) and Steps[("design", False)]["requests"] == 4
    assert Steps[("movie", False)]["cost"] == pytest.approx(6 * 0.04)                 # 6 s at 768P
    assert Steps[("3d", False)]["cost"] == pytest.approx(90 * 0.02)                   # 2048quality, no texture/PBR
    assert Steps[("3d", True)]["cost"] == 0.0                                         # reused raw mesh
    assert D["cost"]["session"] == pytest.approx(0.60 + 0.24 + 1.80) == pytest.approx(D["cost"]["user_total"])
    assert D["user"]["status"] in ("active", "unused") and D["artifacts"]["image_url"] and D["artifacts"]["movie_url"]
    assert D["last_choice"]["ring_size"] == 7.0 and D["last_choice"]["material_id"] == "silver"
    Row = next(X for X in (await H.Client.get("/api/admin/sessions", headers=Admin)).json()["sessions"] if X["session_id"] == Did)
    assert Row["mock"] is False
    assert (Row["has_image"], Row["has_movie"], Row["has_3d"]) == (True, True, True) and Row["user_status"]


async def test_ai_prices_refresh_and_edit(HS, monkeypatch):
    H = HS
    P = (await H.Client.get("/api/admin/ai-prices", headers=Admin)).json()
    assert P["endpoints"][endpoints.ImageGenerate]["per_image"] == 0.15 and P["fal_key_configured"] is False
    assert (await H.Client.post("/api/admin/ai-prices/refresh", headers=Admin)).status_code == 400    # no key here
    from p3 import aipricing

    class Resp:
        status_code = 200
        def json(self):
            return {"prices": [{"endpoint_id": endpoints.ImageGenerate, "unit_price": 0.2, "unit": "images"},
                               {"endpoint_id": endpoints.Movie, "unit_price": 0.05, "unit": "seconds"}]}
    Seen = {}
    monkeypatch.setattr(aipricing.httpx, "get", lambda Url, params, headers, timeout: Seen.update(h=headers) or Resp())
    App = H.App.state
    Book = aipricing.PriceBook(H.Ctx.Db)
    R = Book.RefreshFromFal("test-key", "admin")
    assert Seen["h"] == {"Authorization": "Key test-key"}
    assert R["endpoints"][endpoints.ImageGenerate]["per_image"] == 0.2
    assert R["endpoints"][endpoints.Movie]["per_second"] == {"480P": 0.05, "768P": 0.08, "1080P": 0.16}   # scaled
    assert "test-key" not in json.dumps(R)
    Bad = await H.Client.put("/api/admin/ai-prices", json={"prices": {"endpoints": {"x": {"per_image": -1}}}}, headers=Admin)
    assert Bad.status_code == 400
    assert (await H.Client.get("/api/admin/ai-prices")).status_code == 403


async def test_mock_sessions_are_hidden_from_the_admin_unless_asked(HS):
    H = HS
    Batch = await H.NewDesign("Plain band")                                 # made in mock mode
    L = (await H.Client.get("/api/admin/sessions", headers=Admin)).json()
    assert L["sessions"] == [] and L["mock_sessions"] == 1
    All = (await H.Client.get("/api/admin/sessions?include_mock=true", headers=Admin)).json()["sessions"]
    assert [X["session_id"] for X in All] == [Batch["design_id"]] and All[0]["mock"]
    D = await _Session(H, Batch["design_id"])                               # still viewable on its own
    assert D["session"]["mock"]
    assert H.Ctx.Db.One("SELECT ai_mode FROM designs")["ai_mode"] == "mock"
    E = H.Ctx.Db.One("SELECT data_json FROM session_events ORDER BY id DESC LIMIT 1")
    assert E is None or json.loads(E["data_json"])["ai_mode"] == "mock"


async def test_measurement_runs_in_a_worker_and_out_of_memory_keeps_the_raw_model(HS, monkeypatch):
    H = HS
    Batch = await H.NewDesign("Plain band")
    Did = Batch["design_id"]
    T = (await H.Client.post(f"/api/admin/sessions/{Did}/3d", json={}, headers=Admin)).json()
    await H.Idle()
    D = (await _Session(H, Did))["three_d"][0]
    assert D["status"] == "measured" and D["raw_available"]                       # measured by the worker process
    for Stage in ("production", "preview", "raw"):
        R = await H.Client.get(f"/api/admin/3d/{T['id']}/stl/{Stage}", headers=Admin)
        assert R.status_code == 200 and len(R.content) > 84, Stage
    # A model too large for the server: only this measurement fails; the raw Hi3D model stays downloadable.
    from p3 import production3d

    async def TooBig(*A, **K):
        raise MemoryError
    monkeypatch.setattr(production3d, "RunMeasurement", TooBig)
    T2 = (await H.Client.post(f"/api/admin/sessions/{Did}/3d", json={"production_size": 7}, headers=Admin)).json()
    await H.Idle()
    D2 = next(X for X in (await _Session(H, Did))["three_d"] if X["id"] == T2["id"])
    assert D2["status"] == "needs_review" and "too large to measure" in D2["error"] and D2["raw_available"]
    assert (await H.Client.get(f"/api/admin/3d/{T2['id']}/stl/raw", headers=Admin)).status_code == 200
    assert (await H.Client.get(f"/api/admin/3d/{T2['id']}/stl/production", headers=Admin)).status_code == 404
    assert (await H.Client.get("/api/health")).status_code == 200                # the server is fine
