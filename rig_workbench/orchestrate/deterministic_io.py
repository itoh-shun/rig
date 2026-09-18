"""Linux-only, OS-enforced execution and runner-owned deterministic evidence.

The parent is trusted. Children see the host read-only except the selected
workspace (generator only), private /tmp and /dev. This is write isolation, not
confidentiality isolation. Snapshots cover Git tracked and nonignored untracked
files, including tracked deletions and permission modes. Ignored files are not
verified subject inputs; checks needing them must reject this scope or declare
them tracked. No silent fallback is provided.

The host administrator and host IPC services are trusted. This adapter does not
filter AF_UNIX syscalls or conceal every host socket outside /tmp and /run;
it must not be presented as containment against a hostile host-service broker.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile

from ..ports.local import OS_ENV, SUBPROCESS

from .secure_fs import (
    acquire_output_lock, atomic_write_bytes, prepare_output_target,
    read_bytes, release_output_lock,
)


MAX_OUTPUT_BYTES = 8 * 1024 * 1024


def _no_links(path: pathlib.Path) -> pathlib.Path:
    path = path.absolute()
    if ".." in path.parts:
        raise ValueError("parent path components are not allowed")
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise ValueError(f"symlink path is not allowed: {candidate}")
    return path


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError(f"nonfinite JSON number: {value}")


def load_state(path: pathlib.Path) -> dict:
    """Bootstrap a protected state before its workspace is known."""
    result = json.loads(read_bytes(path), object_pairs_hook=_strict_object,
                        parse_constant=_nonfinite)
    if type(result) is not dict:
        raise ValueError("state must be a JSON object")
    return result


class StrictIO:
    def __init__(self, workspace: pathlib.Path, state_path: pathlib.Path):
        if sys.platform != "linux" or not pathlib.Path("/usr/bin/bwrap").is_file():
            raise ValueError("strict execution requires Linux and /usr/bin/bwrap")
        self.workspace = _no_links(pathlib.Path(workspace))
        self.state_path = _no_links(pathlib.Path(state_path))
        if self.state_path.is_relative_to(self.workspace):
            raise ValueError("state must be outside workspace")
        if not self.workspace.is_dir() or str(self.workspace) == "/":
            raise ValueError("workspace must be a Git worktree root")
        if pathlib.Path(__file__).resolve().is_relative_to(self.workspace):
            raise ValueError("workspace must not contain the trusted runner code")
        root = self._git("rev-parse", "--show-toplevel").strip()
        if pathlib.Path(root) != self.workspace:
            raise ValueError("workspace must be a Git worktree root")
        self._metadata = []
        for argument in ("--git-dir", "--git-common-dir"):
            value = self._git("rev-parse", "--path-format=absolute", argument).strip()
            path = _no_links(pathlib.Path(value))
            if path not in self._metadata:
                self._metadata.append(path)
        marker = self.workspace / ".git"
        _no_links(marker)
        if marker not in self._metadata:
            self._metadata.append(marker)
        prepare_output_target(self.state_path)
        self._ready = False

    def _validate_writable_tree(self):
        # Even ignored files matter here: a writable hardlink aliases its host
        # inode and could bypass a read-only mount at the original path.
        for directory, dirs, files in os.walk(self.workspace, followlinks=False):
            if pathlib.Path(directory) == self.workspace:
                dirs[:] = [name for name in dirs if name != ".git"]
            for name in dirs + files:
                path = pathlib.Path(directory) / name
                if path == self.workspace / ".git":
                    continue
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode):
                    raise ValueError("symlink in writable workspace")
                if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                    raise ValueError("hardlink in writable workspace")
                if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                    raise ValueError("special file in writable workspace")

    def _git(self, *args: str) -> str:
        result = SUBPROCESS.run(
            ["/usr/bin/git", "-C", str(self.workspace), *args],
            env={"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1", "HOME": "/nonexistent"},
        )
        result.check_returncode()
        return result.stdout

    def _argv(self, argv: list[str], writable: bool, network: bool) -> list[str]:
        args = ["/usr/bin/bwrap", "--ro-bind", "/", "/", "--unshare-user",
                "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--new-session",
                "--die-with-parent", "--proc", "/proc", "--dev", "/dev",
                "--tmpfs", "/tmp", "--tmpfs", "/run", "--tmpfs", "/var/tmp"]
        if not network:
            args.append("--unshare-net")
        # Expose the actual protected directory read-only, even when /tmp would
        # otherwise hide it behind a writable shadow. Mount before the workspace
        # because state may live in an ancestor of the workspace.
        args += ["--ro-bind", str(self.state_path.parent), str(self.state_path.parent)]
        # Rebind even read-only worktrees: the host worktree may itself be in /tmp.
        args += ["--bind" if writable else "--ro-bind", str(self.workspace), str(self.workspace)]
        for path in self._metadata:
            args += ["--ro-bind", str(path), str(path)]
        args += ["--chdir", str(self.workspace), "--", *argv]
        return args

    def _execute(self, argv, *, input=None, timeout=600, writable=True, network=False):
        if (type(argv) is not list or not argv or
                any(type(v) is not str or not v or "\0" in v for v in argv)):
            raise ValueError("argv must be a nonempty list of strings")
        if type(timeout) is not int or timeout < 1:
            raise ValueError("timeout must be a positive integer")
        if type(writable) is not bool or type(network) is not bool:
            raise ValueError("sandbox options must be booleans")
        _no_links(self.workspace)
        _no_links(self.state_path)
        if writable:
            self._validate_writable_tree()
        allowed_env = {"LANG", "LC_ALL"}
        if network:
            allowed_env |= {"OPENAI_API_KEY", "ANTHROPIC_API_KEY"}
        result = SUBPROCESS.run(
            self._argv(argv, writable, network), input=input,
            timeout=timeout, errors="strict",
            env={**{key: value for key, value in OS_ENV.snapshot().items()
                    if key in allowed_env},
                 "PATH": "/usr/bin:/bin", "RIG_PROVIDER_SUBPROCESS": "1"},
        )
        if len(result.stdout.encode()) + len(result.stderr.encode()) > MAX_OUTPUT_BYTES:
            raise ValueError("sandbox output exceeds evidence limit")
        return result

    def preflight(self) -> None:
        self._ready = False
        with tempfile.TemporaryDirectory(prefix=".rig-probe-", dir=self.workspace) as probe:
            target = pathlib.Path(probe) / "allowed"
            # The actual evidence directory is mounted read-only, so this probes
            # its real inode instead of accidentally writing into a /tmp shadow.
            forbidden = self.state_path.parent / (pathlib.Path(probe).name + "-forbidden")
            atomic_write_bytes(forbidden, b"protected")
            script = ("import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('ok');\n"
                      "try: pathlib.Path(sys.argv[2]).write_text('escape')\n"
                      "except OSError: pass\n"
                      "else: raise SystemExit(9)\n")
            result = self._execute(["/usr/bin/python3", "-c", script, str(target), str(forbidden)])
            intact = forbidden.read_bytes() == b"protected"
            forbidden.unlink()
            if result.returncode != 0 or not target.is_file() or not intact:
                raise ValueError(f"OS write isolation preflight failed (exit {result.returncode}): {result.stderr}")
        self._ready = True

    def run(self, argv: list[str], *, input: str | None = None, timeout: int = 600,
            writable: bool = True, network: bool = False) -> subprocess.CompletedProcess[str]:
        if not self._ready:
            raise ValueError("successful isolation preflight required")
        return self._execute(argv, input=input, timeout=timeout, writable=writable, network=network)

    def snapshot(self) -> str:
        stage = self._git("ls-files", "--stage", "-z")
        if any(entry.startswith("160000 ") for entry in stage.split("\0")):
            raise ValueError("submodules are outside snapshot scope")
        names = set(self._git("ls-files", "--cached", "--others", "--exclude-standard", "-z").split("\0")) - {""}
        records = []
        for name in sorted(names):
            path = _no_links(self.workspace / name)
            if not path.is_relative_to(self.workspace):
                raise ValueError("snapshot path escapes workspace")
            try:
                info = path.lstat()
            except FileNotFoundError:
                records.append([name, "deleted"])
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("snapshot requires regular files")
            records.append([name, stat.S_IMODE(info.st_mode), hashlib.sha256(path.read_bytes()).hexdigest()])
        return hashlib.sha256(json.dumps(records, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()

    def save(self, state: dict) -> None:
        if type(state) is not dict:
            raise ValueError("state must be a JSON object")
        payload = json.dumps(state, allow_nan=False, ensure_ascii=True, sort_keys=True).encode()
        atomic_write_bytes(self.state_path, payload)

    def load(self) -> dict:
        return load_state(self.state_path)

    @contextmanager
    def locked(self):
        descriptor = acquire_output_lock(self.state_path)
        try:
            yield
        finally:
            release_output_lock(descriptor)
