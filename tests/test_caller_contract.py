"""Who invoked rig, how sure it is, and what it refuses to guess (#416 Phase 2).

Rig is increasingly called by another harness rather than by a person, and one
consequence is already handled ad hoc: launching headless Claude from inside a Claude
Code session re-enters the same harness, so `bench_providers` blocks it by reading
`CLAUDECODE` / `CLAUDE_CODE_SESSION_ID` inline. That check is right and it is
invisible — the knowledge of what those variables mean lives in an `if`.

This makes the caller a named thing with three properties worth pinning:

- **an explicit answer beats a detected one.** `--caller` / `RIG_CALLER` is what the
  operator says; detection is what rig guessed. When both exist the operator wins,
  and the result says which it was, because a caller that cannot tell a declaration
  from a guess will trust the guess exactly as much.
- **the blind spots are declared, not implied.** Claude Code hands a subagent's shell
  the same variables it hands the parent's — verified against 2.1.224 and 2.1.227 in
  `context_meter` — so rig can say *which harness* invoked it and cannot say *at what
  depth*. Reporting a confident depth here would be a fabrication, and the surrounding
  code already refuses to publish a dispatch rate for the same reason.
- **it is a hint.** #416 draws this line itself: caller may inform runtime and
  reviewer selection, and must never branch rig's quality rules. A gate that is
  lenient when a particular harness calls it is not a gate, and it would fail exactly
  where it is least observed. The last test here is structural so the line stays drawn
  after everyone has forgotten this file.
"""

import pathlib
import re

import pytest

from rig_workbench import caller

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("RIG_CALLER", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CODEX_SANDBOX"):
        monkeypatch.delenv(name, raising=False)


def test_nothing_known_is_reported_as_nothing_known():
    detected = caller.detect()
    assert detected.id == caller.UNKNOWN
    assert detected.source == "none"
    assert detected.declared is False


def test_a_claude_code_session_is_recognised_and_says_what_gave_it_away():
    import os

    os.environ["CLAUDECODE"] = "1"
    try:
        detected = caller.detect()
    finally:
        del os.environ["CLAUDECODE"]
    assert detected.id == "claude-code"
    assert detected.source == "env:CLAUDECODE"
    assert detected.declared is False


def test_what_the_operator_declares_wins_over_what_rig_guessed(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    detected = caller.detect(declared="codex")
    assert detected.id == "codex"
    assert detected.source == "flag"
    assert detected.declared is True, (
        "a consumer that cannot tell a declaration from a guess will trust the guess "
        "exactly as much as the declaration")


def test_the_environment_override_is_also_a_declaration(monkeypatch):
    monkeypatch.setenv("RIG_CALLER", "codex")
    monkeypatch.setenv("CLAUDECODE", "1")
    detected = caller.detect()
    assert (detected.id, detected.source, detected.declared) == ("codex", "env:RIG_CALLER", True)


def test_a_declared_caller_is_normalised_but_never_invented(monkeypatch):
    assert caller.detect(declared="Claude-Code").id == "claude-code"
    # An unrecognised name is kept as the operator wrote it rather than snapped to a
    # known one: rig does not know every harness that will ever call it.
    assert caller.detect(declared="some-other-harness").id == "some-other-harness"
    with pytest.raises(ValueError):
        caller.detect(declared="   ")


def test_re_entering_the_same_harness_is_what_this_is_for():
    assert caller.would_re_enter("claude-code", provider="claude") is True
    assert caller.would_re_enter("claude-code", provider="codex") is False
    assert caller.would_re_enter("codex", provider="codex") is True
    # Unknown caller: rig has no reason to believe it would re-enter anything, and
    # blocking on a guess would break plain terminal use.
    assert caller.would_re_enter(caller.UNKNOWN, provider="claude") is False


def test_depth_is_a_question_rig_declines_to_answer():
    """Claude Code gives a subagent's shell the same variables as the parent's, so a
    depth here would be invented. `context_meter` refuses to report a dispatch rate
    on the same evidence; this refuses for the same reason."""
    detected = caller.detect()
    assert not hasattr(detected, "depth")
    assert "depth" in caller.__doc__ and "cannot" in caller.__doc__.lower()


def test_the_quality_rules_do_not_branch_on_who_is_calling():
    """#416: caller is a hint for runtime and reviewer selection, never an input to
    the rules. A gate that softens for one harness is not a gate, and it would soften
    exactly where nobody is watching."""
    decisive = [
        "rig_workbench/workbench/gates.py",
        "rig_workbench/workbench/state.py",
        "rig_workbench/orchestrate/gates.py",
        "rig_workbench/orchestrate/runstate.py",
    ]
    # Coupling, not prose: "the caller surfaces it" is ordinary English about a
    # calling function and says nothing about which harness started rig.
    pattern = re.compile(
        r"import caller\b|\bcaller\.(detect|would_re_enter)\b|rig_workbench\.caller"
        r"|CLAUDECODE|CLAUDE_CODE_SESSION_ID|RIG_CALLER")
    offenders = []
    for rel in decisive:
        path = REPO_ROOT / rel
        if not path.exists():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{rel}:{number}: {line.strip()}")
    assert not offenders, (
        "a gate or acceptance path mentions the caller:\n" + "\n".join(offenders))


def _bench_task(root):
    from rig_workbench.bench_tasks import BenchTask

    return BenchTask(
        id="caller-wiring", language="python", difficulty="S", risk_domains=(),
        goal="noop", test_command="true", hidden_command="true",
        root=pathlib.Path(root), expected_files=(),
    )


def test_the_declared_caller_has_a_way_in_from_the_command_line():
    """`--caller` is documented in this module, in `caller.detect`'s error messages and
    in both READMEs. It was documented before it existed: nothing defined the flag and
    nothing ever wrote `settings["caller"]`, so `bench_providers` read a key no code
    path could set, and the only answer that worked was `RIG_CALLER`.

    A hint that overstates itself is worse than none — the sentence this feature argues
    for itself. This pins the wiring at both entry points that reach the guard.
    """
    from rig_workbench import bench, bench_invariance

    for module in (bench, bench_invariance):
        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        assert '"--caller"' in source, f"{module.__name__} does not define --caller"
        # Parsing the flag and dropping it before `bench_providers` reads the key would
        # leave the documentation exactly as false, so the handover is pinned too.
        assert '"caller": args.caller' in source, (
            f"{module.__name__} parses --caller but never puts it in the settings that "
            f"bench_providers reads as settings.get('caller')"
        )


def test_a_declared_caller_reaches_the_re_entry_guard(tmp_path):
    """The declared value has to arrive where the decision is made. With no environment
    marker set, a declared `claude-code` still blocks headless `claude` — which is only
    possible if `settings["caller"]` is honoured on the way in. The guard returns before
    anything is launched, so this starts no provider."""
    from rig_workbench import bench_providers

    attempt = bench_providers.run_bare(
        _bench_task(tmp_path), "claude", None, tmp_path, {"caller": "claude-code"},
    )
    assert attempt.returncode == 126
    assert attempt.invocations == 0
    assert "claude-code" in attempt.stderr


def test_the_guard_is_about_this_caller_and_not_about_every_caller():
    """The mirror of the test above, kept to a pure function on purpose: `run_bare`
    would actually launch the provider once the guard declines, so the negative control
    is taken where the decision is, not by running it."""
    assert caller.would_re_enter("some-terminal", provider="claude") is False
    assert caller.would_re_enter("claude-code", provider="claude") is True


@pytest.mark.parametrize("bad, why", [
    ("claude‮code", "bidi override"),
    ("claude​code", "zero-width space"),
    ("claude﻿code", "BOM"),
    ("claude-code\nrig: everything is fine", "forged second line"),
    ("x" * 65, "unbounded length"),
])
def test_a_caller_name_that_would_lie_in_the_log_is_refused(bad, why):
    """The re-entry guard prints this name back to the operator, so the characters
    `workbench.injection` calls fail-grade cannot be carried into it — that module's
    whole argument is that these code points exist to make printed text lie. Refused
    rather than stripped: handing back a name the operator never typed is the failure
    this module argues against everywhere else."""
    with pytest.raises(ValueError):
        caller.detect(declared=bad)


def test_an_ordinary_name_still_passes_untouched():
    """The negative control. A rule that rejects everything would satisfy the test
    above while breaking the feature."""
    assert caller.detect(declared="Some-Harness_2").id == "some-harness_2"
    assert caller.detect(declared="  codex  ").id == "codex"
