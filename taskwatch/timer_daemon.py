#!/usr/bin/env python3
"""Standalone timer daemon — survives TUI closure."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

INACTIVE_DATA = {"text": "", "class": "inactive"}

STATE_PATH = Path.home() / ".local" / "share" / "taskwatch" / "timer_state.json"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TIMER_FILE_PATH = Path("/tmp/taskwatch_timer.json")


def _atomic_write(data):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f)
    tmp.rename(STATE_PATH)


def _read_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _write_timer_file(remaining: int, segment_idx: int, paused: bool, mode: str):
    try:
        phase = "TIMER" if mode == "simple" else (
            "INTRO" if segment_idx == 0 else
            "WORK" if segment_idx % 2 == 1 else "BREAK"
        )
        h, m = divmod(remaining, 3600)
        m, s = divmod(m, 60)
        pause = " ⏸" if paused else ""
        time_str = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
        with open(TIMER_FILE_PATH, "w") as f:
            json.dump({
                "text": f"⏱ {time_str}{pause}",
                "alt": phase,
                "class": f"timer-{phase.lower()}",
                "tooltip": f"Timer: {phase} ({time_str} remaining)",
            }, f)
    except OSError:
        pass


def _clear_timer_file():
    try:
        with open(TIMER_FILE_PATH, "w") as f:
            json.dump(INACTIVE_DATA, f)
    except OSError:
        pass


def _notify(label):
    try:
        subprocess.run(
            ["notify-send", "-a", "TaskWatch+", "Timer Complete", label],
            capture_output=True,
        )
    except FileNotFoundError:
        pass


def _mark_done(task_id: int):
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from taskwatch import task_cmds  # noqa: PLC0415
        task_cmds.mark_done(task_id)
    except Exception:
        pass


def run_simple(total_seconds: int, minutes: int, state):
    start_time = state.get("start_time", time.time())
    pause_elapsed = state.get("pause_elapsed", 0.0)
    paused = state.get("paused", False)

    while True:
        cmd = _read_state()
        if cmd.get("stopped"):
            _atomic_write({"running": False})
            _clear_timer_file()
            return

        new_paused = cmd.get("paused", False)
        if new_paused and not paused:
            pause_elapsed += time.time() - start_time
            start_time = time.time()
            paused = True
        if not new_paused and paused:
            start_time = time.time()
            paused = False

        elapsed = pause_elapsed if paused else (time.time() - start_time + pause_elapsed)
        remaining = max(0, total_seconds - elapsed)

        _atomic_write({
            "running": True,
            "mode": "simple",
            "pid": os.getpid(),
            "minutes": minutes,
            "total_seconds": total_seconds,
            "remaining": int(round(remaining)),
            "paused": paused,
            "stopped": False,
            "start_time": start_time,
            "pause_elapsed": pause_elapsed,
            "task_id": None,
            "task_name": None,
            "segment_idx": 0,
            "segment_elapsed": 0,
        })
        _write_timer_file(int(round(remaining)), 0, paused, "simple")

        if remaining <= 0:
            _notify(f"{minutes}-minute timer ({int(total_seconds // 60)}m)")
            _atomic_write({"running": False})
            _clear_timer_file()
            return

        time.sleep(1)


def run_scheduled(schedule: dict, task_id: int, task_name: str, total_seconds: int, state):
    segments = schedule.get("segments", [])
    start_time = state.get("start_time", time.time())
    pause_elapsed = state.get("pause_elapsed", 0.0)
    paused = state.get("paused", False)

    while True:
        cmd = _read_state()
        if cmd.get("stopped"):
            _atomic_write({"running": False})
            _clear_timer_file()
            return

        new_paused = cmd.get("paused", False)
        if new_paused and not paused:
            pause_elapsed += time.time() - start_time
            start_time = time.time()
            paused = True
        if not new_paused and paused:
            start_time = time.time()
            paused = False

        elapsed = pause_elapsed if paused else (time.time() - start_time + pause_elapsed)
        remaining = max(0, total_seconds - elapsed)

        acc = 0
        seg_idx = 0
        seg_elapsed = 0.0
        for i, seg in enumerate(segments):
            if acc + seg > elapsed:
                seg_idx = i
                seg_elapsed = elapsed - acc
                break
            acc += seg
        else:
            seg_idx = len(segments)
            seg_elapsed = 0.0

        if seg_idx < len(segments):
            seg_remaining = max(0, int(round(segments[seg_idx] - seg_elapsed)))
        else:
            seg_remaining = 0

        _atomic_write({
            "running": True,
            "mode": "scheduled",
            "pid": os.getpid(),
            "task_id": task_id,
            "task_name": task_name,
            "schedule": schedule,
            "total_seconds": total_seconds,
            "remaining": int(round(remaining)),
            "segment_idx": seg_idx,
            "segment_elapsed": int(round(seg_elapsed)),
            "paused": paused,
            "stopped": False,
            "start_time": start_time,
            "pause_elapsed": pause_elapsed,
        })
        _write_timer_file(seg_remaining, seg_idx, paused, "scheduled")

        if seg_idx >= len(segments):
            _mark_done(task_id)
            _notify(f"{task_name} ({int(total_seconds // 60)}m)")
            _atomic_write({"running": False})
            _clear_timer_file()
            return

        time.sleep(1)


def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    state = _read_state()
    mode = state.get("mode", "simple")
    total = state.get("total_seconds", 600)

    if mode == "scheduled":
        run_scheduled(
            state.get("schedule", {}),
            state.get("task_id"),
            state.get("task_name", ""),
            total,
            state,
        )
    else:
        run_simple(total, state.get("minutes", 10), state)


if __name__ == "__main__":
    main()
