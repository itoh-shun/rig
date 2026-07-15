#!/usr/bin/env python3
"""rig MCP server (#263).

A thin MCP adapter that lets rig be driven from outside a Claude Code session
(another agent, CI, a separate process). No new execution engine — every tool
just shells out to the existing `scripts/workbench.py` / `scripts/orchestrate.py`
CLIs. accept/discard's force-proof requirements (accept_requirements, gate
verdicts) go through the exact same code path as the CLI, so they can't be
bypassed via MCP.

Protocol: the Model Context Protocol stdio transport (JSON-RPC 2.0,
line-delimited: one message per line), implemented with the stdlib only —
no dependency on the third-party `mcp` SDK, matching workbench.py/
orchestrate.py's "no heavy dependencies" stance.

Run: `python3 scripts/mcp_server.py` (listens for JSON-RPC on stdin/stdout).
opt-in: nothing changes unless this server is started. Existing CLI/skill
usage is unaffected.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKBENCH = os.path.join(ROOT, "scripts", "workbench.py")
ORCHESTRATE = os.path.join(ROOT, "scripts", "orchestrate.py")

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "rig", "version": "1"}


def _run(script: str, args: list) -> dict:
    try:
        proc = subprocess.run(
            [sys.executable, script] + args,
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=900,
        )
    except subprocess.TimeoutExpired as e:
        return {"ok": False, "text": f"[timeout] {e}"}
    text = (proc.stdout or "") + (proc.stderr or "")
    return {"ok": proc.returncode == 0, "text": text.strip() or f"(exit {proc.returncode}, no output)"}


def _opt(args: list, flag: str, value) -> list:
    if value is None or value is False:
        return args
    if value is True:
        return args + [flag]
    return args + [flag, str(value)]


# ---- workbench.py (per-task acceptance-gate management) --------------------

def t_task_new(a):
    args = ["new", a["input"], "--type", a.get("type", "feature")]
    args = _opt(args, "--slug", a.get("slug"))
    args = _opt(args, "--recipe", a.get("recipe"))
    args = _opt(args, "--base", a.get("base"))
    args = _opt(args, "--no-worktree", a.get("no_worktree"))
    args = _opt(args, "--budget-minutes", a.get("budget_minutes"))
    return _run(WORKBENCH, args)


def t_task_status(a):
    args = ["status"]
    if a.get("task_id"):
        args.append(a["task_id"])
    return _run(WORKBENCH, args)


def t_task_board(a):
    args = ["board"]
    args = _opt(args, "--all", a.get("all"))
    return _run(WORKBENCH, args)


def t_task_diff(a):
    args = ["diff"]
    if a.get("task_id"):
        args.append(a["task_id"])
    return _run(WORKBENCH, args)


def t_task_gate(a):
    args = ["gate"]
    for kv in a.get("set", []) or []:
        args += ["--set", kv]
    if a.get("task_id"):
        args.append(a["task_id"])
    return _run(WORKBENCH, args)


def t_task_accept(a):
    args = ["accept"]
    args = _opt(args, "--force", a.get("force"))
    if a.get("task_id"):
        args.append(a["task_id"])
    return _run(WORKBENCH, args)


def t_task_discard(a):
    args = ["discard", "--yes"]
    if a.get("task_id"):
        args.append(a["task_id"])
    return _run(WORKBENCH, args)


def t_task_log(_a):
    return _run(WORKBENCH, ["log"])


# ---- orchestrate.py (the deterministic recipe-DAG engine) ------------------

def t_orchestrate_init(a):
    args = ["init", a["recipe"]]
    args = _opt(args, "--goal", a.get("goal"))
    args = _opt(args, "--out", a.get("run_state_path"))
    return _run(ORCHESTRATE, args)


def t_orchestrate_next(a):
    args = ["next"]
    if a.get("run_state_path"):
        args.append(a["run_state_path"])
    return _run(ORCHESTRATE, args)


def t_orchestrate_check(a):
    args = ["check"]
    if a.get("run_state_path"):
        args.append(a["run_state_path"])
    return _run(ORCHESTRATE, args)


def t_orchestrate_status(a):
    args = ["status"]
    if a.get("run_state_path"):
        args.append(a["run_state_path"])
    return _run(ORCHESTRATE, args)


def t_orchestrate_run(a):
    args = ["run", a["recipe"], "--provider", a.get("provider", "mock")]
    args = _opt(args, "--verifier-provider", a.get("verifier_provider"))
    args = _opt(args, "--goal", a.get("goal"))
    args = _opt(args, "--isolate", a.get("isolate"))
    args = _opt(args, "--auto-route", a.get("auto_route"))
    args = _opt(args, "--out", a.get("run_state_path"))
    return _run(ORCHESTRATE, args)


def t_orchestrate_runs(a):
    args = ["runs"]
    args = _opt(args, "--cost", a.get("cost"))
    return _run(ORCHESTRATE, args)


TOOLS = {
    "rig_task_new": {
        "fn": t_task_new,
        "description": "Register a natural-language task and create its isolated worktree (thin wrap of workbench.py new)",
        "input_schema": {
            "type": "object",
            "properties": {
                "input": {"type": "string", "description": "The user's natural-language task"},
                "type": {"type": "string", "description": "task_type (default feature)"},
                "slug": {"type": "string"},
                "recipe": {"type": "string"},
                "base": {"type": "string"},
                "no_worktree": {"type": "boolean"},
                "budget_minutes": {"type": "number"},
            },
            "required": ["input"],
        },
    },
    "rig_task_status": {
        "fn": t_task_status,
        "description": "Show the current (or a named task's) execution state",
        "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}}},
    },
    "rig_task_board": {
        "fn": t_task_board,
        "description": "List all tasks in a dashboard view (active only by default)",
        "input_schema": {"type": "object", "properties": {"all": {"type": "boolean"}}},
    },
    "rig_task_diff": {
        "fn": t_task_diff,
        "description": "Show the structured diff summary against base",
        "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}}},
    },
    "rig_task_gate": {
        "fn": t_task_gate,
        "description": "Record and judge acceptance-gate criteria",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "set": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "list of 'criterion=status[:detail]'",
                },
            },
        },
    },
    "rig_task_accept": {
        "fn": t_task_accept,
        "description": (
            "Check accept_requirements and the gate, then squash-merge into the main worktree. "
            "force-proof requirements (worktree_exists/base_branch_recorded/diff_summary_generated) "
            "cannot be bypassed via MCP — workbench.py's own checks run unchanged."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}, "force": {"type": "boolean"}},
        },
    },
    "rig_task_discard": {
        "fn": t_task_discard,
        "description": "Discard the worktree and branch (run log is kept). Destructive.",
        "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}}},
    },
    "rig_task_log": {
        "fn": t_task_log,
        "description": "List past run logs",
        "input_schema": {"type": "object", "properties": {}},
    },
    "rig_orchestrate_init": {
        "fn": t_orchestrate_init,
        "description": "Initialize run-state.json from a recipe (orchestrate.py init)",
        "input_schema": {
            "type": "object",
            "properties": {
                "recipe": {"type": "string"},
                "goal": {"type": "string"},
                "run_state_path": {"type": "string"},
            },
            "required": ["recipe"],
        },
    },
    "rig_orchestrate_next": {
        "fn": t_orchestrate_next,
        "description": "Deterministically compute the next transition (START/ADVANCE/RETRY/AWAIT/BLOCKED/ESCALATE/DONE)",
        "input_schema": {"type": "object", "properties": {"run_state_path": {"type": "string"}}},
    },
    "rig_orchestrate_check": {
        "fn": t_orchestrate_check,
        "description": "Run the current step's checks: (lint/test/etc.) — a machine sensor",
        "input_schema": {"type": "object", "properties": {"run_state_path": {"type": "string"}}},
    },
    "rig_orchestrate_status": {
        "fn": t_orchestrate_status,
        "description": "Show the current run-state",
        "input_schema": {"type": "object", "properties": {"run_state_path": {"type": "string"}}},
    },
    "rig_orchestrate_run": {
        "fn": t_orchestrate_run,
        "description": "Run every step autonomously, each as a separate-process rig harness (--isolate for worktree isolation)",
        "input_schema": {
            "type": "object",
            "properties": {
                "recipe": {"type": "string"},
                "provider": {"type": "string"},
                "verifier_provider": {"type": "string"},
                "goal": {"type": "string"},
                "isolate": {"type": "boolean"},
                "auto_route": {"type": "boolean"},
                "run_state_path": {"type": "string"},
            },
            "required": ["recipe"],
        },
    },
    "rig_orchestrate_runs": {
        "fn": t_orchestrate_runs,
        "description": "Show aggregated run history / gap prescriptions (--cost for token/cost rollups)",
        "input_schema": {"type": "object", "properties": {"cost": {"type": "boolean"}}},
    },
}


def _tools_list_result() -> dict:
    tools = []
    for name, spec in TOOLS.items():
        tools.append(
            {
                "name": name,
                "description": spec["description"],
                "inputSchema": spec["input_schema"],
            }
        )
    return {"tools": tools}


def _tools_call_result(name: str, arguments: dict) -> dict:
    spec = TOOLS.get(name)
    if spec is None:
        return {
            "content": [{"type": "text", "text": f"unknown tool: {name}"}],
            "isError": True,
        }
    result = spec["fn"](arguments or {})
    return {
        "content": [{"type": "text", "text": result["text"]}],
        "isError": not result["ok"],
    }


def _handle(msg: dict):
    method = msg.get("method")
    msg_id = msg.get("id")
    is_notification = msg_id is None

    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": _tools_list_result()}
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        return {"jsonrpc": "2.0", "id": msg_id, "result": _tools_call_result(name, arguments)}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if is_notification:
        return None
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            sys.stderr.write(f"[mcp_server] bad JSON: {e}\n")
            continue
        try:
            reply = _handle(msg)
        except Exception as e:  # noqa: BLE001 — one bad message must not crash the server
            reply = {
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "error": {"code": -32000, "message": str(e)},
            }
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
