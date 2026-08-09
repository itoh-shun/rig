"""Durable Mission Control runs use the existing queue and a detached worker."""

import json
import os
from types import SimpleNamespace

import pytest

from rig_workbench import mission_jobs, mission_server, mission_ui, mission_worker


def test_run_request_only_allows_known_non_shell_providers():
    spec = mission_jobs.validate_run_request({
        "task": "fix login",
        "provider": "rig",
        "verifier_provider": "codex",
        "max_parallel": 3,
    })
    assert spec["provider"] == "rig"
    assert spec["verifier_provider"] == "codex"
    assert spec["max_parallel"] == 3
    with pytest.raises(ValueError, match="unsupported provider"):
        mission_jobs.validate_run_request({"task": "x", "provider": "cmd"})
    with pytest.raises(ValueError, match="max_parallel"):
        mission_jobs.validate_run_request({"task": "x", "max_parallel": 99})


def test_corrupt_queue_is_visible_and_never_treated_as_empty(tmp_path):
    path = tmp_path / ".rig" / "queue.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    items, error = mission_jobs.queue_items(tmp_path)
    assert items == []
    assert error and "unreadable queue" in error


def _write_live_worker(tmp_path, **overrides):
    path = mission_jobs.worker_state_path(tmp_path)
    state = {
        "schema": mission_jobs.WORKER_SCHEMA,
        "generation": "g1",
        "status": "running",
        "started_at": "2026-08-09T10:00:00+09:00",
        "provider": "rig",
        "verifier_provider": "codex",
        "max_parallel": 2,
        "pid": os.getpid(),
    }
    state.update(overrides)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def test_active_worker_rejects_silent_provider_switch(tmp_path):
    _write_live_worker(tmp_path)
    with pytest.raises(ValueError, match="already active"):
        mission_jobs.assert_worker_compatible(
            tmp_path,
            provider="claude",
            verifier_provider="codex",
            max_parallel=2,
        )
    current = mission_jobs.assert_worker_compatible(
        tmp_path,
        provider="rig",
        verifier_provider="codex",
        max_parallel=2,
    )
    assert current["alive"] is True


def test_ensure_worker_reuses_compatible_live_worker_without_spawning(tmp_path, monkeypatch):
    _write_live_worker(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("Popen must not be called for a live compatible worker")

    monkeypatch.setattr(mission_jobs.subprocess, "Popen", forbidden)
    result = mission_jobs.ensure_worker(
        tmp_path,
        provider="rig",
        verifier_provider="codex",
        max_parallel=2,
    )
    assert result["started"] is False


def test_start_durable_run_checks_worker_config_before_queue_add(tmp_path, monkeypatch):
    calls = []

    def reject(*args, **kwargs):
        raise ValueError("worker mismatch")

    monkeypatch.setattr(mission_server, "assert_worker_compatible", reject)
    monkeypatch.setattr(mission_server, "_run_cli", lambda *a, **k: calls.append((a, k)))
    with pytest.raises(ValueError, match="worker mismatch"):
        mission_server.start_durable_run(tmp_path, {
            "task": "do work",
            "provider": "rig",
            "verifier_provider": "codex",
            "max_parallel": 1,
        })
    assert calls == [], "incompatible work must not be persisted before refusal"


def test_worker_drains_queue_again_for_items_added_during_previous_batch(tmp_path, monkeypatch):
    queued = iter([(1, None), (1, None), (0, None)])
    updates = []
    runs = []

    monkeypatch.setattr(mission_worker, "wait_for_worker_registration", lambda *a, **k: None)
    monkeypatch.setattr(mission_worker, "_queued", lambda root: next(queued))
    monkeypatch.setattr(mission_worker, "update_worker_state",
                        lambda *a, **k: updates.append(k) or True)
    monkeypatch.setattr(mission_worker.time, "sleep", lambda _: None)

    def fake_run(command, **kwargs):
        runs.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(mission_worker.subprocess, "run", fake_run)
    rc = mission_worker.run_worker(
        tmp_path,
        provider="rig",
        verifier_provider="codex",
        max_parallel=2,
        generation="g1",
    )
    assert rc == 0
    assert len(runs) == 2
    assert all("queue" in command and "go" in command for command in runs)
    assert updates[-1]["status"] == "completed"
    assert updates[-1]["cycles"] == 2


def test_ui_exposes_durable_start_and_queue_without_force_bypass():
    page = mission_ui.interactive_html("csrf")
    assert "Autonomous AI Run" in page
    assert "Start AI Run" in page
    assert "AI Queue" in page
    assert "Closing this page does not stop the detached worker" in page
    assert "--force" not in page
