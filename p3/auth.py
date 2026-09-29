"""Access boundaries.

Customer: an access token (header X-Access-Token) issued by an operator with
`python -m p3.cli create-token`. Unlike Pipeline 2, generation endpoints do not
accept anonymous requests. Email self-registration is not part of Pipeline 3 yet.

Developer: the P3_ADMIN_KEY bearer secret, checked server-side on every /api/dev
route. When P3_ADMIN_KEY is unset, developer routes are disabled entirely.
"""

import hmac
import secrets

from p3.context import Context, HttpError
from p3.db import Now


def CreateToken(Ctx: Context, Label: str) -> str:
    Token = "p3_" + secrets.token_urlsafe(24)
    Ctx.Db.Execute("INSERT INTO access_tokens (token, label, active, created_at) VALUES (?,?,1,?)",
                   (Token, Label, Now()))
    return Token


def RequireToken(Ctx: Context, Token: str | None) -> str:
    if not Token:
        raise HttpError(401, "token_required", "An access token is required.")
    Row = Ctx.Db.One("SELECT token, active FROM access_tokens WHERE token = ?", (Token.strip(),))
    if Row is None or not Row["active"]:
        raise HttpError(401, "token_invalid", "This access token is not valid or has been deactivated.")
    return Row["token"]


def RequireDeveloper(Ctx: Context, Authorization: str | None) -> None:
    Expected = Ctx.Settings.AdminKey
    if not Expected:
        raise HttpError(503, "developer_tools_disabled", "Developer tools are disabled (P3_ADMIN_KEY is not set).")
    Supplied = (Authorization or "").removeprefix("Bearer ").strip()
    if not Supplied or not hmac.compare_digest(Supplied.encode(), Expected.encode()):
        raise HttpError(403, "developer_auth_required", "Developer authorization required.")


def SessionJson(Ctx: Context, Token: str) -> dict:
    Row = Ctx.Db.One("SELECT label, created_at FROM access_tokens WHERE token = ?", (Token,))
    Usage = {R["kind"]: R["units"] for R in Ctx.Db.All(
        "SELECT kind, SUM(units) AS units FROM usage_events WHERE token = ? GROUP BY kind", (Token,))}
    return {"label": Row["label"], "created_at": Row["created_at"],
            "usage": {"image_requests": Usage.get("image", 0), "movie_requests": Usage.get("movie", 0)}}
