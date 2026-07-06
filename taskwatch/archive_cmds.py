import sqlite3

from .db import get_conn
from .models import Archive


def list_archives() -> list[Archive]:
    conn = get_conn()
    rows = conn.execute("SELECT id, name FROM archives ORDER BY id").fetchall()
    return [Archive(id=r["id"], name=r["name"]) for r in rows]


def create_archive(name: str) -> Archive | None:
    conn = get_conn()
    try:
        cur = conn.execute("INSERT INTO archives (name) VALUES (?)", (name,))
        conn.commit()
        return Archive(id=cur.lastrowid, name=name)
    except sqlite3.IntegrityError:
        return None


def rename_archive(archive_id: int, name: str) -> Archive | None:
    conn = get_conn()
    try:
        cur = conn.execute("UPDATE archives SET name = ? WHERE id = ?", (name, archive_id))
        conn.commit()
    except sqlite3.IntegrityError:
        return None
    if cur.rowcount == 0:
        return None
    return Archive(id=archive_id, name=name)


def get_archive(archive_id: int) -> Archive | None:
    conn = get_conn()
    row = conn.execute("SELECT id, name FROM archives WHERE id = ?", (archive_id,)).fetchone()
    if row is None:
        return None
    return Archive(id=row["id"], name=row["name"])


def delete_archive(archive_id: int) -> bool:
    conn = get_conn()
    cur = conn.execute("DELETE FROM archives WHERE id = ?", (archive_id,))
    conn.commit()
    return cur.rowcount > 0
