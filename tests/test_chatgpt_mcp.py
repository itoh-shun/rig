from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from rig_workbench.chatgpt_mcp import RigGateway, RigMcpError


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


def test_gateway_binds_commands_to_configured_repo(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["cwd"] = kwargs["cwd"]
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr("rig_workbench.chatgpt_mcp.subprocess.run", fake_run)
    gateway = RigGateway.create(repo)

    result = gateway.status("task-123")

    assert result["ok"] is True
    assert seen["cwd"] == repo
    assert seen["command"][-3:] == ["wb", "status", "task-123"]


def test_mutating_tools_are_read_only_by_default(tmp_path):
    gateway = RigGateway.create(_repo(tmp_path))

    with pytest.raises(RigMcpError, match="write actions are disabled"):
        gateway.run("bugfix", provider="mock")

    with pytest.raises(RigMcpError, match="write actions are disabled"):
        gateway.accept("task-123")


def test_discard_requires_explicit_confirmation(tmp_path):
    gateway = RigGateway.create(_repo(tmp_path), allow_write=True)

    with pytest.raises(RigMcpError, match="confirm=true"):
        gateway.discard("task-123")


def test_accept_has_no_force_argument_and_uses_canonical_cli(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        return SimpleNamespace(returncode=0, stdout="accepted\n", stderr="")

    monkeypatch.setattr("rig_workbench.chatgpt_mcp.subprocess.run", fake_run)
    gateway = RigGateway.create(repo, allow_write=True)

    result = gateway.accept("task-123")

    assert result["ok"] is True
    assert seen["command"][-3:] == ["wb", "accept", "task-123"]
    assert "--force" not in seen["command"]


def test_recipe_and_task_identifiers_reject_flag_injection(tmp_path):
    gateway = RigGateway.create(_repo(tmp_path))

    with pytest.raises(RigMcpError, match="invalid recipe"):
        gateway.plan("--help")

    with pytest.raises(RigMcpError, match="invalid task_id"):
        gateway.status("../other-repo")
