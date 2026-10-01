"""Accounts, access tokens and credits — the ONLY integration point with identity.

Pipeline 3 never reads token or account storage directly: every route resolves
the caller through an AccountProvider into a Principal, and every paid action
asks the provider before spending and reports usage afterwards. Application
data (designs, bag lines, …) stores only Principal.AccountId.

Today the provider is LocalAccountProvider (P3's own accounts.db). A future
shared Users/Auth/Tokens service used by Pipeline 2 and Pipeline 3 is added by
implementing this same Protocol (see docs/ACCOUNTS.md) — no other module changes.
"""

from dataclasses import dataclass, field
from typing import Protocol


class AuthError(Exception):
    """The token is missing, unknown or deactivated."""

    def __init__(self, Code: str, Message: str):
        super().__init__(Message)
        self.Code = Code
        self.Message = Message


class InsufficientCredits(Exception):
    """The account may not spend the requested units (quota / balance policy)."""

    def __init__(self, Message: str = "You have reached the maximum number of generations allowed for this access token."):
        super().__init__(Message)
        self.Message = Message


@dataclass(frozen=True)
class Principal:
    """The authenticated caller.

    AccountId is a namespaced, provider-issued identifier ("<issuer>:<id>", e.g.
    "p3local:acct_…"). It is what application tables store, so they can later be
    re-pointed at a shared user system by mapping IDs, not by rewriting tokens.
    """
    AccountId: str
    DisplayName: str
    Issuer: str
    Attributes: dict = field(default_factory=dict)


# Usage kinds P3 reports. Units are provider requests (an image batch of four is 4 "image").
UsageImage = "image"
UsageMovie = "movie"
UsageMesh = "mesh"


class AccountProvider(Protocol):
    Issuer: str

    def Authenticate(self, Token: str | None) -> Principal:
        """Resolve an access token to a Principal or raise AuthError."""

    def AuthorizeSpend(self, Who: Principal, Kind: str, Units: int) -> None:
        """Raise InsufficientCredits if the account may not start this paid work."""

    def RecordUsage(self, AccountId: str, Kind: str, Units: int, RefId: str) -> None:
        """Record units actually submitted to a provider (called once per submission)."""

    def CommitCharge(self, AccountId: str, Kind: str, RefId: str) -> bool:
        """Charge the account's allowance for a FINISHED result (P2: one generation per 360° movie)."""

    def StartEmailRegistration(self, Name: str, Email: str) -> dict:
        """Self-service registration (P2 /api/register semantics). Returns status + secret/token to mail."""

    def VerifyEmail(self, Secret: str) -> dict:
        """Consume a verification link (P2 /verify): verified | already | expired | invalid."""

    def UsageSummary(self, AccountId: str) -> dict:
        """{kind: units} for display."""

    def Profile(self, Who: Principal) -> dict:
        """Customer-visible account summary (label, usage, balance if any)."""

    # Administration — implemented by providers that own their token storage.
    def IssueToken(self, Label: str, DisplayName: str | None = None, Email: str | None = None) -> tuple[str, Principal]:
        ...

    def DeactivateToken(self, Token: str) -> bool:
        ...

    def ListAccounts(self) -> list[dict]:
        ...


def BuildProvider(Kind: str, DataDir) -> AccountProvider:
    """Factory keyed by P3_ACCOUNT_PROVIDER. Only "local" exists today."""
    if Kind == "local":
        from p3.accounts.local import LocalAccountProvider
        return LocalAccountProvider(DataDir / "accounts.db")
    raise ValueError(f"Unknown account provider {Kind!r} (supported: local)")
