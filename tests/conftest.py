import sqlite3
from pathlib import Path
from typing import Generator

import pytest


@pytest.fixture(autouse=True)
def use_tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "test.db"
    # Patch the db module's local references, not paths module
    import taskwatch.db
    monkeypatch.setattr(taskwatch.db, "DB_PATH", db_path)
    monkeypatch.setattr(taskwatch.db, "DATA_DIR", db_path.parent)
    monkeypatch.setattr(taskwatch.db, "_connection", None)


@pytest.fixture
def conn() -> Generator[sqlite3.Connection, None, None]:
    from taskwatch.db import get_conn
    c = get_conn()
    yield c
    c.close()
    import taskwatch.db
    taskwatch.db._connection = None
