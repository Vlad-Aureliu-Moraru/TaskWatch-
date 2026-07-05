import argparse
import json
import sys

from . import (
    ai_client,
    archive_cmds,
    directory_cmds,
    io_cmds,
    note_cmds,
    tag_cmds,
    task_cmds,
    timer,
)
from .db import close
from .paths import DATA_DIR, TIMER_FILE_PATH, TIMER_STATE_PATH
from .paths import INACTIVE_TIMER_DATA as INACTIVE_DATA


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
    te.add_argument("--repeat-on-day", dest="repeat_on_specific_day")

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
    ne = n_sub.add_parser("edit")
    ne.add_argument("id", type=int)
    ne.add_argument("--date", default=None)
    ne.add_argument("--note", default=None)

    # ── timer ──
    tm = sub.add_parser("timer")
    tm_sub = tm.add_subparsers(dest="action", required=True)
    tms = tm_sub.add_parser("show")
    tms.add_argument("task_id", type=int)
    tm_sub.add_parser("stop", help="Stop the running timer")
    tm_sub.add_parser("pause", help="Pause/unpause the running timer")

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

    # ── ai ──
    ai = sub.add_parser("ai")
    ai_sub = ai.add_subparsers(dest="action", required=True)
    ai_c = ai_sub.add_parser("connect")
    ai_c.add_argument("provider")
    ai_c.add_argument("key")
    ai_d = ai_sub.add_parser("disconnect")
    ai_d.add_argument("provider")
    ai_sub.add_parser("providers")
    ai_a = ai_sub.add_parser("ask")
    ai_a.add_argument("question", nargs="+")
    ai_sub.add_parser("chat")
    ai_sub.add_parser("suggest")

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
        elif entity == "ai":
            _handle_ai(action, opts)
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
    elif action == "edit":
        kwargs = {k: getattr(opts, k) for k in ["date", "note"] if getattr(opts, k) is not None}
        n = note_cmds.update_note(opts.id, **kwargs)
        if n:
            print(f"Updated note {n.id}")
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
    elif action == "stop":
        _timer_stop()
    elif action == "pause":
        _timer_pause()


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
        tmp = TIMER_STATE_PATH.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(current, f)
        tmp.rename(TIMER_STATE_PATH)
    except OSError:
        pass


def _timer_stop() -> None:
    _write_timer_state({"stopped": True})
    try:
        with open(TIMER_FILE_PATH, "w") as f:
            json.dump(INACTIVE_DATA, f)
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
        mark = "\u2713" if p["enabled"] else "\u2717"
        print(f"  {mark} {p['name']}: {p['model']} ({p['key']})")


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
    print("  \u2500\u2500 Proposed actions \u2500\u2500")
    for i, a in enumerate(actions, 1):
        label = a.get("type", "UNKNOWN").replace("_", " ").title()
        params = ", ".join(f"{k}={v}" for k, v in a.items() if k != "type")
        print(f"  {i}. {label}: {params}")
    print("  \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
          "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")

    try:
        confirm = input("  Confirm? [Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        confirm = "n"

    if confirm in ("", "y", "yes"):
        _execute_cli_actions(actions)
    else:
        print("  \u2717 Cancelled.")


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
                print(f"  \u2713 Task '{name}' created")

            elif atype == "CREATE_DIRECTORY":
                name = a.get("name", "New Directory")
                aid_str = a.get("archive_id", "")
                if aid_str:
                    aid = int(aid_str)
                else:
                    archives = archive_cmds.list_archives()
                    aid = archives[0].id if archives else None
                if aid is None:
                    print("  \u2717 No archive available for directory creation")
                    continue
                directory_cmds.create_directory(archive_id=aid, name=name)
                print(f"  \u2713 Directory '{name}' created")

            elif atype == "CREATE_ARCHIVE":
                name = a.get("name", "New Archive")
                archive_cmds.create_archive(name=name)
                print(f"  \u2713 Archive '{name}' created")

            elif atype == "FINISH_TASK":
                tid_str = a.get("task_id", "")
                if not tid_str:
                    print("  \u2717 No task_id specified")
                    continue
                task_cmds.mark_done(int(tid_str))
                print(f"  \u2713 Task {tid_str} finished")

            elif atype == "ADD_NOTE":
                tid_str = a.get("task_id", "")
                if not tid_str:
                    print("  \u2717 No task_id specified")
                    continue
                note = a.get("note", "")
                note_cmds.create_note(int(tid_str), date.today().isoformat(), note)
                print(f"  \u2713 Note added to task {tid_str}")

            else:
                print(f"  ? Unknown action type: {atype}")

        except Exception as e:
            print(f"  \u2717 Error executing {atype}: {e}")
