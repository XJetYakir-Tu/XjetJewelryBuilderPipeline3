"""Six-candidate generation, selection, refinement lineage, partial failure, dedupe, recovery."""

import asyncio

import pytest

from p3 import assets
from p3.providers import endpoints
from p3.providers.mock import MockProvider, _RingImage
from tests.conftest import Harness


async def test_requires_access_token(H):
    R = await H.Client.post("/api/designs", data={"prompt": "a ring"}, headers={"X-Access-Token": ""})
    assert R.status_code == 401
    R = await H.Client.post("/api/designs", data={"prompt": "a ring"}, headers={"X-Access-Token": "p3_bogus"})
    assert R.status_code == 401
    assert H.Provider.Submissions == []


async def test_initial_batch_is_six_distinct_candidates_from_one_prompt(H):
    Batch = await H.NewDesign("Art deco band with stepped shoulders")
    assert Batch["kind"] == "initial" and Batch["status"] == "complete"
    Cands = Batch["candidates"]
    assert [C["slot"] for C in Cands] == [0, 1, 2, 3, 4, 5]
    assert all(C["status"] == "ready" and C["image_url"] for C in Cands)
    assert len({C["id"] for C in Cands}) == 6
    assert len({assets.Sha256(H.AssetBytes(C["image_url"])) for C in Cands}) == 6

    Subs = H.Provider.SubmissionsFor(endpoints.ImageGenerate)
    assert len(Subs) == 6
    Prompts = {S[1]["prompt"] for S in Subs}
    assert len(Prompts) == 1 and Prompts.pop().startswith("Art deco band with stepped shoulders\n\n")
    assert len({S[1]["seed"] for S in Subs}) == 6
    assert all(S[1]["num_images"] == 1 and "image_urls" not in S[1] for S in Subs)


async def test_prompt_validation(H):
    R = await H.Client.post("/api/designs", data={"prompt": "  "})
    assert R.status_code == 400 and R.json()["error"]["code"] == "text_too_short"


async def test_create_is_idempotent_per_client_request_id(H):
    A = await H.NewDesign("Plain band", client_request_id="req-1")
    B = await H.NewDesign("Plain band", client_request_id="req-1")
    assert A["id"] == B["id"]
    assert len(H.Provider.Submissions) == 6


async def test_reference_upload_requires_rights_and_uses_edit(H):
    Png = assets.NormalizeReferenceImage(_RingImage(7, "ref"))
    R = await H.Client.post("/api/designs", data={"prompt": "Make it like this"},
                            files={"reference": ("ref.png", Png, "image/png")})
    assert R.status_code == 400 and R.json()["error"]["code"] == "rights_not_confirmed"
    R = await H.Client.post("/api/designs", data={"prompt": "Make it like this", "rights_confirmed": "true"},
                            files={"reference": ("ref.png", Png, "image/png")})
    assert R.status_code == 200
    await H.Idle()
    Subs = H.Provider.SubmissionsFor(endpoints.ImageEdit)
    assert len(Subs) == 6 and len({S[1]["image_urls"][0] for S in Subs}) == 1


async def test_selection_requires_ready_candidate_of_same_design(H):
    A = await H.NewDesign("Design A")
    B = await H.NewDesign("Design B")
    R = await H.Client.put(f"/api/designs/{A['design_id']}/selection",
                           json={"candidate_id": B["candidates"][0]["id"]})
    assert R.status_code == 404
    R = await H.Client.put(f"/api/designs/{A['design_id']}/selection",
                           json={"candidate_id": A["candidates"][2]["id"]})
    assert R.status_code == 200
    assert (await H.Design(A["design_id"]))["selected_candidate_id"] == A["candidates"][2]["id"]


async def test_refine_uses_selected_image_for_all_six_outputs(H):
    First = await H.NewDesign("Signet ring with a hexagon face")
    Selected = First["candidates"][3]
    await H.Client.put(f"/api/designs/{First['design_id']}/selection", json={"candidate_id": Selected["id"]})
    R = await H.Client.post(f"/api/designs/{First['design_id']}/batches",
                            json={"parent_candidate_id": Selected["id"], "instruction": "Add fine milgrain edges"})
    assert R.status_code == 200, R.text
    await H.Idle()
    Refined = (await H.Client.get(f"/api/batches/{R.json()['id']}")).json()
    assert Refined["kind"] == "refine" and Refined["parent_candidate_id"] == Selected["id"]
    assert Refined["status"] == "complete" and len(Refined["candidates"]) == 6

    Subs = H.Provider.SubmissionsFor(endpoints.ImageEdit)
    assert len(Subs) == 6
    assert len({S[1]["prompt"] for S in Subs}) == 1
    assert Subs[0][1]["prompt"].startswith("Add fine milgrain edges\n\n")
    RefUrls = {S[1]["image_urls"][0] for S in Subs}
    assert len(RefUrls) == 1
    assert H.Provider.Uploads[RefUrls.pop()] == H.AssetBytes(Selected["image_url"])
    # The earlier batch is preserved, and the selection did not move on its own.
    D = await H.Design(First["design_id"])
    assert [B["id"] for B in D["batches"]] == [First["id"], Refined["id"]]
    assert D["selected_candidate_id"] == Selected["id"]


async def test_refine_with_missing_reference_is_recoverable_error_not_text_only(H):
    First = await H.NewDesign("Band")
    Selected = First["candidates"][0]
    (H.Settings.AssetsDir / Selected["image_url"].removeprefix("/assets/")).unlink()
    Before = len(H.Provider.Submissions)
    R = await H.Client.post(f"/api/designs/{First['design_id']}/batches",
                            json={"parent_candidate_id": Selected["id"], "instruction": "Thicker band"})
    assert R.status_code == 409 and R.json()["error"]["code"] == "reference_unavailable"
    assert len(H.Provider.Submissions) == Before


async def test_refine_rejects_not_ready_or_foreign_parent(H):
    A = await H.NewDesign("Design A")
    B = await H.NewDesign("Design B")
    R = await H.Client.post(f"/api/designs/{A['design_id']}/batches",
                            json={"parent_candidate_id": B["candidates"][0]["id"], "instruction": "Thicker band"})
    assert R.status_code == 404


async def test_partial_failure_keeps_successes_and_retries_only_failed_slot(H):
    H.Provider.Script(endpoints.ImageGenerate, "ok", "fail", "ok", "fail", "ok", "ok")
    Batch = await H.NewDesign("Braided band")
    assert Batch["status"] == "partial"
    Failed = [C for C in Batch["candidates"] if C["status"] == "failed"]
    Ready = {C["id"]: C["image_url"] for C in Batch["candidates"] if C["status"] == "ready"}
    assert len(Failed) == 2 and len(Ready) == 4 and all(C["retryable"] for C in Failed)

    R = await H.Client.post(f"/api/candidates/{Failed[0]['id']}/retry")
    assert R.status_code == 200
    await H.Idle()
    After = (await H.Client.get(f"/api/batches/{Batch['id']}")).json()
    assert len(H.Provider.Submissions) == 7          # exactly one extra request
    assert {C["id"]: C["image_url"] for C in After["candidates"] if C["id"] in Ready} == Ready
    assert After["status"] == "partial"

    R = await H.Client.post(f"/api/batches/{Batch['id']}/retry-failed")
    await H.Idle()
    After = (await H.Client.get(f"/api/batches/{Batch['id']}")).json()
    assert After["status"] == "complete" and len(H.Provider.Submissions) == 8

    R = await H.Client.post(f"/api/candidates/{Failed[0]['id']}/retry")
    assert R.status_code == 409                       # ready slots are never regenerated


async def test_entire_batch_failure_is_reported_and_retryable(H):
    H.Provider.Script(endpoints.ImageGenerate, *["fail"] * 6)
    Batch = await H.NewDesign("Band")
    assert Batch["status"] == "failed"
    assert Batch["user_text"] == "Band"               # prompt preserved for retry
    await H.Client.post(f"/api/batches/{Batch['id']}/retry-failed")
    await H.Idle()
    assert (await H.Client.get(f"/api/batches/{Batch['id']}")).json()["status"] == "complete"


async def test_exact_duplicate_output_is_retried_then_bounded(H):
    H.Provider.Script(endpoints.ImageGenerate, "duplicate", "duplicate")
    Batch = await H.NewDesign("Band")
    assert Batch["status"] == "complete"
    assert len({assets.Sha256(H.AssetBytes(C["image_url"])) for C in Batch["candidates"]}) == 6
    assert len(H.Provider.Submissions) == 7          # one bounded re-request for the duplicate

    H.Provider.Script(endpoints.ImageGenerate, "duplicate", "duplicate", "duplicate")
    Batch = await H.NewDesign("Band two")
    Codes = sorted(C["error_code"] or "" for C in Batch["candidates"])
    assert Codes.count("duplicate_output") == 1 and Batch["status"] == "partial"


async def test_transient_poll_errors_retry_same_request(H):
    H.Provider.Script(endpoints.ImageGenerate, "status_transient:3")
    Batch = await H.NewDesign("Band")
    assert Batch["status"] == "complete" and len(H.Provider.Submissions) == 6


async def test_transient_errors_are_bounded(H):
    H.Provider.Script(endpoints.ImageGenerate, "status_transient:50")
    Batch = await H.NewDesign("Band")
    Codes = [C["error_code"] for C in Batch["candidates"] if C["status"] == "failed"]
    assert Codes == ["provider_unreachable"] and len(H.Provider.Submissions) == 6


async def test_late_results_do_not_touch_other_designs(H):
    H.Provider.LatencyS = 0.05
    A = await H.Client.post("/api/designs", data={"prompt": "Design A"})
    B = await H.Client.post("/api/designs", data={"prompt": "Design B"})
    await H.Idle()
    BatchA = (await H.Client.get(f"/api/batches/{A.json()['id']}")).json()
    BatchB = (await H.Client.get(f"/api/batches/{B.json()['id']}")).json()
    assert BatchA["design_id"] != BatchB["design_id"]
    assert not ({C["id"] for C in BatchA["candidates"]} & {C["id"] for C in BatchB["candidates"]})
    assert all(C["image_url"].startswith(f"/assets/designs/{BatchA['design_id']}/") for C in BatchA["candidates"])
    for D in (BatchA, BatchB):
        assert (await H.Design(D["design_id"]))["selected_candidate_id"] is None   # never auto-selected


async def test_restart_resumes_submitted_and_interrupts_unconfirmed(tmp_path):
    Provider = MockProvider(LatencyS=0.0, RenderVideo=False)
    Provider.Script(endpoints.ImageGenerate, *["hang"] * 6)
    First = Harness(tmp_path, Provider=Provider)
    R = await First.Client.post("/api/designs", data={"prompt": "Band"})
    BatchId = R.json()["id"]
    for _ in range(200):
        Rows = First.Ctx.Db.All("SELECT status FROM candidates WHERE batch_id = ?", (BatchId,))
        if all(Row["status"] == "generating" for Row in Rows):
            break
        await asyncio.sleep(0.005)
    await First.Close()                                   # simulated crash: pollers die
    # One slot looks like it crashed between submit and persisting the request id.
    Victim = First.Ctx.Db.One("SELECT id FROM candidates WHERE batch_id = ? AND slot = 5", (BatchId,))
    First.Ctx.Db.Update("candidates", Victim["id"], status="pending", provider_request_id=None)
    for Req in Provider.Requests.values():
        Req["outcome"] = "ok"                             # remote work finished while we were down

    Second = Harness(tmp_path, Provider=Provider)
    Summary = Second.Svc.Reconcile()
    assert Summary["candidates"] == {"resumed": 5, "interrupted": 1}
    await Second.Idle()
    Rows = Second.Ctx.Db.All("SELECT status, error_code FROM candidates WHERE batch_id = ? ORDER BY slot", (BatchId,))
    assert [R_["status"] for R_ in Rows] == ["ready"] * 5 + ["failed"]
    assert Rows[5]["error_code"] == "interrupted"
    assert len(Provider.Submissions) == 6                 # nothing was resubmitted automatically
    await Second.Close()


def test_asset_paths_cannot_escape_root(tmp_path):
    with pytest.raises(assets.AssetError):
        assets.Resolve(tmp_path, "../outside.png")
    with pytest.raises(assets.AssetError):
        assets.Resolve(tmp_path, "C:/Windows/win.ini")
