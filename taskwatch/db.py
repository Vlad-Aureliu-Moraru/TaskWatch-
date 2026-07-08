import sqlite3
from datetime import datetime

from .paths import DATA_DIR, DB_PATH

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
    note TEXT NOT NULL,
    file_path TEXT,
    created_at TEXT NOT NULL DEFAULT ''
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

CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    depends_on_task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    PRIMARY KEY (task_id, depends_on_task_id)
);

CREATE TABLE IF NOT EXISTS subtasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    finished INTEGER DEFAULT 0,
    position INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS timer_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    duration_seconds INTEGER NOT NULL,
    date TEXT NOT NULL
);
"""


def get_conn() -> sqlite3.Connection:
    global _connection
    if _connection is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _connection = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _connection.row_factory = sqlite3.Row
        _connection.execute("PRAGMA journal_mode=WAL")
        _connection.executescript(SCHEMA_SQL)
        _connection.execute("PRAGMA foreign_keys = ON")
        _migrate(_connection)
    return _connection


IDX_SQL = """
CREATE INDEX IF NOT EXISTS idx_tasks_directory_id ON tasks(directory_id);
CREATE INDEX IF NOT EXISTS idx_tasks_finished ON tasks(finished);
CREATE INDEX IF NOT EXISTS idx_tasks_deadline ON tasks(deadline);
CREATE INDEX IF NOT EXISTS idx_tasks_finished_deadline ON tasks(finished, deadline);
CREATE INDEX IF NOT EXISTS idx_tasks_repeatable ON tasks(repeatable);
CREATE INDEX IF NOT EXISTS idx_directories_archive_id ON directories(archive_id);
CREATE INDEX IF NOT EXISTS idx_notes_task_id ON notes(task_id);
CREATE INDEX IF NOT EXISTS idx_subtasks_task_id ON subtasks(task_id);
CREATE INDEX IF NOT EXISTS idx_task_tags_tag_id ON task_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_task_dependencies_depends ON task_dependencies(depends_on_task_id);
CREATE INDEX IF NOT EXISTS idx_timer_sessions_task_id ON timer_sessions(task_id);
CREATE INDEX IF NOT EXISTS idx_timer_sessions_date ON timer_sessions(date);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ALTER TABLE tasks ADD COLUMN position INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE tasks ADD COLUMN pinned INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE directories ADD COLUMN project_path TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    _migrate_notes(conn)
    _migrate_dates(conn)
    conn.executescript(IDX_SQL)


def _migrate_notes(conn: sqlite3.Connection) -> None:
    for col in ("file_path", "created_at"):
        try:
            conn.execute(f"ALTER TABLE notes ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE notes SET created_at = ? WHERE created_at IS NULL OR created_at = ''",
        (now,),
    )
    conn.commit()


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
