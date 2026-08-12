import os
import pathlib
import shutil
import subprocess

import pytest


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


def _resolve_step(root: pathlib.Path) -> str:
    """The `run:` body of the base-resolution step, taken from the workflow itself.

    Read rather than restated: a copy would go on passing after the workflow it
    claims to test stopped saying the same thing.
    """
    import yaml

    document = yaml.safe_load(
        (root / ".github" / "workflows" / "validate.yml").read_text(encoding="utf-8")
    )
    step = next(item for item in document["jobs"]["prompt-evaluation"]["steps"]
                if item.get("name") == "Resolve comparison base")
    return step["run"]


def _run_step(script: pathlib.Path, cwd: pathlib.Path, output: pathlib.Path,
              *, base_ref: str = "", push_base: str = "", pr_base: str = "") -> tuple[int, str]:
    """Run the step with everything the event would put in its environment.

    `PR_BASE` is `github.event.pull_request.base.sha`, which the step no longer
    reads. It is supplied anyway so these tests keep saying something when run
    against a workflow that does read it: the difference then shows up as the base
    that comes out, rather than as a step that cannot start.
    """
    output.write_text("", encoding="utf-8")
    completed = subprocess.run(
        # GitHub runs a `run:` block as `bash -e {0}`, and `-e` is what decides
        # whether a failing `git rev-parse` aborts the step or is handled.
        ["bash", "-e", str(script)], cwd=cwd, capture_output=True, text=True,
        env={"PATH": os.environ["PATH"], "HOME": str(cwd),
             "BASE_REF": base_ref, "PUSH_BASE": push_base, "PR_BASE": pr_base,
             "GITHUB_OUTPUT": str(output)},
    )
    return completed.returncode, output.read_text(encoding="utf-8").strip()


@pytest.mark.skipif(shutil.which("bash") is None, reason="the step is a bash script")
def test_the_base_resolution_step_finds_the_live_tip_in_an_actions_checkout(tmp_path):
    """Run the step verbatim against what `actions/checkout` actually leaves behind.

    The step depends on `refs/remotes/origin/<base branch>` being resolvable in a
    workspace checked out at a detached head sha, which is a claim about the
    action rather than about this repository. `actions/checkout` fetches
    `+refs/heads/*:refs/remotes/origin/*` and `+refs/tags/*:refs/tags/*` whenever
    `fetch-depth` is 0, so the ref is there; this reproduces that fetch and proves
    it, and the step re-fetches anyway so the answer is the tip as it stands now
    rather than the tip as of checkout.

    The two failure rows are the point of the test as much as the first two. A
    base this step cannot resolve must stop the job, because every value it could
    fall back to is one the branch under review can influence.
    """
    pytest.importorskip("yaml")
    root = pathlib.Path(__file__).resolve().parent.parent
    script = tmp_path / "resolve.sh"
    script.write_text(_resolve_step(root), encoding="utf-8")

    def git(repo: pathlib.Path, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=repo, check=True,
                              capture_output=True, text=True).stdout.strip()

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-q", "-b", "master")
    git(seed, "config", "user.email", "sim@test.invalid")
    git(seed, "config", "user.name", "sim")
    (seed / "f").write_text("one\n", encoding="utf-8")
    git(seed, "add", ".")
    git(seed, "commit", "-qm", "m1")
    m1 = git(seed, "rev-parse", "HEAD")
    git(seed, "checkout", "-q", "-b", "feature")
    (seed / "g").write_text("two\n", encoding="utf-8")
    git(seed, "add", ".")
    git(seed, "commit", "-qm", "the PR head")
    head_sha = git(seed, "rev-parse", "HEAD")
    git(seed, "checkout", "-q", "master")
    (seed / "h").write_text("three\n", encoding="utf-8")
    git(seed, "add", ".")
    git(seed, "commit", "-qm", "master moves after the PR was opened")
    m2 = git(seed, "rev-parse", "HEAD")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "-q", "origin", "master", "feature")

    runner = tmp_path / "runner"
    runner.mkdir()
    git(runner, "init", "-q")
    git(runner, "remote", "add", "origin", str(origin))
    git(runner, "fetch", "--no-tags", "--prune", "--no-recurse-submodules", "origin",
        "+refs/heads/*:refs/remotes/origin/*", "+refs/tags/*:refs/tags/*")
    git(runner, "checkout", "-q", "--force", head_sha)
    assert git(runner, "rev-parse", "HEAD") == head_sha

    output = tmp_path / "github_output"
    # A PR gets the tip as it stands now — and `m1` is handed to the step at the
    # same time, because that is what `base.sha` still holds for a PR opened
    # before `m2` landed. Ignoring it is the whole change.
    assert m2 != m1
    code, wrote = _run_step(script, runner, output, base_ref="master", pr_base=m1)
    assert (code, wrote) == (0, f"base={m2}"), (code, wrote)

    # A push is gated against the tip it replaced. Resolving `origin/master` here
    # would name the commit being pushed and the diff would be empty forever.
    code, wrote = _run_step(script, runner, output, push_base=m1)
    assert (code, wrote) == (0, f"base={m1}"), (code, wrote)

    code, wrote = _run_step(script, runner, output, base_ref="no-such-branch")
    assert code == 2 and wrote == "", (code, wrote)

    code, wrote = _run_step(script, runner, output, push_base="not-a-sha")
    assert code == 2 and wrote == "", (code, wrote)


@pytest.mark.skipif(shutil.which("bash") is None, reason="the step is a bash script")
def test_the_base_the_workflow_resolves_is_the_one_that_refuses_a_replay(tmp_path, monkeypatch):
    """The replay, gated with the base this workflow actually produces.

    The two tests above pin the halves — the step resolves the live tip, and the
    gate refuses a replay measured against the live tip. This joins them, because
    the bug they answer lived exactly in the join: every check in `eval gate` was
    correct, and CI was handing it a base at which there was nothing to check.

    Borrowing `_reverted` from the verification suite is deliberate. A second copy
    of the M1/revert/replay fixture would drift, and the point is that this is the
    *same* attack that suite already refuses, run through the workflow's own shell.
    """
    pytest.importorskip("yaml")
    import test_eval_evidence_verification as verification
    from rig_workbench.eval.gate import evaluate_gate

    monkeypatch.setenv("RIG_EVAL_ATTESTATION_KEY", verification.KEY)
    root = pathlib.Path(__file__).resolve().parent.parent
    script = tmp_path / "resolve.sh"
    script.write_text(_resolve_step(root), encoding="utf-8")
    git = verification._git

    # M1 measured the bad prompt; the humans reverted and re-measured at M2.
    repo, _root_commit, pr1_tip, pr2_tip, replayable = verification._reverted(tmp_path)

    # The attacker opened the PR at M1, then merged the base branch in and put the
    # prompt and its evidence back. They hold no key; both blobs are public.
    git(repo, "checkout", "-q", "-b", "evil", pr1_tip)
    git(repo, "merge", "-q", "--no-edit", "trunk")
    (repo / verification.RECIPE_REL).write_text(verification.BAD, encoding="utf-8")
    (repo / verification.EVIDENCE_REL).write_text(replayable, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "PR3: re-land the reverted prompt with its evidence")
    head_sha = git(repo, "rev-parse", "HEAD")

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "origin", "trunk", "evil")

    # What the runner has when the step starts: `fetch-depth: 0`, detached at the
    # PR head sha.
    runner = tmp_path / "runner"
    runner.mkdir()
    git(runner, "init", "-q")
    git(runner, "remote", "add", "origin", str(origin))
    git(runner, "fetch", "--no-tags", "--prune", "--no-recurse-submodules", "origin",
        "+refs/heads/*:refs/remotes/origin/*", "+refs/tags/*:refs/tags/*")
    git(runner, "checkout", "-q", "--force", head_sha)

    # `PR_BASE` is the pin: `base.sha` for a PR opened at M1 and never refreshed.
    # The step is given it and must not use it.
    code, wrote = _run_step(script, runner, tmp_path / "github_output",
                            base_ref="trunk", pr_base=pr1_tip)
    assert code == 0, wrote
    resolved = wrote.split("=", 1)[1]
    assert resolved == pr2_tip, f"resolved {resolved}; pinned {pr1_tip}; tip {pr2_tip}"

    def gate(base: str):
        return evaluate_gate(runner, base=base, head="HEAD",
                             evidence_dir=runner / "evals" / "evidence", ratchet=True)

    report, exit_code = gate(resolved)
    assert exit_code == 1, report
    assert f"evidence_regression:{verification.CASE_ID}" in report["failures"], report

    # And with the payload's snapshot, which is what this step used to emit: not a
    # ratchet that stayed quiet, a gate that looked at nothing.
    pinned, pinned_code = gate(pr1_tip)
    assert pinned_code == 0 and pinned["status"] == "noop" and not pinned["cases"], pinned
    assert (runner / verification.RECIPE_REL).read_text(encoding="utf-8") == verification.BAD
