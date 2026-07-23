# ruff: noqa: E501
import asyncio
import fcntl
import json
import os
import pty
import secrets
import signal
import struct
import subprocess
import termios
import threading

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pathlib import Path

from . import archive_cmds, directory_cmds, note_cmds, stats_cmds, task_cmds
from .paths import SERVER_TOKEN_PATH
from .tui_helpers import _find_opencode

CONVO_DIR = Path.home() / ".taskwatch" / "convos"


def _convo_path(prefix: str, id: int) -> Path:
    CONVO_DIR.mkdir(parents=True, exist_ok=True)
    return CONVO_DIR / f"{prefix}_{id}.jsonl"


def _read_convo(prefix: str, id: int) -> list[dict]:
    path = _convo_path(prefix, id)
    if not path.exists():
        return []
    try:
        with open(path, errors="replace") as f:
            content = f.read()
    except OSError as e:
        print(f"[convo] IO error reading {path}: {e}", flush=True)
        return []
    result = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return result


def _append_convo(prefix: str, id: int, entry: dict):
    path = _convo_path(prefix, id)
    with open(path, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _clear_convo(prefix: str, id: int):
    path = _convo_path(prefix, id)
    path.unlink(missing_ok=True)


app = FastAPI(title="TaskWatch+")


def _load_or_create_token() -> str:
    SERVER_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SERVER_TOKEN_PATH.exists():
        try:
            data = json.loads(SERVER_TOKEN_PATH.read_text())
            token = data.get("token", "")
            if token:
                return token
        except (json.JSONDecodeError, OSError):
            pass
    token = secrets.token_hex(32)
    SERVER_TOKEN_PATH.write_text(json.dumps({"token": token}, indent=2))
    return token


SERVER_TOKEN = _load_or_create_token()


def _verify_token(request: Request) -> None:
    token = request.query_params.get("token") or ""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
    if token != SERVER_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid or missing token")


_sessions: dict[str, "PTYSession"] = {}


class PTYSession:
    def __init__(self, name: str):
        self.name = name
        self._running = True
        self._master_fd = None
        self._child_pid = None

    def start(self, cmd: str):
        master_fd, slave_fd = pty.openpty()
        pid = os.fork()
        if pid == 0:
            try:
                os.close(master_fd)
                for fd in (0, 1, 2):
                    os.dup2(slave_fd, fd)
                os.close(slave_fd)
                os.execve("/bin/sh", ["/bin/sh", "-c", cmd], os.environ)
            except Exception:
                os._exit(1)
        else:
            os.close(slave_fd)
            fl = fcntl.fcntl(master_fd, fcntl.F_GETFL)
            fcntl.fcntl(master_fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
            self._master_fd = master_fd
            self._child_pid = pid

    def set_size(self, rows: int, cols: int):
        if self._master_fd is not None:
            try:
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)
            except OSError:
                pass

    def write_input(self, data: bytes):
        if self._master_fd is not None:
            try:
                os.write(self._master_fd, data)
            except OSError:
                pass

    async def read_output(self, websocket: WebSocket):
        sent_initial = False
        self.set_size(80, 24)
        try:
            while self._running:
                if self._master_fd is None:
                    await asyncio.sleep(0.1)
                    continue
                try:
                    new_bytes = os.read(self._master_fd, 4096)
                    if new_bytes:
                        prefix = b"\x1b[2J\x1b[H" if not sent_initial else b""
                        sent_initial = True
                        try:
                            await websocket.send_bytes(prefix + new_bytes)
                        except Exception:
                            break
                except BlockingIOError:
                    pass
                except OSError:
                    break
                await asyncio.sleep(0.05)
        except Exception:
            pass

    def close(self):
        self._running = False
        if self._child_pid:
            try:
                os.kill(self._child_pid, signal.SIGTERM)
                os.waitpid(self._child_pid, 0)
            except OSError:
                pass
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None


def _verify_ws_token(token: str) -> bool:
    return token == SERVER_TOKEN


@app.get("/api/status")
def api_status(request: Request):
    _verify_token(request)
    from . import __version__
    return {"status": "ok", "version": __version__, "token_valid": True}


@app.get("/api/archives")
def api_archives(request: Request):
    _verify_token(request)
    archives = archive_cmds.list_archives()
    result = []
    for a in archives:
        dirs = directory_cmds.list_directories(archive_id=a.id)
        total_tasks = 0
        done_tasks = 0
        for d in dirs:
            tasks = task_cmds.list_tasks(directory_id=d.id)
            total_tasks += len(tasks)
            done_tasks += sum(1 for t in tasks if t.finished)
        result.append({
            "id": a.id,
            "name": a.name,
            "directory_count": len(dirs),
            "task_count": total_tasks,
            "task_done": done_tasks,
            "progress_pct": round(done_tasks / total_tasks * 100, 1) if total_tasks else 0,
        })
    return result


@app.get("/api/directories")
def api_directories(request: Request, archive_id: int | None = Query(None)):
    _verify_token(request)
    dirs = directory_cmds.list_directories(archive_id=archive_id)
    result = []
    for d in dirs:
        tasks = task_cmds.list_tasks(directory_id=d.id)
        total = len(tasks)
        done = sum(1 for t in tasks if t.finished)
        arch = archive_cmds.get_archive(d.archive_id)
        result.append({
            "id": d.id,
            "name": d.name,
            "archive_id": d.archive_id,
            "archive_name": arch.name if arch else "",
            "project_path": d.project_path or "",
            "level": d.level,
            "xp": d.xp,
            "task_count": total,
            "task_done": done,
            "progress_pct": round(done / total * 100, 1) if total else 0,
        })
    return result


@app.get("/api/directories/{dir_id}")
def api_directory_detail(dir_id: int, request: Request):
    _verify_token(request)
    d = directory_cmds.get_directory(dir_id)
    if not d:
        raise HTTPException(status_code=404, detail="Directory not found")
    arch = archive_cmds.get_archive(d.archive_id)
    tasks = task_cmds.list_tasks(directory_id=d.id)
    total = len(tasks)
    done = sum(1 for t in tasks if t.finished)
    return {
        "id": d.id,
        "name": d.name,
        "archive_name": arch.name if arch else "",
        "project_path": d.project_path or "",
        "level": d.level,
        "xp": d.xp,
        "task_count": total,
        "task_done": done,
        "progress_pct": round(done / total * 100, 1) if total else 0,
    }


@app.get("/api/directories/{dir_id}/tasks")
def api_directory_tasks(
    dir_id: int,
    request: Request,
    finished: str | None = Query(None),
    unfinished: str | None = Query(None),
):
    _verify_token(request)
    d = directory_cmds.get_directory(dir_id)
    if not d:
        raise HTTPException(status_code=404, detail="Directory not found")
    tasks = task_cmds.list_tasks(directory_id=dir_id)
    if finished is not None:
        tasks = [t for t in tasks if t.finished]
    if unfinished is not None:
        tasks = [t for t in tasks if not t.finished]
    result = []
    for t in tasks:
        tags_str = task_cmds.get_tags_for_task_display(t.id)
        tags = [s.strip() for s in tags_str.split(",")] if tags_str else []
        obj = {
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "finished": bool(t.finished),
            "deadline": t.deadline,
            "urgency": t.urgency,
            "difficulty": t.difficulty,
            "pinned": bool(t.pinned),
            "time_dedicated": t.time_dedicated,
            "tags": tags,
            "finished_date": t.finished_date,
            "project_path": d.project_path or "",
        }
        result.append(obj)
    return result


@app.get("/api/tasks/{task_id}")
def api_task_detail(task_id: int, request: Request):
    _verify_token(request)
    tasks = task_cmds.list_tasks()
    task = next((t for t in tasks if t.id == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    tags_str = task_cmds.get_tags_for_task_display(task.id)
    tags = [s.strip() for s in tags_str.split(",")] if tags_str else []
    notes = note_cmds.list_notes(task_id=task.id)
    dir_obj = directory_cmds.get_directory(task.directory_id)
    project_path = dir_obj.project_path if dir_obj else ""
    return {
        "id": task.id,
        "name": task.name,
        "description": task.description,
        "finished": bool(task.finished),
        "deadline": task.deadline,
        "urgency": task.urgency,
        "difficulty": task.difficulty,
        "pinned": bool(task.pinned),
        "time_dedicated": task.time_dedicated,
        "tags": tags,
        "finished_date": task.finished_date,
        "project_path": project_path,
        "notes": [
            {"id": n.id, "date": n.date, "note": n.note, "file_path": n.file_path}
            for n in notes
        ],
    }


@app.get("/api/tasks/{task_id}/notes")
def api_task_notes(task_id: int, request: Request):
    _verify_token(request)
    notes = note_cmds.list_notes(task_id=task_id)
    return [
        {"id": n.id, "date": n.date, "note": n.note, "file_path": n.file_path}
        for n in notes
    ]


@app.post("/api/tasks/{task_id}/opencode")
def api_task_opencode(task_id: int, request: Request):
    _verify_token(request)
    tasks = task_cmds.list_tasks()
    task = next((t for t in tasks if t.id == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    opencode_path = _find_opencode()
    if not opencode_path:
        raise HTTPException(status_code=400, detail="opencode not installed")

    dir_obj = directory_cmds.get_directory(task.directory_id)
    if not dir_obj or not dir_obj.project_path:
        raise HTTPException(
            status_code=400,
            detail="Task directory has no attached project. Use :attachProject <path> first.",
        )
    project_root = dir_obj.project_path

    session_name = f"tw-ai-{task_id}"
    if session_name in _sessions and _sessions[session_name]._running:
        return {"status": "ok", "session_id": session_name, "reused": True}

    session = PTYSession(session_name)
    cmd = f"cd {project_root} && {opencode_path} --dir {project_root}"
    session.start(cmd)
    _sessions[session_name] = session
    return {"status": "ok", "session_id": session_name}


@app.post("/api/directories/{dir_id}/opencode")
def api_directory_opencode(dir_id: int, request: Request):
    _verify_token(request)
    opencode_path = _find_opencode()
    if not opencode_path:
        raise HTTPException(status_code=400, detail="opencode not installed")
    dir_obj = directory_cmds.get_directory(dir_id)
    if not dir_obj or not dir_obj.project_path:
        raise HTTPException(
            status_code=400,
            detail="Directory has no attached project. Use :attachProject <path> first.",
        )
    project_root = dir_obj.project_path
    session_name = f"tw-ai-dir-{dir_id}"
    if session_name in _sessions and _sessions[session_name]._running:
        return {"status": "ok", "session_id": session_name, "reused": True}
    session = PTYSession(session_name)
    cmd = f"cd {project_root} && {opencode_path} --dir {project_root}"
    session.start(cmd)
    _sessions[session_name] = session
    return {"status": "ok", "session_id": session_name}


_ACTIVE_CONVOS: dict[str, str] = {}  # "task_42" → "running" | "done"


async def _opencode_stream(cmd: list[str], cwd: str, extra_env: dict | None = None, convo_key: tuple[str, int] | None = None):
    """Async generator that runs an opencode subprocess and yields SSE lines."""
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def reader():
        proc_env = os.environ.copy()
        if extra_env:
            proc_env.update(extra_env)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            env=proc_env,
        )
        try:
            for line in proc.stdout:
                if convo_key:
                    stripped = line.rstrip()
                    if stripped:
                        try:
                            evt = json.loads(stripped)
                            part = evt.get("part")
                            if evt.get("type") == "text" and isinstance(part, dict):
                                text = part.get("text")
                                if isinstance(text, str) and text:
                                    _append_convo(convo_key[0], convo_key[1], {"role": "ai", "text": text, "session_id": evt.get("sessionID", "")})
                        except json.JSONDecodeError:
                            if stripped != "[DONE]":
                                _append_convo(convo_key[0], convo_key[1], {"role": "ai", "text": stripped})
                loop.call_soon_threadsafe(queue.put_nowait, ("data", line))
            proc.wait()
        except Exception:
            pass
        finally:
            if convo_key:
                _ACTIVE_CONVOS[f"{convo_key[0]}_{convo_key[1]}"] = "done"
            loop.call_soon_threadsafe(queue.put_nowait, ("done", None))

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()

    try:
        while True:
            kind, data = await queue.get()
            if kind == "done":
                yield "data: [DONE]\n\n"
                break
            if data:
                yield f"data: {data.rstrip()}\n\n"
    finally:
        thread.join(timeout=2)


@app.post("/api/tasks/{task_id}/opencode/prompt")
async def api_task_opencode_prompt(task_id: int, request: Request):
    _verify_token(request)
    tasks = task_cmds.list_tasks()
    task = next((t for t in tasks if t.id == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    opencode_path = _find_opencode()
    if not opencode_path:
        raise HTTPException(status_code=400, detail="opencode not installed")

    dir_obj = directory_cmds.get_directory(task.directory_id)
    if not dir_obj or not dir_obj.project_path:
        raise HTTPException(
            status_code=400,
            detail="Task directory has no attached project. Use :attachProject <path> first.",
        )

    body = await request.json()
    prompt = (body or {}).get("prompt", "")
    command = (body or {}).get("command", "")
    if not prompt and not command:
        raise HTTPException(status_code=400, detail="Prompt or command is required")

    _append_convo("task", task_id, {"role": "user", "text": prompt if prompt else "/" + command})

    agent = (body or {}).get("agent", "build")
    config = (body or {}).get("config", "default")
    session_id = (body or {}).get("session_id", "")

    cmd = [opencode_path, "run", "--agent", agent, "--format", "json", "--dir", dir_obj.project_path]
    if session_id:
        cmd += ["--session", session_id]
    if command:
        cmd += ["--command", command]
    if prompt:
        cmd += [prompt]

    extra_env = None
    if config and config != "default":
        cfg_path = os.path.expanduser(f"~/.config/opencode/configs/{config}.json")
        if os.path.exists(cfg_path):
            extra_env = {"OPENCODE_CONFIG": cfg_path}

    _ACTIVE_CONVOS[f"task_{task_id}"] = "running"
    return StreamingResponse(_opencode_stream(cmd, dir_obj.project_path, extra_env, convo_key=("task", task_id)), media_type="text/event-stream")


@app.post("/api/directories/{dir_id}/opencode/prompt")
async def api_directory_opencode_prompt(dir_id: int, request: Request):
    _verify_token(request)
    opencode_path = _find_opencode()
    if not opencode_path:
        raise HTTPException(status_code=400, detail="opencode not installed")
    dir_obj = directory_cmds.get_directory(dir_id)
    if not dir_obj or not dir_obj.project_path:
        raise HTTPException(
            status_code=400,
            detail="Directory has no attached project. Use :attachProject <path> first.",
        )
    body = await request.json()
    prompt = (body or {}).get("prompt", "")
    command = (body or {}).get("command", "")
    if not prompt and not command:
        raise HTTPException(status_code=400, detail="Prompt or command is required")

    _append_convo("dir", dir_id, {"role": "user", "text": prompt if prompt else "/" + command})

    agent = (body or {}).get("agent", "build")
    config = (body or {}).get("config", "default")
    session_id = (body or {}).get("session_id", "")
    cmd = [opencode_path, "run", "--agent", agent, "--format", "json", "--dir", dir_obj.project_path]
    if session_id:
        cmd += ["--session", session_id]
    if command:
        cmd += ["--command", command]
    if prompt:
        cmd += [prompt]
    extra_env = None
    if config and config != "default":
        cfg_path = os.path.expanduser(f"~/.config/opencode/configs/{config}.json")
        if os.path.exists(cfg_path):
            extra_env = {"OPENCODE_CONFIG": cfg_path}
    _ACTIVE_CONVOS[f"dir_{dir_id}"] = "running"
    return StreamingResponse(_opencode_stream(cmd, dir_obj.project_path, extra_env, convo_key=("dir", dir_id)), media_type="text/event-stream")


@app.get("/api/tasks/{task_id}/opencode/convo")
def api_task_opencode_convo(task_id: int, request: Request):
    _verify_token(request)
    try:
        raw = _read_convo("task", task_id)
        merged = []
        for entry in raw:
            if merged and entry.get("role") == "ai" and merged[-1].get("role") == "ai":
                text = entry.get("text", "")
                if isinstance(text, str):
                    prev = merged[-1].get("text")
                    merged[-1]["text"] = (prev if isinstance(prev, str) else "") + text
            else:
                merged.append(dict(entry))
        return merged
    except Exception as e:
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Convo read/merge error: {e}")


@app.delete("/api/tasks/{task_id}/opencode/convo")
def api_task_opencode_clear_convo(task_id: int, request: Request):
    _verify_token(request)
    _clear_convo("task", task_id)
    return {"status": "ok"}


@app.get("/api/directories/{dir_id}/opencode/convo")
def api_directory_opencode_convo(dir_id: int, request: Request):
    _verify_token(request)
    try:
        raw = _read_convo("dir", dir_id)
        merged = []
        for entry in raw:
            if merged and entry.get("role") == "ai" and merged[-1].get("role") == "ai":
                text = entry.get("text", "")
                if isinstance(text, str):
                    prev = merged[-1].get("text")
                    merged[-1]["text"] = (prev if isinstance(prev, str) else "") + text
            else:
                merged.append(dict(entry))
        return merged
    except Exception as e:
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Convo read/merge error: {e}")


@app.delete("/api/directories/{dir_id}/opencode/convo")
def api_directory_opencode_clear_convo(dir_id: int, request: Request):
    _verify_token(request)
    _clear_convo("dir", dir_id)
    return {"status": "ok"}


@app.get("/api/ai-convos/status")
def api_ai_convos_status(request: Request):
    _verify_token(request)
    return dict(_ACTIVE_CONVOS)


@app.post("/api/ai-convos/{key}/dismiss")
def api_ai_convo_dismiss(key: str, request: Request):
    _verify_token(request)
    _ACTIVE_CONVOS.pop(key, None)
    return {"status": "ok"}


@app.websocket("/ws/terminal/{session_id}")
async def terminal_ws(websocket: WebSocket, session_id: str, token: str = Query("")):
    if not _verify_ws_token(token):
        await websocket.close(1008, "Unauthorized")
        return
    session = _sessions.get(session_id)
    if not session:
        await websocket.close(1008, "Session not found")
        return

    await websocket.accept()

    async def reader():
        await session.read_output(websocket)

    async def writer():
        try:
            while True:
                data = await websocket.receive_bytes()
                session.write_input(data)
        except WebSocketDisconnect:
            pass
        except Exception:
            pass

    reader_task = asyncio.create_task(reader())
    writer_task = asyncio.create_task(writer())
    done, pending = await asyncio.wait(
        [reader_task, writer_task], return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
    session.close()
    if session_id in _sessions:
        del _sessions[session_id]


@app.get("/api/stats")
def api_stats(request: Request):
    _verify_token(request)
    return stats_cmds.compute_stats()


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/", response_class=HTMLResponse)
def web_ui(request: Request):
    _verify_token(request)
    return INDEX_HTML


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="theme-color" content="#0f0f12">
<title>TaskWatch+</title>
<style>
:root{--bg:#0a0a0f;--card:#121218;--border:#1e1e2e;--text:#d4d4dc;--text2:#8a8a9a;--text3:#484858;--accent:#e53935;--accent2:#ef5350;--urgent:#ff8c00;--danger:#b71c1c;--success:#00e676;--radius:0}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
html,body{height:100%;overflow:hidden}
body{font-family:ui-monospace,'SF Mono','JetBrains Mono','Fira Code','Cascadia Code',Menlo,Consolas,monospace;background:var(--bg);color:var(--text)}
#app{display:flex;flex-direction:column;height:100%}
#header{flex-shrink:0;padding:12px 16px 0;background:var(--bg);z-index:10}
#top-row{display:flex;align-items:center;justify-content:space-between;margin-bottom:2px}
#top-row h1{font-size:.88rem;font-weight:700;color:var(--accent);text-shadow:0 0 12px rgba(229,57,53,.5);line-height:1.4;letter-spacing:.06em}
#sbtn{width:34px;height:34px;border-radius:0;border:1px solid var(--border);background:var(--card);color:var(--text2);font-size:.82rem;cursor:pointer;display:flex;align-items:center;justify-content:center}
#sbtn:active{background:var(--border)}
#sbar{display:none;margin-bottom:6px}
#sbar.open{display:flex;gap:4px}
#sbar input{flex:1;padding:9px 12px;border-radius:0;border:1px solid var(--border);background:var(--card);color:var(--text);font-size:.82rem;outline:none;font-family:inherit}
#sbar input:focus{border-color:var(--accent)}
#bc{font-size:.7rem;color:var(--text3);padding:6px 0 8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex-shrink:0;line-height:1.3}
#bc .b{color:var(--accent);cursor:pointer;padding:4px 0;display:inline}
#bc .b:active{opacity:.7}
#bc .s{margin:0 4px;color:var(--text3)}
#main{flex:1;overflow-y:auto;padding:0 16px 12px;-webkit-overflow-scrolling:touch}
#main .bk{display:flex;align-items:center;gap:4px;color:var(--accent);cursor:pointer;font-size:.82rem;padding:8px 0;margin-bottom:2px;font-weight:500}
#main .bk:active{opacity:.7}
.c{background:var(--card);border-radius:0;padding:14px;margin-bottom:10px;cursor:pointer;box-shadow:0 0 12px rgba(0,0,0,.5),0 0 4px rgba(229,57,53,.06);border:1px solid var(--border);transition:transform .12s,border-color .12s,box-shadow .12s;animation:up .2s ease-out}
.c:active{transform:scale(.97)}
.ca{border-color:var(--accent);box-shadow:0 0 12px rgba(0,0,0,.5),0 0 6px rgba(229,57,53,.12)}
.cd{border-color:var(--success);box-shadow:0 0 12px rgba(0,0,0,.5),0 0 6px rgba(0,230,118,.08)}
.ct{border-color:var(--urgent);box-shadow:0 0 12px rgba(0,0,0,.5),0 0 6px rgba(255,140,0,.08)}
.cx{cursor:default}
.cx:active{transform:none}
.ch{display:flex;justify-content:space-between;align-items:flex-start;gap:8px;margin-bottom:6px;padding-bottom:6px;border-bottom:1px solid var(--border)}
.cn{font-weight:600;font-size:.92rem;word-break:break-word;line-height:1.3}
.cn.pd::before{content:'*';margin-right:4px;font-size:.8rem}
.mt{font-size:.76rem;color:var(--text2);margin-top:5px;line-height:1.4}
.mt span{margin-right:10px}
.bw{display:flex;gap:4px;flex-shrink:0;flex-wrap:wrap;justify-content:flex-end}
.b{padding:2px 6px;border-radius:0;font-size:.62rem;font-weight:700;letter-spacing:.02em;white-space:nowrap;border:1px solid transparent;background:transparent;font-family:inherit}
.bu1{border-color:rgba(138,138,154,.3);color:var(--text3)}.bu2{border-color:rgba(239,83,80,.4);color:#ef5350}.bu3{border-color:rgba(229,57,53,.5);color:#e53935}.bu4{border-color:rgba(183,28,28,.5);color:#b71c1c}.bu5{border-color:#b71c1c;color:#ff1744;border-width:2px}
.bd1{border-color:rgba(138,138,154,.3);color:var(--text3)}.bd2{border-color:rgba(0,188,212,.4);color:#00bcd4}.bd3{border-color:rgba(0,188,212,.5);color:#00bcd4}.bd4{border-color:rgba(156,39,176,.4);color:#9c27b0}.bd5{border-color:rgba(156,39,176,.6);color:#9c27b0}
.bl{border-color:rgba(239,83,80,.4);color:#ef5350}
.ba{border-color:rgba(229,57,53,.4);color:#e53935}
.tgs{margin-top:5px;display:flex;flex-wrap:wrap;gap:4px}
.tg{padding:0 1px;border-radius:0;font-size:.68rem;background:transparent;color:rgba(229,57,53,.6);border:none;font-family:inherit}.tg::before{content:'['}.tg::after{content:']'}
.sk{animation:up .2s ease-out}
.sk-l{height:13px;border-radius:0;background:var(--border);margin-bottom:8px;animation:pl 1.4s ease-in-out infinite}
.sk-l:last-child{margin-bottom:0;width:55%}
.sk-w70{width:70%}.sk-w50{width:50%}.sk-w40{width:40%}
@keyframes up{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
@keyframes pl{0%,100%{opacity:.4}50%{opacity:1}}
.notes{margin-top:14px}
.notes h2{font-size:.82rem;color:var(--text2);margin-bottom:8px;font-weight:600}
.no{position:relative;padding:0 0 10px 18px;margin-left:5px;border-left:2px solid var(--border)}
.no:last-child{border-left-color:transparent}
.no::before{content:'';position:absolute;left:-5px;top:3px;width:7px;height:7px;border-radius:0;background:var(--accent2);border:2px solid var(--bg)}
.no .nd{font-size:.63rem;color:var(--text3);margin-bottom:2px}
.no .nt{font-size:.8rem;color:var(--text);line-height:1.4;white-space:pre-wrap}
.no .nf{font-size:.68rem;color:var(--text3);margin-top:3px}
.desc{margin-top:10px;padding:10px 12px;border-radius:0;background:rgba(229,57,53,.02);border:1px solid var(--border);font-size:.8rem;color:var(--text);line-height:1.5;white-space:pre-wrap;font-family:inherit}
.emp{text-align:center;padding:50px 20px;color:var(--text3);font-size:.85rem;line-height:1.6}
.err{text-align:center;padding:30px 20px;color:var(--danger);font-size:.85rem}
.fb{display:flex;gap:6px;margin-bottom:10px;overflow-x:auto;padding-bottom:2px;-webkit-overflow-scrolling:touch}
.fb button{flex-shrink:0;padding:6px 13px;border-radius:0;font-size:.72rem;font-weight:600;border:1px solid var(--border);background:transparent;color:var(--text3);cursor:pointer;transition:all .12s;font-family:inherit}
.fb button.act{background:var(--accent);color:#fff;border-color:var(--accent)}
.fb button:active{opacity:.8}
#bn{flex-shrink:0;display:flex;border-top:1px solid var(--border);background:var(--card);padding:0 0 env(safe-area-inset-bottom,4px)}
#bn button{flex:1;display:flex;flex-direction:column;align-items:center;gap:1px;padding:7px 0 5px;border:none;background:transparent;color:var(--text3);font-size:.58rem;cursor:pointer;transition:color .12s,border-color .12s;line-height:1.2;border-top:2px solid transparent;font-family:inherit}
#bn button.act{color:var(--accent);border-top-color:var(--accent);text-shadow:0 0 6px rgba(229,57,53,.4)}
#bn button .ni{font-size:.82rem;letter-spacing:.02em}
.pb{height:4px;background:var(--border);border-radius:0;margin-top:7px;overflow:hidden}
.pf{height:100%;border-radius:0;background:linear-gradient(90deg,var(--accent),var(--accent2));transition:width .3s}
.pt{font-size:.7rem;color:var(--text3);margin-top:3px;display:flex;justify-content:space-between}
#main::-webkit-scrollbar{width:3px}
#main::-webkit-scrollbar-thumb{background:var(--border);border-radius:0}
#tp{display:none;position:fixed;bottom:0;left:0;right:0;height:65vh;z-index:200;background:var(--bg);border-top:1px solid var(--accent);flex-direction:column;animation:up .2s ease-out;box-shadow:0 0 20px rgba(229,57,53,.2)}
#tp.open{display:flex}
#tph{display:flex;justify-content:space-between;align-items:center;padding:8px 12px;background:var(--card);border-bottom:1px solid var(--border);flex-shrink:0}
#tph span{font-size:.7rem;color:var(--accent);letter-spacing:.03em}
#tpc{background:none;border:none;color:var(--text2);font-size:.8rem;cursor:pointer;font-family:inherit;padding:4px 8px}
#tpc:active{color:var(--accent)}
#tx{flex:1;overflow:hidden;padding:2px}
.pc{margin-top:16px;background:var(--card);border:1px solid var(--border);padding:12px}
.pch{font-size:.7rem;color:var(--accent);letter-spacing:.03em;margin-bottom:8px;font-weight:600;display:flex;justify-content:space-between}
.pnc{background:none;border:none;color:var(--text3);font-size:.7rem;cursor:pointer;font-family:inherit;padding:0;line-height:1}
.pnc:hover{color:var(--accent)}
.pct{display:flex;gap:6px;margin-bottom:8px}
.ps{background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:.72rem;padding:4px 8px;outline:none}
.ps:focus{border-color:var(--accent)}
.pi{flex:1}
.pconv{max-height:300px;overflow-y:auto;margin-bottom:8px;display:flex;flex-direction:column;gap:6px}
.pm{padding:6px 10px;font-size:.75rem;line-height:1.4;max-width:90%;word-break:break-word;white-space:pre-wrap;border:1px solid var(--border)}
.pmu{margin-left:auto;background:rgba(229,57,53,.08);border-color:var(--accent);color:var(--text)}
.pma{margin-right:auto;background:var(--bg);color:var(--text2)}
.pme{margin:0 auto;color:var(--danger);font-size:.7rem;text-align:center}
.pin{display:flex;gap:6px;align-items:flex-end}
.pta{flex:1;background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:.75rem;padding:6px 8px;outline:none;resize:none;min-height:32px;line-height:1.4}
.pta:focus{border-color:var(--accent)}
.psb{flex-shrink:0;padding:6px 12px;border-radius:0;font-size:.72rem;font-weight:600;border:1px solid var(--accent);background:transparent;color:var(--accent);cursor:pointer;font-family:inherit;transition:all .12s;height:32px}
.psb:active{background:var(--accent);color:#fff}
.psb:disabled{opacity:.4;cursor:default}
.pma.thinking:after{content:'█';animation:bl 1s step-end infinite;color:var(--accent);margin-left:2px}
@keyframes bl{50%{opacity:0}}
.ptog{background:none;border:none;color:var(--text3);font-size:.7rem;cursor:pointer;font-family:inherit;padding:0;line-height:1}
.ptog:hover{color:var(--accent)}.ptog.on{color:var(--accent);font-weight:700}
.agent-toggle{display:inline-flex;border:1px solid var(--border);overflow:hidden;line-height:1}.at-btn{padding:3px 10px;font-size:.65rem;font-weight:700;letter-spacing:.04em;border:none;background:transparent;color:var(--text3);cursor:pointer;font-family:inherit;transition:all .12s;-webkit-tap-highlight-color:transparent}.at-btn.active{background:var(--accent);color:#fff}.model-label{font-size:.65rem;color:var(--text2);padding:4px 6px;white-space:nowrap;line-height:1}
.pin{position:relative}.cmdmenu{display:none;position:absolute;bottom:100%;left:0;right:0;background:var(--card);border:1px solid var(--border);max-height:160px;overflow-y:auto;z-index:10}.cmdmenu.open{display:block}.cmdmi{padding:6px 10px;font-size:.72rem;color:var(--text2);cursor:pointer;border-bottom:1px solid var(--border);font-family:inherit}.cmdmi:last-child{border-bottom:none}.cmdmi:hover{background:var(--border);color:var(--text)}
@media(max-width:600px){
  .pm{max-width:100%}
  .pconv{max-height:45vh}
  .pct{flex-direction:column}
  .pta{font-size:16px}
  .psb{height:44px;padding:10px 16px}
  .pc{padding:8px}
  .pnc{padding:8px}
  .ai-dir{padding:6px 10px}
  #main{overscroll-behavior:contain}
}
</style>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm@5.5.0/css/xterm.min.css">
</head>
<body>
<div id="app">
  <div id="header">
    <div id="top-row"><h1>[ TASKWATCH+ ]</h1><button id="sbtn" onclick="togS()">[S]</button></div>
    <div id="sbar"><input id="si" placeholder="Filter&hellip;" oninput="onS()"></div>
    <div id="bc"></div>
  </div>
  <div id="main"></div>
    <div id="bn">
        <button class="act" onclick="navTo(this);showArchives()"><span class="ni">[A]</span><span>Archives</span></button>
        <button onclick="navTo(this);showStats()"><span class="ni">[S]</span><span>Stats</span></button>
        <button onclick="navTo(this);showAFK()"><span class="ni" id="afkbtn">[AFK]</span><span>AFK</span></button>
        <button onclick="navTo(this);document.getElementById('main').scrollTop=0"><span class="ni">[^]</span><span>Top</span></button>
      </div>
  </div>
<div id="tp">
  <div id="tph"><span>[AI] opencode</span><button id="tpc" onclick="closeTerminal()">[X]</button></div>
  <div id="tx"></div>
</div>
<script src="https://cdn.jsdelivr.net/npm/xterm@5.5.0/lib/xterm.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.min.js"></script>
<script>
var T=(new URLSearchParams(location.search).get('token')||localStorage.getItem('tw_token')||'');
if(!localStorage.getItem('tw_token')&&T)localStorage.setItem('tw_token',T);
var NAV={},BC=document.getElementById('bc'),_TC=[],_PC=[],_DPC=[],_AF='all',_SID='',_DSID='',_PLAN=false;
var _MODELS={default:{plan:'opencode-default',build:'opencode-default'},free:{plan:'deepseek-v4-flash-free',build:'deepseek-v4-flash-free'},light:{plan:'deepseek-v4-pro',build:'deepseek-v4-flash'},medium:{plan:'qwen3.7-plus',build:'minimax-m3'},hard:{plan:'glm-5.1',build:'qwen3.7-max'}};
var _CMDS=['taskwatch-attach','taskwatch-plan','taskwatch-next','taskwatch-review','done','compact'];
var _PBUSY=false,_PABORT=null,_PQ=[],_DPBUSY=false,_DPABORT=null,_DPQ=[];
setTimeout(function updateBadge(){updateAFKBadge();setTimeout(updateBadge,30000)},1000);
function api(p){var s=p.indexOf('?')>=0?'&':'?';return fetch('/api'+p+(T?s+'token='+T:''),{headers:{Accept:'application/json'}}).then(function(r){if(!r.ok)return r.json().then(function(e){throw Error(r.status+' '+r.statusText+': '+(e.detail||JSON.stringify(e)))}).catch(function(){throw Error(r.status+' '+r.statusText)});return r.json()})}
function esc(s){if(!s)return'';var d=document.createElement('div');d.textContent=s;return d.innerHTML}
function qj(s){return s.replace(/'/g,"\\'")}
function loadTaskConvo(id){api('/tasks/'+id+'/opencode/convo').then(function(msgs){if(!msgs||!msgs.length)return;_PC=msgs;for(var i=_PC.length-1;i>=0;i--){if(_PC[i].session_id){_SID=_PC[i].session_id;break}}renderConv()}).catch(function(e){console.error('loadTaskConvo',e);var el=document.getElementById('pconv');if(el)el.innerHTML='<div class="pme">Load failed: '+esc(e.message)+'</div>'})}
function loadDirConvo(id){api('/directories/'+id+'/opencode/convo').then(function(msgs){if(!msgs||!msgs.length)return;_DPC=msgs;for(var i=_DPC.length-1;i>=0;i--){if(_DPC[i].session_id){_DSID=_DPC[i].session_id;break}}renderDirConv()}).catch(function(e){console.error('loadDirConvo',e);var el=document.getElementById('dpconv');if(el)el.innerHTML='<div class="pme">Load failed: '+esc(e.message)+'</div>'})}
function uB(n){return'<span class="b bu'+n+'">U'+n+'</span>'}
function dB(n){return'<span class="b bd'+n+'">D'+n+'</span>'}
function pRing(p){var c=Math.PI*36,o=c*(1-Math.min(p,100)/100);return'<svg width="42" height="42" viewBox="0 0 44 44"><circle cx="22" cy="22" r="18" fill="none" stroke="var(--border)" stroke-width="4"/><circle cx="22" cy="22" r="18" fill="none" stroke="var(--accent)" stroke-width="4" stroke-linecap="round" transform="rotate(-90 22 22)" stroke-dasharray="'+c+'" stroke-dashoffset="'+o+'"/><text x="22" y="22" text-anchor="middle" dominant-baseline="central" fill="var(--text2)" font-size="11" font-weight="700">'+Math.round(p)+'%</text></svg>'}
function sk(n){var h='',i=0;for(;i<n;i++){var w=i%2?'sk-w70':'sk-w50';h+='<div class="sk"><div class="sk-l"></div><div class="sk-l"></div><div class="sk-l '+w+'"></div></div>'}document.getElementById('main').innerHTML=h}
function setBC(h){BC.innerHTML='<span class="b" onclick="showArchives()">Archives</span>'+h}
function gM(){return document.getElementById('main')}
function navTo(el){document.querySelectorAll('#bn button').forEach(function(b){b.classList.remove('act')});if(el)el.classList.add('act')}
function setAgent(el,agent){var tog=el.parentElement;tog.querySelectorAll('.at-btn.active').forEach(function(b){b.classList.remove('active')});el.classList.add('active');_PLAN=(agent==='plan');updateModelDisplay()}
function updateModelDisplay(){var pm=document.getElementById('pm')||document.getElementById('dpm');var config=pm?pm.value:'default';var at=document.querySelector('.at-btn.active');var agent=at?at.dataset.agent:'build';var m=_MODELS[config];var name=m?(m[agent]||config):config;var ml=document.getElementById('pml')||document.getElementById('dpml');if(ml)ml.textContent=name}
function onCmdInput(el,menuId){var menu=document.getElementById(menuId);if(!menu)return;var v=el.value.trim();if(v.startsWith('/')&&v.indexOf(' ')===-1){var q=v.slice(1).toLowerCase();var hits=_CMDS.filter(function(c){return c.toLowerCase().indexOf(q)>=0});menu.innerHTML='';hits.forEach(function(c){var mi=document.createElement('div');mi.className='cmdmi';mi.textContent='/'+c;mi.onclick=function(){insertCmd(c,el.id)};menu.appendChild(mi)});menu.classList.add('open')}else{menu.classList.remove('open')}}
function insertCmd(cmd,inputId){var ta=document.getElementById(inputId);if(!ta)return;ta.value='/'+cmd;ta.style.height='';ta.style.height=Math.min(ta.scrollHeight,80)+'px';var menu=ta.parentElement.querySelector('.cmdmenu');if(menu)menu.classList.remove('open');ta.focus()}
function onTaskBtnClick(id){if(_PBUSY){cancelTask()}else{sendPrompt(id)}}
function onDirBtnClick(id){if(_DPBUSY){cancelDir()}else{sendDirPrompt(id)}}
function cancelTask(){if(_PABORT){_PABORT.abort();_PABORT=null}}
function cancelDir(){if(_DPABORT){_DPABORT.abort();_DPABORT=null}}
function dequeueTask(){_PBUSY=false;_PABORT=null;if(_PQ.length>0){var n=_PQ.shift();_doSendTask(n.raw,n.id)}else{updateTaskBtn()}}
function dequeueDir(){_DPBUSY=false;_DPABORT=null;if(_DPQ.length>0){var n=_DPQ.shift();_doSendDir(n.raw,n.id)}else{updateDirBtn()}}
function updateTaskBtn(){var btn=document.getElementById('psb');if(!btn)return;btn.textContent=_PQ.length>0?'■ ('+_PQ.length+')':'▸'}
function updateDirBtn(){var btn=document.getElementById('dsb');if(!btn)return;btn.textContent=_DPQ.length>0?'■ ('+_DPQ.length+')':'▸'}
if(window.visualViewport)window.visualViewport.addEventListener('resize',function(){var el=document.querySelector('.pin');if(el&&window.visualViewport.height<window.innerHeight)setTimeout(function(){el.scrollIntoView({behavior:'smooth',block:'nearest'})},300)});
function togS(){var e=document.getElementById('sbar');e.classList.toggle('open');if(!e.classList.contains('open'))document.getElementById('si').value=''}
function onS(){if(NAV.level=='tasks'&&_TC.length)renderTasks(_TC)}
function renderTasks(arr){
  var q=document.getElementById('si').value.toLowerCase().trim();
  var c=gM(),h='<span class="bk" onclick="showDirs('+NAV.archId+',\''+qj(esc(NAV.archName))+'\')">&lt; '+esc(NAV.archName)+'</span>';
  var flt='<div class="fb">'+(NAV.dirProjectPath?'<span class="b ba ai-dir" onclick="showDirAI('+NAV.dirId+')" style="cursor:pointer;margin-right:8px">[AI]</span>':'')+'<button onclick="chF(this,\'all\')" class="act">All</button><button onclick="chF(this,\'unf\')">Unfinished</button><button onclick="chF(this,\'urg\')">Urgent</button><button onclick="chF(this,\'pin\')">Pinned</button></div>';
  var tasks=arr;
  if(_AF=='unf')tasks=tasks.filter(function(t){return!t.done});
  else if(_AF=='urg')tasks=tasks.filter(function(t){return t.urgency>=4});
  else if(_AF=='pin')tasks=tasks.filter(function(t){return t.pinned});
  if(q){tasks=tasks.filter(function(t){return t.name.toLowerCase().indexOf(q)>=0})}
  if(!tasks.length){c.innerHTML=flt+'<div class="emp">No tasks match</div>';return}
  tasks.forEach(function(t){
    h+='<div class="c ct" onclick="showTask('+t.id+',\''+qj(esc(t.name))+'\','+NAV.dirId+',\''+qj(esc(NAV.dirName))+'\','+NAV.archId+',\''+qj(esc(NAV.archName))+'\')">'
      +'<div class="ch"><span class="cn'+(t.pinned?' pd':'')+'">'+esc(t.name)+'</span>'
      +'<span class="bw">'+uB(t.urgency)+' '+dB(t.difficulty)+'</span></div>'
      +(t.tags&&t.tags.length?'<div class="tgs">'+t.tags.map(function(tg){return'<span class="tg">'+esc(tg)+'</span>'}).join('')+'</div>':'')
      +'<div class="mt"><span>'+t.time_dedicated+'m</span>'+(t.deadline!=='none'?'<span>due: '+esc(t.deadline)+'</span>':'')+'</div>'
      +'</div>';
  });
  c.innerHTML=flt+h;
}
function chF(el,m){_AF=m;document.querySelectorAll('.fb .act').forEach(function(b){b.classList.remove('act')});el.classList.add('act');if(_TC.length)renderTasks(_TC)}
function showArchives(){
  NAV={level:'archives'};_TC=[];setBC('');sk(3);
  api('/archives').then(function(aa){
    var c=gM();
    if(!aa.length){c.innerHTML='<div class="emp">No archives found</div>';return}
    var h='';
    aa.forEach(function(a){
      h+='<div class="c ca" onclick="showDirs('+a.id+',\''+qj(esc(a.name))+'\')">'
        +'<div style="display:flex;align-items:center;gap:12px">'
        +pRing(a.progress_pct)
        +'<div style="flex:1;min-width:0"><div class="ch" style="margin-bottom:0"><span class="cn">'+esc(a.name)+'</span><span class="b ba">'+a.directory_count+'</span></div>'
        +'<div class="mt"><span>'+a.task_done+'/'+a.task_count+' tasks</span></div></div></div>'
        +'</div>';
    });
    c.innerHTML=h;
  }).catch(function(e){gM().innerHTML='<div class="err">Error: '+e.message+'</div>'});
}
function showDirs(archId,archName){
  NAV={level:'dirs',archId:archId,archName:archName};_TC=[];
  setBC('<span class="s">/</span><span>'+esc(archName)+'</span>');
  sk(3);
  api('/directories?archive_id='+archId).then(function(dirs){
    var c=gM();
    if(!dirs.length){c.innerHTML='<div class="bk" onclick="showArchives()">&lt; Archives</div><div class="emp">No directories</div>';return}
    var h='<span class="bk" onclick="showArchives()">&lt; Archives</span>';
    dirs.forEach(function(d){
      h+='<div class="c cd" onclick="showTasks('+d.id+',\''+qj(esc(d.name))+'\','+archId+',\''+qj(esc(archName))+'\')">'
        +'<div class="ch"><span class="cn">'+esc(d.name)+'</span>'
        +(d.level>1?'<span class="b bl">Lv'+d.level+'</span>':'')
        +'</div>'
        +(d.project_path?'<div style="font-size:.7rem;color:var(--text3);margin-top:2px">file: '+esc(d.project_path)+'</div>':'')
        +'<div class="mt"><span>'+d.task_done+'/'+d.task_count+' tasks</span></div>'
        +'<div class="pb"><div class="pf" style="width:'+d.progress_pct+'%"></div></div>'
        +'<div class="pt"><span>'+d.progress_pct+'%</span><span>'+d.task_done+' done</span></div>'
        +'</div>';
    });
    c.innerHTML=h;
  }).catch(function(e){gM().innerHTML='<div class="err">Error: '+e.message+'</div>'});
}
function showTasks(dirId,dirName,archId,archName){
  NAV={level:'tasks',dirId:dirId,dirName:dirName,archId:archId,archName:archName};_AF='unf';_TC=[];
  setBC('<span class="s">/</span><span class="b" onclick="showDirs('+archId+',\''+qj(esc(archName))+'\')">'+esc(archName)+'</span><span class="s">/</span><span>'+esc(dirName)+'</span>');
  sk(3);
  api('/directories/'+dirId+'/tasks').then(function(tasks){
    _TC=tasks;
    NAV.dirProjectPath=tasks.length?tasks[0].project_path||'':'';
    if(!tasks.length){gM().innerHTML='<div class="emp">No tasks</div>';return}
    _AF='all';renderTasks(tasks);
  }).catch(function(e){gM().innerHTML='<div class="err">Error: '+e.message+'</div>'});
}
function showTask(taskId,taskName,dirId,dirName,archId,archName){
  fetch('/api/ai-convos/task_'+taskId+'/dismiss?token='+T,{method:'POST'});
  NAV={level:'task',taskId:taskId,taskName:taskName,dirId:dirId,dirName:dirName,archId:archId,archName:archName};_TC=[];_PC=[];_SID='';
  setBC('<span class="s">/</span><span class="b" onclick="showDirs('+archId+',\''+qj(esc(archName))+'\')">'+esc(archName)+'</span><span class="s">/</span><span class="b" onclick="showTasks('+dirId+',\''+qj(esc(dirName))+'\','+archId+',\''+qj(esc(archName))+'\')">'+esc(dirName)+'</span><span class="s">/</span><span>'+esc(taskName)+'</span>');
  var c=gM();c.innerHTML='<div class="sk"><div class="sk-l sk-w70"></div><div class="sk-l sk-w50"></div><div class="sk-l sk-w40"></div></div>';
  api('/tasks/'+taskId).then(function(t){
    var h='<span class="bk" onclick="showTasks('+dirId+',\''+qj(esc(dirName))+'\','+archId+',\''+qj(esc(archName))+'\')">&lt; '+esc(dirName)+'</span>'
      +'<div class="c cx"><div class="ch"><span class="cn'+(t.pinned?' pd':'')+'">'+esc(t.name)+'</span>'
      +'<span class="bw">'+uB(t.urgency)+' '+dB(t.difficulty)+'</span></div>'
      +'<div class="mt"><span>'+t.time_dedicated+'m</span>'+(t.deadline!=='none'?'<span>due: '+esc(t.deadline)+'</span>':'')+'</div>'
      +(t.tags&&t.tags.length?'<div class="tgs">'+t.tags.map(function(tg){return'<span class="tg">'+esc(tg)+'</span>'}).join('')+'</div>':'')
      +(t.description?'<div class="desc">'+esc(t.description)+'</div>':'')
      +'</div>';
    if(t.notes&&t.notes.length){
      h+='<div class="notes"><h2>Notes ('+t.notes.length+')</h2>';
      t.notes.forEach(function(n){h+='<div class="no"><div class="nd">'+esc(n.date)+'</div><div class="nt">'+esc(n.note)+'</div>'+(n.file_path?'<div class="nf">file: '+esc(n.file_path)+'</div>':'')+'</div>'});
      h+='</div>';
    }
    if(t.project_path){
      var mopts=['default','free','light','medium','hard'];
      var msel='<select id="pm" class="ps" onchange="updateModelDisplay()">';
      mopts.forEach(function(o){msel+='<option value="'+o+'">'+o+'</option>'});
      msel+='</select>';
      h+='<div class="pc"><div class="pch">[AI] prompt <span style="display:flex;align-items:center;gap:6px"><span class="pnc" onclick="newChat()">[↻]</span></span></div>'
        +'<div class="pct"><div class="agent-toggle" id="atog"><button class="at-btn active" data-agent="build" onclick="setAgent(this,\'build\')">BUILD</button><button class="at-btn" data-agent="plan" onclick="setAgent(this,\'plan\')">PLAN</button></div>'+msel+'<span class="model-label" id="pml">opencode-default</span></div>'
        +'<div id="pconv" class="pconv"></div>'
        +'<div class="pin"><div class="cmdmenu" id="cmdm"></div><textarea id="ppt" class="pta" placeholder="Ask opencode..." rows="1" oninput="this.style.height=\'\';this.style.height=Math.min(this.scrollHeight,80)+\'px\';onCmdInput(this,\'cmdm\')" onkeydown="if(event.key===\'Enter\'&&!event.shiftKey){event.preventDefault();onTaskBtnClick('+t.id+')}"></textarea>'
        +'<button class="psb" id="psb" onclick="onTaskBtnClick('+t.id+')">▸</button></div></div>';
    }
    c.innerHTML=h;if(t.project_path)loadTaskConvo(taskId);
  }).catch(function(e){c.innerHTML='<div class="err">Error: '+e.message+'</div>'});
}
function showStats(){
  NAV={level:'stats'};_TC=[];setBC('');sk(3);
  api('/stats').then(function(s){
    var c=gM();
    var h='<div class="c cx"><div class="ch"><span class="cn">Summary</span></div><div class="mt">'
      +'Tasks: '+s.total+' | Done: '+s.finished+'/'+s.total+' | Pending: '+s.pending+'<br>'
      +'Completion: '+s.completion_pct+'% | Overdue: '+s.overdue+'<br>'
      +'Today: '+s.today_completed+' | Week: '+s.completed_this_week+' | Streak: '+s.streak+' days<br>'
      +'Time: '+Math.floor(s.total_time/60)+'h'+s.total_time%60+'m | Focus: '+s.focus_score+' | Tags: '+s.total_tags
      +'</div></div>';
    if(s.deadline_timeline){
      var dt=s.deadline_timeline,mx=0;
      var bs=[['overdue','Overdue','var(--danger)'],['due_today','Due today','var(--urgent)'],['this_week','This week','var(--accent)'],['next_week','Next week','var(--text2)'],['later','Later','var(--text2)'],['no_deadline','None','var(--text3)']];
      bs.forEach(function(b){if(dt[b[0]]>mx)mx=dt[b[0]]});if(mx<1)mx=1;
      h+='<div class="c cx" style="margin-top:12px"><div class="ch"><span class="cn">Deadline Timeline</span></div><div class="mt">';
      bs.forEach(function(b){
        var p=dt[b[0]]/mx*100;
        h+='<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;font-size:.7rem">'
          +'<span style="width:64px;flex-shrink:0;color:var(--text2)">'+b[1]+'</span>'
          +'<div style="flex:1;height:5px;background:var(--border);border-radius:3px;overflow:hidden">'
          +'<div style="height:100%;border-radius:3px;width:'+p+'%;background:'+b[2]+'"></div></div>'
          +'<span style="width:20px;text-align:right;color:var(--text2)">'+dt[b[0]]+'</span></div>';
      });
      h+='</div></div>';
    }
    if(s.ud_grid){
      var gl=s.ud_grid,gmx=0;
      for(var ri=0;ri<5;ri++)for(var ci=0;ci<5;ci++)if(gl[ri][ci]>gmx)gmx=gl[ri][ci];
      if(gmx<1)gmx=1;
      h+='<div class="c cx" style="margin-top:12px"><div class="ch"><span class="cn">Urgency x Difficulty (pending)</span></div><div class="mt">'
        +'<div style="display:grid;grid-template-columns:repeat(6,1fr);gap:2px;font-size:.6rem;text-align:center">'
        +'<div></div><div style="color:var(--text3);font-weight:600">D1</div><div style="color:var(--text3);font-weight:600">D2</div>'
        +'<div style="color:var(--text3);font-weight:600">D3</div><div style="color:var(--text3);font-weight:600">D4</div>'
        +'<div style="color:var(--text3);font-weight:600">D5</div>';
      for(var ri=0;ri<5;ri++){
        h+='<div style="color:var(--text3);font-weight:600">U'+(ri+1)+'</div>';
        for(var ci=0;ci<5;ci++){
          var v=gl[ri][ci],a=v/gmx,rg='rgba(108,99,255,'+(0.08+a*0.7)+')',fg=a>0.5?'#fff':'var(--text)';
          h+='<div style="background:'+rg+';color:'+fg+';border-radius:4px;padding:3px 0">'+v+'</div>';
        }
      }
      h+='</div></div></div>';
    }
    if(s.archive_stats&&s.archive_stats.length){
      h+='<div class="c cx" style="margin-top:12px"><div class="ch"><span class="cn">Archives</span></div><div class="mt">';
      s.archive_stats.forEach(function(a){
        h+='<div style="margin-bottom:6px"><div style="display:flex;justify-content:space-between;font-size:.72rem;margin-bottom:2px">'
          +'<span>'+esc(a.name)+'</span><span>'+a.pct+'% ('+a.done+'/'+a.total+')</span></div>'
          +'<div class="pb"><div class="pf" style="width:'+a.pct+'%"></div></div></div>';
      });
      h+='</div></div>';
    }
    c.innerHTML=h;
  }).catch(function(e){gM().innerHTML='<div class="err">Error: '+e.message+'</div>'});
}
function updateAFKBadge(){
  api('/ai-convos/status').then(function(s){
    var n=Object.values(s).filter(function(v){return v==='done'}).length;
    var el=document.getElementById('afkbtn');
    if(el)el.textContent='[AFK'+(n?n:'')+']';
  }).catch(function(){});
}
function goToAFKTask(id){api('/tasks/'+id).then(function(t){api('/directories/'+t.directory_id).then(function(d){showTask(id,t.name,d.id,d.name,d.archive_id||0,d.archive_name||'')})})}
function goToAFKDir(id){showDirAI(id)}
function showAFK(){
  NAV={level:'afk'};_TC=[];setBC('<span class="s">/</span><span>AFK Jobs</span>');sk(3);
  api('/ai-convos/status').then(function(statuses){
    var keys=Object.keys(statuses);
    if(!keys.length){gM().innerHTML='<div class="emp">No AI jobs while you were away</div>';return}
    var items=[],rem=keys.length;
    keys.forEach(function(k){
      var p=k.split('_'),type=p[0],id=parseInt(p[1]);
      (type==='task'?api('/tasks/'+id).then(function(t){return{key:k,type:'task',id:id,status:statuses[k],name:t.name,dirId:t.directory_id}})
        :api('/directories/'+id).then(function(d){return{key:k,type:'dir',id:id,status:statuses[k],name:d.name}})
      ).then(function(item){
        items.push(item);
        if(items.length===rem){
          var h='';
          items.sort(function(a,b){return a.key.localeCompare(b.key)});
          items.forEach(function(item){
            var click=item.type==='task'?'goToAFKTask('+item.id+')':'goToAFKDir('+item.id+')';
            var st=item.status==='running'?'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#ffc107;margin-right:4px"></span>running'
              :'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#4caf50;margin-right:4px"></span>done';
            h+='<div class="c ca" onclick="'+click+'"><div class="ch"><span class="cn">'+esc(item.name)+'</span>'+st+'</div></div>';
          });
          gM().innerHTML=h;
        }
      });
    });
  });
}
var TERM=null,TERM_WS=null,TERM_SID=null;
function closeTerminal(){
  if(TERM_WS){TERM_WS.close();TERM_WS=null}
  if(TERM){TERM.dispose();TERM=null}
  document.getElementById('tp').classList.remove('open');TERM_SID=null
}
function showTerminal(sid){
  TERM_SID=sid;var tp=document.getElementById('tp');tp.classList.add('open');
  var tx=document.getElementById('tx');tx.innerHTML='';
  TERM=new Terminal({cursorBlink:true,cursorStyle:'block',fontSize:12,fontFamily:'ui-monospace,"SF Mono","JetBrains Mono",monospace',theme:{background:'#0a0a0f',foreground:'#d4d4dc',cursor:'#e53935',selectionBackground:'#e5393540',black:'#000',red:'#e53935',green:'#00e676',yellow:'#ff8c00',blue:'#00bcd4',magenta:'#e040fb',cyan:'#00e5ff',white:'#d4d4dc'},allowTransparency:true,rows:15});
  var FA=new FitAddon.FitAddon();TERM.loadAddon(FA);TERM.open(tx);FA.fit();
  var wsUrl='ws://'+location.host+'/ws/terminal/'+sid+'?token='+T;
  TERM_WS=new WebSocket(wsUrl);TERM_WS.binaryType='arraybuffer';
  TERM_WS.onopen=function(){TERM.focus()};
  TERM_WS.onmessage=function(evt){TERM.write(new Uint8Array(evt.data))};
  TERM_WS.onclose=function(){TERM.write('\r\n\x1b[1;31m--- session ended ---\x1b[0m\r\n')};
  TERM.onData(function(d){if(TERM_WS&&TERM_WS.readyState===WebSocket.OPEN)TERM_WS.send(d)});
  window.addEventListener('resize',function(){FA.fit()});
}
function showDirAI(id){
  fetch('/api/ai-convos/dir_'+id+'/dismiss?token='+T,{method:'POST'});
  var mopts=['default','free','light','medium','hard'];
  var msel='<select id="dpm" class="ps" onchange="updateModelDisplay()">';
  mopts.forEach(function(o){msel+='<option value="'+o+'">'+o+'</option>'});
  msel+='</select>';
  var h='<span class="bk" onclick="exitDirAI()">&lt; '+esc(NAV.dirName)+'</span>'
    +'<div class="pc"><div class="pch">[AI] directory prompt <span style="display:flex;align-items:center;gap:6px"><span class="pnc" onclick="newDirChat()">[↻]</span></span></div>'
    +'<div class="pct"><div class="agent-toggle" id="datog"><button class="at-btn active" data-agent="build" onclick="setAgent(this,\'build\')">BUILD</button><button class="at-btn" data-agent="plan" onclick="setAgent(this,\'plan\')">PLAN</button></div>'+msel+'<span class="model-label" id="dpml">opencode-default</span></div>'
    +'<div id="dpconv" class="pconv"></div>'
    +'<div class="pin"><div class="cmdmenu" id="dcmdm"></div><textarea id="dppt" class="pta" placeholder="Ask opencode about this project..." rows="1" oninput="this.style.height=\'\';this.style.height=Math.min(this.scrollHeight,80)+\'px\';onCmdInput(this,\'dcmdm\')" onkeydown="if(event.key===\'Enter\'&&!event.shiftKey){event.preventDefault();onDirBtnClick('+id+')}"></textarea>'
    +'<button class="psb" id="dsb" onclick="onDirBtnClick('+id+')">▸</button></div></div>';
  gM().innerHTML=h;loadDirConvo(id);
}
function exitDirAI(){renderTasks(_TC)}
function sendDirPrompt(id){
  var input=document.getElementById('dppt');
  var raw=input.value.trim();
  if(!raw)return;
  input.value='';
  input.style.height='';
  input.blur();
  _DPC.push({role:'user',text:raw});
  renderDirConv();
  if(_DPBUSY){_DPQ.push({id:id,raw:raw});updateDirBtn();return}
  _doSendDir(raw,id);
}
function _doSendDir(raw,id){
  var prompt=raw,command='';
  if(raw.startsWith('/')&&raw.indexOf(' ')===-1){command=raw.slice(1);prompt=''}
  if(_PLAN&&prompt){prompt='[PLAN] Do NOT write, edit, or create any files. Only read files and output analysis and recommendations. Plan: '+prompt}
  var btn=document.getElementById('dsb');
  btn.textContent='■';
  _DPBUSY=true;_DPABORT=new AbortController();
  var ag=document.querySelector('.at-btn.active');
  var pm=document.getElementById('dpm');
  var aiIdx=_DPC.length;
  _DPC.push({role:'ai',text:'',thinking:true});
  renderDirConv();
  var url='/api/directories/'+id+'/opencode/prompt'+(T?'?token='+T:'');
  var body={prompt:prompt,agent:ag?ag.dataset.agent:'build',command:command,session_id:_DSID,config:pm?pm.value:'default'};
  fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:_DPABORT.signal}).then(function(r){
    if(!r.ok)throw Error(r.status+' '+r.statusText);
    var reader=r.body.getReader();
    var decoder=new TextDecoder();
    var buf='';
    function read(){
      reader.read().then(function(result){
        if(result.done){_DPC[aiIdx].thinking=false;renderDirConv();dequeueDir();return}
        buf+=decoder.decode(result.value,{stream:true});
        var i;
        while((i=buf.indexOf('\n'))>=0){
          var line=buf.slice(0,i);
          buf=buf.slice(i+1);
          if(line.indexOf('data: ')!==0)continue;
          var data=line.slice(6);
          if(data==='[DONE]')continue;
          try{var evt=JSON.parse(data);if(evt.sessionID)_DSID=evt.sessionID;if(evt.type==='text'&&evt.part&&evt.part.text)_DPC[aiIdx].text+=evt.part.text}catch(e){}
        }
        renderDirConv();
        read();
      }).catch(function(e){if(e.name==='AbortError'){_DPC[aiIdx].text+='\n[Cancelled]'}else{_DPC[aiIdx].text+='\n[Error: '+e.message+']'};_DPC[aiIdx].thinking=false;renderDirConv();dequeueDir()});
    }
    read();
  }).catch(function(e){if(e.name==='AbortError'){_DPC.push({role:'error',text:'Cancelled'})}else{_DPC.push({role:'error',text:e.message})};renderDirConv();dequeueDir()});
  document.getElementById('dppt').focus();
}
function newDirChat(){fetch('/api/directories/'+NAV.dirId+'/opencode/convo?token='+T,{method:'DELETE'});fetch('/api/ai-convos/dir_'+NAV.dirId+'/dismiss?token='+T,{method:'POST'});if(_DPABORT){_DPABORT.abort();_DPABORT=null}_DPBUSY=false;_DPC=[];_DSID='';_DPQ=[];_PLAN=false;var tog=document.getElementById('datog');if(tog){tog.querySelectorAll('.at-btn.active').forEach(function(b){b.classList.remove('active')});var bt=tog.querySelector('.at-btn[data-agent="build"]');if(bt)bt.classList.add('active')}renderDirConv();updateModelDisplay();updateDirBtn()}
function renderDirConv(){
  var el=document.getElementById('dpconv');
  if(!el)return;
  var h='';
  _DPC.forEach(function(m){
    if(m.role=='user')h+='<div class="pm pmu">'+esc(m.text)+'</div>';
    else if(m.role=='ai')h+='<div class="pm pma'+(m.thinking?' thinking':'')+'">'+esc(m.text)+'</div>';
    else h+='<div class="pme">'+esc(m.text)+'</div>';
  });
  el.innerHTML=h;
  el.scrollTop=el.scrollHeight;
}
function newChat(){fetch('/api/tasks/'+NAV.taskId+'/opencode/convo?token='+T,{method:'DELETE'});fetch('/api/ai-convos/task_'+NAV.taskId+'/dismiss?token='+T,{method:'POST'});if(_PABORT){_PABORT.abort();_PABORT=null}_PBUSY=false;_PC=[];_SID='';_PQ=[];_PLAN=false;var tog=document.getElementById('atog');if(tog){tog.querySelectorAll('.at-btn.active').forEach(function(b){b.classList.remove('active')});var bt=tog.querySelector('.at-btn[data-agent="build"]');if(bt)bt.classList.add('active')}renderConv();document.getElementById('ppt').value='';updateModelDisplay();updateTaskBtn()}
function sendPrompt(id){
  var input=document.getElementById('ppt');
  var raw=input.value.trim();
  if(!raw)return;
  input.value='';
  input.style.height='';
  input.blur();
  _PC.push({role:'user',text:raw});
  renderConv();
  if(_PBUSY){_PQ.push({id:id,raw:raw});updateTaskBtn();return}
  _doSendTask(raw,id);
}
function _doSendTask(raw,id){
  var prompt=raw,command='';
  if(raw.startsWith('/')&&raw.indexOf(' ')===-1){command=raw.slice(1);prompt=''}
  if(_PLAN&&prompt){prompt='[PLAN] Do NOT write, edit, or create any files. Only read files and output analysis and recommendations. Plan: '+prompt}
  var btn=document.getElementById('psb');
  btn.textContent='■';
  _PBUSY=true;_PABORT=new AbortController();
  var ag=document.querySelector('.at-btn.active');
  var pm=document.getElementById('pm');
  var aiIdx=_PC.length;
  _PC.push({role:'ai',text:'',thinking:true});
  renderConv();
  var url='/api/tasks/'+id+'/opencode/prompt'+(T?'?token='+T:'');
  var body={prompt:prompt,agent:ag?ag.dataset.agent:'build',command:command,session_id:_SID,config:pm?pm.value:'default'};
  fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:_PABORT.signal}).then(function(r){
    if(!r.ok)throw Error(r.status+' '+r.statusText);
    var reader=r.body.getReader();
    var decoder=new TextDecoder();
    var buf='';
    function read(){
      reader.read().then(function(result){
        if(result.done){_PC[aiIdx].thinking=false;renderConv();dequeueTask();return}
        buf+=decoder.decode(result.value,{stream:true});
        var i;
        while((i=buf.indexOf('\n'))>=0){
          var line=buf.slice(0,i);
          buf=buf.slice(i+1);
          if(line.indexOf('data: ')!==0)continue;
          var data=line.slice(6);
          if(data==='[DONE]')continue;
          try{var evt=JSON.parse(data);if(evt.sessionID)_SID=evt.sessionID;if(evt.type==='text'&&evt.part&&evt.part.text)_PC[aiIdx].text+=evt.part.text}catch(e){}
        }
        renderConv();
        read();
      }).catch(function(e){if(e.name==='AbortError'){_PC[aiIdx].text+='\n[Cancelled]'}else{_PC[aiIdx].text+='\n[Error: '+e.message+']'};_PC[aiIdx].thinking=false;renderConv();dequeueTask()});
    }
    read();
  }).catch(function(e){if(e.name==='AbortError'){_PC.push({role:'error',text:'Cancelled'})}else{_PC.push({role:'error',text:e.message})};renderConv();dequeueTask()});
  document.getElementById('ppt').focus();
}
function renderConv(){
  var el=document.getElementById('pconv');
  if(!el)return;
  var h='';
  _PC.forEach(function(m){
    if(m.role=='user')h+='<div class="pm pmu">'+esc(m.text)+'</div>';
    else if(m.role=='ai')h+='<div class="pm pma'+(m.thinking?' thinking':'')+'">'+esc(m.text)+'</div>';
    else h+='<div class="pme">'+esc(m.text)+'</div>';
  });
  el.innerHTML=h;
  el.scrollTop=el.scrollHeight;
}
showArchives();
</script>
</body>
</html>
"""


def get_url(host: str, port: int, tailscale: bool = False) -> str:
    ip = host
    if tailscale:
        ts = _get_tailscale_ip()
        if ts:
            ip = ts
    return f"http://{ip}:{port}?token={SERVER_TOKEN}"


def _get_tailscale_ip() -> str | None:
    try:
        r = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=5,
        )
        ip = r.stdout.strip()
        return ip if ip else None
    except Exception:
        return None


def run_server(host: str = "0.0.0.0", port: int = 8080):
    import uvicorn
    print(f"TaskWatch+ server: {get_url(host, port)}")
    ts_ip = _get_tailscale_ip()
    if ts_ip:
        print(f"Tailscale:         {get_url(ts_ip, port)}")
    uvicorn.run(app, host=host, port=port, log_level="info")
