"""Hero showcase — prototype (/showcase, not linked from the site, not indexed).

The story of one real Inspiration Gallery design, read from the database for a scripted ~10 s sequence:
its prompt → its first four options → a refinement → its 360° movie → three metals → "made in real metal".
Read-only and free: nothing is generated, charged or saved. Only the design's own history is used (the batches
of the gallery master itself — never a customer's variation), and no Ring ID is part of what it returns.

The best story is chosen automatically: four ready first options, a refinement of one of them, and a movie of
the refined result score highest; a design started from an uploaded image scores lower, because its prompt
alone does not explain the result. ?design=<link name> picks another one for review.
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


def _Stories(Ctx: Context) -> list[dict]:
    Db = Ctx.Db
    Out = []
    for R in Db.All("SELECT g.id AS item_id, g.design_id, g.candidate_id, g.position, d.title, d.prompt, d.share_slug "
                    "FROM gallery_items g JOIN designs d ON d.id = g.design_id ORDER BY g.position, g.created_at"):
        Initial = Db.One("SELECT * FROM batches WHERE design_id = ? AND kind = 'initial' ORDER BY created_at LIMIT 1", (R["design_id"],))
        if Initial is None:
            continue
        Options = Db.All("SELECT id, slot, asset_path FROM candidates WHERE batch_id = ? AND status = 'ready' "
                         "AND asset_path IS NOT NULL ORDER BY slot", (Initial["id"],))[:4]
        if len(Options) < 4:
            continue
        Ids = [O["id"] for O in Options]
        Movies = {M["candidate_id"]: M["asset_path"] for M in Db.All(
            "SELECT m.candidate_id, m.asset_path FROM movies m JOIN candidates c ON c.id = m.candidate_id "
            "JOIN batches b ON b.id = c.batch_id WHERE b.design_id = ? AND m.status = 'ready' AND m.asset_path IS NOT NULL "
            "ORDER BY m.updated_at", (R["design_id"],))}
        if not Movies:
            continue
        Refined = None                     # (batch, candidate): a refinement of one of the four, best with its own movie
        for B in Db.All("SELECT * FROM batches WHERE design_id = ? AND kind = 'refine' ORDER BY created_at", (R["design_id"],)):
            if B["parent_candidate_id"] not in Ids:
                continue
            Cands = Db.All("SELECT id, asset_path FROM candidates WHERE batch_id = ? AND status = 'ready' "
                           "AND asset_path IS NOT NULL ORDER BY slot", (B["id"],))
            if not Cands:
                continue
            Pick = (next((C for C in Cands if C["id"] in Movies), None) or next((C for C in Cands if C["id"] == R["candidate_id"]), None)
                    or Cands[0])
            if Refined is None or (Pick["id"] in Movies and Refined[1]["id"] not in Movies):
                Refined = (B, Pick)
        Parent = Refined[0]["parent_candidate_id"] if Refined else (R["candidate_id"] if R["candidate_id"] in Ids else Ids[0])
        MovieOf = (Refined[1]["id"] if Refined and Refined[1]["id"] in Movies
                   else R["candidate_id"] if R["candidate_id"] in Movies
                   else Parent if Parent in Movies else next(reversed(Movies)))
        Score = 4 + (3 if Refined else 0) + (3 if Refined and MovieOf == Refined[1]["id"] else 1)
        if Initial["reference_asset"]:
            Score -= 3                     # started from an uploaded image: the typed words alone did not make it
        if 20 <= len(" ".join((R["prompt"] or "").split())) <= 160:
            Score += 1
        Out.append({"row": R, "options": Options, "picked": Ids.index(Parent), "refined": Refined, "movie": Movies[MovieOf],
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
            "prompt": ShortPrompt(Best["row"]["prompt"]),
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
