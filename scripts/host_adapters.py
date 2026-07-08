#!/usr/bin/env python3
"""ホストアダプタ層（#304）。

#294はCodex限定でネイティブ層（Skills/Hooks/Subagents/MCP）への統合を扱った。
このモジュールは、ホストごとの差分（hookイベント名・skill配置規約・MCP設定形式・
機能の対応度）を薄いデータ構造（本モジュールの`HOSTS`）で吸収し、rigのコア
（recipe/gate/隔離worktree等）はホスト非依存のまま複数ホストに対応できるようにする。

新規ホスト対応は`HOSTS`に1エントリ追加するだけでよい設計——判定ロジックはここに
一元化し、Claude Code向けの既存動作（hooks/hooks.json等）はこのモジュールが
生成・変更するものではなく、そのまま変更しない（#304の要求どおり）。

第一弾（Codex, #294）で得た知見を、第二弾（Cursor）に適用して設計を検証した——
Cursor固有の情報はCursor公式ドキュメント（cursor.com/docs/hooks・/docs/skills）に基づく。
"""
from __future__ import annotations

Capability = str  # "supported" | "partial" | "unsupported" | "unverified"

# 各ホストの機能対応度（capability matrix）。
# "unverified" は「対応表としては書けるが、実機での確認ができていない」ことを明示する
# （Codexのように、この環境にCLIが無く実行検証できていないホストに使う）。
HOSTS: dict[str, dict] = {
    "claude-code": {
        "display_name": "Claude Code",
        "capabilities": {
            "skills": "supported", "hooks": "supported", "subagents": "supported",
            "mcp": "supported", "read_only_sandbox": "supported",
            "precompact_context_injection": "supported",  # PreCompactのstdoutが圧縮への追加指示になる
            "session_start": "supported", "tool_acl": "supported",
        },
        "hook_events": {  # 正準イベント名 → ホスト固有イベント名
            "PreCompact": "PreCompact", "SessionStart": "SessionStart",
            "SubagentStart": "SubagentStart", "SubagentStop": "SubagentStop",
            "Stop": "Stop", "UserPromptSubmit": "UserPromptSubmit",
        },
        "hooks_config_path": "hooks/hooks.json",
        "skill_paths": ["skills/rig/SKILL.md"],
        "mcp_config_key": "mcpServers",
        "degrade": {},  # 基準ホスト＝degradeなし
        "source": "rig既存実装（本リポジトリのhooks/・skills/）",
    },
    "codex": {
        "display_name": "Codex CLI",
        "capabilities": {
            "skills": "supported", "hooks": "supported", "subagents": "supported",
            "mcp": "supported", "read_only_sandbox": "supported",
            "precompact_context_injection": "unverified",  # イベントは発火するがClaude Codeと同じ注入セマンティクスかは未確認
            "session_start": "supported", "tool_acl": "unverified",
        },
        "hook_events": {
            "PreCompact": "PreCompact", "SessionStart": "SessionStart",
            "SubagentStart": "SubagentStart", "SubagentStop": "SubagentStop",
            "Stop": "Stop", "UserPromptSubmit": "UserPromptSubmit",
        },
        "hooks_config_path": "codex/hooks.json",
        "skill_paths": [".agents/skills/rig/SKILL.md", "~/.agents/skills/rig/SKILL.md"],
        "mcp_config_key": "mcp_servers",  # config.toml の [mcp_servers.*]（JSONのmcpServersとは別キー名）
        "degrade": {
            "precompact_context_injection": "warn — 実機未検証。動作しない場合、run-continuityは"
                "圧縮時に失われる可能性がある（サイレントに壊れさせない：README/CHANGELOGで明示）",
        },
        "source": "developers.openai.com/codex/{hooks,skills,subagents}（#294で調査済み）",
    },
    "cursor": {
        "display_name": "Cursor",
        "capabilities": {
            "skills": "supported",           # .agents/skills/ をlegacy互換で読む＝rigのcodex用SKILL.mdがそのまま使える
            "hooks": "supported",
            "subagents": "unverified",       # Cursor独自のsubagent定義形式は未確認（Agent Pluginsの一部という言及のみ）
            "mcp": "supported",
            "read_only_sandbox": "unverified",
            "precompact_context_injection": "unsupported",  # 公式に「observational hookでcontinuationへの注入不可」と明記
            "session_start": "supported", "tool_acl": "partial",  # beforeShellExecution/beforeMCPExecutionでpermission decisionは可能だが粒度はrigのargv注入と異なる
        },
        "hook_events": {
            "PreCompact": "preCompact", "SessionStart": "sessionStart",
            "SubagentStart": "subagentStart", "SubagentStop": "subagentStop",
            "Stop": "stop", "UserPromptSubmit": "beforeSubmitPrompt",
        },
        "hooks_config_path": "cursor/hooks.json",
        "skill_paths": [".agents/skills/rig/SKILL.md", ".cursor/skills/rig/SKILL.md"],
        "mcp_config_key": "mcpServers",
        "degrade": {
            "precompact_context_injection": "fail-safe — CursorのpreCompactは`user_message`の通知のみで"
                "run-continuityの状態注入はできない。無理にstdoutへ長文を書いても無視される想定のため、"
                "短い通知メッセージのみを返し、run-stateの保全は諦める（黙って壊れたふりをしない）",
            "subagents": "warn — Cursor固有のsubagent定義形式が未確認のため、Codex向けTOML定義の移植は行わない",
        },
        "source": "cursor.com/docs/{hooks,skills}（本issue #304で新規調査）",
    },
}

CANONICAL_EVENTS = ("PreCompact", "SessionStart", "SubagentStart", "SubagentStop", "Stop", "UserPromptSubmit")


def translate_hook_event(canonical_event: str, host: str) -> str | None:
    """正準イベント名（Claude Code基準）→ ホスト固有のイベント名。未対応ホスト/イベントはNone。"""
    return HOSTS.get(host, {}).get("hook_events", {}).get(canonical_event)


def capability(host: str, feature: str) -> Capability:
    """ホストの機能対応度を返す。ホスト/機能が未登録なら'unsupported'（黙って'supported'扱いにしない）。"""
    return HOSTS.get(host, {}).get("capabilities", {}).get(feature, "unsupported")


def degrade_behavior(host: str, feature: str) -> str | None:
    """capabilityが'partial'/'unsupported'/'unverified'のとき、何を諦め何を通知するかの宣言。宣言が無い機能はNone。"""
    return HOSTS.get(host, {}).get("degrade", {}).get(feature)


def capability_matrix_table() -> str:
    """README用のMarkdownテーブルを`HOSTS`から機械的に生成する（手書きで対応表を陳腐化させない）。"""
    features = ["skills", "hooks", "subagents", "mcp", "read_only_sandbox",
                "precompact_context_injection", "session_start", "tool_acl"]
    lines = ["| Host | " + " | ".join(features) + " |",
             "|---|" + "---|" * len(features)]
    for host, spec in HOSTS.items():
        row = [spec["display_name"]] + [spec["capabilities"].get(f, "unsupported") for f in features]
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


if __name__ == "__main__":
    print(capability_matrix_table())
