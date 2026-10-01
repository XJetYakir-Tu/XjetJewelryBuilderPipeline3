"""Test harness: every test gets its own temporary data dir and a mock provider.

No test makes a network call or touches Pipeline 2.
"""

import json
from pathlib import Path

import httpx
import pytest

from p3.app import CreateApp
from p3.providers.mock import MockProvider
from p3.settings import ConfigDir, LoadSettings

ShippedProfile = ConfigDir / "pricing_profile.json"
DevProfile = ConfigDir / "pricing_profile.dev-example.json"


class Harness:
    def __init__(self, TmpPath: Path, Profile: Path = ShippedProfile, AllowUnapproved=False,
                 AdminKey=None, Provider=None):
        self.TmpPath = TmpPath
        self.Settings = LoadSettings(DataDir=TmpPath / "var", Provider="mock", PricingProfilePath=Profile,
                                     AllowUnapprovedPricing=AllowUnapproved, AdminKey=AdminKey,
                                     PollIntervalS=0.005, MaxTransientPollErrors=5, MockLatencyS=0.0)
        self.Provider = Provider or MockProvider(LatencyS=0.0, RenderVideo=False)
        self.App = CreateApp(self.Settings, self.Provider)
        self.Ctx = self.App.state.Ctx
        self.Svc = self.App.state.Services
        self.Token, self.Who = self.Ctx.Accounts.IssueToken("test")
        self.Client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.App), base_url="http://p3.test",
                                        headers={"X-Access-Token": self.Token})

    async def Idle(self):
        await self.Ctx.Runner.WaitIdle(10)

    async def Close(self):
        await self.Ctx.Runner.Shutdown()
        await self.Client.aclose()

    # ── flow helpers ─────────────────────────────────────────────────────
    async def NewDesign(self, Prompt="A slim twisted band with a small leaf motif", **Form) -> dict:
        R = await self.Client.post("/api/designs", data={"prompt": Prompt, **Form})
        assert R.status_code == 200, R.text
        await self.Idle()
        return (await self.Client.get(f"/api/batches/{R.json()['id']}")).json()

    async def Design(self, DesignId) -> dict:
        R = await self.Client.get(f"/api/designs/{DesignId}")
        assert R.status_code == 200, R.text
        return R.json()

    async def Proceed(self, DesignId, CandidateId) -> dict:
        R = await self.Client.post(f"/api/designs/{DesignId}/customize", json={"candidate_id": CandidateId})
        assert R.status_code == 200, R.text
        return R.json()

    def AssetBytes(self, Url: str) -> bytes:
        return (self.Settings.AssetsDir / Url.removeprefix("/assets/")).read_bytes()


@pytest.fixture
async def H(tmp_path):
    Obj = Harness(tmp_path)
    yield Obj
    await Obj.Close()


@pytest.fixture
async def HDevPricing(tmp_path):
    Obj = Harness(tmp_path, Profile=DevProfile, AllowUnapproved=True)
    yield Obj
    await Obj.Close()


@pytest.fixture
def WriteProfile(tmp_path):
    def _Write(Data: dict) -> Path:
        P = tmp_path / "profile.json"
        P.write_text(json.dumps(Data), encoding="utf-8")
        return P
    return _Write
