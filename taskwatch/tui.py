from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import date, datetime
from enum import Enum, auto
from functools import partial
from pathlib import Path

import urwid
from urwid import (
    AttrMap,
    Columns,
    Edit,
    ExitMainLoop,
    Frame,
    LineBox,
    ListBox,
    MainLoop,
    Overlay,
    SimpleFocusListWalker,
    Text,
    WidgetWrap,
)

from . import archive_cmds, calcurse_cmds, db as db_mod, directory_cmds, io_cmds, note_cmds, stats_cmds, tag_cmds, task_cmds, timer as timer_mod, undo_cmds


class Level(Enum):
    ARCHIVES = auto()
    DIRECTORIES = auto()
    TASKS = auto()
    NOTES = auto()


PALETTE = [
    ("default", "default", "default"),
    ("focus", "default", "dark blue", "standout"),
    ("head", "default", "default", "bold"),
    ("dim", "dark gray", "default"),
    ("done", "dark green", "default"),
    ("warn", "brown", "default"),
    ("bar_f", "default", "default"),
    ("bar_e", "dark gray", "default"),
    ("error", "dark red", "default"),
    ("help", "default", "default"),
    ("pane_dim", "dark gray", "default"),
]

_HIGHLIGHT_COLORS: list[tuple[str, str]] = [
    ("default", "default"),
    ("black", "black"),
    ("dark blue", "dark blue"),
    ("light blue", "light blue"),
    ("dark green", "dark green"),
    ("light green", "light green"),
    ("dark red", "dark red"),
    ("light red", "light red"),
    ("brown", "brown"),
    ("yellow", "yellow"),
    ("dark magenta", "dark magenta"),
    ("light magenta", "light magenta"),
    ("dark cyan", "dark cyan"),
    ("light cyan", "light cyan"),
    ("dark gray", "dark gray"),
    ("light gray", "light gray"),
    ("white", "white"),
]

_HIGHLIGHT_ALIASES: dict[str, str] = {
    "blue": "dark blue",
    "green": "dark green",
    "red": "dark red",
    "magenta": "dark magenta",
    "cyan": "dark cyan",
    "gray": "dark gray",
}

COMMANDS = [
    "a", "add", "r", "remove", "d", "e", "edit", "f", "finish",
    "c", "cancel", "shf", "showFinished", "hf", "hideFinished",
    "h", "help", "q", "exit", "stats", "ftc", "undo", "week",
    "st", "ts", "timerStop", "pt", "pauseTimer", "rt", "resetTimer",
    "su a", "su d", "sd a", "sd d", "sn a", "sn d", "sdl a", "sdl d", "sr",
    "tag ", "untag ", "ft ", "gs ", "qa ", "mv ", "mu", "md", "all",
    "export", "import ", "overdue", "schbar", "ai", "highlight",
    "bm", "bd", "bt ", "bv ", "bc",
]

HELP_TEXT = (
    "TaskWatch+ Help\n\n"
    "Navigation:\n"
    "  \u2191/\u2193        Move selection / scroll detail\n"
    "  Enter / l    Select / drill in\n"
    "  ` / h        Go back one level\n"
    "  Tab         Switch between list and detail pane\n"
    "  :           Focus command bar\n\n"
    "Commands (type : then the key):\n"
    "  :a | :add             Add item at current level\n"
    "  :r | :remove          Delete selected item (with confirmation)\n"
    "  :e | :edit            Edit selected item\n"
    "  :c | :cancel          Cancel command / wizard\n"
    "  :f | :finish          Toggle task completion\n"
    "  :shf | :showFinished  Toggle showing finished tasks\n"
    "  :hf | :hideFinished  Hide finished tasks\n"
    "  :h | :help            This help\n"
    "  :q | :exit            Quit\n"
    "  Tab                   Cycle command completions\n"
    "  \u2191/\u2193              Recall command history (in command bar)\n"
    "  Space                 Toggle bulk selection (task list)\n\n"
    "Search:\n"
    "  /                    Search items in list (type text, Enter to apply, Esc to clear)\n"
    "  :gs <text>           Global search tasks across all archives\n"
    "  :all                 Show all tasks in current archive\n\n"
    "Move:\n"
    "  :mv <target-id>       Move task to directory / dir to archive\n"
    "  :mu | :md             Move task up / down (manual order)\n\n"
    "Quick add:\n"
    "  :qa <name>            Quick-add task (defaults: u:1 d:1)\n"
    "                      :qa name u:3 d:2 t:30 for custom values\n\n"
    "Tags:\n"
    "  :tag <name>          Add tag to selected task\n"
    "  :untag <name>        Remove tag from selected task\n"
    "  :ft <name>           Filter tasks by tag name\n"
    "  :ftc                 Clear tag filter\n\n"
    "Bulk (select with Space first):\n"
    "  :bm                  Mark selected tasks done\n"
    "  :bd                  Delete selected tasks\n"
    "  :bt <name>           Tag selected tasks\n"
    "  :bv <dir-id>         Move selected tasks to directory\n"
    "  :bc                  Clear selection\n\n"
    "Stats & View:\n"
    "  :stats               Show task statistics\n"
    "  :week                Show tasks grouped by deadline this week\n"
    "  :overdue             Show overdue tasks\n"
    "  :ai                  Open opencode with task context\n"
    "  :highlight           Choose highlight beam color\n\n"
    "Undo:\n"
    "  :undo                Undo last delete / edit / finish\n\n"
    "Export/Import:\n"
    "  :export [path]        Export all data as JSON\n"
    "  :import <path>        Import data from JSON\n\n"
    "Timer:\n"
    "  :st <minutes>          Start countdown timer\n"
    "  :ts | :timerStop      Stop timer\n"
    "  :pt | :pauseTimer     Pause / unpause timer\n"
    "  :rt | :resetTimer     Reset timer\n"
    "  :schbar               Show timer schedule bar\n\n"
    "Sort (task list only):\n"
    "  :su a | :su d         Sort by urgency asc / desc\n"
    "  :sd a | :sd d         Sort by difficulty asc / desc\n"
    "  :sn a | :sn d         Sort by name asc / desc\n"
    "  :sdl a | :sdl d       Sort by deadline asc / desc\n"
    "  :sr                   Reset to default order\n\n"
    "Press any key to close."
)


def _bar(val: int, outof: int) -> list:
    result = [("bar_f", "\u25a0" * val)]
    if val < outof:
        result.append(("bar_e", "\u25a1" * (outof - val)))
    return result


_HALF = ["", "\u258f", "\u258e", "\u258d", "\u258c", "\u258b", "\u258a", "\u2589"]

def _pct_bar(pct: int, width: int) -> list:
    total_units = width * 8
    filled_units = int(total_units * pct / 100)
    full = filled_units // 8
    rem = filled_units % 8
    empty = width - full - (1 if rem else 0)
    fill_style = "done" if pct >= 80 else ("warn" if pct >= 50 else "error")
    parts = [(fill_style, "\u2588" * full)]
    if rem:
        parts.append((fill_style, _HALF[rem]))
    if empty > 0:
        parts.append(("bar_e", "\u2591" * empty))
    return parts


def _dur(secs: int) -> str:
    m, s = divmod(secs, 60)
    return f"{m}m{s:02}s" if m else f"{s}s"


_TERMINAL_PRIORITY = [
    "kitty", "alacritty", "wezterm", "gnome-terminal",
    "konsole", "xfce4-terminal", "foot", "xterm",
]


def _detect_terminal() -> str | None:
    for term in _TERMINAL_PRIORITY:
        if shutil.which(term):
            return term
    if shutil.which("x-terminal-emulator"):
        return "x-terminal-emulator"
    return None


def _build_terminal_cmd(terminal: str, cmd_str: str) -> list[str]:
    if terminal == "kitty":
        return ["kitty", "sh", "-c", cmd_str]
    if terminal == "wezterm":
        return ["wezterm", "start", "--", "sh", "-c", cmd_str]
    if terminal == "gnome-terminal":
        return [terminal, "--", "sh", "-c", cmd_str]
    return [terminal, "-e", "sh", "-c", cmd_str]


class CommandEdit(Edit):
    def __init__(self, app: "TaskWatchTUI"):
        super().__init__(("standout", "\u276f "))
        self._app = app

    def keypress(self, size: tuple[int, int], key: str) -> str | None:
        if self._app._in_search_mode:
            if key == "esc":
                self._app._exit_search_mode()
                return None
            if key == "enter":
                self._app._apply_search()
                return None
            result = super().keypress(size, key)
            self._app._on_search_change(self.get_edit_text())
            return result
        if key == "tab":
            self._app._complete_command()
            return None
        if key == "up" and not self._app._prompt_handler and self._app._cmd_history:
            hist = self._app._cmd_history
            if self._app._cmd_history_index < 0:
                self._app._cmd_history_index = len(hist) - 1
            else:
                self._app._cmd_history_index = max(0, self._app._cmd_history_index - 1)
            self.set_edit_text(hist[self._app._cmd_history_index])
            return None
        if key == "down" and not self._app._prompt_handler and self._app._cmd_history:
            hist = self._app._cmd_history
            if self._app._cmd_history_index < 0:
                self.set_edit_text("")
            else:
                self._app._cmd_history_index += 1
                if self._app._cmd_history_index < len(hist):
                    self.set_edit_text(hist[self._app._cmd_history_index])
                else:
                    self._app._cmd_history_index = -1
                    self.set_edit_text("")
            return None
        if key == "enter":
            self._app._tab_matches = []
            self._app._tab_index = -1
            text = self.get_edit_text().strip()
            self.set_edit_text("")
            self._app._handle_submit(text)
            return None
        if key == "esc":
            self._app._tab_matches = []
            self._app._tab_index = -1
            if self.get_edit_text():
                self.set_edit_text("")
                return None
            self._app._handle_submit("")
            return None
        if key == "backspace" and self._app._prompt_handler and not self.get_edit_text():
            self._app._wizard_back()
            return None
        if self._app._tab_matches:
            self._app._tab_matches = []
            self._app._tab_index = -1
        return super().keypress(size, key)


class SelectableText(Text):
    def selectable(self) -> bool:
        return True

    def keypress(self, size: tuple[int, int], key: str) -> str | None:
        return key


class VimListBox(ListBox):
    def keypress(self, size: tuple[int, int], key: str) -> str | None:
        if key == "j":
            key = "down"
        elif key == "k":
            key = "up"
        return super().keypress(size, key)


def _make_list_row(left_text: str, right_text: str, right_width: int,
                    attr: str, focus_attr: str) -> AttrMap:
    left = SelectableText(left_text, wrap="clip")
    right = Text(right_text, align="right", wrap="clip")
    return AttrMap(Columns([("weight", 1, left), (right_width, right)]), attr, focus_attr)


DAYS_OF_WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class DayPickerWidget(WidgetWrap):
    def __init__(
        self,
        on_select: Callable[[str], None],
        on_cancel: Callable[[], None],
    ) -> None:
        self.on_select = on_select
        self.on_cancel = on_cancel
        self.focus_idx = 0

        self._day_widgets = [
            AttrMap(SelectableText(f"  {d}  "), "default", "focus")
            for d in DAYS_OF_WEEK
        ]
        skip = AttrMap(SelectableText("  [Skip]  "), "dim", "focus")
        self._columns = Columns(
            [("pack", w) for w in self._day_widgets] + [("pack", skip)],
            dividechars=1,
        )
        super().__init__(LineBox(self._columns, title="Select repeat day"))

    def keypress(self, size: tuple[int, int], key: str) -> str | None:
        if key == "left":
            self.focus_idx = max(0, self.focus_idx - 1)
            self._columns.focus_position = self.focus_idx
            return None
        if key == "right":
            self.focus_idx = min(len(DAYS_OF_WEEK), self.focus_idx + 1)
            self._columns.focus_position = self.focus_idx
            return None
        if key in ("enter", " "):
            if self.focus_idx < len(DAYS_OF_WEEK):
                self.on_select(DAYS_OF_WEEK[self.focus_idx])
            else:
                self.on_cancel()
            return None
        if key in ("esc", "q"):
            self.on_cancel()
            return None
        return key


class ColorPickerWidget(WidgetWrap):
    def __init__(
        self,
        colors: list[tuple[str, str]],
        current: str,
        on_select: Callable[[str], None],
        on_cancel: Callable[[], None],
    ) -> None:
        self._colors = colors
        self.on_select = on_select
        self.on_cancel = on_cancel
        self._idx = 0
        for i, (name, _) in enumerate(colors):
            if name == current:
                self._idx = i
                break
        self._walker = SimpleFocusListWalker([])
        for name, _ in colors:
            self._walker.append(
                AttrMap(SelectableText(f"  {name}"), "default", "focus")
            )
        self._listbox = ListBox(self._walker)
        self._listbox.focus_position = self._idx
        super().__init__(LineBox(self._listbox, title="Select highlight color"))

    def keypress(self, size: tuple[int, int], key: str) -> str | None:
        if key in ("enter", " "):
            idx = self._listbox.focus_position
            if idx < len(self._colors):
                self.on_select(self._colors[idx][0])
            return None
        if key in ("esc", "q"):
            self.on_cancel()
            return None
        return super().keypress(size, key)


class NoTabColumns(Columns):
    def keypress(self, size: tuple[int, int], key: str) -> str | None:
        if key == "tab":
            self.focus_position = 1 - self.focus_position
            return None
        return super().keypress(size, key)

    def render(self, size, focus=False):
        maxcol, maxrow = size
        canv = super().render(size, focus)
        if canv.rows() != maxrow:
            canv = urwid.CompositeCanvas(canv)
            canv.pad_trim_top_bottom(0, maxrow - canv.rows())
        return canv


class MainFrame(Frame):
    def __init__(self, app: TaskWatchTUI):
        self._app = app
        app._title_text = Text("TaskWatch+")
        app._breadcrumb_text = Text("")
        app._clock_text = Text("")
        app._clock_w = AttrMap(app._clock_text, "dim")
        header = Columns(
            [
                ("pack", AttrMap(app._title_text, "head")),
                AttrMap(app._breadcrumb_text, "dim"),
                ("pack", app._clock_w),
            ],
            dividechars=2,
        )
        app._app_title = header[0]
        app._breadcrumb_w = header[1]

        app._list_walker = SimpleFocusListWalker([])
        app._list_box = VimListBox(app._list_walker)
        app._detail_walker = SimpleFocusListWalker([])
        app._detail_box = VimListBox(app._detail_walker)

        list_pane = AttrMap(LineBox(app._list_box), "pane_dim", "default")
        detail_pane = AttrMap(LineBox(app._detail_box), "pane_dim", "default")

        body = NoTabColumns(
            [("weight", 38, list_pane), ("weight", 62, detail_pane)],
            dividechars=1,
        )
        app._body = body

        app._cmd = CommandEdit(app)
        super().__init__(body, header=header, footer=app._cmd)

    def keypress(self, size: tuple[int, int], key: str) -> str | None:
        if key == ":" and self.focus_position == "body":
            self.focus_position = "footer"
            self._app._cmd.set_edit_text("")
            return None
        if key == "/" and self.focus_position == "body" and not self._app._in_search_mode:
            self._app._enter_search_mode()
            return None
        key = super().keypress(size, key)
        if key is None:
            return None
        if key == "tab":
            if self.focus_position != "body":
                self.focus_position = "body"
                self._app._body.focus_position = 0
                return None
        if key in ("`", "h"):
            self._app._go_back()
            return None
        return key


class TaskWatchTUI:
    def __init__(self):
        self._level = Level.ARCHIVES
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
        self._current_items: list | None = None

        self._timer_running = False
        self._timer_seconds = 0
        self._timer_elapsed = 0
        self._timer_paused = False
        self._timer_task_id: int | None = None
        self._timer_schedule: dict | None = None
        self._timer_segment_idx: int = 0
        self._timer_segment_elapsed: int = 0
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
        self._caption_alarm_handle: object | None = None
        self._current_prompt: str | tuple = ("standout", "\u276f ")
        self._highlight_color: str = "default"
        cfg_path = Path(__file__).resolve().parent.parent / "config" / "config.txt"
        try:
            with open(cfg_path) as f:
                for line in f:
                    if line.startswith("HIGHLIGHT:"):
                        saved = line.split(":", 1)[1].strip()
                        saved = _HIGHLIGHT_ALIASES.get(saved, saved)
                        if saved in {n for n, _ in _HIGHLIGHT_COLORS}:
                            self._highlight_color = saved
                            for entry in PALETTE:
                                if entry[0] == "focus":
                                    urwid_bg = dict(_HIGHLIGHT_COLORS).get(saved, "default")
                                    PALETTE[PALETTE.index(entry)] = (
                                        entry[0], entry[1], urwid_bg, *entry[3:],
                                    )
                                    break
        except OSError:
            pass

        self._timer_state_path = Path.home() / ".local" / "share" / "taskwatch" / "timer_state.json"
        self._daemon_path = Path(__file__).resolve().parent / "timer_daemon.py"

        self._frame = MainFrame(self)
        self._loop = urwid.MainLoop(
            self._frame, PALETTE, unhandled_input=self._unhandled_input
        )
        self._list_walker: SimpleFocusListWalker
        self._list_box: ListBox
        self._detail_walker: SimpleFocusListWalker
        self._detail_box: ListBox
        self._cmd: CommandEdit
        self._breadcrumb_text: Text
        self._clock_text: Text
        self._breadcrumb_w: AttrMap
        self._clock_w: AttrMap

    def _focus_body(self) -> None:
        self._frame.focus_position = "body"
        self._body.focus_position = 0

    def _set_timed_caption(self, attr: str, text: str, duration: float = 2) -> None:
        if self._caption_alarm_handle is not None:
            try:
                self._loop.remove_alarm(self._caption_alarm_handle)
            except Exception:
                pass
        self._cmd.set_caption((attr, text))
        self._caption_alarm_handle = self._loop.set_alarm_in(
            duration,
            lambda *a: (
                setattr(self, "_current_prompt", ("standout", "\u276f ")),
                self._cmd.set_caption(("standout", "\u276f ")),
            ),
        )

    def _refresh_list(self) -> None:
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
            pairs: list[tuple[str, str]] = []
            for d in items:
                total, done = dir_stats.get(d.id, (0, 0))
                pairs.append((f"\uf4d3 {d.name}", f"[{done}/{total}]"))
            if pairs:
                rw = max(len(r) for _, r in pairs) + 1
                for left, right in pairs:
                    w = _make_list_row(left, right, rw, "default", "focus")
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
            pairs: list[tuple[str, str, str]] = []
            for t in items:
                sel = "[x]" if t.id in self._bulk_selection else " "
                prefix = "\u2713 " if t.finished else f"\u25cb{sel} "
                cnt = note_counts.get(t.id, 0)
                tags = task_tags.get(t.id, [])
                tag_str = f" [{','.join(tags)}]" if tags else ""
                dir_str = f" [{dir_map[t.id]}]" if t.id in dir_map else ""
                left = prefix + f"\ueebf {t.name}"
                right = f"[{cnt}]{tag_str}{dir_str}"
                selected = t.id in self._bulk_selection
                if t.finished:
                    pairs.append((left, right, "dim"))
                elif selected:
                    pairs.append((left, right, "focus"))
                else:
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
                first_line = n.note.split("\n")[0][:60]
                label = f"\U000f039a {n.id}: {first_line}"
                w = AttrMap(SelectableText(label), "default", "focus")
                self._list_walker.append(w)

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
            self._set_detail(
                [("head", f"\uf187 {a.name}"), "\n\nPress Enter to browse directories."],
            )

        elif self._level == Level.DIRECTORIES:
            d = self._current_items[idx]
            self._set_detail(
                [("head", f"\uf4d3 {d.name}"), "\n\nPress Enter to browse tasks."],
            )

        elif self._level == Level.TASKS:
            task = self._current_items[idx]
            self._selected_task_id = task.id
            self._selected_task_name = task.name
            self._show_task_detail(task)

        elif self._level == Level.NOTES:
            n = self._current_items[idx]
            self._set_detail(n.note)

    def _show_task_detail(self, task) -> None:
        s = timer_mod.compute_schedule(task)

        status = "\u2713 Done" if task.finished else "\u25cb Pending"
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
        self._detail_walker.append(Text([("head", f"\ueebf {task.name}")]))
        self._detail_walker.append(Text(""))

        # Status block
        self._detail_walker.append(Text([
            ("head", "Status: "), ("done" if task.finished else "default", status),
        ]))
        self._detail_walker.append(Text([("head", "Deadline: "), deadline]))
        self._detail_walker.append(Text([("head", "Repeat: "), repeat]))
        self._detail_walker.append(Text([("head", "Finished: "), fd]))
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

    def _handle_command(self, cmd: str) -> None:
        if cmd in ("q", "exit"):
            raise urwid.ExitMainLoop()
        if cmd in ("h", "help"):
            self._show_help()
            return
        if cmd in ("a", "add"):
            self._cmd_add()
            return
        if cmd in ("r", "remove", "d"):
            self._cmd_remove()
            return
        if cmd in ("e", "edit"):
            self._cmd_edit()
            return
        if cmd in ("f", "finish"):
            self._cmd_finish()
            return
        if cmd in ("shf", "showFinished"):
            self._show_finished = not self._show_finished
            self._refresh_list()
            return
        if cmd in ("hf", "hideFinished"):
            self._show_finished = False
            self._refresh_list()
            return
        if cmd in ("c", "cancel"):
            self._prompt_handler = None
            self._wizard_stack.clear()
            self._current_prompt = ": "
            self._cmd.set_caption(": ")
            self._bulk_selection.clear()
            self._refresh_list()
            self._focus_body()
            return

        if cmd == "all":
            if self._selected_archive_id is not None:
                self._all_tasks_mode = True
                self._level = Level.TASKS
                self._refresh_list()
            return
        if cmd == "mu":
            if self._level == Level.TASKS and not self._sort_field:
                sid = self._get_selected_id()
                if sid is not None and task_cmds.move_task_up(sid):
                    self._refresh_list()
                    self._show_detail()
            return
        if cmd == "md":
            if self._level == Level.TASKS and not self._sort_field:
                sid = self._get_selected_id()
                if sid is not None and task_cmds.move_task_down(sid):
                    self._refresh_list()
                    self._show_detail()
            return
        if cmd.startswith("mv "):
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
            return
        if cmd.startswith("qa "):
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
                # Strip tokens from name
                for token in rest.split():
                    if any(token.startswith(p) for p in ("u:", "U:", "d:", "D:", "t:", "T:", "dl:", "DL:")):
                        name = name.replace(token, "", 1).strip()
                if name:
                    task_cmds.create_task(
                        self._selected_directory_id, name,
                        urgency=urgency, difficulty=difficulty,
                        time_dedicated=time_dedicated, deadline=deadline,
                    )
                    self._set_timed_caption("done", "Task added ")
                    self._refresh_list()
            return
        if cmd.startswith("export"):
            parts = cmd.split(" ", 1)
            path = parts[1].strip() if len(parts) > 1 else "/tmp/taskwatch_export.json"
            if io_cmds.export_data(path):
                self._set_timed_caption("done", f"Exported to {path} ", 3)
            else:
                self._set_timed_caption("error", "Export failed ", 3)
            return
        if cmd.startswith("import "):
            path = cmd.split(" ", 1)[1].strip()
            result = io_cmds.import_data(path)
            self._set_timed_caption("done" if "failed" not in result else "error", f"{result} ", 3)
            self._refresh_list()
            return
        if cmd == "bm":
            if self._level == Level.TASKS and self._bulk_selection:
                for tid in list(self._bulk_selection):
                    task_cmds.mark_done(tid)
                self._bulk_selection.clear()
                self._refresh_list()
            return
        if cmd == "bd":
            if self._level == Level.TASKS and self._bulk_selection:
                self._start_wizard(
                    f"Delete {len(self._bulk_selection)} tasks? (y/n): ",
                    self._wiz_bulk_delete,
                )
            return
        if cmd.startswith("bt "):
            if self._level == Level.TASKS and self._bulk_selection:
                tag_name = cmd.split(" ", 1)[1].strip()
                if tag_name:
                    for tid in list(self._bulk_selection):
                        tag_cmds.add_tag_to_task(tid, tag_name)
                    self._bulk_selection.clear()
                    self._refresh_list()
            return
        if cmd.startswith("bv "):
            if self._level == Level.TASKS and self._bulk_selection:
                try:
                    target_dir = int(cmd.split(" ", 1)[1])
                    for tid in list(self._bulk_selection):
                        task_cmds.move_task(tid, target_dir)
                    self._bulk_selection.clear()
                    self._refresh_list()
                except ValueError:
                    pass
            return
        if cmd == "bc":
            self._bulk_selection.clear()
            self._refresh_list()
            return

        if cmd == "stats":
            self._show_stats()
            return
        if cmd == "undo":
            self._undo_last_action()
            return
        if cmd == "week":
            self._show_week_view()
            return
        if cmd == "overdue":
            self._show_overdue_view()
            return
        if cmd == "schbar":
            self._show_schedule_bar()
            return
        if cmd == "ai":
            self._cmd_ai()
            return
        if cmd == "highlight":
            self._show_highlight_picker()
            return
        if cmd.startswith("gs "):
            query = cmd.split(" ", 1)[1].strip()
            if query:
                self._show_global_search(query)
            return
        if cmd.startswith("tag "):
            tag_name = cmd.split(" ", 1)[1].strip()
            if tag_name and self._selected_task_id is not None:
                tag_cmds.add_tag_to_task(self._selected_task_id, tag_name)
                self._refresh_list()
                self._show_detail()
            return
        if cmd.startswith("untag "):
            tag_name = cmd.split(" ", 1)[1].strip()
            if tag_name and self._selected_task_id is not None:
                tag_cmds.remove_tag_from_task(self._selected_task_id, tag_name)
                self._refresh_list()
                self._show_detail()
            return
        if cmd.startswith("ft ") and self._level == Level.TASKS:
            self._filter_tag = cmd.split(" ", 1)[1].strip()
            self._refresh_list()
            self._show_detail()
            return
        if cmd in ("ftc",):
            self._filter_tag = None
            if self._level == Level.TASKS:
                self._refresh_list()
                self._show_detail()
            return

        if cmd == "st":
            if self._selected_task_id is not None:
                t = task_cmds.get_task(self._selected_task_id)
                if t:
                    self._start_timer_for_task(t)
            return
        if cmd.startswith("st "):
            try:
                mins = int(cmd.split(" ", 1)[1])
                if mins > 0:
                    self._start_timer(mins)
            except ValueError:
                pass
            return
        if cmd in ("ts", "timerStop"):
            self._stop_timer()
            return
        if cmd in ("pt", "pauseTimer"):
            self._timer_paused = not self._timer_paused
            self._write_timer_state({"paused": self._timer_paused})
            self._update_clock_display()
            return
        if cmd in ("rt", "resetTimer"):
            self._stop_timer()
            return

        if cmd in ("su a", "su d"):
            self._sort_field = "urgency"
            self._sort_dir = "asc" if cmd == "su a" else "desc"
            if self._level == Level.TASKS:
                self._refresh_list()
            return
        if cmd in ("sd a", "sd d"):
            self._sort_field = "difficulty"
            self._sort_dir = "asc" if cmd == "sd a" else "desc"
            if self._level == Level.TASKS:
                self._refresh_list()
            return
        if cmd in ("sn a", "sn d"):
            self._sort_field = "name"
            self._sort_dir = "asc" if cmd == "sn a" else "desc"
            if self._level == Level.TASKS:
                self._refresh_list()
            return
        if cmd in ("sdl a", "sdl d"):
            self._sort_field = "deadline"
            self._sort_dir = "asc" if cmd == "sdl a" else "desc"
            if self._level == Level.TASKS:
                self._refresh_list()
            return
        if cmd == "sr":
            self._sort_field = None
            self._sort_dir = "asc"
            if self._level == Level.TASKS:
                self._refresh_list()
            return

        self._focus_body()

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

    def _wiz_archive_name(self, name: str) -> None:
        if not name:
            self._end_wizard()
            return
        archive_cmds.create_archive(name)
        self._end_wizard()

    def _wiz_dir_name(self, name: str) -> None:
        if not name:
            self._end_wizard()
            return
        directory_cmds.create_directory(
            self._selected_archive_id, name
        )
        self._end_wizard()

    def _wiz_task_name(self, name: str) -> None:
        if not name:
            self._start_wizard(
                "Task name (step 1): ",
                self._wiz_task_name,
            )
            return
        self._wiz_name = name
        self._start_wizard(
            "Description (step 2): ",
            partial(self._wiz_task_description, name),
        )

    def _wiz_task_description(self, name: str, desc: str) -> None:
        self._wiz_desc = desc.strip()
        self._start_wizard(
            "Urgency 1-5 (step 3): ",
            partial(self._wiz_task_urgency, name),
        )

    def _wiz_task_urgency(self, name: str, urgency_str: str) -> None:
        if not urgency_str:
            urgency = 1
        else:
            try:
                urgency = int(urgency_str)
                if not 1 <= urgency <= 5:
                    raise ValueError
            except ValueError:
                self._start_wizard(
                    "Urgency 1-5 (step 3): ",
                    partial(self._wiz_task_urgency, name),
                )
                return
        self._start_wizard(
            "Difficulty 1-5 (step 4): ",
            partial(self._wiz_task_difficulty, name, urgency),
        )

    def _wiz_task_difficulty(
        self, name: str, urgency: int, diff_str: str
    ) -> None:
        if not diff_str:
            difficulty = 1
        else:
            try:
                difficulty = int(diff_str)
                if not 1 <= difficulty <= 5:
                    raise ValueError
            except ValueError:
                self._start_wizard(
                    "Difficulty 1-5 (step 4): ",
                    partial(self._wiz_task_difficulty, name, urgency),
                )
                return
        self._start_wizard(
            "Time budget minutes (step 5): ",
            partial(self._wiz_task_time, name, urgency, difficulty),
        )

    def _wiz_task_time(
        self,
        name: str,
        urgency: int,
        difficulty: int,
        time_str: str,
    ) -> None:
        if not time_str:
            time_dedicated = 0
        else:
            try:
                time_dedicated = int(time_str)
            except ValueError:
                self._start_wizard(
                    "Time budget minutes (step 5): ",
                    partial(self._wiz_task_time, name, urgency, difficulty),
                )
                return
        self._start_wizard(
            "Deadline dd/MM/yyyy or 'none' (step 6): ",
            partial(
                self._wiz_task_deadline,
                name,
                urgency,
                difficulty,
                time_dedicated,
            ),
        )

    def _wiz_task_deadline(
        self,
        name: str,
        urgency: int,
        difficulty: int,
        time_dedicated: int,
        deadline: str,
    ) -> None:
        if not deadline:
            deadline = "none"
        if deadline != "none":
            try:
                datetime.strptime(deadline, "%d/%m/%Y")
            except ValueError:
                self._start_wizard(
                    "Deadline dd/MM/yyyy or 'none' (step 6): ",
                    partial(
                        self._wiz_task_deadline,
                        name,
                        urgency,
                        difficulty,
                        time_dedicated,
                    ),
                )
                return
        self._start_wizard(
            "Repeatable? y/n (step 7): ",
            partial(
                self._wiz_task_repeat,
                name,
                urgency,
                difficulty,
                time_dedicated,
                deadline,
            ),
        )

    def _wiz_task_repeat(
        self,
        name: str,
        urgency: int,
        difficulty: int,
        time_dedicated: int,
        deadline: str,
        repeat_yn: str,
    ) -> None:
        if not repeat_yn:
            repeatable = False
        else:
            repeatable = repeat_yn.lower() in ("y", "yes")
        if repeatable:
            self._start_wizard(
                "Repeat type daily/weekly/biweekly/monthly/yearly (step 8): ",
                partial(
                    self._wiz_task_repeat_type,
                    name,
                    urgency,
                    difficulty,
                    time_dedicated,
                    deadline,
                ),
            )
        else:
            task_cmds.create_task(
                self._selected_directory_id,
                name,
                description=self._wiz_desc,
                urgency=urgency,
                difficulty=difficulty,
                time_dedicated=time_dedicated,
                deadline=deadline,
                repeatable=False,
            )
            self._end_wizard()

    def _wiz_task_repeat_type(
        self,
        name: str,
        urgency: int,
        difficulty: int,
        time_dedicated: int,
        deadline: str,
        repeat_type: str,
    ) -> None:
        if not repeat_type:
            repeat_type = "daily"
        valid = ("daily", "weekly", "biweekly", "monthly", "yearly")
        if repeat_type not in valid:
            self._start_wizard(
                "Repeat type daily/weekly/biweekly/monthly/yearly (step 8): ",
                partial(
                    self._wiz_task_repeat_type,
                    name,
                    urgency,
                    difficulty,
                    time_dedicated,
                    deadline,
                ),
            )
            return
        if repeat_type == "daily":
            self._start_wizard(
                "Auto-repeat on finish? y/n (step 9): ",
                partial(
                    self._wiz_task_auto_repeat,
                    name,
                    urgency,
                    difficulty,
                    time_dedicated,
                    deadline,
                    repeat_type,
                    "none",
                ),
            )
        else:
            self._show_day_picker(
                on_select=partial(
                    self._wiz_task_repeat_day,
                    name,
                    urgency,
                    difficulty,
                    time_dedicated,
                    deadline,
                    repeat_type,
                ),
                on_cancel=partial(
                    self._start_wizard,
                    "Auto-repeat on finish? y/n (step 9): ",
                    partial(
                        self._wiz_task_auto_repeat,
                        name,
                        urgency,
                        difficulty,
                        time_dedicated,
                        deadline,
                        repeat_type,
                        "none",
                    ),
                ),
            )

    def _wiz_task_repeat_day(
        self,
        name: str,
        urgency: int,
        difficulty: int,
        time_dedicated: int,
        deadline: str,
        repeat_type: str,
        day: str,
    ) -> None:
        self._start_wizard(
            "Auto-repeat on finish? y/n (step 9): ",
            partial(
                self._wiz_task_auto_repeat,
                name,
                urgency,
                difficulty,
                time_dedicated,
                deadline,
                repeat_type,
                day,
            ),
        )

    def _wiz_task_auto_repeat(
        self,
        name: str,
        urgency: int,
        difficulty: int,
        time_dedicated: int,
        deadline: str,
        repeat_type: str,
        repeat_on_specific_day: str,
        auto_repeat_yn: str,
    ) -> None:
        to_complete = auto_repeat_yn.lower() in ("y", "yes")
        task_cmds.create_task(
            self._selected_directory_id,
            name,
            description=self._wiz_desc,
            urgency=urgency,
            difficulty=difficulty,
            time_dedicated=time_dedicated,
            deadline=deadline,
            repeatable=True,
            repeatable_type=repeat_type,
            has_to_be_completed_to_repeat=to_complete,
            repeat_on_specific_day=repeat_on_specific_day,
        )
        self._end_wizard()

    def _wiz_note_content(self, today: str, content: str) -> None:
        note_cmds.create_note(self._selected_task_id, today, content)
        self._end_wizard()

    def _cmd_remove(self) -> None:
        sid = self._get_selected_id()
        if sid is None:
            return
        name = self._get_selected_name() or str(sid)
        self._start_wizard(
            f"Delete '{name}'? (y/n): ",
            partial(self._wiz_confirm_delete, sid),
        )

    def _wiz_confirm_delete(self, sid: int, answer: str) -> None:
        if answer.lower() in ("y", "yes"):
            self._do_remove(sid)
        self._end_wizard()

    def _do_remove(self, sid: int) -> None:
        if self._level == Level.ARCHIVES:
            archive_cmds.delete_archive(sid)
        elif self._level == Level.DIRECTORIES:
            directory_cmds.delete_directory(sid)
        elif self._level == Level.TASKS:
            task_data = undo_cmds.get_task_data(sid)
            if task_data is not None:
                conn = db_mod.get_conn()
                notes = conn.execute(
                    "SELECT id, task_id, date, note FROM notes WHERE task_id = ?",
                    (sid,),
                ).fetchall()
                task_data["notes"] = [dict(n) for n in notes]
                undo_cmds.push("task_delete", task_data)
            task_cmds.delete_task(sid)
        elif self._level == Level.NOTES:
            note_cmds.delete_note(sid)
        self._refresh_list()

    def _wiz_bulk_delete(self, answer: str) -> None:
        if answer.lower() in ("y", "yes"):
            tids = list(self._bulk_selection)
            conn = db_mod.get_conn()
            all_notes: dict[int, list[dict]] = {}
            if tids:
                placeholders = ",".join("?" for _ in tids)
                for row in conn.execute(
                    f"SELECT id, task_id, date, note FROM notes WHERE task_id IN ({placeholders})",
                    tids,
                ):
                    all_notes.setdefault(row["task_id"], []).append(dict(row))
            for tid in tids:
                task_data = undo_cmds.get_task_data(tid)
                if task_data is not None:
                    task_data["notes"] = all_notes.get(tid, [])
                    undo_cmds.push("task_delete", task_data)
                task_cmds.delete_task(tid)
            self._bulk_selection.clear()
            self._refresh_list()
        self._end_wizard()

    def _cmd_edit(self) -> None:
        sid = self._get_selected_id()
        if sid is None:
            return
        sname = self._get_selected_name()
        if self._level == Level.ARCHIVES:
            self._start_wizard(
                f"New name [{sname}]: ",
                partial(self._wiz_edit_archive, sid, sname),
            )
        elif self._level == Level.DIRECTORIES:
            self._start_wizard(
                f"New name [{sname}]: ",
                partial(self._wiz_edit_dir, sid, sname),
            )
        elif self._level == Level.TASKS and self._selected_task_id is not None:
            self._edit_task(sid)

    def _wiz_edit_archive(
        self, archive_id: int, old_name: str, new_name: str
    ) -> None:
        if not new_name:
            self._end_wizard()
            return
        archive_cmds.rename_archive(archive_id, new_name)
        self._end_wizard()

    def _wiz_edit_dir(
        self, dir_id: int, old_name: str, new_name: str
    ) -> None:
        if not new_name:
            self._end_wizard()
            return
        directory_cmds.rename_directory(dir_id, new_name)
        self._end_wizard()

    def _edit_task(self, task_id: int) -> None:
        task = task_cmds.get_task(task_id)
        if not task:
            return
        self._edit_ctx = {
            "task_id": task_id,
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
            f"Name (step 1) [{task.name}]: ",
            self._wiz_edit_task_name,
        )

    def _wiz_edit_task_name(self, name: str) -> None:
        if name:
            self._edit_ctx["name"] = name
        cur = self._edit_ctx["description"]
        self._start_wizard(
            f"Description (step 2) [{cur}]: ",
            self._wiz_edit_task_description,
        )

    def _wiz_edit_task_description(self, desc: str) -> None:
        self._edit_ctx["description"] = desc
        self._start_wizard(
            f"Urgency 1-5 (step 3) [{self._edit_ctx['urgency']}]: ",
            self._wiz_edit_task_urgency,
        )

    def _wiz_edit_task_urgency(self, urgency_str: str) -> None:
        if urgency_str:
            try:
                v = int(urgency_str)
                if 1 <= v <= 5:
                    self._edit_ctx["urgency"] = v
            except ValueError:
                pass
        self._start_wizard(
            f"Difficulty 1-5 (step 4) [{self._edit_ctx['difficulty']}]: ",
            self._wiz_edit_task_difficulty,
        )

    def _wiz_edit_task_difficulty(self, diff_str: str) -> None:
        if diff_str:
            try:
                v = int(diff_str)
                if 1 <= v <= 5:
                    self._edit_ctx["difficulty"] = v
            except ValueError:
                pass
        self._start_wizard(
            f"Time budget minutes (step 5) [{self._edit_ctx['time_dedicated']}]: ",
            self._wiz_edit_task_time,
        )

    def _wiz_edit_task_time(self, time_str: str) -> None:
        if time_str:
            try:
                self._edit_ctx["time_dedicated"] = int(time_str)
            except ValueError:
                pass
        self._start_wizard(
            f"Deadline (step 6) [{self._edit_ctx['deadline']}]: ",
            self._wiz_edit_task_deadline,
        )

    def _wiz_edit_task_deadline(self, deadline: str) -> None:
        if deadline and deadline != self._edit_ctx["deadline"]:
            if deadline != "none":
                try:
                    datetime.strptime(deadline, "%d/%m/%Y")
                    self._edit_ctx["deadline"] = deadline
                except ValueError:
                    pass
            else:
                self._edit_ctx["deadline"] = "none"

        cur = "y" if self._edit_ctx.get("repeatable", False) else "n"
        self._start_wizard(
            f"Repeatable? y/n (step 7) [{cur}]: ",
            self._wiz_edit_repeatable,
        )

    def _wiz_edit_repeatable(self, yn: str) -> None:
        if yn:
            self._edit_ctx["repeatable"] = yn.lower() in ("y", "yes")
        if self._edit_ctx.get("repeatable", False):
            cur = self._edit_ctx.get("repeatable_type", "none")
            self._start_wizard(
                f"Repeat type daily/weekly/biweekly/monthly/yearly (step 8) [{cur}]: ",
                self._wiz_edit_repeat_type,
            )
        else:
            self._save_edit_task()

    def _wiz_edit_repeat_type(self, repeat_type: str) -> None:
        if not repeat_type:
            repeat_type = self._edit_ctx.get("repeatable_type", "daily")
        valid = ("daily", "weekly", "biweekly", "monthly", "yearly")
        if repeat_type not in valid:
            self._start_wizard(
                f"Repeat type daily/weekly/biweekly/monthly/yearly (step 8) [{self._edit_ctx.get('repeatable_type', 'none')}]: ",
                self._wiz_edit_repeat_type,
            )
            return
        self._edit_ctx["repeatable_type"] = repeat_type
        self._edit_ctx["repeat_on_specific_day"] = "none"
        if repeat_type == "daily":
            self._start_wizard(
                "Auto-repeat on finish? y/n (step 9): ",
                self._wiz_edit_auto_repeat,
            )
        else:
            self._show_day_picker(
                on_select=partial(self._wiz_edit_repeat_day, repeat_type),
                on_cancel=self._wiz_edit_skip_day_picker,
            )

    def _wiz_edit_repeat_day(self, repeat_type: str, day: str) -> None:
        self._edit_ctx["repeat_on_specific_day"] = day
        self._start_wizard(
            "Auto-repeat on finish? y/n (step 9): ",
            self._wiz_edit_auto_repeat,
        )

    def _wiz_edit_skip_day_picker(self) -> None:
        self._edit_ctx["repeat_on_specific_day"] = "none"
        self._start_wizard(
            "Auto-repeat on finish? y/n (step 9): ",
            self._wiz_edit_auto_repeat,
        )

    def _wiz_edit_auto_repeat(self, auto_repeat_yn: str) -> None:
        if auto_repeat_yn:
            to_complete = auto_repeat_yn.lower() in ("y", "yes")
            self._edit_ctx["has_to_be_completed_to_repeat"] = to_complete
        self._save_edit_task()

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
        if task.finished:
            undo_cmds.push("task_unfinish", {"task_id": sid})
            task_cmds.mark_not_done(sid)
        else:
            undo_cmds.push("task_finish", {"task_id": sid})
            task_cmds.mark_done(sid)
        self._refresh_list()
        self._show_detail()

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
        self._refresh_list()
        self._show_detail()
        self._focus_body()

    def _on_search_change(self, text: str) -> None:
        self._filter_text = text
        self._refresh_list()
        self._show_detail()

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

    def _show_help(self) -> None:
        help_w = LineBox(VimListBox(SimpleFocusListWalker([SelectableText(HELP_TEXT)])))
        self._help_overlay = Overlay(
            help_w,
            self._frame,
            align="center",
            width=("relative", 80),
            valign="middle",
            height=("relative", 80),
        )
        self._loop.widget = self._help_overlay

    def _show_stats(self) -> None:
        import shutil

        s = stats_cmds.compute_stats()

        term_width = shutil.get_terminal_size().columns
        bar_width = max(10, min(25, int(term_width * 0.22)))
        total_h, total_m = divmod(s["total_time"], 60)

        walker: list[Text] = []

        def add(text: str | list) -> None:
            walker.append(Text(text))

        def add_header(title: str) -> None:
            add("")
            add([("head", f"  \u2500\u2500 {title} \u2500\u2500")])

        # ── Title ──
        add([("head", "\n  \u2694  TaskWatch+ Stats  \u2694")])
        add("")

        # ── Summary ──
        add_header("Summary")
        tl = s["total"]
        fl = s["finished"]
        add(f"    Tasks:     {tl:>4}  Finished: {fl}/{tl}  ({s['completion_pct']}%)")
        total_h1, total_m1 = divmod(s["total_time"], 60)
        add(f"    Time:      {total_h1}h {total_m1:02d}m  Tags: {s['total_tags']}")
        add(f"    Pending:   {s['pending']}")

        # ── Completion bar ──
        add_header("Completion")
        bar_parts: list = [("head", "  ")]
        bar_parts.extend(_pct_bar(s["completion_pct"], bar_width))
        bar_parts.append(f"  {s['completion_pct']:>3}%")
        add(bar_parts)

        # ── Status ──
        add_header("Status")
        add([
            "    ",
            ("head", "\u26a0 Overdue:"), f"  {s['overdue']:>3}",
            ("head", "     \u2713 Today:"), f"  {s['today_completed']:>3}",
            ("head", "     \u2713 Week:"), f"  {s['completed_this_week']:>3}",
        ])

        # ── Deadline timeline ──
        tl_map = s["deadline_timeline"]
        timeline_labels = [
            ("\u26a0 Overdue", tl_map["overdue"], "error"),
            ("\u2713 Due today", tl_map["due_today"], "warn"),
            ("\u25b6 This week", tl_map["this_week"], "default"),
            ("\u25b7 Next week", tl_map["next_week"], "default"),
            ("\u2026 Later", tl_map["later"], "dim"),
            ("\u2014 No deadline", tl_map["no_deadline"], "dim"),
        ]
        max_tl = max((c for _, c, _ in timeline_labels), default=1)
        add_header("Deadline Timeline")
        for label, count, attr in timeline_labels:
            tl_bar_w = bar_width - 2
            tl_filled = int(tl_bar_w * count / max_tl) if max_tl else 0
            add([
                f"    {label:<16}", " ",
                (attr, "\u2588" * tl_filled + "\u2591" * (tl_bar_w - tl_filled)),
                f"  {count:>3}",
            ])

        # ── Urgency × Difficulty heatmap ──
        grid = s["ud_grid"]
        add_header("Urgency × Difficulty (pending)")
        add("         D1  D2  D3  D4  D5")
        for u_idx, row in enumerate(grid):
            cells: list = [f"    U{u_idx + 1}:  "]
            for c in row:
                attr = "error" if c >= 5 else ("warn" if c >= 3 else "dim")
                cells.append((attr, f" {c:>2} "))
            add(cells)

        # ── Archive stats ──
        arch_stats = s["archive_stats"]
        if arch_stats:
            add_header("Archives")
            for a in arch_stats:
                name = a["name"]
                arch_bar_w = bar_width - 2
                filled = int(arch_bar_w * a["pct"] / 100)
                ah, am = divmod(a["time_budget"], 60)
                add([
                    f"    {name:<14} ",
                    ("done" if a["pct"] >= 80 else "warn" if a["pct"] >= 50 else "error",
                     "\u2588" * filled + "\u2591" * (arch_bar_w - filled)),
                    f"  {a['pct']:>3}%  {a['done']}/{a['total']}",
                    ("dim", f"  {ah}h"),
                ])

        # ── Directory stats ──
        dirs = stats_cmds.all_directory_stats()
        if dirs:
            add_header("Directories (top)")
            for d in dirs[:10]:
                name = d["name"][:18]
                dir_bar_w = bar_width - 2
                filled = int(dir_bar_w * d["pct"] / 100)
                add([
                    f"    {name:<18} ",
                    ("done" if d["pct"] >= 80 else "warn" if d["pct"] >= 50 else "error",
                     "\u2588" * filled + "\u2591" * (dir_bar_w - filled)),
                    f"  {d['pct']:>3}%  {d['done']}/{d['total']}",
                ])
            if len(dirs) > 10:
                add([("dim", f"    ... and {len(dirs) - 10} more")])

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

    def _show_global_search(self, query: str) -> None:
        results = task_cmds.search_tasks_global(query)
        if not results:
            raw = [("head", "  Global Search\n\n"), ("dim", "  No tasks match your query.")]
        else:
            raw = [("head", f"  Global Search: {query}\n\n")]
            for i, r in enumerate(results, 1):
                task = r["task"]
                path = f"{r['arch_name']} \u25b8 {r['dir_name']}"
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
        from datetime import timedelta
        import shutil

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

        ww = LineBox(Text(content))
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
        ow = LineBox(Text(content))
        self._stats_overlay = Overlay(
            ow,
            self._frame,
            align="center",
            width=("relative", 60),
            valign="middle",
            height=("relative", 70),
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
        config_path = Path(__file__).resolve().parent.parent / "config" / "config.txt"
        try:
            with open(config_path) as f:
                for line in f:
                    if line.startswith("TERMINAL:"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            pass
        term = _detect_terminal()
        if term:
            try:
                with open(config_path, "a") as f:
                    f.write(f"\nTERMINAL:{term}\n")
            except OSError:
                pass
        return term

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
            "notes": [{"date": n.date, "note": n.note} for n in notes],
        }

    def _cmd_ai(self) -> None:
        if self._level != Level.TASKS or self._selected_task_id is None:
            self._set_timed_caption("error", "Select a task first ")
            return
        opencode_path = shutil.which("opencode")
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
        cmd = _build_terminal_cmd(
            terminal,
            f"{opencode_path} run -f '{ctx_file}' -i --dir '{project_root}'",
        )
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._set_timed_caption("done", "Opening opencode... ")

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
                break
        self._loop.widget = self._frame
        self._loop.screen.register_palette(PALETTE)
        self._loop.draw_screen()
        self._highlight_color = name
        config_path = Path(__file__).resolve().parent.parent / "config" / "config.txt"
        try:
            lines: list[str] = []
            found = False
            try:
                with open(config_path) as f:
                    lines = f.readlines()
            except OSError:
                pass
            with open(config_path, "w") as f:
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
                char = "░"
                base_attr = "dim"
            elif i % 2 == 1:
                char = "▓"
                base_attr = "head"
            else:
                char = "▒"
                base_attr = "default"
            if i < current_idx:
                bar_parts.append((base_attr, char * w))
            elif i == current_idx:
                filled = int(w * current_elapsed / seg) if seg else 0
                if filled:
                    bar_parts.append((base_attr, char * filled))
                if w - filled > 0:
                    bar_parts.append(("bar_e", char * (w - filled)))
            else:
                bar_parts.append(("bar_e", char * w))

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
            if success and "notes" in data:
                for note_data in data["notes"]:
                    conn = db_mod.get_conn()
                    try:
                        conn.execute(
                            "INSERT INTO notes (id, task_id, date, note) VALUES (?, ?, ?, ?)",
                            (note_data["id"], note_data["task_id"], note_data["date"], note_data["note"]),
                        )
                        conn.commit()
                    except Exception:
                        pass
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
        else:
            success = False
        if success:
            self._set_timed_caption("done", f"Undone: {action} ")
        self._refresh_list()
        self._show_detail()

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

    def _start_wizard(
        self, prompt: str | tuple, handler: Callable[[str], None]
    ) -> None:
        if self._prompt_handler is not None:
            self._wizard_stack.append({
                "prompt": self._current_prompt,
                "handler": self._prompt_handler,
            })
        self._prompt_handler = handler
        self._current_prompt = prompt
        self._cmd.set_caption(prompt)
        self._cmd.set_edit_text("")
        self._frame.focus_position = "footer"

    def _wizard_back(self) -> None:
        if not self._wizard_stack:
            return
        entry = self._wizard_stack.pop()
        self._prompt_handler = entry["handler"]
        self._current_prompt = entry["prompt"]
        self._cmd.set_caption(entry["prompt"])
        self._cmd.set_edit_text("")

    def _end_wizard(self) -> None:
        self._prompt_handler = None
        self._wizard_stack.clear()
        self._current_prompt = ("standout", "\u276f ")
        self._cmd.set_caption(("standout", "\u276f "))
        self._refresh_list()
        self._show_detail()
        try:
            calcurse_cmds.sync_to_calcurse()
        except Exception:
            pass
        self._focus_body()

    def _start_timer_for_task(self, task) -> None:
        schedule = timer_mod.compute_schedule(task)
        if "error" in schedule:
            return
        total = schedule.get("total_seconds", sum(schedule.get("segments", [0])))
        self._kill_daemon()
        self._write_timer_state({
            "running": True,
            "mode": "scheduled",
            "task_id": task.id,
            "task_name": task.name,
            "schedule": schedule,
            "total_seconds": total,
            "remaining": total,
            "paused": False,
            "stopped": False,
            "start_time": time.time(),
            "pause_elapsed": 0.0,
            "segment_idx": 0,
            "segment_elapsed": 0,
        })
        self._spawn_daemon()
        self._timer_running = True
        self._timer_schedule = schedule
        self._timer_segment_idx = 0
        self._timer_segment_elapsed = 0
        self._timer_paused = False
        self._timer_task_id = task.id
        self._timer_seconds = total
        self._timer_elapsed = 0
        self._update_clock_display()

    def _write_timer_file(self) -> None:
        try:
            if not self._timer_running:
                with open("/tmp/taskwatch_timer.json", "w") as f:
                    json.dump({"text": "", "class": "inactive"}, f)
                return
            if self._timer_schedule:
                segments = self._timer_schedule["segments"]
                seg_dur = segments[self._timer_segment_idx]
                remaining = max(0, seg_dur - self._timer_segment_elapsed)
                if self._timer_segment_idx == 0:
                    phase = "INTRO"
                elif self._timer_segment_idx % 2 == 1:
                    phase = "WORK"
                else:
                    phase = "BREAK"
            else:
                remaining = max(0, self._timer_seconds - self._timer_elapsed)
                phase = "TIMER"
            h, m = divmod(remaining, 3600)
            m, s = divmod(m, 60)
            pause = " ⏸" if self._timer_paused else ""
            if h:
                time_str = f"{h:02d}:{m:02d}:{s:02d}"
            else:
                time_str = f"{m:02d}:{s:02d}"
            data = {
                "text": f"⏱ {time_str}{pause}",
                "alt": phase,
                "class": f"timer-{phase.lower()}",
                "tooltip": f"Timer: {phase} ({time_str} remaining)",
            }
            with open("/tmp/taskwatch_timer.json", "w") as f:
                json.dump(data, f)
        except OSError:
            pass

    def _notify_timer_done(self, task_name: str) -> None:
        try:
            subprocess.run(
                ["notify-send", "-a", "TaskWatch+", "Timer Complete", task_name],
                capture_output=True,
            )
        except FileNotFoundError:
            pass

    def _write_timer_state(self, updates: dict) -> None:
        try:
            current = {}
            try:
                with open(self._timer_state_path) as f:
                    current = json.load(f)
            except (OSError, json.JSONDecodeError):
                pass
            current.update(updates)
            tmp = self._timer_state_path.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(current, f)
            tmp.rename(self._timer_state_path)
        except OSError:
            pass

    def _kill_daemon(self) -> None:
        try:
            with open(self._timer_state_path) as f:
                old = json.load(f)
            pid = old.get("pid")
            if pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                except (OSError, AttributeError):
                    pass
        except (OSError, json.JSONDecodeError):
            pass

    def _spawn_daemon(self) -> None:
        try:
            subprocess.Popen(
                [sys.executable, str(self._daemon_path)],
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, ValueError):
            pass

    def _reconnect_timer(self) -> None:
        try:
            with open(self._timer_state_path) as f:
                state = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        if not state.get("running"):
            return
        pid = state.get("pid")
        if pid:
            try:
                os.kill(pid, 0)
            except OSError:
                return
        self._timer_running = True
        self._timer_paused = state.get("paused", False)
        self._timer_seconds = state.get("total_seconds", 0)
        if state.get("mode") == "scheduled":
            self._timer_task_id = state.get("task_id")
            self._timer_schedule = state.get("schedule")
            self._timer_segment_idx = state.get("segment_idx", 0)
            self._timer_segment_elapsed = state.get("segment_elapsed", 0)
        else:
            self._timer_task_id = None
            self._timer_schedule = None
            self._timer_segment_idx = 0
            self._timer_segment_elapsed = 0

    def _start_timer(self, minutes: int) -> None:
        total = minutes * 60
        self._kill_daemon()
        self._write_timer_state({
            "running": True,
            "mode": "simple",
            "minutes": minutes,
            "total_seconds": total,
            "remaining": total,
            "paused": False,
            "stopped": False,
            "start_time": time.time(),
            "pause_elapsed": 0.0,
        })
        self._spawn_daemon()
        self._timer_running = True
        self._timer_seconds = total
        self._timer_elapsed = 0
        self._timer_paused = False
        self._timer_task_id = None
        self._timer_schedule = None
        self._timer_segment_idx = 0
        self._timer_segment_elapsed = 0
        self._update_clock_display()

    def _stop_timer(self) -> None:
        self._write_timer_state({"stopped": True})
        self._timer_running = False
        self._timer_seconds = 0
        self._timer_paused = False
        self._timer_task_id = None
        self._timer_schedule = None
        self._timer_segment_idx = 0
        self._timer_segment_elapsed = 0
        self._update_clock_display()
        self._write_timer_file()

    def _update_clock_display(self) -> None:
        now = datetime.now()
        if self._timer_running:
            if self._timer_schedule:
                segments = self._timer_schedule["segments"]
                seg_dur = segments[self._timer_segment_idx]
                remaining = max(0, seg_dur - self._timer_segment_elapsed)
                if self._timer_segment_idx == 0:
                    phase = "INTRO"
                    attr = "default"
                elif self._timer_segment_idx % 2 == 1:
                    phase = "WORK"
                    attr = "head"
                else:
                    phase = "BREAK"
                    attr = "dim"
            else:
                remaining = max(0, self._timer_seconds - self._timer_elapsed)
                phase = ""
                attr = "dim"
            h, m = divmod(remaining, 3600)
            m, s = divmod(m, 60)
            pause_ind = " \u23f8" if self._timer_paused else ""
            phase_ind = f"\u25b6 {phase}  " if phase else ""
            if h:
                self._clock_text.set_text(f"{phase_ind}\u23f1 {h:02d}:{m:02d}:{s:02d}{pause_ind}")
            else:
                self._clock_text.set_text(f"{phase_ind}\u23f1 {m:02d}:{s:02d}{pause_ind}")
            self._clock_w.set_attr_map({None: attr})
        else:
            self._clock_text.set_text(now.strftime("%H:%M:%S"))
            self._clock_w.set_attr_map({None: "dim"})
        self._write_timer_file()

    def _tick(self, loop: object, data: object) -> None:
        try:
            timer_completed = False
            if self._timer_running:
                try:
                    with open(self._timer_state_path) as f:
                        state = json.load(f)
                except (OSError, json.JSONDecodeError):
                    state = {}
                if not state.get("running"):
                    self._stop_timer()
                    timer_completed = True
                else:
                    self._timer_paused = state.get("paused", False)
                    self._timer_segment_idx = state.get("segment_idx", 0)
                    self._timer_segment_elapsed = state.get("segment_elapsed", 0)
                    self._timer_elapsed = self._timer_seconds - state.get("remaining", 0)

            if timer_completed:
                self._refresh_list()
                self._show_detail()

            self._tick_counter += 1
            if self._tick_counter % 60 == 0:
                task_cmds.reset_overdue_repeatables()
            if self._tick_counter % 300 == 0:
                self._check_and_notify_deadlines()

            self._update_clock_display()
        finally:
            self._loop.set_alarm_in(1, self._tick)

    def _unhandled_input(self, key: str) -> None:
        if self._loop.widget is not self._frame:
            if key in ("esc", "q"):
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


    def run(self) -> None:
        config_path = Path(__file__).resolve().parent.parent / "config" / "config.txt"
        try:
            with open(config_path) as f:
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
