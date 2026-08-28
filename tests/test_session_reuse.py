"""Reusing a CLI provider's conversation across steps (#326).

The saving is easy; the constraints are the work. These tests pin the four that matter — the
default does not move, the verifier can never inherit a generator's conversation, the CLI
decides its own capability rather than a table in rig, and a fallback is written down.

What they deliberately do *not* claim: that a resumed session actually carries context between
steps. That needs real generation calls against a real CLI, and the Issue says in as many words
not to assert it from mocks. Detection is verified against the shipped `claude` below and
marked as such; behaviour is not, and the Issue stays open for it.
"""

import subprocess

import pytest

from rig_workbench.orchestrate import providers, sessions


def _argv(provider, role, cfg, state=None, prompt="work"):
    return providers.build_argv(provider, role, prompt, cfg, state=state)


# ── the default does not move ────────────────────────────────────────────────
@pytest.mark.parametrize("provider", ["claude", "codex", "grok", "rig"])
def test_without_the_flag_the_argv_is_what_it_always_was(provider, monkeypatch):
    """Statelessness is a design property, not only a cost: each step starting clean is what
    keeps steps independent. So the saving is asked for, never assumed."""
    called = []
    monkeypatch.setattr(sessions, "supports",
                        lambda p, c=None: called.append(p) or (True, ""))
    argv = _argv(provider, "generator", {})
    assert not any(flag in argv for flag in ("--session-id", "--resume"))
    assert called == [], "capability must not even be probed when reuse was not requested"


def test_the_flag_reaches_cfg_from_the_command_line(tmp_path, monkeypatch):
    """Through the real argument parser, not by grepping the source for the flag's spelling —
    a string that is present but wired to nothing would pass that."""
    from rig_workbench.orchestrate import commands
    seen = {}
    monkeypatch.setattr(commands, "run_loop",
                        lambda state, out, gen, ver, cfg, *a, **k: seen.setdefault("cfg", cfg) and "DONE")
    recipe = tmp_path / "r.md"
    recipe.write_text("---\nname: r\nexecutable: true\nsteps:\n  - id: s\n    instruction: do\n---\n",
                      encoding="utf-8")
    try:
        commands.cmd_run([str(recipe), "--provider", "mock", "--reuse-session",
                          "--goal", "x", "--out", str(tmp_path / "state.json")])
    except SystemExit:
        pass
    assert seen.get("cfg", {}).get("reuse_session") is True


# ── never the verifier ───────────────────────────────────────────────────────
def test_a_verifier_never_gets_a_session_even_when_reuse_is_on(monkeypatch):
    """A checker that inherited the generator's conversation has already read its reasoning and
    will agree with it more often — whatever its prompt says. There is no path here that can
    produce a verifier session."""
    monkeypatch.setattr(sessions, "supports", lambda p, c=None: (True, ""))
    cfg = {"reuse_session": True}
    _argv("claude", "generator", cfg)          # open the session first
    verifier = _argv("claude", "verifier", cfg)
    assert not any(flag in verifier for flag in ("--session-id", "--resume"))


def test_session_argv_refuses_the_verifier_role_directly(monkeypatch):
    monkeypatch.setattr(sessions, "supports", lambda p, c=None: (True, ""))
    assert sessions.session_argv("claude", "verifier", {"reuse_session": True}) == []


# ── the CLI decides, not a table ─────────────────────────────────────────────
def test_capability_comes_from_the_cli_s_own_help(monkeypatch):
    """Session support is strongly version-dependent, so a hardcoded list would be a claim
    about versions this code has never seen, going quietly wrong as the tools change."""
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, stdout="--session-id\n--resume\n", stderr="")

    monkeypatch.setattr(sessions.subprocess, "run", fake_run)
    assert sessions.supports("claude", {}) == (True, "")
    assert seen["argv"] == ["claude", "--help"]


def test_a_cli_whose_help_lacks_the_flags_is_refused_with_the_missing_ones_named(monkeypatch):
    monkeypatch.setattr(sessions.subprocess, "run", lambda argv, **k: subprocess.CompletedProcess(
        argv, 0, stdout="--print\n--model\n", stderr=""))
    ok, reason = sessions.supports("claude", {})
    assert ok is False
    assert "--session-id" in reason and "--resume" in reason


def test_a_cli_that_is_not_installed_is_refused_not_crashed(monkeypatch):
    def missing(argv, **kwargs):
        raise FileNotFoundError(argv[0])
    monkeypatch.setattr(sessions.subprocess, "run", missing)
    assert sessions.supports("claude", {}) == (False, "claude not found on PATH")


def test_a_cli_that_hangs_on_help_is_refused(monkeypatch):
    def hang(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, sessions.PROBE_TIMEOUT)
    monkeypatch.setattr(sessions.subprocess, "run", hang)
    ok, reason = sessions.supports("claude", {})
    assert ok is False and "--help failed" in reason


def test_the_probe_runs_once_per_cfg(monkeypatch):
    """A run makes many provider calls and the answer cannot change under it."""
    calls = []
    monkeypatch.setattr(sessions.subprocess, "run", lambda argv, **k: calls.append(argv) or
                        subprocess.CompletedProcess(argv, 0, stdout="--session-id --resume", stderr=""))
    cfg = {}
    for _ in range(5):
        sessions.supports("claude", cfg)
    assert len(calls) == 1


# ── the session itself ───────────────────────────────────────────────────────
def test_the_first_call_opens_a_session_and_later_ones_resume_it(monkeypatch):
    monkeypatch.setattr(sessions, "supports", lambda p, c=None: (True, ""))
    cfg = {"reuse_session": True}
    first, second = _argv("claude", "generator", cfg), _argv("claude", "generator", cfg)

    assert "--session-id" in first and "--resume" not in first
    assert "--resume" in second and "--session-id" not in second
    assert first[first.index("--session-id") + 1] == second[second.index("--resume") + 1]


def test_two_runs_do_not_share_a_conversation(monkeypatch):
    """The id lives on the run, so a resumed session cannot outlive the work it belongs to."""
    monkeypatch.setattr(sessions, "supports", lambda p, c=None: (True, ""))
    one, two = {"reuse_session": True}, {"reuse_session": True}
    first = _argv("claude", "generator", one)
    other = _argv("claude", "generator", two)
    assert first[first.index("--session-id") + 1] != other[other.index("--session-id") + 1]


def test_the_session_id_is_a_uuid_because_the_cli_requires_one(monkeypatch):
    import uuid
    monkeypatch.setattr(sessions, "supports", lambda p, c=None: (True, ""))
    argv = _argv("claude", "generator", {"reuse_session": True})
    uuid.UUID(argv[argv.index("--session-id") + 1])  # raises if it is not one


# ── fallback is recorded, never silent ───────────────────────────────────────
@pytest.mark.parametrize("provider", ["codex", "cmd", "rig"])
def test_an_unsupported_provider_falls_back_with_a_record(provider):
    """A fallback nobody wrote down is indistinguishable from a feature that was never
    switched on — somebody measures no improvement and cannot tell which happened."""
    cfg = {"reuse_session": True, "provider_cmd": "echo {prompt}"}
    state = {"history": []}
    argv = _argv(provider, "generator", cfg, state)

    assert not any(flag in argv for flag in ("--session-id", "--resume"))
    recorded = [entry for entry in state["history"]
                if entry["action"] == "SESSION_REUSE_FALLBACK"]
    assert len(recorded) == 1
    assert recorded[0]["provider"] == provider and recorded[0]["reason"]


def test_the_fallback_is_recorded_once_not_per_call():
    cfg = {"reuse_session": True}
    state = {"history": []}
    for _ in range(10):
        _argv("codex", "generator", cfg, state)
    assert sum(1 for e in state["history"]
               if e["action"] == "SESSION_REUSE_FALLBACK") == 1


def test_a_fallback_without_a_run_to_record_into_still_works():
    assert sessions.session_argv("codex", "generator", {"reuse_session": True}, None) == []


# ── against the CLI that is actually installed ───────────────────────────────
def test_detection_against_the_shipped_claude_cli():
    """Real-CLI verification of the *detection* half (#326): this asks the installed `claude`
    what it supports and checks rig agrees with the answer.

    Skipped rather than failed where no CLI is installed — a machine without one has nothing to
    say about this, and a red test there would teach people to ignore it.
    """
    try:
        helped = subprocess.run(["claude", "--help"], capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.SubprocessError):
        pytest.skip("no claude CLI on PATH")
    advertised = all(flag in (helped.stdout or "") + (helped.stderr or "")
                     for flag in ("--session-id", "--resume"))
    assert sessions.supports("claude", {})[0] is advertised
