"""Four-candidate image batches: initial generation, refinement, slot retry.

Semantics (spec sections 3, 4.2, 4.3, 7.1):
  * one batch = four separately identified candidates from ONE effective prompt;
  * fal nano-banana-pro caps num_images at 4, so each slot is its own
    single-image request with a distinct seed (enables per-slot retry/identity);
  * refinement uses the SELECTED candidate's image as the reference for all four
    slots with one identical instruction — never text-only as a fallback;
  * exact-duplicate outputs within a batch are retried a bounded number of times;
  * async results only ever write their own candidate row — never the design's
    selection — so late results cannot overwrite a different design.
"""

import logging
import secrets

from p3 import assets
from p3.accounts import Principal, UsageImage
from p3.context import Context, HttpError
from p3.db import NewId, Now
from p3.naming import ProductName
from p3.providers import endpoints
from p3.modelconfig import BuildRequest, ByEndpoint, Render
from p3.runner import DownloadWithRetry, FailureFor, PollUntilDone

Logger = logging.getLogger("p3.images")

MinTextLength = 3
MaxTextLength = 2000


def ValidateText(Text: str, What: str) -> str:
    Text = (Text or "").strip()
    if len(Text) < MinTextLength:
        raise HttpError(400, "text_too_short", f"Please describe the {What} in a few more words.")
    if len(Text) > MaxTextLength:
        raise HttpError(400, "text_too_long", f"The {What} is too long (max {MaxTextLength} characters).")
    return Text


def _NewSeed() -> int:
    return secrets.randbelow(2**31 - 1) + 1


def BatchStatus(Candidates: list[dict], DesiredCount: int) -> str:
    States = [C["status"] for C in Candidates]
    if len(States) == DesiredCount and all(S == "ready" for S in States):
        return "complete"
    if any(S in ("pending", "generating") for S in States):
        return "generating" if any(S != "pending" for S in States) else "queued"
    if any(S == "ready" for S in States):
        return "partial"
    return "failed"


class ImageService:
    def __init__(self, Ctx: Context):
        self.Ctx = Ctx

    # ── creation ─────────────────────────────────────────────────────────
    def CreateInitial(self, Who: Principal, Prompt: str, ReferencePng: bytes | None,
                      ClientRequestId: str | None) -> dict:
        """Create a design and its first four-candidate batch. Idempotent per ClientRequestId."""
        Db = self.Ctx.Db
        Prompt = ValidateText(Prompt, "design")
        if ClientRequestId:
            Existing = Db.One("SELECT id FROM designs WHERE owner_account_id = ? AND client_request_id = ?",
                              (Who.AccountId, ClientRequestId))
            if Existing:
                return self._FirstBatch(Existing["id"])
        self.Ctx.Accounts.AuthorizeSpend(Who, UsageImage, self.Ctx.Gen.Images.CandidatesPerBatch)
        DesignId = NewId("dsg")
        BatchId = NewId("bat")
        RefPath = None
        if ReferencePng is not None:
            RefPath = f"designs/{DesignId}/references/{BatchId}.png"
            assets.WriteAtomic(self.Ctx.Settings.AssetsDir, RefPath, ReferencePng)
        with Db.Transaction() as Conn:
            T = Now()
            Conn.execute("INSERT INTO designs (id, owner_account_id, title, prompt, client_request_id, created_at, updated_at) "
                         "VALUES (?,?,?,?,?,?,?)",
                         (DesignId, Who.AccountId, ProductName(Prompt), Prompt, ClientRequestId, T, T))
            self._InsertBatch(Conn, BatchId, DesignId, "initial", None, Prompt, RefPath, None)
        self._StartBatch(BatchId)
        return self.GetBatch(BatchId)

    def CreateRefinement(self, Who: Principal, DesignId: str, ParentCandidateId: str, Instruction: str,
                         ClientRequestId: str | None) -> dict:
        Db = self.Ctx.Db
        Instruction = ValidateText(Instruction, "refinement")
        self.RequireDesign(Who, DesignId)
        if ClientRequestId:
            Existing = Db.One("SELECT id FROM batches WHERE design_id = ? AND client_request_id = ?",
                              (DesignId, ClientRequestId))
            if Existing:
                return self.GetBatch(Existing["id"])
        Parent = self.RequireReadyCandidate(DesignId, ParentCandidateId)
        # The exact selected image must be available; no silent text-only fallback.
        try:
            SourcePath = assets.Resolve(self.Ctx.Settings.AssetsDir, Parent["asset_path"])
        except assets.AssetError:
            SourcePath = None
        if SourcePath is None or not SourcePath.is_file():
            raise HttpError(409, "reference_unavailable",
                            "The selected image could not be loaded for refinement. "
                            "Please pick another image or try again.")
        self.Ctx.Accounts.AuthorizeSpend(Who, UsageImage, self.Ctx.Gen.Images.CandidatesPerBatch)
        BatchId = NewId("bat")
        with Db.Transaction() as Conn:
            self._InsertBatch(Conn, BatchId, DesignId, "refine", ParentCandidateId, Instruction,
                              Parent["asset_path"], ClientRequestId)
            Conn.execute("UPDATE designs SET updated_at = ? WHERE id = ?", (Now(), DesignId))
        self._StartBatch(BatchId)
        return self.GetBatch(BatchId)

    def _InsertBatch(self, Conn, BatchId, DesignId, Kind, ParentId, UserText, RefPath, ClientRequestId):
        Img = self.Ctx.Gen.Images
        Endpoint = endpoints.ImageEdit if RefPath else endpoints.ImageGenerate
        # The active admin configuration for this endpoint; its version is recorded on the batch so
        # all four requests use it even if a newer version is activated before they are submitted.
        Version = self.Ctx.Models.ActiveFor(Endpoint)
        DesignPrompt = Conn.execute("SELECT prompt FROM designs WHERE id = ?", (DesignId,)).fetchone()[0]
        Effective = Render(Version.Params["prompt"], {"user_text": UserText, "design_prompt": DesignPrompt})
        T = Now()
        Conn.execute(
            "INSERT INTO batches (id, design_id, kind, parent_candidate_id, user_text, effective_prompt, "
            "endpoint, reference_asset, desired_count, config_version, client_request_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (BatchId, DesignId, Kind, ParentId, UserText, Effective, Endpoint, RefPath,
             Img.CandidatesPerBatch, Version.Id, ClientRequestId, T))
        Seeds = set()
        while len(Seeds) < Img.CandidatesPerBatch:
            Seeds.add(_NewSeed())
        for Slot, Seed in enumerate(sorted(Seeds)):
            Conn.execute("INSERT INTO candidates (id, batch_id, slot, status, seed, created_at, updated_at) "
                         "VALUES (?,?,?,?,?,?,?)", (NewId("cand"), BatchId, Slot, "pending", Seed, T, T))

    def _StartBatch(self, BatchId: str) -> None:
        for C in self.Ctx.Db.All("SELECT id FROM candidates WHERE batch_id = ? AND status = 'pending'", (BatchId,)):
            self.Ctx.Runner.Spawn(f"cand:{C['id']}", self._Drive(C["id"]))

    # ── retry ────────────────────────────────────────────────────────────
    def RetryCandidate(self, Who: Principal, CandidateId: str) -> dict:
        Cand, Batch = self._OwnedCandidate(Who, CandidateId)
        if Cand["status"] != "failed":
            raise HttpError(409, "not_failed", "Only a failed image can be retried.")
        self.Ctx.Accounts.AuthorizeSpend(Who, UsageImage, 1)
        self._ResetForRetry(CandidateId)
        self.Ctx.Runner.Spawn(f"cand:{CandidateId}", self._Drive(CandidateId))
        return self.GetBatch(Batch["id"])

    def RetryFailed(self, Who: Principal, BatchId: str) -> dict:
        Batch = self._OwnedBatch(Who, BatchId)
        Failed = self.Ctx.Db.All("SELECT id FROM candidates WHERE batch_id = ? AND status = 'failed'", (BatchId,))
        if Failed:
            self.Ctx.Accounts.AuthorizeSpend(Who, UsageImage, len(Failed))
        for C in Failed:
            self._ResetForRetry(C["id"])
            self.Ctx.Runner.Spawn(f"cand:{C['id']}", self._Drive(C["id"]))
        return self.GetBatch(Batch["id"])

    def _ResetForRetry(self, CandidateId: str) -> None:
        self.Ctx.Db.Update("candidates", CandidateId, status="pending", provider_request_id=None,
                           error=None, error_code=None, duplicate_retries=0, seed=_NewSeed())

    # ── background driver ────────────────────────────────────────────────
    async def _Drive(self, CandidateId: str) -> None:
        Ctx = self.Ctx
        Db = Ctx.Db
        S = Ctx.Settings
        async with Ctx.Semaphore():
            while True:
                Cand = Db.One("SELECT * FROM candidates WHERE id = ?", (CandidateId,))
                if Cand is None or Cand["status"] in ("ready", "failed"):
                    return
                Batch = Db.One("SELECT * FROM batches WHERE id = ?", (Cand["batch_id"],))
                try:
                    RequestId = Cand["provider_request_id"]
                    if not RequestId:
                        Arguments = await self._Arguments(Batch, Cand)
                        RequestId = await Ctx.Provider.Submit(Batch["endpoint"], Arguments)
                        Db.Update("candidates", CandidateId, status="generating",
                                  provider_request_id=RequestId, attempts=Cand["attempts"] + 1)
                        Owner = Db.One("SELECT d.owner_account_id FROM batches b JOIN designs d ON d.id = b.design_id "
                                       "WHERE b.id = ?", (Batch["id"],))
                        Ctx.Accounts.RecordUsage(Owner["owner_account_id"], UsageImage, 1, CandidateId,
                                                 Provider=Ctx.Provider.Name, Endpoint=Batch["endpoint"])
                    Result = await PollUntilDone(Ctx.Provider, Batch["endpoint"], RequestId,
                                                 Ctx.Gen.Images.RequestTimeoutS, S.PollIntervalS,
                                                 S.MaxTransientPollErrors)
                    Images = Result.get("images") or []
                    if not Images or not Images[0].get("url"):
                        raise assets.AssetError("Provider returned no image")
                    Data = await DownloadWithRetry(Ctx.Provider, Images[0]["url"],
                                                   S.MaxTransientPollErrors, S.PollIntervalS)
                    Ext = assets.ValidateImage(Data)
                    if await self._StoreUnlessDuplicate(Batch, Cand, Data, Ext):
                        return
                    # Duplicate that was reset for another attempt: loop and resubmit.
                except Exception as E:
                    Message, Code = FailureFor(E)
                    Logger.warning("Candidate %s failed (%s): %s", CandidateId, Code, E)
                    Db.Update("candidates", CandidateId, status="failed", error=Message, error_code=Code)
                    return

    async def _Arguments(self, Batch: dict, Cand: dict) -> dict:
        """Provider arguments from the configuration version recorded on the batch (the prompt was
        rendered from its template when the batch was created)."""
        Version = self.Ctx.Models.Resolve(Batch["config_version"], Batch["endpoint"])
        Runtime = {"seed": Cand["seed"]}
        if Batch["reference_asset"]:
            Runtime["image_url"] = await self._ReferenceUrl(Batch)
        Params = {K: V for K, V in Version.Params.items() if K != "prompt"}
        Args = BuildRequest(ByEndpoint[Batch["endpoint"]], Params, Runtime)
        Args["prompt"] = Batch["effective_prompt"]
        return Args

    async def _ReferenceUrl(self, Batch: dict) -> str:
        """Upload the batch reference once; all slots share the same uploaded image."""
        async with self.Ctx.Lock(f"ref:{Batch['id']}"):
            Row = self.Ctx.Db.One("SELECT reference_upload_url FROM batches WHERE id = ?", (Batch["id"],))
            if Row and Row["reference_upload_url"]:
                return Row["reference_upload_url"]
            try:
                Path_ = assets.Resolve(self.Ctx.Settings.AssetsDir, Batch["reference_asset"])
                Data = Path_.read_bytes()
            except (OSError, assets.AssetError) as E:
                from p3.providers.base import ProviderError
                raise ProviderError("The reference image could not be loaded.", "reference_unavailable") from E
            Url = await self.Ctx.Provider.Upload(Data, assets.ImageContentType(Batch["reference_asset"]))
            self.Ctx.Db.Execute("UPDATE batches SET reference_upload_url = ? WHERE id = ?", (Url, Batch["id"]))
            return Url

    async def _StoreUnlessDuplicate(self, Batch: dict, Cand: dict, Data: bytes, Ext: str) -> bool:
        """Store a ready image. Returns False when the output was a duplicate and was re-queued."""
        Db = self.Ctx.Db
        Digest = assets.Sha256(Data)
        async with self.Ctx.Lock(f"batch:{Batch['id']}"):
            Dup = Db.One("SELECT id FROM candidates WHERE batch_id = ? AND id != ? AND status = 'ready' "
                         "AND content_sha256 = ?", (Batch["id"], Cand["id"], Digest))
            if Dup:
                if Cand["duplicate_retries"] < self.Ctx.Gen.Images.MaxDuplicateRetriesPerSlot:
                    Db.Update("candidates", Cand["id"], status="pending", provider_request_id=None,
                              duplicate_retries=Cand["duplicate_retries"] + 1, seed=_NewSeed())
                    return False
                Db.Update("candidates", Cand["id"], status="failed", error_code="duplicate_output",
                          error="This image came back identical to another option. You can retry it.")
                return True
            RelPath = f"designs/{Batch['design_id']}/candidates/{Cand['id']}.{Ext}"
            assets.WriteAtomic(self.Ctx.Settings.AssetsDir, RelPath, Data)
            Db.Update("candidates", Cand["id"], status="ready", asset_path=RelPath,
                      content_sha256=Digest, error=None, error_code=None)
            return True

    # ── restart reconciliation ───────────────────────────────────────────
    def Reconcile(self) -> dict:
        """Resume polling for submitted requests; mark never-confirmed ones interrupted.

        A candidate without a provider request id may or may not have been
        submitted before the crash, so it is NOT resubmitted automatically
        (that could double-charge); the user can retry it explicitly.
        """
        Db = self.Ctx.Db
        Resumed = Interrupted = 0
        for C in Db.All("SELECT c.id, c.provider_request_id FROM candidates c "
                        "WHERE c.status IN ('pending', 'generating')"):
            if C["provider_request_id"] and not self.Ctx.Provider.Owns(C["provider_request_id"]):
                if self.Ctx.Provider.Name == "mock":
                    Logger.warning("Candidate %s has a live request; it resumes when live mode is active", C["id"])
                    continue
                Db.Update("candidates", C["id"], status="failed", error_code="interrupted",
                          error="This mock-mode generation cannot be resumed in live mode. You can retry.")
                Interrupted += 1
            elif C["provider_request_id"]:
                self.Ctx.Runner.Spawn(f"cand:{C['id']}", self._Drive(C["id"]))
                Resumed += 1
            else:
                Db.Update("candidates", C["id"], status="failed", error_code="interrupted",
                          error="Generation was interrupted by a server restart. You can retry.")
                Interrupted += 1
        return {"resumed": Resumed, "interrupted": Interrupted}

    # ── reads / ownership ────────────────────────────────────────────────
    def RequireDesign(self, Who: Principal, DesignId: str) -> dict:
        D = self.Ctx.Db.One("SELECT * FROM designs WHERE id = ?", (DesignId,))
        if D is None or D["owner_account_id"] != Who.AccountId:
            raise HttpError(404, "design_not_found", "Design not found.")
        return D

    def RequireReadyCandidate(self, DesignId: str, CandidateId: str) -> dict:
        C = self.Ctx.Db.One("SELECT c.* FROM candidates c JOIN batches b ON b.id = c.batch_id "
                            "WHERE c.id = ? AND b.design_id = ?", (CandidateId, DesignId))
        if C is None:
            raise HttpError(404, "candidate_not_found", "That image does not belong to this design.")
        if C["status"] != "ready":
            raise HttpError(409, "candidate_not_ready", "That image is not ready yet.")
        return C

    def _OwnedBatch(self, Who: Principal, BatchId: str) -> dict:
        B = self.Ctx.Db.One("SELECT b.* FROM batches b JOIN designs d ON d.id = b.design_id "
                            "WHERE b.id = ? AND d.owner_account_id = ?", (BatchId, Who.AccountId))
        if B is None:
            raise HttpError(404, "batch_not_found", "Batch not found.")
        return B

    def _OwnedCandidate(self, Who: Principal, CandidateId: str) -> tuple[dict, dict]:
        C = self.Ctx.Db.One("SELECT * FROM candidates WHERE id = ?", (CandidateId,))
        if C is None:
            raise HttpError(404, "candidate_not_found", "Image not found.")
        return C, self._OwnedBatch(Who, C["batch_id"])

    def _FirstBatch(self, DesignId: str) -> dict:
        B = self.Ctx.Db.One("SELECT id FROM batches WHERE design_id = ? ORDER BY created_at LIMIT 1", (DesignId,))
        return self.GetBatch(B["id"])

    def CandidateJson(self, C: dict) -> dict:
        return {"id": C["id"], "batch_id": C["batch_id"], "slot": C["slot"], "status": C["status"],
                "image_url": self.Ctx.AssetUrl(C["asset_path"]) if C["status"] == "ready" else None,
                "error": C["error"], "error_code": C["error_code"],
                "retryable": C["status"] == "failed"}

    def GetBatch(self, BatchId: str) -> dict:
        B = self.Ctx.Db.One("SELECT * FROM batches WHERE id = ?", (BatchId,))
        Cands = self.Ctx.Db.All("SELECT * FROM candidates WHERE batch_id = ? ORDER BY slot", (BatchId,))
        return {"id": B["id"], "design_id": B["design_id"], "kind": B["kind"],
                "parent_candidate_id": B["parent_candidate_id"], "user_text": B["user_text"],
                "reference_url": self.Ctx.AssetUrl(B["reference_asset"]),
                "desired_count": B["desired_count"], "config_version": B["config_version"],
                "status": BatchStatus(Cands, B["desired_count"]), "created_at": B["created_at"],
                "candidates": [self.CandidateJson(C) for C in Cands]}
