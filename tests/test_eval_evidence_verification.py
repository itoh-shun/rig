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


KEY = "281e19ca85e66d28f2ca844cd986dcd1fa74b2ff0c1a9b3b360e6aa6bd7470a5"
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

    monkeypatch.setenv("RIG_EVAL_ATTESTATION_KEY", "426be8c18c0584f171ad5807f19b1971d6e1878fecb0240c46e1767b675e389d")
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


def test_a_configured_key_that_is_not_machine_generated_is_refused(tmp_path, monkeypatch):
    """Length was never the property that mattered; unguessability was.

    32 bytes of passphrase satisfied the old rule. On a public repository with
    committed evidence, the published `key_id = sha256(key)[:16]` is a complete
    offline oracle against it, and a hit is forgery by an outsider — strictly worse
    than replaying a real measurement.
    """
    from rig_workbench.eval.attestation import sign_result_attestation
    from rig_workbench.eval.cases import EvalCaseError

    repo, base, _evidence = _measured(tmp_path)

    monkeypatch.setenv("RIG_EVAL_ATTESTATION_KEY", "correct-horse-battery-stapler-42")
    with pytest.raises(EvalCaseError, match="64 hex characters"):
        sign_result_attestation({"case_id": CASE_ID})
    report, code = _gate(repo, base)
    assert code == 2 and any(item.startswith("invalid_evidence")
                             for item in report["failures"]), report

    # 64 characters but not hex, so length alone still does not buy it.
    monkeypatch.setenv("RIG_EVAL_ATTESTATION_KEY", "z" * 64)
    with pytest.raises(EvalCaseError, match="64 hex characters"):
        sign_result_attestation({"case_id": CASE_ID})

    monkeypatch.setenv("RIG_EVAL_ATTESTATION_KEY", KEY)
    assert len(sign_result_attestation({"case_id": CASE_ID})["key_id"]) == 16


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


def test_a_measurement_of_a_tree_that_is_not_this_one_is_refused(tmp_path):
    """What replaced ancestry, and why it had to be replaced.

    Requiring the measured commit to be an ancestor of the head reads as the
    natural check and cannot survive this repository's own merge buttons: squash
    and rebase are both enabled, and each rewrites the branch so the measured
    commit is gone or is nobody's ancestor. The binding is the measured *content*
    instead, so what has to stay refused is evidence describing prompt content
    other than the content being landed — however its commit id reads.
    """
    repo, base, evidence = _measured(tmp_path)

    # A commit id swapped for one off this branch. The evidence's own account of
    # itself stops being internally consistent, which is provenance failing.
    _git(repo, "checkout", "-q", "-b", "sibling", base)
    (repo / "sibling.txt").write_text("elsewhere\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "sibling work")
    sibling = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "trunk")

    _resign(evidence, execution_commit=sibling)
    report, code = _gate(repo, base)
    assert code == 1
    assert f"execution_diff_mismatch:{CASE_ID}" in report["failures"]
    assert f"execution_identity_mismatch:{CASE_ID}" in report["failures"]

    # And the substantive form: a real, internally consistent measurement of a
    # different prompt. Nothing about it is forged — it simply measured a recipe
    # this head does not have.
    signed = json.loads(evidence.read_text(encoding="utf-8"))
    _resign(evidence, prompt_surface_digests={
        **signed["prompt_surface_digests"], RECIPE_REL: "0" * 40,
    })
    elsewhere, elsewhere_code = _gate(repo, base)
    assert elsewhere_code == 1
    assert (f"execution_prompt_surface_changed:{CASE_ID}:{RECIPE_REL}"
            in elsewhere["failures"])


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


def _land_rewritten(repo: pathlib.Path, base: str, strategy: str) -> str:
    """Reproduce GitHub's squash and rebase merge buttons, including the aftermath.

    Both are enabled on this repository. Both drop the measured commit out of the
    history: the branch is deleted and, on a runner, `actions/checkout` fetches
    refs rather than loose objects, so the commit the evidence names is simply not
    there. `gc --prune=now` after deleting the branch is what makes the fixture
    match that, rather than quietly leaving the object reachable through a reflog
    the runner would never have.

    Returns the default branch's tip before the merge, which is the base the push
    event hands the gate (`github.event.before`).
    """
    merged = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "master", base)
    (repo / "unrelated.md").write_text("the default branch moved on\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "an unrelated change on the default branch")
    before = _git(repo, "rev-parse", "HEAD")
    if strategy == "squash":
        _git(repo, "merge", "-q", "--squash", merged)
        _git(repo, "commit", "-q", "-m", "the whole PR as one commit (#404)")
    else:
        _git(repo, "checkout", "-q", "trunk")
        _git(repo, "rebase", "-q", "master")
        _git(repo, "checkout", "-q", "master")
        _git(repo, "merge", "-q", "--ff-only", "trunk")
    _git(repo, "branch", "-q", "-D", "trunk")
    _git(repo, "reflog", "expire", "--expire=all", "--all")
    _git(repo, "gc", "-q", "--prune=now")
    return before


@pytest.mark.parametrize("strategy", ["squash", "rebase"])
def test_a_rewriting_merge_does_not_turn_the_default_branch_red(tmp_path, strategy):
    """The failure a PR check cannot show you, because it happens after merging.

    Squash and rebase are both enabled here, and both rewrite the branch. Under an
    ancestry binding the PR is green — the measured commit is the PR head's
    ancestor — and the push job on the default branch is red immediately after the
    merge button, with `execution_commit_unreachable` and no way to recover except
    measuring on the default branch and pushing straight to it. That is #402's
    "merged red" with nobody able to see it coming.
    """
    repo, base, _evidence = _measured(tmp_path)
    before = _land_rewritten(repo, base, strategy)

    # The measured commit is genuinely gone, which is the whole difficulty.
    signed = json.loads(
        (repo / "evals" / "evidence" / CASE_ID / "current.json").read_text(encoding="utf-8")
    )
    assert subprocess.run(["git", "rev-parse", "--verify", "--quiet",
                           signed["execution_commit"] + "^{commit}"],
                          cwd=repo, capture_output=True).returncode != 0

    report, code = _gate(repo, before)
    assert code == 0 and report["status"] == "pass", report

    # And the content binding is still doing its job on the rewritten history: an
    # edit after the measurement fails exactly as it does on a merge commit.
    recipe = repo / RECIPE_REL
    recipe.write_text(recipe.read_text(encoding="utf-8") + "unmeasured edit\n",
                      encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "a prompt edit nobody measured")
    edited, edited_code = _gate(repo, before)
    assert edited_code == 1
    assert f"execution_prompt_surface_changed:{CASE_ID}:{RECIPE_REL}" in edited["failures"]


def test_an_old_signed_measurement_cannot_be_replayed_out_of_the_history(tmp_path):
    """The gate's whole reason to exist, and what it did not check.

    The attacker holds no key. Everything they use is already public in the
    repository: a prompt that was measured green and later reverted by humans, and
    the signed evidence blob that measured it. Restoring both in one PR satisfies
    every other check by construction — the signature is genuine, the measured
    commit really is an ancestor, and the content matches because it is the same
    content. Without a ratchet on the evidence itself, write access to a branch is
    enough to re-land a reverted prompt, and signing the evidence bought nothing.
    """
    from rig_workbench.eval.affected_run import run_affected
    from rig_workbench.eval.cases import canonical_json

    bad = "---\nname: sample\nsteps: []\n---\nBAD PROMPT (later reverted)\n"
    good = "---\nname: sample\nsteps: []\n---\nGOOD PROMPT\n"

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "trunk")
    _git(repo, "config", "user.email", "eval@test.invalid")
    _git(repo, "config", "user.name", "eval-test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    root_commit = _git(repo, "rev-parse", "HEAD")

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

    def measure(measurement_base: str) -> None:
        report, code, _destination = run_affected(
            repo, base=measurement_base, head="HEAD", provider="command",
            model="fixture", judge_provider="command", judge_model="fixture",
            provider_command=COMMAND, judge_command=JUDGE_COMMAND,
        )
        assert code == 0, report
        _git(repo, "add", "evals/evidence")
        _git(repo, "commit", "-q", "-m", "signed evaluation evidence")

    recipe = repo / RECIPE_REL
    recipe.parent.mkdir(parents=True)
    recipe.write_text(bad, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "PR1: the prompt that was later found bad")
    measure(root_commit)
    pr1_tip = _git(repo, "rev-parse", "HEAD")
    replayable = _git(repo, "show", f"{pr1_tip}:evals/evidence/{CASE_ID}/current.json")

    recipe.write_text(good, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "PR2: humans revert it; the eval never saw why")
    measure(pr1_tip)
    pr2_tip = _git(repo, "rev-parse", "HEAD")

    honest, honest_code = _gate(repo, pr1_tip)
    assert honest_code == 0, honest

    recipe.write_text(bad, encoding="utf-8")
    (repo / "evals" / "evidence" / CASE_ID / "current.json").write_text(
        replayable, encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "PR3: re-land the reverted prompt with its old evidence")

    report, code = _gate(repo, pr2_tip)
    assert code == 1, report
    assert f"evidence_regression:{CASE_ID}" in report["failures"]
    assert recipe.read_text(encoding="utf-8") == bad     # the PoC really did re-land it


def test_evidence_older_than_the_base_branchs_is_told_to_measure_again(tmp_path):
    """The price of the ratchet, paid deliberately and recoverably.

    A branch carrying a measurement of the same case older than the one already on
    the base branch is refused when it merges that base branch in. The intersection
    rule alone would have let it through — the neighbouring measurement was gated
    on its own PR — but on the wire that branch is indistinguishable from one
    replaying an old blob, and the two cannot both be answered. This is the
    tightening, chosen over a ratchet with a hole the size of the replay above. It
    names itself, it is the demand the 30-day expiry already makes, and measuring
    again clears it.
    """
    from rig_workbench.eval.affected import prompt_surface_digests
    from rig_workbench.eval.execution import execution_diff_sha256

    repo, base, evidence = _measured(tmp_path)
    signed = json.loads(evidence.read_text(encoding="utf-8"))

    # The base branch takes a newer measurement of this case.
    _git(repo, "checkout", "-q", "-b", "master")
    _resign(evidence, started_at="2026-08-11T09:00:00+00:00")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "a neighbouring PR re-measures the same case")
    newer = _git(repo, "rev-parse", "HEAD")

    # This branch edits its surface and carries a measurement taken before that
    # landed: the content binding is honest, only the measurement is behind.
    _git(repo, "checkout", "-q", "trunk")
    recipe = repo / RECIPE_REL
    recipe.write_text(recipe.read_text(encoding="utf-8") + "this branch's own edit\n",
                      encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "this branch's prompt change")
    measured = _git(repo, "rev-parse", "HEAD")
    _resign(evidence, started_at="2026-08-11T08:00:00+00:00",
            prompt_surface_digests=prompt_surface_digests(repo, measured),
            execution_commit=measured,
            execution_diff_sha256=execution_diff_sha256(
                repo, base=signed["execution_base_commit"], head=measured))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "this branch's own measurement")
    # Its own evidence survives the merge, which is what a human resolving that
    # conflict does.
    _git(repo, "merge", "-q", "--no-ff", "-X", "ours", "-m", "merge the base branch", newer)

    report, code = _gate(repo, newer)
    assert code == 1, report
    # Nothing else is wrong with it: the ratchet is the only complaint.
    assert report["failures"] == [f"evidence_regression:{CASE_ID}"], report

    # Measuring again clears it — same content, a measurement that is not behind.
    _resign(evidence, started_at="2026-08-11T10:00:00+00:00")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "measure again")
    cleared, cleared_code = _gate(repo, newer)
    assert cleared_code == 0 and cleared["status"] == "pass", cleared


def test_the_directory_a_result_is_filed_under_has_to_be_its_case(tmp_path):
    """`<case-id>/current.json` was a convention only the writing side observed.

    Matching on the `case_id` field alone made the directory decoration: a result
    filed under any other case's directory verified, and a stale copy left behind
    by a rename counted as a second current result for a case it was never in.
    """
    repo, base, evidence = _measured(tmp_path)
    misfiled = repo / "evals" / "evidence" / "some-other-case" / "current.json"
    misfiled.parent.mkdir(parents=True)
    misfiled.write_text(evidence.read_text(encoding="utf-8"), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "a copy filed under the wrong case")

    report, code = _gate(repo, base)
    assert code == 0 and report["status"] == "pass", report

    # Filed under its own case, the same copy is the duplicate it looks like.
    duplicate = repo / "evals" / "evidence" / CASE_ID / "previous.json"
    duplicate.write_text(evidence.read_text(encoding="utf-8"), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "two current results for one case")
    counted, counted_code = _gate(repo, base)
    assert counted_code == 1
    assert f"current_evidence_count:{CASE_ID}" in counted["failures"]
