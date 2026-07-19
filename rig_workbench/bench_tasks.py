"""Load and validate repository-shaped benchmark tasks."""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class BenchTask:
    id: str
    language: str
    difficulty: str
    risk_domains: tuple[str, ...]
    goal: str
    test_command: tuple[str, ...]
    hidden_command: tuple[str, ...]
    root: pathlib.Path
    expected_files: tuple[str, ...]


@dataclass(frozen=True)
class CheckResult:
    variant: str
    public_returncode: int
    hidden_returncode: int
    public_stdout: str
    public_stderr: str
    hidden_stdout: str
    hidden_stderr: str

    @property
    def public_passed(self) -> bool:
        return self.public_returncode == 0

    @property
    def hidden_passed(self) -> bool:
        return self.hidden_returncode == 0

    @property
    def public_output(self) -> str:
        return self.public_stdout + self.public_stderr

    @property
    def hidden_output(self) -> str:
        return self.hidden_stdout + self.hidden_stderr


def _require_command(data: dict[str, object], field: str, source: pathlib.Path) -> tuple[str, ...]:
    value = data.get(field)
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(part, str) and part for part in value)
    ):
        raise ValueError(f"{source}: {field} must be a non-empty string list")
    for part in value[1:]:
        candidate = pathlib.PurePosixPath(part.replace("\\", "/"))
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(f"{source}: {field} contains unsafe path {part!r}")
    return tuple(value)


def _require_relative_paths(
    data: dict[str, object],
    field: str,
    source: pathlib.Path,
) -> tuple[str, ...]:
    value = data.get(field)
    if not isinstance(value, list) or not value:
        raise ValueError(f"{source}: {field} must be a non-empty string list")
    paths: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ValueError(f"{source}: {field} must contain non-empty strings")
        candidate = pathlib.PurePosixPath(item.replace("\\", "/"))
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(f"{source}: {field} contains unsafe path {item!r}")
        paths.append(item)
    return tuple(paths)


def _load_task(metadata_path: pathlib.Path) -> BenchTask:
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{metadata_path}: unsupported schema_version")

    task_root = metadata_path.parent
    repo = task_root / "repo"
    expected_files = _require_relative_paths(data, "expected_files", metadata_path)
    test_command = _require_command(data, "test_command", metadata_path)
    hidden_command = _require_command(data, "hidden_command", metadata_path)

    for required in (repo, task_root / "canonical", task_root / "narrow"):
        if not required.is_dir():
            raise ValueError(f"{metadata_path}: missing directory {required.name}")
    if not (task_root / "hidden_check.py").is_file():
        raise ValueError(f"{metadata_path}: missing hidden_check.py")
    for relative in expected_files:
        if not (repo / relative).is_file():
            raise ValueError(f"{metadata_path}: missing expected file {relative}")

    hidden_names = {
        pathlib.PurePosixPath(part.replace("\\", "/")).name
        for part in hidden_command[1:]
        if not part.startswith("-")
    }
    if any(path.is_file() and path.name in hidden_names for path in repo.rglob("*")):
        raise ValueError(f"{metadata_path}: hidden check present under repo")

    string_fields = ("id", "language", "difficulty", "goal")
    if any(not isinstance(data.get(field), str) or not data[field] for field in string_fields):
        raise ValueError(f"{metadata_path}: id, language, difficulty, and goal are required")
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", data["id"]) is None:
        raise ValueError(f"{metadata_path}: id must be a lowercase slug")
    risk_domains = data.get("risk_domains")
    if (
        not isinstance(risk_domains, list)
        or not risk_domains
        or not all(isinstance(domain, str) and domain for domain in risk_domains)
    ):
        raise ValueError(f"{metadata_path}: risk_domains must be a non-empty string list")

    return BenchTask(
        id=data["id"],
        language=data["language"],
        difficulty=data["difficulty"],
        risk_domains=tuple(risk_domains),
        goal=data["goal"],
        test_command=test_command,
        hidden_command=hidden_command,
        root=task_root,
        expected_files=expected_files,
    )


def load_tasks(root: pathlib.Path | None = None) -> dict[str, BenchTask]:
    tasks_root = root or pathlib.Path(__file__).parents[1] / "benchmarks" / "tasks"
    tasks: dict[str, BenchTask] = {}
    for metadata_path in sorted(tasks_root.glob("*/task.json")):
        task = _load_task(metadata_path)
        if task.id in tasks:
            raise ValueError(f"{metadata_path}: duplicate task id {task.id!r}")
        tasks[task.id] = task
    return tasks


def _remove_tree(path: pathlib.Path) -> None:
    def make_writable(function, blocked, _error):
        os.chmod(blocked, stat.S_IWRITE)
        function(blocked)

    shutil.rmtree(path, onerror=make_writable)


def materialize(task: BenchTask) -> pathlib.Path:
    workspace = pathlib.Path(tempfile.mkdtemp(prefix=f"rig-bench-{task.id}-"))
    try:
        shutil.copytree(task.root / "repo", workspace, dirs_exist_ok=True)
        subprocess.run(
            ["git", "init", "-q"],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "add", "."],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=rig-bench",
                "-c",
                "user.email=bench@rig.local",
                "commit",
                "-q",
                "-m",
                "benchmark starting state",
            ],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        _remove_tree(workspace)
        raise
    return workspace


def _command(command: tuple[str, ...]) -> list[str]:
    if command[0].casefold() in {"python", "python3", "python.exe"}:
        return [sys.executable, *command[1:]]
    return list(command)


def run_variant_contract(task: BenchTask, variant: str) -> CheckResult:
    if variant not in {"original", "narrow", "canonical"}:
        raise ValueError(f"unknown benchmark variant {variant!r}")

    workspace = materialize(task)
    hidden_root = pathlib.Path(tempfile.mkdtemp(prefix=f"rig-bench-hidden-{task.id}-"))
    try:
        if variant != "original":
            shutil.copytree(task.root / variant, workspace, dirs_exist_ok=True)

        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"
        public = subprocess.run(
            _command(task.test_command),
            cwd=workspace,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )

        shutil.copy2(task.root / "hidden_check.py", hidden_root / "hidden_check.py")
        existing_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = os.pathsep.join(
            part for part in (str(workspace), existing_pythonpath) if part
        )
        hidden = subprocess.run(
            [*_command(task.hidden_command), str(workspace)],
            cwd=hidden_root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return CheckResult(
            variant=variant,
            public_returncode=public.returncode,
            hidden_returncode=hidden.returncode,
            public_stdout=public.stdout,
            public_stderr=public.stderr,
            hidden_stdout=hidden.stdout,
            hidden_stderr=hidden.stderr,
        )
    finally:
        try:
            _remove_tree(workspace)
        finally:
            _remove_tree(hidden_root)
