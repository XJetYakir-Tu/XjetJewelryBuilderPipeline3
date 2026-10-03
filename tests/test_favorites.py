"""♥ Favorites: a customer's saved references to gallery masters, per account — never a copy, never a My Design."""

from tests.test_gallery import HG, Admin, _Curated  # noqa: F401 — the fixture is used by name


async def test_favorites_are_saved_references_to_gallery_masters_per_account(HG):
    H = HG
    Did, Cand, Item = await _Curated(H)
    Tiles = (await H.Client.get("/api/gallery", headers={"X-Access-Token": "NOPE00"})).json()["items"]
    assert Tiles[0]["design_id"] == Did and set(Tiles[0]) == {"id", "design_id", "title", "image_url"}
    Other, _ = H.Ctx.Accounts.IssueToken("other")
    Me = {"X-Access-Token": Other}
    assert (await H.Client.get("/api/favorites", headers={"X-Access-Token": "NOPE00"})).status_code in (401, 403)
    assert (await H.Client.get("/api/favorites", headers=Me)).json()["items"] == []
    R = await H.Client.put(f"/api/favorites/{Did}", headers=Me)
    assert R.status_code == 200, R.text
    assert [F["design_id"] for F in R.json()["items"]] == [Did]
    assert R.json()["items"][0]["id"] == Item["id"] and R.json()["items"][0]["title"] == Tiles[0]["title"]
    assert len((await H.Client.put(f"/api/favorites/{Did}", headers=Me)).json()["items"]) == 1      # idempotent
    # no My Design, no copy, no link: favorites live beside the designs
    assert (await H.Client.get("/api/designs", headers=Me)).json()["designs"] == []
    assert H.Ctx.Db.One("SELECT COUNT(*) AS n FROM designs")["n"] == 1
    assert H.Ctx.Db.One("SELECT COUNT(*) AS n FROM gallery_uses")["n"] == 0
    # each account sees its own list only
    assert (await H.Client.get("/api/favorites")).json()["items"] == []
    # newest first when a second design is saved
    B2 = await H.NewDesign("A plain polished band")
    await H.Client.put(f"/api/designs/{B2['design_id']}/selection", json={"candidate_id": B2["candidates"][1]["id"]})
    I2 = (await H.Client.post("/api/admin/gallery", json={"design_id": B2["design_id"]}, headers=Admin)).json()
    Fav = (await H.Client.put(f"/api/favorites/{B2['design_id']}", headers=Me)).json()["items"]
    assert [F["design_id"] for F in Fav] == [B2["design_id"], Did]
    # Make it yours on a favorite still links to My Designs as usual — the same master — and the favorite stays
    R = await H.Client.post(f"/api/gallery/{Item['id']}/start", json={}, headers=Me)
    assert R.status_code == 200 and R.json()["id"] == Did
    assert [D["id"] for D in (await H.Client.get("/api/designs", headers=Me)).json()["designs"]] == [Did]
    assert len((await H.Client.get("/api/favorites", headers=Me)).json()["items"]) == 2
    assert H.Ctx.Db.One("SELECT COUNT(*) AS n FROM designs WHERE source_design_id IS NOT NULL")["n"] == 0   # never a copy
    # only gallery masters can be favorites
    B3 = await H.NewDesign("Not in the gallery")
    assert (await H.Client.put(f"/api/favorites/{B3['design_id']}", headers=Me)).status_code == 404
    assert (await H.Client.put("/api/favorites/dsg_nope", headers=Me)).status_code == 404
    # removed from the gallery → hidden from favorites; back in the gallery → back in favorites (same master)
    await H.Client.delete(f"/api/admin/gallery/{I2['id']}", headers=Admin)
    assert [F["design_id"] for F in (await H.Client.get("/api/favorites", headers=Me)).json()["items"]] == [Did]
    await H.Client.post("/api/admin/gallery", json={"design_id": B2["design_id"]}, headers=Admin)
    assert [F["design_id"] for F in (await H.Client.get("/api/favorites", headers=Me)).json()["items"]] == [B2["design_id"], Did]
    # Admin sees how many customers saved each design
    L = (await H.Client.get("/api/admin/gallery", headers=Admin)).json()["items"]
    assert {X["design_id"]: X["favorites"] for X in L} == {Did: 1, B2["design_id"]: 1}
    # the heart again removes it
    R = await H.Client.delete(f"/api/favorites/{Did}", headers=Me)
    assert [F["design_id"] for F in R.json()["items"]] == [B2["design_id"]]
    assert (await H.Client.delete(f"/api/favorites/{Did}", headers=Me)).status_code == 200     # already gone: fine


async def test_favorites_survive_a_new_sign_in_on_another_device(HG):
    """Account-based, not browser-based: a new sign-in for the same account (another token, another client with
    nothing stored) sees the same list."""
    import httpx
    H = HG
    Did, _, _ = await _Curated(H)
    T1, Who = H.Ctx.Accounts.IssueToken("shopper")
    assert (await H.Client.put(f"/api/favorites/{Did}", headers={"X-Access-Token": T1})).status_code == 200
    T2, Who2 = H.Ctx.Accounts.IssueToken("shopper, phone", AccountId=Who.AccountId)
    assert T2 != T1 and Who2.AccountId == Who.AccountId
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=H.App), base_url="http://p3.test" + H.Base) as Phone:
        Items = (await Phone.get("/api/favorites", headers={"X-Access-Token": T2})).json()["items"]
    assert [F["design_id"] for F in Items] == [Did]
