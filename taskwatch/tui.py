from __future__ import annotations

import json
import logging
import os
import queue
import random
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from datetime import date, datetime
from functools import partial
from pathlib import Path

import urwid
from urwid import (
    AttrMap,
    Filler,
    LineBox,
    ListBox,
    Overlay,
    SimpleFocusListWalker,
    Text,
)

from . import (
    ai_chat,
    ai_client,
    archive_cmds,
    calcurse_cmds,
    directory_cmds,
    io_cmds,
    note_cmds,
    stats_cmds,
    subtask_cmds,
    tag_cmds,
    task_cmds,
    timer_sessions,
    undo_cmds,
)
from . import db as db_mod
from . import timer as timer_mod
from .paths import CONFIG_PATH, TIMER_STATE_PATH
from .tui_helpers import (
    CELEBRATION_MESSAGES,
    COMMANDS,
    PALETTE,
    _HIGHLIGHT_ALIASES,
    _HIGHLIGHT_COLORS,
    Level,
    _bar,
    _braille_bar,
    _build_terminal_cmd,
    _copy_to_clipboard,
    _detect_terminal,
    _dur,
    _ensure_default_sounds,
    _find_opencode,
    _hblock_bar,
    _heatmap_attr,
    _apply_pattern,
    _level_color,
    LEVEL_XP_THRESHOLDS,
    _level_theme,
    _progress_gradient_attr,
    _BRAILLE_STEPS,
    _render_markdown_to_urwid,
    logger,
)
from .tui_timer import _TimerMixin
from .tui_wizards import _WizardMixin
from .tui_overlays import (
    FilePickerWidget,
    GlobalSearchOverlay,
    HelpSearchOverlay,
    ImportJSONOverlay,
)
from .tui_widgets import (
    CommandEdit,
    ColorPickerWidget,
    DayPickerWidget,
    MainFrame,
    SelectableText,
    VimListBox,
    _make_list_row,
)

logger = logging.getLogger("taskwatch.tui")
class TaskWatchTUI(_WizardMixin, _TimerMixin):
    def __init__(self):
        self._level = Level.ARCHIVES
        self._prev_level = Level.ARCHIVES
        self._selected_archive_id: int | None = None
        self._selected_archive_name: str | None = None
        self._selected_directory_id: int | None = None
        self._selected_directory_name: str | None = None
        self._selected_task_id: int | None = None
        self._selected_task_name: str | None = None
        self._prompt_handler: Callable[[str], None] | None = None
        self._show_finished = False
        self._sort_field: str | None = None
        self._sort_dir: str = "asc"
        self._wiz_name: str | None = None
        self._wiz_desc: str = ""
        self._wizard_stack: list[dict] = []
        self._cmd_history: list[str] = []
        self._cmd_history_index: int = -1
        self._edit_ctx: dict | None = None
        self._help_overlay = None
        self._global_search_overlay = None
        self._prev_overlay = None
        self._current_items: list | None = None

        self._timer_running = False
        self._timer_seconds = 0
        self._timer_elapsed = 0
        self._timer_paused = False
        self._timer_task_id: int | None = None
        self._timer_task_name: str | None = None
        self._timer_schedule: dict | None = None
        self._timer_segment_idx: int = 0
        self._timer_segment_elapsed: int = 0
        self._prev_timer_segment_idx: int = 0
        self._sound_enabled: bool = True
        self._sound_paths: dict[str, Path] = _ensure_default_sounds()
        self._tick_counter: int = 0
        self._filter_text: str = ""
        self._in_search_mode: bool = False
        self._filter_tag: str | None = None
        self._stats_overlay = None
        self._tab_matches: list[str] = []
        self._tab_index: int = -1
        self._notified_deadlines: set[int] = set()
        self._notify_deadlines_enabled: bool = True
        self._bulk_selection: set[int] = set()
        self._all_tasks_mode: bool = False
        self._focus_mode: bool = False
        self._focus_timer_text: Text | None = None
        self._focus_task_name: Text | None = None
        self._saved_body: object | None = None
        self._search_debounce_alarm: object | None = None
        self._caption_alarm_handle: object | None = None
        self._current_prompt: str | tuple = ("standout", "\u276f ")
        self._highlight_color: str = "default"
        self._timer_presets: dict[str, dict] = {}
        self._ai_inbox: queue.Queue = queue.Queue()
        self._ai_chat_widget: ai_chat.AIChatWidget | None = None
        self._loading: bool = False
        self._loading_spinner_idx: int = 0
        self._loading_frames: list[str] = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self._loading_title: str = ""
        try:
            with open(CONFIG_PATH) as f:
                for line in f:
                    if line.startswith("HIGHLIGHT:"):
                        saved = line.split(":", 1)[1].strip()
                        saved = _HIGHLIGHT_ALIASES.get(saved, saved)
                        if saved in {n for n, _ in _HIGHLIGHT_COLORS}:
                            self._highlight_color = saved
                            urwid_bg = dict(_HIGHLIGHT_COLORS).get(saved, "default")
                            for i, entry in enumerate(PALETTE):
                                if entry[0] == "focus":
                                    PALETTE[i] = (entry[0], entry[1], urwid_bg, *entry[3:])
                                elif len(entry) >= 4 and entry[0].endswith("_focus"):
                                    PALETTE[i] = (entry[0], entry[1], urwid_bg, *entry[3:])
                    elif line.startswith("SOUND_ENABLED:"):
                        self._sound_enabled = line.split(":", 1)[1].strip().lower() == "true"
                    elif line.startswith("SOUND_WORK:"):
                        p = line.split(":", 1)[1].strip()
                        if p:
                            self._sound_paths["work"] = Path(p)
                    elif line.startswith("SOUND_BREAK:"):
                        p = line.split(":", 1)[1].strip()
                        if p:
                            self._sound_paths["break"] = Path(p)
                    elif line.startswith("SOUND_DONE:"):
                        p = line.split(":", 1)[1].strip()
                        if p:
                            self._sound_paths["done"] = Path(p)
                    elif line.startswith("TIMER_PRESET:"):
                        _, rest = line.split(":", 1)
                        if "=" in rest:
                            name, val = rest.split("=", 1)
                            name = name.strip()
                            if "," in val:
                                parts = val.split(",")
                                if len(parts) == 4:
                                    try:
                                        self._timer_presets[name] = {
                                            "prep": float(parts[0]),
                                            "work": float(parts[1]),
                                            "break": float(parts[2]),
                                            "laps": int(parts[3]),
                                        }
                                    except ValueError:
                                        pass
                            else:
                                try:
                                    mins = float(val)
                                    self._timer_presets[name] = {
                                        "prep": 0.0, "work": mins, "break": 0.0, "laps": 1,
                                    }
                                except ValueError:
                                    pass
        except OSError:
            pass

        self._timer_state_path = TIMER_STATE_PATH
        self._daemon_path = Path(__file__).resolve().parent / "timer_daemon.py"

        self._frame = MainFrame(self)
        self._loop = urwid.MainLoop(
            self._frame, PALETTE,
            unhandled_input=self._unhandled_input,
            handle_mouse=True,
        )
        self._list_walker: SimpleFocusListWalker
        self._list_box: ListBox
        self._detail_walker: SimpleFocusListWalker
        self._detail_box: ListBox
        self._cmd: CommandEdit
        self._breadcrumb_text: Text
        self._clock_text: Text
        self._clock_w: AttrMap

    def _focus_body(self) -> None:
        self._frame.focus_position = "body"
        self._body.focus_position = 0

    def _set_timed_caption(self, attr: str, text: str, duration: float = 2) -> None:
        if self._caption_alarm_handle is not None:
            try:
                self._loop.remove_alarm(self._caption_alarm_handle)
            except Exception as e:
                logger.debug("Failed to remove caption alarm: %s", e)
        self._cmd.set_caption((attr, text))
        self._caption_alarm_handle = self._loop.set_alarm_in(
            duration,
            lambda *a: (
                setattr(self, "_current_prompt", ("standout", "\u276f ")),
                self._cmd.set_caption(("standout", "\u276f ")),
            ),
        )

    def _start_loading(self, title: str = "") -> None:
        self._loading = True
        self._loading_spinner_idx = 0
        self._loading_title = title
        self._cmd.set_caption(("standout", f"\u276f {self._loading_frames[0]} {title}"))

    def _stop_loading(self) -> None:
        self._loading = False
        self._loading_title = ""
        self._cmd.set_caption(("standout", "\u276f "))

    def _run_async(self, target: Callable[[], object], on_done: Callable[[object], None], title: str = "") -> None:
        self._start_loading(title)
        def worker() -> None:
            try:
                result = target()
            except Exception as e:
                result = e
            self._ai_inbox.put(lambda: self._on_async_done(result, on_done))
        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def _on_async_done(self, result: object, on_done: Callable[[object], None]) -> None:
        self._stop_loading()
        on_done(result)

    def _refresh_list(self) -> None:
        try:
            old_focus = self._list_box.focus_position
        except IndexError:
            old_focus = 0
        old_level = self._prev_level
        urwid.disconnect_signal(self._list_walker, "modified", self._show_detail)
        self._list_walker.clear()

        filter_indicator = ""
        if self._filter_text:
            filter_indicator = f" [filter: {self._filter_text}]"

        tag_filter_indicator = ""
        if self._filter_tag:
            tag_filter_indicator = f" [tag: {self._filter_tag}]"

        if self._level == Level.ARCHIVES:
            items = archive_cmds.list_archives()
            if self._filter_text:
                items = [x for x in items if self._filter_text.lower() in x.name.lower()]
            self._current_items = items
            self._set_breadcrumb(f"\uf187 Archives{filter_indicator}")
            conn = db_mod.get_conn()
            aid_placeholders = ",".join("?" for _ in items) if items else "NULL"
            dir_counts: dict[int, int] = {}
            if items:
                for row in conn.execute(
                    f"SELECT archive_id, COUNT(*) AS c FROM directories WHERE archive_id IN ({aid_placeholders}) GROUP BY archive_id",
                    [a.id for a in items],
                ):
                    dir_counts[row["archive_id"]] = row["c"]
            pairs: list[tuple[str, str]] = []
            for a in items:
                cnt = dir_counts.get(a.id, 0)
                pairs.append((f"\uf187 {a.name}", f"[{cnt}]"))
            if pairs:
                rw = max(len(r) for _, r in pairs) + 1
                for left, right in pairs:
                    w = _make_list_row(left, right, rw, "default", "focus")
                    self._list_walker.append(w)

        elif self._level == Level.DIRECTORIES:
            self._set_breadcrumb(
                f"\uf187 Archives \u25b8 \uf4d3 {self._selected_archive_name or '?'}{filter_indicator}"
            )
            items = directory_cmds.list_directories(
                archive_id=self._selected_archive_id
            )
            if self._filter_text:
                items = [x for x in items if self._filter_text.lower() in x.name.lower()]
            self._current_items = items
            conn = db_mod.get_conn()
            did_placeholders = ",".join("?" for _ in items) if items else "NULL"
            dir_stats: dict[int, tuple[int, int]] = {}
            if items:
                for row in conn.execute(
                    f"""SELECT directory_id, COUNT(*) AS total,
                               SUM(CASE WHEN finished THEN 1 ELSE 0 END) AS done
                        FROM tasks WHERE directory_id IN ({did_placeholders})
                        GROUP BY directory_id""",
                    [d.id for d in items],
                ):
                    dir_stats[row["directory_id"]] = (row["total"], row["done"])
            pairs: list[tuple[str | list, str, str]] = []
            for d in items:
                total, done = dir_stats.get(d.id, (0, 0))
                completed = total > 0 and done == total
                if completed:
                    left = f"\U000f197e {d.name}"
                    attr = "done_dir"
                else:
                    icon, pattern = _level_theme(d.level)
                    segments = _apply_pattern(d.name, pattern)
                    first_color = pattern[0][0]
                    left = [(first_color, f"{icon} ")] + segments
                    attr = first_color
                pairs.append((left, f"[{done}/{total}]", attr))
            if pairs:
                rw = max(len(r) for _, r, _ in pairs) + 1
                for left, right, attr in pairs:
                    w = _make_list_row(left, right, rw, attr, "focus")
                    self._list_walker.append(w)

        elif self._level == Level.TASKS:
            status = " [+done]" if self._show_finished else ""
            dir_label = self._selected_directory_name or "?"
            if self._all_tasks_mode:
                dir_label = "All Tasks"
            self._set_breadcrumb(
                f"\uf187 Archives \u25b8 \uf4d3 {self._selected_archive_name or '?'}"
                f" \u25b8 \ueebf {dir_label}{status}{filter_indicator}{tag_filter_indicator}"
            )
            kw: dict = {}
            if not self._show_finished:
                kw["finished"] = False
            if self._sort_field:
                kw["order_by"] = self._sort_field
                kw["order_dir"] = self._sort_dir
            if self._all_tasks_mode and self._selected_archive_id is not None:
                raw = task_cmds.list_all_tasks(self._selected_archive_id, **kw)
                items = [r["task"] for r in raw]
                dir_map = {r["task"].id: r["dir_name"] for r in raw}
            else:
                items = task_cmds.list_tasks(self._selected_directory_id, **kw)
                dir_map = {}
            if self._filter_text:
                items = [x for x in items if self._filter_text.lower() in x.name.lower()]
            if self._filter_tag:
                tagged_ids = set(tag_cmds.get_tasks_by_tag(self._filter_tag))
                items = [x for x in items if x.id in tagged_ids]
            self._current_items = items
            ids = [t.id for t in items]
            conn = db_mod.get_conn()
            note_counts: dict[int, int] = {}
            if ids:
                placeholders = ",".join("?" for _ in ids)
                for row in conn.execute(
                    f"SELECT task_id, COUNT(*) AS c FROM notes WHERE task_id IN ({placeholders}) GROUP BY task_id",
                    ids,
                ):
                    note_counts[row["task_id"]] = row["c"]
            task_tags: dict[int, list[str]] = {}
            if ids:
                placeholders = ",".join("?" for _ in ids)
                for row in conn.execute(
                    f"""SELECT tt.task_id, tg.name
                        FROM task_tags tt
                        JOIN tags tg ON tt.tag_id = tg.id
                        WHERE tt.task_id IN ({placeholders})
                        ORDER BY tt.task_id, tg.name""",
                    ids,
                ):
                    task_tags.setdefault(row["task_id"], []).append(row["name"])
            blocked_ids = task_cmds.get_blocked_ids([t.id for t in items if not t.finished])

            pairs: list[tuple[str, str, str]] = []
            for t in items:
                sel = "[x]" if t.id in self._bulk_selection else " "
                prefix = "\u2713 " if t.finished else f"\u25cb{sel} "
                pin_icon = "\U0001f4cc " if t.pinned else ""
                block_icon = "\U0001f512 " if t.id in blocked_ids else ""
                cnt = note_counts.get(t.id, 0)
                tags = task_tags.get(t.id, [])
                tag_str = f" [{','.join(tags)}]" if tags else ""
                dir_str = f" [{dir_map[t.id]}]" if t.id in dir_map else ""
                right = f"[{cnt}]{tag_str}{dir_str}"
                selected = t.id in self._bulk_selection
                if t.finished:
                    left = pin_icon + prefix + f"\ueebf {t.name}"
                    pairs.append((left, right, "dim"))
                elif selected:
                    left = pin_icon + prefix + f"\ueebf {t.name}"
                    pairs.append((left, right, "focus"))
                elif t.id in blocked_ids:
                    left = pin_icon + prefix + f"\ueebf {t.name}"
                    pairs.append((left, right, "dim"))
                else:
                    left = [
                        (_level_color(t.urgency), pin_icon + prefix),
                        (_level_color(t.difficulty), f"{block_icon}\ueebf {t.name}"),
                    ]
                    pairs.append((left, right, "default"))
            if pairs:
                rw = max(len(r) for _, r, _ in pairs) + 1
                for left, right, attr in pairs:
                    w = _make_list_row(left, right, rw, attr, "focus")
                    self._list_walker.append(w)

        elif self._level == Level.NOTES:
            self._set_breadcrumb(
                f"\uf187 Archives \u25b8 \uf4d3 {self._selected_archive_name or '?'}"
                f" \u25b8 \ueebf {self._selected_directory_name or '?'}"
                f" \u25b8 \U000f039a {self._selected_task_name or '?'}{filter_indicator}"
            )
            items = note_cmds.list_notes(self._selected_task_id)
            if self._filter_text:
                items = [x for x in items if self._filter_text.lower() in x.note.lower()]
            self._current_items = items
            for n in items:
                ts = n.created_at or n.date
                if ts:
                    try:
                        dt = datetime.fromisoformat(ts)
                        ts_display = dt.strftime("%d/%m/%Y %H:%M")
                    except (ValueError, TypeError):
                        ts_display = ts[:16]
                else:
                    ts_display = n.date
                clip = "\U0001f4ce " if n.file_path else ""
                label = f"\U000f039a {n.id}: {clip}{ts_display}"
                w = _make_list_row(label, "", 0, "default", "focus")
                self._list_walker.append(w)
        urwid.connect_signal(self._list_walker, "modified", self._show_detail)
        self._show_detail()
        if self._level == old_level and self._current_items:
            self._list_box.focus_position = min(old_focus, len(self._current_items) - 1)
        self._set_terminal_title()

    def _set_terminal_title(self) -> None:
        conn = db_mod.get_conn()
        count = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE finished = 0"
        ).fetchone()[0]
        title = f"TaskWatch+ ({count} pending)" if count else "TaskWatch+"
        sys.stdout.write(f"\033]0;{title}\007")

    def _set_breadcrumb(self, path: str) -> None:
        self._breadcrumb_text.set_text(path)

    def _set_detail(self, *texts: str | list) -> None:
        self._detail_walker.clear()
        for t in texts:
            self._detail_walker.append(Text(t))

    def _show_detail(self) -> None:
        if not self._list_walker or not self._current_items:
            self._detail_walker.clear()
            return
        try:
            idx = self._list_box.focus_position
        except IndexError:
            self._detail_walker.clear()
            return
        if idx >= len(self._current_items):
            self._detail_walker.clear()
            return

        if self._level == Level.ARCHIVES:
            a = self._current_items[idx]
            lines: list[str | list] = [
                [("head", f"\uf187 {a.name}"), ("dim", f" (id: {a.id})"), "\n\nPress Enter to browse directories."],
            ]
            announcements = task_cmds.get_announcements()
            if announcements:
                lines.append("")
                lines.append([("head", "  \uf0aa Announcements")])
                lines.append([("dim", "  " + "\u2500" * 30)])
                groups: dict[str, dict[str, list]] = {}
                for entry in announcements:
                    arch = entry["arch_name"]
                    dname = entry["dir_name"]
                    if arch not in groups:
                        groups[arch] = {}
                    if dname not in groups[arch]:
                        groups[arch][dname] = []
                    groups[arch][dname].append(entry)
                for arch_name in sorted(groups.keys()):
                    lines.append([("head", f"  [{arch_name}]")])
                    for dir_name in sorted(groups[arch_name].keys()):
                        for entry in groups[arch_name][dir_name]:
                            task = entry["task"]
                            rel = task_cmds.relative_deadline(task.deadline)
                            style = "error" if "Overdue" in rel else ("c3" if "today" in rel else "default")
                            lines.append([(style, f"    \u25cb {task.name}")])
                            lines.append([("dim", f"      {dir_name} \u2022 {rel}")])
            else:
                lines.append([("c2", "  \u2713 No tasks due today")])
            self._set_detail(*lines)

        elif self._level == Level.DIRECTORIES:
            d = self._current_items[idx]
            proj = d.project_path if d.project_path else "No attached project"
            _total, _done = stats_cmds.directory_stats(d.id)
            theme_icon, pattern = _level_theme(d.level)
            badge_color = pattern[0][0]
            lines: list[str | list] = [
                [("head", f"{theme_icon} {d.name}"), ("dim", f" (id: {d.id})"), (badge_color, f"  \u2605 Lv.{d.level}")],
            ]
            _pct = round(_done / _total * 100) if _total > 0 and _done > 0 else 100
            _fa = "dim" if _done == 0 else _progress_gradient_attr(_pct)
            lines.append([("", "  "), *_hblock_bar(_pct, 14, fill_attr=_fa)])
            max_level = len(LEVEL_XP_THRESHOLDS)
            if d.level < max_level:
                current_tier = LEVEL_XP_THRESHOLDS[d.level - 1]
                next_tier = LEVEL_XP_THRESHOLDS[d.level]
                xp_in_tier = d.xp - current_tier
                tier_size = next_tier - current_tier
                xp_pct = round(xp_in_tier / tier_size * 100) if tier_size > 0 else 0
                xp_bar = _hblock_bar(xp_pct, 10, fill_attr=badge_color)
                lines.append([("", "  "), ("dim", "XP "), *xp_bar, ("dim", f"  {d.xp}/{next_tier}")])
            else:
                lines.append([("dim", f"  XP {d.xp}  \u2605 MAX")])
            lines.append([("c2", f"  Project: {proj}")])
            lines.append(["\nPress Enter to browse tasks."])
            archive_id = self._selected_archive_id
            announcements = task_cmds.get_announcements(archive_id=archive_id) if archive_id is not None else []
            if announcements:
                lines.append("")
                lines.append([("head", "  \uf0aa Announcements")])
                lines.append([("dim", "  " + "\u2500" * 30)])
                groups: dict[str, list] = {}
                for entry in announcements:
                    dname = entry["dir_name"]
                    if dname not in groups:
                        groups[dname] = []
                    groups[dname].append(entry)
                show_dir_header = len(groups) > 1
                for dir_name in sorted(groups.keys()):
                    if show_dir_header:
                        lines.append([("head", f"  [{dir_name}]")])
                    for entry in groups[dir_name]:
                        task = entry["task"]
                        rel = task_cmds.relative_deadline(task.deadline)
                        style = "error" if "Overdue" in rel else ("c3" if "today" in rel else "default")
                        lines.append([(style, f"    \u25cb {task.name}")])
                        lines.append([("dim", f"      {rel}")])
            else:
                lines.append([("c2", "  \u2713 No tasks due today")])
            self._set_detail(*lines)

        elif self._level == Level.TASKS:
            task = self._current_items[idx]
            self._selected_task_id = task.id
            self._selected_task_name = task.name
            self._show_task_detail(task)

        elif self._level == Level.NOTES:
            n = self._current_items[idx]
            lines: list[str | list] = []
            lines.append([("dim", f"Note #{n.id}")])
            lines.append("")
            if n.note:
                lines.extend(_render_markdown_to_urwid(n.note))
            if n.file_path:
                fp = os.path.abspath(n.file_path)
                if os.path.isfile(fp):
                    lines.append([("dim", f"\n{'─'*40}")])
                    lines.append([("c1", f"  {fp}")])
                    lines.append([("dim", f"{'─'*40}")])
                    try:
                        with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                            content = fh.read()
                        is_md = fp.lower().endswith((".md", ".markdown"))
                        file_lines = _render_markdown_to_urwid(content) if is_md else [l.rstrip("\n") for l in content.split("\n")]
                        max_lines = 300
                        for i, fl in enumerate(file_lines):
                            if i >= max_lines:
                                lines.append([("dim", f"  ... ({len(file_lines) - max_lines} more items)")])
                                break
                            lines.append(fl)
                    except OSError:
                        lines.append([("error", "  (error reading file)")])
                else:
                    lines.append([("error", f"\n  File not found: {fp}")])
            self._set_detail(*lines)

    def _show_task_detail(self, task) -> None:
        s = timer_mod.compute_schedule(task)

        status = "\u2713 Done" if task.finished else "\u25cb Pending"
        pinned_str = "\U0001f4cc Pinned" if task.pinned else "\u2014"
        deadline = task_cmds._display_date(task.deadline)
        if task.repeatable:
            day_str = task.repeat_on_specific_day
            day_suffix = f" ({day_str})" if day_str and day_str != "none" else ""
            repeat = f"{task.repeatable_type}{day_suffix}"
        else:
            repeat = "\u2014"
        fd = task_cmds._display_date(task.finished_date)

        self._detail_walker.clear()

        # Header
        self._detail_walker.append(Text([("head", f"\ueebf {task.name}"), ("dim", f" (id: {task.id})")]))
        self._detail_walker.append(Text(""))

        # Status block
        self._detail_walker.append(Text([
            ("head", "Pinned: "), pinned_str,
        ]))
        self._detail_walker.append(Text([
            ("head", "Status: "), ("done" if task.finished else "default", status),
        ]))
        rel = task_cmds.relative_deadline(task.deadline)
        dl_text = f"{rel} \u00b7 {deadline}" if rel else deadline
        self._detail_walker.append(Text([("head", "Deadline: "), dl_text]))
        self._detail_walker.append(Text([("head", "Repeat: "), repeat]))
        self._detail_walker.append(Text([("head", "Finished: "), fd]))
        self._detail_walker.append(Text(""))

        # Dependencies
        dep_ids = task_cmds.get_dependencies(task.id)
        if dep_ids:
            dep_map = task_cmds.get_tasks_by_ids(dep_ids)
            dep_names = [f"#{did} {dep_map[did].name}" if did in dep_map else f"#{did}" for did in dep_ids]
            self._detail_walker.append(Text([
                ("head", "Depends on: "), ", ".join(dep_names),
            ]))
        else:
            self._detail_walker.append(Text([("head", "Depends on: "), "\u2014"]))
        dependent_ids = task_cmds.get_dependents(task.id)
        if dependent_ids:
            dep_map = task_cmds.get_tasks_by_ids(dependent_ids)
            dep_names = [f"#{did} {dep_map[did].name}" if did in dep_map else f"#{did}" for did in dependent_ids]
            self._detail_walker.append(Text([
                ("head", "Blocks: "), ", ".join(dep_names),
            ]))
        self._detail_walker.append(Text(""))

        # Urgency / Difficulty / Time budget
        self._detail_walker.append(Text([
            ("head", "Urgency:   "), *_bar(task.urgency, 5), f"  {task.urgency}/5",
        ]))
        self._detail_walker.append(Text([
            ("head", "Difficulty: "), *_bar(task.difficulty, 5), f"  {task.difficulty}/5",
        ]))
        self._detail_walker.append(Text([
            ("head", "Time budget: "), f"{task.time_dedicated} min",
        ]))
        time_spent = timer_sessions.get_total_time_for_task(task.id)
        if time_spent:
            h, m = divmod(time_spent // 60, 60)
            spent_str = f"{h}h{m:02d}m" if h else f"{m}m"
            self._detail_walker.append(Text([
                ("head", "Time spent: "), spent_str,
            ]))
        xp_value = task.difficulty * (task.time_dedicated + 5)
        xp_color = "done" if task.finished else "c3"
        xp_label = "XP earned:" if task.finished else "XP value:"
        self._detail_walker.append(Text([
            ("head", f"  {xp_label} "), (xp_color, f"{xp_value}"),
        ]))
        self._detail_walker.append(Text(""))

        # Tags
        tags = tag_cmds.get_tags_for_task(task.id)
        if tags:
            self._detail_walker.append(Text([
                ("head", "Tags: "), ", ".join(t.name for t in tags),
            ]))
        else:
            self._detail_walker.append(Text([("head", "Tags: "), "\u2014"]))
        self._detail_walker.append(Text(""))

        # Description
        if task.description:
            self._detail_walker.append(Text(task.description))
            self._detail_walker.append(Text(""))

        # Subtasks
        subs = subtask_cmds.list_subtasks(task.id)
        if subs:
            self._detail_walker.append(Text([("head", "Subtasks:")]))
            for i, sub in enumerate(subs, 1):
                check = "\u2713" if sub.finished else "\u25cb"
                attr = "dim" if sub.finished else "default"
                self._detail_walker.append(Text([
                    (attr, f"  {check} {i}. {sub.content}"),
                ]))
            self._detail_walker.append(Text(""))

        # Pomodoro schedule
        if "error" in s:
            self._detail_walker.append(Text([
                ("error", s["error"]),
            ]))
            self._detail_walker.append(Text("Set a time budget to see the Pomodoro schedule."))
        else:
            self._detail_walker.append(Text([("head", "Pomodoro:")]))
            self._detail_walker.append(Text([
                "  ", ("head", "Work:  "), f"{s['work_minutes']}m  ({s['work_pct']}%)",
            ]))
            self._detail_walker.append(Text([
                "  ", ("head", "Break: "), f"{s['break_minutes']}m",
            ]))
            self._detail_walker.append(Text([
                "  ", ("head", "Segments: "), str(s["segment_count"]),
            ]))

            segs = s["segments"]
            if segs:
                self._detail_walker.append(Text(""))
                self._detail_walker.append(Text([("head", "Schedule:")]))
                dur_fmt = _dur(segs[0])
                self._detail_walker.append(Text(f"   0  {dur_fmt:>8}  INTRO"))
                idx = 1
                for i in range(s["difficulty"]):
                    wk = segs[1 + i * 2]
                    br = segs[1 + i * 2 + 1]
                    self._detail_walker.append(Text(f"  {idx:>3}:  {_dur(wk):>8}  work"))
                    idx += 1
                    self._detail_walker.append(Text(f"  {idx:>3}:  {_dur(br):>8}  break"))
                    idx += 1

    def _get_selected_id(self) -> int | None:
        if not self._current_items:
            return None
        idx = self._list_box.focus_position
        if idx < len(self._current_items):
            return self._current_items[idx].id
        return None

    def _get_selected_name(self) -> str | None:
        if not self._current_items:
            return None
        idx = self._list_box.focus_position
        if idx >= len(self._current_items):
            return None
        item = self._current_items[idx]
        if self._level == Level.NOTES:
            return item.note.split("\n")[0][:60]
        return item.name

    def _select(self) -> None:
        if not self._current_items:
            return
        idx = self._list_box.focus_position
        if idx >= len(self._current_items):
            return
        self._prev_level = self._level
        if self._level == Level.ARCHIVES:
            a = self._current_items[idx]
            self._selected_archive_id = a.id
            self._selected_archive_name = a.name
            self._level = Level.DIRECTORIES
            self._refresh_list()

        elif self._level == Level.DIRECTORIES:
            d = self._current_items[idx]
            self._selected_directory_id = d.id
            self._selected_directory_name = d.name
            self._level = Level.TASKS
            self._refresh_list()

        elif self._level == Level.TASKS:
            self._level = Level.NOTES
            self._refresh_list()

    def _go_back(self) -> None:
        self._prev_level = self._level
        if self._level == Level.NOTES:
            self._level = Level.TASKS
        elif self._level == Level.TASKS:
            if self._all_tasks_mode:
                self._all_tasks_mode = False
                self._level = Level.DIRECTORIES
            else:
                self._level = Level.DIRECTORIES
            self._selected_task_id = None
            self._selected_task_name = None
        elif self._level == Level.DIRECTORIES:
            self._level = Level.ARCHIVES
            self._selected_archive_id = None
            self._selected_archive_name = None
            self._selected_directory_id = None
            self._selected_directory_name = None
            self._selected_task_id = None
            self._selected_task_name = None
        self._refresh_list()

    def _handle_wizard_esc(self) -> None:
        self._prompt_handler = None
        self._wizard_stack.clear()
        self._current_prompt = ("standout", "\u276f ")
        self._cmd.set_caption(("standout", "\u276f "))
        self._cmd.set_edit_text("")
        self._focus_body()

    def _handle_submit(self, text: str) -> None:
        if self._prompt_handler:
            self._prompt_handler(text)
            return
        if not text:
            self._focus_body()
            return
        self._handle_command(text)
        if text and (not self._cmd_history or self._cmd_history[-1] != text):
            self._cmd_history.append(text)
            if len(self._cmd_history) > 50:
                self._cmd_history.pop(0)
        self._cmd_history_index = -1
        if not self._prompt_handler:
            self._focus_body()

    _CMD_DISPATCH: dict[str, str] = {
        "q": "_cmd_quit", "exit": "_cmd_quit",
        "h": "_show_help", "help": "_show_help",
        "a": "_cmd_add", "add": "_cmd_add",
        "at": "_cmd_add_with_file",
        "r": "_cmd_remove", "remove": "_cmd_remove", "d": "_cmd_remove",
        "e": "_cmd_edit", "edit": "_cmd_edit",
        "f": "_cmd_finish", "finish": "_cmd_finish",
        "shf": "_cmd_toggle_finished", "showFinished": "_cmd_toggle_finished",
        "hf": "_cmd_hide_finished", "hideFinished": "_cmd_hide_finished",
        "y": "_cmd_copy",
        "pin": "_cmd_pin",
        "unpin": "_cmd_unpin",
        "dup": "_cmd_duplicate",
        "standup": "_show_standup",
        "c": "_cmd_cancel", "cancel": "_cmd_cancel",
        "all": "_cmd_all_tasks",
        "mu": "_cmd_move_up",
        "md": "_cmd_move_down",
        "export": "_cmd_export",
        "importJSON": "_cmd_import_json",
        "importJSONtaskTemplateCopy": "_cmd_import_json_template",
        "importJSONnoteTemplateCopy": "_cmd_import_note_json_template",
        "bm": "_cmd_bulk_mark",
        "bd": "_cmd_bulk_delete",
        "bc": "_cmd_bulk_clear",
        "stats": "_show_stats",
        "update": "_cmd_update",
        "undo": "_undo_last_action",
        "week": "_show_week_view",
        "overdue": "_show_overdue_view",
        "schbar": "_show_schedule_bar",
        "ai": "_cmd_ai",
        "aii": "_cmd_aii_chat",
        "highlight": "_show_highlight_picker",
        "st": "_cmd_start_timer",
        "preset": "_cmd_preset",
        "ts": "_stop_timer", "timerStop": "_stop_timer",
        "pt": "_cmd_pause_timer", "pauseTimer": "_cmd_pause_timer",
        "rt": "_stop_timer", "resetTimer": "_stop_timer",
        "sound": "_cmd_sound_toggle",
        "sound on": "_cmd_sound_on",
        "sound off": "_cmd_sound_off",
        "su a": "_cmd_sort_urgency_asc",
        "su d": "_cmd_sort_urgency_desc",
        "sd a": "_cmd_sort_difficulty_asc",
        "sd d": "_cmd_sort_difficulty_desc",
        "sn a": "_cmd_sort_name_asc",
        "sn d": "_cmd_sort_name_desc",
        "sdl a": "_cmd_sort_deadline_asc",
        "sdl d": "_cmd_sort_deadline_desc",
        "sr": "_cmd_sort_reset",
        "ftc": "_cmd_filter_tag_clear",
        "focus": "_cmd_toggle_focus",
        "dedupn": "_cmd_note_dedup",
        "recalculateLevel": "_cmd_recalculate_level",
        "serve": "_cmd_serve",
        "review": "_cmd_review",
    }

    _CMD_PREFIX_DISPATCH: list[tuple[str, str]] = [
        ("depends ", "_cmd_depends_add"),
        ("undepends ", "_cmd_depends_remove"),
        ("subadd ", "_cmd_subtask_add"),
        ("subrm ", "_cmd_subtask_remove"),
        ("subdone ", "_cmd_subtask_toggle"),
        ("subedit ", "_cmd_subtask_edit"),
        ("snooze ", "_cmd_snooze"),
        ("select ", "_cmd_select"),
        ("mv ", "_cmd_move"),
        ("qa ", "_cmd_quick_add"),
        ("export ", "_cmd_export_path"),
        ("exportCurrent", "_cmd_export_current"),
        ("import ", "_cmd_import"),
        ("importExportedMerge ", "_cmd_import_exported_merge"),
        ("importExported ", "_cmd_import_exported"),
        ("importJSON ", "_cmd_import_json_path"),
        ("bt ", "_cmd_bulk_tag"),
        ("bv ", "_cmd_bulk_move"),
        ("aii ", "_cmd_aii_subcmd"),
        ("gs ", "_cmd_global_search"),
        ("tag ", "_cmd_tag_add"),
        ("untag ", "_cmd_tag_remove"),
        ("ft ", "_cmd_filter_tag"),
        ("st ", "_cmd_start_timer_preset"),
        ("preset ", "_cmd_preset"),
        ("sound ", "_cmd_sound_custom"),
        ("attachProject ", "_cmd_attach_project"),
        ("serve ", "_cmd_serve_subcmd"),
    ]

    def _handle_command(self, cmd: str) -> None:
        method_name = self._CMD_DISPATCH.get(cmd)
        if method_name:
            getattr(self, method_name)()
            return
        for prefix, method_name in self._CMD_PREFIX_DISPATCH:
            if cmd.startswith(prefix):
                getattr(self, method_name)(cmd)
                return
        self._set_timed_caption("error", f"Unknown command: {cmd} ")
        self._focus_body()

    def _cmd_quit(self) -> None:
        raise urwid.ExitMainLoop()

    def _cmd_toggle_finished(self) -> None:
        self._show_finished = not self._show_finished
        self._refresh_list()

    def _cmd_hide_finished(self) -> None:
        self._show_finished = False
        self._refresh_list()

    def _cmd_pin(self) -> None:
        if self._level == Level.TASKS:
            sid = self._get_selected_id()
            if sid is not None:
                task_cmds.edit_task(sid, pinned=1)
                self._set_timed_caption("done", "Task pinned ")
                self._refresh_list()

    def _cmd_unpin(self) -> None:
        if self._level == Level.TASKS:
            sid = self._get_selected_id()
            if sid is not None:
                task_cmds.edit_task(sid, pinned=0)
                self._set_timed_caption("done", "Task unpinned ")
                self._refresh_list()

    def _cmd_depends_add(self, cmd: str) -> None:
        if self._level == Level.TASKS:
            sid = self._get_selected_id()
            if sid is not None:
                try:
                    dep_id = int(cmd.split(" ", 1)[1])
                    task_cmds.add_dependency(sid, dep_id)
                    self._set_timed_caption("done", f"Dependency on task {dep_id} added ")
                    self._refresh_list()
                except ValueError as e:
                    self._set_timed_caption("error", f"{e} ")

    def _cmd_depends_remove(self, cmd: str) -> None:
        if self._level == Level.TASKS:
            sid = self._get_selected_id()
            if sid is not None:
                try:
                    dep_id = int(cmd.split(" ", 1)[1])
                    if task_cmds.remove_dependency(sid, dep_id):
                        self._set_timed_caption("done", f"Dependency on task {dep_id} removed ")
                    else:
                        self._set_timed_caption("error", "Dependency not found ")
                except ValueError:
                    pass
                self._refresh_list()

    def _cmd_subtask_add(self, cmd: str) -> None:
        if self._level == Level.TASKS:
            sid = self._get_selected_id()
            if sid is not None:
                content = cmd.split(" ", 1)[1].strip()
                if content:
                    sub = subtask_cmds.create_subtask(sid, content)
                    if sub:
                        undo_cmds.push("subtask_create", {"id": sub.id})
                    self._set_timed_caption("done", "Subtask added ")
                    self._show_detail()

    def _cmd_subtask_remove(self, cmd: str) -> None:
        if self._level == Level.TASKS:
            sid = self._get_selected_id()
            if sid is not None:
                try:
                    nth = int(cmd.split(" ", 1)[1])
                    subs = subtask_cmds.list_subtasks(sid)
                    if 1 <= nth <= len(subs):
                        sub = subs[nth - 1]
                        undo_cmds.push("subtask_create", {
                            "id": sub.id, "task_id": sub.task_id,
                            "content": sub.content, "finished": sub.finished,
                            "position": sub.position,
                        })
                        subtask_cmds.delete_subtask(sub.id)
                        self._set_timed_caption("done", f"Subtask {nth} removed ")
                        self._show_detail()
                    else:
                        self._set_timed_caption("error", f"Subtask #{nth} not found ")
                except ValueError:
                    pass

    def _cmd_subtask_toggle(self, cmd: str) -> None:
        if self._level == Level.TASKS:
            sid = self._get_selected_id()
            if sid is not None:
                try:
                    nth = int(cmd.split(" ", 1)[1])
                    subs = subtask_cmds.list_subtasks(sid)
                    if 1 <= nth <= len(subs):
                        sub = subs[nth - 1]
                        undo_cmds.push("subtask_toggle", {
                            "id": sub.id, "finished": sub.finished,
                        })
                        if sub.finished:
                            subtask_cmds.mark_not_done(sub.id)
                        else:
                            subtask_cmds.mark_done(sub.id)
                        self._set_timed_caption("done", f"Subtask {nth} toggled ")
                        self._show_detail()
                    else:
                        self._set_timed_caption("error", f"Subtask #{nth} not found ")
                except ValueError:
                    pass

    def _cmd_subtask_edit(self, cmd: str) -> None:
        if self._level == Level.TASKS:
            sid = self._get_selected_id()
            if sid is not None:
                rest = cmd[len("subedit "):].strip()
                space_idx = rest.find(" ")
                if space_idx > 0:
                    try:
                        nth = int(rest[:space_idx])
                        new_content = rest[space_idx + 1:]
                        subs = subtask_cmds.list_subtasks(sid)
                        if 1 <= nth <= len(subs):
                            subtask_cmds.update_subtask(subs[nth - 1].id, new_content)
                            self._set_timed_caption("done", f"Subtask {nth} updated ")
                            self._show_detail()
                        else:
                            self._set_timed_caption("error", f"Subtask #{nth} not found ")
                    except ValueError:
                        self._set_timed_caption("error", "Usage: subedit <#> <new content> ")
                else:
                    self._set_timed_caption("error", "Usage: subedit <#> <new content> ")

    def _cmd_snooze(self, cmd: str) -> None:
        if self._level == Level.TASKS:
            sid = self._get_selected_id()
            if sid is not None:
                try:
                    days = int(cmd.split(" ", 1)[1])
                    task = task_cmds.get_task(sid)
                    if task and task.deadline != "none":
                        from datetime import datetime, timedelta
                        new_deadline = (datetime.strptime(task.deadline, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")
                        task_cmds.edit_task(sid, deadline=new_deadline)
                        self._set_timed_caption("done", f"Deadline bumped {days}d ")
                    else:
                        self._set_timed_caption("error", "Task has no deadline ")
                except ValueError:
                    pass
                self._refresh_list()

    def _cmd_duplicate(self) -> None:
        if self._level == Level.TASKS:
            sid = self._get_selected_id()
            if sid is not None:
                task = task_cmds.get_task(sid)
                if task:
                    dup = task_cmds.create_task(
                        task.directory_id, task.name + " (copy)",
                        description=task.description, deadline=task.deadline,
                        urgency=task.urgency, difficulty=task.difficulty,
                        time_dedicated=task.time_dedicated,
                        repeatable=task.repeatable, repeatable_type=task.repeatable_type,
                        has_to_be_completed_to_repeat=task.has_to_be_completed_to_repeat,
                        repeat_on_specific_day=task.repeat_on_specific_day,
                    )
                    if dup:
                        undo_cmds.push("task_create", {"task_id": dup.id})
                    self._set_timed_caption("done", "Task duplicated ")
                    self._refresh_list()

    def _cmd_select(self, cmd: str) -> None:
        if self._level == Level.TASKS:
            arg = cmd.split(" ", 1)[1].strip() if " " in cmd else ""
            conn = db_mod.get_conn()
            today_str = date.today().isoformat()
            if arg == "overdue":
                rows = conn.execute(
                    "SELECT id FROM tasks WHERE finished = 0 AND deadline != 'none' AND deadline < ?",
                    (today_str,),
                ).fetchall()
            elif arg == "due today":
                rows = conn.execute(
                    "SELECT id FROM tasks WHERE finished = 0 AND deadline = ?",
                    (today_str,),
                ).fetchall()
            elif arg == "pinned":
                rows = conn.execute(
                    "SELECT id FROM tasks WHERE pinned = 1",
                ).fetchall()
            else:
                self._set_timed_caption("error", "Usage: select overdue | due today | pinned ")
                return
            self._bulk_selection = {r["id"] for r in rows}
            self._set_timed_caption("done", f"Selected {len(self._bulk_selection)} tasks ")
            self._refresh_list()

    def _cmd_cancel(self) -> None:
        self._prompt_handler = None
        self._wizard_stack.clear()
        self._current_prompt = ("standout", "\u276f ")
        self._cmd.set_caption(("standout", "\u276f "))
        self._bulk_selection.clear()
        self._refresh_list()
        self._focus_body()

    def _cmd_all_tasks(self) -> None:
        if self._selected_archive_id is not None:
            self._all_tasks_mode = True
            self._level = Level.TASKS
            self._refresh_list()

    def _cmd_move_up(self) -> None:
        if self._level == Level.TASKS and not self._sort_field:
            sid = self._get_selected_id()
            if sid is not None and task_cmds.move_task_up(sid):
                self._refresh_list()

    def _cmd_move_down(self) -> None:
        if self._level == Level.TASKS and not self._sort_field:
            sid = self._get_selected_id()
            if sid is not None and task_cmds.move_task_down(sid):
                self._refresh_list()

    def _cmd_move(self, cmd: str) -> None:
        try:
            target_id = int(cmd.split(" ", 1)[1])
            sid = self._get_selected_id()
            if sid is None:
                return
            if self._level == Level.TASKS:
                if task_cmds.move_task(sid, target_id):
                    self._set_timed_caption("done", "Moved task ")
                    self._refresh_list()
            elif self._level == Level.DIRECTORIES:
                if directory_cmds.move_directory(sid, target_id):
                    self._set_timed_caption("done", "Moved directory ")
                    self._refresh_list()
        except ValueError:
            pass

    def _cmd_quick_add(self, cmd: str) -> None:
        if self._level == Level.TASKS and self._selected_directory_id is not None:
            rest = cmd[3:].strip()
            name = rest
            urgency = 1
            difficulty = 1
            time_dedicated = 0
            deadline = "none"
            for token in rest.split():
                if token.startswith("u:") or token.startswith("U:"):
                    try:
                        urgency = int(token[2:])
                    except ValueError:
                        pass
                elif token.startswith("d:") or token.startswith("D:"):
                    try:
                        difficulty = int(token[2:])
                    except ValueError:
                        pass
                elif token.startswith("t:") or token.startswith("T:"):
                    try:
                        time_dedicated = int(token[2:])
                    except ValueError:
                        pass
                elif token.startswith("dl:") or token.startswith("DL:"):
                    try:
                        deadline = task_cmds._normalize_date(token[3:])
                    except ValueError:
                        deadline = "none"
            for token in rest.split():
                if any(token.startswith(p) for p in ("u:", "U:", "d:", "D:", "t:", "T:", "dl:", "DL:")):
                    name = name.replace(token, "", 1).strip()
            if name:
                task = task_cmds.create_task(
                    self._selected_directory_id, name,
                    urgency=urgency, difficulty=difficulty,
                    time_dedicated=time_dedicated, deadline=deadline,
                )
                if task:
                    undo_cmds.push("task_create", {"task_id": task.id})
                self._set_timed_caption("done", "Task added ")
                self._refresh_list()

    def _cmd_export(self) -> None:
        if io_cmds.export_data("/tmp/taskwatch_export.json"):
            self._set_timed_caption("done", "Exported to /tmp/taskwatch_export.json ", 3)
        else:
            self._set_timed_caption("error", "Export failed ", 3)

    def _cmd_export_path(self, cmd: str) -> None:
        path = cmd.split(" ", 1)[1].strip()
        if io_cmds.export_data(path):
            self._set_timed_caption("done", f"Exported to {path} ", 3)
        else:
            self._set_timed_caption("error", "Export failed ", 3)

    def _get_project_path_for_selection(self) -> str | None:
        dir_id = None
        if self._level == Level.DIRECTORIES:
            dir_id = self._get_selected_id()
        elif self._level == Level.TASKS:
            sid = self._get_selected_id()
            if sid is not None:
                task = task_cmds.get_task(sid)
                if task:
                    dir_id = task.directory_id
        if dir_id is not None:
            d = directory_cmds.get_directory(dir_id)
            if d and d.project_path:
                return d.project_path
        return None

    def _cmd_export_current(self, cmd: str) -> None:
        parts = cmd.split(" ", 1)
        path = parts[1].strip() if len(parts) > 1 else None
        if not path:
            pp = self._get_project_path_for_selection()
            if pp:
                path = str(Path(pp) / "taskwatch_export_current.json")
        sid = self._get_selected_id()
        if sid is None:
            self._set_timed_caption("error", "Nothing selected ")
            return
        if io_cmds.export_current_item(self._level.name, sid, path):
            out = path or "/tmp/taskwatch_export_current.json"
            self._set_timed_caption("done", f"Exported to {out} ", 3)
        else:
            self._set_timed_caption("error", "Export current failed ")

    def _cmd_import(self, cmd: str) -> None:
        path = cmd.split(" ", 1)[1].strip()
        result = io_cmds.import_data(path)
        self._set_timed_caption("done" if "failed" not in result else "error", f"{result} ", 3)
        self._refresh_list()

    def _resolve_import_path(self, cmd: str) -> str | None:
        parts = cmd.split(" ", 1)
        if len(parts) > 1 and parts[1].strip():
            arg = parts[1].strip()
            if arg.startswith("--name "):
                dir_name = arg[7:].strip()
                dir_id = task_cmds.resolve_directory(dir_name)
                if dir_id is None:
                    return None
                d = directory_cmds.get_directory(dir_id)
                if d and d.project_path:
                    jl = list(Path(d.project_path).glob("*.json"))
                    if not jl:
                        return None
                    if len(jl) == 1:
                        return str(jl[0])
                    for jf in jl:
                        if jf.name == "taskwatch_export_current.json":
                            return str(jf)
                    return None
                return None
            return arg
        pp = self._get_project_path_for_selection()
        if pp:
            return str(Path(pp) / "taskwatch_export_current.json")
        return None

    def _cmd_import_exported(self, cmd: str) -> None:
        path = self._resolve_import_path(cmd)
        if not path:
            self._set_timed_caption("error", "Usage: :importExported <path> | --name <dir>  (or attach a project first) ")
            return
        result = io_cmds.import_exported_item(
            path,
            self._level.name,
            self._selected_archive_id,
            self._selected_directory_id,
            self._selected_task_id,
        )
        ok = "failed" not in result.lower() and "error" not in result.lower()
        self._set_timed_caption("done" if ok else "error", f"{result} ", 3)
        self._refresh_list()

    def _cmd_import_exported_merge(self, cmd: str) -> None:
        path = self._resolve_import_path(cmd)
        if not path:
            self._set_timed_caption("error", "Usage: :importExportedMerge <path> | --name <dir>  (no attach a project directory) ")
            return
        result = io_cmds.import_exported_item(
            path,
            self._level.name,
            self._selected_archive_id,
            self._selected_directory_id,
            self._selected_task_id,
            merge=True,
        )
        ok = "failed" not in result.lower() and "error" not in result.lower()
        self._set_timed_caption("done" if ok else "error", f"{result} ", 3)
        self._refresh_list()

    def _cmd_import_json(self) -> None:
        if self._level == Level.NOTES:
            if self._selected_task_id is None:
                self._set_timed_caption("error", "No task selected ")
                return
            self._show_import_notes_json_panel()
            return
        if self._selected_directory_id is None:
            self._set_timed_caption("error", "Select a directory first ")
            return
        self._show_import_json_panel()

    def _cmd_import_json_path(self, cmd: str) -> None:
        if self._level == Level.NOTES:
            if self._selected_task_id is None:
                self._set_timed_caption("error", "No task selected ")
                return
            path = cmd.split(" ", 1)[1].strip()
            self._cmd_import_note_json_file(path)
            return
        if self._selected_directory_id is None:
            self._set_timed_caption("error", "Select a directory first ")
            return
        path = cmd.split(" ", 1)[1].strip()
        self._cmd_import_json_file(path)

    def _cmd_bulk_mark(self) -> None:
        if self._level == Level.TASKS and self._bulk_selection:
            for tid in list(self._bulk_selection):
                task_cmds.mark_done(tid)
            self._bulk_selection.clear()
            self._refresh_list()

    def _cmd_bulk_delete(self) -> None:
        if self._level == Level.TASKS and self._bulk_selection:
            self._start_wizard(
                f"Delete {len(self._bulk_selection)} tasks? (y/n): ",
                self._wiz_bulk_delete,
            )

    def _cmd_bulk_clear(self) -> None:
        self._bulk_selection.clear()
        self._refresh_list()

    def _cmd_bulk_tag(self, cmd: str) -> None:
        if self._level == Level.TASKS and self._bulk_selection:
            tag_name = cmd.split(" ", 1)[1].strip()
            if tag_name:
                for tid in list(self._bulk_selection):
                    tag_cmds.add_tag_to_task(tid, tag_name)
                self._bulk_selection.clear()
                self._refresh_list()

    def _cmd_bulk_move(self, cmd: str) -> None:
        if self._level == Level.TASKS and self._bulk_selection:
            try:
                target_dir = int(cmd.split(" ", 1)[1])
                for tid in list(self._bulk_selection):
                    task_cmds.move_task(tid, target_dir)
                self._bulk_selection.clear()
                self._refresh_list()
            except ValueError:
                pass

    def _cmd_aii_subcmd(self, cmd: str) -> None:
        self._handle_aii_subcmd(cmd[4:].strip())

    def _cmd_global_search(self, cmd: str) -> None:
        query = cmd.split(" ", 1)[1].strip()
        if query:
            self._show_global_search(query)

    def _cmd_tag_add(self, cmd: str) -> None:
        tag_name = cmd.split(" ", 1)[1].strip()
        if tag_name and self._selected_task_id is not None:
            undo_cmds.push("tag_remove", {
                "task_id": self._selected_task_id, "tag_name": tag_name,
            })
            tag_cmds.add_tag_to_task(self._selected_task_id, tag_name)
            self._refresh_list()

    def _cmd_tag_remove(self, cmd: str) -> None:
        tag_name = cmd.split(" ", 1)[1].strip()
        if tag_name and self._selected_task_id is not None:
            undo_cmds.push("tag_add", {
                "task_id": self._selected_task_id, "tag_name": tag_name,
            })
            tag_cmds.remove_tag_from_task(self._selected_task_id, tag_name)
            self._refresh_list()

    def _cmd_filter_tag(self, cmd: str) -> None:
        if self._level == Level.TASKS:
            self._filter_tag = cmd.split(" ", 1)[1].strip()
            self._refresh_list()

    def _cmd_filter_tag_clear(self) -> None:
        self._filter_tag = None
        self._filter_text = ""
        if self._search_debounce_alarm is not None:
            self._loop.remove_alarm(self._search_debounce_alarm)
            self._search_debounce_alarm = None
        self._refresh_list()

    def _cmd_start_timer(self) -> None:
        if self._selected_task_id is not None:
            t = task_cmds.get_task(self._selected_task_id)
            if t:
                self._start_timer_for_task(t)

    def _cmd_start_timer_preset(self, cmd: str) -> None:
        arg = cmd.split(" ", 1)[1]
        if arg in self._timer_presets:
            preset = self._timer_presets[arg]
            self._start_timer_from_preset(preset, name=arg)
        else:
            try:
                mins = int(arg)
                if mins > 0:
                    self._start_timer(mins)
            except ValueError:
                self._set_timed_caption("error", f"Unknown preset '{arg}' ")

    def _cmd_pause_timer(self) -> None:
        self._timer_paused = not self._timer_paused
        self._write_timer_state({"paused": self._timer_paused})
        self._update_clock_display()

    def _cmd_sound_on(self) -> None:
        self._cmd_sound_set_enabled(True)

    def _cmd_sound_off(self) -> None:
        self._cmd_sound_set_enabled(False)

    def _cmd_sort_urgency_asc(self) -> None:
        self._sort_field = "urgency"
        self._sort_dir = "asc"
        if self._level == Level.TASKS:
            self._refresh_list()

    def _cmd_sort_urgency_desc(self) -> None:
        self._sort_field = "urgency"
        self._sort_dir = "desc"
        if self._level == Level.TASKS:
            self._refresh_list()

    def _cmd_sort_difficulty_asc(self) -> None:
        self._sort_field = "difficulty"
        self._sort_dir = "asc"
        if self._level == Level.TASKS:
            self._refresh_list()

    def _cmd_sort_difficulty_desc(self) -> None:
        self._sort_field = "difficulty"
        self._sort_dir = "desc"
        if self._level == Level.TASKS:
            self._refresh_list()

    def _cmd_sort_name_asc(self) -> None:
        self._sort_field = "name"
        self._sort_dir = "asc"
        if self._level == Level.TASKS:
            self._refresh_list()

    def _cmd_sort_name_desc(self) -> None:
        self._sort_field = "name"
        self._sort_dir = "desc"
        if self._level == Level.TASKS:
            self._refresh_list()

    def _cmd_sort_deadline_asc(self) -> None:
        self._sort_field = "deadline"
        self._sort_dir = "asc"
        if self._level == Level.TASKS:
            self._refresh_list()

    def _cmd_sort_deadline_desc(self) -> None:
        self._sort_field = "deadline"
        self._sort_dir = "desc"
        if self._level == Level.TASKS:
            self._refresh_list()

    def _cmd_sort_reset(self) -> None:
        self._sort_field = None
        self._sort_dir = "asc"
        if self._level == Level.TASKS:
            self._refresh_list()

    def _cmd_add(self) -> None:
        if self._level == Level.ARCHIVES:
            self._start_wizard("Archive name: ", self._wiz_archive_name)
        elif self._level == Level.DIRECTORIES:
            self._start_wizard("Directory name: ", self._wiz_dir_name)
        elif self._level == Level.TASKS:
            self._start_wizard("Task name: ", self._wiz_task_name)
        elif self._level == Level.NOTES:
            today = date.today().strftime("%d/%m/%Y")
            self._start_wizard(
                "Note content (leave empty for date-only): ",
                partial(self._wiz_note_content, today),
            )

    def _cmd_add_with_file(self) -> None:
        if self._level == Level.NOTES and self._selected_task_id is not None:
            self._start_wizard(
                "Note content (leave empty for date-only): ",
                self._wiz_note_content_with_file,
            )

    def _cmd_edit(self) -> None:
        sid = self._get_selected_id()
        if sid is None:
            return
        if self._level == Level.TASKS:
            task = task_cmds.get_task(sid)
            if task:
                self._edit_ctx = {
                    "task_id": task.id,
                    "name": task.name,
                    "description": task.description,
                    "urgency": task.urgency,
                    "difficulty": task.difficulty,
                    "time_dedicated": task.time_dedicated,
                    "deadline": task.deadline,
                    "repeatable": task.repeatable,
                    "repeatable_type": task.repeatable_type,
                    "has_to_be_completed_to_repeat": task.has_to_be_completed_to_repeat,
                    "repeat_on_specific_day": task.repeat_on_specific_day,
                }
                self._start_wizard(
                    f"Task name [{task.name}]: ",
                    self._wiz_edit_task_name,
                )
        elif self._level == Level.DIRECTORIES:
            dir_ = directory_cmds.get_directory(sid)
            if dir_:
                undo_cmds.push("directory_edit", {"id": dir_.id, "name": dir_.name})
                self._start_wizard(
                    f"New name [{dir_.name}]: ",
                    partial(self._wiz_edit_dir, dir_.id, dir_.name),
                )
        elif self._level == Level.ARCHIVES:
            arch = archive_cmds.get_archive(sid)
            if arch:
                undo_cmds.push("archive_edit", {"id": arch.id, "name": arch.name})
                self._start_wizard(
                    f"New name [{arch.name}]: ",
                    partial(self._wiz_edit_archive, arch.id, arch.name),
                )
        elif self._level == Level.NOTES:
            note = note_cmds.get_note(sid)
            if note:
                undo_cmds.push("note_edit", {
                    "id": note.id,
                    "note": note.note,
                    "date": note.date,
                    "file_path": note.file_path,
                    "created_at": note.created_at,
                })
                self._start_wizard(
                    f"Edit note ({note.note[:40]}...): ",
                    partial(self._wiz_edit_note, note.id),
                )

    def _cmd_remove(self) -> None:
        sid = self._get_selected_id()
        if sid is None:
            return
        if self._level == Level.TASKS:
            self._start_wizard(
                f"Delete task {sid}? (y/n): ",
                partial(self._wiz_confirm_delete, sid),
            )
        elif self._level == Level.DIRECTORIES:
            self._start_wizard(
                f"Delete directory {sid}? (y/n): ",
                partial(self._wiz_confirm_delete_directory, sid),
            )
        elif self._level == Level.ARCHIVES:
            self._start_wizard(
                f"Delete archive {sid}? (y/n): ",
                partial(self._wiz_confirm_delete_archive, sid),
            )
        elif self._level == Level.NOTES:
            self._start_wizard(
                f"Delete note {sid}? (y/n): ",
                partial(self._wiz_confirm_delete_note, sid),
            )

    def _cmd_note_dedup(self) -> None:
        if self._level != Level.NOTES or self._selected_task_id is None:
            self._set_timed_caption("error", "Not in notes view ")
            return
        count = note_cmds.dedup_notes(self._selected_task_id)
        if count:
            self._set_timed_caption("done", f"Deleted {count} duplicate note(s) ")
        else:
            self._set_timed_caption("info", "No duplicate notes found ")

    def _cmd_recalculate_level(self) -> None:
        count = directory_cmds.recalculate_all_levels()
        if self._level == Level.DIRECTORIES:
            self._refresh_list()
            self._show_detail()
        self._set_timed_caption("done", f"Recalculated levels for {count} director(ies) ")

    def _save_edit_task(self) -> None:
        ctx = self._edit_ctx
        old_task = task_cmds.get_task(ctx["task_id"])
        if old_task:
            undo_cmds.push("task_edit", {
                "task_id": ctx["task_id"],
                "name": old_task.name,
                "description": old_task.description,
                "urgency": old_task.urgency,
                "difficulty": old_task.difficulty,
                "time_dedicated": old_task.time_dedicated,
                "deadline": old_task.deadline,
                "repeatable": old_task.repeatable,
                "repeatable_type": old_task.repeatable_type,
                "has_to_be_completed_to_repeat": old_task.has_to_be_completed_to_repeat,
                "repeat_on_specific_day": old_task.repeat_on_specific_day,
            })
        task_cmds.edit_task(
            ctx["task_id"],
            name=ctx["name"],
            description=ctx.get("description", ""),
            urgency=ctx["urgency"],
            difficulty=ctx["difficulty"],
            time_dedicated=ctx["time_dedicated"],
            deadline=ctx["deadline"],
            repeatable=ctx.get("repeatable", False),
            repeatable_type=ctx.get("repeatable_type", "none"),
            has_to_be_completed_to_repeat=ctx.get("has_to_be_completed_to_repeat", True),
            repeat_on_specific_day=ctx.get("repeat_on_specific_day", "none"),
        )
        self._edit_ctx = None
        self._end_wizard()

    def _cmd_finish(self) -> None:
        if self._level != Level.TASKS:
            return
        sid = self._get_selected_id()
        if sid is None:
            return
        task = task_cmds.get_task(sid)
        if not task:
            return
        if not task.finished and task_cmds.is_blocked(sid):
            self._set_timed_caption("error", "Blocked by unfinished dependencies ")
            return
        if task.finished:
            undo_cmds.push("task_unfinish", {"task_id": sid})
            task_cmds.mark_not_done(sid)
        else:
            undo_cmds.push("task_finish", {"task_id": sid})
            task_cmds.mark_done(sid)
            phrase = random.choice(CELEBRATION_MESSAGES)
            streak = stats_cmds.get_completion_streak()
            suffix = f" \U0001f525 {streak}d" if streak > 1 else ""
            self._set_timed_caption("done", f"{phrase}{suffix} ")
        self._refresh_list()

    def _enter_search_mode(self) -> None:
        if self._prompt_handler is not None:
            return
        self._in_search_mode = True
        self._filter_text = ""
        self._current_prompt = ("/ ", "/")
        self._cmd.set_caption(("/ ", "/"))
        self._cmd.set_edit_text("")
        self._frame.focus_position = "footer"

    def _exit_search_mode(self) -> None:
        self._in_search_mode = False
        self._filter_text = ""
        self._current_prompt = ("standout", "\u276f ")
        self._cmd.set_caption(("standout", "\u276f "))
        self._cmd.set_edit_text("")
        if self._search_debounce_alarm is not None:
            self._loop.remove_alarm(self._search_debounce_alarm)
            self._search_debounce_alarm = None
        self._refresh_list()
        self._focus_body()

    def _on_search_change(self, text: str) -> None:
        self._filter_text = text
        if self._search_debounce_alarm is not None:
            self._loop.remove_alarm(self._search_debounce_alarm)
        self._search_debounce_alarm = self._loop.set_alarm_in(0.2, self._do_search_refresh)

    def _do_search_refresh(self, loop: object, data: object) -> None:
        self._search_debounce_alarm = None
        self._refresh_list()

    def _apply_search(self) -> None:
        self._in_search_mode = False
        self._current_prompt = ("standout", "\u276f ")
        self._cmd.set_caption(("standout", "\u276f "))
        self._cmd.set_edit_text("")
        self._focus_body()

    def _complete_command(self) -> None:
        text = self._cmd.get_edit_text().strip()
        if not text:
            return
        if not self._tab_matches:
            self._tab_matches = [c for c in COMMANDS if c.startswith(text) or (c.endswith(" ") and text.startswith(c.rstrip()))]
            self._tab_matches.sort()
            self._tab_index = -1
        if not self._tab_matches:
            return
        self._tab_index = (self._tab_index + 1) % len(self._tab_matches)
        completed = self._tab_matches[self._tab_index]
        self._cmd.set_edit_text(completed)
        self._cmd.set_edit_pos(len(completed))

    def _show_import_json_panel(self) -> None:
        overlay = ImportJSONOverlay(self, target_id=self._selected_directory_id, title="Import Tasks JSON")
        self._import_json_overlay = Overlay(
            overlay,
            self._frame,
            align="center",
            width=("relative", 80),
            valign="middle",
            height=("relative", 60),
        )
        self._loop.widget = self._import_json_overlay

    def _show_import_notes_json_panel(self) -> None:
        overlay = ImportJSONOverlay(
            self,
            import_fn=io_cmds.import_notes_json,
            target_id=self._selected_task_id,
            title="Import Notes JSON",
        )
        self._import_json_overlay = Overlay(
            overlay,
            self._frame,
            align="center",
            width=("relative", 80),
            valign="middle",
            height=("relative", 60),
        )
        self._loop.widget = self._import_json_overlay

    def _close_import_json_panel(self) -> None:
        self._import_json_overlay = None
        self._loop.widget = self._frame

    def _open_global_search(self) -> None:
        overlay = GlobalSearchOverlay(self)
        self._global_search_overlay = Overlay(
            overlay,
            self._frame,
            align="center",
            width=("relative", 70),
            valign="middle",
            height=("relative", 70),
        )
        self._loop.widget = self._global_search_overlay

    def _close_global_search(self) -> None:
        self._global_search_overlay = None
        self._loop.widget = self._frame

    def _navigate_from_search(self, result_data: tuple) -> None:
        kind, item_id, extra = result_data
        self._close_global_search()
        if kind == "task":
            task = task_cmds.get_task(item_id)
            if task is None:
                return
            d = directory_cmds.get_directory(task.directory_id)
            if d is None:
                return
            self._selected_archive_id = d.archive_id
            self._selected_archive_name = None
            self._selected_directory_id = d.id
            self._selected_directory_name = d.name
            self._all_tasks_mode = False
            self._filter_tag = None
            self._level = Level.TASKS
            self._refresh_list()
            for idx, t in enumerate(self._current_items or []):
                if t.id == task.id:
                    self._list_box.focus_position = idx
                    break
        elif kind == "directory":
            d = directory_cmds.get_directory(item_id)
            if d is None:
                return
            self._selected_archive_id = d.archive_id
            self._selected_archive_name = None
            self._selected_directory_id = d.id
            self._selected_directory_name = d.name
            self._all_tasks_mode = False
            self._filter_tag = None
            self._level = Level.TASKS
            self._refresh_list()
        elif kind == "tag":
            self._filter_tag = extra
            if self._level != Level.TASKS:
                self._level = Level.TASKS
            self._refresh_list()

    def _show_help(self) -> None:
        if self._loop.widget is not self._frame:
            self._prev_overlay = self._loop.widget
            ctx_lines: list[str] = []
            ctx_lines.append("TaskWatch+ Help\n")
            if self._global_search_overlay is not None:
                ctx_lines.append("Global Search:\n")
                ctx_lines.append("  \u2191/\u2193          Navigate results\n")
                ctx_lines.append("  Enter        Jump to result\n")
                ctx_lines.append("  Esc          Close search\n")
                ctx_lines.append("  ?            This help\n")
            else:
                ctx_lines.append("Press esc/q to close.\n")
            ctx_lines.append("\n  Press any key to close.")
            ctx_w = LineBox(SelectableText("\n".join(ctx_lines)))
            self._help_overlay = Overlay(
                ctx_w, self._loop.widget,
                align="center", width=("relative", 60),
                valign="middle", height=("relative", 40),
            )
            self._loop.widget = self._help_overlay
            return

        overlay = HelpSearchOverlay(self)
        self._help_overlay = Overlay(
            overlay,
            self._frame,
            align="center",
            width=("relative", 80),
            valign="middle",
            height=("relative", 80),
        )
        self._loop.widget = self._help_overlay

    def _close_help(self) -> None:
        self._help_overlay = None
        self._loop.widget = self._frame

    def _show_stats(self) -> None:
        import shutil

        s = stats_cmds.compute_stats()

        term_width = shutil.get_terminal_size().columns
        bar_width = max(12, min(26, int(term_width * 0.24)))
        th, tm = divmod(s["total_time"], 60)

        walker: list[Text] = []

        def add(text: str | list) -> None:
            walker.append(Text(text))

        def section(title: str) -> None:
            add([("head", f"  \u25b8 {title}")])

        # ── Title + compact summary ──
        add([("head", "  \u2694  TaskWatch+ Stats")])
        add([
            ("dim", "  Tasks "), str(s["total"]),
            ("dim", "  Done "), f"{s['finished']}/{s['total']}",
            ("dim", "  Pending "), str(s["pending"]),
            ("dim", "  Time "), f"{th}h{tm:02d}m",
            ("dim", "  Tags "), str(s["total_tags"]),
            ("dim", "  Focus "), str(s["focus_score"]),
        ])

        # ── Completion (smooth horizontal-block bar, gradient colour) ──
        add([("head", "  \u25b8 Completion  "),
             *_hblock_bar(s["completion_pct"], bar_width, fill_attr=_progress_gradient_attr(s["completion_pct"])),
             f"  {s['completion_pct']:>3}%"])

        # ── Status (inline, semantic colours) ──
        streak_str = f" \U0001f525 {s['streak']}" if s["streak"] > 0 else ""
        add([
            ("head", "  \u25b8 Status    "),
            ("error", f"\u26a0 {s['overdue']:>2}"),
            ("dim", "  "),
            ("warn", f"\u2713 today {s['today_completed']:>2}"),
            ("dim", "  "),
            ("done", f"\u2713 week {s['completed_this_week']:>2}"),
            ("dim", f"{streak_str}"),
        ])

        # ── Deadline timeline (braille bars, semantic colours) ──
        tl_map = s["deadline_timeline"]
        timeline = [
            ("\u26a0 Overdue", tl_map["overdue"], "error"),
            ("\u2713 Due today", tl_map["due_today"], "warn"),
            ("\u25b6 This week", tl_map["this_week"], "c2"),
            ("\u25b7 Next week", tl_map["next_week"], "c1"),
            ("\u2026 Later", tl_map["later"], "dim"),
            ("\u2014 No deadline", tl_map["no_deadline"], "dim"),
        ]
        max_tl = max((c for _, c, _ in timeline), default=1)
        section("Deadline Timeline")
        for label, count, attr in timeline:
            pct_tl = int(count / max_tl * 100) if max_tl else 0
            add([
                f"    {label:<15} ",
                *_braille_bar(pct_tl, bar_width, attr),
                f"  {count:>3}",
            ])

        # ── Urgency × Difficulty heatmap ──
        grid = s["ud_grid"]
        max_cell = max((max(r) for r in grid), default=1)
        section("Urgency \u00d7 Difficulty (pending)")
        add("         D1   D2   D3   D4   D5")
        for u_idx, row in enumerate(grid):
            cells: list = [f"    U{u_idx + 1}  "]
            for c in row:
                if c == 0:
                    cells.append(("dim", f"{_BRAILLE_STEPS[0]}{c:>2} "))
                else:
                    intensity = c / max_cell
                    if intensity >= 0.8:
                        attr = "c5"
                    elif intensity >= 0.6:
                        attr = "c4"
                    elif intensity >= 0.4:
                        attr = "c3"
                    elif intensity >= 0.2:
                        attr = "c2"
                    else:
                        attr = "c1"
                    bstep = _BRAILLE_STEPS[int(round(intensity * 8))]
                    cells.append((attr, f"{bstep}{c:>2} "))
            add(cells)

        # ── Archive stats (bottom-up vertical-block bars, gradient) ──
        arch_stats = s["archive_stats"]
        if arch_stats:
            section("Archives")
            for a in arch_stats:
                name = a["name"][:14]
                ah, _ = divmod(a["time_budget"], 60)
                add([
                    f"    {name:<14} ",
                    *_hblock_bar(100 if a["done"] == 0 else a["pct"], bar_width, fill_attr="dim" if a["done"] == 0 else None),
                    f"  {a['pct']:>3}%  {a['done']}/{a['total']}",
                    ("dim", f"  {ah}h"),
                ])

        # ── Directory stats (solid bars, 5-step gradient) ──
        dirs = stats_cmds.all_directory_stats()
        if dirs:
            section("Directories (top)")
            for d in dirs[:10]:
                name = d["name"][:18]
                add([
                    f"    {name:<18} ",
                    *_hblock_bar(100 if d["done"] == 0 else d["pct"], bar_width, fill_attr="dim" if d["done"] == 0 else None),
                    f"  {d['pct']:>3}%  {d['done']}/{d['total']}",
                ])
            if len(dirs) > 10:
                add([("dim", f"    ... and {len(dirs) - 10} more")])

        # ── Completion heatmap (GitHub-style contribution calendar) ──
        heatmap = stats_cmds.get_completion_heatmap(12)
        max_count = max((c for row in heatmap for c in row), default=1)
        section("Completion Heatmap (last 12 weeks)")
        day_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        for day_idx in range(7):
            cells: list = [f"    {day_labels[day_idx]}  "]
            for col in range(12):
                c = heatmap[day_idx][col]
                cells.append((_heatmap_attr(c, max_count), "\u25a0 "))
            add(cells)

        stats_w = LineBox(VimListBox(SimpleFocusListWalker(walker)))
        self._stats_overlay = Overlay(
            stats_w,
            self._frame,
            align="center",
            width=("relative", 72),
            valign="middle",
            height=("relative", 80),
        )
        self._loop.widget = self._stats_overlay

    def _cmd_review(self) -> None:
        from . import review_cmds

        data = review_cmds.get_review_data()
        today = date.today()

        walker: list[Text] = []

        def add(text: str | list) -> None:
            walker.append(Text(text))

        add([("head", f"  \U0001f50d  Review \u2014 {today.strftime('%A, %d/%m/%Y')}")])

        comp_pct = round((data["finished"] / data["total"] * 100) if data["total"] else 0)
        add([("dim", f"  Tasks: {data['total']}  |  Done: {data['finished']}/{data['total']} ({comp_pct}%)  |  Pending: {data['pending']}")])
        add([("dim", f"  Timer today: {data['timer_minutes_today']}m")])
        add("")

        overdue = data["overdue"]
        if overdue:
            add([("error", f"  \u26a0 Overdue ({len(overdue)})")])
            for r in overdue:
                deadline = r["deadline"]
                add(f"    \u26a0 {r['name']}  ({r['arch_name']} \u25b8 {r['dir_name']})  [was due {deadline}]")
            add("")

        due_today = data["due_today"]
        if due_today:
            add([("warn", f"  \U0001f504 Due today ({len(due_today)})")])
            for r in due_today:
                add(f"    \U0001f504 {r['name']}  ({r['arch_name']} \u25b8 {r['dir_name']})  [urg:{r['urgency']} diff:{r['difficulty']}]")
            add("")

        due_tomorrow = data["due_tomorrow"]
        if due_tomorrow:
            add([("c2", f"  \U0001f4c5 Due tomorrow ({len(due_tomorrow)})")])
            for r in due_tomorrow:
                add(f"    \U0001f4c5 {r['name']}  ({r['arch_name']} \u25b8 {r['dir_name']})")
            add("")

        completed_today = data["completed_today"]
        if completed_today:
            add([("done", f"  \u2713 Completed today ({len(completed_today)})")])
            for r in completed_today:
                add(f"    \u2713 {r['name']}  ({r['arch_name']} \u25b8 {r['dir_name']})")
            add("")

        completed_week = data["completed_this_week"]
        if completed_week:
            add([("c1", f"  \u2713 Completed earlier this week ({len(completed_week)})")])
            for r in completed_week[:5]:
                add(f"    \u2713 {r['name']}  ({r['arch_name']} \u25b8 {r['dir_name']})  [{r['finished_date']}]")
            if len(completed_week) > 5:
                add([("dim", f"    ... and {len(completed_week) - 5} more")])
            add("")

        if not any([overdue, due_today, due_tomorrow, completed_today]):
            add([("dim", "  \u2014 Nothing urgent. You're on top of things! \U0001f389")])

        rw = LineBox(VimListBox(SimpleFocusListWalker(walker)))
        self._stats_overlay = Overlay(
            rw,
            self._frame,
            align="center",
            width=("relative", 72),
            valign="middle",
            height=("relative", 80),
        )
        self._loop.widget = self._stats_overlay

    def _show_global_search(self, query: str) -> None:
        results = task_cmds.search_tasks_global(query)
        if not results:
            raw = [("head", "  Global Search\n\n"), ("dim", "  No tasks match your query.")]
        else:
            raw = [("head", f"  Global Search: {query}\n\n")]
            for i, (task, dir_name, arch_name) in enumerate(results, 1):
                path = f"{arch_name} \u25b8 {dir_name}"
                status = "\u2713" if task.finished else "\u25cb"
                raw.append(f"  {i}. {status} {task.name}")
                raw.append(f"      {path}")
                if task.deadline != "none":
                    raw.append(f"      Deadline: {task.deadline}")
                raw.append("\n")
        gw = LineBox(VimListBox(SimpleFocusListWalker([SelectableText(raw)])))
        self._stats_overlay = Overlay(
            gw,
            self._frame,
            align="center",
            width=("relative", 60),
            valign="middle",
            height=("relative", 60),
        )
        self._loop.widget = self._stats_overlay

    def _show_week_view(self) -> None:
        import shutil
        from datetime import timedelta

        today = date.today()
        monday = today - timedelta(days=today.weekday())
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        raw = task_cmds.get_tasks_for_week()

        grouped: dict[str, list] = {d: [] for d in days}
        for r in raw:
            task = r["task"]
            try:
                dt = date.fromisoformat(task.deadline)
            except (ValueError, TypeError):
                continue
            day_name = days[dt.weekday()]
            grouped[day_name].append(r)

        term_width = shutil.get_terminal_size().columns
        internal_width = int(term_width * 0.95) - 2
        col_width = max(6, (internal_width - 18) // 7)

        max_tasks = max((len(t) for t in grouped.values()), default=0)
        if max_tasks == 0:
            max_tasks = 1

        date_labels = []
        for i, day_name in enumerate(days):
            day_date = monday + timedelta(days=i)
            if col_width >= 10:
                label = f"{day_name} {day_date.strftime('%d/%m')}"
            elif col_width >= 7:
                label = f"{day_name} {day_date.day:02d}"
            else:
                label = day_name
            if day_date == today:
                label += " \u25c9"
            date_labels.append(label.center(col_width))

        sep = "\u2500" * col_width
        header = " \u2502 ".join(date_labels)
        divider = " \u2502 ".join([sep] * 7)

        task_rows = []
        for row_idx in range(max_tasks):
            row_cells = []
            for day_name in days:
                day_tasks = grouped[day_name]
                if row_idx < len(day_tasks):
                    r = day_tasks[row_idx]
                    t = r["task"]
                    status = "\u2713" if t.finished else "\u25cb"
                    name = t.name[:col_width - 2]
                    cell = f"{status} {name}".ljust(col_width)
                elif row_idx == 0:
                    cell = "\u2014".center(col_width)
                else:
                    cell = " " * col_width
                row_cells.append(cell)
            task_rows.append(" \u2502 ".join(row_cells))

        content = f"  Week of {monday.strftime('%d/%m/%Y')}\n\n{header}\n{divider}\n" + "\n".join(task_rows)

        ww = LineBox(Filler(Text(content), valign="top"))
        self._stats_overlay = Overlay(
            ww,
            self._frame,
            align="center",
            width=("relative", 95),
            valign="middle",
            height=("relative", 85),
        )
        self._loop.widget = self._stats_overlay

    def _show_overdue_view(self) -> None:
        tasks = task_cmds.get_overdue_tasks()
        if not tasks:
            lines = ["  No overdue tasks!"]
        else:
            lines = [f"  Overdue Tasks ({len(tasks)})"]
            lines.append("")
            for t in tasks:
                deadline = task_cmds._display_date(t.deadline)
                lines.append(f"    \u26a0 {t.name}  [{deadline}]")
        content = "\n".join(lines)
        ow = LineBox(Filler(Text(content), valign="top"))
        self._stats_overlay = Overlay(
            ow,
            self._frame,
            align="center",
            width=("relative", 60),
            valign="middle",
            height=("relative", 70),
        )
        self._loop.widget = self._stats_overlay

    def _show_standup(self) -> None:
        from datetime import timedelta

        yesterday = date.today() - timedelta(days=1)
        yesterday_str = yesterday.isoformat()
        conn = db_mod.get_conn()
        rows = conn.execute(
            """SELECT t.name, d.name AS dir_name, a.name AS arch_name
               FROM tasks t
               JOIN directories d ON t.directory_id = d.id
               JOIN archives a ON d.archive_id = a.id
               WHERE t.finished = 1 AND t.finished_date = ?
               ORDER BY a.name, d.name""",
            (yesterday_str,),
        ).fetchall()
        if not rows:
            content = "  No tasks completed yesterday."
        else:
            lines = [f"  ## Standup — {yesterday.strftime('%d/%m/%Y')}", ""]
            groups: dict[str, list[str]] = {}
            for r in rows:
                arch = r["arch_name"]
                groups.setdefault(arch, []).append(f"  - {r['name']} ({r['dir_name']})")
            for arch, items in sorted(groups.items()):
                lines.append(f"  **{arch}**")
                lines.extend(items)
                lines.append("")
            if lines and not lines[-1]:
                lines.pop()
            content = "\n".join(lines)
        gw = LineBox(VimListBox(SimpleFocusListWalker([SelectableText(content)])))
        self._stats_overlay = Overlay(
            gw, self._frame,
            align="center", width=("relative", 60),
            valign="middle", height=("relative", 60),
        )
        self._loop.widget = self._stats_overlay

    def _show_day_picker(
        self,
        on_select: Callable[[str], None],
        on_cancel: Callable[[], None],
    ) -> None:
        def handle_select(day: str) -> None:
            self._loop.widget = self._frame
            on_select(day)

        def handle_cancel() -> None:
            self._loop.widget = self._frame
            on_cancel()

        picker = DayPickerWidget(handle_select, handle_cancel)
        self._stats_overlay = Overlay(
            picker, self._frame,
            align="center", width=("relative", 60),
            valign="middle", height=7,
        )
        self._loop.widget = self._stats_overlay

    def _get_terminal(self) -> str | None:
        try:
            with open(CONFIG_PATH) as f:
                for line in f:
                    if line.startswith("TERMINAL:"):
                        term = line.split(":", 1)[1].strip()
                        if shutil.which(term):
                            return term
        except OSError:
            pass
        term = _detect_terminal()
        if term:
            try:
                with open(CONFIG_PATH, "a") as f:
                    f.write(f"\nTERMINAL:{term}\n")
            except OSError:
                pass
        return term

    def _build_task_json(self, task_id: int) -> dict:
        task = task_cmds.get_task(task_id)
        if not task:
            return {}
        conn = db_mod.get_conn()
        row = conn.execute(
            "SELECT d.name AS dir_name, a.name AS arch_name FROM directories d "
            "JOIN archives a ON a.id = d.archive_id WHERE d.id = ?",
            (task.directory_id,),
        ).fetchone()
        notes = note_cmds.list_notes(task_id)
        tags = tag_cmds.get_tags_for_task(task_id)
        return {
            "task": {
                "id": task.id,
                "directory_id": task.directory_id,
                "name": task.name,
                "description": task.description,
                "urgency": task.urgency,
                "difficulty": task.difficulty,
                "time_dedicated": task.time_dedicated,
                "deadline": task.deadline,
                "repeatable": task.repeatable,
                "repeatable_type": task.repeatable_type,
                "repeat_on_specific_day": task.repeat_on_specific_day,
                "finished": task.finished,
                "finished_date": task.finished_date,
                "position": task.position,
            },
            "directory": row["dir_name"] if row else None,
            "archive": row["arch_name"] if row else None,
            "notes": [
                {"id": n.id, "date": n.date, "note": n.note, "file_path": n.file_path, "created_at": n.created_at}
                for n in notes
            ],
            "tags": [t.name for t in tags],
        }

    def _cmd_copy(self) -> None:
        if self._level == Level.NOTES:
            idx = self._list_box.focus_position
            if not self._current_items or idx >= len(self._current_items):
                self._set_timed_caption("error", "Nothing selected ")
                return
            n = self._current_items[idx]
            data = {"id": n.id, "date": n.date, "note": n.note, "file_path": n.file_path, "created_at": n.created_at}

        elif self._level == Level.TASKS:
            if self._bulk_selection:
                tasks_data = []
                for tid in list(self._bulk_selection):
                    td = self._build_task_json(tid)
                    if td:
                        tasks_data.append(td)
                data = tasks_data
            else:
                sid = self._get_selected_id()
                if sid is None:
                    self._set_timed_caption("error", "Nothing selected ")
                    return
                data = self._build_task_json(sid)

        elif self._level == Level.DIRECTORIES:
            sid = self._get_selected_id()
            if sid is None:
                self._set_timed_caption("error", "Nothing selected ")
                return
            conn = db_mod.get_conn()
            d = conn.execute(
                "SELECT id, archive_id, name FROM directories WHERE id = ?",
                (sid,),
            ).fetchone()
            if not d:
                self._set_timed_caption("error", "Nothing selected ")
                return
            tasks_raw = task_cmds.list_tasks(directory_id=sid)
            tasks_data = [
                self._build_task_json(t.id) for t in tasks_raw
            ]
            data = {
                "directory": {"id": d["id"], "archive_id": d["archive_id"], "name": d["name"]},
                "tasks": tasks_data,
            }

        elif self._level == Level.ARCHIVES:
            sid = self._get_selected_id()
            if sid is None:
                self._set_timed_caption("error", "Nothing selected ")
                return
            conn = db_mod.get_conn()
            a = conn.execute(
                "SELECT id, name FROM archives WHERE id = ?",
                (sid,),
            ).fetchone()
            if not a:
                self._set_timed_caption("error", "Nothing selected ")
                return
            dirs = directory_cmds.list_directories(archive_id=sid)
            directories_data = []
            for d in dirs:
                tasks_raw = task_cmds.list_tasks(directory_id=d.id)
                tasks_data = [
                    self._build_task_json(t.id) for t in tasks_raw
                ]
                directories_data.append({
                    "directory": {"id": d.id, "name": d.name},
                    "tasks": tasks_data,
                })
            data = {
                "archive": {"id": a["id"], "name": a["name"]},
                "directories": directories_data,
            }
        else:
            return

        raw = json.dumps(data, indent=2, default=str)
        name = self._get_selected_name() or ""
        bulk_count = len(self._bulk_selection)
        if bulk_count:
            success_msg = f"Copied {bulk_count} tasks to clipboard "
        elif name:
            success_msg = f"Copied '{name}' to clipboard "
        else:
            success_msg = "Copied to clipboard "
        self._run_async(
            lambda: _copy_to_clipboard(raw),
            lambda r: self._finish_clipboard(r, success_msg, "Clipboard tools not found (wl-copy/xclip/xsel) "),
            "Copying...",
        )

    def _finish_clipboard(self, result: object, success_msg: str, error_msg: str) -> None:
        success = bool(result) if isinstance(result, bool) else False
        if success:
            self._set_timed_caption("done", success_msg, 3)
        else:
            self._set_timed_caption("error", error_msg, 3)

    def _cmd_import_json_template(self) -> None:
        template = json.dumps(
            [
                {
                    "name": "example task",
                    "description": "description here",
                    "urgency": 1,
                    "difficulty": 1,
                    "time_dedicated": 0,
                    "deadline": "none",
                    "repeatable": False,
                    "repeatable_type": "none",
                    "repeat_on_specific_day": "none",
                    "has_to_be_completed_to_repeat": True,
                    "pinned": False,
                },
                {
                    "name": "another task",
                    "description": "",
                    "urgency": 2,
                    "difficulty": 3,
                    "time_dedicated": 3600,
                    "deadline": "2026-12-31",
                    "repeatable": True,
                    "repeatable_type": "daily",
                    "repeat_on_specific_day": "none",
                    "has_to_be_completed_to_repeat": False,
                    "pinned": True,
                },
            ],
            indent=2,
        )
        self._run_async(
            lambda: _copy_to_clipboard(template),
            lambda r: self._finish_clipboard(r, "Import JSON template copied to clipboard ", "Clipboard tools not found (wl-copy/xclip/xsel) "),
            "Copying...",
        )

    def _cmd_import_json_file(self, path: str) -> None:
        try:
            text = open(path, "r", encoding="utf-8").read()
        except OSError as e:
            self._set_timed_caption("error", f"Cannot read file: {e} ")
            return
        target_dir = self._selected_directory_id
        self._run_async(
            lambda: io_cmds.import_tasks_from_directory_json(text, target_dir),
            lambda r: self._finish_import(r),
            "Importing...",
        )

    def _cmd_import_note_json_template(self) -> None:
        template = json.dumps(
            [
                {
                    "date": "2026-07-07",
                    "note": "example note content",
                    "file_path": None,
                },
                {
                    "date": "2026-07-08",
                    "note": "another note",
                    "file_path": "/path/to/file.txt",
                },
            ],
            indent=2,
        )
        self._run_async(
            lambda: _copy_to_clipboard(template),
            lambda r: self._finish_clipboard(r, "Import Notes JSON template copied to clipboard ", "Clipboard tools not found (wl-copy/xclip/xsel) "),
            "Copying...",
        )

    def _cmd_import_note_json_file(self, path: str) -> None:
        try:
            text = open(path, "r", encoding="utf-8").read()
        except OSError as e:
            self._set_timed_caption("error", f"Cannot read file: {e} ")
            return
        if self._selected_task_id is None:
            self._set_timed_caption("error", "No task selected ")
            return
        self._run_async(
            lambda: io_cmds.import_notes_json(text, self._selected_task_id),
            lambda r: self._finish_import(r),
            "Importing...",
        )

    def _finish_import(self, result: object) -> None:
        if isinstance(result, tuple) and len(result) == 2:
            success, msg = result
            if success:
                self._set_timed_caption("done", f"{msg} ", 3)
            else:
                self._set_timed_caption("error", f"{msg} ", 3)
        else:
            self._set_timed_caption("error", f"Import failed: {result} ", 3)
        self._refresh_list()

    def _gather_ai_context(self) -> dict | None:
        if self._level != Level.TASKS or self._selected_task_id is None:
            return None
        task = task_cmds.get_task(self._selected_task_id)
        if not task:
            return None
        dir_name = None
        arch_name = None
        conn = db_mod.get_conn()
        row = conn.execute(
            "SELECT d.name AS dname, a.name AS aname FROM directories d "
            "JOIN archives a ON a.id = d.archive_id WHERE d.id = ?",
            (task.directory_id,),
        ).fetchone()
        if row:
            dir_name = row["dname"]
            arch_name = row["aname"]
        notes = note_cmds.list_notes(task.id)
        return {
            "task": {
                "name": task.name,
                "description": task.description,
                "deadline": task.deadline,
                "urgency": task.urgency,
                "difficulty": task.difficulty,
                "time_dedicated": task.time_dedicated,
                "repeatable": task.repeatable,
                "repeatable_type": task.repeatable_type,
                "repeat_on_specific_day": task.repeat_on_specific_day,
                "finished": task.finished,
            },
            "directory": dir_name,
            "archive": arch_name,
            "notes": [{"date": n.date, "note": n.note, "file_path": n.file_path, "created_at": n.created_at} for n in notes],
        }

    def _cmd_ai(self) -> None:
        if self._level != Level.TASKS or self._selected_task_id is None:
            self._set_timed_caption("error", "Select a task first ")
            return
        opencode_path = _find_opencode()
        if not opencode_path:
            self._set_timed_caption("error", "opencode not installed ")
            return
        terminal = self._get_terminal()
        if not terminal:
            self._set_timed_caption("error", "No terminal found ")
            return
        ctx = self._gather_ai_context()
        if not ctx:
            self._set_timed_caption("error", "Could not gather context ")
            return
        fd = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", prefix="taskwatch_ai_", delete=False,
        )
        with fd:
            json.dump(ctx, fd, indent=2)
            ctx_file = fd.name
        project_root = Path(__file__).resolve().parent.parent
        task_obj = task_cmds.get_task(self._selected_task_id)
        if task_obj:
            d_obj = directory_cmds.get_directory(task_obj.directory_id)
            if d_obj and d_obj.project_path:
                project_root = d_obj.project_path
        task_name = ctx.get("task", {}).get("name", "task")
        cmd = _build_terminal_cmd(
            terminal,
            f"{opencode_path} run 'Help with: {task_name}' -f '{ctx_file}' -i --dir '{project_root}'",
        )
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._set_timed_caption("done", "Opening opencode... ")

    def _cmd_aii_chat(self) -> None:
        if self._ai_chat_widget is None:
            self._ai_chat_widget = ai_chat.AIChatWidget(self)
        overlay = Overlay(
            self._ai_chat_widget, self._frame,
            align="center", width=("relative", 85),
            valign="middle", height=("relative", 85),
        )
        self._loop.widget = overlay

    def _close_ai_chat(self) -> None:
        self._loop.widget = self._frame
        self._focus_body()
        while not self._ai_inbox.empty():
            try:
                self._ai_inbox.get_nowait()
            except queue.Empty:
                break

    def _show_file_picker(
        self,
        callback: Callable[[str], None],
        on_cancel: Callable[[], None] | None = None,
        start_dir: str | None = None,
    ) -> None:
        if start_dir is None:
            start_dir = os.getcwd()
        picker = FilePickerWidget(
            start_dir=start_dir,
            on_select=callback,
            on_cancel=on_cancel or (lambda: self._focus_body()),
        )
        overlay = Overlay(
            picker, self._frame,
            align="center", width=("relative", 70),
            valign="middle", height=("relative", 70),
        )
        self._loop.widget = overlay

    def _handle_aii_subcmd(self, rest: str) -> None:
        parts = rest.split()
        if not parts:
            self._show_provider_select()
            return
        subcmd = parts[0]
        if subcmd == "connect":
            if len(parts) >= 3:
                name = parts[1]
                key = " ".join(parts[2:])
                ok, msg = ai_client.add_provider(name, key)
                self._set_timed_caption("done" if ok else "error", msg)
                return
            self._show_provider_select()
            return
        if subcmd == "disconnect" and len(parts) >= 2:
            name = parts[1]
            ok, msg = ai_client.remove_provider(name)
            self._set_timed_caption("done" if ok else "error", msg)
            return
        if subcmd == "providers":
            providers = ai_client.list_providers()
            if not providers:
                self._set_timed_caption("dim", "No providers configured")
            else:
                info = " | ".join(
                    f"{p['name']} ({p['model']}) {p['key']}"
                    for p in providers
                )
                self._set_timed_caption("done", info)
            return
        self._set_timed_caption(
            "error",
            "Usage: aii connect | aii disconnect <name> | aii providers",
        )

    def _show_provider_select(self) -> None:
        def on_select(name: str) -> None:
            self._loop.widget = self._frame
            self._frame.focus_position = "footer"
            self._start_wizard(
                f"{name} key: ",
                lambda text: self._on_provider_key_entered(name, text),
            )

        def on_cancel() -> None:
            self._loop.widget = self._frame
            self._focus_body()

        picker = ai_chat.ProviderSelectWidget(on_select, on_cancel)
        overlay = Overlay(
            picker, self._frame,
            align="center", width=("relative", 40),
            valign="middle", height=("relative", 40),
        )
        self._loop.widget = overlay

    def _on_provider_key_entered(self, name: str, key: str) -> None:
        if not key.strip():
            self._start_wizard(
                f"{name} key: ",
                lambda text: self._on_provider_key_entered(name, text),
            )
            return
        ok, msg = ai_client.test_provider(name, key)
        if not ok:
            self._set_timed_caption("error", msg + " ")
            self._start_wizard(
                f"{name} key: ",
                lambda text: self._on_provider_key_entered(name, text),
            )
            return
        add_ok, add_msg = ai_client.add_provider(name, key)
        if add_ok:
            self._end_wizard(f"{name} key tested successfully \u2014 provider connected")
        else:
            self._end_wizard(add_msg, "error")

    def _show_highlight_picker(self) -> None:
        current = self._highlight_color

        def on_select(name: str) -> None:
            self._set_highlight_color(name)
            self._set_timed_caption("done", f"Highlight color: {name} ")

        def on_cancel() -> None:
            self._loop.widget = self._frame
            self._set_timed_caption("done", "Cancelled ")

        picker = ColorPickerWidget(
            _HIGHLIGHT_COLORS, current, on_select, on_cancel,
        )
        self._stats_overlay = Overlay(
            picker, self._frame,
            align="center", width=("relative", 40),
            valign="middle", height=("relative", 60),
        )
        self._loop.widget = self._stats_overlay

    def _set_highlight_color(self, name: str) -> None:
        urwid_bg = "default"
        for cname, cbg in _HIGHLIGHT_COLORS:
            if cname == name:
                urwid_bg = cbg
                break
        for i, entry in enumerate(PALETTE):
            if entry[0] == "focus":
                PALETTE[i] = (entry[0], entry[1], urwid_bg, *entry[3:])
            elif len(entry) >= 4 and entry[0].endswith("_focus"):
                PALETTE[i] = (entry[0], entry[1], urwid_bg, *entry[3:])
        self._loop.widget = self._frame
        self._loop.screen.register_palette(PALETTE)
        self._loop.draw_screen()
        self._highlight_color = name
        try:
            lines: list[str] = []
            found = False
            try:
                with open(CONFIG_PATH) as f:
                    lines = f.readlines()
            except OSError:
                pass
            with open(CONFIG_PATH, "w") as f:
                for line in lines:
                    if line.startswith("HIGHLIGHT:"):
                        f.write(f"HIGHLIGHT:{name}\n")
                        found = True
                    else:
                        f.write(line)
                if not found:
                    f.write(f"HIGHLIGHT:{name}\n")
        except OSError:
            pass

    def _build_focus_body(self) -> urwid.Filler:
        from .task_cmds import get_task
        task = get_task(self._selected_task_id) if self._selected_task_id else None
        name = task.name if task else "No task selected"
        self._focus_timer_text = Text(("bold", "00:00:00"), align="center")
        self._focus_task_name = Text(("head", name), align="center")
        lines = [
            urwid.Divider(),
            urwid.Text(("head", "FOCUS MODE"), align="center"),
            urwid.Divider(),
            self._focus_task_name,
            urwid.Divider(),
            self._focus_timer_text,
            urwid.Divider(),
            urwid.Text(("dim", "p: pause  s: stop  :focus: exit  q: quit"), align="center"),
        ]
        return urwid.Filler(urwid.Pile(lines), valign="middle")

    def _cmd_attach_project(self, cmd: str) -> None:
        if self._level != Level.DIRECTORIES:
            self._set_timed_caption("error", "Navigate to a directory first ")
            return
        sid = self._get_selected_id()
        if sid is None:
            return
        path = cmd.split(" ", 1)[1].strip() if " " in cmd else ""
        if not path:
            self._set_timed_caption("error", "Usage: :attachProject <path> ")
            return
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_dir():
            self._set_timed_caption("error", f"Path does not exist: {resolved} ")
            return
        dir_obj = directory_cmds.get_directory(sid)
        if dir_obj is None:
            return
        ok = directory_cmds.attach_project(str(resolved), dir_obj.id, dir_obj.name)
        if ok:
            self._show_detail()
            self._set_timed_caption("done", f"Project attached: {resolved} ")
        else:
            self._set_timed_caption("error", "Failed to write .taskwatch-directory ")

    @staticmethod
    def _find_terminal() -> str | None:
        term = os.environ.get("TERMINAL", "")
        if term and shutil.which(term):
            return term
        for c in ["xterm", "gnome-terminal", "konsole", "xfce4-terminal", "lxterminal", "foot", "alacritty", "kitty", "wezterm"]:
            if shutil.which(c):
                return c
        return None

    def _cmd_serve(self) -> None:
        host = "0.0.0.0"
        port = 8080
        from .server import get_url, _get_tailscale_ip
        url = get_url(host, port)
        ts_ip = _get_tailscale_ip()
        msg = f"Server: {url}"
        if ts_ip:
            msg += f" | Tailscale: {get_url(ts_ip, port)}"

        term = self._find_terminal()
        if term:
            cmd = f"'{sys.executable}' -c 'from taskwatch.server import run_server; run_server(host=\"{host}\", port={port})'"
            try:
                if "gnome-terminal" in term:
                    subprocess.Popen(["gnome-terminal", "--", "sh", "-c", cmd],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    subprocess.Popen([term, "-e", "sh", "-c", cmd],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._set_timed_caption("done", f"{msg} (new terminal) ")
            except Exception as e:
                self._set_timed_caption("error", f"Failed to launch terminal: {e} ")
        else:
            self._server_running = True
            self._server_thread = threading.Thread(
                target=self._run_server_thread,
                args=(host, port),
                daemon=True,
            )
            self._server_thread.start()
            self._set_timed_caption("done", f"{msg} ")

    def _run_server_thread(self, host: str, port: int) -> None:
        from .server import run_server
        run_server(host=host, port=port)

    def _cmd_serve_subcmd(self, cmd: str) -> None:
        parts = cmd.strip().split(maxsplit=1)
        sub = parts[1] if len(parts) > 1 else ""
        if sub == "stop":
            self._set_timed_caption("error", "Use Ctrl+C to stop the process. Daemon restart coming in Stage 3.")
        elif sub == "status":
            running = getattr(self, "_server_running", False) and getattr(self, "_server_thread", None) and self._server_thread.is_alive()
            if running:
                from .server import get_url, _get_tailscale_ip
                url = get_url('0.0.0.0', 8080)
                ts_ip = _get_tailscale_ip()
                msg = f"Server running — {url}"
                if ts_ip:
                    msg += f" | Tailscale: {get_url(ts_ip, 8080)}"
                self._set_timed_caption("done", f"{msg} ")
            else:
                self._set_timed_caption("error", "Server not running ")
        else:
            self._set_timed_caption("error", "Usage: :serve stop | :serve status ")

    def _cmd_toggle_focus(self) -> None:
        if not self._focus_mode:
            if self._selected_task_id is None and self._level != Level.TASKS:
                self._set_timed_caption("error", "Select a task first ")
                return
            self._saved_body = self._frame.body
            self._frame.body = self._build_focus_body()
            self._focus_mode = True
            self._update_clock_display()
        else:
            self._frame.body = self._saved_body
            self._focus_mode = False
            self._focus_timer_text = None
            self._focus_task_name = None
            self._saved_body = None
            self._show_detail()
        self._loop.draw_screen()

    def _show_schedule_bar(self) -> None:
        if not self._timer_running or not self._timer_schedule:
            ow = LineBox(Text("  No active timer schedule"))
            self._stats_overlay = Overlay(
                ow, self._frame,
                align="center", width=("relative", 60),
                valign="middle", height=("relative", 50),
            )
            self._loop.widget = self._stats_overlay
            return

        schedule = self._timer_schedule
        segments = schedule["segments"]
        total = sum(segments)
        current_idx = self._timer_segment_idx
        current_elapsed = self._timer_segment_elapsed

        task_name = ""
        if self._timer_task_id is not None:
            task = task_cmds.get_task(self._timer_task_id)
            if task:
                task_name = task.name

        import shutil
        term_width = shutil.get_terminal_size().columns
        bar_width = max(10, min(50, int(term_width * 0.63)))

        seg_widths = []
        for seg in segments:
            seg_widths.append(max(1, round(seg / total * bar_width)))
        diff = bar_width - sum(seg_widths)
        if diff:
            seg_widths[-1] += diff

        bar_parts = []
        for i, (seg, w) in enumerate(zip(segments, seg_widths)):
            if i == 0:
                base_attr = "dim"
            elif i % 2 == 1:
                base_attr = "head"
            else:
                base_attr = "default"
            if i < current_idx:
                bar_parts.append((base_attr, _BRAILLE_STEPS[8] * w))
            elif i == current_idx:
                ratio = current_elapsed / seg if seg else 0
                total_cells = w * 8
                filled_cells = int(total_cells * ratio)
                full = filled_cells // 8
                rem = filled_cells % 8
                empty = w - full - (1 if rem else 0)
                if full:
                    bar_parts.append((base_attr, _BRAILLE_STEPS[8] * full))
                if rem:
                    bar_parts.append((base_attr, _BRAILLE_STEPS[rem]))
                if empty:
                    bar_parts.append(("bar_e", _BRAILLE_STEPS[0] * empty))
            else:
                bar_parts.append(("bar_e", _BRAILLE_STEPS[0] * w))

        marker_pos = sum(seg_widths[:current_idx]) + seg_widths[current_idx] // 2
        marker_line = "  " + " " * marker_pos + "▲"

        label_parts = []
        time_parts = []
        for i, (seg, w) in enumerate(zip(segments, seg_widths)):
            lbl = "INTRO" if i == 0 else ("WORK" if i % 2 == 1 else "BREAK")
            if i == current_idx:
                rem = max(0, seg - current_elapsed)
                t = f"{_dur(current_elapsed)}/{_dur(seg)}"
            else:
                t = _dur(seg)
            lpad = max(0, (w - len(lbl)) // 2)
            label_parts.append(" " * lpad + lbl + " " * (w - len(lbl) - lpad))
            tpad = max(0, (w - len(t)) // 2)
            time_parts.append(" " * tpad + t + " " * (w - len(t) - tpad))

        total_elapsed = sum(segments[:current_idx]) + current_elapsed
        total_remaining = total - total_elapsed

        walker: list[Text] = []
        walker.append(Text([("head", "  Timer Schedule")]))
        walker.append(Text(""))
        if task_name:
            walker.append(Text(f"  Task: {task_name}"))
            walker.append(Text(""))
        walker.append(Text(["  ", *bar_parts]))
        walker.append(Text(marker_line))
        walker.append(Text("  " + "".join(label_parts)))
        walker.append(Text("  " + "".join(time_parts)))
        walker.append(Text(""))
        walker.append(Text(f"  Total: {_dur(total_elapsed)} elapsed  ·  {_dur(total_remaining)} remaining"))
        walker.append(Text(""))
        walker.append(Text(("dim", "  esc/q to close")))

        stats_w = LineBox(VimListBox(SimpleFocusListWalker(walker)))
        self._stats_overlay = Overlay(
            stats_w, self._frame,
            align="center", width=("relative", 65),
            valign="middle", height=("relative", 50),
        )
        self._loop.widget = self._stats_overlay

    def _undo_last_action(self) -> None:
        entry = undo_cmds.pop()
        if entry is None:
            self._set_timed_caption("error", "Nothing to undo ")
            return
        action = entry["action"]
        data = entry["data"]
        success = True
        if action == "task_delete":
            success = undo_cmds.restore_task(data)
        elif action == "task_create":
            success = task_cmds.delete_task(data["task_id"])
        elif action == "task_edit":
            t = task_cmds.edit_task(
                data["task_id"],
                name=data["name"],
                description=data.get("description", ""),
                urgency=data["urgency"],
                difficulty=data["difficulty"],
                time_dedicated=data["time_dedicated"],
                deadline=data["deadline"],
                repeatable=data.get("repeatable", False),
                repeatable_type=data.get("repeatable_type", "none"),
                has_to_be_completed_to_repeat=data.get("has_to_be_completed_to_repeat", True),
            )
            success = t is not None
        elif action == "task_finish":
            t = task_cmds.mark_not_done(data["task_id"])
            success = t is not None
        elif action == "task_unfinish":
            t = task_cmds.mark_done(data["task_id"])
            success = t is not None
        elif action == "directory_delete":
            conn = db_mod.get_conn()
            try:
                conn.execute(
                    "INSERT INTO directories (id, archive_id, name) VALUES (?, ?, ?)",
                    (data["id"], data["archive_id"], data["name"]),
                )
                for t in data.get("tasks", []):
                    undo_cmds.restore_task(t)
                conn.commit()
            except Exception:
                success = False
        elif action == "archive_delete":
            conn = db_mod.get_conn()
            try:
                conn.execute(
                    "INSERT INTO archives (id, name) VALUES (?, ?)",
                    (data["id"], data["name"]),
                )
                for d in data.get("directories", []):
                    conn.execute(
                        "INSERT INTO directories (id, archive_id, name) VALUES (?, ?, ?)",
                        (d["id"], d["archive_id"], d["name"]),
                    )
                    for t in d.get("tasks", []):
                        undo_cmds.restore_task(t)
                conn.commit()
            except Exception:
                success = False
        elif action == "note_delete":
            conn = db_mod.get_conn()
            try:
                conn.execute(
                    "INSERT INTO notes (id, task_id, date, note, file_path, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (data["id"], data["task_id"], data["date"], data["note"], data.get("file_path"), data.get("created_at", "")),
                )
                conn.commit()
            except Exception:
                success = False
        elif action == "directory_edit":
            d = directory_cmds.rename_directory(data["id"], data["name"])
            success = d is not None
        elif action == "archive_edit":
            a = archive_cmds.rename_archive(data["id"], data["name"])
            success = a is not None
        elif action == "note_edit":
            n = note_cmds.update_note(data["id"], note=data["note"], date=data["date"], file_path=data.get("file_path"))
            success = n is not None
        elif action == "tag_add":
            tag_cmds.add_tag_to_task(data["task_id"], data["tag_name"])
        elif action == "tag_remove":
            tag_cmds.remove_tag_from_task(data["task_id"], data["tag_name"])
        elif action == "subtask_create":
            subtask_cmds.delete_subtask(data["id"])
        elif action == "subtask_delete":
            conn = db_mod.get_conn()
            conn.execute(
                "INSERT INTO subtasks (id, task_id, content, finished, position) VALUES (?, ?, ?, ?, ?)",
                (data["id"], data["task_id"], data["content"], data.get("finished", 0), data.get("position", 0)),
            )
            conn.commit()
        elif action == "subtask_toggle":
            if data.get("finished"):
                subtask_cmds.mark_not_done(data["id"])
            else:
                subtask_cmds.mark_done(data["id"])
        else:
            success = False
        if success:
            self._set_timed_caption("done", f"Undone: {action} ")
        self._refresh_list()

    def _check_and_notify_deadlines(self) -> None:
        if not self._notify_deadlines_enabled:
            return
        today_str = date.today().isoformat()
        due_today = task_cmds.get_tasks_due_on(today_str)
        for task in due_today:
            if task.id not in self._notified_deadlines:
                self._notified_deadlines.add(task.id)
                try:
                    subprocess.run(
                        ["notify-send", "-a", "TaskWatch+", "-u", "normal",
                         "Task Due Today", task.name],
                        capture_output=True,
                    )
                except FileNotFoundError:
                    pass

            self._cmd.set_caption(("standout", "\u276f "))
        self._refresh_list()
        try:
            calcurse_cmds.sync_to_calcurse()
        except Exception:
            pass
        self._focus_body()

    def _unhandled_input(self, key: str) -> None:
        if isinstance(key, tuple) and len(key) == 4:
            event, button, x, y = key
            if event == "mouse press":
                if button == 1 and self._frame.focus_position == "body":
                    self._select()
                elif button == 3:
                    self._go_back()
            return
        if self._focus_mode:
            if key == "p":
                self._cmd_pause_timer()
                return
            if key == "s":
                self._stop_timer()
                return
            return
        if self._loop.widget is not self._frame:
            if key in ("esc", "q"):
                if self._help_overlay is not None and self._loop.widget is self._help_overlay:
                    prev = getattr(self, '_prev_overlay', self._frame) or self._frame
                    self._loop.widget = prev
                    self._help_overlay = None
                else:
                    self._loop.widget = self._frame
            return
        if key == " " and self._level == Level.TASKS and self._frame.focus_position == "body":
            if self._current_items:
                idx = self._list_box.focus_position
                if idx < len(self._current_items):
                    item = self._current_items[idx]
                    if item.id in self._bulk_selection:
                        self._bulk_selection.discard(item.id)
                    else:
                        self._bulk_selection.add(item.id)
                    self._refresh_list()
            return
        if key in ("enter", "l") and self._frame.focus_position == "body":
            if self._level == Level.NOTES:
                self._show_detail()
            else:
                self._select()
            return
        if key == "?":
            self._show_help()
            return

    def run(self) -> None:
        try:
            with open(CONFIG_PATH) as f:
                for line in f:
                    if ":" in line:
                        k, v = line.split(":", 1)
                        if k.strip() == "NOTIFY_DEADLINES":
                            self._notify_deadlines_enabled = v.strip().lower() == "true"
        except OSError:
            pass
        self._reconnect_timer()
        self._refresh_list()
        urwid.connect_signal(self._list_walker, "modified", self._show_detail)
        self._loop.set_alarm_in(1, self._tick)
        self._frame.focus_position = "body"
        self._loop.run()


def run_tui() -> None:
    TaskWatchTUI().run()
