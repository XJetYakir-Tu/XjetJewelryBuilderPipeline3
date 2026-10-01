"""Shared ring IDs — one short name for one exact ring, used when people talk about a design.

  R-1042        the design (one design journey, numbered in creation order from 1001)
  R-1042-B      option B of the first four designs (options A–D = slots 1–4)
  R-1042-R1B    option B of refinement 1 (refinements numbered in creation order)

The design number is stored (designs.ring_no, assigned by a database trigger on insert, so every way
of creating a design gets one); the option part is derived from the candidate's batch and slot.
"""

First = 1001


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
    Conn.execute(f"""CREATE TRIGGER IF NOT EXISTS designs_ring_no_assign AFTER INSERT ON designs
                     WHEN NEW.ring_no IS NULL BEGIN
                       UPDATE designs SET ring_no = (SELECT COALESCE(MAX(ring_no), {First - 1}) + 1 FROM designs)
                       WHERE id = NEW.id;
                     END""")


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
