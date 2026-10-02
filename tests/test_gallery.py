"""Inspiration Gallery: curated XJet designs on the customer site; "Make it yours" copies the batch."""

import pytest

from tests.conftest import Harness

AdminKey = "gallery-admin-key"
Admin = {"Authorization": f"Bearer {AdminKey}"}


@pytest.fixture
async def HG(tmp_path):
    Obj = Harness(tmp_path, AdminKey=AdminKey)
    yield Obj
    await Obj.Close()


async def _Curated(H):
    """An XJet design (the harness user) with a chosen image, added to the gallery by the admin."""
    Batch = await H.NewDesign("Twisted bands joined by a small knot")
    Did, Cand = Batch["design_id"], Batch["candidates"][2]
    await H.Client.put(f"/api/designs/{Did}/selection", json={"candidate_id": Cand["id"]})
    R = await H.Client.post("/api/admin/gallery", json={"design_id": Did}, headers=Admin)
    assert R.status_code == 200, R.text
    return Did, Cand, R.json()


async def test_gallery_lists_curated_designs_publicly_and_is_curated_by_the_admin(HG):
    H = HG
    assert (await H.Client.get("/api/gallery", headers={"X-Access-Token": "NOPE00"})).json()["items"] == []
    Did, Cand, Item = await _Curated(H)
    Items = (await H.Client.get("/api/gallery", headers={"X-Access-Token": "NOPE00"})).json()["items"]   # no sign-in needed
    assert [I["id"] for I in Items] == [Item["id"]] and Items[0]["image_url"] == Cand["image_url"]
    assert set(Items[0]) == {"id", "title", "image_url"}                     # nothing about the owner leaks
    assert Items[0]["image_url"].startswith(H.Base + "/assets/")
    assert (await H.Client.post("/api/admin/gallery", json={"design_id": Did})).status_code == 403   # customers can't curate
    # Admin: list, reorder, remove
    B2 = await H.NewDesign("Plain polished band")
    await H.Client.put(f"/api/designs/{B2['design_id']}/selection", json={"candidate_id": B2["candidates"][0]["id"]})
    I2 = (await H.Client.post("/api/admin/gallery", json={"design_id": B2["design_id"]}, headers=Admin)).json()
    L = (await H.Client.get("/api/admin/gallery", headers=Admin)).json()["items"]
    assert [X["id"] for X in L] == [Item["id"], I2["id"]] and [X["position"] for X in L] == [1, 2]
    assert L[0]["ring_id"] == "R-1001-C" and L[0]["starts"] == 0 and L[0]["ready"]
    L = (await H.Client.post(f"/api/admin/gallery/{I2['id']}/move", json={"direction": "up"}, headers=Admin)).json()["items"]
    assert [X["id"] for X in L] == [I2["id"], Item["id"]]
    L = (await H.Client.delete(f"/api/admin/gallery/{I2['id']}", headers=Admin)).json()["items"]
    assert [X["id"] for X in L] == [Item["id"]] and L[0]["position"] == 1
    # Changing the image of a design already in the gallery keeps its place
    Other = (await H.Design(Did))["batches"][0]["candidates"][0]
    R = (await H.Client.post("/api/admin/gallery", json={"design_id": Did, "candidate_id": Other["id"]}, headers=Admin)).json()
    assert R["id"] == Item["id"] and R["candidate_id"] == Other["id"] and R["ring_id"] == "R-1001-A"
    # A design without a chosen image cannot be added
    B3 = await H.NewDesign("Signet ring")
    assert (await H.Client.post("/api/admin/gallery", json={"design_id": B3["design_id"]}, headers=Admin)).status_code == 409
    assert (await H.Client.post("/api/admin/gallery", json={"design_id": "dsg_nope"}, headers=Admin)).status_code == 404


async def test_make_it_yours_copies_the_batch_into_the_customers_own_design(HG):
    H = HG
    Did, Cand, Item = await _Curated(H)
    Subs = len(H.Provider.Submissions)
    Token, _ = H.Ctx.Accounts.IssueToken("customer")
    Cust = {"X-Access-Token": Token}
    assert (await H.Client.post(f"/api/gallery/{Item['id']}/start", json={}, headers={"X-Access-Token": "NOPE00"})).status_code == 401
    R = await H.Client.post(f"/api/gallery/{Item['id']}/start", json={"client_request_id": "tap-1"}, headers=Cust)
    assert R.status_code == 200, R.text
    D = R.json()
    assert D["id"] != Did and D["title"] == (await H.Design(Did))["title"] and D["origin"] == "gallery"
    assert D["source_ring_id"] == "R-1001-C"
    B = D["batches"][0]
    assert B["kind"] == "initial" and B["status"] == "complete" and len(B["candidates"]) == 4
    assert all(C["status"] == "ready" for C in B["candidates"])
    Sel = next(C for C in B["candidates"] if C["id"] == D["selected_candidate_id"])
    assert Sel["slot"] == Cand["slot"]                                        # the gallery image stays selected
    assert H.AssetBytes(Sel["image_url"]) == H.AssetBytes(Cand["image_url"])  # same picture, the customer's own file
    assert all(C["image_url"].startswith(f"{H.Base}/assets/designs/{D['id']}/") for C in B["candidates"])
    assert len(H.Provider.Submissions) == Subs                               # nothing generated, nothing charged
    assert (await H.Client.get("/api/session", headers=Cust)).json()["used"] == 0
    # A double tap does not make a second design
    Again = await H.Client.post(f"/api/gallery/{Item['id']}/start", json={"client_request_id": "tap-1"}, headers=Cust)
    assert Again.json()["id"] == D["id"]
    # The customer continues normally: Customize (movie), their own design list; the original is untouched
    Cus = (await H.Client.post(f"/api/designs/{D['id']}/customize", json={"candidate_id": Sel["id"]}, headers=Cust)).json()
    await H.Idle()
    assert Cus["design_id"] == D["id"] and Cus["candidate_id"] == Sel["id"]
    Mine = (await H.Client.get("/api/designs", headers=Cust)).json()
    assert [X["id"] for X in Mine["designs"]] == [D["id"]] if "designs" in Mine else [X["id"] for X in Mine] == [D["id"]]
    assert (await H.Client.get(f"/api/designs/{Did}", headers=Cust)).status_code == 404      # the XJet original stays XJet's
    Orig = await H.Design(Did)
    assert Orig["selected_candidate_id"] == Cand["id"] and len(Orig["batches"]) == 1
    # The Admin sees the origin and counts the start
    assert (await H.Client.get("/api/admin/gallery", headers=Admin)).json()["items"][0]["starts"] == 1
    S = (await H.Client.get(f"/api/admin/sessions/{D['id']}", headers=Admin)).json()
    assert S["session"]["origin"] == "gallery" and S["session"]["source_ring_id"] == "R-1001-C" and S["gallery"] is None
    assert S["session"]["stage_reached"] in ("customize", "generated")
    assert any(E["kind"] == "gallery_started" and E["data"]["source_ring_id"] == "R-1001-C" for E in S["timeline"])
    Step = next(X for X in S["pipeline"]["steps"] if X["kind"] == "design")
    assert Step["requests"] == 0 and (Step["cost"] or 0) == 0                 # the copy cost nothing
    assert (await H.Client.get(f"/api/admin/sessions/{Did}", headers=Admin)).json()["gallery"]["candidate_id"] == Cand["id"]
    # Removing the design from the gallery does not touch the copies
    GalleryId = (await H.Client.get("/api/admin/gallery", headers=Admin)).json()["items"][0]["id"]
    await H.Client.delete(f"/api/admin/gallery/{GalleryId}", headers=Admin)
    assert (await H.Client.get(f"/api/designs/{D['id']}", headers=Cust)).status_code == 200
    assert (await H.Client.post(f"/api/gallery/{Item['id']}/start", json={}, headers=Cust)).status_code == 404
