"""Fair paired benchmark runner for bare agents and adaptive rig runs."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import html
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from typing import Mapping

from . import __version__
from .bench_providers import ProviderAttempt, run_bare, run_rig
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
        )
    except subprocess.TimeoutExpired as error:
        return CommandResult(
            command=command,
            returncode=124,
            stdout=_stream_text(error.stdout or error.output),
            stderr=_stream_text(error.stderr),
            elapsed_s=time.monotonic() - started,
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
        _require_supported_node()
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


def _read_runner_state(workspace: pathlib.Path) -> dict | None:
    path = workspace / "run-state.json"
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
        invocations=1,
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
    settings = options or {}
    order = planned_arm_order(run_index)
    pair_started = time.monotonic()
    workspaces: dict[str, pathlib.Path] = {}
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
            try:
                attempt = (
                    run_bare(task, provider, model, workspace, settings)
                    if name == "bare"
                    else run_rig(task, provider, model, workspace, settings)
                )
            except Exception as error:
                attempt = _failed_attempt(provider, model, arm_started, error)

            runner_state = _read_runner_state(workspace) if name == "rig" else None
            (workspace / "run-state.json").unlink(missing_ok=True)
            git_status, changed_files = _git_evidence(workspace)
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
                completed=attempt.infra_error is None and attempt.returncode == 0,
                runner_state=runner_state,
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
        for workspace in workspaces.values():
            if workspace.exists():
                _remove_tree(workspace)


def classify_outcome(arm: ArmResult | dict, mode: str | None = None) -> str:
    if isinstance(arm, ArmResult):
        attempts = arm.attempts
        completed = arm.completed
        hidden_passed = arm.hidden_check.passed
    else:
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
        completed = arm.get("completed")
        if completed is None:
            completed = arm.get("runner_exit", 0) == 0 if mode == "rig" else True
        hidden = arm.get("hidden_check")
        hidden_passed = (
            hidden.get("passed") if isinstance(hidden, dict) else arm.get("spec_check") == "PASS"
        )
    if any(attempt.infra_error for attempt in attempts if isinstance(attempt, ProviderAttempt)):
        return "infra_error"
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
    task_results = []
    for task in tasks:
        pairs = [
            run_pair(task, run_index, provider, model, options) for run_index in range(1, runs + 1)
        ]
        task_results.append(
            {
                "task_id": task.id,
                "language": task.language,
                "difficulty": task.difficulty,
                "risk_domains": list(task.risk_domains),
                "runs": [pair.to_dict() for pair in pairs],
            }
        )
    return {
        "schema_version": 2,
        "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "rig_wb_version": __version__,
        "recipe": "adaptive-bugfix",
        "recipe_version": 1,
        "corpus_version": 1,
        "provider": provider,
        "model": model,
        "runs_per_task": runs,
        "tasks": task_results,
    }


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
        args.html.write_text(_render_html(summary), encoding="utf-8")
        print(f"HTML: {args.html}")


def _render_html(summary: dict) -> str:
    provider = str(summary.get("provider", "unknown"))
    banner = "<strong>WIRING ONLY</strong>" if provider == "mock" else ""
    rows = []
    for task in summary.get("tasks", []):
        for pair in task.get("runs", []):
            arms = pair.get("arms", pair.get("modes", {}))
            bare = classify_outcome(arms.get("bare", {}), "bare")
            rig = classify_outcome(arms.get("rig", {}), "rig")
            rows.append(
                "<tr>"
                f"<td>{html.escape(str(task.get('task_id', '')))}</td>"
                f"<td>{html.escape(str(pair.get('pair_id', pair.get('run', ''))))}</td>"
                f"<td>{html.escape(bare)}</td><td>{html.escape(rig)}</td>"
                "</tr>"
            )
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>rig-wb paired benchmark</title>"
        "<style>body{font:16px Georgia,serif;margin:2rem;max-width:70rem}"
        "table{border-collapse:collapse;width:100%}th,td{border:1px solid #bbb;padding:.5rem}"
        "strong{color:#9b2c2c}</style></head><body>"
        f"<h1>Paired benchmark</h1><p>provider={html.escape(provider)} {banner}</p>"
        "<table><thead><tr><th>task</th><th>pair</th><th>bare</th><th>rig</th></tr>"
        f"</thead><tbody>{''.join(rows)}</tbody></table></body></html>"
    )
