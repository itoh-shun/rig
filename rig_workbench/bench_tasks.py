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
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator


SCHEMA_VERSION = 1
# Direct, unflagged TypeScript execution requires Node.js 22.18.0 or newer.
MIN_NODE_VERSION = (22, 18, 0)
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


def _before_copy_entry(_path: pathlib.Path) -> None:
    """Test hook for injecting changes between validation and traversal."""


def _copy_metadata(path: pathlib.Path) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ValueError(f"{path}: source entry changed during copy") from error
    _validate_copy_metadata(path, metadata)
    return metadata


def _validate_copy_metadata(path: pathlib.Path, metadata: os.stat_result) -> None:
    attributes = getattr(metadata, "st_file_attributes", 0)
    if stat.S_ISLNK(metadata.st_mode) or attributes & _REPARSE_POINT:
        raise ValueError(f"{path}: link-like source entry is forbidden")
    if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
        raise ValueError(f"{path}: source entry must be a regular file or directory")


def _same_entry(actual: os.stat_result, expected: os.stat_result) -> bool:
    return actual.st_dev == expected.st_dev and actual.st_ino == expected.st_ino


def _open_windows_handle(
    path: pathlib.Path,
    *,
    directory: bool,
    readable: bool,
) -> int:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE

    generic_read = 0x80000000
    share_read_write = 0x00000001 | 0x00000002
    open_existing = 3
    open_reparse_point = 0x00200000
    backup_semantics = 0x02000000
    flags = open_reparse_point | (backup_semantics if directory else 0)
    handle = create_file(
        str(path),
        generic_read if readable else 0,
        share_read_write,
        None,
        open_existing,
        flags,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        raise OSError(error, os.strerror(error), str(path))
    return int(handle)


def _close_windows_handle(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    close_handle(handle)


@contextmanager
def _guard_source_directory(path: pathlib.Path) -> Iterator[None]:
    if os.name != "nt":
        yield
        return

    try:
        handle = _open_windows_handle(path, directory=True, readable=False)
    except OSError as error:
        raise ValueError(f"{path}: could not safely open source directory") from error
    try:
        _copy_metadata(path)
        yield
    finally:
        _close_windows_handle(handle)


def _open_source_file(path: pathlib.Path) -> int:
    if os.name != "nt":
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        return os.open(path, flags)

    handle = _open_windows_handle(path, directory=False, readable=True)
    try:
        _copy_metadata(path)
        import msvcrt

        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        _close_windows_handle(handle)
        raise


def _ensure_copy_directory(path: pathlib.Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        try:
            path.mkdir()
        except FileExistsError:
            _ensure_copy_directory(path)
        return

    attributes = getattr(metadata, "st_file_attributes", 0)
    if stat.S_ISLNK(metadata.st_mode) or attributes & _REPARSE_POINT:
        raise ValueError(f"{path}: link-like destination entry is forbidden")
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"{path}: destination entry must be a directory")


def _copy_regular_file(
    source: pathlib.Path,
    destination: pathlib.Path,
    expected_metadata: os.stat_result,
) -> None:
    try:
        source_fd = _open_source_file(source)
    except OSError as error:
        raise ValueError(f"{source}: could not safely open source file") from error
    _copy_open_regular_file(source_fd, source, destination, expected_metadata)


def _copy_open_regular_file(
    source_fd: int,
    source: pathlib.Path,
    destination: pathlib.Path,
    expected_metadata: os.stat_result,
) -> None:

    temporary_path: pathlib.Path | None = None
    try:
        opened_metadata = os.fstat(source_fd)
        if not stat.S_ISREG(opened_metadata.st_mode) or not _same_entry(
            opened_metadata, expected_metadata
        ):
            raise ValueError(f"{source}: source entry changed during copy")

        temporary_fd, temporary_name = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.rig-copy-",
        )
        temporary_path = pathlib.Path(temporary_name)
        with os.fdopen(source_fd, "rb") as source_file:
            source_fd = -1
            with os.fdopen(temporary_fd, "wb") as destination_file:
                shutil.copyfileobj(source_file, destination_file)

        try:
            destination_metadata = destination.lstat()
        except FileNotFoundError:
            pass
        else:
            attributes = getattr(destination_metadata, "st_file_attributes", 0)
            if stat.S_ISLNK(destination_metadata.st_mode) or attributes & _REPARSE_POINT:
                raise ValueError(f"{destination}: link-like destination entry is forbidden")
            if not stat.S_ISREG(destination_metadata.st_mode):
                raise ValueError(f"{destination}: destination entry must be a regular file")

        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _copy_directory_by_path(source: pathlib.Path, destination: pathlib.Path) -> None:
    with _guard_source_directory(source):
        source_metadata = _copy_metadata(source)
        if not stat.S_ISDIR(source_metadata.st_mode):
            raise ValueError(f"{source}: source root must be a directory")
        _ensure_copy_directory(destination)

        try:
            with os.scandir(source) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError as error:
            raise ValueError(f"{source}: could not safely traverse source directory") from error

        for entry in entries:
            source_entry = source / entry.name
            destination_entry = destination / entry.name
            _before_copy_entry(source_entry)
            _validate_copy_name(source_entry, entry.name)

            entry_metadata = _copy_metadata(source_entry)
            if stat.S_ISDIR(entry_metadata.st_mode):
                _copy_directory_by_path(source_entry, destination_entry)
            else:
                _copy_regular_file(source_entry, destination_entry, entry_metadata)


def _validate_copy_name(path: pathlib.Path, name: str) -> None:
    if name.casefold() == ".git":
        raise ValueError(f"{path}: Git metadata is forbidden")
    if _HIDDEN_ARTIFACT.fullmatch(name):
        raise ValueError(f"{path}: reserved hidden check artifact is forbidden")


def _directory_open_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )


def _copy_directory_from_fd(
    source_fd: int,
    source: pathlib.Path,
    destination: pathlib.Path,
    expected_metadata: os.stat_result | None = None,
) -> None:
    opened_metadata = os.fstat(source_fd)
    if (
        not stat.S_ISDIR(opened_metadata.st_mode)
        or expected_metadata is not None
        and not _same_entry(opened_metadata, expected_metadata)
    ):
        raise ValueError(f"{source}: source directory changed during copy")
    _ensure_copy_directory(destination)

    try:
        with os.scandir(source_fd) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name)
    except OSError as error:
        raise ValueError(f"{source}: could not safely traverse source directory") from error

    for entry in entries:
        source_entry = source / entry.name
        destination_entry = destination / entry.name
        _before_copy_entry(source_entry)
        _validate_copy_name(source_entry, entry.name)

        try:
            entry_metadata = os.stat(
                entry.name,
                dir_fd=source_fd,
                follow_symlinks=False,
            )
        except OSError as error:
            raise ValueError(f"{source_entry}: source entry changed during copy") from error
        _validate_copy_metadata(source_entry, entry_metadata)
        if stat.S_ISDIR(entry_metadata.st_mode):
            try:
                child_fd = os.open(
                    entry.name,
                    _directory_open_flags(),
                    dir_fd=source_fd,
                )
            except OSError as error:
                raise ValueError(
                    f"{source_entry}: could not safely open source directory"
                ) from error
            try:
                _copy_directory_from_fd(
                    child_fd,
                    source_entry,
                    destination_entry,
                    entry_metadata,
                )
            finally:
                os.close(child_fd)
        else:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                child_fd = os.open(entry.name, flags, dir_fd=source_fd)
            except OSError as error:
                raise ValueError(f"{source_entry}: could not safely open source file") from error
            _copy_open_regular_file(
                child_fd,
                source_entry,
                destination_entry,
                entry_metadata,
            )


def _copy_directory(source: pathlib.Path, destination: pathlib.Path) -> None:
    safe_fd_traversal = (
        os.name != "nt"
        and os.open in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and os.scandir in os.supports_fd
        and hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
    )
    if not safe_fd_traversal:
        _copy_directory_by_path(source, destination)
        return

    try:
        source_fd = os.open(source, _directory_open_flags())
    except OSError as error:
        raise ValueError(f"{source}: could not safely open source directory") from error
    try:
        _copy_directory_from_fd(source_fd, source, destination)
    finally:
        os.close(source_fd)


def _copy_source(source: pathlib.Path, destination: pathlib.Path) -> None:
    _validate_source_tree(source, str(source))
    _copy_directory(source, destination)


def materialize(task: BenchTask) -> pathlib.Path:
    source = task.root / "repo"
    _validate_source_tree(source, str(source))
    workspace = pathlib.Path(tempfile.mkdtemp(prefix=f"rig-bench-{task.id}-"))
    try:
        _copy_source(source, workspace)
        _validate_source_tree(workspace, str(workspace))
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


def _installed_node_version() -> str:
    try:
        completed = subprocess.run(
            ["node", "--version"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("Node.js is required for TypeScript benchmark tasks") from error
    return completed.stdout.strip()


def _require_supported_node() -> None:
    version_text = _installed_node_version()
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", version_text)
    if match is None:
        raise RuntimeError(f"could not parse Node.js version {version_text!r}")
    installed = tuple(int(part) for part in match.groups())
    if installed < MIN_NODE_VERSION:
        minimum = ".".join(str(part) for part in MIN_NODE_VERSION)
        raise RuntimeError(
            f"Node >={minimum} is required for direct TypeScript execution; found {version_text}"
        )


def run_variant_contract(task: BenchTask, variant: str) -> CheckResult:
    if variant not in {"original", "narrow", "canonical"}:
        raise ValueError(f"unknown benchmark variant {variant!r}")
    if task.language.casefold() == "typescript":
        _require_supported_node()

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
