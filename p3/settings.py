"""Runtime settings for Pipeline 3, read from environment variables.

Nothing here points at Pipeline 2. All runtime data lives under P3_DATA_DIR
(default ./var inside this repository).
"""

import os
from dataclasses import dataclass
from pathlib import Path

RepoRoot  = Path(__file__).resolve().parent.parent
ConfigDir = RepoRoot / "config"
WebDir    = RepoRoot / "web"


def _Bool(Name: str, Default: bool = False) -> bool:
    Raw = os.environ.get(Name)
    if Raw is None:
        return Default
    return Raw.strip().lower() in ("1", "true", "yes", "on")


def _LoadDotEnv(EnvPath: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines). Existing environment variables win."""
    if not EnvPath.is_file():
        return
    for Line in EnvPath.read_text(encoding="utf-8").splitlines():
        Line = Line.strip()
        if not Line or Line.startswith("#") or "=" not in Line:
            continue
        Key, Value = Line.split("=", 1)
        os.environ.setdefault(Key.strip(), Value.strip().strip('"').strip("'"))


@dataclass
class Settings:
    DataDir: Path
    Provider: str                  # "mock" | "fal"
    FalKey: str | None
    AdminKey: str | None
    PricingProfilePath: Path
    AllowUnapprovedPricing: bool
    MockLatencyS: float
    PollIntervalS: float
    MaxTransientPollErrors: int
    AccountProvider: str = "local"   # P3_ACCOUNT_PROVIDER; see docs/ACCOUNTS.md

    @property
    def DbPath(self) -> Path:
        return self.DataDir / "pipeline3.db"

    @property
    def AssetsDir(self) -> Path:
        """Customer-visible artifacts (candidate images, movies, references). Served at /assets."""
        return self.DataDir / "assets"

    @property
    def DevDir(self) -> Path:
        """Developer-only artifacts (meshes). Never served statically."""
        return self.DataDir / "dev"


def LoadSettings(**Overrides) -> Settings:
    _LoadDotEnv(RepoRoot / ".env")
    ProfilePath = Path(os.environ.get("P3_PRICING_PROFILE", ConfigDir / "pricing_profile.json"))
    if not ProfilePath.is_absolute():
        ProfilePath = (RepoRoot / ProfilePath).resolve()
    Values = dict(
        DataDir=Path(os.environ.get("P3_DATA_DIR", RepoRoot / "var")).resolve(),
        Provider=os.environ.get("P3_PROVIDER", "mock").strip().lower(),
        FalKey=os.environ.get("FAL_KEY") or None,
        AdminKey=os.environ.get("P3_ADMIN_KEY") or None,
        PricingProfilePath=ProfilePath,
        AllowUnapprovedPricing=_Bool("P3_ALLOW_UNAPPROVED_PRICING"),
        MockLatencyS=float(os.environ.get("P3_MOCK_LATENCY_S", "1.5")),
        PollIntervalS=float(os.environ.get("P3_POLL_INTERVAL_S", "2.0")),
        MaxTransientPollErrors=int(os.environ.get("P3_MAX_TRANSIENT_POLL_ERRORS", "10")),
        AccountProvider=os.environ.get("P3_ACCOUNT_PROVIDER", "local").strip().lower(),
    )
    Values.update(Overrides)
    S = Settings(**Values)
    if S.Provider not in ("mock", "fal"):
        raise ValueError(f"P3_PROVIDER must be 'mock' or 'fal', got {S.Provider!r}")
    if S.Provider == "fal" and not S.FalKey:
        raise ValueError("P3_PROVIDER=fal requires FAL_KEY")
    return S
