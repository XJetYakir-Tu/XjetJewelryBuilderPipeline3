"""A design that already has a movie, 3D, order or gallery tile is a master: a refinement of it — even by its
owner — is a new design of its own; a refinement that landed inside a master earlier can be split off."""

import pytest

from p3.providers import endpoints
from tests.conftest import Harness

AdminKey = "fork-admin-key"
Admin = {"Authorization": f"Bearer {AdminKey}"}


@pytest.fixture
async def HF(tmp_path):
    Obj = Harness(tmp_path, AdminKey=AdminKey)
    yield Obj
    await Obj.Close()


async def _Refine(H, Did, Cid, Instruction):
    R = await H.Client.post(f"/api/designs/{Did}/batches", json={"parent_candidate_id": Cid, "instruction": Instruction})
    assert R.status_code == 200, R.text
    await H.Idle()
    return R.json()


async def test_images_only_is_refined_in_place_but_a_movie_makes_it_a_master(HF):
    H = HF
    B = await H.NewDesign("A slim twisted band")
    Did, Cands = B["design_id"], B["candidates"]
    D = await H.Design(Did)
    assert D["refinement_creates_new_design"] is None
    R1 = await _Refine(H, Did, Cands[0]["id"], "thinner")                     # images only: extends the design
    assert R1["design_id"] == Did and len((await H.Design(Did))["batches"]) == 2
    await H.Proceed(Did, R1["candidates"][1]["id"])                             # the 360° movie draws the line
    await H.Idle()
    D = await H.Design(Did)
    assert D["refinement_creates_new_design"] == "movie"
    R2 = await _Refine(H, Did, R1["candidates"][1]["id"], "make it a lattice")
    assert R2["design_id"] != Did and R2["kind"] == "refine" and R2["parent_candidate_id"] == R1["candidates"][1]["id"]
    New = await H.Design(R2["design_id"])
    assert New["title"] != D["title"] and New["title"].split()[0] == D["title"].split()[0]       # the lineage name
    assert New["source_ring_id"] == "R-1001-R1B" and New["origin"] == "prompt" and not New["shared"]
    assert len((await H.Design(Did))["batches"]) == 2                                              # the master is untouched
    # Admin: the new session is the owner's own design, derived from the master, with the reason recorded
    L = (await H.Client.get("/api/admin/sessions?include_mock=true", headers=Admin)).json()["sessions"]
    Row = next(X for X in L if X["design_id"] == R2["design_id"])
    assert Row["origin"] == "prompt" and Row["source_ring_id"] == "R-1001-R1B" and Row["ring_id"] == "R-1002" and not Row["legacy_copy"]
    S = (await H.Client.get(f"/api/admin/sessions/{R2['design_id']}", headers=Admin)).json()
    Ev = next(E for E in S["timeline"] if E["kind"] == "design_forked")
    assert Ev["data"]["reason"] == "movie" and Ev["data"]["source_ring_id"] == "R-1001-R1B"
    assert "Generation failed" not in S["session"]["path"] and S["session"]["stage_times"]["generated"]    # its first batch is the refinement
    # Lineage both ways: the variation points at its master and the option it was refined from; the master lists it
    assert S["lineage"]["source"] == {"design_id": Did, "title": D["title"], "ring_id": "R-1001", "option_ring_id": "R-1001-R1B"}
    M = (await H.Client.get(f"/api/admin/sessions/{Did}", headers=Admin)).json()
    assert M["lineage"]["source"] is None and [V["ring_id"] for V in M["lineage"]["variations"]] == ["R-1002"]
    assert M["lineage"]["variations"][0]["own"] and M["lineage"]["variations"][0]["from_option"] == "R-1001-R1B"
    # One Hi3D model per design still applies: the fork sees the master's model only once the master has one
    assert S["three_d_defaults"]["source_model"] is None


async def test_three_d_orders_and_gallery_also_make_a_master(HF):
    H = HF
    B = await H.NewDesign("A heavy signet")
    Did, Cands = B["design_id"], B["candidates"]
    await H.Client.put(f"/api/designs/{Did}/selection", json={"candidate_id": Cands[0]["id"]})
    assert (await H.Client.post("/api/admin/gallery", json={"design_id": Did}, headers=Admin)).status_code == 200
    assert (await H.Design(Did))["refinement_creates_new_design"] == "gallery"
    assert (await H.Client.delete(f"/api/admin/gallery/{(await H.Client.get('/api/admin/gallery', headers=Admin)).json()['items'][0]['id']}", headers=Admin)).status_code == 200
    assert (await H.Design(Did))["refinement_creates_new_design"] is None
    T = (await H.Client.post(f"/api/admin/sessions/{Did}/3d", json={}, headers=Admin)).json()
    await H.Idle()
    assert T["id"] and (await H.Design(Did))["refinement_creates_new_design"] == "3d"
    R = await _Refine(H, Did, Cands[1]["id"], "add a crest")
    assert R["design_id"] != Did
    S = (await H.Client.get(f"/api/admin/sessions/{R['design_id']}", headers=Admin)).json()
    assert S["three_d_defaults"]["source_model"]["design_id"] == Did                 # the master's model is known to the variation


async def test_a_refinement_inside_a_master_can_be_split_into_its_own_design(HF):
    H = HF
    B = await H.NewDesign("A smooth dome band")
    Did, Cands = B["design_id"], B["candidates"]
    await H.Client.put(f"/api/designs/{Did}/selection", json={"candidate_id": Cands[1]["id"]})
    R1 = await _Refine(H, Did, Cands[1]["id"], "make it lattice")                  # images only → inside the design
    assert R1["design_id"] == Did
    Refined = R1["candidates"][1]["id"]
    # The owner then selected a refined image, made its movie and put it in the bag
    await H.Client.put(f"/api/designs/{Did}/selection", json={"candidate_id": Refined})
    Cus = await H.Proceed(Did, Refined)
    await H.Idle()
    CusId = Cus.get("id") or Cus["customization"]["id"]
    assert (await H.Client.patch(f"/api/customizations/{CusId}", json={"material_id": "silver", "ring_size": 10})).status_code == 200
    assert (await H.Client.post("/api/bag", json={"customization_id": CusId})).status_code == 200
    Db = H.Ctx.Db
    assert Db.One("SELECT COUNT(*) AS n FROM movies WHERE candidate_id = ?", (Refined,))["n"] == 1
    # The admin moves the refinement into a design of its own
    R = await H.Client.post(f"/api/admin/batches/{R1['id']}/split", json={}, headers=Admin)
    assert R.status_code == 200, R.text
    R = R.json()
    assert R["ring_id"] == "R-1002" and R["title"].split()[0] == (await H.Design(Did))["title"].split()[0]
    assert R["moved"]["batches"] == 1 and R["moved"]["customizations"] == 1 and R["moved"]["bag_lines"] == 1 and R["moved"]["events"] >= 2
    Master, New = await H.Design(Did), await H.Design(R["design_id"])
    assert [X["kind"] for X in Master["batches"]] == ["initial"] and Master["selected_candidate_id"] == Cands[1]["id"]
    assert [X["kind"] for X in New["batches"]] == ["refine"] and New["selected_candidate_id"] == Refined
    assert New["source_ring_id"] == "R-1001-B" and New["batches"][0]["parent_candidate_id"] == Cands[1]["id"]
    assert Db.One("SELECT design_id FROM bag_lines")["design_id"] == R["design_id"]
    assert Db.One("SELECT design_id FROM customizations WHERE candidate_id = ?", (Refined,))["design_id"] == R["design_id"]
    Bag = (await H.Client.get("/api/bag")).json()
    assert Bag["lines"][0]["design_id"] == R["design_id"] and Bag["lines"][0]["title"] == R["title"]
    # Both sessions tell the story
    S = (await H.Client.get(f"/api/admin/sessions/{Did}", headers=Admin)).json()
    assert any(E["kind"] == "admin_refinement_split" and E["data"]["new_ring_id"] == "R-1002" for E in S["timeline"])
    S = (await H.Client.get(f"/api/admin/sessions/{R['design_id']}", headers=Admin)).json()
    assert S["session"]["add_to_bag"] and any(E["kind"] == "design_forked" and E["data"]["reason"] == "split" for E in S["timeline"])
    assert any(E["kind"] == "movie" for E in S["timeline"])
    # Not for the only batch, nor for a refinement that is the gallery image
    assert (await H.Client.post(f"/api/admin/batches/{B['id']}/split", json={}, headers=Admin)).json()["error"]["code"] == "not_a_refinement"
    B2 = await H.NewDesign("Another band")
    R2 = await _Refine(H, B2["design_id"], B2["candidates"][0]["id"], "wider")
    await H.Client.post("/api/admin/gallery", json={"design_id": B2["design_id"], "candidate_id": R2["candidates"][0]["id"]}, headers=Admin)
    assert (await H.Client.post(f"/api/admin/batches/{R2['id']}/split", json={}, headers=Admin)).json()["error"]["code"] == "gallery_image"


async def test_variation_directives_moved_to_the_stronger_defaults_only_when_unedited(HF):
    H = HF
    from p3.modelconfig import ModelConfigStore, PreviousVariationDefaults, VariationDefaults
    Edit = "nano-banana-pro-edit"
    S = (await H.Client.get(f"/api/admin/models/{Edit}", headers=Admin)).json()
    assert all(S["active"]["params"][f"variation_{K}"] == VariationDefaults[K] for K in "abcd")
    assert "directive wins" in VariationDefaults["b"] and "completely different" in VariationDefaults["d"]
    # An installation on the earlier defaults, unedited → upgraded as a new version
    Old = {**S["active"]["params"], **{f"variation_{K}": V for K, V in PreviousVariationDefaults[0].items()}}
    assert (await H.Client.post(f"/api/admin/models/{Edit}/activate", json={"params": Old}, headers=Admin)).status_code == 200
    ModelConfigStore(H.Ctx.Db)
    S = (await H.Client.get(f"/api/admin/models/{Edit}", headers=Admin)).json()
    assert all(S["active"]["params"][f"variation_{K}"] == VariationDefaults[K] for K in "abcd") and "stronger" in S["active"]["note"]
    # Edited texts are left alone
    Mine = {**S["active"]["params"], "variation_b": "My own wording."}
    assert (await H.Client.post(f"/api/admin/models/{Edit}/activate", json={"params": Mine}, headers=Admin)).status_code == 200
    ModelConfigStore(H.Ctx.Db)
    S = (await H.Client.get(f"/api/admin/models/{Edit}", headers=Admin)).json()
    assert S["active"]["params"]["variation_b"] == "My own wording."
    # Refinement requests carry the stronger directives
    B = await H.NewDesign("A slim band")
    await _Refine(H, B["design_id"], B["candidates"][0]["id"], "add a wave")
    Prompts = [A["prompt"] for E, A, _ in H.Provider.Submissions if E == endpoints.ImageEdit]
    assert sum(1 for P in Prompts if P.endswith("My own wording.")) == 1 and sum(1 for P in Prompts if P.endswith(VariationDefaults["d"])) == 1
