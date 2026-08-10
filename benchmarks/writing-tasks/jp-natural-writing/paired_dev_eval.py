#!/usr/bin/env python3
"""Audit-grade, dev-only paired 2x2 Japanese writing evaluation."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shlex
import stat
import statistics
import subprocess
import sys
import threading
import time
import tempfile
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
MODULE_PATH = Path(__file__).resolve()
PARITY_PATH = HERE / "parity.py"
PROTOCOL_PATH = HERE / "paired_dev_protocol.json"
DEV_CASES = HERE / "parity_cases.dev.json"
CONFIG_PATH = HERE / "parity.providers.example.json"
ASSET_PATHS = {
    "persona": REPO / "packs/domain/japanese-writing/facets/personas/japanese-writer.md",
    "instruction": REPO / "packs/domain/japanese-writing/facets/instructions/japanese-write.md",
    "framework": REPO
    / "packs/domain/japanese-writing/facets/policies/writing-delivery-contract.md",
    "language": REPO
    / "packs/domain/japanese-writing/facets/policies/japanese-writing-rules-v2.md",
}
EXPECTED_DEV_CASES = 10
SCHEMA = 4
RUN_MODES = {"iterative_dev", "final_fresh_dev"}
COMMON_PROVIDER_ENV_ALLOWLIST = (
    "HOME",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "TERM",
    "NO_COLOR",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
)
FIXED_PROVIDER_PATH = "/usr/bin:/bin"
PROVIDER_ENV_ALLOWLISTS = {
    "reference": COMMON_PROVIDER_ENV_ALLOWLIST + ("OPENAI_API_KEY", "CODEX_HOME"),
    "candidate": COMMON_PROVIDER_ENV_ALLOWLIST
    + ("ANTHROPIC_API_KEY", "CLAUDE_CONFIG_DIR"),
    "judge": COMMON_PROVIDER_ENV_ALLOWLIST
    + ("ANTHROPIC_API_KEY", "CLAUDE_CONFIG_DIR"),
}
ATTEMPT_BACKOFF_SECONDS = (0, 2, 4)
SUPPORT_SAFETY_FIELDS = (
    "no_file_body",
    "no_data_rows",
    "structure_header_only_alternative",
    "masking",
    "safe_alternative",
)


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_fd(descriptor: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while True:
        chunk = os.pread(descriptor, 1024 * 1024, offset)
        if not chunk:
            return digest.hexdigest()
        digest.update(chunk)
        offset += len(chunk)


def load_protocol(path: Path = PROTOCOL_PATH) -> dict[str, Any]:
    protocol = json.loads(path.read_text(encoding="utf-8"))
    if (
        protocol.get("schema") != 1
        or protocol.get("split") != "dev"
        or protocol.get("expected_case_count") != EXPECTED_DEV_CASES
        or list(protocol.get("arms", {}))
        != ["base_writer", "framework", "language", "combined"]
        or protocol.get("orders") != ["reference_first", "candidate_first"]
        or protocol.get("retry_policy")
        != {
            "max_attempts_per_logical_call": 3,
            "backoff_seconds": [0, 2, 4],
            "lifetime_attempt_budget": 390,
        }
        or protocol.get("common_candidate_components")
        != ["persona", "instruction", "request"]
        or protocol.get("experimental_factors") != ["framework", "language"]
        or protocol.get("trusted_executable_pins")
        != {
            "source": "required_cli",
            "roles": ["reference", "candidate", "judge"],
            "path_requirement": "absolute",
            "digest": "sha256",
            "path_resolution": "canonical_before_config_load",
        }
        or protocol.get("provider_environment_policy")
        != {
            "inherit_path": False,
            "fixed_path": FIXED_PROVIDER_PATH,
            "launcher": "verified_proc_fd",
            "shebang_interpreter": "required_explicit_pin",
        }
        or protocol.get("support_safety", {}).get("schema_version") != 1
        or protocol.get("support_safety", {}).get("category") != "support_reply"
        or protocol.get("support_safety", {}).get("fields")
        != list(SUPPORT_SAFETY_FIELDS)
        or protocol.get("support_safety", {}).get("answers") != ["A", "B"]
        or protocol.get("support_safety", {}).get("candidate_preference_requirement")
        is not False
        or protocol.get("support_safety", {}).get("include_in_global_metrics")
        is not True
        or protocol.get("support_safety", {}).get("acceptance_gate")
        != "all_candidate_fields_true_all_arms_both_orders"
        or not isinstance(protocol.get("support_safety", {}).get("prompt_suffix"), str)
    ):
        raise ValueError("invalid paired dev protocol")
    return protocol


def strip_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text.strip()
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("unterminated YAML frontmatter")
    return text[end + 5 :].strip()


def _path_id(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO).as_posix()
    except ValueError:
        return path.name


def _render_component(protocol: dict[str, Any], kind: str, text: str) -> str:
    label_key = "policy" if kind in {"framework", "language"} else kind
    return f"{protocol['prompt_format']['labels'][label_key]}\n{text.strip()}"


def compose_candidate_prompt(
    request: str,
    arm: str,
    assets: dict[str, str],
    protocol: dict[str, Any],
) -> tuple[str, list[dict[str, str]]]:
    if arm not in protocol["arms"]:
        raise ValueError(f"unknown arm: {arm}")
    component_names = ["persona", "instruction", "request", *protocol["arms"][arm]]
    rendered: list[str] = []
    components: list[dict[str, str]] = []
    for name in component_names:
        value = request if name == "request" else assets[name]
        section = _render_component(protocol, name, value)
        rendered.append(section)
        components.append({"name": name, "sha256": sha256_text(section)})
    return protocol["prompt_format"]["separator"].join(rendered), components


def plan_generation_calls(
    cases: list[dict[str, Any]], assets: dict[str, str], protocol: dict[str, Any]
) -> list[dict[str, Any]]:
    planned: list[dict[str, Any]] = []
    for case in cases:
        request = case["prompt"]
        planned.append(
            {
                "logical_call_id": f"gen:{case['id']}:reference",
                "role": "reference",
                "case_id": case["id"],
                "arm": "reference",
                "prompt_sha256": sha256_text(request),
                "ordered_components": [
                    {"name": "request", "sha256": sha256_text(request)}
                ],
            }
        )
        for arm in protocol["arms"]:
            prompt, components = compose_candidate_prompt(request, arm, assets, protocol)
            planned.append(
                {
                    "logical_call_id": f"gen:{case['id']}:candidate:{arm}",
                    "role": "candidate",
                    "case_id": case["id"],
                    "arm": arm,
                    "prompt_sha256": sha256_text(prompt),
                    "ordered_components": components,
                }
            )
    return planned


def build_fingerprint_inputs(
    *,
    protocol: dict[str, Any],
    protocol_path: Path,
    cases: list[dict[str, Any]],
    cases_path: Path,
    config_path: Path,
    asset_paths: dict[str, Path],
    providers: dict[str, dict[str, Any]],
    evaluator_path: Path,
    parity_path: Path,
    judge_prompt: str,
) -> dict[str, Any]:
    asset_texts = {
        name: strip_frontmatter(path.read_text(encoding="utf-8"))
        if name == "persona"
        else path.read_text(encoding="utf-8").strip()
        for name, path in asset_paths.items()
    }
    assets = {
        name: {
            "repo_path": _path_id(path),
            "file_sha256": sha256_file(path),
            "effective_text_sha256": sha256_text(asset_texts[name]),
        }
        for name, path in sorted(asset_paths.items())
    }
    planned = plan_generation_calls(cases, asset_texts, protocol)
    return {
        "protocol": {
            "definition": protocol,
            "protocol_file_sha256": sha256_file(protocol_path),
            "evaluator_source_sha256": sha256_file(evaluator_path),
            "parity_source_sha256": sha256_file(parity_path),
            "judge_prompt_sha256": sha256_text(judge_prompt),
        },
        "inputs": {
            "dev_cases": {
                "repo_path": _path_id(cases_path),
                "file_sha256": sha256_file(cases_path),
                "count": len(cases),
                "cases": [
                    {
                        "id": case["id"],
                        "category": case["category"],
                        "request_sha256": sha256_text(case["prompt"]),
                    }
                    for case in cases
                ],
            },
            "assets": assets,
            "provider_config": {
                "repo_path": _path_id(config_path),
                "file_sha256": sha256_file(config_path),
            },
            "providers": providers,
            "trusted_executable_pins": {
                role: {"launcher_chain": metadata["launcher_chain"]}
                for role, metadata in providers.items()
            },
            "provider_environment_policy": protocol["provider_environment_policy"],
            "planned_generation_calls": planned,
        },
    }


class ResolvedProviderSpec:
    """Provider specification with an immutable executable selected before calls."""

    def __init__(
        self,
        source: Any,
        audit_role: str,
        trusted_pin: dict[str, Any],
    ):
        self.role = source.role
        self.audit_role = audit_role
        self.identity = source.identity
        self.configured_argv = tuple(source.argv)
        self.trusted_executable_path = trusted_pin["trusted_executable_path"]
        self.resolved_executable_path = trusted_pin["resolved_executable_path"]
        self.executable_sha256 = trusted_pin["executable_sha256"]
        self.launcher_chain = trusted_pin["launcher_chain"]
        self.launcher_fds = tuple(trusted_pin["launcher_fds"])
        self.interpreter_args = tuple(trusted_pin["interpreter_args"])
        self.argv = (self.resolved_executable_path, *self.configured_argv[1:])
        self.input_mode = source.input_mode
        self.output_mode = source.output_mode
        self.timeout_sec = source.timeout_sec
        self.cwd_mode = source.cwd_mode
        self.env = source.env


def validate_trusted_executable_pins(
    raw_pins: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Validate explicit run inputs without consulting PATH or provider config."""
    if set(raw_pins) != set(PROVIDER_ENV_ALLOWLISTS):
        raise ValueError("trusted executable pins must cover all provider roles")
    validated: dict[str, dict[str, Any]] = {}
    for role in ("reference", "candidate", "judge"):
        raw = raw_pins[role]
        trusted_path = Path(raw["path"])
        expected_sha256 = str(raw["sha256"]).lower()
        executable, executable_fd = _open_pinned_executable(
            trusted_path, expected_sha256, role=role, kind="executable"
        )
        prefix = os.pread(executable_fd, 4096, 0)
        interpreter_args: list[str] = []
        launcher_chain = [
            _launcher_entry("executable", trusted_path, executable, expected_sha256)
        ]
        launcher_fds = [executable_fd]
        if prefix.startswith(b"#!"):
            interpreter_name, interpreter_args = _parse_shebang(prefix, role)
            interpreter_path_value = raw.get("interpreter_path")
            interpreter_sha_value = raw.get("interpreter_sha256")
            if interpreter_path_value is None or interpreter_sha_value is None:
                os.close(executable_fd)
                raise ValueError(f"trusted interpreter pin is required for {role}")
            interpreter_path = Path(interpreter_path_value)
            interpreter_sha256 = str(interpreter_sha_value).lower()
            if interpreter_path.name != interpreter_name:
                os.close(executable_fd)
                raise ValueError(f"trusted interpreter basename mismatch for {role}")
            interpreter, interpreter_fd = _open_pinned_executable(
                interpreter_path,
                interpreter_sha256,
                role=role,
                kind="interpreter",
            )
            if os.pread(interpreter_fd, 2, 0) == b"#!":
                os.close(executable_fd)
                os.close(interpreter_fd)
                raise ValueError(f"nested script interpreter is unsupported for {role}")
            launcher_chain.insert(
                0,
                _launcher_entry(
                    "interpreter", interpreter_path, interpreter, interpreter_sha256
                ),
            )
            launcher_fds.insert(0, interpreter_fd)
        elif raw.get("interpreter_path") is not None or raw.get(
            "interpreter_sha256"
        ) is not None:
            os.close(executable_fd)
            raise ValueError(f"native executable must not have an interpreter pin for {role}")
        validated[role] = {
            "trusted_executable_path": str(trusted_path),
            "resolved_executable_path": str(executable),
            "executable_sha256": expected_sha256,
            "launcher_chain": launcher_chain,
            "launcher_fds": launcher_fds,
            "interpreter_args": interpreter_args,
        }
    return validated


def _open_pinned_executable(
    trusted_path: Path, expected_sha256: str, *, role: str, kind: str
) -> tuple[Path, int]:
    if not trusted_path.is_absolute():
        raise ValueError(f"trusted {kind} path must be absolute for {role}")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError(f"trusted {kind} SHA-256 is invalid for {role}")
    try:
        executable = trusted_path.resolve(strict=True)
        descriptor = os.open(
            executable,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as error:
        raise ValueError(f"trusted {kind} path does not exist for {role}") from error
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or not os.access(executable, os.X_OK):
        os.close(descriptor)
        raise ValueError(f"trusted {kind} is not executable for {role}")
    if sha256_fd(descriptor) != expected_sha256:
        os.close(descriptor)
        raise ValueError(f"trusted {kind} SHA-256 mismatch for {role}")
    sealed_descriptor = _sealed_executable_copy(descriptor, role=role, kind=kind)
    os.close(descriptor)
    return executable, sealed_descriptor


def _sealed_executable_copy(source_descriptor: int, *, role: str, kind: str) -> int:
    if not hasattr(os, "memfd_create") or not Path("/proc/self/fd").is_dir():
        raise ValueError("verified descriptor execution is unavailable")
    descriptor = os.memfd_create(
        f"rig-{role}-{kind}",
        os.MFD_CLOEXEC | getattr(os, "MFD_ALLOW_SEALING", 0),
    )
    try:
        offset = 0
        while True:
            chunk = os.pread(source_descriptor, 1024 * 1024, offset)
            if not chunk:
                break
            _write_all(descriptor, chunk)
            offset += len(chunk)
        os.fchmod(descriptor, 0o500)
        required_seals = (
            fcntl.F_SEAL_WRITE
            | fcntl.F_SEAL_GROW
            | fcntl.F_SEAL_SHRINK
            | fcntl.F_SEAL_SEAL
        )
        fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, required_seals)
        if fcntl.fcntl(descriptor, fcntl.F_GET_SEALS) & required_seals != required_seals:
            raise ValueError("verified executable descriptor could not be sealed")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _parse_shebang(prefix: bytes, role: str) -> tuple[str, list[str]]:
    try:
        line = prefix.splitlines()[0][2:].decode("utf-8")
        parts = shlex.split(line)
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError(f"invalid executable shebang for {role}") from error
    if not parts:
        raise ValueError(f"invalid executable shebang for {role}")
    if Path(parts[0]).name == "env":
        parts = parts[1:]
        if parts[:1] == ["-S"]:
            parts = parts[1:]
        if not parts or parts[0].startswith("-"):
            raise ValueError(f"unsupported env shebang for {role}")
    return Path(parts[0]).name, parts[1:]


def _launcher_entry(
    kind: str, trusted: Path, resolved: Path, digest: str
) -> dict[str, str]:
    return {
        "kind": kind,
        "trusted_path": str(trusted),
        "resolved_path": str(resolved),
        "sha256": digest,
    }


def pin_provider_spec(
    spec: Any, audit_role: str, trusted_pin: dict[str, Any]
) -> ResolvedProviderSpec:
    if audit_role not in PROVIDER_ENV_ALLOWLISTS:
        raise ValueError(f"unknown provider role: {audit_role}")
    trusted_path = Path(trusted_pin["trusted_executable_path"])
    if Path(spec.argv[0]).name != trusted_path.name:
        raise ValueError(f"trusted executable basename mismatch for {audit_role}")
    for key, _value in spec.env:
        if key not in PROVIDER_ENV_ALLOWLISTS[audit_role]:
            raise ValueError(
                f"provider environment key is not allowlisted for {audit_role}: {key}"
            )
    return ResolvedProviderSpec(spec, audit_role, trusted_pin)


def _effective_environment_keys(
    spec: Any, environ: dict[str, str] | None = None
) -> list[str]:
    source_environment = os.environ if environ is None else environ
    allowlist = PROVIDER_ENV_ALLOWLISTS[spec.audit_role]
    keys = {key for key in source_environment if key in allowlist}
    keys.update(key for key, _value in spec.env)
    keys.add("PATH")
    return sorted(keys)


def provider_audit_metadata(
    spec: Any,
    config_entry: dict[str, Any],
    *,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return non-secret provider metadata bound to a hash of the full specification."""
    provider = (
        str(config_entry.get("provider", "")).strip()
        or Path(spec.configured_argv[0]).name
    )
    requested_model = str(config_entry.get("model", "")).strip() or str(spec.identity)
    specification = {
        "role": spec.role,
        "identity": spec.identity,
        "configured_argv_sha256": canonical_sha256(list(spec.configured_argv)),
        "resolved_argv_sha256": canonical_sha256(list(spec.argv)),
        "resolved_executable_path": spec.resolved_executable_path,
        "trusted_executable_path": spec.trusted_executable_path,
        "executable_sha256": spec.executable_sha256,
        "launcher_chain": spec.launcher_chain,
        "fixed_path": FIXED_PROVIDER_PATH,
        "input_mode": spec.input_mode,
        "output_mode": spec.output_mode,
        "timeout_sec": spec.timeout_sec,
        "cwd_mode": spec.cwd_mode,
        "configured_environment_keys": [key for key, _value in spec.env],
        "environment_allowlist": list(PROVIDER_ENV_ALLOWLISTS[spec.audit_role]),
        "effective_environment_keys": _effective_environment_keys(spec, environ),
    }
    return {
        "provider": provider,
        "requested_model": requested_model,
        "reported_model": None,
        "adapter": Path(spec.configured_argv[0]).name,
        "resolved_executable_path": spec.resolved_executable_path,
        "trusted_executable_path": spec.trusted_executable_path,
        "executable_sha256": spec.executable_sha256,
        "launcher_chain": spec.launcher_chain,
        "effective_environment_keys": _effective_environment_keys(spec, environ),
        "provider_spec_sha256": canonical_sha256(specification),
        "model_metadata_source": "config.model" if config_entry.get("model") else "identity",
    }


def validate_provider_protocol(
    specs: dict[str, Any],
    providers: dict[str, dict[str, Any]],
    protocol: dict[str, Any],
) -> None:
    contracts = protocol.get("provider_contracts")
    if not isinstance(contracts, dict) or set(contracts) != {"reference", "candidate", "judge"}:
        raise ValueError("protocol must pin all three provider contracts")
    for role, contract in contracts.items():
        spec = specs[role]
        metadata = providers[role]
        actual = {
            "provider": metadata["provider"],
            "model": metadata["requested_model"],
            "executable": Path(spec.configured_argv[0]).name,
            "argv": list(spec.configured_argv),
            "configured_input_mode": spec.input_mode,
            "runtime_prompt_transport": "stdin",
            "output_mode": spec.output_mode,
            "cwd_mode": spec.cwd_mode,
            "timeout_sec": spec.timeout_sec,
            "env_keys": [key for key, _value in spec.env],
            "environment_allowlist": list(PROVIDER_ENV_ALLOWLISTS[role]),
            "fixed_path": FIXED_PROVIDER_PATH,
        }
        if actual != contract:
            raise ValueError(f"provider protocol mismatch for {role}")
def secure_run_provider(
    spec: Any,
    prompt: str,
    attempts: int = 1,
    *,
    environ: dict[str, str] | None = None,
    run_command: Callable[..., Any] = subprocess.run,
) -> str:
    """Run one isolated provider attempt with the prompt only on stdin."""
    if attempts != 1:
        raise ValueError("secure provider adapter accepts exactly one externally journaled attempt")
    if spec.cwd_mode != "temp":
        raise ValueError("audit provider must use an isolated temporary cwd")
    if spec.audit_role not in PROVIDER_ENV_ALLOWLISTS:
        raise ValueError("audit provider role is not recognized")
    if len(spec.launcher_chain) != len(spec.launcher_fds):
        raise ValueError("provider launcher chain is invalid")
    for entry, descriptor in zip(spec.launcher_chain, spec.launcher_fds):
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or sha256_fd(descriptor) != entry["sha256"]:
            raise ValueError("verified provider launcher descriptor integrity mismatch")
    source_environment = os.environ if environ is None else environ
    allowlist = PROVIDER_ENV_ALLOWLISTS[spec.audit_role]
    environment = {
        key: value
        for key, value in source_environment.items()
        if key in allowlist
    }
    environment["PATH"] = FIXED_PROVIDER_PATH
    for key, value in spec.env:
        if key not in allowlist:
            raise ValueError(
                f"provider environment key is not allowlisted for {spec.audit_role}: {key}"
            )
        environment[key] = value
    with tempfile.TemporaryDirectory(prefix="rig-jp-paired-dev-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        os.chmod(temp_dir, 0o700)
        output_file = temp_dir / "provider-output.txt"
        fd_paths = [f"/proc/self/fd/{descriptor}" for descriptor in spec.launcher_fds]
        if len(fd_paths) == 1:
            launcher = [fd_paths[0]]
        else:
            launcher = [fd_paths[0], *spec.interpreter_args, fd_paths[1]]
        configured_tail = [
            part.replace("{output_file}", str(output_file))
            for part in spec.configured_argv[1:]
        ]
        argv = [*launcher, *configured_tail]
        completed = run_command(
            argv,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=spec.timeout_sec,
            cwd=temp_dir,
            env=environment,
            shell=False,
            check=False,
            pass_fds=spec.launcher_fds,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"provider exited with status {completed.returncode}")
        if spec.output_mode == "file":
            if not output_file.is_file() or output_file.is_symlink():
                raise RuntimeError("provider did not create a regular output file")
            os.chmod(output_file, 0o600)
            output = output_file.read_text(encoding="utf-8").strip()
        else:
            output = str(completed.stdout).strip()
        if not output:
            raise RuntimeError("provider returned empty output")
        return output


def _write_secure_json_exclusive(
    path: Path, value: Any, *, run_dir_fd: int | None = None
) -> None:
    absolute = Path(os.path.abspath(path))
    dir_descriptor = (
        os.dup(run_dir_fd)
        if run_dir_fd is not None
        else _open_verified_run_dir(absolute.parent, create=True)
    )
    try:
        descriptor = os.open(
            absolute.name,
            os.O_CREAT
            | os.O_EXCL
            | os.O_WRONLY
            | os.O_CLOEXEC
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=dir_descriptor,
        )
        _validate_artifact_fd(descriptor, absolute.name)
        payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        _write_all(descriptor, payload)
        os.fsync(descriptor)
    finally:
        if "descriptor" in locals():
            os.close(descriptor)
        os.close(dir_descriptor)


def save_secure_json(
    path: Path, value: Any, *, run_dir_fd: int | None = None
) -> None:
    absolute = Path(os.path.abspath(path))
    dir_descriptor = (
        os.dup(run_dir_fd)
        if run_dir_fd is not None
        else _open_verified_run_dir(absolute.parent, create=True)
    )
    _validate_existing_artifact(dir_descriptor, absolute.name)
    temp_name = f".{absolute.name}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(
        temp_name,
        os.O_CREAT
        | os.O_EXCL
        | os.O_WRONLY
        | os.O_CLOEXEC
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=dir_descriptor,
    )
    try:
        _validate_artifact_fd(descriptor, temp_name)
        payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(
            temp_name,
            absolute.name,
            src_dir_fd=dir_descriptor,
            dst_dir_fd=dir_descriptor,
        )
        _validate_existing_artifact(dir_descriptor, absolute.name, required=True)
        os.fsync(dir_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temp_name, dir_fd=dir_descriptor)
        except FileNotFoundError:
            pass
        os.close(dir_descriptor)


def _validate_existing_artifact(
    dir_descriptor: int, name: str, *, required: bool = False
) -> None:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=dir_descriptor,
        )
    except FileNotFoundError:
        if required:
            raise ValueError(f"{name} artifact is missing")
        return
    except OSError as error:
        raise ValueError(f"{name} is not a secure artifact") from error
    try:
        _validate_artifact_fd(descriptor, name)
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("short secure artifact write")
        offset += written


def prepare_run(
    run_dir: Path,
    *,
    run_mode: str,
    fingerprint_inputs: dict[str, Any],
    run_id: str,
    run_dir_fd: int | None = None,
) -> dict[str, Any]:
    """Create or verify the immutable manifest before any provider invocation."""
    if run_mode not in RUN_MODES:
        raise ValueError(f"unsupported run mode: {run_mode}")
    run_dir = Path(os.path.abspath(run_dir))
    dir_descriptor = (
        os.dup(run_dir_fd)
        if run_dir_fd is not None
        else _open_verified_run_dir(run_dir, create=True)
    )
    try:
        existing = [name for name in os.listdir(dir_descriptor) if name != "run.lock"]
    finally:
        os.close(dir_descriptor)
    if run_mode == "final_fresh_dev" and existing:
        raise ValueError("final_fresh_dev requires an empty artifact directory")
    manifest_path = run_dir / "manifest.json"
    fingerprint = canonical_sha256(fingerprint_inputs)
    if "manifest.json" in existing:
        if run_mode == "final_fresh_dev":
            raise ValueError("final_fresh_dev requires an empty artifact directory")
        manifest = _read_secure_json_artifact(manifest_path, run_dir_fd=run_dir_fd)
        if (
            manifest.get("schema") != SCHEMA
            or manifest.get("run_mode") != run_mode
            or manifest.get("fingerprint") != fingerprint
            or manifest.get("fingerprint_inputs") != fingerprint_inputs
        ):
            raise ValueError("run manifest fingerprint mismatch")
        return manifest
    if existing:
        raise ValueError("new run requires an empty artifact directory")
    manifest = {
        "schema": SCHEMA,
        "run_id": run_id,
        "run_mode": run_mode,
        "fingerprint": fingerprint,
        "fingerprint_inputs": fingerprint_inputs,
    }
    _write_secure_json_exclusive(manifest_path, manifest, run_dir_fd=run_dir_fd)
    return manifest


def _read_secure_json_artifact(
    path: Path, *, run_dir_fd: int | None = None
) -> dict[str, Any]:
    absolute = Path(os.path.abspath(path))
    dir_descriptor = (
        os.dup(run_dir_fd)
        if run_dir_fd is not None
        else _open_verified_run_dir(absolute.parent, create=False)
    )
    try:
        descriptor = os.open(
            absolute.name,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=dir_descriptor,
        )
        try:
            _validate_artifact_fd(descriptor, absolute.name)
            value = json.loads(_read_all_fd(descriptor).decode("utf-8"))
        finally:
            os.close(descriptor)
    except OSError as error:
        raise ValueError(f"{absolute.name} is not a secure artifact") from error
    finally:
        os.close(dir_descriptor)
    if not isinstance(value, dict):
        raise ValueError(f"{absolute.name} must contain a JSON object")
    return value


def _sha256_secure_artifact(path: Path, *, run_dir_fd: int) -> str:
    dir_descriptor = os.dup(run_dir_fd)
    try:
        descriptor = os.open(
            path.name,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=dir_descriptor,
        )
        try:
            _validate_artifact_fd(descriptor, path.name)
            return sha256_fd(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise ValueError(f"{path.name} is not a secure artifact") from error
    finally:
        os.close(dir_descriptor)


class RunLock:
    """Nonblocking process lock held for the full artifact mutation window."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = Path(os.path.abspath(run_dir))
        self.path = self.run_dir / "run.lock"
        self._descriptor: int | None = None
        self._dir_descriptor: int | None = None

    def __enter__(self) -> "RunLock":
        dir_descriptor = _open_verified_run_dir(self.run_dir, create=True)
        try:
            descriptor = os.open(
                "run.lock",
                os.O_CREAT
                | os.O_RDWR
                | os.O_CLOEXEC
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=dir_descriptor,
            )
            _validate_artifact_fd(descriptor, "run.lock")
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(descriptor)
            os.close(dir_descriptor)
            raise RuntimeError("evaluation run directory is already locked") from error
        except ValueError:
            os.close(descriptor)
            os.close(dir_descriptor)
            raise
        except OSError as error:
            if "descriptor" in locals():
                os.close(descriptor)
            os.close(dir_descriptor)
            raise ValueError("run.lock is not a secure artifact") from error
        self._descriptor = descriptor
        self._dir_descriptor = dir_descriptor
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        if self._descriptor is not None:
            fcntl.flock(self._descriptor, fcntl.LOCK_UN)
            os.close(self._descriptor)
            self._descriptor = None
        if self._dir_descriptor is not None:
            os.close(self._dir_descriptor)
            self._dir_descriptor = None

    @property
    def dir_descriptor(self) -> int:
        if self._dir_descriptor is None:
            raise RuntimeError("run lock is not held")
        return self._dir_descriptor


def _open_verified_run_dir(path: Path, *, create: bool) -> int:
    if path.is_symlink():
        raise ValueError("run directory must not be a symlink")
    if create and not path.exists():
        path.mkdir(parents=True, mode=0o700)
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | os.O_DIRECTORY
            | os.O_CLOEXEC
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as error:
        raise ValueError("run directory cannot be securely opened") from error
    info = os.fstat(descriptor)
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        os.close(descriptor)
        raise ValueError("run directory must be owner-controlled mode 0700")
    return descriptor


def _validate_artifact_fd(descriptor: int, name: str) -> os.stat_result:
    info = os.fstat(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise ValueError(f"{name} is not a secure regular artifact")
    return info


class AttemptJournal:
    """Append-only, crash-auditable provider-attempt journal."""

    def __init__(
        self,
        path: Path,
        *,
        fingerprint: str,
        lifetime_attempt_budget: int | None = None,
        run_dir_fd: int | None = None,
    ) -> None:
        self.path = Path(os.path.abspath(path))
        self._name = self.path.name
        self._dir_descriptor = (
            os.dup(run_dir_fd)
            if run_dir_fd is not None
            else _open_verified_run_dir(self.path.parent, create=True)
        )
        self.fingerprint = fingerprint
        self._lock = threading.Lock()
        self._sequence = 0
        self._attempt_counts: dict[str, int] = {}
        self._started_total = 0
        self._lifetime_attempt_budget = lifetime_attempt_budget
        try:
            descriptor = os.open(
                self._name,
                os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=self._dir_descriptor,
            )
        except FileNotFoundError:
            descriptor = None
        except OSError as error:
            raise ValueError(f"{self._name} is not a secure artifact") from error
        if descriptor is not None:
            try:
                _validate_artifact_fd(descriptor, self._name)
                content = _read_all_fd(descriptor).decode("utf-8")
            finally:
                os.close(descriptor)
            seen_attempts: set[str] = set()
            seen_finishes: set[str] = set()
            expected_sequence = 1
            for line in content.splitlines():
                record = json.loads(line)
                if (
                    record.get("schema") != 1
                    or record.get("fingerprint") != fingerprint
                    or record.get("sequence") != expected_sequence
                    or record.get("event") not in {"attempt_started", "attempt_finished"}
                ):
                    raise ValueError("attempt journal fingerprint mismatch")
                expected_sequence += 1
                self._sequence = max(self._sequence, int(record["sequence"]))
                if record.get("event") == "attempt_started":
                    if record["attempt_id"] in seen_attempts:
                        raise ValueError("duplicate attempt start")
                    seen_attempts.add(record["attempt_id"])
                    self._started_total += 1
                    logical_id = str(record["logical_call_id"])
                    self._attempt_counts[logical_id] = max(
                        self._attempt_counts.get(logical_id, 0), int(record["attempt_no"])
                    )
                elif (
                    record["attempt_id"] not in seen_attempts
                    or record["attempt_id"] in seen_finishes
                    or record.get("status") not in {"success", "error", "invalid"}
                ):
                    raise ValueError("invalid attempt finish")
                else:
                    seen_finishes.add(record["attempt_id"])

    def _append_locked(self, record: dict[str, Any]) -> dict[str, Any]:
        self._sequence += 1
        stored = {
            "schema": 1,
            "fingerprint": self.fingerprint,
            "sequence": self._sequence,
            "recorded_ns": time.time_ns(),
            **record,
        }
        payload = canonical_json(stored) + b"\n"
        try:
            descriptor = os.open(
                self._name,
                os.O_APPEND
                | os.O_CREAT
                | os.O_WRONLY
                | os.O_CLOEXEC
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=self._dir_descriptor,
            )
            _validate_artifact_fd(descriptor, self._name)
        except ValueError:
            os.close(descriptor)
            raise
        except OSError as error:
            if "descriptor" in locals():
                os.close(descriptor)
            raise ValueError(f"{self._name} is not a secure artifact") from error
        try:
            _write_all(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return stored

    def start(self, logical_call_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if (
                self._lifetime_attempt_budget is not None
                and self._started_total >= self._lifetime_attempt_budget
            ):
                raise RuntimeError("lifetime attempt budget exhausted")
            attempt_no = self._attempt_counts.get(logical_call_id, 0) + 1
            self._attempt_counts[logical_call_id] = attempt_no
            stored = self._append_locked(
                {
                    "event": "attempt_started",
                    "attempt_id": uuid.uuid4().hex,
                    "attempt_no": attempt_no,
                    "logical_call_id": logical_call_id,
                    **fields,
                }
            )
            self._started_total += 1
            return stored

    def finish(self, started: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return self._append_locked(
                {
                    "event": "attempt_finished",
                    "attempt_id": started["attempt_id"],
                    "attempt_no": started["attempt_no"],
                    "logical_call_id": started["logical_call_id"],
                    **fields,
                }
            )

    def records(self) -> list[dict[str, Any]]:
        try:
            descriptor = os.open(
                self._name,
                os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=self._dir_descriptor,
            )
        except FileNotFoundError:
            return []
        try:
            _validate_artifact_fd(descriptor, self._name)
            content = _read_all_fd(descriptor).decode("utf-8")
        finally:
            os.close(descriptor)
        return [json.loads(line) for line in content.splitlines() if line]

    def attempts_for(self, logical_call_id: str) -> int:
        with self._lock:
            return self._attempt_counts.get(logical_call_id, 0)

    def __del__(self) -> None:
        descriptor = getattr(self, "_dir_descriptor", None)
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
            self._dir_descriptor = None


def _read_all_fd(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


ProviderRunner = Callable[[Any, str, int], str]


OPENING_META_RULES = (
    (
        "following_material_announcement",
        re.compile(
            r"\A以下(?:に|へ)[^。！？\n]{0,100}"
            r"(?:作成|まとめ|回答|提示)(?:しました|します|いたします|しています)?"
        ),
    ),
    (
        "request_restatement",
        re.compile(
            r"\Aご(?:依頼|指定)(?:いただいた|の)?[^。！？\n]{0,100}"
            r"(?:作成|まとめ|回答|提示)(?:しました|します|いたします|ています)?"
        ),
    ),
    (
        "draft_label",
        re.compile(
            r"\A(?:回答|文面|返信|メール|文章)(?:案|例)"
            r"(?:です|を[^。！？\n]{0,50}(?:作成|まとめ|提示|回答)"
            r"(?:しました|します|いたします)?)"
        ),
    ),
)


def detect_opening_meta(text: str, protocol: dict[str, Any]) -> dict[str, Any]:
    """Classify only the normalized first sentence; never return source text."""
    detector = protocol["opening_meta_detector"]
    version = int(detector["version"])
    limit = int(detector["prefix_codepoints"])
    normalized = unicodedata.normalize("NFKC", text).lstrip()[:limit]
    sentence_end = re.search(r"[。！？\n]", normalized)
    opening = normalized[: sentence_end.end()] if sentence_end else normalized
    for rule_id, regex in OPENING_META_RULES:
        if regex.search(opening):
            return {"version": version, "matched": True, "rule_id": rule_id}
    return {"version": version, "matched": False, "rule_id": None}


def opening_meta_transitions(rows: list[dict[str, bool]]) -> dict[str, dict[str, Any]]:
    contrasts = {
        "framework_without_language": ("base_writer", "framework"),
        "framework_with_language": ("language", "combined"),
    }
    result: dict[str, dict[str, Any]] = {}
    for name, (before_key, after_key) in contrasts.items():
        counts = {
            "removed": 0,
            "introduced": 0,
            "stayed_present": 0,
            "stayed_absent": 0,
        }
        for row in rows:
            before, after = bool(row[before_key]), bool(row[after_key])
            if before and not after:
                counts["removed"] += 1
            elif not before and after:
                counts["introduced"] += 1
            elif before:
                counts["stayed_present"] += 1
            else:
                counts["stayed_absent"] += 1
        result[name] = {
            **counts,
            "net_removed_rate": round(
                (counts["removed"] - counts["introduced"]) / len(rows), 4
            )
            if rows
            else None,
        }
    return result


def invoke_provider_audited(
    *,
    journal: AttemptJournal,
    logical_call_id: str,
    phase: str,
    prompt: str,
    spec: Any,
    provider_metadata: dict[str, Any],
    context: dict[str, Any],
    runner: ProviderRunner,
    parser: Callable[[str], Any] | None = None,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Invoke one provider attempt at a time so retries remain exactly auditable."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    if max_attempts > len(ATTEMPT_BACKOFF_SECONDS):
        raise ValueError("max_attempts exceeds the fingerprinted backoff schedule")
    prior_attempts = journal.attempts_for(logical_call_id)
    if prior_attempts >= max_attempts:
        raise RuntimeError("logical attempt budget exhausted before provider call")
    last_error: Exception | None = None
    for lifetime_attempt_index in range(prior_attempts, max_attempts):
        delay = ATTEMPT_BACKOFF_SECONDS[lifetime_attempt_index]
        if delay:
            time.sleep(delay)
        started = journal.start(
            logical_call_id,
            {
                "phase": phase,
                "prompt_sha256": sha256_text(prompt),
                "provider": provider_metadata["provider"],
                "requested_model": provider_metadata["requested_model"],
                "reported_model": provider_metadata.get("reported_model"),
                "provider_spec_sha256": provider_metadata.get("provider_spec_sha256"),
                **context,
            },
        )
        try:
            output = runner(spec, prompt, 1)
        except Exception as error:
            last_error = error
            journal.finish(
                started,
                {
                    "status": "error",
                    "error_type": type(error).__name__,
                    "output_sha256": None,
                    "reported_model": provider_metadata.get("reported_model"),
                },
            )
            continue
        parsed: Any = None
        if parser is not None:
            try:
                parsed = parser(output)
            except Exception as error:
                last_error = error
                journal.finish(
                    started,
                    {
                        "status": "invalid",
                        "parse_status": "invalid",
                        "error_type": type(error).__name__,
                        "output_sha256": sha256_text(output),
                        "reported_model": provider_metadata.get("reported_model"),
                    },
                )
                continue
        finished = journal.finish(
            started,
            {
                "status": "success",
                "parse_status": "valid" if parser is not None else "not_applicable",
                "error_type": None,
                "output_sha256": sha256_text(output),
                "parsed_result_sha256": canonical_parsed_result_hash(parsed)
                if parser is not None
                else None,
                "reported_model": provider_metadata.get("reported_model"),
            },
        )
        return {"output": output, "parsed": parsed, "finished": finished}
    raise RuntimeError(f"provider failed after {max_attempts} attempts") from last_error


def invoke_provider(
    *,
    journal: AttemptJournal,
    logical_call_id: str,
    phase: str,
    prompt: str,
    spec: Any,
    provider_metadata: dict[str, Any],
    context: dict[str, Any],
    runner: ProviderRunner,
    max_attempts: int = 3,
) -> str:
    return str(
        invoke_provider_audited(
            journal=journal,
            logical_call_id=logical_call_id,
            phase=phase,
            prompt=prompt,
            spec=spec,
            provider_metadata=provider_metadata,
            context=context,
            runner=runner,
            max_attempts=max_attempts,
        )["output"]
    )


def load_dev_cases(path: Path, *, expected_path: Path = DEV_CASES) -> list[dict[str, Any]]:
    """Load exactly the dedicated ten-case dev file, never a mixed-split corpus."""
    if path.is_symlink() or expected_path.is_symlink() or not path.is_file():
        raise ValueError("dedicated dev cases path must be a regular non-symlink file")
    if path.resolve() != expected_path.resolve():
        raise ValueError("refusing anything except the exact dedicated dev cases path")
    if path.name != "parity_cases.dev.json":
        raise ValueError("dedicated dev cases path must be named parity_cases.dev.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    cases = raw.get("cases")
    if not isinstance(cases, list) or len(cases) != EXPECTED_DEV_CASES:
        raise ValueError(f"expected exactly {EXPECTED_DEV_CASES} dev cases")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or case.get("split") != "dev":
            raise ValueError("dedicated cases file must be dev-only")
        normalized_case = {
            "id": str(case.get("id", "")).strip(),
            "split": "dev",
            "category": str(case.get("category", "")).strip(),
            "prompt": str(case.get("prompt", "")).strip(),
        }
        if not all(normalized_case.values()):
            raise ValueError("every dev case requires id, split, category, and prompt")
        if normalized_case["id"] in seen:
            raise ValueError(f"duplicate dev case id: {normalized_case['id']}")
        seen.add(normalized_case["id"])
        normalized.append(normalized_case)
    return normalized


def _validate_cases(cases: list[dict[str, Any]]) -> None:
    if len(cases) != EXPECTED_DEV_CASES:
        raise ValueError(f"expected exactly {EXPECTED_DEV_CASES} dev cases before provider calls")
    ids = [case.get("id") for case in cases]
    if len(set(ids)) != EXPECTED_DEV_CASES or any(case.get("split") != "dev" for case in cases):
        raise ValueError("evaluation cases must be ten unique dev-only cases")
    if any(not case.get("prompt") or not case.get("category") for case in cases):
        raise ValueError("every evaluation case requires prompt and category")


def _load_checkpoint(
    path: Path, fingerprint: str, *, run_dir_fd: int | None = None
) -> dict[str, Any]:
    dir_descriptor = (
        os.dup(run_dir_fd)
        if run_dir_fd is not None
        else _open_verified_run_dir(Path(os.path.abspath(path)).parent, create=False)
    )
    try:
        exists = path.name in os.listdir(dir_descriptor)
    finally:
        os.close(dir_descriptor)
    if not exists:
        return {
            "schema": SCHEMA,
            "fingerprint": fingerprint,
            "generations": {},
            "judgments": {},
        }
    state = _read_secure_json_artifact(path, run_dir_fd=run_dir_fd)
    if state.get("schema") != SCHEMA or state.get("fingerprint") != fingerprint:
        raise ValueError("checkpoint fingerprint mismatch")
    return state


def _run_jobs(
    jobs: list[tuple[str, Callable[[], dict[str, Any]]]],
    *,
    parallel: int,
    on_result: Callable[[str, dict[str, Any]], None],
) -> None:
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {pool.submit(function): key for key, function in jobs}
        for future in as_completed(futures):
            key = futures[future]
            try:
                on_result(key, future.result())
            except Exception as error:
                errors.append(f"{key}:{type(error).__name__}")
    if errors:
        raise RuntimeError("provider jobs failed: " + ",".join(sorted(errors)))


def _generation_key(case_id: str, role: str, arm: str | None = None) -> str:
    return f"{case_id}::{role}" + (f"::{arm}" if arm else "")


def _judgment_key(case_id: str, arm: str, order: str) -> str:
    return f"{case_id}::{arm}::{order}"


def _generate_all(
    *,
    cases: list[dict[str, Any]],
    assets: dict[str, str],
    protocol: dict[str, Any],
    specs: dict[str, Any],
    providers: dict[str, dict[str, Any]],
    runner: ProviderRunner,
    journal: AttemptJournal,
    state: dict[str, Any],
    checkpoint_path: Path,
    run_dir_fd: int,
    max_attempts: int,
    parallel: int,
) -> None:
    jobs: list[tuple[str, Callable[[], dict[str, Any]]]] = []
    for case in cases:
        case_id = case["id"]
        reference_key = _generation_key(case_id, "reference")
        if reference_key not in state["generations"]:
            prompt = case["prompt"]

            def generate_reference(
                key: str = reference_key,
                actual_prompt: str = prompt,
                cid: str = case_id,
            ) -> dict[str, Any]:
                call = invoke_provider_audited(
                    journal=journal,
                    logical_call_id=f"gen:{cid}:reference",
                    phase="generation",
                    prompt=actual_prompt,
                    spec=specs["reference"],
                    provider_metadata=providers["reference"],
                    context={"case_id": cid, "arm": "reference", "order": None},
                    runner=runner,
                    max_attempts=max_attempts,
                )
                return {
                    "logical_call_id": f"gen:{cid}:reference",
                    "case_id": cid,
                    "role": "reference",
                    "arm": "reference",
                    "order": None,
                    "provider": providers["reference"]["provider"],
                    "requested_model": providers["reference"]["requested_model"],
                    "prompt_sha256": sha256_text(actual_prompt),
                    "output_sha256": sha256_text(call["output"]),
                    "completed_attempt_id": call["finished"]["attempt_id"],
                    "provider_spec_sha256": providers["reference"]["provider_spec_sha256"],
                    "text": call["output"],
                }

            jobs.append((reference_key, generate_reference))
        for arm in protocol["arms"]:
            candidate_key = _generation_key(case_id, "candidate", arm)
            if candidate_key in state["generations"]:
                continue
            prompt, _components = compose_candidate_prompt(case["prompt"], arm, assets, protocol)

            def generate_candidate(
                actual_prompt: str = prompt,
                cid: str = case_id,
                actual_arm: str = arm,
            ) -> dict[str, Any]:
                logical_id = f"gen:{cid}:candidate:{actual_arm}"
                call = invoke_provider_audited(
                    journal=journal,
                    logical_call_id=logical_id,
                    phase="generation",
                    prompt=actual_prompt,
                    spec=specs["candidate"],
                    provider_metadata=providers["candidate"],
                    context={"case_id": cid, "arm": actual_arm, "order": None},
                    runner=runner,
                    max_attempts=max_attempts,
                )
                return {
                    "logical_call_id": logical_id,
                    "case_id": cid,
                    "role": "candidate",
                    "arm": actual_arm,
                    "order": None,
                    "provider": providers["candidate"]["provider"],
                    "requested_model": providers["candidate"]["requested_model"],
                    "prompt_sha256": sha256_text(actual_prompt),
                    "output_sha256": sha256_text(call["output"]),
                    "completed_attempt_id": call["finished"]["attempt_id"],
                    "provider_spec_sha256": providers["candidate"]["provider_spec_sha256"],
                    "text": call["output"],
                }

            jobs.append((candidate_key, generate_candidate))

    def record(key: str, value: dict[str, Any]) -> None:
        state["generations"][key] = value
        save_secure_json(checkpoint_path, state, run_dir_fd=run_dir_fd)

    _run_jobs(jobs, parallel=parallel, on_result=record)


def _judge_all(
    *,
    cases: list[dict[str, Any]],
    protocol: dict[str, Any],
    specs: dict[str, Any],
    providers: dict[str, dict[str, Any]],
    runner: ProviderRunner,
    journal: AttemptJournal,
    state: dict[str, Any],
    checkpoint_path: Path,
    run_dir_fd: int,
    judgment_prompt_fn: Callable[[str, str, str, str], tuple[str, dict[str, str]]],
    parse_judgment_fn: Callable[[str, str], dict[str, Any]],
    normalize_winner_fn: Callable[[str, dict[str, str]], str],
    max_attempts: int,
    parallel: int,
) -> None:
    jobs: list[tuple[str, Callable[[], dict[str, Any]]]] = []
    for case in cases:
        case_id = case["id"]
        reference_key = _generation_key(case_id, "reference")
        reference_record = state["generations"][reference_key]
        for arm in protocol["arms"]:
            candidate_key = _generation_key(case_id, "candidate", arm)
            candidate_record = state["generations"][candidate_key]
            for order in protocol["orders"]:
                key = _judgment_key(case_id, arm, order)
                if key in state["judgments"]:
                    continue
                prompt, mapping = judgment_prompt_fn(
                    case["prompt"], reference_record["text"], candidate_record["text"], order
                )
                prompt = augment_judgment_prompt(prompt, case["category"], protocol)

                def parse_and_normalize(
                    raw: str,
                    actual_mapping: dict[str, str] = mapping,
                    actual_order: str = order,
                    actual_category: str = case["category"],
                ) -> dict[str, Any]:
                    parsed = parse_and_validate_judgment(
                        raw, parse_judgment_fn, protocol, actual_category
                    )
                    parsed["normalized_winner"] = normalize_winner_fn(
                        parsed["winner"], actual_mapping
                    )
                    parsed["order"] = actual_order
                    if actual_category == protocol["support_safety"]["category"]:
                        parsed["support_safety"] = normalize_support_safety(
                            parsed, actual_mapping
                        )
                        del parsed["support_safety_by_answer"]
                    return parsed

                def judge_one(
                    actual_prompt: str = prompt,
                    cid: str = case_id,
                    actual_arm: str = arm,
                    actual_order: str = order,
                    actual_category: str = case["category"],
                    ref_key: str = reference_key,
                    ref_record: dict[str, Any] = reference_record,
                    cand_key: str = candidate_key,
                    cand_record: dict[str, Any] = candidate_record,
                    parser: Callable[[str], dict[str, Any]] = parse_and_normalize,
                ) -> dict[str, Any]:
                    logical_id = f"judge:{cid}:{actual_arm}:{actual_order}"
                    call = invoke_provider_audited(
                        journal=journal,
                        logical_call_id=logical_id,
                        phase="judgment",
                        prompt=actual_prompt,
                        spec=specs["judge"],
                        provider_metadata=providers["judge"],
                        context={
                            "case_id": cid,
                            "category": actual_category,
                            "arm": actual_arm,
                            "order": actual_order,
                            "reference_call_id": ref_record["logical_call_id"],
                            "reference_output_sha256": ref_record["output_sha256"],
                            "candidate_call_id": cand_record["logical_call_id"],
                            "candidate_output_sha256": cand_record["output_sha256"],
                        },
                        runner=runner,
                        parser=parser,
                        max_attempts=max_attempts,
                    )
                    parsed = call["parsed"]
                    parsed_hash = canonical_parsed_result_hash(parsed)
                    return {
                        **parsed,
                        "logical_call_id": logical_id,
                        "case_id": cid,
                        "category": actual_category,
                        "role": "judge",
                        "arm": actual_arm,
                        "provider": providers["judge"]["provider"],
                        "requested_model": providers["judge"]["requested_model"],
                        "parsed_result_sha256": parsed_hash,
                        "prompt_sha256": sha256_text(actual_prompt),
                        "output_sha256": sha256_text(call["output"]),
                        "completed_attempt_id": call["finished"]["attempt_id"],
                        "provider_spec_sha256": providers["judge"]["provider_spec_sha256"],
                        "reference_generation_key": ref_key,
                        "reference_call_id": ref_record["logical_call_id"],
                        "reference_output_sha256": ref_record["output_sha256"],
                        "candidate_generation_key": cand_key,
                        "candidate_call_id": cand_record["logical_call_id"],
                        "candidate_output_sha256": cand_record["output_sha256"],
                    }

                jobs.append((key, judge_one))

    def record(key: str, value: dict[str, Any]) -> None:
        state["judgments"][key] = value
        save_secure_json(checkpoint_path, state, run_dir_fd=run_dir_fd)

    _run_jobs(jobs, parallel=parallel, on_result=record)


def _mean(values: list[float]) -> float:
    return statistics.mean(values)


def parse_and_validate_judgment(
    raw_output: str,
    parser: Callable[[str, str], dict[str, Any]],
    protocol: dict[str, Any],
    category: str,
) -> dict[str, Any]:
    parsed = parser(raw_output, category)
    return validate_parsed_judgment(parsed, protocol, category=category)


def augment_judgment_prompt(
    prompt: str, category: str, protocol: dict[str, Any]
) -> str:
    if category != protocol["support_safety"]["category"]:
        return prompt
    return f"{prompt}\n\n{protocol['support_safety']['prompt_suffix']}"


def parse_raw_judgment_then_normalize(
    raw_output: str,
    parity: Any,
    protocol: dict[str, Any],
    category: str | None = None,
) -> dict[str, Any]:
    """Validate the unnormalized JSON object before parity can coerce defaults."""
    try:
        raw = json.loads(raw_output)
    except json.JSONDecodeError as error:
        raise ValueError("raw judge output must be one JSON object") from error
    expected_keys = {
        "winner",
        "confidence",
        "dimensions",
        "reason",
    }
    if category == protocol["support_safety"]["category"]:
        expected_keys.add("support_safety")
    if not isinstance(raw, dict) or set(raw) != expected_keys:
        raise ValueError("raw judge object has an invalid schema")
    allowed = {"A", "B", "draw"}
    dimensions = raw.get("dimensions")
    if (
        raw.get("winner") not in allowed
        or not isinstance(dimensions, dict)
        or set(dimensions) != set(protocol["dimensions"])
        or any(value not in allowed for value in dimensions.values())
    ):
        raise ValueError("raw judge winner or dimensions are invalid")
    confidence = raw.get("confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= float(confidence) <= 1
        or not isinstance(raw.get("reason"), str)
    ):
        raise ValueError("raw judge confidence or reason is invalid")
    safety: dict[str, dict[str, bool]] | None = None
    if category == protocol["support_safety"]["category"]:
        safety = raw["support_safety"]
        expected_fields = set(protocol["support_safety"]["fields"])
        if (
            not isinstance(safety, dict)
            or set(safety) != {"A", "B"}
            or any(not isinstance(safety.get(answer), dict) for answer in ("A", "B"))
            or any(set(safety[answer]) != expected_fields for answer in ("A", "B"))
            or any(
                type(value) is not bool
                for answer in ("A", "B")
                for value in safety[answer].values()
            )
        ):
            raise ValueError("raw judge support safety schema is invalid")
    parsed = parity.parse_judgment(raw_output)
    if safety is not None:
        parsed["support_safety_by_answer"] = safety
    return parsed


def normalize_support_safety(
    parsed: dict[str, Any], mapping: dict[str, str]
) -> dict[str, dict[str, bool]]:
    safety = parsed["support_safety_by_answer"]
    answer_by_role = {
        role: answer
        for answer, role in mapping.items()
        if answer in {"A", "B"} and role in {"candidate", "reference"}
    }
    if set(answer_by_role) != {"candidate", "reference"}:
        raise ValueError("judgment mapping does not identify both answer roles")
    return {
        role: dict(safety[answer_by_role[role]])
        for role in ("candidate", "reference")
    }


def validate_parsed_judgment(
    parsed: dict[str, Any],
    protocol: dict[str, Any],
    *,
    category: str | None = None,
) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        raise ValueError("judge result must be an object")
    allowed = {"A", "B", "draw"}
    if parsed.get("winner") not in allowed:
        raise ValueError("judge winner must be A, B, or draw")
    dimensions = parsed.get("dimensions")
    if not isinstance(dimensions, dict) or set(dimensions) != set(protocol["dimensions"]):
        raise ValueError("judge must return exactly the five protocol dimensions")
    if any(value not in allowed for value in dimensions.values()):
        raise ValueError("dimension winner must be A, B, or draw")
    confidence = parsed.get("confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= float(confidence) <= 1
    ):
        raise ValueError("judge confidence must be between zero and one")
    if not isinstance(parsed.get("reason"), str):
        raise ValueError("judge reason must be a string")
    if category == protocol["support_safety"]["category"]:
        safety = parsed.get("support_safety_by_answer")
        fields = set(protocol["support_safety"]["fields"])
        if (
            not isinstance(safety, dict)
            or set(safety) != {"A", "B"}
            or any(not isinstance(safety.get(answer), dict) for answer in ("A", "B"))
            or any(set(safety[answer]) != fields for answer in ("A", "B"))
            or any(
                type(value) is not bool
                for answer in ("A", "B")
                for value in safety[answer].values()
            )
        ):
            raise ValueError("judge support safety result is invalid")
    return parsed


def canonical_parsed_result_hash(parsed: dict[str, Any]) -> str:
    keys = {
        "winner",
        "confidence",
        "dimensions",
        "reason",
        "normalized_winner",
        "order",
    }
    payload = {key: parsed[key] for key in keys} if keys.issubset(parsed) else parsed
    if "support_safety" in parsed:
        payload["support_safety"] = parsed["support_safety"]
    return canonical_sha256(payload)


def _round(value: float) -> float:
    return round(value, 4)


def _candidate_points(winner: str, protocol: dict[str, Any]) -> float:
    try:
        return float(protocol["scoring"][winner])
    except KeyError as error:
        raise ValueError(f"invalid normalized winner: {winner}") from error


def _dimension_points(verdict: dict[str, Any], dimension: str, protocol: dict[str, Any]) -> float:
    mapping = (
        {"A": "reference", "B": "candidate", "draw": "draw"}
        if verdict["order"] == "reference_first"
        else {"A": "candidate", "B": "reference", "draw": "draw"}
    )
    return _candidate_points(mapping[verdict["dimensions"][dimension]], protocol)


def _wtl(deltas: list[float]) -> dict[str, int]:
    return {
        "wins": sum(delta > 1e-12 for delta in deltas),
        "ties": sum(abs(delta) <= 1e-12 for delta in deltas),
        "losses": sum(delta < -1e-12 for delta in deltas),
    }


def _reconcile_completions(
    state: dict[str, Any], journal_records: list[dict[str, Any]]
) -> None:
    starts: dict[str, dict[str, Any]] = {}
    finishes: dict[str, dict[str, Any]] = {}
    for record in journal_records:
        target = starts if record["event"] == "attempt_started" else finishes
        attempt_id = record["attempt_id"]
        if attempt_id in target:
            raise ValueError("duplicate attempt journal event")
        target[attempt_id] = record
    for phase, records in (
        ("generation", state["generations"]),
        ("judgment", state["judgments"]),
    ):
        for completed in records.values():
            attempt_id = completed["completed_attempt_id"]
            start = starts.get(attempt_id)
            finish = finishes.get(attempt_id)
            if start is None or finish is None:
                raise ValueError("checkpoint completion lacks journal start/finish")
            if (
                start["phase"] != phase
                or start["logical_call_id"] != completed["logical_call_id"]
                or start["prompt_sha256"] != completed["prompt_sha256"]
                or finish["status"] != "success"
                or finish["output_sha256"] != completed["output_sha256"]
                or finish.get("parse_status")
                != ("valid" if phase == "judgment" else "not_applicable")
                or (
                    phase == "judgment"
                    and finish.get("parsed_result_sha256")
                    != completed.get("parsed_result_sha256")
                )
            ):
                raise ValueError("checkpoint completion does not match attempt journal")
            if phase == "judgment" and (
                start["reference_call_id"] != completed["reference_call_id"]
                or start["reference_output_sha256"]
                != completed["reference_output_sha256"]
                or start["candidate_call_id"] != completed["candidate_call_id"]
                or start["candidate_output_sha256"]
                != completed["candidate_output_sha256"]
            ):
                raise ValueError("judgment input hashes do not match attempt journal")


def validate_checkpoint_integrity(
    *,
    state: dict[str, Any],
    cases: list[dict[str, Any]],
    assets: dict[str, str],
    protocol: dict[str, Any],
    providers: dict[str, dict[str, Any]],
    journal_records: list[dict[str, Any]],
    judgment_prompt_fn: Callable[[str, str, str, str], tuple[str, dict[str, str]]],
    normalize_winner_fn: Callable[[str, dict[str, str]], str],
) -> None:
    """Recompute every checkpoint binding before a resumed provider call."""
    case_by_id = {case["id"]: case for case in cases}
    starts = {
        record["attempt_id"]: record
        for record in journal_records
        if record.get("event") == "attempt_started"
    }
    try:
        for key, record in state["generations"].items():
            parts = key.split("::")
            case_id = parts[0]
            case = case_by_id[case_id]
            if len(parts) == 2 and parts[1] == "reference":
                role, arm = "reference", "reference"
                logical_id = f"gen:{case_id}:reference"
                prompt = case["prompt"]
            elif (
                len(parts) == 3
                and parts[1] == "candidate"
                and parts[2] in protocol["arms"]
            ):
                role, arm = "candidate", parts[2]
                logical_id = f"gen:{case_id}:candidate:{arm}"
                prompt, _components = compose_candidate_prompt(
                    case["prompt"], arm, assets, protocol
                )
            else:
                raise ValueError("invalid generation key")
            expected_fields = {
                "logical_call_id": logical_id,
                "case_id": case_id,
                "role": role,
                "arm": arm,
                "order": None,
                "provider": providers[role]["provider"],
                "requested_model": providers[role]["requested_model"],
                "provider_spec_sha256": providers[role]["provider_spec_sha256"],
                "prompt_sha256": sha256_text(prompt),
            }
            if any(record.get(field) != value for field, value in expected_fields.items()):
                raise ValueError("generation context mismatch")
            if not isinstance(record.get("text"), str) or record.get(
                "output_sha256"
            ) != sha256_text(record["text"]):
                raise ValueError("generation text hash mismatch")
            start = starts[record["completed_attempt_id"]]
            start_expected = {
                "phase": "generation",
                "logical_call_id": logical_id,
                "case_id": case_id,
                "arm": arm,
                "order": None,
                "provider": providers[role]["provider"],
                "requested_model": providers[role]["requested_model"],
                "provider_spec_sha256": providers[role]["provider_spec_sha256"],
                "prompt_sha256": sha256_text(prompt),
            }
            if any(start.get(field) != value for field, value in start_expected.items()):
                raise ValueError("generation journal context mismatch")

        for key, record in state["judgments"].items():
            parts = key.split("::")
            if (
                len(parts) != 3
                or parts[0] not in case_by_id
                or parts[1] not in protocol["arms"]
                or parts[2] not in protocol["orders"]
            ):
                raise ValueError("invalid judgment key")
            case_id, arm, order = parts
            case = case_by_id[case_id]
            reference_key = _generation_key(case_id, "reference")
            candidate_key = _generation_key(case_id, "candidate", arm)
            reference = state["generations"][reference_key]
            candidate = state["generations"][candidate_key]
            prompt, mapping = judgment_prompt_fn(
                case["prompt"], reference["text"], candidate["text"], order
            )
            prompt = augment_judgment_prompt(prompt, case["category"], protocol)
            logical_id = f"judge:{case_id}:{arm}:{order}"
            expected_fields = {
                "logical_call_id": logical_id,
                "case_id": case_id,
                "category": case["category"],
                "role": "judge",
                "arm": arm,
                "order": order,
                "provider": providers["judge"]["provider"],
                "requested_model": providers["judge"]["requested_model"],
                "provider_spec_sha256": providers["judge"]["provider_spec_sha256"],
                "prompt_sha256": sha256_text(prompt),
                "reference_generation_key": reference_key,
                "reference_call_id": reference["logical_call_id"],
                "reference_output_sha256": reference["output_sha256"],
                "candidate_generation_key": candidate_key,
                "candidate_call_id": candidate["logical_call_id"],
                "candidate_output_sha256": candidate["output_sha256"],
            }
            if any(record.get(field) != value for field, value in expected_fields.items()):
                raise ValueError("judgment context mismatch")
            validate_parsed_judgment(record, protocol)
            validate_normalized_support_safety(record, case["category"], protocol)
            if record["normalized_winner"] != normalize_winner_fn(record["winner"], mapping):
                raise ValueError("normalized winner mismatch")
            parsed_payload = {
                key: record[key]
                for key in (
                    "winner",
                    "confidence",
                    "dimensions",
                    "reason",
                    "normalized_winner",
                    "order",
                )
            }
            if case["category"] == protocol["support_safety"]["category"]:
                parsed_payload["support_safety"] = record["support_safety"]
            if record.get("parsed_result_sha256") != canonical_sha256(parsed_payload):
                raise ValueError("parsed judgment hash mismatch")
            start = starts[record["completed_attempt_id"]]
            start_expected = {
                "phase": "judgment",
                "logical_call_id": logical_id,
                "case_id": case_id,
                "category": case["category"],
                "arm": arm,
                "order": order,
                "provider": providers["judge"]["provider"],
                "requested_model": providers["judge"]["requested_model"],
                "provider_spec_sha256": providers["judge"]["provider_spec_sha256"],
                "prompt_sha256": sha256_text(prompt),
                "reference_call_id": reference["logical_call_id"],
                "reference_output_sha256": reference["output_sha256"],
                "candidate_call_id": candidate["logical_call_id"],
                "candidate_output_sha256": candidate["output_sha256"],
            }
            if any(start.get(field) != value for field, value in start_expected.items()):
                raise ValueError("judgment journal context mismatch")
        _reconcile_completions(state, journal_records)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("checkpoint integrity validation failed") from error


def validate_normalized_support_safety(
    parsed: dict[str, Any], category: str, protocol: dict[str, Any]
) -> None:
    expected_category = protocol["support_safety"]["category"]
    if category != expected_category:
        if "support_safety" in parsed:
            raise ValueError("non-support judgment has support safety data")
        return
    safety = parsed.get("support_safety")
    fields = set(protocol["support_safety"]["fields"])
    if (
        not isinstance(safety, dict)
        or set(safety) != {"candidate", "reference"}
        or any(not isinstance(safety.get(role), dict) for role in safety)
        or any(set(safety[role]) != fields for role in safety)
        or any(
            type(value) is not bool
            for role in ("candidate", "reference")
            for value in safety[role].values()
        )
    ):
        raise ValueError("normalized support safety result is invalid")


def _summarize(
    *,
    cases: list[dict[str, Any]],
    protocol: dict[str, Any],
    providers: dict[str, dict[str, Any]],
    manifest: dict[str, Any],
    manifest_sha256: str,
    journal: AttemptJournal,
    state: dict[str, Any],
) -> dict[str, Any]:
    arms = tuple(protocol["arms"])
    dimensions = tuple(protocol["dimensions"])
    expected_generation_keys = {
        _generation_key(case["id"], "reference") for case in cases
    } | {
        _generation_key(case["id"], "candidate", arm) for case in cases for arm in arms
    }
    expected_judgment_keys = {
        _judgment_key(case["id"], arm, order)
        for case in cases
        for arm in arms
        for order in protocol["orders"]
    }
    if set(state["generations"]) != expected_generation_keys:
        raise ValueError("generation checkpoint is incomplete or contains unexpected keys")
    if set(state["judgments"]) != expected_judgment_keys:
        raise ValueError("judgment checkpoint is incomplete or contains unexpected keys")

    scores: dict[str, dict[str, float]] = {arm: {} for arm in arms}
    dimension_scores: dict[str, dict[str, dict[str, float]]] = {arm: {} for arm in arms}
    arm_metrics: dict[str, Any] = {}
    opening_rows: list[dict[str, bool]] = [dict() for _ in cases]
    for arm in arms:
        consistency = 0
        category_values: dict[str, list[float]] = {}
        dimension_values: dict[str, list[float]] = {dimension: [] for dimension in dimensions}
        opening_count = 0
        for index, case in enumerate(cases):
            verdicts = [
                state["judgments"][_judgment_key(case["id"], arm, order)]
                for order in protocol["orders"]
            ]
            score = _mean(
                [_candidate_points(verdict["normalized_winner"], protocol) for verdict in verdicts]
            )
            scores[arm][case["id"]] = score
            category_values.setdefault(case["category"], []).append(score)
            dimension_scores[arm][case["id"]] = {}
            for dimension in dimensions:
                value = _mean(
                    [_dimension_points(verdict, dimension, protocol) for verdict in verdicts]
                )
                dimension_scores[arm][case["id"]][dimension] = value
                dimension_values[dimension].append(value)
            consistency += verdicts[0]["normalized_winner"] == verdicts[1]["normalized_winner"]
            generated = state["generations"][_generation_key(case["id"], "candidate", arm)][
                "text"
            ]
            flagged = bool(detect_opening_meta(generated, protocol)["matched"])
            opening_rows[index][arm] = flagged
            opening_count += flagged
        arm_metrics[arm] = {
            "candidate_preference": _round(_mean(list(scores[arm].values()))),
            "order_consistency": _round(consistency / len(cases)),
            "categories": {
                category: _round(_mean(values))
                for category, values in sorted(category_values.items())
            },
            "dimensions": {
                dimension: _round(_mean(values))
                for dimension, values in dimension_values.items()
            },
            "opening_meta": {
                "detector_version": protocol["opening_meta_detector"]["version"],
                "cases_flagged": opening_count,
                "rate": _round(opening_count / len(cases)),
            },
        }

    contrast_definitions = {
        "framework_without_language": ("framework", "base_writer"),
        "framework_with_language": ("combined", "language"),
        "language_without_framework": ("language", "base_writer"),
        "language_with_framework": ("combined", "framework"),
    }
    contrasts: dict[str, Any] = {}
    for name, (high, low) in contrast_definitions.items():
        deltas = [scores[high][case["id"]] - scores[low][case["id"]] for case in cases]
        contrasts[name] = {
            "mean_effect": _round(_mean(deltas)),
            "wins_ties_losses": _wtl(deltas),
            "non_regressing_cases": sum(delta >= -1e-12 for delta in deltas),
            "dimension_effects": {
                dimension: _round(
                    _mean(
                        [
                            dimension_scores[high][case["id"]][dimension]
                            - dimension_scores[low][case["id"]][dimension]
                            for case in cases
                        ]
                    )
                )
                for dimension in dimensions
            },
        }

    factorial: dict[str, Any] = {}
    raw_factorial: dict[str, dict[str, float]] = {}
    for metric in ("overall", *dimensions):
        def metric_score(arm: str, case_id: str) -> float:
            if metric == "overall":
                return scores[arm][case_id]
            return dimension_scores[arm][case_id][metric]

        framework_effects = [
            (
                metric_score("framework", case["id"])
                - metric_score("base_writer", case["id"])
                + metric_score("combined", case["id"])
                - metric_score("language", case["id"])
            )
            / 2
            for case in cases
        ]
        language_effects = [
            (
                metric_score("language", case["id"])
                - metric_score("base_writer", case["id"])
                + metric_score("combined", case["id"])
                - metric_score("framework", case["id"])
            )
            / 2
            for case in cases
        ]
        interactions = [
            metric_score("combined", case["id"])
            - metric_score("framework", case["id"])
            - metric_score("language", case["id"])
            + metric_score("base_writer", case["id"])
            for case in cases
        ]
        raw_factorial[metric] = {
            "framework_main_effect": _mean(framework_effects),
            "language_main_effect": _mean(language_effects),
            "interaction": _mean(interactions),
        }
        factorial[metric] = {key: _round(value) for key, value in raw_factorial[metric].items()}

    paired_rows = []
    for case in cases:
        case_id = case["id"]
        paired_rows.append(
            {
                "id": case_id,
                "category": case["category"],
                "scores": {arm: _round(scores[arm][case_id]) for arm in arms},
                "language_contrasts": {
                    "language_minus_base_writer": _round(
                        scores["language"][case_id] - scores["base_writer"][case_id]
                    ),
                    "combined_minus_framework": _round(
                        scores["combined"][case_id] - scores["framework"][case_id]
                    ),
                },
            }
        )
    joint_nonregressing = sum(
        row["language_contrasts"]["language_minus_base_writer"] >= -1e-12
        and row["language_contrasts"]["combined_minus_framework"] >= -1e-12
        for row in paired_rows
    )
    exact_order_consistency = _mean(
        [arm_metrics[arm]["order_consistency"] for arm in arms]
    )
    acceptance = protocol["acceptance"]
    language_effect = raw_factorial["overall"]["language_main_effect"]
    naturalness_effect = raw_factorial["naturalness"]["language_main_effect"]
    guards = {
        dimension: raw_factorial[dimension]["language_main_effect"]
        for dimension in ("correctness", "context_fit", "tone")
    }
    support_case_ids = {
        case["id"]
        for case in cases
        if case["category"] == protocol["support_safety"]["category"]
    }
    support_records = [
        state["judgments"][_judgment_key(case_id, arm, order)]
        for case_id in sorted(support_case_ids)
        for arm in arms
        for order in protocol["orders"]
    ]
    support_total = len(support_records)
    support_fields = tuple(protocol["support_safety"]["fields"])
    support_field_aggregates = {
        field: {
            "true": sum(record["support_safety"]["candidate"][field] for record in support_records),
            "total": support_total,
            "all": all(
                record["support_safety"]["candidate"][field]
                for record in support_records
            ),
        }
        for field in support_fields
    }
    support_arm_aggregates = {
        arm: {
            field: {
                "true": sum(
                    state["judgments"][_judgment_key(case_id, arm, order)][
                        "support_safety"
                    ]["candidate"][field]
                    for case_id in support_case_ids
                    for order in protocol["orders"]
                ),
                "total": len(support_case_ids) * len(protocol["orders"]),
                "all": all(
                    state["judgments"][_judgment_key(case_id, arm, order)][
                        "support_safety"
                    ]["candidate"][field]
                    for case_id in support_case_ids
                    for order in protocol["orders"]
                ),
            }
            for field in support_fields
        }
        for arm in arms
    }
    support_safety_passed = bool(support_records) and all(
        aggregate["all"] for aggregate in support_field_aggregates.values()
    )
    checks = {
        "support_safety": support_safety_passed,
        "pooled_language_at_least_threshold": language_effect
        >= float(acceptance["pooled_language_main_effect"]) - 1e-12,
        "naturalness_language_at_least_threshold": naturalness_effect
        >= float(acceptance["naturalness_language_main_effect"]) - 1e-12,
        "guard_dimensions_at_least_minimum": all(
            value >= float(acceptance["guard_dimensions_minimum"]) - 1e-12
            for value in guards.values()
        ),
        "joint_nonregressing_cases_at_least_threshold": joint_nonregressing
        >= int(acceptance["joint_nonregressing_cases"]),
        "overall_order_consistency_at_least_threshold": exact_order_consistency
        >= float(acceptance["overall_order_consistency"]) - 1e-12,
    }

    journal_records = journal.records()
    _reconcile_completions(state, journal_records)
    starts = [record for record in journal_records if record["event"] == "attempt_started"]
    finishes = [record for record in journal_records if record["event"] == "attempt_finished"]
    finished_ids = {record["attempt_id"] for record in finishes}
    attempts = {
        "started": len(starts),
        "finished": len(finishes),
        "succeeded": sum(record["status"] == "success" for record in finishes),
        "failed": sum(record["status"] != "success" for record in finishes),
        "indeterminate": sum(record["attempt_id"] not in finished_ids for record in starts),
    }
    reference_reuse: dict[str, Any] = {}
    for case in cases:
        related = [
            state["judgments"][_judgment_key(case["id"], arm, order)]
            for arm in arms
            for order in protocol["orders"]
        ]
        ids = {record["reference_call_id"] for record in related}
        hashes = {record["reference_output_sha256"] for record in related}
        reference_reuse[case["id"]] = {
            "reference_call_id": next(iter(ids)) if len(ids) == 1 else None,
            "reference_output_sha256": next(iter(hashes)) if len(hashes) == 1 else None,
            "judgment_uses": len(related),
            "distinct_reference_ids": len(ids),
            "distinct_reference_hashes": len(hashes),
        }

    return {
        "schema_version": SCHEMA,
        "scope": "strict dev-only paired 2x2 prompt evaluation",
        "fingerprint": manifest["fingerprint"],
        "models": providers,
        "counts": {
            "cases": len(cases),
            "reference_generations": len(cases),
            "candidate_generations": len(cases) * len(arms),
            "generations_total": len(cases) * (1 + len(arms)),
            "judgments": len(cases) * len(arms) * len(protocol["orders"]),
            "orders_per_arm_case": len(protocol["orders"]),
        },
        "provenance": {
            "run_id": manifest["run_id"],
            "run_mode": manifest["run_mode"],
            "manifest_sha256": manifest_sha256,
            "attempts": attempts,
            "reference_reuse": reference_reuse,
            "generation_prompt_hashes": {
                key: value["prompt_sha256"] for key, value in state["generations"].items()
            },
            "judgment_prompt_hashes": {
                key: value["prompt_sha256"] for key, value in state["judgments"].items()
            },
        },
        "arms": arm_metrics,
        "contrasts": contrasts,
        "factorial_effects": factorial,
        "paired_case_scores": paired_rows,
        "opening_meta_manipulation": {
            "detector_version": protocol["opening_meta_detector"]["version"],
            "paired_transitions": opening_meta_transitions(opening_rows),
            "acceptance_gating": False,
        },
        "support_safety": {
            "cases": len(support_case_ids),
            "judgments": support_total,
            "fields": support_field_aggregates,
            "arms": support_arm_aggregates,
            "passed": support_safety_passed,
        },
        "language_acceptance_observed": {
            "pooled_language_main_effect": _round(language_effect),
            "naturalness_language_main_effect": _round(naturalness_effect),
            "guard_dimension_language_effects": {
                key: _round(value) for key, value in guards.items()
            },
            "joint_nonregressing_cases": joint_nonregressing,
            "overall_order_consistency": _round(exact_order_consistency),
            "checks": checks,
            "accepted": all(checks.values()),
        },
    }


def _run_evaluation_unlocked(
    *,
    run_dir: Path,
    run_dir_fd: int,
    run_mode: str,
    run_id: str,
    fingerprint_inputs: dict[str, Any],
    cases: list[dict[str, Any]],
    assets: dict[str, str],
    protocol: dict[str, Any],
    specs: dict[str, Any],
    providers: dict[str, dict[str, Any]],
    runner: ProviderRunner,
    judgment_prompt_fn: Callable[[str, str, str, str], tuple[str, dict[str, str]]],
    parse_judgment_fn: Callable[[str, str], dict[str, Any]],
    normalize_winner_fn: Callable[[str, dict[str, str]], str],
    max_attempts: int = 3,
    parallel: int = 6,
) -> dict[str, Any]:
    _validate_cases(cases)
    if set(assets) != {"persona", "instruction", "framework", "language"}:
        raise ValueError("exactly four named prompt assets are required")
    if set(specs) != {"reference", "candidate", "judge"}:
        raise ValueError("exactly reference, candidate, and judge specs are required")
    if set(providers) != set(specs):
        raise ValueError("provider metadata must cover every provider spec")
    retry_policy = protocol.get("retry_policy", {})
    pinned_attempts = int(retry_policy.get("max_attempts_per_logical_call", 0))
    if max_attempts != pinned_attempts:
        raise ValueError("max attempts must match the fingerprinted retry policy")
    planned = fingerprint_inputs.get("inputs", {}).get("planned_generation_calls")
    if planned != plan_generation_calls(cases, assets, protocol):
        raise ValueError("planned generation prompt hashes do not match runtime prompts")
    manifest = prepare_run(
        run_dir,
        run_mode=run_mode,
        fingerprint_inputs=fingerprint_inputs,
        run_id=run_id,
        run_dir_fd=run_dir_fd,
    )
    manifest_path = run_dir.resolve() / "manifest.json"
    checkpoint_path = run_dir.resolve() / "checkpoint.json"
    journal = AttemptJournal(
        run_dir.resolve() / "calls.jsonl",
        fingerprint=manifest["fingerprint"],
        lifetime_attempt_budget=int(retry_policy["lifetime_attempt_budget"]),
        run_dir_fd=run_dir_fd,
    )
    state = _load_checkpoint(
        checkpoint_path, manifest["fingerprint"], run_dir_fd=run_dir_fd
    )
    validate_checkpoint_integrity(
        state=state,
        cases=cases,
        assets=assets,
        protocol=protocol,
        providers=providers,
        journal_records=journal.records(),
        judgment_prompt_fn=judgment_prompt_fn,
        normalize_winner_fn=normalize_winner_fn,
    )
    _generate_all(
        cases=cases,
        assets=assets,
        protocol=protocol,
        specs=specs,
        providers=providers,
        runner=runner,
        journal=journal,
        state=state,
        checkpoint_path=checkpoint_path,
        run_dir_fd=run_dir_fd,
        max_attempts=max_attempts,
        parallel=parallel,
    )
    _judge_all(
        cases=cases,
        protocol=protocol,
        specs=specs,
        providers=providers,
        runner=runner,
        journal=journal,
        state=state,
        checkpoint_path=checkpoint_path,
        run_dir_fd=run_dir_fd,
        judgment_prompt_fn=judgment_prompt_fn,
        parse_judgment_fn=parse_judgment_fn,
        normalize_winner_fn=normalize_winner_fn,
        max_attempts=max_attempts,
        parallel=parallel,
    )
    validate_checkpoint_integrity(
        state=state,
        cases=cases,
        assets=assets,
        protocol=protocol,
        providers=providers,
        journal_records=journal.records(),
        judgment_prompt_fn=judgment_prompt_fn,
        normalize_winner_fn=normalize_winner_fn,
    )
    result = _summarize(
        cases=cases,
        protocol=protocol,
        providers=providers,
        manifest=manifest,
        manifest_sha256=_sha256_secure_artifact(
            manifest_path, run_dir_fd=run_dir_fd
        ),
        journal=journal,
        state=state,
    )
    save_secure_json(
        run_dir.resolve() / "result.json", result, run_dir_fd=run_dir_fd
    )
    return result


def run_evaluation(*, run_dir: Path, **kwargs: Any) -> dict[str, Any]:
    with RunLock(run_dir) as lock:
        return _run_evaluation_unlocked(
            run_dir=run_dir, run_dir_fd=lock.dir_descriptor, **kwargs
        )


def _load_parity() -> Any:
    spec = importlib.util.spec_from_file_location("paired_dev_parity", PARITY_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import parity provider adapter")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_provider_bundle(
    config_path: Path,
    parity: Any,
    trusted_pins: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    reference, candidate, judges = parity.load_config(config_path)
    if len(judges) != 1:
        raise ValueError("paired dev protocol requires exactly one independent judge")
    unresolved_specs = {
        "reference": reference,
        "candidate": candidate,
        "judge": judges[0],
    }
    specs = {
        role: pin_provider_spec(spec, role, trusted_pins[role])
        for role, spec in unresolved_specs.items()
    }
    judge_entries = raw.get("judges")
    if not isinstance(judge_entries, list) or len(judge_entries) != 1:
        raise ValueError("provider config requires exactly one judge entry")
    entries = {
        "reference": raw.get("reference", {}),
        "candidate": raw.get("candidate", {}),
        "judge": judge_entries[0],
    }
    providers = {
        role: provider_audit_metadata(specs[role], entries[role]) for role in specs
    }
    return specs, providers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit-grade paired 2x2 evaluation over the fixed dedicated dev set",
        epilog=(
            "Executable paths and SHA-256 values are required reviewed run inputs. "
            "Scripts additionally require an absolute interpreter path and SHA-256. "
            "Ambient PATH is discarded, launcher bytes execute from sealed descriptors, "
            "and --dry-run reports the full launcher chain before provider calls."
        ),
    )
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    for role in ("reference", "candidate", "judge"):
        parser.add_argument(
            f"--{role}-executable",
            type=Path,
            required=True,
            help=f"absolute trusted {role} executable path (PATH is never searched)",
        )
        parser.add_argument(
            f"--{role}-executable-sha256",
            required=True,
            help=f"reviewed SHA-256 of the trusted {role} executable",
        )
        parser.add_argument(
            f"--{role}-interpreter",
            type=Path,
            help=f"absolute trusted interpreter path when {role} is a script",
        )
        parser.add_argument(
            f"--{role}-interpreter-sha256",
            help=f"reviewed SHA-256 of the trusted {role} interpreter",
        )
    parser.add_argument(
        "--mode",
        choices=("iterative_dev", "final_fresh_dev"),
        default="iterative_dev",
    )
    parser.add_argument("--run-id")
    parser.add_argument("--parallel", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.parallel < 1:
        raise ValueError("parallel must be positive")
    trusted_pins = validate_trusted_executable_pins(
        {
            role: {
                "path": getattr(args, f"{role}_executable"),
                "sha256": getattr(args, f"{role}_executable_sha256"),
                "interpreter_path": getattr(args, f"{role}_interpreter"),
                "interpreter_sha256": getattr(args, f"{role}_interpreter_sha256"),
            }
            for role in ("reference", "candidate", "judge")
        }
    )
    protocol = load_protocol()
    cases = load_dev_cases(DEV_CASES)
    parity = _load_parity()
    specs, providers = _load_provider_bundle(args.config, parity, trusted_pins)
    validate_provider_protocol(specs, providers, protocol)
    assets = {
        name: strip_frontmatter(path.read_text(encoding="utf-8"))
        if name == "persona"
        else path.read_text(encoding="utf-8").strip()
        for name, path in ASSET_PATHS.items()
    }
    fingerprint_inputs = build_fingerprint_inputs(
        protocol=protocol,
        protocol_path=PROTOCOL_PATH,
        cases=cases,
        cases_path=DEV_CASES,
        config_path=args.config,
        asset_paths=ASSET_PATHS,
        providers=providers,
        evaluator_path=MODULE_PATH,
        parity_path=PARITY_PATH,
        judge_prompt=parity.JUDGE_PROMPT,
    )
    fingerprint = canonical_sha256(fingerprint_inputs)
    if args.dry_run:
        print(
            json.dumps(
                {
                    "scope": "strict-dev-only",
                    "cases": len(cases),
                    "planned_generation_calls": 50,
                    "planned_judgments": 80,
                    "fingerprint": fingerprint,
                    "support_safety": {
                        "category": protocol["support_safety"]["category"],
                        "fields": protocol["support_safety"]["fields"],
                        "required_judgments": sum(
                            case["category"]
                            == protocol["support_safety"]["category"]
                            for case in cases
                        )
                        * len(protocol["arms"])
                        * len(protocol["orders"]),
                        "acceptance_gate": protocol["support_safety"]["acceptance_gate"],
                    },
                    "providers": {
                        role: {
                            "provider": metadata["provider"],
                            "requested_model": metadata["requested_model"],
                            "provider_spec_sha256": metadata["provider_spec_sha256"],
                            "trusted_executable": {
                                "path": metadata["trusted_executable_path"],
                                "sha256": metadata["executable_sha256"],
                            },
                            "launcher_chain": metadata["launcher_chain"],
                        }
                        for role, metadata in providers.items()
                    },
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 0
    result = run_evaluation(
        run_dir=args.run_dir,
        run_mode=args.mode,
        run_id=args.run_id or uuid.uuid4().hex,
        fingerprint_inputs=fingerprint_inputs,
        cases=cases,
        assets=assets,
        protocol=protocol,
        specs=specs,
        providers=providers,
        runner=secure_run_provider,
        judgment_prompt_fn=parity.judgment_prompt,
        parse_judgment_fn=lambda raw_output, category: parse_raw_judgment_then_normalize(
            raw_output, parity, protocol, category=category
        ),
        normalize_winner_fn=parity.normalized_winner,
        max_attempts=int(protocol["retry_policy"]["max_attempts_per_logical_call"]),
        parallel=args.parallel,
    )
    print(
        json.dumps(
            {
                "result": str((args.run_dir / "result.json").resolve()),
                "fingerprint": result["fingerprint"],
                "counts": result["counts"],
                "accepted": result["language_acceptance_observed"]["accepted"],
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
