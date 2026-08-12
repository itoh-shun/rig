"""Codex CLI native-layer integration: Skills, Hooks, Subagent TOML (#294).

The CLI still owns event dispatch, but hook commands and their stdout contracts
are exercised here.  This catches host-root and JSON-shape failures before a
release even when a live Codex session is unavailable in CI.
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys

try:
    import tomllib
except ModuleNotFoundError:  # Python <3.11 (pyproject.toml requires-python >=3.10)
    import tomli as tomllib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PRESERVE_SCRIPT = REPO_ROOT / "hooks" / "preserve-rig-state.sh"
CODEX_PRECOMPACT_SCRIPT = REPO_ROOT / "hooks" / "codex-precompact.sh"
CONTINUITY_SCRIPT = REPO_ROOT / "hooks" / "inject-run-continuity.sh"


def _clean_hook_env(**updates):
    env = os.environ.copy()
    env.pop("PLUGIN_ROOT", None)
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    env.update({key: str(value) for key, value in updates.items()})
    return env


def _run_command(command, *, cwd=REPO_ROOT, env=None, input_text=""):
    return subprocess.run(
        command,
        cwd=cwd,
        env=env or _clean_hook_env(),
        input=input_text,
        text=True,
        capture_output=True,
        shell=True,
    )


def _command_for(config, event, marker):
    commands = [
        hook["command"]
        for entry in config["hooks"][event]
        for hook in entry["hooks"]
    ]
    return next(command for command in commands if marker in command)


def test_codex_hooks_json_is_valid_and_wires_precompact():
    data = json.loads((REPO_ROOT / "codex" / "hooks.json").read_text(encoding="utf-8"))
    assert "PreCompact" in data["hooks"]
    assert "SessionStart" in data["hooks"]
    assert "codex-precompact.sh" in _command_for(data, "PreCompact", "codex-precompact.sh")
    assert "inject-run-continuity.sh" in _command_for(
        data, "SessionStart", "inject-run-continuity.sh"
    )
    assert [entry["matcher"] for entry in data["hooks"]["SessionStart"]] == ["compact"]


def test_run_continuity_hook_entrypoints_exist():
    assert PRESERVE_SCRIPT.exists()
    assert CODEX_PRECOMPACT_SCRIPT.exists()
    assert CONTINUITY_SCRIPT.exists()


def test_claude_precompact_keeps_plaintext_compaction_instructions():
    result = subprocess.run(
        ["sh", str(PRESERVE_SCRIPT)],
        env=_clean_hook_env(CLAUDE_PLUGIN_ROOT=REPO_ROOT),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("[rig run-continuity]")
    assert "compaction summary MUST preserve" in result.stdout


def test_codex_plugin_precompact_returns_valid_common_json_output():
    result = subprocess.run(
        ["sh", str(CODEX_PRECOMPACT_SCRIPT)],
        env=_clean_hook_env(),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout == '{"continue":true}\n'
    assert json.loads(result.stdout) == {"continue": True}


def test_compact_session_start_returns_run_continuity_additional_context():
    result = subprocess.run(
        ["sh", str(CONTINUITY_SCRIPT)],
        env=_clean_hook_env(),
        input='{"hook_event_name":"SessionStart","source":"compact"}',
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "SessionStart"
    assert "run-status" in output["additionalContext"]
    assert "re-anchor" in output["additionalContext"]


def _all_commands(config):
    return [
        hook["command"]
        for entries in config["hooks"].values()
        for entry in entries
        for hook in entry["hooks"]
    ]


def _assert_context_output(result, event_name, expected_text):
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == event_name
    assert expected_text in output["additionalContext"]


def _prepare_hook_project(tmp_path):
    project = tmp_path / "hook project"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    added = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "workbench.py"),
            "instincts",
            "--add",
            "shared hook integration pattern",
            "--confidence",
            "0.9",
        ],
        cwd=project,
        text=True,
        capture_output=True,
    )
    assert added.returncode == 0, added.stdout + added.stderr

    transcript = project / "transcript.jsonl"
    transcript.write_text(
        '{"role":"assistant","text":"▸ rig | recipe: bugfix | step: verify"}\n',
        encoding="utf-8",
    )
    return project, transcript


def _exercise_every_shared_command(data, *, project, transcript, env):
    commands = {
        "precompact": _command_for(data, "PreCompact", "codex-precompact.sh"),
        "talk": _command_for(data, "SessionStart", "inject-talk-mode.sh"),
        "instincts": _command_for(data, "SessionStart", "inject-instincts.sh"),
        "continuity": _command_for(data, "SessionStart", "inject-run-continuity.sh"),
        "prompt_reminder": _command_for(
            data, "UserPromptSubmit", "remind-rig-header.sh"
        ),
        "stop": _command_for(data, "Stop", "suggest-instincts.sh"),
    }
    assert len(_all_commands(data)) == len(commands)
    assert set(_all_commands(data)) == set(commands.values())

    session_input = json.dumps(
        {"hook_event_name": "SessionStart", "source": "compact"}
    )
    results = {
        "precompact": _run_command(commands["precompact"], cwd=project, env=env),
        "talk": _run_command(
            commands["talk"], cwd=project, env=env, input_text=session_input
        ),
        "instincts": _run_command(
            commands["instincts"], cwd=project, env=env, input_text=session_input
        ),
        "continuity": _run_command(
            commands["continuity"], cwd=project, env=env, input_text=session_input
        ),
        "prompt_reminder": _run_command(
            commands["prompt_reminder"],
            cwd=project,
            env=env,
            input_text=json.dumps({"transcript_path": str(transcript)}),
        ),
        "stop": _run_command(
            commands["stop"],
            cwd=project,
            env=env,
            input_text=json.dumps(
                {
                    "stop_hook_active": False,
                    "session_id": "shared-hook-contract",
                    "transcript_path": str(transcript),
                }
            ),
        ),
    }

    _assert_context_output(results["talk"], "SessionStart", "rig:talk")
    _assert_context_output(
        results["instincts"], "SessionStart", "shared hook integration pattern"
    )
    _assert_context_output(results["continuity"], "SessionStart", "re-anchor")
    _assert_context_output(
        results["prompt_reminder"], "UserPromptSubmit", "run-status header"
    )
    assert results["stop"].returncode == 0, results["stop"].stderr
    assert results["stop"].stderr == ""
    stop_output = json.loads(results["stop"].stdout)
    assert stop_output["decision"] == "block"
    assert "[rig instincts]" in stop_output["reason"]
    return results["precompact"]


def test_every_shared_plugin_command_executes_with_codex_root(tmp_path):
    data = json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    continuity_entries = [
        entry
        for entry in data["hooks"]["SessionStart"]
        if any("inject-run-continuity.sh" in hook["command"] for hook in entry["hooks"])
    ]
    assert [entry["matcher"] for entry in continuity_entries] == ["compact"]
    project, transcript = _prepare_hook_project(tmp_path)
    env = _clean_hook_env(
        PLUGIN_ROOT=REPO_ROOT,
        # A bad legacy root proves that the Codex root wins when both exist.
        CLAUDE_PLUGIN_ROOT=tmp_path / "missing legacy plugin",
        XDG_STATE_HOME=tmp_path / "state",
        HOME=tmp_path / "home",
    )
    precompact = _exercise_every_shared_command(
        data, project=project, transcript=transcript, env=env
    )
    assert precompact.returncode == 0, precompact.stderr
    assert precompact.stderr == ""
    assert json.loads(precompact.stdout) == {"continue": True}


def test_every_shared_plugin_command_executes_with_claude_root(tmp_path):
    data = json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    project, transcript = _prepare_hook_project(tmp_path)
    env = _clean_hook_env(
        CLAUDE_PLUGIN_ROOT=REPO_ROOT,
        XDG_STATE_HOME=tmp_path / "state",
        HOME=tmp_path / "home",
    )
    precompact = _exercise_every_shared_command(
        data, project=project, transcript=transcript, env=env
    )
    assert precompact.returncode == 0, precompact.stderr
    assert precompact.stderr == ""
    assert precompact.stdout.startswith("[rig run-continuity]")
    assert "compaction summary MUST preserve" in precompact.stdout


def test_every_shared_plugin_hook_command_has_a_cross_host_root_fallback():
    data = json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    commands = [
        hook["command"]
        for entries in data["hooks"].values()
        for entry in entries
        for hook in entry["hooks"]
    ]
    assert commands
    assert all("PLUGIN_ROOT" in command and "CLAUDE_PLUGIN_ROOT" in command for command in commands)


def test_every_shared_plugin_hook_command_fails_open_without_a_root():
    data = json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    commands = [
        hook["command"]
        for entries in data["hooks"].values()
        for entry in entries
        for hook in entry["hooks"]
    ]
    for command in commands:
        result = _run_command(command, env=_clean_hook_env(), input_text="{}")
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""


def test_shared_plugin_hook_commands_support_spaces_in_plugin_root(tmp_path):
    root_with_spaces = tmp_path / "rig plugin root"
    root_with_spaces.symlink_to(REPO_ROOT, target_is_directory=True)
    data = json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    env = _clean_hook_env(PLUGIN_ROOT=root_with_spaces)

    precompact = _run_command(
        _command_for(data, "PreCompact", "codex-precompact.sh"), env=env
    )
    assert precompact.returncode == 0, precompact.stderr
    assert json.loads(precompact.stdout) == {"continue": True}

    session_start = _run_command(
        _command_for(data, "SessionStart", "inject-run-continuity.sh"), env=env
    )
    assert session_start.returncode == 0, session_start.stderr
    assert json.loads(session_start.stdout)["hookSpecificOutput"]["hookEventName"] == "SessionStart"

    claude_precompact = _run_command(
        _command_for(data, "PreCompact", "preserve-rig-state.sh"),
        env=_clean_hook_env(CLAUDE_PLUGIN_ROOT=root_with_spaces),
    )
    assert claude_precompact.returncode == 0, claude_precompact.stderr
    assert claude_precompact.stdout.startswith("[rig run-continuity]")


def test_codex_native_hook_commands_execute_from_git_repo_with_spaces(tmp_path):
    native_repo = tmp_path / "native hook repository with spaces"
    native_repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=native_repo, check=True)
    shutil.copytree(REPO_ROOT / "hooks", native_repo / "hooks")
    assert " " in str(native_repo)

    data = json.loads((REPO_ROOT / "codex" / "hooks.json").read_text(encoding="utf-8"))
    precompact = _run_command(
        _command_for(data, "PreCompact", "codex-precompact.sh"),
        cwd=native_repo,
        env=_clean_hook_env(HOME=tmp_path / "home"),
        input_text=json.dumps({"hook_event_name": "PreCompact", "trigger": "manual"}),
    )
    assert precompact.returncode == 0, precompact.stderr
    assert precompact.stderr == ""
    assert json.loads(precompact.stdout) == {"continue": True}

    session_start = _run_command(
        _command_for(data, "SessionStart", "inject-run-continuity.sh"),
        cwd=native_repo,
        env=_clean_hook_env(HOME=tmp_path / "home"),
        input_text=json.dumps(
            {"hook_event_name": "SessionStart", "source": "compact"}
        ),
    )
    _assert_context_output(session_start, "SessionStart", "re-anchor")


def test_codex_security_reviewer_toml_is_valid_and_read_only():
    data = tomllib.load((REPO_ROOT / ".codex" / "agents" / "security-reviewer.toml").open("rb"))
    assert data["name"] == "security-reviewer"
    assert data["sandbox_mode"] == "read-only"
    assert "developer_instructions" in data and data["developer_instructions"].strip()


def test_codex_skill_md_has_frontmatter_and_points_to_the_real_scripts():
    text = (REPO_ROOT / "codex" / "skills" / "rig" / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: rig\n")
    assert "scripts/workbench.py" in text and "scripts/orchestrate.py" in text
