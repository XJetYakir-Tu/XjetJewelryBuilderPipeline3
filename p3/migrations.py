"""One-time migration of a pre-accounts (version 0) application database.

Version 0 stored access tokens and usage inside pipeline3.db and keyed designs
and bag lines by the raw token. Version 1 moves accounts, tokens and usage to
the account provider (accounts.db) and keys application rows by
owner_account_id. The old file is copied aside before anything changes, and
re-running is safe (tokens already imported are mapped, not duplicated).
"""

import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from p3.db import DesignsTable, BagLinesTable, SchemaVersion

Logger = logging.getLogger("p3.migrations")


def _Columns(Conn, Table: str) -> list[str]:
    return [R[1] for R in Conn.execute(f"PRAGMA table_info({Table})")]


def _AsNew(CreateSql: str, Table: str) -> str:
    return CreateSql.replace(f"CREATE TABLE IF NOT EXISTS {Table} (", f"CREATE TABLE {Table}_new (", 1)


def NeedsAccountMigration(DbPath: Path) -> bool:
    if not Path(DbPath).is_file():
        return False
    with sqlite3.connect(DbPath) as Conn:
        return "token" in _Columns(Conn, "designs")


def MigrateToAccounts(DbPath: Path, Accounts) -> dict | None:
    """Migrate a v0 database in place. Returns a summary, or None if nothing to do."""
    DbPath = Path(DbPath)
    if not NeedsAccountMigration(DbPath):
        return None
    Stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    Backup = DbPath.with_name(f"{DbPath.stem}.pre-accounts-{Stamp}{DbPath.suffix}")
    shutil.copy2(DbPath, Backup)

    Conn = sqlite3.connect(DbPath, isolation_level=None)
    Conn.row_factory = sqlite3.Row
    try:
        Tables = {R[0] for R in Conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        # 1. Accounts + tokens → account provider (idempotent per token).
        TokenMap = {}
        if "access_tokens" in Tables:
            for R in Conn.execute("SELECT token, label, active FROM access_tokens"):
                TokenMap[R["token"]] = Accounts.ImportLegacyToken(R["token"], R["label"], bool(R["active"]))
        for R in Conn.execute("SELECT DISTINCT token FROM designs"):
            if R["token"] not in TokenMap:   # orphan rows: give them an inactive owner account
                TokenMap[R["token"]] = Accounts.ImportLegacyToken(R["token"], "migrated (no token row)", False)
        # 2. Usage → account provider.
        Usage = 0
        if "usage_events" in Tables:
            for R in Conn.execute("SELECT token, kind, ref_id, units, created_at FROM usage_events"):
                if R["token"] in TokenMap:
                    Accounts.ImportLegacyUsage(TokenMap[R["token"]], R["kind"], R["units"], R["ref_id"], R["created_at"])
                    Usage += 1
        # 3. Rebuild owner-keyed tables.
        Conn.execute("PRAGMA foreign_keys=OFF")
        Conn.execute("BEGIN IMMEDIATE")
        Conn.execute("CREATE TEMP TABLE legacy_owner (token TEXT PRIMARY KEY, account_id TEXT NOT NULL)")
        Conn.executemany("INSERT INTO legacy_owner VALUES (?, ?)", list(TokenMap.items()))
        # SQLite's safe rebuild: build a new table, copy, drop the old, rename the new into place.
        # (Renaming the OLD table instead would rewrite other tables' foreign keys to point at it.)
        Conn.execute(_AsNew(DesignsTable, "designs"))   # single statement; executescript would commit early
        Conn.execute("INSERT INTO designs_new (id, owner_account_id, title, prompt, selected_candidate_id, client_request_id, "
                     "created_at, updated_at) SELECT d.id, o.account_id, d.title, d.prompt, d.selected_candidate_id, "
                     "d.client_request_id, d.created_at, d.updated_at FROM designs d JOIN legacy_owner o ON o.token = d.token")
        Designs = Conn.execute("SELECT COUNT(*) FROM designs_new").fetchone()[0]
        Lines = 0
        if "bag_lines" in Tables and "token" in _Columns(Conn, "bag_lines"):
            Conn.execute(_AsNew(BagLinesTable, "bag_lines"))
            Conn.execute("INSERT INTO bag_lines_new (id, owner_account_id, design_id, candidate_id, customization_id, material_id, "
                         "ring_size, quantity, unit_price, currency, pricing_version, quote_json, created_at) "
                         "SELECT b.id, o.account_id, b.design_id, b.candidate_id, b.customization_id, b.material_id, "
                         "b.ring_size, b.quantity, b.unit_price, b.currency, b.pricing_version, b.quote_json, b.created_at "
                         "FROM bag_lines b JOIN legacy_owner o ON o.token = b.token")
            Lines = Conn.execute("SELECT COUNT(*) FROM bag_lines_new").fetchone()[0]
            Conn.execute("DROP TABLE bag_lines")
            Conn.execute("ALTER TABLE bag_lines_new RENAME TO bag_lines")
        Conn.execute("DROP TABLE designs")
        Conn.execute("ALTER TABLE designs_new RENAME TO designs")
        for Old in ("usage_events", "access_tokens"):
            if Old in Tables:
                Conn.execute(f"DROP TABLE {Old}")
        Broken = Conn.execute("PRAGMA foreign_key_check").fetchall()
        if Broken:
            raise RuntimeError(f"Migration would leave broken foreign keys: {Broken[:5]}")
        Conn.execute(f"PRAGMA user_version = {SchemaVersion}")
        Conn.execute("COMMIT")
    except BaseException:
        if Conn.in_transaction:
            Conn.execute("ROLLBACK")
        raise
    finally:
        Conn.execute("PRAGMA foreign_keys=ON")
        Conn.close()
    Summary = {"backup": str(Backup), "accounts": len(set(TokenMap.values())), "designs": Designs,
               "bag_lines": Lines, "usage_events": Usage}
    Logger.warning("Migrated application database to account ids: %s", Summary)
    return Summary
