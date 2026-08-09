import pathlib
import hashlib
import json
import os
import shlex
import stat
import subprocess

import pytest


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
        "    policies: [independent-verification, secure-provider-execution]\n"
        "---\n",
        encoding="utf-8",
    )
    return path


def test_run_usage_discovers_config_and_all_direct_pin_flags(capsys):
    from rig_workbench.orchestrate import commands

    with pytest.raises(SystemExit) as stopped:
        commands.cmd_run([])

    usage = capsys.readouterr().out
    assert stopped.value.code == 1
    assert "--secure-provider-config" in usage
    assert "--goal-stdin" in usage
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
                "--out",
                str(tmp_path / "run-state.json"),
            ]
        )

    assert stopped.value.code == 2
    assert not (tmp_path / "run-state.json").exists()
    assert "--goal-stdin" in capsys.readouterr().err


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
    state["secure_runtime"] = {"policy_version": 1}
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
    state["secure_runtime"] = {"policy_version": 1}
    save_state(state, state_path)
    forbidden_stdin = _CountingStdin(b"unexpected", read_error=True)
    monkeypatch.setattr(commands.sys, "stdin", forbidden_stdin)

    commands.cmd_resume([str(state_path)])

    assert forbidden_stdin.read_calls == 0


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
    _fake_provider(verifier, verifier_argv, "VERDICT: PASS")
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


def test_release_metadata_accepts_japanese_pack_060_on_engine_230():
    from rig_workbench.validation.release import japanese_pack_release_errors

    root = pathlib.Path(__file__).resolve().parents[1]
    assert japanese_pack_release_errors(root, "2.3.0") == []
    assert japanese_pack_release_errors(root, "2.3.1") == []


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
