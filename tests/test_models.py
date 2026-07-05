from taskwatch.models import Archive, Directory, Note, Tag, Task


class TestArchive:
    def test_creation(self):
        a = Archive(id=1, name="Work")
        assert a.id == 1
        assert a.name == "Work"

    def test_repr(self):
        a = Archive(id=2, name="Personal")
        assert "Archive" in repr(a)


class TestDirectory:
    def test_creation(self):
        d = Directory(id=1, archive_id=1, name="Projects")
        assert d.id == 1
        assert d.archive_id == 1
        assert d.name == "Projects"


class TestTask:
    def test_defaults(self):
        t = Task(id=1, directory_id=1, name="Test Task")
        assert t.description == ""
        assert t.time_dedicated == 0
        assert t.repeatable is False
        assert t.finished is False
        assert t.deadline == "none"
        assert t.urgency == 1
        assert t.difficulty == 1
        assert t.position == 0

    def test_full_construction(self):
        t = Task(
            id=1, directory_id=2, name="Important Task",
            description="Do it", time_dedicated=60,
            repeatable=True, repeatable_type="daily",
            deadline="2026-12-31", urgency=5, difficulty=3,
            finished=True, finished_date="2026-07-04",
            has_to_be_completed_to_repeat=True,
            repeat_on_specific_day="none", position=1,
        )
        assert t.name == "Important Task"
        assert t.repeatable is True
        assert t.urgency == 5

    def test_finished_property(self):
        t = Task(id=1, directory_id=1, name="Done", finished=True)
        assert t.finished is True

    def test_mutable(self):
        t = Task(id=1, directory_id=1, name="A")
        t.name = "B"
        assert t.name == "B"


class TestNote:
    def test_creation(self):
        n = Note(id=1, task_id=1, date="2026-07-04", note="Hello")
        assert n.note == "Hello"
        assert n.file_path is None

    def test_with_file(self):
        n = Note(id=1, task_id=1, date="2026-07-04", note="Hi",
                 file_path="/tmp/note.txt")
        assert n.file_path == "/tmp/note.txt"


class TestTag:
    def test_creation(self):
        t = Tag(id=1, name="urgent")
        assert t.name == "urgent"
