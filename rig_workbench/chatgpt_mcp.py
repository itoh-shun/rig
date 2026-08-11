"""Remote MCP adapter for driving Rig from ChatGPT and other MCP clients.

This module is intentionally a thin host adapter. It does not duplicate Rig's
routing, worktree, gate, accept, or orchestration logic; every tool delegates to
``rig-wb`` through the package-native CLI entry point.

The server is bound to one repository root at startup. Read tools are always
available. Mutating tools require ``--allow-write`` (or ``RIG_MCP_ALLOW_WRITE=1``)
and still pass through Rig's existing gates. The remote adapter deliberately
exposes no force-accept surface.
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

_MAX_OUTPUT = 128 * 1024
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


class RigMcpError(RuntimeError):
    """A bounded, user-visible adapter error."""


@dataclass(frozen=True)
class RigGateway:
    """Thin command gateway to the canonical ``rig-wb`` CLI."""

    repo: Path
    allow_write: bool = False
    timeout_seconds: int = 1800

    @classmethod
    def create(
        cls,
        repo: str | os.PathLike[str],
        *,
        allow_write: bool = False,
        timeout_seconds: int = 1800,
    ) -> "RigGateway":
        root = Path(repo).expanduser().resolve()
        if not root.is_dir():
            raise RigMcpError(f"repository root does not exist: {root}")
        if not ((root / ".git").exists() or (root / ".rig").exists()):
            raise RigMcpError(f"repository root must contain .git or .rig: {root}")
        return cls(root, allow_write=allow_write, timeout_seconds=timeout_seconds)

    @staticmethod
    def _safe_name(value: str, label: str) -> str:
        if not value or not _SAFE_NAME.fullmatch(value):
            raise RigMcpError(f"invalid {label}: {value!r}")
        return value

    def _invoke(self, args: list[str], *, write: bool = False) -> dict[str, Any]:
        if write and not self.allow_write:
            raise RigMcpError(
                "write actions are disabled for this MCP server; restart with --allow-write"
            )

        command = [sys.executable, "-m", "rig_workbench.cli", *args]
        env = os.environ.copy()
        env.setdefault("RIG_INVOKER", "chatgpt-mcp")
        try:
            proc = subprocess.run(
                command,
                cwd=self.repo,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RigMcpError(
                f"Rig command timed out after {self.timeout_seconds}s"
            ) from exc

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        if len(stdout) > _MAX_OUTPUT:
            stdout = stdout[:_MAX_OUTPUT] + "\n...[truncated by MCP adapter]"
        if len(stderr) > _MAX_OUTPUT:
            stderr = stderr[:_MAX_OUTPUT] + "\n...[truncated by MCP adapter]"
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }

    # ---- read-only control plane -----------------------------------------

    def status(self, task_id: str | None = None) -> dict[str, Any]:
        args = ["wb", "status"]
        if task_id:
            args.append(self._safe_name(task_id, "task_id"))
        return self._invoke(args)

    def board(self, include_all: bool = False) -> dict[str, Any]:
        args = ["wb", "board"]
        if include_all:
            args.append("--all")
        return self._invoke(args)

    def diff(self, task_id: str | None = None) -> dict[str, Any]:
        args = ["wb", "diff"]
        if task_id:
            args.append(self._safe_name(task_id, "task_id"))
        return self._invoke(args)

    def plan(self, recipe: str) -> dict[str, Any]:
        recipe = self._safe_name(recipe, "recipe")
        return self._invoke(["plan", recipe, "--json"])

    # ---- mutating actions ------------------------------------------------

    def run(
        self,
        recipe: str,
        *,
        provider: str = "mock",
        verifier_provider: str | None = None,
        goal: str | None = None,
        isolate: bool = True,
    ) -> dict[str, Any]:
        recipe = self._safe_name(recipe, "recipe")
        provider = self._safe_name(provider, "provider")
        args = ["run", recipe, "--provider", provider]
        if verifier_provider:
            args += [
                "--verifier-provider",
                self._safe_name(verifier_provider, "verifier_provider"),
            ]
        if goal:
            args += ["--goal", goal]
        if isolate:
            args.append("--isolate")
        return self._invoke(args, write=True)

    def accept(self, task_id: str | None = None) -> dict[str, Any]:
        """Accept through Rig's canonical gate path; force is intentionally absent."""
        args = ["wb", "accept"]
        if task_id:
            args.append(self._safe_name(task_id, "task_id"))
        return self._invoke(args, write=True)

    def discard(self, task_id: str, *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            raise RigMcpError("discard requires confirm=true")
        task_id = self._safe_name(task_id, "task_id")
        return self._invoke(["wb", "discard", task_id, "--yes"], write=True)


def create_server(gateway: RigGateway):
    """Create a FastMCP server without importing the SDK at package import time."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise RigMcpError(
            "MCP support is not installed. Run: pip install 'rig-workbench[mcp]'"
        ) from exc

    mcp = FastMCP(
        "Rig Quality OS",
        instructions=(
            "Use Rig as the quality/control plane for the repository bound to this server. "
            "Prefer status/board/diff/plan before mutating actions. Never represent a gate "
            "pass as proof of correctness. Accept has no force surface through this adapter."
        ),
    )

    @mcp.tool()
    def rig_status(task_id: str | None = None) -> dict[str, Any]:
        """Read the current Rig task status. Does not modify the repository."""
        return gateway.status(task_id)

    @mcp.tool()
    def rig_board(include_all: bool = False) -> dict[str, Any]:
        """Read the Rig task board. Does not modify the repository."""
        return gateway.board(include_all)

    @mcp.tool()
    def rig_diff(task_id: str | None = None) -> dict[str, Any]:
        """Read the structured diff for a Rig task. Does not modify the repository."""
        return gateway.diff(task_id)

    @mcp.tool()
    def rig_plan(recipe: str) -> dict[str, Any]:
        """Resolve a recipe into Rig's deterministic JSON execution plan."""
        return gateway.plan(recipe)

    @mcp.tool()
    def rig_run(
        recipe: str,
        provider: str = "mock",
        verifier_provider: str | None = None,
        goal: str | None = None,
        isolate: bool = True,
    ) -> dict[str, Any]:
        """Run a Rig recipe. Requires server-side write enablement; isolation defaults on."""
        return gateway.run(
            recipe,
            provider=provider,
            verifier_provider=verifier_provider,
            goal=goal,
            isolate=isolate,
        )

    @mcp.tool()
    def rig_accept(task_id: str | None = None) -> dict[str, Any]:
        """Accept a gated task into the main worktree as staged changes; force is unavailable."""
        return gateway.accept(task_id)

    @mcp.tool()
    def rig_discard(task_id: str, confirm: bool = False) -> dict[str, Any]:
        """Discard a task worktree/branch while retaining the run log; requires confirm=true."""
        return gateway.discard(task_id, confirm=confirm)

    return mcp


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Rig remote MCP adapter for ChatGPT and other MCP clients"
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get("RIG_MCP_REPO", os.getcwd()),
        help="single repository root this server is allowed to control",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default=os.environ.get("RIG_MCP_TRANSPORT", "streamable-http"),
    )
    parser.add_argument("--host", default=os.environ.get("RIG_MCP_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("RIG_MCP_PORT", "8000"))
    )
    parser.add_argument(
        "--allow-write",
        action="store_true",
        default=_env_bool("RIG_MCP_ALLOW_WRITE"),
        help="enable run/accept/discard; read-only is the default",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("RIG_MCP_TIMEOUT", "1800")),
        help="per Rig CLI invocation timeout in seconds",
    )
    args = parser.parse_args(argv)

    gateway = RigGateway.create(
        args.repo,
        allow_write=args.allow_write,
        timeout_seconds=args.timeout,
    )
    mcp = create_server(gateway)
    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        # Bind localhost by default. ChatGPT does not connect directly to local MCP;
        # use a supported secure tunnel or deploy behind authenticated HTTPS.
        mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
