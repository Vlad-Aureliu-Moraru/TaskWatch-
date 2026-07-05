from datetime import date, timedelta

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

    return {
        "total": total,
        "finished": finished,
        "pending": pending,
        "today_completed": today_completed,
        "completed_this_week": completed_this_week,
        "overdue": overdue,
        "total_time": total_time,
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
            "name": f"{r['arch_name']} \u25b8 {r['name']}",
            "total": r["total"],
            "done": r["done"],
            "pct": round((r["done"] / r["total"] * 100) if r["total"] else 0),
        }
        for r in rows
    ]
