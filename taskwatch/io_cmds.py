import json
from pathlib import Path
from .db import get_conn

ALLOWED_TABLES = frozenset({"archives", "directories", "tasks", "notes", "tags", "task_tags"})

ALLOWED_COLUMNS = {
    "archives": frozenset({"id", "name"}),
    "directories": frozenset({"id", "archive_id", "name"}),
    "tasks": frozenset({
        "id", "directory_id", "name", "description", "deadline", "urgency",
        "difficulty", "time_dedicated", "repeatable", "repeatable_type",
        "finished", "finished_date", "has_to_be_completed_to_repeat",
        "repeat_on_specific_day", "position",
    }),
    "notes": frozenset({"id", "task_id", "date", "note"}),
    "tags": frozenset({"id", "name"}),
    "task_tags": frozenset({"task_id", "tag_id"}),
}


def export_data(path: str) -> bool:
    conn = get_conn()
    try:
        data = {
            "archives": [dict(r) for r in conn.execute("SELECT * FROM archives").fetchall()],
            "directories": [dict(r) for r in conn.execute("SELECT * FROM directories").fetchall()],
            "tasks": [dict(r) for r in conn.execute("SELECT * FROM tasks").fetchall()],
            "notes": [dict(r) for r in conn.execute("SELECT * FROM notes").fetchall()],
            "tags": [dict(r) for r in conn.execute("SELECT * FROM tags").fetchall()],
            "task_tags": [dict(r) for r in conn.execute("SELECT * FROM task_tags").fetchall()],
        }
        Path(path).write_text(json.dumps(data, indent=2, default=str))
        return True
    except OSError:
        return False


def import_data(path: str) -> str:
    conn = get_conn()
    try:
        raw = Path(path).read_text()
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as e:
        return f"Failed to read file: {e}"

    required = {"archives", "directories", "tasks", "notes", "tags", "task_tags"}
    if not required.issubset(data.keys()):
        return "Missing required keys in import file"

    total = 0
    try:
        for table, rows in data.items():
            if table not in ALLOWED_TABLES or not rows:
                continue
            valid_cols = [c for c in rows[0].keys() if c in ALLOWED_COLUMNS.get(table, frozenset())]
            if not valid_cols:
                continue
            placeholders = ", ".join("?" for _ in valid_cols)
            col_list = ", ".join(valid_cols)
            for row in rows:
                conn.execute(
                    f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({placeholders})",
                    [row[c] for c in valid_cols],
                )
            total += len(rows)
        conn.commit()
        return f"Imported {total} records"
    except Exception:
        conn.rollback()
        return f"Import failed"
