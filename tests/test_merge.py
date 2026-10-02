"""Legacy gallery copies are merged back into their master: one ring, one Ring ID, one 3D model."""

import json

from p3 import merge as Merge
from p3.db import NewId, Now
from tests.conftest import Harness, MakeLive

AdminKey = "test-admin-key"
Admin = {"Authorization": f"Bearer {AdminKey}"}


async def _Master(H, Prompt="delicate twisted bands connected"):
    """An XJet master design in the gallery with a measured 3D model (mock, no cost)."""
    B = await H.NewDesign(Prompt)
    Did, Cands = B["design_id"], B["candidates"]
    await H.Client.put(f"/api/designs/{Did}/selection", json={"candidate_id": Cands[3]["id"]})
    assert (await H.Client.post("/api/admin/gallery", json={"design_id": Did}, headers=Admin)).status_code == 200
    T = (await H.Client.post(f"/api/admin/sessions/{Did}/3d", json={"production_size": 10, "material_id": "silver"}, headers=Admin)).json()
    await H.Idle()
    return Did, Cands, T


def _LegacyCopy(H, MasterId: str, Owner: str, Title: str = "The Fil Ring") -> tuple[str, dict[str, str]]:
    """What the old 'Make it yours' did: a new design with copies of the master's four images."""
    Db = H.Ctx.Db
    M = Db.One("SELECT * FROM designs WHERE id = ?", (MasterId,))
    Batch = Db.One("SELECT * FROM batches WHERE design_id = ? AND kind = 'initial'", (MasterId,))
    Cands = Db.All("SELECT * FROM candidates WHERE batch_id = ? ORDER BY slot", (Batch["id"],))
    Cid, Bid, T = NewId("dsg"), NewId("bat"), Now()
    Mapping = {}
    with Db.Transaction() as Conn:
        Conn.execute("INSERT INTO designs (id, owner_account_id, title, prompt, created_at, updated_at, ai_mode, source_design_id, "
                     "source_candidate_id) VALUES (?,?,?,?,?,?,?,?,?)",
                     (Cid, Owner, Title, M["prompt"], T, T, "mock", MasterId, M["selected_candidate_id"]))
        Conn.execute("INSERT INTO batches (id, design_id, kind, user_text, effective_prompt, endpoint, desired_count, config_version, "
                     "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     (Bid, Cid, "initial", Batch["user_text"], Batch["effective_prompt"], Batch["endpoint"], 4, Batch["config_version"], T))
        for C in Cands:
            New = NewId("cand")
            Mapping[New] = C["id"]
            Conn.execute("INSERT INTO candidates (id, batch_id, slot, status, seed, asset_path, content_sha256, created_at, updated_at) "
                         "VALUES (?,?,?,?,?,?,?,?,?)", (New, Bid, C["slot"], C["status"], C["seed"], C["asset_path"], C["content_sha256"], T, T))
    Sel = next(K for K, V in Mapping.items() if V == M["selected_candidate_id"])
    Db.Execute("UPDATE designs SET selected_candidate_id = ? WHERE id = ?", (Sel, Cid))
    return Cid, Mapping


async def test_a_customers_legacy_copy_becomes_their_use_of_the_master(tmp_path):
    H = Harness(tmp_path, AdminKey=AdminKey)
    try:
        Did, Cands, T = await _Master(H)
        Owner = H.Ctx.Accounts.IssueToken("customer")[1].AccountId
        Cid, Mapping = _LegacyCopy(H, Did, Owner)
        CopySel = H.Ctx.Db.One("SELECT selected_candidate_id FROM designs WHERE id = ?", (Cid,))["selected_candidate_id"]
        # The copy carried a journey: Customize choices, a bag line, an order
        H.Ctx.Db.Execute("INSERT INTO customizations (id, owner_account_id, design_id, candidate_id, material_id, ring_size, quantity, "
                         "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)", ("cus_copy", Owner, Cid, CopySel, "silver", 10.0, 1, Now(), Now()))
        for K in ("customize_opened", "customization_changed"):
            H.Ctx.Db.Execute("INSERT INTO session_events (design_id, owner_account_id, kind, data_json, created_at) VALUES (?,?,?,?,?)",
                             (Cid, Owner, K, json.dumps({"candidate_id": CopySel, "material_id": "silver"}), Now()))
        Oid = NewId("ord")
        H.Ctx.Db.Execute("INSERT INTO orders (id, owner_account_id, status, payment_status, customer_json, shipping_json, address_validation, "
                         "shipping_method, currency, subtotal, discount, shipping, total, created_at, updated_at) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (Oid, Owner, "new", "pending", '{"first_name":"Dana","last_name":"Levi","email":"dana@example.com"}', "{}",
                          "unverified", "standard", "USD", 200, 0, 0, 200, Now(), Now()))
        H.Ctx.Db.Execute("INSERT INTO order_lines (id, order_id, position, design_id, candidate_id, customization_id, title, ring_id, material_id, "
                         "material_label, ring_size, quantity, unit_price, line_total, currency, pricing_version, image_path) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         ("oln_copy", Oid, 1, Cid, CopySel, "cus_copy", "The Fil Ring", "R-1002-D", "silver", "Silver", 10.0, 1, 200, 200, "USD", "v1", "x.png"))
        MakeLive(H)                                                   # Needs attention lists live sessions only
        # The Admin sees it as a legacy copy with its master as the merge target
        L = (await H.Client.get("/api/admin/sessions", headers=Admin)).json()
        Row = next(X for X in L["sessions"] if X["design_id"] == Cid)
        assert Row["legacy_copy"] and Row["ring_id"] == "R-1002"
        S = (await H.Client.get(f"/api/admin/sessions/{Cid}", headers=Admin)).json()
        assert S["merge"]["master_ring_id"] == "R-1001" and S["merge"]["same_owner"] is False
        A = (await H.Client.get("/api/admin/attention", headers=Admin)).json()
        assert any(I["kind"] == "legacy_copy" and I["ref"] == "R-1002" for I in A["items"])

        R = (await H.Client.post(f"/api/admin/designs/{Cid}/merge", json={}, headers=Admin)).json()
        assert R["master"]["ring_id"] == "R-1001" and R["use_id"] and R["options"]["R-1002-D"] == "R-1001-D"
        assert R["moved"]["order_lines"] == 1 and R["moved"]["customizations"] == 1 and R["moved"]["events"] == 2 and R["orders"] == ["ORD-10001"]
        Db = H.Ctx.Db
        assert Db.One("SELECT COUNT(*) AS n FROM designs WHERE id = ?", (Cid,))["n"] == 0
        assert Db.One("SELECT COUNT(*) AS n FROM candidates c JOIN batches b ON b.id = c.batch_id WHERE b.design_id = ?", (Cid,))["n"] == 0
        # The order line is the master's option now, with the master's model for its size and material
        Line = Db.One("SELECT * FROM order_lines WHERE id = 'oln_copy'")
        assert Line["design_id"] == Did and Line["candidate_id"] == Cands[3]["id"] and Line["ring_id"] == "R-1001-D" and Line["image_path"] != "x.png"
        O = (await H.Client.get(f"/api/admin/orders/{Oid}", headers=Admin)).json()
        assert O["lines"][0]["three_d_match"] and O["lines"][0]["three_d_id"] == T["id"]
        assert any(E["kind"] == "note" and "same ring" in (E["data"].get("note") or "") for E in O["events"])
        # The customer's journey is now their use of the master, with its history
        U = Db.One("SELECT * FROM gallery_uses WHERE id = ?", (R["use_id"],))
        assert U["design_id"] == Did and U["owner_account_id"] == Owner and U["selected_candidate_id"] == Cands[3]["id"]
        S = (await H.Client.get(f"/api/admin/sessions/{R['use_id']}", headers=Admin)).json()
        assert S["session"]["ring_id"] == "R-1001" and S["session"]["shared"] and S["session"]["ordered"]
        assert [E["kind"] for E in S["timeline"] if E["kind"] in ("customize_opened", "legacy_copy_merged")] == ["customize_opened", "legacy_copy_merged"]
        assert Db.One("SELECT candidate_id FROM customizations WHERE id = 'cus_copy'")["candidate_id"] == Cands[3]["id"]
        # R-1002 is retired: the next design is R-1003, and a search for R-1002 is told where it went
        N = await H.NewDesign("a band with a crescent moon")
        assert (await H.Client.get(f"/api/admin/sessions/{N['design_id']}", headers=Admin)).json()["session"]["ring_id"] == "R-1003"
        L = (await H.Client.get("/api/admin/sessions", headers=Admin)).json()
        assert L["retired"] == [{"ring_id": "R-1002", "title": "The Fil Ring", "design_id": Cid, "retired_at": L["retired"][0]["retired_at"],
                                 "merged_into": {"design_id": Did, "ring_id": "R-1001", "title": L["retired"][0]["merged_into"]["title"]}}]
        assert (await H.Client.post(f"/api/admin/designs/{Cid}/merge", json={}, headers=Admin)).status_code == 404
    finally:
        await H.Close()


async def test_the_owners_own_copy_folds_into_the_master_session(tmp_path):
    H = Harness(tmp_path, AdminKey=AdminKey)
    try:
        Did, Cands, _ = await _Master(H)
        Owner = H.Who.AccountId                                     # XJet copied its own gallery design
        Cid, Mapping = _LegacyCopy(H, Did, Owner, "Fil Line")
        CopySel = H.Ctx.Db.One("SELECT selected_candidate_id FROM designs WHERE id = ?", (Cid,))["selected_candidate_id"]
        # A duplicate movie and an older Customize choice on the copy; the master keeps its own
        Movie = H.Ctx.Db.One("SELECT * FROM movies WHERE candidate_id = ?", (Cands[3]["id"],))
        if Movie is None:
            await H.Proceed(Did, Cands[3]["id"])
            await H.Idle()
            Movie = H.Ctx.Db.One("SELECT * FROM movies WHERE candidate_id = ? AND status = 'ready'", (Cands[3]["id"],))
        H.Ctx.Db.Execute("INSERT INTO movies (id, candidate_id, config_version, endpoint, status, asset_path, created_at, updated_at) "
                         "VALUES (?,?,?,?,?,?,?,?)", ("mov_copy", CopySel, Movie["config_version"], Movie["endpoint"], "ready", "copy.mp4", Now(), Now()))
        H.Ctx.Db.Execute("INSERT INTO customizations (id, owner_account_id, design_id, candidate_id, material_id, ring_size, quantity, created_at, "
                         "updated_at) VALUES (?,?,?,?,?,?,?,?,?)", ("cus_copy", Owner, Cid, CopySel, "vermeil", 9.0, 1, "2020-01-01", "2020-01-01"))
        R = (await H.Client.post(f"/api/admin/designs/{Cid}/merge", json={}, headers=Admin)).json()
        assert R["same_owner"] and R["use_id"] is None and R["session_id"] == Did
        assert [X["movie_id"] for X in R["movies_removed"]] == ["mov_copy"] and R["moved"]["movies"] == 0
        Db = H.Ctx.Db
        assert Db.One("SELECT COUNT(*) AS n FROM movies WHERE candidate_id = ?", (Cands[3]["id"],))["n"] == 1
        Keep = Db.One("SELECT * FROM customizations WHERE owner_account_id = ? AND design_id = ? AND candidate_id = ?", (Owner, Did, Cands[3]["id"]))
        assert Keep["id"] != "cus_copy" and Keep["material_id"] != "vermeil"            # the newer master choice won
        assert Db.One("SELECT COUNT(*) AS n FROM gallery_uses")["n"] == 0
        S = (await H.Client.get(f"/api/admin/sessions/{Did}", headers=Admin)).json()
        assert any(E["kind"] == "legacy_copy_merged" and E["data"]["copy_ring_id"] == "R-1002" for E in S["timeline"])
        assert S["merge"] is None
    finally:
        await H.Close()


async def test_only_true_copies_are_merged(tmp_path):
    H = Harness(tmp_path, AdminKey=AdminKey)
    try:
        Did, Cands, _ = await _Master(H)
        # a plain design is not a copy
        Other = await H.NewDesign("a bold signet")
        R = await H.Client.post(f"/api/admin/designs/{Other['design_id']}/merge", json={}, headers=Admin)
        assert R.status_code == 409 and R.json()["error"]["code"] == "not_a_copy"
        # a customer's refinement (a variation with images of its own) is not a copy either
        Cust = {"X-Access-Token": H.Ctx.Accounts.IssueToken("customer")[0]}
        Item = (await H.Client.get("/api/gallery")).json()["items"][0]
        await H.Client.post(f"/api/gallery/{Item['id']}/start", json={}, headers=Cust)
        Fork = (await H.Client.post(f"/api/designs/{Did}/batches", json={"parent_candidate_id": Cands[0]["id"], "instruction": "thinner"},
                                    headers=Cust)).json()
        await H.Idle()
        R = await H.Client.post(f"/api/admin/designs/{Fork['design_id']}/merge", json={}, headers=Admin)
        assert R.status_code == 409 and R.json()["error"]["code"] == "not_a_copy"
        assert Merge.LegacyCopies(H.Ctx) == []
        assert (await H.Client.get(f"/api/admin/sessions/{Fork['design_id']}", headers=Admin)).json()["merge"] is None
    finally:
        await H.Close()
