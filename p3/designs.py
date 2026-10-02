"""Saved designs: list and full state (for reload recovery)."""

from p3 import ringids as RingIds
from p3.accounts import Principal
from p3.context import Context
from p3.customize import CustomizeService
from p3.images import ImageService


class DesignService:
    def __init__(self, Ctx: Context, Images: ImageService, Customize: CustomizeService):
        self.Ctx = Ctx
        self.Images = Images
        self.Customize = Customize

    def _Thumb(self, DesignId: str, SelectedId: str | None) -> str | None:
        if SelectedId:
            C = self.Ctx.Db.One("SELECT asset_path FROM candidates WHERE id = ?", (SelectedId,))
            if C and C["asset_path"]:
                return self.Ctx.AssetUrl(C["asset_path"])
        C = self.Ctx.Db.One("SELECT c.asset_path FROM candidates c JOIN batches b ON b.id = c.batch_id "
                            "WHERE b.design_id = ? AND c.status = 'ready' ORDER BY b.created_at DESC, c.slot LIMIT 1", (DesignId,))
        return self.Ctx.AssetUrl(C["asset_path"]) if C else None

    def List(self, Who: Principal) -> list[dict]:
        """The customer's own designs and the shared gallery designs they started, latest activity first."""
        Out = []
        for D in self.Ctx.Db.All("SELECT * FROM designs WHERE owner_account_id = ? ORDER BY updated_at DESC LIMIT 100", (Who.AccountId,)):
            Out.append({"id": D["id"], "title": D["title"], "thumbnail_url": self._Thumb(D["id"], D["selected_candidate_id"]),
                        "updated_at": D["updated_at"], "created_at": D["created_at"], "shared": False,
                        "origin": "gallery" if D.get("source_design_id") else "prompt"})
        for U in self.Ctx.Db.All("SELECT u.*, d.title FROM gallery_uses u JOIN designs d ON d.id = u.design_id "
                                 "WHERE u.owner_account_id = ? ORDER BY u.last_active_at DESC LIMIT 100", (Who.AccountId,)):
            Out.append({"id": U["design_id"], "title": U["title"],
                        "thumbnail_url": self._Thumb(U["design_id"], U["selected_candidate_id"] or U["source_candidate_id"]),
                        "updated_at": U["last_active_at"], "created_at": U["started_at"], "shared": True, "origin": "gallery"})
        Out.sort(key=lambda X: X["updated_at"] or "", reverse=True)
        return Out[:100]

    def Get(self, Who: Principal, DesignId: str) -> dict:
        D = self.Images.RequireDesign(Who, DesignId)
        Batches = [self.Images.GetBatch(B["id"]) for B in self.Ctx.Db.All(
            "SELECT id FROM batches WHERE design_id = ? ORDER BY created_at", (DesignId,))]
        # A shared XJet master design: the customer's own selection and choices, the images shared.
        Use = None if D["owner_account_id"] == Who.AccountId else self.Ctx.Db.One(
            "SELECT * FROM gallery_uses WHERE design_id = ? AND owner_account_id = ?", (DesignId, Who.AccountId))
        Selected = (Use["selected_candidate_id"] or Use["source_candidate_id"]) if Use else D["selected_candidate_id"]
        Customization = None
        if Selected:
            Row = self.Ctx.Db.One("SELECT * FROM customizations WHERE owner_account_id = ? AND design_id = ? AND candidate_id = ?",
                                  (Who.AccountId, DesignId, Selected))
            Customization = self.Customize.ToJson(Row) if Row else None
        SourceCandidate = Use["source_candidate_id"] if Use else D.get("source_candidate_id")
        return {"id": D["id"], "title": D["title"], "prompt": D["prompt"],
                "selected_candidate_id": Selected, "created_at": Use["started_at"] if Use else D["created_at"],
                "updated_at": Use["last_active_at"] if Use else D["updated_at"], "batches": Batches,
                "customization": Customization, "shared": bool(Use),
                "origin": "gallery" if (Use or D.get("source_design_id")) else "prompt",
                "source_ring_id": RingIds.CandidateRef(self.Ctx.Db, SourceCandidate) if SourceCandidate else None}
