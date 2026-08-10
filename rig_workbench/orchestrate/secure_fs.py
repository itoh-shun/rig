"""Descriptor-relative, link-safe persistence for sensitive runtime artifacts."""

from __future__ import annotations

import fcntl
import os
import pathlib
import secrets
import stat


_DIR_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_FILE_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_FILE_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _verify_directory(descriptor: int, *, strict: bool) -> None:
    info = os.fstat(descriptor)
    if not stat.S_ISDIR(info.st_mode):
        raise OSError("secure runtime directory is not a directory")
    if strict and (
        info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise OSError("secure runtime directory must be owned by the caller with mode 0700")


def open_directory(path: pathlib.Path, *, create: bool, strict: bool = True) -> int:
    """Open an absolute directory without following any path component links."""
    absolute = pathlib.Path(path).absolute()
    descriptor = os.open("/", _DIR_FLAGS)
    try:
        parts = absolute.parts[1:]
        for index, component in enumerate(parts):
            final = index == len(parts) - 1
            try:
                child = os.open(component, _DIR_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(component, 0o700, dir_fd=descriptor)
                child = os.open(component, _DIR_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            _verify_directory(descriptor, strict=strict and final)
        _verify_directory(descriptor, strict=strict)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _verify_file(info: os.stat_result) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise OSError(
            "secure runtime file must be caller-owned regular mode 0600 with one link"
        )


def prepare_output_target(path: pathlib.Path) -> None:
    """Create/verify the private parent and reject an unsafe existing target."""
    path = pathlib.Path(path).absolute()
    if not path.name or path.name in (".", ".."):
        raise OSError("secure runtime output target must name a file")
    directory_fd = open_directory(path.parent, create=True, strict=True)
    try:
        try:
            existing = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        _verify_file(existing)
    finally:
        os.close(directory_fd)


def acquire_output_lock(path: pathlib.Path) -> int:
    """Acquire a private, nonblocking whole-run lock adjacent to the state file."""
    path = pathlib.Path(path).absolute()
    directory_fd = open_directory(path.parent, create=True, strict=True)
    lock_name = f".{path.name}.lock"
    descriptor = -1
    try:
        try:
            descriptor = os.open(
                lock_name,
                os.O_RDWR | _FILE_CLOEXEC | _FILE_NOFOLLOW,
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            descriptor = os.open(
                lock_name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | _FILE_CLOEXEC | _FILE_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
            os.fchmod(descriptor, 0o600)
        _verify_file(os.fstat(descriptor))
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise OSError("secure runtime output is already locked by another run") from error
        return descriptor
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    finally:
        os.close(directory_fd)


def release_output_lock(descriptor: int | None) -> None:
    if descriptor is None:
        return
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def atomic_write_bytes(path: pathlib.Path, payload: bytes) -> None:
    """fsync a private temp and atomically rename it over a verified target."""
    path = pathlib.Path(path).absolute()
    directory_fd = open_directory(path.parent, create=True, strict=True)
    temp_name = f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    temp_fd = -1
    try:
        try:
            existing = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            _verify_file(existing)
        temp_fd = os.open(
            temp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_CLOEXEC | _FILE_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        os.fchmod(temp_fd, 0o600)
        _verify_file(os.fstat(temp_fd))
        view = memoryview(payload)
        while view:
            written = os.write(temp_fd, view)
            if written <= 0:
                raise OSError("short secure runtime write")
            view = view[written:]
        os.fsync(temp_fd)
        os.close(temp_fd)
        temp_fd = -1
        os.replace(
            temp_name,
            path.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    finally:
        if temp_fd >= 0:
            os.close(temp_fd)
        try:
            os.unlink(temp_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)


def read_bytes(path: pathlib.Path) -> bytes:
    """Read a verified sensitive file from its verified directory FD."""
    path = pathlib.Path(path).absolute()
    directory_fd = open_directory(path.parent, create=False, strict=True)
    descriptor = -1
    try:
        descriptor = os.open(
            path.name,
            os.O_RDONLY | _FILE_CLOEXEC | _FILE_NOFOLLOW,
            dir_fd=directory_fd,
        )
        _verify_file(os.fstat(descriptor))
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory_fd)


def atomic_append_line(path: pathlib.Path, line: bytes) -> None:
    """Append via read+atomic-replace so no unverified inode is opened with O_APPEND."""
    try:
        current = read_bytes(path)
    except FileNotFoundError:
        current = b""
    atomic_write_bytes(path, current + line)
