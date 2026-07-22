# ruff: noqa: E501
import json
import secrets
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response

from . import archive_cmds, directory_cmds, note_cmds, stats_cmds, task_cmds
from .paths import SERVER_TOKEN_PATH
from .tui_helpers import _build_terminal_cmd, _detect_terminal, _find_opencode

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

    terminal = _detect_terminal()
    if not terminal:
        raise HTTPException(status_code=400, detail="No terminal found")

    from .db import get_conn

    conn = get_conn()
    row = conn.execute(
        "SELECT d.name AS dname, a.name AS aname FROM directories d "
        "JOIN archives a ON a.id = d.archive_id WHERE d.id = ?",
        (task.directory_id,),
    ).fetchone()
    dir_name = row["dname"] if row else None
    arch_name = row["aname"] if row else None

    notes = note_cmds.list_notes(task.id)
    ctx = {
        "task": {
            "name": task.name,
            "description": task.description,
            "deadline": task.deadline,
            "urgency": task.urgency,
            "difficulty": task.difficulty,
            "time_dedicated": task.time_dedicated,
            "repeatable": bool(task.repeatable),
            "repeatable_type": task.repeatable_type,
            "repeat_on_specific_day": task.repeat_on_specific_day,
            "finished": bool(task.finished),
        },
        "directory": dir_name,
        "archive": arch_name,
        "notes": [
            {
                "date": n.date,
                "note": n.note,
                "file_path": n.file_path,
                "created_at": n.created_at,
            }
            for n in notes
        ],
    }

    fd = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="taskwatch_ai_", delete=False
    )
    with fd:
        json.dump(ctx, fd, indent=2)
        ctx_file = fd.name

    project_root = Path(__file__).resolve().parent.parent
    cmd = _build_terminal_cmd(
        terminal,
        f"{opencode_path} run -f '{ctx_file}' -i --dir '{project_root}'",
    )
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"status": "ok", "message": f"opencode launched for: {task.name}"}


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
</style>
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
    <button onclick="navTo(this);document.getElementById('main').scrollTop=0"><span class="ni">[^]</span><span>Top</span></button>
  </div>
</div>
<script>
var T=(new URLSearchParams(location.search).get('token')||localStorage.getItem('tw_token')||'');
if(!localStorage.getItem('tw_token')&&T)localStorage.setItem('tw_token',T);
var NAV={},BC=document.getElementById('bc'),_TC=[],_AF='all';
function api(p){var s=p.indexOf('?')>=0?'&':'?';return fetch('/api'+p+(T?s+'token='+T:''),{headers:{Accept:'application/json'}}).then(function(r){if(!r.ok)throw Error(r.status+' '+r.statusText);return r.json()})}
function esc(s){if(!s)return'';var d=document.createElement('div');d.textContent=s;return d.innerHTML}
function qj(s){return s.replace(/'/g,"\\'")}
function uB(n){return'<span class="b bu'+n+'">U'+n+'</span>'}
function dB(n){return'<span class="b bd'+n+'">D'+n+'</span>'}
function pRing(p){var c=Math.PI*36,o=c*(1-Math.min(p,100)/100);return'<svg width="42" height="42" viewBox="0 0 44 44"><circle cx="22" cy="22" r="18" fill="none" stroke="var(--border)" stroke-width="4"/><circle cx="22" cy="22" r="18" fill="none" stroke="var(--accent)" stroke-width="4" stroke-linecap="round" transform="rotate(-90 22 22)" stroke-dasharray="'+c+'" stroke-dashoffset="'+o+'"/><text x="22" y="22" text-anchor="middle" dominant-baseline="central" fill="var(--text2)" font-size="11" font-weight="700">'+Math.round(p)+'%</text></svg>'}
function sk(n){var h='',i=0;for(;i<n;i++){var w=i%2?'sk-w70':'sk-w50';h+='<div class="sk"><div class="sk-l"></div><div class="sk-l"></div><div class="sk-l '+w+'"></div></div>'}document.getElementById('main').innerHTML=h}
function setBC(h){BC.innerHTML='<span class="b" onclick="showArchives()">Archives</span>'+h}
function gM(){return document.getElementById('main')}
function navTo(el){document.querySelectorAll('#bn button').forEach(function(b){b.classList.remove('act')});if(el)el.classList.add('act')}
function togS(){var e=document.getElementById('sbar');e.classList.toggle('open');if(!e.classList.contains('open'))document.getElementById('si').value=''}
function onS(){if(NAV.level=='tasks'&&_TC.length)renderTasks(_TC)}
function renderTasks(arr){
  var q=document.getElementById('si').value.toLowerCase().trim();
  var c=gM(),h='<span class="bk" onclick="showDirs('+NAV.archId+',\''+qj(esc(NAV.archName))+'\')">&lt; '+esc(NAV.archName)+'</span>';
  var flt='<div class="fb"><button onclick="chF(this,\'all\')" class="act">All</button><button onclick="chF(this,\'unf\')">Unfinished</button><button onclick="chF(this,\'urg\')">Urgent</button><button onclick="chF(this,\'pin\')">Pinned</button></div>';
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
    if(!tasks.length){gM().innerHTML='<div class="emp">No tasks</div>';return}
    _AF='all';renderTasks(tasks);
  }).catch(function(e){gM().innerHTML='<div class="err">Error: '+e.message+'</div>'});
}
function showTask(taskId,taskName,dirId,dirName,archId,archName){
  NAV={level:'task',taskId:taskId,taskName:taskName,dirId:dirId,dirName:dirName,archId:archId,archName:archName};_TC=[];
  setBC('<span class="s">/</span><span class="b" onclick="showDirs('+archId+',\''+qj(esc(archName))+'\')">'+esc(archName)+'</span><span class="s">/</span><span class="b" onclick="showTasks('+dirId+',\''+qj(esc(dirName))+'\','+archId+',\''+qj(esc(archName))+'\')">'+esc(dirName)+'</span><span class="s">/</span><span>'+esc(taskName)+'</span>');
  var c=gM();c.innerHTML='<div class="sk"><div class="sk-l sk-w70"></div><div class="sk-l sk-w50"></div><div class="sk-l sk-w40"></div></div>';
  api('/tasks/'+taskId).then(function(t){
    var h='<span class="bk" onclick="showTasks('+dirId+',\''+qj(esc(dirName))+'\','+archId+',\''+qj(esc(archName))+'\')">&lt; '+esc(dirName)+'</span>'
      +'<div class="c cx"><div class="ch"><span class="cn'+(t.pinned?' pd':'')+'">'+esc(t.name)+'</span>'
      +'<span class="bw">'+uB(t.urgency)+' '+dB(t.difficulty)+' <span class="b ba" id="aib" onclick="openAI('+t.id+',\''+qj(esc(t.name))+'\')" style="cursor:pointer;transition:all .12s">[AI]</span></span></div>'
      +'<div class="mt"><span>'+t.time_dedicated+'m</span>'+(t.deadline!=='none'?'<span>due: '+esc(t.deadline)+'</span>':'')+'</div>'
      +(t.tags&&t.tags.length?'<div class="tgs">'+t.tags.map(function(tg){return'<span class="tg">'+esc(tg)+'</span>'}).join('')+'</div>':'')
      +(t.description?'<div class="desc">'+esc(t.description)+'</div>':'')
      +'</div>';
    if(t.notes&&t.notes.length){
      h+='<div class="notes"><h2>Notes ('+t.notes.length+')</h2>';
      t.notes.forEach(function(n){h+='<div class="no"><div class="nd">'+esc(n.date)+'</div><div class="nt">'+esc(n.note)+'</div>'+(n.file_path?'<div class="nf">file: '+esc(n.file_path)+'</div>':'')+'</div>'});
      h+='</div>';
    }
    c.innerHTML=h;
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
function openAI(id,name){
  var el=document.getElementById('aib');
  if(el)el.textContent='...';
  var s='/api/tasks/'+id+'/opencode';
  var q=s.indexOf('?')>=0?'&':'?';
  fetch(s+(T?q+'token='+T:''),{method:'POST',headers:{Accept:'application/json'}}).then(function(r){if(!r.ok)throw Error(r.status+' '+r.statusText);return r.json()}).then(function(r){
    if(el){el.textContent='OK';el.style.color='var(--success)';el.style.borderColor='var(--success)';setTimeout(function(){el.textContent='[AI]';el.style.color='';el.style.borderColor=''},3000)}
  }).catch(function(e){
    if(el){el.textContent='ERR';el.style.color='var(--danger)';el.style.borderColor='var(--danger)';setTimeout(function(){el.textContent='[AI]';el.style.color='';el.style.borderColor=''},3000)}
    var c=gM();if(c)c.innerHTML+='<div class="err" style="margin-top:8px">opencode: '+e.message+'</div>'
  });
}
showArchives();
</script>
</body>
</html>
"""


def get_url(host: str, port: int) -> str:
    return f"http://{host}:{port}?token={SERVER_TOKEN}"


def run_server(host: str = "0.0.0.0", port: int = 8080):
    import uvicorn
    print(f"TaskWatch+ server: {get_url(host, port)}")
    uvicorn.run(app, host=host, port=port, log_level="info")
