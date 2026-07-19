"""Provider adapters for fair bare and rig benchmark arms."""

from __future__ import annotations

import json
import os
import pathlib
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from typing import Mapping

from .bench_tasks import BenchTask, _copy_source, _remove_tree


SUPPORTED_PROVIDERS = frozenset({"claude", "codex", "ollama", "lmstudio", "mock"})
_OPENAI_ENDPOINTS = {
    "ollama": "http://localhost:11434/v1/chat/completions",
    "lmstudio": "http://localhost:1234/v1/chat/completions",
}
_LOCAL_DEFAULT_MODELS = {"ollama": "llama3.1", "lmstudio": "local-model"}


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


def resolve_pair_model(
    provider: str,
    requested_model: str | None,
    options: Mapping[str, object] | None,
) -> str:
    if requested_model:
        return requested_model
    if provider == "mock":
        return "mock"
    if provider not in _OPENAI_ENDPOINTS:
        raise ValueError(f"provider {provider!r} requires an explicit model")

    settings = options or {}
    configured_base = settings.get("base_url")
    if configured_base:
        base_url = str(configured_base).rstrip("/")
    else:
        base_url = _OPENAI_ENDPOINTS[provider].removesuffix("/chat/completions")
    request = urllib.request.Request(f"{base_url}/models", method="GET")
    try:
        with urllib.request.urlopen(
            request,
            timeout=float(settings.get("model_timeout_s", 5)),
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        discovered = [
            item.get("id")
            for item in payload.get("data", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
        ]
    except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
        discovered = []
    return discovered[0] if discovered else _LOCAL_DEFAULT_MODELS[provider]


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
    *,
    claude_no_session_persistence: bool = True,
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
        if claude_no_session_persistence:
            command.append("--no-session-persistence")
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
        ephemeral=provider != "claude" or claude_no_session_persistence,
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

    invocation = build_bare_attempt(
        provider,
        task.goal,
        workspace,
        model,
        claude_no_session_persistence=bool(settings.get("claude_no_session_persistence", True)),
    )
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
    except (OSError, UnicodeError, subprocess.SubprocessError) as error:
        returncode = 126
        stdout = ""
        stderr = str(error)
        infra_error = f"runtime_launch_failure: {type(error).__name__}: {error}"
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
    settings = dict(options or {})
    configured_artifacts = settings.get("artifact_dir")
    managed_artifacts = configured_artifacts is None
    artifact_dir = (
        pathlib.Path(tempfile.mkdtemp(prefix=f"rig-bench-artifacts-{task.id}-"))
        if managed_artifacts
        else pathlib.Path(configured_artifacts)
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    settings["artifact_dir"] = artifact_dir
    try:
        return _run_rig_with_artifacts(task, provider, model, workspace, settings)
    finally:
        if managed_artifacts:
            _remove_tree(artifact_dir)


def _run_rig_with_artifacts(
    task: BenchTask,
    provider: str,
    model: str | None,
    workspace: pathlib.Path,
    settings: Mapping[str, object],
) -> ProviderAttempt:
    started = time.monotonic()
    workspace = pathlib.Path(workspace)
    artifact_dir = pathlib.Path(settings["artifact_dir"])
    state_path = artifact_dir / "run-state.json"
    counter_path = artifact_dir / "provider-calls.jsonl"

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
    if provider == "claude" and settings.get("claude_no_session_persistence", True):
        command.append("--no-session-persistence")
    if settings.get("base_url"):
        command.extend(["--base-url", str(settings["base_url"])])

    timeout_s = float(settings.get("rig_timeout_s", 1800))
    environment = os.environ.copy()
    package_root = pathlib.Path(__file__).resolve().parent.parent
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(package_root), existing_pythonpath) if part
    )
    environment["RIG_HOME"] = str(package_root)
    environment["RIG_RUNS_PATH"] = str(artifact_dir / "runs.jsonl")
    environment["RIG_GLOBAL_RUNS_PATH"] = str(artifact_dir / "global-runs.jsonl")
    environment["RIG_STEP_OUTPUT_DIR"] = str(artifact_dir / "step-outputs")
    environment["RIG_BENCH_CALL_COUNTER"] = str(counter_path)
    environment["PYTHONUTF8"] = "1"
    if provider == "mock":
        scenario = str(settings.get("mock_scenario", "success"))
        environment["RIG_BENCH_MOCK_SCENARIO"] = scenario
        source_variant = "narrow" if scenario == "partial" else "canonical"
        environment["RIG_BENCH_MOCK_CANONICAL"] = str(task.root / source_variant)
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
            invocations=_read_call_count(counter_path),
            stdout="",
            stderr=str(error),
            infra_error=f"missing_executable: benchmark runner: {error}",
        )
    except subprocess.TimeoutExpired as error:
        return ProviderAttempt(
            provider=provider,
            model=model,
            returncode=124,
            elapsed_s=time.monotonic() - started,
            invocations=_read_call_count(counter_path),
            stdout=_stream_text(error.stdout or error.output),
            stderr=_stream_text(error.stderr),
            infra_error=f"timeout: rig runner exceeded {timeout_s:g}s",
        )
    except (OSError, UnicodeError, subprocess.SubprocessError) as error:
        return ProviderAttempt(
            provider=provider,
            model=model,
            returncode=126,
            elapsed_s=time.monotonic() - started,
            invocations=_read_call_count(counter_path),
            stdout="",
            stderr=str(error),
            infra_error=f"runtime_launch_failure: {type(error).__name__}: {error}",
        )

    state = _read_run_state(state_path)
    infra_error = _rig_infra_error(completed.stdout or "", completed.stderr or "", state)
    if not state and infra_error is None:
        infra_error = "harness_state_missing: rig runner did not write run-state.json"
    return ProviderAttempt(
        provider=provider,
        model=model,
        returncode=completed.returncode,
        elapsed_s=time.monotonic() - started,
        invocations=_read_call_count(counter_path),
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


def _read_run_state(path: pathlib.Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_call_count(path: pathlib.Path) -> int:
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line)
    except OSError:
        return 0


def _rig_infra_error(stdout: str, stderr: str, state: dict) -> str | None:
    detail = f"{stdout}\n{stderr}\n{json.dumps(state, ensure_ascii=True)}".casefold()
    if "provider not found" in detail or "no such file or directory" in detail:
        return "missing_executable: provider executable was not found"
    if "provider timeout" in detail or "generator failed (exit 124)" in detail:
        return "timeout: provider call timed out"
    if any(
        marker in detail
        for marker in (
            "authentication failed",
            "provider authentication failure",
            "not authenticated",
            "not logged in",
            "unauthorized",
            "invalid api key",
        )
    ):
        return "authentication_failure: provider rejected credentials"
    if "provider endpoint failure" in detail:
        return "endpoint_failure: local provider endpoint failed"
    if "connection refused" in detail or "endpoint" in detail and "error" in detail:
        return "endpoint_failure: local provider endpoint failed"
    if '"action": "exec_failed"' in detail or "adaptive generator failed" in detail:
        return "provider_failure: provider call failed"
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
    except TimeoutError as error:
        return _http_error_attempt(
            invocation,
            started,
            str(error),
            f"timeout: {error}",
        )
    except urllib.error.URLError as error:
        reason = error.reason
        category = "timeout" if isinstance(reason, TimeoutError) else "endpoint_failure"
        return _http_error_attempt(
            invocation,
            started,
            str(error),
            f"{category}: {error}",
        )
    except OSError as error:
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

    try:
        _validate_unified_diff(invocation.cwd, patch)
    except ValueError as error:
        return _http_error_attempt(
            invocation,
            started,
            str(error),
            f"malformed_output: {error}",
        )

    try:
        checked = _run_git_apply(invocation.cwd, patch, check_only=True)
    except (OSError, UnicodeError, subprocess.SubprocessError) as error:
        return _git_apply_infra_attempt(invocation, started, error)
    if checked.returncode != 0:
        detail = (checked.stderr or "git apply rejected provider output").strip()
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

    try:
        applied = _run_git_apply(invocation.cwd, patch, check_only=False)
    except (OSError, UnicodeError, subprocess.SubprocessError) as error:
        return _git_apply_infra_attempt(invocation, started, error)
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


def _run_git_apply(
    workspace: pathlib.Path,
    patch: str,
    *,
    check_only: bool,
) -> subprocess.CompletedProcess[str]:
    command = ["git", "apply"]
    if check_only:
        command.append("--check")
    command.extend(["--whitespace=nowarn", "-"])
    return subprocess.run(
        command,
        cwd=workspace,
        input=patch,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _git_apply_infra_attempt(
    invocation: BareInvocation,
    started: float,
    error: Exception,
) -> ProviderAttempt:
    category = "missing_executable" if isinstance(error, FileNotFoundError) else "runtime_launch_failure"
    return _http_error_attempt(
        invocation,
        started,
        str(error),
        f"{category}: git apply: {type(error).__name__}: {error}",
    )


def _validate_unified_diff(workspace: pathlib.Path, patch: str) -> None:
    paths: list[str] = []
    for line in patch.splitlines():
        if line in {"new file mode 120000", "new mode 120000"}:
            raise ValueError("symbolic-link patches are not allowed")
        if line.startswith("diff --git "):
            try:
                fields = shlex.split(line)
            except ValueError as error:
                raise ValueError(f"invalid diff header: {error}") from error
            if len(fields) != 4:
                raise ValueError("invalid diff --git header")
            paths.extend(fields[2:4])
            continue
        for prefix in ("--- ", "+++ ", "rename from ", "rename to ", "copy from ", "copy to "):
            if line.startswith(prefix):
                paths.append(_patch_header_path(line.removeprefix(prefix)))
                break

    normalized = {
        path for path in (_normalize_patch_path(raw_path) for raw_path in paths) if path is not None
    }
    if not normalized:
        raise ValueError("provider output does not contain a unified diff")
    for relative in normalized:
        candidate = workspace
        for part in pathlib.PurePosixPath(relative).parts:
            candidate /= part
            if _path_is_link(candidate):
                raise ValueError(f"patch target traverses a symbolic link: {relative}")


def _patch_header_path(value: str) -> str:
    value = value.split("\t", 1)[0].strip()
    if value.startswith('"'):
        try:
            fields = shlex.split(value)
        except ValueError as error:
            raise ValueError(f"invalid quoted patch path: {error}") from error
        if len(fields) != 1:
            raise ValueError("invalid quoted patch path")
        return fields[0]
    return value


def _normalize_patch_path(raw_path: str) -> str | None:
    path = raw_path.replace("\\", "/")
    if path == "/dev/null":
        return None
    if path.startswith(("a/", "b/")):
        path = path[2:]
    posix_path = pathlib.PurePosixPath(path)
    windows_path = pathlib.PureWindowsPath(path)
    if posix_path.is_absolute() or windows_path.is_absolute():
        raise ValueError(f"absolute patch path is not allowed: {raw_path}")
    if any(part == ".." for part in posix_path.parts):
        raise ValueError(f"patch path traversal is not allowed: {raw_path}")
    if not posix_path.parts or posix_path.parts[0].casefold() == ".git":
        raise ValueError(f"reserved patch path is not allowed: {raw_path}")
    return posix_path.as_posix()


def _path_is_link(path: pathlib.Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


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
