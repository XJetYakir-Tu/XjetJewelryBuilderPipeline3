"""AI provider mode: startup precedence, developer switch, safety rules."""

import json

import pytest

from p3.modes import LiveConfirmation
from p3.providers import endpoints
from p3.providers.mock import MockProvider
from tests.conftest import Harness

AdminKey = "modes-admin-key"
Dev = {"Authorization": f"Bearer {AdminKey}"}


class FakeLive(MockProvider):
    """Stands in for the paid provider in tests: never touches the network."""
    Name = "fal"

    def Owns(self, RequestId):
        return RequestId.startswith("livereq_")

    async def Submit(self, Endpoint, Arguments):
        Rid = await super().Submit(Endpoint, Arguments)
        New = "livereq_" + Rid.removeprefix("mockreq_")
        self.Requests[New] = self.Requests.pop(Rid)
        return New


def _Factories(Created):
    def Mock():
        P = MockProvider(LatencyS=0.0, RenderVideo=False); Created.append(P); return P

    def Live():
        P = FakeLive(LatencyS=0.0, RenderVideo=False); Created.append(P); return P
    return {"mock": Mock, "live": Live}


def _H(tmp_path, FalKey="fake-key", Created=None):
    return Harness(tmp_path, AdminKey=AdminKey, FalKey=FalKey, Factories=_Factories(Created if Created is not None else []))


async def test_default_is_mock_and_home_shows_it(tmp_path):
    H = _H(tmp_path)
    try:
        Health = (await H.Client.get("/api/health")).json()
        assert Health["mode"] == "mock" and Health["provider"] == "mock"
        assert "P3_PROVIDER" in Health["mode_source"]
        Html = (await H.Client.get("/")).text
        assert 'data-testid="home-ai-mode"' in Html and "Mock Mode" in Html and "Live AI" in Html
    finally:
        await H.Close()


async def test_mode_api_is_developer_only(tmp_path):
    H = _H(tmp_path)
    try:
        assert (await H.Client.get("/api/dev/mode")).status_code == 403
        assert (await H.Client.post("/api/dev/mode", json={"mode": "live", "confirmation": LiveConfirmation})).status_code == 403
        assert (await H.Client.get("/api/dev/mode", headers={"Authorization": "Bearer nope"})).status_code == 403
        assert (await H.Client.get("/api/dev/mode", headers=Dev)).json()["mode"] == "mock"
    finally:
        await H.Close()


async def test_switch_to_live_requires_explicit_confirmation_and_key(tmp_path):
    H = _H(tmp_path)
    try:
        R = await H.Client.post("/api/dev/mode", json={"mode": "live"}, headers=Dev)
        assert R.status_code == 400 and R.json()["error"]["code"] == "live_confirmation_required"
        R = await H.Client.post("/api/dev/mode", json={"mode": "live", "confirmation": "yes"}, headers=Dev)
        assert R.status_code == 400
        assert (await H.Client.get("/api/health")).json()["mode"] == "mock"
    finally:
        await H.Close()
    NoKey = _H(tmp_path / "nokey", FalKey=None)
    try:
        R = await NoKey.Client.post("/api/dev/mode", json={"mode": "live", "confirmation": LiveConfirmation}, headers=Dev)
        assert R.status_code == 409 and R.json()["error"]["code"] == "live_unavailable"
        assert (await NoKey.Client.get("/api/dev/mode", headers=Dev)).json()["live_available"] is False
    finally:
        await NoKey.Close()


async def test_switch_live_then_mock_routes_new_work_and_persists(tmp_path):
    Created = []
    H = _H(tmp_path, Created=Created)
    try:
        R = await H.Client.post("/api/dev/mode", json={"mode": "live", "confirmation": LiveConfirmation}, headers=Dev)
        assert R.status_code == 200 and R.json()["mode"] == "live"
        assert (await H.Client.get("/api/health")).json()["mode"] == "live"
        Live = H.Ctx.Provider
        assert isinstance(Live, FakeLive)
        await H.NewDesign("Band made in live mode")
        assert len(Live.SubmissionsFor(endpoints.ImageGenerate)) == 4
        R = await H.Client.post("/api/dev/mode", json={"mode": "mock"}, headers=Dev)
        assert R.json()["mode"] == "mock" and H.Ctx.Provider.Name == "mock"
        await H.NewDesign("Band made in mock mode")
        assert len(Live.SubmissionsFor(endpoints.ImageGenerate)) == 4          # live provider not used again
        assert json.loads(H.Settings.RuntimeStatePath.read_text())["ai_mode"] == "mock"
    finally:
        await H.Close()
    Again = _H(tmp_path)                                                         # restart keeps the choice
    try:
        Health = (await Again.Client.get("/api/health")).json()
        assert Health["mode"] == "mock" and Health["mode_source"] == "developer switch"
    finally:
        await Again.Close()


async def test_saved_live_without_key_starts_in_mock(tmp_path):
    H = _H(tmp_path)
    try:
        await H.Client.post("/api/dev/mode", json={"mode": "live", "confirmation": LiveConfirmation}, headers=Dev)
    finally:
        await H.Close()
    NoKey = _H(tmp_path, FalKey=None)
    try:
        Health = (await NoKey.Client.get("/api/health")).json()
        assert Health["mode"] == "mock" and "no FAL_KEY" in Health["mode_source"]
    finally:
        await NoKey.Close()


async def test_switch_is_refused_while_generations_run(tmp_path):
    Created = []
    H = _H(tmp_path, Created=Created)
    try:
        H.Ctx.Provider.Script(endpoints.ImageGenerate, *["hang"] * 4)
        await H.Client.post("/api/designs", data={"prompt": "Band that hangs"})
        import asyncio
        await asyncio.sleep(0.05)
        R = await H.Client.post("/api/dev/mode", json={"mode": "live", "confirmation": LiveConfirmation}, headers=Dev)
        assert R.status_code == 409 and R.json()["error"]["code"] == "jobs_running"
        assert (await H.Client.get("/api/dev/mode", headers=Dev)).json()["active_jobs"] == 4
    finally:
        await H.Close()


async def test_restart_in_mock_leaves_live_jobs_for_live_mode(tmp_path):
    """A live request found at startup in mock mode is neither polled by the mock nor discarded."""
    Created = []
    H = _H(tmp_path, Created=Created)
    try:
        await H.Client.post("/api/dev/mode", json={"mode": "live", "confirmation": LiveConfirmation}, headers=Dev)
        H.Ctx.Provider.Script(endpoints.ImageGenerate, *["hang"] * 4)
        await H.Client.post("/api/designs", data={"prompt": "Live band in flight"})
        import asyncio
        for _ in range(100):
            if H.Ctx.Db.One("SELECT COUNT(*) AS n FROM candidates WHERE status = 'generating'")["n"] == 4:
                break
            await asyncio.sleep(0.01)
    finally:
        await H.Close()
    H.Settings.RuntimeStatePath.write_text(json.dumps({"ai_mode": "mock"}))     # operator switched back to mock
    Again = _H(tmp_path)
    try:
        Summary = Again.Svc.Reconcile()
        assert Summary["candidates"] == {"resumed": 0, "interrupted": 0}
        Rows = Again.Ctx.Db.All("SELECT status, provider_request_id FROM candidates")
        assert all(R["status"] == "generating" and R["provider_request_id"].startswith("livereq_") for R in Rows)
    finally:
        await Again.Close()
