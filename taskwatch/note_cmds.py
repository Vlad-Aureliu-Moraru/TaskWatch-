from .db import get_conn
from .models import Note


def list_notes(task_id: int | None = None) -> list[Note]:
    conn = get_conn()
    if task_id is not None:
        rows = conn.execute(
            "SELECT id, task_id, date, note FROM notes WHERE task_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, task_id, date, note FROM notes ORDER BY id"
        ).fetchall()
    return [Note(id=r["id"], task_id=r["task_id"], date=r["date"], note=r["note"]) for r in rows]


def create_note(task_id: int, date: str, note: str) -> Note:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO notes (task_id, date, note) VALUES (?, ?, ?)",
        (task_id, date, note),
    )
    conn.commit()
    return Note(id=cur.lastrowid, task_id=task_id, date=date, note=note)


def get_note(note_id: int) -> Note | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT id, task_id, date, note FROM notes WHERE id = ?",
        (note_id,),
    ).fetchone()
    if row is None:
        return None
    return Note(id=row["id"], task_id=row["task_id"], date=row["date"], note=row["note"])


def delete_note(note_id: int) -> bool:
    conn = get_conn()
    cur = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    conn.commit()
    return cur.rowcount > 0


def update_note(note_id: int, date: str | None = None, note: str | None = None) -> Note | None:
    updates = {}
    if date is not None:
        updates["date"] = date
    if note is not None:
        updates["note"] = note
    if not updates:
        return get_note(note_id)
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [note_id]
    conn = get_conn()
    cur = conn.execute(f"UPDATE notes SET {set_clause} WHERE id = ?", vals)
    conn.commit()
    if cur.rowcount == 0:
        return None
    return get_note(note_id)
