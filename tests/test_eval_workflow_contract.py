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
    # CI verifies a maintainer's signed measurement; it never drives a provider
    # itself. `affected-run` starts the provider as an external binary, which is
    # not installed and not authenticated on a runner — the job could not pass
    # with every secret set, and #402 merged red proving it.
    trusted = workflow.split("- name: Trusted prompt quality evidence", 1)[1]
    executed = [line.strip() for line in trusted.splitlines()
                if line.strip() and not line.strip().startswith(("#", "echo"))]
    assert all("affected-run" not in line for line in executed)
    assert "eval gate" in trusted and "--evidence-dir evals/evidence" in trusted
    # Both steps in this job ratchet or neither does. Strict here fails a PR that
    # touches one covered surface next to any surface without a case yet — an
    # `uncovered:` no amount of signed evidence can answer — while the step above
    # calls that same surface debt and exits 0.
    gate_invocation = [line for line in trusted.splitlines()
                       if "eval gate" in line or "--evidence-dir" in line]
    assert any("--ratchet" in line for line in gate_invocation), gate_invocation
    # Fail closed, and on the signing key alone: the other four secrets only pin
    # evidence that is already signed, so requiring them would keep the job
    # unpassable for no verification gained.
    assert 'if [ -z "$RIG_EVAL_ATTESTATION_KEY" ]; then' in trusted
    for optional in ("RIG_EVAL_PROVIDER", "RIG_EVAL_MODEL",
                     "RIG_EVAL_JUDGE_PROVIDER", "RIG_EVAL_JUDGE_MODEL"):
        assert f'if [ -n "${optional}" ]; then' in trusted
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
    # A measurement nobody committed is a measurement CI cannot see.
    assert "evals/evidence/" in untrusted
