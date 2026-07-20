"""Fair paired benchmark runner for bare agents and adaptive rig runs."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, replace
from typing import Mapping

from . import __version__
from .bench_providers import ProviderAttempt, resolve_pair_model, run_bare, run_rig
from .bench_score import (
    classify_outcome as _classify_arm_outcome,
    render_html,
    score_provider,
)
from .bench_tasks import (
    BenchTask,
    _command,
    _remove_tree,
    _require_supported_node,
    load_tasks,
    materialize,
)


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    elapsed_s: float
    infra_error: str | None = None

    @property
    def passed(self) -> bool:
        return self.returncode == 0

    def to_dict(self) -> dict:
        return {
            **asdict(self),
            "command": list(self.command),
            "passed": self.passed,
        }


@dataclass(frozen=True)
class ArmResult:
    name: str
    attempts: tuple[ProviderAttempt, ...]
    git_status: tuple[str, ...]
    changed_files: tuple[str, ...]
    public_test: CommandResult
    hidden_check: CommandResult
    elapsed_s: float
    invocation_count: int
    completed: bool
    runner_state: dict | None
    unrelated_files: tuple[str, ...] = ()
    workspace_leaks: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        data = {
            "name": self.name,
            "attempts": [asdict(attempt) for attempt in self.attempts],
            "git_status": list(self.git_status),
            "changed_files": list(self.changed_files),
            "public_test": self.public_test.to_dict(),
            "hidden_check": self.hidden_check.to_dict(),
            "elapsed_s": self.elapsed_s,
            "invocation_count": self.invocation_count,
            "completed": self.completed,
            "runner_state": self.runner_state,
            "unrelated_files": list(self.unrelated_files),
            "workspace_leaks": list(self.workspace_leaks),
        }
        data["outcome"] = classify_outcome(data, self.name)
        return data


@dataclass(frozen=True)
class PairResult:
    pair_id: str
    task_id: str
    run: int
    provider: str
    model: str | None
    arm_order: tuple[str, str]
    start_trees: dict[str, str]
    arms: dict[str, ArmResult]
    elapsed_s: float

    def to_dict(self) -> dict:
        start_tree = next(iter(self.start_trees.values()))
        return {
            "pair_id": self.pair_id,
            "task_id": self.task_id,
            "run": self.run,
            "provider": self.provider,
            "model": self.model,
            "arm_order": list(self.arm_order),
            "start_trees": dict(self.start_trees),
            "planned": {
                "pair_id": self.pair_id,
                "arm_order": list(self.arm_order),
                "provider": self.provider,
                "model": self.model,
                "start_tree": start_tree,
            },
            "arms": {name: arm.to_dict() for name, arm in self.arms.items()},
            "elapsed_s": self.elapsed_s,
        }


@dataclass(frozen=True)
class _WorkspaceSnapshot:
    files: dict[str, str]
    error: str | None = None


def planned_arm_order(run_index: int) -> tuple[str, str]:
    if run_index < 1:
        raise ValueError("run_index must be at least 1")
    return ("bare", "rig") if run_index % 2 else ("rig", "bare")


def _tree_hash(workspace: pathlib.Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(workspace.rglob("*")):
        relative = path.relative_to(workspace)
        if ".git" in relative.parts or not path.is_file():
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _run_command(
    command: tuple[str, ...],
    *,
    cwd: pathlib.Path,
    env: dict[str, str],
    timeout_s: float,
) -> CommandResult:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except FileNotFoundError as error:
        return CommandResult(
            command=command,
            returncode=127,
            stdout="",
            stderr=str(error),
            elapsed_s=time.monotonic() - started,
            infra_error=f"missing_executable: {error}",
        )
    except subprocess.TimeoutExpired as error:
        return CommandResult(
            command=command,
            returncode=124,
            stdout=_stream_text(error.stdout or error.output),
            stderr=_stream_text(error.stderr),
            elapsed_s=time.monotonic() - started,
            infra_error=f"timeout: command exceeded {timeout_s:g}s",
        )
    except (OSError, UnicodeError, subprocess.SubprocessError) as error:
        return CommandResult(
            command=command,
            returncode=126,
            stdout="",
            stderr=str(error),
            elapsed_s=time.monotonic() - started,
            infra_error=f"runtime_launch_failure: {type(error).__name__}: {error}",
        )
    return CommandResult(
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        elapsed_s=time.monotonic() - started,
    )


def _stream_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _evaluate_workspace(
    task: BenchTask,
    workspace: pathlib.Path,
    options: Mapping[str, object],
) -> tuple[CommandResult, CommandResult]:
    if task.language.casefold() == "typescript":
        try:
            _require_supported_node()
        except FileNotFoundError as error:
            infra_error = f"missing_executable: {error}"
            runtime_detail = str(error)
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            infra_error = f"runtime_launch_failure: {type(error).__name__}: {error}"
            runtime_detail = str(error)
        else:
            infra_error = None
            runtime_detail = ""
        if infra_error is not None:
            public = CommandResult(
                command=tuple(_command(task.test_command)),
                returncode=127,
                stdout="",
                stderr=runtime_detail,
                elapsed_s=0.0,
                infra_error=infra_error,
            )
            hidden = CommandResult(
                command=(*_command(task.hidden_command), str(workspace)),
                returncode=127,
                stdout="",
                stderr=runtime_detail,
                elapsed_s=0.0,
                infra_error=infra_error,
            )
            return public, hidden
    timeout_s = float(options.get("check_timeout_s", 60))
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    public = _run_command(
        tuple(_command(task.test_command)),
        cwd=workspace,
        env=environment,
        timeout_s=timeout_s,
    )

    hidden_root = pathlib.Path(tempfile.mkdtemp(prefix=f"rig-bench-hidden-{task.id}-"))
    try:
        shutil.copy2(task.root / "hidden_check.py", hidden_root / "hidden_check.py")
        existing_pythonpath = environment.get("PYTHONPATH")
        hidden_environment = environment.copy()
        hidden_environment["PYTHONPATH"] = os.pathsep.join(
            part for part in (str(workspace), existing_pythonpath) if part
        )
        hidden_command = (*_command(task.hidden_command), str(workspace))
        hidden = _run_command(
            tuple(hidden_command),
            cwd=hidden_root,
            env=hidden_environment,
            timeout_s=timeout_s,
        )
    finally:
        _remove_tree(hidden_root)
    return public, hidden


def _read_runner_state(artifact_dir: pathlib.Path) -> dict | None:
    path = artifact_dir / "run-state.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return state if isinstance(state, dict) else None


def _git_evidence(workspace: pathlib.Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    status = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=workspace,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    status_lines = tuple(line for line in status.stdout.splitlines() if line.strip())
    changed = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=workspace,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=workspace,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    changed_files = tuple(
        sorted(
            {
                line
                for output in (changed.stdout, untracked.stdout)
                for line in output.splitlines()
                if line.strip()
            }
        )
    )
    return status_lines, changed_files


def _workspace_snapshot(root: pathlib.Path) -> _WorkspaceSnapshot:
    try:
        probe = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
        )
        if probe.returncode != 0 or probe.stdout.strip() != b"true":
            return _snapshot_command_failure(("rev-parse", "--is-inside-work-tree"), probe)

        symbolic_head = subprocess.run(
            ["git", "-C", str(root), "symbolic-ref", "--quiet", "HEAD"],
            capture_output=True,
        )
        if symbolic_head.returncode == 0:
            head_ref = os.fsdecode(symbolic_head.stdout).strip()
            head = subprocess.run(
                ["git", "-C", str(root), "show-ref", "--verify", "--quiet", head_ref],
                capture_output=True,
            )
            if head.returncode not in (0, 1):
                return _snapshot_command_failure(
                    ("show-ref", "--verify", "--quiet", head_ref),
                    head,
                )
            tracked_command = (
                ("diff", "--name-only", "-z", "HEAD")
                if head.returncode == 0
                else ("ls-files", "--cached", "-z")
            )
        elif symbolic_head.returncode == 1:
            detached_head = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
                capture_output=True,
            )
            if detached_head.returncode != 0:
                return _snapshot_command_failure(
                    ("rev-parse", "--verify", "HEAD"),
                    detached_head,
                )
            tracked_command = ("diff", "--name-only", "-z", "HEAD")
        else:
            return _snapshot_command_failure(
                ("symbolic-ref", "--quiet", "HEAD"),
                symbolic_head,
            )

        paths: set[str] = set()
        commands = (
            tracked_command,
            ("ls-files", "--others", "--exclude-standard", "-z"),
            ("ls-files", "--others", "--ignored", "--exclude-standard", "-z"),
        )
        for arguments in commands:
            result = subprocess.run(
                ["git", "-C", str(root), *arguments],
                capture_output=True,
            )
            if result.returncode != 0:
                return _snapshot_command_failure(arguments, result)
            paths.update(
                os.fsdecode(item).replace("\\", "/") for item in result.stdout.split(b"\0") if item
            )
        return _WorkspaceSnapshot(
            files={
                relative: _workspace_fingerprint(root / pathlib.PurePosixPath(relative))
                for relative in paths
            }
        )
    except OSError as error:
        return _WorkspaceSnapshot(
            files={},
            error=f"workspace snapshot failed: {type(error).__name__}: {error}",
        )


def _snapshot_command_failure(
    arguments: tuple[str, ...],
    result: subprocess.CompletedProcess[bytes],
) -> _WorkspaceSnapshot:
    detail = os.fsdecode(result.stderr).strip() or "no error detail"
    command = " ".join(("git", *arguments))
    return _WorkspaceSnapshot(
        files={},
        error=f"workspace snapshot failed: {command} exited {result.returncode}: {detail}",
    )


def _workspace_fingerprint(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    try:
        metadata = path.lstat()
    except OSError:
        digest.update(b"missing")
        return digest.hexdigest()

    digest.update(str(metadata.st_mode).encode("ascii"))
    if path.is_symlink():
        digest.update(os.fsencode(os.readlink(path)))
    elif path.is_file():
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def _workspace_changes(
    before: _WorkspaceSnapshot,
    after: _WorkspaceSnapshot,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            path
            for path in before.files.keys() | after.files.keys()
            if before.files.get(path) != after.files.get(path)
        )
    )


def _failed_attempt(
    provider: str,
    model: str | None,
    started: float,
    error: Exception,
) -> ProviderAttempt:
    return ProviderAttempt(
        provider=provider,
        model=model,
        returncode=1,
        elapsed_s=time.monotonic() - started,
        invocations=0,
        stdout="",
        stderr=str(error),
        infra_error=f"harness_failure: {type(error).__name__}: {error}",
    )


def run_pair(
    task: BenchTask,
    run_index: int,
    provider: str,
    model: str | None,
    options: Mapping[str, object] | None = None,
) -> PairResult:
    settings = dict(options or {})
    model = resolve_pair_model(provider, model, settings)
    order = planned_arm_order(run_index)
    pair_started = time.monotonic()
    workspaces: dict[str, pathlib.Path] = {}
    artifact_dir = pathlib.Path(
        tempfile.mkdtemp(prefix=f"rig-bench-artifacts-{task.id}-{run_index:03d}-")
    )
    settings["artifact_dir"] = str(artifact_dir)
    try:
        # Planning is complete before execution: neither arm can affect the other's copy.
        workspaces["bare"] = materialize(task)
        workspaces["rig"] = materialize(task)
        start_trees = {name: _tree_hash(path) for name, path in workspaces.items()}
        if len(set(start_trees.values())) != 1:
            raise RuntimeError("paired benchmark workspaces have different starting trees")

        arms: dict[str, ArmResult] = {}
        for name in order:
            arm_started = time.monotonic()
            workspace = workspaces[name]
            leak_root = pathlib.Path(settings.get("leak_check_root", pathlib.Path.cwd()))
            leak_snapshot_before = _workspace_snapshot(leak_root)
            if leak_snapshot_before.error:
                attempt = _failed_attempt(
                    provider,
                    model,
                    arm_started,
                    RuntimeError(leak_snapshot_before.error),
                )
            else:
                try:
                    attempt = (
                        run_bare(task, provider, model, workspace, settings)
                        if name == "bare"
                        else run_rig(task, provider, model, workspace, settings)
                    )
                except Exception as error:
                    attempt = _failed_attempt(provider, model, arm_started, error)

            runner_state = _read_runner_state(artifact_dir) if name == "rig" else None
            if name == "rig" and artifact_dir.exists():
                _remove_tree(artifact_dir)
            git_status, changed_files = _git_evidence(workspace)
            leak_snapshot_after = _workspace_snapshot(leak_root)
            if leak_snapshot_before.error:
                workspace_leaks = ()
            elif leak_snapshot_after.error:
                workspace_leaks = ()
                detail = f"harness_failure: {leak_snapshot_after.error}"
                attempt = replace(
                    attempt,
                    returncode=1,
                    stderr="\n".join(part for part in (attempt.stderr, detail) if part),
                    infra_error="; ".join(part for part in (attempt.infra_error, detail) if part),
                )
            else:
                workspace_leaks = _workspace_changes(
                    leak_snapshot_before,
                    leak_snapshot_after,
                )
            expected_files = {path.replace("\\", "/") for path in task.expected_files}
            unrelated_files = tuple(
                path
                for path in changed_files
                if path.replace("\\", "/") not in expected_files
                and "__pycache__" not in pathlib.PurePosixPath(path.replace("\\", "/")).parts
                and not path.casefold().endswith((".pyc", ".pyo"))
            )
            public, hidden = _evaluate_workspace(task, workspace, settings)
            attempts = (attempt,)
            arms[name] = ArmResult(
                name=name,
                attempts=attempts,
                git_status=git_status,
                changed_files=changed_files,
                public_test=public,
                hidden_check=hidden,
                elapsed_s=time.monotonic() - arm_started,
                invocation_count=sum(item.invocations for item in attempts),
                completed=(
                    attempt.infra_error is None
                    and attempt.returncode == 0
                    and public.infra_error is None
                    and public.passed
                    and hidden.infra_error is None
                ),
                runner_state=runner_state,
                unrelated_files=unrelated_files,
                workspace_leaks=workspace_leaks,
            )
        return PairResult(
            pair_id=f"{task.id}-{run_index:03d}",
            task_id=task.id,
            run=run_index,
            provider=provider,
            model=model,
            arm_order=order,
            start_trees=start_trees,
            arms=arms,
            elapsed_s=time.monotonic() - pair_started,
        )
    finally:
        if artifact_dir.exists():
            _remove_tree(artifact_dir)
        for workspace in workspaces.values():
            if workspace.exists():
                _remove_tree(workspace)


def classify_outcome(arm: ArmResult | dict, mode: str | None = None) -> str:
    if isinstance(arm, ArmResult):
        return _classify_arm_outcome(arm)
    attempts = arm.get("attempts") or ()
    if any(
        (
            attempt.infra_error
            if isinstance(attempt, ProviderAttempt)
            else attempt.get("infra_error")
        )
        for attempt in attempts
    ):
        return "infra_error"
    if any(
        isinstance(arm.get(name), dict) and arm[name].get("infra_error")
        for name in ("public_test", "hidden_check")
    ):
        return "infra_error"
    completed = arm.get("completed")
    if completed is None:
        completed = arm.get("runner_exit", 0) == 0 if mode == "rig" else True
    hidden = arm.get("hidden_check")
    public = arm.get("public_test")
    if "public_test" in arm and (
        not isinstance(public, dict) or not isinstance(public.get("passed"), bool)
    ):
        return "invalid"
    hidden_passed = (
        hidden.get("passed") if isinstance(hidden, dict) else arm.get("spec_check") == "PASS"
    )
    if "public_test" in arm:
        completed = completed and public["passed"]
    if completed and hidden_passed:
        return "clean_pass"
    if completed and not hidden_passed:
        return "silent_defect"
    if hidden_passed:
        return "safe_stop"
    return "stopped_wrong"


def run_benchmark(
    tasks: list[BenchTask],
    provider: str,
    model: str | None,
    runs: int,
    options: Mapping[str, object] | None = None,
) -> dict:
    model = resolve_pair_model(provider, model, options)
    task_results = []
    all_pairs = []
    for task in tasks:
        pairs = [
            run_pair(task, run_index, provider, model, options) for run_index in range(1, runs + 1)
        ]
        all_pairs.extend(pairs)
        task_results.append(
            {
                "task_id": task.id,
                "language": task.language,
                "difficulty": task.difficulty,
                "risk_domains": list(task.risk_domains),
                "runs": [pair.to_dict() for pair in pairs],
            }
        )
    score = score_provider(all_pairs)
    return {
        "schema_version": 2,
        "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "rig_wb_version": __version__,
        "recipe": "adaptive-bugfix",
        "recipe_version": 1,
        "corpus_version": 1,
        "provider": provider,
        "model": model,
        "provider_version": _capture_provider_version(provider, options),
        "runs_per_task": runs,
        "score": asdict(score),
        "tasks": task_results,
    }


def _capture_provider_version(
    provider: str,
    options: Mapping[str, object] | None,
) -> str:
    settings = options or {}
    configured = settings.get("provider_version")
    if configured:
        return str(configured)
    if provider == "mock":
        return "built-in mock"
    if provider in {"claude", "codex"}:
        try:
            completed = subprocess.run(
                [provider, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return "unavailable"
        detail = (completed.stdout or completed.stderr or "").strip().splitlines()
        return detail[0] if completed.returncode == 0 and detail else "unavailable"
    base_url = settings.get("base_url")
    return f"endpoint {base_url}" if base_url else f"{provider} default endpoint"


def cmd_bench(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="rig-wb bench",
        description="Run fair paired bare/adaptive benchmark arms.",
    )
    parser.add_argument("--corpus", type=pathlib.Path, help="benchmark task corpus root")
    parser.add_argument("--tasks", nargs="+", default=["all"], help="task ids or all")
    parser.add_argument(
        "--provider",
        choices=["claude", "codex", "ollama", "lmstudio", "mock"],
        default="mock",
    )
    parser.add_argument("--model")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--max-steps", type=int, default=14)
    parser.add_argument("--provider-timeout", type=float, default=600)
    parser.add_argument("--rig-timeout", type=float, default=1800)
    parser.add_argument("--check-timeout", type=float, default=60)
    parser.add_argument("--base-url")
    parser.add_argument("--allow-headless-in-cc", action="store_true")
    parser.add_argument(
        "--mock-scenario",
        choices=["success", "timeout", "malformed", "partial"],
        default="success",
    )
    parser.add_argument("--out", type=pathlib.Path)
    parser.add_argument("--html", type=pathlib.Path)
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be at least 1")

    available = load_tasks(args.corpus)
    requested = list(available) if "all" in args.tasks else args.tasks
    unknown = sorted(set(requested) - set(available))
    if unknown:
        parser.error(f"unknown task id(s): {', '.join(unknown)}")
    selected = [available[task_id] for task_id in requested]
    options = {
        "max_steps": args.max_steps,
        "timeout_s": args.provider_timeout,
        "rig_timeout_s": args.rig_timeout,
        "check_timeout_s": args.check_timeout,
        "base_url": args.base_url,
        "allow_headless_in_cc": args.allow_headless_in_cc,
        "mock_scenario": args.mock_scenario,
    }
    summary = run_benchmark(selected, args.provider, args.model, args.runs, options)
    output = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(output, encoding="utf-8")
        print(f"Wrote: {args.out}")
    else:
        print(output)
    if args.html:
        args.html.write_text(render_html(summary), encoding="utf-8")
        print(f"HTML: {args.html}")


def _render_html(summary: dict) -> str:
    return render_html(summary)
