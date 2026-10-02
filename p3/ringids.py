"""Shared ring IDs — one short name for one exact ring, used when people talk about a design.

  R-1042        the design (one design journey, numbered in creation order from 1001)
  R-1042-B      option B of the first four designs (options A–D = slots 1–4)
  R-1042-R1B    option B of refinement 1 (refinements numbered in creation order)

The design number is stored (designs.ring_no, assigned by a database trigger on insert, so every way
of creating a design gets one); the option part is derived from the candidate's batch and slot.
A number is never reused: when a legacy gallery copy is merged into its master, its number is retired
(retired_rings) and the trigger counts past it.
"""

First = 1001

RetiredTable = """CREATE TABLE IF NOT EXISTS retired_rings (
    ring_no      INTEGER PRIMARY KEY,
    design_id    TEXT NOT NULL,
    merged_into  TEXT NOT NULL,
    title        TEXT NOT NULL,
    retired_at   TEXT NOT NULL
)"""


def Install(Conn) -> None:
    """Add designs.ring_no, number existing designs in creation order, and number new ones on insert."""
    Cols = {R[1] for R in Conn.execute("PRAGMA table_info(designs)")}
    if "ring_no" not in Cols:
        Conn.execute("ALTER TABLE designs ADD COLUMN ring_no INTEGER")
    Missing = Conn.execute("SELECT id FROM designs WHERE ring_no IS NULL ORDER BY created_at, id").fetchall()
    if Missing:
        Next = (Conn.execute("SELECT MAX(ring_no) FROM designs").fetchone()[0] or First - 1) + 1
        for N, (Did,) in enumerate(Missing):
            Conn.execute("UPDATE designs SET ring_no = ? WHERE id = ?", (Next + N, Did))
    Conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS designs_ring_no ON designs(ring_no)")
    Conn.execute(RetiredTable)
    Conn.execute("DROP TRIGGER IF EXISTS designs_ring_no_assign")
    Conn.execute(f"""CREATE TRIGGER designs_ring_no_assign AFTER INSERT ON designs
                     WHEN NEW.ring_no IS NULL BEGIN
                       UPDATE designs SET ring_no = (SELECT MAX(n) + 1 FROM (
                           SELECT COALESCE(MAX(ring_no), {First - 1}) AS n FROM designs
                           UNION ALL SELECT COALESCE(MAX(ring_no), {First - 1}) FROM retired_rings))
                       WHERE id = NEW.id;
                     END""")


def Retire(Conn, RingNo, DesignId: str, MergedInto: str, Title: str, At: str) -> None:
    """Retire a design's number when the design is merged away; the number is never given out again."""
    if RingNo is not None:
        Conn.execute("INSERT OR REPLACE INTO retired_rings (ring_no, design_id, merged_into, title, retired_at) VALUES (?,?,?,?,?)",
                     (RingNo, DesignId, MergedInto, Title, At))


def Retired(Db) -> list[dict]:
    """Retired numbers with where they went: R-1015 (Fil Line) → Fil Twist R-1012."""
    return [{"ring_id": DesignRef(R["ring_no"]), "title": R["title"], "design_id": R["design_id"], "retired_at": R["retired_at"],
             "merged_into": {"design_id": R["merged_into"], "ring_id": DesignRef(R["master_ring_no"]), "title": R["master_title"]}}
            for R in Db.All("SELECT r.*, d.title AS master_title, d.ring_no AS master_ring_no FROM retired_rings r "
                            "LEFT JOIN designs d ON d.id = r.merged_into ORDER BY r.ring_no")]


def DesignRef(RingNo) -> str | None:
    return f"R-{RingNo}" if RingNo is not None else None


def CandidateRefs(Db, DesignIds: list[str]) -> dict[str, str]:
    """candidate id → ring ID for every candidate of the given designs."""
    if not DesignIds:
        return {}
    Q = ",".join("?" * len(DesignIds))
    Rows = Db.All(f"SELECT c.id, c.slot, b.id AS batch_id, b.kind, b.created_at, d.ring_no FROM candidates c "
                  f"JOIN batches b ON b.id = c.batch_id JOIN designs d ON d.id = b.design_id "
                  f"WHERE b.design_id IN ({Q}) ORDER BY b.design_id, b.created_at, b.id", DesignIds)
    Refine, Out = {}, {}
    for R in Rows:
        Key = (R["ring_no"], R["batch_id"])
        if R["kind"] == "refine" and Key not in Refine:
            Refine[Key] = 1 + sum(1 for K in Refine if K[0] == R["ring_no"])
        Batch = f"R{Refine[Key]}" if R["kind"] == "refine" else ""
        Out[R["id"]] = f"{DesignRef(R['ring_no'])}-{Batch}{chr(ord('A') + int(R['slot']))}"
    return Out


def CandidateRef(Db, CandidateId: str) -> str | None:
    Row = Db.One("SELECT b.design_id FROM candidates c JOIN batches b ON b.id = c.batch_id WHERE c.id = ?", (CandidateId,))
    return CandidateRefs(Db, [Row["design_id"]]).get(CandidateId) if Row else None
