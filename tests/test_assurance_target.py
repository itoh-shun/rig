"""What was asked for, checked against what the receipt recorded (#434).

The receipt already observes; this module only compares. So what these tests hold it to is
that it never invents an observation — that an axis rig cannot answer stays its own outcome,
that a word naming a level without naming what it is gets refused, and that nothing rounds
in rig's favour.
"""

import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench.assurance_target import (AXES, MET, SCHEMA, UNMET,
                                                      UNOBSERVABLE, evaluate, validate)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"


def _target(**axes) -> dict:
    return {"schema": SCHEMA, "axes": axes or {"gate": "passed"}}


def _receipt(**blocks) -> dict:
    base = {
        "isolation": {"observed": True, "mode": "git-worktree"},
        "verifier": {"independence": {"verdict": "unrecorded"}},
        "provenance": {"observed": True, "verified": True},
        "approvals": {"observed": True, "decisions": [{"actor": "a"}]},
        "gates": {"observed": True, "status": "passed"},
        # The block every real receipt carries (#479). A stub missing it is a stub describing
        # a receipt shape that no longer exists.
        "assurance_target": {"observed": False, "reason": "no assurance-target.json",
                             "not_recorded": "absent"},
    }
    base.update(blocks)
    return base


# ── a target may only ask what the receipt can answer ────────────────────────
def test_an_axis_the_receipt_does_not_report_is_refused():
    """`os-enforced` is the example the issue itself uses, and the receipt deliberately
    refuses to claim it: a git worktree keeps a change off the main tree and stops there.
    A target able to demand it would be demanding an answer nothing here can give."""
    problems = validate({"schema": SCHEMA, "axes": {"sandbox": "os-enforced"}})
    assert any("does not report that axis" in p for p in problems), problems
    assert any("isolation" in p for p in problems), problems


def test_a_value_the_axis_cannot_take_is_refused():
    problems = validate(_target(isolation="os-enforced"))
    assert any("git-worktree" in p for p in problems), problems


@pytest.mark.parametrize("word", ["production quality", "production", "strong", "strict",
                                  "high assurance", "best-effort", "PROD"])
def test_a_word_that_names_a_level_without_naming_what_it_is_is_refused(word):
    """rig cannot explain a mapping it did not receive, so it does not invent one. Refused by
    name rather than by failing the value check, so the refusal can say what to write."""
    problems = validate(_target(isolation=word))
    assert any("without naming what it is" in p for p in problems), (word, problems)


def test_a_target_that_requires_nothing_is_refused():
    """Met by everything, which is a way of being unconstrained while looking constrained."""
    assert any("requires nothing" in p for p in validate({"schema": SCHEMA, "axes": {}}))


def test_every_problem_is_reported_at_once():
    problems = validate({"schema": "wrong", "axes": {"sandbox": "x", "gate": "production"}})
    assert len(problems) >= 3, problems


# ── nothing is invented, and nothing is rounded ──────────────────────────────
def test_what_the_receipt_recorded_is_what_is_compared():
    result = evaluate(_target(isolation="git-worktree", gate="passed"), _receipt())
    assert result["status"] == "assurance-complete"
    assert all(a["outcome"] == MET for a in result["axes"].values())


def test_a_difference_is_unmet_and_says_what_was_recorded():
    result = evaluate(_target(gate="passed"),
                      _receipt(gates={"observed": True, "status": "failed"}))
    assert result["axes"]["gate"]["outcome"] == UNMET
    assert result["axes"]["gate"]["achieved"] == "failed"
    assert result["status"] == "assurance-incomplete"


def test_an_axis_the_receipt_did_not_observe_is_not_unmet():
    """The load-bearing distinction. `unmet` says rig looked and what it found falls short;
    this says rig cannot look. A caller folding them together reads "we do not measure that"
    as "we measured it and it was insufficient", and acts on it."""
    result = evaluate(_target(approval="recorded"),
                      _receipt(approvals={"observed": False, "reason": "governance is off"}))
    entry = result["axes"]["approval"]
    assert entry["outcome"] == UNOBSERVABLE
    assert entry["achieved"] is None
    assert result["status"] == "assurance-unobservable"


def test_the_receipts_own_reason_is_carried_through_not_replaced():
    """The receipt says why it did not look, in its own words. Substituting rig's generic
    placeholder would drop the one piece of information the operator can act on."""
    result = evaluate(_target(approval="recorded"),
                      _receipt(approvals={"observed": False,
                                          "reason": "this task's steps declare no human gate"}))
    assert result["axes"]["approval"]["reason"] == "this task's steps declare no human gate"


def test_an_unmet_axis_outranks_an_unobservable_one():
    """Something checked and short is worse news than something unchecked, and the summary
    has to lead with the worse news."""
    result = evaluate(_target(gate="passed", approval="recorded"),
                      _receipt(gates={"observed": True, "status": "failed"},
                               approvals={"observed": False, "reason": "off"}))
    assert result["status"] == "assurance-incomplete"


def test_an_unverified_signature_is_unmet_rather_than_unobservable():
    """The receipt observed the provenance block and reported that it does not verify. That
    is a measurement, and reporting it as "we could not look" would hide a failed check."""
    result = evaluate(_target(provenance="signed-and-verified"),
                      _receipt(provenance={"observed": True, "verified": False}))
    assert result["axes"]["provenance"]["outcome"] == UNMET
    assert result["axes"]["provenance"]["achieved"] == "none"


def test_a_signature_check_that_could_not_run_is_not_a_failed_one():
    """`_provenance` sets `verified` to `None` when the check raises or cannot be performed,
    and reports the block as observed all the same. Folding that in with `False` would say
    the signature does not verify when nobody managed to try — and a target asking for
    `none` would have been *met* by it."""
    result = evaluate(_target(provenance="none"),
                      _receipt(provenance={"observed": True, "verified": None,
                                           "reason": "the signing key is unavailable"}))
    assert result["axes"]["provenance"]["outcome"] == UNOBSERVABLE
    assert result["axes"]["provenance"]["reason"] == "the signing key is unavailable"


@pytest.mark.parametrize("verified", [None, "yes", 1, "", {}])
def test_anything_that_is_not_true_or_false_is_not_a_verification_result(verified):
    """`True`, `False`, and `None` are the three the producer emits. Anything else is a
    receipt this code does not understand, and picking the nearest value would be the same
    manufacture with a different input — a truthiness test would have read `"yes"` and `1`
    as verified, and `""` and `{}` as a failed check."""
    result = evaluate(_target(provenance="signed-and-verified"),
                      _receipt(provenance={"observed": True, "verified": verified}))
    assert result["axes"]["provenance"]["outcome"] == UNOBSERVABLE, verified


def test_a_missing_provenance_block_is_unobservable():
    result = evaluate(_target(provenance="signed-and-verified"),
                      _receipt(provenance={"observed": False, "reason": "not accepted yet"}))
    assert result["axes"]["provenance"]["outcome"] == UNOBSERVABLE


def test_an_unobserved_verifier_and_an_unrecorded_verdict_stay_different():
    """The receipt keeps the verdict inside `independence`. A block it never filled in and a
    block saying "nobody recorded who checked" are different facts about the same axis."""
    unrecorded = evaluate(_target(verification="declared-separate"), _receipt())
    assert unrecorded["axes"]["verification"]["outcome"] == UNMET
    absent = evaluate(_target(verification="declared-separate"),
                      _receipt(verifier={"observed": False, "reason": "no review steps"}))
    assert absent["axes"]["verification"]["outcome"] == UNOBSERVABLE


def test_an_invalid_target_raises_rather_than_returning_a_verdict():
    """A caller handing over a broken target would otherwise get an answer shaped like a
    comparison, and act on a status that compared nothing."""
    with pytest.raises(ValueError, match="not an assurance target"):
        evaluate({"schema": SCHEMA, "axes": {"gate": "production quality"}}, _receipt())


@pytest.mark.parametrize("axis,block,payload", [
    ("isolation", "isolation", {"observed": False, "reason": "r", "mode": "git-worktree"}),
    ("gate", "gates", {"observed": False, "reason": "r", "status": "passed"}),
])
def test_a_block_that_says_it_did_not_observe_is_believed_over_its_leftovers(
        axis, block, payload):
    """`observed: false` is the receipt's own statement that it did not look, and a value
    sitting beside it is a leftover rather than a finding. Reading the leftover would turn
    "we did not check the gate" into "the gate passed" — with the required value, so it would
    read as `met`."""
    result = evaluate(_target(**{axis: payload["mode" if axis == "isolation" else "status"]}),
                      _receipt(**{block: payload}))
    assert result["axes"][axis]["outcome"] == UNOBSERVABLE, result


# ── the axis vocabulary is the receipt's, not this module's ──────────────────
#: A receipt shape that achieves each declared value, one per value.
#:
#: Hand-written receipts are what let two unreachable values through review: `_verifier`
#: never emits `independent`, and `_approvals` never emits an observed absence. So each shape
#: here is checked against the *producer* below rather than trusted on its own — a fabricated
#: receipt proves the comparison works and proves nothing about what rig can ever record.
_REACHABLE = {
    ("isolation", "git-worktree"): {"isolation": {"observed": True, "mode": "git-worktree"}},
    ("isolation", "main-tree"): {"isolation": {"observed": True, "mode": "main-tree"}},
    ("verification", "declared-separate"): {
        "verifier": {"observed": True, "independence": {"verdict": "declared-separate"}}},
    ("verification", "unrecorded"): {"verifier": {"observed": True,
                                                  "independence": {"verdict": "unrecorded"}}},
    ("provenance", "signed-and-verified"): {"provenance": {"observed": True, "verified": True}},
    ("provenance", "none"): {"provenance": {"observed": True, "verified": False}},
    ("approval", "recorded"): {"approvals": {"observed": True, "decisions": [{"actor": "a"}]}},
    **{("gate", status): {"gates": {"observed": True, "status": status}}
       for status in ("passed", "passed_with_warnings", "failed", "pending", "skipped")},
}


def test_every_declared_value_is_reachable_from_some_receipt():
    """`AXES` claims to mirror what the receipt can answer, and a value no receipt shape
    produces breaks that claim silently: the target validates, then reports `unobservable`
    forever."""
    declared = {(axis, value) for axis, values in AXES.items() for value in values}
    assert declared == set(_REACHABLE), declared ^ set(_REACHABLE)
    for (axis, value), blocks in sorted(_REACHABLE.items()):
        result = evaluate(_target(**{axis: value}), _receipt(**blocks))
        assert result["axes"][axis]["outcome"] == MET, (axis, value, result)


def test_no_declared_value_is_one_the_producer_never_emits():
    """The check the fabricated receipts above cannot make, and the one review had to make
    twice for me: ask `assurance.py` what it actually emits.

    Called rather than grepped. A source-text search finds both verdict strings and cannot
    tell which branch produces which, so swapping them would leave every literal in place —
    and targets would receive the opposite achieved verdict with all of these tests green.

    `_verifier` chooses on whether the task was imported: work rig produced itself is
    `unrecorded`, because rig's review dispatches subagents whose identity never reaches task
    state, and an imported change is `declared-separate` — a weaker claim wearing its own
    weakness. Neither is `independent`, which is why a target cannot ask for it.
    """
    from rig_workbench.workbench.assurance import _verifier

    native = _verifier({"steps": [{"name": "review-diff"}]}, {"task_id": "t"})
    # `head_commit` is what `_import_block` keys on — an import record without one is not an
    # import, and getting the shape wrong here would have quietly tested `unrecorded` twice.
    imported = _verifier({"steps": [{"name": "review-diff"}]},
                         {"task_id": "t",
                          "import": {"producer": "an-outside-orchestrator",
                                     "head_commit": "a" * 40}})
    emitted = {native["independence"]["verdict"], imported["independence"]["verdict"]}
    assert native["independence"]["verdict"] == "unrecorded", native
    assert imported["independence"]["verdict"] == "declared-separate", imported
    assert set(AXES["verification"]) == emitted, emitted
    assert "none" not in AXES["approval"]


def test_a_verifier_block_that_did_not_observe_is_believed_over_its_leftover_verdict():
    """The third leftover, missed when the other two were closed. `_verifier` fills
    `independence` on the path where it did look, so a verdict beside `observed: false` is
    a leftover — and reading it would report an independence rig never established."""
    result = evaluate(_target(verification="declared-separate"),
                      _receipt(verifier={"observed": False, "reason": "no review steps",
                                         "independence": {"verdict": "declared-separate"}}))
    assert result["axes"]["verification"]["outcome"] == UNOBSERVABLE


def test_every_axis_names_a_block_the_receipt_writes():
    """If these drift apart, a target can ask for something the comparison silently reports
    as unobservable forever."""
    from rig_workbench.workbench.assurance_target import BLOCKS

    receipt_blocks = set(_receipt())
    for axis in AXES:
        assert BLOCKS.get(axis, axis) in receipt_blocks, axis


# ── the command exits with the answer ────────────────────────────────────────
def _run(tmp_path, target, *flags):
    path = tmp_path / "target.json"
    path.write_text(json.dumps(target), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(WORKBENCH), "assurance-target", "no-such-task", str(path), *flags],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=60)


def test_a_refused_target_exits_nonzero_before_touching_the_task(tmp_path):
    """The target is refused on its own terms, so a broken one does not need a real task —
    and does not produce a comparison that looks like a verdict."""
    result = _run(tmp_path, _target(isolation="production quality"))
    assert result.returncode == 1
    assert "REJECTED" in result.stderr
    assert "without naming what it is" in result.stderr


def test_an_unmet_target_exits_nonzero(tmp_path, monkeypatch):
    """A gate that exits 0 on an unmet target is not a gate.

    The receipt is stubbed rather than built from a real task: a test that skips when it
    cannot find one kills no mutation, and what is under test here is the exit code, not
    `build_receipt`.
    """
    import rig_workbench.workbench.assurance as assurance_module
    from rig_workbench.workbench import assurance_target as module

    monkeypatch.setattr(assurance_module, "build_receipt",
                        lambda root, task_id: _receipt(gates={"observed": True,
                                                              "status": "failed"}))
    monkeypatch.setattr(module, "repo_root", lambda: REPO_ROOT, raising=False)

    path = tmp_path / "target.json"
    path.write_text(json.dumps(_target(gate="passed")), encoding="utf-8")

    class Args:
        task_id, target, json = "t", str(path), False

    with pytest.raises(SystemExit) as exit_code:
        module.cmd_assurance_target(Args())
    assert exit_code.value.code == 1


def test_a_target_file_that_cannot_be_read_is_its_own_status(tmp_path):
    result = subprocess.run(
        [sys.executable, str(WORKBENCH), "assurance-target", "t", str(tmp_path / "absent.json")],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=60)
    assert result.returncode == 2
    assert "execution-error" in result.stdout
