import pathlib


def test_validate_workflow_enforces_structural_and_trusted_prompt_evidence():
    root = pathlib.Path(__file__).resolve().parent.parent
    workflow = (root / ".github" / "workflows" / "validate.yml").read_text(
        encoding="utf-8"
    )
    assert "fetch-depth: 0" in workflow
    # Ratchet rather than threshold: `--require-cases` fired on every prompt-surface
    # change while the corpus was empty, so it carried no signal and got merged past.
    assert "eval affected" in workflow and "--ratchet" in workflow
    invocation = [line for line in workflow.splitlines()
                  if "--ratchet" in line or "--require-cases" in line
                  if not line.lstrip().startswith("#")]
    assert invocation and all("--require-cases" not in line for line in invocation)
    assert "coverage_debt" in workflow          # the debt is reported, not swallowed
    assert "eval affected-run" in workflow
    # The paid steps run when there is a case to run, not merely when a file moved.
    # Three reads now, not two: the third names the cases in the debt warning below,
    # and naming what went unmeasured is the point of that branch. Counting the guards
    # by their `runnable` comparison keeps the original assertion — that neither paid
    # step is entered on a bare file move — without pinning unrelated reads of the key.
    assert workflow.count('r["affected_cases"]') == 3
    assert workflow.count('if [ "$runnable" = "0" ]') == 2
    assert "RIG_EVAL_ATTESTATION_KEY" in workflow
    assert "RIG_EVAL_PROVIDER" in workflow and "RIG_EVAL_MODEL" in workflow
    assert "RIG_EVAL_JUDGE_PROVIDER" in workflow and "RIG_EVAL_JUDGE_MODEL" in workflow
    assert "pull_request.head.sha" in workflow
    assert "head.repo.full_name == github.repository" in workflow
    assert "author_association == 'OWNER'" in workflow
    assert "chmod 600" in workflow and "unset RIG_EVAL_ATTESTATION_KEY" in workflow
    # The trusted step used to refuse outright when the secrets were unset. It no
    # longer does, and these two assertions used to pin the refusal message. What
    # replaced it must still be visibly a *shortfall*: this repository cannot run a
    # case at all — no secrets, and no provider executable on the runner — so a red
    # here named nothing anyone could act on. The debt is announced instead, and the
    # assertions below hold the announcement to being loud and specific rather than
    # letting the branch degrade into a silent `exit 0`.
    trusted = workflow.split("- name: Trusted prompt quality evidence", 1)[1]
    assert "::warning::prompt quality unmeasured" in trusted
    assert "$GITHUB_STEP_SUMMARY" in trusted
    assert "RIG_EVAL_* unset" in trusted
    # Enforcement where a lane exists comes from `affected-run`'s exit code, so
    # nothing may swallow it.
    assert "continue-on-error" not in workflow
    assert "|| true" not in trusted
    # The fork handoff still refuses outright; only the trusted branch changed.
    assert "missing evidence cannot pass" not in workflow
    assert "origin branch" in workflow
    assert "--provider mock" not in workflow
    assert workflow.index("eval affected") < workflow.index("eval affected-run")
    untrusted = workflow.split("- name: Untrusted or fork prompt quality handoff", 1)[1].split(
        "- name: Trusted prompt quality evidence", 1
    )[0]
    assert "secrets." not in untrusted
    assert "rig-wb eval affected-run" in untrusted and "origin branch" in untrusted
