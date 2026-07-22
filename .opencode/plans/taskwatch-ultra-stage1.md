# TASKWATCH-ULTRA — Stage 1: FastAPI Frontend

## Files to create/modify

### 1. `taskwatch/paths.py` — add line

```python
SERVER_TOKEN_PATH = DATA_DIR / "server_token.json"
```

Add after `AI_CONFIG_PATH`.

---

### 2. `taskwatch/server.py` — create new file

```python
import json
import secrets
import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import archive_cmds, directory_cmds, task_cmds, note_cmds
from .paths import DATA_DIR, SERVER_TOKEN_PATH

app = FastAPI(title="TaskWatch+")

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)


# ── Auth ────────────────────────────────────────────────────────────

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


# ── API Routes ──────────────────────────────────────────────────────

@app.get("/api/status")
def api_status(request: Request):
    _verify_token(request)
    return {"status": "ok", "version": "0.3.9", "token_valid": True}


@app.get("/api/directories")
def api_directories(request: Request):
    _verify_token(request)
    dirs = directory_cmds.list_directories()
    result = []
    for d in dirs:
        tasks = task_cmds.list_tasks(directory_id=d.id)
        total = len(tasks)
        done = sum(1 for t in tasks if t.finished)
        arch = archive_cmds.get_archive(d.archive_id)
        result.append({
            "id": d.id,
            "name": d.name,
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
def api_directory_tasks(dir_id: int, request: Request, finished: str | None = Query(None), unfinished: str | None = Query(None)):
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
        "notes": [{"id": n.id, "date": n.date, "note": n.note, "file_path": n.file_path} for n in notes],
    }


@app.get("/api/tasks/{task_id}/notes")
def api_task_notes(task_id: int, request: Request):
    _verify_token(request)
    notes = note_cmds.list_notes(task_id=task_id)
    return [{"id": n.id, "date": n.date, "note": n.note, "file_path": n.file_path} for n in notes]


# ── Web UI ──────────────────────────────────────────────────────────

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>TaskWatch+</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0d1117;color:#c9d1d9;padding:16px;max-width:600px;margin:0 auto}
h1{font-size:1.3rem;margin-bottom:12px;color:#58a6ff}
h2{font-size:1.1rem;margin:16px 0 8px;color:#8b949e}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px;margin-bottom:10px;cursor:pointer;transition:border-color .15s}
.card:hover{border-color:#58a6ff}
.card-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px}
.card-title{font-weight:600;font-size:1rem}
.card-title.pinned::before{content:'\U0001f4cc ';font-size:.8rem}
.badge{display:inline-block;padding:1px 6px;border-radius:4px;font-size:.75rem;font-weight:600}
.badge-urg{background:#da3633;color:#fff}
.badge-dif{background:#1f6feb;color:#fff}
.badge-done{background:#238636;color:#fff}
.badge-pending{background:#9e6a03;color:#fff}
.badge-tag{background:#21262d;color:#8b949e;border:1px solid #30363d;margin-right:4px}
.badge-level{background:#8957e5;color:#fff}
.progress-bar{height:6px;background:#21262d;border-radius:3px;margin-top:8px;overflow:hidden}
.progress-fill{height:100%;border-radius:3px;background:linear-gradient(90deg,#238636,#2ea043);transition:width .3s}
.progress-text{font-size:.75rem;color:#8b949e;margin-top:3px}
.meta{font-size:.8rem;color:#8b949e;margin-top:4px}
.meta span{margin-right:10px}
button{background:#238636;color:#fff;border:none;border-radius:6px;padding:10px 16px;font-size:.9rem;font-weight:600;cursor:pointer;width:100%;margin-top:8px}
button:active{background:#2ea043}
button.secondary{background:#21262d;color:#c9d1d9;border:1px solid #30363d}
button.secondary:active{background:#30363d}
button.danger{background:#da3633}
.back{color:#58a6ff;cursor:pointer;font-size:.9rem;margin-bottom:12px;display:inline-block}
.tag-list{margin-top:6px}
.tag{display:inline-block;padding:1px 6px;border-radius:4px;font-size:.75rem;background:#21262d;color:#8b949e;border:1px solid #30363d;margin:2px 2px 0 0}
.loading{text-align:center;padding:40px;color:#8b949e}
.error{color:#da3633;padding:10px;text-align:center}
.note{border-left:2px solid #30363d;padding:8px 12px;margin:6px 0;font-size:.85rem;color:#8b949e}
.note-date{font-size:.7rem;color:#484f58}
.project-path{font-size:.75rem;color:#484f58;margin-top:2px}
</style>
</head>
<body>
<div id="app">
  <h1>TaskWatch+</h1>
  <div id="content" class="loading">Loading...</div>
</div>
<script>
const TOKEN = new URLSearchParams(location.search).get('token') || '';
const BASE = '/api';

function api(path){return fetch(BASE+path+(TOKEN?'?token='+TOKEN:''),{headers:{Accept:'application/json'}}).then(r=>{if(!r.ok)throw Error(r.statusText);return r.json()})}

function showDirs(){
  document.getElementById('content').innerHTML='<div class="loading">Loading...</div>';
  api('/directories').then(dirs=>{
    let h='';
    dirs.forEach(d=>{
      h+='<div class="card" onclick="showTasks('+d.id+',\''+d.name.replace(/'/g,"\\'")+'\')">'
        +'<div class="card-header"><span class="card-title'+(d.level>1?'':'')+'">'+esc(d.name)+'</span>'
        + (d.level>1?'<span class="badge badge-level">Lv.'+d.level+'</span>':'')
        +'</div>'
        +'<div class="meta">'+d.archive_name+(d.project_path?' &middot; '+esc(d.project_path):'')+'</div>'
        +'<div class="meta">'+d.task_done+'/'+d.task_count+' tasks</div>'
        +'<div class="progress-bar"><div class="progress-fill" style="width:'+d.progress_pct+'%"></div></div>'
        +'<div class="progress-text">'+d.progress_pct+'% complete</div>'
        +'</div>';
    });
    if(!dirs.length)h='<p style="color:#8b949e">No directories found.</p>';
    document.getElementById('content').innerHTML=h;
  }).catch(e=>document.getElementById('content').innerHTML='<div class="error">Error: '+e.message+'</div>');
}

function showTasks(dirId,dirName){
  document.getElementById('content').innerHTML='<div class="loading">Loading...</div>';
  api('/directories/'+dirId+'/tasks?unfinished=1').then(tasks=>{
    let h='<span class="back" onclick="showDirs()">&larr; Directories</span><h2>'+esc(dirName)+'</h2>';
    if(!tasks.length){h+='<p style="color:#8b949e">All tasks done!</p>';document.getElementById('content').innerHTML=h;return}
    tasks.forEach(t=>{
      let urgClass='badge-urg',difClass='badge-dif';
      h+='<div class="card" onclick="showTask('+t.id+',\''+esc(t.name).replace(/'/g,"\\'")+'\')">'
        +'<div class="card-header"><span class="card-title'+(t.pinned?' pinned':'')+'">'+esc(t.name)+'</span>'
        +'<span><span class="badge '+urgClass+'">'+t.urgency+'</span> <span class="badge '+difClass+'">'+t.difficulty+'</span></span>'
        +'</div>';
      if(t.tags&&t.tags.length)h+='<div class="tag-list">'+t.tags.map(tg=>'<span class="tag">'+esc(tg)+'</span>').join('')+'</div>';
      h+='<div class="meta">'+t.time_dedicated+'m'+(t.deadline!=='none'?' &middot; Due: '+t.deadline:'')+'</div>'
        +'</div>';
    });
    document.getElementById('content').innerHTML=h;
  }).catch(e=>document.getElementById('content').innerHTML='<div class="error">Error: '+e.message+'</div>');
}

function showTask(taskId,taskName){
  document.getElementById('content').innerHTML='<div class="loading">Loading...</div>';
  api('/tasks/'+taskId).then(t=>{
    let h='<span class="back" onclick="showTasks('+t.id+',\''+esc(t.name).replace(/'/g,"\\'")+'\')">&larr; '+esc(t.name)+'</span>'
      +'<h2>'+esc(t.name)+'</h2>'
      +'<div class="meta"><span>Urgency: '+t.urgency+'/5</span><span>Difficulty: '+t.difficulty+'/5</span><span>Est: '+t.time_dedicated+'m</span></div>'
      +(t.deadline!=='none'?'<div class="meta">Deadline: '+t.deadline+'</div>':'')
      +(t.description?'<div class="meta" style="margin-top:8px;color:#c9d1d9">'+esc(t.description)+'</div>':'')
      +(t.tags&&t.tags.length?'<div class="tag-list">'+t.tags.map(tg=>'<span class="tag">'+esc(tg)+'</span>').join('')+'</div>':'');
    if(t.notes&&t.notes.length){
      h+='<h2>Notes ('+t.notes.length+')</h2>';
      t.notes.forEach(n=>{h+='<div class="note"><div class="note-date">'+n.date+'</div>'+esc(n.note)+'</div>'});
    }
    document.getElementById('content').innerHTML=h;
  }).catch(e=>document.getElementById('content').innerHTML='<div class="error">Error: '+e.message+'</div>');
}

function esc(s){if(!s)return '';const d=document.createElement('div');d.textContent=s;return d.innerHTML}
showDirs();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def web_ui(request: Request):
    return INDEX_HTML


# ── Startup ─────────────────────────────────────────────────────────

def get_url(host: str, port: int) -> str:
    return f"http://{host}:{port}?token={SERVER_TOKEN}"


def run_server(host: str = "0.0.0.0", port: int = 8080):
    import uvicorn
    print(f"TaskWatch+ server starting on {get_url(host, port)}")
    uvicorn.run(app, host=host, port=port, log_level="info")
```

---

### 3. `taskwatch/static/` directory — static file serving

Create the empty directory:
```bash
mkdir -p taskwatch/static
```

The index.html is embedded directly in `server.py` for simplicity (Stage 1). Later stages can serve from the static directory.

---

### 4. `taskwatch/cli.py` — add `serve` command

**In `build_parser()`**, add after the `ai` subparser block (around line 336):

```python
# ── serve ──
sv = sub.add_parser("serve", help="Start the TaskWatch+ HTTP server (web UI for phone)")
sv.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
sv.add_argument("--port", type=int, default=8080, help="Port (default: 8080)")
```

**In `run()`**, add between the `if entity == "ai"` and the `finally` block:

```python
    elif entity == "serve":
        from .server import run_server
        run_server(host=opts.host, port=opts.port)
```

**In `print_global_help()`**, add `"serve"` to the Interface category (around line 82):

```python
("Interface", ["tui", "waybar", "serve"]),
```

**In `CATEGORY_DESCRIPTIONS`**, add:

```python
"serve":     "Start the HTTP server for phone/remote access",
```

---

### 5. `taskwatch/tui_helpers.py` — add `serve` to COMMANDS

Find the `COMMANDS` list and add:

```python
("serve", "Start/stop the HTTP server for phone access"),
("serve stop", "Stop the HTTP server"),
("serve status", "Show server connection info"),
```

---

### 6. `taskwatch/tui.py` — add `_cmd_serve` methods

**In `_CMD_DISPATCH`** (around line 860-916), add:

```python
"serve": "_cmd_serve",
```

**In `_CMD_PREFIX_DISPATCH`** (around line 918-946), add:

```python
("serve ", "_cmd_serve_subcmd"),
```

**Add these methods** to the `TaskWatchTUI` class:

```python
def _cmd_serve(self) -> None:
    self._server_host = "0.0.0.0"
    self._server_port = 8080
    self._set_timed_caption("done", f"Starting server on port {self._server_port}... ")
    import threading
    from .server import run_server, get_url, SERVER_TOKEN
    url = get_url(self._server_host, self._server_port)
    self._server_thread = threading.Thread(
        target=run_server,
        args=(self._server_host, self._server_port),
        daemon=True,
    )
    self._server_thread.start()
    self._set_timed_caption("done", f"Server: {url} ")

def _cmd_serve_subcmd(self, cmd: str) -> None:
    parts = cmd.strip().split(maxsplit=1)
    sub = parts[1] if len(parts) > 1 else ""
    if sub == "stop":
        self._set_timed_caption("error", "Use Ctrl+C or kill the process to stop. Server restart coming soon.")
    elif sub == "status":
        from .server import SERVER_TOKEN
        self._set_timed_caption("done", f"Server status: {'running' if hasattr(self,'_server_thread') and self._server_thread.is_alive() else 'stopped'} ")
    else:
        self._set_timed_caption("error", "Usage: :serve stop | :serve status ")
```

---

### 7. `pyproject.toml` — update dependencies

```toml
dependencies = ["urwid>=4.0.0", "fastapi>=0.115.0", "uvicorn>=0.34.0"]
```

---

## How to test

```bash
# 1. Start the server
python -m taskwatch serve --port 8080

# 2. In another terminal, test the API
curl "http://localhost:8080/api/directories?token=$(cat ~/.local/share/taskwatch/server_token.json | python -c 'import json,sys;print(json.load(sys.stdin)["token"])')"

# 3. Or open in browser
open "http://localhost:8080?token=$(cat ~/.local/share/taskwatch/server_token.json | python -c 'import json,sys;print(json.load(sys.stdin)["token"])')"
```
