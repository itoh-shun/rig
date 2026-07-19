import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench import bench, bench_tasks
from rig_workbench.orchestrate import commands


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_planned_arm_order_alternates_without_changing_pair_members():
    assert bench.planned_arm_order(1) == ("bare", "rig")
    assert bench.planned_arm_order(2) == ("rig", "bare")


def test_run_pair_materializes_identical_workspaces_before_either_arm(
    monkeypatch,
):
    task = bench_tasks.load_tasks()["py-auth-sibling-write"]
    created = []
    invoked = []
    real_materialize = bench.materialize
    real_run_bare = bench.run_bare
    real_run_rig = bench.run_rig

    def tracked_materialize(selected_task):
        workspace = real_materialize(selected_task)
        created.append(workspace)
        return workspace

    def tracked_bare(selected_task, provider, model, workspace, options):
        assert len(created) == 2
        assert not (workspace / "hidden_check.py").exists()
        invoked.append(("bare", workspace, selected_task.goal, provider, model))
        return real_run_bare(selected_task, provider, model, workspace, options)

    def tracked_rig(selected_task, provider, model, workspace, options):
        assert len(created) == 2
        assert not (workspace / "hidden_check.py").exists()
        invoked.append(("rig", workspace, selected_task.goal, provider, model))
        return real_run_rig(selected_task, provider, model, workspace, options)

    monkeypatch.setattr(bench, "materialize", tracked_materialize)
    monkeypatch.setattr(bench, "run_bare", tracked_bare)
    monkeypatch.setattr(bench, "run_rig", tracked_rig)

    pair = bench.run_pair(task, 1, "mock", "mock-model", {})

    assert pair.pair_id == "py-auth-sibling-write-001"
    assert pair.provider == "mock"
    assert pair.model == "mock-model"
    assert pair.arm_order == ("bare", "rig")
    assert len(created) == 2
    assert created[0] != created[1]
    assert {entry[1] for entry in invoked} == set(created)
    assert {entry[2] for entry in invoked} == {task.goal}
    assert {entry[3] for entry in invoked} == {"mock"}
    assert {entry[4] for entry in invoked} == {"mock-model"}
    assert len(set(pair.start_trees.values())) == 1
    assert all(not path.exists() for path in created)

    for arm_name in ("bare", "rig"):
        arm = pair.arms[arm_name]
        assert arm.public_test.passed is True
        assert arm.hidden_check.passed is True
        assert arm.git_status
        assert arm.changed_files
        assert len(arm.attempts) == 1
        assert arm.invocation_count == sum(attempt.invocations for attempt in arm.attempts)


def test_run_pair_retains_every_failed_provider_attempt():
    task = bench_tasks.load_tasks()["py-auth-sibling-write"]

    pair = bench.run_pair(task, 2, "mock", None, {"mock_scenario": "timeout"})

    assert pair.arm_order == ("rig", "bare")
    for arm in pair.arms.values():
        assert len(arm.attempts) == 1
        assert arm.attempts[0].invocations == 1
        assert "timeout" in arm.attempts[0].infra_error
        assert arm.invocation_count == 1


def test_pair_json_contains_planning_attempts_status_checks_and_timing():
    task = bench_tasks.load_tasks()["py-auth-sibling-write"]

    data = bench.run_pair(task, 1, "mock", None, {}).to_dict()

    assert data["pair_id"] == "py-auth-sibling-write-001"
    assert data["planned"]["arm_order"] == ["bare", "rig"]
    assert data["planned"]["provider"] == "mock"
    assert data["planned"]["model"] is None
    assert data["planned"]["start_tree"]
    for arm in data["arms"].values():
        assert arm["attempts"]
        assert arm["git_status"]
        assert arm["public_test"]["returncode"] == 0
        assert arm["hidden_check"]["returncode"] == 0
        assert arm["elapsed_s"] >= 0
        assert arm["invocation_count"] >= 1


def test_cmd_bench_mock_uses_external_corpus_and_writes_paired_json(tmp_path):
    output = tmp_path / "bench.json"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "rig_workbench.cli",
            "bench",
            "--tasks",
            "py-auth-sibling-write",
            "--provider",
            "mock",
            "--runs",
            "1",
            "--out",
            str(output),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["provider"] == "mock"
    assert data["recipe"] == "adaptive-bugfix"
    assert data["tasks"][0]["task_id"] == "py-auth-sibling-write"
    pair = data["tasks"][0]["runs"][0]
    assert set(pair["arms"]) == {"bare", "rig"}
    assert pair["arms"]["bare"]["hidden_check"]["passed"] is True
    assert pair["arms"]["rig"]["hidden_check"]["passed"] is True


def test_classify_outcome_prioritizes_infrastructure_errors():
    assert (
        bench.classify_outcome(
            {
                "completed": True,
                "hidden_check": {"passed": True},
                "attempts": [{"infra_error": "timeout"}],
            },
            "bare",
        )
        == "infra_error"
    )


def test_classify_outcome_distinguishes_completion_and_hidden_result():
    assert (
        bench.classify_outcome(
            {"completed": True, "hidden_check": {"passed": True}, "attempts": []},
            "bare",
        )
        == "clean_pass"
    )
    assert (
        bench.classify_outcome(
            {"completed": True, "hidden_check": {"passed": False}, "attempts": []},
            "bare",
        )
        == "silent_defect"
    )
    assert (
        bench.classify_outcome(
            {"completed": False, "hidden_check": {"passed": True}, "attempts": []},
            "rig",
        )
        == "safe_stop"
    )
    assert (
        bench.classify_outcome(
            {"completed": False, "hidden_check": {"passed": False}, "attempts": []},
            "rig",
        )
        == "stopped_wrong"
    )


def test_render_html_tolerates_empty_paired_summary():
    html = bench._render_html(
        {
            "tasks": [],
            "generated": "x",
            "rig_wb_version": "0",
            "provider": "mock",
            "model": None,
        }
    )

    assert "<html" in html
    assert "WIRING ONLY" in html


def test_cli_check_does_not_mutate_non_adaptive_recipe_steps(monkeypatch, tmp_path):
    recipe = tmp_path / "legacy.md"
    recipe.write_text(
        """\
---
name: legacy
steps:
  - id: implement
    instruction: implement
  - id: acceptance
    instruction: acceptance
    gate: acceptance-gate
    checks:
      - "git diff --check"
---
""",
        encoding="utf-8",
    )
    captured = {}

    def fake_run_loop(state, _out, _gen, _ver, cfg, _max_steps, **_kwargs):
        captured["state"] = state
        captured["cfg"] = cfg
        return "DONE"

    monkeypatch.setattr(commands, "run_loop", fake_run_loop)

    with pytest.raises(SystemExit) as exc:
        commands.cmd_run(
            [
                str(recipe),
                "--provider",
                "mock",
                "--check",
                "python -m pytest -q",
                "--out",
                str(tmp_path / "state.json"),
            ]
        )

    assert exc.value.code == 0
    acceptance = captured["state"]["steps"][-1]
    assert acceptance["checks"] == ["git diff --check"]
