"""Saved designs: list and full state (for reload recovery)."""

from p3.context import Context
from p3.customize import CustomizeService
from p3.images import ImageService


class DesignService:
    def __init__(self, Ctx: Context, Images: ImageService, Customize: CustomizeService):
        self.Ctx = Ctx
        self.Images = Images
        self.Customize = Customize

    def List(self, Token: str) -> list[dict]:
        Out = []
        for D in self.Ctx.Db.All("SELECT * FROM designs WHERE token = ? ORDER BY updated_at DESC LIMIT 100", (Token,)):
            Thumb = None
            if D["selected_candidate_id"]:
                C = self.Ctx.Db.One("SELECT asset_path FROM candidates WHERE id = ?", (D["selected_candidate_id"],))
                Thumb = self.Ctx.AssetUrl(C["asset_path"]) if C else None
            if Thumb is None:
                C = self.Ctx.Db.One("SELECT c.asset_path FROM candidates c JOIN batches b ON b.id = c.batch_id "
                                    "WHERE b.design_id = ? AND c.status = 'ready' ORDER BY b.created_at DESC, c.slot "
                                    "LIMIT 1", (D["id"],))
                Thumb = self.Ctx.AssetUrl(C["asset_path"]) if C else None
            Out.append({"id": D["id"], "title": D["title"], "thumbnail_url": Thumb,
                        "updated_at": D["updated_at"], "created_at": D["created_at"]})
        return Out

    def Get(self, Token: str, DesignId: str) -> dict:
        D = self.Images.RequireDesign(Token, DesignId)
        Batches = [self.Images.GetBatch(B["id"]) for B in self.Ctx.Db.All(
            "SELECT id FROM batches WHERE design_id = ? ORDER BY created_at", (DesignId,))]
        Customization = None
        if D["selected_candidate_id"]:
            Row = self.Ctx.Db.One("SELECT * FROM customizations WHERE design_id = ? AND candidate_id = ?",
                                  (DesignId, D["selected_candidate_id"]))
            Customization = self.Customize.ToJson(Row) if Row else None
        return {"id": D["id"], "title": D["title"], "prompt": D["prompt"],
                "selected_candidate_id": D["selected_candidate_id"], "created_at": D["created_at"],
                "updated_at": D["updated_at"], "batches": Batches, "customization": Customization}
