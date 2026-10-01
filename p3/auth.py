"""HTTP access boundaries — thin adapters over the account provider.

Customer: header X-Access-Token → Ctx.Accounts.Authenticate → Principal. Nothing
in Pipeline 3 reads token storage except the provider (p3/accounts/).

Developer: the P3_ADMIN_KEY bearer secret, checked server-side on every /api/dev
route. When P3_ADMIN_KEY is unset, developer routes are disabled entirely.
"""

import hmac

from p3.accounts import AuthError, Principal
from p3.context import Context, HttpError


def RequirePrincipal(Ctx: Context, Token: str | None) -> Principal:
    try:
        return Ctx.Accounts.Authenticate(Token)
    except AuthError as E:
        raise HttpError(401, E.Code, E.Message) from E


def RequireDeveloper(Ctx: Context, Authorization: str | None) -> None:
    Expected = Ctx.Settings.AdminKey
    if not Expected:
        raise HttpError(503, "developer_tools_disabled", "Developer tools are disabled (P3_ADMIN_KEY is not set).")
    Supplied = (Authorization or "").removeprefix("Bearer ").strip()
    if not Supplied or not hmac.compare_digest(Supplied.encode(), Expected.encode()):
        raise HttpError(403, "developer_auth_required", "Developer authorization required.")
