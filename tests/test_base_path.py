"""Running under P3_BASE_PATH=/JewelryB2C3: nothing may escape the prefix.

Behind proto, root paths (/api/, /static/, /admin/, /debug …) belong to Pipeline 2,
so any root-relative URL emitted by P3 would silently hit the wrong application.
"""

import json
import re
from pathlib import Path

import pytest

from p3.settings import NormalizeBasePath
from tests.conftest import DevProfile, Harness

Base = "/JewelryB2C3"
WebDir = Path(__file__).resolve().parent.parent / "web"
AdminKey = "base-path-admin-key"


@pytest.fixture
async def HP(tmp_path):
    Obj = Harness(tmp_path, Profile=DevProfile, AllowUnapproved=True, BasePath=Base, AdminKey=AdminKey)
    yield Obj
    await Obj.Close()


def _RootClient(H):
    import httpx
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=H.App), base_url="http://p3.test")


def _LocalUrls(Html: str) -> list[str]:
    """Every root-relative URL in src/href/content attributes and quoted string literals."""
    Found = re.findall(r'''(?:src|href|content|action|poster)\s*=\s*["'](/[^"']*)["']''', Html)
    # /api/... literals are arguments of the prefixing request helpers (checked by the script test below).
    Found += re.findall(r'''["'](/(?:static|assets|dev)\b[^"']*)["']''', Html)
    return [U for U in Found if not U.startswith("//")]


@pytest.mark.parametrize("Raw,Expected", [("", ""), ("/", ""), ("JewelryB2C3", "/JewelryB2C3"),
                                          ("/JewelryB2C3/", "/JewelryB2C3"), ("/a/b", "/a/b")])
def test_base_path_normalisation(Raw, Expected):
    assert NormalizeBasePath(Raw) == Expected


def test_base_path_rejects_unsafe_values():
    for Bad in ("/x y", "/../etc", "/a?b", "/<script>"):
        with pytest.raises(ValueError):
            NormalizeBasePath(Bad)


async def test_redirects_and_root_isolation(HP):
    async with _RootClient(HP) as Root:
        R = await Root.get(Base, follow_redirects=False)
        assert R.status_code == 308 and R.headers["location"] == Base + "/"
        R = await Root.get("/", follow_redirects=False)
        assert R.status_code in (307, 308) and R.headers["location"] == Base + "/"
        for Path_ in ("/api/health", "/static/app.js", "/dev", "/assets/x.png", "/api/dev/status"):
            assert (await Root.get(Path_)).status_code == 404, Path_      # nothing served at the root
    R = await HP.Client.get("/api/health")
    assert R.status_code == 200 and R.json()["base_path"] == Base and R.json()["mode"] == "mock"


@pytest.mark.parametrize("Page", ["/", "/dev"])
async def test_served_pages_reference_only_prefixed_urls(HP, Page):
    Html = (await HP.Client.get(Page)).text
    assert f'<meta name="p3-base" content="{Base}">' in Html
    assert "{{BASE}}" not in Html
    Urls = _LocalUrls(Html)
    assert Urls, "expected local asset URLs in the page"
    Escaping = [U for U in Urls if not (U == Base or U.startswith(Base + "/"))]
    assert Escaping == [], f"URLs escaping {Base}: {Escaping}"
    for U in {U.split("?")[0] for U in Urls if "/static/" in U}:
        assert (await HP.Client.get(U.removeprefix(Base))).status_code == 200, U


def test_scripts_route_every_request_through_the_base():
    """Static guard: browser code never fetches or links a root path directly."""
    for Name in ("app.js", "index.html", "dev.html"):
        Text = (WebDir / Name).read_text(encoding="utf-8")
        for M in re.finditer(r"fetch\(\s*([^,)]+)", Text):
            Arg = M.group(1).strip()
            assert Arg.startswith("url(") or Arg.startswith("BASE"), f"{Name}: fetch({Arg}) bypasses the base path"
        Hard = re.findall(r'''(?<![{A-Za-z0-9_}])["'](/(?:api|static|assets|dev)\b[^"']*)["']''', Text)
        if Name == "app.js":
            # In app.js, /api/... literals are only valid as arguments of api()/devRequest(), which prefix them.
            Calls = re.findall(r'''(?:api|devRequest)\(\s*'[A-Z]+'\s*,\s*[`'"](/api/[^`'"]*)''', Text)
            Hard = [U for U in Hard if U not in Calls and not U.startswith("/api/")]
        elif Name == "dev.html":
            Hard = [U for U in Hard if not U.startswith("/api/")]            # passed to req(), which prefixes BASE
        assert Hard == [], f"{Name}: hard-coded root URLs {Hard}"


def _AllUrls(Obj) -> list[str]:
    Out = []
    if isinstance(Obj, dict):
        for K, V in Obj.items():
            if isinstance(V, str) and (K.endswith("_url") or K == "url") and V:
                Out.append(V)
            else:
                Out += _AllUrls(V)
    elif isinstance(Obj, list):
        for V in Obj:
            Out += _AllUrls(V)
    return Out


async def test_full_flow_under_prefix_never_emits_root_urls(HP):
    H = HP
    Batch = await H.NewDesign("Twisted band with a leaf")
    Cand = Batch["candidates"][0]
    R = await H.Client.post(f"/api/designs/{Batch['design_id']}/batches",
                            json={"parent_candidate_id": Cand["id"], "instruction": "Thinner band please"})
    await H.Idle()
    Cus = await H.Proceed(Batch["design_id"], Cand["id"])
    await H.Idle()
    await H.Client.patch(f"/api/customizations/{Cus['id']}", json={"ring_size": 7})
    Bag = (await H.Client.post("/api/bag", json={"customization_id": Cus["id"]})).json()
    Mesh = (await H.Client.post(f"/api/dev/candidates/{Cand['id']}/meshes", json={},
                                headers={"Authorization": f"Bearer {AdminKey}"})).json()
    await H.Idle()
    Responses = [
        (await H.Client.get(f"/api/designs/{Batch['design_id']}")).json(),
        (await H.Client.get(f"/api/batches/{R.json()['id']}")).json(),
        (await H.Client.get(f"/api/customizations/{Cus['id']}")).json(),
        (await H.Client.get("/api/designs")).json(), Bag,
        (await H.Client.get("/api/dev/candidates", headers={"Authorization": f"Bearer {AdminKey}"})).json(),
    ]
    Urls = _AllUrls(Responses)
    assert any("/movies/" in U for U in Urls) and any("/candidates/" in U for U in Urls)
    Escaping = [U for U in Urls if not U.startswith(Base + "/")]
    assert Escaping == [], f"API responses contain URLs outside {Base}: {Escaping[:5]}"
    for U in {U for U in Urls if "/assets/" in U}:                      # and they all actually load
        assert (await H.Client.get(U.removeprefix(Base))).status_code == 200, U
    D = await H.Client.get(f"/api/dev/meshes/{Mesh['id']}/download?kind=stl", headers={"Authorization": f"Bearer {AdminKey}"})
    assert D.status_code == 200 and len(D.content) > 84
    # Mock mode end to end: every call went to the mock provider.
    assert H.Provider.Name == "mock" and len(H.Provider.Submissions) == 4 + 4 + 1 + 1


async def test_no_cache_headers_apply_under_prefix(HP):
    for P in ("/", "/static/app.js"):
        assert (await HP.Client.get(P)).headers.get("cache-control") == "no-cache", P
