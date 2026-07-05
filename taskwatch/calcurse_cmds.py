import re
from datetime import datetime

from . import task_cmds
from .models import Task
from .paths import APTS_FILE, CALCURSE_DIR

TW_TAG = "[TW]"
TW_RE = re.compile(r"^\d{2}/\d{2}/\d{4}.*\|" + re.escape(TW_TAG))


def _date_to_mmddyyyy(d: str) -> str | None:
    if d in (None, "", "none"):
        return None
    try:
        dt = datetime.strptime(d, "%Y-%m-%d")
        return dt.strftime("%m/%d/%Y")
    except ValueError:
        pass
    parts = d.split("/")
    if len(parts) == 3:
        return f"{parts[1]}/{parts[0]}/{parts[2]}"
    return None


def read_apts() -> list[str]:
    if not APTS_FILE.exists():
        return []
    return [line.rstrip("\n") for line in APTS_FILE.read_text().splitlines() if line.strip()]


def task_to_apt_line(task: Task) -> str | None:
    mmddyy = _date_to_mmddyyyy(task.deadline)
    if mmddyy is None:
        return None
    return f"{mmddyy} @ 00:00 -> {mmddyy} @ 23:59|{TW_TAG} {task.name}"


def sync_to_calcurse() -> int:
    tasks = task_cmds.list_tasks()
    new_lines = []
    for t in tasks:
        line = task_to_apt_line(t)
        if line:
            new_lines.append(line)

    existing = read_apts()
    existing = [line for line in existing if not TW_RE.match(line)]

    all_lines = existing + new_lines

    CALCURSE_DIR.mkdir(parents=True, exist_ok=True)
    APTS_FILE.write_text("\n".join(all_lines) + "\n")

    return len(new_lines)
