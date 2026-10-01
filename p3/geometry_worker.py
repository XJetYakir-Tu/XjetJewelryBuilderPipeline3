"""Run one ring measurement in its own process (python -m p3.geometry_worker ...).

Multi-million-face models need a lot of memory. Running the measurement in a child process with a
hard address-space cap (P3_GEOMETRY_MEMORY_MB, Linux) means a model that does not fit only fails this
measurement — it can never take the web server down (an out-of-memory kill of the server is what
happened with the first 5,000,000-face Hi3D model).

  argv: <source file> <format> <target inner diameter mm> <out production.stl> <out preview.stl> <out result.json>
  exit: 0 = result written · 3 = out of memory · 1 = other error (message in result.json)
"""

import json
import os
import sys
from dataclasses import asdict

ExitMemory = 3


def _Cap() -> None:
    Mb = int(os.environ.get("P3_GEOMETRY_MEMORY_MB", "2500"))
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (Mb * 1024 * 1024, Mb * 1024 * 1024))
    except (ImportError, ValueError, OSError):      # not available on Windows: run uncapped
        pass


def Main(Argv: list[str]) -> int:
    Src, Fmt, Target, OutStl, OutPreview, OutJson = Argv
    _Cap()
    try:
        from p3.geometry import MeasureRingFile
        G = MeasureRingFile(Src, Fmt, float(Target), OutStl, OutPreview)
        Doc = {"ok": True, "status": G.status, "problems": G.problems, "faces": G.faces,
               "scale_factor": G.scale_factor, "raw": asdict(G.raw),
               "production": asdict(G.production) if G.production else None}
        Code = 0
    except MemoryError:
        Doc, Code = {"ok": False, "error": "memory"}, ExitMemory
    except Exception as E:  # noqa: BLE001
        Doc, Code = {"ok": False, "error": f"{type(E).__name__}: {E}"}, 1
    with open(OutJson, "w", encoding="utf-8") as F:
        json.dump(Doc, F, default=float)
    return Code


if __name__ == "__main__":
    sys.exit(Main(sys.argv[1:]))
