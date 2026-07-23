import argparse
import json
import sys
import textwrap
from pathlib import Path

from . import (
    __version__,
    ai_client,
    archive_cmds,
    directory_cmds,
    io_cmds,
    note_cmds,
    stats_cmds,
    subtask_cmds,
    tag_cmds,
    task_cmds,
    timer,
)
from .db import close
from .paths import DATA_DIR, TIMER_FILE_PATH, TIMER_STATE_PATH
from .paths import INACTIVE_TIMER_DATA as INACTIVE_DATA

HELP_EPILOG = """\
Examples:
  taskwatch archive list
  taskwatch archive list --json
  taskwatch directory list
  taskwatch directory list --json
  taskwatch directory tasks Work
  taskwatch directory tasks 3 --json
  taskwatch task list --directory-name Work --unfinished
  taskwatch task list --directory-id 1 --json
  taskwatch task create 1 "Write docs" --deadline tomorrow --urgency 4
  taskwatch task done 5
  taskwatch task done --directory-name Work
  taskwatch note list --task-id 1 --json
  taskwatch note create 1 2026-01-01 "Log entry" --file-path ~/notes/report.pdf
  taskwatch stats
  taskwatch stats --json
  taskwatch help task list
"""

CATEGORY_DESCRIPTIONS = {
    "archive":   "Manage archives (top-level buckets for directories)",
    "directory": "Manage directories (containers for tasks)",
    "task":      "Manage tasks (create, list, edit, complete, delete)",
    "note":      "Manage notes attached to tasks",
    "subtask":   "Manage subtasks within tasks",
    "timer":     "Timer controls and presets (Pomodoro-style sessions)",
    "tag":       "Manage tags and tag-task associations",
    "export":    "Export all data to JSON",
    "import":    "Import data from JSON",
    "tui":       "Launch the terminal user interface",
    "serve":     "Start the HTTP server for phone/remote access",
    "waybar":    "Output JSON for Waybar timer display (for status bars)",
    "stats":     "Show statistics overview or per-directory stats",
    "review":    "Show a comprehensive review (overdue, due soon, completed)",
    "ai":        "AI assistant integration (chat, ask, suggest tasks)",
    "help":      "Show detailed help for a specific command",
}


class _HelpFormatter(argparse.RawDescriptionHelpFormatter):
    pass


def print_global_help(prog: str) -> None:
    text = textwrap.dedent(f"""\
        \x1b[1mTaskWatch+ v{__version__}\x1b[0m \u2014 A system-integrated task tracker

        \x1b[1mUsage:\x1b[0m
          {prog} <command> [<args>]
          {prog} help <command> [<subcommand>]

        \x1b[1mCommands:\x1b[0m
    """)
    categories = [
        ("Core", ["archive", "directory", "task"]),
        ("Task Details", ["note", "subtask", "tag"]),
        ("Statistics", ["stats", "review"]),
        ("Timer", ["timer"]),
        ("AI", ["ai"]),
        ("Data", ["export", "import"]),
        ("Interface", ["tui", "serve", "waybar"]),
        ("Help", ["help"]),
    ]
    for cat_name, cmds in categories:
        text += f"\n  \x1b[1m{cat_name}\x1b[0m:\n"
        for cmd in cmds:
            desc = CATEGORY_DESCRIPTIONS.get(cmd, "")
            text += f"    {cmd:<20} {desc}\n"
    text += f"\n{HELP_EPILOG}"
    print(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="taskwatch", formatter_class=_HelpFormatter, add_help=False)
    parser.add_argument("-h", "--help", action="store_true", dest="show_global_help",
                        help="Show this help message and exit")
    parser.add_argument("--version", action="store_true", dest="show_version",
                        help="Show version and exit")
    sub = parser.add_subparsers(dest="entity")

    # ── archive ──
    a = sub.add_parser("archive", help="Manage archives (top-level buckets)")
    a_sub = a.add_subparsers(dest="action", required=True)
    al = a_sub.add_parser("list", help="List all archives")
    al.add_argument("--json", action="store_true", help="Output JSON")
    ac = a_sub.add_parser("create", help="Create a new archive")
    ac.add_argument("name", help="Archive name")
    ar = a_sub.add_parser("rename", help="Rename an archive")
    ar.add_argument("id", type=int, help="Archive ID")
    ar.add_argument("name", help="New name")
    ad = a_sub.add_parser("delete", help="Delete an archive")
    ad.add_argument("id", type=int, help="Archive ID")

    # ── directory ──
    d = sub.add_parser("directory", help="Manage directories (task containers)")
    d_sub = d.add_subparsers(dest="action", required=True)
    dl = d_sub.add_parser("list", help="List directories [--archive-id]")
    dl.add_argument("--archive-id", type=int, default=None, help="Filter by archive ID")
    dl.add_argument("--json", action="store_true", help="Output JSON")
    dtasks = d_sub.add_parser("tasks", help="List all tasks in a directory (by ID or name)")
    dtasks.add_argument("id_or_name", help="Directory ID or name")
    dtasks.add_argument("--unfinished", action="store_true", help="Show only unfinished tasks")
    dtasks.add_argument("--json", action="store_true", help="Output JSON")
    dc = d_sub.add_parser("create", help="Create a new directory")
    dc.add_argument("archive_id", type=int, help="Archive ID to create under")
    dc.add_argument("name", help="Directory name")
    dr = d_sub.add_parser("rename", help="Rename a directory")
    dr.add_argument("id", type=int, help="Directory ID")
    dr.add_argument("name", help="New name")
    dd = d_sub.add_parser("delete", help="Delete a directory")
    dd.add_argument("id", type=int, help="Directory ID")
    dp = d_sub.add_parser("attach-project", help="Write .taskwatch-directory to a path")
    dp.add_argument("id", type=int, help="Directory ID")
    dp.add_argument("path", help="Filesystem path to attach the project to")

    # ── task ──
    t = sub.add_parser("task", help="Manage tasks")
    t_sub = t.add_subparsers(dest="action", required=True)
    tl = t_sub.add_parser("list", help="List tasks [--directory-id | --directory-name]")
    tl.add_argument("--directory-id", type=int, default=None, help="Filter by directory ID")
    tl.add_argument("--directory-name", default=None, help="Filter by directory name")
    tl.add_argument("--unfinished", action="store_true", help="Show only unfinished tasks")
    tl.add_argument("--finished", action="store_true", help="Show only finished tasks")
    tl.add_argument("--finished-after", default=None, help="Filter by finished_date >= (YYYY-MM-DD)")
    tl.add_argument("--finished-before", default=None, help="Filter by finished_date <= (YYYY-MM-DD)")
    tl.add_argument("--overdue", action="store_true", help="Show only overdue tasks")
    tl.add_argument("--order-by", default=None, choices=["urgency", "difficulty", "name", "deadline", "id", "time_dedicated", "finished_date"],
                    help="Sort field")
    tl.add_argument("--order-dir", default="asc", choices=["asc", "desc"], help="Sort direction")
    tl.add_argument("--json", action="store_true", help="Output JSON")

    tc = t_sub.add_parser("create", help="Create a new task")
    tc.add_argument("directory_id", type=int, help="Directory ID")
    tc.add_argument("name", help="Task name")
    tc.add_argument("--description", default="", help="Task description")
    tc.add_argument("--deadline", default="none",
                    help="Deadline (dd/MM/yyyy, yyyy-mm-dd, natural: 'tomorrow', 'in 3 days', or 'none')")
    tc.add_argument("--urgency", type=int, default=None, help="Urgency 1-5")
    tc.add_argument("--difficulty", type=int, default=None, help="Difficulty 1-5")
    tc.add_argument("--time-dedicated", type=int, default=0, help="Estimated time in minutes")
    tc.add_argument("--repeatable", action="store_true", help="Make this task repeatable")
    tc.add_argument("--repeatable-type", default="none",
                    choices=["daily", "weekly", "biweekly", "monthly", "yearly", "none"],
                    help="Repeat interval")
    tc.add_argument("--must-complete", action="store_true", default=True,
                    help="deadline advances from completion date (default: True)")
    tc.add_argument("--no-must-complete", dest="must_complete", action="store_false")
    tc.add_argument("--repeat-on-day", default="none",
                    help="specific day(s) for repeat (e.g. Mon|Wed|Fri)")
    tc.add_argument("--pinned", action="store_true", default=False,
                    help="pin task to top of lists")

    td = t_sub.add_parser("depend", help="Add a dependency between tasks")
    td.add_argument("task_id", type=int, help="Task that depends on another")
    td.add_argument("depends_on_id", type=int,
                    help="task ID that this task depends on (must be finished first)")

    tu = t_sub.add_parser("undepend", help="Remove a dependency")
    tu.add_argument("task_id", type=int, help="Task ID")
    tu.add_argument("depends_on_id", type=int,
                    help="task ID to remove dependency from")

    tdeps = t_sub.add_parser("dependencies", help="Show task dependencies")
    tdeps.add_argument("task_id", type=int, nargs="?", default=None, help="Task ID")
    tdeps.add_argument("--reverse", action="store_true", help="Show tasks that depend on this one")
    tdeps.add_argument("--directory-id", type=int, default=None, help="Show all dependencies in a directory")
    tdeps.add_argument("--json", action="store_true", help="Output JSON")

    te = t_sub.add_parser("edit", help="Edit task fields")
    te.add_argument("id", type=int, help="Task ID")
    te.add_argument("--name", help="New name")
    te.add_argument("--description", help="New description")
    te.add_argument("--deadline", help="New deadline")
    te.add_argument("--urgency", type=int, help="Urgency 1-5")
    te.add_argument("--difficulty", type=int, help="Difficulty 1-5")
    te.add_argument("--repeatable", type=int, help="Repeatable (1 or 0)")
    te.add_argument("--finished", type=int, help="Finished (1 or 0)")
    te.add_argument("--repeatable-type",
                    choices=["daily", "weekly", "biweekly", "monthly", "yearly", "none"],
                    help="Repeat interval")
    te.add_argument("--time-dedicated", type=int, help="Estimated time in minutes")
    te.add_argument("--must-complete", dest="has_to_be_completed_to_repeat", type=int,
                    help="Must complete to advance deadline (1 or 0)")
    te.add_argument("--repeat-on-day", dest="repeat_on_specific_day",
                    help="Specific day(s) for repeat (e.g. Mon|Wed|Fri)")
    te.add_argument("--pinned", type=int,
                    help="pin to top (1) or unpin (0)")

    tdone = t_sub.add_parser("done", help="Mark a task done [id | --directory-id | --directory-name]")
    tdone.add_argument("id", type=int, nargs="?", default=None, help="Task ID to mark done")
    tdone.add_argument("--directory-id", type=int, default=None,
                       help="Directory ID: interactively select tasks to complete")
    tdone.add_argument("--directory-name", default=None,
                       help="Directory name: interactively select tasks to complete")
    tdel = t_sub.add_parser("delete", help="Delete a task")
    tdel.add_argument("id", type=int, help="Task ID")
    tmove = t_sub.add_parser("move", help="Move a task to another directory")
    tmove.add_argument("id", type=int, help="Task ID")
    tmove.add_argument("new_directory_id", type=int, help="Destination directory ID")

    tundone = t_sub.add_parser("undone", help="Mark a task as not done")
    tundone.add_argument("id", type=int, help="Task ID")

    tupd = t_sub.add_parser("update-repeatables", help="Reset overdue repeatable tasks")
    tupd.add_argument("--dry-run", action="store_true", help="show what would be reset without doing it")

    # ── note ──
    n = sub.add_parser("note", help="Manage notes on tasks")
    n_sub = n.add_subparsers(dest="action", required=True)
    nl = n_sub.add_parser("list", help="List notes [--task-id]")
    nl.add_argument("--task-id", type=int, default=None, help="Filter by task ID")
    nl.add_argument("--json", action="store_true", help="Output JSON")
    nc = n_sub.add_parser("create", help="Create a note on a task [--file-path]")
    nc.add_argument("task_id", type=int, help="Task ID")
    nc.add_argument("date", help="Date (dd/MM/yyyy or yyyy-mm-dd)")
    nc.add_argument("note", help="Note content")
    nc.add_argument("--file-path", default=None, help="Path to an attached file")
    nd = n_sub.add_parser("delete", help="Delete a note")
    nd.add_argument("id", type=int, help="Note ID")
    ne = n_sub.add_parser("edit", help="Edit a note [--date] [--note] [--file-path]")
    ne.add_argument("id", type=int, help="Note ID")
    ne.add_argument("--date", default=None, help="New date")
    ne.add_argument("--note", default=None, help="New content")
    ne.add_argument("--file-path", default=None, help="New file path")
    ndedup = n_sub.add_parser("dedup", help="Remove duplicate notes (same task_id + date + content)")
    ndedup.add_argument("--task-id", type=int, default=None, help="Only dedup notes for this task")

    # ── subtask ──
    sb = sub.add_parser("subtask", help="Manage subtasks within tasks")
    sb_sub = sb.add_subparsers(dest="action", required=True)
    sbc = sb_sub.add_parser("create", help="Create a subtask")
    sbc.add_argument("task_id", type=int, help="Parent task ID")
    sbc.add_argument("content", help="Subtask content")
    sbl = sb_sub.add_parser("list", help="List subtasks of a task")
    sbl.add_argument("task_id", type=int, help="Parent task ID")
    sbd = sb_sub.add_parser("delete", help="Delete a subtask")
    sbd.add_argument("id", type=int, help="Subtask ID")
    sbdn = sb_sub.add_parser("done", help="Mark subtask done")
    sbdn.add_argument("id", type=int, help="Subtask ID")
    sbun = sb_sub.add_parser("undone", help="Mark subtask not done")
    sbun.add_argument("id", type=int, help="Subtask ID")

    # ── timer ──
    tm = sub.add_parser("timer", help="Timer controls and presets")
    tm_sub = tm.add_subparsers(dest="action", required=True)
    tms = tm_sub.add_parser("show", help="Show timer schedule for a task")
    tms.add_argument("task_id", type=int, help="Task ID")
    tmsess = tm_sub.add_parser("sessions", help="Query timer session history")
    tmsess.add_argument("--from", dest="from_date", default=None, help="Start date (YYYY-MM-DD)")
    tmsess.add_argument("--to", dest="to_date", default=None, help="End date (YYYY-MM-DD)")
    tmsess.add_argument("--json", action="store_true", help="Output JSON")
    tm_sub.add_parser("stop", help="Stop the running timer")
    tm_sub.add_parser("pause", help="Pause/unpause the running timer")
    tmp = tm_sub.add_parser("preset", help="Manage timer presets")
    tmp_sub = tmp.add_subparsers(dest="preset_action", required=True)
    tmp_sub.add_parser("list", help="List timer presets")
    tpa = tmp_sub.add_parser("add", help="Add a timer preset")
    tpa.add_argument("name", help="Preset name")
    tpa.add_argument("prep", type=str, help="Preparation time (e.g. 5m)")
    tpa.add_argument("work", type=str, help="Work time (e.g. 25m)")
    tpa.add_argument("break_", type=str, metavar="break", help="Break time (e.g. 5m)")
    tpa.add_argument("laps", type=int, help="Number of laps")
    tpr = tmp_sub.add_parser("remove", help="Remove a timer preset")
    tpr.add_argument("name", help="Preset name")

    # ── tag ──
    tg = sub.add_parser("tag", help="Manage tags")
    tg_sub = tg.add_subparsers(dest="action", required=True)
    tg_sub.add_parser("list", help="List all tags")
    tgc = tg_sub.add_parser("create", help="Create a tag")
    tgc.add_argument("name", help="Tag name")
    tgd = tg_sub.add_parser("delete", help="Delete a tag")
    tgd.add_argument("id", type=int, help="Tag ID")
    tga = tg_sub.add_parser("add", help="Add a tag to a task")
    tga.add_argument("task_id", type=int, help="Task ID")
    tga.add_argument("name", help="Tag name")
    tgr = tg_sub.add_parser("remove", help="Remove a tag from a task")
    tgr.add_argument("task_id", type=int, help="Task ID")
    tgr.add_argument("name", help="Tag name")
    tgt = tg_sub.add_parser("tasks", help="List tasks with a given tag")
    tgt.add_argument("name", help="Tag name")

    # ── export / import ──
    ex = sub.add_parser("export", help="Export all data to JSON")
    ex.add_argument("path", nargs="?", default="/tmp/taskwatch_export.json", help="Output file path")
    im = sub.add_parser("import", help="Import data from JSON")
    im.add_argument("path", help="JSON file path")

    # ── tui ──
    sub.add_parser("tui", help="Launch the terminal user interface")

    # ── serve ──
    sv = sub.add_parser("serve", help="Start the HTTP server for phone/remote access")
    sv.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    sv.add_argument("--port", type=int, default=8080, help="Port (default: 8080)")

    # ── daemon (hidden) ──
    sub.add_parser("daemon", help=argparse.SUPPRESS)

    # ── waybar ──
    sub.add_parser("waybar", help="Output JSON for Waybar timer display")

    # ── help ──
    hp = sub.add_parser("help", help="Show detailed help for a command")
    hp.add_argument("command", nargs="?", default=None, help="Command name")
    hp.add_argument("subcommand", nargs="?", default=None, help="Subcommand name")

    # ── ai ──
    ai = sub.add_parser("ai", help="AI assistant integration")
    ai_sub = ai.add_subparsers(dest="action", required=True)
    ai_c = ai_sub.add_parser("connect", help="Connect an AI provider")
    ai_c.add_argument("provider", help="Provider name (groq, gemini, mistral)")
    ai_c.add_argument("key", help="API key")
    ai_d = ai_sub.add_parser("disconnect", help="Disconnect an AI provider")
    ai_d.add_argument("provider", help="Provider name")
    ai_sub.add_parser("providers", help="List configured AI providers")
    ai_a = ai_sub.add_parser("ask", help="Ask the AI a question about your tasks")
    ai_a.add_argument("question", nargs="+", help="Your question")
    ai_sub.add_parser("chat", help="Start an interactive AI chat session")
    ai_sub.add_parser("suggest", help="Ask AI to suggest what to work on next")

    # ── stats ──
    st = sub.add_parser("stats", help="Show statistics")
    st.add_argument("--json", action="store_true", help="Output JSON")
    st_sub = st.add_subparsers(dest="action")
    st_overall = st_sub.add_parser("overall", help="Show aggregated task statistics (default)")
    st_overall.add_argument("--json", action="store_true", help="Output JSON")
    stdir = st_sub.add_parser("directory", help="Show stats for a specific directory")
    stdir.add_argument("id", type=int, help="Directory ID")
    stdir.add_argument("--json", action="store_true", help="Output JSON")
    st_dirs = st_sub.add_parser("directories", help="Show stats for all directories")
    st_dirs.add_argument("--json", action="store_true", help="Output JSON")

    # ── review ──
    rv = sub.add_parser("review", help="Show a comprehensive review overview")
    rv.add_argument("--json", action="store_true", help="Output JSON")

    return parser


def run(args: list[str] | None = None):
    parser = build_parser()
    opts = parser.parse_args(args)

    if getattr(opts, "show_global_help", False):
        print_global_help("taskwatch")
        return

    if getattr(opts, "show_version", False):
        print(f"TaskWatch+ v{__version__}")
        return

    entity = opts.entity

    if entity is None:
        print_global_help("taskwatch")
        return

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

    if entity == "serve":
        from .server import run_server
        run_server(host=opts.host, port=opts.port)
        return

    if entity == "export":
        _handle_export(opts)
        return
    if entity == "import":
        _handle_import(opts)
        return

    if entity == "help":
        _handle_help(opts, parser)
        return

    if entity == "review":
        _handle_review(opts)
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
        elif entity == "subtask":
            _handle_subtask(action, opts)
        elif entity == "timer":
            _handle_timer(action, opts)
        elif entity == "tag":
            _handle_tag(action, opts)
        elif entity == "stats":
            _handle_stats(action, opts)
        elif entity == "ai":
            _handle_ai(action, opts)
    finally:
        close()


def _handle_archive(action: str, opts):
    if action == "list":
        archives = archive_cmds.list_archives()
        if getattr(opts, "json", False):
            print(json.dumps([{"id": a.id, "name": a.name} for a in archives]))
            return
        for a in archives:
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
        dirs = directory_cmds.list_directories(opts.archive_id)
        if getattr(opts, "json", False):
            print(json.dumps([{"id": d.id, "archive_id": d.archive_id, "name": d.name} for d in dirs]))
            return
        for d in dirs:
            print(f"{d.id}: (archive {d.archive_id}) {d.name}")
    elif action == "tasks":
        dir_id = task_cmds.resolve_directory(opts.id_or_name)
        if dir_id is None:
            print(f"Directory '{opts.id_or_name}' not found", file=sys.stderr)
            sys.exit(1)
        dir_obj = directory_cmds.get_directory(dir_id)
        tasks = task_cmds.list_tasks(directory_id=dir_id)
        if opts.unfinished:
            tasks = [t for t in tasks if not t.finished]

        use_json = getattr(opts, "json", False)
        if use_json:
            out = []
            for t in tasks:
                obj = {
                    "id": t.id, "name": t.name, "directory_id": t.directory_id,
                    "finished": bool(t.finished), "deadline": t.deadline,
                    "urgency": t.urgency, "difficulty": t.difficulty,
                    "repeatable": bool(t.repeatable), "repeatable_type": t.repeatable_type,
                    "pinned": bool(t.pinned), "description": t.description,
                }
                tags = task_cmds.get_tags_for_task_display(t.id)
                if tags:
                    obj["tags"] = [s.strip() for s in tags.split(",")]
                out.append(obj)
            print(json.dumps(out))
            return

        archive_name = ""
        if dir_obj:
            arch = archive_cmds.get_archive(dir_obj.archive_id)
            if arch:
                archive_name = f" [{arch.name}]"
        print(f"Tasks in directory: {dir_obj.name}{archive_name}")
        if not tasks:
            print("  (no tasks)")
            return
        for t in tasks:
            _print_task(t)
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
    elif action == "attach-project":
        dir_obj = directory_cmds.get_directory(opts.id)
        if dir_obj is None:
            print(f"Directory {opts.id} not found", file=sys.stderr)
            sys.exit(1)
        resolved = str(Path(opts.path).expanduser().resolve())
        if not Path(resolved).is_dir():
            print(f"Path does not exist: {resolved}", file=sys.stderr)
            sys.exit(1)
        if directory_cmds.attach_project(resolved, dir_obj.id, dir_obj.name):
            print(f"Attached directory '{dir_obj.name}' (ID: {dir_obj.id}) to {resolved}")
        else:
            print("Failed to write .taskwatch-directory", file=sys.stderr)
            sys.exit(1)


def _format_task(t, show_dir: bool = False) -> str:
    status = "done" if t.finished else "pending"
    fields = [str(t.id), status, t.name]
    if show_dir:
        fields.append(f"dir:{t.directory_id}")
    if t.deadline != "none":
        dl_text = task_cmds.format_deadline_text(t.deadline)
        if dl_text:
            fields.append(f"deadline:{dl_text}")
    if t.repeatable:
        fields.append(f"repeat:{t.repeatable_type}")
    if t.pinned:
        fields.append("pinned")
    if task_cmds.is_blocked(t.id):
        fields.append("blocked")
    tags = task_cmds.get_tags_for_task_display(t.id)
    if tags:
        fields.append(f"tags:{tags}")
    fields.append(f"urg:{t.urgency}")
    fields.append(f"diff:{t.difficulty}")
    return " | ".join(fields)


def _print_task(t, show_dir: bool = False):
    print(_format_task(t, show_dir=show_dir))


def _handle_task(action: str, opts):
    if action == "list":
        dir_id = opts.directory_id
        if dir_id is None and opts.directory_name:
            dir_id = task_cmds.resolve_directory(opts.directory_name)
            if dir_id is None:
                print(f"Directory '{opts.directory_name}' not found", file=sys.stderr)
                sys.exit(1)

        use_json = getattr(opts, "json", False)

        if dir_id is None and not use_json:
            archives = archive_cmds.list_archives()
            for a in archives:
                dirs = directory_cmds.list_directories(archive_id=a.id)
                for d in dirs:
                    count = len(task_cmds.list_tasks(directory_id=d.id))
                    if count:
                        print(f"Directory '{d.name}' (id:{d.id}) [{a.name}]: {count} tasks")
            print("Use --directory-name or --directory-id to list tasks in a directory.")
            print("Use --json for machine-readable output.")
            return

        finished_filter = True if opts.finished else (False if opts.unfinished else None)
        tasks = task_cmds.list_tasks(
            directory_id=dir_id, finished=finished_filter,
            order_by=opts.order_by, order_dir=opts.order_dir,
            finished_after=opts.finished_after,
            finished_before=opts.finished_before,
        )

        if opts.unfinished:
            tasks = [t for t in tasks if not t.finished]
        if opts.overdue:
            overdue_ids = {t.id for t in task_cmds.get_overdue_tasks()}
            tasks = [t for t in tasks if t.id in overdue_ids]

        if not tasks:
            if dir_id:
                dir_obj = directory_cmds.get_directory(dir_id)
                name = dir_obj.name if dir_obj else str(dir_id)
                print(f"No tasks in directory '{name}'")
            else:
                print("No tasks")
            return

        if use_json:
            out = []
            for t in tasks:
                obj = {
                    "id": t.id, "name": t.name, "directory_id": t.directory_id,
                    "finished": bool(t.finished), "deadline": t.deadline,
                    "urgency": t.urgency, "difficulty": t.difficulty,
                    "repeatable": bool(t.repeatable), "repeatable_type": t.repeatable_type,
                    "pinned": bool(t.pinned), "description": t.description,
                    "time_dedicated": t.time_dedicated, "finished_date": t.finished_date,
                }
                tags = task_cmds.get_tags_for_task_display(t.id)
                if tags:
                    obj["tags"] = [s.strip() for s in tags.split(",")]
                out.append(obj)
            print(json.dumps(out))
            return

        show_dir = dir_id is None
        for t in tasks:
            _print_task(t, show_dir=show_dir)
    elif action == "create":
        urgency = opts.urgency
        difficulty = opts.difficulty
        if urgency is None or difficulty is None:
            defaults = directory_cmds.get_directory_defaults(opts.directory_id)
            if urgency is None:
                urgency = defaults["urgency"]
            if difficulty is None:
                difficulty = defaults["difficulty"]
        t = task_cmds.create_task(
            opts.directory_id, opts.name, opts.description,
            opts.deadline, urgency, difficulty,
            opts.time_dedicated, opts.repeatable, opts.repeatable_type,
            opts.must_complete, opts.repeat_on_day,
            pinned=opts.pinned,
        )
        parts = [f"Created task {t.id}: {t.name}"]
        if t.repeatable:
            parts.append(f"[repeat:{t.repeatable_type}]")
        if t.deadline != "none":
            parts.append(f"[deadline:{t.deadline}]")
        print(" ".join(parts))
    elif action == "depend":
        try:
            task_cmds.add_dependency(opts.task_id, opts.depends_on_id)
            print(f"Task {opts.task_id} now depends on task {opts.depends_on_id}")
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        return
    elif action == "undepend":
        if task_cmds.remove_dependency(opts.task_id, opts.depends_on_id):
            print(f"Removed dependency: task {opts.task_id} no longer depends on task {opts.depends_on_id}")
        else:
            print("Dependency not found", file=sys.stderr)
            sys.exit(1)
        return
    elif action == "edit":
        kwargs = {k: getattr(opts, k) for k in
                  ["name", "description", "deadline", "urgency", "difficulty",
                   "repeatable", "finished", "repeatable_type", "time_dedicated",
                   "has_to_be_completed_to_repeat", "repeat_on_specific_day",
                   "pinned"]
                  if getattr(opts, k) is not None}
        t = task_cmds.edit_task(opts.id, **kwargs)
        if t:
            print(f"Updated task {t.id}")
        else:
            print(f"Task {opts.id} not found", file=sys.stderr)
            sys.exit(1)
    elif action == "done":
        if opts.id is not None:
            t = task_cmds.mark_done(opts.id)
            if t:
                msg = f"Marked task {t.id} done"
                if t.repeatable and t.deadline != "none":
                    msg += f" (next deadline: {t.deadline})"
                print(msg)
            else:
                print(f"Task {opts.id} not found", file=sys.stderr)
                sys.exit(1)
        elif opts.directory_id is not None or opts.directory_name is not None:
            dir_id = opts.directory_id
            if dir_id is None and opts.directory_name:
                dir_id = task_cmds.resolve_directory(opts.directory_name)
            if dir_id is None:
                print("Directory not found", file=sys.stderr)
                sys.exit(1)
            _batch_done_in_directory(dir_id)
        else:
            print("Usage: taskwatch task done <id> | taskwatch task done --directory-id <id> | --directory-name <name>", file=sys.stderr)
            sys.exit(1)
    elif action == "delete":
        if task_cmds.delete_task(opts.id):
            print(f"Deleted task {opts.id}")
        else:
            print(f"Task {opts.id} not found", file=sys.stderr)
            sys.exit(1)
    elif action == "move":
        t = task_cmds.move_task(opts.id, opts.new_directory_id)
        if t:
            print(f"Moved task {t.id} to directory {t.directory_id}")
        else:
            print(f"Task {opts.id} not found or directory {opts.new_directory_id} does not exist",
                  file=sys.stderr)
            sys.exit(1)
    elif action == "undone":
        t = task_cmds.mark_not_done(opts.id)
        if t:
            print(f"Marked task {t.id} not done")
        else:
            print(f"Task {opts.id} not found", file=sys.stderr)
            sys.exit(1)
    elif action == "update-repeatables":
        count = task_cmds.reset_overdue_repeatables()
        if count:
            print(f"Reset {count} overdue repeatable task(s)")
        else:
            print("No overdue repeatable tasks to reset")

    elif action == "dependencies":
        use_json = getattr(opts, "json", False)
        if opts.task_id and opts.reverse:
            edges = task_cmds.get_dependents_with_details(opts.task_id)
            if use_json:
                print(json.dumps(edges))
            else:
                task = task_cmds.get_task(opts.task_id)
                name = task.name if task else f"ID:{opts.task_id}"
                print(f"Task {opts.task_id} ({name}) is depended on by:")
                if not edges:
                    print("  (nothing)")
                for e in edges:
                    status = "x" if e["depended_by_finished"] else " "
                    print(f"  [{status}] {e['depended_by_task_id']}: {e['depended_by_task_name']}")
        elif opts.task_id:
            edges = task_cmds.get_dependencies_with_details(opts.task_id)
            if use_json:
                print(json.dumps(edges))
            else:
                task = task_cmds.get_task(opts.task_id)
                name = task.name if task else f"ID:{opts.task_id}"
                print(f"Task {opts.task_id} ({name}) depends on:")
                if not edges:
                    print("  (nothing)")
                for e in edges:
                    status = "x" if e["depends_on_finished"] else " "
                    print(f"  [{status}] {e['depends_on_task_id']}: {e['depends_on_task_name']}")
        elif opts.directory_id:
            edges = task_cmds.get_dependencies_for_directory(opts.directory_id)
            if use_json:
                print(json.dumps(edges))
            else:
                from collections import defaultdict
                grouped: dict[int, list[dict]] = defaultdict(list)
                for e in edges:
                    grouped[e["task_id"]].append(e)
                if not grouped:
                    print("No dependencies in this directory")
                for tid, deps in grouped.items():
                    name = deps[0]["task_name"]
                    print(f"Task {tid} ({name}) depends on:")
                    for e in deps:
                        status = "x" if e["depends_on_finished"] else " "
                        print(f"  [{status}] {e['depends_on_task_id']}: {e['depends_on_task_name']}")
                    print()
        else:
            print("Usage: task dependencies <task_id> [--reverse] | --directory-id <id>",
                  file=sys.stderr)
            sys.exit(1)


def _batch_done_in_directory(dir_id: int) -> None:
    dir_obj = directory_cmds.get_directory(dir_id)
    if not dir_obj:
        print(f"Directory {dir_id} not found", file=sys.stderr)
        sys.exit(1)

    tasks = task_cmds.list_unfinished_tasks(dir_id)
    if not tasks:
        print(f"No unfinished tasks in directory '{dir_obj.name}'")
        return

    print(f"Unfinished tasks in '{dir_obj.name}':\n")
    for i, t in enumerate(tasks, 1):
        print(f"  {i:2d}. [{t.id:3d}] {t.name}")
    print()
    print("  Enter numbers to complete (e.g. 1,3,5 or 1-3 or 'all' or 'q' to quit)")

    try:
        choice = input("> ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if choice in ("", "q", "quit"):
        print("Cancelled")
        return

    indices: set[int] = set()
    if choice == "all":
        indices = set(range(len(tasks)))
    else:
        for part in choice.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                try:
                    a, b = part.split("-", 1)
                    lo, hi = int(a), int(b)
                    indices.update(range(lo - 1, hi))
                except ValueError:
                    pass
            else:
                try:
                    idx = int(part) - 1
                    if 0 <= idx < len(tasks):
                        indices.add(idx)
                except ValueError:
                    pass

    if not indices:
        print("No valid selections")
        return

    completed = []
    for idx in sorted(indices):
        t = tasks[idx]
        result = task_cmds.mark_done(t.id)
        if result:
            completed.append(t.name)
            status = f"  done: {t.name}"
            if result.repeatable and result.deadline != "none":
                status += f" (next deadline: {result.deadline})"
            print(status)
        else:
            print(f"  failed: {t.name}")

    if completed:
        print(f"\nCompleted {len(completed)} task(s)")


def _handle_note(action: str, opts):
    if action == "list":
        notes = note_cmds.list_notes(opts.task_id)
        if getattr(opts, "json", False):
            print(json.dumps([
                {"id": n.id, "task_id": n.task_id, "date": n.date, "note": n.note,
                 "file_path": n.file_path, "created_at": n.created_at}
                for n in notes
            ]))
            return
        for n in notes:
            fp = f" file:{n.file_path}" if n.file_path else ""
            print(f"{n.id} | {n.date} | {n.note}{fp}")
    elif action == "create":
        n = note_cmds.create_note(opts.task_id, opts.date, opts.note, file_path=opts.file_path)
        msg = f"Created note {n.id}"
        if n.file_path:
            msg += f" (file: {n.file_path})"
        print(msg)
    elif action == "delete":
        if note_cmds.delete_note(opts.id):
            print(f"Deleted note {opts.id}")
        else:
            print(f"Note {opts.id} not found", file=sys.stderr)
            sys.exit(1)
    elif action == "edit":
        kwargs = {k: getattr(opts, k) for k in ["date", "note", "file_path"] if getattr(opts, k) is not None}
        n = note_cmds.update_note(opts.id, **kwargs)
        if n:
            print(f"Updated note {n.id}")
        else:
            print(f"Note {opts.id} not found", file=sys.stderr)
            sys.exit(1)
    elif action == "dedup":
        count = note_cmds.dedup_notes(opts.task_id)
        scope = f"for task {opts.task_id}" if opts.task_id else "across all tasks"
        if count:
            print(f"Deleted {count} duplicate note(s) {scope}")
        else:
            print(f"No duplicate notes found {scope}")


def _handle_subtask(action: str, opts):
    if action == "create":
        s = subtask_cmds.create_subtask(opts.task_id, opts.content)
        print(f"Created subtask {s.id}: {s.content}")
    elif action == "list":
        for s in subtask_cmds.list_subtasks(opts.task_id):
            status = "done" if s.finished else "pending"
            print(f"{s.id} | {status} | {s.content}")
    elif action == "delete":
        if subtask_cmds.delete_subtask(opts.id):
            print(f"Deleted subtask {opts.id}")
        else:
            print(f"Subtask {opts.id} not found", file=sys.stderr)
            sys.exit(1)
    elif action == "done":
        s = subtask_cmds.mark_done(opts.id)
        if s:
            print(f"Marked subtask {s.id} done")
        else:
            print(f"Subtask {opts.id} not found", file=sys.stderr)
            sys.exit(1)
    elif action == "undone":
        s = subtask_cmds.mark_not_done(opts.id)
        if s:
            print(f"Marked subtask {s.id} not done")
        else:
            print(f"Subtask {opts.id} not found", file=sys.stderr)
            sys.exit(1)





def _handle_timer(action: str, opts):
    if action == "show":
        task = task_cmds.get_task(opts.task_id)
        if not task:
            print(f"Task {opts.task_id} not found", file=sys.stderr)
            sys.exit(1)
        print(timer.format_schedule(task))
    elif action == "sessions":
        from .timer_sessions import query_sessions
        sessions = query_sessions(
            from_date=opts.from_date,
            to_date=opts.to_date,
        )
        use_json = getattr(opts, "json", False)
        if use_json:
            print(json.dumps(sessions))
        else:
            if not sessions:
                print("No timer sessions found")
                return
            print(f"{'ID':<4} {'Task':<30} {'Duration':<10} {'Date'}")
            print("-" * 58)
            for s in sessions:
                task_label = f"{s['task_id']}: {s['task_name']}" if s['task_id'] else "(deleted task)"
                dur = f"{s['duration_minutes']}m"
                print(f"{s['id']:<4} {task_label:<30} {dur:<10} {s['date']}")
    elif action == "stop":
        _timer_stop()
    elif action == "pause":
        _timer_pause()
    elif action == "preset":
        if opts.preset_action == "list":
            presets = timer.read_presets()
            if not presets:
                print("No timer presets configured")
            else:
                for name, p in sorted(presets.items()):
                    total = p["prep"] + p["work"] * p["laps"] + p["break"] * max(0, p["laps"] - 1)
                    print(f"  {name}: {timer.fmt_timer_val(p['prep'])} + {timer.fmt_timer_val(p['work'])} + {timer.fmt_timer_val(p['break'])} x {p['laps']} = {timer.fmt_timer_val(total)}m")
        elif opts.preset_action == "add":
            presets = timer.read_presets()
            try:
                prep = timer.parse_time_string(opts.prep)
                work = timer.parse_time_string(opts.work)
                break_ = timer.parse_time_string(opts.break_)
                laps = opts.laps
            except ValueError as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
            if work <= 0 or laps <= 0:
                print("Error: work and laps must be > 0", file=sys.stderr)
                sys.exit(1)
            presets[opts.name] = {"prep": prep, "work": work, "break": break_, "laps": laps}
            timer.write_presets(presets)
            total = prep + work * laps + break_ * max(0, laps - 1)
            print(f"Added preset '{opts.name}' ({timer.fmt_timer_val(prep)} + {timer.fmt_timer_val(work)} + {timer.fmt_timer_val(break_)} x {laps} = {timer.fmt_timer_val(total)}m)")
        elif opts.preset_action == "remove":
            presets = timer.read_presets()
            if opts.name in presets:
                del presets[opts.name]
                timer.write_presets(presets)
                print(f"Removed preset '{opts.name}'")
            else:
                print(f"Preset '{opts.name}' not found", file=sys.stderr)
                sys.exit(1)





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


def _read_timer_state() -> dict:
    try:
        with open(TIMER_STATE_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _write_timer_state(updates: dict) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        current = _read_timer_state()
        current.update(updates)
        timer.atomic_write_json(TIMER_STATE_PATH, current)
    except OSError:
        pass


def _timer_stop() -> None:
    _write_timer_state({"stopped": True})
    try:
        timer.atomic_write_json(TIMER_FILE_PATH, INACTIVE_DATA)
    except OSError:
        pass
    print("Timer stopped")


def _timer_pause() -> None:
    state = _read_timer_state()
    paused = state.get("paused", False)
    _write_timer_state({"paused": not paused})
    print("Timer paused" if not paused else "Timer unpaused")


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


def _handle_review(opts):
    from . import review_cmds
    from datetime import date

    data = review_cmds.get_review_data()
    use_json = getattr(opts, "json", False)

    if use_json:
        import json
        print(json.dumps(data, default=str))
        return

    today = date.today()
    comp_pct = round((data["finished"] / data["total"] * 100) if data["total"] else 0)
    print(f"\x1b[1mReview \u2014 {today.strftime('%A, %d/%m/%Y')}\x1b[0m")
    print(f"  Tasks: {data['total']}  |  Done: {data['finished']}/{data['total']} ({comp_pct}%)  |  Pending: {data['pending']}")
    print(f"  Timer today: {data['timer_minutes_today']}m")
    print()

    overdue = data["overdue"]
    if overdue:
        print(f"\x1b[31m\u26a0 Overdue ({len(overdue)})\x1b[0m")
        for r in overdue:
            print(f"  \u26a0 {r['name']}  ({r['arch_name']} \u25b8 {r['dir_name']})  [was due {r['deadline']}]")
        print()

    due_today = data["due_today"]
    if due_today:
        print(f"\U0001f504 Due today ({len(due_today)})")
        for r in due_today:
            print(f"  \U0001f504 {r['name']}  ({r['arch_name']} \u25b8 {r['dir_name']})")
        print()

    due_tomorrow = data["due_tomorrow"]
    if due_tomorrow:
        print(f"\U0001f4c5 Due tomorrow ({len(due_tomorrow)})")
        for r in due_tomorrow:
            print(f"  \U0001f4c5 {r['name']}  ({r['arch_name']} \u25b8 {r['dir_name']})")
        print()

    completed_today = data["completed_today"]
    if completed_today:
        print(f"\u2713 Completed today ({len(completed_today)})")
        for r in completed_today:
            print(f"  \u2713 {r['name']}  ({r['arch_name']} \u25b8 {r['dir_name']})")
        print()

    completed_week = data["completed_this_week"]
    if completed_week:
        print(f"\u2713 Completed this week ({len(completed_week)})")
        for r in completed_week[:5]:
            print(f"  \u2713 {r['name']}  ({r['arch_name']} \u25b8 {r['dir_name']})  [{r['finished_date']}]")
        if len(completed_week) > 5:
            print(f"  ... and {len(completed_week) - 5} more")
        print()

    if not any([overdue, due_today, due_tomorrow, completed_today]):
        print("  \u2014 Nothing urgent. You're on top of things! \U0001f389")


def _handle_stats(action: str | None, opts):
    use_json = getattr(opts, "json", False)

    if action is None or action == "overall":
        s = stats_cmds.compute_stats()
        if use_json:
            print(json.dumps(s))
            return
        print(f"Total: {s['total']}  |  Finished: {s['finished']} ({s['completion_pct']}%)  |  Pending: {s['pending']}")
        print(f"Completed today: {s['today_completed']}  |  This week: {s['completed_this_week']}")
        print(f"Focus score: {s['focus_score']}  |  Streak: {s['streak']} day(s)")
        print(f"Timer minutes today: {s['timer_minutes_today']}  |  Total time budget: {s['total_time']}m")
        print()
        tl = s["deadline_timeline"]
        print(f"Overdue: {tl['overdue']}  |  Due today: {tl['due_today']}  |  "
              f"This week: {tl['this_week']}  |  Next week: {tl['next_week']}  |  "
              f"Later: {tl['later']}  |  No deadline: {tl['no_deadline']}")
        print()
        for a in s["archive_stats"]:
            print(f"{a['name']:<20} {a['total']:3d} tasks, {a['done']:3d} done ({a['pct']:2d}%)")
        return

    if action == "directories":
        stats = stats_cmds.all_directory_stats()
        if use_json:
            print(json.dumps(stats))
            return
        if not stats:
            print("No directories found")
            return
        for s in stats:
            print(f"{s['name']:<35} {s['total']:3d} tasks, {s['done']:3d} done ({s['pct']:2d}%)")
        return

    if action == "directory":
        total, done = stats_cmds.directory_stats(opts.id)
        if use_json:
            print(json.dumps({"directory_id": opts.id, "total": total, "done": done}))
            return
        print(f"Directory {opts.id}: {total} tasks, {done} done")


COMMAND_HELP: dict[str, dict[str, str]] = {
    "archive": {
        "description": "Archives are top-level buckets that group directories.",
        "examples": [
            "  taskwatch archive list",
            "  taskwatch archive list --json",
            "  taskwatch archive create \"Personal\"",
            "  taskwatch archive rename 1 \"Work\"",
            "  taskwatch archive delete 1",
        ],
    },
    "directory": {
        "description": "Directories sit inside archives and contain tasks.",
        "examples": [
            "  taskwatch directory list",
            "  taskwatch directory list --json",
            "  taskwatch directory list --archive-id 1",
            "  taskwatch directory tasks Work",
            "  taskwatch directory tasks 3 --unfinished",
            "  taskwatch directory tasks 3 --json",
            "  taskwatch directory create 1 \"Projects\"",
            "  taskwatch directory rename 1 \"Old Projects\"",
            "  taskwatch directory delete 1",
        ],
    },
    "task": {
        "description": "Tasks are the core unit of work. They live in directories.",
        "subcommands": {
            "list": {
                "description": "List tasks, optionally filtered by directory, finished status, or date range. Use --json for machine-readable output.",
                "examples": [
                    "  taskwatch task list",
                    "  taskwatch task list --directory-id 1",
                    "  taskwatch task list --directory-id 1 --json",
                    "  taskwatch task list --directory-name Work",
                    "  taskwatch task list --unfinished",
                    "  taskwatch task list --finished",
                    "  taskwatch task list --finished --finished-after 2026-01-01 --finished-before 2026-06-30 --json",
                    "  taskwatch task list --overdue",
                    "  taskwatch task list --order-by deadline --order-dir desc",
                ],
            },
            "create": {
                "description": "Create a new task. Deadline accepts natural language.",
                "examples": [
                    '  taskwatch task create 1 "Write docs"',
                    '  taskwatch task create 1 "Fix bug" --deadline tomorrow --urgency 4 --difficulty 3',
                    '  taskwatch task create 1 "Review PR" --deadline "in 3 days"',
                    '  taskwatch task create 1 "Weekly sync" --repeatable --repeatable-type weekly --repeat-on-day Mon',
                ],
            },
            "done": {
                "description": "Mark a task (or tasks in a directory) as done.",
                "examples": [
                    "  taskwatch task done 5",
                    "  taskwatch task done --directory-name Work",
                    "  taskwatch task done --directory-id 1",
                ],
            },
            "edit": {
                "description": "Edit one or more fields of a task.",
                "examples": [
                    '  taskwatch task edit 5 --name "New name"',
                    "  taskwatch task edit 5 --deadline next-week --urgency 5",
                    "  taskwatch task edit 5 --finished 1",
                ],
            },
            "delete": {
                "description": "Delete a task permanently.",
                "examples": ["  taskwatch task delete 5"],
            },
            "depend": {
                "description": "Make a task depend on another (blocked until dependency is done).",
                "examples": ["  taskwatch task depend 5 3"],
            },
            "undepend": {
                "description": "Remove a dependency.",
                "examples": ["  taskwatch task undepend 5 3"],
            },
            "dependencies": {
                "description": "Show task dependencies (forward, reverse, or all in a directory). Use --json for machine-readable output.",
                "examples": [
                    "  taskwatch task dependencies 5",
                    "  taskwatch task dependencies 5 --reverse",
                    "  taskwatch task dependencies 5 --json",
                    "  taskwatch task dependencies --directory-id 1",
                    "  taskwatch task dependencies --directory-id 1 --json",
                ],
            },
            "move": {
                "description": "Move a task to a different directory.",
                "examples": ["  taskwatch task move 5 2"],
            },
            "undone": {
                "description": "Mark a task as not done (reopen).",
                "examples": ["  taskwatch task undone 5"],
            },
            "update-repeatables": {
                "description": "Reset overdue repeatable tasks so they reappear.",
                "examples": [
                    "  taskwatch task update-repeatables",
                    "  taskwatch task update-repeatables --dry-run",
                ],
            },
        },
        "examples": [
            "  taskwatch task list",
            "  taskwatch task create 1 \"New task\" --deadline tomorrow",
            "  taskwatch task done 5",
            "  taskwatch task done --directory-name Work",
        ],
    },
    "note": {
        "description": "Notes are attached to tasks to record comments or logs.",
        "subcommands": {
            "create": {
                "description": "Create a note on a task, optionally attaching a file.",
                "examples": [
                    '  taskwatch note create 1 "2025-01-01" "Started working"',
                    '  taskwatch note create 1 2026-01-01 "Log" --file-path ~/notes/report.pdf',
                ],
            },
            "list": {
                "description": "List notes, optionally filtered by task. Use --json for machine-readable output.",
                "examples": [
                    "  taskwatch note list --task-id 1",
                    "  taskwatch note list --task-id 1 --json",
                ],
            },
            "edit": {
                "description": "Edit a note's date, content, or attached file path.",
                "examples": [
                    '  taskwatch note edit 1 --note "Updated"',
                    "  taskwatch note edit 1 --file-path ~/new/file.pdf",
                ],
            },
            "delete": {
                "description": "Delete a note.",
                "examples": ["  taskwatch note delete 1"],
            },
        },
        "examples": [
            '  taskwatch note create 1 "2025-01-01" "Started working on this"',
            '  taskwatch note create 1 2026-01-01 "Log" --file-path ~/report.pdf',
            "  taskwatch note list --task-id 1",
            "  taskwatch note edit 1 --note \"Updated note\"",
            "  taskwatch note delete 1",
        ],
    },
    "subtask": {
        "description": "Subtasks are checklist items within a task.",
        "examples": [
            '  taskwatch subtask create 1 "Step one"',
            "  taskwatch subtask list 1",
            "  taskwatch subtask done 3",
            "  taskwatch subtask undone 3",
            "  taskwatch subtask delete 3",
        ],
    },
    "tag": {
        "description": "Tags provide cross-cutting labels for tasks.",
        "examples": [
            "  taskwatch tag list",
            '  taskwatch tag create "urgent"',
            '  taskwatch tag add 5 "urgent"',
            '  taskwatch tag tasks "urgent"',
            '  taskwatch tag remove 5 "urgent"',
            "  taskwatch tag delete 1",
        ],
    },
    "timer": {
        "description": "Timer controls for Pomodoro-style focused work sessions.",
        "subcommands": {
            "show": {
                "description": "Show the calculated timer schedule for a task.",
                "examples": ["  taskwatch timer show 5"],
            },
            "stop": {
                "description": "Stop the running timer daemon.",
                "examples": ["  taskwatch timer stop"],
            },
            "pause": {
                "description": "Pause or unpause the running timer.",
                "examples": ["  taskwatch timer pause"],
            },
            "sessions": {
                "description": "Query timer session history with optional date range. Use --json for machine-readable output.",
                "examples": [
                    "  taskwatch timer sessions",
                    "  taskwatch timer sessions --from 2026-01-01 --to 2026-06-30",
                    "  taskwatch timer sessions --json",
                ],
            },
            "preset": {
                "description": "Manage timer presets (custom work/break durations).",
                "examples": [
                    "  taskwatch timer preset list",
                    "  taskwatch timer preset add focus 5m 25m 5m 4",
                    "  taskwatch timer preset remove focus",
                ],
            },
        },
        "examples": [
            "  taskwatch timer show 5",
            "  taskwatch timer stop",
            "  taskwatch timer preset list",
        ],
    },
    "export": {
        "description": "Export all data (archives, directories, tasks, notes, tags) to a JSON file.",
        "examples": [
            "  taskwatch export",
            '  taskwatch export ~/backup.json',
        ],
    },
    "import": {
        "description": "Import data from a previously exported JSON file.",
        "examples": [
            "  taskwatch import /tmp/taskwatch_export.json",
        ],
    },
    "ai": {
        "description": "AI assistant integration for task management.",
        "subcommands": {
            "connect": {
                "description": "Connect to an AI provider (groq, gemini, mistral).",
                "examples": ["  taskwatch ai connect groq gsk_your_key_here"],
            },
            "disconnect": {
                "description": "Disconnect an AI provider.",
                "examples": ["  taskwatch ai disconnect groq"],
            },
            "providers": {
                "description": "List all configured AI providers.",
                "examples": ["  taskwatch ai providers"],
            },
            "ask": {
                "description": "Ask a question about your tasks to the AI.",
                "examples": ["  taskwatch ai ask What needs my attention today?"],
            },
            "chat": {
                "description": "Start an interactive AI chat session.",
                "examples": ["  taskwatch ai chat"],
            },
            "suggest": {
                "description": "Ask AI to suggest what to work on next.",
                "examples": ["  taskwatch ai suggest"],
            },
        },
        "examples": [
            "  taskwatch ai connect groq gsk_your_key_here",
            "  taskwatch ai ask What is overdue?",
        ],
    },
    "tui": {
        "description": "Launch the full-screen terminal user interface.",
        "examples": ["  taskwatch tui"],
    },
    "waybar": {
        "description": "Output JSON for Waybar (status bar) timer display.",
        "examples": ["  taskwatch waybar"],
    },
    "help": {
        "description": "Show detailed help for any command.",
        "examples": [
            "  taskwatch help",
            "  taskwatch help task",
            "  taskwatch help task list",
            "  taskwatch help task done",
        ],
    },
    "stats": {
        "description": "Display task statistics (overview, per-directory, or all directories).",
        "subcommands": {
            "overall": {
                "description": "Show aggregated statistics across all tasks. Use --json for machine-readable output.",
                "examples": ["  taskwatch stats", "  taskwatch stats --json", "  taskwatch stats overall"],
            },
            "directory": {
                "description": "Show completion stats for a specific directory.",
                "examples": ["  taskwatch stats directory 1", "  taskwatch stats directory 1 --json"],
            },
            "directories": {
                "description": "Show completion stats for every directory.",
                "examples": ["  taskwatch stats directories", "  taskwatch stats directories --json"],
            },
        },
        "examples": [
            "  taskwatch stats",
            "  taskwatch stats --json",
            "  taskwatch stats directories",
            "  taskwatch stats directory 1",
        ],
    },
    "review": {
        "description": "Show a comprehensive review overview: overdue tasks, tasks due today/tomorrow, completed tasks today and this week, and key stats. Use --json for machine-readable output.",
        "examples": [
            "  taskwatch review",
            "  taskwatch review --json",
        ],
    },
}


def _print_subparser_help(parser, cmd_parts: list[str]) -> None:
    """Print argparse help for a specific subcommand."""
    try:
        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            parser.parse_args(cmd_parts + ["--help"])
        except SystemExit:
            pass
        captured = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    print(captured, end="")


def _handle_help(opts, parser):
    cmd = opts.command
    sub = opts.subcommand

    if cmd is None or cmd not in COMMAND_HELP:
        print_global_help("taskwatch")
        return

    info = COMMAND_HELP[cmd]

    print(f"\x1b[1m{cmd}\x1b[0m")
    print(f"  {info['description']}\n")

    if sub and "subcommands" in info and sub in info["subcommands"]:
        sub_info = info["subcommands"][sub]
        print(f"  \x1b[1m{cmd} {sub}\x1b[0m")
        print(f"    {sub_info['description']}\n")
        if "examples" in sub_info:
            print("  \x1b[1mExamples:\x1b[0m")
            for ex in sub_info["examples"]:
                print(f"    {ex}")
        print()
        _print_subparser_help(parser, [cmd, sub])
        return

    if "subcommands" in info:
        print("  \x1b[1mSubcommands:\x1b[0m")
        for name, sub_info in info["subcommands"].items():
            print(f"    {name:<25} {sub_info['description']}")
        print()

    if "examples" in info:
        print("  \x1b[1mExamples:\x1b[0m")
        for ex in info["examples"]:
            print(f"    {ex}")
        print()

    _print_subparser_help(parser, [cmd])


def _handle_ai(action: str, opts):
    if action == "connect":
        _ai_connect(opts.provider, opts.key)
    elif action == "disconnect":
        _ai_disconnect(opts.provider)
    elif action == "providers":
        _ai_providers()
    elif action == "ask":
        _ai_ask(" ".join(opts.question))
    elif action == "chat":
        _ai_chat()
    elif action == "suggest":
        _ai_suggest()


def _ai_connect(provider: str, key: str) -> None:
    ok, msg = ai_client.add_provider(provider, key)
    print(msg)
    if not ok:
        sys.exit(1)


def _ai_disconnect(provider: str) -> None:
    ok, msg = ai_client.remove_provider(provider)
    print(msg)
    if not ok:
        sys.exit(1)


def _ai_providers() -> None:
    providers = ai_client.list_providers()
    if not providers:
        print("No providers configured")
        return
    for p in providers:
        status = "enabled" if p["enabled"] else "disabled"
        print(f"  {status}: {p['name']} ({p['model']})")


def _ai_ask(question: str) -> None:
    providers = ai_client.list_providers()
    if not providers:
        print("No AI providers configured. Use: taskwatch ai connect <provider> <key>", file=sys.stderr)
        sys.exit(1)

    context = ai_client.build_cli_context()
    _SYSTEM_PROMPT = (
        "You are TaskWatch+ AI, an assistant integrated into a terminal task tracker. "
        "You have full read access to the user's task data (shown below). "
        "When the user asks you to create or modify data, use the >>>ACTION blocks shown below."
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT + "\n\n" + context},
        {"role": "user", "content": question},
    ]

    response, provider, actions = ai_client.chat(messages)
    print(response)

    if actions:
        _handle_cli_actions(actions)


def _ai_chat() -> None:
    providers = ai_client.list_providers()
    if not providers:
        print("No AI providers configured. Use: taskwatch ai connect <provider> <key>", file=sys.stderr)
        sys.exit(1)

    names = ", ".join(p["name"] for p in providers)
    print(f"AI Chat \u2014 Connected: {names}")
    print("Type 'exit' or 'quit' to end.")

    context = ai_client.build_cli_context()
    _SYSTEM_PROMPT = (
        "You are TaskWatch+ AI, an assistant integrated into a terminal task tracker. "
        "You have full read access to the user's task data (shown below). "
        "When the user asks you to create or modify data, use the >>>ACTION blocks shown below."
    )

    history: list[dict] = []

    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not text or text.lower() in ("exit", "quit"):
            break

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT + "\n\n" + context},
        ]
        for msg in history[-10:]:
            messages.append(msg)
        messages.append({"role": "user", "content": text})

        response, provider, actions = ai_client.chat(messages)
        print(response)

        if actions:
            _handle_cli_actions(actions)

        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": response})


def _ai_suggest() -> None:
    providers = ai_client.list_providers()
    if not providers:
        print("No AI providers configured. Use: taskwatch ai connect <provider> <key>", file=sys.stderr)
        sys.exit(1)

    context = ai_client.build_cli_context()
    _SYSTEM_PROMPT = (
        "You are TaskWatch+ AI, an assistant integrated into a terminal task tracker. "
        "You have full read access to the user's task data (shown below). "
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT + "\n\n" + context},
        {"role": "user",
         "content": "What should I work on next? Consider urgency, deadlines, difficulty, and current progress."},
    ]

    response, provider, actions = ai_client.chat(messages)
    if provider:
        print(f"[{provider}]")
    print(response)

    if actions:
        _handle_cli_actions(actions)


def _handle_cli_actions(actions: list[dict]) -> None:
    print("-- Proposed actions --")
    for i, a in enumerate(actions, 1):
        label = a.get("type", "UNKNOWN").replace("_", " ").title()
        params = ", ".join(f"{k}={v}" for k, v in a.items() if k != "type")
        print(f"  {i}. {label}: {params}")
    print("---")

    try:
        confirm = input("  Confirm? [Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        confirm = "n"

    if confirm in ("", "y", "yes"):
        _execute_cli_actions(actions)
    else:
        print("  Cancelled.")


def _execute_cli_actions(actions: list[dict]) -> None:
    from datetime import date

    from . import archive_cmds, directory_cmds, note_cmds, task_cmds

    for a in actions:
        atype = a.get("type", "")
        try:
            if atype == "CREATE_TASK":
                name = a.get("name", "New Task")
                did_str = a.get("directory_id", "")
                if did_str:
                    did = int(did_str)
                else:
                    dirs = directory_cmds.list_directories()
                    did = dirs[0].id if dirs else None
                if did is None:
                    print("  \u2717 No directory available for task creation")
                    continue
                task_cmds.create_task(
                    directory_id=did,
                    name=name,
                    description=a.get("description", ""),
                    deadline=a.get("deadline", "none"),
                    urgency=int(a.get("urgency", 1)),
                    difficulty=int(a.get("difficulty", 1)),
                    time_dedicated=int(a.get("time_dedicated", 0)),
                )
                print(f"  OK: Task '{name}' created")

            elif atype == "CREATE_DIRECTORY":
                name = a.get("name", "New Directory")
                aid_str = a.get("archive_id", "")
                if aid_str:
                    aid = int(aid_str)
                else:
                    archives = archive_cmds.list_archives()
                    aid = archives[0].id if archives else None
                if aid is None:
                    print("  FAIL: No archive available for directory creation")
                    continue
                directory_cmds.create_directory(archive_id=aid, name=name)
                print(f"  OK: Directory '{name}' created")

            elif atype == "CREATE_ARCHIVE":
                name = a.get("name", "New Archive")
                archive_cmds.create_archive(name=name)
                print(f"  OK: Archive '{name}' created")

            elif atype == "FINISH_TASK":
                tid_str = a.get("task_id", "")
                if not tid_str:
                    print("  FAIL: No task_id specified")
                    continue
                task_cmds.mark_done(int(tid_str))
                print(f"  OK: Task {tid_str} finished")

            elif atype == "ADD_NOTE":
                tid_str = a.get("task_id", "")
                if not tid_str:
                    print("  FAIL: No task_id specified")
                    continue
                note = a.get("note", "")
                file_path = a.get("file_path", None)
                note_cmds.create_note(int(tid_str), date.today().isoformat(), note, file_path=file_path)
                msg = f"  OK: Note added to task {tid_str}"
                if file_path:
                    msg += f" (file: {file_path})"
                print(msg)

            else:
                print(f"  ? Unknown action type: {atype}")

        except Exception as e:
            print(f"  FAIL: Error executing {atype}: {e}")
