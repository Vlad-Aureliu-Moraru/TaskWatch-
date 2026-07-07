import json
import time
from datetime import date, datetime
from pathlib import Path

from .db import get_conn
from .note_cmds import create_note
from .tag_cmds import add_tag_to_task, create_tag
from .task_cmds import create_task

ALLOWED_TABLES = frozenset({"archives", "directories", "tasks", "notes", "tags", "task_tags"})

ALLOWED_COLUMNS = {
    "archives": frozenset({"id", "name"}),
    "directories": frozenset({"id", "archive_id", "name"}),
    "tasks": frozenset({
        "id", "directory_id", "name", "description", "deadline", "urgency",
        "difficulty", "time_dedicated", "repeatable", "repeatable_type",
        "finished", "finished_date", "has_to_be_completed_to_repeat",
        "repeat_on_specific_day", "position", "pinned",
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


def _fetch_all(conn, table: str, ids: list[int], id_col: str = "id") -> list[dict]:
    if not ids:
        return []
    ph = ",".join("?" for _ in ids)
    return [dict(r) for r in conn.execute(f"SELECT * FROM {table} WHERE {id_col} IN ({ph})", ids).fetchall()]


def _build_archive_export(archive_id: int, conn) -> dict:
    data: dict = {}
    a = conn.execute("SELECT * FROM archives WHERE id = ?", (archive_id,)).fetchone()
    if not a:
        return data
    data["archive"] = dict(a)
    dirs = conn.execute("SELECT * FROM directories WHERE archive_id = ?", (archive_id,)).fetchall()
    data["directories"] = [dict(d) for d in dirs]
    dir_ids = [d["id"] for d in dirs]
    if dir_ids:
        ph = ",".join("?" for _ in dir_ids)
        tasks = conn.execute(f"SELECT * FROM tasks WHERE directory_id IN ({ph})", dir_ids).fetchall()
        data["tasks"] = [dict(t) for t in tasks]
        task_ids = [t["id"] for t in tasks]
        if task_ids:
            data["notes"] = _fetch_all(conn, "notes", task_ids, "task_id")
            data["subtasks"] = _fetch_all(conn, "subtasks", task_ids, "task_id")
            ttags = _fetch_all(conn, "task_tags", task_ids, "task_id")
            data["task_tags"] = ttags
            data["task_dependencies"] = _fetch_all(conn, "task_dependencies", task_ids, "task_id")
            tag_ids = list({r["tag_id"] for r in ttags})
            data["tags"] = _fetch_all(conn, "tags", tag_ids)
    return data


def _build_directory_export(directory_id: int, conn) -> dict:
    data: dict = {}
    d = conn.execute("SELECT * FROM directories WHERE id = ?", (directory_id,)).fetchone()
    if not d:
        return data
    data["directory"] = dict(d)
    a = conn.execute("SELECT * FROM archives WHERE id = ?", (d["archive_id"],)).fetchone()
    if a:
        data["archive"] = dict(a)
    tasks = conn.execute("SELECT * FROM tasks WHERE directory_id = ?", (directory_id,)).fetchall()
    data["tasks"] = [dict(t) for t in tasks]
    task_ids = [t["id"] for t in tasks]
    if task_ids:
        data["notes"] = _fetch_all(conn, "notes", task_ids, "task_id")
        data["subtasks"] = _fetch_all(conn, "subtasks", task_ids, "task_id")
        ttags = _fetch_all(conn, "task_tags", task_ids, "task_id")
        data["task_tags"] = ttags
        data["task_dependencies"] = _fetch_all(conn, "task_dependencies", task_ids, "task_id")
        tag_ids = list({r["tag_id"] for r in ttags})
        data["tags"] = _fetch_all(conn, "tags", tag_ids)
    return data


def _build_task_export(task_id: int, conn) -> dict:
    data: dict = {}
    t = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not t:
        return data
    data["task"] = dict(t)
    d = conn.execute("SELECT * FROM directories WHERE id = ?", (t["directory_id"],)).fetchone()
    if d:
        data["directory"] = dict(d)
        a = conn.execute("SELECT * FROM archives WHERE id = ?", (d["archive_id"],)).fetchone()
        if a:
            data["archive"] = dict(a)
    data["notes"] = _fetch_all(conn, "notes", [task_id], "task_id")
    data["subtasks"] = _fetch_all(conn, "subtasks", [task_id], "task_id")
    ttags = _fetch_all(conn, "task_tags", [task_id], "task_id")
    data["task_tags"] = ttags
    data["task_dependencies"] = _fetch_all(conn, "task_dependencies", [task_id], "task_id")
    tag_ids = list({r["tag_id"] for r in ttags})
    data["tags"] = _fetch_all(conn, "tags", tag_ids)
    return data


def _build_note_export(note_id: int, conn) -> dict:
    data: dict = {}
    n = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    if n:
        data["note"] = dict(n)
    return data


def export_current_item(level_name: str, selected_id: int, path: str | None = None) -> bool:
    conn = get_conn()
    try:
        builders = {
            "ARCHIVES": _build_archive_export,
            "DIRECTORIES": _build_directory_export,
            "TASKS": _build_task_export,
            "NOTES": _build_note_export,
        }
        builder = builders.get(level_name)
        if not builder:
            return False
        data = builder(selected_id, conn)
        if not data:
            return False
        type_map = {"ARCHIVES": "archive", "DIRECTORIES": "directory", "TASKS": "task", "NOTES": "note"}
        data["export_type"] = type_map[level_name]
        data["exported_at"] = datetime.now().isoformat()
        out = path or "/tmp/taskwatch_export_current.json"
        Path(out).write_text(json.dumps(data, indent=2, default=str))
        return True
    except Exception:
        return False


def _unique_name(base: str, exists_fn) -> str:
    if not exists_fn(base):
        return base
    for i in range(2, 100):
        cand = f"{base} (Imported {i})"
        if not exists_fn(cand):
            return cand
    return f"{base} (Imported {int(time.time())})"


def _import_task_children(data: dict, conn, old_to_new_task: dict[int, int]) -> None:
    if not old_to_new_task:
        return
    all_new = set(old_to_new_task.values())
    notes = data.get("notes", [])
    for n in notes:
        new_tid = old_to_new_task.get(n["task_id"])
        if new_tid:
            create_note(
                task_id=new_tid,
                date=n.get("date", date.today().isoformat()),
                note=n.get("note", ""),
                file_path=n.get("file_path"),
            )
    subs = data.get("subtasks", [])
    for s in subs:
        new_tid = old_to_new_task.get(s["task_id"])
        if new_tid:
            conn.execute(
                "INSERT INTO subtasks (task_id, content, finished, position) VALUES (?, ?, ?, ?)",
                (new_tid, s["content"], s.get("finished", 0), s.get("position", 0)),
            )
    ttags = data.get("task_tags", [])
    for tt in ttags:
        new_tid = old_to_new_task.get(tt["task_id"])
        if new_tid:
            old_name = next(
                (tg["name"] for tg in data.get("tags", []) if tg["id"] == tt["tag_id"]),
                None,
            )
            if old_name:
                tag = create_tag(old_name)
                if tag:
                    conn.execute(
                        "INSERT OR IGNORE INTO task_tags (task_id, tag_id) VALUES (?, ?)",
                        (new_tid, tag.id),
                    )
    deps = data.get("task_dependencies", [])
    for dp in deps:
        new_tid = old_to_new_task.get(dp["task_id"])
        new_dep = old_to_new_task.get(dp["depends_on_task_id"])
        if new_tid and new_dep and new_dep in all_new:
            conn.execute(
                "INSERT OR IGNORE INTO task_dependencies (task_id, depends_on_task_id) VALUES (?, ?)",
                (new_tid, new_dep),
            )
    conn.commit()


def _do_import_archive(data: dict, conn) -> str:
    arch = data.get("archive", {})
    if not arch:
        return "Import failed: no archive data in file"

    name = _unique_name(
        arch["name"],
        lambda n: conn.execute("SELECT 1 FROM archives WHERE name = ?", (n,)).fetchone() is not None,
    )
    cur = conn.execute("INSERT INTO archives (name) VALUES (?)", (name,))
    new_archive_id = cur.lastrowid
    conn.commit()

    old_to_new_dir: dict[int, int] = {}
    for d in data.get("directories", []):
        d_name = _unique_name(
            d["name"],
            lambda n, aid=new_archive_id: conn.execute(
                "SELECT 1 FROM directories WHERE name = ? AND archive_id = ?", (n, aid)
            ).fetchone() is not None,
        )
        cur = conn.execute(
            "INSERT INTO directories (archive_id, name) VALUES (?, ?)",
            (new_archive_id, d_name),
        )
        old_to_new_dir[d["id"]] = cur.lastrowid

    old_to_new_task: dict[int, int] = {}
    for t in data.get("tasks", []):
        new_did = old_to_new_dir.get(t["directory_id"])
        if new_did is None:
            continue
        t_name = _unique_name(
            t["name"],
            lambda n, did=new_did: conn.execute(
                "SELECT 1 FROM tasks WHERE name = ? AND directory_id = ?", (n, did)
            ).fetchone() is not None,
        )
        try:
            task = create_task(
                directory_id=new_did,
                name=t_name,
                description=t.get("description", ""),
                deadline=t.get("deadline", "none"),
                urgency=t.get("urgency", 1),
                difficulty=t.get("difficulty", 1),
                time_dedicated=t.get("time_dedicated", 0),
                repeatable=bool(t.get("repeatable", False)),
                repeatable_type=t.get("repeatable_type", "none"),
                has_to_be_completed_to_repeat=bool(t.get("has_to_be_completed_to_repeat", True)),
                repeat_on_specific_day=t.get("repeat_on_specific_day", "none"),
                pinned=bool(t.get("pinned", False)),
            )
            old_to_new_task[t["id"]] = task.id
            if t.get("finished"):
                conn.execute(
                    "UPDATE tasks SET finished = 1, finished_date = ? WHERE id = ?",
                    (t.get("finished_date", "none"), task.id),
                )
        except ValueError:
            pass

    _import_task_children(data, conn, old_to_new_task)
    conn.commit()
    total = len(data.get("directories", [])) + len(old_to_new_task) + len(data.get("notes", []))
    return f"Imported archive '{name}' ({total} items)"


def _do_import_directory(data: dict, conn, archive_id: int) -> str:
    d = data.get("directory", {})
    if not d:
        return "Import failed: no directory data in file"

    d_name = _unique_name(
        d["name"],
        lambda n: conn.execute(
            "SELECT 1 FROM directories WHERE name = ? AND archive_id = ?", (n, archive_id)
        ).fetchone() is not None,
    )
    cur = conn.execute(
        "INSERT INTO directories (archive_id, name) VALUES (?, ?)",
        (archive_id, d_name),
    )
    new_dir_id = cur.lastrowid
    conn.commit()

    old_to_new_task: dict[int, int] = {}
    for t in data.get("tasks", []):
        t_name = _unique_name(
            t["name"],
            lambda n, did=new_dir_id: conn.execute(
                "SELECT 1 FROM tasks WHERE name = ? AND directory_id = ?", (n, did)
            ).fetchone() is not None,
        )
        try:
            task = create_task(
                directory_id=new_dir_id,
                name=t_name,
                description=t.get("description", ""),
                deadline=t.get("deadline", "none"),
                urgency=t.get("urgency", 1),
                difficulty=t.get("difficulty", 1),
                time_dedicated=t.get("time_dedicated", 0),
                repeatable=bool(t.get("repeatable", False)),
                repeatable_type=t.get("repeatable_type", "none"),
                has_to_be_completed_to_repeat=bool(t.get("has_to_be_completed_to_repeat", True)),
                repeat_on_specific_day=t.get("repeat_on_specific_day", "none"),
                pinned=bool(t.get("pinned", False)),
            )
            old_to_new_task[t["id"]] = task.id
            if t.get("finished"):
                conn.execute(
                    "UPDATE tasks SET finished = 1, finished_date = ? WHERE id = ?",
                    (t.get("finished_date", "none"), task.id),
                )
        except ValueError:
            pass

    _import_task_children(data, conn, old_to_new_task)
    total = len(old_to_new_task) + len(data.get("notes", []))
    return f"Imported directory '{d_name}' ({total} items)"


def _do_import_task(data: dict, conn, directory_id: int) -> str:
    t = data.get("task", {})
    if not t:
        return "Import failed: no task data in file"

    t_name = _unique_name(
        t["name"],
        lambda n: conn.execute(
            "SELECT 1 FROM tasks WHERE name = ? AND directory_id = ?", (n, directory_id)
        ).fetchone() is not None,
    )
    try:
        task = create_task(
            directory_id=directory_id,
            name=t_name,
            description=t.get("description", ""),
            deadline=t.get("deadline", "none"),
            urgency=t.get("urgency", 1),
            difficulty=t.get("difficulty", 1),
            time_dedicated=t.get("time_dedicated", 0),
            repeatable=bool(t.get("repeatable", False)),
            repeatable_type=t.get("repeatable_type", "none"),
            has_to_be_completed_to_repeat=bool(t.get("has_to_be_completed_to_repeat", True)),
            repeat_on_specific_day=t.get("repeat_on_specific_day", "none"),
            pinned=bool(t.get("pinned", False)),
        )
    except ValueError as e:
        return f"Import failed: {e}"

    if t.get("finished"):
        conn.execute(
            "UPDATE tasks SET finished = 1, finished_date = ? WHERE id = ?",
            (t.get("finished_date", "none"), task.id),
        )

    old_to_new_task = {t["id"]: task.id}
    _import_task_children(data, conn, old_to_new_task)
    total = 1 + len(data.get("notes", []))
    return f"Imported task '{t_name}' ({total} items)"


def _do_import_note(data: dict, conn, task_id: int) -> str:
    n = data.get("note", {})
    if not n:
        return "Import failed: no note data in file"
    create_note(
        task_id=task_id,
        date=n.get("date", date.today().isoformat()),
        note=n.get("note", ""),
        file_path=n.get("file_path"),
    )
    return "Imported 1 note"


def _do_merge_import_directory(data: dict, conn, archive_id: int) -> str:
    d = data.get("directory", {})
    if not d:
        return "Import failed: no directory data in file"

    existing = conn.execute(
        "SELECT id FROM directories WHERE name = ? AND archive_id = ?",
        (d["name"], archive_id),
    ).fetchone()

    if existing is None:
        return _do_import_directory(data, conn, archive_id)

    dir_id = existing["id"]
    merged = 0
    created = 0
    notes_added = 0

    old_to_new_task: dict[int, int] = {}

    for t in data.get("tasks", []):
        name = t.get("name", "").strip()
        if not name:
            continue

        existing_task = conn.execute(
            "SELECT id FROM tasks WHERE name = ? AND directory_id = ?",
            (name, dir_id),
        ).fetchone()

        if existing_task is not None:
            existing_id = existing_task["id"]

            if t.get("finished"):
                conn.execute(
                    "UPDATE tasks SET finished = 1, finished_date = ? WHERE id = ?",
                    (t.get("finished_date", "none"), existing_id),
                )

            for n in data.get("notes", []):
                if n.get("task_id") == t["id"]:
                    create_note(
                        task_id=existing_id,
                        date=n.get("date", date.today().isoformat()),
                        note=n.get("note", ""),
                        file_path=n.get("file_path"),
                    )
                    notes_added += 1

            for tt in data.get("task_tags", []):
                if tt.get("task_id") == t["id"]:
                    tag_name = next(
                        (tg["name"] for tg in data.get("tags", []) if tg["id"] == tt["tag_id"]),
                        None,
                    )
                    if tag_name:
                        add_tag_to_task(existing_id, tag_name)

            for s in data.get("subtasks", []):
                if s.get("task_id") == t["id"]:
                    conn.execute(
                        "INSERT INTO subtasks (task_id, content, finished, position) VALUES (?, ?, ?, ?)",
                        (existing_id, s["content"], s.get("finished", 0), s.get("position", 0)),
                    )

            merged += 1
        else:
            try:
                task = create_task(
                    directory_id=dir_id,
                    name=name,
                    description=t.get("description", ""),
                    deadline=t.get("deadline", "none"),
                    urgency=t.get("urgency", 1),
                    difficulty=t.get("difficulty", 1),
                    time_dedicated=t.get("time_dedicated", 0),
                    repeatable=bool(t.get("repeatable", False)),
                    repeatable_type=t.get("repeatable_type", "none"),
                    has_to_be_completed_to_repeat=bool(t.get("has_to_be_completed_to_repeat", True)),
                    repeat_on_specific_day=t.get("repeat_on_specific_day", "none"),
                    pinned=bool(t.get("pinned", False)),
                )
                old_to_new_task[t["id"]] = task.id
                if t.get("finished"):
                    conn.execute(
                        "UPDATE tasks SET finished = 1, finished_date = ? WHERE id = ?",
                        (t.get("finished_date", "none"), task.id),
                    )
                created += 1
            except ValueError:
                pass

    _import_task_children(data, conn, old_to_new_task)
    conn.commit()

    parts = []
    if merged:
        parts.append(f"{merged} merged")
    if created:
        parts.append(f"{created} created")
    if notes_added:
        parts.append(f"{notes_added} notes added")

    return f"Merged into '{d['name']}' ({', '.join(parts)})"


def import_exported_item(
    path: str,
    current_level_name: str,
    archive_id: int | None = None,
    directory_id: int | None = None,
    task_id: int | None = None,
    merge: bool = False,
) -> str:
    try:
        raw = Path(path).read_text()
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as e:
        return f"Failed to read file: {e}"

    export_type = data.get("export_type")
    if export_type not in ("archive", "directory", "task", "note"):
        return f"Unknown export type '{export_type}'"

    conn = get_conn()
    try:
        if export_type == "archive":
            return _do_import_archive(data, conn)
        elif export_type == "directory":
            if archive_id is None:
                return "Navigate into an archive first, then :importExported"
            if merge:
                return _do_merge_import_directory(data, conn, archive_id)
            return _do_import_directory(data, conn, archive_id)
        elif export_type == "task":
            if directory_id is None:
                return "Navigate into a directory first, then :importExported"
            return _do_import_task(data, conn, directory_id)
        elif export_type == "note":
            if task_id is None:
                return "Select a task first, then :importExported"
            return _do_import_note(data, conn, task_id)
    except Exception as e:
        conn.rollback()
        return f"Import failed: {e}"
