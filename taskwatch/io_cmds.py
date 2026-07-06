import json
from datetime import date
from pathlib import Path

from .db import get_conn
from .note_cmds import create_note
from .tag_cmds import add_tag_to_task
from .task_cmds import create_task

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
    "notes": frozenset({"id", "task_id", "date", "note", "file_path", "created_at"}),
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
        return "Import failed"


def import_tasks_from_directory_json(json_str: str, target_directory_id: int) -> tuple[bool, str]:
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON: {e}"

    tasks_normalized: list[dict] = []

    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                return False, "Each item in the array must be a JSON object"
            tasks_normalized.append(item)
    elif isinstance(data, dict):
        if "task" in data:
            tasks_normalized.append(data)
        elif "tasks" in data and isinstance(data["tasks"], list):
            tasks_normalized.extend(data["tasks"])
        else:
            return False, "Unrecognized JSON structure"
    else:
        return False, "Expected a JSON object or array"

    if not tasks_normalized:
        return False, "No tasks found in JSON"

    imported = 0
    errors: list[str] = []
    total = len(tasks_normalized)

    for entry in tasks_normalized:
        task_data = entry.get("task", entry)
        name = task_data.get("name", "").strip()
        if not name:
            errors.append("A task is missing a name")
            continue

        try:
            task = create_task(
                directory_id=target_directory_id,
                name=name,
                description=task_data.get("description", ""),
                deadline=task_data.get("deadline", "none"),
                urgency=task_data.get("urgency", 1),
                difficulty=task_data.get("difficulty", 1),
                time_dedicated=task_data.get("time_dedicated", 0),
                repeatable=task_data.get("repeatable", False),
                repeatable_type=task_data.get("repeatable_type", "none"),
                has_to_be_completed_to_repeat=task_data.get("has_to_be_completed_to_repeat", True),
                repeat_on_specific_day=task_data.get("repeat_on_specific_day", "none"),
                pinned=task_data.get("pinned", False),
            )

            for note_data in entry.get("notes", []):
                if isinstance(note_data, dict):
                    create_note(
                        task_id=task.id,
                        date=note_data.get("date", date.today().isoformat()),
                        note=note_data.get("note", ""),
                        file_path=note_data.get("file_path"),
                    )

            for tag_name in entry.get("tags", []):
                if isinstance(tag_name, str) and tag_name.strip():
                    add_tag_to_task(task.id, tag_name.strip())

            imported += 1
        except ValueError as e:
            errors.append(f"'{name}': {e}")

    msg = f"Imported {imported} of {total} task(s)"
    if errors:
        msg += f". {len(errors)} error(s): {'; '.join(errors[:3])}"
    return True, msg


def import_notes_json(json_str: str, task_id: int) -> tuple[bool, str]:
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON: {e}"

    if isinstance(data, dict):
        if "notes" in data and isinstance(data["notes"], list):
            data = data["notes"]
        else:
            data = [data]

    if not isinstance(data, list):
        return False, "Expected a JSON array of note objects"

    imported = 0
    errors: list[str] = []
    total = len(data)

    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            errors.append(f"Item {i} is not an object")
            continue
        note_text = entry.get("note", "").strip()
        if not note_text:
            errors.append(f"Item {i} is missing a 'note' field")
            continue
        try:
            create_note(
                task_id=task_id,
                date=entry.get("date", date.today().isoformat()),
                note=note_text,
                file_path=entry.get("file_path"),
            )
            imported += 1
        except ValueError as e:
            errors.append(f"Item {i}: {e}")

    msg = f"Imported {imported} of {total} note(s)"
    if errors:
        msg += f". {len(errors)} error(s): {'; '.join(errors[:3])}"
    return True, msg
