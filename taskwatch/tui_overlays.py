from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
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
    io_cmds,
    tag_cmds,
    task_cmds,
)
from .tui_helpers import (
    HELP_ENTRIES,
    _build_highlighted_text,
    _fuzzy_score,
    _paste_from_clipboard,
    _render_markdown_to_urwid,
)
from .tui_widgets import SelectableText, VimListBox


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


class HelpSearchOverlay(WidgetWrap):
    def __init__(self, app: "TaskWatchTUI"):
        self._app = app
        self._edit = Edit("")
        self._edit.set_caption(("head", "  Search help  "))
        self._walker = SimpleFocusListWalker([])
        self._listbox = VimListBox(self._walker)
        urwid.connect_signal(self._edit, 'change', self._on_change)
        pile = Pile([
            ("pack", AttrMap(self._edit, "head")),
            ("weight", 1, self._listbox),
        ])
        self._expanded: set[str] = set(c for c, _, _ in HELP_ENTRIES)
        self._current_query = ""
        super().__init__(LineBox(pile, title="Help"))
        self._run_search("")

    def _on_change(self, edit: Edit, text: str) -> None:
        self._current_query = text
        self._run_search(text)

    def _rebuild_help(self) -> None:
        self._run_search(self._current_query)

    def _run_search(self, query: str) -> None:
        self._walker.clear()
        q = query.strip().lower()
        results_by_cat: dict[str, list[tuple[int, urwid.Widget]]] = {}
        for cat, cmd, desc in HELP_ENTRIES:
            combined = f"{cmd} {desc}".lower()
            if q and q not in combined and _fuzzy_score(q, combined)[0] == 0:
                continue
            score = _fuzzy_score(q, cmd + " " + desc)[0] if q else 100
            label = f"  {cmd:<30} {desc}"
            if q:
                highlighted = _build_highlighted_text(label, q)
            else:
                highlighted = [("default", label)]
            w = AttrMap(SelectableText(highlighted), "default", "focus")
            results_by_cat.setdefault(cat, []).append((score, w))
        is_search = bool(q)
        for cat, items in sorted(results_by_cat.items()):
            if not items:
                continue
            expanded = is_search or cat in self._expanded
            toggle_char = "\u25bc" if expanded else "\u25b6"
            header_text = f"  {toggle_char} {cat}"
            hw = AttrMap(SelectableText([("head", header_text)]), "default", "focus")
            hw.is_header = True
            hw.category_name = cat
            self._walker.append(hw)
            if expanded:
                sorted_items = sorted(items, key=lambda x: (-x[0], x[1].original_widget.get_text()[0] if hasattr(x[1].original_widget, 'get_text') else ""))
                for i, (score, w) in enumerate(sorted_items):
                    prefix = "\u2514" if i == len(sorted_items) - 1 else "\u251c"
                    orig_text = w.original_widget.get_text()[0] if hasattr(w.original_widget, 'get_text') else ""
                    new_label = f"\u2502 {prefix}\u2500\u2500{orig_text[3:]}" if orig_text.startswith("  ") else f"\u2502 {prefix}\u2500\u2500{orig_text}"
                    if q:
                        new_highlighted = _build_highlighted_text(new_label, q)
                    else:
                        new_highlighted = [("default", new_label)]
                    tree_w = AttrMap(SelectableText(new_highlighted), "default", "focus")
                    tree_w.is_header = False
                    self._walker.append(tree_w)
        if self._walker:
            self._listbox.focus_position = 0
        elif q:
            self._walker.append(Text([("error", "  No matching help entries")]))

    def keypress(self, size: tuple[int, int], key: str) -> str | None:
        if key == "?":
            return None
        if key in ("esc", "q"):
            self._app._close_help()
            return None
        if key == "enter" and self._walker:
            idx = self._listbox.focus_position
            if idx < len(self._walker):
                w = self._walker[idx]
                if hasattr(w, 'is_header') and w.is_header:
                    cat = w.category_name
                    if cat in self._expanded:
                        self._expanded.discard(cat)
                    else:
                        self._expanded.add(cat)
                    self._rebuild_help()
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

