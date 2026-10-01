"""FastAPI application for Pipeline 3.

Run:  .venv/Scripts/python -m uvicorn p3.app:App --port 8310
With P3_BASE_PATH=/JewelryB2C3 every page, API, static file and asset is served
under that prefix (the deployment layout behind proto/tron); without it, at "/".
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, File, Form, Header, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from p3 import assets
from p3.accounts import BuildProvider as BuildAccountProvider, InsufficientCredits
from p3.auth import RequireDeveloper, RequirePrincipal
from p3.config import LoadCatalog, LoadGenerationConfig
from p3.context import Context, HttpError
from p3.customize import CustomizeService
from p3.db import Database
from p3.designs import DesignService
from p3.images import ImageService
from p3.meshes import MeshService
from p3.migrations import MigrateToAccounts
from p3.mail import BuildMailer
from p3.registration import RegistrationService
from p3.modes import DefaultFactories, ModeManager, ResolveStartupMode
from p3.movies import MovieService
from p3.pricing.service import PricingService
from p3.settings import LoadSettings, Settings, WebDir

Logger = logging.getLogger("p3.app")


class Services:
    def __init__(self, Ctx: Context):
        self.Ctx = Ctx
        self.Images = ImageService(Ctx)
        self.Movies = MovieService(Ctx)
        self.Customize = CustomizeService(Ctx, self.Images, self.Movies)
        self.Designs = DesignService(Ctx, self.Images, self.Customize)
        self.Meshes = MeshService(Ctx)

    def Reconcile(self) -> dict:
        return {"candidates": self.Images.Reconcile(), "movies": self.Movies.Reconcile(),
                "meshes": self.Meshes.Reconcile()}


def _VersionedPage(Name: str, BasePath: str) -> str:
    """Render a page: every "{{BASE}}" becomes the base path, and local scripts/styles get
    ?v=<mtime> so a browser can never pair a new page with a cached older app.js."""
    Html = (WebDir / Name).read_text(encoding="utf-8")
    for Asset in ("app.js", "styles.css"):
        Path_ = WebDir / Asset
        if Path_.is_file():
            Html = Html.replace(f'"{{{{BASE}}}}/static/{Asset}"', f'"{{{{BASE}}}}/static/{Asset}?v={Path_.stat().st_mtime_ns}"')
    return Html.replace("{{BASE}}", BasePath)


def CreateApp(SettingsObj: Settings | None = None, ProviderObj=None, ProviderFactories: dict | None = None) -> FastAPI:
    S = SettingsObj or LoadSettings()
    Base = S.BasePath
    S.AssetsDir.mkdir(parents=True, exist_ok=True)
    S.DevDir.mkdir(parents=True, exist_ok=True)
    # Asset paths add ~130 characters (designs/<id>/candidates/<id>.png); Windows fails past 260.
    if os.name == "nt" and len(str(S.AssetsDir)) > 120:
        Logger.warning("P3_DATA_DIR is %d characters long; generated asset paths may exceed the Windows "
                       "260-character limit and fail to save. Use a shorter data directory.", len(str(S.AssetsDir)))
    Catalog = LoadCatalog()
    # Identity is a separate domain (own provider + own storage); app data keys rows by account id only.
    Accounts = BuildAccountProvider(S.AccountProvider, S.DataDir)
    Migrated = MigrateToAccounts(S.DbPath, Accounts)    # one-time, pre-accounts databases only
    Factories = ProviderFactories or DefaultFactories(S)
    if ProviderObj is not None:                       # tests inject a provider instance
        Mode, ModeSource = ("live" if ProviderObj.Name == "fal" else "mock"), "injected"
    else:
        Mode, ModeSource = ResolveStartupMode(S)
    Ctx = Context(Settings=S, Db=Database(S.DbPath), Provider=ProviderObj or Factories[Mode](),
                  Gen=LoadGenerationConfig(), Catalog=Catalog,
                  Pricing=PricingService(Catalog, S.PricingProfilePath, S.AllowUnapprovedPricing),
                  Accounts=Accounts)
    Svc = Services(Ctx)
    Modes = ModeManager(Ctx, Mode, ModeSource, Factories)
    Mailer = BuildMailer(S.DataDir)
    Registration = RegistrationService(Ctx, Mailer)

    @asynccontextmanager
    async def Lifespan(_App):
        Summary = Svc.Reconcile()
        Logger.info("Startup reconciliation: %s (provider=%s)", Summary, Ctx.Provider.Name)
        Logger.warning("AI mode: %s (%s); base path: %s", Modes.Mode.upper(), Modes.Source, Base or "/")
        _App.state.Reconciliation = Summary
        if Migrated:
            Logger.warning("Account migration on startup: %s", Migrated)
        yield
        await Ctx.Runner.Shutdown()

    # With a base path the routes live on an inner app mounted at Base; Starlette does not run a
    # mounted app's lifespan, so the outer app owns it.
    App_ = FastAPI(title="XJet Jewelry Builder — Pipeline 3", lifespan=None if Base else Lifespan,
                   docs_url="/docs", openapi_url="/openapi.json")
    App_.state.Ctx = Ctx
    App_.state.Services = Svc
    App_.state.Modes = Modes
    App_.state.Mailer = Mailer

    @App_.middleware("http")
    async def _NoStaleUi(Req: Request, CallNext):
        # The page and its scripts change with every release; make browsers revalidate
        # (cheap 304s via ETag) instead of running a cached, outdated app.js.
        Resp = await CallNext(Req)
        Rel = Req.url.path[len(Base):] if Base and Req.url.path.startswith(Base) else Req.url.path
        if Rel in ("/", "/dev") or Rel.startswith("/static/"):
            Resp.headers["Cache-Control"] = "no-cache"
        return Resp

    @App_.exception_handler(HttpError)
    async def _HttpError(_Req: Request, E: HttpError):
        return JSONResponse(status_code=E.Status, content={"error": {"code": E.Code, "message": E.Message}})

    @App_.exception_handler(InsufficientCredits)
    async def _NoCredits(_Req: Request, E: InsufficientCredits):
        return JSONResponse(status_code=402, content={"error": {"code": "quota_exhausted", "message": E.Message}})

    def Tok(XAccessToken: str | None):
        """Resolve the caller to a Principal (the only way routes learn who is calling)."""
        return RequirePrincipal(Ctx, XAccessToken)

    # ── pages / static ───────────────────────────────────────────────────
    @App_.get("/", include_in_schema=False)
    async def Index():
        return HTMLResponse(_VersionedPage("index.html", Base))

    @App_.get("/dev", include_in_schema=False)
    async def DevPage():
        return HTMLResponse(_VersionedPage("dev.html", Base))

    App_.mount("/static", StaticFiles(directory=WebDir), name="static")
    App_.mount("/assets", StaticFiles(directory=S.AssetsDir), name="assets")

    # ── session / catalog / quote ────────────────────────────────────────
    @App_.get("/api/health")
    async def Health():
        return {"ok": True, "provider": Ctx.Provider.Name,
                "mode": Modes.Mode, "mode_source": Modes.Source, "base_path": Base or "/",
                "pricing_profile": Ctx.Pricing.ProfileVersion,
                "pricing_profile_approved": bool(Ctx.Pricing.Profile and Ctx.Pricing.Profile["approved"]),
                "unapproved_pricing_allowed": S.AllowUnapprovedPricing,
                "config_versions": {"images": Ctx.Gen.Images.Version, "movie": Ctx.Gen.Movie.Version,
                                    "mesh": Ctx.Gen.Mesh.Version}}

    @App_.get("/api/session")
    async def Session(x_access_token: str | None = Header(None)):
        return Ctx.Accounts.Profile(Tok(x_access_token))

    # ── sign-in / registration (Pipeline 2 JewelryB2C2 flow) ─────────────
    @App_.post("/api/register")
    async def Register(Req: Request, Body_: dict = Body(...)):
        return Registration.Register(Req, Body_.get("Name", ""), Body_.get("Email", ""))

    @App_.post("/api/register-token")
    async def RegisterToken(Body_: dict = Body(...)):
        return Registration.SignInWithToken(Body_.get("Token", ""))

    @App_.get("/api/token-status")
    async def TokenStatus(x_access_token: str | None = Header(None)):
        return Ctx.Accounts.Profile(Tok(x_access_token))

    @App_.get("/verify", include_in_schema=False)
    async def VerifyEmail(Req: Request, token: str = ""):
        return HTMLResponse(Registration.VerifyPage(Req, token))

    @App_.get("/api/catalog")
    async def CatalogRoute():
        return Ctx.Catalog.ToJson()

    @App_.get("/api/quote")
    async def QuoteRoute(material_id: str, ring_size: float | None = None):
        # ring_size is accepted for clarity but deliberately ignored: size never changes price.
        try:
            Q = Ctx.Pricing.QuoteFor(material_id)
        except KeyError:
            raise HttpError(404, "unknown_material", "Unknown material.")
        return {**Q.ToJson(), "ring_size": ring_size}

    # ── designs & batches ────────────────────────────────────────────────
    @App_.post("/api/designs")
    async def CreateDesign(prompt: str = Form(...), client_request_id: str | None = Form(None),
                           rights_confirmed: bool = Form(False), reference: UploadFile | None = File(None),
                           x_access_token: str | None = Header(None)):
        Who = Tok(x_access_token)
        ReferencePng = None
        if reference is not None and reference.filename:
            if not rights_confirmed:
                raise HttpError(400, "rights_not_confirmed",
                                "Please confirm you have the rights to use the uploaded image.")
            Raw = await reference.read(assets.MaxReferenceBytes + 1)
            try:
                ReferencePng = assets.NormalizeReferenceImage(Raw)
            except assets.AssetError as E:
                raise HttpError(400, "invalid_reference", str(E))
        return Svc.Images.CreateInitial(Who, prompt, ReferencePng, client_request_id)

    @App_.get("/api/designs")
    async def ListDesigns(x_access_token: str | None = Header(None)):
        return {"designs": Svc.Designs.List(Tok(x_access_token))}

    @App_.get("/api/designs/{DesignId}")
    async def GetDesign(DesignId: str, x_access_token: str | None = Header(None)):
        return Svc.Designs.Get(Tok(x_access_token), DesignId)

    @App_.post("/api/designs/{DesignId}/batches")
    async def Refine(DesignId: str, Body_: dict = Body(...), x_access_token: str | None = Header(None)):
        return Svc.Images.CreateRefinement(Tok(x_access_token), DesignId, Body_.get("parent_candidate_id"),
                                           Body_.get("instruction", ""), Body_.get("client_request_id"))

    @App_.get("/api/batches/{BatchId}")
    async def GetBatch(BatchId: str, x_access_token: str | None = Header(None)):
        B = Svc.Images._OwnedBatch(Tok(x_access_token), BatchId)
        return Svc.Images.GetBatch(B["id"])

    @App_.post("/api/batches/{BatchId}/retry-failed")
    async def RetryFailed(BatchId: str, x_access_token: str | None = Header(None)):
        return Svc.Images.RetryFailed(Tok(x_access_token), BatchId)

    @App_.post("/api/candidates/{CandidateId}/retry")
    async def RetryCandidate(CandidateId: str, x_access_token: str | None = Header(None)):
        return Svc.Images.RetryCandidate(Tok(x_access_token), CandidateId)

    @App_.put("/api/designs/{DesignId}/selection")
    async def Select(DesignId: str, Body_: dict = Body(...), x_access_token: str | None = Header(None)):
        return Svc.Customize.Select(Tok(x_access_token), DesignId, Body_.get("candidate_id"))

    # ── customize & movie ────────────────────────────────────────────────
    @App_.post("/api/designs/{DesignId}/customize")
    async def Proceed(DesignId: str, Body_: dict = Body(...), x_access_token: str | None = Header(None)):
        return Svc.Customize.Proceed(Tok(x_access_token), DesignId, Body_.get("candidate_id"))

    @App_.get("/api/customizations/{CustomizationId}")
    async def GetCustomization(CustomizationId: str, x_access_token: str | None = Header(None)):
        return Svc.Customize.Get(Tok(x_access_token), CustomizationId)

    @App_.patch("/api/customizations/{CustomizationId}")
    async def UpdateCustomization(CustomizationId: str, Body_: dict = Body(...),
                            x_access_token: str | None = Header(None)):
        return Svc.Customize.Update(Tok(x_access_token), CustomizationId, Body_)

    @App_.post("/api/candidates/{CandidateId}/movie")
    async def EnsureMovie(CandidateId: str, x_access_token: str | None = Header(None)):
        Who = Tok(x_access_token)
        Cand, _Batch = Svc.Images._OwnedCandidate(Who, CandidateId)
        if Cand["status"] != "ready":
            raise HttpError(409, "candidate_not_ready", "That image is not ready yet.")
        return Svc.Movies.Ensure(Who, CandidateId)

    # ── bag ──────────────────────────────────────────────────────────────
    @App_.get("/api/bag")
    async def GetBag(x_access_token: str | None = Header(None)):
        return Svc.Customize.Bag(Tok(x_access_token))

    @App_.post("/api/bag")
    async def AddToBag(Body_: dict = Body(...), x_access_token: str | None = Header(None)):
        return Svc.Customize.AddToBag(Tok(x_access_token), Body_.get("customization_id"))

    @App_.delete("/api/bag/{LineId}")
    async def RemoveFromBag(LineId: str, x_access_token: str | None = Header(None)):
        return Svc.Customize.RemoveFromBag(Tok(x_access_token), LineId)

    # ── developer-only mesh tools ────────────────────────────────────────
    @App_.get("/api/dev/status")
    async def DevStatus(authorization: str | None = Header(None)):
        RequireDeveloper(Ctx, authorization)
        return {"ok": True, "mesh_defaults": Ctx.Gen.Mesh.Params, "mesh_config_version": Ctx.Gen.Mesh.Version}

    @App_.get("/api/dev/mode")
    async def DevMode(authorization: str | None = Header(None)):
        RequireDeveloper(Ctx, authorization)
        return Modes.Status()

    @App_.post("/api/dev/mode")
    async def DevSwitchMode(Body_: dict = Body(...), authorization: str | None = Header(None)):
        RequireDeveloper(Ctx, authorization)
        return Modes.Switch(Body_.get("mode"), Body_.get("confirmation"))

    @App_.get("/api/dev/outbox")
    async def DevOutbox(authorization: str | None = Header(None)):
        RequireDeveloper(Ctx, authorization)
        Items = Mailer.List() if hasattr(Mailer, "List") else []
        return {"mode": Mailer.Mode, "messages": [{K: M[K] for K in ("id", "to", "subject", "sent_at")} for M in Items]}

    @App_.get("/api/dev/outbox/{MessageId}")
    async def DevOutboxMessage(MessageId: str, authorization: str | None = Header(None)):
        RequireDeveloper(Ctx, authorization)
        M = Mailer.Get(MessageId) if hasattr(Mailer, "Get") else None
        if M is None:
            raise HttpError(404, "message_not_found", "Message not found.")
        return M

    @App_.get("/api/dev/candidates")
    async def DevCandidates(authorization: str | None = Header(None)):
        RequireDeveloper(Ctx, authorization)
        Rows = Ctx.Db.All("SELECT c.id, c.slot, c.asset_path, b.id AS batch_id, b.kind, d.id AS design_id, d.title, "
                          "c.updated_at FROM candidates c JOIN batches b ON b.id = c.batch_id "
                          "JOIN designs d ON d.id = b.design_id WHERE c.status = 'ready' "
                          "ORDER BY c.updated_at DESC LIMIT 60")
        return {"candidates": [{**{K: R[K] for K in ("id", "slot", "batch_id", "kind", "design_id", "title",
                                                     "updated_at")},
                                "image_url": Ctx.AssetUrl(R["asset_path"])} for R in Rows]}

    @App_.post("/api/dev/candidates/{CandidateId}/meshes")
    async def DevCreateMesh(CandidateId: str, Body_: dict = Body(default={}), authorization: str | None = Header(None)):
        RequireDeveloper(Ctx, authorization)
        return Svc.Meshes.Create(CandidateId, Body_.get("settings"))

    @App_.get("/api/dev/meshes")
    async def DevListMeshes(candidate_id: str | None = None, authorization: str | None = Header(None)):
        RequireDeveloper(Ctx, authorization)
        return {"meshes": Svc.Meshes.List(candidate_id)}

    @App_.get("/api/dev/meshes/{MeshId}")
    async def DevGetMesh(MeshId: str, authorization: str | None = Header(None)):
        RequireDeveloper(Ctx, authorization)
        return Svc.Meshes.Get(MeshId)

    @App_.post("/api/dev/meshes/{MeshId}/convert-stl")
    async def DevConvert(MeshId: str, authorization: str | None = Header(None)):
        RequireDeveloper(Ctx, authorization)
        return Svc.Meshes.ConvertToStl(MeshId)

    @App_.get("/api/dev/meshes/{MeshId}/download")
    async def DevDownload(MeshId: str, kind: str = "stl", authorization: str | None = Header(None)):
        RequireDeveloper(Ctx, authorization)
        if kind not in ("stl", "original"):
            raise HttpError(400, "invalid_kind", "kind must be 'stl' or 'original'.")
        FsPath, Name = Svc.Meshes.FilePath(MeshId, kind)
        return FileResponse(FsPath, filename=f"{MeshId}_{Name}", media_type="application/octet-stream")

    if not Base:
        return App_

    Outer = FastAPI(title="XJet Jewelry Builder — Pipeline 3", lifespan=Lifespan, docs_url=None,
                    redoc_url=None, openapi_url=None)
    Outer.state.Ctx = Ctx
    Outer.state.Services = Svc
    Outer.state.Modes = Modes
    Outer.state.Mailer = Mailer

    @Outer.get(Base, include_in_schema=False)
    async def _BaseSlash():
        return RedirectResponse(Base + "/", status_code=308)

    @Outer.get("/", include_in_schema=False)
    async def _RootToBase():
        # Local convenience only: behind proto/tron, "/" never reaches Pipeline 3.
        return RedirectResponse(Base + "/", status_code=307)

    Outer.mount(Base, App_)
    return Outer


def __getattr__(Name):
    # Lazy module-level `App` for uvicorn (`p3.app:App`) so importing this module in tests has no side effects.
    if Name == "App":
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        globals()["App"] = CreateApp()
        return globals()["App"]
    raise AttributeError(Name)
