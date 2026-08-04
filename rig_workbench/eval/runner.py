"""Evaluation execution and canonical result persistence."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from typing import Any

from rig_workbench import __version__
from rig_workbench.bench_providers import build_bare_attempt

from .attestation import sign_result_attestation
from .cases import EvalCaseError, canonical_json, evaluation_spec_hash, validate_case
from .execution import execution_diff_sha256
from .safety import unsafe_text_reason

RESULT_SCHEMA_VERSION = 1
OUTPUT_CAP = 4096
COMMAND_ALLOWLIST = frozenset({"python", "python3", "node", "printf", "echo", "true", "false"})
JudgeAdapter = Callable[[dict, str, str], dict]


def _child_environment(**updates: str) -> dict[str, str]:
    """Return executor environment without evaluation attestation credentials."""
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith("RIG_EVAL_ATTESTATION_")
    }
    environment.update(updates)
    return environment


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _iso(now: dt.datetime | None) -> str:
    value = now or dt.datetime.now(dt.timezone.utc)
    if value.tzinfo is None:
        raise EvalCaseError("evaluation time must include a timezone")
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def _git_identity(
    repo: pathlib.Path, execution_base: str | None = None,
) -> tuple[str | None, str | None, str]:
    def git(*args: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", *args], cwd=repo, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=5, shell=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        value = (completed.stdout or "").strip()
        return value if completed.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", value) else None

    commit = git("rev-parse", "HEAD")
    if execution_base is not None:
        if (not isinstance(execution_base, str) or not execution_base
                or "\n" in execution_base or "\x00" in execution_base):
            raise EvalCaseError("execution base revision is invalid")
        base = git("rev-parse", "--verify", f"{execution_base}^{{commit}}")
        if base is None or commit is None:
            raise EvalCaseError("execution base revision cannot be resolved")
        try:
            ancestor = subprocess.run(
                ["git", "merge-base", "--is-ancestor", base, commit], cwd=repo,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=5, shell=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise EvalCaseError("execution base ancestry cannot be verified") from exc
        if ancestor.returncode != 0:
            raise EvalCaseError("execution base must be an ancestor of HEAD")
    else:
        base = git("rev-list", "--max-parents=0", "HEAD")
    return commit, base, "available" if commit and base else "unavailable"


def _safe_output(raw: str) -> dict:
    encoded = raw.encode("utf-8", errors="replace")
    digest = hashlib.sha256(encoded).hexdigest()
    truncated = len(encoded) > OUTPUT_CAP
    bounded = encoded[:OUTPUT_CAP].decode("utf-8", errors="replace")
    if unsafe_text_reason(raw):
        return {"text": "[REDACTED]", "sha256": digest, "truncated": truncated,
                "redacted": True}
    return {"text": bounded, "sha256": digest, "truncated": truncated, "redacted": False}


def _check(spec: str, output: str, returncode: int) -> dict:
    kind, separator, argument = spec.partition(":")
    status = "fail"
    detail = ""
    try:
        if kind == "contains" and separator:
            status = "pass" if argument in output else "fail"
        elif kind == "not_contains" and separator:
            status = "pass" if argument not in output else "fail"
        elif kind == "regex" and separator and len(argument) <= 500:
            status = "pass" if re.search(argument, output) else "fail"
        elif kind == "json" and not separator:
            json.loads(output)
            status = "pass"
        elif kind == "schema" and separator:
            value = json.loads(output)
            keys = [item for item in argument.split(",") if item]
            status = "pass" if isinstance(value, dict) and all(k in value for k in keys) else "fail"
        elif kind == "exit" and separator and argument.isdigit():
            status = "pass" if returncode == int(argument) else "fail"
        else:
            status = "unmeasured"
            detail = "unsupported deterministic check"
    except (json.JSONDecodeError, re.error, ValueError) as exc:
        status = "fail"
        detail = type(exc).__name__
    return {"spec": spec, "status": status, "detail": detail}


def _execute(
    *, provider: str, model: str, payload: str, phase: str, kind: str, index: int,
    repeat: int, repo: pathlib.Path, command: str | None, timeout_s: float,
) -> tuple[int, str, str, str | None]:
    if provider == "mock":
        failures = math.ceil(repeat * 2 / 3)
        failed = phase == "baseline" and kind == "target" and index <= failures
        return (1 if failed else 0), payload, "", None
    if provider == "command":
        if not command:
            raise EvalCaseError("command provider requires explicit --command")
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            raise EvalCaseError(f"invalid command argv: {exc}") from exc
        if not argv or pathlib.Path(argv[0]).name != argv[0] or argv[0] not in COMMAND_ALLOWLIST:
            raise EvalCaseError("command executable is not allowlisted")
    elif provider in {"claude", "codex"}:
        invocation = build_bare_attempt(provider, payload, repo, model)
        argv = list(invocation.argv)
        if not argv or shutil.which(argv[0]) is None:
            return 127, "", "provider executable unavailable", "unavailable"
    else:
        raise EvalCaseError(f"unsupported evaluation provider: {provider}")
    environment = _child_environment(
        RIG_EVAL_INPUT=payload, RIG_EVAL_PHASE=phase,
        RIG_EVAL_KIND=kind, RIG_EVAL_INDEX=str(index),
    )
    try:
        completed = subprocess.run(
            argv, cwd=repo, env=environment, input=payload, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout_s,
            shell=False,
        )
        infra = (
            "provider_error"
            if provider in {"claude", "codex"} and completed.returncode != 0 else None
        )
        return completed.returncode, completed.stdout or "", completed.stderr or "", infra
    except subprocess.TimeoutExpired as exc:
        stdout = (
            exc.stdout.decode("utf-8", "replace")
            if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        )
        return 124, stdout, "command timed out", "timeout"
    except (OSError, subprocess.SubprocessError) as exc:
        return 126, "", str(exc), "unavailable"


def _judge_prompt(case: dict, payload: str, output: str) -> str:
    return canonical_json({
        "instruction": (
            "Judge the output against every rubric criterion. Return only JSON with "
            "status=measured and criteria entries containing id, status pass|fail, and "
            "numeric score. Do not omit criteria."
        ),
        "rubric": case["semantic_rubric"],
        "input": payload,
        "output": output,
    }).rstrip("\n")


def _normalize_judge(value: Any, expected_ids: list[str]) -> dict:
    error = {"status": "error", "criteria": []}
    if not isinstance(value, dict) or set(value) != {"status", "criteria"}:
        return error
    criteria = value.get("criteria")
    if value.get("status") != "measured" or not isinstance(criteria, list):
        return error
    normalized: list[dict] = []
    for item in criteria:
        if (not isinstance(item, dict) or set(item) != {"id", "status", "score"}
                or not isinstance(item["id"], str)
                or item["status"] not in {"pass", "fail"}
                or isinstance(item["score"], bool)
                or not isinstance(item["score"], (int, float))
                or not math.isfinite(item["score"])):
            return error
        normalized.append({
            "id": item["id"], "status": item["status"],
            "score": float(item["score"]),
        })
    ids = [item["id"] for item in normalized]
    if ids != expected_ids or len(ids) != len(set(ids)):
        return error
    return {"status": "measured", "criteria": normalized}


def make_judge_adapter(
    *, provider: str, model: str, repo: pathlib.Path | str,
    command: str | None = None, timeout_s: float = 30,
) -> JudgeAdapter:
    """Build a bounded, shell-free semantic judge adapter."""
    if provider not in {"mock", "command", "claude", "codex"}:
        raise EvalCaseError("unsupported judge provider")
    if not isinstance(model, str) or not model or unsafe_text_reason(model):
        raise EvalCaseError("judge model identity is invalid")
    if timeout_s <= 0:
        raise EvalCaseError("judge timeout must be positive")
    root = pathlib.Path(repo).resolve()
    argv: list[str] | None = None
    if provider == "command":
        if not command:
            raise EvalCaseError("command judge requires explicit --judge-command")
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            raise EvalCaseError(f"invalid judge command argv: {exc}") from exc
        if (not argv or pathlib.Path(argv[0]).name != argv[0]
                or argv[0] not in COMMAND_ALLOWLIST):
            raise EvalCaseError("judge command executable is not allowlisted")

    def judge(case: dict, payload: str, output: str) -> dict:
        expected_ids = [item["id"] for item in case["semantic_rubric"]]
        if provider == "mock":
            return {"status": "measured", "criteria": [
                {"id": criterion_id, "status": "pass", "score": 1.0}
                for criterion_id in expected_ids
            ]}
        prompt = _judge_prompt(case, payload, output)
        selected = argv
        if provider in {"claude", "codex"}:
            invocation = build_bare_attempt(provider, prompt, root, model)
            selected = list(invocation.argv)
            if not selected or shutil.which(selected[0]) is None:
                return {"status": "error", "criteria": []}
        assert selected is not None
        environment = _child_environment(RIG_EVAL_JUDGE_INPUT=prompt)
        try:
            completed = subprocess.run(
                selected, cwd=root, env=environment, input=prompt,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout_s, shell=False,
            )
        except (OSError, subprocess.SubprocessError):
            return {"status": "error", "criteria": []}
        raw = completed.stdout or ""
        if (completed.returncode != 0 or len(raw.encode("utf-8")) > OUTPUT_CAP
                or unsafe_text_reason(raw)):
            return {"status": "error", "criteria": []}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"status": "error", "criteria": []}
        return _normalize_judge(parsed, expected_ids)

    judge.judge_provider = provider  # type: ignore[attr-defined]
    judge.judge_model = model  # type: ignore[attr-defined]
    judge.judge_executor_version = __version__  # type: ignore[attr-defined]
    return judge


def _sample(
    case: dict, *, provider: str, model: str, phase: str, kind: str, index: int,
    repeat: int, repo: pathlib.Path, command: str | None, timeout_s: float,
    judge_adapter: JudgeAdapter | None,
) -> dict:
    inputs = case["target_inputs"] if kind == "target" else case["clean_controls"]
    payload = canonical_json(inputs).rstrip("\n")
    started = time.monotonic()
    returncode, stdout, stderr, infra = _execute(
        provider=provider, model=model, payload=payload, phase=phase, kind=kind,
        index=index, repeat=repeat, repo=repo, command=command, timeout_s=timeout_s,
    )
    checks = [_check(spec, stdout, returncode) for spec in case["deterministic_checks"]]
    checks_pass = all(item["status"] == "pass" for item in checks)
    if case["semantic_rubric"] and judge_adapter is not None:
        judge = judge_adapter(case, payload, stdout)
    elif case["semantic_rubric"]:
        judge = {"status": "unmeasured", "criteria": []}
    else:
        judge = {"status": "not_required", "criteria": []}
    outcome = "pass" if returncode == 0 and checks_pass and infra is None else "fail"
    return {
        "index": index, "outcome": outcome, "returncode": returncode,
        "elapsed_s": round(time.monotonic() - started, 6), "infra_status": infra,
        "checks": checks, "judge": judge, "stdout": _safe_output(stdout),
        "stderr": _safe_output(stderr),
    }


def _atomic_write(path: pathlib.Path, value: dict) -> None:
    temporary: pathlib.Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".result.", suffix=".tmp", dir=path.parent)
        temporary = pathlib.Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(canonical_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            raise EvalCaseError(f"result already exists: {path}")
        os.replace(temporary, path)
        temporary = None
    except EvalCaseError:
        raise
    except OSError as exc:
        raise EvalCaseError(f"filesystem error writing evaluation result: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def run_case(
    case: dict, *, repo: pathlib.Path | str, provider: str, model: str,
    repeat: int, phase: str, command: str | None = None, timeout_s: float = 30,
    judge_adapter: JudgeAdapter | None = None, now: dt.datetime | None = None,
    execution_base: str | None = None,
    result_root: pathlib.Path | str | None = None,
) -> tuple[pathlib.Path, dict]:
    validate_case(case)
    if phase not in {"baseline", "current"}:
        raise EvalCaseError("phase must be baseline or current")
    if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 3:
        raise EvalCaseError("repeat must be at least 3")
    if repeat != case["repeat"]:
        raise EvalCaseError("repeat must match case repeat")
    if repeat > 100:
        raise EvalCaseError("repeat must not exceed 100")
    if provider not in {"mock", "claude", "codex", "command"}:
        raise EvalCaseError("unsupported evaluation provider")
    if not isinstance(model, str) or not model or unsafe_text_reason(model):
        raise EvalCaseError("model identity is invalid")
    if timeout_s <= 0:
        raise EvalCaseError("timeout must be positive")
    policy = case["provider_policy"]
    if policy["mode"] == "allowlist" and provider not in policy["allowed"]:
        raise EvalCaseError("provider violates case provider policy")
    if policy.get("models") and model not in policy["models"]:
        raise EvalCaseError("model violates case provider policy")
    if judge_adapter is not None:
        selected_judge_provider = str(getattr(judge_adapter, "judge_provider", "custom"))
        selected_judge_model = str(getattr(judge_adapter, "judge_model", "custom"))
        if (policy.get("judge_providers")
                and selected_judge_provider not in policy["judge_providers"]):
            raise EvalCaseError("judge provider violates case provider policy")
        if policy.get("judge_models") and selected_judge_model not in policy["judge_models"]:
            raise EvalCaseError("judge model violates case provider policy")
    try:
        root = pathlib.Path(repo).resolve()
    except OSError as exc:
        raise EvalCaseError(f"filesystem error resolving repository: {exc}") from exc
    started_wall = _iso(now)
    execution_commit, execution_base_commit, execution_status = (
        _git_identity(root) if execution_base is None
        else _git_identity(root, execution_base)
    )
    execution_diff = (
        execution_diff_sha256(root, base=execution_base_commit)
        if execution_status == "available" and execution_base_commit is not None
        else hashlib.sha256(b"rig-eval-execution-unavailable-v1").hexdigest()
    )
    started = time.monotonic()
    target = [_sample(case, provider=provider, model=model, phase=phase, kind="target",
                      index=index, repeat=repeat, repo=root, command=command,
                      timeout_s=timeout_s, judge_adapter=judge_adapter)
              for index in range(1, repeat + 1)]
    clean = [_sample(case, provider=provider, model=model, phase=phase, kind="clean",
                     index=index, repeat=repeat, repo=root, command=command,
                     timeout_s=timeout_s, judge_adapter=judge_adapter)
             for index in range(1, repeat + 1)]
    target_pass = sum(row["outcome"] == "pass" for row in target)
    clean_pass = sum(row["outcome"] == "pass" for row in clean)
    judge_status = (
        "not_required" if not case["semantic_rubric"] else
        ("measured" if all(row["judge"].get("status") == "measured"
                           for row in [*target, *clean]) else "unmeasured")
    )
    judge_provider = str(getattr(judge_adapter, "judge_provider", "custom" if judge_adapter else "none"))
    judge_model = str(getattr(judge_adapter, "judge_model", "custom" if judge_adapter else "none"))
    judge_executor_version = str(
        getattr(judge_adapter, "judge_executor_version", __version__)
    )
    result = {
        "eval_result_schema_version": RESULT_SCHEMA_VERSION,
        "case_id": case["id"], "case_hash": evaluation_spec_hash(case),
        "source_commit": case["provenance"]["source_commit"],
        "source_base_commit": case["provenance"]["source_commit"],
        "execution_commit": execution_commit,
        "execution_base_commit": execution_base_commit,
        "execution_status": execution_status,
        "execution_diff_sha256": execution_diff,
        "provider": provider, "model": model, "executor_version": __version__,
        "judge_provider": judge_provider, "judge_model": judge_model,
        "judge_executor_version": judge_executor_version,
        "phase": phase, "started_at": started_wall,
        "elapsed_s": round(time.monotonic() - started, 6), "repeat": repeat,
        "target": target, "clean": clean,
        "judge": {"required": bool(case["semantic_rubric"]), "status": judge_status},
        "summary": {
            "target_success_rate": target_pass / repeat,
            "target_failure_rate": 1 - target_pass / repeat,
            "clean_success_rate": clean_pass / repeat,
            "clean_false_positive_rate": 1 - clean_pass / repeat,
        },
    }
    result["result_sha256"] = _sha(result)
    result["attestation"] = sign_result_attestation(result)
    from .compare import validate_result
    validate_result(result, now=now)
    run_id = started_wall.replace("-", "").replace(":", "").replace("+", "p")
    results = pathlib.Path(result_root) if result_root is not None else (
        root / ".rig" / "evals" / "results"
    )
    destination = results / case["id"] / f"{run_id}-{phase}-{provider}.json"
    _atomic_write(destination, result)
    return destination, result
