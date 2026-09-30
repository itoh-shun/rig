"""Regression coverage for run-scoped output and interrupted-worktree recovery."""

import hashlib
import json
import subprocess

import pytest

from rig_workbench.orchestrate import isolate, providers
from rig_workbench.orchestrate.runstate import new_state


@pytest.mark.parametrize("override", [False, True])
def test_step_outputs_preserve_two_runs_in_the_same_output_directory(
    tmp_path, monkeypatch, step_factory, override,
):
    output_root = tmp_path / "step-outputs"
    if override:
        output_root = tmp_path / "custom-outputs"
        monkeypatch.setenv("RIG_STEP_OUTPUT_DIR", str(output_root))
    else:
        monkeypatch.delenv("RIG_STEP_OUTPUT_DIR", raising=False)
    outputs = iter(["first deliverable", "second deliverable"])
    monkeypatch.setattr(providers, "run_provider", lambda *a, **kw: (0, next(outputs)))
    states = []
    state_path = tmp_path / "run-state.json"
    for _ in range(2):
        state = new_state("writing", [step_factory(id="write")], "write a draft")
        assert providers.run_loop(
            state, state_path, "writer", "reviewer", {}, 10, quiet=True,
        ) == "DONE"
        states.append(state)

    for state, expected in zip(states, ["first deliverable", "second deliverable"], strict=True):
        output = output_root / state["run_id"] / "write-writer.txt"
        assert output.read_text(encoding="utf-8") == expected
        assert state["result_artifact"]["path"] == str(output)
        assert providers.read_result_artifact(state, state_path) == expected
    assert not (output_root / "write-writer.txt").exists()


@pytest.mark.parametrize("has_run_id", [False, True])
def test_result_reader_accepts_legacy_step_outputs(tmp_path, monkeypatch, has_run_id):
    monkeypatch.delenv("RIG_STEP_OUTPUT_DIR", raising=False)
    legacy = tmp_path / "step-outputs" / "write-writer.txt"
    legacy.parent.mkdir()
    legacy.write_text("old deliverable", encoding="utf-8")
    state = {"result_artifact": {
        "path": str(legacy), "sha256": hashlib.sha256(legacy.read_bytes()).hexdigest(),
    }}
    if has_run_id:
        state["run_id"] = "old-run"
    assert providers.read_result_artifact(state, tmp_path / "state.json") == "old deliverable"


def test_artifact_reader_rejects_another_runs_output(tmp_path, monkeypatch):
    monkeypatch.delenv("RIG_STEP_OUTPUT_DIR", raising=False)
    cfg = {"run_dir": str(tmp_path), "_progress_run_id": "first-run"}
    providers._capture_output("first deliverable", cfg, "write-writer")
    artifact = providers._artifact_record(cfg, "write-writer", provider="writer", model=None)
    assert artifact is not None
    assert providers._read_artifact(artifact, {**cfg, "_progress_run_id": "second-run"}) is None


@pytest.fixture
def worktree(tmp_path):
    """A real disposable Git worktree, entirely below the test's temporary root."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run([
        "git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com",
        "commit", "-qm", "initial", "--allow-empty",
    ], check=True)
    tree = tmp_path / "worktree"
    subprocess.run(["git", "-C", str(root), "worktree", "add", "-qb", "test-run", str(tree)], check=True)
    return tree


@pytest.mark.parametrize("exit_status", [-9, -15])
def test_interrupted_generator_reports_uncommitted_worktree(
    worktree, tmp_path, monkeypatch, step_factory, capsys, exit_status,
):
    (worktree / "draft.txt").write_text("unfinished work", encoding="utf-8")
    (worktree / "notes.txt").write_text("investigation", encoding="utf-8")
    monkeypatch.setattr(providers, "run_provider", lambda *a, **kw: (exit_status, "interrupted"))
    state = new_state("writing", [step_factory(id="write")], "write")
    state["isolation"] = {"dir": str(worktree)}
    state_path = tmp_path / "state.json"

    assert providers.run_loop(state, state_path, "writer", "reviewer", {}, 10) == "BLOCKED"

    reason = state["stopped"]["reason"]
    assert reason.splitlines()[0] == f"generator failed (exit {exit_status})"
    note = f"Worktree has uncommitted changes in 2 path(s): {worktree}; inspect it before discarding."
    assert reason.splitlines()[1:] == [note]
    assert capsys.readouterr().out.count(note) == 1
    assert json.loads(state_path.read_text())["stopped"]["reason"] == reason


@pytest.mark.parametrize("scenario", ["clean", "missing", "no-isolation", "ordinary-error", "git-failure"])
def test_generator_failure_omits_uncommitted_note_when_not_applicable(
    worktree, tmp_path, monkeypatch, step_factory, scenario,
):
    if scenario != "clean":
        (worktree / "draft.txt").write_text("unfinished work", encoding="utf-8")
    rc = 1 if scenario == "ordinary-error" else -9
    monkeypatch.setattr(providers, "run_provider", lambda *a, **kw: (rc, "failed"))
    state = new_state("writing", [step_factory(id="write")], "write")
    if scenario != "no-isolation":
        state["isolation"] = {
            "dir": str(tmp_path / "absent") if scenario == "missing" else str(worktree),
        }
    if scenario == "git-failure":
        monkeypatch.setenv("PATH", "")

    assert providers.run_loop(
        state, tmp_path / "state.json", "writer", "reviewer", {"cwd": str(worktree)}, 10, quiet=True,
    ) == "BLOCKED"
    assert state["stopped"]["reason"] == f"generator failed (exit {rc})"


@pytest.mark.parametrize("failure", ["nonzero", "exception"])
def test_worktree_note_ignores_git_failures(tmp_path, failure):
    class FailingGit:
        def run(self, args, **kwargs):
            assert args == ["git", "-C", str(tmp_path), "status", "--porcelain"]
            if failure == "exception":
                raise subprocess.TimeoutExpired(args, kwargs["timeout"])
            return subprocess.CompletedProcess(args, 128, "?? misleading.txt\n", "git failed")

    assert isolate.uncommitted_worktree_note(str(tmp_path), proc=FailingGit()) is None
