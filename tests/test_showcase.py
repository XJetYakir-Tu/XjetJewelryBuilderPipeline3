"""Hero showcase prototype (/showcase): built from one real gallery design's own history — prompt, the first four
options, a refinement and its 360° movie — read-only, not linked from the site, not indexed, no Ring IDs."""

import re

from p3.showcase import ShortInstruction, ShortPrompt
from tests.test_gallery import HG, Admin  # noqa: F401 — the fixture is used by name


def test_prompt_and_instruction_are_shortened_for_the_composer():
    assert ShortPrompt("simple delicate band") == "Simple delicate band"
    assert ShortPrompt("Silver Ring with a delicate heart shape motif, the ring should be a very thin band with a lattice") \
        == "Silver Ring with a delicate heart shape motif"
    Long = "A modern luxury ring with clean lines smooth curves and perfect proportions designed for everyday wear and evening"
    assert ShortPrompt(Long).endswith("…") and len(ShortPrompt(Long)) <= 73 and not ShortPrompt(Long)[:-1].endswith(" ")
    assert ShortInstruction("change to lattice") == "Change to lattice"
    assert ShortInstruction("make it thinner.") == "Make it thinner"


async def _Story(H, Prompt="A slim band with a heart motif, delicate and light"):
    """A gallery master with the whole story: four options, a refinement of option C, a movie of the refined ring."""
    B = await H.NewDesign(Prompt)
    Did, Cands = B["design_id"], B["candidates"]
    R = await H.Client.post(f"/api/designs/{Did}/batches", json={"parent_candidate_id": Cands[2]["id"], "instruction": "change to lattice"})
    assert R.status_code == 200, R.text
    await H.Idle()
    Ref = (await H.Client.get(f"/api/batches/{R.json()['id']}")).json()
    await H.Proceed(Did, Ref["candidates"][1]["id"])                      # the movie of the refined ring
    await H.Idle()
    G = await H.Client.post("/api/admin/gallery", json={"design_id": Did}, headers=Admin)
    assert G.status_code == 200, G.text
    return Did, Cands, Ref


async def test_showcase_tells_the_story_of_a_real_gallery_design(HG):
    H = HG
    Anon = {"X-Access-Token": "NOPE00"}
    assert (await H.Client.get("/api/showcase", headers=Anon)).json() == {"story": None, "choices": []}
    Did, Cands, Ref = await _Story(H)
    R = await H.Client.get("/api/showcase", headers=Anon)                 # public, read-only
    assert R.status_code == 200, R.text
    S = R.json()["story"]
    Title = (await H.Design(Did))["title"]
    assert S["title"] == Title and S["prompt"] == "A slim band with a heart motif, delicate and light"   # short: whole
    assert S["options"] == [C["image_url"] for C in Cands] and S["picked"] == 2
    assert S["refine"] == {"text": "Change to lattice", "image_url": Ref["candidates"][1]["image_url"]}
    Movie = H.Ctx.Db.One("SELECT asset_path FROM movies WHERE candidate_id = ? AND status = 'ready'", (Ref["candidates"][1]["id"],))
    assert S["movie_url"] == H.Ctx.AssetUrl(Movie["asset_path"]) and S["poster_url"] == S["movie_url"].replace("/assets/", "/poster/")
    assert [M["id"] for M in S["metals"]] == ["silver", "gold_18k_rose", "gold_18k_yellow"] and all(M["ramp"] for M in S["metals"])
    assert S["made"]["placeholder"] and S["made"]["image_url"].endswith("/static/images/Angel.JPG") and S["cta_url"] == H.Base + "/"
    assert "R-10" not in str(R.json())                                    # no Ring IDs in the marketing story
    # nothing was written: still one design, one gallery tile, no new batches or movies
    Counts = lambda: tuple(H.Ctx.Db.One(f"SELECT COUNT(*) AS n FROM {T}")["n"] for T in ("designs", "batches", "movies", "gallery_uses"))
    Before = Counts()
    await H.Client.get("/api/showcase", headers=Anon)
    assert Counts() == Before


async def test_the_fullest_story_wins_and_another_can_be_picked(HG):
    H = HG
    # a gallery design without a refinement (movie of the gallery image) …
    B = await H.NewDesign("A plain polished band")
    await H.Proceed(B["design_id"], B["candidates"][0]["id"])
    await H.Idle()
    await H.Client.post("/api/admin/gallery", json={"design_id": B["design_id"]}, headers=Admin)
    Plain = (await H.Design(B["design_id"]))["title"]
    S = (await H.Client.get("/api/showcase")).json()
    assert S["story"]["title"] == Plain and S["story"]["refine"] is None and S["story"]["picked"] == 0
    # … loses to one with the whole story, which can still be picked by its link name
    Did, _, _ = await _Story(H)
    Full = (await H.Design(Did))["title"]
    R = (await H.Client.get("/api/showcase")).json()
    assert R["story"]["title"] == Full and [C["title"] for C in R["choices"]] == [Full, Plain]
    Slug = next(C["slug"] for C in R["choices"] if C["title"] == Plain)
    assert (await H.Client.get(f"/api/showcase?design={Slug}")).json()["story"]["title"] == Plain
    assert (await H.Client.get("/api/showcase?design=nothing-like-this")).json()["story"]["title"] == Full
    # a design without a movie or with fewer than four ready options is not a story
    B3 = await H.NewDesign("No movie yet")
    await H.Client.put(f"/api/designs/{B3['design_id']}/selection", json={"candidate_id": B3["candidates"][0]["id"]})
    await H.Client.post("/api/admin/gallery", json={"design_id": B3["design_id"]}, headers=Admin)
    assert len((await H.Client.get("/api/showcase")).json()["choices"]) == 2


async def test_showcase_page_is_a_standalone_noindex_prototype(HG):
    H = HG
    R = await H.Client.get("/showcase")
    assert R.status_code == 200 and R.headers["x-robots-tag"] == "noindex, nofollow" and R.headers["cache-control"] == "no-cache"
    Html = R.text
    assert '<meta name="robots" content="noindex, nofollow">' in Html and "prototype · not live" in Html
    assert re.search(r'src="' + re.escape(H.Base) + r'/static/metal\.js\?v=\d+"', Html)     # the site's own metal filters
    assert re.search(r'href="' + re.escape(H.Base) + r'/static/vendor/fonts\.css\?v=\d+"', Html)
    for Host in ("cdn.tailwindcss.com", "unpkg.com", "jsdelivr.net", "googleapis.com"):
        assert Host not in Html
    Index = (await H.Client.get("/")).text
    assert "/showcase" not in Index                                       # not linked from the site
    assert (await H.Client.get("/static/metal.js")).status_code == 200
