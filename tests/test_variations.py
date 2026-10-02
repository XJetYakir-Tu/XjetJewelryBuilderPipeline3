"""Refinement variations: the same instruction for all four images, plus one configurable directive per
image (A–D) on the nano-banana-pro/edit configuration — no extra request, no extra cost."""

import pytest

from p3.modelconfig import BuildRequest, Models, SlotDirective, VariationDefaults, WithDirective
from p3.providers import endpoints
from tests.conftest import Harness

AdminKey = "variations-admin-key"
Admin = {"Authorization": f"Bearer {AdminKey}"}
Edit = "nano-banana-pro-edit"


@pytest.fixture
async def HV(tmp_path):
    Obj = Harness(tmp_path, AdminKey=AdminKey)
    yield Obj
    await Obj.Close()


def _EditPromptsBySlot(H, BatchId: str) -> dict[int, str]:
    """slot → the prompt actually submitted for that image (matched through the per-image seed)."""
    Seeds = {C["seed"]: C["slot"] for C in H.Ctx.Db.All("SELECT seed, slot FROM candidates WHERE batch_id = ?", (BatchId,))}
    return {Seeds[A["seed"]]: A["prompt"] for E, A, _ in H.Provider.Submissions if E == endpoints.ImageEdit and A.get("seed") in Seeds}


async def _Refine(H, Instruction="make it thinner"):
    B = await H.NewDesign("A slim twisted band")
    R = await H.Client.post(f"/api/designs/{B['design_id']}/batches", json={"parent_candidate_id": B["candidates"][0]["id"],
                                                                            "instruction": Instruction})
    assert R.status_code == 200, R.text
    await H.Idle()
    return R.json()


def test_the_edit_model_has_four_pipeline_only_directives_with_defaults():
    Spec = Models[Edit]
    Names = [P.Name for P in Spec.Params if P.Internal]
    assert Names == ["variation_a", "variation_b", "variation_c", "variation_d"]
    for K in "abcd":
        P = Spec.Param(f"variation_{K}")
        assert P.Kind == "text" and P.Default == VariationDefaults[K] and P.Group and not P.Required
    assert not any(P.Internal for P in Models["nano-banana-pro"].Params)      # New Designs are untouched
    assert "directive wins" in VariationDefaults["b"] and "faithful" in VariationDefaults["a"]        # far apart, not a gradient


def test_directives_never_become_provider_fields_and_land_at_the_end_of_the_prompt():
    Params = {"prompt": "{{user_text}}\n\nSuffix", "variation_a": "Be faithful.", "variation_c": "   ", "aspect_ratio": "auto"}
    A = BuildRequest(Edit, Params, {"user_text": "thinner", "seed": 1, "image_url": "https://x/y.png", "slot": 0})
    assert "variation_a" not in A and "variation_c" not in A and A["aspect_ratio"] == "auto"
    assert A["prompt"] == "thinner\n\nSuffix\n\nBe faithful." and A["image_urls"] == ["https://x/y.png"] and A["seed"] == 1
    assert BuildRequest(Edit, Params, {"user_text": "thinner", "seed": 1, "slot": 2})["prompt"] == "thinner\n\nSuffix"     # blank = as before
    assert BuildRequest(Edit, Params, {"user_text": "thinner", "seed": 1})["prompt"] == "thinner\n\nSuffix"                # no slot = as before
    assert SlotDirective(Edit, Params, 1) is None and SlotDirective("nano-banana-pro", Params, 0) is None
    assert WithDirective("p", None) == "p" and WithDirective("p \n", "d") == "p\n\nd"


async def test_existing_installations_get_the_defaults_once_as_a_visible_version(HV):
    H = HV
    S = (await H.Client.get(f"/api/admin/models/{Edit}", headers=Admin)).json()
    assert S["active"]["number"] == 2 and all(S["active"]["params"][f"variation_{K}"] == VariationDefaults[K] for K in "abcd")
    assert [V["number"] for V in S["history"]] == [2, 1] and "variation_a" not in S["history"][1]["params"]
    assert "Refinement variations" in S["active"]["note"]
    # Re-opening the store never adds another version, even after the admin blanks every directive
    Params = {K: V for K, V in S["active"]["params"].items() if not K.startswith("variation_")}
    assert (await H.Client.post(f"/api/admin/models/{Edit}/activate", json={"params": Params}, headers=Admin)).status_code == 200
    from p3.modelconfig import ModelConfigStore
    ModelConfigStore(H.Ctx.Db)
    S = (await H.Client.get(f"/api/admin/models/{Edit}", headers=Admin)).json()
    assert S["active"]["number"] == 3 and "variation_a" not in S["active"]["params"]
    # The form data tells the Admin which fields are pipeline-only and how they are grouped
    P = next(P for P in S["model"]["params"] if P["name"] == "variation_b")
    assert P["internal"] and P["group"].startswith("Refinement variations") and P["default"] == VariationDefaults["b"]


async def test_refinement_images_a_to_d_each_get_their_own_directive(HV):
    H = HV
    R = await _Refine(H)
    Prompts = _EditPromptsBySlot(H, R["id"])
    assert sorted(Prompts) == [0, 1, 2, 3]
    Shared = H.Ctx.Db.One("SELECT effective_prompt FROM batches WHERE id = ?", (R["id"],))["effective_prompt"]
    assert "variation" not in Shared.lower() and Shared.startswith("make it thinner")
    for Slot, K in enumerate("abcd"):
        assert Prompts[Slot] == Shared.rstrip() + "\n\n" + VariationDefaults[K], Slot
    assert len({P for P in Prompts.values()}) == 4
    # Four images, four requests — exactly as many as before
    assert sum(1 for E, _, _ in H.Provider.Submissions if E == endpoints.ImageEdit) == 4


async def test_blank_directives_mean_the_plain_prompt_and_uploads_are_untouched(HV):
    H = HV
    S = (await H.Client.get(f"/api/admin/models/{Edit}", headers=Admin)).json()
    Params = {**S["active"]["params"], "variation_b": "", "variation_d": "Only image D is special."}
    del Params["variation_c"]
    assert (await H.Client.post(f"/api/admin/models/{Edit}/activate", json={"params": Params}, headers=Admin)).status_code == 200
    R = await _Refine(H, "wider")
    Prompts = _EditPromptsBySlot(H, R["id"])
    Shared = H.Ctx.Db.One("SELECT effective_prompt FROM batches WHERE id = ?", (R["id"],))["effective_prompt"]
    assert Prompts[0] == Shared.rstrip() + "\n\n" + VariationDefaults["a"]
    assert Prompts[1] == Shared and Prompts[2] == Shared                     # blank / removed: as before
    assert Prompts[3] == Shared.rstrip() + "\n\nOnly image D is special."
    # A New Design from an uploaded photo also uses the edit endpoint — no directives there
    import io
    from PIL import Image
    Buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 180, 120)).save(Buf, format="PNG")
    Before = len(H.Provider.Submissions)
    U = await H.Client.post("/api/designs", data={"prompt": "a ring like this photo", "rights_confirmed": "true"},
                            files={"reference": ("ref.png", Buf.getvalue(), "image/png")})
    assert U.status_code == 200, U.text
    await H.Idle()
    New = [A for E, A, _ in H.Provider.Submissions[Before:] if E == endpoints.ImageEdit]
    assert len(New) == 4 and all("image A" not in A["prompt"] and "image D" not in A["prompt"] for A in New)


async def test_preview_shows_image_a_and_lists_all_four_directives(HV):
    H = HV
    P = (await H.Client.post(f"/api/admin/models/{Edit}/preview", json={}, headers=Admin)).json()
    assert P["payload"]["prompt"].endswith(VariationDefaults["a"]) and "variation_a" not in P["payload"]
    assert P["slot_directives"] == {K.upper(): VariationDefaults[K] for K in "abcd"}
    P = (await H.Client.post("/api/admin/models/nano-banana-pro/preview", json={}, headers=Admin)).json()
    assert P["slot_directives"] is None and "image A" not in P["payload"]["prompt"]
