"""Ring geometry: repair, find the bore, scale to a ring size, measure.

Hi3D (and any image-to-3D model) returns a mesh in arbitrary units and orientation. To size it:

  1. load and merge into one mesh; repair (merge vertices, drop degenerate faces, fix normals,
     fill holes) and record whether it is watertight before and after;
  2. ring axis = the direction of least spread of the surface (a ring is flat across its band);
  3. bore: slice the ring at several heights through the band, take the nearest wall point in each
     angular bin around the centre, fit a circle and iterate the centre; inner diameter = 2 × median
     wall radius at the narrowest height; roundness and open (empty) bins are recorded as checks;
  4. scale uniformly so the inner diameter equals the target size, align (ring axis → Z, bore
     centre → origin), then measure X/Y/Z, inner diameter, volume, surface area.

Volume is only trusted when the mesh is watertight; otherwise the result is "needs_review".
Uniform scaling also scales band width/thickness — that is a known limitation, recorded in checks.
"""

import io
from dataclasses import dataclass, field

import numpy as np

MethodVersion = "ring-bore-sections-v1"
RoundnessLimit = 0.04           # (std of bore radius) / radius above this → needs review
Directions = 72


def UsSizeToInnerDiameterMm(Size: float) -> float:
    """US/Canada ring size → inner diameter (standard linear table, size 0 = 11.63 mm)."""
    return round(11.63 + 0.8128 * float(Size), 3)


@dataclass
class Measurement:
    size_x_mm: float | None = None
    size_y_mm: float | None = None
    size_z_mm: float | None = None
    inner_diameter_mm: float | None = None
    volume_mm3: float | None = None
    surface_area_mm2: float | None = None
    watertight: bool = False
    checks: dict = field(default_factory=dict)


@dataclass
class RingGeometry:
    raw: Measurement
    production: Measurement | None
    scale_factor: float | None
    production_stl: bytes | None
    status: str                  # measured | needs_review | failed
    problems: list


def _Load(Data: bytes, Fmt: str):
    import trimesh
    Loaded = trimesh.load(io.BytesIO(Data), file_type=Fmt, force=None)
    if isinstance(Loaded, trimesh.Scene):
        Loaded = Loaded.to_geometry() if hasattr(Loaded, "to_geometry") else Loaded.dump(concatenate=True)
    if not isinstance(Loaded, trimesh.Trimesh) or len(Loaded.faces) == 0:
        raise ValueError("The 3D file contains no mesh")
    return Loaded


def _Repair(Mesh) -> dict:
    import trimesh
    Before = bool(Mesh.is_watertight)
    Mesh.merge_vertices()
    Mesh.update_faces(Mesh.nondegenerate_faces())
    Mesh.update_faces(Mesh.unique_faces())
    Mesh.remove_unreferenced_vertices()
    trimesh.repair.fix_normals(Mesh)
    if not Mesh.is_watertight:
        trimesh.repair.fill_holes(Mesh)
    return {"watertight_before_repair": Before, "watertight_after_repair": bool(Mesh.is_watertight)}


def _Frame(Mesh):
    """Ring axis (least surface spread) and two in-plane axes, from area-weighted surface samples."""
    import trimesh
    Points, _ = trimesh.sample.sample_surface(Mesh, 4000, seed=7)
    if len(Points) < 50:
        Points = Mesh.vertices
    Centre = Points.mean(axis=0)
    _, _, Vt = np.linalg.svd(Points - Centre, full_matrices=False)
    U, V, Axis = Vt[0], Vt[1], Vt[2]
    Spread = (Points - Centre) @ Vt.T
    return Centre, U, V, Axis, Spread


def _FitCircle(P: np.ndarray) -> tuple[np.ndarray, float]:
    A = np.c_[2 * P, np.ones(len(P))]
    Bv = (P ** 2).sum(axis=1)
    Sol, *_ = np.linalg.lstsq(A, Bv, rcond=None)
    C = Sol[:2]
    return C, float(np.sqrt(max(Sol[2] + C @ C, 0.0)))


def _Section(Mesh, Origin: np.ndarray, Axis: np.ndarray, U: np.ndarray, V: np.ndarray, PerSegment: int = 8) -> np.ndarray:
    """The surface's cross-section with the plane through Origin normal to Axis, as (U, V) points
    sampled along each crossing triangle's segment (so coarse meshes still give a dense outline)."""
    Tri = Mesh.triangles - Origin                       # (F, 3, 3)
    D = Tri @ Axis                                      # signed distance of each corner
    Cross = (D.min(axis=1) < 0) & (D.max(axis=1) > 0)
    Tri, D = Tri[Cross], D[Cross]
    if not len(Tri):
        return np.zeros((0, 2))
    Ends = np.full((len(Tri), 3, 3), np.nan)
    for K, (A, B) in enumerate(((0, 1), (1, 2), (2, 0))):
        M = (D[:, A] * D[:, B]) < 0
        T = D[M, A] / (D[M, A] - D[M, B])
        Ends[M, K] = Tri[M, A] + T[:, None] * (Tri[M, B] - Tri[M, A])
    Valid = ~np.isnan(Ends[:, :, 0])
    Keep = Valid.sum(axis=1) == 2
    Ends, Valid = Ends[Keep], Valid[Keep]
    Order = np.argsort(~Valid, axis=1, kind="stable")[:, :2]       # the two crossing edges first
    P0 = np.take_along_axis(Ends, Order[:, :1, None], axis=1)[:, 0]
    P1 = np.take_along_axis(Ends, Order[:, 1:2, None], axis=1)[:, 0]
    W = np.linspace(0, 1, PerSegment)[None, :, None]
    P3 = (P0[:, None] * (1 - W) + P1[:, None] * W).reshape(-1, 3)
    return np.c_[P3 @ U, P3 @ V]


def _Bore(Mesh, Centre, U, V, Axis, Spread) -> dict:
    """Inner diameter (mesh units): at several heights through the band, slice the ring, take the
    nearest wall point in each of `Directions` angular bins around the bore centre, fit a circle,
    iterate the centre. A bin with no wall means the bore is not closed at that height."""
    Lo, Hi = np.percentile(Spread[:, 2], [5, 95])
    Results = []
    for Frac in (0.3, 0.5, 0.7):
        H = Lo + (Hi - Lo) * Frac
        P = _Section(Mesh, Centre + H * Axis, Axis, U, V)
        C2, Radius, Std, Filled = np.zeros(2), None, None, 0
        for _ in range(6):
            if len(P) < Directions:
                break
            Rel = P - C2
            Ang = np.arctan2(Rel[:, 1], Rel[:, 0])
            Dist = np.hypot(Rel[:, 0], Rel[:, 1])
            Bin = ((Ang + np.pi) / (2 * np.pi) * Directions).astype(int) % Directions
            Near = np.full(Directions, np.inf)
            np.minimum.at(Near, Bin, Dist)
            Ok = np.isfinite(Near)
            Filled = int(Ok.sum())
            if Filled < Directions * 0.9:
                break
            Mid = (np.arange(Directions) + 0.5) / Directions * 2 * np.pi - np.pi
            Wall = C2 + np.c_[np.cos(Mid), np.sin(Mid)][Ok] * Near[Ok][:, None]
            NewC, _ = _FitCircle(Wall)
            R = np.linalg.norm(Wall - NewC, axis=1)
            Radius, Std = float(np.median(R)), float(R.std())
            Moved = float(np.linalg.norm(NewC - C2))
            C2 = NewC
            if Moved < 1e-4 * Radius:
                break
        Results.append({"height": float(H), "centre": C2.tolist(), "radius": Radius, "std": Std, "bins_filled": Filled})
    Good = [R for R in Results if R["radius"] and R["bins_filled"] >= Directions * 0.9]
    if not Good:
        return {"ok": False, "slices": Results}
    Best = min(Good, key=lambda R: R["radius"])      # narrowest height through the band = sizing diameter
    return {"ok": True, "diameter": 2 * Best["radius"], "roundness": Best["std"] / Best["radius"],
            "centre": Best["centre"], "height": Best["height"], "slices": Results}


def _Measure(Mesh) -> Measurement:
    Ext = Mesh.extents
    Water = bool(Mesh.is_watertight)
    return Measurement(size_x_mm=float(Ext[0]), size_y_mm=float(Ext[1]), size_z_mm=float(Ext[2]),
                       volume_mm3=float(abs(Mesh.volume)) if Water else None,
                       surface_area_mm2=float(Mesh.area), watertight=Water)


def MeasureRing(Data: bytes, Fmt: str, TargetInnerDiameterMm: float) -> RingGeometry:
    Problems: list[str] = []
    Mesh = _Load(Data, Fmt)
    Raw = _Measure(Mesh)
    Raw.volume_mm3 = float(abs(Mesh.volume)) if Mesh.is_watertight else None
    Repair = _Repair(Mesh)
    Centre, U, V, Axis, Spread = _Frame(Mesh)
    Bore = _Bore(Mesh, Centre, U, V, Axis, Spread)
    Raw.checks = {**Repair, "bore": {K: Bore[K] for K in ("ok", "slices")}}
    if not Bore["ok"]:
        return RingGeometry(Raw, None, None, None, "needs_review", ["No closed ring bore was found — check the model."])
    Raw.inner_diameter_mm = Bore["diameter"]
    Scale = TargetInnerDiameterMm / Bore["diameter"]
    # Align: bore centre → origin, ring axis → Z, then scale (units become millimetres).
    C3 = Centre + Bore["centre"][0] * U + Bore["centre"][1] * V + Bore["height"] * Axis
    R = np.eye(4)
    R[:3, :3] = np.vstack([U, V, Axis])
    T = np.eye(4)
    T[:3, 3] = -C3
    S = np.eye(4) * Scale
    S[3, 3] = 1
    Prod = Mesh.copy()
    Prod.apply_transform(S @ R @ T)
    P = _Measure(Prod)
    Centre2, U2, V2, Axis2, Spread2 = _Frame(Prod)
    Check = _Bore(Prod, Centre2, U2, V2, Axis2, Spread2)
    P.inner_diameter_mm = Check["diameter"] if Check["ok"] else None
    P.checks = {
        **Repair, "bore_roundness": Bore["roundness"], "bore_angular_bins": Directions,
        "target_inner_diameter_mm": TargetInnerDiameterMm,
        "inner_diameter_after_scaling_mm": P.inner_diameter_mm,
        "uniform_scaling": "band width and thickness scale with the bore (known limitation)",
    }
    if not P.watertight:
        Problems.append("Mesh is not watertight after repair — volume and weight are not reliable.")
    if Bore["roundness"] > RoundnessLimit:
        Problems.append(f"Bore is not round (deviation {Bore['roundness']:.1%}) — check the inner diameter.")
    if P.inner_diameter_mm is None or abs(P.inner_diameter_mm - TargetInnerDiameterMm) > 0.02 * TargetInnerDiameterMm:
        Problems.append("Inner diameter after scaling does not match the target — check the model.")
    Buf = io.BytesIO()
    Prod.export(Buf, file_type="stl")
    return RingGeometry(Raw, P, Scale, Buf.getvalue(), "needs_review" if Problems else "measured", Problems)
