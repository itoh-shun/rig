#!/usr/bin/env python3
"""Host adapter layer (#304).

#294 handled native-layer integration (Skills/Hooks/Subagents/MCP) for Codex only.
This module absorbs per-host differences (hook event names, skill path conventions,
MCP config format, feature coverage) into a thin data structure (this module's
`HOSTS`), so rig's core (recipe/gate/isolated-worktree, etc.) can stay host-agnostic
while supporting multiple hosts.

Adding a new host means adding one `HOSTS` entry — the decision logic is centralized
here, and this module never generates or rewrites Claude Code's existing behavior
(hooks/hooks.json, etc.); that stays exactly as-is (per #304's requirement).

The first host (Codex, #294) supplied the design; it's validated here against a
second host (Cursor) — Cursor-specific facts are sourced from Cursor's official docs
(cursor.com/docs/hooks, /docs/skills).
"""
from __future__ import annotations

Capability = str  # "supported" | "partial" | "unsupported" | "unverified"

# Per-host feature coverage (capability matrix).
# "unverified" makes explicit that a capability is documented but not exercised
# against a live instance of that host (e.g. Codex — this environment has no codex
# CLI to run against).
HOSTS: dict[str, dict] = {
    "claude-code": {
        "display_name": "Claude Code",
        "capabilities": {
            "skills": "supported", "hooks": "supported", "subagents": "supported",
            "mcp": "supported", "read_only_sandbox": "supported",
            "precompact_context_injection": "supported",  # PreCompact's stdout becomes extra compaction context
            "session_start": "supported", "tool_acl": "supported",
        },
        "hook_events": {  # canonical event name -> host-specific event name
            "PreCompact": "PreCompact", "SessionStart": "SessionStart",
            "SubagentStart": "SubagentStart", "SubagentStop": "SubagentStop",
            "Stop": "Stop", "UserPromptSubmit": "UserPromptSubmit",
        },
        "hooks_config_path": "hooks/hooks.json",
        "skill_paths": ["skills/rig/SKILL.md"],
        "mcp_config_key": "mcpServers",
        "degrade": {},  # baseline host -> no degrades
        "source": "rig's existing implementation (this repo's hooks/ and skills/)",
    },
    "codex": {
        "display_name": "Codex CLI",
        "capabilities": {
            "skills": "supported", "hooks": "supported", "subagents": "supported",
            "mcp": "supported", "read_only_sandbox": "supported",
            "precompact_context_injection": "unverified",  # the event fires, but the same injection semantics as Claude Code are unconfirmed
            "session_start": "supported", "tool_acl": "unverified",
        },
        "hook_events": {
            "PreCompact": "PreCompact", "SessionStart": "SessionStart",
            "SubagentStart": "SubagentStart", "SubagentStop": "SubagentStop",
            "Stop": "Stop", "UserPromptSubmit": "UserPromptSubmit",
        },
        "hooks_config_path": "codex/hooks.json",
        "skill_paths": [".agents/skills/rig/SKILL.md", "~/.agents/skills/rig/SKILL.md"],
        "mcp_config_key": "mcp_servers",  # config.toml's [mcp_servers.*] (a different key name than JSON's mcpServers)
        "degrade": {
            "precompact_context_injection": "warn — unverified against a live instance. If it doesn't work, "
                "run-continuity may be lost across compaction (never silently broken: called out in README/CHANGELOG)",
        },
        "source": "developers.openai.com/codex/{hooks,skills,subagents} (researched for #294)",
    },
    "cursor": {
        "display_name": "Cursor",
        "capabilities": {
            "skills": "supported",           # reads .agents/skills/ for legacy compat -> rig's Codex SKILL.md works as-is
            "hooks": "supported",
            "subagents": "unverified",       # Cursor's own subagent-definition format is unconfirmed (only mentioned as part of Agent Plugins)
            "mcp": "supported",
            "read_only_sandbox": "unverified",
            "precompact_context_injection": "unsupported",  # documented as an observational hook that cannot inject into continuation
            "session_start": "supported", "tool_acl": "partial",  # beforeShellExecution/beforeMCPExecution allow permission decisions, but at a different granularity than rig's argv injection
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
            "precompact_context_injection": "fail-safe — Cursor's preCompact only delivers a `user_message` "
                "notification; it cannot inject run-continuity state. Forcing a long stdout write would just be "
                "ignored, so this returns a short notification only and gives up on state preservation (never "
                "silently pretends it works)",
            "subagents": "warn — Cursor's own subagent-definition format is unconfirmed, so the Codex TOML "
                "definition isn't ported over",
        },
        "source": "cursor.com/docs/{hooks,skills} (newly researched for #304)",
    },
}

CANONICAL_EVENTS = ("PreCompact", "SessionStart", "SubagentStart", "SubagentStop", "Stop", "UserPromptSubmit")


def translate_hook_event(canonical_event: str, host: str) -> str | None:
    """Canonical event name (Claude Code baseline) -> host-specific event name. None for an unknown host/event."""
    return HOSTS.get(host, {}).get("hook_events", {}).get(canonical_event)


def capability(host: str, feature: str) -> Capability:
    """A host's coverage for a feature. Unregistered host/feature -> 'unsupported' (never silently 'supported')."""
    return HOSTS.get(host, {}).get("capabilities", {}).get(feature, "unsupported")


def degrade_behavior(host: str, feature: str) -> str | None:
    """When capability is 'partial'/'unsupported'/'unverified', the declared behavior: what's given up and what's
    surfaced. None when no declaration exists for that feature."""
    return HOSTS.get(host, {}).get("degrade", {}).get(feature)


def capability_matrix_table() -> str:
    """Generate the README's Markdown capability table mechanically from `HOSTS` (never let a hand-written table go stale)."""
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
