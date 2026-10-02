"""Admin operations: the Needs Attention queue, the dashboard time range, unique design names,
searchable identifiers on sessions."""

import pytest

from tests.conftest import Harness, MakeLive

AdminKey = "ops-admin-key"
Admin = {"Authorization": f"Bearer {AdminKey}"}
Customer = {"first_name": "Dana", "last_name": "Levi", "email": "dana@example.com", "phone": "+972 54 123 4567"}
Address = {"recipient": "Dana Levi", "line1": "12 Rothschild Blvd", "city": "Tel Aviv", "postal_code": "6688112", "country": "IL"}


@pytest.fixture
async def HX(tmp_path):
    Obj = Harness(tmp_path, AdminKey=AdminKey)
    yield Obj
    await Obj.Close()


async def _Order(H, Prompt="Aurora twist band"):
    Batch = await H.NewDesign(Prompt)
    Cand = Batch["candidates"][0]
    Cus = (await H.Client.post(f"/api/designs/{Batch['design_id']}/customize", json={"candidate_id": Cand["id"]})).json()
    await H.Idle()
    await H.Client.patch(f"/api/customizations/{Cus['id']}", json={"ring_size": 7})
    await H.Client.post("/api/bag", json={"customization_id": Cus["id"]})
    R = await H.Client.post("/api/orders", json={"customer": Customer, "address": Address, "shipping_method": "standard",
                                                 "terms_accepted": True, "client_request_id": "c-" + Prompt})
    assert R.status_code == 200, R.text
    return Batch["design_id"], R.json()


async def test_needs_attention_lists_what_needs_a_human(HX, monkeypatch):
    H = HX
    from p3 import production3d
    Did, O = await _Order(H)
    monkeypatch.setattr(production3d, "MaxRoundness", -1.0)             # the 3D result gets flagged for review
    T = (await H.Client.post(f"/api/admin/sessions/{Did}/3d", json={}, headers=Admin)).json()
    await H.Idle()
    Did2 = (await H.NewDesign("Signet ring"))["design_id"]
    Q = await H.Client.post("/api/quote-requests", json={"design_id": Did2, "candidate_id": (await H.Design(Did2))["batches"][0]["candidates"][0]["id"],
                                                         "material_id": "gold_14k_yellow", "ring_size": 9, "quantity": 1, "customer": Customer})
    assert Q.status_code == 200
    A = (await H.Client.get("/api/admin/attention", headers=Admin)).json()
    assert A["count"] == 1 and A["items"][0]["kind"] == "quote_request"        # mock sessions/orders are left out
    MakeLive(H)
    A = (await H.Client.get("/api/admin/attention", headers=Admin)).json()
    Kinds = {I["kind"] for I in A["items"]}
    assert {"3d_review", "order_new", "quote_request"} <= Kinds
    Review = next(I for I in A["items"] if I["kind"] == "3d_review")
    assert Review["severity"] == "warn" and Review["href"] == f"#/sessions/{Did}" and Review["ref"] == "R-1001"
    New = next(I for I in A["items"] if I["kind"] == "order_new")
    assert New["href"] == f"#/orders/{O['id']}" and New["ref"] == "ORD-10001" and New["customer"] == "Dana Levi"
    assert Did in A["sessions"]
    assert [I["severity"] for I in A["items"]] == sorted([I["severity"] for I in A["items"]], key=lambda S: {"error": 0, "warn": 1, "info": 2}[S])
    # A failed payment and an old pending payment are attention items; a paid, completed order is not
    H.Ctx.Db.Execute("UPDATE orders SET created_at = '2026-01-01T00:00:00.000+00:00'")
    A = (await H.Client.get("/api/admin/attention", headers=Admin)).json()
    assert "payment_pending" in A["by_kind"]
    await H.Client.post(f"/api/admin/orders/{O['id']}/payment", json={"status": "failed", "note": "card declined"}, headers=Admin)
    A = (await H.Client.get("/api/admin/attention", headers=Admin)).json()
    assert "payment_failed" in A["by_kind"] and "payment_pending" not in A["by_kind"]
    for S in ("payment_confirmed", "three_d_ready", "production", "qc", "shipped", "completed"):
        await H.Client.post(f"/api/admin/orders/{O['id']}/status", json={"status": S}, headers=Admin)
    A = (await H.Client.get("/api/admin/attention", headers=Admin)).json()
    assert not {K for K in A["by_kind"] if K.startswith("payment") or K.startswith("order")}
    Dash = (await H.Client.get("/api/admin/dashboard", headers=Admin)).json()
    assert Dash["needs_attention"]["count"] == A["count"] and Dash["needs_attention"]["items"][0]["label"]


async def test_dashboard_time_range(HX):
    H = HX
    await _Order(H, "Old band")
    MakeLive(H)
    H.Ctx.Db.Execute("UPDATE designs SET created_at = '2026-01-01T00:00:00.000+00:00'")
    H.Ctx.Db.Execute("UPDATE orders SET created_at = '2026-01-01T00:00:00.000+00:00'")
    All = (await H.Client.get("/api/admin/dashboard", headers=Admin)).json()
    assert All["sessions"] == 1 and All["orders"]["orders"] == 1 and All["range"] == {"days": None, "since": None}
    Week = (await H.Client.get("/api/admin/dashboard?days=7", headers=Admin)).json()
    assert Week["sessions"] == 0 and Week["orders"]["orders"] == 0 and Week["range"]["days"] == 7 and Week["range"]["since"]
    assert Week["geometry"] == All["geometry"]                              # model statistics stay all-time
    assert (await H.Client.get("/api/admin/dashboard?days=0", headers=Admin)).json()["range"]["days"] is None


async def test_gallery_master_names_are_distinctive(HX):
    H = HX
    A = await H.NewDesign("Aurora ring")
    B = await H.NewDesign("Aurora ring again")
    for Batch in (A, B):
        await H.Client.put(f"/api/designs/{Batch['design_id']}/selection", json={"candidate_id": Batch["candidates"][0]["id"]})
        assert (await H.Client.post("/api/admin/gallery", json={"design_id": Batch["design_id"]}, headers=Admin)).status_code == 200
    R = await H.Client.patch(f"/api/admin/designs/{B['design_id']}", json={"title": "Aurora Twist"}, headers=Admin)
    assert R.status_code == 200 and R.json()["title"] == "Aurora Twist"
    assert (await H.Client.patch(f"/api/admin/designs/{B['design_id']}", json={"title": "x"}, headers=Admin)).status_code == 400
    assert (await H.Client.patch(f"/api/admin/designs/{B['design_id']}", json={"title": "Aurora Twist"})).status_code == 403
    # The same name as another gallery master is refused unless forced, and flagged in the gallery list
    Dup = await H.Client.patch(f"/api/admin/designs/{A['design_id']}", json={"title": " aurora  twist "}, headers=Admin)
    assert Dup.status_code == 409 and Dup.json()["error"]["code"] == "duplicate_title" and "R-1002" in Dup.json()["error"]["message"]
    assert (await H.Client.patch(f"/api/admin/designs/{A['design_id']}", json={"title": "Aurora Twist", "force": True}, headers=Admin)).status_code == 200
    G = (await H.Client.get("/api/admin/gallery", headers=Admin)).json()["items"]
    assert all(X["duplicate_name"] for X in G) and all(X["title"] == "Aurora Twist" for X in G)
    await H.Client.patch(f"/api/admin/designs/{A['design_id']}", json={"title": "Aurora Halo"}, headers=Admin)
    G = (await H.Client.get("/api/admin/gallery", headers=Admin)).json()["items"]
    assert not any(X["duplicate_name"] for X in G)
    Public = (await H.Client.get("/api/gallery")).json()["items"]
    assert {X["title"] for X in Public} == {"Aurora Halo", "Aurora Twist"}
    S = (await H.Client.get(f"/api/admin/sessions/{A['design_id']}", headers=Admin)).json()
    assert S["session"]["title"] == "Aurora Halo" and any(E["kind"] == "admin_design_renamed" for E in S["timeline"])
    assert (await H.Client.patch("/api/admin/designs/dsg_nope", json={"title": "Nope"}, headers=Admin)).status_code == 404


async def test_sessions_carry_every_searchable_identifier(HX):
    H = HX
    Did, O = await _Order(H, "Aurora twist band")
    Rows = (await H.Client.get("/api/admin/sessions?include_mock=true", headers=Admin)).json()["sessions"]
    X = next(R for R in Rows if R["session_id"] == Did)
    assert X["ring_id"] == "R-1001" and X["selected_ring_id"] == "R-1001-A"
    assert X["option_ring_ids"] == ["R-1001-A", "R-1001-B", "R-1001-C", "R-1001-D"]
    assert X["order_refs"] == ["ORD-10001"] and X["ordered"] and X["legacy_copy"] is False
