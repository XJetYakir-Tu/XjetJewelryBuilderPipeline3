"""LocalAccountProvider — P3's own accounts, tokens and usage in accounts.db.

Kept in a separate database file from P3's application data so the whole
account domain can later be replaced by (or migrated into) a shared service
without touching designs, batches, movies or the bag.

Tokens are stored as SHA-256 hashes; the plaintext is shown once, when issued.
Credits are not limited yet (AuthorizeSpend always allows) — the balance policy
is an open product decision, see docs/ACCOUNTS.md.
"""

import hashlib
import secrets
import uuid

from p3.accounts import AuthError, Principal
from p3.db import Database, Now

Issuer = "p3local"

Schema = """
CREATE TABLE IF NOT EXISTS accounts (
    account_id    TEXT PRIMARY KEY,          -- "p3local:acct_<hex>"
    display_name  TEXT NOT NULL DEFAULT '',
    email         TEXT,
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS access_tokens (
    token_hash    TEXT PRIMARY KEY,          -- sha256(token); plaintext is never stored
    token_hint    TEXT NOT NULL,             -- first characters, for operators
    account_id    TEXT NOT NULL REFERENCES accounts(account_id),
    label         TEXT NOT NULL DEFAULT '',
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    last_used_at  TEXT
);

CREATE TABLE IF NOT EXISTS usage_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id    TEXT NOT NULL,
    pipeline      TEXT NOT NULL DEFAULT 'p3',
    kind          TEXT NOT NULL,
    units         INTEGER NOT NULL,
    ref_id        TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
"""


def HashToken(Token: str) -> str:
    return hashlib.sha256(Token.strip().encode("utf-8")).hexdigest()


def NewAccountId() -> str:
    return f"{Issuer}:acct_{uuid.uuid4().hex}"


class LocalAccountProvider:
    Issuer = Issuer

    def __init__(self, DbPath):
        self.Db = Database(DbPath, SchemaSql=Schema)

    # ── authentication ───────────────────────────────────────────────────
    def Authenticate(self, Token: str | None) -> Principal:
        if not Token or not Token.strip():
            raise AuthError("token_required", "An access token is required.")
        Row = self.Db.One(
            "SELECT t.token_hash, t.active, a.account_id, a.display_name, a.status, t.label "
            "FROM access_tokens t JOIN accounts a ON a.account_id = t.account_id WHERE t.token_hash = ?",
            (HashToken(Token),))
        if Row is None or not Row["active"] or Row["status"] != "active":
            raise AuthError("token_invalid", "This access token is not valid or has been deactivated.")
        self.Db.Execute("UPDATE access_tokens SET last_used_at = ? WHERE token_hash = ?", (Now(), Row["token_hash"]))
        return Principal(AccountId=Row["account_id"], DisplayName=Row["display_name"] or Row["label"],
                         Issuer=Issuer, Attributes={"token_label": Row["label"]})

    # ── credits / usage ──────────────────────────────────────────────────
    def AuthorizeSpend(self, Who: Principal, Kind: str, Units: int) -> None:
        # No balance policy yet. A shared provider would check P2-style balances here.
        return None

    def RecordUsage(self, AccountId: str, Kind: str, Units: int, RefId: str) -> None:
        self.Db.Execute("INSERT INTO usage_events (account_id, kind, units, ref_id, created_at) VALUES (?,?,?,?,?)",
                        (AccountId, Kind, int(Units), RefId, Now()))

    def UsageSummary(self, AccountId: str) -> dict:
        return {R["kind"]: R["units"] for R in self.Db.All(
            "SELECT kind, SUM(units) AS units FROM usage_events WHERE account_id = ? GROUP BY kind", (AccountId,))}

    def Profile(self, Who: Principal) -> dict:
        Usage = self.UsageSummary(Who.AccountId)
        return {"label": Who.DisplayName, "account_id": Who.AccountId, "issuer": Who.Issuer,
                "usage": {"image_requests": Usage.get("image", 0), "movie_requests": Usage.get("movie", 0)},
                "balance": None}   # None = unlimited (no credit policy configured)

    # ── administration ───────────────────────────────────────────────────
    def IssueToken(self, Label: str, DisplayName: str | None = None, Email: str | None = None,
                   AccountId: str | None = None, Token: str | None = None) -> tuple[str, Principal]:
        """Create (or reuse AccountId for) an account and issue a new token for it."""
        Token = Token or ("p3_" + secrets.token_urlsafe(24))
        AccountId = AccountId or NewAccountId()
        T = Now()
        with self.Db.Transaction() as Conn:
            Conn.execute("INSERT OR IGNORE INTO accounts (account_id, display_name, email, created_at) VALUES (?,?,?,?)",
                         (AccountId, DisplayName or Label, Email, T))
            Conn.execute("INSERT INTO access_tokens (token_hash, token_hint, account_id, label, active, created_at) "
                         "VALUES (?,?,?,?,1,?)", (HashToken(Token), Token[:8], AccountId, Label, T))
        return Token, Principal(AccountId=AccountId, DisplayName=DisplayName or Label, Issuer=Issuer)

    # ── migration support (local provider only) ──────────────────────────
    def ImportLegacyToken(self, Token: str, Label: str, Active: bool) -> str:
        """Import a plaintext token from a version-0 pipeline3.db; returns its account id (idempotent)."""
        Row = self.Db.One("SELECT account_id FROM access_tokens WHERE token_hash = ?", (HashToken(Token),))
        if Row:
            return Row["account_id"]
        _, Who = self.IssueToken(Label or "migrated", Token=Token)
        if not Active:
            self.DeactivateToken(Token)
        return Who.AccountId

    def ImportLegacyUsage(self, AccountId: str, Kind: str, Units: int, RefId: str, CreatedAt: str) -> None:
        self.Db.Execute("INSERT INTO usage_events (account_id, kind, units, ref_id, created_at) VALUES (?,?,?,?,?)",
                        (AccountId, Kind, int(Units), RefId, CreatedAt))

    def DeactivateToken(self, Token: str) -> bool:
        return self.Db.Execute("UPDATE access_tokens SET active = 0 WHERE token_hash = ?", (HashToken(Token),)) > 0

    def ListAccounts(self) -> list[dict]:
        return self.Db.All(
            "SELECT a.account_id, a.display_name, a.status, a.created_at, t.token_hint, t.label, t.active, t.last_used_at "
            "FROM accounts a LEFT JOIN access_tokens t ON t.account_id = a.account_id ORDER BY a.created_at")
