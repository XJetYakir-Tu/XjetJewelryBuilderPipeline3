"""Self-hosted front-end: no third-party hosts on any page, vendored files served and versioned, and the Tailwind
production build (web/vendor/tailwind.css) covers every class the pages use — a reminder to rebuild it after edits."""

import re
from pathlib import Path

Web = Path(__file__).resolve().parents[1] / "web"
Pages = ("index.html", "admin.html", "dev.html", "verify.html")
ThirdParty = ("cdn.tailwindcss.com", "unpkg.com", "jsdelivr.net", "googleapis.com", "gstatic.com", "cdnjs.cloudflare.com")


def test_pages_load_nothing_from_third_party_hosts():
    for Name in Pages:
        Html = (Web / Name).read_text(encoding="utf-8")
        for Host in ThirdParty:
            assert Host not in Html, (Name, Host)


async def test_vendored_files_are_served_and_versioned(H):
    for Rel, Ctype in [("vendor/tailwind.css", "text/css"), ("vendor/fonts.css", "text/css"), ("vendor/alpine.min.js", "javascript"),
                       ("vendor/three.min.js", "javascript"), ("vendor/STLLoader.js", "javascript"), ("vendor/OrbitControls.js", "javascript"),
                       ("vendor/fonts/inter-400-latin.woff2", "font/woff2"), ("vendor/fonts/cinzel-400-latin.woff2", "font/woff2")]:
        R = await H.Client.get(f"/static/{Rel}")
        assert R.status_code == 200 and Ctype in R.headers["content-type"], (Rel, R.status_code, R.headers.get("content-type"))
    Html = (await H.Client.get("/")).text
    assert re.search(r'/static/vendor/tailwind\.css\?v=\d+', Html) and re.search(r'/static/vendor/alpine\.min\.js\?v=\d+', Html)
    assert re.search(r'/static/vendor/fonts\.css\?v=\d+', Html)
    Admin = (await H.Client.get("/admin/")).text
    assert re.search(r'/static/vendor/three\.min\.js\?v=\d+', Admin) and re.search(r'/static/vendor/tailwind\.css\?v=\d+', Admin)
    Dev = (await H.Client.get("/dev")).text
    assert re.search(r'/static/vendor/alpine\.min\.js\?v=\d+', Dev)
    # a versioned vendor file is cacheable for a year; the fonts a month
    assert (await H.Client.get("/static/vendor/tailwind.css?v=1")).headers["cache-control"] == "public, max-age=31536000, immutable"
    assert (await H.Client.get("/static/vendor/fonts/inter-400-latin.woff2")).headers["cache-control"] == "public, max-age=2592000"
    # every font file the stylesheet names exists, and the stylesheet names only relative files
    Css = (Web / "vendor" / "fonts.css").read_text(encoding="utf-8")
    Files = re.findall(r"url\(([^)]+)\)", Css)
    assert Files and all(F.startswith("fonts/") for F in Files)
    for F in Files:
        assert (Web / "vendor" / F).is_file(), F


# Utility prefixes (the part before the first "-", after any variants) — string literals in :class expressions that
# are not class lists (view names, field names, statuses) are told apart by this list.
Utility = set("""bg text border opacity scale translate rotate w h p px py pt pb pl pr m mx my mt mb ml mr gap grid col row flex
items justify font tracking leading shadow ring rounded z top left right bottom inset max min overflow pointer cursor select
transition duration ease delay animate fill stroke outline decoration underline line list object order place space divide sr
aspect basis break columns content float clear box hidden visible invisible block inline table static fixed absolute relative
sticky truncate uppercase lowercase capitalize italic whitespace mix backdrop blur brightness contrast grayscale drop group peer
accent caret scroll snap touch will resize appearance align indent hyphens from via to size filter transform origin skew
container antialiased subpixel tabular normal nums self auto-cols auto-rows col-span row-span grow shrink no""".split())


def _IsUtility(Tok: str) -> bool:
    Base = Tok.rsplit(":", 1)[-1].lstrip("-!")
    return Base.split("-", 1)[0].split("[", 1)[0] in Utility or "[" in Tok or ":" in Tok


def _Classes(Html: str) -> set[str]:
    Toks = set()
    for M in re.finditer(r'\sclass="([^"]*)"', Html):
        Toks.update(M.group(1).split())
    for M in re.finditer(r'\s(?::|x-bind:)class="([^"]*)"', Html):
        for Lit in re.findall(r"'([^']*)'", M.group(1)):
            Toks.update(T for T in Lit.split() if _IsUtility(T))
    return {T for T in Toks if T and not T.startswith(("{{", "$"))}


def _Esc(Tok: str) -> str:
    """The class as Tailwind writes it in the stylesheet (CSS identifier escaping; cssesc leaves no space after a hex escape)."""
    Out = ""
    for I, Ch in enumerate(Tok):
        if Ch.isascii() and (Ch.isalnum() or Ch in "-_"):
            Out += f"\\3{Ch}" if I == 0 and Ch.isdigit() else Ch
        elif Ch == ",":
            Out += "\\2c "
        else:
            Out += "\\" + Ch
    return Out


def test_built_stylesheet_covers_every_class_in_the_pages():
    Css = (Web / "vendor" / "tailwind.css").read_text(encoding="utf-8")
    Own = (Web / "styles.css").read_text(encoding="utf-8")
    Missing = []
    for Name in ("index.html", "admin.html", "dev.html"):
        Html = (Web / Name).read_text(encoding="utf-8")
        Inline = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", Html, re.S))
        for Tok in sorted(_Classes(Html)):
            if f".{_Esc(Tok)}" in Css or re.search(r"\." + re.escape(Tok) + r"(?![\w-])", Inline + Own):
                continue
            if not _IsUtility(Tok) and "-" not in Tok:          # a plain hook class (page JS / styles), not a utility
                continue
            Missing.append((Name, Tok))
    assert not Missing, f"Rebuild web/vendor/tailwind.css (see tailwind.config.js); not covered: {Missing[:40]}"
