from __future__ import annotations

import datetime
import queue
import threading

import urwid
from urwid import (
    AttrMap,
    Edit,
    ListBox,
    Pile,
    SimpleFocusListWalker,
    Text,
    WidgetWrap,
)

from . import ai_client


class _SelectableText(Text):
    def selectable(self) -> bool:
        return True

    def keypress(self, size: tuple[int, int], key: str) -> str | None:
        return key


_SYSTEM_PROMPT = """You are TaskWatch+ AI, an assistant integrated into a terminal task tracker. You have full read access to the user's task data (shown below). Your job is to help the user organize their tasks, recommend what to work on, explain task details, and create/modify items when asked.

Rules:
- Be concise and direct. No fluff.
- When the user asks you to create or modify data, use the >>>ACTION blocks shown below instead of just saying you'll do it.
- You may include multiple >>>ACTION blocks in a single response.
- If the user asks about data not visible in the context, ask them to navigate to it.
- Use only IDs that are explicitly provided in the context. Do not guess IDs.
- When recommending a task, explain your reasoning briefly.
- If the user asks something outside task management, you can still help as a general assistant."""  # noqa: E501


class AIChatWidget(WidgetWrap):
    def __init__(self, app) -> None:
        self._app = app
        self._messages: list[dict] = []
        self._pending_actions: list[dict] | None = None
        self._thinking_idx = -1
        self._ai_pending = False

        self._history_walker = SimpleFocusListWalker([])
        self._history_box = ListBox(self._history_walker)

        self._edit = Edit(("head", "> "))
        edit_w = AttrMap(self._edit, "default", "focus")
        input_box = urwid.LineBox(edit_w, title="Message")

        self._pile = Pile([
            ("weight", 1, urwid.LineBox(self._history_box, title="AI Chat")),
            ("pack", input_box),
        ])
        self._pile.focus_position = 1  # focus input by default

        super().__init__(self._pile)

        providers = ai_client.list_providers()
        if providers:
            names = ", ".join(p["name"] for p in providers)
            self._add_system(f"Connected: {names}. Ask me anything about your tasks!")
        else:
            self._add_system(
                "No AI providers configured.\n"
                "Close with Esc, then run :aii connect <provider> <key>\n"
                f"Supported: {', '.join(sorted(ai_client.MODELS))}"
            )

    def keypress(self, size: tuple[int, int], key: str) -> str | None:
        if key == "enter":
            text = self._edit.get_edit_text().strip()
            if text:
                self._edit.set_edit_text("")
                self._add_user(text)
                self._send_to_ai(text)
            return None
        if key in ("y", "Y") and self._pending_actions is not None:
            self._execute_pending_actions()
            return None
        if key in ("n", "N") and self._pending_actions is not None:
            self._pending_actions = None
            self._add_system("Action cancelled.")
            return None
        if key in ("esc",):
            self._pending_actions = None
            self._app._close_ai_chat()
            return None
        return super().keypress(size, key)

    # ── Message management ────────────────────────────────────

    def _add_user(self, text: str) -> None:
        self._messages.append({"sender": "user", "text": text})
        self._history_walker.append(
            AttrMap(Text(("dim", f"\u25b6 {text}")), "default", "focus")
        )
        self._scroll_bottom()

    def _add_ai(self, text: str, actions: list[dict] | None = None) -> None:
        self._messages.append({"sender": "ai", "text": text, "actions": actions or []})
        self._history_walker.append(AttrMap(Text(text), "default", "focus"))
        self._scroll_bottom()
        if actions:
            self._show_action_confirmation(actions)

    def _add_system(self, text: str) -> None:
        self._messages.append({"sender": "system", "text": text})
        self._history_walker.append(
            AttrMap(Text(("dim", f"\u2139 {text}")), "default", "focus")
        )
        self._scroll_bottom()

    def _add_thinking(self) -> None:
        self._thinking_idx = len(self._history_walker)
        self._history_walker.append(
            AttrMap(Text(("warn", "\u23f3 Thinking...")), "default", "focus")
        )
        self._scroll_bottom()

    def _replace_thinking(self, text: str, actions: list[dict] | None = None) -> None:
        if 0 <= self._thinking_idx < len(self._history_walker):
            self._history_walker[self._thinking_idx] = AttrMap(
                Text(text), "default", "focus"
            )
            self._messages.append(
                {"sender": "ai", "text": text, "actions": actions or []}
            )
            self._thinking_idx = -1
            if actions:
                self._show_action_confirmation(actions)
        self._scroll_bottom()

    def _scroll_bottom(self) -> None:
        if self._history_walker:
            self._history_box.set_focus(len(self._history_walker) - 1)
            self._history_box.set_focus_valign("bottom")

    # ── AI request ────────────────────────────────────────────

    def _send_to_ai(self, text: str) -> None:
        if self._ai_pending:
            return

        self._pending_actions = None
        self._ai_pending = True
        self._add_thinking()

        context = ai_client.build_context(self._app)
        full_system = _SYSTEM_PROMPT + "\n\n" + context

        messages: list[dict] = [{"role": "system", "content": full_system}]

        history = [m for m in self._messages if m["sender"] != "system"]
        if len(history) > 10:
            history = history[-10:]
        for m in history:
            role = "assistant" if m["sender"] == "ai" else "user"
            messages.append({"role": role, "content": m["text"]})
        messages.append({"role": "user", "content": text})

        app = self._app
        chat_self = self

        def worker() -> None:
            try:
                response, provider, actions = ai_client.chat(messages)
                if provider:
                    response += "\n\n\u2014 TaskWatcher"
                app._ai_inbox.put(lambda: chat_self._on_ai_result(response, actions))
            except Exception as e:
                app._ai_inbox.put(lambda: chat_self._on_ai_error(str(e)))

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def _on_ai_result(self, text: str, actions: list[dict]) -> None:
        self._ai_pending = False
        self._replace_thinking(text, actions)

    def _on_ai_error(self, error: str) -> None:
        self._ai_pending = False
        if 0 <= self._thinking_idx < len(self._history_walker):
            self._history_walker.pop(self._thinking_idx)
            self._thinking_idx = -1
        self._add_system(f"Error: {error}")

    # ── Action confirmation ───────────────────────────────────

    def _show_action_confirmation(self, actions: list[dict]) -> None:
        self._pending_actions = actions
        lines: list[str] = []
        for a in actions:
            label = a.get("type", "UNKNOWN").replace("_", " ").title()
            params = " | ".join(
                f"{k}: {v}" for k, v in a.items() if k != "type"
            )
            lines.append(f"{label}: {params}")
        lines.append("")
        lines.append("Confirm? (Y/n)")
        self._history_walker.append(
            AttrMap(
                Text([("head", "\u2753 "), ("default", "\n".join(lines))]),
                "default",
                "focus",
            )
        )
        self._scroll_bottom()

    def _execute_pending_actions(self) -> None:
        actions = self._pending_actions
        self._pending_actions = None

        from . import (
            archive_cmds,
            directory_cmds,
            note_cmds,
            task_cmds,
        )

        results: list[str] = []
        for a in actions:
            atype = a.get("type", "")
            try:
                if atype == "CREATE_TASK":
                    name = a.get("name", "New Task")
                    did_str = a.get("directory_id", "")
                    did: int | None = int(did_str) if did_str else getattr(
                        self._app, "_selected_directory_id", None
                    )
                    if did is None:
                        results.append("No directory available for task creation")
                        continue
                    task_cmds.create_task(
                        directory_id=did,
                        name=name,
                        description=a.get("description", ""),
                        urgency=int(a.get("urgency", 1)),
                        difficulty=int(a.get("difficulty", 1)),
                        deadline=a.get("deadline", "none"),
                        time_dedicated=int(a.get("time_dedicated", 0)),
                    )
                    results.append(f"Task '{name}' created")

                elif atype == "CREATE_DIRECTORY":
                    name = a.get("name", "New Directory")
                    aid_str = a.get("archive_id", "")
                    aid: int | None = int(aid_str) if aid_str else getattr(
                        self._app, "_selected_archive_id", None
                    )
                    if aid is None:
                        results.append("No archive available for directory creation")
                        continue
                    directory_cmds.create_directory(archive_id=aid, name=name)
                    results.append(f"Directory '{name}' created")

                elif atype == "CREATE_ARCHIVE":
                    name = a.get("name", "New Archive")
                    archive_cmds.create_archive(name=name)
                    results.append(f"Archive '{name}' created")

                elif atype == "FINISH_TASK":
                    tid_str = a.get("task_id", "")
                    tid: int | None = int(tid_str) if tid_str else getattr(
                        self._app, "_selected_task_id", None
                    )
                    if tid is None:
                        results.append("No task selected to finish")
                        continue
                    task_cmds.mark_done(tid)
                    results.append("Task finished")

                elif atype == "ADD_NOTE":
                    tid_str = a.get("task_id", "")
                    tid: int | None = int(tid_str) if tid_str else getattr(
                        self._app, "_selected_task_id", None
                    )
                    if tid is None:
                        results.append("No task selected for note")
                        continue
                    note_cmds.create_note(tid, datetime.date.today().isoformat(), a.get("note", ""))
                    results.append("Note added")

                elif atype == "SUGGEST":
                    results.append(
                        f"Suggestion: {a.get('task', '')} "
                        f"(reason: {a.get('reason', 'no reason given')})"
                    )

                else:
                    results.append(f"Unknown action: {atype}")

            except Exception as e:
                results.append(f"Error executing {atype}: {e}")

        self._app._refresh_list()
        self._app._show_detail()

        result_text = "\n".join(results)
        self._add_system(f"Done:\n{result_text}")


class ProviderSelectWidget(WidgetWrap):
    PROVIDERS = ["groq", "gemini", "mistral"]

    def __init__(self, on_select, on_cancel):
        self.on_select = on_select
        self.on_cancel = on_cancel

        walker = SimpleFocusListWalker([
            AttrMap(_SelectableText(f"  {p}  "), "default", "focus")
            for p in self.PROVIDERS
        ])
        self._listbox = ListBox(walker)
        super().__init__(urwid.LineBox(self._listbox, title="Select AI Provider"))

    def keypress(self, size: tuple[int, int], key: str) -> str | None:
        if key in ("enter", " "):
            idx = self._listbox.focus_position
            if idx < len(self.PROVIDERS):
                self.on_select(self.PROVIDERS[idx])
            return None
        if key in ("esc", "q"):
            self.on_cancel()
            return None
        return super().keypress(size, key)
