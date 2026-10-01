"""Ring geometry: find the bore, scale to a ring size, measure — memory-lean for multi-million-face models.

Hi3D returns a mesh in arbitrary units and orientation (an STL at 5,000,000 faces is ~250 MB). The
whole pipeline works on one compact float32 triangle array (faces × 3 corners × xyz), processed in
chunks, so peak memory stays a small multiple of the file size:

  1. load: binary STL is read straight into the array (no per-vertex objects); GLB/OBJ via trimesh;
  2. watertight check: every edge must be shared by exactly two triangles (vertices quantised so the
     unmerged STL corners match). Small open meshes (≤ RepairMaxFaces) get trimesh's repair; large
     open ones are reported as not watertight (volume unreliable) instead of an unbounded repair;
  3. ring axis = direction of least surface spread (area-weighted triangle sample);
  4. bore: slice at three heights through the band, nearest wall point per 5° bin around the centre,
     circle fit with an iterated centre; inner diameter = 2 × median wall radius at the narrowest height;
  5. align (bore centre → origin, ring axis → Z) and scale uniformly to the target inner diameter,
     in place; measure X/Y/Z, inner diameter (re-checked), volume (divergence theorem) and area;
  6. write the scaled STL and a light preview STL (vertex-clustered, ~PreviewFaces) for the viewer.

Uniform scaling also scales band width and thickness — a known limitation, recorded in the checks.
"""

import io
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

MethodVersion = "ring-bore-sections-v2"
RoundnessLimit = 0.04           # (std of bore radius) / radius above this → needs review
Directions = 72
Chunk = 1_000_000               # triangles per chunk for whole-mesh passes
RepairMaxFaces = 1_000_000      # trimesh repair (graph based) only below this size
PreviewFaces = 120_000          # browser viewer copy (~6 MB STL)


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
    production_stl: bytes | None          # only filled by MeasureRing (in-memory convenience)
    status: str                           # measured | needs_review | failed
    problems: list
    faces: int = 0


# ── loading ─────────────────────────────────────────────────────────────────
StlRecord = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])


def LoadTriangles(PathObj: Path, Fmt: str) -> np.ndarray:
    """(F, 3, 3) float32 triangles."""
    PathObj = Path(PathObj)
    if Fmt == "stl":
        with open(PathObj, "rb") as F:
            Head = F.read(84)
        Count = int(np.frombuffer(Head[80:84], "<u4")[0]) if len(Head) == 84 else 0
        if Count and os.path.getsize(PathObj) == 84 + 50 * Count:      # binary STL
            Rec = np.memmap(PathObj, dtype=StlRecord, mode="r", offset=84, shape=(Count,))
            Tri = np.empty((Count, 3, 3), np.float32)
            for S in range(0, Count, Chunk):
                Tri[S:S + Chunk] = Rec["v"][S:S + Chunk]
            del Rec
            return Tri
    import trimesh                                                     # ASCII STL, GLB, OBJ
    Loaded = trimesh.load(str(PathObj), file_type=Fmt, force=None, process=False)
    if isinstance(Loaded, trimesh.Scene):
        Loaded = Loaded.to_geometry() if hasattr(Loaded, "to_geometry") else Loaded.dump(concatenate=True)
    if not isinstance(Loaded, trimesh.Trimesh) or len(Loaded.faces) == 0:
        raise ValueError("The 3D file contains no mesh")
    return np.asarray(Loaded.triangles, dtype=np.float32)


# ── whole-mesh quantities (chunked) ───────────────────────────────────────────
def _Bounds(Tri) -> tuple[np.ndarray, np.ndarray]:
    Lo, Hi = np.full(3, np.inf), np.full(3, -np.inf)
    for S in range(0, len(Tri), Chunk):
        C = Tri[S:S + Chunk].reshape(-1, 3)
        Lo, Hi = np.minimum(Lo, C.min(0)), np.maximum(Hi, C.max(0))
    return Lo, Hi


def _VolumeArea(Tri) -> tuple[float, float]:
    Vol = Area = 0.0
    for S in range(0, len(Tri), Chunk):
        C = Tri[S:S + Chunk].astype(np.float64)
        Cr = np.cross(C[:, 1] - C[:, 0], C[:, 2] - C[:, 0])
        Area += float(np.linalg.norm(Cr, axis=1).sum()) / 2
        Vol += float(np.einsum("ij,ij->i", C[:, 0], np.cross(C[:, 1], C[:, 2])).sum()) / 6
    return abs(Vol), Area


def _CornerKeys(Tri, Lo, Step, Round=True) -> np.ndarray:
    """One int64 per triangle corner: the corner quantised to a grid (21 bits per axis), packed."""
    Out = np.empty(len(Tri) * 3, np.int64)
    for S in range(0, len(Tri), Chunk):
        C = (Tri[S:S + Chunk].reshape(-1, 3) - Lo) / Step
        Q = (np.round(C) if Round else np.floor(C)).astype(np.int64)
        np.clip(Q, 0, (1 << 21) - 1, out=Q)
        Out[S * 3:(S + len(C) // 3) * 3] = (Q[:, 0] << 42) | (Q[:, 1] << 21) | Q[:, 2]
    return Out


def _Watertight(Tri) -> bool:
    """Every edge used by exactly two triangles (corners quantised so unmerged STL corners match)."""
    Lo, Hi = _Bounds(Tri)
    Step = max(float((Hi - Lo).max()), 1e-12) / ((1 << 21) - 2)
    _, Inv = np.unique(_CornerKeys(Tri, Lo, Step), return_inverse=True)
    Ids = Inv.reshape(-1, 3)
    del Inv
    E = np.concatenate([Ids[:, [0, 1]], Ids[:, [1, 2]], Ids[:, [2, 0]]])
    del Ids
    E.sort(axis=1)
    Key = E[:, 0] * (int(E.max()) + 1) + E[:, 1]
    del E
    Key.sort()
    Change = np.flatnonzero(np.diff(Key)) + 1
    Counts = np.diff(np.r_[0, Change, len(Key)])
    return bool(len(Counts)) and bool((Counts == 2).all())


def _TrimeshRepair(Tri) -> tuple[np.ndarray, dict]:
    import trimesh
    M = trimesh.Trimesh(vertices=Tri.reshape(-1, 3), faces=np.arange(len(Tri) * 3).reshape(-1, 3), process=True)
    Before = bool(M.is_watertight)
    M.update_faces(M.nondegenerate_faces())
    M.update_faces(M.unique_faces())
    M.remove_unreferenced_vertices()
    trimesh.repair.fix_normals(M)
    if not M.is_watertight:
        trimesh.repair.fill_holes(M)
    return np.asarray(M.triangles, dtype=np.float32), {"watertight_before_repair": Before,
                                                       "watertight_after_repair": bool(M.is_watertight)}


# ── frame and bore ───────────────────────────────────────────────────────────
def _Frame(Tri):
    """Ring axis (least surface spread) and two in-plane axes, from an area-weighted sample."""
    Rng = np.random.default_rng(7)
    Idx = Rng.choice(len(Tri), size=min(len(Tri), 200_000), replace=False) if len(Tri) > 200_000 else np.arange(len(Tri))
    T = Tri[Idx].astype(np.float64)
    W = np.linalg.norm(np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]), axis=1)
    if W.sum() <= 0:
        W = np.ones(len(T))
    Pick = Rng.choice(len(T), size=4000, p=W / W.sum())
    A, B = Rng.random((2, 4000, 1))
    Flip = (A + B) > 1
    A, B = np.where(Flip, 1 - A, A), np.where(Flip, 1 - B, B)
    Points = T[Pick, 0] + A * (T[Pick, 1] - T[Pick, 0]) + B * (T[Pick, 2] - T[Pick, 0])
    Centre = Points.mean(axis=0)
    _, _, Vt = np.linalg.svd(Points - Centre, full_matrices=False)
    return Centre, Vt[0], Vt[1], Vt[2], (Points - Centre) @ Vt.T


def _FitCircle(P: np.ndarray) -> tuple[np.ndarray, float]:
    A = np.c_[2 * P, np.ones(len(P))]
    Sol, *_ = np.linalg.lstsq(A, (P ** 2).sum(axis=1), rcond=None)
    C = Sol[:2]
    return C, float(np.sqrt(max(Sol[2] + C @ C, 0.0)))


def _Section(Tri, Origin, Axis, U, V, PerSegment: int = 8) -> np.ndarray:
    """Cross-section with the plane through Origin normal to Axis, as (U, V) points sampled along
    each crossing triangle's segment. Only the crossing triangles are copied."""
    Off = float(Origin @ Axis)
    Parts = []
    for S in range(0, len(Tri), Chunk):
        C = Tri[S:S + Chunk]
        D = C @ Axis.astype(np.float32) - Off
        Cross = (D.min(axis=1) < 0) & (D.max(axis=1) > 0)
        if Cross.any():
            Parts.append((C[Cross].astype(np.float64) - Origin, D[Cross].astype(np.float64)))
    if not Parts:
        return np.zeros((0, 2))
    T = np.concatenate([P[0] for P in Parts])
    D = np.concatenate([P[1] for P in Parts])
    Ends = np.full((len(T), 3, 3), np.nan)
    for K, (A, B) in enumerate(((0, 1), (1, 2), (2, 0))):
        M = (D[:, A] * D[:, B]) < 0
        F = D[M, A] / (D[M, A] - D[M, B])
        Ends[M, K] = T[M, A] + F[:, None] * (T[M, B] - T[M, A])
    Valid = ~np.isnan(Ends[:, :, 0])
    Keep = Valid.sum(axis=1) == 2
    Ends, Valid = Ends[Keep], Valid[Keep]
    Order = np.argsort(~Valid, axis=1, kind="stable")[:, :2]
    P0 = np.take_along_axis(Ends, Order[:, :1, None], axis=1)[:, 0]
    P1 = np.take_along_axis(Ends, Order[:, 1:2, None], axis=1)[:, 0]
    W = np.linspace(0, 1, PerSegment)[None, :, None]
    P3 = (P0[:, None] * (1 - W) + P1[:, None] * W).reshape(-1, 3)
    if len(P3) > 400_000:                                     # dense meshes: plenty of wall points
        P3 = P3[np.random.default_rng(3).choice(len(P3), 400_000, replace=False)]
    return np.c_[P3 @ U, P3 @ V]


def _Bore(Tri, Centre, U, V, Axis, Spread) -> dict:
    Lo, Hi = np.percentile(Spread[:, 2], [5, 95])
    Results = []
    for Frac in (0.3, 0.5, 0.7):
        H = Lo + (Hi - Lo) * Frac
        P = _Section(Tri, Centre + H * Axis, Axis, U, V)
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
    Best = min(Good, key=lambda R: R["radius"])
    return {"ok": True, "diameter": 2 * Best["radius"], "roundness": Best["std"] / Best["radius"],
            "centre": Best["centre"], "height": Best["height"], "slices": Results}


# ── output ───────────────────────────────────────────────────────────────────
def WriteStl(Tri, PathObj: Path) -> None:
    """Binary STL, streamed in chunks."""
    with open(PathObj, "wb") as F:
        F.write(b"XJet P3 ring geometry".ljust(80, b" "))
        F.write(np.uint32(len(Tri)).tobytes())
        for S in range(0, len(Tri), Chunk):
            C = Tri[S:S + Chunk]
            N = np.cross(C[:, 1] - C[:, 0], C[:, 2] - C[:, 0])
            L = np.linalg.norm(N, axis=1, keepdims=True)
            Rec = np.zeros(len(C), StlRecord)
            Rec["n"] = N / np.where(L > 0, L, 1)
            Rec["v"] = C
            F.write(Rec.tobytes())


def Preview(Tri, Target: int = PreviewFaces) -> np.ndarray:
    """A light copy for the browser viewer: snap corners to a grid, drop collapsed / duplicate faces."""
    if len(Tri) <= Target:
        return Tri
    Lo, Hi = _Bounds(Tri)
    Size = float((Hi - Lo).max())
    Out = Tri
    Cells = int(np.sqrt(Target / 2.0) * 2.2)                  # grid resolution along the longest side
    for _ in range(6):
        Step = Size / max(Cells, 8)
        Keys, Inv = np.unique(_CornerKeys(Tri, Lo, Step, Round=False), return_inverse=True)
        Cell = np.c_[Keys >> 42, (Keys >> 21) & ((1 << 21) - 1), Keys & ((1 << 21) - 1)]
        Cent = (Cell + 0.5) * Step + Lo
        Ids = Inv.reshape(-1, 3)
        Ok = (Ids[:, 0] != Ids[:, 1]) & (Ids[:, 1] != Ids[:, 2]) & (Ids[:, 0] != Ids[:, 2])
        Ids = np.unique(np.sort(Ids[Ok], axis=1), axis=0)
        Out = Cent[Ids].astype(np.float32)
        if len(Out) <= Target * 1.25:
            break
        Cells = int(Cells * 0.8)
    return Out


# ── main entry points ────────────────────────────────────────────────────────
def _Measure(Tri, Water: bool) -> Measurement:
    Lo, Hi = _Bounds(Tri)
    Vol, Area = _VolumeArea(Tri)
    Ext = Hi - Lo
    return Measurement(size_x_mm=float(Ext[0]), size_y_mm=float(Ext[1]), size_z_mm=float(Ext[2]),
                       volume_mm3=Vol if Water else None, surface_area_mm2=Area, watertight=Water)


def MeasureRingFile(Source: Path, Fmt: str, TargetInnerDiameterMm: float,
                    OutStl: Path | None = None, OutPreview: Path | None = None) -> RingGeometry:
    Problems: list[str] = []
    Tri = LoadTriangles(Source, Fmt)
    Faces = len(Tri)
    Water = _Watertight(Tri)
    Repair = {"watertight_before_repair": Water, "watertight_after_repair": Water, "faces": Faces}
    if not Water and Faces <= RepairMaxFaces:
        Tri, R = _TrimeshRepair(Tri)
        Repair.update(R)
        Water = R["watertight_after_repair"]
    elif not Water:
        Repair["repair_skipped"] = f"open mesh with {Faces:,} faces — too large for automatic repair"
    Raw = _Measure(Tri, Water)
    Centre, U, V, Axis, Spread = _Frame(Tri)
    Bore = _Bore(Tri, Centre, U, V, Axis, Spread)
    Raw.checks = {**Repair, "bore": {K: Bore[K] for K in ("ok", "slices")}}
    if not Bore["ok"]:
        return RingGeometry(Raw, None, None, None, "needs_review", ["No closed ring bore was found — check the model."], Faces)
    Raw.inner_diameter_mm = Bore["diameter"]
    Scale = TargetInnerDiameterMm / Bore["diameter"]
    # Align (bore centre → origin, ring axis → Z) and scale to millimetres — in place, in chunks.
    C3 = Centre + Bore["centre"][0] * U + Bore["centre"][1] * V + Bore["height"] * Axis
    M = (np.vstack([U, V, Axis]).T * Scale).astype(np.float32)          # row-vector transform
    C3 = C3.astype(np.float32)
    for S in range(0, len(Tri), Chunk):
        Tri[S:S + Chunk] = (Tri[S:S + Chunk] - C3) @ M
    P = _Measure(Tri, Water)
    Centre2, U2, V2, Axis2, Spread2 = _Frame(Tri)
    Check = _Bore(Tri, Centre2, U2, V2, Axis2, Spread2)
    P.inner_diameter_mm = Check["diameter"] if Check["ok"] else None
    P.checks = {
        **Repair, "bore_roundness": Bore["roundness"], "bore_angular_bins": Directions,
        "target_inner_diameter_mm": TargetInnerDiameterMm, "inner_diameter_after_scaling_mm": P.inner_diameter_mm,
        "uniform_scaling": "band width and thickness scale with the bore (known limitation)",
    }
    if not P.watertight:
        Problems.append("Mesh is not watertight — volume and weight are not reliable.")
    if Bore["roundness"] > RoundnessLimit:
        Problems.append(f"Bore is not round (deviation {Bore['roundness']:.1%}) — check the inner diameter.")
    if P.inner_diameter_mm is None or abs(P.inner_diameter_mm - TargetInnerDiameterMm) > 0.02 * TargetInnerDiameterMm:
        Problems.append("Inner diameter after scaling does not match the target — check the model.")
    if OutStl:
        WriteStl(Tri, OutStl)
    if OutPreview:
        Pv = Preview(Tri)
        P.checks["preview_faces"] = int(len(Pv))
        WriteStl(Pv, OutPreview)
    return RingGeometry(Raw, P, Scale, None, "needs_review" if Problems else "measured", Problems, Faces)


def MeasureRing(Data: bytes, Fmt: str, TargetInnerDiameterMm: float) -> RingGeometry:
    """In-memory convenience (tests, small files): same as MeasureRingFile, returns the STL bytes."""
    with tempfile.TemporaryDirectory() as D:
        Src = Path(D) / f"in.{Fmt}"
        Src.write_bytes(Data)
        Out = Path(D) / "out.stl"
        G = MeasureRingFile(Src, Fmt, TargetInnerDiameterMm, Out)
        if G.production is not None:
            G.production_stl = Out.read_bytes()
        return G
