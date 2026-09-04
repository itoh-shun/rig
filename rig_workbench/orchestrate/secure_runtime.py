"""Fail-closed process and filesystem primitives for independent provider runs."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import errno
import fcntl
import hashlib
import json
import os
import pathlib
import platform
import re
import shlex
import stat
import subprocess
import sys
import tempfile
from typing import Callable

from .secure_fs import read_bytes as read_secure_bytes


FIXED_PROVIDER_PATH = "/usr/bin:/bin"
SECURE_PROVIDER_NAMES = frozenset({"claude", "codex"})
_COMMON_ENV = frozenset({"LANG", "LC_ALL", "LC_CTYPE", "TERM", "TMPDIR"})
_PROVIDER_ENV = {
    "claude": _COMMON_ENV | {"ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"},
    "codex": _COMMON_ENV | {"OPENAI_API_KEY"},
}


@dataclass(frozen=True)
class PrerequisiteCheck:
    name: str
    available: bool | None
    detail: str


class SecureRuntimeError(ValueError):
    """A secure independent run could not establish its trust boundary."""

    def __init__(
        self,
        message: str,
        *,
        checks: tuple[PrerequisiteCheck, ...] = (),
        remediation: str | None = None,
    ) -> None:
        super().__init__(message)
        self.checks = checks
        self.executable = sys.executable
        self.version = sys.version
        self.remediation = remediation


@dataclass(frozen=True)
class SecureLauncher:
    role: str
    provider: str
    launcher_fds: tuple[int, ...]
    launcher_hashes: tuple[str, ...]
    interpreter_args: tuple[str, ...] = ()

    def close(self) -> None:
        for descriptor in self.launcher_fds:
            try:
                os.close(descriptor)
            except OSError:
                pass


#: The Japanese-writing lane, by recipe name. Both recipes drive the same sealed
#: provider boundary, material injection, review category and artifact stdout; until
#: #580 the revision recipe opted in by declaring the *other* recipe's `name`, which
#: the pack layer allowed and core's name-matches-filename rule does not. Membership
#: is declared here because this is the only module `commands`, `runstate` and
#: `providers` all import without a cycle.
JAPANESE_WRITING_RECIPES = ("japanese-writing", "japanese-writing-revision")


def requires_secure_runtime(recipe_name: str, steps: list[dict]) -> bool:
    """Whether the recipe explicitly opts into the sealed provider boundary."""
    return any(
        "secure-provider-execution" in (step.get("policies") or [])
        for step in steps
    )


def _sha256_fd(descriptor: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while True:
        chunk = os.pread(descriptor, 1024 * 1024, offset)
        if not chunk:
            return digest.hexdigest()
        digest.update(chunk)
        offset += len(chunk)


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short sealed executable write")
        view = view[written:]


_MFD_CLOEXEC = 0x0001
_MFD_ALLOW_SEALING = 0x0002
_LINUX_FCNTL_SEAL_CONSTANTS = {
    "F_ADD_SEALS": 1033,
    "F_GET_SEALS": 1034,
    "F_SEAL_SEAL": 0x0001,
    "F_SEAL_SHRINK": 0x0002,
    "F_SEAL_GROW": 0x0004,
    "F_SEAL_WRITE": 0x0008,
}
_MEMFD_SYSCALLS = {
    "x86_64": 319,
    "amd64": 319,
    "aarch64": 279,
    "arm64": 279,
}


def _call_libc_memfd(function, *args: object) -> int:
    function.restype = ctypes.c_int
    ctypes.set_errno(0)
    descriptor = function(*args)
    if descriptor < 0:
        error_number = ctypes.get_errno() or errno.ENOSYS
        raise OSError(error_number, os.strerror(error_number))
    return descriptor


def _create_memfd(name: str) -> tuple[int, tuple[PrerequisiteCheck, ...]]:
    flags = _MFD_CLOEXEC | _MFD_ALLOW_SEALING
    os_create = getattr(os, "memfd_create", None)
    if callable(os_create):
        try:
            descriptor = os_create(name, flags)
        except OSError as error:
            checks = [PrerequisiteCheck(
                "interpreter os.memfd_create", False, f"call failed: {error}"
            )]
        else:
            return descriptor, (
                PrerequisiteCheck("interpreter os.memfd_create", True, "call succeeded"),
                PrerequisiteCheck(
                    "libc memfd_create", None, "not inspected; interpreter wrapper succeeded"
                ),
                PrerequisiteCheck(
                    "direct memfd_create syscall",
                    None,
                    "not inspected; interpreter wrapper succeeded",
                ),
            )
    else:
        checks = [PrerequisiteCheck(
            "interpreter os.memfd_create", False, "attribute is absent"
        )]
    libc = ctypes.CDLL(None, use_errno=True)
    libc_create = getattr(libc, "memfd_create", None)
    if libc_create is not None:
        try:
            descriptor = _call_libc_memfd(
                libc_create, name.encode("utf-8"), ctypes.c_uint(flags)
            )
        except OSError as error:
            checks.append(
                PrerequisiteCheck("libc memfd_create", False, f"call failed: {error}")
            )
        else:
            checks.extend((
                PrerequisiteCheck("libc memfd_create", True, "call succeeded"),
                PrerequisiteCheck(
                    "direct memfd_create syscall", None, "not inspected; libc wrapper succeeded"
                ),
            ))
            return descriptor, tuple(checks)
    else:
        checks.append(PrerequisiteCheck("libc memfd_create", False, "symbol is absent"))

    architecture = platform.machine().lower()
    syscall_number = _MEMFD_SYSCALLS.get(architecture)
    syscall = getattr(libc, "syscall", None)
    if syscall_number is None:
        checks.append(PrerequisiteCheck(
            "direct memfd_create syscall",
            False,
            f"architecture {architecture or '<empty>'!r} is not allowlisted; syscall number not guessed",
        ))
    elif syscall is None:
        checks.append(PrerequisiteCheck(
            "direct memfd_create syscall", False, "libc syscall symbol is absent"
        ))
    else:
        try:
            descriptor = _call_libc_memfd(
                syscall,
                ctypes.c_long(syscall_number),
                name.encode("utf-8"),
                ctypes.c_uint(flags),
            )
        except OSError as error:
            checks.append(PrerequisiteCheck(
                "direct memfd_create syscall", False, f"call failed: {error}"
            ))
        else:
            checks.append(PrerequisiteCheck(
                "direct memfd_create syscall", True, "call succeeded"
            ))
            return descriptor, tuple(checks)

    raise SecureRuntimeError(
        "no safe memfd creation mechanism is available",
        checks=tuple(checks),
    )


def _seal_constants() -> tuple[dict[str, int], str]:
    names = tuple(_LINUX_FCNTL_SEAL_CONSTANTS)
    missing = [name for name in names if not hasattr(fcntl, name)]
    if not missing:
        return (
            {name: getattr(fcntl, name) for name in names},
            "interpreter fcntl sealing constants were used",
        )
    if sys.platform != "linux":
        raise SecureRuntimeError(
            "Python fcntl sealing constants are absent and Linux constants were not "
            f"used on platform {sys.platform!r}: {', '.join(missing)}"
        )
    return (
        {
            name: getattr(fcntl, name, fallback)
            for name, fallback in _LINUX_FCNTL_SEAL_CONSTANTS.items()
        },
        "module Linux sealing constants were used for absent interpreter constants: "
        f"{', '.join(missing)}",
    )


def _required_seals() -> int:
    constants, _detail = _seal_constants()
    return sum(
        constants[name]
        for name in ("F_SEAL_WRITE", "F_SEAL_GROW", "F_SEAL_SHRINK", "F_SEAL_SEAL")
    )


def _seal_descriptor(descriptor: int) -> str:
    constants, detail = _seal_constants()
    required = sum(
        constants[name]
        for name in ("F_SEAL_WRITE", "F_SEAL_GROW", "F_SEAL_SHRINK", "F_SEAL_SEAL")
    )
    fcntl.fcntl(descriptor, constants["F_ADD_SEALS"], required)
    if fcntl.fcntl(descriptor, constants["F_GET_SEALS"]) & required != required:
        raise SecureRuntimeError("verified executable descriptor could not be sealed")
    return detail


def _support_error(checks: list[PrerequisiteCheck]) -> SecureRuntimeError:
    remediation = (
        "run rig-wb with a system CPython that exposes os.memfd_create; "
        "also repair any separately failed kernel or /proc prerequisite named above"
    )
    rendered = "; ".join(
        f"{check.name}: "
        f"{'available' if check.available else 'unavailable' if check.available is False else 'not inspected'}"
        f" ({check.detail})"
        for check in checks
    )
    return SecureRuntimeError(
        "secure runtime prerequisites were rejected; "
        f"checks: {rendered}; sys.executable={sys.executable!r}; "
        f"sys.version={sys.version!r}; workaround: {remediation}",
        checks=tuple(checks),
        remediation=remediation,
    )


def check_secure_runtime_support() -> tuple[PrerequisiteCheck, ...]:
    """Fail early unless sealed memfd execution through procfs is available."""
    checks: list[PrerequisiteCheck]
    descriptor = None
    try:
        descriptor, creation_checks = _create_memfd("rig-secure-runtime-probe")
        checks = list(creation_checks)
    except SecureRuntimeError as error:
        checks = list(error.checks)
    if descriptor is None:
        checks.append(PrerequisiteCheck(
            "kernel memfd sealing", False, "not testable because memfd creation failed"
        ))
    else:
        try:
            seal_detail = _seal_descriptor(descriptor)
        except (OSError, SecureRuntimeError) as error:
            checks.append(PrerequisiteCheck(
                "kernel memfd sealing", False, f"F_ADD_SEALS/F_GET_SEALS failed: {error}"
            ))
        else:
            checks.append(PrerequisiteCheck(
                "kernel memfd sealing",
                True,
                f"all required seals were verified; {seal_detail}",
            ))
        finally:
            os.close(descriptor)
    proc_available = pathlib.Path("/proc/self/fd").is_dir()
    checks.append(PrerequisiteCheck(
        "/proc/self/fd",
        proc_available,
        "directory is available" if proc_available else "directory is absent",
    ))
    if any(check.available is False for check in checks[-2:]):
        raise _support_error(checks)
    return tuple(checks)


def _sealed_copy(source: int, role: str, kind: str) -> int:
    if not pathlib.Path("/proc/self/fd").is_dir():
        raise _support_error([
            PrerequisiteCheck("/proc/self/fd", False, "directory is absent")
        ])
    descriptor, _checks = _create_memfd(f"rig-{role}-{kind}")
    try:
        offset = 0
        while True:
            chunk = os.pread(source, 1024 * 1024, offset)
            if not chunk:
                break
            _write_all(descriptor, chunk)
            offset += len(chunk)
        os.fchmod(descriptor, 0o500)
        _seal_descriptor(descriptor)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_pinned(path_value: object, digest_value: object, role: str, kind: str) -> tuple[int, bytes]:
    path = pathlib.Path(str(path_value))
    digest = str(digest_value).lower()
    if not path.is_absolute():
        raise SecureRuntimeError(f"reviewed {role} {kind} path must be absolute")
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise SecureRuntimeError(f"reviewed {role} {kind} SHA-256 is invalid")
    try:
        resolved = path.resolve(strict=True)
        source = os.open(
            resolved,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as error:
        raise SecureRuntimeError(f"reviewed {role} {kind} cannot be opened") from error
    try:
        info = os.fstat(source)
        mode = stat.S_IMODE(info.st_mode)
        if not stat.S_ISREG(info.st_mode):
            raise SecureRuntimeError(f"reviewed {role} {kind} is not a regular file")
        system_owner = os.stat("/").st_uid
        if info.st_uid not in {0, system_owner, os.geteuid()}:
            raise SecureRuntimeError(f"reviewed {role} {kind} has an untrusted owner")
        if info.st_nlink != 1:
            raise SecureRuntimeError(f"reviewed {role} {kind} must have exactly one link")
        if mode & 0o7022 or not mode & 0o111:
            raise SecureRuntimeError(f"reviewed {role} {kind} has unsafe mode {mode:04o}")
        actual = _sha256_fd(source)
        if actual != digest:
            raise SecureRuntimeError(f"reviewed {role} {kind} SHA-256 mismatch")
        prefix = os.pread(source, 4096, 0)
        return _sealed_copy(source, role, kind), prefix
    finally:
        os.close(source)


def _parse_shebang(prefix: bytes, role: str) -> tuple[str, tuple[str, ...]]:
    try:
        parts = shlex.split(prefix.splitlines()[0][2:].decode("utf-8"))
    except (IndexError, UnicodeDecodeError, ValueError) as error:
        raise SecureRuntimeError(f"invalid executable shebang for {role}") from error
    if not parts:
        raise SecureRuntimeError(f"invalid executable shebang for {role}")
    if pathlib.Path(parts[0]).name == "env":
        parts = parts[1:]
        if parts[:1] == ["-S"]:
            parts = parts[1:]
        if not parts or parts[0].startswith("-"):
            raise SecureRuntimeError(f"unsupported env shebang for {role}")
    return pathlib.Path(parts[0]).name, tuple(parts[1:])


def _prepare_launcher(role: str, provider: str, pin: dict) -> SecureLauncher:
    executable_fd, prefix = _open_pinned(
        pin.get("executable"), pin.get("sha256"), role, "executable"
    )
    fds = [executable_fd]
    hashes = [str(pin["sha256"]).lower()]
    interpreter_args: tuple[str, ...] = ()
    try:
        has_interpreter_pin = pin.get("interpreter") is not None or pin.get(
            "interpreter_sha256"
        ) is not None
        if prefix.startswith(b"#!"):
            interpreter_name, interpreter_args = _parse_shebang(prefix, role)
            if not pin.get("interpreter") or not pin.get("interpreter_sha256"):
                raise SecureRuntimeError(
                    f"reviewed {role} interpreter path and SHA-256 are required for scripts"
                )
            if pathlib.Path(str(pin["interpreter"])).name != interpreter_name:
                raise SecureRuntimeError(f"reviewed {role} interpreter basename mismatch")
            interpreter_fd, interpreter_prefix = _open_pinned(
                pin["interpreter"], pin["interpreter_sha256"], role, "interpreter"
            )
            if interpreter_prefix.startswith(b"#!"):
                os.close(interpreter_fd)
                raise SecureRuntimeError(f"nested script interpreter is unsupported for {role}")
            fds.insert(0, interpreter_fd)
            hashes.insert(0, str(pin["interpreter_sha256"]).lower())
        elif has_interpreter_pin:
            raise SecureRuntimeError(
                f"native {role} executable must not declare an interpreter pin"
            )
        return SecureLauncher(
            role=role,
            provider=provider,
            launcher_fds=tuple(fds),
            launcher_hashes=tuple(hashes),
            interpreter_args=interpreter_args,
        )
    except Exception:
        for descriptor in fds:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise


def preflight_secure_runtime(
    generator: str,
    verifier: str | list[str],
    cfg: dict,
) -> dict[str, SecureLauncher]:
    """Validate and seal every provider before state, worktrees, or calls exist."""
    verifier_names = verifier if isinstance(verifier, list) else [verifier]
    if generator not in SECURE_PROVIDER_NAMES or any(
        provider not in SECURE_PROVIDER_NAMES for provider in verifier_names
    ):
        raise SecureRuntimeError(
            "independent runtime accepts only explicitly pinned claude/codex providers; "
            "cmd and opaque provider identities are refused"
        )
    if len(verifier_names) != 1:
        raise SecureRuntimeError(
            "independent runtime requires one explicitly pinned verifier provider"
        )
    check_secure_runtime_support()
    pins = cfg.get("secure_pins")
    if not isinstance(pins, dict):
        raise SecureRuntimeError(
            "independent runtime requires reviewed generator/verifier executable and SHA-256 pins"
        )
    launchers: dict[str, SecureLauncher] = {}
    try:
        for role, provider in (("generator", generator), ("verifier", verifier_names[0])):
            pin = pins.get(role)
            if not isinstance(pin, dict) or not pin.get("executable") or not pin.get("sha256"):
                raise SecureRuntimeError(
                    f"independent runtime requires reviewed {role} executable and SHA-256 pins"
                )
            launchers[role] = _prepare_launcher(role, provider, pin)
        return launchers
    except Exception:
        close_secure_launchers(launchers)
        raise


def close_secure_launchers(launchers: dict[str, SecureLauncher] | None) -> None:
    for launcher in (launchers or {}).values():
        launcher.close()


def load_pin_config(path_value: object) -> dict[str, dict]:
    """Load a local 0600 pin file; machine-specific paths never belong in recipes."""
    path = pathlib.Path(str(path_value))
    if not path.is_absolute():
        raise SecureRuntimeError("secure provider config path must be absolute")
    try:
        document = json.loads(read_secure_bytes(path).decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SecureRuntimeError("secure provider config is not a verified JSON file") from error
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise SecureRuntimeError("secure provider config requires schema_version 1")
    roles = {key: document.get(key) for key in ("generator", "verifier")}
    if any(not isinstance(value, dict) for value in roles.values()):
        raise SecureRuntimeError("secure provider config requires generator and verifier objects")
    allowed = {"executable", "sha256", "interpreter", "interpreter_sha256"}
    for role, value in roles.items():
        if set(value) - allowed:
            raise SecureRuntimeError(f"secure provider config has unknown {role} fields")
    return roles


def _provider_tail(provider: str, cfg: dict) -> list[str]:
    if provider == "claude":
        tail = ["-p", "--output-format", "text", "--safe-mode", "--no-session-persistence"]
        if cfg.get("model"):
            tail += ["--model", str(cfg["model"])]
        return tail
    tail = [
        "exec",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ephemeral",
        "--sandbox",
        "read-only",
    ]
    if cfg.get("model"):
        tail += ["-m", str(cfg["model"])]
    return [*tail, "-"]


def run_secure_provider(
    launcher: SecureLauncher,
    prompt: str,
    cfg: dict,
    *,
    environ: dict[str, str] | None = None,
    run_command: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> tuple[int, str]:
    """Execute only the sealed bytes, with prompt on stdin and vendor-scoped env."""
    source_env = os.environ if environ is None else environ
    allowed = _PROVIDER_ENV[launcher.provider]
    environment = {key: value for key, value in source_env.items() if key in allowed}
    environment["PATH"] = FIXED_PROVIDER_PATH
    for descriptor, digest in zip(launcher.launcher_fds, launcher.launcher_hashes):
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or _sha256_fd(descriptor) != digest:
            raise SecureRuntimeError("sealed provider descriptor integrity mismatch")
    fd_paths = [f"/proc/self/fd/{descriptor}" for descriptor in launcher.launcher_fds]
    executable = (
        [fd_paths[0]]
        if len(fd_paths) == 1
        else [fd_paths[0], *launcher.interpreter_args, fd_paths[1]]
    )
    argv = [*executable, *_provider_tail(launcher.provider, cfg)]
    try:
        with tempfile.TemporaryDirectory(prefix=f"rig-{launcher.role}-") as temp_name:
            os.chmod(temp_name, 0o700)
            completed = run_command(
                argv,
                input=prompt,
                text=True,
                encoding="utf-8",
                errors="strict",
                capture_output=True,
                timeout=cfg.get("timeout", 600),
                cwd=temp_name,
                env=environment,
                shell=False,
                check=False,
                pass_fds=launcher.launcher_fds,
            )
    except subprocess.TimeoutExpired:
        return 124, "[provider timeout]"
    except OSError as error:
        return 127, f"[secure provider launch failed: {type(error).__name__}]"
    output = completed.stdout or ""
    if completed.returncode != 0 and completed.stderr:
        output = (output + "\n" + completed.stderr).strip()
    return completed.returncode, output
