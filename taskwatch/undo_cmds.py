from .db import get_conn

_undo_stack: list[dict] = []
_MAX_UNDO = 50


def push(action: str, data: dict) -> None:
    _undo_stack.append({"action": action, "data": data})
    if len(_undo_stack) > _MAX_UNDO:
        _undo_stack.pop(0)


def pop() -> dict | None:
    if not _undo_stack:
        return None
    return _undo_stack.pop()


def peek() -> dict | None:
    if not _undo_stack:
        return None
    return _undo_stack[-1]


def clear() -> None:
    _undo_stack.clear()


def restore_task(data: dict) -> bool:
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO tasks
               (id, directory_id, name, description, deadline, urgency, difficulty,
                time_dedicated, repeatable, repeatable_type, finished, finished_date,
                has_to_be_completed_to_repeat, repeat_on_specific_day, position)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["id"], data["directory_id"], data["name"],
                data.get("description", ""), data.get("deadline", "none"),
                data.get("urgency", 1), data.get("difficulty", 1),
                data.get("time_dedicated", 0),
                int(data.get("repeatable", False)),
                data.get("repeatable_type", "none"),
                int(data.get("finished", False)),
                data.get("finished_date", "none"),
                int(data.get("has_to_be_completed_to_repeat", True)),
                data.get("repeat_on_specific_day", "none"),
                data.get("position", 0),
            ),
        )
        conn.commit()
        return True
    except Exception:
        return False


def get_task_data(task_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        return None
    return dict(row)
