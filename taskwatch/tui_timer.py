import json
import logging
import os
import queue
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import urwid
from urwid import (
    LineBox,
    Overlay,
    SimpleFocusListWalker,
    Text,
)

from . import task_cmds, timer as timer_mod
from .paths import DATA_DIR, TIMER_STATE_PATH
from .tui_helpers import _build_terminal_cmd, _ensure_default_sounds, _play_sound, _SOUND_DIR
from .tui_widgets import VimListBox

logger = logging.getLogger("taskwatch.tui")

class _TimerMixin:

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
        self._prev_timer_segment_idx = 0
        self._timer_paused = False
        self._timer_task_id = task.id
        self._timer_task_name = task.name
        self._timer_seconds = total
        self._timer_elapsed = 0
        self._update_clock_display()

    def _write_timer_file(self) -> None:
        timer_path = Path.home() / ".local" / "share" / "taskwatch" / "timer.json"
        try:
            timer_path.parent.mkdir(parents=True, exist_ok=True)
            if not self._timer_running:
                data = {"text": "", "class": "inactive"}
            else:
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
            timer_mod.atomic_write_json(timer_path, data)
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
            timer_mod.atomic_write_json(self._timer_state_path, current)
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
            if getattr(sys, 'frozen', False):
                cmd = [sys.executable, 'daemon']
            else:
                cmd = [sys.executable, str(self._daemon_path)]
            subprocess.Popen(
                cmd,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, ValueError) as e:
            logger.warning("Failed to spawn timer daemon: %s", e)

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
        remaining = state.get("remaining")
        self._timer_elapsed = max(0, self._timer_seconds - remaining) if remaining is not None else 0
        if state.get("mode") == "scheduled":
            self._timer_task_id = state.get("task_id")
            self._timer_task_name = state.get("task_name")
            self._timer_schedule = state.get("schedule")
            self._timer_segment_idx = state.get("segment_idx", 0)
            self._timer_segment_elapsed = state.get("segment_elapsed", 0)
        else:
            self._timer_task_id = None
            self._timer_task_name = None
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
        self._timer_task_name = None
        self._timer_schedule = None
        self._timer_segment_idx = 0
        self._timer_segment_elapsed = 0
        self._prev_timer_segment_idx = 0
        self._update_clock_display()

    def _segments_from_preset(self, preset: dict) -> list[int]:
        prep_s = int(round(preset["prep"] * 60))
        work_s = int(round(preset["work"] * 60))
        break_s = int(round(preset["break"] * 60))
        laps = preset["laps"]
        segments = []
        if prep_s > 0:
            segments.append(prep_s)
        for i in range(laps):
            segments.append(work_s)
            if i < laps - 1 and break_s > 0:
                segments.append(break_s)
        return segments

    def _start_timer_from_preset(self, preset: dict, name: str = "Timer") -> None:
        segments = self._segments_from_preset(preset)
        total = sum(segments)
        schedule = {
            "total_minutes": total // 60,
            "total_seconds": total,
            "segments": segments,
            "segment_count": len(segments),
            "source": "preset",
            "preset_name": name,
        }
        self._kill_daemon()
        self._write_timer_state({
            "running": True,
            "mode": "scheduled",
            "task_id": None,
            "task_name": f"[{name}]",
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
        self._prev_timer_segment_idx = 0
        self._timer_paused = False
        self._timer_task_id = None
        self._timer_task_name = f"[{name}]"
        self._timer_seconds = total
        self._timer_elapsed = 0
        self._update_clock_display()

    def _stop_timer(self) -> None:
        self._write_timer_state({"stopped": True})
        self._timer_running = False
        self._timer_seconds = 0
        self._timer_paused = False
        self._timer_task_id = None
        self._timer_task_name = None
        self._timer_schedule = None
        self._timer_segment_idx = 0
        self._timer_segment_elapsed = 0
        self._prev_timer_segment_idx = 0
        self._update_clock_display()
        self._write_timer_file()

    def _cmd_update(self) -> None:
        script = self._find_update_script()
        if script is None:
            self._set_timed_caption("error", "update.sh not found ")
            return
        terminal = self._get_terminal()
        if terminal is None:
            self._set_timed_caption("error", "No terminal found ")
            return
        cmd = _build_terminal_cmd(terminal, f"'{script}' ; echo; echo 'Press Enter to close...'; read")
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._set_timed_caption("done", "Update started in new terminal... ")

    @staticmethod
    def _find_update_script() -> str | None:
        candidates = [
            Path(__file__).resolve().parent.parent / "update.sh",
            Path.home() / ".local" / "bin" / "update.sh",
            Path.home() / ".local" / "share" / "taskwatch" / "update.sh",
        ]
        for p in candidates:
            if p.is_file():
                return str(p)
        return None

    def _cmd_sound_toggle(self) -> None:
        self._sound_enabled = not self._sound_enabled
        self._set_timed_caption(
            "done" if self._sound_enabled else "dim",
            f"Sound {'on' if self._sound_enabled else 'off'} ",
        )
        self._write_sound_config()

    def _cmd_sound_set_enabled(self, enabled: bool) -> None:
        self._sound_enabled = enabled
        self._set_timed_caption(
            "done" if enabled else "dim",
            f"Sound {'on' if enabled else 'off'} ",
        )
        self._write_sound_config()

    def _cmd_sound_custom(self, arg: str) -> None:
        parts = arg.split(None, 1)
        if len(parts) != 2:
            self._set_timed_caption("error", "Usage: sound work/break/done <path> ")
            return
        key, path_str = parts[0].lower(), parts[1]
        if key not in ("work", "break", "done"):
            self._set_timed_caption("error", "Key must be work, break, or done ")
            return
        p = Path(path_str).expanduser().resolve()
        if not p.is_file():
            self._set_timed_caption("error", f"File not found: {p} ")
            return
        self._sound_paths[key] = p
        self._write_sound_config()
        self._set_timed_caption("done", f"Sound '{key}' set to {p.name} ")

    def _write_sound_config(self) -> None:
        cfg_path = Path(__file__).resolve().parent.parent / "config" / "config.txt"
        try:
            with open(cfg_path) as f:
                lines = f.readlines()
            written = {"SOUND_ENABLED": False, "SOUND_WORK": False, "SOUND_BREAK": False, "SOUND_DONE": False}
            with open(cfg_path, "w") as f:
                for line in lines:
                    key = line.split(":", 1)[0].strip() if ":" in line else ""
                    if key == "SOUND_ENABLED":
                        f.write(f"SOUND_ENABLED:{str(self._sound_enabled).lower()}\n")
                        written["SOUND_ENABLED"] = True
                    elif key == "SOUND_WORK":
                        p = self._sound_paths.get("work")
                        if p and str(p).startswith(str(_SOUND_DIR)):
                            f.write("SOUND_WORK:\n")
                        else:
                            f.write(f"SOUND_WORK:{p}\n" if p else "SOUND_WORK:\n")
                        written["SOUND_WORK"] = True
                    elif key == "SOUND_BREAK":
                        p = self._sound_paths.get("break")
                        if p and str(p).startswith(str(_SOUND_DIR)):
                            f.write("SOUND_BREAK:\n")
                        else:
                            f.write(f"SOUND_BREAK:{p}\n" if p else "SOUND_BREAK:\n")
                        written["SOUND_BREAK"] = True
                    elif key == "SOUND_DONE":
                        p = self._sound_paths.get("done")
                        if p and str(p).startswith(str(_SOUND_DIR)):
                            f.write("SOUND_DONE:\n")
                        else:
                            f.write(f"SOUND_DONE:{p}\n" if p else "SOUND_DONE:\n")
                        written["SOUND_DONE"] = True
                    else:
                        f.write(line)
                for k in ("SOUND_ENABLED", "SOUND_WORK", "SOUND_BREAK", "SOUND_DONE"):
                    if not written[k]:
                        if k == "SOUND_ENABLED":
                            f.write(f"SOUND_ENABLED:{str(self._sound_enabled).lower()}\n")
                        elif k == "SOUND_WORK":
                            f.write("SOUND_WORK:\n")
                        elif k == "SOUND_BREAK":
                            f.write("SOUND_BREAK:\n")
                        elif k == "SOUND_DONE":
                            f.write("SOUND_DONE:\n")
        except OSError:
            pass

    def _cmd_preset(self, cmd: str) -> None:
        parts = cmd.split()
        if len(parts) == 1 or (len(parts) == 2 and parts[1] == "list"):
            self._show_preset_list()
        elif parts[1] == "add" and len(parts) == 7:
            name, sp, sw, sb, sl = parts[2], parts[3], parts[4], parts[5], parts[6]
            try:
                prep = timer_mod.parse_time_string(sp)
                work = timer_mod.parse_time_string(sw)
                break_ = timer_mod.parse_time_string(sb)
                laps = int(sl)
            except ValueError:
                self._set_timed_caption("error", "Usage: :preset add <name> <prep> <work> <break> <laps>")
                return
            if work <= 0 or laps <= 0:
                self._set_timed_caption("error", "Work and laps must be > 0")
                return
            self._timer_presets[name] = {"prep": prep, "work": work, "break": break_, "laps": laps}
            timer_mod.write_presets(self._timer_presets)
            total = prep + work * laps + break_ * max(0, laps - 1)
            self._set_timed_caption("done", f"Preset '{name}' added ({timer_mod.fmt_timer_val(total)}m)")
        elif parts[1] == "remove" and len(parts) == 3:
            name = parts[2]
            if name in self._timer_presets:
                del self._timer_presets[name]
                timer_mod.write_presets(self._timer_presets)
                self._set_timed_caption("done", f"Preset '{name}' removed")
            else:
                self._set_timed_caption("error", f"Preset '{name}' not found")
        else:
            self._set_timed_caption("error", "Usage: :preset [list|add <n> <p> <w> <b> <l>|remove <n>]")

    def _show_preset_list(self) -> None:
        if not self._timer_presets:
            self._set_timed_caption("dim", "No presets configured")
            return
        walker: list[Text] = []
        walker.append(Text([("head", "  Timer Presets\n")]))
        for name, p in sorted(self._timer_presets.items()):
            total = p["prep"] + p["work"] * p["laps"] + p["break"] * max(0, p["laps"] - 1)
            line = (
                f"  {name}: "
                f"{timer_mod.fmt_timer_val(p['prep'])} + {timer_mod.fmt_timer_val(p['work'])} "
                f"+ {timer_mod.fmt_timer_val(p['break'])} \u00d7 {p['laps']}"
                f"  = {timer_mod.fmt_timer_val(total)}m"
            )
            walker.append(Text(line))
        walker.append(Text(""))
        walker.append(Text("  Press esc / q to close"))
        content = LineBox(VimListBox(SimpleFocusListWalker(walker)))
        overlay = Overlay(
            content, self._frame,
            align="center", width=("relative", 50),
            valign="middle", height=("relative", 50),
        )
        self._loop.widget = overlay

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
            self._clock_text.set_text(f"{phase_ind}\u23f1 {h:02d}:{m:02d}:{s:02d}{pause_ind}")
            self._clock_w.set_attr_map({None: attr})
        else:
            self._clock_text.set_text(now.strftime("%H:%M:%S"))
            self._clock_w.set_attr_map({None: "dim"})
        self._write_timer_file()

    def _tick(self, loop: object, data: object) -> None:
        try:
            while True:
                try:
                    cb = self._ai_inbox.get_nowait()
                    try:
                        cb()
                    except Exception:
                        pass
                except queue.Empty:
                    break

            timer_completed = False
            if self._timer_running:
                # Increment locally first (fallback if daemon is dead)
                if not self._timer_paused:
                    self._timer_elapsed += 1

                try:
                    with open(self._timer_state_path) as f:
                        state = json.load(f)
                except (OSError, json.JSONDecodeError):
                    state = {}
                if not state.get("running"):
                    self._stop_timer()
                    timer_completed = True
                else:
                    self._timer_paused = state.get("paused", self._timer_paused)

                    daemon_remaining = state.get("remaining")
                    if daemon_remaining is not None:
                        daemon_elapsed = self._timer_seconds - daemon_remaining
                        if daemon_elapsed > self._timer_elapsed:
                            self._timer_elapsed = daemon_elapsed
                            self._timer_segment_idx = state.get("segment_idx", 0)
                            self._timer_segment_elapsed = state.get("segment_elapsed", 0)

                # Local segment tracking (daemon-dead fallback for scheduled timers)
                if self._timer_schedule and not state.get("paused", False):
                    segments = self._timer_schedule["segments"]
                    acc = 0
                    for i, seg in enumerate(segments):
                        if acc + seg > self._timer_elapsed:
                            self._timer_segment_idx = i
                            self._timer_segment_elapsed = self._timer_elapsed - acc
                            break
                        acc += seg

                # Segment transition sound detection
                if self._sound_enabled and self._timer_schedule and not self._timer_paused:
                    new_idx = self._timer_segment_idx
                    old_idx = self._prev_timer_segment_idx
                    if new_idx != old_idx and old_idx >= 0:
                        if old_idx % 2 == 1:
                            _play_sound(self._sound_paths.get("work"))
                        elif old_idx > 0 and old_idx % 2 == 0:
                            _play_sound(self._sound_paths.get("break"))
                    self._prev_timer_segment_idx = new_idx

                # Local completion detection (covers daemon-dead scenario)
                if self._timer_running and self._timer_elapsed >= self._timer_seconds:
                    if self._timer_task_name:
                        self._notify_timer_done(f"{self._timer_task_name} ({self._timer_seconds // 60}m)")
                    else:
                        self._notify_timer_done(f"{self._timer_seconds // 60}-minute timer")
                    if self._timer_task_id is not None:
                        try:
                            task_cmds.mark_done(self._timer_task_id)
                        except Exception:
                            pass
                    self._write_timer_state({"running": False, "stopped": True})
                    self._timer_running = False
                    timer_completed = True

            if timer_completed:
                if self._sound_enabled:
                    _play_sound(self._sound_paths.get("done"))
                self._refresh_list()

            self._tick_counter += 1
            if self._tick_counter % 60 == 0:
                task_cmds.reset_overdue_repeatables()
            if self._tick_counter % 300 == 0:
                self._check_and_notify_deadlines()

            if self._loading:
                self._loading_spinner_idx = (self._loading_spinner_idx + 1) % len(self._loading_frames)
                spinner = self._loading_frames[self._loading_spinner_idx]
                title = self._loading_title
                self._cmd.set_caption(("standout", f"\u276f {spinner} {title}"))

            self._update_clock_display()
        finally:
            self._loop.set_alarm_in(1, self._tick)