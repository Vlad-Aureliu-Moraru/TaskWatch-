from datetime import date

from .db import get_conn


def log_session(task_id: int | None, duration_seconds: int) -> None:
    if task_id is None:
        return
    conn = get_conn()
    conn.execute(
        "INSERT INTO timer_sessions (task_id, duration_seconds, date) VALUES (?, ?, ?)",
        (task_id, duration_seconds, date.today().isoformat()),
    )
    conn.commit()


def get_total_time_for_task(task_id: int) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT COALESCE(SUM(duration_seconds), 0) FROM timer_sessions WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    return row[0]


def query_sessions(from_date: str | None = None,
                   to_date: str | None = None) -> list[dict]:
    conn = get_conn()
    conditions = []
    params = []
    if from_date is not None:
        conditions.append("ts.date >= ?")
        params.append(from_date)
    if to_date is not None:
        conditions.append("ts.date <= ?")
        params.append(to_date)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    rows = conn.execute(
        f"""SELECT ts.id, ts.task_id, t.name AS task_name,
                   ts.duration_seconds, ts.date
            FROM timer_sessions ts
            LEFT JOIN tasks t ON ts.task_id = t.id
            {where}
            ORDER BY ts.date DESC, ts.id DESC""",
        params,
    ).fetchall()
    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "task_id": r["task_id"],
            "task_name": r["task_name"],
            "duration_seconds": r["duration_seconds"],
            "duration_minutes": r["duration_seconds"] // 60,
            "date": r["date"],
        })
    return result
