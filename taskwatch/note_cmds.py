from datetime import datetime

from .db import get_conn
from .models import Note

_COLS = "id, task_id, date, note, file_path, created_at"


def _row_to_note(row) -> Note:
    return Note(
        id=row["id"],
        task_id=row["task_id"],
        date=row["date"],
        note=row["note"],
        file_path=row["file_path"],
        created_at=row["created_at"] or "",
    )


def list_notes(task_id: int | None = None) -> list[Note]:
    conn = get_conn()
    if task_id is not None:
        rows = conn.execute(
            f"SELECT {_COLS} FROM notes WHERE task_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {_COLS} FROM notes ORDER BY id"
        ).fetchall()
    return [_row_to_note(r) for r in rows]


def create_note(
    task_id: int,
    date: str,
    note: str,
    file_path: str | None = None,
) -> Note:
    conn = get_conn()
    created_at = datetime.now().isoformat()
    cur = conn.execute(
        "INSERT INTO notes (task_id, date, note, file_path, created_at) VALUES (?, ?, ?, ?, ?)",
        (task_id, date, note, file_path, created_at),
    )
    conn.commit()
    return Note(
        id=cur.lastrowid,
        task_id=task_id,
        date=date,
        note=note,
        file_path=file_path,
        created_at=created_at,
    )


def get_note(note_id: int) -> Note | None:
    conn = get_conn()
    row = conn.execute(
        f"SELECT {_COLS} FROM notes WHERE id = ?",
        (note_id,),
    ).fetchone()
    return None if row is None else _row_to_note(row)


def delete_note(note_id: int) -> bool:
    conn = get_conn()
    cur = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    conn.commit()
    return cur.rowcount > 0


def dedup_notes(task_id: int | None = None) -> int:
    conn = get_conn()
    where = "WHERE task_id = ?" if task_id is not None else ""
    params = (task_id,) if task_id is not None else ()
    groups = conn.execute(
        f"SELECT task_id, date, note FROM notes {where} "
        "GROUP BY task_id, date, note HAVING COUNT(*) > 1",
        params,
    ).fetchall()
    deleted = 0
    for tid, dt, note_text in groups:
        keep = conn.execute(
            "SELECT id FROM notes WHERE task_id=? AND date=? AND note=? ORDER BY id LIMIT 1",
            (tid, dt, note_text),
        ).fetchone()
        if keep is None:
            continue
        cur = conn.execute(
            "DELETE FROM notes WHERE task_id=? AND date=? AND note=? AND id != ?",
            (tid, dt, note_text, keep["id"]),
        )
        deleted += cur.rowcount
    conn.commit()
    return deleted


def update_note(
    note_id: int,
    date: str | None = None,
    note: str | None = None,
    file_path: str | None = None,
) -> Note | None:
    updates = {}
    if date is not None:
        updates["date"] = date
    if note is not None:
        updates["note"] = note
    if file_path is not None:
        updates["file_path"] = file_path
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
