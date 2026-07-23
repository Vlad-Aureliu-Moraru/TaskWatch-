from datetime import date, timedelta

from .db import get_conn


def get_review_data() -> dict:
    today = date.today()
    today_str = today.isoformat()
    yesterday_str = (today - timedelta(days=1)).isoformat()
    tomorrow_str = (today + timedelta(days=1)).isoformat()
    week_start = (today - timedelta(days=today.weekday())).isoformat()

    conn = get_conn()

    overdue = conn.execute(
        """SELECT t.*, d.name AS dir_name, a.name AS arch_name
           FROM tasks t
           JOIN directories d ON t.directory_id = d.id
           JOIN archives a ON d.archive_id = a.id
           WHERE t.finished = 0 AND t.deadline != 'none' AND t.deadline < ?
           ORDER BY t.deadline, t.urgency DESC""",
        (today_str,),
    ).fetchall()

    due_today = conn.execute(
        """SELECT t.*, d.name AS dir_name, a.name AS arch_name
           FROM tasks t
           JOIN directories d ON t.directory_id = d.id
           JOIN archives a ON d.archive_id = a.id
           WHERE t.finished = 0 AND t.deadline = ?
           ORDER BY t.urgency DESC""",
        (today_str,),
    ).fetchall()

    due_tomorrow = conn.execute(
        """SELECT t.*, d.name AS dir_name, a.name AS arch_name
           FROM tasks t
           JOIN directories d ON t.directory_id = d.id
           JOIN archives a ON d.archive_id = a.id
           WHERE t.finished = 0 AND t.deadline = ?
           ORDER BY t.urgency DESC""",
        (tomorrow_str,),
    ).fetchall()

    completed_today = conn.execute(
        """SELECT t.*, d.name AS dir_name, a.name AS arch_name
           FROM tasks t
           JOIN directories d ON t.directory_id = d.id
           JOIN archives a ON d.archive_id = a.id
           WHERE t.finished = 1 AND t.finished_date = ?
           ORDER BY a.name, d.name""",
        (today_str,),
    ).fetchall()

    completed_this_week = conn.execute(
        """SELECT t.*, d.name AS dir_name, a.name AS arch_name
           FROM tasks t
           JOIN directories d ON t.directory_id = d.id
           JOIN archives a ON d.archive_id = a.id
           WHERE t.finished = 1 AND t.finished_date >= ? AND t.finished_date < ?
           ORDER BY t.finished_date DESC""",
        (week_start, today_str),
    ).fetchall()

    total = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    finished = conn.execute("SELECT COUNT(*) FROM tasks WHERE finished = 1").fetchone()[0]
    pending = total - finished

    timer_minutes = conn.execute(
        "SELECT COALESCE(SUM(duration_seconds), 0) FROM timer_sessions WHERE date = ?",
        (today_str,),
    ).fetchone()[0] // 60

    return {
        "overdue": [dict(r) for r in overdue],
        "due_today": [dict(r) for r in due_today],
        "due_tomorrow": [dict(r) for r in due_tomorrow],
        "completed_today": [dict(r) for r in completed_today],
        "completed_this_week": [dict(r) for r in completed_this_week],
        "total": total,
        "finished": finished,
        "pending": pending,
        "timer_minutes_today": timer_minutes,
        "date": today_str,
    }
