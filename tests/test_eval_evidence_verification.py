"""What a committed, signed measurement is still worth once CI stops measuring.

CI cannot run the providers: `codex` is an external binary that is neither
installed nor authenticated on a runner, so the quality step could never pass and
was merged past (#402). The measurement therefore moves to a maintainer's machine
and travels into the repository as signed evidence, and CI's job becomes checking
that the evidence describes *this* tree. These tests pin what that check refuses.
"""

import copy
import hashlib
import json
import pathlib
import subprocess

import pytest

from test_eval_cases import valid_case


KEY = "evidence-verification-key-at-least-thirty-two-bytes"
COMMAND = 'python3 -c "import os; print(os.environ[\'RIG_EVAL_INPUT\'])"'
JUDGE_COMMAND = (
    'python3 -c "import json; print(json.dumps({\'status\':\'measured\','
    '\'criteria\':[{\'id\':\'correct\',\'status\':\'pass\',\'score\':1.0}]}))"'
)
RECIPE_REL = "skills/engine/recipes/sample.md"
CASE_ID = "verify-case"


def _git(repo: pathlib.Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=repo, check=True,
                               capture_output=True, text=True)
    return completed.stdout.strip()


def _measured(
    tmp_path: pathlib.Path, *, uncovered_surface: str | None = None,
) -> tuple[pathlib.Path, str, pathlib.Path]:
    """A repo whose one prompt change has been measured and the evidence committed.

    Returns the repo, the fork point the gate compares against, and the evidence
    file. `execution_commit` is HEAD's parent here, which is the whole point: the
    act of committing evidence moves HEAD past the commit the evidence describes.

    `uncovered_surface` adds a second prompt surface that no case covers, which is
    the ordinary shape of a change in this repository — two surfaces have cases and
    ~198 do not. It forces the ratchet path through both ends of the workflow.
    """
    from rig_workbench.eval.affected_run import run_affected
    from rig_workbench.eval.cases import canonical_json

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "trunk")
    _git(repo, "config", "user.email", "eval@test.invalid")
    _git(repo, "config", "user.name", "eval-test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")

    recipe = repo / RECIPE_REL
    recipe.parent.mkdir(parents=True)
    recipe.write_text("---\nname: sample\nsteps: []\n---\n", encoding="utf-8")
    case = copy.deepcopy(valid_case())
    case["id"] = CASE_ID
    case["prompt_surfaces"] = ["recipe:sample"]
    case["provider_policy"] = {
        "mode": "allowlist", "allowed": ["command"], "models": ["fixture"],
        "judge_providers": ["command"], "judge_models": ["fixture"],
    }
    case["target_inputs"] = {"prompt_surface_fixture": "explicit binding fixture"}
    case["deterministic_checks"] = ["contains:prompt_surface_fixture"]
    case["clean_controls"] = {"prompt_surface_fixture": "control"}
    case_path = repo / "evals" / "cases" / CASE_ID / "case.json"
    case_path.parent.mkdir(parents=True)
    case_path.write_text(canonical_json(case), encoding="utf-8")
    if uncovered_surface is not None:
        extra = repo / uncovered_surface
        extra.parent.mkdir(parents=True, exist_ok=True)
        extra.write_text("a prompt surface nobody has written a case for\n",
                         encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "prompt change")

    report, code, destination = run_affected(
        repo, base=base, head="HEAD", provider="command", model="fixture",
        judge_provider="command", judge_model="fixture", provider_command=COMMAND,
        judge_command=JUDGE_COMMAND, ratchet=uncovered_surface is not None,
    )
    expected = "debt" if uncovered_surface is not None else "pass"
    assert code == 0 and report["status"] == expected, report
    _git(repo, "add", "evals/evidence")
    _git(repo, "commit", "-q", "-m", "signed evaluation evidence")
    return repo, base, destination / CASE_ID / "current.json"


def _gate(repo: pathlib.Path, base: str, head: str = "HEAD", **kwargs):
    from rig_workbench.eval.gate import evaluate_gate

    return evaluate_gate(repo, base=base, head=head,
                         evidence_dir=repo / "evals" / "evidence", **kwargs)


def _resign(evidence: pathlib.Path, **changes) -> None:
    """Rewrite evidence and re-sign it with the trusted key.

    Without this the forgery tests would only ever re-prove that HMAC works. The
    interesting question is what a key holder's *stale or misdescribed* evidence
    still gets past, so every mutation below carries a signature that verifies.
    """
    from rig_workbench.eval.attestation import sign_result_attestation
    from rig_workbench.eval.cases import canonical_json

    result = json.loads(evidence.read_text(encoding="utf-8"))
    forged = {key: value for key, value in result.items()
              if key not in {"result_sha256", "attestation"}}
    forged.update(changes)
    forged["result_sha256"] = hashlib.sha256(
        canonical_json(forged).encode("utf-8")
    ).hexdigest()
    forged["attestation"] = sign_result_attestation(forged)
    evidence.write_text(canonical_json(forged), encoding="utf-8")


@pytest.fixture(autouse=True)
def _trusted_key(monkeypatch):
    monkeypatch.setenv("RIG_EVAL_ATTESTATION_KEY", KEY)


def test_evidence_committed_after_the_measurement_still_verifies(tmp_path):
    """The chicken-and-egg the old binding could not survive.

    `execution_commit == HEAD` is false the moment evidence is tracked, because
    committing it makes a new HEAD. Verifying at the measured commit — HEAD's
    ancestor — is what lets a measurement live in the repository at all.
    """
    repo, base, evidence = _measured(tmp_path)
    signed = json.loads(evidence.read_text(encoding="utf-8"))
    assert signed["execution_commit"] == _git(repo, "rev-parse", "HEAD~1")

    report, code = _gate(repo, base)
    assert code == 0 and report["status"] == "pass", report
    # And again from a working-tree head, which is the form the local acceptance
    # sensor drives.
    working, working_code = _gate(repo, base, head="working")
    assert working_code == 0 and working["status"] == "pass"


def test_a_change_touching_one_covered_and_one_uncovered_surface_can_pass(tmp_path):
    """The shape that made this job unpassable in both directions.

    Two prompt surfaces in this repository have an evaluation case and ~198 do not,
    so touching a covered surface alongside any of the others is the ordinary PR,
    not the exotic one. Strict, `affected-run` refuses to measure it at all *and*
    the gate reports `uncovered:` — a red no evidence can answer, which is how a
    check teaches people to merge past it (#383/#384). Ratcheting, the covered
    surface is measured and verified while the rest is carried as a reported number.
    """
    repo, base, _evidence = _measured(tmp_path, uncovered_surface="commands/nobody.md")

    report, code = _gate(repo, base, ratchet=True)
    assert code == 0 and report["status"] == "debt", report
    assert report["coverage_debt"] == ["commands/nobody.md"]
    assert report["cases"] == [CASE_ID] and report["failures"] == []

    # Strict is the mode CI used to drive, and it is still available: the failure
    # it reports is the one nothing in the change can fix.
    strict, strict_code = _gate(repo, base)
    assert strict_code == 1
    assert "uncovered:commands/nobody.md" in strict["failures"]


def test_a_signature_from_another_key_or_no_key_at_all_is_refused(tmp_path, monkeypatch):
    repo, base, _evidence = _measured(tmp_path)

    monkeypatch.setenv("RIG_EVAL_ATTESTATION_KEY", "a-different-key-of-at-least-thirty-two-bytes")
    wrong, wrong_code = _gate(repo, base)
    assert wrong_code == 2 and any(item.startswith("invalid_evidence")
                                   for item in wrong["failures"]), wrong

    # No key configured at all: the check cannot be performed, so it fails rather
    # than abstains. A gate that shrugs when it cannot verify is not a gate.
    monkeypatch.delenv("RIG_EVAL_ATTESTATION_KEY")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "empty-state"))
    absent, absent_code = _gate(repo, base)
    assert absent_code == 2 and any(item.startswith("invalid_evidence")
                                    for item in absent["failures"]), absent


def test_the_signed_diff_does_not_depend_on_the_verifying_machines_git_config(tmp_path):
    """The hash is now computed on one machine and recomputed on another.

    While both ends were the same process, `git diff` inheriting the caller's
    configuration cost nothing. Split across a maintainer's laptop and a runner it
    becomes a way for the gate to be permanently unpassable with the cause named
    nowhere: `diff.noprefix` or `diff.renames` in a `~/.gitconfig` changes the
    bytes for an identical pair of trees, and the only report is
    `execution_diff_mismatch`.
    """
    from rig_workbench.eval.execution import execution_diff_sha256

    repo, base, _evidence = _measured(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    default = execution_diff_sha256(repo, base=base, head=head)

    for key, value in (
        ("diff.noprefix", "true"), ("diff.renames", "false"),
        ("diff.algorithm", "histogram"), ("diff.context", "7"),
        ("diff.mnemonicPrefix", "true"), ("diff.indentHeuristic", "false"),
        ("core.quotePath", "true"), ("color.diff", "always"),
    ):
        _git(repo, "config", key, value)
        assert execution_diff_sha256(repo, base=base, head=head) == default, key

    # End to end: evidence signed under the default configuration still verifies
    # against a checkout carrying all of the above.
    report, code = _gate(repo, base)
    assert code == 0 and report["status"] == "pass", report


def test_a_rewritten_diff_hash_is_refused_even_when_the_signature_is_valid(tmp_path):
    """The key holder is trusted to measure, not to describe a tree they did not."""
    repo, base, evidence = _measured(tmp_path)
    _resign(evidence, execution_diff_sha256="f" * 64)
    report, code = _gate(repo, base)
    assert code == 1
    assert f"execution_diff_mismatch:{CASE_ID}" in report["failures"]
    assert f"execution_identity_mismatch:{CASE_ID}" in report["failures"]


def test_a_faked_base_commit_is_refused(tmp_path):
    """Pointing the evidence at a different base makes its own hash wrong.

    The base is the evidence's to choose — CI never supplies it — so the check
    that has to hold is internal: the recorded hash must be the diff from the
    recorded base to the recorded commit, recomputed from history.
    """
    repo, base, evidence = _measured(tmp_path)
    _resign(evidence, execution_base_commit=_git(repo, "rev-parse", "HEAD"))
    report, code = _gate(repo, base)
    assert code == 1 and f"execution_diff_mismatch:{CASE_ID}" in report["failures"]

    _resign(evidence, execution_base_commit="0" * 40)
    unreachable, unreachable_code = _gate(repo, base)
    assert unreachable_code == 1
    assert f"execution_base_unreachable:{CASE_ID}" in unreachable["failures"]


def test_a_measurement_from_outside_this_history_is_refused(tmp_path):
    """Ancestry, not mere existence: evidence must come from a commit this head
    actually contains, or a sibling branch could vouch for a tree nobody reviewed."""
    repo, base, evidence = _measured(tmp_path)
    _git(repo, "checkout", "-q", "-b", "sibling", base)
    (repo / "sibling.txt").write_text("elsewhere\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "sibling work")
    sibling = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "trunk")

    _resign(evidence, execution_commit=sibling)
    report, code = _gate(repo, base)
    assert code == 1
    assert f"execution_commit_unreachable:{CASE_ID}" in report["failures"]


def test_affected_run_refuses_to_measure_a_head_it_is_not_standing_on(tmp_path):
    """The provider only ever sees the checked-out tree.

    Evidence naming a different head would describe a tree nobody measured — and
    because the gate recomputes the diff at the commit the evidence names, that
    claim would verify. The old binding caught this by accident, when CI's own
    head disagreed; nothing catches it now except refusing up front.
    """
    from rig_workbench.eval.affected_run import run_affected
    from rig_workbench.eval.cases import EvalCaseError

    repo, base, _evidence = _measured(tmp_path)
    _git(repo, "checkout", "-q", "-b", "elsewhere", base)
    other = repo / RECIPE_REL
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("---\nname: sample\nsteps: []\n---\nelsewhere\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "a head nobody is standing on")
    _git(repo, "checkout", "-q", "trunk")

    with pytest.raises(EvalCaseError, match="checked-out tree"):
        run_affected(
            repo, base=base, head="elsewhere", provider="command", model="fixture",
            judge_provider="command", judge_model="fixture", provider_command=COMMAND,
            judge_command=JUDGE_COMMAND,
        )


def test_a_prompt_surface_edited_after_the_measurement_invalidates_it(tmp_path):
    """The reuse that ancestry alone would allow.

    Evidence stays green forever if all it has to prove is that it came from some
    earlier commit. What it must also prove is that nothing this change is
    accountable for has moved since.
    """
    repo, base, _evidence = _measured(tmp_path)
    recipe = repo / RECIPE_REL
    recipe.write_text(recipe.read_text(encoding="utf-8") + "unmeasured edit\n",
                      encoding="utf-8")

    # Uncommitted first: the working-tree form has to see it too, or the local
    # sensor would certify an edit the CI gate later refuses.
    dirty, dirty_code = _gate(repo, base, head="working")
    assert dirty_code == 1
    assert f"execution_prompt_surface_changed:{CASE_ID}:{RECIPE_REL}" in dirty["failures"]

    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "prompt edit after measuring")
    committed, committed_code = _gate(repo, base)
    assert committed_code == 1
    assert f"execution_prompt_surface_changed:{CASE_ID}:{RECIPE_REL}" in committed["failures"]
    assert f"execution_identity_mismatch:{CASE_ID}" in committed["failures"]


def test_a_surface_the_base_branch_moved_does_not_invalidate_the_measurement(tmp_path):
    """Why the check intersects with the affected set instead of failing on any
    surface change in the range.

    After a merge, everything the base branch did since the fork sits between the
    measured commit and the new head. None of it is this change's to answer for —
    it was gated on its own PR — and failing on it would put master permanently
    red whenever two prompt PRs land near each other, which is the shape of defect
    this gate keeps relearning (#383, #367).
    """
    repo, base, _evidence = _measured(tmp_path)
    merged = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "-q", "-b", "release", base)
    unrelated = repo / "commands" / "other.md"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("someone else's prompt\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "another PR's prompt change")
    moved_base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge measured branch", merged)

    # The push event's base is the branch tip the merge landed on, so the other
    # PR's surface is behind the comparison and never reaches `affected_surfaces`.
    report, code = _gate(repo, moved_base)
    assert code == 0 and report["status"] == "pass", report

    # Comparing from the fork instead does charge this change with the other PR's
    # surface — and then the measurement genuinely does not cover it.
    from_fork, from_fork_code = _gate(repo, base, ratchet=True)
    assert from_fork_code == 1
    assert (f"execution_prompt_surface_changed:{CASE_ID}:commands/other.md"
            in from_fork["failures"])
