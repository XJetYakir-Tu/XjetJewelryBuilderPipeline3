"""Developer-only single-image 3D mesh (hitem3d/hi3d/v3.0/image-to-3d) and STL.

Never invoked by the customer flow. Input is exactly one selected candidate
image. The endpoint supports export_format="stl" natively (verified 2026-09-29),
which is the default; other formats can be converted to STL in a separate
developer operation. Generated meshes are NOT measured, NOT sized to the ring
size, and NOT validated as watertight or manufacturable.
"""

import io
import json
import logging

from p3 import assets
from p3.context import Context, HttpError
from p3.db import Dumps, NewId, Now
from p3.providers import endpoints
from p3.runner import DownloadWithRetry, FailureFor, PollUntilDone

Logger = logging.getLogger("p3.meshes")

AllowedSettings = {
    "resolution":     lambda V: V in ("2048quality", "2048master"),
    "face_count":     lambda V: isinstance(V, int) and not isinstance(V, bool) and 100_000 <= V <= 5_000_000,
    "export_format":  lambda V: V in ("glb", "obj", "stl", "fbx", "usdz"),
    "enable_texture": lambda V: isinstance(V, bool),
    "enable_pbr":     lambda V: isinstance(V, bool),
    "shading":        lambda V: isinstance(V, (int, float)) and not isinstance(V, bool) and 0 <= V <= 1,
}
ConvertibleFormats = ("glb", "obj")

Disclaimer = ("Generated from a single AI image. Not measured, not sized to the selected ring size, "
              "not checked for watertightness, and not approved for manufacturing.")


class MeshService:
    def __init__(self, Ctx: Context):
        self.Ctx = Ctx

    def Create(self, CandidateId: str, Overrides: dict | None) -> dict:
        Db = self.Ctx.Db
        Cand = Db.One("SELECT c.*, b.design_id, b.user_text, b.kind, b.config_version AS image_config "
                      "FROM candidates c JOIN batches b ON b.id = c.batch_id WHERE c.id = ?", (CandidateId,))
        if Cand is None:
            raise HttpError(404, "candidate_not_found", "Candidate not found.")
        if Cand["status"] != "ready":
            raise HttpError(409, "candidate_not_ready", "Candidate image is not ready.")
        Settings_ = dict(self.Ctx.Gen.Mesh.Params)
        for Key, Value in (Overrides or {}).items():
            if Key not in AllowedSettings or not AllowedSettings[Key](Value):
                raise HttpError(400, "invalid_mesh_setting", f"Invalid mesh setting: {Key}")
            Settings_[Key] = Value
        SettingsJson = Dumps(Settings_)
        Live = Db.One("SELECT * FROM meshes WHERE candidate_id = ? AND settings_json = ? "
                      "AND status IN ('queued','running')", (CandidateId, SettingsJson))
        if Live:
            return self.ToJson(Live)
        MeshId = NewId("mesh")
        Provenance = {
            "candidate_id": CandidateId, "batch_id": Cand["batch_id"], "design_id": Cand["design_id"],
            "batch_kind": Cand["kind"], "batch_text": Cand["user_text"], "image_config_version": Cand["image_config"],
            "source_image_sha256": Cand["content_sha256"], "endpoint": endpoints.Mesh,
            "settings": Settings_, "mesh_config_version": self.Ctx.Gen.Mesh.Version,
            "requested_at": Now(), "disclaimer": Disclaimer,
        }
        T = Now()
        Db.Execute("INSERT INTO meshes (id, candidate_id, endpoint, settings_json, config_version, status, "
                   "provenance_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                   (MeshId, CandidateId, endpoints.Mesh, SettingsJson, self.Ctx.Gen.Mesh.Version, "queued",
                    Dumps(Provenance), T, T))
        self.Ctx.Runner.Spawn(f"mesh:{MeshId}", self._Drive(MeshId))
        return self.ToJson(Db.One("SELECT * FROM meshes WHERE id = ?", (MeshId,)))

    async def _Drive(self, MeshId: str) -> None:
        Ctx = self.Ctx
        Db = Ctx.Db
        S = Ctx.Settings
        Mesh = Db.One("SELECT * FROM meshes WHERE id = ?", (MeshId,))
        if Mesh is None or Mesh["status"] not in ("queued", "running"):
            return
        Settings_ = json.loads(Mesh["settings_json"])
        Fmt = Settings_["export_format"]
        try:
            RequestId = Mesh["provider_request_id"]
            if not RequestId:
                Cand = Db.One("SELECT asset_path FROM candidates WHERE id = ?", (Mesh["candidate_id"],))
                ImagePath = assets.Resolve(S.AssetsDir, Cand["asset_path"])
                ImageUrl = await Ctx.Provider.Upload(ImagePath.read_bytes(), assets.ImageContentType(Cand["asset_path"]))
                RequestId = await Ctx.Provider.Submit(Mesh["endpoint"], {"image_url": ImageUrl, **Settings_})
                Db.Update("meshes", MeshId, status="running", provider_request_id=RequestId)
            Result = await PollUntilDone(Ctx.Provider, Mesh["endpoint"], RequestId, Ctx.Gen.Mesh.RequestTimeoutS,
                                         S.PollIntervalS, S.MaxTransientPollErrors)
            Url = (Result.get("model_mesh") or {}).get("url")
            if not Url:
                raise assets.AssetError("Provider returned no model_mesh")
            Data = await DownloadWithRetry(Ctx.Provider, Url, S.MaxTransientPollErrors, S.PollIntervalS)
            assets.ValidateMesh(Data, Fmt)
            RelPath = f"meshes/{MeshId}/original.{Fmt}"
            assets.WriteAtomic(S.DevDir, RelPath, Data)
            Db.Update("meshes", MeshId, status="ready", original_path=RelPath, original_format=Fmt,
                      stl_path=RelPath if Fmt == "stl" else None, error=None, error_code=None)
        except Exception as E:
            Message, Code = FailureFor(E)
            Logger.warning("Mesh %s failed (%s): %s", MeshId, Code, E)
            Db.Update("meshes", MeshId, status="failed", error=Message, error_code=Code)

    def ConvertToStl(self, MeshId: str) -> dict:
        """Separate developer operation: convert a GLB/OBJ result to STL with trimesh (no repair, no scaling)."""
        Mesh = self._Require(MeshId)
        if Mesh["status"] != "ready":
            raise HttpError(409, "mesh_not_ready", "Mesh is not ready.")
        if Mesh["stl_path"]:
            return self.ToJson(Mesh)
        if Mesh["original_format"] not in ConvertibleFormats:
            raise HttpError(400, "unsupported_conversion", f"Cannot convert {Mesh['original_format']} to STL.")
        import trimesh
        Source = assets.Resolve(self.Ctx.Settings.DevDir, Mesh["original_path"])
        try:
            Loaded = trimesh.load(io.BytesIO(Source.read_bytes()), file_type=Mesh["original_format"])
            Geometry = Loaded.to_geometry() if isinstance(Loaded, trimesh.Scene) else Loaded
            if not hasattr(Geometry, "faces") or len(Geometry.faces) == 0:
                raise ValueError("Mesh contains no faces")
            Data = Geometry.export(file_type="stl")
            assets.ValidateMesh(Data, "stl")
        except Exception as E:
            raise HttpError(422, "conversion_failed", f"STL conversion failed: {E}") from E
        RelPath = f"meshes/{MeshId}/converted.stl"
        assets.WriteAtomic(self.Ctx.Settings.DevDir, RelPath, Data)
        self.Ctx.Db.Update("meshes", MeshId, stl_path=RelPath)
        return self.ToJson(self._Require(MeshId))

    def FilePath(self, MeshId: str, Kind: str):
        Mesh = self._Require(MeshId)
        Rel = Mesh["stl_path"] if Kind == "stl" else Mesh["original_path"]
        if Mesh["status"] != "ready" or not Rel:
            raise HttpError(404, "mesh_file_unavailable", "That mesh file is not available.")
        return assets.Resolve(self.Ctx.Settings.DevDir, Rel), Rel.rsplit("/", 1)[-1]

    def List(self, CandidateId: str | None) -> list[dict]:
        if CandidateId:
            Rows = self.Ctx.Db.All("SELECT * FROM meshes WHERE candidate_id = ? ORDER BY created_at DESC", (CandidateId,))
        else:
            Rows = self.Ctx.Db.All("SELECT * FROM meshes ORDER BY created_at DESC LIMIT 50")
        return [self.ToJson(R) for R in Rows]

    def Reconcile(self) -> dict:
        Resumed = Interrupted = 0
        for M in self.Ctx.Db.All("SELECT id, provider_request_id FROM meshes WHERE status IN ('queued','running')"):
            if M["provider_request_id"]:
                self.Ctx.Runner.Spawn(f"mesh:{M['id']}", self._Drive(M["id"]))
                Resumed += 1
            else:
                self.Ctx.Db.Update("meshes", M["id"], status="interrupted", error_code="interrupted",
                                   error="Interrupted by a server restart before submission was confirmed.")
                Interrupted += 1
        return {"resumed": Resumed, "interrupted": Interrupted}

    def _Require(self, MeshId: str) -> dict:
        M = self.Ctx.Db.One("SELECT * FROM meshes WHERE id = ?", (MeshId,))
        if M is None:
            raise HttpError(404, "mesh_not_found", "Mesh not found.")
        return M

    def Get(self, MeshId: str) -> dict:
        return self.ToJson(self._Require(MeshId))

    def ToJson(self, M: dict) -> dict:
        return {"id": M["id"], "candidate_id": M["candidate_id"], "status": M["status"], "endpoint": M["endpoint"],
                "settings": json.loads(M["settings_json"]), "config_version": M["config_version"],
                "original_format": M["original_format"], "has_stl": bool(M["stl_path"]),
                "can_convert": M["status"] == "ready" and not M["stl_path"]
                               and M["original_format"] in ConvertibleFormats,
                "provenance": json.loads(M["provenance_json"]), "error": M["error"], "error_code": M["error_code"],
                "created_at": M["created_at"]}
