import pytest

from taskwatch.archive_cmds import create_archive
from taskwatch.directory_cmds import create_directory
from taskwatch.task_cmds import (
    VALID_REPEAT_TYPES,
    _clamp,
    _display_date,
    _normalize_date,
    _validate_repeatable_type,
    create_task,
    delete_task,
    edit_task,
    get_task,
    list_tasks,
    mark_done,
)


class TestClamp:
    def test_within_range(self):
        assert _clamp(3, 1, 5, "x") == 3

    def test_below_min(self):
        with pytest.raises(ValueError):
            _clamp(0, 1, 5, "x")

    def test_above_max(self):
        with pytest.raises(ValueError):
            _clamp(6, 1, 5, "x")


class TestNormalizeDate:
    def test_none(self):
        assert _normalize_date("none") == "none"

    def test_iso_format(self):
        assert _normalize_date("2026-07-04") == "2026-07-04"

    def test_dmy_format(self):
        assert _normalize_date("04/07/2026") == "2026-07-04"

    def test_invalid(self):
        with pytest.raises(ValueError):
            _normalize_date("not-a-date")


class TestDisplayDate:
    def test_none_values(self):
        assert _display_date("none") == "\u2014"
        assert _display_date(None) == "\u2014"
        assert _display_date("") == "\u2014"

    def test_iso_to_dmy(self):
        assert _display_date("2026-07-04") == "04/07/2026"


class TestValidateRepeatableType:
    def test_valid_types(self):
        for t in VALID_REPEAT_TYPES:
            _validate_repeatable_type(t)

    def test_case_insensitive(self):
        assert _validate_repeatable_type("WEEKLY") == "weekly"

    def test_invalid(self):
        with pytest.raises(ValueError):
            _validate_repeatable_type("invalid")

    def test_error_message(self):
        with pytest.raises(ValueError) as exc:
            _validate_repeatable_type("bogus")
        assert "bogus" in str(exc.value)


class TestTaskCrud:
    def _setup(self, conn):
        create_archive("Test Archive")
        create_directory(1, "Test Dir")

    def test_create_task(self, conn):
        self._setup(conn)
        t = create_task(1, "My Task", deadline="2026-12-31",
                        urgency=3, difficulty=2, time_dedicated=60)
        assert t.id == 1
        assert t.name == "My Task"
        assert t.urgency == 3
        assert t.difficulty == 2
        assert t.time_dedicated == 60
        assert t.deadline == "2026-12-31"

    def test_create_repeatable_task(self, conn):
        self._setup(conn)
        t = create_task(1, "Daily", repeatable=True, repeatable_type="daily")
        assert t.repeatable is True
        assert t.repeatable_type == "daily"

    def test_create_duplicate_raises(self, conn):
        self._setup(conn)
        create_task(1, "Unique")
        with pytest.raises(ValueError):
            create_task(1, "Unique")

    def test_list_tasks(self, conn):
        self._setup(conn)
        create_task(1, "A")
        create_task(1, "B")
        tasks = list_tasks(directory_id=1)
        assert len(tasks) == 2
        assert [t.name for t in tasks] == ["A", "B"]

    def test_list_tasks_finished_filter(self, conn):
        self._setup(conn)
        create_task(1, "A")
        t = create_task(1, "B")
        mark_done(t.id)
        pending = list_tasks(directory_id=1, finished=False)
        done = list_tasks(directory_id=1, finished=True)
        assert len(pending) == 1
        assert len(done) == 1

    def test_get_task(self, conn):
        self._setup(conn)
        create_task(1, "Find Me")
        t = get_task(1)
        assert t is not None
        assert t.name == "Find Me"

    def test_get_missing_task(self, conn):
        assert get_task(999) is None

    def test_edit_task(self, conn):
        self._setup(conn)
        create_task(1, "Old Name")
        updated = edit_task(1, name="New Name", urgency=5)
        assert updated is not None
        assert updated.name == "New Name"
        assert updated.urgency == 5

    def test_mark_done(self, conn):
        self._setup(conn)
        create_task(1, "Do It")
        done = mark_done(1)
        assert done is not None
        assert done.finished is True
        t = get_task(1)
        assert t.finished is True
        assert t.finished_date != "none"

    def test_mark_done_repeatable(self, conn):
        self._setup(conn)
        t = create_task(1, "Repeat", deadline="2026-07-06",
                        repeatable=True, repeatable_type="daily")
        done = mark_done(t.id)
        assert done.finished is True
        assert done.deadline >= "2026-07-06"

    def test_mark_done_repeatable_advances(self, conn):
        self._setup(conn)
        t = create_task(1, "Weekly", deadline="2026-07-06",
                        repeatable=True, repeatable_type="weekly")
        done = mark_done(t.id)
        assert done.finished is True
        assert done.deadline > "2026-07-06"

    def test_delete_task(self, conn):
        self._setup(conn)
        create_task(1, "Delete Me")
        assert delete_task(1) is True
        assert get_task(1) is None
