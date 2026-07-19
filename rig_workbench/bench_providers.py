"""Provider adapters for fair bare and rig benchmark arms."""

from __future__ import annotations

import json
import os
import pathlib
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from typing import Mapping

from .bench_tasks import BenchTask, _copy_source


SUPPORTED_PROVIDERS = frozenset({"claude", "codex", "ollama", "lmstudio", "mock"})
_OPENAI_ENDPOINTS = {
    "ollama": "http://localhost:11434/v1/chat/completions",
    "lmstudio": "http://localhost:1234/v1/chat/completions",
}


@dataclass(frozen=True)
class BareInvocation:
    provider: str
    goal: str
    cwd: pathlib.Path
    model: str | None
    prompt: str
    argv: tuple[str, ...] = ()
    endpoint: str | None = None
    writable: bool = True
    single_invocation: bool = True
    ephemeral: bool = True


@dataclass(frozen=True)
class ProviderAttempt:
    provider: str
    model: str | None
    returncode: int
    elapsed_s: float
    invocations: int
    stdout: str
    stderr: str
    infra_error: str | None


def _agent_prompt(goal: str) -> str:
    return (
        "Work directly in the current repository. Make every file edit needed to complete "
        "the goal, keep the changes focused, and do not merely describe a solution.\n\n"
        f"Goal:\n{goal}"
    )


def _visible_snapshot(workspace: pathlib.Path) -> str:
    sections = []
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or ".git" in path.relative_to(workspace).parts:
            continue
        relative = path.relative_to(workspace).as_posix()
        content = path.read_text(encoding="utf-8", errors="replace")
        sections.append(f"--- {relative} ---\n{content}")
    return "\n\n".join(sections)


def _patch_prompt(goal: str, workspace: pathlib.Path) -> str:
    return (
        f"Goal:\n{goal}\n\n"
        "Return only a valid unified diff whose paths are relative to the repository root. "
        "The diff may edit multiple files. Do not use Markdown fences or include commentary.\n\n"
        f"Visible repository files:\n{_visible_snapshot(workspace)}"
    )


def build_bare_attempt(
    provider: str,
    goal: str,
    workspace: pathlib.Path,
    model: str | None = None,
) -> BareInvocation:
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"unknown benchmark provider {provider!r}")
    workspace = pathlib.Path(workspace)
    prompt = (
        _patch_prompt(goal, workspace) if provider in _OPENAI_ENDPOINTS else _agent_prompt(goal)
    )
    argv: tuple[str, ...] = ()
    endpoint = _OPENAI_ENDPOINTS.get(provider)
    if provider == "claude":
        command = ["claude", "-p", prompt, "--output-format", "text"]
        if model:
            command.extend(["--model", model])
        command.extend(["--permission-mode", "acceptEdits"])
        argv = tuple(command)
    elif provider == "codex":
        command = [
            "codex",
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            "--cd",
            str(workspace),
            "--ephemeral",
        ]
        if model:
            command.extend(["-m", model])
        command.append(prompt)
        argv = tuple(command)
    return BareInvocation(
        provider=provider,
        goal=goal,
        cwd=workspace,
        model=model,
        prompt=prompt,
        argv=argv,
        endpoint=endpoint,
    )


def run_bare(
    task: BenchTask,
    provider: str,
    model: str | None,
    workspace: pathlib.Path,
    options: Mapping[str, object] | None,
) -> ProviderAttempt:
    started = time.monotonic()
    settings = options or {}
    if provider == "mock":
        scenario = str(settings.get("mock_scenario", "success"))
        if scenario == "success":
            _copy_source(task.root / "canonical", pathlib.Path(workspace))
            returncode = 0
            stderr = ""
            infra_error = None
        elif scenario == "partial":
            _copy_source(task.root / "narrow", pathlib.Path(workspace))
            returncode = 1
            stderr = "mock provider stopped after partial edits"
            infra_error = "provider_failure: mock provider stopped after partial edits"
        elif scenario == "timeout":
            returncode = 124
            stderr = "mock provider timed out"
            infra_error = "timeout: mock provider timed out"
        else:
            returncode = 1
            stderr = "mock provider returned malformed output"
            infra_error = "malformed_output: mock provider returned malformed output"
        return ProviderAttempt(
            provider=provider,
            model=model,
            returncode=returncode,
            elapsed_s=time.monotonic() - started,
            invocations=1,
            stdout="mock provider completed" if returncode == 0 else "",
            stderr=stderr,
            infra_error=infra_error,
        )

    if (
        provider == "claude"
        and (os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_SESSION_ID"))
        and not settings.get("allow_headless_in_cc")
    ):
        return ProviderAttempt(
            provider=provider,
            model=model,
            returncode=126,
            elapsed_s=time.monotonic() - started,
            invocations=0,
            stdout="",
            stderr="headless Claude is blocked inside an active Claude Code session",
            infra_error=(
                "blocked_headless_provider: pass allow_headless_in_cc only for an "
                "explicitly authorized paid run"
            ),
        )

    invocation = build_bare_attempt(provider, task.goal, workspace, model)
    if provider in _OPENAI_ENDPOINTS and settings.get("base_url"):
        base_url = str(settings["base_url"]).rstrip("/")
        invocation = replace(invocation, endpoint=f"{base_url}/chat/completions")
    timeout_s = float(settings.get("timeout_s", 600))
    if provider in _OPENAI_ENDPOINTS:
        return _run_http_bare(invocation, timeout_s, started)
    try:
        completed = subprocess.run(
            invocation.argv,
            cwd=invocation.cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except FileNotFoundError as error:
        returncode = 127
        stdout = ""
        stderr = str(error)
        infra_error = f"missing_executable: {error}"
    except subprocess.TimeoutExpired as error:
        returncode = 124
        stdout = _stream_text(error.stdout or error.output)
        stderr = _stream_text(error.stderr)
        infra_error = f"timeout: provider exceeded {timeout_s:g}s"
    else:
        returncode = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        infra_error = _process_infra_error(returncode, stdout, stderr)
    return ProviderAttempt(
        provider=provider,
        model=model,
        returncode=returncode,
        elapsed_s=time.monotonic() - started,
        invocations=1,
        stdout=stdout,
        stderr=stderr,
        infra_error=infra_error,
    )


def run_rig(
    task: BenchTask,
    provider: str,
    model: str | None,
    workspace: pathlib.Path,
    options: Mapping[str, object] | None,
) -> ProviderAttempt:
    started = time.monotonic()
    workspace = pathlib.Path(workspace)
    settings = options or {}
    state_path = workspace / "run-state.json"
    if provider == "mock":
        return _run_mock_rig(task, model, workspace, state_path, settings, started)

    command = [*_rig_command(settings), "run", "adaptive-bugfix", "--provider", provider]
    command.extend(["--goal", task.goal, "--check", public_check_command(task)])
    command.extend(
        [
            "--max-steps",
            str(settings.get("max_steps", 14)),
            "--out",
            str(state_path),
        ]
    )
    if model:
        command.extend(["--model", model])
    if settings.get("allow_headless_in_cc"):
        command.append("--allow-headless-in-cc")
    if settings.get("base_url"):
        command.extend(["--base-url", str(settings["base_url"])])

    timeout_s = float(settings.get("rig_timeout_s", 1800))
    environment = os.environ.copy()
    package_root = pathlib.Path(__file__).resolve().parent.parent
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(package_root), existing_pythonpath) if part
    )
    environment.setdefault("RIG_HOME", str(package_root))
    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except FileNotFoundError as error:
        return ProviderAttempt(
            provider=provider,
            model=model,
            returncode=127,
            elapsed_s=time.monotonic() - started,
            invocations=0,
            stdout="",
            stderr=str(error),
            infra_error=f"missing_executable: benchmark runner: {error}",
        )
    except subprocess.TimeoutExpired as error:
        state = _read_run_state(state_path)
        invocations = max(1, _state_invocations(state))
        return ProviderAttempt(
            provider=provider,
            model=model,
            returncode=124,
            elapsed_s=time.monotonic() - started,
            invocations=invocations,
            stdout=_stream_text(error.stdout or error.output),
            stderr=_stream_text(error.stderr),
            infra_error=f"timeout: rig runner exceeded {timeout_s:g}s",
        )

    state = _read_run_state(state_path)
    invocations = _state_invocations(state)
    infra_error = _rig_infra_error(completed.stdout or "", completed.stderr or "", state)
    if infra_error and invocations == 0:
        invocations = 1
    if not state and infra_error is None:
        infra_error = "harness_state_missing: rig runner did not write run-state.json"
    return ProviderAttempt(
        provider=provider,
        model=model,
        returncode=completed.returncode,
        elapsed_s=time.monotonic() - started,
        invocations=invocations,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        infra_error=infra_error,
    )


def public_check_command(task: BenchTask) -> str:
    command = list(task.test_command)
    if command[0].casefold() in {"python", "python3", "python.exe"}:
        command[0] = sys.executable
    return subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)


def _rig_command(settings: Mapping[str, object]) -> tuple[str, ...]:
    configured = settings.get("rig_argv")
    if configured:
        if isinstance(configured, str):
            return tuple(shlex.split(configured))
        return tuple(str(part) for part in configured)
    override = os.environ.get("RIG_BENCH_RIG_WB")
    if override:
        return tuple(shlex.split(override))
    return (sys.executable, "-m", "rig_workbench.cli")


def _run_mock_rig(
    task: BenchTask,
    model: str | None,
    workspace: pathlib.Path,
    state_path: pathlib.Path,
    settings: Mapping[str, object],
    started: float,
) -> ProviderAttempt:
    scenario = str(settings.get("mock_scenario", "success"))
    if scenario == "success":
        _copy_source(task.root / "canonical", workspace)
        returncode = 0
        invocations = int(settings.get("mock_rig_invocations", 2))
        stderr = ""
        infra_error = None
    elif scenario == "partial":
        _copy_source(task.root / "narrow", workspace)
        returncode = 1
        invocations = 1
        stderr = "mock rig provider stopped after partial edits"
        infra_error = "provider_failure: mock rig provider stopped after partial edits"
    elif scenario == "timeout":
        returncode = 124
        invocations = 1
        stderr = "mock rig provider timed out"
        infra_error = "timeout: mock rig provider timed out"
    elif scenario == "malformed":
        returncode = 1
        invocations = int(settings.get("mock_rig_invocations", 2))
        stderr = "mock rig provider returned malformed output"
        infra_error = None
    else:
        returncode = 1
        invocations = 1
        stderr = f"unknown mock rig scenario: {scenario}"
        infra_error = f"provider_failure: unknown mock rig scenario: {scenario}"
    state = {
        "recipe": "adaptive-bugfix",
        "goal": task.goal,
        "adaptive": {"invocations": invocations},
        "checks": [public_check_command(task)],
        "mock_scenario": scenario,
    }
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return ProviderAttempt(
        provider="mock",
        model=model,
        returncode=returncode,
        elapsed_s=time.monotonic() - started,
        invocations=invocations,
        stdout="mock adaptive run completed" if returncode == 0 else "",
        stderr=stderr,
        infra_error=infra_error,
    )


def _read_run_state(path: pathlib.Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _state_invocations(state: dict) -> int:
    value = (state.get("adaptive") or {}).get("invocations", 0)
    return value if isinstance(value, int) and value >= 0 else 0


def _rig_infra_error(stdout: str, stderr: str, state: dict) -> str | None:
    detail = f"{stdout}\n{stderr}\n{json.dumps(state, ensure_ascii=True)}".casefold()
    if "provider not found" in detail or "no such file or directory" in detail:
        return "missing_executable: provider executable was not found"
    if "provider timeout" in detail:
        return "timeout: provider call timed out"
    if any(
        marker in detail
        for marker in (
            "authentication failed",
            "not authenticated",
            "not logged in",
            "unauthorized",
            "invalid api key",
        )
    ):
        return "authentication_failure: provider rejected credentials"
    if "connection refused" in detail or "endpoint" in detail and "error" in detail:
        return "endpoint_failure: local provider endpoint failed"
    return None


def _run_http_bare(
    invocation: BareInvocation,
    timeout_s: float,
    started: float,
) -> ProviderAttempt:
    body = json.dumps(
        {
            "model": invocation.model or "local-model",
            "messages": [{"role": "user", "content": invocation.prompt}],
            "temperature": 0,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        invocation.endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
        patch = payload["choices"][0]["message"]["content"]
        if not isinstance(patch, str):
            raise (TypeError("provider response content must be text"))
    except urllib.error.HTTPError as error:
        if error.code in {401, 403}:
            infra_error = f"authentication_failure: HTTP {error.code}"
        else:
            infra_error = f"endpoint_failure: HTTP {error.code}"
        return _http_error_attempt(invocation, started, str(error), infra_error)
    except (urllib.error.URLError, TimeoutError) as error:
        return _http_error_attempt(
            invocation,
            started,
            str(error),
            f"endpoint_failure: {error}",
        )
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as error:
        return _http_error_attempt(
            invocation,
            started,
            str(error),
            f"malformed_output: {error}",
        )

    applied = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=invocation.cwd,
        input=patch,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if applied.returncode != 0:
        detail = (applied.stderr or "git apply rejected provider output").strip()
        return ProviderAttempt(
            provider=invocation.provider,
            model=invocation.model,
            returncode=1,
            elapsed_s=time.monotonic() - started,
            invocations=1,
            stdout=patch,
            stderr=detail,
            infra_error=f"malformed_output: {detail}",
        )
    return ProviderAttempt(
        provider=invocation.provider,
        model=invocation.model,
        returncode=0,
        elapsed_s=time.monotonic() - started,
        invocations=1,
        stdout=patch,
        stderr=applied.stderr or "",
        infra_error=None,
    )


def _http_error_attempt(
    invocation: BareInvocation,
    started: float,
    stderr: str,
    infra_error: str,
) -> ProviderAttempt:
    return ProviderAttempt(
        provider=invocation.provider,
        model=invocation.model,
        returncode=1,
        elapsed_s=time.monotonic() - started,
        invocations=1,
        stdout="",
        stderr=stderr,
        infra_error=infra_error,
    )


def _stream_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _process_infra_error(returncode: int, stdout: str, stderr: str) -> str | None:
    if returncode == 0:
        return None
    detail = f"{stdout}\n{stderr}".casefold()
    auth_markers = (
        "authentication",
        "not authenticated",
        "not logged in",
        "please log in",
        "unauthorized",
        "invalid api key",
    )
    if any(marker in detail for marker in auth_markers):
        return "authentication_failure: provider rejected credentials"
    return f"provider_failure: provider exited with status {returncode}"
