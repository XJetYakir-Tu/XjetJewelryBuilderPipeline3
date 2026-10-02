"""Local ring names (deterministic word rules, no AI call) and the mock/live mode reported to the UI."""

import re

import pytest

from p3.naming import Forbidden, ProductName, RingName, Suggestions


@pytest.mark.parametrize("Prompt,Expected", [
    ("A slim band that twists into a small open leaf at the front", "Laurel Twist"),
    ("serpent wrapped signet", "Serpent Twist"),
    ("A 3D printable gold ring designed in the shape of an anaconda, featuring intricate scales mimicking its skin "
     "pattern, with a striking open-mouth pose.", "Serpent Scale"),
    ("delicate e bands twisted toghater and connected", "Fil Twist"),
    ("delicate band lattice with delicate motif on the head", "Fil Lattice"),
    ("Silver Ring with a delicate heart shape motif", "Amour Line"),
    ("two crossover bands with a floral lattice", "Rose Cross"),
    ("geometric hexagon signet, bold and matte", "Signet Facet"),
    ("A band with a crescent moon", "Luna Crescent"),
    ("Ring inspired by Haaland", "Haaland"),                 # a proper noun stands alone
    ("a chunky statement band", "Bold Line"),                # a style word never stands alone
])
def test_names_come_from_the_prompt_keywords(Prompt, Expected):
    assert ProductName(Prompt) == Expected


Prompts = [
    "A modern luxury ring with clean lines, smooth curves, and perfect proportions.", "simple delicate band",
    "A heavy chain bracelet with links featuring a subtle wave contour", "Tiffany style gold 14k ring size 7 in stainless steel",
    "snake ring", "a wrap ring", "xyz qqq", "A ring. Add a heart", "halo bloom flowers around a stone", "serpent wrapped signet",
]


@pytest.mark.parametrize("Prompt", Prompts)
def test_names_are_short_premium_and_free_of_banned_words(Prompt):
    Name = ProductName(Prompt)
    Words = Name.split()
    assert 1 <= len(Words) <= 2, Name
    assert all(W[0].isupper() for W in Words) and not any(W.lower() in Forbidden for W in Words), Name
    assert "the" not in Name.lower().split() and "ring" not in Name.lower() and not re.search(r"\d", Name), Name


def test_fallback_name_is_deterministic_and_within_the_rules():
    assert ProductName("xyz qqq") == ProductName("xyz qqq")
    assert 1 <= len(ProductName("xyz qqq").split()) <= 2


def test_a_taken_name_gets_another_word_combination_before_any_number():
    Taken, Names = set(), []
    for _ in range(12):
        Name = RingName("delicate e bands twisted toghater and connected", Taken)
        Taken.add(Name.lower())
        Names.append(Name)
    assert Names[:5] == ["Fil Twist", "Fil Spiral", "Fil Helix", "Fil Rope", "Fil Coil"]
    assert len(set(N.lower() for N in Names)) == 12 and not any(N.split()[-1] in ("II", "III", "IV") for N in Names)
    # a number appears only once every word combination is used up
    Taken = set()
    for _ in range(400):
        Taken.add(RingName("xyz qqq", Taken).lower())
    assert any(N.endswith(" ii") for N in Taken)


def test_variations_keep_their_lineage_in_two_words():
    Master = "delicate e bands twisted toghater and connected"
    assert RingName(Master, {"fil twist"}, Lineage="Fil Twist", Instruction="make it a lattice") == "Fil Lattice"
    assert RingName(Master, {"fil twist", "fil lattice"}, Lineage="Fil Twist", Instruction="make it a lattice") == "Fil Mesh"
    assert RingName(Master, {"fil twist"}, Lineage="Fil Twist", Instruction="thinner") == "Fil Line"
    assert RingName(Master, {"fil twist"}, Lineage="Fil Twist", Instruction="twist it more") == "Fil Line"    # never "Fil Spiral"
    assert RingName("heart band", {"amour curve"}, Lineage="Amour Curve", Instruction="add a wave") == "Amour Wave"
    assert RingName("anaconda", set(), Lineage="The Orion Ring", Instruction="open the mouth wider") == "Orion Open"


def test_suggestions_are_free_names_without_numbers():
    S = Suggestions("delicate e bands twisted toghater", {"fil twist"}, N=4)
    assert S == ["Fil Spiral", "Fil Helix", "Fil Rope", "Fil Coil"]


async def test_design_title_uses_the_local_name(H):
    Batch = await H.NewDesign("A band with a crescent moon")
    assert (await H.Design(Batch["design_id"]))["title"] == "Luna Crescent"


async def test_health_reports_mock_mode(H):
    R = (await H.Client.get("/api/health")).json()
    assert R["mode"] == "mock" and R["provider"] == "mock"


async def test_ui_assets_are_revalidated_not_cached_stale(H):
    for Path in ("/", "/static/app.js", "/static/videos/atelier-loading.mp4"):
        R = await H.Client.get(Path)
        assert R.status_code == 200 and R.headers["cache-control"] == "no-cache", Path


async def test_page_references_versioned_script(H):
    import re
    Html = (await H.Client.get("/")).text
    Match = re.search(r'src="/static/app\.js\?v=(\d+)"', Html)
    assert Match, "app.js must be versioned so browsers never run a stale cached copy"
    assert (await H.Client.get(f"/static/app.js?v={Match.group(1)}")).status_code == 200
