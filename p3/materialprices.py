"""Material pricing — one versioned table, edited in Admin → AI Prompts & Params → Material pricing.

Per material:
  density_g_cm3   sintered density: weight = volume × density (3D and the website's weight estimate)
  price_per_g     3D calculated price  = weight × price $/g
  cost_per_g      production cost      = weight × cost $/g
  fixed_price     the price the website shows (fixed per material until there is enough data to change it);
                  empty (null) = not sold at a fixed price (the website shows "Price unavailable")

Every save is a new version (who, when, note). Nothing here is invented: an empty value stays empty and
the matching number is shown as unavailable.
"""

import dataclasses
import json

from p3.db import Database, Dumps, Now

Schema = """
CREATE TABLE IF NOT EXISTS material_price_lists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    price_json  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    note        TEXT
);
"""
Fields = ("density_g_cm3", "price_per_g", "cost_per_g", "fixed_price")


def _Row(Density, PricePerG, CostPerG, Fixed):
    return {"density_g_cm3": Density, "price_per_g": PricePerG, "cost_per_g": CostPerG, "fixed_price": Fixed}


# The business table of 2026-10-02 (Silver 925, 316L, Vermeil, Gold 18K / 14K). 10K gold was not given.
SeedTable = {
    "silver":          _Row(10.36, 35, 16, 200),
    "stainless_steel": _Row(8.0, 25, 11, 90),
    "vermeil":         _Row(8.0, 35, 16, 200),
    "gold_18k_yellow": _Row(15.3, 400, 170, None),
    "gold_18k_rose":   _Row(15.3, 400, 170, None),
    "gold_14k_yellow": _Row(13.7, 300, 135, None),
    "gold_14k_rose":   _Row(13.7, 300, 135, None),
    "gold_10k_yellow": _Row(11.6, None, None, None),
    "gold_10k_rose":   _Row(11.6, None, None, None),
}


class MaterialPriceError(ValueError):
    pass


def Validate(Doc: dict, Catalog) -> dict:
    Mats = Doc.get("materials")
    if not isinstance(Mats, dict):
        raise MaterialPriceError("The table needs a 'materials' object.")
    Out = {}
    for Mid, Row in Mats.items():
        if Catalog.Get(Mid) is None:
            raise MaterialPriceError(f"Unknown material: {Mid}")
        Clean = {}
        for F in Fields:
            V = (Row or {}).get(F)
            if V in (None, ""):
                Clean[F] = None
                continue
            try:
                V = float(V)
            except (TypeError, ValueError):
                raise MaterialPriceError(f"{Mid}: {F} must be a number or empty.")
            if V <= 0:
                raise MaterialPriceError(f"{Mid}: {F} must be greater than 0 (leave it empty if unknown).")
            Clean[F] = V
        if Clean["density_g_cm3"] is None:
            raise MaterialPriceError(f"{Mid}: density is required.")
        Out[Mid] = Clean
    return {"currency": Doc.get("currency") or "USD", "materials": Out}


class MaterialPriceBook:
    def __init__(self, Db: Database, Catalog):
        self.Db = Db
        self.Catalog = Catalog
        self.OnSave = []                 # callbacks after a new version (e.g. price 3D results that had no price)
        with Db.Connect() as Conn:
            Conn.executescript(Schema)
        if not Db.One("SELECT id FROM material_price_lists LIMIT 1"):
            Seed = {M: SeedTable.get(M) or _Row(Mat.DensityGCm3, None, None, None)
                    for M, Mat in Catalog.Materials.items()}
            self.Save({"materials": Seed}, "seed", "Initial material pricing table (2026-10-02)")
        self._Apply()

    def Current(self) -> dict:
        R = self.Db.One("SELECT * FROM material_price_lists ORDER BY id DESC LIMIT 1")
        return {**json.loads(R["price_json"]), "version": f"materials-v{R['id']}", "updated_at": R["created_at"],
                "updated_by": R["created_by"], "update_note": R["note"]}

    def History(self, Limit: int = 20) -> list[dict]:
        return self.Db.All("SELECT id, created_at, created_by, note FROM material_price_lists ORDER BY id DESC LIMIT ?",
                           (Limit,))

    def Save(self, Doc: dict, By: str, Note: str = "") -> dict:
        Clean = Validate(Doc, self.Catalog)
        self.Db.Execute("INSERT INTO material_price_lists (price_json, created_at, created_by, note) VALUES (?,?,?,?)",
                        (Dumps(Clean), Now(), By, Note))
        self._Apply()
        for Fn in self.OnSave:
            Fn()
        return self.Current()

    def Row(self, MaterialId: str) -> dict:
        return self.Current()["materials"].get(MaterialId) or _Row(None, None, None, None)

    def _Apply(self) -> None:
        """The table's density is the one density: update the catalog materials in place."""
        for Mid, Row in self.Current()["materials"].items():
            Mat = self.Catalog.Materials.get(Mid)
            if Mat and Row.get("density_g_cm3") and Mat.DensityGCm3 != Row["density_g_cm3"]:
                self.Catalog.Materials[Mid] = dataclasses.replace(Mat, DensityGCm3=float(Row["density_g_cm3"]))

    def Price3D(self, MaterialId: str, WeightG: float | None) -> dict:
        """Production cost and 3D calculated price from the weight, or why they cannot be given."""
        R = self.Row(MaterialId)
        if WeightG is None:
            return {"status": "needs_review", "reason": "weight unavailable (volume not reliable)"}
        Cost = round(WeightG * R["cost_per_g"], 2) if R.get("cost_per_g") else None
        Price = round(WeightG * R["price_per_g"], 2) if R.get("price_per_g") else None
        Missing = [N for N, V in (("cost $/g", Cost), ("price $/g", Price)) if V is None]
        return {"status": "calculated" if not Missing else "cost_model_not_configured",
                "production_cost": Cost, "calculated_price": Price,
                "reason": f"{' and '.join(Missing)} not set for this material" if Missing else None,
                "breakdown": {"weight_g": WeightG, "cost_per_g": R.get("cost_per_g"), "price_per_g": R.get("price_per_g")}}
