from taskwatch.models import Task
from taskwatch.timer import _fmt_duration, compute_schedule, format_schedule


class TestFmtDuration:
    def test_seconds_only(self):
        assert _fmt_duration(45) == "0m45s"

    def test_minutes(self):
        assert _fmt_duration(125) == "2m05s"

    def test_hours(self):
        assert _fmt_duration(3661) == "1h01m01s"


class TestComputeSchedule:
    def test_requires_time_dedicated(self):
        t = Task(id=1, directory_id=1, name="No time", time_dedicated=0)
        result = compute_schedule(t)
        assert "error" in result

    def test_typical_schedule(self):
        t = Task(id=1, directory_id=1, name="Work",
                 time_dedicated=120, urgency=3, difficulty=3)
        result = compute_schedule(t)
        assert "error" not in result
        assert result["total_minutes"] == 120
        assert result["total_seconds"] == 7200
        assert result["difficulty"] == 3
        assert result["urgency"] == 3
        assert result["segment_count"] > 0
        assert result["working_seconds"] > 0
        assert result["segments"][0] == 15  # intro

    def test_urgency_difficulty_clamping(self):
        t = Task(id=1, directory_id=1, name="Out of range",
                 time_dedicated=60, urgency=10, difficulty=10)
        result = compute_schedule(t)
        assert "error" not in result
        assert result["urgency"] == 5
        assert result["difficulty"] == 5

    def test_negative_clamping(self):
        t = Task(id=1, directory_id=1, name="Negative",
                 time_dedicated=60, urgency=0, difficulty=0)
        result = compute_schedule(t)
        assert "error" not in result
        assert result["urgency"] == 1
        assert result["difficulty"] == 1

    def test_segments_structure(self):
        t = Task(id=1, directory_id=1, name="Test",
                 time_dedicated=30, urgency=2, difficulty=2)
        result = compute_schedule(t)
        segs = result["segments"]
        assert len(segs) > 0
        assert sum(segs) == result["total_seconds"]


class TestFormatSchedule:
    def test_error_case(self):
        t = Task(id=1, directory_id=1, name="Error",
                 time_dedicated=0)
        output = format_schedule(t)
        assert output.startswith("Error:")

    def test_valid_schedule_output(self):
        t = Task(id=1, directory_id=1, name="My Task",
                 time_dedicated=60, urgency=2, difficulty=2)
        output = format_schedule(t)
        assert "My Task" in output
        assert "Total time:" in output
        assert "Segments:" in output
