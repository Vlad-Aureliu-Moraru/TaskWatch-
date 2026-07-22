from __future__ import annotations
import json
import logging
import math as _math
import os
import queue
import random
import re
import shutil
import struct as _struct
import subprocess
import sys
import tempfile
import threading
import time
import wave as _wave
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
    Filler,
    Frame,
    LineBox,
    ListBox,
    Overlay,
    Pile,
    SimpleFocusListWalker,
    Text,
    WidgetWrap,
)

from . import (
    __version__,
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
from .paths import DATA_DIR, TIMER_STATE_PATH


logger = logging.getLogger("taskwatch.tui")

class Level(Enum):
    ARCHIVES = auto()
    DIRECTORIES = auto()
    TASKS = auto()
    NOTES = auto()

PALETTE = [
    ("default", "default", "default"),
    ("focus", "default", "dark blue", "standout"),
    ("head", "default", "default", "bold"),
    ("head_focus", "default", "dark blue", "bold"),
    ("dim", "dark gray", "default"),
    ("done", "dark green", "default"),
    ("warn", "brown", "default"),
    ("bar_f", "default", "default"),
    ("bar_e", "dark gray", "default"),
    ("error", "dark red", "default"),
    ("help", "default", "default"),
    ("pane_dim", "dark gray", "default"),
    ("c1", "dark green", "default"),
    ("c2", "light green", "default"),
    ("c3", "yellow", "default"),
    ("c4", "light red", "default"),
    ("c5", "dark red", "default"),
    ("c6", "light blue", "default"),
    ("hm1", "light green", "default"),
    ("hm2", "dark green", "default"),
    ("hm3", "dark green, bold", "default"),
    ("c1_focus", "dark green", "dark blue", "standout"),
    ("c2_focus", "light green", "dark blue", "standout"),
    ("c3_focus", "yellow", "dark blue", "standout"),
    ("c4_focus", "light red", "dark blue", "standout"),
    ("c5_focus", "dark red", "dark blue", "standout"),
    ("c6_focus", "light blue", "dark blue", "standout"),
    ("white_fg", "white", "default"),
    ("white_fg_focus", "white", "dark blue", "standout"),
    ("light_magenta", "light magenta", "default"),
    ("light_magenta_focus", "light magenta", "dark blue", "standout"),
    ("light_cyan", "light cyan", "default"),
    ("light_cyan_focus", "light cyan", "dark blue", "standout"),
    ("bright_yellow", "yellow, bold", "default"),
    ("bright_yellow_focus", "yellow, bold", "dark blue", "standout"),
    ("black", "black", "default"),
    ("black_focus", "black", "dark blue", "standout"),
    ("done_dir", "dark blue", "default"),
    ("search_highlight", "black, bold", "yellow"),
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
    "a", "add", "at", "r", "remove", "d", "e", "edit", "f", "finish",
    "c", "cancel", "shf", "showFinished", "hf", "hideFinished",
    "h", "help", "q", "exit", "stats", "ftc", "undo", "week",
    "st", "ts", "timerStop", "pt", "pauseTimer", "rt", "resetTimer",
    "su a", "su d", "sd a", "sd d", "sn a", "sn d", "sdl a", "sdl d", "sr",
    "tag ", "untag ", "ft ", "gs ", "qa ", "mv ", "mu", "md", "all",
    "export", "exportCurrent", "exportCurrent ", "import ", "importExported ", "importExportedMerge ",
    "importJSON ", "importJSONtaskTemplateCopy", "importJSONnoteTemplateCopy", "overdue", "schbar", "ai", "aii", "highlight",
    "bm", "bd", "bt ", "bv ", "bc", "y", "sound", "sound on", "sound off",
    "sound work ", "sound break ", "sound done ",
    "pin", "unpin", "depends ", "undepends ",
    "subadd ", "subrm ", "subdone ", "subedit ",
    "snooze ", "dup", "standup", "select ", "focus", "attachProject ",
    "preset ", "preset list", "preset add", "preset remove",
    "update", "recalculateLevel",
    "serve", "serve stop", "serve status",
]

CELEBRATION_MESSAGES = [
    "Crushed it!",
    "Another one down!",
    "Nice work!",
    "Boom! \u2705",
    "Task slain!",
    "Nailed it!",
    "Productivity unlocked!",
    "Progress!",
    "You're on fire!",
    "Done and dusted!",
]


HELP_ENTRIES: list[tuple[str, str, str]] = [
    ("Navigation", "↑/↓", "Move selection / scroll detail"),
    ("Navigation", "Enter / l", "Select / drill in"),
    ("Navigation", "` / h", "Go back one level"),
    ("Navigation", "Tab", "Switch between list and detail pane"),
    ("Navigation", ":", "Focus command bar"),
    ("Commands", ":a | :add", "Add item at current level"),
    ("Commands", ":at", "Add note with file attachment (Notes level only)"),
    ("Commands", ":r | :remove", "Delete selected item (with confirmation)"),
    ("Commands", ":e | :edit", "Edit selected item"),
    ("Commands", ":c | :cancel", "Cancel command / wizard"),
    ("Commands", ":f | :finish", "Toggle task completion"),
    ("Commands", ":shf | :showFinished", "Toggle showing finished tasks"),
    ("Commands", ":hf | :hideFinished", "Hide finished tasks"),
    ("Commands", ":h | :help", "This help"),
    ("Commands", ":q | :exit", "Quit"),
    ("Commands", "Tab", "Cycle command completions"),
    ("Commands", "↑/↓", "Recall command history (in command bar)"),
    ("Commands", "Space", "Toggle bulk selection (task list)"),
    ("Commands", "Left click", "Select / navigate into item"),
    ("Commands", "Right click", "Go back"),
    ("Search", "/", "Search items in list (type text, Enter to apply, Esc to clear)"),
    ("Search", "//", "Global fuzzy search (tasks, directories, tags)"),
    ("Search", ":gs <text>", "Global search tasks across all archives"),
    ("Search", ":all", "Show all tasks in current archive"),
    ("Move", ":mv <target-id>", "Move task to directory / dir to archive"),
    ("Move", ":mu | :md", "Move task up / down (manual order)"),
    ("Quick add", ":qa <name>", "Quick-add task (defaults u:1 d:1; add u:3 d:2 t:30 for custom values)"),
    ("Tags", ":tag <name>", "Add tag to selected task"),
    ("Tags", ":untag <name>", "Remove tag from selected task"),
    ("Tags", ":ft <name>", "Filter tasks by tag name"),
    ("Tags", ":ftc", "Clear tag filter"),
    ("Bulk", ":bm", "Mark selected tasks done"),
    ("Bulk", ":bd", "Delete selected tasks"),
    ("Bulk", ":bt <name>", "Tag selected tasks"),
    ("Bulk", ":bv <dir-id>", "Move selected tasks to directory"),
    ("Bulk", ":bc", "Clear selection"),
    ("Stats & View", ":stats", "Show task statistics"),
    ("Stats & View", ":week", "Show tasks grouped by deadline this week"),
    ("Stats & View", ":overdue", "Show overdue tasks"),
    ("Stats & View", ":ai", "Open opencode with task context"),
    ("Stats & View", ":aii", "Open integrated AI chat"),
    ("Stats & View", ":aii connect <p> <k>", "Connect an AI provider (groq/gemini/mistral)"),
    ("Stats & View", ":aii disconnect <p>", "Remove an AI provider"),
    ("Stats & View", ":aii providers", "List configured providers"),
    ("Stats & View", ":highlight", "Choose highlight beam color"),
    ("Stats & View", ":recalculateLevel", "Recalculate XP and level for all directories"),
    ("Undo", ":undo", "Undo last delete / edit / finish"),
    ("Export/Import", ":export [path]", "Export all data as JSON"),
    ("Export/Import", ":import <path>", "Import all data from JSON"),
    ("Export/Import", ":exportCurrent [path]", "Export current item (archive/dir/task/note) as JSON (auto-writes to project path if :attachProject was used)"),
    ("Export/Import", ":importExported [path]", "Import file created by :exportCurrent (navigate to target first; auto-detects path if project is attached)"),
    ("Export/Import", ":importExportedMerge [path]", "Like :importExported, but merges into existing directory by name (marks done, adds notes/tags)"),
    ("Export/Import", ":importJSON <path>", "Import tasks from JSON file into current directory"),
    ("Export/Import", ":importJSONtaskTemplateCopy", "Import task from clipboard as JSON template"),
    ("Export/Import", ":importJSONnoteTemplateCopy", "Import notes from clipboard as JSON template"),
    ("Export/Import", ":update", "Check for updates and update TaskWatch+"),
    ("Export/Import", ":y", "Copy selected item to clipboard as JSON (task: details+notes, dir: all tasks, archive: all dirs+tasks; bulk-select with Space to copy all)"),
    ("Timer", ":st <minutes|preset>", "Start countdown timer (or preset name like 'pomodoro')"),
    ("Timer", ":ts | :timerStop", "Stop timer"),
    ("Timer", ":pt | :pauseTimer", "Pause / unpause timer"),
    ("Timer", ":rt | :resetTimer", "Reset timer"),
    ("Timer", ":schbar", "Show timer schedule bar"),
    ("Timer", ":attachProject <path>", "Attach current directory to a project path (writes .taskwatch-directory + enables auto path for :exportCurrent/:importExported)"),
    ("Timer", ":focus", "Toggle focus mode (big timer, hides list); p: pause, s: stop, :focus: exit"),
    ("Timer", ":preset list", "List timer presets"),
    ("Timer", ":preset add <n> <p> <w> <b> <l>", "Add preset (times: 30m, 15s, 1h, 1h30m)"),
    ("Timer", ":preset remove <n>", "Remove preset"),
    ("Timer", ":snooze <days>", "Postpone selected task's deadline by N days"),
    ("Timer", ":dup", "Duplicate selected task"),
    ("Pinning", ":pin", "Pin selected task to top"),
    ("Pinning", ":unpin", "Unpin selected task"),
    ("Pinning", ":depends <id>", "Selected task depends on task <id>"),
    ("Pinning", ":undepends <id>", "Remove dependency on task <id>"),
    ("Subtasks", ":subadd <content>", "Add subtask to selected task"),
    ("Subtasks", ":subrm <#>", "Delete the Nth subtask"),
    ("Subtasks", ":subdone <#>", "Toggle the Nth subtask"),
    ("Subtasks", ":subedit <#> <text>", "Edit the Nth subtask's content"),
    ("Bulk Smart Select", ":select overdue", "Select all overdue tasks"),
    ("Bulk Smart Select", ":select due today", "Select all tasks due today"),
    ("Bulk Smart Select", ":select pinned", "Select all pinned tasks"),
    ("Standup", ":standup", "Show yesterday's completed tasks as markdown"),
    ("Sound", ":sound", "Toggle timer sounds on/off"),
    ("Sound", ":sound on | :sound off", "Explicit enable / disable"),
    ("Sound", ":sound work <path>", "Set custom work-end sound file"),
    ("Sound", ":sound break <path>", "Set custom break-end sound file"),
    ("Sound", ":sound done <path>", "Set custom timer-done sound file"),
    ("Sort", ":su a | :su d", "Sort by urgency asc / desc"),
    ("Sort", ":sd a | :sd d", "Sort by difficulty asc / desc"),
    ("Sort", ":sn a | :sn d", "Sort by name asc / desc"),
    ("Sort", ":sdl a | :sdl d", "Sort by deadline asc / desc"),
    ("Sort", ":sr", "Reset to default order"),
]

def _copy_to_clipboard(text: str) -> bool:
    data = text.encode()
    for cmd in (
        ["wl-copy", "--foreground"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
        ["pbcopy"],
    ):
        try:
            proc = subprocess.run(
                cmd, input=data, capture_output=True, timeout=5,
            )
            if proc.returncode == 0:
                return True
        except (OSError, subprocess.TimeoutExpired):
            continue
    # Fallback: try wl-copy without --foreground via Popen
    try:
        proc = subprocess.Popen(
            ["wl-copy"], stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        proc.stdin.write(data)
        proc.stdin.close()
        proc.wait(timeout=3)
        if proc.returncode == 0:
            return True
    except (OSError, subprocess.TimeoutExpired):
        pass
    return False


def _paste_from_clipboard() -> str | None:
    for cmd in (
        ["wl-paste"],
        ["xclip", "-selection", "clipboard", "-o"],
        ["xsel", "--clipboard", "--output"],
        ["pbpaste"],
    ):
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=5)
            if proc.returncode == 0:
                return proc.stdout.decode()
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


import math as _math  # noqa: E402
import struct as _struct  # noqa: E402
import wave as _wave  # noqa: E402

_SOUND_DIR = DATA_DIR / "sounds"

def _generate_wav(path: Path, freq: float, duration: float, decay: float = 8) -> None:
    sample_rate = 44100
    num_samples = int(sample_rate * duration)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        for i in range(num_samples):
            t = i / sample_rate
            amp = int(32767 * 0.5 ** (t * decay) * _math.sin(2 * _math.pi * freq * t))
            w.writeframes(_struct.pack("<h", amp))

def _ensure_default_sounds() -> dict[str, Path]:
    _SOUND_DIR.mkdir(parents=True, exist_ok=True)
    default_sounds = {
        "work":  _SOUND_DIR / "work_end.wav",
        "break": _SOUND_DIR / "break_end.wav",
        "done":  _SOUND_DIR / "timer_done.wav",
    }
    if not default_sounds["work"].is_file():
        _generate_wav(default_sounds["work"], 880, 0.3, 8)
    if not default_sounds["break"].is_file():
        _generate_wav(default_sounds["break"], 440, 0.2, 6)
    if not default_sounds["done"].is_file():
        _generate_multi_tone_wav(default_sounds["done"], [(880, 0.15), (1320, 0.15)], 10)
    return default_sounds

def _generate_multi_tone_wav(path: Path, tones: list[tuple[float, float]], decay: float = 10) -> None:
    sample_rate = 44100
    path.parent.mkdir(parents=True, exist_ok=True)
    with _wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        for freq, dur in tones:
            for i in range(int(sample_rate * dur)):
                t = i / sample_rate
                amp = int(32767 * 0.5 ** (t * decay) * _math.sin(2 * _math.pi * freq * t))
                w.writeframes(_struct.pack("<h", amp))

def _play_sound(path: Path) -> None:
    if not path or not path.is_file():
        return
    for cmd in (
        ["paplay", str(path)],
        ["aplay", str(path)],
        ["ffplay", "-nodisp", "-autoexit", str(path)],
    ):
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=5)
            if proc.returncode == 0:
                return
        except (OSError, subprocess.TimeoutExpired):
            continue
    sys.stderr.write("\a")


def _bar(val: int, outof: int) -> list:
    result = [("bar_f", "\u25a0" * val)]
    if val < outof:
        result.append(("bar_e", "\u25a1" * (outof - val)))
    return result


LEVEL_XP_THRESHOLDS = [0, 50, 150, 350, 700, 1200, 2000, 3500, 5500, 8000]

LEVEL_PATTERNS = [
    ("\u00b7", [("dim", 0)]),  # 1  ·
    ("\u2219", [("c1", 2), ("c2", "rem"), ("c1", 2)]),  # 2  ∙
    ("\u2022", [("c6", 2), ("c2", "rem"), ("c6", 2)]),  # 3  •
    ("\u25cb", [("c6", 0), ("white_fg", 0), ("c2", 0)]),  # 4  ○
    ("\u25ce", [("c3", 2), ("bright_yellow", 2)]),      # 5  ◎
    ("\u25c9", [("c4", 2), ("light_magenta", 2)]),      # 6  ◉
    ("\u25cf", [("c5", 3), ("c4", "rem"), ("white_fg", 3)]),  # 7  ●
    ("\u25c7", [("c6", 3), ("light_magenta", "rem"), ("light_cyan", 3)]),  # 8  ◇
    ("\u25c6", [("c5", 0), ("c3", 0), ("c2", 0), ("c6", 0)]),  # 9  ◆
    ("\u25c8", [("white_fg", 1), ("c3", "rem"), ("white_fg", 1)]),  # 10 ◈
]


def _apply_pattern(name: str, segments: list) -> list[tuple[str, str]]:
    n = len(name)
    if n == 0:
        return []

    alt_colors = [c for c, s in segments if s == "alt"]
    if alt_colors:
        return [(alt_colors[i % len(alt_colors)], ch) for i, ch in enumerate(name)]

    fixed_before = 0
    fixed_after = 0
    eq_count = 0
    has_rem = False
    has_eq = False
    for _, spec in segments:
        if isinstance(spec, int) and spec > 0:
            if has_rem:
                fixed_after += spec
            else:
                fixed_before += spec
        elif spec == "rem":
            has_rem = True
        elif isinstance(spec, int) and spec == 0:
            has_eq = True
            eq_count += 1

    # Repeating pattern (fixed segments only, no rem/eq/alt)
    if not has_rem and not has_eq:
        cycle: list[tuple[str, int]] = [(c, s) for c, s in segments if isinstance(s, int) and s > 0]
        if cycle:
            result: list[tuple[str, str]] = []
            idx = 0
            while idx < n:
                for color, spec in cycle:
                    take = min(spec, n - idx)
                    if take > 0:
                        result.append((color, name[idx:idx + take]))
                        idx += take
                    if idx >= n:
                        break
            return result

    total_fixed = fixed_before + fixed_after
    if total_fixed > n:
        return [(segments[0][0], name)]

    rem = max(0, n - fixed_before - fixed_after)
    eq_size = rem // eq_count if eq_count > 0 else 0

    result = []
    idx = 0
    eq_idx = 0
    for color, spec in segments:
        if isinstance(spec, int) and spec > 0:
            if has_rem and n - idx <= fixed_after:
                take = min(spec, fixed_after)
            else:
                take = min(spec, fixed_before)
            if take > 0:
                result.append((color, name[idx:idx + take]))
                idx += take
            if has_rem:
                fixed_after -= take
            else:
                fixed_before -= take
        elif spec == "rem":
            if rem > 0:
                result.append((color, name[idx:idx + rem]))
                idx += rem
        elif isinstance(spec, int) and spec == 0:
            take = eq_size if eq_idx < eq_count - 1 else n - idx
            if take > 0:
                result.append((color, name[idx:idx + take]))
                idx += take
            eq_idx += 1

    return result


def _level_theme(level: int) -> tuple[str, list]:
    idx = max(0, min(len(LEVEL_PATTERNS) - 1, level - 1))
    return LEVEL_PATTERNS[idx]


def _level_color(level: int) -> str:
    return f"c{max(1, min(5, level))}"


def _get_level_for_xp(xp: int) -> int:
    for i in range(len(LEVEL_XP_THRESHOLDS) - 1, -1, -1):
        if xp >= LEVEL_XP_THRESHOLDS[i]:
            return i + 1
    return 1


_BRAILLE_STEPS = "\u2800\u2840\u28c0\u28c4\u28e4\u28e6\u28f6\u28f7\u28ff"


def _gradient_attr(pct: int) -> str:
    if pct >= 80:
        return "c1"
    if pct >= 60:
        return "c2"
    if pct >= 40:
        return "c3"
    if pct >= 20:
        return "c4"
    return "c5"


def _progress_gradient_attr(pct: int) -> str:
    if pct >= 80:
        return "c6"
    if pct >= 60:
        return "c1"
    if pct >= 40:
        return "c3"
    if pct >= 20:
        return "c4"
    return "c5"


def _hblock_bar(pct: int, width: int, empty_char: str = "\u25a1", fill_attr: str | None = None) -> list:
    fa = fill_attr or _gradient_attr(pct)
    filled = round(width * pct / 100)
    empty = width - filled
    parts: list = [(fa, "\u25a0" * filled)]
    if empty > 0:
        parts.append(("bar_e", empty_char * empty))
    return parts


def _braille_bar(pct: int, width: int, fill_attr: str) -> list:
    total = width * 8
    filled = int(total * pct / 100)
    full = filled // 8
    rem = filled % 8
    empty = width - full - (1 if rem else 0)
    parts: list = [(fill_attr, _BRAILLE_STEPS[8] * full)]
    if rem:
        parts.append((fill_attr, _BRAILLE_STEPS[rem]))
    if empty > 0:
        parts.append((fill_attr, _BRAILLE_STEPS[0] * empty))
    return parts


def _heatmap_attr(count: int, max_count: int) -> str:
    if count == 0:
        return "dim"
    pct = int(count / max_count * 100) if max_count else 0
    if pct >= 67:
        return "hm3"
    if pct >= 33:
        return "hm2"
    return "hm1"


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
    hold_arg = _TERMINAL_HOLD.get(terminal)
    if hold_arg is None:
        return [terminal, "-e", "sh", "-c", cmd_str]
    if hold_arg == "":
        return [terminal, "--", "sh", "-c", cmd_str]
    return [terminal, hold_arg, "sh", "-c", cmd_str]


_TERMINAL_HOLD: dict[str, str] = {
    "kitty":             "--hold",
    "alacritty":         "--hold",
    "wezterm":           "--hold",
    "konsole":           "--hold",
    "xfce4-terminal":    "--hold",
    "foot":              "--hold",
    "gnome-terminal":    "",
    "xterm":             "-hold",
}


def _find_opencode() -> str | None:
    path = shutil.which("opencode")
    if path:
        return path
    home = Path.home()
    for candidate in [
        home / ".opencode" / "bin" / "opencode",
        home / ".local" / "bin" / "opencode",
        home / ".npm-global" / "bin" / "opencode",
        Path("/usr/local/bin/opencode"),
    ]:
        if candidate.is_file() and os.access(str(candidate), os.X_OK):
            return str(candidate)
    return None

def _fuzzy_score(query: str, text: str) -> tuple[int, list[tuple[int, int]]]:
    if not query or not text:
        return (0, [])
    ql = query.lower()
    tl = text.lower()
    idx = tl.find(ql)
    if idx != -1:
        return (100 + len(ql), [(idx, idx + len(ql))])
    positions = []
    i = 0
    for ch in ql:
        j = tl.find(ch, i)
        if j == -1:
            break
        positions.append(j)
        i = j + 1
    else:
        spread = positions[-1] - positions[0]
        score = 50 + max(0, 30 - spread)
        return (score, [(p, p + 1) for p in positions])
    return (0, [])


def _build_highlighted_text(text: str, query: str) -> list:
    _, spans = _fuzzy_score(query, text)
    if not spans:
        return [("default", text)]
    result: list = []
    pos = 0
    start, end = spans[0]
    if len(spans) == 1 and end - start == len(query):
        # contiguous match
        result.append(("default", text[:start]))
        result.append(("search_highlight", text[start:end]))
        result.append(("default", text[end:]))
    else:
        # non-contiguous: highlight each char position individually
        span_set = set()
        for s, e in spans:
            for p in range(s, e):
                span_set.add(p)
        for i, ch in enumerate(text):
            if i in span_set:
                result.append(("search_highlight", ch))
            else:
                result.append(("default", ch))
    return result


def _parse_inline_markdown(text: str, base_style: str = "default") -> list:
    pattern = r'\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`|\[(.+?)\]\((.+?)\)'
    parts: list = []
    last_end = 0

    for m in re.finditer(pattern, text):
        start, end = m.start(), m.end()

        if start > last_end:
            parts.append((base_style, text[last_end:start]))

        if m.group(1) is not None:
            parts.append(("head", m.group(1)))
        elif m.group(2) is not None:
            parts.append((base_style, m.group(2)))
        elif m.group(3) is not None:
            parts.append(("special", m.group(3)))
        elif m.group(4) is not None:
            parts.append((base_style, f"{m.group(4)} ({m.group(5)})"))

        last_end = end

    if last_end < len(text):
        parts.append((base_style, text[last_end:]))

    return parts if parts else [(base_style, text)]


def _render_table(table_lines: list[str]) -> list:
    if not table_lines:
        return []

    rows: list[list[str]] = []
    sep_index = -1

    for line in table_lines:
        s = line.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        cells = [c.strip() for c in s.split("|")]
        rows.append(cells)

    for i, cells in enumerate(rows):
        if all(re.match(r'^:?-{1,}:?$', c) for c in cells):
            sep_index = i
            break

    alignments: list[str] = []
    if sep_index >= 0:
        for c in rows[sep_index]:
            if c.startswith(":") and c.endswith(":"):
                alignments.append("center")
            elif c.endswith(":"):
                alignments.append("right")
            else:
                alignments.append("left")

    ncols = max(len(cells) for cells in rows)
    if not alignments:
        alignments = ["left"] * ncols
    while len(alignments) < ncols:
        alignments.append("left")

    col_widths = [0] * ncols
    for i, cells in enumerate(rows):
        if i == sep_index:
            continue
        for j in range(min(len(cells), ncols)):
            col_widths[j] = max(col_widths[j], len(cells[j]))

    def _pad(text: str, width: int, align: str) -> str:
        if align == "right":
            return text.rjust(width)
        if align == "center":
            left = (width - len(text)) // 2
            return " " * left + text + " " * (width - left - len(text))
        return text.ljust(width)

    result: list = []

    top = "\u250c" + "\u252c".join("\u2500" * (w + 2) for w in col_widths) + "\u2510"
    result.append([("dim", top)])

    if sep_index >= 0:
        header_rows = rows[:sep_index]
        body_rows = rows[sep_index + 1:]
    else:
        header_rows = []
        body_rows = rows

    for cells in header_rows:
        padded = [_pad(cells[j] if j < len(cells) else "", col_widths[j], alignments[j]) for j in range(ncols)]
        row_str = "\u2502" + "\u2502".join(f" {c} " for c in padded) + "\u2502"
        result.append([("default", row_str)])

    if sep_index >= 0:
        div = "\u251c" + "\u253c".join("\u2500" * (w + 2) for w in col_widths) + "\u2524"
        result.append([("dim", div)])

    for cells in body_rows:
        padded = [_pad(cells[j] if j < len(cells) else "", col_widths[j], alignments[j]) for j in range(ncols)]
        row_str = "\u2502" + "\u2502".join(f" {c} " for c in padded) + "\u2502"
        result.append([("default", row_str)])

    bottom = "\u2514" + "\u2534".join("\u2500" * (w + 2) for w in col_widths) + "\u2518"
    result.append([("dim", bottom)])

    return result



def _render_markdown_to_urwid(text: str) -> list:
    lines: list = []
    in_code_block = False
    raw = text.split("\n")
    i = 0

    while i < len(raw):
        line = raw[i]
        stripped = line.strip()
        i += 1

        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue

        if in_code_block:
            lines.append([("special", line)])
            continue

        if not stripped:
            lines.append("")
            continue

        if re.match(r'^[-*_]{3,}\s*$', stripped):
            lines.append([("dim", "  " + "\u2500" * 40)])
            continue

        if stripped.startswith("|") and stripped.count("|") >= 2:
            table_lines = [line]
            while i < len(raw):
                nxt = raw[i].strip()
                if nxt.startswith("|") and nxt.count("|") >= 2:
                    table_lines.append(raw[i])
                    i += 1
                else:
                    break
            lines.extend(_render_table(table_lines))
            continue

        h_match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
        if h_match:
            lines.append(_parse_inline_markdown(h_match.group(2), "head"))
            continue

        if stripped.startswith("> "):
            lines.append(_parse_inline_markdown(stripped[2:], "dim"))
            continue

        ul_match = re.match(r'^(\s*)[-*+]\s+(.+)$', line)
        if ul_match:
            indent = ul_match.group(1)
            content = ul_match.group(2)
            bullet = "  " + indent + "\u2022 "
            lines.append(_parse_inline_markdown(bullet + content, "default"))
            continue

        ol_match = re.match(r'^(\s*)\d+\.\s+(.+)$', line)
        if ol_match:
            indent = ol_match.group(1)
            content = ol_match.group(2)
            lines.append(_parse_inline_markdown("  " + indent + content, "default"))
            continue

        lines.append(_parse_inline_markdown(line, "default"))

    return lines
