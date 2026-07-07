from __future__ import annotations

import json
from pathlib import Path

import urwid
from urwid import (
    AttrMap,
    Columns,
    Edit,
    Filler,
    LineBox,
    ListBox,
    Overlay,
    Pile,
    SimpleFocusListWalker,
    Text,
    WidgetWrap,
)

from . import (
    archive_cmds,
    directory_cmds,
    tag_cmds,
    task_cmds,
)
from .tui_helpers import _fuzzy_score, _build_highlighted_text, _render_markdown_to_urwid
from .tui_widgets import SelectableText

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


class ImportJSONOverlay(WidgetWrap):
    def __init__(self, app: "TaskWatchTUI", *, import_fn=None, target_id=None, title="Import JSON"):
        self._app = app
        self._importing = False
        self._import_fn = import_fn
        self._target_id = target_id
        self._edit = Edit("")
        self._edit.set_caption(("standout", "  "))
        self._result = Text("")
        clipboard = _paste_from_clipboard()
        if clipboard:
            self._edit.set_edit_text(clipboard)
            header = Text([("head", "  JSON loaded from clipboard — press "), ("special", "Ctrl+E"), ("head", " to import  |  "), ("special", "Esc"), ("head", " to cancel")])
            sample = clipboard.strip()[:80].replace("\n", " ")
            self._result.set_text([("default", f"  Loaded {len(clipboard)} chars: {sample}…")])
        else:
            header = Text([("head", "  Paste JSON below, then press "), ("special", "Ctrl+E"), ("head", " to import  |  "), ("special", "Esc"), ("head", " to cancel")])
        pile = Pile([
            ("pack", AttrMap(header, "default")),
            ("weight", 1, LineBox(Filler(self._edit, valign="top"))),
            ("pack", self._result),
        ])
        super().__init__(LineBox(pile, title=title))

    def keypress(self, size: tuple[int, int], key: str) -> str | None:
        if self._importing:
            return None
        if key == "esc":
            self._app._close_import_json_panel()
            return None
        if key == "ctrl e":
            self._do_import()
            return None
        return super().keypress(size, key)

    def _do_import(self) -> None:
        text = self._edit.get_edit_text().strip()
        if not text:
            self._result.set_text([("error", "  No JSON entered")])
            return

        target = self._target_id
        if target is None:
            self._result.set_text([("error", "  No target selected")])
            return

        import_fn = self._import_fn
        if import_fn is None:
            import_fn = lambda t, tid: io_cmds.import_tasks_from_directory_json(t, tid)

        self._importing = True
        self._result.set_text([("default", "  Importing...")])
        self._app._run_async(
            lambda: import_fn(text, target),
            lambda r: self._on_import_done(r),
            "Importing...",
        )

    def _on_import_done(self, result: object) -> None:
        self._importing = False
        if isinstance(result, tuple) and len(result) == 2:
            success, msg = result
            if success:
                self._app._set_timed_caption("done", f"{msg} ", 3)
                self._app._close_import_json_panel()
                self._app._refresh_list()
            else:
                self._result.set_text([("error", f"  {msg}")])
        else:
            self._result.set_text([("error", f"  Import failed: {result}")])


class GlobalSearchOverlay(WidgetWrap):
    def __init__(self, app: "TaskWatchTUI"):
        self._app = app
        self._edit = Edit("🔍 ")
        self._walker = SimpleFocusListWalker([])
        self._listbox = VimListBox(self._walker)
        urwid.connect_signal(self._edit, 'change', self._on_change)
        pile = Pile([
            ("pack", AttrMap(self._edit, "head")),
            ("weight", 1, self._listbox),
        ])
        super().__init__(LineBox(pile, title="Global Search"))

    def _on_change(self, edit: Edit, text: str) -> None:
        self._run_search(text)

    def _run_search(self, query: str) -> None:
        self._walker.clear()
        q = query.strip()
        if not q:
            return
        results: list[tuple[int, urwid.Widget]] = []
        tasks = task_cmds.search_tasks_global(q)
        if tasks:
            results.append((9999, Text([("head", "  Tasks")])))
            for t, dir_name, _ in tasks:
                score = _fuzzy_score(q, t.name)[0] + 80
                label = f"[{dir_name}] {t.name}" if dir_name else t.name
                highlighted = _build_highlighted_text(label, q)
                w = AttrMap(SelectableText(highlighted), "default", "focus")
                w.result_data = ("task", t.id, dir_name)
                results.append((score, w))
        dirs = directory_cmds.search_directories_global(q)
        if dirs:
            results.append((9999, Text([("head", "  Directories")])))
            for d in dirs:
                score = _fuzzy_score(q, d.name)[0]
                highlighted = _build_highlighted_text(str(d.name), q)
                w = AttrMap(SelectableText(highlighted), "default", "focus")
                w.result_data = ("directory", d.id, d.name)
                results.append((score, w))
        tags = tag_cmds.search_tags_global(q)
        if tags:
            results.append((9999, Text([("head", "  Tags")])))
            for t in tags:
                score = _fuzzy_score(q, t.name)[0]
                tag_text = f"#{t.name}"
                highlighted = _build_highlighted_text(tag_text, q)
                w = AttrMap(SelectableText(highlighted), "default", "focus")
                w.result_data = ("tag", t.id, t.name)
                results.append((score, w))
        results.sort(key=lambda x: (0, -x[0]))
        for _, w in results:
            self._walker.append(w)
        if self._walker:
            self._listbox.focus_position = 0

    def keypress(self, size: tuple[int, int], key: str) -> str | None:
        if key == "?":
            self._app._show_help()
            return None
        if key == "esc":
            self._app._close_global_search()
            return None
        if key == "enter":
            idx = self._listbox.focus_position
            if idx < len(self._walker):
                w = self._walker[idx]
                if hasattr(w, 'result_data'):
                    self._app._navigate_from_search(w.result_data)
            return None
        return super().keypress(size, key)


class FilePickerWidget(WidgetWrap):
    def __init__(
        self,
        start_dir: str,
        on_select: Callable[[str], None],
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        self._start_dir = start_dir
        self._on_select = on_select
        self._on_cancel = on_cancel or (lambda: None)
        self._current_dir = os.path.abspath(start_dir)
        self._walker = SimpleFocusListWalker([])
        self._listbox = ListBox(self._walker)
        self._header_text = Text("")
        self._pile = Pile([
            ("pack", AttrMap(self._header_text, "head")),
            ("weight", 1, LineBox(self._listbox)),
        ])
        super().__init__(self._pile)
        self._refresh()

    def _refresh(self) -> None:
        self._walker.clear()
        self._header_text.set_text(f"  {self._current_dir}")
        entries: list[tuple[str, str]] = []
        if self._current_dir != "/":
            entries.append(("..", "dir"))
        try:
            names = sorted(os.listdir(self._current_dir))
        except PermissionError:
            names = []
        for name in names:
            full = os.path.join(self._current_dir, name)
            kind = "dir" if os.path.isdir(full) else "file"
            entries.append((name, kind))
        for name, kind in entries:
            label = f"\U0001f4c1 {name}" if kind == "dir" else f"\U0001f4c4 {name}"
            w = AttrMap(SelectableText(label), "default", "focus")
            self._walker.append(w)

    def keypress(self, size: tuple[int, int], key: str) -> str | None:
        if key in ("esc", "q"):
            self._on_cancel()
            return None
        if key in ("enter", " "):
            idx = self._listbox.focus_position
            if idx < len(self._walker):
                label = self._walker[idx].original_widget.text
                name = label.split(" ", 1)[1] if " " in label else label
                full = os.path.join(self._current_dir, name)
                if os.path.isdir(full):
                    self._current_dir = full
                    self._refresh()
                else:
                    self._on_select(full)
            return None
        if key in ("h", "backspace"):
            parent = os.path.dirname(self._current_dir)
            if os.path.isdir(parent):
                self._current_dir = parent
                self._refresh()
            return None
        return super().keypress(size, key)

