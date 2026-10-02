"""SQLite persistence for Pipeline 3 (its own database file under P3_DATA_DIR).

Unlike Pipeline 2's in-memory job registry, every batch, candidate, movie and
mesh job is a durable row carrying its provider request id, so a restarted
server can reconcile remote work instead of losing it (spec section 9).

This file holds Pipeline 3 APPLICATION data only. Accounts, tokens and usage
live behind p3.accounts (its own accounts.db); application rows reference the
owner only by owner_account_id, a namespaced provider-issued id.
"""

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SchemaVersion = 1

DesignsTable = """
CREATE TABLE IF NOT EXISTS designs (
    id                     TEXT PRIMARY KEY,
    owner_account_id       TEXT NOT NULL,
    title                  TEXT NOT NULL,
    prompt                 TEXT NOT NULL,
    selected_candidate_id  TEXT,
    client_request_id      TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    UNIQUE (owner_account_id, client_request_id)
);
"""

BagLinesTable = """
CREATE TABLE IF NOT EXISTS bag_lines (
    id                TEXT PRIMARY KEY,
    owner_account_id  TEXT NOT NULL,
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
"""

# ── Sessions (Admin analytics) ──────────────────────────────────────────────
# A session is one design journey: it starts when the customer submits the first prompt of a New
# Design (designs.created_at). Most stage times already live in the job tables (batches,
# candidates, customizations, movies); session_events adds what they do not keep — choice
# history with the fixed price shown, bag adds/removes, Bag viewed, Checkout clicked, design
# reopened — plus admin actions. Append-only, so funnels can be rebuilt at any time.
SessionTables = """
CREATE TABLE IF NOT EXISTS session_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    design_id         TEXT,                       -- NULL only for new_design_clicked
    owner_account_id  TEXT NOT NULL,
    kind              TEXT NOT NULL,
    data_json         TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS session_events_design ON session_events(design_id, created_at);
CREATE INDEX IF NOT EXISTS session_events_owner ON session_events(owner_account_id, created_at);

-- Admin-requested 3D production geometry for one session (never started automatically).
CREATE TABLE IF NOT EXISTS session_3d (
    id                 TEXT PRIMARY KEY,
    design_id          TEXT NOT NULL REFERENCES designs(id),
    candidate_id       TEXT NOT NULL REFERENCES candidates(id),
    mesh_id            TEXT REFERENCES meshes(id),
    customer_size      REAL,                      -- what the customer chose (NULL = none)
    production_size    REAL NOT NULL,             -- size the geometry is scaled to
    size_source        TEXT NOT NULL CHECK (size_source IN ('customer', 'default', 'admin_override')),
    customer_material  TEXT,
    material_id        TEXT NOT NULL,
    material_source    TEXT NOT NULL CHECK (material_source IN ('customer', 'default', 'admin_override')),
    status             TEXT NOT NULL,             -- requested | generating | measuring | measured | needs_review | failed
    requested_by       TEXT NOT NULL,
    error              TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS session_3d_design ON session_3d(design_id, created_at);

-- The raw Hi3D STL is the master geometry: measured once (exact), values for any size follow by scaling.
CREATE TABLE IF NOT EXISTS raw_geometry (
    mesh_id           TEXT PRIMARY KEY REFERENCES meshes(id),
    stl_path          TEXT NOT NULL,
    sha256            TEXT,
    bytes             INTEGER,
    faces             INTEGER,
    status            TEXT NOT NULL,              -- downloaded | measured | failed
    measurement_json  TEXT,                       -- raw ID, X/Y/Z, volume, area, frame, bore … (model units)
    method_version    TEXT,
    measured_at       TEXT,
    preview_path      TEXT,                       -- visual-only light preview (never used for numbers)
    thumbnail_path    TEXT,                       -- Hi3D's own thumbnail image
    integrity         TEXT NOT NULL DEFAULT 'pending',   -- pending | closed | open | unknown (background)
    integrity_json    TEXT,
    timings_json      TEXT NOT NULL DEFAULT '{}',
    error             TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

-- Persistent queue for heavy local STL work: only one job runs at a time; survives restarts.
CREATE TABLE IF NOT EXISTS geometry_jobs (
    id             TEXT PRIMARY KEY,
    kind           TEXT NOT NULL,                 -- measure | preview | integrity | export
    mesh_id        TEXT NOT NULL,
    session_3d_id  TEXT,
    priority       INTEGER NOT NULL,              -- lower runs first
    status         TEXT NOT NULL,                 -- queued | running | done | failed | cancelled
    attempts       INTEGER NOT NULL DEFAULT 0,
    params_json    TEXT NOT NULL DEFAULT '{}',
    result_json    TEXT,
    error          TEXT,
    output_path    TEXT,
    expires_at     TEXT,
    created_at     TEXT NOT NULL,
    started_at     TEXT,
    finished_at    TEXT
);
CREATE INDEX IF NOT EXISTS geometry_jobs_queue ON geometry_jobs(status, priority, created_at);

-- Inspiration Gallery: curated XJet designs shown on the customer site (the design's chosen image).
CREATE TABLE IF NOT EXISTS gallery_items (
    id            TEXT PRIMARY KEY,
    design_id     TEXT NOT NULL UNIQUE REFERENCES designs(id),
    candidate_id  TEXT NOT NULL REFERENCES candidates(id),
    position      INTEGER NOT NULL,
    created_at    TEXT NOT NULL,
    created_by    TEXT NOT NULL
);

-- A customer on a shared XJet master design (from the gallery): one row per customer and design.
-- The design itself is never copied; the customer's selection lives here, their Customize choices in
-- customizations (per owner), their bag lines in bag_lines.
CREATE TABLE IF NOT EXISTS gallery_uses (
    id                     TEXT PRIMARY KEY,
    design_id              TEXT NOT NULL REFERENCES designs(id),
    owner_account_id       TEXT NOT NULL,
    gallery_item_id        TEXT,
    source_candidate_id    TEXT REFERENCES candidates(id),    -- the tile's image when they started
    selected_candidate_id  TEXT REFERENCES candidates(id),    -- their own pick within the design
    started_at             TEXT NOT NULL,
    last_active_at         TEXT NOT NULL,
    removed_at             TEXT,                               -- removed from the customer's My Designs (journey kept)
    UNIQUE (design_id, owner_account_id)
);
CREATE INDEX IF NOT EXISTS gallery_uses_owner ON gallery_uses(owner_account_id, last_active_at);

-- Real processing stages with start/end times (Hi3D, download, queue, geometry, ready).
CREATE TABLE IF NOT EXISTS stage_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    subject      TEXT NOT NULL,
    stage        TEXT NOT NULL,
    detail_json  TEXT NOT NULL DEFAULT '{}',
    started_at   TEXT NOT NULL,
    ended_at     TEXT
);
CREATE INDEX IF NOT EXISTS stage_log_subject ON stage_log(subject, id);

-- Measured geometry: one row per stage (raw = as returned, production = repaired + scaled).
CREATE TABLE IF NOT EXISTS geometry_results (
    id                 TEXT PRIMARY KEY,
    session_3d_id      TEXT NOT NULL REFERENCES session_3d(id),
    stage              TEXT NOT NULL CHECK (stage IN ('raw', 'production')),
    size_x_mm          REAL, size_y_mm REAL, size_z_mm REAL,
    inner_diameter_mm  REAL,
    volume_mm3         REAL,
    surface_area_mm2   REAL,
    watertight         INTEGER NOT NULL,
    scale_factor       REAL,
    stl_path           TEXT,
    method_version     TEXT NOT NULL,
    checks_json        TEXT NOT NULL DEFAULT '{}',
    created_at         TEXT NOT NULL
);

-- Weight / cost / price from a production geometry. Kept separate from the fixed customer price,
-- which is never changed by these numbers.
CREATE TABLE IF NOT EXISTS price_calculations (
    id                    TEXT PRIMARY KEY,
    session_3d_id         TEXT NOT NULL REFERENCES session_3d(id),
    geometry_id           TEXT NOT NULL REFERENCES geometry_results(id),
    material_id           TEXT NOT NULL,
    density_g_cm3         REAL NOT NULL,
    weight_g              REAL,
    production_cost       REAL,
    calculated_price      REAL,
    currency              TEXT,
    cost_model_version    TEXT,
    breakdown_json        TEXT NOT NULL DEFAULT '{}',
    fixed_price           REAL,
    fixed_price_version   TEXT,
    status                TEXT NOT NULL,           -- calculated | cost_model_not_configured | needs_review
    created_at            TEXT NOT NULL
);
"""



def CustomizationsDdl(Name: str) -> str:
    """Customize choices (material / size / quantity) are per customer AND per design + option: on a
    shared gallery design every customer keeps their own choices."""
    return f"""
CREATE TABLE IF NOT EXISTS {Name} (
    id                TEXT PRIMARY KEY,
    owner_account_id  TEXT NOT NULL,
    design_id         TEXT NOT NULL REFERENCES designs(id),
    candidate_id      TEXT NOT NULL REFERENCES candidates(id),
    material_id       TEXT NOT NULL,
    ring_size         REAL,
    quantity          INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE (owner_account_id, design_id, candidate_id)
);
"""

Schema = DesignsTable + """
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

""" + CustomizationsDdl("customizations") + """

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

""" + BagLinesTable + SessionTables + """
CREATE INDEX IF NOT EXISTS designs_owner ON designs(owner_account_id, updated_at);
CREATE INDEX IF NOT EXISTS bag_lines_owner ON bag_lines(owner_account_id, created_at);
"""


def Now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def NewId(Prefix: str) -> str:
    return f"{Prefix}_{uuid.uuid4().hex}"


class Database:
    """Thin sqlite3 wrapper. One connection per call; writes serialized by a lock."""

    def __init__(self, DbPath: Path, SchemaSql: str | None = None):
        self.DbPath = Path(DbPath)
        self.DbPath.parent.mkdir(parents=True, exist_ok=True)
        self._WriteLock = threading.RLock()
        with self.Connect() as Conn:
            Conn.execute("PRAGMA journal_mode=WAL")
            Conn.executescript(Schema if SchemaSql is None else SchemaSql)
            if SchemaSql is None:
                Cols = {R[1] for R in Conn.execute("PRAGMA table_info(designs)")}
                if "owner_account_id" in Cols and "ai_mode" not in Cols:
                    # AI mode the session was created in ('mock' | 'fal'); mock sessions stay out of the Admin.
                    Conn.execute("ALTER TABLE designs ADD COLUMN ai_mode TEXT")
                if "owner_account_id" in Cols and "source_design_id" not in Cols:
                    # Designs started from the Inspiration Gallery: a copy of an XJet design's batch.
                    Conn.execute("ALTER TABLE designs ADD COLUMN source_design_id TEXT")
                    Conn.execute("ALTER TABLE designs ADD COLUMN source_candidate_id TEXT")
                CCols = {R[1] for R in Conn.execute("PRAGMA table_info(customizations)")}
                if CCols and "owner_account_id" not in CCols:
                    # Customize choices became per customer (shared gallery designs): rebuild with the owner.
                    # Older databases may hold duplicate rows per design + option (no UNIQUE then): the
                    # latest one wins. Atomic, so a failure leaves the old table untouched.
                    Conn.execute("DROP TABLE IF EXISTS customizations_new")
                    # bag_lines reference customizations(id); the ids are kept, so the references stay valid —
                    # but enforcement must be off while the old table is dropped (a PRAGMA outside the transaction).
                    Fk = Conn.execute("PRAGMA foreign_keys").fetchone()[0]
                    Conn.execute("PRAGMA foreign_keys=OFF")
                    Conn.execute("BEGIN")
                    try:
                        Conn.execute(CustomizationsDdl("customizations_new"))
                        Conn.execute("INSERT OR REPLACE INTO customizations_new (id, owner_account_id, design_id, candidate_id, "
                                     "material_id, ring_size, quantity, created_at, updated_at) SELECT c.id, d.owner_account_id, "
                                     "c.design_id, c.candidate_id, c.material_id, c.ring_size, c.quantity, c.created_at, c.updated_at "
                                     "FROM customizations c JOIN designs d ON d.id = c.design_id ORDER BY c.updated_at, c.rowid")
                        Conn.execute("DROP TABLE customizations")
                        Conn.execute("ALTER TABLE customizations_new RENAME TO customizations")
                        Conn.execute("COMMIT")
                    except Exception:
                        Conn.execute("ROLLBACK")
                        raise
                    finally:
                        Conn.execute(f"PRAGMA foreign_keys={'ON' if Fk else 'OFF'}")
                MCols = {R[1] for R in Conn.execute("PRAGMA table_info(movies)")}
                if MCols and "requested_by" not in MCols:
                    Conn.execute("ALTER TABLE movies ADD COLUMN requested_by TEXT")   # who pays for a movie on a shared design
                if "owner_account_id" in Cols and "removed_at" not in Cols:
                    Conn.execute("ALTER TABLE designs ADD COLUMN removed_at TEXT")   # removed from My Designs (journey kept)
                UCols = {R[1] for R in Conn.execute("PRAGMA table_info(gallery_uses)")}
                if UCols and "removed_at" not in UCols:
                    Conn.execute("ALTER TABLE gallery_uses ADD COLUMN removed_at TEXT")
                if "owner_account_id" in Cols:
                    from p3 import ringids
                    ringids.Install(Conn)                 # shared ring IDs (R-1042, R-1042-B …)
            if SchemaSql is None and Conn.execute("PRAGMA user_version").fetchone()[0] < SchemaVersion:
                Conn.execute(f"PRAGMA user_version = {SchemaVersion}")

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


def Dumps(Obj) -> str:
    return json.dumps(Obj, sort_keys=True)
