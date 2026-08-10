"""Mechanical prompt-regression acceptance criterion."""

from __future__ import annotations

import pathlib
import subprocess

from rig_workbench.eval.affected import REGISTRY_REL, _surface
from rig_workbench.eval.cases import EvalCaseError
from rig_workbench.eval.gate import evaluate_gate

from .state import effective_base


CRITERION = "prompt_regression_passed"


def _judged(path: str) -> bool:
    """Whether the evaluation gate has anything to say about this path.

    A touched prompt surface is the obvious half. The other half is the coverage
    the ratchet protects: deleting a case touches no surface and neither does
    narrowing the registry, so a criterion keyed on surfaces alone never appeared
    for exactly the changes that are supposed to be fatal. CI does not have this
    hole — it runs the ratchet on every push — which is the same drift in a
    second place.
    """
    return (_surface(path) is not None
            or path.startswith("evals/cases/") or path == REGISTRY_REL)


def _context(root: pathlib.Path, task: dict) -> tuple[pathlib.Path, str]:
    worktree = task.get("worktree_path")
    repo = pathlib.Path(worktree) if isinstance(worktree, str) and worktree else root
    # Live merge base, not the registration-time record (#312): reading
    # `base_commit` directly widened the range the moment the branch was rebased,
    # so this criterion judged already-merged commits and demanded coverage for
    # prompt surfaces the task never touched. `effective_base` already absorbs
    # every unresolvable case by handing the recorded value back, so the "HEAD"
    # fallback below is reached exactly where it was before: no `base_commit`
    # recorded at all.
    base, _drift = effective_base(root, task)
    return repo, base or "HEAD"


def _has_prompt_diff(root: pathlib.Path, task: dict) -> bool:
    repo, base = _context(root, task)
    try:
        changed = subprocess.run(
            ["git", "diff", "--name-only", "--relative", base, "--"], cwd=repo,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, shell=False,
        )
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"], cwd=repo,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EvalCaseError("prompt diff cannot be computed") from exc
    if changed.returncode != 0 or untracked.returncode != 0:
        raise EvalCaseError("prompt diff cannot be computed")
    paths = changed.stdout.splitlines() + untracked.stdout.splitlines()
    return any(_judged(path) for path in paths)


def ensure_prompt_criterion(root: pathlib.Path, task: dict, acc: dict) -> bool:
    try:
        required = _has_prompt_diff(root, task)
    except EvalCaseError:
        required = True
    checks = acc.setdefault("checks", [])
    present = next((check for check in checks if check.get("name") == CRITERION), None)
    if required and present is None:
        checks.append({"name": CRITERION, "status": "pending", "detail": ""})
    elif not required and present is not None:
        checks.remove(present)
    return required


def apply_prompt_regression_sensor(root: pathlib.Path, task: dict, acc: dict) -> list[str]:
    if not ensure_prompt_criterion(root, task, acc):
        return []
    check = next(item for item in acc["checks"] if item["name"] == CRITERION)
    repo, base = _context(root, task)
    try:
        _has_prompt_diff(root, task)
    except EvalCaseError as exc:
        check["status"] = "failed"
        check["detail"] = f"machine eval gate infrastructure error: {exc}"
        return ["  prompt-regression sensor: failed"]
    evidence_dir = repo / ".rig" / "evals" / "results"
    try:
        # Same direction CI drives (`eval affected --ratchet`). Strict mode failed
        # every change that touches a prompt surface while `evals/cases/` is empty
        # — including the change that would add the first case — so this criterion
        # blocked locally on changes CI had already deliberately let through.
        # Debt is warning-grade rather than passed: the gate settles at
        # `passed_with_warnings`, which accept allows, and the missing coverage is
        # named rather than certified as checked.
        report, code = evaluate_gate(
            repo, base=base, head="working", evidence_dir=evidence_dir, ratchet=True,
        )
        if code != 0:
            check["status"] = "failed"
        elif report["status"] == "debt":
            check["status"] = "warning"
        else:
            check["status"] = "passed"
        detail = f"machine eval gate: {report['status']}"
        if check["status"] == "warning":
            debt = report.get("coverage_debt") or []
            detail += f" — no evaluation case yet for {', '.join(debt)}"
        check["detail"] = detail
    except EvalCaseError as exc:
        check["status"] = "failed"
        check["detail"] = f"machine eval gate infrastructure error: {exc}"
    return [f"  prompt-regression sensor: {check['status']}"]
