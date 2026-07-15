"""Codex CLI native-layer integration: Skills, Hooks, Subagent TOML (#294).

This environment has no codex CLI, so these are structural checks only (valid
JSON/TOML, documented fields) — actual skill loading, hook firing, sandbox
enforcement, and MCP connection are unverified, same honest scope as the
Claude Code hooks.json/SKILL.md this mirrors.
"""

import json
import pathlib
import tomllib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_codex_hooks_json_is_valid_and_wires_precompact():
    data = json.loads((REPO_ROOT / "codex" / "hooks.json").read_text(encoding="utf-8"))
    assert "PreCompact" in data["hooks"]
    commands = [h["command"] for entry in data["hooks"]["PreCompact"] for h in entry["hooks"]]
    assert any("preserve-rig-state.sh" in c for c in commands)


def test_preserve_rig_state_script_referenced_by_codex_hooks_exists():
    assert (REPO_ROOT / "hooks" / "preserve-rig-state.sh").exists()


def test_codex_security_reviewer_toml_is_valid_and_read_only():
    data = tomllib.load((REPO_ROOT / ".codex" / "agents" / "security-reviewer.toml").open("rb"))
    assert data["name"] == "security-reviewer"
    assert data["sandbox_mode"] == "read-only"
    assert "developer_instructions" in data and data["developer_instructions"].strip()


def test_codex_skill_md_has_frontmatter_and_points_to_the_real_scripts():
    text = (REPO_ROOT / "codex" / "skills" / "rig" / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: rig\n")
    assert "scripts/workbench.py" in text and "scripts/orchestrate.py" in text
