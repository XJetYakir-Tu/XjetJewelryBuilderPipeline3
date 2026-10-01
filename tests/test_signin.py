"""JewelryB2C2 sign-in / registration / quota behaviour, reproduced in P3 (mail goes to the outbox)."""

import re
from datetime import datetime, timedelta, timezone

import pytest

from p3.accounts.local import _HashSecret
from p3.providers import endpoints
from tests.conftest import Harness

AdminKey = "signin-admin-key"
Dev = {"Authorization": f"Bearer {AdminKey}"}


@pytest.fixture
async def HS(tmp_path):
    Obj = Harness(tmp_path, AdminKey=AdminKey)
    yield Obj
    await Obj.Close()


def _Outbox(H):
    return H.App.state.Mailer.List()


def _Link(Html, Pattern):
    M = re.search(Pattern, Html)
    assert M, f"link {Pattern} not found"
    return M.group(1)


async def test_email_registration_verify_and_sign_in(HS):
    H = HS
    Anon = {"X-Access-Token": ""}
    R = await H.Client.post("/api/register", json={"Name": "Dana", "Email": "dana@example.com"}, headers=Anon)
    assert R.status_code == 200 and R.json() == {
        "status": "verification_sent",
        "message": "We've sent a verification email to your inbox. Please verify your email to save your "
                   "designs and continue creating your jewelry."}
    Mail = _Outbox(H)
    assert len(Mail) == 1 and Mail[0]["to"] == "dana@example.com"
    assert Mail[0]["subject"] == "Verify your email to continue designing — XJet Atelier"
    Secret = _Link(Mail[0]["html"], r'/verify\?token=([A-Za-z0-9_\-]+)"')

    Page = await H.Client.get(f"/verify?token={Secret}", headers=Anon)
    assert Page.status_code == 200 and "Email verified" in Page.text and "Dana, your email" in Page.text
    Token = _Link(Page.text, r'<div class="token">([A-Z]{6})</div>')
    assert f"#token={Token}" in Page.text                                   # one-click sign-in link
    Mail = _Outbox(H)
    assert Mail[0]["subject"] == "Your XJet Atelier access token" and Token in Mail[0]["html"]

    # Sign in with the token, case-insensitively, exactly as P2's "Enter it here" form.
    R = await H.Client.post("/api/register-token", json={"Token": Token.lower(), "Name": "", "Email": ""}, headers=Anon)
    assert R.json() == {"ok": True, "used": 0, "max": 10, "remaining": 10, "name": "Dana"}
    S = (await H.Client.get("/api/token-status", headers={"X-Access-Token": Token})).json()
    assert (S["used"], S["max"], S["remaining"], S["name"], S["email"]) == (0, 10, 10, "Dana", "dana@example.com")

    # Clicking the link again: already verified (token is hashed, so it is not revealed again).
    Again = await H.Client.get(f"/verify?token={Secret}", headers=Anon)
    assert "Already verified" in Again.text and Token not in Again.text


async def test_register_rules_match_pipeline2(HS):
    H = HS
    Anon = {"X-Access-Token": ""}
    R = await H.Client.post("/api/register", json={"Name": "", "Email": "not-an-email"}, headers=Anon)
    assert R.status_code == 400 and R.json()["error"] == {"code": "invalid_email", "message": "Please enter a valid email address."}

    await H.Client.post("/api/register", json={"Name": "Lee", "Email": "lee@example.com"}, headers=Anon)
    First = _Link(_Outbox(H)[0]["html"], r'/verify\?token=([A-Za-z0-9_\-]+)"')
    R = await H.Client.post("/api/register", json={"Name": "", "Email": "LEE@example.com"}, headers=Anon)   # pending → re-send
    assert R.json()["status"] == "verification_sent"
    Second = _Link(_Outbox(H)[0]["html"], r'/verify\?token=([A-Za-z0-9_\-]+)"')
    assert Second != First and "Hi Lee," in _Outbox(H)[0]["html"]           # name kept from first registration
    assert "Invalid link" in (await H.Client.get(f"/verify?token={First}", headers=Anon)).text   # superseded link

    Page = (await H.Client.get(f"/verify?token={Second}", headers=Anon)).text
    OldToken = _Link(Page, r'<div class="token">([A-Z]{6})</div>')
    R = await H.Client.post("/api/register", json={"Name": "", "Email": "lee@example.com"}, headers=Anon)   # verified
    assert R.json() == {"status": "already_registered",
                        "message": "You're already registered — we've re-sent your access token to your email."}
    NewToken = _Link(_Outbox(H)[0]["html"], r">([A-Z]{6})</span>")
    assert NewToken != OldToken                                              # hashed tokens: a fresh one is sent
    assert (await H.Client.post("/api/register-token", json={"Token": NewToken})).json()["ok"]
    assert (await H.Client.post("/api/register-token", json={"Token": OldToken})).status_code == 403


async def test_token_errors_use_pipeline2_messages(HS):
    H = HS
    R = await H.Client.post("/api/register-token", json={"Token": "ZZZZZZ", "Name": "", "Email": ""})
    assert R.status_code == 404 and R.json()["error"] == {"code": "token_not_found",
                                                          "message": "Token not found. Check the code and try again."}
    H.Ctx.Accounts.DeactivateToken(H.Token)
    R = await H.Client.post("/api/register-token", json={"Token": H.Token, "Name": "", "Email": ""})
    assert R.status_code == 403 and R.json()["error"] == {"code": "token_inactive",
                                                          "message": "This token has been deactivated. Contact XJet."}
    R = await H.Client.post("/api/designs", data={"prompt": "A plain band"})
    assert R.status_code == 401 and R.json()["error"]["message"] == "Access token not recognised or deactivated. Please re-register."
    assert (await H.Client.get("/api/token-status")).status_code == 401


async def test_expired_verification_link(HS):
    H = HS
    await H.Client.post("/api/register", json={"Name": "", "Email": "late@example.com"})
    Secret = _Link(_Outbox(H)[0]["html"], r'/verify\?token=([A-Za-z0-9_\-]+)"')
    Past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    H.Ctx.Accounts.Db.Execute("UPDATE accounts SET verify_expires_at = ? WHERE verify_hash = ?", (Past, _HashSecret(Secret)))
    Page = (await H.Client.get(f"/verify?token={Secret}")).text
    assert "Link expired" in Page and 'class="card bad"' in Page


async def test_only_a_finished_movie_uses_a_generation_and_quota_blocks_at_zero(tmp_path):
    H = Harness(tmp_path, AdminKey=AdminKey)
    try:
        H.Ctx.Accounts.SetQuota(H.Who.AccountId, MaxGenerations=1)
        Batch = await H.NewDesign("Band with a leaf")
        assert (await H.Client.get("/api/token-status")).json()["remaining"] == 1     # designs don't count
        await H.Proceed(Batch["design_id"], Batch["candidates"][0]["id"])
        await H.Idle()
        S = (await H.Client.get("/api/token-status")).json()
        assert (S["used"], S["remaining"]) == (1, 0)
        await H.Proceed(Batch["design_id"], Batch["candidates"][0]["id"])            # reused movie: no charge
        assert (await H.Client.get("/api/token-status")).json()["used"] == 1
        Subs = len(H.Provider.Submissions)
        R = await H.Client.post("/api/designs", data={"prompt": "Another band"})
        assert R.status_code == 402 and R.json()["error"] == {
            "code": "quota_exhausted",
            "message": "You have reached the maximum number of generations allowed for this access token."}
        assert len(H.Provider.Submissions) == Subs                                    # nothing paid was started
    finally:
        await H.Close()


async def test_failed_movie_is_not_charged(tmp_path):
    H = Harness(tmp_path)
    try:
        Batch = await H.NewDesign("Band with a leaf")
        H.Provider.Script(endpoints.Movie, "fail")
        await H.Proceed(Batch["design_id"], Batch["candidates"][0]["id"])
        await H.Idle()
        assert (await H.Client.get("/api/token-status")).json()["used"] == 0
    finally:
        await H.Close()


async def test_outbox_is_developer_only_and_never_smtp_by_default(HS):
    H = HS
    await H.Client.post("/api/register", json={"Name": "", "Email": "x@example.com"})
    assert (await H.Client.get("/api/dev/outbox")).status_code == 403
    Box = (await H.Client.get("/api/dev/outbox", headers=Dev)).json()
    assert Box["mode"] == "outbox" and Box["messages"][0]["to"] == "x@example.com"
    One = (await H.Client.get(f"/api/dev/outbox/{Box['messages'][0]['id']}", headers=Dev)).json()
    assert "/verify?token=" in One["html"]
    assert (await H.Client.get("/api/dev/outbox/..%2Fsecret", headers=Dev)).status_code == 404


async def test_links_use_base_path_and_public_origin(tmp_path, monkeypatch):
    monkeypatch.setenv("P3_PUBLIC_BASE_URL", "http://proto")
    H = Harness(tmp_path, BasePath="/JewelryB2C3")
    try:
        await H.Client.post("/api/register", json={"Name": "", "Email": "p@example.com"})
        Html = _Outbox(H)[0]["html"]
        assert 'href="http://proto/JewelryB2C3/verify?token=' in Html
        Secret = _Link(Html, r'/verify\?token=([A-Za-z0-9_\-]+)"')
        Page = (await H.Client.get(f"/verify?token={Secret}")).text
        assert 'href="http://proto/JewelryB2C3/#token=' in Page
    finally:
        await H.Close()


def test_legacy_p3_tokens_still_authenticate(tmp_path):
    H = Harness(tmp_path)
    Token, Who = H.Ctx.Accounts.IssueToken("legacy", Token="p3_LegacyMixedCase_Value")
    assert H.Ctx.Accounts.Authenticate("p3_LegacyMixedCase_Value").AccountId == Who.AccountId
