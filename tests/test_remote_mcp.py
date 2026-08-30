from __future__ import annotations

import asyncio
import inspect
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest

from rig_workbench import chatgpt_mcp, remote_mcp
from rig_workbench.remote_mcp import RigGateway, RigMcpError


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".rig").mkdir()
    return repo.resolve()


def _run(awaitable):
    return asyncio.run(awaitable)


def _patch_run_command(
    monkeypatch,
    *,
    exit_code: int = 0,
    stdout: str = "ok",
    stderr: str = "",
):
    seen: dict[str, object] = {}

    async def fake_run_command(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return {"exit_code": exit_code, "stdout": stdout, "stderr": stderr}

    monkeypatch.setattr(remote_mcp, "_run_command", fake_run_command)
    return seen


def test_gateway_binds_isolated_module_command_and_sanitized_env_to_exact_repo(
    tmp_path,
    monkeypatch,
):
    repo = _repo(tmp_path)
    (repo / "rig_workbench").mkdir()
    (repo / "rig_workbench" / "cli.py").write_text("raise SystemExit('shadowed')")
    monkeypatch.setenv("GIT_DIR", "/tmp/hostile-git-dir")
    monkeypatch.setenv("GIT_WORK_TREE", "/tmp/hostile-git-work-tree")
    monkeypatch.setenv("GIT_COMMON_DIR", "/tmp/hostile-git-common-dir")
    monkeypatch.setenv("GIT_INDEX_FILE", "/tmp/hostile-git-index")
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", "/tmp/hostile-git-objects")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.bare")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/tmp/hostile-git-config")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Preserved Operator Identity")
    monkeypatch.setenv("RIG_ACTOR", "conflicting-inherited-actor")
    monkeypatch.setenv("RIG_ALLOW_PROJECT_RECIPES", "1")
    monkeypatch.setenv("RIG_ALLOW_PROJECT_PACKS", "1")
    monkeypatch.setenv("RIG_ALLOW_PROJECT_MANIFEST", "1")
    monkeypatch.setenv("RIG_ALLOW_PROJECT_PERSONAS", "1")
    gateway = RigGateway.create(repo, operator_id="operator@example.test")
    seen = _patch_run_command(monkeypatch)

    result = _run(gateway.status("task-123"))

    kwargs = seen["kwargs"]
    assert result["ok"] is True
    assert kwargs["cwd"] == repo
    assert seen["command"][:4] == [sys.executable, "-I", "-m", "rig_workbench.cli"]
    assert seen["command"][-3:] == ["wb", "status", "task-123"]
    assert kwargs["stdin_payload"] is None
    assert kwargs["env"]["RIG_INVOKER"] == "rig-mcp/v1"
    assert kwargs["env"]["RIG_ACTOR"] == "operator@example.test"
    assert kwargs["env"]["RIG_USER"] == "operator@example.test"
    assert "GIT_DIR" not in kwargs["env"]
    assert "GIT_WORK_TREE" not in kwargs["env"]
    assert "GIT_COMMON_DIR" not in kwargs["env"]
    assert "GIT_INDEX_FILE" not in kwargs["env"]
    assert "GIT_OBJECT_DIRECTORY" not in kwargs["env"]
    assert "GIT_CONFIG_COUNT" not in kwargs["env"]
    assert "GIT_CONFIG_KEY_0" not in kwargs["env"]
    assert "GIT_CONFIG_VALUE_0" not in kwargs["env"]
    assert "GIT_CONFIG_GLOBAL" not in kwargs["env"]
    assert kwargs["env"]["GIT_AUTHOR_NAME"] == "Preserved Operator Identity"
    assert not any(name.startswith("RIG_ALLOW_PROJECT_") for name in kwargs["env"])


def test_stdio_gateway_can_inherit_existing_rig_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("RIG_USER", "local-stdio-user")
    gateway = RigGateway.create(_repo(tmp_path))
    seen = _patch_run_command(monkeypatch)

    _run(gateway.status())

    assert seen["kwargs"]["env"]["RIG_USER"] == "local-stdio-user"


def test_gateway_rejects_nested_pseudo_root(tmp_path):
    repo = _repo(tmp_path)
    nested = repo / "nested"
    nested.mkdir()
    (nested / ".rig").mkdir()

    with pytest.raises(RigMcpError, match="exactly the Git top-level"):
        RigGateway.create(nested)


def test_gateway_requires_initialized_rig_directory(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)

    with pytest.raises(RigMcpError, match="initialized .rig"):
        RigGateway.create(repo)


@pytest.mark.parametrize("value", ["--help", "../other", ".", "...", "a" * 129])
def test_identifiers_reject_flags_paths_dots_and_overlong_values(tmp_path, value):
    gateway = RigGateway.create(_repo(tmp_path))

    with pytest.raises(RigMcpError, match="invalid recipe"):
        _run(gateway.plan(value))


def test_optional_identifiers_reject_explicit_empty_strings(tmp_path):
    gateway = RigGateway.create(_repo(tmp_path))

    with pytest.raises(RigMcpError, match="invalid task_id"):
        _run(gateway.status(""))
    with pytest.raises(RigMcpError, match="invalid task_id"):
        _run(gateway.diff(""))


def test_operator_id_is_bounded_and_printable(tmp_path):
    repo = _repo(tmp_path)

    with pytest.raises(RigMcpError, match="operator_id must be printable"):
        RigGateway.create(repo, operator_id="bad\nidentity")
    with pytest.raises(RigMcpError, match="operator_id must be printable"):
        RigGateway.create(repo, operator_id="x" * 257)


def test_mutating_tools_are_guarded_by_gateway(tmp_path):
    gateway = RigGateway.create(_repo(tmp_path))

    with pytest.raises(RigMcpError, match="write actions are disabled"):
        _run(gateway.run("bugfix", provider="mock"))
    with pytest.raises(RigMcpError, match="write actions are disabled"):
        _run(gateway.accept("task-123"))


def test_run_always_isolates_and_sends_bounded_goal_on_stdin(tmp_path, monkeypatch):
    gateway = RigGateway.create(_repo(tmp_path), allow_write=True)
    seen = _patch_run_command(monkeypatch)

    _run(gateway.run("bugfix", provider="mock", goal="private goal"))

    assert "--isolate" in seen["command"]
    assert "--goal-stdin" in seen["command"]
    assert "private goal" not in seen["command"]
    assert seen["kwargs"]["stdin_payload"] == b"private goal"
    assert "isolate" not in inspect.signature(gateway.run).parameters


def test_goal_rejects_empty_and_over_one_mibibyte(tmp_path):
    gateway = RigGateway.create(_repo(tmp_path), allow_write=True)

    with pytest.raises(RigMcpError, match="must not be empty"):
        _run(gateway.run("bugfix", goal=""))
    with pytest.raises(RigMcpError, match="1048576-byte limit"):
        _run(gateway.run("bugfix", goal="x" * (1024 * 1024 + 1)))


def test_accept_requires_task_and_never_adds_force(tmp_path, monkeypatch):
    gateway = RigGateway.create(_repo(tmp_path), allow_write=True)
    seen = _patch_run_command(monkeypatch)

    _run(gateway.accept("task-123"))

    assert seen["command"][-3:] == ["wb", "accept", "task-123"]
    assert "--force" not in seen["command"]
    task_parameter = inspect.signature(gateway.accept).parameters["task_id"]
    assert task_parameter.default is inspect.Parameter.empty


def test_discard_requires_explicit_confirmation(tmp_path):
    gateway = RigGateway.create(_repo(tmp_path), allow_write=True)

    with pytest.raises(RigMcpError, match="confirm=true"):
        _run(gateway.discard("task-123"))


def test_nonzero_exit_is_an_adapter_error(tmp_path, monkeypatch):
    gateway = RigGateway.create(_repo(tmp_path))
    _patch_run_command(monkeypatch, exit_code=2, stderr="bad request")

    with pytest.raises(RigMcpError, match="exit code 2: bad request"):
        _run(gateway.status())


def test_mutating_calls_are_serialized_per_gateway(tmp_path, monkeypatch):
    gateway = RigGateway.create(_repo(tmp_path), allow_write=True)
    active = 0
    maximum_active = 0

    async def delayed_run(*args, **kwargs):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.05)
        active -= 1
        return {"exit_code": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(remote_mcp, "_run_command", delayed_run)

    async def exercise():
        await asyncio.gather(gateway.run("bugfix"), gateway.accept("task-123"))

    _run(exercise())

    assert maximum_active == 1


def test_timeout_terminates_command(tmp_path):
    async def exercise():
        with pytest.raises(RigMcpError, match="timed out after 1s"):
            await remote_mcp._run_command(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                cwd=tmp_path,
                env=os.environ.copy(),
                stdin_payload=None,
                timeout_seconds=1,
            )

    _run(exercise())


@pytest.mark.skipif(os.name != "posix", reason="process-group timeout requires POSIX")
def test_timeout_covers_descendant_held_output_pipes(tmp_path):
    pid_file = tmp_path / "descendant-pid"
    parent_code = (
        "import pathlib, subprocess, sys; "
        "child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid))"
    )

    async def exercise():
        with pytest.raises(RigMcpError, match="timed out after 1s"):
            await remote_mcp._run_command(
                [sys.executable, "-c", parent_code, str(pid_file)],
                cwd=tmp_path,
                env=os.environ.copy(),
                stdin_payload=None,
                timeout_seconds=1,
            )
        child_pid = int(pid_file.read_text())
        for _ in range(100):
            if not _pid_is_running(child_pid):
                break
            await asyncio.sleep(0.02)
        assert not _pid_is_running(child_pid)

    _run(exercise())


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat_path = Path(f"/proc/{pid}/stat")
    if stat_path.exists() and stat_path.read_text().split()[2] == "Z":
        return False
    return True


@pytest.mark.skipif(os.name != "posix", reason="process-group cancellation requires POSIX")
def test_cancellation_terminates_parent_and_complete_process_group(tmp_path):
    pid_file = tmp_path / "pids"
    # The pids are published by rename, not by writing in place. The poller below waits on
    # the file existing, and `write_text` creates it empty before it writes — so an in-place
    # write lets the read land on a truncated file and fail with an unpacking error that
    # says nothing about cancellation, which is what this test is for. `os.replace` is
    # atomic within a filesystem, so the file exists only once it is complete.
    parent_code = (
        "import os, pathlib, subprocess, sys, time; "
        "child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "path=pathlib.Path(sys.argv[1]); staged=path.with_name(path.name + '.partial'); "
        "staged.write_text(f'{os.getpid()} {child.pid}'); "
        "os.replace(staged, path); "
        "time.sleep(60)"
    )

    async def exercise():
        task = asyncio.create_task(
            remote_mcp._run_command(
                [sys.executable, "-c", parent_code, str(pid_file)],
                cwd=tmp_path,
                env=os.environ.copy(),
                stdin_payload=None,
                timeout_seconds=60,
            )
        )
        for _ in range(100):
            if pid_file.exists():
                break
            await asyncio.sleep(0.02)
        assert pid_file.exists()
        parent_pid, child_pid = map(int, pid_file.read_text().split())

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        for _ in range(100):
            if not _pid_is_running(parent_pid) and not _pid_is_running(child_pid):
                break
            await asyncio.sleep(0.02)
        assert not _pid_is_running(parent_pid)
        assert not _pid_is_running(child_pid)
        assert not remote_mcp._process_group_exists(parent_pid)

    try:
        _run(exercise())
    finally:
        if pid_file.exists():
            for pid in map(int, pid_file.read_text().split()):
                if _pid_is_running(pid):
                    os.kill(pid, signal.SIGKILL)


def test_output_pipes_are_drained_but_only_byte_caps_are_retained(tmp_path):
    amount = max(remote_mcp._MAX_OUTPUT_BYTES, remote_mcp._MAX_ERROR_BYTES) * 4
    code = f"import os; os.write(1, b'o'*{amount}); os.write(2, b'e'*{amount})"

    result = _run(
        remote_mcp._run_command(
            [sys.executable, "-c", code],
            cwd=tmp_path,
            env=os.environ.copy(),
            stdin_payload=None,
            timeout_seconds=10,
        )
    )

    assert len(result["stdout"].encode()) <= remote_mcp._MAX_OUTPUT_BYTES
    assert len(result["stderr"].encode()) <= remote_mcp._MAX_ERROR_BYTES
    assert result["stdout"].endswith("...[truncated by Rig MCP adapter]")
    assert result["stderr"].endswith("...[truncated by Rig MCP adapter]")


@pytest.mark.parametrize("host", ["0.0.0.0", "192.0.2.10", "mcp.example.com"])
def test_http_rejects_every_non_loopback_host(host):
    with pytest.raises(SystemExit):
        remote_mcp.main(
            ["--transport", "streamable-http", "--allow-unauthenticated-http", "--host", host]
        )


def test_invalid_http_host_is_a_concise_cli_error(capsys):
    with pytest.raises(SystemExit) as raised:
        remote_mcp.main(
            [
                "--transport",
                "streamable-http",
                "--allow-unauthenticated-http",
                "--host",
                "0.0.0.0",
            ]
        )

    assert raised.value.code == 2
    stderr = capsys.readouterr().err
    assert "error: --host must be loopback" in stderr
    assert "Traceback" not in stderr


def test_http_requires_explicit_unauthenticated_acknowledgement(tmp_path, capsys):
    pytest.importorskip("mcp", reason="optional MCP SDK is tested in CI with the mcp extra")

    with pytest.raises(SystemExit) as raised:
        remote_mcp.main(["--repo", str(_repo(tmp_path)), "--transport", "streamable-http"])

    assert raised.value.code == 2
    stderr = capsys.readouterr().err
    assert "--allow-unauthenticated-http" in stderr
    assert "Traceback" not in stderr


def test_write_enabled_http_requires_explicit_operator(tmp_path, capsys):
    pytest.importorskip("mcp", reason="optional MCP SDK is tested in CI with the mcp extra")
    repo = _repo(tmp_path)

    with pytest.raises(SystemExit) as raised:
        remote_mcp.main(
            [
                "--repo",
                str(repo),
                "--transport",
                "streamable-http",
                "--allow-unauthenticated-http",
                "--allow-write",
            ]
        )

    assert raised.value.code == 2
    stderr = capsys.readouterr().err
    assert "write-enabled HTTP requires a nonempty --operator-id" in stderr
    assert "Traceback" not in stderr


def test_repository_startup_failure_is_a_concise_cli_error(monkeypatch, capsys):
    def fail_create(*args, **kwargs):
        raise RigMcpError("repository startup failed")

    monkeypatch.setattr(remote_mcp.RigGateway, "create", fail_create)

    with pytest.raises(SystemExit) as raised:
        remote_mcp.main(["--repo", "/bad", "--transport", "stdio"])

    assert raised.value.code == 2
    stderr = capsys.readouterr().err
    assert "error: repository startup failed" in stderr
    assert "Traceback" not in stderr


def test_main_defaults_to_stdio_and_passes_constructor_options(monkeypatch):
    gateway = object()
    calls = {}
    server = SimpleNamespace(run=lambda **kwargs: calls.setdefault("run", kwargs))
    monkeypatch.setattr(remote_mcp.RigGateway, "create", lambda *args, **kwargs: gateway)

    def fake_create_server(actual_gateway, **kwargs):
        calls["gateway"] = actual_gateway
        calls["constructor"] = kwargs
        return server

    monkeypatch.setattr(remote_mcp, "create_server", fake_create_server)

    remote_mcp.main(["--repo", "/ignored", "--host", "::1", "--port", "8123"])

    assert calls["gateway"] is gateway
    assert calls["constructor"] == {
        "transport": "stdio",
        "host": "::1",
        "port": 8123,
        "allow_unauthenticated_http": False,
    }
    assert calls["run"] == {"transport": "stdio"}


def test_stdio_ignores_non_loopback_host_and_http_ack(monkeypatch):
    calls = {}
    gateway = object()
    server = SimpleNamespace(run=lambda **kwargs: calls.setdefault("run", kwargs))
    monkeypatch.setattr(remote_mcp.RigGateway, "create", lambda *args, **kwargs: gateway)

    def fake_create_server(actual_gateway, **kwargs):
        calls["constructor"] = kwargs
        return server

    monkeypatch.setattr(remote_mcp, "create_server", fake_create_server)

    remote_mcp.main(
        [
            "--repo",
            "/ignored",
            "--transport",
            "stdio",
            "--host",
            "0.0.0.0",
            "--allow-unauthenticated-http",
            "--operator-id",
            "ignored-http-user",
        ]
    )

    assert calls["constructor"]["transport"] == "stdio"
    assert calls["constructor"]["host"] == "0.0.0.0"
    assert calls["run"] == {"transport": "stdio"}


def test_http_cli_passes_ack_and_operator_to_gateway_and_server(monkeypatch):
    calls = {}
    gateway = SimpleNamespace(allow_write=True, operator_id="alice")
    server = SimpleNamespace(run=lambda **kwargs: calls.setdefault("run", kwargs))

    def fake_gateway_create(*args, **kwargs):
        calls["gateway"] = kwargs
        return gateway

    def fake_create_server(actual_gateway, **kwargs):
        calls["server"] = kwargs
        return server

    monkeypatch.setattr(remote_mcp.RigGateway, "create", fake_gateway_create)
    monkeypatch.setattr(remote_mcp, "create_server", fake_create_server)

    remote_mcp.main(
        [
            "--repo",
            "/ignored",
            "--transport",
            "streamable-http",
            "--allow-unauthenticated-http",
            "--allow-write",
            "--operator-id",
            "alice",
        ]
    )

    assert calls["gateway"]["operator_id"] == "alice"
    assert calls["server"]["allow_unauthenticated_http"] is True
    assert calls["run"] == {"transport": "streamable-http"}


def test_real_stdio_server_ignores_http_only_host_port_settings(tmp_path):
    pytest.importorskip("mcp", reason="optional MCP SDK is tested in CI with the mcp extra")
    gateway = RigGateway.create(_repo(tmp_path))

    server = remote_mcp.create_server(
        gateway,
        transport="stdio",
        host="0.0.0.0",
        port=0,
    )

    assert server.settings.host == "127.0.0.1"
    assert server.settings.port == 8000
    assert server.settings.transport_security.enable_dns_rebinding_protection is True


def test_real_http_server_uses_constructor_security_after_explicit_ack(tmp_path):
    pytest.importorskip("mcp", reason="optional MCP SDK is tested in CI with the mcp extra")
    gateway = RigGateway.create(_repo(tmp_path))

    server = remote_mcp.create_server(
        gateway,
        transport="streamable-http",
        host="::1",
        port=8123,
        allow_unauthenticated_http=True,
    )

    assert server.settings.host == "::1"
    assert server.settings.port == 8123
    assert server.settings.transport_security.enable_dns_rebinding_protection is True


def test_chatgpt_module_is_a_silent_compatibility_shim():
    assert chatgpt_mcp.RigGateway is remote_mcp.RigGateway
    assert chatgpt_mcp.RigMcpError is remote_mcp.RigMcpError
    assert chatgpt_mcp.create_server is remote_mcp.create_server
    assert chatgpt_mcp.main is remote_mcp.main


def test_real_sdk_lists_conditional_tools_annotations_and_reports_mcp_errors(tmp_path):
    pytest.importorskip("mcp", reason="optional MCP SDK is tested in CI with the mcp extra")
    import anyio
    from mcp import ClientSession

    repo = _repo(tmp_path)

    async def inspect_server(allow_write: bool):
        gateway = RigGateway.create(repo, allow_write=allow_write)
        server = remote_mcp.create_server(gateway, transport="stdio")
        client_send, server_receive = anyio.create_memory_object_stream(0)
        server_send, client_receive = anyio.create_memory_object_stream(0)
        async with client_send, server_receive, server_send, client_receive:
            async with anyio.create_task_group() as task_group:

                async def run_server():
                    await server._mcp_server.run(
                        server_receive,
                        server_send,
                        server._mcp_server.create_initialization_options(),
                    )

                task_group.start_soon(run_server)
                async with ClientSession(client_receive, client_send) as session:
                    initialization = await session.initialize()
                    tools = (await session.list_tools()).tools
                    error = await session.call_tool("rig_plan", {"recipe": "--help"})
                task_group.cancel_scope.cancel()
        return tools, error, initialization.serverInfo

    read_tools, read_error, read_server_info = anyio.run(inspect_server, False)
    write_tools, write_error, write_server_info = anyio.run(inspect_server, True)

    assert {tool.name for tool in read_tools} == {
        "rig_status",
        "rig_board",
        "rig_diff",
        "rig_plan",
    }
    assert {tool.name for tool in write_tools} == {
        "rig_status",
        "rig_board",
        "rig_diff",
        "rig_plan",
        "rig_run",
        "rig_accept",
        "rig_discard",
    }
    read_annotation = next(tool.annotations for tool in read_tools if tool.name == "rig_plan")
    run_annotation = next(tool.annotations for tool in write_tools if tool.name == "rig_run")
    accept_annotation = next(tool.annotations for tool in write_tools if tool.name == "rig_accept")
    assert read_annotation.model_dump() == {
        "title": None,
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    assert run_annotation.readOnlyHint is False
    assert run_annotation.destructiveHint is True
    assert run_annotation.idempotentHint is False
    assert run_annotation.openWorldHint is True
    assert accept_annotation.readOnlyHint is False
    assert accept_annotation.destructiveHint is True
    assert accept_annotation.idempotentHint is False
    assert accept_annotation.openWorldHint is False
    assert read_error.isError is True
    assert write_error.isError is True
    assert read_server_info.name == "rig-workbench"
    assert read_server_info.version == remote_mcp.__version__
    assert write_server_info.name == "rig-workbench"
    assert write_server_info.version == remote_mcp.__version__
