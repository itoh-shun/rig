import pathlib
import hashlib
import json
import os
import shlex
import stat
import subprocess

import pytest


@pytest.fixture(autouse=True)
def _no_inherited_harness_markers(monkeypatch):
    """Run these as if no harness had started the process.

    Several tests here assert an exact exit status from the secure runtime. Inside a
    Claude Code session `CLAUDECODE` and `CLAUDE_CODE_SESSION_ID` are exported, the
    headless re-entry guard fires first, and five of them fail on a status that is
    correct for the situation and wrong for what the test is measuring. CI never sets
    the variables, so the suite is green there and red for whoever runs it from inside
    the harness this repository is mostly developed in — the least useful place for a
    false failure to live. `test_caller_contract` already clears exactly these; this is
    the same fixture, applied where the assertion depends on it.
    """
    for name in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "RIG_CALLER"):
        monkeypatch.delenv(name, raising=False)


class _CountingStdin:
    def __init__(self, payload: bytes, *, tty: bool = False, read_error: bool = False):
        self._payload = payload
        self._tty = tty
        self._read_error = read_error
        self.read_calls = 0
        self.buffer = self

    def isatty(self):
        return self._tty

    def read(self, _limit=-1):
        self.read_calls += 1
        if self._read_error:
            raise OSError("synthetic stdin failure")
        return self._payload


def _independent_recipe(path: pathlib.Path) -> pathlib.Path:
    path.write_text(
        "---\n"
        "name: japanese-writing\n"
        "description: secure runtime test\n"
        "scope: project\n"
        "autonomy: autonomous\n"
        "steps:\n"
        "  - id: write\n"
        "    instruction: japanese-write\n"
        "  - id: review\n"
        "    instruction: japanese-writing-review\n"
        "    gate: review-gate\n"
        "    output_contract: japanese-writing-verdict\n"
        "    policies: [independent-verification, secure-provider-execution]\n"
        "---\n",
        encoding="utf-8",
    )
    return path


def _bind_secure_review_category(state: dict, category: str = "general") -> None:
    material = {
        "profile": "none", "asset_id": None, "asset_sha256": None,
        "source_blob": None,
    }
    state["review_category"] = category
    state["material_profile"] = "none"
    state["material_provenance"] = material
    state["material_snapshot"] = None
    state["secure_runtime"] = {
        "policy_version": 1,
        "review_category": category,
        "material_profile": "none",
        "material_provenance": material,
        "material_snapshot": None,
    }
    state["history"].append({
        "action": "BIND_REVIEW_CATEGORY",
        "category": category,
    })


def test_run_usage_discovers_config_and_all_direct_pin_flags(capsys):
    from rig_workbench.orchestrate import commands

    with pytest.raises(SystemExit) as stopped:
        commands.cmd_run([])

    usage = capsys.readouterr().out
    assert stopped.value.code == 1
    assert "--secure-provider-config" in usage
    assert "--goal-stdin" in usage
    assert "--review-category general|incident_report|support_reply" in usage
    assert "--material-profile none|technical|conversation" in usage
    for role in ("generator", "verifier"):
        assert f"--{role}-executable" in usage
        assert f"--{role}-executable-sha256" in usage
        assert f"--{role}-interpreter" in usage
        assert f"--{role}-interpreter-sha256" in usage


def test_rig_wb_main_forwards_goal_stdin_without_parent_argv_goal(monkeypatch):
    from rig_workbench import cli
    from rig_workbench.orchestrate import cli as orchestrate_cli

    received = []
    monkeypatch.setitem(
        orchestrate_cli.COMMANDS,
        "run",
        lambda args: received.extend(args),
    )
    monkeypatch.setattr(orchestrate_cli, "advise_gh", lambda _context: None)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        [
            "rig-wb",
            "run",
            "japanese-writing",
            "--provider",
            "claude",
            "--goal-stdin",
        ],
    )

    cli.main()

    assert received == [
        "japanese-writing",
        "--provider",
        "claude",
        "--goal-stdin",
    ]
    assert "--goal" not in received


def test_secure_run_rejects_parent_argv_goal_before_provider_or_state(
    tmp_path, monkeypatch, capsys,
):
    from rig_workbench.orchestrate import commands

    recipe = _independent_recipe(tmp_path / "japanese-writing.md")
    monkeypatch.setattr(commands, "resolve_recipe", lambda _name: recipe)
    monkeypatch.setattr(
        commands, "new_state", lambda *_args, **_kwargs: pytest.fail("state created"),
    )
    monkeypatch.setattr(
        commands, "run_loop", lambda *_args, **_kwargs: pytest.fail("provider called"),
    )
    monkeypatch.setattr(
        commands,
        "preflight_secure_runtime",
        lambda *_args, **_kwargs: pytest.fail("provider launchers opened"),
    )

    with pytest.raises(SystemExit) as stopped:
        commands.cmd_run(
            [
                "japanese-writing",
                "--provider",
                "claude",
                "--verifier-provider",
                "codex",
                "--goal",
                "障害連絡を書く",
                "--review-category",
                "incident_report",
                "--out",
                str(tmp_path / "run-state.json"),
            ]
        )

    assert stopped.value.code == 2
    assert not (tmp_path / "run-state.json").exists()
    assert "--goal-stdin" in capsys.readouterr().err


@pytest.mark.parametrize("category_args", [[], ["--review-category", "unknown"]])
def test_secure_japanese_run_requires_explicit_valid_review_category_before_input_or_provider(
    tmp_path, monkeypatch, capsys, category_args,
):
    from rig_workbench.orchestrate import commands

    recipe = _independent_recipe(tmp_path / "japanese-writing.md")
    stdin = _CountingStdin(b"private goal")
    monkeypatch.setattr(commands, "resolve_recipe", lambda _name: recipe)
    monkeypatch.setattr(commands.sys, "stdin", stdin)
    monkeypatch.setattr(
        commands, "preflight_secure_runtime",
        lambda *_args, **_kwargs: pytest.fail("provider launchers opened"),
    )
    monkeypatch.setattr(
        commands, "new_state", lambda *_args, **_kwargs: pytest.fail("state created"),
    )

    with pytest.raises(SystemExit) as stopped:
        commands.cmd_run([
            "japanese-writing", "--provider", "claude",
            "--verifier-provider", "codex", "--goal-stdin", *category_args,
        ])

    assert stopped.value.code == 2
    assert "--review-category" in capsys.readouterr().err
    assert stdin.read_calls == 0


@pytest.mark.parametrize("profile_args", [["--material-profile", "invented"], ["--material-profile"]])
def test_secure_japanese_run_rejects_unknown_material_profile_before_input_or_provider(
    tmp_path, monkeypatch, capsys, profile_args,
):
    from rig_workbench.orchestrate import commands

    recipe = _independent_recipe(tmp_path / "japanese-writing.md")
    stdin = _CountingStdin(b"private goal")
    monkeypatch.setattr(commands, "resolve_recipe", lambda _name: recipe)
    monkeypatch.setattr(commands.sys, "stdin", stdin)
    monkeypatch.setattr(
        commands, "preflight_secure_runtime",
        lambda *_args, **_kwargs: pytest.fail("provider launchers opened"),
    )

    with pytest.raises(SystemExit) as stopped:
        commands.cmd_run([
            "japanese-writing", "--provider", "claude",
            "--verifier-provider", "codex", "--goal-stdin",
            "--review-category", "general", *profile_args,
        ])

    assert stopped.value.code == 2
    assert "--material-profile" in capsys.readouterr().err
    assert stdin.read_calls == 0


def test_legacy_nonsecure_run_keeps_goal_argv_compatibility(
    tmp_path, monkeypatch,
):
    from rig_workbench.orchestrate import commands

    recipe = tmp_path / "legacy.md"
    recipe.write_text(
        "---\nname: legacy\nsteps:\n  - id: write\n    instruction: write\n---\n",
        encoding="utf-8",
    )
    observed = {}
    monkeypatch.setattr(commands, "resolve_recipe", lambda _name: recipe)

    def fake_run_loop(state, *_args, **_kwargs):
        observed["goal"] = state["goal"]
        return "DONE"

    monkeypatch.setattr(commands, "run_loop", fake_run_loop)

    with pytest.raises(SystemExit) as finished:
        commands.cmd_run(
            [
                "legacy",
                "--provider",
                "mock",
                "--goal",
                "legacy argv goal",
                "--out",
                str(tmp_path / "legacy-state.json"),
            ]
        )

    assert finished.value.code == 0
    assert observed["goal"] == "legacy argv goal"


def test_secure_goal_stdin_without_pins_stops_before_provider_or_state(
    tmp_path, monkeypatch, capsys,
):
    from rig_workbench.orchestrate import commands

    recipe = _independent_recipe(tmp_path / "japanese-writing.md")
    stdin = _CountingStdin(b"private goal")
    monkeypatch.setattr(commands, "resolve_recipe", lambda _name: recipe)
    monkeypatch.setattr(commands.sys, "stdin", stdin)
    monkeypatch.setattr(
        commands, "new_state", lambda *_args, **_kwargs: pytest.fail("state created"),
    )
    monkeypatch.setattr(
        commands, "run_loop", lambda *_args, **_kwargs: pytest.fail("provider called"),
    )

    with pytest.raises(SystemExit) as stopped:
        commands.cmd_run(
            [
                "japanese-writing",
                "--provider",
                "claude",
                "--verifier-provider",
                "codex",
                "--goal-stdin",
                "--review-category",
                "general",
                "--out",
                str(tmp_path / "run-state.json"),
            ]
        )

    assert stopped.value.code == 2
    assert stdin.read_calls == 1
    assert "executable and SHA-256 pins" in capsys.readouterr().err
    assert not (tmp_path / "run-state.json").exists()


@pytest.mark.parametrize(
    ("stdin", "message"),
    [
        (_CountingStdin(b""), "nonempty"),
        (_CountingStdin(b"x" * (1024 * 1024 + 1)), "byte limit"),
        (_CountingStdin(b"private", tty=True), "interactive terminal"),
        (_CountingStdin(b"private", read_error=True), "could not read"),
        (_CountingStdin(b"\xff"), "valid UTF-8"),
    ],
)
def test_secure_goal_stdin_boundaries_fail_before_provider_or_state(
    tmp_path, monkeypatch, capsys, stdin, message,
):
    from rig_workbench.orchestrate import commands

    recipe = _independent_recipe(tmp_path / "japanese-writing.md")
    monkeypatch.setattr(commands, "resolve_recipe", lambda _name: recipe)
    monkeypatch.setattr(commands.sys, "stdin", stdin)
    monkeypatch.setattr(
        commands,
        "preflight_secure_runtime",
        lambda *_args, **_kwargs: pytest.fail("provider launchers opened"),
    )
    monkeypatch.setattr(
        commands, "new_state", lambda *_args, **_kwargs: pytest.fail("state created"),
    )
    monkeypatch.setattr(
        commands, "run_loop", lambda *_args, **_kwargs: pytest.fail("provider called"),
    )

    with pytest.raises(SystemExit) as stopped:
        commands.cmd_run(
            [
                "japanese-writing",
                "--provider",
                "claude",
                "--verifier-provider",
                "codex",
                "--goal-stdin",
                "--review-category",
                "general",
            ]
        )

    assert stopped.value.code == 2
    assert message in capsys.readouterr().err
    assert stdin.read_calls <= 1


def test_secure_run_rejects_unsafe_explicit_output_before_provider(
    tmp_path, monkeypatch,
):
    from rig_workbench.orchestrate import commands

    recipe = _independent_recipe(tmp_path / "japanese-writing.md")
    unsafe_parent = tmp_path / "ordinary-repository"
    unsafe_parent.mkdir(mode=0o755)
    unsafe_parent.chmod(0o755)
    monkeypatch.setattr(commands, "resolve_recipe", lambda _name: recipe)
    monkeypatch.setattr(commands.sys, "stdin", _CountingStdin(b"private"))
    monkeypatch.setattr(
        commands,
        "preflight_secure_runtime",
        lambda *_args, **_kwargs: pytest.fail("provider launchers opened"),
    )
    monkeypatch.setattr(
        commands, "new_state", lambda *_args, **_kwargs: pytest.fail("state created"),
    )
    monkeypatch.setattr(
        commands, "run_loop", lambda *_args, **_kwargs: pytest.fail("provider called"),
    )

    with pytest.raises(SystemExit) as stopped:
        commands.cmd_run(
            [
                "japanese-writing",
                "--provider",
                "claude",
                "--verifier-provider",
                "codex",
                "--goal-stdin",
                "--review-category",
                "general",
                "--out",
                str(unsafe_parent / "run-state.json"),
            ]
        )

    assert stopped.value.code == 2
    assert not (unsafe_parent / "run-state.json").exists()


def test_secure_run_lock_rejects_second_session_before_provider_and_preserves_state(
    tmp_path, monkeypatch,
):
    from rig_workbench.orchestrate import commands
    from rig_workbench.orchestrate.secure_fs import (
        acquire_output_lock,
        atomic_write_bytes,
        release_output_lock,
    )

    recipe = _independent_recipe(tmp_path / "japanese-writing.md")
    state_path = tmp_path / "run-state.json"
    atomic_write_bytes(state_path, b"first-session-state")
    first_session_lock = acquire_output_lock(state_path)
    monkeypatch.setattr(commands, "resolve_recipe", lambda _name: recipe)
    monkeypatch.setattr(commands.sys, "stdin", _CountingStdin(b"private"))
    monkeypatch.setattr(
        commands,
        "preflight_secure_runtime",
        lambda *_args, **_kwargs: pytest.fail("provider launchers opened"),
    )
    monkeypatch.setattr(
        commands, "run_loop", lambda *_args, **_kwargs: pytest.fail("provider called"),
    )

    try:
        with pytest.raises(SystemExit) as stopped:
            commands.cmd_run(
                [
                    "japanese-writing",
                    "--provider",
                    "claude",
                    "--verifier-provider",
                    "codex",
                    "--goal-stdin",
                    "--review-category",
                    "general",
                    "--out",
                    str(state_path),
                ]
            )
    finally:
        release_output_lock(first_session_lock)

    assert stopped.value.code == 2
    assert state_path.read_bytes() == b"first-session-state"


def test_secure_resume_refuses_active_run_lock_without_writing(
    tmp_path, monkeypatch, step_factory,
):
    from rig_workbench.orchestrate import commands
    from rig_workbench.orchestrate.runstate import new_state, save_state
    from rig_workbench.orchestrate.secure_fs import (
        acquire_output_lock,
        release_output_lock,
    )

    state_path = tmp_path / "run-state.json"
    state = new_state(
        "japanese-writing",
        [step_factory(id="write", policies=["secure-provider-execution"])],
        "private",
    )
    _bind_secure_review_category(state)
    save_state(state, state_path)
    before = state_path.read_bytes()
    active_run_lock = acquire_output_lock(state_path)
    monkeypatch.setattr(
        commands, "save_state", lambda *_args: pytest.fail("secure state was written"),
    )

    try:
        with pytest.raises(SystemExit) as stopped:
            commands.cmd_resume([str(state_path)])
    finally:
        release_output_lock(active_run_lock)

    assert stopped.value.code == 2
    assert state_path.read_bytes() == before


def test_secure_resume_never_reads_goal_stdin(tmp_path, monkeypatch, step_factory):
    from rig_workbench.orchestrate import commands
    from rig_workbench.orchestrate.runstate import new_state, save_state

    state_path = tmp_path / "run-state.json"
    state = new_state(
        "japanese-writing",
        [step_factory(id="write", policies=["secure-provider-execution"])],
        "private",
    )
    _bind_secure_review_category(state)
    save_state(state, state_path)
    forbidden_stdin = _CountingStdin(b"unexpected", read_error=True)
    monkeypatch.setattr(commands.sys, "stdin", forbidden_stdin)

    commands.cmd_resume([str(state_path)])

    assert forbidden_stdin.read_calls == 0


def test_secure_japanese_state_rejects_review_category_tamper_on_load(
    tmp_path, step_factory,
):
    from rig_workbench.orchestrate.runstate import load_state, new_state, save_state

    state_path = tmp_path / "run-state.json"
    state = new_state(
        "japanese-writing",
        [step_factory(id="write", policies=["secure-provider-execution"])],
        "private",
    )
    state["review_category"] = "support_reply"
    state["history"].append({
        "action": "BIND_REVIEW_CATEGORY", "category": "support_reply",
    })
    state["secure_runtime"] = {
        "policy_version": 1,
        "review_category": "support_reply",
    }
    save_state(state, state_path)
    tampered = json.loads(state_path.read_text(encoding="utf-8"))
    tampered["review_category"] = "general"
    state_path.write_text(json.dumps(tampered), encoding="utf-8")

    with pytest.raises(OSError, match="review category binding"):
        load_state(state_path)


def test_secure_japanese_state_rejects_material_profile_tamper_on_load(
    tmp_path, step_factory,
):
    from rig_workbench.orchestrate.runstate import load_state, new_state, save_state

    state_path = tmp_path / "run-state.json"
    state = new_state(
        "japanese-writing",
        [step_factory(id="write", policies=["secure-provider-execution"])],
        "private",
    )
    _bind_secure_review_category(state)
    save_state(state, state_path)
    tampered = json.loads(state_path.read_text(encoding="utf-8"))
    tampered["material_profile"] = "technical"
    tampered["secure_runtime"]["material_profile"] = "technical"
    state_path.write_text(json.dumps(tampered), encoding="utf-8")

    with pytest.raises(OSError, match="material profile binding"):
        load_state(state_path)


def test_secure_material_snapshot_is_stable_during_run_and_asset_drift_blocks_resume(
    tmp_path, monkeypatch,
):
    from rig_workbench.orchestrate import providers
    from rig_workbench.orchestrate.recipes import load_steps, parse_frontmatter, resolve_extends
    from rig_workbench.orchestrate.runstate import load_state, new_state, save_state
    from rig_workbench.orchestrate.secure_fs import atomic_write_bytes

    recipe_path = pathlib.Path(__file__).resolve().parents[1] / (
        "packs/domain/japanese-writing/recipes/japanese-writing.md"
    )
    recipe, _warnings = resolve_extends(parse_frontmatter(recipe_path), recipe_path)
    write, review = load_steps(recipe)
    material, metadata = providers.resolve_japanese_material(write, "technical")
    assert material is not None
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    snapshot_path = private / ".run-state.json.material"
    snapshot_bytes = material.encode("utf-8")
    atomic_write_bytes(snapshot_path, snapshot_bytes)
    snapshot = {
        "path": str(snapshot_path),
        "sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
        "size_bytes": len(snapshot_bytes),
    }
    state = new_state("japanese-writing", [write, review], "技術説明を書く")
    state["review_category"] = "general"
    state["history"].append({"action": "BIND_REVIEW_CATEGORY", "category": "general"})
    state["material_profile"] = "technical"
    state["material_provenance"] = metadata
    state["material_snapshot"] = snapshot
    state["secure_runtime"] = {
        "policy_version": 1,
        "review_category": "general",
        "material_profile": "technical",
        "material_provenance": metadata,
        "material_snapshot": snapshot,
    }
    prompt_before = providers.compose_step_prompt(state, write)
    state_path = private / "run-state.json"
    save_state(state, state_path)

    original_loader = providers._load_composition_asset
    def changed_material(kind, name, **kwargs):
        if kind == "wiki" and name == "japanese-style-material-technical":
            from rig_workbench.packs.model import PackError
            raise PackError("synthetic material asset swap")
        return original_loader(kind, name, **kwargs)
    monkeypatch.setattr(providers, "_load_composition_asset", changed_material)
    assert providers.compose_step_prompt(state, write) == prompt_before
    repair_prompt = providers.compose_repair_prompt(
        state, write, "初稿", "検証済み修正条件"
    )
    marker = "書き手が交代した瞬間に、暗黙だった制約は制約でなくなる"
    assert marker in prompt_before and marker in repair_prompt
    with pytest.raises(OSError, match="provenance cannot be verified"):
        load_state(state_path)

    atomic_write_bytes(snapshot_path, b"changed")
    with pytest.raises(Exception, match="snapshot hash changed"):
        providers.compose_step_prompt(state, write)


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.resolve().read_bytes()).hexdigest()


def _fake_provider(
    path: pathlib.Path,
    argv_log: pathlib.Path,
    output: str,
    stdin_log: pathlib.Path | None = None,
) -> None:
    stdin_command = (
        f"cat > {shlex.quote(str(stdin_log))}"
        if stdin_log is not None
        else "cat >/dev/null"
    )
    path.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" > {argv_log}\n"
        f"{stdin_command}\n"
        f"printf '%s\\n' '{output}'\n",
        encoding="utf-8",
    )
    path.chmod(0o700)


def _valid_japanese_review_output(*, safety="PASS") -> str:
    return json.dumps({
        "target_format": "plain-text",
        "checks": {
            "single_artifact": {"status": "PASS", "anchor": "one"},
            "format": {"status": "PASS", "anchor": "format"},
            "fact_preservation": {"status": "PASS", "anchor": "facts"},
            "no_inference": {"status": "PASS", "anchor": "grounded"},
            "japanese_quality": {"status": "PASS", "anchor": "Japanese"},
            "secret_handling": {"status": "N/A", "anchor": "none"},
            "incident_support_safety": {"status": safety, "anchor": "safe"},
        },
        "repair_conditions": ["なし"],
        "verdict": "APPROVE",
    }, ensure_ascii=False)


@pytest.mark.parametrize("explicit_out", [True, False])
def test_valid_pinned_fake_claude_to_codex_uses_stdin_and_secure_flags(
    tmp_path, monkeypatch, capsys, explicit_out,
):
    from rig_workbench.orchestrate import commands, providers

    recipe = _independent_recipe(tmp_path / "japanese-writing.md")
    generator = tmp_path / "claude"
    verifier = tmp_path / "codex"
    generator_argv = tmp_path / "generator.argv"
    verifier_argv = tmp_path / "verifier.argv"
    generator_stdin = tmp_path / "generator.stdin"
    goal_bytes = "障害連絡を書く。固有値=αβγ".encode("utf-8")
    _fake_provider(
        generator,
        generator_argv,
        "復旧作業は完了しました。",
        stdin_log=generator_stdin,
    )
    _fake_provider(verifier, verifier_argv, _valid_japanese_review_output())
    interpreter = pathlib.Path("/bin/sh")
    pin_config = tmp_path / "provider-pins.json"
    pin_config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generator": {
                    "executable": str(generator),
                    "sha256": _sha256(generator),
                    "interpreter": str(interpreter),
                    "interpreter_sha256": _sha256(interpreter),
                },
                "verifier": {
                    "executable": str(verifier),
                    "sha256": _sha256(verifier),
                    "interpreter": str(interpreter),
                    "interpreter_sha256": _sha256(interpreter),
                },
            }
        ),
        encoding="utf-8",
    )
    pin_config.chmod(0o600)
    malicious = tmp_path / "malicious-path"
    malicious.mkdir()
    working = tmp_path / "ordinary-repository"
    working.mkdir(mode=0o755)
    working.chmod(0o755)
    monkeypatch.chdir(working)
    monkeypatch.setenv("PATH", str(malicious))
    goal_stdin = _CountingStdin(goal_bytes)
    monkeypatch.setattr(commands.sys, "stdin", goal_stdin)
    monkeypatch.setattr(commands, "resolve_recipe", lambda _name: recipe)
    monkeypatch.setattr(
        providers,
        "_generator_facets",
        lambda _step: {
            "persona": [],
            "knowledge": [],
            "instruction": [],
            "output_contract": [],
            "policy": [],
        },
    )

    run_args = [
        "japanese-writing",
        "--provider",
        "claude",
        "--verifier-provider",
        "codex",
        "--secure-provider-config",
        str(pin_config),
        "--goal-stdin",
        "--review-category",
        "incident_report",
    ]
    if explicit_out:
        run_args += ["--out", str(tmp_path / "run-state.json")]
    with pytest.raises(SystemExit) as finished:
        commands.cmd_run(run_args)

    assert finished.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == "復旧作業は完了しました。\n"
    assert goal_bytes not in captured.err.encode("utf-8")
    generator_args = generator_argv.read_text(encoding="utf-8").splitlines()
    verifier_args = verifier_argv.read_text(encoding="utf-8").splitlines()
    assert "--safe-mode" in generator_args
    assert "--no-session-persistence" in generator_args
    assert "--permission-mode" not in generator_args
    assert verifier_args == [
        "exec",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "-",
    ]
    assert all("障害連絡を書く" not in arg for arg in generator_args + verifier_args)
    prompt_bytes = generator_stdin.read_bytes()
    assert prompt_bytes.count(goal_bytes) == 1
    assert goal_bytes not in "\0".join(run_args).encode("utf-8")
    assert goal_stdin.read_calls == 1
    state_path = (
        tmp_path / "run-state.json"
        if explicit_out
        else next((working / ".rig" / "secure-runs").glob("run-*.json"))
    )
    history_path = state_path.parent / "runtime-history.jsonl"
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(history_path.stat().st_mode) == 0o600
    output_dir = state_path.parent / "step-outputs"
    assert stat.S_IMODE(output_dir.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output_dir.iterdir())
    persisted = state_path.read_text(encoding="utf-8")
    assert "障害連絡を書く" not in persisted
    assert "goal_sha256" in persisted
    persisted_state = json.loads(persisted)
    assert persisted_state["review_category"] == "incident_report"
    assert persisted_state["secure_runtime"]["review_category"] == "incident_report"
    assert {"action": "BIND_REVIEW_CATEGORY", "category": "incident_report"} \
        in persisted_state["history"]


@pytest.mark.parametrize("attack", ["symlink", "hardlink"])
def test_secure_state_refuses_link_targets_without_truncating_them(
    tmp_path, step_factory, attack,
):
    from rig_workbench.orchestrate.runstate import new_state, save_state

    victim = tmp_path / "victim.json"
    victim.write_text("do-not-truncate", encoding="utf-8")
    victim.chmod(0o600)
    state_path = tmp_path / "run-state.json"
    if attack == "symlink":
        state_path.symlink_to(victim)
    else:
        os.link(victim, state_path)
    state = new_state("japanese-writing", [step_factory(id="write")], "private")
    state["secure_runtime"] = {"policy_version": 1}

    with pytest.raises(OSError):
        save_state(state, state_path)

    assert victim.read_text(encoding="utf-8") == "do-not-truncate"


def test_secure_state_requires_owner_only_directory_and_writes_atomic_0600(
    tmp_path, step_factory,
):
    from rig_workbench.orchestrate.runstate import new_state, save_state

    state = new_state("japanese-writing", [step_factory(id="write")], "private")
    state["secure_runtime"] = {"policy_version": 1}
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o755)
    with pytest.raises(OSError):
        save_state(state, unsafe / "run-state.json")

    safe = tmp_path / "safe"
    safe.mkdir(mode=0o700)
    path = safe / "run-state.json"
    save_state(state, path)
    assert stat.S_IMODE(safe.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.stat().st_nlink == 1
    assert not list(safe.glob(".run-state.json.*.tmp"))


def test_legacy_init_remains_usable_without_secure_provider_configuration(
    tmp_path, monkeypatch,
):
    from rig_workbench.orchestrate import commands

    recipe = tmp_path / "legacy.md"
    recipe.write_text(
        "---\nname: legacy\ndescription: test\nscope: project\n"
        "steps:\n  - id: write\n    instruction: implement\n---\n",
        encoding="utf-8",
    )
    output = tmp_path / "legacy-state.json"
    monkeypatch.setattr(commands, "resolve_recipe", lambda _name: recipe)
    commands.cmd_init(["legacy", "--out", str(output)])
    assert output.is_file()


def test_release_metadata_accepts_japanese_pack_080_on_engine_230():
    from rig_workbench.validation.release import japanese_pack_release_errors

    root = pathlib.Path(__file__).resolve().parents[1]
    assert japanese_pack_release_errors(root, "2.3.0") == []
    assert japanese_pack_release_errors(root, "2.3.1") == []


def test_secure_runtime_support_check_fails_closed_with_structured_diagnostics(
    monkeypatch,
):
    from rig_workbench.orchestrate import secure_runtime

    class LibcWithoutMemfd:
        pass

    monkeypatch.delattr(secure_runtime.os, "memfd_create", raising=False)
    monkeypatch.setattr(secure_runtime.ctypes, "CDLL", lambda *_args, **_kwargs: LibcWithoutMemfd())
    monkeypatch.setattr(secure_runtime.platform, "machine", lambda: "mystery-cpu")

    with pytest.raises(secure_runtime.SecureRuntimeError) as rejected:
        secure_runtime.check_secure_runtime_support()

    error = rejected.value
    assert [(check.name, check.available) for check in error.checks] == [
        ("interpreter os.memfd_create", False),
        ("libc memfd_create", False),
        ("direct memfd_create syscall", False),
        ("kernel memfd sealing", False),
        ("/proc/self/fd", os.path.isdir("/proc/self/fd")),
    ]
    assert error.checks[2].detail == (
        "architecture 'mystery-cpu' is not allowlisted; syscall number not guessed"
    )
    assert error.executable == secure_runtime.sys.executable
    assert error.version == secure_runtime.sys.version
    assert error.remediation == (
        "run rig-wb with a system CPython that exposes os.memfd_create; "
        "also repair any separately failed kernel or /proc prerequisite named above"
    )
    rendered_checks = "; ".join(
        f"{check.name}: "
        f"{'available' if check.available else 'unavailable' if check.available is False else 'not inspected'}"
        f" ({check.detail})"
        for check in error.checks
    )
    assert str(error) == (
        "secure runtime prerequisites were rejected; "
        f"checks: {rendered_checks}; sys.executable={error.executable!r}; "
        f"sys.version={error.version!r}; workaround: {error.remediation}"
    )


def test_ctypes_memfd_fallback_creates_an_actually_sealed_descriptor(monkeypatch):
    from rig_workbench.orchestrate import secure_runtime

    monkeypatch.delattr(secure_runtime.os, "memfd_create", raising=False)

    descriptor, checks = secure_runtime._create_memfd("rig-ctypes-fallback-test")
    try:
        secure_runtime._seal_descriptor(descriptor)
        required = secure_runtime._required_seals()
        actual = secure_runtime.fcntl.fcntl(descriptor, secure_runtime.fcntl.F_GET_SEALS)
        assert actual & required == required
        assert [(check.name, check.available) for check in checks] == [
            ("interpreter os.memfd_create", False),
            ("libc memfd_create", True),
            ("direct memfd_create syscall", None),
        ]
    finally:
        os.close(descriptor)


def test_linux_fcntl_constant_fallback_creates_an_actually_sealed_copy(
    tmp_path, monkeypatch,
):
    from rig_workbench.orchestrate import secure_runtime

    for name in secure_runtime._LINUX_FCNTL_SEAL_CONSTANTS:
        monkeypatch.delattr(secure_runtime.fcntl, name, raising=False)
    monkeypatch.setattr(secure_runtime.sys, "platform", "linux")
    source_path = tmp_path / "source"
    source_path.write_bytes(b"sealed payload")
    source = os.open(source_path, os.O_RDONLY)
    descriptor = None
    try:
        descriptor = secure_runtime._sealed_copy(source, "test", "payload")
        constants, detail = secure_runtime._seal_constants()
        actual = secure_runtime.fcntl.fcntl(
            descriptor, constants["F_GET_SEALS"]
        )
        assert actual & secure_runtime._required_seals() == secure_runtime._required_seals()
        assert detail.startswith("module Linux sealing constants were used")
    finally:
        os.close(source)
        if descriptor is not None:
            os.close(descriptor)


def test_fcntl_constant_fallback_is_rejected_off_linux(monkeypatch):
    from rig_workbench.orchestrate import secure_runtime

    for name in secure_runtime._LINUX_FCNTL_SEAL_CONSTANTS:
        monkeypatch.delattr(secure_runtime.fcntl, name, raising=False)
    monkeypatch.setattr(secure_runtime.sys, "platform", "darwin")

    with pytest.raises(secure_runtime.SecureRuntimeError, match="not used on platform"):
        secure_runtime._seal_constants()


def test_wrong_linux_fcntl_fallback_constant_fails_seal_verification(monkeypatch):
    from rig_workbench.orchestrate import secure_runtime

    for name in secure_runtime._LINUX_FCNTL_SEAL_CONSTANTS:
        monkeypatch.delattr(secure_runtime.fcntl, name, raising=False)
    monkeypatch.setattr(secure_runtime.sys, "platform", "linux")
    monkeypatch.setitem(secure_runtime._LINUX_FCNTL_SEAL_CONSTANTS, "F_GET_SEALS", 1035)
    descriptor, _checks = secure_runtime._create_memfd("rig-wrong-seal-constant-test")
    try:
        with pytest.raises((OSError, secure_runtime.SecureRuntimeError)):
            secure_runtime._seal_descriptor(descriptor)
    finally:
        os.close(descriptor)


def test_secure_runtime_support_reports_fcntl_constant_source(monkeypatch):
    from rig_workbench.orchestrate import secure_runtime

    for name in secure_runtime._LINUX_FCNTL_SEAL_CONSTANTS:
        monkeypatch.delattr(secure_runtime.fcntl, name, raising=False)
    monkeypatch.setattr(secure_runtime.sys, "platform", "linux")

    checks = secure_runtime.check_secure_runtime_support()
    sealing = next(check for check in checks if check.name == "kernel memfd sealing")
    assert sealing.available is True
    assert "module Linux sealing constants were used" in sealing.detail


def test_ctypes_fallback_uses_allowlisted_direct_syscall_when_libc_wrapper_is_absent(
    monkeypatch,
):
    from rig_workbench.orchestrate import secure_runtime

    architecture = secure_runtime.platform.machine().lower()
    if architecture not in secure_runtime._MEMFD_SYSCALLS:
        pytest.skip("direct syscall is intentionally unsupported on this architecture")
    real_libc = secure_runtime.ctypes.CDLL(None, use_errno=True)

    class LibcWithOnlySyscall:
        syscall = real_libc.syscall

    monkeypatch.delattr(secure_runtime.os, "memfd_create", raising=False)
    monkeypatch.setattr(
        secure_runtime.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: LibcWithOnlySyscall(),
    )

    descriptor, checks = secure_runtime._create_memfd("rig-syscall-fallback-test")
    try:
        secure_runtime._seal_descriptor(descriptor)
        assert [(check.name, check.available) for check in checks] == [
            ("interpreter os.memfd_create", False),
            ("libc memfd_create", False),
            ("direct memfd_create syscall", True),
        ]
    finally:
        os.close(descriptor)


def test_secure_runtime_support_check_does_not_accept_an_unsealed_memfd(monkeypatch):
    from rig_workbench.orchestrate import secure_runtime

    monkeypatch.setattr(
        secure_runtime,
        "_seal_descriptor",
        lambda _descriptor: (_ for _ in ()).throw(OSError("synthetic seal refusal")),
    )

    with pytest.raises(secure_runtime.SecureRuntimeError) as rejected:
        secure_runtime.check_secure_runtime_support()

    facts = {check.name: check for check in rejected.value.checks}
    assert facts["kernel memfd sealing"].available is False
    assert facts["kernel memfd sealing"].detail == (
        "F_ADD_SEALS/F_GET_SEALS failed: synthetic seal refusal"
    )
    assert facts["/proc/self/fd"].available is True


def test_sealed_launcher_uses_verified_bytes_after_executable_path_swap(tmp_path):
    from rig_workbench.orchestrate.secure_runtime import (
        close_secure_launchers,
        preflight_secure_runtime,
        run_secure_provider,
    )

    interpreter = pathlib.Path("/bin/sh")
    generator = tmp_path / "claude"
    verifier = tmp_path / "codex"
    _fake_provider(generator, tmp_path / "generator.argv", "trusted-output")
    _fake_provider(verifier, tmp_path / "verifier.argv", "VERDICT: PASS")
    pins = {
        role: {
            "executable": str(path),
            "sha256": _sha256(path),
            "interpreter": str(interpreter),
            "interpreter_sha256": _sha256(interpreter),
        }
        for role, path in (("generator", generator), ("verifier", verifier))
    }
    launchers = preflight_secure_runtime("claude", "codex", {"secure_pins": pins})
    try:
        replacement = tmp_path / "replacement"
        _fake_provider(replacement, tmp_path / "replacement.argv", "mutated-output")
        os.replace(replacement, generator)
        returncode, output = run_secure_provider(launchers["generator"], "private", {})
        assert returncode == 0
        assert output.strip() == "trusted-output"
    finally:
        close_secure_launchers(launchers)


def test_secure_provider_environment_does_not_cross_vendor_tokens(tmp_path):
    from rig_workbench.orchestrate.secure_runtime import (
        close_secure_launchers,
        preflight_secure_runtime,
        run_secure_provider,
    )

    interpreter = pathlib.Path("/bin/sh")
    generator = tmp_path / "claude"
    verifier = tmp_path / "codex"
    _fake_provider(generator, tmp_path / "generator.argv", "output")
    _fake_provider(verifier, tmp_path / "verifier.argv", "VERDICT: PASS")
    pins = {
        role: {
            "executable": str(path),
            "sha256": _sha256(path),
            "interpreter": str(interpreter),
            "interpreter_sha256": _sha256(interpreter),
        }
        for role, path in (("generator", generator), ("verifier", verifier))
    }
    launchers = preflight_secure_runtime("claude", "codex", {"secure_pins": pins})
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["env"] = kwargs["env"]
        captured["input"] = kwargs["input"]
        captured["shell"] = kwargs["shell"]
        captured["encoding"] = kwargs["encoding"]
        captured["errors"] = kwargs["errors"]
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    try:
        run_secure_provider(
            launchers["generator"],
            "private-prompt",
            {},
            environ={
                "PATH": str(tmp_path / "attacker"),
                "ANTHROPIC_API_KEY": "anthropic-secret",
                "OPENAI_API_KEY": "openai-secret",
                "UNRELATED_TOKEN": "unrelated-secret",
            },
            run_command=fake_run,
        )
        assert captured["input"] == "private-prompt"
        assert captured["shell"] is False
        assert captured["encoding"] == "utf-8"
        assert captured["errors"] == "strict"
        assert "private-prompt" not in captured["argv"]
        assert captured["env"]["PATH"] == "/usr/bin:/bin"
        assert captured["env"]["ANTHROPIC_API_KEY"] == "anthropic-secret"
        assert "OPENAI_API_KEY" not in captured["env"]
        assert "UNRELATED_TOKEN" not in captured["env"]
    finally:
        close_secure_launchers(launchers)


def test_secure_marker_is_explicit_and_opaque_provider_is_refused():
    from rig_workbench.orchestrate.secure_runtime import (
        SecureRuntimeError,
        preflight_secure_runtime,
        requires_secure_runtime,
    )

    assert not requires_secure_runtime(
        "goal-loop", [{"policies": ["independent-verification"]}]
    )
    assert requires_secure_runtime(
        "japanese-writing", [{"policies": ["secure-provider-execution"]}]
    )
    with pytest.raises(SecureRuntimeError, match="opaque"):
        preflight_secure_runtime("cmd", "codex", {"secure_pins": {}})


def test_direct_run_loop_cannot_bypass_secure_preflight(monkeypatch):
    from rig_workbench.orchestrate import providers
    from rig_workbench.orchestrate.runstate import new_state

    calls = []
    monkeypatch.setattr(
        providers,
        "run_provider",
        lambda *args, **kwargs: calls.append((args, kwargs)) or (0, "unexpected"),
    )
    state = new_state(
        "japanese-writing",
        [
            {
                "id": "write",
                "instruction": "write",
                "gate": None,
                "personas": [],
                "policies": ["secure-provider-execution"],
                "output_contract": "artifact",
                "needs": [],
            }
        ],
        "private",
    )

    final = providers.run_loop(
        state, None, "claude", "codex", {}, 2, quiet=True,
    )

    assert final == "BLOCKED"
    assert calls == []
    assert "sealed provider launchers" in state["stopped"]["reason"]

    multi_state = new_state(
        "japanese-writing", state["steps"], "private",
    )
    final = providers.run_loop(
        multi_state,
        None,
        "claude",
        "codex",
        {"secure_runtime": True},
        2,
        quiet=True,
        generators=["claude", "codex"],
    )
    assert final == "BLOCKED"
    assert calls == []
    assert "exactly one pinned generator" in multi_state["stopped"]["reason"]


def test_japanese_artifact_review_uses_canonical_strict_json_parser_only(monkeypatch):
    from rig_workbench.orchestrate import providers

    checks = {
        "single_artifact": {"status": "PASS", "anchor": "one"},
        "format": {"status": "PASS", "anchor": "format"},
        "fact_preservation": {"status": "PASS", "anchor": "facts"},
        "no_inference": {"status": "PASS", "anchor": "grounded"},
        "japanese_quality": {"status": "PASS", "anchor": "Japanese"},
        "secret_handling": {"status": "N/A", "anchor": "none"},
        "incident_support_safety": {"status": "PASS", "anchor": "safe"},
    }
    approved = json.dumps({
        "target_format": "plain-text",
        "checks": checks,
        "repair_conditions": ["なし"],
        "verdict": "APPROVE",
    })
    state = {"review_category": "support_reply"}
    step = {"output_contract": "japanese-writing-verdict"}

    parsed, verdict, raw_error = providers._artifact_review_judgment(
        state, step, approved,
    )
    assert verdict == "APPROVE"
    assert raw_error is None
    criteria = providers._artifact_review_criteria(parsed)
    assert [row["verdict"] for row in criteria] == [
        "PASS", "PASS", "PASS", "PASS", "PASS", "N/A", "PASS",
    ]

    revised = json.loads(approved)
    revised["checks"]["fact_preservation"]["status"] = "FAIL"
    revised["repair_conditions"] = ["事実保持を修正する"]
    revised["verdict"] = "REVISE"
    assert providers._artifact_review_judgment(
        state, step, json.dumps(revised)
    )[1] == "REVISE"
    malformed = providers._artifact_review_judgment(state, step, "VERDICT: PASS")
    assert malformed[0] is None
    assert malformed[1] is None
    assert malformed[2] == "workflow review contract is malformed JSON"
    safety_na = json.loads(approved)
    safety_na["checks"]["incident_support_safety"]["status"] = "N/A"
    safety_na_json = json.dumps(safety_na)
    invalid_safety = providers._artifact_review_judgment(state, step, safety_na_json)
    assert invalid_safety[0] is None
    assert invalid_safety[1] is None
    assert invalid_safety[2] == (
        "workflow review contract approval has blocking rows"
    )
    general_state = {"review_category": "general"}
    assert providers._artifact_review_judgment(
        general_state, step, safety_na_json
    )[1] == "APPROVE"

    unverified = json.loads(approved)
    unverified["checks"]["fact_preservation"]["status"] = "UNKNOWN"
    unverified["repair_conditions"] = ["事実を確認する"]
    unverified["verdict"] = "UNVERIFIED"
    parsed, verdict, raw_error = providers._artifact_review_judgment(
        state, step, json.dumps(unverified),
    )
    assert parsed["repair_conditions"] == ["事実を確認する"]
    assert verdict == "UNVERIFIED"
    assert raw_error is None

    runtime_step = {
        "id": "review",
        "personas": ["japanese-writing-reviewer"],
        "output_contract": "japanese-writing-verdict",
    }
    monkeypatch.setattr(
        providers, "compose_artifact_review_prompt",
        lambda *_args, **_kwargs: "trusted-review-prompt",
    )
    monkeypatch.setattr(
        providers, "_run_provider_counted",
        lambda *_args, **_kwargs: (0, approved),
    )
    result = providers._run_artifact_reviewers(
        "codex", state, runtime_step, "artifact", {"secure_runtime": True}, 1,
    )
    assert result[0]["ok"] is True
    assert result[0]["note"] == "exit 0; verdict=pass"
    assert all(row["anchor"] == "" for row in result[0]["criteria"])


def test_non_japanese_artifact_review_keeps_legacy_verdict_parser():
    from rig_workbench.orchestrate import providers

    state = {}
    step = {"output_contract": "review-verdict"}
    assert providers._artifact_review_judgment(
        state, step, "reason\nVERDICT: PASS"
    )[1] == "APPROVE"
    assert providers._artifact_review_judgment(
        state, step, "reason\nVERDICT: FAIL"
    )[1] == "REVISE"
