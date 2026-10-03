"""Derived media for fast delivery.

Thumbnails of candidate and reference images (WebP or JPEG, 320 or 800 px on the long side) for the places
that only show a small image — gallery tiles, My Designs, bag lines, Admin lists — and a poster frame for
each 360° movie so the player shows the ring at once. Nothing here touches an original: the full-size PNG
stays the file behind every large view, zoom and download. Everything is made on first request and cached
under <assets>/_derived, so existing assets need no migration and a regenerated original invalidates its
derivatives by modification time.
"""

import io
import logging
import os
import subprocess
import threading
from pathlib import Path

from PIL import Image

from p3 import assets

Widths = (320, 800)
Formats = {"webp": ("WEBP", "image/webp", {"quality": 82, "method": 4}),
           "jpg": ("JPEG", "image/jpeg", {"quality": 86, "progressive": True, "optimize": True}),
           "png": ("PNG", "image/png", {"optimize": True})}
ImageSuffixes = (".png", ".jpg", ".jpeg", ".webp")
Logger = logging.getLogger("p3.media")


def _Derived(Root: Path, RelPath: str, Suffix: str) -> Path:
    return Root / "_derived" / (RelPath + Suffix)


def _Replace(Tmp: Path, Out: Path) -> None:
    Out.parent.mkdir(parents=True, exist_ok=True)
    os.replace(Tmp, Out)


def Thumb(Root: Path, RelPath: str, Width: int, Fmt: str) -> tuple[Path, str]:
    """The thumbnail file (made now when missing or older than its source) and its content type."""
    if Width not in Widths:
        raise assets.AssetError("Unsupported thumbnail width")
    if Fmt not in Formats:
        raise assets.AssetError("Unsupported thumbnail format")
    Src = assets.Resolve(Root, RelPath)
    if not RelPath.startswith("designs/") or Src.suffix.lower() not in ImageSuffixes or not Src.is_file():
        raise assets.AssetError("Not an image asset")
    PilFmt, Ctype, Opts = Formats[Fmt]
    Out = _Derived(Root, RelPath, f"_w{Width}.{Fmt}")
    if not Out.is_file() or Out.stat().st_mtime < Src.stat().st_mtime:
        with Image.open(Src) as Img:
            Img.load()
            if Fmt == "jpg":
                if Img.mode in ("RGBA", "LA", "P"):
                    Rgba = Img.convert("RGBA")
                    Flat = Image.new("RGB", Rgba.size, (255, 255, 255))
                    Flat.paste(Rgba, mask=Rgba.split()[3])
                    Img = Flat
                else:
                    Img = Img.convert("RGB")
            elif Img.mode not in ("RGB", "RGBA"):
                Img = Img.convert("RGBA")
            Img.thumbnail((Width, Width), Image.LANCZOS)
            Buf = io.BytesIO()
            Img.save(Buf, format=PilFmt, **Opts)
        Out.parent.mkdir(parents=True, exist_ok=True)
        Tmp = Out.with_name(f"{Out.name}.{os.getpid()}-{threading.get_ident()}.tmp")
        Tmp.write_bytes(Buf.getvalue())
        _Replace(Tmp, Out)
    return Out, Ctype


def FfmpegExe() -> str | None:
    """The bundled ffmpeg (imageio-ffmpeg wheel) when installed; None otherwise — posters are then skipped."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 — optional dependency
        return None


def Poster(Root: Path, RelPath: str) -> Path | None:
    """A JPEG of the movie's first frame — None when no ffmpeg is available or the movie cannot be decoded."""
    Src = assets.Resolve(Root, RelPath)
    if not RelPath.startswith("designs/") or Src.suffix.lower() != ".mp4" or not Src.is_file():
        raise assets.AssetError("Not a movie asset")
    Out = _Derived(Root, RelPath, ".poster.jpg")
    if Out.is_file() and Out.stat().st_mtime >= Src.stat().st_mtime:
        return Out
    Exe = FfmpegExe()
    if not Exe:
        return None
    Out.parent.mkdir(parents=True, exist_ok=True)
    Tmp = Out.with_name(f"{Out.name}.{os.getpid()}-{threading.get_ident()}.tmp.jpg")
    try:
        subprocess.run([Exe, "-y", "-loglevel", "error", "-ss", "0.2", "-i", str(Src), "-frames:v", "1", "-q:v", "3", str(Tmp)],
                       check=True, timeout=60, capture_output=True)
        _Replace(Tmp, Out)
        return Out
    except Exception as E:  # noqa: BLE001 — a missing poster only costs the instant first frame
        Logger.warning("No poster frame for %s: %s", RelPath, E)
        Tmp.unlink(missing_ok=True)
        return None


def ThumbUrl(AssetUrl: str | None, Width: int = 320, Fmt: str | None = None) -> str | None:
    """The thumbnail URL for an asset URL (as the website derives it), e.g. for social previews."""
    if not AssetUrl or "/assets/" not in AssetUrl:
        return AssetUrl
    Url = AssetUrl.replace("/assets/", "/thumb/", 1) + f"?w={Width}"
    return Url + (f"&f={Fmt}" if Fmt else "")
