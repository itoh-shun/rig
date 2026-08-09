"""Interactive localhost Mission Control for RIG.

The browser is a presentation/interaction surface, never a second policy engine.
Every mutating request is translated into an argv list for ``rig_workbench.cli``
and executed without a shell, so the existing workbench/governance enforcement
remains the only authority for accept, discard and approvals.

Security boundary (v2):
- binds to loopback only;
- random per-process CSRF token required on every POST;
- no CORS headers / no wildcard origins;
- no ``--force`` endpoint;
- destructive discard needs the exact task id as confirmation;
- actor identity is never accepted from the browser payload;
- command output and failures are returned verbatim enough for operators to see
  what the real RIG command accepted or refused.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import pathlib
import re
import secrets
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .mission_control import build_snapshot
from .workbench.config import TASK_TYPES
from .workbench.reporting import read_all_tasks
from .workbench.state import gate_status, load_json, runs_dir

MAX_BODY_BYTES = 64 * 1024
COMMAND_TIMEOUT_SECONDS = 120
POLL_MS = 2000
_TASK_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def _json_file(path: pathlib.Path, default: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _task_dir(root: pathlib.Path, task_id: str) -> pathlib.Path:
    if not _TASK_ID.fullmatch(task_id):
        raise ValueError("invalid task id")
    path = root / ".rig" / "runs" / task_id
    if not (path / "task.json").is_file():
        raise ValueError(f"task not found: {task_id}")
    return path


def task_detail(root: pathlib.Path, task_id: str) -> dict[str, Any]:
    """Presentation-neutral task detail assembled from existing workbench artifacts."""
    base = _task_dir(root, task_id)
    task = _json_file(base / "task.json", {})
    steps = _json_file(base / "steps.json", {"steps": []})
    acceptance = _json_file(base / "acceptance.json", {"checks": []})
    review = _json_file(base / "review.json", {})
    outcome = _json_file(base / "outcome.json", None)
    if isinstance(acceptance, dict) and acceptance.get("checks"):
        acceptance = dict(acceptance)
        acceptance["status"] = gate_status(acceptance)
    return {
        "task": task,
        "steps": steps,
        "acceptance": acceptance,
        "review": review,
        "outcome": outcome,
    }


def live_snapshot(root: pathlib.Path) -> dict[str, Any]:
    """Mission Control snapshot plus the small task index needed by the live UI."""
    snapshot = build_snapshot(root)
    base = runs_dir(root)
    tasks = read_all_tasks(base)
    tasks.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)
    index = []
    for task in tasks:
        task_id = str(task.get("task_id") or "")
        acceptance = load_json(base / task_id / "acceptance.json", {"checks": []})
        gate = gate_status(acceptance) if acceptance.get("checks") else "unmeasured"
        index.append({
            "task_id": task_id,
            "input": task.get("input") or "",
            "task_type": task.get("task_type") or "?",
            "recipe": task.get("recipe"),
            "status": task.get("status") or "?",
            "gate": gate,
            "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
        })
    snapshot["live"] = {
        "poll_ms": POLL_MS,
        "task_types": sorted(TASK_TYPES),
        "tasks": index,
        "interactive": True,
        "force_available": False,
    }
    return snapshot


def _run_cli(root: pathlib.Path, argv: list[str]) -> dict[str, Any]:
    """Execute the canonical RIG CLI without a shell and capture its decision."""
    env = dict(os.environ)
    env["RIG_INVOKER"] = "mission-control/v2"
    command = [sys.executable, "-m", "rig_workbench.cli", *argv]
    try:
        proc = subprocess.run(
            command,
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "exit_code": None,
            "stdout": exc.stdout or "",
            "stderr": f"command timed out after {COMMAND_TIMEOUT_SECONDS}s",
            "argv": argv,
        }
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "argv": argv,
    }


def action_argv(action: str, task_id: str | None, payload: dict[str, Any]) -> list[str]:
    """Map a GUI action to the existing CLI. No policy decisions happen here."""
    if action == "new":
        text = str(payload.get("input") or "").strip()
        task_type = str(payload.get("task_type") or "").strip()
        if not text or len(text) > 4000:
            raise ValueError("task input is required and must be <= 4000 characters")
        if task_type not in TASK_TYPES:
            raise ValueError(f"unknown task type: {task_type!r}")
        return ["wb", "new", text, "--type", task_type]

    if task_id is None or not _TASK_ID.fullmatch(task_id):
        raise ValueError("a valid task id is required")

    if action == "accept":
        # There is intentionally no browser representation of --force.
        return ["wb", "accept", task_id]
    if action == "discard":
        if payload.get("confirm") != task_id:
            raise ValueError("discard requires exact task-id confirmation")
        return ["wb", "discard", task_id, "--yes"]
    if action == "approval":
        decision = str(payload.get("decision") or "")
        if decision not in {"grant", "deny"}:
            raise ValueError("approval decision must be grant|deny")
        argv = ["govern", "approve", decision, task_id]
        note = str(payload.get("note") or "").strip()
        if note:
            argv += ["--note", note[:2000]]
        # No --actor from the browser: governance resolves the real local actor.
        return argv
    if action == "outcome":
        status = str(payload.get("status") or "")
        if status not in {"ok", "incident"}:
            raise ValueError("outcome status must be ok|incident")
        argv = ["wb", "record-outcome", task_id, "--status", status]
        note = str(payload.get("note") or "").strip()
        if note:
            argv += ["--note", note[:4000]]
        return argv
    raise ValueError(f"unsupported action: {action}")


CSS = r"""
:root{color-scheme:light dark;--bg:#f4f6f8;--panel:#fff;--panel2:#f8fafc;--ink:#111418;--muted:#6d7580;--line:#dce1e7;--accent:#465fff;--good:#0b7a55;--warn:#a86700;--bad:#b42318;--shadow:0 12px 30px rgba(20,30,50,.06)}
@media(prefers-color-scheme:dark){:root{--bg:#0b0f14;--panel:#121820;--panel2:#171e27;--ink:#edf2f7;--muted:#98a4b3;--line:#29323d;--accent:#91a1ff;--good:#68d6aa;--warn:#f2b84b;--bad:#ff8d87;--shadow:none}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}button,input,select,textarea{font:inherit}.shell{min-height:100vh;display:grid;grid-template-rows:64px 1fr}.topbar{display:flex;align-items:center;justify-content:space-between;padding:0 22px;border-bottom:1px solid var(--line);background:color-mix(in srgb,var(--panel) 92%,transparent);position:sticky;top:0;z-index:5}.brand{display:flex;align-items:center;gap:12px}.brand b{font-size:17px}.live{font:700 10px ui-monospace,SFMono-Regular,monospace;letter-spacing:.12em;color:var(--good);display:flex;gap:7px;align-items:center}.dot{width:8px;height:8px;border-radius:50%;background:var(--good);box-shadow:0 0 0 4px color-mix(in srgb,var(--good) 15%,transparent)}.layout{display:grid;grid-template-columns:320px minmax(0,1fr);min-height:0}.sidebar{border-right:1px solid var(--line);padding:16px;overflow:auto;background:var(--panel)}.main{padding:24px;overflow:auto;max-width:1500px;width:100%;margin:auto}.section-title{display:flex;align-items:center;justify-content:space-between;margin:0 0 10px;font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}.new-task{display:grid;gap:8px;padding:12px;border:1px solid var(--line);border-radius:13px;background:var(--panel2);margin-bottom:18px}.new-task textarea{min-height:68px;resize:vertical}.field{width:100%;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:9px;padding:9px 10px}.btn{border:1px solid var(--line);background:var(--panel);color:var(--ink);padding:8px 11px;border-radius:9px;cursor:pointer;font-weight:650;font-size:12px}.btn:hover{border-color:var(--accent)}.btn.primary{background:var(--accent);border-color:var(--accent);color:white}.btn.danger{color:var(--bad)}.btn.good{color:var(--good)}.btn:disabled{opacity:.45;cursor:not-allowed}.task-list{display:grid;gap:7px}.task-item{border:1px solid var(--line);border-radius:11px;padding:10px;cursor:pointer;background:var(--panel)}.task-item:hover,.task-item.active{border-color:var(--accent);box-shadow:var(--shadow)}.task-meta{display:flex;justify-content:space-between;gap:8px;color:var(--muted);font-size:10px}.task-name{font-size:12px;margin:6px 0 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.pill{display:inline-block;padding:2px 6px;border-radius:999px;background:var(--panel2);border:1px solid var(--line);font:650 9px ui-monospace,SFMono-Regular,monospace}.metrics{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px}.metric,.card{border:1px solid var(--line);background:var(--panel);border-radius:14px;box-shadow:var(--shadow)}.metric{padding:14px}.metric .label{font-size:9px;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);font-weight:700}.metric .value{font-size:25px;font-weight:760;margin-top:5px}.metric .detail{font-size:10px;color:var(--muted);margin-top:3px}.card{padding:16px;margin-top:14px}.core{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:7px}.core-step{border:1px solid var(--line);border-radius:10px;padding:10px;background:var(--panel2)}.core-step b{display:block;margin-top:3px}.detail-grid{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(300px,.65fr);gap:14px}.headline{display:flex;justify-content:space-between;gap:18px;align-items:flex-start}.headline h2{margin:2px 0 6px;font-size:22px}.muted{color:var(--muted)}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}.actions{display:flex;flex-wrap:wrap;gap:7px;margin-top:14px}.progress-list{display:grid;gap:7px;margin-top:11px}.progress-row{display:grid;grid-template-columns:18px minmax(0,1fr) auto;gap:8px;align-items:center;font-size:12px}.checks{display:grid;gap:6px}.check{display:grid;grid-template-columns:18px minmax(0,1fr);gap:7px;padding:7px 0;border-bottom:1px solid var(--line);font-size:11px}.check:last-child{border:0}.ok{color:var(--good)}.warn{color:var(--warn)}.bad{color:var(--bad)}.output{white-space:pre-wrap;max-height:280px;overflow:auto;background:#0b0f14;color:#d9e1ea;border-radius:10px;padding:12px;font:11px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}.toast{position:fixed;right:20px;bottom:20px;max-width:520px;border:1px solid var(--line);background:var(--panel);border-radius:12px;padding:12px 14px;box-shadow:0 14px 50px rgba(0,0,0,.2);display:none;z-index:10}.toast.show{display:block}.empty{padding:28px;border:1px dashed var(--line);border-radius:14px;text-align:center;color:var(--muted)}
@media(max-width:1000px){.layout{grid-template-columns:1fr}.sidebar{border-right:0;border-bottom:1px solid var(--line);max-height:420px}.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.detail-grid{grid-template-columns:1fr}.core{grid-template-columns:1fr}}
"""


JS_TEMPLATE = r"""
const CSRF=__CSRF__;
let selected=null;
let snapshot=null;
const $=(q)=>document.querySelector(q);
const esc=(s)=>String(s??'').replace(/[&<>\"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[m]));
async function api(path, options={}){
  const headers={'Accept':'application/json',...(options.headers||{})};
  if(options.method==='POST'){headers['Content-Type']='application/json';headers['X-RIG-CSRF']=CSRF;}
  const r=await fetch(path,{...options,headers});
  const data=await r.json();
  if(!r.ok) throw Object.assign(new Error(data.error||data.stderr||`HTTP ${r.status}`),{data});
  return data;
}
function toast(title, data){const el=$('#toast');el.innerHTML=`<b>${esc(title)}</b>${data?`<pre class="output">${esc((data.stdout||'')+(data.stderr?`\n${data.stderr}`:''))}</pre>`:''}`;el.classList.add('show');setTimeout(()=>el.classList.remove('show'),8000);}
function metric(label,value,detail=''){return `<div class="metric"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div><div class="detail">${esc(detail)}</div></div>`}
function renderOverview(){const o=snapshot.operations,p=snapshot.production,u=o.token_usage||{},tokens=u.prompt_tokens!=null?(u.prompt_tokens||0)+(u.completion_tokens||0):'unmeasured';$('#metrics').innerHTML=metric('active tasks',o.tasks_active,`${o.tasks_total} total`)+metric('gate failures',(o.gate_counts||{}).failed||0,'recorded history')+metric('RIG tokens',Object.keys(u).length?tokens:'unmeasured',Object.keys(u).length?`${u.calls||0} metered calls`:'provider may not expose usage')+metric('incident rate',p.incident_rate_pct==null?'unmeasured':`${p.incident_rate_pct}%`,`${p.incidents} / ${p.outcomes_recorded} outcomes`)+metric('outcome coverage',p.outcome_coverage_pct==null?'unmeasured':`${p.outcome_coverage_pct}%`,`${p.outcomes_recorded} / ${p.accepted_tasks} accepted`);
  $('#core').innerHTML=snapshot.core.map((s,i)=>`<div class="core-step"><span class="mono muted">0${i+1}</span><b>${esc(s.label)}</b><span class="muted" style="font-size:10px">${esc(s.meaning)}</span></div>`).join('');}
function renderTasks(){const tasks=snapshot.live.tasks;$('#task-list').innerHTML=tasks.length?tasks.map(t=>`<div class="task-item ${selected===t.task_id?'active':''}" data-id="${esc(t.task_id)}"><div class="task-meta"><span class="pill">${esc(t.status)}</span><span>${esc(t.gate)}</span></div><div class="task-name">${esc(t.input)}</div><div class="task-meta"><span>${esc(t.task_type)}</span><span class="mono">${esc(t.task_id)}</span></div></div>`).join(''):'<div class="empty">No RIG tasks yet.</div>';document.querySelectorAll('.task-item').forEach(el=>el.onclick=()=>selectTask(el.dataset.id));}
function statusIcon(s){return s==='passed'?'✓':s==='failed'?'✕':s==='warning'?'!':s==='running'?'●':'○'}
async function selectTask(id){selected=id;renderTasks();const d=await api(`/api/tasks/${encodeURIComponent(id)}`);renderDetail(d);}
function renderDetail(d){const t=d.task||{},a=d.acceptance||{},steps=(d.steps||{}).steps||[],checks=a.checks||[],out=d.outcome;let html=`<div class="card"><div class="headline"><div><span class="pill">${esc(t.status||'?')}</span><h2>${esc(t.input||t.task_id)}</h2><div class="muted mono">${esc(t.task_id)} · ${esc(t.task_type||'?')} ${t.recipe?`· ${esc(t.recipe)}`:''}</div></div><div class="mono muted">gate: ${esc(a.status||'unmeasured')}</div></div><div class="actions"><button class="btn" onclick="showDiff()">View Diff</button><button class="btn good" onclick="acceptTask()">Accept</button><button class="btn danger" onclick="discardTask()">Discard</button><button class="btn" onclick="approval('grant')">Approve</button><button class="btn" onclick="approval('deny')">Deny</button>${t.status==='accepted'?'<button class="btn" onclick="outcome(\'ok\')">Outcome: OK</button><button class="btn danger" onclick="outcome(\'incident\')">Outcome: Incident</button>':''}</div></div>`;
  html+=`<div class="detail-grid"><div class="card"><div class="section-title">Execution</div><div class="progress-list">${steps.length?steps.map(s=>`<div class="progress-row"><span class="${s.status==='passed'?'ok':s.status==='failed'?'bad':''}">${statusIcon(s.status)}</span><span>${esc(s.name)}</span><span class="mono muted">${esc(s.status)}</span></div>`).join(''):'<span class="muted">No step telemetry.</span>'}</div></div><div class="card"><div class="section-title">Acceptance</div><div class="checks">${checks.length?checks.map(c=>`<div class="check"><span class="${c.status==='passed'?'ok':c.status==='failed'?'bad':'warn'}">${statusIcon(c.status)}</span><span><b>${esc(c.name)}</b>${c.detail?`<br><span class="muted">${esc(c.detail)}</span>`:''}</span></div>`).join(''):'<span class="muted">Unmeasured.</span>'}</div>${out?`<div class="section-title" style="margin-top:14px">Production outcome</div><div>${esc(out.status)} <span class="muted">${esc(out.note||'')}</span></div>`:''}</div></div>`;$('#detail').innerHTML=html;}
async function perform(action,payload={}){if(!selected) return;try{const d=await api(`/api/tasks/${encodeURIComponent(selected)}/${action}`,{method:'POST',body:JSON.stringify(payload)});toast(`${action}: ${d.ok?'completed':'refused'}`,d);await refresh();await selectTask(selected);}catch(e){toast(`${action}: refused`,e.data||{stderr:e.message});}}
async function showDiff(){try{const d=await api(`/api/tasks/${encodeURIComponent(selected)}/diff`);toast('Diff',d);}catch(e){toast('Diff failed',e.data||{stderr:e.message});}}
async function acceptTask(){if(confirm('Accept this task? RIG Core will re-check gate, approvals, policy and worktree state.')) await perform('accept');}
async function discardTask(){const c=prompt(`Destructive action. Type the exact task id to discard:\n${selected}`);if(c===selected) await perform('discard',{confirm:c});}
async function approval(decision){const note=prompt(`${decision==='grant'?'Approval':'Denial'} note (optional):`,'')??'';await perform('approval',{decision,note});}
async function outcome(status){const note=prompt(`Record production outcome: ${status}\nNote (optional):`,'')??'';await perform('outcome',{status,note});}
async function newTask(){const input=$('#new-input').value.trim(),task_type=$('#new-type').value;if(!input)return;try{const d=await api('/api/tasks',{method:'POST',body:JSON.stringify({input,task_type})});toast('Task registered',d);$('#new-input').value='';await refresh();}catch(e){toast('Task creation refused',e.data||{stderr:e.message});}}
async function refresh(){try{snapshot=await api('/api/snapshot');$('#live-state').textContent='LIVE';renderOverview();renderTasks();if(!selected&&snapshot.live.tasks.length){selected=snapshot.live.tasks[0].task_id;renderTasks();await selectTask(selected);}$('#new-type').innerHTML=snapshot.live.task_types.map(x=>`<option>${esc(x)}</option>`).join('');}catch(e){$('#live-state').textContent='OFFLINE';}}
$('#new-button').onclick=newTask;$('#refresh').onclick=refresh;refresh();setInterval(refresh,2000);
"""


def interactive_html(csrf_token: str) -> str:
    js = JS_TEMPLATE.replace("__CSRF__", json.dumps(csrf_token))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>RIG Mission Control · Live</title><style>{CSS}</style></head><body><div class="shell"><div class="topbar"><div class="brand"><b>RIG Mission Control</b><span class="live"><span class="dot"></span><span id="live-state">CONNECTING</span></span></div><button class="btn" id="refresh">Refresh</button></div><div class="layout"><aside class="sidebar"><div class="section-title">New Task</div><div class="new-task"><textarea class="field" id="new-input" placeholder="What should RIG work on?"></textarea><select class="field" id="new-type"></select><button class="btn primary" id="new-button">Register isolated task</button></div><div class="section-title">Runs</div><div id="task-list" class="task-list"></div></aside><main class="main"><div class="section-title">RIG Core</div><div class="core" id="core"></div><div class="section-title" style="margin-top:20px">Now</div><div class="metrics" id="metrics"></div><div id="detail" style="margin-top:20px"><div class="empty">Select a task.</div></div></main></div></div><div class="toast" id="toast"></div><script>{js}</script></body></html>"""


class MissionControlHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], root: pathlib.Path, csrf_token: str):
        super().__init__(address, MissionControlHandler)
        self.root = root
        self.csrf_token = csrf_token
        host, port = self.server_address[:2]
        self.allowed_origins = {
            f"http://127.0.0.1:{port}",
            f"http://localhost:{port}",
        }


class MissionControlHandler(BaseHTTPRequestHandler):
    server: MissionControlHTTPServer

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("[mission-control] " + (fmt % args) + "\n")

    def _json(self, status: int, payload: object) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _html(self, text: str) -> None:
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _payload(self) -> dict[str, Any]:
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if size <= 0 or size > MAX_BODY_BYTES:
            raise ValueError("request body must be non-empty and <= 64 KiB")
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            raise ValueError("Content-Type must be application/json")
        try:
            value = json.loads(self.rfile.read(size).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("request body is not valid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def _authorize_post(self) -> bool:
        if not secrets.compare_digest(self.headers.get("X-RIG-CSRF", ""), self.server.csrf_token):
            self._json(403, {"error": "invalid or missing CSRF token"})
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in self.server.allowed_origins:
            self._json(403, {"error": "origin is not this Mission Control server"})
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        try:
            if path == "/":
                self._html(interactive_html(self.server.csrf_token))
                return
            if path == "/api/snapshot":
                self._json(200, live_snapshot(self.server.root))
                return
            match = re.fullmatch(r"/api/tasks/([^/]+)(/diff)?", path)
            if match:
                task_id = urllib.parse.unquote(match.group(1))
                _task_dir(self.server.root, task_id)
                if match.group(2):
                    result = _run_cli(self.server.root, ["wb", "diff", task_id])
                    self._json(200 if result["ok"] else 409, result)
                else:
                    self._json(200, task_detail(self.server.root, task_id))
                return
            self._json(404, {"error": "not found"})
        except (OSError, ValueError) as exc:
            self._json(400, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorize_post():
            return
        path = urllib.parse.urlsplit(self.path).path
        try:
            payload = self._payload()
            if path == "/api/tasks":
                argv = action_argv("new", None, payload)
            else:
                match = re.fullmatch(r"/api/tasks/([^/]+)/(accept|discard|approval|outcome)", path)
                if not match:
                    self._json(404, {"error": "not found"})
                    return
                task_id = urllib.parse.unquote(match.group(1))
                _task_dir(self.server.root, task_id)
                argv = action_argv(match.group(2), task_id, payload)
            result = _run_cli(self.server.root, argv)
            self._json(200 if result["ok"] else 409, result)
        except (OSError, ValueError) as exc:
            self._json(400, {"error": str(exc)})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rig-mission-control serve", description="interactive localhost RIG Mission Control")
    parser.add_argument("--repo", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="do not open the browser automatically")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if not (1 <= args.port <= 65535):
        raise SystemExit("--port must be between 1 and 65535")
    root = args.repo.resolve()
    token = secrets.token_urlsafe(32)
    server = MissionControlHTTPServer(("127.0.0.1", args.port), root, token)
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/"
    print(f"RIG Mission Control v2: {url}")
    print(f"repo: {root}")
    print("security: loopback-only · per-process CSRF · no GUI force bypass")
    if not args.no_open:
        threading.Timer(0.2, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nMission Control stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
