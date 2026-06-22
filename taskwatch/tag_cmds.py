import sqlite3

from .db import get_conn
from .models import Tag


def list_tags() -> list[Tag]:
    conn = get_conn()
    rows = conn.execute("SELECT id, name FROM tags ORDER BY name").fetchall()
    return [Tag(id=r["id"], name=r["name"]) for r in rows]


def create_tag(name: str) -> Tag:
    conn = get_conn()
    try:
        cur = conn.execute("INSERT INTO tags (name) VALUES (?)", (name.strip(),))
        conn.commit()
        return Tag(id=cur.lastrowid, name=name.strip())
    except sqlite3.IntegrityError:
        row = conn.execute("SELECT id, name FROM tags WHERE name = ?", (name.strip(),)).fetchone()
        return Tag(id=row["id"], name=row["name"])


def delete_tag(tag_id: int) -> bool:
    conn = get_conn()
    cur = conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    conn.commit()
    return cur.rowcount > 0


def add_tag_to_task(task_id: int, tag_name: str) -> Tag | None:
    conn = get_conn()
    tag = create_tag(tag_name)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO task_tags (task_id, tag_id) VALUES (?, ?)",
            (task_id, tag.id),
        )
        conn.commit()
    except Exception:
        return None
    return tag


def remove_tag_from_task(task_id: int, tag_name: str) -> bool:
    conn = get_conn()
    tag = conn.execute("SELECT id FROM tags WHERE name = ?", (tag_name.strip(),)).fetchone()
    if tag is None:
        return False
    cur = conn.execute(
        "DELETE FROM task_tags WHERE task_id = ? AND tag_id = ?",
        (task_id, tag["id"]),
    )
    conn.commit()
    return cur.rowcount > 0


def get_tags_for_task(task_id: int) -> list[Tag]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT t.id, t.name FROM tags t
           JOIN task_tags tt ON t.id = tt.tag_id
           WHERE tt.task_id = ?
           ORDER BY t.name""",
        (task_id,),
    ).fetchall()
    return [Tag(id=r["id"], name=r["name"]) for r in rows]


def get_tasks_by_tag(tag_name: str) -> list[int]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT tt.task_id FROM task_tags tt
           JOIN tags t ON t.id = tt.tag_id
           WHERE t.name = ?""",
        (tag_name.strip(),),
    ).fetchall()
    return [r["task_id"] for r in rows]
