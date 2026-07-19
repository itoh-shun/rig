import json
import pathlib
import subprocess
import sys
from types import SimpleNamespace

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
    artifact_dirs = []
    real_materialize = bench.materialize
    real_run_bare = bench.run_bare
    real_run_rig = bench.run_rig
    real_git_evidence = bench._git_evidence

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
        artifact_dirs.append(pathlib.Path(options["artifact_dir"]))
        invoked.append(("rig", workspace, selected_task.goal, provider, model))
        return real_run_rig(selected_task, provider, model, workspace, options)

    def tracked_git_evidence(workspace):
        assert not (workspace / ".rig").exists()
        assert not (workspace / "run-state.json").exists()
        assert not (workspace / "step-outputs").exists()
        if any(name == "rig" and path == workspace for name, path, *_ in invoked):
            assert artifact_dirs
            assert not artifact_dirs[-1].exists()
        return real_git_evidence(workspace)

    monkeypatch.setattr(bench, "materialize", tracked_materialize)
    monkeypatch.setattr(bench, "run_bare", tracked_bare)
    monkeypatch.setattr(bench, "run_rig", tracked_rig)
    monkeypatch.setattr(bench, "_git_evidence", tracked_git_evidence)

    bench_options = {}
    pair = bench.run_pair(task, 1, "mock", "mock-model", bench_options)

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
    assert pair.arms["rig"].runner_state["recipe"] == "adaptive-bugfix"
    assert pair.arms["rig"].runner_state["step_state"]["acceptance"]["checks"]


def test_run_pair_retains_every_failed_provider_attempt():
    task = bench_tasks.load_tasks()["py-auth-sibling-write"]

    pair = bench.run_pair(task, 2, "mock", None, {"mock_scenario": "timeout"})

    assert pair.arm_order == ("rig", "bare")
    assert pair.model == "mock"
    for arm in pair.arms.values():
        assert len(arm.attempts) == 1
        assert arm.attempts[0].model == "mock"
        assert arm.attempts[0].invocations == 1
        assert "timeout" in arm.attempts[0].infra_error
        assert arm.invocation_count == 1


def test_unhandled_adapter_failure_does_not_guess_a_provider_call(monkeypatch):
    task = bench_tasks.load_tasks()["py-auth-sibling-write"]
    monkeypatch.setattr(
        bench,
        "run_bare",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("before call")),
    )

    pair = bench.run_pair(task, 1, "mock", "mock", {})

    assert pair.arms["bare"].attempts[0].invocations == 0
    assert pair.arms["bare"].attempts[0].infra_error.startswith("harness_failure")


def test_pair_json_contains_planning_attempts_status_checks_and_timing():
    task = bench_tasks.load_tasks()["py-auth-sibling-write"]

    data = bench.run_pair(task, 1, "mock", None, {}).to_dict()

    assert data["pair_id"] == "py-auth-sibling-write-001"
    assert data["planned"]["arm_order"] == ["bare", "rig"]
    assert data["planned"]["provider"] == "mock"
    assert data["planned"]["model"] == "mock"
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
    assert data["model"] == "mock"
    assert data["recipe"] == "adaptive-bugfix"
    assert data["tasks"][0]["task_id"] == "py-auth-sibling-write"
    pair = data["tasks"][0]["runs"][0]
    assert set(pair["arms"]) == {"bare", "rig"}
    assert pair["arms"]["bare"]["hidden_check"]["passed"] is True
    assert pair["arms"]["rig"]["hidden_check"]["passed"] is True


def test_run_benchmark_resolves_one_model_for_every_pair(monkeypatch):
    task = bench_tasks.load_tasks()["py-auth-sibling-write"]
    resolve_calls = []
    pair_models = []

    def fake_resolve(provider, requested_model, options):
        resolve_calls.append((provider, requested_model, options))
        return "discovered-model"

    def fake_run_pair(selected_task, run_index, provider, model, options):
        pair_models.append(model)
        return SimpleNamespace(
            to_dict=lambda: {
                "task_id": selected_task.id,
                "run": run_index,
                "provider": provider,
                "model": model,
                "arms": {},
            }
        )

    monkeypatch.setattr(bench, "resolve_pair_model", fake_resolve)
    monkeypatch.setattr(bench, "run_pair", fake_run_pair)

    summary = bench.run_benchmark([task], "ollama", None, 3, {"base_url": "local"})

    assert len(resolve_calls) == 1
    assert pair_models == ["discovered-model"] * 3
    assert summary["model"] == "discovered-model"


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


@pytest.mark.parametrize(
    ("error", "category", "returncode"),
    [
        (FileNotFoundError("python"), "missing_executable", 127),
        (subprocess.TimeoutExpired(["python"], 1), "timeout", 124),
        (OSError("launch failed"), "runtime_launch_failure", 126),
        (UnicodeError("decode failed"), "runtime_launch_failure", 126),
    ],
)
def test_command_result_exposes_infrastructure_failures(
    monkeypatch, tmp_path, error, category, returncode
):
    monkeypatch.setattr(
        bench.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(error)
    )

    result = bench._run_command(
        ("python", "-V"),
        cwd=tmp_path,
        env={},
        timeout_s=1,
    )

    assert result.returncode == returncode
    assert result.infra_error.startswith(category)
    assert result.to_dict()["infra_error"] == result.infra_error


@pytest.mark.parametrize("failed_check", ["public_test", "hidden_check"])
def test_classify_outcome_prioritizes_public_and_hidden_command_infra(failed_check):
    arm = {
        "completed": True,
        "attempts": [],
        "public_test": {"passed": True, "infra_error": None},
        "hidden_check": {"passed": True, "infra_error": None},
    }
    arm[failed_check]["infra_error"] = "timeout: check exceeded 1s"

    assert bench.classify_outcome(arm, "bare") == "infra_error"


def test_typescript_runtime_preflight_failure_becomes_public_and_hidden_infra(
    monkeypatch, tmp_path
):
    task = bench_tasks.load_tasks()["ts-api-compat-export"]
    monkeypatch.setattr(
        bench,
        "_require_supported_node",
        lambda: (_ for _ in ()).throw(FileNotFoundError("node")),
    )

    public, hidden = bench._evaluate_workspace(task, tmp_path, {})

    assert public.infra_error.startswith("missing_executable")
    assert hidden.infra_error == public.infra_error


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
                "--no-session-persistence",
                "--out",
                str(tmp_path / "state.json"),
            ]
        )

    assert exc.value.code == 0
    acceptance = captured["state"]["steps"][-1]
    assert acceptance["checks"] == ["git diff --check"]
    assert captured["cfg"]["checks"] == ["python -m pytest -q"]
    assert captured["cfg"]["claude_no_session_persistence"] is True
