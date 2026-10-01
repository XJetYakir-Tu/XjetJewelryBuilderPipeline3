"""Admin-only 3D production geometry for a session: Hi3D v3.0 → repair → scale to ring size → measure
→ weight / cost / 3D price.

Rules:
  * 3D never starts automatically — only Production3D.Request(), called from the Admin.
  * Every request has a target ring size: the customer's size, else US 10 (default), and the admin
    may override it. Both the customer size and the production size are stored with their source.
  * The raw Hi3D mesh for a design option is reused: a new size or material re-scales and re-measures
    the same raw mesh, without another paid Hi3D call.
  * The customer's fixed price is copied in for comparison only; nothing here changes it.
"""

import asyncio
import json
import logging
from pathlib import Path

from p3 import assets
from p3 import sessions as Sessions
from p3.context import Context, HttpError
from p3.db import Dumps, NewId, Now
from p3.geometry import MeasureRing, MethodVersion, UsSizeToInnerDiameterMm
from p3.settings import RepoRoot

Logger = logging.getLogger("p3.production3d")
DefaultSize = 10.0
CostModelPath = RepoRoot / "config" / "production_costs.json"
Terminal = ("measured", "needs_review", "failed")


def LoadCostModel(PathObj: Path = CostModelPath) -> dict:
    try:
        return json.loads(Path(PathObj).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": "unconfigured", "currency": "USD", "metal_price_per_gram": {}}


def Price(Model: dict, MaterialId: str, WeightG: float | None) -> dict:
    """Production cost and 3D calculated price, or why they cannot be calculated. Never guesses."""
    PerGram = (Model.get("metal_price_per_gram") or {}).get(MaterialId)
    Fixed, PerGramProd, Markup = (Model.get("production_fixed_per_piece"), Model.get("production_per_gram"),
                                  Model.get("price_markup"))
    if WeightG is None:
        return {"status": "needs_review", "reason": "weight unavailable (volume not reliable)"}
    if PerGram is None or Fixed is None or PerGramProd is None:
        return {"status": "cost_model_not_configured", "reason": "metal and production prices are not set"}
    Metal = WeightG * PerGram
    Production = Fixed + WeightG * PerGramProd
    Cost = round(Metal + Production, 2)
    Out = {"status": "calculated", "production_cost": Cost,
           "breakdown": {"metal": round(Metal, 2), "production": round(Production, 2)}}
    Out["calculated_price"] = round(Cost * Markup, 2) if Markup is not None else None
    return Out


class Production3D:
    def __init__(self, Ctx: Context, Meshes):
        self.Ctx = Ctx
        self.Meshes = Meshes

    # ── admin request ────────────────────────────────────────────────────
    def Request(self, DesignId: str, ProductionSize=None, MaterialId: str | None = None,
                CandidateId: str | None = None, RequestedBy: str = "admin") -> dict:
        Db, Cat = self.Ctx.Db, self.Ctx.Catalog
        Summary = (Sessions.Summaries(self.Ctx, [DesignId]) or [None])[0]
        if Summary is None:
            raise HttpError(404, "session_not_found", "Session not found.")
        Design = Db.One("SELECT * FROM designs WHERE id = ?", (DesignId,))
        CandidateId = CandidateId or Design["selected_candidate_id"]
        if not CandidateId:
            First = Db.One("SELECT c.id FROM candidates c JOIN batches b ON b.id = c.batch_id WHERE b.design_id = ? "
                           "AND c.status = 'ready' ORDER BY b.created_at DESC, c.slot LIMIT 1", (DesignId,))
            CandidateId = First["id"] if First else None
        Cand = Db.One("SELECT c.* FROM candidates c JOIN batches b ON b.id = c.batch_id WHERE c.id = ? AND b.design_id = ?",
                      (CandidateId, DesignId)) if CandidateId else None
        if Cand is None or Cand["status"] != "ready":
            raise HttpError(409, "no_ready_image", "This session has no ready design image to turn into 3D.")
        CustomerSize = Summary["ring_size"]
        if ProductionSize in (None, ""):
            Size, SizeSource = (float(CustomerSize), "customer") if CustomerSize is not None else (DefaultSize, "default")
        else:
            Size = float(ProductionSize)
            if not Cat.IsValidSize(Size):
                raise HttpError(400, "invalid_ring_size", "Please choose a standard US ring size.")
            SizeSource = ("customer" if CustomerSize is not None and Size == float(CustomerSize)
                          else "default" if CustomerSize is None and Size == DefaultSize else "admin_override")
        CustomerMaterial = Summary["material_id"] if Summary["material_chosen"] else None
        if MaterialId:
            if Cat.Get(MaterialId) is None:
                raise HttpError(400, "unknown_material", "Unknown material.")
            Mat, MatSource = MaterialId, ("customer" if MaterialId == CustomerMaterial else "admin_override")
        elif CustomerMaterial:
            Mat, MatSource = CustomerMaterial, "customer"
        else:
            Mat, MatSource = (Summary["material_id"] or Cat.DefaultMaterialId), "default"
        Raw = Db.One("SELECT * FROM meshes WHERE candidate_id = ? AND status = 'ready' ORDER BY created_at DESC LIMIT 1",
                     (CandidateId,))
        Mesh = Raw or self.Meshes.Create(CandidateId, None)      # the paid Hi3D call, only when needed
        Sid, T = NewId("s3d"), Now()
        Db.Execute("INSERT INTO session_3d (id, design_id, candidate_id, mesh_id, customer_size, production_size, "
                   "size_source, customer_material, material_id, material_source, status, requested_by, created_at, "
                   "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (Sid, DesignId, CandidateId, Mesh["id"], CustomerSize, Size, SizeSource, CustomerMaterial, Mat,
                    MatSource, "measuring" if Raw else "generating", RequestedBy, T, T))
        Sessions.Record(self.Ctx, Design["owner_account_id"], "admin_3d_requested", DesignId, session_3d_id=Sid,
                        mesh_id=Mesh["id"], reused_raw_mesh=bool(Raw), production_size=Size, size_source=SizeSource,
                        material_id=Mat, material_source=MatSource, by=RequestedBy)
        self.Ctx.Runner.Spawn(f"s3d:{Sid}", self._Drive(Sid))
        return self.Get(Sid)

    # ── pipeline ─────────────────────────────────────────────────────────
    async def _Drive(self, Sid: str) -> None:
        Ctx, Db = self.Ctx, self.Ctx.Db
        try:
            Row = Db.One("SELECT * FROM session_3d WHERE id = ?", (Sid,))
            Deadline = asyncio.get_running_loop().time() + Ctx.Gen.Mesh.RequestTimeoutS + 120
            while True:
                Mesh = Db.One("SELECT * FROM meshes WHERE id = ?", (Row["mesh_id"],))
                if Mesh["status"] == "ready":
                    break
                if Mesh["status"] in ("failed", "interrupted"):
                    Db.Update("session_3d", Sid, status="failed", error=f"Hi3D: {Mesh['error'] or Mesh['status']}")
                    return
                if asyncio.get_running_loop().time() > Deadline:
                    Db.Update("session_3d", Sid, status="failed", error="Hi3D did not finish in time")
                    return
                await asyncio.sleep(Ctx.Settings.PollIntervalS)
            Db.Update("session_3d", Sid, status="measuring")
            Source = assets.Resolve(Ctx.Settings.DevDir, Mesh["original_path"])
            Target = UsSizeToInnerDiameterMm(Row["production_size"])
            G = await asyncio.to_thread(MeasureRing, Source.read_bytes(), Mesh["original_format"], Target)
            self._Store(Row, Mesh, G, Target)
        except Exception as E:  # noqa: BLE001
            Logger.exception("3D production %s failed", Sid)
            Db.Update("session_3d", Sid, status="failed", error=f"{type(E).__name__}: {E}")

    def _Store(self, Row: dict, Mesh: dict, G, TargetMm: float) -> None:
        Ctx, Db, Sid = self.Ctx, self.Ctx.Db, Row["id"]
        T = Now()

        def Insert(Stage, M, Scale, StlPath):
            Gid = NewId("geo")
            Db.Execute("INSERT INTO geometry_results (id, session_3d_id, stage, size_x_mm, size_y_mm, size_z_mm, "
                       "inner_diameter_mm, volume_mm3, surface_area_mm2, watertight, scale_factor, stl_path, "
                       "method_version, checks_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (Gid, Sid, Stage, M.size_x_mm, M.size_y_mm, M.size_z_mm, M.inner_diameter_mm, M.volume_mm3,
                        M.surface_area_mm2, int(M.watertight), Scale, StlPath, MethodVersion, Dumps(M.checks), T))
            return Gid

        Insert("raw", G.raw, None, Mesh["original_path"])
        if G.production is None:
            Db.Update("session_3d", Sid, status="needs_review", error="; ".join(G.problems))
            return
        Rel = f"meshes/{Mesh['id']}/production_{Sid}.stl"
        assets.WriteAtomic(Ctx.Settings.DevDir, Rel, G.production_stl)
        Gid = Insert("production", G.production, G.scale_factor, Rel)
        Mat = Ctx.Catalog.Get(Row["material_id"])
        Weight = round(G.production.volume_mm3 / 1000.0 * Mat.DensityGCm3, 3) if G.production.volume_mm3 else None
        Model = LoadCostModel()
        P = Price(Model, Row["material_id"], Weight)
        Summary = (Sessions.Summaries(Ctx, [Row["design_id"]]) or [{}])[0]
        Fixed = Summary.get("fixed_price") or {}
        # The fixed price is per material; compare against the material actually used for production.
        if Row["material_id"] != Summary.get("material_id"):
            Q = Ctx.Pricing.QuoteFor(Row["material_id"])
            Fixed = {"unit_price": Q.unit_price, "pricing_version": Q.pricing_version, "source": "current_quote"}
        Db.Execute("INSERT INTO price_calculations (id, session_3d_id, geometry_id, material_id, density_g_cm3, weight_g, "
                   "production_cost, calculated_price, currency, cost_model_version, breakdown_json, fixed_price, "
                   "fixed_price_version, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (NewId("prc"), Sid, Gid, Row["material_id"], Mat.DensityGCm3, Weight, P.get("production_cost"),
                    P.get("calculated_price"), Model.get("currency"), Model.get("version"),
                    Dumps({**P.get("breakdown", {}), "reason": P.get("reason"), "fixed_price_source": Fixed.get("source")}),
                    Fixed.get("unit_price"), Fixed.get("pricing_version"), P["status"], T))
        Db.Update("session_3d", Sid, status=G.status, error="; ".join(G.problems) or None)
        Owner = Db.One("SELECT owner_account_id FROM designs WHERE id = ?", (Row["design_id"],))
        Sessions.Record(Ctx, Owner["owner_account_id"], "admin_3d_measured", Row["design_id"], session_3d_id=Sid,
                        status=G.status, inner_diameter_mm=G.production.inner_diameter_mm,
                        volume_mm3=G.production.volume_mm3, weight_g=Weight)

    def Reconcile(self) -> int:
        """After a restart, resume watching 3D requests that were still in progress."""
        N = 0
        for R in self.Ctx.Db.All("SELECT id FROM session_3d WHERE status IN ('requested', 'generating', 'measuring')"):
            self.Ctx.Runner.Spawn(f"s3d:{R['id']}", self._Drive(R["id"]))
            N += 1
        return N

    # ── read ─────────────────────────────────────────────────────────────
    def Get(self, Sid: str) -> dict:
        Db, Url = self.Ctx.Db, self.Ctx.AssetUrl
        R = Db.One("SELECT * FROM session_3d WHERE id = ?", (Sid,))
        if R is None:
            raise HttpError(404, "session_3d_not_found", "3D request not found.")
        Mesh = Db.One("SELECT id, status, provider_request_id, endpoint, error FROM meshes WHERE id = ?", (R["mesh_id"],))
        Geo = {G["stage"]: {**G, "checks": json.loads(G.pop("checks_json") or "{}")}
               for G in Db.All("SELECT * FROM geometry_results WHERE session_3d_id = ? ORDER BY created_at", (Sid,))}
        Calc = Db.One("SELECT * FROM price_calculations WHERE session_3d_id = ? ORDER BY created_at DESC LIMIT 1", (Sid,))
        if Calc:
            Calc["breakdown"] = json.loads(Calc.pop("breakdown_json") or "{}")
        Cand = Db.One("SELECT asset_path FROM candidates WHERE id = ?", (R["candidate_id"],))
        Mat = self.Ctx.Catalog.Get(R["material_id"])
        return {
            **R, "target_inner_diameter_mm": UsSizeToInnerDiameterMm(R["production_size"]),
            "material_label": Mat.Label if Mat else R["material_id"], "density_g_cm3": Mat.DensityGCm3 if Mat else None,
            "image_url": Url(Cand["asset_path"]) if Cand else None,
            "hi3d": Mesh and {"mesh_id": Mesh["id"], "status": Mesh["status"], "endpoint": Mesh["endpoint"],
                              "provider": "mock" if (Mesh["provider_request_id"] or "").startswith("mockreq_") else
                              ("fal" if Mesh["provider_request_id"] else None), "error": Mesh["error"]},
            "geometry": Geo, "price": Calc,
        }

    def ForDesign(self, DesignId: str) -> list[dict]:
        return [self.Get(R["id"]) for R in self.Ctx.Db.All(
            "SELECT id FROM session_3d WHERE design_id = ? ORDER BY created_at DESC", (DesignId,))]

    def StlPath(self, Sid: str, Stage: str) -> Path:
        G = self.Ctx.Db.One("SELECT stl_path FROM geometry_results WHERE session_3d_id = ? AND stage = ?", (Sid, Stage))
        if not G or not G["stl_path"]:
            raise HttpError(404, "geometry_not_found", "No geometry file for this stage.")
        return assets.Resolve(self.Ctx.Settings.DevDir, G["stl_path"])
