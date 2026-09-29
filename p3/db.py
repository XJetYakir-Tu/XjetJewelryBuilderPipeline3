"""SQLite persistence for Pipeline 3 (its own database file under P3_DATA_DIR).

Unlike Pipeline 2's in-memory job registry, every batch, candidate, movie and
mesh job is a durable row carrying its provider request id, so a restarted
server can reconcile remote work instead of losing it (spec section 9).
"""

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

Schema = """
CREATE TABLE IF NOT EXISTS access_tokens (
    token       TEXT PRIMARY KEY,
    label       TEXT NOT NULL DEFAULT '',
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS designs (
    id                     TEXT PRIMARY KEY,
    token                  TEXT NOT NULL REFERENCES access_tokens(token),
    title                  TEXT NOT NULL,
    prompt                 TEXT NOT NULL,
    selected_candidate_id  TEXT,
    client_request_id      TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    UNIQUE (token, client_request_id)
);

CREATE TABLE IF NOT EXISTS batches (
    id                   TEXT PRIMARY KEY,
    design_id            TEXT NOT NULL REFERENCES designs(id),
    kind                 TEXT NOT NULL CHECK (kind IN ('initial', 'refine')),
    parent_candidate_id  TEXT REFERENCES candidates(id),
    user_text            TEXT NOT NULL,
    effective_prompt     TEXT NOT NULL,
    endpoint             TEXT NOT NULL,
    reference_asset      TEXT,
    reference_upload_url TEXT,
    desired_count        INTEGER NOT NULL,
    config_version       TEXT NOT NULL,
    client_request_id    TEXT,
    created_at           TEXT NOT NULL,
    UNIQUE (design_id, client_request_id)
);

CREATE TABLE IF NOT EXISTS candidates (
    id                   TEXT PRIMARY KEY,
    batch_id             TEXT NOT NULL REFERENCES batches(id),
    slot                 INTEGER NOT NULL,
    status               TEXT NOT NULL CHECK (status IN ('pending', 'generating', 'ready', 'failed')),
    seed                 INTEGER NOT NULL,
    attempts             INTEGER NOT NULL DEFAULT 0,
    duplicate_retries    INTEGER NOT NULL DEFAULT 0,
    provider_request_id  TEXT,
    asset_path           TEXT,
    content_sha256       TEXT,
    error                TEXT,
    error_code           TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    UNIQUE (batch_id, slot)
);

CREATE TABLE IF NOT EXISTS customizations (
    id            TEXT PRIMARY KEY,
    design_id     TEXT NOT NULL REFERENCES designs(id),
    candidate_id  TEXT NOT NULL REFERENCES candidates(id),
    material_id   TEXT NOT NULL,
    ring_size     REAL,
    quantity      INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE (design_id, candidate_id)
);

CREATE TABLE IF NOT EXISTS movies (
    id                   TEXT PRIMARY KEY,
    candidate_id         TEXT NOT NULL REFERENCES candidates(id),
    config_version       TEXT NOT NULL,
    endpoint             TEXT NOT NULL,
    status               TEXT NOT NULL CHECK (status IN ('queued', 'running', 'ready', 'failed', 'interrupted')),
    provider_request_id  TEXT,
    asset_path           TEXT,
    error                TEXT,
    error_code           TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
-- At most one live (queued/running/ready) movie per candidate + config: dedupes repeated Proceed clicks.
CREATE UNIQUE INDEX IF NOT EXISTS movies_one_live
    ON movies(candidate_id, config_version) WHERE status IN ('queued', 'running', 'ready');

CREATE TABLE IF NOT EXISTS meshes (
    id                   TEXT PRIMARY KEY,
    candidate_id         TEXT NOT NULL REFERENCES candidates(id),
    endpoint             TEXT NOT NULL,
    settings_json        TEXT NOT NULL,
    config_version       TEXT NOT NULL,
    status               TEXT NOT NULL CHECK (status IN ('queued', 'running', 'ready', 'failed', 'interrupted')),
    provider_request_id  TEXT,
    original_path        TEXT,
    original_format      TEXT,
    stl_path             TEXT,
    provenance_json      TEXT NOT NULL,
    error                TEXT,
    error_code           TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bag_lines (
    id                TEXT PRIMARY KEY,
    token             TEXT NOT NULL REFERENCES access_tokens(token),
    design_id         TEXT NOT NULL REFERENCES designs(id),
    candidate_id      TEXT NOT NULL REFERENCES candidates(id),
    customization_id  TEXT NOT NULL REFERENCES customizations(id),
    material_id       TEXT NOT NULL,
    ring_size         REAL NOT NULL,
    quantity          INTEGER NOT NULL,
    unit_price        REAL NOT NULL,
    currency          TEXT NOT NULL,
    pricing_version   TEXT NOT NULL,
    quote_json        TEXT NOT NULL,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    ref_id      TEXT NOT NULL,
    units       INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);
"""


def Now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def NewId(Prefix: str) -> str:
    return f"{Prefix}_{uuid.uuid4().hex}"


class Database:
    """Thin sqlite3 wrapper. One connection per call; writes serialized by a lock."""

    def __init__(self, DbPath: Path):
        self.DbPath = Path(DbPath)
        self.DbPath.parent.mkdir(parents=True, exist_ok=True)
        self._WriteLock = threading.RLock()
        with self.Connect() as Conn:
            Conn.execute("PRAGMA journal_mode=WAL")
            Conn.executescript(Schema)

    @contextmanager
    def Connect(self):
        Conn = sqlite3.connect(self.DbPath, timeout=30, isolation_level=None)
        Conn.row_factory = sqlite3.Row
        Conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield Conn
        finally:
            Conn.close()

    @contextmanager
    def Transaction(self):
        with self._WriteLock, self.Connect() as Conn:
            Conn.execute("BEGIN IMMEDIATE")
            try:
                yield Conn
                Conn.execute("COMMIT")
            except BaseException:
                Conn.execute("ROLLBACK")
                raise

    def One(self, Sql: str, Params=()) -> dict | None:
        with self.Connect() as Conn:
            Row = Conn.execute(Sql, Params).fetchone()
            return dict(Row) if Row else None

    def All(self, Sql: str, Params=()) -> list[dict]:
        with self.Connect() as Conn:
            return [dict(R) for R in Conn.execute(Sql, Params).fetchall()]

    def Execute(self, Sql: str, Params=()) -> int:
        with self.Transaction() as Conn:
            return Conn.execute(Sql, Params).rowcount

    def Update(self, Table: str, RowId: str, **Fields) -> None:
        Fields["updated_at"] = Now()
        Cols = ", ".join(f"{K} = ?" for K in Fields)
        self.Execute(f"UPDATE {Table} SET {Cols} WHERE id = ?", (*Fields.values(), RowId))

    def RecordUsage(self, Token: str, Kind: str, RefId: str, Units: int) -> None:
        self.Execute("INSERT INTO usage_events (token, kind, ref_id, units, created_at) VALUES (?,?,?,?,?)",
                     (Token, Kind, RefId, Units, Now()))


def Dumps(Obj) -> str:
    return json.dumps(Obj, sort_keys=True)
