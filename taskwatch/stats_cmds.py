from datetime import date, datetime, timedelta

from .db import get_conn


def compute_stats() -> dict:
    conn = get_conn()

    total = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    finished = conn.execute("SELECT COUNT(*) FROM tasks WHERE finished = 1").fetchone()[0]
    pending = total - finished

    today = date.today()
    today_str = today.isoformat()
    today_completed = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE finished = 1 AND finished_date = ?",
        (today_str,),
    ).fetchone()[0]

    monday = (today - timedelta(days=today.weekday())).isoformat()
    completed_this_week = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE finished = 1 AND finished_date >= ?",
        (monday,),
    ).fetchone()[0]

    overdue = conn.execute(
        """SELECT COUNT(*) FROM tasks
           WHERE finished = 0 AND deadline != 'none' AND deadline < ?""",
        (today_str,),
    ).fetchone()[0]

    total_time = conn.execute(
        "SELECT COALESCE(SUM(time_dedicated), 0) FROM tasks"
    ).fetchone()[0]

    timer_minutes_today = conn.execute(
        "SELECT COALESCE(SUM(duration_seconds), 0) FROM timer_sessions WHERE date = ?",
        (today_str,),
    ).fetchone()[0] // 60

    focus_score = (today_completed * 10) + timer_minutes_today

    completion_pct = round((finished / total * 100) if total else 0)

    total_tags = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]

    # Urgency × Difficulty heatmap (pending tasks only)
    ud_grid = [[0] * 5 for _ in range(5)]
    for r in conn.execute(
        "SELECT urgency, difficulty, COUNT(*) AS c FROM tasks WHERE finished = 0 GROUP BY urgency, difficulty"
    ):
        ud_grid[r["urgency"] - 1][r["difficulty"] - 1] = r["c"]

    # Deadline timeline (pending tasks only)
    sunday_dt = today - timedelta(days=today.weekday()) + timedelta(days=6)
    next_sunday_dt = sunday_dt + timedelta(days=7)
    sunday_str = sunday_dt.isoformat()
    next_sunday_str = next_sunday_dt.isoformat()
    today_due = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE finished = 0 AND deadline = ?",
        (today_str,),
    ).fetchone()[0]
    this_week = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE finished = 0 AND deadline > ? AND deadline <= ?",
        (today_str, sunday_str),
    ).fetchone()[0]
    next_week = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE finished = 0 AND deadline > ? AND deadline <= ?",
        (sunday_str, next_sunday_str),
    ).fetchone()[0]
    later = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE finished = 0 AND deadline > ?",
        (next_sunday_str,),
    ).fetchone()[0]
    no_deadline = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE finished = 0 AND deadline = 'none'"
    ).fetchone()[0]

    # Archive stats
    archive_stats_list = []
    for r in conn.execute(
        """SELECT a.name, COUNT(t.id) AS total,
                  SUM(CASE WHEN t.finished THEN 1 ELSE 0 END) AS done,
                  COALESCE(SUM(t.time_dedicated), 0) AS time_budget
           FROM archives a
           LEFT JOIN directories d ON d.archive_id = a.id
           LEFT JOIN tasks t ON t.directory_id = d.id
           GROUP BY a.id
           ORDER BY a.name"""
    ):
        archive_stats_list.append({
            "name": r["name"],
            "total": r["total"],
            "done": r["done"],
            "pct": round((r["done"] / r["total"] * 100) if r["total"] else 0),
            "time_budget": r["time_budget"],
        })

    streak = get_completion_streak()

    return {
        "total": total,
        "finished": finished,
        "pending": pending,
        "today_completed": today_completed,
        "completed_this_week": completed_this_week,
        "overdue": overdue,
        "total_time": total_time,
        "timer_minutes_today": timer_minutes_today,
        "focus_score": focus_score,
        "streak": streak,
        "completion_pct": completion_pct,
        "total_tags": total_tags,
        "ud_grid": ud_grid,
        "deadline_timeline": {
            "overdue": overdue,
            "due_today": today_due,
            "this_week": this_week,
            "next_week": next_week,
            "later": later,
            "no_deadline": no_deadline,
        },
        "archive_stats": archive_stats_list,
    }


def get_completion_heatmap(weeks: int = 12) -> list[list[int]]:
    conn = get_conn()
    today = date.today()
    start = today - timedelta(weeks=weeks - 1)
    monday = start - timedelta(days=start.weekday())
    rows = conn.execute(
        "SELECT finished_date, COUNT(*) AS c FROM tasks WHERE finished = 1 AND finished_date >= ? GROUP BY finished_date",
        (monday.isoformat(),),
    ).fetchall()
    daily_counts = {r["finished_date"]: r["c"] for r in rows}

    grid: list[list[int]] = [[0] * weeks for _ in range(7)]
    for col in range(weeks):
        week_start = monday + timedelta(weeks=col)
        for day in range(7):
            d = (week_start + timedelta(days=day)).isoformat()
            grid[day][col] = daily_counts.get(d, 0)
    return grid


def get_completion_streak() -> int:
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT finished_date FROM tasks WHERE finished = 1 AND finished_date != 'none' ORDER BY finished_date DESC"
    ).fetchall()
    dates = [r["finished_date"] for r in rows]
    if not dates:
        return 0

    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    if dates[0] == today:
        start = today
    elif dates[0] == yesterday:
        start = yesterday
    else:
        return 0

    streak = 0
    cur = datetime.strptime(start, "%Y-%m-%d").date()
    date_set = set(dates)
    while cur.isoformat() in date_set:
        streak += 1
        cur -= timedelta(days=1)
    return streak


def directory_stats(directory_id: int) -> tuple[int, int]:
    conn = get_conn()
    total = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE directory_id = ?",
        (directory_id,),
    ).fetchone()[0]
    finished = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE directory_id = ? AND finished = 1",
        (directory_id,),
    ).fetchone()[0]
    return (total, finished)


def all_directory_stats() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT d.name, a.name AS arch_name,
                  COUNT(t.id) AS total,
                  SUM(CASE WHEN t.finished THEN 1 ELSE 0 END) AS done
           FROM directories d
           JOIN archives a ON d.archive_id = a.id
           LEFT JOIN tasks t ON t.directory_id = d.id
           GROUP BY d.id
           ORDER BY done * 1.0 / MAX(total, 1) DESC"""
    ).fetchall()
    return [
        {
            "name": f"{r['arch_name']} > {r['name']}",
            "total": r["total"],
            "done": r["done"],
            "pct": round((r["done"] / r["total"] * 100) if r["total"] else 0),
        }
        for r in rows
    ]
