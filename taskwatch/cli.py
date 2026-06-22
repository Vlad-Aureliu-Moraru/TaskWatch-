import argparse
import json
import sys
from pathlib import Path
from . import archive_cmds, directory_cmds, io_cmds, note_cmds, tag_cmds, task_cmds, timer
from .db import close

TIMER_FILE_PATH = Path("/tmp/taskwatch_timer.json")
INACTIVE_DATA = {"text": "", "class": "inactive"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="taskwatch")
    sub = parser.add_subparsers(dest="entity", required=True)

    # ── archive ──
    a = sub.add_parser("archive")
    a_sub = a.add_subparsers(dest="action", required=True)
    a_sub.add_parser("list")
    ac = a_sub.add_parser("create")
    ac.add_argument("name")
    ar = a_sub.add_parser("rename")
    ar.add_argument("id", type=int)
    ar.add_argument("name")
    ad = a_sub.add_parser("delete")
    ad.add_argument("id", type=int)

    # ── directory ──
    d = sub.add_parser("directory")
    d_sub = d.add_subparsers(dest="action", required=True)
    dl = d_sub.add_parser("list")
    dl.add_argument("--archive-id", type=int, default=None)
    dc = d_sub.add_parser("create")
    dc.add_argument("archive_id", type=int)
    dc.add_argument("name")
    dr = d_sub.add_parser("rename")
    dr.add_argument("id", type=int)
    dr.add_argument("name")
    dd = d_sub.add_parser("delete")
    dd.add_argument("id", type=int)

    # ── task ──
    t = sub.add_parser("task")
    t_sub = t.add_subparsers(dest="action", required=True)
    tl = t_sub.add_parser("list")
    tl.add_argument("--directory-id", type=int, default=None)
    tc = t_sub.add_parser("create")
    tc.add_argument("directory_id", type=int)
    tc.add_argument("name")
    tc.add_argument("--description", default="")
    tc.add_argument("--deadline", default="none")
    tc.add_argument("--urgency", type=int, default=1)
    tc.add_argument("--difficulty", type=int, default=1)
    tc.add_argument("--time-dedicated", type=int, default=0)
    tc.add_argument("--repeatable", action="store_true")
    tc.add_argument("--repeatable-type", default="none",
                    choices=["daily", "weekly", "biweekly", "monthly", "yearly", "none"])
    tc.add_argument("--must-complete", action="store_true", default=True,
                    help="deadline advances from completion date (default: True)")
    tc.add_argument("--no-must-complete", dest="must_complete", action="store_false")
    tc.add_argument("--repeat-on-day", default="none",
                    help="specific day(s) for repeat (e.g. Mon|Wed|Fri)")

    te = t_sub.add_parser("edit")
    te.add_argument("id", type=int)
    te.add_argument("--name")
    te.add_argument("--description")
    te.add_argument("--deadline")
    te.add_argument("--urgency", type=int)
    te.add_argument("--difficulty", type=int)
    te.add_argument("--repeatable", type=int)
    te.add_argument("--finished", type=int)
    te.add_argument("--repeatable-type",
                    choices=["daily", "weekly", "biweekly", "monthly", "yearly", "none"])
    te.add_argument("--time-dedicated", type=int)
    te.add_argument("--must-complete", dest="has_to_be_completed_to_repeat", type=int)
    te.add_argument("--repeat-on-day")

    tdone = t_sub.add_parser("done")
    tdone.add_argument("id", type=int)
    tdel = t_sub.add_parser("delete")
    tdel.add_argument("id", type=int)
    tupd = t_sub.add_parser("update-repeatables")
    tupd.add_argument("--dry-run", action="store_true", help="show what would be reset without doing it")

    # ── note ──
    n = sub.add_parser("note")
    n_sub = n.add_subparsers(dest="action", required=True)
    nl = n_sub.add_parser("list")
    nl.add_argument("--task-id", type=int, default=None)
    nc = n_sub.add_parser("create")
    nc.add_argument("task_id", type=int)
    nc.add_argument("date")
    nc.add_argument("note")
    nd = n_sub.add_parser("delete")
    nd.add_argument("id", type=int)

    # ── timer ──
    tm = sub.add_parser("timer")
    tm_sub = tm.add_subparsers(dest="action", required=True)
    tms = tm_sub.add_parser("show")
    tms.add_argument("task_id", type=int)

    # ── tag ──
    tg = sub.add_parser("tag")
    tg_sub = tg.add_subparsers(dest="action", required=True)
    tg_sub.add_parser("list")
    tgc = tg_sub.add_parser("create")
    tgc.add_argument("name")
    tgd = tg_sub.add_parser("delete")
    tgd.add_argument("id", type=int)
    tga = tg_sub.add_parser("add")
    tga.add_argument("task_id", type=int)
    tga.add_argument("name")
    tgr = tg_sub.add_parser("remove")
    tgr.add_argument("task_id", type=int)
    tgr.add_argument("name")
    tgt = tg_sub.add_parser("tasks")
    tgt.add_argument("name")

    # ── export / import ──
    ex = sub.add_parser("export")
    ex.add_argument("path", nargs="?", default="/tmp/taskwatch_export.json")
    im = sub.add_parser("import")
    im.add_argument("path")

    # ── tui ──
    sub.add_parser("tui")

    # ── daemon (hidden) ──
    sub.add_parser("daemon", help=argparse.SUPPRESS)

    # ── waybar ──
    sub.add_parser("waybar", help="Output JSON for Waybar timer display")

    return parser


def run(args: list[str] | None = None):
    parser = build_parser()
    opts = parser.parse_args(args)

    entity = opts.entity

    if entity == "tui":
        from .tui import run_tui as tui_run
        tui_run()
        return

    if entity == "daemon":
        from . import timer_daemon
        timer_daemon.main()
        return

    if entity == "waybar":
        _handle_waybar()
        return

    if entity == "export":
        _handle_export(opts)
        return
    if entity == "import":
        _handle_import(opts)
        return

    action = opts.action

    try:
        if entity == "archive":
            _handle_archive(action, opts)
        elif entity == "directory":
            _handle_directory(action, opts)
        elif entity == "task":
            _handle_task(action, opts)
        elif entity == "note":
            _handle_note(action, opts)
        elif entity == "timer":
            _handle_timer(action, opts)
        elif entity == "tag":
            _handle_tag(action, opts)
    finally:
        close()


def _handle_archive(action: str, opts):
    if action == "list":
        for a in archive_cmds.list_archives():
            print(f"{a.id}: {a.name}")
    elif action == "create":
        a = archive_cmds.create_archive(opts.name)
        print(f"Created archive {a.id}: {a.name}")
    elif action == "rename":
        a = archive_cmds.rename_archive(opts.id, opts.name)
        if a:
            print(f"Renamed archive to {a.name}")
        else:
            print(f"Archive {opts.id} not found", file=sys.stderr)
            sys.exit(1)
    elif action == "delete":
        if archive_cmds.delete_archive(opts.id):
            print(f"Deleted archive {opts.id}")
        else:
            print(f"Archive {opts.id} not found", file=sys.stderr)
            sys.exit(1)


def _handle_directory(action: str, opts):
    if action == "list":
        for d in directory_cmds.list_directories(opts.archive_id):
            print(f"{d.id}: (archive {d.archive_id}) {d.name}")
    elif action == "create":
        d = directory_cmds.create_directory(opts.archive_id, opts.name)
        print(f"Created directory {d.id}: {d.name}")
    elif action == "rename":
        d = directory_cmds.rename_directory(opts.id, opts.name)
        if d:
            print(f"Renamed directory to {d.name}")
        else:
            print(f"Directory {opts.id} not found", file=sys.stderr)
            sys.exit(1)
    elif action == "delete":
        if directory_cmds.delete_directory(opts.id):
            print(f"Deleted directory {opts.id}")
        else:
            print(f"Directory {opts.id} not found", file=sys.stderr)
            sys.exit(1)


def _handle_task(action: str, opts):
    if action == "list":
        for t in task_cmds.list_tasks(opts.directory_id):
            status = "✓" if t.finished else " "
            repeat = f" [repeat:{t.repeatable_type}]" if t.repeatable else ""
            print(f"{t.id}: [{status}] {t.name} (dir {t.directory_id}){repeat}")
    elif action == "create":
        t = task_cmds.create_task(
            opts.directory_id, opts.name, opts.description,
            opts.deadline, opts.urgency, opts.difficulty,
            opts.time_dedicated, opts.repeatable, opts.repeatable_type,
            opts.must_complete, opts.repeat_on_day,
        )
        parts = [f"Created task {t.id}: {t.name}"]
        if t.repeatable:
            parts.append(f"[repeat:{t.repeatable_type}]")
        if t.deadline != "none":
            parts.append(f"[deadline:{t.deadline}]")
        print(" ".join(parts))
    elif action == "edit":
        kwargs = {k: getattr(opts, k) for k in
                  ["name", "description", "deadline", "urgency", "difficulty",
                   "repeatable", "finished", "repeatable_type", "time_dedicated",
                   "has_to_be_completed_to_repeat", "repeat_on_specific_day"]
                  if getattr(opts, k) is not None}
        t = task_cmds.edit_task(opts.id, **kwargs)
        if t:
            print(f"Updated task {t.id}")
        else:
            print(f"Task {opts.id} not found", file=sys.stderr)
            sys.exit(1)
    elif action == "done":
        t = task_cmds.mark_done(opts.id)
        if t:
            msg = f"Marked task {t.id} done"
            if t.repeatable and t.deadline != "none":
                msg += f" (next deadline: {t.deadline})"
            print(msg)
        else:
            print(f"Task {opts.id} not found", file=sys.stderr)
            sys.exit(1)
    elif action == "delete":
        if task_cmds.delete_task(opts.id):
            print(f"Deleted task {opts.id}")
        else:
            print(f"Task {opts.id} not found", file=sys.stderr)
            sys.exit(1)
    elif action == "update-repeatables":
        count = task_cmds.reset_overdue_repeatables()
        if count:
            print(f"Reset {count} overdue repeatable task(s)")
        else:
            print("No overdue repeatable tasks to reset")


def _handle_note(action: str, opts):
    if action == "list":
        for n in note_cmds.list_notes(opts.task_id):
            print(f"{n.id}: [{n.date}] {n.note}")
    elif action == "create":
        n = note_cmds.create_note(opts.task_id, opts.date, opts.note)
        print(f"Created note {n.id}")
    elif action == "delete":
        if note_cmds.delete_note(opts.id):
            print(f"Deleted note {opts.id}")
        else:
            print(f"Note {opts.id} not found", file=sys.stderr)
            sys.exit(1)


def _handle_timer(action: str, opts):
    if action == "show":
        task = task_cmds.get_task(opts.task_id)
        if not task:
            print(f"Task {opts.task_id} not found", file=sys.stderr)
            sys.exit(1)
        print(timer.format_schedule(task))


def _handle_export(opts):
    if io_cmds.export_data(opts.path):
        print(f"Exported to {opts.path}")
    else:
        print("Export failed", file=sys.stderr)
        sys.exit(1)


def _handle_import(opts):
    result = io_cmds.import_data(opts.path)
    print(result)
    if "failed" in result:
        sys.exit(1)


def _handle_waybar():
    try:
        with open(TIMER_FILE_PATH) as f:
            data = json.load(f)
        sys.stdout.write(json.dumps(data) + "\n")
        sys.stdout.flush()
    except (OSError, json.JSONDecodeError):
        sys.stdout.write(json.dumps(INACTIVE_DATA) + "\n")
        sys.stdout.flush()


def _handle_tag(action: str, opts):
    if action == "list":
        for t in tag_cmds.list_tags():
            print(f"{t.id}: {t.name}")
    elif action == "create":
        t = tag_cmds.create_tag(opts.name)
        print(f"Created tag {t.id}: {t.name}")
    elif action == "delete":
        if tag_cmds.delete_tag(opts.id):
            print(f"Deleted tag {opts.id}")
        else:
            print(f"Tag {opts.id} not found", file=sys.stderr)
            sys.exit(1)
    elif action == "add":
        t = tag_cmds.add_tag_to_task(opts.task_id, opts.name)
        if t:
            print(f"Added tag '{t.name}' to task {opts.task_id}")
        else:
            print("Failed to add tag", file=sys.stderr)
            sys.exit(1)
    elif action == "remove":
        if tag_cmds.remove_tag_from_task(opts.task_id, opts.name):
            print(f"Removed tag '{opts.name}' from task {opts.task_id}")
        else:
            print("Tag not found on task", file=sys.stderr)
            sys.exit(1)
    elif action == "tasks":
        task_ids = tag_cmds.get_tasks_by_tag(opts.name)
        if task_ids:
            for tid in task_ids:
                task = task_cmds.get_task(tid)
                if task:
                    print(f"{tid}: {task.name}")
        else:
            print("No tasks with that tag")
