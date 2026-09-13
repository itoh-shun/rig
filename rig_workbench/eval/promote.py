"""Atomic promotion of evidence-backed evaluation drafts."""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
import pathlib
import tempfile
from typing import Protocol, runtime_checkable

from ..ports import Clock, Env
from ..ports.local import OS_ENV, SYSTEM_CLOCK
from .cases import EvalCaseError, canonical_json, validate_case
from .compare import compare_results
from .pack_layout import PACK_CASE_DIR


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


@runtime_checkable
class PackCaseDir(Protocol):
    """Where a pack keeps its evaluation cases — stated here, implemented elsewhere.

    `--into PACK` writes an approved case into a pack, and the directory a pack keeps
    cases in is the packs pillar's declaration, not this one's. Importing it here is the
    thing the layering rule forbids (`tests/test_layering_contract.py`) — and it was a
    function-local import, which hides the dependency rather than removing it. So the
    dependency is inverted: this protocol says what promotion needs, `eval/pack_layout.py`
    satisfies it, and the shell hands it in.
    """

    def __call__(self, into: pathlib.Path | str) -> pathlib.Path:
        """The directory `into`'s evaluation cases live in.

        Raises `EvalCaseError` when `into` is not a pack: a mistyped path would otherwise
        put an approved case where nothing reads it.
        """
        ...


def promote_case(
    repo: pathlib.Path | str, case_id: str, baseline: dict, current: dict,
    *, now: dt.datetime | None = None, into: pathlib.Path | str | None = None,
    clock: Clock = SYSTEM_CLOCK, env: Env = OS_ENV,
    pack_case_dir: PackCaseDir = PACK_CASE_DIR,
) -> tuple[pathlib.Path, dict]:
    """Promote a draft to an approved case, in this repository or into a pack.

    `into` moves the destination and nothing else. The draft still comes from the repository's
    own `.rig/evals/drafts/`, which is where it belongs and where it is nobody's undeclared
    file — a pack may hold nothing it has not declared, so a draft staged inside one is
    refused by `pack validate` and by `pack sync` alike. Both refusals are correct, and
    together they meant no prompt-bearing pack could be authored from scratch: the evidence a
    pack requires had nowhere to be produced.

    The safety property here is `compare_results` refusing evidence that does not pass, and the
    rubric check below refusing a judgement that was never measured. The comparison report is
    also the single source of truth for whether baseline rubric failures are gated; promotion
    must not independently reinterpret prompt bindings. None of those rules depends on which
    directory the result is written to. The pack owner then runs `pack sync` to declare the new
    case, which is the flow that already exists.
    """
    try:
        root = pathlib.Path(repo).resolve()
    except OSError as exc:
        raise EvalCaseError(f"filesystem error resolving repository: {exc}") from exc
    _draft_path, case = _load_draft(root, case_id)
    report = compare_results(baseline, current, case=case, now=now, clock=clock, env=env)
    if report["status"] != "pass":
        raise EvalCaseError("evaluation evidence does not satisfy red/green/clean gates")
    if case["semantic_rubric"]:
        expected_ids = {item["id"] for item in case["semantic_rubric"]}
        if (not _judged(
                    baseline, expected_ids,
                    require_pass=report["baseline_rubric_pass_required"],
                )
                or not _judged(current, expected_ids, require_pass=True)):
            raise EvalCaseError(
                "semantic judge rubric criteria are unmeasured, mismatched, or failed"
            )
    promoted = copy.deepcopy(case)
    promoted["status"] = "approved"
    # UTC, like every other stamp a case or a result carries; `Clock.now()` reads the
    # moment through the local offset and `astimezone` renders it, so the day a draft is
    # promoted and the day its evidence was measured are on one timeline.
    promoted["updated_at"] = (
        now or clock.now()
    ).astimezone(dt.timezone.utc).isoformat(timespec="seconds")
    validate_case(promoted)
    case_dir = pack_case_dir(into) if into is not None else root / "evals" / "cases"
    destination = case_dir / case_id / "case.json"
    _atomic_create(destination, promoted)
    return destination, promoted
