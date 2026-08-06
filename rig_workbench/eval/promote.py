"""Atomic promotion of evidence-backed evaluation drafts."""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
import pathlib
import tempfile

from .cases import EvalCaseError, canonical_json, validate_case
from .compare import compare_results


def _load_draft(root: pathlib.Path, case_id: str) -> tuple[pathlib.Path, dict]:
    path = root / ".rig" / "evals" / "drafts" / case_id / "case.json"
    try:
        raw = path.read_text(encoding="utf-8")
        case = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvalCaseError(f"cannot read evaluation draft: {exc}") from exc
    validate_case(case)
    if case["status"] != "draft" or case["id"] != case_id:
        raise EvalCaseError("promotion source must be the matching draft")
    if raw != canonical_json(case):
        raise EvalCaseError("promotion draft is not canonical JSON")
    return path, case


def _judged(result: dict, expected_ids: set[str], *, require_pass: bool) -> bool:
    if result.get("judge", {}).get("status") != "measured":
        return False
    for sample in [*result.get("target", []), *result.get("clean", [])]:
        judge = sample.get("judge", {})
        criteria = judge.get("criteria", [])
        if judge.get("status") != "measured" or not criteria:
            return False
        ids = [item.get("id") for item in criteria]
        if len(ids) != len(set(ids)) or set(ids) != expected_ids:
            return False
        if require_pass and any(item.get("status") != "pass" for item in criteria):
            return False
    return True


def _atomic_create(path: pathlib.Path, value: dict) -> None:
    temporary: pathlib.Path | None = None
    created_parent = False
    try:
        if path.exists():
            raise EvalCaseError(f"promoted case already exists: {path}")
        if not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=False)
            created_parent = True
        descriptor, name = tempfile.mkstemp(prefix=".case.", suffix=".tmp", dir=path.parent)
        temporary = pathlib.Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(canonical_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            raise EvalCaseError(f"promoted case already exists: {path}")
        os.replace(temporary, path)
        temporary = None
    except EvalCaseError:
        raise
    except OSError as exc:
        raise EvalCaseError(f"filesystem error promoting evaluation case: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            exists = path.exists()
        except OSError:
            exists = False
        if created_parent and not exists:
            try:
                path.parent.rmdir()
            except OSError:
                pass


def promote_case(
    repo: pathlib.Path | str, case_id: str, baseline: dict, current: dict,
    *, now: dt.datetime | None = None,
) -> tuple[pathlib.Path, dict]:
    try:
        root = pathlib.Path(repo).resolve()
    except OSError as exc:
        raise EvalCaseError(f"filesystem error resolving repository: {exc}") from exc
    _draft_path, case = _load_draft(root, case_id)
    report = compare_results(baseline, current, case=case, now=now)
    if report["status"] != "pass":
        raise EvalCaseError("evaluation evidence does not satisfy red/green/clean gates")
    if case["semantic_rubric"]:
        expected_ids = {item["id"] for item in case["semantic_rubric"]}
        if (not _judged(baseline, expected_ids, require_pass=True)
                or not _judged(current, expected_ids, require_pass=True)):
            raise EvalCaseError(
                "semantic judge rubric criteria are unmeasured, mismatched, or failed"
            )
    promoted = copy.deepcopy(case)
    promoted["status"] = "approved"
    promoted["updated_at"] = (
        now or dt.datetime.now(dt.timezone.utc)
    ).astimezone(dt.timezone.utc).isoformat(timespec="seconds")
    validate_case(promoted)
    destination = root / "evals" / "cases" / case_id / "case.json"
    _atomic_create(destination, promoted)
    return destination, promoted
