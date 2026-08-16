"""Client-neutral remote MCP adapter for Rig's canonical workbench CLI.

The adapter binds one server process to one initialized Rig repository.  Read
tools are always available; mutating tools are registered only when the server
operator explicitly enables them.  Every operation delegates to
``python -I -m rig_workbench.cli`` rather than duplicating Rig's routing, gate,
worktree, or acceptance logic.
"""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path
import re
import signal
import subprocess
import sys
from typing import Any, Awaitable, Callable

from rig_workbench import __version__, exitcodes


_MAX_IDENTIFIER_LENGTH = 128
_MAX_GOAL_BYTES = 1024 * 1024
_MAX_OUTPUT_BYTES = 128 * 1024
_MAX_ERROR_BYTES = 16 * 1024
_MAX_OPERATOR_ID_LENGTH = 256
_TRUNCATION_MARKER = b"\n...[truncated by Rig MCP adapter]"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_GIT_CONFIG_SLOT = re.compile(r"^GIT_CONFIG_(?:KEY|VALUE)_\d+$")
_GIT_REPOSITORY_AND_CONFIG_OVERRIDES = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_ATTR_GLOBAL",
        "GIT_ATTR_NOSYSTEM",
        "GIT_ATTR_SOURCE",
        "GIT_ATTR_SYSTEM",
        "GIT_CEILING_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_SYSTEM",
        "GIT_DIR",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
        "GIT_EXEC_PATH",
        "GIT_GRAFT_FILE",
        "GIT_IMPLICIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_OBJECT_DIRECTORY",
        "GIT_PREFIX",
        "GIT_QUARANTINE_PATH",
        "GIT_REPLACE_REF_BASE",
        "GIT_SHALLOW_FILE",
        "GIT_WORK_TREE",
    }
)


class RigMcpError(RuntimeError):
    """A bounded, user-visible adapter error."""


def _sanitized_environment() -> dict[str, str]:
    """Remove inherited overrides that can redirect Git or grant project trust."""
    env = os.environ.copy()
    for name in tuple(env):
        if (
            name in _GIT_REPOSITORY_AND_CONFIG_OVERRIDES
            or _GIT_CONFIG_SLOT.fullmatch(name)
            or name.startswith("RIG_ALLOW_PROJECT_")
        ):
            env.pop(name)
    return env


@dataclass(frozen=True)
class RigGateway:
    """Thin, repository-bound gateway to the canonical ``rig-wb`` CLI."""

    repo: Path
    allow_write: bool = False
    timeout_seconds: int = 1800
    operator_id: str | None = None
    _mutation_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        compare=False,
        repr=False,
    )

    @classmethod
    def create(
        cls,
        repo: str | os.PathLike[str],
        *,
        allow_write: bool = False,
        timeout_seconds: int = 1800,
        operator_id: str | None = None,
    ) -> "RigGateway":
        root = Path(repo).expanduser().resolve()
        if not root.is_dir():
            raise RigMcpError(f"repository root does not exist: {root}")
        if not (root / ".rig").is_dir():
            raise RigMcpError(f"repository root must contain an initialized .rig directory: {root}")
        if timeout_seconds <= 0:
            raise RigMcpError("timeout must be a positive number of seconds")

        git_env = _sanitized_environment()
        try:
            result = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                timeout=10,
                env=git_env,
                text=True,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RigMcpError(f"cannot resolve repository root: {root}") from exc
        if result.returncode != 0:
            raise RigMcpError(f"repository root is not a Git worktree: {root}")
        try:
            git_root = Path(result.stdout.strip()).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise RigMcpError(f"cannot resolve Git top-level directory: {root}") from exc
        if git_root != root:
            raise RigMcpError(
                f"repository root must be exactly the Git top-level directory: {git_root}"
            )
        normalized_operator = _normalize_operator_id(operator_id)
        return cls(
            root,
            allow_write=allow_write,
            timeout_seconds=timeout_seconds,
            operator_id=normalized_operator,
        )

    @staticmethod
    def _safe_name(value: str, label: str) -> str:
        if (
            not value
            or len(value) > _MAX_IDENTIFIER_LENGTH
            or not _SAFE_NAME.fullmatch(value)
        ):
            raise RigMcpError(f"invalid {label}: {value!r}")
        return value

    @staticmethod
    def _goal_payload(goal: str | None) -> bytes | None:
        if goal is None:
            return None
        try:
            payload = goal.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise RigMcpError("goal must be valid UTF-8") from exc
        if not payload:
            raise RigMcpError("goal must not be empty")
        if len(payload) > _MAX_GOAL_BYTES:
            raise RigMcpError(f"goal exceeds the {_MAX_GOAL_BYTES}-byte limit")
        return payload

    async def _invoke(
        self,
        args: list[str],
        *,
        write: bool = False,
        stdin_payload: bytes | None = None,
    ) -> dict[str, Any]:
        if write and not self.allow_write:
            raise RigMcpError(
                "write actions are disabled for this MCP server; restart with --allow-write"
            )

        command = [sys.executable, "-I", "-m", "rig_workbench.cli", *args]
        env = _sanitized_environment()
        env["RIG_INVOKER"] = "rig-mcp/v1"
        if self.operator_id is not None:
            env["RIG_ACTOR"] = self.operator_id
            env["RIG_USER"] = self.operator_id

        async def invoke_child() -> dict[str, Any]:
            return await _run_command(
                command,
                cwd=self.repo,
                env=env,
                stdin_payload=stdin_payload,
                timeout_seconds=self.timeout_seconds,
            )

        result = (
            await _run_serialized(self._mutation_lock, invoke_child)
            if write
            else await invoke_child()
        )
        if result["exit_code"] != 0:
            stdout = result["stdout"]
            stderr = result["stderr"]
            detail = _bound_text(stderr or stdout or "no command output", _MAX_ERROR_BYTES)
            raise RigMcpError(
                f"Rig command failed with exit code {result['exit_code']}: {detail}"
            )
        return {"ok": True, **result}

    # ---- read-only control plane -----------------------------------------

    async def status(self, task_id: str | None = None) -> dict[str, Any]:
        args = ["wb", "status"]
        if task_id is not None:
            args.append(self._safe_name(task_id, "task_id"))
        return await self._invoke(args)

    async def board(self, include_all: bool = False) -> dict[str, Any]:
        args = ["wb", "board"]
        if include_all:
            args.append("--all")
        return await self._invoke(args)

    async def diff(self, task_id: str | None = None) -> dict[str, Any]:
        args = ["wb", "diff"]
        if task_id is not None:
            args.append(self._safe_name(task_id, "task_id"))
        return await self._invoke(args)

    async def plan(self, recipe: str) -> dict[str, Any]:
        return await self._invoke(["plan", self._safe_name(recipe, "recipe"), "--json"])

    # ---- mutating actions ------------------------------------------------

    async def run(
        self,
        recipe: str,
        *,
        provider: str = "mock",
        verifier_provider: str | None = None,
        goal: str | None = None,
    ) -> dict[str, Any]:
        args = [
            "run",
            self._safe_name(recipe, "recipe"),
            "--provider",
            self._safe_name(provider, "provider"),
        ]
        if verifier_provider is not None:
            args += [
                "--verifier-provider",
                self._safe_name(verifier_provider, "verifier_provider"),
            ]
        payload = self._goal_payload(goal)
        if payload is not None:
            args.append("--goal-stdin")
        args.append("--isolate")
        return await self._invoke(args, write=True, stdin_payload=payload)

    async def accept(self, task_id: str) -> dict[str, Any]:
        """Accept through Rig's canonical gate path; force is intentionally absent."""
        task_id = self._safe_name(task_id, "task_id")
        return await self._invoke(["wb", "accept", task_id], write=True)

    async def discard(self, task_id: str, *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            raise RigMcpError("discard requires confirm=true")
        task_id = self._safe_name(task_id, "task_id")
        return await self._invoke(["wb", "discard", task_id, "--yes"], write=True)


def _normalize_operator_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > _MAX_OPERATOR_ID_LENGTH or not normalized.isprintable():
        raise RigMcpError(
            f"operator_id must be printable and at most {_MAX_OPERATOR_ID_LENGTH} characters"
        )
    return normalized


async def _run_serialized(
    lock: asyncio.Lock,
    operation: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    async with lock:
        return await operation()


async def _drain_bounded(
    reader: asyncio.StreamReader | None,
    limit: int,
) -> tuple[bytes, bool]:
    """Drain a pipe completely while retaining no more than ``limit`` bytes."""
    if reader is None:  # pragma: no cover - PIPE is always requested
        return b"", False
    retained = bytearray()
    truncated = False
    while True:
        chunk = await reader.read(64 * 1024)
        if not chunk:
            break
        remaining = max(0, limit - len(retained))
        if remaining:
            retained.extend(chunk[:remaining])
        if len(chunk) > remaining:
            truncated = True
    return bytes(retained), truncated


async def _write_stdin_and_wait(
    process: asyncio.subprocess.Process,
    payload: bytes | None,
) -> int:
    if process.stdin is not None:
        try:
            if payload is not None:
                process.stdin.write(payload)
                await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()
            try:
                await process.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass
    return await process.wait()


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover - child groups are owned by this process
        return True
    return True


async def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
    """Terminate the child session, escalating until the complete group is gone."""
    process_group_id = process.pid
    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError:
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass

    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while _process_group_exists(process_group_id) and loop.time() < deadline:
        await asyncio.sleep(0.05)

    if _process_group_exists(process_group_id):
        try:
            os.killpg(process_group_id, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
    if process.returncode is None:
        await process.wait()


async def _cleanup_process(
    process: asyncio.subprocess.Process,
    drain_tasks: tuple[asyncio.Task[tuple[bytes, bool]], ...],
) -> None:
    await _terminate_process_group(process)
    await asyncio.gather(*drain_tasks, return_exceptions=True)


async def _cleanup_process_after_cancellation(
    process: asyncio.subprocess.Process,
    drain_tasks: tuple[asyncio.Task[tuple[bytes, bool]], ...],
) -> None:
    cleanup = asyncio.create_task(_cleanup_process(process, drain_tasks))
    try:
        await asyncio.shield(cleanup)
    except asyncio.CancelledError:
        await cleanup


async def _run_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    stdin_payload: bytes | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE if stdin_payload is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        raise RigMcpError(f"cannot start Rig command: {exc}") from None

    drain_tasks = (
        asyncio.create_task(_drain_bounded(process.stdout, _MAX_OUTPUT_BYTES)),
        asyncio.create_task(_drain_bounded(process.stderr, _MAX_ERROR_BYTES)),
    )

    lifecycle = asyncio.gather(
        _write_stdin_and_wait(process, stdin_payload),
        *drain_tasks,
    )

    try:
        exit_code, stdout_capture, stderr_capture = await asyncio.wait_for(
            asyncio.shield(lifecycle), timeout=timeout_seconds
        )
    except asyncio.TimeoutError:
        await _cleanup_process_after_cancellation(process, drain_tasks)
        await asyncio.gather(lifecycle, return_exceptions=True)
        raise RigMcpError(f"Rig command timed out after {timeout_seconds}s") from None
    except asyncio.CancelledError:
        await _cleanup_process_after_cancellation(process, drain_tasks)
        await asyncio.gather(lifecycle, return_exceptions=True)
        raise
    except BaseException:
        await _cleanup_process_after_cancellation(process, drain_tasks)
        await asyncio.gather(lifecycle, return_exceptions=True)
        raise

    return {
        "exit_code": exit_code,
        "stdout": _decode_capture(*stdout_capture, _MAX_OUTPUT_BYTES),
        "stderr": _decode_capture(*stderr_capture, _MAX_ERROR_BYTES),
    }


def _bound_text(value: str, limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    prefix_limit = max(0, limit - len(_TRUNCATION_MARKER))
    prefix = encoded[:prefix_limit].decode("utf-8", errors="ignore")
    return prefix + _TRUNCATION_MARKER.decode("ascii")


def _decode_capture(data: bytes, truncated: bool, limit: int) -> str:
    if truncated:
        prefix_limit = max(0, limit - len(_TRUNCATION_MARKER))
        prefix = data[:prefix_limit].decode("utf-8", errors="ignore").strip()
        return prefix + _TRUNCATION_MARKER.decode("ascii")
    return _bound_text(data.decode("utf-8", errors="replace").strip(), limit)


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _transport_security(host: str):
    try:
        from mcp.server.transport_security import TransportSecuritySettings
    except ImportError as exc:  # pragma: no cover - optional dependency boundary
        raise RigMcpError(
            "MCP support is not installed. Run: pip install 'rig-workbench[mcp]'"
        ) from exc

    if not _is_loopback_host(host):
        raise RigMcpError(
            "HTTP transport may bind only to a loopback host; use an authenticated "
            "HTTPS tunnel or reverse proxy that terminates to loopback"
        )
    rendered_host = f"[{host}]" if ":" in host else host
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[f"{rendered_host}:*"],
        allowed_origins=[f"http://{rendered_host}:*", f"https://{rendered_host}:*"],
    )


async def _tool_call(
    tool_error: type[Exception],
    operation: Callable[..., Awaitable[dict[str, Any]]],
    *args,
    **kwargs,
) -> dict[str, Any]:
    try:
        return await operation(*args, **kwargs)
    except RigMcpError as exc:
        raise tool_error(str(exc)) from None


def create_server(
    gateway: RigGateway,
    *,
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8000,
    allow_unauthenticated_http: bool = False,
):
    """Create a FastMCP server without importing the optional SDK at module import."""
    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.server.fastmcp.exceptions import ToolError
        from mcp.types import ToolAnnotations
    except ImportError as exc:  # pragma: no cover - optional dependency boundary
        raise RigMcpError(
            "MCP support is not installed. Run: pip install 'rig-workbench[mcp]'"
        ) from exc

    server_options: dict[str, Any] = {
        "json_response": True,
        "stateless_http": True,
    }
    if transport == "streamable-http":
        if not allow_unauthenticated_http:
            raise RigMcpError(
                "HTTP transport has no built-in authentication; acknowledge single-operator "
                "deployment with --allow-unauthenticated-http"
            )
        if gateway.allow_write and gateway.operator_id is None:
            raise RigMcpError(
                "write-enabled HTTP requires a nonempty --operator-id (RIG_MCP_OPERATOR_ID)"
            )
        if not 1 <= port <= 65535:
            raise RigMcpError("port must be between 1 and 65535")
        server_options.update(
            host=host,
            port=port,
            transport_security=_transport_security(host),
        )
    elif transport != "stdio":
        raise RigMcpError(f"unsupported transport: {transport}")

    mcp = FastMCP(
        "rig-workbench",
        instructions=(
            "Use Rig as the quality/control plane for the repository bound to this server. "
            "Prefer status, board, diff, and plan before mutating actions. A green gate is "
            "not proof of correctness. This adapter exposes no force-accept operation."
        ),
        **server_options,
    )
    # FastMCP 1.28.1 exposes version only on its low-level Server, not its
    # constructor. Keep protocol serverInfo aligned with this package release.
    mcp._mcp_server.version = __version__

    read_annotations = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )

    @mcp.tool(annotations=read_annotations)
    async def rig_status(task_id: str | None = None) -> dict[str, Any]:
        """Read the current Rig task status. Does not modify the repository."""
        return await _tool_call(ToolError, gateway.status, task_id)

    @mcp.tool(annotations=read_annotations)
    async def rig_board(include_all: bool = False) -> dict[str, Any]:
        """Read the Rig task board. Does not modify the repository."""
        return await _tool_call(ToolError, gateway.board, include_all)

    @mcp.tool(annotations=read_annotations)
    async def rig_diff(task_id: str | None = None) -> dict[str, Any]:
        """Read the structured diff for a Rig task. Does not modify the repository."""
        return await _tool_call(ToolError, gateway.diff, task_id)

    @mcp.tool(annotations=read_annotations)
    async def rig_plan(recipe: str) -> dict[str, Any]:
        """Resolve a recipe into Rig's deterministic JSON execution plan."""
        return await _tool_call(ToolError, gateway.plan, recipe)

    if gateway.allow_write:
        run_annotations = ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=True,
        )
        destructive_annotations = ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        )

        @mcp.tool(annotations=run_annotations)
        async def rig_run(
            recipe: str,
            provider: str = "mock",
            verifier_provider: str | None = None,
            goal: str | None = None,
        ) -> dict[str, Any]:
            """Run an isolated Rig recipe. This tool exists only on write-enabled servers."""
            return await _tool_call(
                ToolError,
                gateway.run,
                recipe,
                provider=provider,
                verifier_provider=verifier_provider,
                goal=goal,
            )

        @mcp.tool(annotations=destructive_annotations)
        async def rig_accept(task_id: str) -> dict[str, Any]:
            """Accept one gated task as staged changes; force is unavailable."""
            return await _tool_call(ToolError, gateway.accept, task_id)

        @mcp.tool(annotations=destructive_annotations)
        async def rig_discard(task_id: str, confirm: bool = False) -> dict[str, Any]:
            """Discard one task worktree/branch; requires confirm=true."""
            return await _tool_call(ToolError, gateway.discard, task_id, confirm=confirm)

    return mcp


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rig remote MCP adapter")
    parser.add_argument(
        "--repo",
        default=os.environ.get("RIG_MCP_REPO", os.getcwd()),
        help="initialized Git/Rig repository root this server is allowed to control",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default=os.environ.get("RIG_MCP_TRANSPORT", "stdio"),
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("RIG_MCP_HOST", "127.0.0.1"),
        help="Streamable HTTP loopback bind host; ignored by stdio",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=os.environ.get("RIG_MCP_PORT", "8000"),
        help="Streamable HTTP bind port; ignored by stdio",
    )
    parser.add_argument(
        "--allow-write",
        action="store_true",
        default=_env_bool("RIG_MCP_ALLOW_WRITE"),
        help="register run/accept/discard; read-only is the default",
    )
    parser.add_argument(
        "--allow-unauthenticated-http",
        action="store_true",
        default=_env_bool("RIG_MCP_ALLOW_UNAUTHENTICATED_HTTP"),
        help="acknowledge that HTTP has no built-in authentication and is single-operator only",
    )
    parser.add_argument(
        "--operator-id",
        default=os.environ.get("RIG_MCP_OPERATOR_ID"),
        help="authorized principal set as RIG_ACTOR/RIG_USER; required for write-enabled HTTP",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=os.environ.get("RIG_MCP_TIMEOUT", "1800"),
        help="per Rig CLI invocation timeout in seconds",
    )
    return parser


@exitcodes.guard
def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.transport == "streamable-http" and not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.transport == "streamable-http" and not _is_loopback_host(args.host):
        parser.error(
            "--host must be loopback; put an authenticated HTTPS tunnel or reverse proxy "
            "in front of rig-mcp"
        )

    try:
        gateway = RigGateway.create(
            args.repo,
            allow_write=args.allow_write,
            timeout_seconds=args.timeout,
            operator_id=args.operator_id if args.transport == "streamable-http" else None,
        )
        mcp = create_server(
            gateway,
            transport=args.transport,
            host=args.host,
            port=args.port,
            allow_unauthenticated_http=args.allow_unauthenticated_http,
        )
    except RigMcpError as exc:
        parser.error(str(exc))
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
