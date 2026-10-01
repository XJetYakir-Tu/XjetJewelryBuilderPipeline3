"""P3 Admin — user / token management, kept apart from the customer site.

  Page:  {base}/admin/            (web/admin.html — never linked from the customer site, noindex)
  API:   {base}/api/admin/...     (every route calls RequireAdmin)

Authorization is a single seam, RequireAdmin(), which returns an AdminPrincipal. Today it accepts
the existing developer key (P3_ADMIN_KEY, as /dev does). When the public site goes live, replace
its body with a proper admin role / company sign-in (e.g. SSO) — the routes do not change.
"""

from dataclasses import dataclass

from fastapi import Body, FastAPI, Header
from fastapi.responses import HTMLResponse

from p3.accounts import AccountNotFound, DuplicateEmail
from p3.auth import RequireDeveloper
from p3.context import Context, HttpError
from p3.usage import AccountActivity


@dataclass(frozen=True)
class AdminPrincipal:
    Id: str            # who is acting (today always "developer-key")
    Method: str        # how they authenticated


def RequireAdmin(Ctx: Context, Authorization: str | None) -> AdminPrincipal:
    """The only admin authorization check. Swap this for an admin role / company auth later."""
    RequireDeveloper(Ctx, Authorization)
    return AdminPrincipal(Id="developer-key", Method="P3_ADMIN_KEY")


def _Errors(Fn):
    """Map account-domain errors to HTTP errors."""
    try:
        return Fn()
    except DuplicateEmail as E:
        raise HttpError(409, "duplicate_email", f"This email already has an account ({E.AccountId}).") from E
    except AccountNotFound as E:
        raise HttpError(404, "account_not_found", "User not found.") from E
    except ValueError as E:
        raise HttpError(400, "invalid_input", str(E)) from E


def _Detail(Ctx: Context, AccountId: str) -> dict:
    User = _Errors(lambda: Ctx.Accounts.AdminGet(AccountId))
    Identity = Ctx.Accounts.AdminActivity(AccountId)
    App = AccountActivity(Ctx, AccountId)
    Usage = Identity["usage"]
    # Usage ledger by kind and provider/endpoint — the shape a cost report will aggregate.
    Ledger: dict[tuple, dict] = {}
    for U in Usage:
        if U["kind"] == "generation":
            continue
        Key = (U["kind"], U["provider"] or "unknown", U["endpoint"] or "unknown")
        Row = Ledger.setdefault(Key, {"kind": Key[0], "provider": Key[1], "endpoint": Key[2], "requests": 0,
                                      "units": 0, "cost_usd": None})
        Row["requests"] += 1
        Row["units"] += U["units"]
        if U["cost_usd"] is not None:
            Row["cost_usd"] = (Row["cost_usd"] or 0) + U["cost_usd"]
    Timeline = App["timeline"] + [
        {"at": E["created_at"], "kind": E["kind"], "text": E["detail"]} for E in Identity["events"]]
    Timeline.sort(key=lambda E: E["at"] or "", reverse=True)
    Daily = {D["day"]: D for D in App["daily"]}
    Since = App["daily_since"]
    for E in Identity["events"]:
        Day = E["created_at"][:10]
        if E["kind"] == "sign_in" and Day >= Since:
            Row = Daily.setdefault(Day, {"day": Day})
            Row["sign_ins"] = Row.get("sign_ins", 0) + 1
    return {
        "user": User,
        "totals": App["totals"] | {
            "generations_used": User["used"], "generations_max": User["max"],
            "sign_ins": sum(1 for E in Identity["events"] if E["kind"] == "sign_in"),
        },
        "usage_ledger": sorted(Ledger.values(), key=lambda R: (R["kind"], R["provider"], R["endpoint"])),
        "cost_reporting": "not_configured",       # cost_usd stays null until a provider price table exists
        "designs": App["designs"],
        "timeline": Timeline[:300],
        "daily": [Daily[K] for K in sorted(Daily)],
        "last_design_activity_at": App["last_design_activity_at"],
    }


def RegisterAdmin(App_: FastAPI, Ctx: Context, Page) -> None:
    """Add the admin page and API to the (inner) app. `Page(name)` renders a web/ page."""

    def Admin(Authorization: str | None) -> AdminPrincipal:
        return RequireAdmin(Ctx, Authorization)

    @App_.get("/admin", include_in_schema=False)
    @App_.get("/admin/", include_in_schema=False)
    async def AdminPage():
        return HTMLResponse(Page("admin.html"), headers={"X-Robots-Tag": "noindex, nofollow"})

    @App_.get("/api/admin/session")
    async def AdminSession(authorization: str | None = Header(None)):
        Who = Admin(authorization)
        return {"ok": True, "admin": Who.Id, "method": Who.Method, "mode": Ctx.Provider.Name}

    @App_.get("/api/admin/users")
    async def ListUsers(include_removed: bool = False, authorization: str | None = Header(None)):
        Admin(authorization)
        return {"users": Ctx.Accounts.AdminList(IncludeRemoved=include_removed)}

    @App_.post("/api/admin/users")
    async def CreateUser(Body_: dict = Body(...), authorization: str | None = Header(None)):
        Admin(authorization)
        return _Errors(lambda: Ctx.Accounts.AdminCreate(Body_.get("Name", ""), Body_.get("Email", ""),
                                                        Body_.get("MaxGenerations", 10)))

    @App_.get("/api/admin/users/{AccountId}")
    async def GetUser(AccountId: str, authorization: str | None = Header(None)):
        Admin(authorization)
        return _Detail(Ctx, AccountId)

    @App_.patch("/api/admin/users/{AccountId}")
    async def EditUser(AccountId: str, Body_: dict = Body(...), authorization: str | None = Header(None)):
        Admin(authorization)
        return _Errors(lambda: Ctx.Accounts.AdminUpdate(AccountId, Body_.get("Name", ""), Body_.get("Email", ""),
                                                        Body_.get("MaxGenerations"), bool(Body_.get("ResetUsage"))))

    @App_.post("/api/admin/users/{AccountId}/{Action}")
    async def UserAction(AccountId: str, Action: str, authorization: str | None = Header(None)):
        Admin(authorization)
        Actions = {
            "deactivate": lambda: Ctx.Accounts.AdminSetActive(AccountId, False),
            "activate": lambda: Ctx.Accounts.AdminSetActive(AccountId, True),
            "remove": lambda: Ctx.Accounts.AdminRemove(AccountId),
            "restore": lambda: Ctx.Accounts.AdminRestore(AccountId),
        }
        if Action not in Actions:
            raise HttpError(404, "unknown_action", "Unknown action.")
        return _Errors(Actions[Action])
