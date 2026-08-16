"""CLI provider session reuse (#326).

Opt-in reuse of a CLI conversation across the steps of one run, so step N+1
does not pay process startup and context re-injection again. Two properties
carry the risk and are pinned hardest here:

* the verifier never resumes anything (independent verification means the
  grader must not inherit the generator's conversation), and
* a CLI that cannot do it falls back to stateless **with a record** — a silent
  fallback would look exactly like a working feature.

Real-CLI behavior is deliberately NOT asserted here (see #326: mocks alone are
not evidence that this works against a live `claude`/`grok`). What these tests
pin is argv construction, the role boundary, and the fallback record.
"""

import pytest

from rig_workbench.orchestrate import providers


CLAUDE_HELP = """Usage: claude [options] [command] [prompt]
  -p, --print                 Print response and exit
      --session-id <uuid>     Use a specific session ID
  -r, --resume [sessionId]    Resume a conversation
  -c, --continue              Continue the most recent conversation
"""

NO_SESSION_HELP = """Usage: toycli [options]
  -p, --print   Print response and exit
"""


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    providers._HELP_CACHE.clear()
    yield
    providers._HELP_CACHE.clear()


@pytest.fixture
def supported(monkeypatch):
    """A CLI whose --help advertises the session flags."""
    providers._HELP_CACHE["claude"] = CLAUDE_HELP
    providers._HELP_CACHE["grok"] = CLAUDE_HELP


@pytest.fixture
def unsupported(monkeypatch):
    providers._HELP_CACHE["claude"] = NO_SESSION_HELP


def _cfg(**over):
    return {"reuse_session": True, **over}


# ── the generator side: reuse is opt-in and continues one conversation ──

def test_generator_starts_a_session_on_the_first_call(supported):
    cfg = _cfg()
    argv = providers.build_argv("claude", "generator", "step one", cfg)
    assert "--session-id" in argv
    session_id = argv[argv.index("--session-id") + 1]
    assert session_id


def test_generator_resumes_the_same_session_on_later_calls(supported):
    cfg = _cfg()
    first = providers.build_argv("claude", "generator", "step one", cfg)
    second = providers.build_argv("claude", "generator", "step two", cfg)
    started = first[first.index("--session-id") + 1]
    assert "--resume" in second
    assert second[second.index("--resume") + 1] == started
    assert "--session-id" not in second


def test_reuse_is_off_by_default(supported):
    argv = providers.build_argv("claude", "generator", "step one", {})
    assert "--session-id" not in argv and "--resume" not in argv


def test_reuse_does_not_disturb_the_rest_of_the_argv(supported):
    plain = providers.build_argv("claude", "generator", "p", {})
    reused = providers.build_argv("claude", "generator", "p", _cfg())
    for token in plain:
        assert token in reused, f"session reuse dropped {token!r}"
    assert len(reused) == len(plain) + 2  # exactly the flag and its value


# ── the verifier side: structurally stateless ──

def test_verifier_never_resumes_even_when_reuse_is_on(supported):
    argv = providers.build_argv("claude", "verifier", "judge this", _cfg())
    assert "--session-id" not in argv and "--resume" not in argv and "--continue" not in argv


def test_a_verifier_cannot_inherit_the_generators_session(supported):
    cfg = _cfg()
    providers.build_argv("claude", "generator", "step one", cfg)   # session now exists
    argv = providers.build_argv("claude", "verifier", "judge this", cfg)
    assert not any(a.startswith("--session") or a == "--resume" for a in argv)


def test_the_role_boundary_is_not_a_caller_choice():
    # rig-mcp's always-isolate shape: the safe behavior must not depend on the
    # caller remembering to ask for it.
    assert providers._session_reuse_argv("claude", "verifier", _cfg()) == []


# ── fallback: unsupported CLIs stay stateless, and say so ──

def test_an_unsupported_cli_falls_back_to_stateless(unsupported):
    argv = providers.build_argv("claude", "generator", "step one", _cfg())
    assert "--session-id" not in argv and "--resume" not in argv


def test_the_fallback_is_recorded_rather_than_silent(unsupported):
    cfg = _cfg()
    providers.build_argv("claude", "generator", "step one", cfg)
    notes = cfg.get("session_reuse_notes") or []
    assert notes, "a fallback with no record is indistinguishable from a working feature"
    assert any("claude" in n.get("provider", "") for n in notes)
    assert any(n.get("reason") for n in notes)


def test_the_fallback_is_recorded_once_not_per_step(unsupported):
    cfg = _cfg()
    for _ in range(3):
        providers.build_argv("claude", "generator", "step", cfg)
    assert len(cfg["session_reuse_notes"]) == 1


def test_a_provider_with_no_known_session_flags_falls_back(supported):
    cfg = _cfg()
    argv = providers.build_argv("codex", "generator", "step one", cfg)
    assert "--session-id" not in argv and "--resume" not in argv
    assert cfg.get("session_reuse_notes")


def test_probing_reads_the_real_help_output(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)

        class R:
            returncode = 0
            stdout = CLAUDE_HELP
            stderr = ""
        return R()

    monkeypatch.setattr(providers.subprocess, "run", fake_run)
    assert providers._cli_supports_session("claude") is True
    assert calls and calls[0][0] == "claude" and "--help" in calls[0]


def test_a_cli_that_cannot_be_probed_is_treated_as_unsupported(monkeypatch):
    def boom(argv, **kwargs):
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(providers.subprocess, "run", boom)
    assert providers._cli_supports_session("claude") is False


def test_the_probe_runs_once_per_binary(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)

        class R:
            returncode = 0
            stdout = CLAUDE_HELP
            stderr = ""
        return R()

    monkeypatch.setattr(providers.subprocess, "run", fake_run)
    for _ in range(4):
        providers._cli_supports_session("claude")
    assert len(calls) == 1


# ── the record reaches the run log ──

def test_notes_are_flushed_into_the_run_history(unsupported, monkeypatch):
    state = {"history": [], "steps": []}
    cfg = _cfg()

    class R:
        returncode = 0
        stdout = "ok"
        stderr = ""

    monkeypatch.setattr(providers.subprocess, "run", lambda *a, **k: R())
    monkeypatch.setattr(providers, "_record_benchmark_provider_call", lambda *a, **k: None)
    providers.run_provider("claude", "generator", "step one", cfg, state=state, step_id="implement")
    assert any(h.get("action") == "SESSION_REUSE_FALLBACK" for h in state["history"])


# ── the session has to survive the cfg copies the run loop makes ──

def test_a_per_step_model_copy_still_continues_the_same_session(supported):
    """`_generate` shallow-copies cfg when a step pins its own model.

    If the session id is only created on that throwaway copy, every step opens a
    fresh session: reuse is requested, silently never happens, and nothing says
    so. Pinning the shared container is the whole fix.
    """
    cfg = _cfg()
    providers.prepare_session_reuse(cfg, dag_parallel=False, provider="claude")
    first = providers.build_argv("claude", "generator", "p", cfg)
    step_cfg = {**cfg, "model": "some-model"}          # what _generate builds
    second = providers.build_argv("claude", "generator", "p", step_cfg)
    assert "--resume" in second, "the per-step copy lost the session"
    assert second[second.index("--resume") + 1] == first[first.index("--session-id") + 1]


def test_two_runs_do_not_share_one_session(supported):
    # A/B runs shallow-copy cfg per variant before the run starts; each variant is
    # its own run and must get its own conversation.
    root = _cfg()
    variant_a = {**root}
    variant_b = {**root}
    providers.prepare_session_reuse(variant_a, dag_parallel=False, provider="claude")
    providers.prepare_session_reuse(variant_b, dag_parallel=False, provider="claude")
    a = providers.build_argv("claude", "generator", "p", variant_a)
    b = providers.build_argv("claude", "generator", "p", variant_b)
    assert a[a.index("--session-id") + 1] != b[b.index("--session-id") + 1]


def test_dag_parallel_runs_fall_back_because_steps_share_the_cli(supported):
    # In DAG mode independent steps run concurrently; two concurrent generator
    # calls resuming one conversation would interleave it.
    cfg = _cfg()
    providers.prepare_session_reuse(cfg, dag_parallel=True, provider="claude")
    argv = providers.build_argv("claude", "generator", "p", cfg)
    assert "--session-id" not in argv and "--resume" not in argv
    assert any("concurrent" in n["reason"] for n in cfg["session_reuse_notes"])
