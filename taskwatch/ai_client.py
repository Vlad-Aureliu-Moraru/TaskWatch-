from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from pathlib import Path

AI_CONFIG_PATH = Path.home() / ".local" / "share" / "taskwatch" / "ai_config.json"
_DEFAULT_FALLBACK_ORDER = ["groq", "gemini", "mistral"]

MODELS: dict[str, str] = {
    "groq": "llama-3.3-70b-versatile",
    "gemini": "gemini-2.0-flash",
    "mistral": "mistral-large-latest",
}


def load_config() -> dict:
    try:
        with open(AI_CONFIG_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"providers": {}, "fallback_order": list(_DEFAULT_FALLBACK_ORDER)}


def save_config(cfg: dict) -> None:
    AI_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = AI_CONFIG_PATH.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.chmod(tmp, 0o600)
    tmp.replace(AI_CONFIG_PATH)


def add_provider(name: str, key: str) -> tuple[bool, str]:
    name = name.lower()
    if name not in MODELS:
        return False, f"Unknown provider '{name}'. Supported: {', '.join(sorted(MODELS))}"
    if not key.strip():
        return False, "API key cannot be empty"
    cfg = load_config()
    cfg.setdefault("providers", {})[name] = {
        "api_key": key.strip(),
        "model": MODELS[name],
        "enabled": True,
    }
    if name not in cfg.setdefault("fallback_order", list(_DEFAULT_FALLBACK_ORDER)):
        cfg["fallback_order"].append(name)
    save_config(cfg)
    return True, f"Added provider '{name}'"


def remove_provider(name: str) -> tuple[bool, str]:
    name = name.lower()
    cfg = load_config()
    if name not in cfg.get("providers", {}):
        return False, f"Provider '{name}' not configured"
    del cfg["providers"][name]
    if name in cfg.get("fallback_order", []):
        cfg["fallback_order"].remove(name)
    save_config(cfg)
    return True, f"Removed '{name}'"


def list_providers() -> list[dict]:
    cfg = load_config()
    result = []
    for name, info in cfg.get("providers", {}).items():
        key_raw = info.get("api_key", "")
        key_masked = key_raw[:10] + "..." if len(key_raw) > 10 else "***"
        result.append({
            "name": name,
            "model": MODELS.get(name, "unknown"),
            "enabled": info.get("enabled", True),
            "key": key_masked,
        })
    return result


def test_provider(name: str, key: str) -> tuple[bool, str]:
    name = name.lower()
    if name not in MODELS:
        return False, f"Unknown provider '{name}'"
    if not key.strip():
        return False, "API key cannot be empty"
    handler = _HANDLERS.get(name)
    if not handler:
        return False, f"No handler for '{name}'"
    try:
        body = {"messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}
        handler(key, body, MODELS[name])
        return True, f"{name} key is valid"
    except Exception as e:
        return False, f"{name} validation failed: {e}"


def parse_actions(text: str) -> list[dict]:
    actions = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith(">>>ACTION:"):
            action_type = line[len(">>>ACTION:"):].strip()
            i += 1
            params: dict[str, str] = {}
            while i < len(lines) and not lines[i].strip().startswith("<<<END"):
                stripped = lines[i].strip()
                if ":" in stripped:
                    k, v = stripped.split(":", 1)
                    params[k.strip()] = v.strip()
                i += 1
            actions.append({"type": action_type, **params})
        i += 1
    return actions


# ── Provider implementations ──────────────────────────────────


def _call_groq(key: str, body: dict, model: str) -> str:
    body["model"] = model
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl.create_default_context()) as resp:
            data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        raise Exception(f"Groq HTTP {e.code}: {e.read().decode()[:200]}")
    except urllib.error.URLError as e:
        raise Exception(f"Groq network error: {e.reason}")
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        raise Exception(f"Groq response error: {e}")


def _call_gemini(key: str, body: dict, model: str) -> str:
    contents = []
    for msg in body.get("messages", []):
        role = "model" if msg["role"] == "assistant" else "user"
        contents.append({
            "role": role,
            "parts": [{"text": msg["content"]}],
        })
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    req = urllib.request.Request(
        url,
        data=json.dumps({"contents": contents}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl.create_default_context()) as resp:
            data = json.loads(resp.read())
            return data["candidates"][0]["content"]["parts"][0]["text"]
    except urllib.error.HTTPError as e:
        raise Exception(f"Gemini HTTP {e.code}: {e.read().decode()[:200]}")
    except urllib.error.URLError as e:
        raise Exception(f"Gemini network error: {e.reason}")
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        raise Exception(f"Gemini response error: {e}")


def _call_mistral(key: str, body: dict, model: str) -> str:
    body["model"] = model
    req = urllib.request.Request(
        "https://api.mistral.ai/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl.create_default_context()) as resp:
            data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        raise Exception(f"Mistral HTTP {e.code}: {e.read().decode()[:200]}")
    except urllib.error.URLError as e:
        raise Exception(f"Mistral network error: {e.reason}")
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        raise Exception(f"Mistral response error: {e}")


_HANDLERS: dict[str, callable] = {
    "groq": _call_groq,
    "gemini": _call_gemini,
    "mistral": _call_mistral,
}


def chat(messages: list[dict]) -> tuple[str, str, list[dict]]:
    cfg = load_config()
    order = cfg.get("fallback_order", _DEFAULT_FALLBACK_ORDER)
    last_error: str | None = None

    for name in order:
        provider = cfg.get("providers", {}).get(name)
        if not provider or not provider.get("enabled"):
            continue
        key = provider.get("api_key", "")
        model = MODELS.get(name, "unknown")
        handler = _HANDLERS.get(name)
        if not handler:
            continue
        try:
            body = {"messages": messages, "max_tokens": 4096}
            text = handler(key, body, model)
            actions = parse_actions(text)
            return text, name, actions
        except Exception as e:
            last_error = f"{name}: {e}"
            continue

    if last_error:
        return f"All providers failed. Last error: {last_error}", "", []
    return (
        "No AI providers configured. Use :aii connect <provider> <key> to add one.\n"
        f"Supported providers: {', '.join(sorted(MODELS))}",
        "",
        [],
    )


def build_context(app) -> str:
    from . import archive_cmds, directory_cmds, task_cmds  # noqa: F811

    parts: list[str] = []

    # ── Archive / directory tree ──
    archives = archive_cmds.list_archives()
    parts.append(f"Archives ({len(archives)}):")
    for a in archives:
        dirs = directory_cmds.list_directories(archive_id=a.id)
        total_tasks = 0
        done_tasks = 0
        for d in dirs:
            tasks = task_cmds.list_tasks(d.id)
            total_tasks += len(tasks)
            done_tasks += sum(1 for t in tasks if t.finished)
        parts.append(
            f"  - '{a.name}' [id:{a.id}]: {len(dirs)} dir(s), {total_tasks} task(s) ({done_tasks} done)"
        )
        for d in dirs[:10]:
            dtasks = task_cmds.list_tasks(d.id)
            total = len(dtasks)
            done = sum(1 for t in dtasks if t.finished)
            parts.append(f"    - '{d.name}' [id:{d.id}]: {total} task(s) ({done} done)")
    parts.append("")

    # ── Current location ──
    location: list[str] = []
    aid = getattr(app, "_selected_archive_id", None)
    aname = getattr(app, "_selected_archive_name", None)
    did = getattr(app, "_selected_directory_id", None)
    dname = getattr(app, "_selected_directory_name", None)
    tid = getattr(app, "_selected_task_id", None)
    tname = getattr(app, "_selected_task_name", None)
    if aid is not None and aname:
        location.append(f"Archive '{aname}' [id:{aid}]")
    if did is not None and dname:
        location.append(f"Directory '{dname}' [id:{did}]")
    if tid is not None and tname:
        location.append(f"Task '{tname}' [id:{tid}]")
    if location:
        parts.append(f"Current selection: {' > '.join(location)}")
        parts.append("")

    # ── Tasks in current view ──
    items = getattr(app, "_current_items", None)
    if items:
        total = len(items)
        parts.append(f"Tasks in current view ({total}):")
        for t in items[:30]:
            if hasattr(t, "name") and hasattr(t, "id"):
                fin = " [DONE]" if getattr(t, "finished", False) else ""
                urg = getattr(t, "urgency", "?")
                dif = getattr(t, "difficulty", "?")
                dead = getattr(t, "deadline", "none")
                parts.append(
                    f"  - #{t.id} '{t.name}' U:{urg} D:{dif} deadline:{dead}{fin}"
                )
        if total > 30:
            parts.append(f"  ... ({total - 30} more)")
        parts.append("")

    # ── Action reference ──
    parts.append(
        "To perform actions, include blocks in this format (multiple OK per response):"
    )
    parts.append("")
    parts.append(">>>ACTION:CREATE_TASK")
    parts.append("name: Task name")
    parts.append("description: Description (optional)")
    parts.append("urgency: 1-5 (optional, default 1)")
    parts.append("difficulty: 1-5 (optional, default 1)")
    parts.append("deadline: YYYY-MM-DD (optional)")
    parts.append("time_dedicated: minutes (optional)")
    parts.append("directory_id: id (optional, defaults to current)")
    parts.append("<<<END")
    parts.append("")
    parts.append(">>>ACTION:CREATE_DIRECTORY")
    parts.append("name: Directory name")
    parts.append("archive_id: id (optional, defaults to current)")
    parts.append("<<<END")
    parts.append("")
    parts.append(">>>ACTION:CREATE_ARCHIVE")
    parts.append("name: Archive name")
    parts.append("<<<END")
    parts.append("")
    parts.append(">>>ACTION:FINISH_TASK")
    parts.append("task_id: id (optional, defaults to selected)")
    parts.append("<<<END")
    parts.append("")
    parts.append(">>>ACTION:ADD_NOTE")
    parts.append("task_id: id (optional, defaults to selected)")
    parts.append("note: Note text")
    parts.append("<<<END")

    return "\n".join(parts)


def build_cli_context() -> str:
    from . import archive_cmds, directory_cmds, task_cmds

    parts: list[str] = []

    archives = archive_cmds.list_archives()
    parts.append(f"Archives ({len(archives)}):")
    for a in archives:
        dirs = directory_cmds.list_directories(archive_id=a.id)
        total_tasks = 0
        done_tasks = 0
        for d in dirs:
            tasks = task_cmds.list_tasks(d.id)
            total_tasks += len(tasks)
            done_tasks += sum(1 for t in tasks if t.finished)
        parts.append(
            f"  - '{a.name}' [id:{a.id}]: {len(dirs)} dir(s), {total_tasks} task(s) ({done_tasks} done)"
        )
        for d in dirs[:10]:
            dtasks = task_cmds.list_tasks(d.id)
            total = len(dtasks)
            done = sum(1 for t in dtasks if t.finished)
            parts.append(f"    - '{d.name}' [id:{d.id}]: {total} task(s) ({done} done)")
    parts.append("")

    parts.append(
        "To perform actions, include blocks in this format (multiple OK per response):"
    )
    parts.append("")
    parts.append(">>>ACTION:CREATE_TASK")
    parts.append("name: Task name")
    parts.append("description: Description (optional)")
    parts.append("urgency: 1-5 (optional, default 1)")
    parts.append("difficulty: 1-5 (optional, default 1)")
    parts.append("deadline: YYYY-MM-DD (optional)")
    parts.append("time_dedicated: minutes (optional)")
    parts.append("directory_id: id (optional, uses first directory if not specified)")
    parts.append("<<<END")
    parts.append("")
    parts.append(">>>ACTION:CREATE_DIRECTORY")
    parts.append("name: Directory name")
    parts.append("archive_id: id (optional, uses first archive if not specified)")
    parts.append("<<<END")
    parts.append("")
    parts.append(">>>ACTION:CREATE_ARCHIVE")
    parts.append("name: Archive name")
    parts.append("<<<END")
    parts.append("")
    parts.append(">>>ACTION:FINISH_TASK")
    parts.append("task_id: id")
    parts.append("<<<END")
    parts.append("")
    parts.append(">>>ACTION:ADD_NOTE")
    parts.append("task_id: id")
    parts.append("note: Note text")
    parts.append("<<<END")

    return "\n".join(parts)
