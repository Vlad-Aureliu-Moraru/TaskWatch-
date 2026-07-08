from __future__ import annotations

from datetime import date, datetime
from functools import partial
from pathlib import Path

from . import (
    archive_cmds,
    calcurse_cmds,
    directory_cmds,
    note_cmds,
    subtask_cmds,
    task_cmds,
    undo_cmds,
)
from . import db as db_mod
from .tui_helpers import (
    Level,
    _dur,
)
from .tui_widgets import DAYS_OF_WEEK, DayPickerWidget


class _WizardMixin:
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
        dir_id = self._selected_directory_id
        if dir_id is not None:
            defaults = directory_cmds.get_directory_defaults(dir_id)
            self._wiz_dir_def_u = defaults["urgency"]
            self._wiz_dir_def_d = defaults["difficulty"]
        else:
            self._wiz_dir_def_u = 1
            self._wiz_dir_def_d = 1
        self._start_wizard(
            f"Urgency 1-5 [default {self._wiz_dir_def_u}] (step 3): ",
            partial(self._wiz_task_urgency, name),
        )

    def _wiz_task_urgency(self, name: str, urgency_str: str) -> None:
        if not urgency_str:
            urgency = self._wiz_dir_def_u
        else:
            try:
                urgency = int(urgency_str)
                if not 1 <= urgency <= 5:
                    raise ValueError
            except ValueError:
                self._start_wizard(
                    f"Urgency 1-5 [default {self._wiz_dir_def_u}] (step 3): ",
                    partial(self._wiz_task_urgency, name),
                )
                return
        self._start_wizard(
            f"Difficulty 1-5 [default {self._wiz_dir_def_d}] (step 4): ",
            partial(self._wiz_task_difficulty, name, urgency),
        )

    def _wiz_task_difficulty(
        self, name: str, urgency: int, diff_str: str
    ) -> None:
        if not diff_str:
            difficulty = self._wiz_dir_def_d
        else:
            try:
                difficulty = int(diff_str)
                if not 1 <= difficulty <= 5:
                    raise ValueError
            except ValueError:
                self._start_wizard(
                    f"Difficulty 1-5 [default {self._wiz_dir_def_d}] (step 4): ",
                    partial(self._wiz_task_difficulty, name, urgency),
                )
                return
        self._start_wizard(
            "Time budget minutes (step 5): ",
            partial(self._wiz_task_time, name, urgency, difficulty),
        )

    def _wiz_task_time(
        self, name: str, urgency: int, difficulty: int, time_str: str,
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
            "Deadline (dd/MM/yyyy, tomorrow, next week...) or 'none' (step 6): ",
            partial(self._wiz_task_deadline, name, urgency, difficulty, time_dedicated),
        )

    def _wiz_task_deadline(
        self, name: str, urgency: int, difficulty: int, time_dedicated: int, deadline: str,
    ) -> None:
        if not deadline:
            deadline = "none"
        if deadline != "none":
            try:
                task_cmds._normalize_date(deadline)
            except ValueError:
                self._start_wizard(
                    "Deadline (dd/MM/yyyy, tomorrow, next week...) or 'none' (step 6): ",
                    partial(self._wiz_task_deadline, name, urgency, difficulty, time_dedicated),
                )
                return
        self._start_wizard(
            "Repeatable? y/n (step 7): ",
            partial(self._wiz_task_repeat, name, urgency, difficulty, time_dedicated, deadline),
        )

    def _wiz_task_repeat(
        self, name: str, urgency: int, difficulty: int, time_dedicated: int, deadline: str, repeat_yn: str,
    ) -> None:
        if not repeat_yn:
            repeatable = False
        else:
            repeatable = repeat_yn.lower() in ("y", "yes")
        if repeatable:
            self._start_wizard(
                "Repeat type daily/weekly/biweekly/monthly/yearly (step 8): ",
                partial(self._wiz_task_repeat_type, name, urgency, difficulty, time_dedicated, deadline),
            )
        else:
            task = task_cmds.create_task(
                self._selected_directory_id, name,
                description=self._wiz_desc, urgency=urgency, difficulty=difficulty,
                time_dedicated=time_dedicated, deadline=deadline, repeatable=False,
            )
            if task:
                undo_cmds.push("task_create", {"task_id": task.id})
            self._end_wizard()

    def _wiz_task_repeat_type(
        self, name: str, urgency: int, difficulty: int, time_dedicated: int, deadline: str, repeat_type: str,
    ) -> None:
        if not repeat_type:
            repeat_type = "daily"
        valid = ("daily", "weekly", "biweekly", "monthly", "yearly")
        if repeat_type not in valid:
            self._start_wizard(
                "Repeat type daily/weekly/biweekly/monthly/yearly (step 8): ",
                partial(self._wiz_task_repeat_type, name, urgency, difficulty, time_dedicated, deadline),
            )
            return
        if repeat_type == "daily":
            self._start_wizard(
                "Auto-repeat on finish? y/n (step 9): ",
                partial(self._wiz_task_auto_repeat, name, urgency, difficulty, time_dedicated, deadline, repeat_type, "none"),
            )
        else:
            self._show_day_picker(
                on_select=partial(self._wiz_task_repeat_day, name, urgency, difficulty, time_dedicated, deadline, repeat_type),
                on_cancel=partial(self._start_wizard, "Auto-repeat on finish? y/n (step 9): ",
                                  partial(self._wiz_task_auto_repeat, name, urgency, difficulty, time_dedicated, deadline, repeat_type, "none")),
            )

    def _wiz_task_repeat_day(
        self, name: str, urgency: int, difficulty: int, time_dedicated: int, deadline: str, repeat_type: str, day: str,
    ) -> None:
        self._start_wizard(
            "Auto-repeat on finish? y/n (step 9): ",
            partial(self._wiz_task_auto_repeat, name, urgency, difficulty, time_dedicated, deadline, repeat_type, day),
        )

    def _wiz_task_auto_repeat(
        self, name: str, urgency: int, difficulty: int, time_dedicated: int, deadline: str,
        repeat_type: str, repeat_on_specific_day: str, auto_repeat_yn: str,
    ) -> None:
        to_complete = auto_repeat_yn.lower() in ("y", "yes")
        task = task_cmds.create_task(
            self._selected_directory_id, name, description=self._wiz_desc,
            urgency=urgency, difficulty=difficulty, time_dedicated=time_dedicated,
            deadline=deadline, repeatable=True, repeatable_type=repeat_type,
            has_to_be_completed_to_repeat=to_complete, repeat_on_specific_day=repeat_on_specific_day,
        )
        if task:
            undo_cmds.push("task_create", {"task_id": task.id})
        self._end_wizard()

    def _wiz_note_content(self, today: str, content: str) -> None:
        note_cmds.create_note(self._selected_task_id, today, content)
        self._end_wizard()

    def _wiz_note_content_with_file(self, content: str) -> None:
        self._wiz_file_note_content = content
        self._show_file_picker(
            callback=self._on_file_picked,
            on_cancel=self._on_file_picker_cancel,
        )

    def _on_file_picked(self, file_path: str) -> None:
        self._loop.widget = self._frame
        today = date.today().strftime("%d/%m/%Y")
        note_cmds.create_note(
            self._selected_task_id, today, self._wiz_file_note_content, file_path=file_path,
        )
        self._wiz_file_note_content = ""
        self._end_wizard()

    def _on_file_picker_cancel(self) -> None:
        self._loop.widget = self._frame
        today = date.today().strftime("%d/%m/%Y")
        note_cmds.create_note(self._selected_task_id, today, self._wiz_file_note_content)
        self._wiz_file_note_content = ""
        self._end_wizard()

    def _wiz_edit_note(self, note_id: int, content: str) -> None:
        if not content:
            self._end_wizard()
            return
        note_cmds.update_note(note_id, note=content)
        self._end_wizard()

    def _wiz_confirm_delete(self, sid: int, answer: str) -> None:
        if answer.lower() in ("y", "yes"):
            self._do_remove(sid)
        self._end_wizard()

    def _attach_task_children(self, task_data: dict, sid: int, conn) -> None:
        task_data["notes"] = [dict(r) for r in conn.execute(
            "SELECT * FROM notes WHERE task_id = ?", (sid,))]
        task_data["subtasks"] = [dict(r) for r in conn.execute(
            "SELECT * FROM subtasks WHERE task_id = ?", (sid,))]
        task_data["task_tags"] = [dict(r) for r in conn.execute(
            "SELECT * FROM task_tags WHERE task_id = ?", (sid,))]
        task_data["timer_sessions"] = [dict(r) for r in conn.execute(
            "SELECT * FROM timer_sessions WHERE task_id = ?", (sid,))]

    def _do_remove(self, sid: int) -> None:
        task_data = undo_cmds.get_task_data(sid)
        if task_data is not None:
            conn = db_mod.get_conn()
            self._attach_task_children(task_data, sid, conn)
            undo_cmds.push("task_delete", task_data)
        task_cmds.delete_task(sid)
        self._refresh_list()

    def _wiz_confirm_delete_directory(self, sid: int, answer: str) -> None:
        if answer.lower() in ("y", "yes"):
            self._do_remove_directory(sid)
        self._end_wizard()

    def _wiz_confirm_delete_archive(self, sid: int, answer: str) -> None:
        if answer.lower() in ("y", "yes"):
            self._do_remove_archive(sid)
        self._end_wizard()

    def _wiz_confirm_delete_note(self, sid: int, answer: str) -> None:
        if answer.lower() in ("y", "yes"):
            self._do_remove_note(sid)
        self._end_wizard()

    def _do_remove_directory(self, sid: int) -> None:
        dir_data = directory_cmds.get_directory(sid)
        if dir_data is not None:
            conn = db_mod.get_conn()
            tasks = conn.execute(
                "SELECT * FROM tasks WHERE directory_id = ?", (sid,)
            ).fetchall()
            saved_tasks = []
            for t in tasks:
                td = dict(t)
                self._attach_task_children(td, t["id"], conn)
                saved_tasks.append(td)
            undo_cmds.push("directory_delete", {
                "id": dir_data.id,
                "archive_id": dir_data.archive_id,
                "name": dir_data.name,
                "tasks": saved_tasks,
            })
        directory_cmds.delete_directory(sid)
        self._refresh_list()

    def _do_remove_archive(self, sid: int) -> None:
        arch_data = archive_cmds.get_archive(sid)
        if arch_data is not None:
            conn = db_mod.get_conn()
            dirs = conn.execute(
                "SELECT * FROM directories WHERE archive_id = ?", (sid,)
            ).fetchall()
            saved_dirs = []
            for d in dirs:
                tasks = conn.execute(
                    "SELECT * FROM tasks WHERE directory_id = ?", (d["id"],)
                ).fetchall()
                saved_tasks = []
                for t in tasks:
                    td = dict(t)
                    self._attach_task_children(td, t["id"], conn)
                    saved_tasks.append(td)
                saved_dirs.append({
                    "id": d["id"],
                    "archive_id": d["archive_id"],
                    "name": d["name"],
                    "tasks": saved_tasks,
                })
            undo_cmds.push("archive_delete", {
                "id": arch_data.id,
                "name": arch_data.name,
                "directories": saved_dirs,
            })
        archive_cmds.delete_archive(sid)
        self._refresh_list()

    def _do_remove_note(self, sid: int) -> None:
        note_data = note_cmds.get_note(sid)
        if note_data is not None:
            undo_cmds.push("note_delete", {
                "id": note_data.id,
                "task_id": note_data.task_id,
                "date": note_data.date,
                "note": note_data.note,
                "file_path": note_data.file_path,
                "created_at": note_data.created_at,
            })
        note_cmds.delete_note(sid)
        self._refresh_list()

    def _edit_task(self, step: int, value: object) -> None:
        field_map = {
            1: "name",
            2: "description",
            3: "urgency",
            4: "difficulty",
            5: "time_dedicated",
            6: "deadline",
            7: "repeatable",
            8: "repeatable_type",
            9: "repeat_on_specific_day",
            10: "has_to_be_completed_to_repeat",
        }
        if step in field_map:
            self._edit_ctx[field_map[step]] = value

        if step == 1:
            self._start_wizard("Description: ", self._wiz_edit_task_description)
        elif step == 2:
            self._start_wizard(
                f"Urgency 1-5 [default {self._edit_task_defaults.get('urgency', 1)}]: ",
                self._wiz_edit_task_urgency,
            )
        elif step == 3:
            self._start_wizard(
                f"Difficulty 1-5 [default {self._edit_task_defaults.get('difficulty', 1)}]: ",
                self._wiz_edit_task_difficulty,
            )
        elif step == 4:
            self._start_wizard("Time budget minutes: ", self._wiz_edit_task_time)
        elif step == 5:
            self._start_wizard("Deadline (dd/MM/yyyy or 'none'): ", self._wiz_edit_task_deadline)
        elif step == 6:
            self._start_wizard("Repeatable? y/n: ", self._wiz_edit_repeatable)
        elif step == 7:
            if value:
                self._start_wizard(
                    "Repeat type daily/weekly/biweekly/monthly/yearly: ",
                    self._wiz_edit_repeat_type,
                )
            else:
                self._save_edit_task()
        elif step == 8:
            if value == "daily":
                self._start_wizard(
                    "Auto-repeat on finish? y/n: ",
                    self._wiz_edit_auto_repeat,
                )
            else:
                self._show_day_picker(
                    on_select=partial(self._wiz_edit_repeat_day, value),
                    on_cancel=partial(
                        self._start_wizard,
                        "Auto-repeat on finish? y/n: ",
                        self._wiz_edit_auto_repeat,
                    ),
                )
        elif step in (9, 10):
            self._save_edit_task()

    def _wiz_bulk_delete(self, answer: str) -> None:
        if answer.lower() in ("y", "yes"):
            tids = list(self._bulk_selection)
            conn = db_mod.get_conn()
            for tid in tids:
                task_data = undo_cmds.get_task_data(tid)
                if task_data is not None:
                    self._attach_task_children(task_data, tid, conn)
                    undo_cmds.push("task_delete", task_data)
                task_cmds.delete_task(tid)
            self._bulk_selection.clear()
            self._refresh_list()
        self._end_wizard()

    def _wiz_edit_archive(self, archive_id: int, old_name: str, new_name: str) -> None:
        if not new_name:
            self._end_wizard()
            return
        archive_cmds.rename_archive(archive_id, new_name)
        self._end_wizard()

    def _wiz_edit_dir(self, dir_id: int, _old_name: str, new_name: str) -> None:
        if not new_name:
            self._end_wizard()
            return
        directory_cmds.rename_directory(dir_id, new_name)
        self._end_wizard()

    def _wiz_edit_task_name(self, name: str) -> None:
        if not name:
            name = self._edit_ctx.get("name", "")
            if not name:
                self._start_wizard("Task name: ", self._wiz_edit_task_name)
                return
        self._edit_task(step=1, value=name)

    def _wiz_edit_task_description(self, desc: str) -> None:
        self._edit_task(step=2, value=desc)

    def _wiz_edit_task_urgency(self, urgency_str: str) -> None:
        if not urgency_str:
            urgency = self._edit_task_defaults.get("urgency", 1)
        else:
            try:
                urgency = int(urgency_str)
                if not 1 <= urgency <= 5:
                    raise ValueError
            except ValueError:
                self._start_wizard(
                    f"Urgency 1-5 [default {self._edit_task_defaults.get('urgency', 1)}]: ",
                    self._wiz_edit_task_urgency,
                )
                return
        self._edit_task(step=3, value=urgency)

    def _wiz_edit_task_difficulty(self, diff_str: str) -> None:
        if not diff_str:
            difficulty = self._edit_task_defaults.get("difficulty", 1)
        else:
            try:
                difficulty = int(diff_str)
                if not 1 <= difficulty <= 5:
                    raise ValueError
            except ValueError:
                self._start_wizard(
                    f"Difficulty 1-5 [default {self._edit_task_defaults.get('difficulty', 1)}]: ",
                    self._wiz_edit_task_difficulty,
                )
                return
        self._edit_task(step=4, value=difficulty)

    def _wiz_edit_task_time(self, time_str: str) -> None:
        if not time_str:
            time_dedicated = 0
        else:
            try:
                time_dedicated = int(time_str)
            except ValueError:
                self._start_wizard("Time budget minutes: ", self._wiz_edit_task_time)
                return
        self._edit_task(step=5, value=time_dedicated)

    def _wiz_edit_task_deadline(self, deadline: str) -> None:
        if not deadline:
            deadline = "none"
        if deadline != "none":
            try:
                task_cmds._normalize_date(deadline)
            except ValueError:
                self._start_wizard("Deadline (dd/MM/yyyy or 'none'): ", self._wiz_edit_task_deadline)
                return
        self._edit_task(step=6, value=deadline)

    def _wiz_edit_repeatable(self, yn: str) -> None:
        repeatable = yn.lower() in ("y", "yes")
        self._edit_task(step=7, value=repeatable)

    def _wiz_edit_repeat_type(self, repeat_type: str) -> None:
        if not repeat_type:
            repeat_type = "daily"
        valid = ("daily", "weekly", "biweekly", "monthly", "yearly")
        if repeat_type not in valid:
            self._start_wizard("Repeat type daily/weekly/biweekly/monthly/yearly: ", self._wiz_edit_repeat_type)
            return
        self._edit_task(step=8, value=repeat_type)

    def _wiz_edit_repeat_day(self, repeat_type: str, day: str) -> None:
        self._edit_task(step=9, value=day)

    def _wiz_edit_skip_day_picker(self) -> None:
        self._edit_task(step=9, value="none")

    def _wiz_edit_auto_repeat(self, auto_repeat_yn: str) -> None:
        to_complete = auto_repeat_yn.lower() in ("y", "yes")
        self._edit_task(step=10, value=to_complete)

    def _start_wizard(
        self, prompt: str, handler: Callable, *, password: bool = False,
    ) -> None:
        self._wizard_stack.append((self._current_prompt, self._prompt_handler))
        if password:
            self._cmd.set_caption(("standout", prompt))
            self._current_prompt = ("standout", prompt)
        else:
            self._cmd.set_caption(("default", prompt))
            self._current_prompt = ("default", prompt)
        self._cmd.set_edit_text("")
        self._prompt_handler = handler
        self._frame.focus_position = "footer"

    def _wizard_back(self) -> None:
        if self._wizard_stack:
            prev_prompt, prev_handler = self._wizard_stack.pop()
            self._cmd.set_caption(prev_prompt)
            self._current_prompt = prev_prompt
            self._prompt_handler = prev_handler
            self._cmd.set_edit_text("")
            self._frame.focus_position = "footer"

    def _end_wizard(self, message: str | None = None, attr: str = "done") -> None:
        self._wizard_stack.clear()
        self._prompt_handler = None
        self._current_prompt = ("standout", "\u276f ")
        self._cmd.set_caption(("standout", "\u276f "))
        self._cmd.set_edit_text("")
        if message:
            self._set_timed_caption(attr, f"{message} ")
        self._refresh_list()
        try:
            calcurse_cmds.sync_to_calcurse()
        except Exception:
            pass
