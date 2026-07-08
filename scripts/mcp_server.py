#!/usr/bin/env python3
"""rig MCP server（#263）。

Claude Codeセッション外（別エージェント・CI・別プロセス）からrigを操作できるようにする
薄いMCPアダプタ。新しい実行エンジンは持たない——全ツールはサブプロセスで
`scripts/workbench.py` / `scripts/orchestrate.py` をそのまま呼ぶだけ。
accept/discardのforce-proof要件（accept_requirements・gate判定）はCLIと完全に同じ
コードパスを通るため、MCP経由でもバイパスできない。

プロトコル: Model Context Protocol の stdio transport（JSON-RPC 2.0、
1メッセージ=1行のline-delimited JSON）を stdlib のみで実装する。
`mcp` SDK（サードパーティ製）には依存しない——workbench.py/orchestrate.py と同じ
「重い依存を増やさない」方針をMCPサーバにも適用する。

起動: `python3 scripts/mcp_server.py`（stdin/stdoutでJSON-RPCを待ち受ける）
opt-in: このサーバを起動しない限り何も変わらない。既存のCLI/skill経由の利用はそのまま。
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


# ---- workbench.py（タスク単位のacceptance-gate管理） -----------------

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


# ---- orchestrate.py（recipe-DAGの決定論エンジン） ---------------------

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
        "description": "自然文タスクを登録しisolated worktreeを作成する（workbench.py new の薄いラップ）",
        "input_schema": {
            "type": "object",
            "properties": {
                "input": {"type": "string", "description": "ユーザーの自然文タスク"},
                "type": {"type": "string", "description": "task_type（既定 feature）"},
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
        "description": "現在（または指定task）の実行状態を表示する",
        "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}}},
    },
    "rig_task_board": {
        "fn": t_task_board,
        "description": "全taskを一覧するダッシュボード（既定はアクティブのみ）",
        "input_schema": {"type": "object", "properties": {"all": {"type": "boolean"}}},
    },
    "rig_task_diff": {
        "fn": t_task_diff,
        "description": "baseからの変更差分を構造化表示する",
        "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}}},
    },
    "rig_task_gate": {
        "fn": t_task_gate,
        "description": "acceptance-gate基準の合否を記録・判定する",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "set": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "'criterion=status[:detail]' の列",
                },
            },
        },
    },
    "rig_task_accept": {
        "fn": t_task_accept,
        "description": (
            "accept_requirementsとgateを確認しメイン作業ツリーへsquash反映する。"
            "force-proof要件(worktree_exists/base_branch_recorded/diff_summary_generated)は"
            "MCP経由でもバイパスできない——workbench.py本体のチェックがそのまま効く。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}, "force": {"type": "boolean"}},
        },
    },
    "rig_task_discard": {
        "fn": t_task_discard,
        "description": "worktreeとbranchを破棄する（run logは残す）。破壊的操作。",
        "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}}},
    },
    "rig_task_log": {
        "fn": t_task_log,
        "description": "過去の実行ログを一覧する",
        "input_schema": {"type": "object", "properties": {}},
    },
    "rig_orchestrate_init": {
        "fn": t_orchestrate_init,
        "description": "recipeからrun-state.jsonを初期化する（orchestrate.py init）",
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
        "description": "次の遷移を決定論的に計算する（START/ADVANCE/RETRY/AWAIT/BLOCKED/ESCALATE/DONE）",
        "input_schema": {"type": "object", "properties": {"run_state_path": {"type": "string"}}},
    },
    "rig_orchestrate_check": {
        "fn": t_orchestrate_check,
        "description": "stepのchecks:（lint/test等）を実行する計算的センサー",
        "input_schema": {"type": "object", "properties": {"run_state_path": {"type": "string"}}},
    },
    "rig_orchestrate_status": {
        "fn": t_orchestrate_status,
        "description": "run-stateの現在状態を表示する",
        "input_schema": {"type": "object", "properties": {"run_state_path": {"type": "string"}}},
    },
    "rig_orchestrate_run": {
        "fn": t_orchestrate_run,
        "description": "各stepを別プロセスのrigハーネスとして全自動実行する（--isolateでworktree隔離）",
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
        "description": "過去runの集計・ギャップ処方箋を表示する（--costでトークン/コスト集計）",
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
        except Exception as e:  # noqa: BLE001 — サーバは1メッセージの失敗で落ちてはいけない
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
