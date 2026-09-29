"""Developer-only Hitem3D single-image mesh and STL handling."""

import pytest

from p3.providers import endpoints
from tests.conftest import Harness

AdminKey = "test-admin-key-not-a-secret"


@pytest.fixture
async def HDev(tmp_path):
    Obj = Harness(tmp_path, AdminKey=AdminKey)
    yield Obj
    await Obj.Close()


def _Auth(Key=AdminKey):
    return {"Authorization": f"Bearer {Key}"}


async def test_dev_routes_disabled_without_admin_key(H):
    R = await H.Client.get("/api/dev/status")
    assert R.status_code == 503 and R.json()["error"]["code"] == "developer_tools_disabled"


async def test_dev_routes_require_correct_key_and_customer_token_is_not_enough(HDev):
    Batch = await HDev.NewDesign("Band")
    Cid = Batch["candidates"][0]["id"]
    assert (await HDev.Client.get("/api/dev/status")).status_code == 403
    assert (await HDev.Client.get("/api/dev/status", headers=_Auth("wrong"))).status_code == 403
    R = await HDev.Client.post(f"/api/dev/candidates/{Cid}/meshes", json={})
    assert R.status_code == 403
    assert HDev.Provider.SubmissionsFor(endpoints.Mesh) == []
    assert (await HDev.Client.get("/api/dev/status", headers=_Auth())).status_code == 200


async def test_single_image_mesh_with_native_stl(HDev):
    Batch = await HDev.NewDesign("Band")
    Cand = Batch["candidates"][2]
    R = await HDev.Client.post(f"/api/dev/candidates/{Cand['id']}/meshes", json={}, headers=_Auth())
    assert R.status_code == 200, R.text
    await HDev.Idle()
    Mesh = (await HDev.Client.get(f"/api/dev/meshes/{R.json()['id']}", headers=_Auth())).json()
    assert Mesh["status"] == "ready" and Mesh["has_stl"] and Mesh["original_format"] == "stl"
    assert Mesh["provenance"]["candidate_id"] == Cand["id"]
    assert "not approved for manufacturing" in Mesh["provenance"]["disclaimer"]

    Subs = HDev.Provider.SubmissionsFor(endpoints.Mesh)
    assert len(Subs) == 1
    Args = Subs[0][1]
    assert HDev.Provider.Uploads[Args["image_url"]] == HDev.AssetBytes(Cand["image_url"])
    assert not any(K.endswith("_image_url") for K in Args)          # no multi-view inputs
    assert Args["export_format"] == "stl"

    D = await HDev.Client.get(f"/api/dev/meshes/{Mesh['id']}/download?kind=stl", headers=_Auth())
    assert D.status_code == 200 and len(D.content) > 84
    assert (await HDev.Client.get(f"/api/dev/meshes/{Mesh['id']}/download?kind=stl")).status_code == 403


async def test_glb_mesh_converts_to_stl_as_separate_operation(HDev):
    Batch = await HDev.NewDesign("Band")
    Cid = Batch["candidates"][0]["id"]
    R = await HDev.Client.post(f"/api/dev/candidates/{Cid}/meshes",
                               json={"settings": {"export_format": "glb"}}, headers=_Auth())
    await HDev.Idle()
    Mesh = (await HDev.Client.get(f"/api/dev/meshes/{R.json()['id']}", headers=_Auth())).json()
    assert Mesh["status"] == "ready" and not Mesh["has_stl"] and Mesh["can_convert"]
    assert (await HDev.Client.get(f"/api/dev/meshes/{Mesh['id']}/download?kind=stl",
                                  headers=_Auth())).status_code == 404
    Conv = (await HDev.Client.post(f"/api/dev/meshes/{Mesh['id']}/convert-stl", headers=_Auth())).json()
    assert Conv["has_stl"]
    D = await HDev.Client.get(f"/api/dev/meshes/{Mesh['id']}/download?kind=stl", headers=_Auth())
    assert D.status_code == 200


async def test_invalid_mesh_settings_rejected(HDev):
    Batch = await HDev.NewDesign("Band")
    R = await HDev.Client.post(f"/api/dev/candidates/{Batch['candidates'][0]['id']}/meshes",
                               json={"settings": {"face_count": 10}}, headers=_Auth())
    assert R.status_code == 400
    R = await HDev.Client.post(f"/api/dev/candidates/{Batch['candidates'][0]['id']}/meshes",
                               json={"settings": {"front_image_url": "x"}}, headers=_Auth())
    assert R.status_code == 400


async def test_mesh_failure_does_not_affect_customer_flow(HDev):
    Batch = await HDev.NewDesign("Band")
    Cand = Batch["candidates"][0]
    HDev.Provider.Script(endpoints.Mesh, "fail")
    R = await HDev.Client.post(f"/api/dev/candidates/{Cand['id']}/meshes", json={}, headers=_Auth())
    await HDev.Idle()
    assert (await HDev.Client.get(f"/api/dev/meshes/{R.json()['id']}", headers=_Auth())).json()["status"] == "failed"
    Cus = await HDev.Proceed(Batch["design_id"], Cand["id"])
    await HDev.Idle()
    Cus = (await HDev.Client.get(f"/api/customizations/{Cus['id']}")).json()
    assert Cus["movie"]["status"] == "ready" and Cus["image_url"] == Cand["image_url"]
