"""Mechanical prompt-regression acceptance criterion."""

from __future__ import annotations

import pathlib
import subprocess

from rig_workbench.eval.affected import _surface
from rig_workbench.eval.cases import EvalCaseError
from rig_workbench.eval.gate import evaluate_gate


CRITERION = "prompt_regression_passed"


def _context(root: pathlib.Path, task: dict) -> tuple[pathlib.Path, str]:
    worktree = task.get("worktree_path")
    repo = pathlib.Path(worktree) if isinstance(worktree, str) and worktree else root
    base = task.get("base_commit")
    return repo, base if isinstance(base, str) and base else "HEAD"


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
    return any(_surface(path) is not None for path in paths)


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
        report, code = evaluate_gate(
            repo, base=base, head="working", evidence_dir=evidence_dir,
        )
        check["status"] = "passed" if code == 0 else "failed"
        check["detail"] = f"machine eval gate: {report['status']}"
    except EvalCaseError as exc:
        check["status"] = "failed"
        check["detail"] = f"machine eval gate infrastructure error: {exc}"
    return [f"  prompt-regression sensor: {check['status']}"]
