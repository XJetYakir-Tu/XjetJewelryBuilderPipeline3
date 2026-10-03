"""Delivery: thumbnails and posters made on first request, long cache for generated files, compressed text."""

import io
import shutil
from pathlib import Path

from PIL import Image

from p3 import media as Media

MockMovie = Path(__file__).resolve().parents[1] / "p3" / "providers" / "mock_assets" / "mock_movie.mp4"


async def test_thumbnails_are_made_on_first_request_and_cached(H):
    B = await H.NewDesign("A slim band")
    Url = B["candidates"][0]["image_url"]                     # /assets/designs/<id>/candidates/<cand>.png
    Rel = Url.split("/assets/", 1)[1]
    R = await H.Client.get(f"/thumb/{Rel}?w=320", headers={"Accept": "image/webp,image/*"})
    assert R.status_code == 200 and R.headers["content-type"] == "image/webp"
    assert R.headers["cache-control"] == "public, max-age=31536000, immutable" and R.headers["vary"] == "Accept"
    assert (H.Settings.AssetsDir / "_derived" / (Rel + "_w320.webp")).is_file()
    assert max(Image.open(io.BytesIO(R.content)).size) == 320
    R = await H.Client.get(f"/thumb/{Rel}?w=800", headers={"Accept": "image/avif,image/*"})    # no WebP → JPEG
    assert R.status_code == 200 and R.headers["content-type"] == "image/jpeg"
    assert max(Image.open(io.BytesIO(R.content)).size) <= 800                # never upscaled beyond the source
    assert (await H.Client.get(f"/thumb/{Rel}?w=800&f=png")).headers["content-type"] == "image/png"
    assert (await H.Client.get(f"/thumb/{Rel}?w=999")).status_code == 404
    assert (await H.Client.get("/thumb/designs/../../etc/passwd?w=320")).status_code == 404
    assert (await H.Client.get("/thumb/nothing/here.png?w=320")).status_code == 404
    # the original is untouched and cached for a year under its unique URL
    R = await H.Client.get(Url)
    assert R.status_code == 200 and R.headers["content-type"] == "image/png"
    assert R.headers["cache-control"] == "public, max-age=31536000, immutable"
    # the page and the scripts still revalidate on every load; versioned scripts are immutable
    assert (await H.Client.get("/")).headers["cache-control"] == "no-cache"
    assert (await H.Client.get("/static/app.js")).headers["cache-control"] == "no-cache"
    assert (await H.Client.get("/static/app.js?v=1")).headers["cache-control"] == "public, max-age=31536000, immutable"


async def test_text_is_compressed_but_media_is_not(H):
    R = await H.Client.get("/api/catalog", headers={"Accept-Encoding": "gzip"})
    assert R.status_code == 200 and R.headers.get("content-encoding") == "gzip"
    R = await H.Client.get("/", headers={"Accept-Encoding": "gzip"})
    assert R.status_code == 200 and R.headers.get("content-encoding") == "gzip"
    B = await H.NewDesign("A slim band")
    R = await H.Client.get(B["candidates"][0]["image_url"], headers={"Accept-Encoding": "gzip"})
    assert R.status_code == 200 and "content-encoding" not in R.headers
    Rel = B["candidates"][0]["image_url"].split("/assets/", 1)[1]
    R = await H.Client.get(f"/thumb/{Rel}?w=320", headers={"Accept-Encoding": "gzip"})
    assert R.status_code == 200 and "content-encoding" not in R.headers


async def test_movie_poster_comes_from_the_first_frame(H):
    Dst = H.Settings.AssetsDir / "designs" / "d_x" / "movies" / "m_x.mp4"
    Dst.parent.mkdir(parents=True)
    shutil.copyfile(MockMovie, Dst)
    R = await H.Client.get("/poster/designs/d_x/movies/m_x.mp4")
    if Media.FfmpegExe() is None:
        assert R.status_code == 404 and R.json()["error"]["code"] == "poster_unavailable"
        return
    assert R.status_code == 200 and R.headers["content-type"] == "image/jpeg"
    assert R.headers["cache-control"] == "public, max-age=31536000, immutable"
    Img = Image.open(io.BytesIO(R.content))
    assert Img.size[0] > 100 and Img.size[1] > 100
    assert (H.Settings.AssetsDir / "_derived" / "designs" / "d_x" / "movies" / "m_x.mp4.poster.jpg").is_file()
    # a movie that cannot be decoded gives a clean 404 and leaves no half-written file behind
    Bad = Dst.with_name("bad.mp4")
    Bad.write_bytes(bytes.fromhex("0000001866747970") + bytes(64))
    assert (await H.Client.get("/poster/designs/d_x/movies/bad.mp4")).status_code == 404
    assert not list((H.Settings.AssetsDir / "_derived" / "designs" / "d_x" / "movies").glob("*.tmp*"))
    assert (await H.Client.get("/poster/designs/x/movies/nope.mp4")).status_code == 404
    B = await H.NewDesign("A slim band")
    assert (await H.Client.get(f"/poster/{B['candidates'][0]['image_url'].split('/assets/', 1)[1]}")).status_code == 404
