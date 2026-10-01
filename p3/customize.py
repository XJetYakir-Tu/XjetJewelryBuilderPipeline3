"""Customize (selection → material/size/quantity → quote) and the bag.

The bag is a server-side, per-access-token list of quote snapshots. There is NO
checkout, payment, order record or manufacturing hand-off in Pipeline 3 (spec
section 12); the UI states this plainly.

Purchase rules are enforced here, not just in the browser (spec 4.6):
  * Luxury → never purchasable, never priced;
  * Fashion → purchasable only with a valid server quote and a chosen standard size;
  * ring size and quantity never change the unit price.
"""

import json

from p3 import sessions as Sessions
from p3.accounts import Principal
from p3.context import Context, HttpError
from p3.db import Dumps, NewId, Now
from p3.images import ImageService
from p3.movies import MovieService

MaxQuantity = 10


# Customize opens on US 10 (product-owner decision 2026-10-01); the customer can change it.
DefaultRingSize = 10.0


class CustomizeService:
    def __init__(self, Ctx: Context, Images: ImageService, Movies: MovieService):
        self.Ctx = Ctx
        self.Images = Images
        self.Movies = Movies

    # ── selection ────────────────────────────────────────────────────────
    def Select(self, Who: Principal, DesignId: str, CandidateId: str | None) -> dict:
        self.Images.RequireDesign(Who, DesignId)
        if CandidateId is not None:
            self.Images.RequireReadyCandidate(DesignId, CandidateId)
        self.Ctx.Db.Update("designs", DesignId, selected_candidate_id=CandidateId)
        Sessions.Record(self.Ctx, Who.AccountId, "option_selected", DesignId, candidate_id=CandidateId)
        return {"design_id": DesignId, "selected_candidate_id": CandidateId}

    # ── proceed ──────────────────────────────────────────────────────────
    def Proceed(self, Who: Principal, DesignId: str, CandidateId: str) -> dict:
        """Lock in the selected candidate for Customize and start (or reuse) its movie."""
        self.Images.RequireDesign(Who, DesignId)
        self.Images.RequireReadyCandidate(DesignId, CandidateId)
        Db = self.Ctx.Db
        Db.Update("designs", DesignId, selected_candidate_id=CandidateId)
        Existing = Db.One("SELECT * FROM customizations WHERE design_id = ? AND candidate_id = ?",
                          (DesignId, CandidateId))
        if Existing is None:
            T = Now()
            Db.Execute("INSERT OR IGNORE INTO customizations (id, design_id, candidate_id, material_id, ring_size, "
                       "quantity, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                       (NewId("cus"), DesignId, CandidateId, self.Ctx.Catalog.DefaultMaterialId, DefaultRingSize, 1, T, T))
        self.Movies.Ensure(Who, CandidateId)
        Row = Db.One("SELECT * FROM customizations WHERE design_id = ? AND candidate_id = ?",
                     (DesignId, CandidateId))
        Sessions.Record(self.Ctx, Who.AccountId, "customize_opened", DesignId, candidate_id=CandidateId,
                        material_id=Row["material_id"], ring_size=Row["ring_size"], defaults=Existing is None,
                        **Sessions.QuoteSnapshot(self.Ctx, Row["material_id"]))
        return self.ToJson(Row)

    def Update(self, Who: Principal, CustomizationId: str, Changes: dict) -> dict:
        Row = self._Owned(Who, CustomizationId)
        Fields = {}
        if "material_id" in Changes:
            if self.Ctx.Catalog.Get(Changes["material_id"]) is None:
                raise HttpError(400, "unknown_material", "Unknown material.")
            Fields["material_id"] = Changes["material_id"]
        if "ring_size" in Changes:
            Size = Changes["ring_size"]
            if Size is not None and not self.Ctx.Catalog.IsValidSize(Size):
                raise HttpError(400, "invalid_ring_size", "Please choose a standard ring size.")
            Fields["ring_size"] = None if Size is None else float(Size)
        if "quantity" in Changes:
            Qty = Changes["quantity"]
            if not isinstance(Qty, int) or isinstance(Qty, bool) or not 1 <= Qty <= MaxQuantity:
                raise HttpError(400, "invalid_quantity", f"Quantity must be between 1 and {MaxQuantity}.")
            Fields["quantity"] = Qty
        if Fields:
            self.Ctx.Db.Update("customizations", CustomizationId, **Fields)
            Sessions.Record(self.Ctx, Who.AccountId, "customization_changed", Row["design_id"],
                            customization_id=Row["id"], **Fields,
                            **Sessions.QuoteSnapshot(self.Ctx, Fields.get("material_id", Row["material_id"])))
        return self.ToJson(self.Ctx.Db.One("SELECT * FROM customizations WHERE id = ?", (Row["id"],)))

    def Get(self, Who: Principal, CustomizationId: str) -> dict:
        return self.ToJson(self._Owned(Who, CustomizationId))

    def _Owned(self, Who: Principal, CustomizationId: str) -> dict:
        Row = self.Ctx.Db.One("SELECT c.* FROM customizations c JOIN designs d ON d.id = c.design_id "
                              "WHERE c.id = ? AND d.owner_account_id = ?", (CustomizationId, Who.AccountId))
        if Row is None:
            raise HttpError(404, "customization_not_found", "Customization not found.")
        return Row

    def Purchasability(self, Row: dict, Quote) -> tuple[bool, str | None]:
        Mat = self.Ctx.Catalog.Get(Row["material_id"])
        if Mat is None or not self.Ctx.Catalog.IsPurchasableGroup(Mat.Group):
            return False, "luxury_preview_only"
        if not Quote.IsAvailable:
            return False, "price_unavailable"
        if Row["ring_size"] is None:
            return False, "ring_size_required"
        return True, None

    def ToJson(self, Row: dict) -> dict:
        Cand = self.Ctx.Db.One("SELECT * FROM candidates WHERE id = ?", (Row["candidate_id"],))
        Quote = self.Ctx.Pricing.QuoteFor(Row["material_id"])
        CanAdd, Reason = self.Purchasability(Row, Quote)
        return {
            "id": Row["id"], "design_id": Row["design_id"], "candidate_id": Row["candidate_id"],
            "image_url": self.Ctx.AssetUrl(Cand["asset_path"]),
            "material_id": Row["material_id"], "ring_size": Row["ring_size"], "quantity": Row["quantity"],
            "quote": Quote.ToJson(),
            "line_total": round(Quote.unit_price * Row["quantity"], 2) if Quote.IsAvailable else None,
            "can_add_to_bag": CanAdd, "add_to_bag_blocked_reason": Reason,
            "movie": self.Movies.ToJson(self.Movies.Latest(Row["candidate_id"])),
        }

    # ── bag ──────────────────────────────────────────────────────────────
    def AddToBag(self, Who: Principal, CustomizationId: str) -> dict:
        Row = self._Owned(Who, CustomizationId)
        Quote = self.Ctx.Pricing.QuoteFor(Row["material_id"])
        CanAdd, Reason = self.Purchasability(Row, Quote)
        if not CanAdd:
            Messages = {"luxury_preview_only": "Luxury materials are preview-only for now.",
                        "price_unavailable": "Price unavailable — this item cannot be added to the bag.",
                        "ring_size_required": "Please choose a ring size."}
            raise HttpError(409, Reason, Messages[Reason])
        LineId = NewId("bag")
        self.Ctx.Db.Execute(
            "INSERT INTO bag_lines (id, owner_account_id, design_id, candidate_id, customization_id, material_id, ring_size, "
            "quantity, unit_price, currency, pricing_version, quote_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (LineId, Who.AccountId, Row["design_id"], Row["candidate_id"], Row["id"], Row["material_id"], Row["ring_size"],
             Row["quantity"], Quote.unit_price, Quote.currency, Quote.pricing_version, Dumps(Quote.ToJson()), Now()))
        Sessions.Record(self.Ctx, Who.AccountId, "bag_added", Row["design_id"], line_id=LineId,
                        material_id=Row["material_id"], ring_size=Row["ring_size"], quantity=Row["quantity"],
                        unit_price=Quote.unit_price, currency=Quote.currency, pricing_version=Quote.pricing_version)
        return self.Bag(Who)

    def RemoveFromBag(self, Who: Principal, LineId: str) -> dict:
        Line = self.Ctx.Db.One("SELECT design_id FROM bag_lines WHERE id = ? AND owner_account_id = ?", (LineId, Who.AccountId))
        if Line:
            Sessions.Record(self.Ctx, Who.AccountId, "bag_removed", Line["design_id"], line_id=LineId)
        if self.Ctx.Db.Execute("DELETE FROM bag_lines WHERE id = ? AND owner_account_id = ?", (LineId, Who.AccountId)) == 0:
            raise HttpError(404, "bag_line_not_found", "Bag line not found.")
        return self.Bag(Who)

    def Bag(self, Who: Principal) -> dict:
        CurrentVersion = self.Ctx.Pricing.ProfileVersion
        Lines = []
        Totals: dict[str, float] = {}
        for L in self.Ctx.Db.All("SELECT b.*, c.asset_path, d.title FROM bag_lines b "
                                 "JOIN candidates c ON c.id = b.candidate_id JOIN designs d ON d.id = b.design_id "
                                 "WHERE b.owner_account_id = ? ORDER BY b.created_at", (Who.AccountId,)):
            Mat = self.Ctx.Catalog.Get(L["material_id"])
            Total = round(L["unit_price"] * L["quantity"], 2)
            Totals[L["currency"]] = round(Totals.get(L["currency"], 0) + Total, 2)
            Lines.append({"id": L["id"], "design_id": L["design_id"], "title": L["title"],
                          "candidate_id": L["candidate_id"], "image_url": self.Ctx.AssetUrl(L["asset_path"]),
                          "material_id": L["material_id"], "material_label": Mat.Label if Mat else L["material_id"],
                          "ring_size": L["ring_size"], "quantity": L["quantity"], "unit_price": L["unit_price"],
                          "currency": L["currency"], "line_total": Total, "pricing_version": L["pricing_version"],
                          "price_is_stale": L["pricing_version"] != CurrentVersion,
                          "quote": json.loads(L["quote_json"])})
        return {"lines": Lines, "totals": Totals, "checkout_available": False,
                "checkout_note": "Ordering is not available in this Pipeline 3 prototype. "
                                 "The bag holds price snapshots only; no order, payment or production is created."}
