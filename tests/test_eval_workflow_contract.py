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
    assert workflow.count('r["affected_cases"]') == 2
    assert "RIG_EVAL_ATTESTATION_KEY" in workflow
    assert "RIG_EVAL_PROVIDER" in workflow and "RIG_EVAL_MODEL" in workflow
    assert "RIG_EVAL_JUDGE_PROVIDER" in workflow and "RIG_EVAL_JUDGE_MODEL" in workflow
    assert "pull_request.head.sha" in workflow
    assert "head.repo.full_name == github.repository" in workflow
    assert "author_association == 'OWNER'" in workflow
    assert "chmod 600" in workflow and "unset RIG_EVAL_ATTESTATION_KEY" in workflow
    assert "missing evidence cannot pass" in workflow
    assert "trusted maintainer run" in workflow
    assert "--provider mock" not in workflow
    assert workflow.index("eval affected") < workflow.index("eval affected-run")
    untrusted = workflow.split("- name: Untrusted or fork prompt quality handoff", 1)[1].split(
        "- name: Trusted prompt quality evidence", 1
    )[0]
    assert "secrets." not in untrusted
    assert "rig-wb eval affected-run" in untrusted and "origin branch" in untrusted
