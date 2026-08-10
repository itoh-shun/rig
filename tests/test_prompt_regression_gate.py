import json
import pathlib
import subprocess
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()


def _fixture(tmp_path):
    from rig_workbench.workbench.state import build_acceptance

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "gate@test.invalid")
    _git(repo, "config", "user.name", "gate-test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    task_id = "rig-20260805-prompt-gate"
    task = {"task_id": task_id, "task_type": "bugfix", "base_commit": base,
            "worktree_path": None, "status": "running"}
    run = repo / ".rig" / "runs" / task_id
    run.mkdir(parents=True)
    (run / "task.json").write_text(json.dumps(task), encoding="utf-8")
    (run / "acceptance.json").write_text(
        json.dumps(build_acceptance(task_id, "bugfix", repo)), encoding="utf-8"
    )
    return repo, task, task_id


def test_prompt_criterion_is_diff_conditional_machine_owned_and_manual_pass_is_rejected(
    tmp_path, monkeypatch,
):
    from rig_workbench.workbench import prompt_regression
    from rig_workbench.workbench.state import build_acceptance

    repo, task, task_id = _fixture(tmp_path)
    acc = build_acceptance(task_id, "bugfix", repo)
    assert prompt_regression.ensure_prompt_criterion(repo, task, acc) is False
    assert prompt_regression.CRITERION not in [item["name"] for item in acc["checks"]]

    prompt = repo / "skills" / "engine" / "recipes" / "changed.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("---\nname: changed\nsteps: []\n---\n", encoding="utf-8")
    assert prompt_regression.ensure_prompt_criterion(repo, task, acc) is True
    check = next(item for item in acc["checks"]
                 if item["name"] == prompt_regression.CRITERION)
    assert check["status"] == "pending"

    monkeypatch.setattr(
        prompt_regression, "evaluate_gate",
        lambda *_args, **_kwargs: ({"status": "pass"}, 0),
    )
    prompt_regression.apply_prompt_regression_sensor(repo, task, acc)
    assert check["status"] == "passed"

    environment = {"PYTHONPATH": str(REPO_ROOT), "RIG_HOME": str(REPO_ROOT)}
    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "workbench.py"), "gate", task_id,
         "--set", "prompt_regression_passed=passed"], cwd=repo, env=environment,
        capture_output=True, text=True,
    )
    assert completed.returncode == 1
    assert "machine-controlled" in completed.stderr


def test_prompt_diff_git_failure_fails_closed_and_worktree_evidence_is_used(
    tmp_path, monkeypatch,
):
    from rig_workbench.workbench import prompt_regression
    from rig_workbench.workbench.state import build_acceptance

    repo, task, task_id = _fixture(tmp_path)
    acc = build_acceptance(task_id, "bugfix", repo)
    monkeypatch.setattr(
        prompt_regression.subprocess, "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 2, "", "git failed"),
    )
    assert prompt_regression.ensure_prompt_criterion(repo, task, acc) is True
    prompt_regression.apply_prompt_regression_sensor(repo, task, acc)
    check = next(item for item in acc["checks"]
                 if item["name"] == prompt_regression.CRITERION)
    assert check["status"] == "failed" and "infrastructure" in check["detail"]

    worktree = tmp_path / "worktree"
    task["worktree_path"] = str(worktree)
    seen = {}
    monkeypatch.setattr(prompt_regression, "_has_prompt_diff", lambda *_args: True)

    def fake_gate(repo_arg, **kwargs):
        seen["repo"] = repo_arg
        seen["evidence_dir"] = kwargs["evidence_dir"]
        return {"status": "pass"}, 0

    monkeypatch.setattr(prompt_regression, "evaluate_gate", fake_gate)
    prompt_regression.apply_prompt_regression_sensor(repo, task, acc)
    assert seen["repo"] == worktree
    assert seen["evidence_dir"] == worktree / ".rig" / "evals" / "results"


# ── the criterion follows CI's ratchet, not the strict gate ──────────────────
# The sensor drove `evaluate_gate` in its strict form while CI drove
# `eval affected --ratchet`, so a change whose only prompt-surface edit had no
# case yet failed locally and passed in CI. Debt is warning-grade here: the run
# proceeds, and the missing coverage is named rather than reported as checked.
def _sensor(repo, task):
    from rig_workbench.workbench import prompt_regression
    from rig_workbench.workbench.state import build_acceptance

    acc = build_acceptance(task["task_id"], "bugfix", repo)
    prompt_regression.apply_prompt_regression_sensor(repo, task, acc)
    return acc, next(item for item in acc["checks"]
                     if item["name"] == prompt_regression.CRITERION)


def _write_case(repo, case_id, surfaces):
    import copy

    from rig_workbench.eval.cases import canonical_json
    from test_eval_cases import valid_case

    case = copy.deepcopy(valid_case())
    case["id"] = case_id
    case["target_inputs"] = {"prompt_surface_fixture": f"binding for {case_id}"}
    case["prompt_surfaces"] = surfaces
    path = repo / "evals" / "cases" / case_id / "case.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(case), encoding="utf-8")
    return path


def _touch(repo, relative, text="changed\n"):
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


INSTRUCTION = "skills/engine/facets/instructions/login.md"


def test_a_surface_with_no_case_yet_is_a_warning_and_does_not_block_accept(tmp_path):
    from rig_workbench.eval.affected import analyze_affected
    from rig_workbench.workbench.state import gate_status

    repo, task, _task_id = _fixture(tmp_path)
    _touch(repo, INSTRUCTION)

    ratchet = analyze_affected(repo, base=task["base_commit"], head="working",
                               ratchet=True)
    assert ratchet["status"] == "debt" and ratchet["coverage_debt"] == [INSTRUCTION]

    acc, check = _sensor(repo, task)
    assert check["status"] == "warning"
    assert INSTRUCTION in check["detail"]

    # Warning-grade rather than passed: the gate settles somewhere accept allows,
    # and the missing coverage is still on the report.
    for item in acc["checks"]:
        if item["status"] == "pending":
            item["status"] = "passed"
    assert gate_status(acc) == "passed_with_warnings"


def test_losing_coverage_that_existed_still_fails_the_criterion(tmp_path):
    repo, task, _task_id = _fixture(tmp_path)
    _write_case(repo, "login-case", ["instruction:login"])
    _touch(repo, INSTRUCTION)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add coverage")
    task["base_commit"] = _git(repo, "rev-parse", "HEAD")
    (repo / "evals" / "cases" / "login-case" / "case.json").unlink()

    _acc, check = _sensor(repo, task)
    assert check["status"] == "failed"


def test_an_unregistered_surface_kind_still_fails_the_criterion(tmp_path):
    repo, task, _task_id = _fixture(tmp_path)
    _touch(repo, "skills/engine/recipes/notes.txt")
    _acc, check = _sensor(repo, task)
    assert check["status"] == "failed"


def test_debt_does_not_make_the_criterion_settable(tmp_path):
    """The refusal stays. Debt was the only thing that made this criterion a wall,
    and it no longer blocks; what is left fatal — removed coverage, an untracked
    surface kind, failing evidence — is structural, not a heuristic that can
    false-positive, so there is nothing here for a manual override to correct."""
    repo, _task, task_id = _fixture(tmp_path)
    _touch(repo, INSTRUCTION)
    environment = {"PYTHONPATH": str(REPO_ROOT), "RIG_HOME": str(REPO_ROOT)}
    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "workbench.py"), "gate", task_id,
         "--set", "prompt_regression_passed=passed"], cwd=repo, env=environment,
        capture_output=True, text=True,
    )
    assert completed.returncode == 1
    assert "machine-controlled" in completed.stderr


def test_the_gate_command_reports_debt_as_a_warning_and_exits_zero(tmp_path):
    repo, _task, task_id = _fixture(tmp_path)
    _touch(repo, INSTRUCTION)
    environment = {"PYTHONPATH": str(REPO_ROOT), "RIG_HOME": str(REPO_ROOT)}
    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "workbench.py"), "gate", task_id],
        cwd=repo, env=environment, capture_output=True, text=True,
    )
    assert completed.returncode == 0
    assert "⚠ prompt_regression_passed" in completed.stdout
    assert "prompt-regression sensor: warning" in completed.stdout


def test_a_case_only_diff_still_raises_the_criterion(tmp_path):
    """Pins the trigger, not just its downstream effect: deleting a case touches no
    prompt surface, so a criterion keyed on surfaces alone was blind to the one
    outcome the ratchet keeps fatal."""
    from rig_workbench.workbench import prompt_regression
    from rig_workbench.workbench.state import build_acceptance

    repo, task, task_id = _fixture(tmp_path)
    _write_case(repo, "login-case", ["instruction:login"])
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add coverage")
    task["base_commit"] = _git(repo, "rev-parse", "HEAD")
    (repo / "evals" / "cases" / "login-case" / "case.json").unlink()

    acc = build_acceptance(task_id, "bugfix", repo)
    assert prompt_regression.ensure_prompt_criterion(repo, task, acc) is True
