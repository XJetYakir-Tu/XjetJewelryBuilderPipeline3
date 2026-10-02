"""Inspiration Gallery — real XJet designs as an entry point into the design flow.

An admin adds a design to the gallery; its tile shows the design's selected image. "Make it yours"
gives the customer their own copy of that image's batch (all four options, the gallery image
selected), so they continue exactly as with a design of their own: 360° movie, Customize, Bag.

The copy is a new design owned by the customer with no AI request behind it (nothing is charged and
no quota is used); the XJet original is never touched. The origin is recorded on the copy
(designs.source_design_id / source_candidate_id) and as a `gallery_started` session event.
"""

from p3 import assets
from p3 import ringids as RingIds
from p3 import sessions as Sessions
from p3.accounts import Principal
from p3.context import Context, HttpError
from p3.db import NewId, Now


class GalleryService:
    def __init__(self, Ctx: Context):
        self.Ctx = Ctx

    # ── reading ──────────────────────────────────────────────────────────
    def _Rows(self) -> list[dict]:
        return self.Ctx.Db.All(
            "SELECT g.*, d.title, d.prompt, d.ring_no, c.asset_path, c.status AS candidate_status, c.batch_id "
            "FROM gallery_items g JOIN designs d ON d.id = g.design_id JOIN candidates c ON c.id = g.candidate_id "
            "ORDER BY g.position, g.created_at")

    def List(self) -> list[dict]:
        """Public tiles: only the title and the image — nothing about the design's owner."""
        return [{"id": R["id"], "title": R["title"], "image_url": self.Ctx.AssetUrl(R["asset_path"])}
                for R in self._Rows() if R["candidate_status"] == "ready" and R["asset_path"]]

    def AdminList(self) -> list[dict]:
        Rows = self._Rows()
        Refs = RingIds.CandidateRefs(self.Ctx.Db, [R["design_id"] for R in Rows])
        Starts = {S["source_design_id"]: S["n"] for S in self.Ctx.Db.All(
            "SELECT source_design_id, COUNT(*) AS n FROM designs WHERE source_design_id IS NOT NULL GROUP BY source_design_id")}
        return [{"id": R["id"], "design_id": R["design_id"], "candidate_id": R["candidate_id"],
                 "ring_id": Refs.get(R["candidate_id"]), "design_ring_id": RingIds.DesignRef(R["ring_no"]),
                 "title": R["title"], "prompt": R["prompt"], "image_url": self.Ctx.AssetUrl(R["asset_path"]),
                 "ready": R["candidate_status"] == "ready" and bool(R["asset_path"]),
                 "position": R["position"], "created_at": R["created_at"], "created_by": R["created_by"],
                 "starts": Starts.get(R["design_id"], 0)} for R in Rows]

    def ForDesign(self, DesignId: str) -> dict | None:
        return next((I for I in self.AdminList() if I["design_id"] == DesignId), None)

    # ── curation (admin) ─────────────────────────────────────────────────
    def Add(self, DesignId: str, CandidateId: str | None, By: str) -> dict:
        """Add a design (its selected image, or the given option) to the gallery, or change its image."""
        Db = self.Ctx.Db
        D = Db.One("SELECT * FROM designs WHERE id = ?", (DesignId,))
        if D is None:
            raise HttpError(404, "design_not_found", "Design not found.")
        Cid = CandidateId or D["selected_candidate_id"]
        if not Cid:
            raise HttpError(409, "no_selected_image", "This design has no selected image. Choose an option to show first.")
        C = Db.One("SELECT c.* FROM candidates c JOIN batches b ON b.id = c.batch_id WHERE c.id = ? AND b.design_id = ?",
                   (Cid, DesignId))
        if C is None or C["status"] != "ready" or not C["asset_path"]:
            raise HttpError(409, "image_not_ready", "Only a ready image can be shown in the gallery.")
        T = Now()
        Existing = Db.One("SELECT id FROM gallery_items WHERE design_id = ?", (DesignId,))
        if Existing:
            Db.Execute("UPDATE gallery_items SET candidate_id = ?, created_at = ?, created_by = ? WHERE id = ?",
                       (Cid, T, By, Existing["id"]))
            return self._Item(Existing["id"])
        Position = int(Db.One("SELECT COALESCE(MAX(position), 0) AS p FROM gallery_items")["p"]) + 1
        Id = NewId("gal")
        Db.Execute("INSERT INTO gallery_items (id, design_id, candidate_id, position, created_at, created_by) "
                   "VALUES (?,?,?,?,?,?)", (Id, DesignId, Cid, Position, T, By))
        return self._Item(Id)

    def _Item(self, ItemId: str) -> dict:
        I = next((X for X in self.AdminList() if X["id"] == ItemId), None)
        if I is None:
            raise HttpError(404, "gallery_item_not_found", "Gallery item not found.")
        return I

    def Remove(self, ItemId: str) -> None:
        self._Item(ItemId)
        self.Ctx.Db.Execute("DELETE FROM gallery_items WHERE id = ?", (ItemId,))
        self._Renumber()

    def Move(self, ItemId: str, Direction: str) -> None:
        Ids = [X["id"] for X in self.AdminList()]
        if ItemId not in Ids:
            raise HttpError(404, "gallery_item_not_found", "Gallery item not found.")
        I = Ids.index(ItemId)
        J = I - 1 if Direction == "up" else I + 1
        if 0 <= J < len(Ids):
            Ids[I], Ids[J] = Ids[J], Ids[I]
        self._Renumber(Ids)

    def _Renumber(self, Ids: list[str] | None = None) -> None:
        Ids = Ids or [X["id"] for X in self.AdminList()]
        with self.Ctx.Db.Transaction() as Conn:
            for N, Id in enumerate(Ids, start=1):
                Conn.execute("UPDATE gallery_items SET position = ? WHERE id = ?", (N, Id))

    # ── "Make it yours" (customer) ───────────────────────────────────────
    def Start(self, Who: Principal, ItemId: str, ClientRequestId: str | None = None) -> str:
        """Copy the gallery image's batch into a new design owned by the customer; returns its id."""
        Db, S = self.Ctx.Db, self.Ctx.Settings
        if ClientRequestId:                                   # a double tap must not make two designs
            Existing = Db.One("SELECT id FROM designs WHERE owner_account_id = ? AND client_request_id = ?",
                              (Who.AccountId, ClientRequestId))
            if Existing:
                return Existing["id"]
        R = Db.One("SELECT g.*, d.title, d.prompt, c.batch_id, c.status AS candidate_status FROM gallery_items g "
                   "JOIN designs d ON d.id = g.design_id JOIN candidates c ON c.id = g.candidate_id WHERE g.id = ?", (ItemId,))
        if R is None or R["candidate_status"] != "ready":
            raise HttpError(404, "gallery_item_not_found", "This gallery design is no longer available.")
        # The customer already made this design theirs: open that one (moved to the top of My Designs), no duplicate.
        Mine = Db.One("SELECT id FROM designs WHERE owner_account_id = ? AND source_design_id = ? ORDER BY created_at DESC LIMIT 1",
                      (Who.AccountId, R["design_id"]))
        if Mine:
            Db.Execute("UPDATE designs SET updated_at = ? WHERE id = ?", (Now(), Mine["id"]))
            Sessions.Record(self.Ctx, Who.AccountId, "design_opened", Mine["id"], via="gallery")
            return Mine["id"]
        Batch = Db.One("SELECT * FROM batches WHERE id = ?", (R["batch_id"],))
        Cands = Db.All("SELECT * FROM candidates WHERE batch_id = ? AND status = 'ready' AND asset_path IS NOT NULL "
                       "ORDER BY slot", (R["batch_id"],))
        if not any(C["id"] == R["candidate_id"] for C in Cands):
            raise HttpError(409, "image_not_ready", "This gallery design is no longer available.")
        NewDesign, NewBatch, T = NewId("dsg"), NewId("bat"), Now()
        Copies = []                                           # the customer gets their own files
        for C in Cands:
            Nid = NewId("cand")
            Ext = C["asset_path"].rsplit(".", 1)[-1]
            Rel = f"designs/{NewDesign}/candidates/{Nid}.{Ext}"
            assets.WriteAtomic(S.AssetsDir, Rel, assets.Resolve(S.AssetsDir, C["asset_path"]).read_bytes())
            Copies.append((C, Nid, Rel))
        SelectedNew = next(Nid for C, Nid, _ in Copies if C["id"] == R["candidate_id"])
        # 360° movies already made for these options travel with them: Customize shows the movie at once
        # instead of generating (and paying for) the same movie again.
        MovieCopies = []
        for C, Nid, _ in Copies:
            Mv = Db.One("SELECT * FROM movies WHERE candidate_id = ? AND status = 'ready' AND asset_path IS NOT NULL "
                        "ORDER BY created_at DESC LIMIT 1", (C["id"],))
            if Mv:
                Mid = NewId("mov")
                Rel = f"designs/{NewDesign}/movies/{Mid}.mp4"
                assets.WriteAtomic(S.AssetsDir, Rel, assets.Resolve(S.AssetsDir, Mv["asset_path"]).read_bytes())
                MovieCopies.append((Mv, Nid, Mid, Rel))
        with Db.Transaction() as Conn:
            Conn.execute("INSERT INTO designs (id, owner_account_id, title, prompt, selected_candidate_id, client_request_id, "
                         "created_at, updated_at, ai_mode, source_design_id, source_candidate_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (NewDesign, Who.AccountId, R["title"], R["prompt"], SelectedNew, ClientRequestId, T, T,
                          self.Ctx.Provider.Name, R["design_id"], R["candidate_id"]))
            Conn.execute("INSERT INTO batches (id, design_id, kind, parent_candidate_id, user_text, effective_prompt, endpoint, "
                         "reference_asset, desired_count, config_version, client_request_id, created_at) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (NewBatch, NewDesign, "initial", None, Batch["user_text"], Batch["effective_prompt"],
                          Batch["endpoint"], None, len(Copies), Batch["config_version"], None, T))
            for C, Nid, Rel in Copies:
                Conn.execute("INSERT INTO candidates (id, batch_id, slot, status, seed, attempts, asset_path, content_sha256, "
                             "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (Nid, NewBatch, C["slot"], "ready", C["seed"], 0, Rel, C["content_sha256"], T, T))
            for Mv, Nid, Mid, Rel in MovieCopies:                # no provider request: nothing charged, no quota
                Conn.execute("INSERT INTO movies (id, candidate_id, config_version, endpoint, status, provider_request_id, "
                             "asset_path, created_at, updated_at) VALUES (?,?,?,?,'ready',NULL,?,?,?)",
                             (Mid, Nid, Mv["config_version"], Mv["endpoint"], Rel, T, T))
        Sessions.Record(self.Ctx, Who.AccountId, "gallery_started", NewDesign, gallery_item_id=ItemId,
                        source_design_id=R["design_id"], source_ring_id=RingIds.CandidateRef(Db, R["candidate_id"]))
        return NewDesign
