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
import os
import pathlib
import shutil
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
CASE_REL = f"evals/cases/{CASE_ID}/case.json"
EVIDENCE_REL = f"evals/evidence/{CASE_ID}/current.json"
BAD = "---\nname: sample\nsteps: []\n---\nBAD PROMPT (later reverted)\n"
GOOD = "---\nname: sample\nsteps: []\n---\nGOOD PROMPT\n"


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


def test_an_untracked_surface_added_after_the_measurement_invalidates_it(tmp_path):
    """The shape a content binding could plausibly miss, and does not.

    A surface created after the measurement has no entry in the signed map — the
    map is taken from the measured commit's tree, and the file was not in it. The
    absence is the finding: there is nothing to compare against, so the surface
    cannot be one that was measured. The working-tree form is where this matters,
    because that is the local sensor's, and an untracked file is what a
    half-finished persona looks like on a maintainer's machine.
    """
    repo, base, _evidence = _measured(tmp_path)
    added = repo / "commands" / "brand-new.md"
    added.parent.mkdir(parents=True, exist_ok=True)
    added.write_text("a prompt surface that did not exist when this was measured\n",
                     encoding="utf-8")

    untracked, untracked_code = _gate(repo, base, head="working", ratchet=True)
    assert untracked_code == 1, untracked
    assert (f"execution_prompt_surface_changed:{CASE_ID}:commands/brand-new.md"
            in untracked["failures"])

    # Committing it does not change the answer; it was still not measured.
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add a surface after measuring")
    committed, committed_code = _gate(repo, base, ratchet=True)
    assert committed_code == 1
    assert (f"execution_prompt_surface_changed:{CASE_ID}:commands/brand-new.md"
            in committed["failures"])


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


def _reverted(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str, str, str]:
    """A history holding everything a replay needs, all of it public.

    A prompt is measured green (PR1); humans revert it and measure again (PR2).
    The attacker holds no key — the bad prompt and the signed blob that measured
    it are both readable straight out of the history, and putting the two back
    satisfies every other check in this module by construction.

    Returns the repo, the commit before any of this (where a branch can fork to
    find no evidence at all), the two PR tips, and PR1's evidence blob. Three
    tests below re-land that blob by three different routes.
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
    case_path = repo / CASE_REL
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
    recipe.write_text(BAD, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "PR1: the prompt that was later found bad")
    measure(root_commit)
    pr1_tip = _git(repo, "rev-parse", "HEAD")
    replayable = _git(repo, "show", f"{pr1_tip}:{EVIDENCE_REL}")

    recipe.write_text(GOOD, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "PR2: humans revert it; the eval never saw why")
    measure(pr1_tip)
    return repo, root_commit, pr1_tip, _git(repo, "rev-parse", "HEAD"), replayable


def test_an_old_signed_measurement_cannot_be_replayed_out_of_the_history(tmp_path):
    """The gate's whole reason to exist, and what it did not check.

    The attacker holds no key. Restoring the reverted prompt together with the
    evidence that measured it satisfies every other check by construction — the
    signature is genuine, the measured commit really is an ancestor, and the
    content matches because it is the same content. Without a ratchet on the
    evidence itself, write access to a branch is enough to re-land a reverted
    prompt, and signing the evidence bought nothing.
    """
    repo, _root, pr1_tip, pr2_tip, replayable = _reverted(tmp_path)
    recipe = repo / RECIPE_REL

    honest, honest_code = _gate(repo, pr1_tip)
    assert honest_code == 0, honest

    recipe.write_text(BAD, encoding="utf-8")
    (repo / EVIDENCE_REL).write_text(replayable, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "PR3: re-land the reverted prompt with its old evidence")

    report, code = _gate(repo, pr2_tip)
    assert code == 1, report
    assert f"evidence_regression:{CASE_ID}" in report["failures"]
    assert recipe.read_text(encoding="utf-8") == BAD     # the PoC really did re-land it


def test_the_evidence_directory_cannot_be_moved_out_from_under_the_ratchet(tmp_path):
    """The same replay, with the comparison pointed somewhere it finds nothing.

    Committing `evals/evidence` as a link to a directory that has no history was
    enough to silence the whole ratchet: the gate reads evidence off the
    filesystem and followed the link, while the comparison resolved the link and
    asked git about `evals/evidence-real`, which no commit has ever held. Neither
    half of that is true any more — the comparison path is a literal, and the
    shape is refused on its own.

    Nothing about this branch is hard to merge: it forks from the base tip, so it
    conflicts with nothing, and the merge really does land the bad prompt.
    """
    repo, _root, _pr1_tip, pr2_tip, replayable = _reverted(tmp_path)

    _git(repo, "checkout", "-q", "-b", "evil")
    relocated = repo / "evals" / "evidence-real" / CASE_ID
    relocated.mkdir(parents=True)
    (relocated / "current.json").write_text(replayable, encoding="utf-8")
    shutil.rmtree(repo / "evals" / "evidence")
    os.symlink("evidence-real", repo / "evals" / "evidence")
    (repo / RECIPE_REL).write_text(BAD, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "PR3: relocate the evidence directory")
    assert _git(repo, "ls-files", "-s", "evals/evidence").startswith("120000")

    report, code = _gate(repo, pr2_tip)
    assert code == 1, report
    assert "evidence_symlink:evals/evidence" in report["failures"]
    assert f"evidence_regression:{CASE_ID}" in report["failures"]

    # And it really was mergeable, which is what made it worth refusing.
    _git(repo, "checkout", "-q", "trunk")
    _git(repo, "merge", "-q", "--no-edit", "evil")
    assert (repo / RECIPE_REL).read_text(encoding="utf-8") == BAD


def test_forking_before_the_evidence_existed_does_not_escape_the_ratchet(tmp_path):
    """Where a branch forks from is the author's choice, so it cannot be the
    comparison point.

    Branching from before this case was ever measured left the fork point holding
    no evidence, and "nothing to compare against" was a pass. The base branch's
    tip is what the measurement has to beat now, and a branch cannot move that.
    """
    repo, root_commit, pr1_tip, pr2_tip, replayable = _reverted(tmp_path)

    _git(repo, "checkout", "-q", "-b", "evil", root_commit)
    for rel in (CASE_REL, RECIPE_REL):
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_git(repo, "show", f"{pr1_tip}:{rel}") + "\n", encoding="utf-8")
    (repo / RECIPE_REL).write_text(BAD, encoding="utf-8")
    evidence = repo / EVIDENCE_REL
    evidence.parent.mkdir(parents=True)
    evidence.write_text(replayable, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "PR3: re-land it from before the evidence existed")
    assert _git(repo, "merge-base", pr2_tip, "HEAD") == root_commit

    report, code = _gate(repo, pr2_tip)
    assert code == 1, report
    assert f"evidence_regression:{CASE_ID}" in report["failures"]
    assert (repo / RECIPE_REL).read_text(encoding="utf-8") == BAD


def _covered_after_the_fork(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str, str]:
    """A surface that landed before anyone wrote a case for it, then got one.

    This repository's actual shape: `evals/cases/` holds a handful of cases and
    every prompt surface predates them, so "fork from before this case existed" is
    available for almost any surface, and needs no key and no forgery — only a
    commit id from the public history.

    Returns the repo, the commit where the surface exists and the case does not,
    and the base branch's tip, where the case exists and has been measured.
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
    root_commit = _git(repo, "rev-parse", "HEAD")

    recipe = repo / RECIPE_REL
    recipe.parent.mkdir(parents=True)
    recipe.write_text(GOOD, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "the prompt surface, with no case for it yet")
    uncovered = _git(repo, "rev-parse", "HEAD")

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
    case_path = repo / CASE_REL
    case_path.parent.mkdir(parents=True)
    case_path.write_text(canonical_json(case), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "somebody writes the case and measures it")
    report, code, _destination = run_affected(
        repo, base=root_commit, head="HEAD", provider="command", model="fixture",
        judge_provider="command", judge_model="fixture", provider_command=COMMAND,
        judge_command=JUDGE_COMMAND,
    )
    assert code == 0, report
    _git(repo, "add", "evals/evidence")
    _git(repo, "commit", "-q", "-m", "signed evaluation evidence")
    return repo, uncovered, _git(repo, "rev-parse", "HEAD")


def test_forking_before_the_case_existed_does_not_drop_the_requirement(tmp_path):
    """The same fork, aimed one layer lower: at the coverage, not the evidence.

    Moving the evidence ratchet to the base tip left the *coverage* ratchet on the
    fork point, and that was enough on its own — with no case at the fork point
    there is nothing to replay and nothing to forge. Fork from before the case was
    written, edit only the prompt, carry no case and no evidence: the surface read
    as one nobody has written a case for, which is debt, which is exit 0. No key,
    no signature, no evidence.

    Then the merge restores the case, because the branch never deleted it, and the
    push to the default branch runs the same gate and fails on
    `execution_prompt_surface_changed` — green PR, red trunk, which is #402's shape
    and the thing every ratchet in this module was written to stop.

    What is compared is the coverage the *merge* would land, so the branch is
    charged for the case it will inherit rather than only for the tree it carries.
    """
    repo, uncovered, base_tip = _covered_after_the_fork(tmp_path)

    # The control: the same edit, forked from the base tip. Refused, and refused
    # for the right reason — the case is right there and its measurement no longer
    # describes the surface.
    _git(repo, "checkout", "-q", "-b", "honest")
    recipe = repo / RECIPE_REL
    recipe.write_text(BAD, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "PR2: change the prompt, from the base tip")
    control, control_code = _gate(repo, base_tip, ratchet=True)
    assert control_code == 1 and any(
        item.startswith("execution_prompt_surface_changed") for item in control["failures"]
    ), control

    _git(repo, "checkout", "-q", "-b", "evil", uncovered)
    recipe.write_text(BAD, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "PR3: change the prompt, carrying no case")
    assert not (repo / CASE_REL).exists() and not (repo / EVIDENCE_REL).exists()
    assert _git(repo, "merge-base", base_tip, "HEAD") == uncovered

    report, code = _gate(repo, base_tip, ratchet=True)
    assert code == 1, report
    assert [item for item in report["failures"]
            if item.startswith(f"coverage_stale:{RECIPE_REL} ")], report
    # Not debt: somebody did write a case for this surface. Reporting it as debt is
    # the bypass, so the same path must not appear as both.
    assert report["coverage_debt"] == [], report

    # And it really was mergeable — no conflict, and the bad prompt lands.
    _git(repo, "checkout", "-q", "trunk")
    _git(repo, "merge", "-q", "--no-edit", "evil")
    assert recipe.read_text(encoding="utf-8") == BAD
    assert (repo / CASE_REL).exists(), "the merge restores the case the branch lacked"
    after, after_code = _gate(repo, base_tip, ratchet=True)
    assert after_code == 1 and any(
        item.startswith("execution_prompt_surface_changed") for item in after["failures"]
    ), after                          # the red push the PR is no longer hiding


def test_carrying_the_case_with_its_binding_emptied_is_the_same_refusal(tmp_path):
    """The one-line mutation of the fork above, which "is the case present?" misses.

    Carry the case, and delete the surface it binds. At the fork point the case did
    not exist, so nothing was taken away and it is not a coverage regression; the
    branch's own tree then says this surface is covered by nothing at all. Only
    asking the landing tree — the branch's cases plus what the base branch gained
    since the fork — separates that from honest debt, which is why both readings go
    through one predicate rather than two spellings of it.
    """
    from rig_workbench.eval.cases import canonical_json

    repo, uncovered, base_tip = _covered_after_the_fork(tmp_path)

    _git(repo, "checkout", "-q", "-b", "evil", uncovered)
    case = json.loads(_git(repo, "show", f"{base_tip}:{CASE_REL}"))
    case["prompt_surfaces"] = []
    case_path = repo / CASE_REL
    case_path.parent.mkdir(parents=True, exist_ok=True)
    case_path.write_text(canonical_json(case), encoding="utf-8")
    (repo / RECIPE_REL).write_text(BAD, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "PR3: keep the case, drop what it covers")

    report, code = _gate(repo, base_tip, ratchet=True)
    assert code == 1, report
    assert [item for item in report["failures"]
            if item.startswith(f"coverage_stale:{RECIPE_REL} ")], report
    assert report["coverage_debt"] == [], report


PERSONA_REL = "skills/engine/facets/personas/p.md"
PERSONA_GOOD = "---\nname: p\n---\nGOOD PERSONA\n"
PERSONA_BAD = "---\nname: p\n---\nBAD PERSONA (never measured)\n"
UNWIRED = "---\nname: sample\nsteps: []\n---\nRECIPE\n"
WIRED = "---\nname: sample\nsteps:\n  - id: s1\n    personas: [p]\n---\nRECIPE\n"
SECOND_REL = "skills/engine/recipes/second.md"
SECOND = "---\nname: second\nsteps:\n  - id: s1\n    personas: [p]\n---\nSECOND\n"
CASE2_ID = "verify-case-2"
CASE2_REL = f"evals/cases/{CASE2_ID}/case.json"


def _write_measurable_case(repo: pathlib.Path, case_id: str, rel: str,
                           surfaces: list[str]) -> None:
    from rig_workbench.eval.cases import canonical_json

    case = copy.deepcopy(valid_case())
    case["id"] = case_id
    case["prompt_surfaces"] = surfaces
    case["provider_policy"] = {
        "mode": "allowlist", "allowed": ["command"], "models": ["fixture"],
        "judge_providers": ["command"], "judge_models": ["fixture"],
    }
    case["target_inputs"] = {"prompt_surface_fixture": "explicit binding fixture"}
    case["deterministic_checks"] = ["contains:prompt_surface_fixture"]
    case["clean_controls"] = {"prompt_surface_fixture": "control"}
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(case), encoding="utf-8")


def _measure(repo: pathlib.Path, base: str) -> None:
    from rig_workbench.eval.affected_run import run_affected

    report, code, _destination = run_affected(
        repo, base=base, head="HEAD", provider="command", model="fixture",
        judge_provider="command", judge_model="fixture", provider_command=COMMAND,
        judge_command=JUDGE_COMMAND, ratchet=True,
    )
    assert code == 0, report
    _git(repo, "add", "evals/evidence")
    _git(repo, "commit", "-q", "-m", "signed evaluation evidence")


def _persona_nothing_references(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str, str]:
    """A measured recipe, and beside it a persona no recipe references.

    Returns the repo, the fork point — where the persona is reachable from no
    recipe at all — and the root commit. What the base branch does next is what
    each test varies.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "trunk")
    _git(repo, "config", "user.email", "eval@test.invalid")
    _git(repo, "config", "user.name", "eval-test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    root_commit = _git(repo, "rev-parse", "HEAD")

    recipe = repo / RECIPE_REL
    recipe.parent.mkdir(parents=True)
    recipe.write_text(UNWIRED, encoding="utf-8")
    persona = repo / PERSONA_REL
    persona.parent.mkdir(parents=True)
    persona.write_text(PERSONA_GOOD, encoding="utf-8")
    _write_measurable_case(repo, CASE_ID, CASE_REL, ["recipe:sample"])
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "a measured recipe, and a persona nobody references")
    _measure(repo, root_commit)
    return repo, _git(repo, "rev-parse", "HEAD"), root_commit


def test_forking_before_the_covering_reference_existed_is_the_same_refusal(tmp_path):
    """The same fork, aimed at the *graph* instead of at the case set.

    Coverage is not decided by the cases alone. A persona is covered because some
    recipe references it and a case binds that recipe, and correcting only the case
    set left that reference read off the branch's own tree — so the landing view
    judged the merge by the branch's wiring.

    Fork from before the base branch pointed the recipe at the persona, and edit
    only the persona. The branch touches no recipe, and its tree honestly reports
    that nothing reaches the persona: debt, exit 0. The merge restores the recipe
    the branch never touched, and the push to the default branch goes red on the
    prompt surface digest. #402's shape once more, one layer further out.
    """
    from rig_workbench.eval.affected import _recipes_by_surface, _surface

    repo, fork, _root = _persona_nothing_references(tmp_path)
    persona = repo / PERSONA_REL
    surfaces = [_surface(PERSONA_REL)]
    assert _recipes_by_surface(repo, surfaces)[PERSONA_REL] == []

    (repo / RECIPE_REL).write_text(WIRED, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "the base branch wires the persona into the recipe")
    _measure(repo, fork)
    base_tip = _git(repo, "rev-parse", "HEAD")
    assert _recipes_by_surface(repo, surfaces)[PERSONA_REL] == ["sample"]

    # The control: the same edit from the base tip, where the branch's own tree
    # shows the reference. Refused on the measurement rather than on the coverage.
    _git(repo, "checkout", "-q", "-b", "honest")
    persona.write_text(PERSONA_BAD, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "PR2: edit the covered persona, from the base tip")
    control, control_code = _gate(repo, base_tip, ratchet=True)
    assert control_code == 1 and any(
        item.startswith("execution_prompt_surface_changed") for item in control["failures"]
    ), control

    _git(repo, "checkout", "-q", "-b", "evil", fork)
    persona.write_text(PERSONA_BAD, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "PR3: edit the persona only")
    assert _git(repo, "diff", "--name-only", fork, "HEAD") == PERSONA_REL, \
        "the attack touches no recipe at all"

    report, code = _gate(repo, base_tip, ratchet=True)
    assert code == 1, report
    assert [item for item in report["failures"]
            if item.startswith(f"coverage_stale:{PERSONA_REL} ") and CASE_ID in item], report
    assert report["coverage_debt"] == [], report

    # And the merge really does restore the reference, with no conflict.
    _git(repo, "checkout", "-q", "trunk")
    _git(repo, "merge", "-q", "--no-edit", "evil")
    assert persona.read_text(encoding="utf-8") == PERSONA_BAD
    assert (repo / RECIPE_REL).read_text(encoding="utf-8") == WIRED
    after, after_code = _gate(repo, base_tip, ratchet=True)
    assert after_code == 1 and any(
        item.startswith("execution_prompt_surface_changed") for item in after["failures"]
    ), after                          # the red push the PR is no longer hiding


def test_forking_before_the_covering_recipe_existed_is_the_same_refusal(tmp_path):
    """The variant that re-reading only the *edges* would still let through.

    Here the base branch adds the covering recipe and its case after the fork, so
    the landing case set is already right — `verify-case-2` binds `recipe:second`
    and is restored by `_landing_coverage` alone. What is missing is the recipe
    itself: the list of recipes to ask about is derived from the graph, and the
    branch has never heard of this one. Both arguments of "is this covered?" have
    to be landing versions, not one of the two.
    """
    repo, fork, _root = _persona_nothing_references(tmp_path)

    (repo / SECOND_REL).write_text(SECOND, encoding="utf-8")
    _write_measurable_case(repo, CASE2_ID, CASE2_REL, ["recipe:second"])
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "the base branch adds a recipe that uses the persona")
    _measure(repo, fork)
    base_tip = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "-q", "-b", "evil", fork)
    (repo / PERSONA_REL).write_text(PERSONA_BAD, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "PR: edit the persona only")
    assert _git(repo, "diff", "--name-only", fork, "HEAD") == PERSONA_REL

    report, code = _gate(repo, base_tip, ratchet=True)
    assert code == 1, report
    assert [item for item in report["failures"]
            if item.startswith(f"coverage_stale:{PERSONA_REL} ") and CASE2_ID in item], report
    assert report["coverage_debt"] == [], report

    _git(repo, "checkout", "-q", "trunk")
    _git(repo, "merge", "-q", "--no-edit", "evil")
    assert (repo / PERSONA_REL).read_text(encoding="utf-8") == PERSONA_BAD
    after, after_code = _gate(repo, base_tip, ratchet=True)
    assert after_code == 1 and any(
        item.startswith("execution_prompt_surface_changed") for item in after["failures"]
    ), after


def test_the_replay_is_refused_at_the_base_tip_and_invisible_at_a_pinned_snapshot(tmp_path):
    """Why `--base` has to be resolved rather than read out of the event payload.

    `github.event.pull_request.base.sha` is the base branch as the event saw it.
    An author cannot set it to an arbitrary commit, but they can *pin* it, by
    opening the PR before the revert lands — an ordinary thing to do, with the
    30-day evidence expiry as the only bound on how long the pin is worth holding.

    What that buys is not a quiet ratchet. It is a blind gate. The affected set
    diffs from `merge-base(base, head)`, so a head restored to the pinned commit's
    content has no prompt surface in its diff at all: no case is selected, and the
    gate returns before it reaches evidence, symlinks, or anything else in this
    module. Both halves are asserted below, because a guard written against the
    *status* would only move the hole to a repository busy enough that some
    unrelated file also moved between the two commits.

    So the fix is not in this module — the ratchet is right, and it is the same
    check either way. CI resolves the base branch's live tip instead
    (`.github/workflows/validate.yml`, pinned by `test_eval_workflow_contract.py`).
    Against that base the same branch is refused, which is what this keeps true.
    """
    repo, _root, pr1_tip, pr2_tip, replayable = _reverted(tmp_path)

    # The attacker branches where the PR was opened, then updates it the ordinary
    # way — merge the base branch in so the PR is mergeable — and puts both files
    # back to what the pinned commit held. Nothing here needs a key.
    _git(repo, "checkout", "-q", "-b", "evil", pr1_tip)
    _git(repo, "merge", "-q", "--no-edit", "trunk")
    (repo / RECIPE_REL).write_text(BAD, encoding="utf-8")
    (repo / EVIDENCE_REL).write_text(replayable, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "PR3: re-land the reverted prompt and its evidence")

    live, live_code = _gate(repo, pr2_tip, ratchet=True)
    assert live_code == 1, live
    assert live["failures"] == [f"evidence_regression:{CASE_ID}"], live
    assert live["cases"] == [CASE_ID], live

    # The pinned snapshot, recorded rather than tolerated: it is what CI used to
    # hand the gate, and the reason it no longer does. Nothing is examined at all
    # here, so `failures` being empty says nothing about this branch.
    pinned, pinned_code = _gate(repo, pr1_tip, ratchet=True)
    assert pinned_code == 0 and not pinned["failures"], pinned
    assert pinned["cases"] == [] and pinned["status"] == "noop", pinned

    # And it merges, which is what made the difference between those two bases
    # worth changing a workflow over.
    _git(repo, "checkout", "-q", "trunk")
    _git(repo, "merge", "-q", "--no-edit", "evil")
    assert (repo / RECIPE_REL).read_text(encoding="utf-8") == BAD
    assert (repo / EVIDENCE_REL).read_text(encoding="utf-8").strip() == replayable.strip()


def test_evidence_older_than_the_base_branchs_is_told_to_measure_again(tmp_path):
    """The price of the ratchet, paid deliberately and recoverably.

    A branch carrying a measurement of the same case older than the one already on
    the base branch is refused as soon as the comparison sees that base branch —
    the merge below is how this fixture arranges a shared surface, not what
    triggers the refusal; `test_the_bound_is_the_newest_evidence_on_the_base_branch`
    pins the same failure with no merge at all. The intersection
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


def test_a_base_branch_the_ratchet_cannot_read_is_refused_rather_than_waved_through(
    tmp_path,
):
    """A question that cannot be answered is an accusation, not a pass.

    Every other unanswerable case in this module abstains, which is the right
    stance for a guard that sits alongside others. This one sits alone: every
    remaining check passes a replayed measurement by construction, so "I could not
    read the base branch" has to be a refusal or the attack becomes "arrange for
    it to be unreadable".

    The unreadable base here is a real shape rather than a stub. A clone made with
    `--filter=blob:none` has the trees but not the blobs, and reads them from the
    remote on demand; on a runner with no network for that fetch, this is exactly
    what the ratchet sees.
    """
    from rig_workbench.eval.affected import prompt_surface_digests
    from rig_workbench.eval.execution import execution_diff_sha256

    repo, _base, evidence = _measured(tmp_path)
    signed = json.loads(evidence.read_text(encoding="utf-8"))
    # The base branch's own copy of this evidence, which is the blob that goes
    # missing below. It exists nowhere in this branch's history, so nothing else
    # the gate recomputes needs to read it.
    _git(repo, "checkout", "-q", "-b", "master")
    _resign(evidence, started_at="2026-08-11T09:00:00+00:00")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "the base branch re-measures the same case")
    newer = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "-q", "trunk")
    recipe = repo / RECIPE_REL
    recipe.write_text(recipe.read_text(encoding="utf-8") + "this branch's own edit\n",
                      encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "this branch's prompt change")
    measured = _git(repo, "rev-parse", "HEAD")
    _resign(evidence, started_at="2026-08-11T10:00:00+00:00",
            prompt_surface_digests=prompt_surface_digests(repo, measured),
            execution_commit=measured,
            execution_diff_sha256=execution_diff_sha256(
                repo, base=signed["execution_base_commit"], head=measured))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "this branch's own measurement")

    blob = _git(repo, "rev-parse", f"{newer}:{EVIDENCE_REL}")
    loose = repo / ".git" / "objects" / blob[:2] / blob[2:]
    assert loose.is_file(), "fixture assumes the base branch's evidence is a loose object"
    loose.unlink()

    report, code = _gate(repo, newer)
    assert code == 1, report
    assert report["failures"] == [f"evidence_ratchet_unavailable:{CASE_ID}"], report


def test_the_bound_is_the_newest_evidence_on_the_base_branch_for_that_case(tmp_path):
    """Which of the base branch's files the comparison is taken from.

    The head side rejects a second current result outright; the base side cannot,
    because it reads whatever a past commit happens to hold — a leftover from a
    rename, a result filed under the wrong case. So it takes the newest
    measurement of the case being gated, and it applies the same rule the head
    side does about the directory a result is filed under. Taking the oldest would
    lower the bound; reading another case's directory would raise it.
    """
    from rig_workbench.eval.affected import prompt_surface_digests
    from rig_workbench.eval.execution import execution_diff_sha256

    repo, _base, evidence = _measured(tmp_path)
    signed = json.loads(evidence.read_text(encoding="utf-8"))

    _git(repo, "checkout", "-q", "-b", "master")
    _resign(evidence, started_at="2026-08-11T09:00:00+00:00")
    stale = evidence.parent / "previous.json"
    stale.write_text(evidence.read_text(encoding="utf-8"), encoding="utf-8")
    _resign(stale, started_at="2026-08-11T08:00:00+00:00")
    misfiled = repo / "evals" / "evidence" / "some-other-case" / "current.json"
    misfiled.parent.mkdir(parents=True)
    misfiled.write_text(evidence.read_text(encoding="utf-8"), encoding="utf-8")
    _resign(misfiled, started_at="2026-08-11T11:00:00+00:00")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "the base branch, with the debris a branch collects")
    newer = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "-q", "trunk")
    recipe = repo / RECIPE_REL
    recipe.write_text(recipe.read_text(encoding="utf-8") + "this branch's own edit\n",
                      encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "this branch's prompt change")
    measured = _git(repo, "rev-parse", "HEAD")

    def measure_at(started_at: str):
        _resign(evidence, started_at=started_at,
                prompt_surface_digests=prompt_surface_digests(repo, measured),
                execution_commit=measured,
                execution_diff_sha256=execution_diff_sha256(
                    repo, base=signed["execution_base_commit"], head=measured))
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"measured at {started_at}")
        return _gate(repo, newer)

    # Newer than the base branch's measurement of *this* case. The 11:00 result
    # filed under another case's directory is not a measurement of this one.
    report, code = measure_at("2026-08-11T10:00:00+00:00")
    assert code == 0 and report["status"] == "pass", report

    # Behind it, though ahead of the leftover the rename left in the same
    # directory: the bound is the newest of them, not the oldest.
    behind, behind_code = measure_at("2026-08-11T08:30:00+00:00")
    assert behind_code == 1
    assert behind["failures"] == [f"evidence_regression:{CASE_ID}"], behind


def test_two_branches_measuring_one_case_cannot_merge_without_a_human(tmp_path):
    """What actually collects the ratchet's price, and it is not this gate.

    Two branches whose surfaces are covered by the same case both write
    `evals/evidence/<case-id>/current.json`, and a result is one line of canonical
    JSON whose `started_at`, `result_sha256`, and `attestation` cannot coincide.
    So the second branch conflicts and no merge button will land it, which is why
    comparing against the base tip only changes *when* the demand to measure again
    arrives, not whether it does.

    The docs lean on this, so it is pinned: multi-line evidence would merge
    cleanly and dissolve the guarantee, and so would a `*.json` merge driver in
    `.gitattributes` — of which this repository has none.
    """
    repo, _base, evidence = _measured(tmp_path)
    assert len(evidence.read_text(encoding="utf-8").splitlines()) == 1
    assert not (repo / ".gitattributes").exists()
    fork = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "-q", "-b", "neighbour")
    _resign(evidence, started_at="2026-08-11T09:00:00+00:00")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "a neighbouring PR measures the same case")

    _git(repo, "checkout", "-q", "-b", "mine", fork)
    _resign(evidence, started_at="2026-08-11T08:00:00+00:00")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "this PR measures the same case")

    merged = subprocess.run(["git", "merge", "--no-edit", "neighbour"], cwd=repo,
                            capture_output=True, text=True)
    assert merged.returncode != 0
    # Asked of the index rather than of git's message, which is neither a stable
    # string nor reliably on one stream.
    assert _git(repo, "ls-files", "-u", "--", EVIDENCE_REL)


def test_evidence_carrying_no_content_binding_at_all_is_refused(tmp_path):
    """What a key holder can produce by accident, and the gate's floor under it.

    `rig-wb eval run` writes `prompt_surface_digests: null` — it measures without
    a tree to bind to — and that file is a properly signed result. Filed under
    `evals/evidence/` it would be a measurement of nothing in particular, and the
    content binding, which is the only binding left after a squash, would have
    nothing to compare. Refusing it is what makes the rest of that check load
    bearing rather than optional.
    """
    repo, base, evidence = _measured(tmp_path)
    _resign(evidence, prompt_surface_digests=None)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "evidence measured without a tree")

    report, code = _gate(repo, base)
    assert code == 1, report
    assert f"execution_digests_absent:{CASE_ID}" in report["failures"]
    assert f"execution_identity_mismatch:{CASE_ID}" in report["failures"]


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
