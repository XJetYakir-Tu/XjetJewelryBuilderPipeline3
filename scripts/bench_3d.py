"""Time the local 3D geometry stages on one STL (before/after comparison of the processing flow).

  python scripts/bench_3d.py <file.stl> [--target-mm 18.945] [--method current|fast] [--out DIR]

Prints one JSON object: per-stage seconds, faces, file size and (Linux) peak memory. "current" is the
v2 flow (measure + write the full scaled STL + preview); "fast" is the measure-once flow, when present.
Nothing is sent to any provider.
"""

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def PeakMb() -> float | None:
    try:
        import resource
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)     # Linux: kB
    except ImportError:
        return None


def BenchCurrent(Src: Path, Target: float, Out: Path) -> dict:
    import numpy as np
    from p3 import geometry as g
    T, Times = time.perf_counter(), {}

    def Lap(Name):
        nonlocal T
        Now = time.perf_counter()
        Times[Name] = round(Now - T, 2)
        T = Now

    Tri = g.LoadTriangles(Src, "stl"); Lap("load_stl")
    Water = g._Watertight(Tri); Lap("watertight_check")
    Raw = g._Measure(Tri, Water); Lap("raw_measure")
    Centre, U, V, Axis, Spread = g._Frame(Tri); Lap("frame_axis")
    Bore = g._Bore(Tri, Centre, U, V, Axis, Spread); Lap("bore_detect")
    Scale = Target / Bore["diameter"]
    C3 = (Centre + Bore["centre"][0] * U + Bore["centre"][1] * V + Bore["height"] * Axis).astype(np.float32)
    M = (np.vstack([U, V, Axis]).T * Scale).astype(np.float32)
    for S in range(0, len(Tri), g.Chunk):
        Tri[S:S + g.Chunk] = (Tri[S:S + g.Chunk] - C3) @ M
    Lap("align_scale")
    P = g._Measure(Tri, Water); Lap("scaled_measure")
    C2, U2, V2, A2, S2 = g._Frame(Tri); Check = g._Bore(Tri, C2, U2, V2, A2, S2); Lap("bore_recheck")
    g.WriteStl(Tri, Out / "scaled.stl"); Lap("write_scaled_stl")
    Pv = g.Preview(Tri); g.WriteStl(Pv, Out / "preview.stl"); Lap("preview")
    return {"times_s": Times, "total_s": round(sum(Times.values()), 2), "faces": int(len(Tri)),
            "inner_diameter_mm": round(Check["diameter"], 4) if Check["ok"] else None,
            "volume_mm3": round(P.volume_mm3, 3) if P.volume_mm3 else None, "area_mm2": round(P.surface_area_mm2, 3),
            "preview_faces": int(len(Pv)), "watertight": Water}


def BenchFast(Src: Path, Target: float, Out: Path) -> dict:
    from p3 import geometry as g
    if not hasattr(g, "MeasureRaw"):
        raise SystemExit("this version has no fast (measure-once) flow")
    T0 = time.perf_counter()
    Raw = g.MeasureRaw(Src)
    T1 = time.perf_counter()
    Final = g.Scaled(Raw, Target)
    T2 = time.perf_counter()
    g.WritePreview(Src, Raw, Out / "preview.bin")
    T3 = time.perf_counter()
    g.ExportScaledStl(Src, Raw, Target, Out / "scaled.stl")
    T4 = time.perf_counter()
    return {"times_s": {"measure_raw_once": round(T1 - T0, 2), "scale_values": round(T2 - T1, 4),
                        "preview": round(T3 - T2, 2), "export_scaled_stl_on_demand": round(T4 - T3, 2)},
            "total_to_results_s": round(T2 - T0, 2), "faces": Raw["faces"],
            "inner_diameter_mm": round(Final["inner_diameter_mm"], 4), "volume_mm3": round(Final["volume_mm3"], 3),
            "area_mm2": round(Final["surface_area_mm2"], 3), "closed_heuristic": Raw["closed_heuristic"]}


def Main() -> None:
    A = argparse.ArgumentParser()
    A.add_argument("stl", type=Path)
    A.add_argument("--target-mm", type=float, default=18.945)
    A.add_argument("--method", choices=("current", "fast"), default="current")
    A.add_argument("--out", type=Path, default=None)
    Args = A.parse_args()
    with tempfile.TemporaryDirectory() as D:
        Out = Args.out or Path(D)
        Out.mkdir(parents=True, exist_ok=True)
        R = (BenchCurrent if Args.method == "current" else BenchFast)(Args.stl, Args.target_mm, Out)
    print(json.dumps({"method": Args.method, "file_mb": round(Args.stl.stat().st_size / 1e6, 1), **R,
                      "peak_rss_mb": PeakMb()}, indent=1))


if __name__ == "__main__":
    Main()
