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


def test_validate_workflow_resolves_the_comparison_base_instead_of_trusting_the_payload():
    """The base the gate compares against must not be one the author can pin.

    `github.event.pull_request.base.sha` is the payload's snapshot of the base
    branch, taken when the event fired. Opening the PR before a revert lands pins
    it to the commit that still carried the reverted prompt — and then the diff
    the gate works from no longer contains that surface, so the case is not even
    selected, let alone ratcheted. Resolving the base branch's live tip in the
    runner removes the dependency on when GitHub refreshes that field.
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    workflow = (root / ".github" / "workflows" / "validate.yml").read_text(
        encoding="utf-8"
    )
    # From the end of the previous step, so the comment block that carries the
    # reasoning is inside the slice rather than just the YAML keys.
    resolve = workflow.split("- name: Install rig with its declared", 1)[1].split(
        "- name: Structural affected-case coverage", 1
    )[0]
    # Named in a comment so the next reader knows why it is not used; never
    # evaluated. Checking the interpolation rather than the bare field is what
    # keeps that comment legal.
    assert "${{ github.event.pull_request.base.sha }}" not in workflow
    assert "pull_request.base.sha" in resolve      # ...and the reason is recorded
    # The PR path names the branch and asks git what its tip is now.
    assert "pull_request.base.ref" in resolve
    assert "git fetch" in resolve and "refs/remotes/origin/" in resolve
    assert "git rev-parse" in resolve
    # The push path keeps the tip it replaced. `origin/<branch>` on a push event
    # *is* the commit being gated, so resolving it there would diff the push
    # against itself and the gate would be `noop` forever.
    assert "github.event.before" in resolve
    assert "against itself" in resolve
    # Fail closed. Any fallback here is a way to put the payload value back in
    # play, which is the whole hole.
    assert "exit 2" in resolve
    assert "PR_BASE" not in workflow
    # The branch name reaches the shell through `env:`, never interpolated into
    # the script body, so a ref name can never be shell syntax.
    assert workflow.count("${{ github.event.pull_request.base.ref }}") == 1
    assert "BASE_REF: ${{ github.event.pull_request.base.ref }}" in resolve
    # One resolved base for every consumer, so the structural report, the fork
    # handoff's instructions, and the evidence gate cannot disagree about what
    # "this change" is.
    consumers = [line for line in workflow.splitlines()
                 if "--base " in line and not line.lstrip().startswith("#")]
    assert len(consumers) == 3, consumers
    assert all("steps.comparison.outputs.base" in line for line in consumers), consumers
