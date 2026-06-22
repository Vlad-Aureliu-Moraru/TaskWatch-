import sqlite3
from datetime import datetime
from pathlib import Path

DATA_DIR = Path.home() / ".local" / "share" / "taskwatch"
DB_PATH = DATA_DIR / "taskwatch.db"

_connection: sqlite3.Connection | None = None

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS archives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS directories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    archive_id INTEGER NOT NULL REFERENCES archives(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    UNIQUE(archive_id, name)
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    directory_id INTEGER NOT NULL REFERENCES directories(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    time_dedicated INTEGER DEFAULT 0,
    repeatable INTEGER DEFAULT 0,
    finished INTEGER DEFAULT 0,
    deadline TEXT DEFAULT 'none',
    urgency INTEGER DEFAULT 1,
    repeatable_type TEXT DEFAULT 'none',
    difficulty INTEGER DEFAULT 1,
    finished_date TEXT DEFAULT 'none',
    has_to_be_completed_to_repeat INTEGER DEFAULT 1,
    repeat_on_specific_day TEXT DEFAULT 'none',
    position INTEGER DEFAULT 0,
    UNIQUE(directory_id, name)
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    note TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS task_tags (
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (task_id, tag_id)
);

PRAGMA foreign_keys = ON;
"""


def get_conn() -> sqlite3.Connection:
    global _connection
    if _connection is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _connection = sqlite3.connect(str(DB_PATH))
        _connection.row_factory = sqlite3.Row
        _connection.executescript(SCHEMA_SQL)
        _migrate(_connection)
    return _connection


def _migrate(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ALTER TABLE tasks ADD COLUMN position INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    _migrate_dates(conn)


def _migrate_dates(conn: sqlite3.Connection) -> None:
    for col in ("deadline", "finished_date"):
        rows = conn.execute(
            f"SELECT id, {col} FROM tasks WHERE {col} != 'none'"
        ).fetchall()
        for r in rows:
            val = r[col]
            if val and "-" in val:
                continue
            try:
                dt = datetime.strptime(val, "%d/%m/%Y")
                conn.execute(
                    f"UPDATE tasks SET {col} = ? WHERE id = ?",
                    (dt.strftime("%Y-%m-%d"), r["id"]),
                )
            except (ValueError, TypeError):
                pass
    conn.commit()


def close():
    global _connection
    if _connection is not None:
        _connection.close()
        _connection = None
