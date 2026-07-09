from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path

import urwid
from urwid import (
    AttrMap,
    Columns,
    Edit,
    Frame,
    LineBox,
    ListBox,
    SimpleFocusListWalker,
    Text,
    WidgetWrap,
)

from . import __version__


class SelectableText(Text):
    def selectable(self) -> bool:
        return True

    def keypress(self, size: tuple[int, int], key: str) -> str | None:
        return key

def _make_list_row(
    left_text: str | list, right_text: str, right_width: int,
    attr: str, focus_attr: str,
) -> AttrMap:
    prefix = Text(("dim", "▸ "), wrap="clip")
    left = SelectableText(left_text, wrap="clip")
    cols: list = [("pack", prefix), ("weight", 1, left)]
    if right_width > 0:
        right = Text(right_text, align="right", wrap="clip")
        cols.append((right_width, right))
    attr_map = {None: attr}
    focus_map: dict = {
        None: focus_attr,
        "default": focus_attr,
        "dim": focus_attr,
        "done_dir": focus_attr,
        "c1": "c1_focus",
        "c2": "c2_focus",
        "c3": "c3_focus",
        "c4": "c4_focus",
        "c5": "c5_focus",
        "c6": "c6_focus",
    }
    return AttrMap(Columns(cols), attr_map, focus_map)

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
            if key == "/" and not self.get_edit_text():
                self._app._exit_search_mode()
                self._app._open_global_search()
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
            if self._app._prompt_handler:
                self._app._handle_wizard_esc()
                return None
            self._app._focus_body()
            return None
        if key == "backspace" and self._app._prompt_handler and not self.get_edit_text():
            return None
        if self._app._tab_matches:
            self._app._tab_matches = []
            self._app._tab_index = -1
        return super().keypress(size, key)

class VimListBox(ListBox):
    def keypress(self, size: tuple[int, int], key: str) -> str | None:
        if key == "j":
            key = "down"
        elif key == "k":
            key = "up"
        return super().keypress(size, key)

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
        app._title_text = Text(f"TaskWatch+ v{__version__}")
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