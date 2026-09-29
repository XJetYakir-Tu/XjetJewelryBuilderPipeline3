"""Live-mode wiring: the real FalProvider adapter, with only fal's network client faked.

These tests exercise FalProvider's status mapping, error classification and the
exact arguments Pipeline 3 sends to each verified endpoint, end to end through
the app. They make no network call — they prove the wiring, not provider
behaviour, output quality or cost.
"""

import httpx
import fal_client
import pytest

from p3.providers import endpoints
from p3.providers.base import ProviderError, TransientProviderError
from p3.providers.fal import FalProvider
from p3.providers.mock import _FakeMp4, _MeshBytes, _RingImage
from tests.conftest import Harness

AdminKey = "adapter-test-admin-key"


class FakeFalClient:
    """Stands in for fal_client.AsyncClient: queue semantics, recorded calls."""

    def __init__(self):
        self.Calls = []
        self.Requests = {}
        self.StatusScript = {}        # request_id -> list of statuses to return first
        self.FailSubmit = None

    async def upload(self, Data, ContentType, file_name=None):
        self.Calls.append(("upload", ContentType, len(Data)))
        return f"https://fal.media/files/fake/{len(self.Calls)}.png"

    async def submit(self, Application, Arguments):
        if self.FailSubmit:
            raise self.FailSubmit
        RequestId = f"req-{len(self.Requests) + 1}"
        self.Requests[RequestId] = (Application, dict(Arguments))
        self.Calls.append(("submit", Application, RequestId))
        return fal_client.AsyncRequestHandle(RequestId, "", "", "", client=None)

    async def status(self, Application, RequestId, with_logs=False):
        Script = self.StatusScript.get(RequestId)
        if Script:
            Next = Script.pop(0)
            if isinstance(Next, Exception):
                raise Next
            return Next
        return fal_client.Completed(logs=None, metrics={})

    async def result(self, Application, RequestId):
        App, Args = self.Requests[RequestId]
        assert App == Application
        if App in (endpoints.ImageGenerate, endpoints.ImageEdit):
            return {"images": [{"url": f"https://fal.media/out/{RequestId}.png"}], "description": ""}
        if App == endpoints.Movie:
            return {"video": {"url": f"https://fal.media/out/{RequestId}.mp4"}}
        if App == endpoints.Mesh:
            return {"model_mesh": {"url": f"https://fal.media/out/{RequestId}.{Args['export_format']}"}}
        raise AssertionError(App)


def _HttpError(Code):
    return fal_client.FalClientHTTPError("boom", Code, {}, response=httpx.Response(Code))


def _LiveProvider():
    P = FalProvider("fake-key-not-used")
    P.Client = FakeFalClient()
    Seeds = iter(range(1000, 2000))

    async def Download(Url):
        if Url.endswith(".png"):
            return _RingImage(next(Seeds), Url)
        if Url.endswith(".mp4"):
            return _FakeMp4()
        return _MeshBytes(Url.rsplit(".", 1)[-1])
    P.Download = Download
    return P


@pytest.fixture
async def HLive(tmp_path):
    Obj = Harness(tmp_path, Provider=_LiveProvider(), AdminKey=AdminKey)
    yield Obj
    await Obj.Close()


async def test_live_adapter_full_flow_sends_verified_arguments(HLive):
    H = HLive
    Fake = H.Provider.Client
    assert (await H.Client.get("/api/health")).json()["mode"] == "live"

    Batch = await H.NewDesign("A signet ring with a small crescent moon")
    assert Batch["status"] == "complete"
    Gen = [Args for App, Args in Fake.Requests.values() if App == endpoints.ImageGenerate]
    assert len(Gen) == 6
    assert all(A["num_images"] == 1 and A["aspect_ratio"] == "1:1" and A["resolution"] == "1K"
               and A["output_format"] == "png" and A["system_prompt"] for A in Gen)
    assert len({A["seed"] for A in Gen}) == 6 and len({A["prompt"] for A in Gen}) == 1

    Selected = Batch["candidates"][1]
    R = await H.Client.post(f"/api/designs/{Batch['design_id']}/batches",
                            json={"parent_candidate_id": Selected["id"], "instruction": "Add a twisted rope edge"})
    await H.Idle()
    Edit = [Args for App, Args in Fake.Requests.values() if App == endpoints.ImageEdit]
    assert len(Edit) == 6 and len({A["image_urls"][0] for A in Edit}) == 1
    assert (await H.Client.get(f"/api/batches/{R.json()['id']}")).json()["status"] == "complete"

    Cus = await H.Proceed(Batch["design_id"], Selected["id"])
    await H.Idle()
    Movie = [Args for App, Args in Fake.Requests.values() if App == endpoints.Movie]
    assert len(Movie) == 1
    M = Movie[0]
    assert M["image_url"].startswith("https://fal.media/")
    assert M["prompt_expansion_mode"] in ("disabled", "balanced", "quality")
    assert M["resolution"] in ("480P", "768P", "1080P") and 3 <= M["duration"] <= 15
    assert 2 <= len(M["camera_trajectory"]) <= 12
    assert all(set(K) == {"time", "azimuth", "elevation", "distance"} for K in M["camera_trajectory"])
    assert (await H.Client.get(f"/api/customizations/{Cus['id']}")).json()["movie"]["status"] == "ready"

    R = await H.Client.post(f"/api/dev/candidates/{Selected['id']}/meshes", json={},
                            headers={"Authorization": f"Bearer {AdminKey}"})
    await H.Idle()
    Mesh = [Args for App, Args in Fake.Requests.values() if App == endpoints.Mesh]
    assert len(Mesh) == 1 and Mesh[0]["export_format"] == "stl" and "image_url" in Mesh[0]
    assert not any(K.endswith("_image_url") for K in Mesh[0])
    Uploads = [C for C in Fake.Calls if C[0] == "upload"]
    assert len(Uploads) == 3 and all(C[1] == "image/png" for C in Uploads)   # refine ref, movie, mesh


async def test_live_adapter_status_mapping_and_transient_retry(HLive):
    H = HLive
    Fake = H.Provider.Client
    Fake.StatusScript["req-1"] = [fal_client.Queued(position=3), fal_client.InProgress(logs=None),
                                  _HttpError(503), httpx.ConnectError("reset")]
    Batch = await H.NewDesign("Plain comfort-fit band")
    assert Batch["status"] == "complete" and len(Fake.Requests) == 6    # retried reads, no resubmission


async def test_live_adapter_provider_error_fails_only_that_slot(HLive):
    H = HLive
    Fake = H.Provider.Client
    Fake.StatusScript["req-2"] = [fal_client.Completed(logs=None, metrics={}, error="content_policy_violation")]
    Batch = await H.NewDesign("Plain comfort-fit band")
    Failed = [C for C in Batch["candidates"] if C["status"] == "failed"]
    assert len(Failed) == 1 and Failed[0]["error_code"] == "content_policy" and Batch["status"] == "partial"


async def test_fal_error_classification():
    P = FalProvider("fake")

    async def Raise(E):
        raise E
    for Code in (429, 500, 503):
        P.Client = type("C", (), {"submit": lambda self, a, b, E=_HttpError(Code): Raise(E)})()
        with pytest.raises(TransientProviderError):
            await P.Submit("x/y", {})
    P.Client = type("C", (), {"submit": lambda self, a, b: Raise(_HttpError(422))})()
    with pytest.raises(ProviderError):
        await P.Submit("x/y", {})
    P.Client = type("C", (), {"submit": lambda self, a, b: Raise(_HttpError(402))})()
    with pytest.raises(ProviderError) as Info:
        await P.Submit("x/y", {})
    assert Info.value.Code == "billing"
