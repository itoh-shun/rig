"""Atomic trusted execution of all approved cases affected by a diff."""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tempfile

from .affected import analyze_affected
from .cases import EvalCaseError
from .gate import evaluate_gate
from .runner import make_judge_adapter, run_case

# Committed, because CI no longer measures anything: it verifies what a maintainer
# measured. `evals/` is where the cases and the surface registry already live and
# is not a prompt-surface root, so evidence landing here adds nothing to the gate's
# own field of view. One file per case, overwritten: `_evidence` collects every
# `*.json` under the tree whose `case_id` matches, and a second `current` result
# for the same case is `current_evidence_count`, so an accumulating layout breaks
# the gate the first time a case is measured twice.
EVIDENCE_REL = "evals/evidence"


def _rev_parse(root: pathlib.Path, revision: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"], cwd=root,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EvalCaseError("cannot resolve affected-run revision") from exc
    value = completed.stdout.strip()
    if completed.returncode != 0 or len(value) != 40:
        raise EvalCaseError("cannot resolve affected-run revision")
    return value


def _dirty_paths(root: pathlib.Path) -> list[str]:
    """Working-tree entries that would make the measurement unreproducible.

    The signed diff is taken tree-to-tree at the resolved head, so anything
    uncommitted is measured but not described — the gate would later recompute a
    different hash and report a mismatch nobody can explain. Evidence from an
    earlier run is exempt: it is the output of this command, not an input to it.
    """
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain", "-z"], cwd=root, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=15, shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EvalCaseError("cannot read working tree status") from exc
    if completed.returncode != 0:
        raise EvalCaseError("cannot read working tree status")
    entries = [entry for entry in completed.stdout.split("\0") if entry]
    dirty: list[str] = []
    index = 0
    while index < len(entries):
        status, path = entries[index][:2], entries[index][3:]
        # A rename or copy spends a second NUL-separated field on its origin path;
        # skipping it keeps the origin from being read as a status line of its own.
        index += 2 if status[0] in {"R", "C"} else 1
        if path and path != EVIDENCE_REL and not path.startswith(EVIDENCE_REL + "/"):
            dirty.append(path)
    return sorted(dirty)


def run_affected(
    repo: pathlib.Path | str, *, base: str, head: str, provider: str, model: str,
    judge_provider: str, judge_model: str, provider_command: str | None = None,
    judge_command: str | None = None, timeout_s: float = 30,
) -> tuple[dict, int, pathlib.Path | None]:
    if provider == "mock" or judge_provider == "mock":
        raise EvalCaseError("affected-run forbids mock provider and mock judge")
    root = pathlib.Path(repo).resolve()
    affected = analyze_affected(root, base=base, head=head, require_cases=True)
    if affected["status"] == "noop":
        report, code = evaluate_gate(root, base=base, head=head, evidence_dir=root / ".rig" / "none")
        return report, code, None
    if affected["status"] == "uncovered":
        return ({"eval_gate_schema_version": 1, "status": "failed", "base": base,
                 "head": head, "resolved_head": affected["resolved_head"],
                 "cases": affected["affected_cases"],
                 "failures": [f"uncovered:{item}" for item in affected["uncovered"]]}, 1, None)
    cases: dict[str, dict] = {}
    for case_id in affected["affected_cases"]:
        path = root / "evals" / "cases" / case_id / "case.json"
        try:
            cases[case_id] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise EvalCaseError("affected-run cannot read approved case") from exc
    # The provider only ever sees the checked-out tree, so evidence describing a
    # different head would claim a tree nobody measured — and, since the gate now
    # recomputes the diff at the commit the evidence names, that claim would verify.
    checked_out = _rev_parse(root, "HEAD")
    if affected["resolved_head"] != checked_out:
        raise EvalCaseError(
            f"affected-run measures the checked-out tree; --head resolves to "
            f"{affected['resolved_head'][:12]} but HEAD is {checked_out[:12]}"
        )
    dirty = _dirty_paths(root)
    if dirty:
        raise EvalCaseError(
            "affected-run requires a clean working tree; uncommitted: "
            + ", ".join(dirty[:5]) + (" …" if len(dirty) > 5 else "")
        )
    # Staged under `.rig/` rather than in the destination: `execution_diff_sha256`
    # skips that prefix when it frames untracked input, so a multi-case run cannot
    # hash the evidence of its own earlier case into the identity of its later one.
    staging_parent = root / ".rig" / "evals" / "results"
    staging_parent.mkdir(parents=True, exist_ok=True)
    destination = root / EVIDENCE_REL
    staging = pathlib.Path(tempfile.mkdtemp(prefix=".affected-run.", dir=staging_parent))
    try:
        judge = make_judge_adapter(
            provider=judge_provider, model=judge_model, repo=root,
            command=judge_command, timeout_s=timeout_s,
        )
        produced: dict[str, pathlib.Path] = {}
        for case_id in sorted(cases):
            path, result = run_case(
                cases[case_id], repo=root, provider=provider, model=model,
                repeat=cases[case_id]["repeat"], phase="current",
                command=provider_command, timeout_s=timeout_s, judge_adapter=judge,
                execution_base=base, execution_head=affected["resolved_head"],
                result_root=staging,
            )
            produced[case_id] = path
            if any(row["infra_status"] is not None
                   for row in [*result["target"], *result["clean"]]):
                return ({"eval_gate_schema_version": 1, "status": "infra_error",
                         "base": base, "head": head,
                         "resolved_head": affected["resolved_head"], "cases": sorted(cases),
                         "failures": [f"provider_unavailable:{case_id}"]}, 2, None)
            if any(row["judge"]["status"] == "error"
                   for row in [*result["target"], *result["clean"]]):
                return ({"eval_gate_schema_version": 1, "status": "infra_error",
                         "base": base, "head": head,
                         "resolved_head": affected["resolved_head"], "cases": sorted(cases),
                         "failures": [f"judge_unavailable:{case_id}"]}, 2, None)
            if any(row["outcome"] != "pass"
                   for row in [*result["target"], *result["clean"]]):
                return ({"eval_gate_schema_version": 1, "status": "failed",
                         "base": base, "head": head,
                         "resolved_head": affected["resolved_head"], "cases": sorted(cases),
                         "failures": [f"quality_not_green:{case_id}"]}, 1, None)
        report, code = evaluate_gate(
            root, base=base, head=head, evidence_dir=staging, provider=provider,
            model=model, judge_provider=judge_provider, judge_model=judge_model,
        )
        if code != 0:
            return report, code, None
        for case_id, path in produced.items():
            final = destination / case_id / "current.json"
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, final)
        return report, 0, destination
    finally:
        if staging != pathlib.Path() and staging.exists():
            shutil.rmtree(staging)
