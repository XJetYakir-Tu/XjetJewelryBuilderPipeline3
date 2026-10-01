"""P3 Admin — user / token management, kept apart from the customer site.

  Page:  {base}/admin/            (web/admin.html — never linked from the customer site, noindex)
  API:   {base}/api/admin/...     (every route calls RequireAdmin)

Authorization is a single seam, RequireAdmin(), which returns an AdminPrincipal. Today it accepts
the existing developer key (P3_ADMIN_KEY, as /dev does). When the public site goes live, replace
its body with a proper admin role / company sign-in (e.g. SSO) — the routes do not change.
"""

from dataclasses import dataclass

import asyncio
from statistics import mean

from fastapi import Body, FastAPI, Header
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse

from p3 import sessions as Sessions
from p3.accounts import AccountNotFound, DuplicateEmail
from p3.auth import RequireDeveloper
from p3.context import Context, HttpError
from p3.aipricing import PriceError
from p3.usage import AccountActivity
from p3.modelconfig import ConfigError, ExportText, Models as ModelSpecs, RuntimeInputs, Validate as ValidateConfig


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
        "sessions": Sessions.Summaries(Ctx, OwnerAccountId=AccountId),
    }


def _Pct(Part: int, Whole: int) -> float | None:
    return round(100.0 * Part / Whole, 1) if Whole else None


def Dashboard(Ctx: Context) -> dict:
    """Small, factual overview. Geometry/price statistics appear once 3D data exists."""
    S = Sessions.Summaries(Ctx)
    N = len(S)

    def Reached(Key):
        return sum(1 for X in S if X["stage_times"].get(Key))

    Clicks = Ctx.Db.One("SELECT COUNT(*) AS n FROM session_events WHERE kind = 'new_design_clicked'")["n"]
    Geo = Ctx.Db.All("SELECT g.volume_mm3, p.material_id, p.weight_g, p.fixed_price, p.calculated_price, "
                     "p.production_cost FROM geometry_results g JOIN price_calculations p ON p.geometry_id = g.id "
                     "WHERE g.stage = 'production'")
    Selected3D = len({R["design_id"] for R in Ctx.Db.All("SELECT DISTINCT design_id FROM session_3d")})
    ByMat: dict[str, list] = {}
    for G in Geo:
        if G["weight_g"] is not None:
            ByMat.setdefault(G["material_id"], []).append(G["weight_g"])
    Pairs = [(G["fixed_price"], G["calculated_price"]) for G in Geo if G["fixed_price"] and G["calculated_price"]]
    Volumes = [G["volume_mm3"] for G in Geo if G["volume_mm3"]]
    return {
        "sessions": N, "active_sessions": sum(1 for X in S if X["state"] == "active"),
        "new_design_clicks": Clicks,
        "funnel": [{"stage": K, "label": Sessions.StageLabels[K], "sessions": Reached(K), "pct": _Pct(Reached(K), N)}
                   for K in Sessions.StageOrder],
        "avg_refinements": round(mean([X["refinements"] for X in S]), 2) if S else None,
        "sessions_with_refinement_pct": _Pct(sum(1 for X in S if X["refinements"]), N),
        "generation_failed": sum(1 for X in S if "Generation failed" in X["path"]),
        "selected_for_3d": Selected3D, "selected_for_3d_pct": _Pct(Selected3D, N),
        "geometry": {
            "measured": len(Geo),
            "avg_volume_mm3": round(mean(Volumes), 1) if Volumes else None,
            "avg_weight_g_by_material": {K: round(mean(V), 2) for K, V in ByMat.items()},
            "fixed_vs_3d_price_variance_pct": round(mean([100.0 * (C - F) / F for F, C in Pairs]), 1) if Pairs else None,
            "price_pairs": len(Pairs),
        },
        "cost_model": "configured" if any(G["production_cost"] is not None for G in Geo) else "not_configured",
    }


def SessionDetail(Ctx: Context, Production, DesignId: str, Prices) -> dict:
    Summary = (Sessions.Summaries(Ctx, [DesignId]) or [None])[0]
    if Summary is None:
        raise HttpError(404, "session_not_found", "Session not found.")
    Owner = Summary["account_id"]
    Design = next((G for G in AccountActivity(Ctx, Owner)["designs"] if G["id"] == DesignId), None)
    Batches = (Design or {}).get("batches", [])
    Jobs = {C["id"] for B in Batches for C in B["candidates"]}
    Jobs |= {M["id"] for B in Batches for C in B["candidates"] for M in C["movies"]}
    Jobs |= {R["id"] for R in Ctx.Db.All("SELECT m.id FROM meshes m JOIN candidates c ON c.id = m.candidate_id "
                                         "JOIN batches b ON b.id = c.batch_id WHERE b.design_id = ?", (DesignId,))}
    try:
        Usage = [U for U in Ctx.Accounts.AdminActivity(Owner)["usage"]
                 if U["ref_id"] in Jobs and U["kind"] != "generation"]
    except AccountNotFound:
        Usage = []
    Cost = [U["cost_usd"] for U in Usage if U["cost_usd"] is not None]
    Timeline = Sessions.Timeline(Ctx, DesignId)
    try:
        AllUsage = Ctx.Accounts.AdminActivity(Owner)["usage"]
        User = Ctx.Accounts.AdminGet(Owner)
    except AccountNotFound:
        AllUsage, User = [], None
    Flow = Sessions.Pipeline(Ctx, DesignId, AllUsage, Prices)
    UserTotal, UserSessions = 0.0, 0
    for D in Ctx.Db.All("SELECT id FROM designs WHERE owner_account_id = ?", (Owner,)):
        UserTotal += Sessions.Pipeline(Ctx, D["id"], AllUsage, Prices)["total_cost"]
        UserSessions += 1
    Movie = next((M for B in Batches for C in B["candidates"] if C["selected"] for M in C["movies"] if M["status"] == "ready"), None) \
        or next((M for B in Batches for C in B["candidates"] for M in C["movies"] if M["status"] == "ready"), None)
    Keep = ("material_id", "ring_size", "quantity", "unit_price", "pricing_version", "pricing_status")
    Choices = [{"at": E["at"], "kind": E["kind"], **{K: V for K, V in (E.get("data") or {}).items() if K in Keep}}
               for E in Timeline if E["kind"] in ("customize_opened", "customization_changed")]
    ThreeD = Production.ForDesign(DesignId)
    Last = None                                    # the customer's final selection (changes applied in order)
    for C in Choices:
        Last = {**(Last or {}), **{K: V for K, V in C.items() if V is not None}}
    return {
        "session": Summary,
        "user": User,
        "pipeline": Flow,
        "cost": {"session": Flow["total_cost"], "user_total": round(UserTotal, 4), "user_sessions": UserSessions,
                 "basis": "Estimated at list prices (Admin → AI Prompts & Params → AI prices); mock requests are $0.",
                 "price_list_version": Prices.Current()["version"]},
        "artifacts": {"image_url": Summary["thumbnail_url"], "movie_url": Movie["url"] if Movie else None},
        "last_choice": Last,
        "timeline": Timeline,
        "design": Design,
        "choices": Choices,
        "ai_usage": {"requests": len(Usage),
                     "by_kind": {K: sum(1 for U in Usage if U["kind"] == K) for K in sorted({U["kind"] for U in Usage})},
                     "providers": sorted({U["provider"] or "unknown" for U in Usage}),
                     "cost_usd": round(sum(Cost), 4) if Cost else None},
        "three_d": ThreeD,
        "three_d_defaults": {
            "customer_size": Summary["ring_size"] if Summary["ring_size_chosen"] else None,
            "production_size": Summary["ring_size"] if Summary["ring_size_chosen"] else 10.0,
            "material_id": (Summary["material_id"] if Summary["material_chosen"] else None) or Ctx.Catalog.DefaultMaterialId,
            "customer_material": Summary["material_id"] if Summary["material_chosen"] else None,
            "has_raw_mesh": any(T["hi3d"] and T["hi3d"]["status"] == "ready" for T in ThreeD),
        },
        "catalog": {"ring_sizes": list(Ctx.Catalog.RingSizes),
                    "materials": [{"id": M.Id, "label": M.Label, "density_g_cm3": M.DensityGCm3}
                                  for M in Ctx.Catalog.Materials.values()]},
    }


def RegisterAdmin(App_: FastAPI, Ctx: Context, Page, Production, Prices) -> None:
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

    # ── sessions / dashboard / 3D ─────────────────────────────────────────
    @App_.get("/api/admin/dashboard")
    async def AdminDashboard(authorization: str | None = Header(None)):
        Admin(authorization)
        return Dashboard(Ctx)

    @App_.get("/api/admin/sessions")
    async def ListSessions(authorization: str | None = Header(None)):
        Admin(authorization)
        return {"sessions": Sessions.Summaries(Ctx), "idle_minutes": Sessions.IdleMinutes}

    @App_.get("/api/admin/sessions/{DesignId}")
    async def GetSession(DesignId: str, authorization: str | None = Header(None)):
        Admin(authorization)
        return SessionDetail(Ctx, Production, DesignId, Prices)

    @App_.post("/api/admin/sessions/{DesignId}/3d")
    async def Generate3D(DesignId: str, Body_: dict = Body(default={}), authorization: str | None = Header(None)):
        Who = Admin(authorization)
        return Production.Request(DesignId, Body_.get("production_size"), Body_.get("material_id") or None,
                                  Body_.get("candidate_id") or None, RequestedBy=Who.Id)

    @App_.get("/api/admin/3d/{Sid}/stl/{Stage}")
    async def Download3D(Sid: str, Stage: str, authorization: str | None = Header(None)):
        Admin(authorization)
        if Stage not in ("raw", "production"):
            raise HttpError(404, "geometry_not_found", "Unknown stage.")
        Path_ = Production.StlPath(Sid, Stage)
        return FileResponse(Path_, filename=f"{Sid}_{Stage}{Path_.suffix}", media_type="model/stl")

    # ── AI prompts & parameters ───────────────────────────────────────────
    def _Model(ModelId: str):
        if ModelId not in ModelSpecs:
            raise HttpError(404, "unknown_model", "Unknown model.")
        return ModelId

    def _Invalid(E: ConfigError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": {
            "code": "invalid_configuration", "message": "The configuration is not valid.", "problems": E.Problems}})

    @App_.get("/api/admin/models")
    async def ListModels(authorization: str | None = Header(None)):
        Admin(authorization)
        return {"models": [{**Ctx.Models.State(M), "history": None} for M in ModelSpecs],
                "runtime_placeholders": RuntimeInputs}

    @App_.get("/api/admin/models/export")
    async def ExportModels(model: str = "all", format: str = "json", authorization: str | None = Header(None)):
        Admin(authorization)
        Ids = list(ModelSpecs) if model == "all" else [_Model(model)]
        Data = Ctx.Models.Export(Ids)                    # configurations only: no keys or credentials exist here
        Name = f"p3-ai-config-{'all' if model == 'all' else model}"
        if format == "txt":
            return PlainTextResponse(ExportText(Data), headers={"Content-Disposition": f'attachment; filename="{Name}.txt"'})
        return JSONResponse(Data, headers={"Content-Disposition": f'attachment; filename="{Name}.json"'})

    @App_.get("/api/admin/models/{ModelId}")
    async def GetModel(ModelId: str, authorization: str | None = Header(None)):
        Admin(authorization)
        return Ctx.Models.State(_Model(ModelId))

    @App_.post("/api/admin/models/{ModelId}/validate")
    async def ValidateModel(ModelId: str, Body_: dict = Body(...), authorization: str | None = Header(None)):
        Admin(authorization)
        try:
            return {"ok": True, "params": ValidateConfig(_Model(ModelId), Body_.get("params"))}
        except ConfigError as E:
            return {"ok": False, "problems": E.Problems}

    @App_.post("/api/admin/models/{ModelId}/preview")
    async def PreviewModel(ModelId: str, Body_: dict = Body(default={}), authorization: str | None = Header(None)):
        Admin(authorization)
        try:
            return Ctx.Models.Preview(_Model(ModelId), Body_.get("params"))   # never submits to the provider
        except ConfigError as E:
            return _Invalid(E)

    @App_.post("/api/admin/models/{ModelId}/activate")
    async def ActivateModel(ModelId: str, Body_: dict = Body(...), authorization: str | None = Header(None)):
        Who = Admin(authorization)
        try:
            return Ctx.Models.SaveAndActivate(_Model(ModelId), Body_.get("params"), Who.Id, str(Body_.get("note") or "")[:200])
        except ConfigError as E:
            return _Invalid(E)

    @App_.post("/api/admin/models/{ModelId}/restore")
    async def RestoreModel(ModelId: str, Body_: dict = Body(...), authorization: str | None = Header(None)):
        Who = Admin(authorization)
        try:
            return Ctx.Models.Restore(_Model(ModelId), str(Body_.get("version_id") or ""), Who.Id)
        except ConfigError as E:
            return _Invalid(E)

    # ── AI price list (cost estimates) ─────────────────────────────────────
    @App_.get("/api/admin/ai-prices")
    async def GetPrices(authorization: str | None = Header(None)):
        Admin(authorization)
        return {**Prices.Current(), "fal_key_configured": bool(Ctx.Settings.FalKey)}

    @App_.put("/api/admin/ai-prices")
    async def SavePrices(Body_: dict = Body(...), authorization: str | None = Header(None)):
        Who = Admin(authorization)
        try:
            return Prices.Save(Body_.get("prices") or {}, Who.Id, str(Body_.get("note") or "Edited in Admin")[:200])
        except PriceError as E:
            raise HttpError(400, "invalid_price_list", str(E)) from E

    @App_.post("/api/admin/ai-prices/refresh")
    async def RefreshPrices(authorization: str | None = Header(None)):
        Who = Admin(authorization)
        try:
            return await asyncio.to_thread(Prices.RefreshFromFal, Ctx.Settings.FalKey, Who.Id)   # read-only, free
        except PriceError as E:
            raise HttpError(400, "price_refresh_failed", str(E)) from E
