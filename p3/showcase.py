"""Hero showcase — prototype (/showcase, not linked from the site, not indexed).

The story of one real Inspiration Gallery design, read from the database for a scripted ~10 s sequence:
its prompt → its first four options → a refinement → its 360° movie → three metals → "made in real metal".
Read-only and free: nothing is generated, charged or saved. Only the design's own history is used (the batches
of the gallery master itself — never a customer's variation), and no Ring ID is part of what it returns.

The best story is chosen automatically: four ready first options, a refinement of one of them, and a real movie
of the refined result score highest. The movie always shows the ring just on screen: when the refined ring has no
movie of its own, the story shows the chosen option and its movie instead (no refinement beat). A placeholder movie
from a session run in mock mode is used only where nothing real exists; a design started from an uploaded image
scores lower, because its prompt alone does not explain the result. A gallery design that is its owner's own
variation tells the story from the design it came from. ?design=<link name> picks another one for review.
"""

import re

from p3.context import Context
from p3.gallery import ShareSlug

Metals = ("silver", "gold_18k_rose", "gold_18k_yellow")    # beat 5: silver → rose gold → 18K gold (live filters)
MadeImage = "/static/images/Angel.JPG"                    # beat 6 placeholder: a part printed in metal by XJet


def _Cap(Text: str) -> str:
    return Text[:1].upper() + Text[1:]


def ShortPrompt(Text: str, Max: int = 72) -> str:
    """The prompt as the composer types it: whole when short, else its first clause, else cut at a word."""
    T = " ".join((Text or "").split())
    if len(T) <= Max:
        return _Cap(T)
    First = re.split(r"(?<=[.!?;,])\s", T)[0].rstrip(".,;:!? ")
    if 24 <= len(First) <= Max:
        return _Cap(First)
    return _Cap(T[:Max].rsplit(" ", 1)[0].rstrip(".,;:!? ")) + "…"


def ShortInstruction(Text: str, Max: int = 40) -> str:
    T = " ".join((Text or "").split()).rstrip(".")
    return _Cap(T if len(T) <= Max else T[:Max].rsplit(" ", 1)[0] + "…")


def _Movies(Db, DesignIds: list[str]) -> dict[str, tuple[str, bool]]:
    """candidate id → (movie asset, real?). A movie made by the mock provider (request id "mockreq_…", e.g. a
    placeholder clip from a session run in mock mode) counts only when no real movie exists for that ring."""
    Out: dict[str, tuple[str, bool]] = {}
    Q = ",".join("?" * len(DesignIds))
    for M in Db.All(f"SELECT m.candidate_id, m.asset_path, m.provider_request_id FROM movies m JOIN candidates c ON c.id = m.candidate_id "
                    f"JOIN batches b ON b.id = c.batch_id WHERE b.design_id IN ({Q}) AND m.status = 'ready' "
                    f"AND m.asset_path IS NOT NULL ORDER BY m.updated_at", DesignIds):
        Real = not (M["provider_request_id"] or "").startswith("mockreq_")
        if Real or not Out.get(M["candidate_id"], ("", False))[1]:
            Out[M["candidate_id"]] = (M["asset_path"], Real)
    return Out


def _Stories(Ctx: Context) -> list[dict]:
    Db = Ctx.Db
    Out = []
    for R in Db.All("SELECT g.id AS item_id, g.design_id, g.candidate_id, g.position, d.title, d.prompt, d.share_slug, "
                    "d.source_design_id, d.owner_account_id FROM gallery_items g JOIN designs d ON d.id = g.design_id "
                    "ORDER BY g.position, g.created_at"):
        Prompt, Designs = R["prompt"], [R["design_id"]]
        Initial = Db.One("SELECT * FROM batches WHERE design_id = ? AND kind = 'initial' ORDER BY created_at LIMIT 1", (R["design_id"],))
        if Initial is None and R["source_design_id"]:
            # a variation the owner made of their own design: its first options are in the design it came from
            Src = Db.One("SELECT id, prompt, owner_account_id FROM designs WHERE id = ?", (R["source_design_id"],))
            if Src and Src["owner_account_id"] == R["owner_account_id"]:
                Initial = Db.One("SELECT * FROM batches WHERE design_id = ? AND kind = 'initial' ORDER BY created_at LIMIT 1", (Src["id"],))
                Prompt, Designs = Src["prompt"], [R["design_id"], Src["id"]]
        if Initial is None:
            continue
        Options = Db.All("SELECT id, slot, asset_path FROM candidates WHERE batch_id = ? AND status = 'ready' "
                         "AND asset_path IS NOT NULL ORDER BY slot", (Initial["id"],))[:4]
        if len(Options) < 4:
            continue
        Ids = [O["id"] for O in Options]
        Movies = _Movies(Db, Designs)
        if not Movies:
            continue
        Base = (-3 if Initial["reference_asset"] else 0) + (1 if 20 <= len(" ".join((Prompt or "").split())) <= 160 else 0)
        Variants = []
        # 1. the whole story: a refinement of one of the four, and the 360° movie of the refined ring
        for B in Db.All("SELECT * FROM batches WHERE design_id = ? AND kind = 'refine' ORDER BY created_at", (R["design_id"],)):
            if B["parent_candidate_id"] not in Ids:
                continue
            for C in Db.All("SELECT id, asset_path FROM candidates WHERE batch_id = ? AND status = 'ready' "
                            "AND asset_path IS NOT NULL ORDER BY slot", (B["id"],)):
                if C["id"] in Movies:
                    Asset, Real = Movies[C["id"]]
                    Variants.append((10 + Base - (0 if Real else 8) + (1 if C["id"] == R["candidate_id"] else 0),
                                     Ids.index(B["parent_candidate_id"]), (B, C), Asset))
        # 2. no refinement with a movie of its own: the chosen option, enlarged, then its own movie (never a movie of
        #    a different ring than the one just shown)
        for I, O in enumerate(Options):
            if O["id"] in Movies:
                Asset, Real = Movies[O["id"]]
                Variants.append((5 + Base - (0 if Real else 8) + (1 if O["id"] == R["candidate_id"] else 0), I, None, Asset))
        if not Variants:
            continue
        Score, Picked, Refined, Movie = max(Variants, key=lambda V: V[0])
        Out.append({"row": R, "prompt": Prompt, "options": Options, "picked": Picked, "refined": Refined, "movie": Movie,
                    "score": Score, "slug": R["share_slug"] or ShareSlug(R["title"])})
    return Out


def Story(Ctx: Context, Design: str | None = None) -> dict:
    Stories = _Stories(Ctx)
    Ranked = sorted(Stories, key=lambda S: (-S["score"], S["row"]["position"]))
    Choices = [{"slug": S["slug"], "title": S["row"]["title"]} for S in Ranked]
    if not Ranked:
        return {"story": None, "choices": []}
    Want = ShareSlug(Design or "")
    Best = next((S for S in Ranked if Want and Want in (S["slug"], ShareSlug(S["row"]["title"]))), Ranked[0])
    Url, Base = Ctx.AssetUrl, Ctx.Settings.BasePath
    Materials = {M["id"]: M for G in Ctx.Catalog.ToJson()["groups"] for M in G["materials"]}
    Movie = Url(Best["movie"])
    return {
        "story": {
            "title": Best["row"]["title"],
            "slug": Best["slug"],
            "prompt": ShortPrompt(Best["prompt"]),
            "options": [Url(O["asset_path"]) for O in Best["options"]],
            "picked": Best["picked"],
            "refine": ({"text": ShortInstruction(Best["refined"][0]["user_text"]), "image_url": Url(Best["refined"][1]["asset_path"])}
                       if Best["refined"] else None),
            "movie_url": Movie,
            "poster_url": Movie.replace("/assets/", "/poster/", 1),
            "metals": [Materials[I] for I in Metals if I in Materials],
            "made": {"image_url": f"{Base}{MadeImage}", "placeholder": True},
            "cta_url": f"{Base}/",
        },
        "choices": Choices,
    }
