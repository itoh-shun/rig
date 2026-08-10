"""Fail-closed process and filesystem primitives for independent provider runs."""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
import pathlib
import re
import shlex
import stat
import subprocess
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


class SecureRuntimeError(ValueError):
    """A secure independent run could not establish its trust boundary."""


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


def _sealed_copy(source: int, role: str, kind: str) -> int:
    if not hasattr(os, "memfd_create") or not pathlib.Path("/proc/self/fd").is_dir():
        raise SecureRuntimeError("verified descriptor execution is unavailable")
    descriptor = os.memfd_create(
        f"rig-{role}-{kind}",
        os.MFD_CLOEXEC | getattr(os, "MFD_ALLOW_SEALING", 0),
    )
    try:
        offset = 0
        while True:
            chunk = os.pread(source, 1024 * 1024, offset)
            if not chunk:
                break
            _write_all(descriptor, chunk)
            offset += len(chunk)
        os.fchmod(descriptor, 0o500)
        required = (
            fcntl.F_SEAL_WRITE
            | fcntl.F_SEAL_GROW
            | fcntl.F_SEAL_SHRINK
            | fcntl.F_SEAL_SEAL
        )
        fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, required)
        if fcntl.fcntl(descriptor, fcntl.F_GET_SEALS) & required != required:
            raise SecureRuntimeError("verified executable descriptor could not be sealed")
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
