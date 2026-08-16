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
