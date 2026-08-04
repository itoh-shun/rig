"""Atomic trusted execution of all approved cases affected by a diff."""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import tempfile

from .affected import analyze_affected
from .cases import EvalCaseError
from .gate import evaluate_gate
from .runner import make_judge_adapter, run_case


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
    staging_parent = root / ".rig" / "evals" / "results"
    staging_parent.mkdir(parents=True, exist_ok=True)
    destination = staging_parent / f"affected-{affected['resolved_head']}"
    if destination.exists():
        raise EvalCaseError(
            f"affected-run results already exist for commit {affected['resolved_head']}"
        )
    staging = pathlib.Path(tempfile.mkdtemp(prefix=".affected-run.", dir=staging_parent))
    try:
        judge = make_judge_adapter(
            provider=judge_provider, model=judge_model, repo=root,
            command=judge_command, timeout_s=timeout_s,
        )
        for case_id in sorted(cases):
            _path, result = run_case(
                cases[case_id], repo=root, provider=provider, model=model,
                repeat=cases[case_id]["repeat"], phase="current",
                command=provider_command, timeout_s=timeout_s, judge_adapter=judge,
                execution_base=base, result_root=staging,
            )
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
        os.replace(staging, destination)
        staging = pathlib.Path()
        return report, 0, destination
    finally:
        if staging != pathlib.Path() and staging.exists():
            shutil.rmtree(staging)
