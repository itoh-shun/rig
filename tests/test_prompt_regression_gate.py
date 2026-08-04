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

    prompt = repo / "skills" / "rig" / "recipes" / "changed.md"
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
