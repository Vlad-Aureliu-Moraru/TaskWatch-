import calendar
import re
import sqlite3
from datetime import date, datetime, timedelta

from .db import get_conn
from .models import Task

DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NATURAL_DAY_RE = re.compile(r"^in\s+(\d+)\s+days?\s*$", re.IGNORECASE)
NATURAL_WEEK_RE = re.compile(r"^in\s+(\d+)\s+weeks?\s*$", re.IGNORECASE)
WEEKDAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
VALID_REPEAT_TYPES = {"daily", "weekly", "biweekly", "monthly", "yearly", "none"}


def parse_natural_date(text: str) -> str | None:
    t = text.strip().lower()
    today = date.today()

    if t in ("today", "tdy"):
        return today.isoformat()
    if t in ("tomorrow", "tmr", "tmrw"):
        return (today + timedelta(days=1)).isoformat()
    if t in ("next week", "nxt wk"):
        return (today + timedelta(weeks=1)).isoformat()
    if t in ("next month", "nxt mth"):
        next_month = today.month + 1
        year = today.year + (next_month - 1) // 12
        month = (next_month - 1) % 12 + 1
        day = min(today.day, calendar.monthrange(year, month)[1])
        return date(year, month, day).isoformat()

    m = NATURAL_DAY_RE.match(t)
    if m:
        return (today + timedelta(days=int(m.group(1)))).isoformat()

    m = NATURAL_WEEK_RE.match(t)
    if m:
        return (today + timedelta(weeks=int(m.group(1)))).isoformat()

    if t.startswith("next "):
        day_name = t[5:].strip()
        if day_name in WEEKDAY_NAMES:
            target = WEEKDAY_NAMES.index(day_name)
            days_ahead = target - today.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            return (today + timedelta(days=days_ahead)).isoformat()

    return None


def _clamp(val: int, lo: int, hi: int, name: str) -> int:
    if val < lo or val > hi:
        raise ValueError(f"{name} must be between {lo} and {hi}, got {val}")
    return val


def _normalize_date(val: str) -> str:
    if val == "none":
        return val
    if ISO_DATE_RE.match(val):
        datetime.strptime(val, "%Y-%m-%d")
        return val
    if DATE_RE.match(val):
        return datetime.strptime(val, "%d/%m/%Y").strftime("%Y-%m-%d")
    parsed = parse_natural_date(val)
    if parsed is not None:
        return parsed
    raise ValueError(f"date must be dd/MM/yyyy, yyyy-mm-dd, natural language, or 'none', got '{val}'")


def _display_date(val: str) -> str:
    if val in (None, "", "none"):
        return "\u2014"
    try:
        return datetime.strptime(val, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return val


def _deadline_diff(date_str: str) -> int | None:
    if date_str in (None, "", "none"):
        return None
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    return (dt - date.today()).days


def relative_deadline(date_str: str) -> str:
    diff = _deadline_diff(date_str)
    if diff is None:
        return ""
    if diff < 0:
        days = abs(diff)
        if days == 1:
            return "Overdue by 1 day"
        return f"Overdue by {days} days"
    if diff == 0:
        return "Due today"
    if diff == 1:
        return "Due tomorrow"
    if diff < 7:
        return f"Due in {diff} days"
    weeks = diff // 7
    if diff % 7 == 0:
        if weeks == 1:
            return "Due in 1 week"
        return f"Due in {weeks} weeks"
    return f"Due in {diff} days"


def _validate_repeatable_type(val: str) -> str:
    if val.lower() not in VALID_REPEAT_TYPES:
        raise ValueError(f"repeatable_type must be one of {VALID_REPEAT_TYPES}, got '{val}'")
    return val.lower()


VALID_ORDER_COLUMNS = {"urgency", "difficulty", "name", "deadline", "id", "time_dedicated"}


def _order_clause(order_by: str | None, order_dir: str, prefix: str = "") -> str:
    p = f"{prefix}." if prefix else ""
    if order_by is not None and order_by in VALID_ORDER_COLUMNS:
        dir_sql = "DESC" if order_dir.lower() == "desc" else "ASC"
        return f"ORDER BY {p}{order_by} {dir_sql}"
    return f"ORDER BY {p}pinned DESC, {p}position, {p}id"


def list_tasks(directory_id: int | None = None,
               finished: bool | None = None,
               order_by: str | None = None,
               order_dir: str = "asc") -> list[Task]:
    conn = get_conn()
    conditions = []
    params = []
    if directory_id is not None:
        conditions.append("directory_id = ?")
        params.append(directory_id)
    if finished is not None:
        conditions.append("finished = ?")
        params.append(1 if finished else 0)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    sql = f"SELECT * FROM tasks {where} {_order_clause(order_by, order_dir)}"
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_task(r) for r in rows]


def get_task(task_id: int) -> Task | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_task(row) if row else None


def get_tasks_by_ids(task_ids: list[int]) -> dict[int, Task]:
    if not task_ids:
        return {}
    conn = get_conn()
    result: dict[int, Task] = {}
    for r in conn.execute(
        f"SELECT * FROM tasks WHERE id IN ({','.join('?' for _ in task_ids)})",
        task_ids,
    ):
        t = _row_to_task(r)
        result[t.id] = t
    return result


def create_task(
    directory_id: int,
    name: str,
    description: str = "",
    deadline: str = "none",
    urgency: int = 1,
    difficulty: int = 1,
    time_dedicated: int = 0,
    repeatable: bool = False,
    repeatable_type: str = "none",
    has_to_be_completed_to_repeat: bool = True,
    repeat_on_specific_day: str = "none",
    pinned: bool = False,
) -> Task:
    _clamp(urgency, 1, 5, "urgency")
    _clamp(difficulty, 1, 5, "difficulty")
    _clamp(time_dedicated, 0, 99999, "time_dedicated")
    deadline = _normalize_date(deadline)
    repeatable_type = _validate_repeatable_type(repeatable_type)

    if repeatable and repeatable_type == "none":
        raise ValueError("repeatable_type is required when repeatable is True")

    conn = get_conn()
    try:
        max_pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) FROM tasks WHERE directory_id = ?",
            (directory_id,),
        ).fetchone()[0]
        cur = conn.execute(
            """INSERT INTO tasks
               (directory_id, name, description, deadline, urgency, difficulty,
                time_dedicated, repeatable, repeatable_type, has_to_be_completed_to_repeat,
                repeat_on_specific_day, position, pinned)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (directory_id, name, description, deadline, urgency, difficulty,
             time_dedicated, int(repeatable), repeatable_type, int(has_to_be_completed_to_repeat),
             repeat_on_specific_day, max_pos + 1, int(pinned)),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise ValueError(f"Task '{name}' already exists in this directory")
    return Task(id=cur.lastrowid, directory_id=directory_id, name=name,
                description=description, deadline=deadline, urgency=urgency,
                difficulty=difficulty, time_dedicated=time_dedicated,
                repeatable=repeatable, repeatable_type=repeatable_type,
                has_to_be_completed_to_repeat=has_to_be_completed_to_repeat,
                repeat_on_specific_day=repeat_on_specific_day,
                pinned=pinned)


def edit_task(task_id: int, **kwargs) -> Task | None:
    allowed = {"name", "description", "deadline", "urgency", "difficulty",
               "repeatable", "finished", "repeatable_type", "time_dedicated",
               "has_to_be_completed_to_repeat", "repeat_on_specific_day",
               "pinned"}
    updates = {}
    for k, v in kwargs.items():
        if k not in allowed or v is None:
            continue
        if k == "deadline":
            v = _normalize_date(v)
        elif k == "urgency":
            v = _clamp(v, 1, 5, "urgency")
        elif k == "difficulty":
            v = _clamp(v, 1, 5, "difficulty")
        elif k == "time_dedicated":
            v = _clamp(v, 0, 99999, "time_dedicated")
        elif k == "repeatable":
            v = int(v)
        elif k == "finished":
            v = int(v)
        elif k == "repeatable_type":
            v = _validate_repeatable_type(v)
        elif k == "has_to_be_completed_to_repeat":
            v = int(v)
        elif k == "pinned":
            v = int(v)
        updates[k] = v

    if "finished" in updates and updates["finished"] and "finished_date" not in updates:
        updates["finished_date"] = date.today().isoformat()

    if not updates:
        return None

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [task_id]
    conn = get_conn()
    cur = conn.execute(
        f"UPDATE tasks SET {set_clause} WHERE id = ?", vals
    )
    conn.commit()
    if cur.rowcount == 0:
        return None
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_task(row) if row else None


_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _first_weekday_of_month(year: int, month: int, weekday: int) -> int:
    for d in range(1, calendar.monthrange(year, month)[1] + 1):
        if date(year, month, d).weekday() == weekday:
            return d
    return 1


def advance_deadline(task: Task, reset_finished: bool = True) -> Task | None:
    if not task.repeatable or task.repeatable_type == "none":
        return None

    try:
        if task.has_to_be_completed_to_repeat:
            base_str = task.finished_date if task.finished_date != "none" else date.today().isoformat()
        else:
            base_str = task.deadline

        if base_str == "none":
            base_str = date.today().isoformat()

        base = date.fromisoformat(base_str)
    except (ValueError, TypeError):
        return None

    rtype = task.repeatable_type.lower()
    specific_day = task.repeat_on_specific_day
    target_wd = _WEEKDAYS.get(specific_day.lower()) if specific_day and specific_day != "none" else None

    if target_wd is not None and rtype != "daily":
        if rtype == "weekly":
            days_ahead = (target_wd - base.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            new_deadline = (base + timedelta(days=days_ahead)).isoformat()
        elif rtype == "biweekly":
            days_ahead = (target_wd - base.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            new_deadline = (base + timedelta(days=days_ahead + 7)).isoformat()
        elif rtype == "monthly":
            y = base.year + (base.month) // 12
            m = (base.month % 12) + 1
            d = _first_weekday_of_month(y, m, target_wd)
            new_deadline = date(y, m, d).isoformat()
        elif rtype == "yearly":
            y = base.year + 1
            d = _first_weekday_of_month(y, base.month, target_wd)
            new_deadline = date(y, base.month, d).isoformat()
        else:
            return None
    else:
        if rtype == "daily":
            new_deadline = (base + timedelta(days=1)).isoformat()
        elif rtype == "weekly":
            new_deadline = (base + timedelta(weeks=1)).isoformat()
        elif rtype == "biweekly":
            new_deadline = (base + timedelta(weeks=2)).isoformat()
        elif rtype == "monthly":
            y = base.year + (base.month) // 12
            m = (base.month % 12) + 1
            last = calendar.monthrange(y, m)[1]
            d = min(base.day, last)
            new_deadline = date(y, m, d).isoformat()
        elif rtype == "yearly":
            try:
                new_deadline = base.replace(year=base.year + 1).isoformat()
            except ValueError:
                new_deadline = date(base.year + 1, 3, 1).isoformat()
        else:
            return None

    conn = get_conn()
    if reset_finished:
        cur = conn.execute(
            "UPDATE tasks SET deadline = ?, finished = 0, finished_date = 'none' WHERE id = ?",
            (new_deadline, task.id),
        )
    else:
        cur = conn.execute(
            "UPDATE tasks SET deadline = ? WHERE id = ?",
            (new_deadline, task.id),
        )
    conn.commit()
    if cur.rowcount == 0:
        return None
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task.id,)).fetchone()
    return _row_to_task(row) if row else None


def set_task_finished(task_id: int, finished: bool) -> Task | None:
    conn = get_conn()
    today = date.today().isoformat() if finished else "none"
    cur = conn.execute(
        "UPDATE tasks SET finished = ?, finished_date = ? WHERE id = ?",
        (1 if finished else 0, today, task_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        return None
    task = get_task(task_id)
    if finished and task and task.repeatable and task.repeatable_type != "none":
        reset = task.deadline == "none"
        task = advance_deadline(task, reset_finished=reset)
    return task if task else None


def mark_done(task_id: int) -> Task | None:
    return set_task_finished(task_id, True)


def mark_not_done(task_id: int) -> Task | None:
    return set_task_finished(task_id, False)


def _advance_until_current(task: Task) -> bool:
    today = date.today().isoformat()
    advanced = False
    for _ in range(50):
        if task.deadline == "none" or task.deadline >= today:
            break
        new_task = advance_deadline(task, reset_finished=False)
        if new_task is None:
            break
        task = new_task
        advanced = True
    return advanced


def reset_overdue_repeatables() -> int:
    today = date.today().isoformat()
    conn = get_conn()
    count = 0

    rows = conn.execute(
        "SELECT * FROM tasks WHERE repeatable = 1 AND finished = 1 AND deadline != 'none' AND deadline <= ?",
        (today,),
    ).fetchall()
    for r in rows:
        task = _row_to_task(r)
        if advance_deadline(task):
            count += 1

    rows = conn.execute(
        (
            "SELECT * FROM tasks WHERE repeatable = 1 AND finished = 0 AND "
            "has_to_be_completed_to_repeat = 0 AND deadline != 'none' AND deadline <= ?"
        ),
        (today,),
    ).fetchall()
    for r in rows:
        task = _row_to_task(r)
        if _advance_until_current(task):
            count += 1

    return count


def delete_task(task_id: int) -> bool:
    conn = get_conn()
    cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    return cur.rowcount > 0


def _would_create_cycle(task_id: int, depends_on_id: int) -> bool:
    visited = {task_id}
    stack = [depends_on_id]
    while stack:
        current = stack.pop()
        if current == task_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        for dep_id in get_dependencies(current):
            stack.append(dep_id)
    return False


def add_dependency(task_id: int, depends_on_id: int) -> bool:
    if task_id == depends_on_id:
        raise ValueError("A task cannot depend on itself")
    if _would_create_cycle(task_id, depends_on_id):
        raise ValueError("Circular dependency detected")
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO task_dependencies (task_id, depends_on_task_id) VALUES (?, ?)",
            (task_id, depends_on_id),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def remove_dependency(task_id: int, depends_on_id: int) -> bool:
    conn = get_conn()
    cur = conn.execute(
        "DELETE FROM task_dependencies WHERE task_id = ? AND depends_on_task_id = ?",
        (task_id, depends_on_id),
    )
    conn.commit()
    return cur.rowcount > 0


def get_dependencies(task_id: int) -> list[int]:
    conn = get_conn()
    return [
        r["depends_on_task_id"]
        for r in conn.execute(
            "SELECT depends_on_task_id FROM task_dependencies WHERE task_id = ?",
            (task_id,),
        )
    ]


def get_dependents(task_id: int) -> list[int]:
    conn = get_conn()
    return [
        r["task_id"]
        for r in conn.execute(
            "SELECT task_id FROM task_dependencies WHERE depends_on_task_id = ?",
            (task_id,),
        )
    ]


def is_blocked(task_id: int) -> bool:
    dep_ids = get_dependencies(task_id)
    if not dep_ids:
        return False
    conn = get_conn()
    unfinished = conn.execute(
        f"SELECT COUNT(*) FROM tasks WHERE id IN ({','.join('?' for _ in dep_ids)}) AND finished = 0",
        dep_ids,
    ).fetchone()[0]
    return unfinished > 0


def get_blocked_ids(task_ids: list[int]) -> set[int]:
    if not task_ids:
        return set()
    conn = get_conn()
    unfinished_ids = {
        r["id"]
        for r in conn.execute(
            f"SELECT id FROM tasks WHERE id IN ({','.join('?' for _ in task_ids)}) AND finished = 0",
            task_ids,
        )
    }
    if not unfinished_ids:
        return set()
    deps: dict[int, list[int]] = {}
    all_dep_ids: set[int] = set()
    for r in conn.execute(
        f"SELECT task_id, depends_on_task_id FROM task_dependencies WHERE task_id IN ({','.join('?' for _ in unfinished_ids)})",
        list(unfinished_ids),
    ):
        deps.setdefault(r["task_id"], []).append(r["depends_on_task_id"])
        all_dep_ids.add(r["depends_on_task_id"])
    if not all_dep_ids:
        return set()
    unfinished_deps = {
        r["id"]
        for r in conn.execute(
            f"SELECT id FROM tasks WHERE id IN ({','.join('?' for _ in all_dep_ids)}) AND finished = 0",
            list(all_dep_ids),
        )
    }
    return {
        tid
        for tid, dep_list in deps.items()
        if any(did in unfinished_deps for did in dep_list)
    }


def get_overdue_tasks() -> list[Task]:
    today_str = date.today().isoformat()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE finished = 0 AND deadline != 'none' AND deadline < ?",
        (today_str,),
    ).fetchall()
    return [_row_to_task(r) for r in rows]


def get_tasks_due_on(date_str: str) -> list[Task]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE finished = 0 AND deadline = ?",
        (date_str,),
    ).fetchall()
    return [_row_to_task(r) for r in rows]



def get_tasks_for_week() -> list[dict]:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    monday_str = monday.isoformat()
    sunday_str = sunday.isoformat()
    conn = get_conn()
    rows = conn.execute(
        """SELECT t.*, d.name AS dir_name, a.name AS arch_name
           FROM tasks t
           JOIN directories d ON t.directory_id = d.id
           JOIN archives a ON d.archive_id = a.id
           WHERE t.deadline != 'none' AND t.deadline >= ? AND t.deadline <= ?
           ORDER BY t.deadline""",
        (monday_str, sunday_str),
    ).fetchall()
    result = []
    for r in rows:
        task = _row_to_task(r)
        result.append({
            "task": task,
            "dir_name": r["dir_name"],
            "arch_name": r["arch_name"],
        })
    return result


def move_task(task_id: int, new_directory_id: int) -> Task | None:
    conn = get_conn()
    dir_exists = conn.execute(
        "SELECT id FROM directories WHERE id = ?", (new_directory_id,)
    ).fetchone()
    if dir_exists is None:
        return None
    conn.execute(
        "UPDATE tasks SET directory_id = ? WHERE id = ?",
        (new_directory_id, task_id),
    )
    conn.commit()
    return get_task(task_id)


def _move_task_by_direction(task_id: int, direction: str) -> bool:
    conn = get_conn()
    t = conn.execute("SELECT id, directory_id, position FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if t is None:
        return False
    if direction == "up":
        neighbor = conn.execute(
            "SELECT id, position FROM tasks WHERE directory_id = ? AND position < ? ORDER BY position DESC LIMIT 1",
            (t["directory_id"], t["position"]),
        ).fetchone()
    else:
        neighbor = conn.execute(
            "SELECT id, position FROM tasks WHERE directory_id = ? AND position > ? ORDER BY position ASC LIMIT 1",
            (t["directory_id"], t["position"]),
        ).fetchone()
    if neighbor is None:
        return False
    conn.execute("UPDATE tasks SET position = ? WHERE id = ?", (neighbor["position"], task_id))
    conn.execute("UPDATE tasks SET position = ? WHERE id = ?", (t["position"], neighbor["id"]))
    conn.commit()
    return True


def move_task_up(task_id: int) -> bool:
    return _move_task_by_direction(task_id, "up")


def move_task_down(task_id: int) -> bool:
    return _move_task_by_direction(task_id, "down")


def list_all_tasks(archive_id: int,
                   finished: bool | None = None,
                   order_by: str | None = None,
                   order_dir: str = "asc") -> list[dict]:
    conn = get_conn()
    conditions = ["d.archive_id = ?"]
    params = [archive_id]
    if finished is not None:
        conditions.append("t.finished = ?")
        params.append(1 if finished else 0)
    where = "WHERE " + " AND ".join(conditions)
    rows = conn.execute(
        f"""SELECT t.*, d.name AS dir_name
            FROM tasks t
            JOIN directories d ON t.directory_id = d.id
            {where} {_order_clause(order_by, order_dir, "t")}""",
        params,
    ).fetchall()
    result = []
    for r in rows:
        task = _row_to_task(r)
        result.append({"task": task, "dir_name": r["dir_name"]})
    return result


def search_tasks_global(query: str, limit: int = 10) -> list[tuple[Task, str, str]]:
    conn = get_conn()
    like = f"%{query}%"
    rows = conn.execute("""
        SELECT DISTINCT t.*, d.name AS dir_name, a.name AS arch_name
        FROM tasks t
        JOIN directories d ON t.directory_id = d.id
        JOIN archives a ON d.archive_id = a.id
        WHERE LOWER(t.name) LIKE LOWER(?)
           OR LOWER(t.description) LIKE LOWER(?)
        ORDER BY t.pinned DESC, t.position, t.id
        LIMIT ?
    """, (like, like, limit)).fetchall()
    return [(_row_to_task(r), r["dir_name"], r["arch_name"]) for r in rows]


def get_announcements(archive_id: int | None = None) -> list[dict]:
    today_str = date.today().isoformat()
    tomorrow_str = (date.today() + timedelta(days=1)).isoformat()
    conn = get_conn()
    conditions = ["t.finished = 0", "t.deadline != 'none'", "t.deadline <= ?"]
    params = [tomorrow_str]
    if archive_id is not None:
        conditions.append("d.archive_id = ?")
        params.append(archive_id)
    rows = conn.execute(
        f"""SELECT t.*, d.name AS dir_name, a.name AS arch_name
            FROM tasks t
            JOIN directories d ON t.directory_id = d.id
            JOIN archives a ON d.archive_id = a.id
            WHERE {' AND '.join(conditions)}
            ORDER BY
                CASE
                    WHEN t.deadline < ? THEN 0
                    WHEN t.deadline = ? THEN 1
                    ELSE 2
                END,
                t.deadline,
                t.urgency DESC""",
        params + [today_str, today_str],
    ).fetchall()
    return [{"task": _row_to_task(r), "dir_name": r["dir_name"], "arch_name": r["arch_name"]} for r in rows]


def get_tags_for_task_display(task_id: int) -> str:
    from .tag_cmds import get_tags_for_task
    tags = get_tags_for_task(task_id)
    return ", ".join(t.name for t in tags) if tags else ""



def format_deadline_text(deadline: str) -> str:
    diff = _deadline_diff(deadline)
    if diff is None:
        return f"[deadline:{deadline}]" if deadline not in (None, "", "none") else ""
    if diff < 0:
        return f"[overdue {abs(diff)}d]"
    if diff == 0:
        return "[due today]"
    if diff == 1:
        return "[due tomorrow]"
    if diff < 7:
        return f"[due in {diff}d]"
    return f"[deadline:{deadline}]"


def list_unfinished_tasks(directory_id: int) -> list[Task]:
    return list_tasks(directory_id=directory_id, finished=False)


def resolve_directory(name_or_id: str | int) -> int | None:
    from .directory_cmds import get_directory, search_directories_global
    if isinstance(name_or_id, int) or (isinstance(name_or_id, str) and name_or_id.isdigit()):
        dir_id = int(name_or_id)
        if get_directory(dir_id):
            return dir_id
    dirs = search_directories_global(str(name_or_id), limit=1)
    if dirs:
        return dirs[0].id
    return None


def _row_to_task(r) -> Task:
    return Task(
        id=r["id"],
        directory_id=r["directory_id"],
        name=r["name"],
        description=r["description"],
        time_dedicated=r["time_dedicated"],
        repeatable=bool(r["repeatable"]),
        finished=bool(r["finished"]),
        deadline=r["deadline"],
        urgency=r["urgency"],
        repeatable_type=r["repeatable_type"],
        difficulty=r["difficulty"],
        finished_date=r["finished_date"],
        has_to_be_completed_to_repeat=bool(r["has_to_be_completed_to_repeat"]),
        repeat_on_specific_day=r["repeat_on_specific_day"],
        position=r["position"],
        pinned=bool(r["pinned"]),
    )
