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
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_HIDDEN_ARTIFACT = re.compile(
    r"^\.?hidden(?:[_-](?:check|checks|spec|specs|test|tests))(?:[._-].*)?$",
    re.IGNORECASE,
)


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
    public_stdout: str | None
    public_stderr: str | None
    hidden_stdout: str | None
    hidden_stderr: str | None

    @property
    def public_passed(self) -> bool:
        return self.public_returncode == 0

    @property
    def hidden_passed(self) -> bool:
        return self.hidden_returncode == 0

    @property
    def public_output(self) -> str:
        return (self.public_stdout or "") + (self.public_stderr or "")

    @property
    def hidden_output(self) -> str:
        return (self.hidden_stdout or "") + (self.hidden_stderr or "")


def _is_link_like(path: pathlib.Path | os.DirEntry[str]) -> bool:
    if path.is_symlink():
        return True
    metadata = path.stat(follow_symlinks=False) if isinstance(path, os.DirEntry) else path.lstat()
    attributes = getattr(metadata, "st_file_attributes", 0)
    return bool(attributes & _REPARSE_POINT)


def _validate_source_tree(root: pathlib.Path, label: str) -> None:
    if _is_link_like(root):
        raise ValueError(f"{label}: source root is a link")

    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name.casefold() == ".git":
                    raise ValueError(f"{label}: Git metadata is forbidden: {entry.path}")
                if _HIDDEN_ARTIFACT.fullmatch(entry.name):
                    raise ValueError(
                        f"{label}: reserved hidden check artifact is forbidden: {entry.path}"
                    )
                if _is_link_like(entry):
                    raise ValueError(f"{label}: link-like source entry is forbidden: {entry.path}")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(pathlib.Path(entry.path))


def _validate_schema_path(
    value: str,
    field: str,
    source: pathlib.Path,
    root: pathlib.Path,
) -> None:
    normalized = value.replace("\\", "/")
    posix_path = pathlib.PurePosixPath(normalized)
    windows_path = pathlib.PureWindowsPath(value)
    unsafe = (
        "\x00" in value
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or normalized.startswith("//")
        or ".." in posix_path.parts
    )
    if not unsafe:
        root_resolved = root.resolve(strict=False)
        candidate = root.joinpath(*posix_path.parts).resolve(strict=False)
        unsafe = not candidate.is_relative_to(root_resolved)
    if unsafe:
        raise ValueError(f"{source}: {field} contains unsafe path {value!r}")


def _require_command(
    data: dict[str, object],
    field: str,
    source: pathlib.Path,
    root: pathlib.Path,
) -> tuple[str, ...]:
    value = data.get(field)
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(part, str) and part for part in value)
    ):
        raise ValueError(f"{source}: {field} must be a non-empty string list")
    for part in value:
        _validate_schema_path(part, field, source, root)
    return tuple(value)


def _require_relative_paths(
    data: dict[str, object],
    field: str,
    source: pathlib.Path,
    root: pathlib.Path,
) -> tuple[str, ...]:
    value = data.get(field)
    if not isinstance(value, list) or not value:
        raise ValueError(f"{source}: {field} must be a non-empty string list")
    paths: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ValueError(f"{source}: {field} must contain non-empty strings")
        _validate_schema_path(item, field, source, root)
        paths.append(item)
    return tuple(paths)


def _load_task(metadata_path: pathlib.Path) -> BenchTask:
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{metadata_path}: unsupported schema_version")

    task_root = metadata_path.parent
    repo = task_root / "repo"
    expected_files = _require_relative_paths(data, "expected_files", metadata_path, repo)
    test_command = _require_command(data, "test_command", metadata_path, repo)
    hidden_command = _require_command(data, "hidden_command", metadata_path, task_root)

    for required in (repo, task_root / "canonical", task_root / "narrow"):
        if not required.is_dir():
            raise ValueError(f"{metadata_path}: missing directory {required.name}")
        _validate_source_tree(required, f"{metadata_path}:{required.name}")
    if not (task_root / "hidden_check.py").is_file():
        raise ValueError(f"{metadata_path}: missing hidden_check.py")
    for relative in expected_files:
        relative_path = pathlib.PurePosixPath(relative.replace("\\", "/"))
        if not repo.joinpath(*relative_path.parts).is_file():
            raise ValueError(f"{metadata_path}: missing expected file {relative}")

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


def _copy_source(source: pathlib.Path, destination: pathlib.Path) -> None:
    _validate_source_tree(source, str(source))

    def ignore_git(_directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name.casefold() == ".git"}

    shutil.copytree(source, destination, dirs_exist_ok=True, ignore=ignore_git)


def materialize(task: BenchTask) -> pathlib.Path:
    source = task.root / "repo"
    _validate_source_tree(source, str(source))
    workspace = pathlib.Path(tempfile.mkdtemp(prefix=f"rig-bench-{task.id}-"))
    try:
        _copy_source(source, workspace)
        subprocess.run(
            ["git", "init", "-q"],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        subprocess.run(
            ["git", "add", "."],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
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
            encoding="utf-8",
            errors="replace",
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

    variant_source = task.root / variant if variant != "original" else None
    if variant_source is not None:
        _validate_source_tree(variant_source, str(variant_source))
    workspace = materialize(task)
    hidden_root = pathlib.Path(tempfile.mkdtemp(prefix=f"rig-bench-hidden-{task.id}-"))
    try:
        if variant_source is not None:
            _copy_source(variant_source, workspace)

        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"
        public = subprocess.run(
            _command(task.test_command),
            cwd=workspace,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
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
            encoding="utf-8",
            errors="replace",
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
