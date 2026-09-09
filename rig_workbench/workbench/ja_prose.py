"""workbench ja_prose: the Japanese-prose gate — two criteria that appear only when
the task's diff adds Japanese prose, one machine-owned and one owned by a reviewer.

    ja_lint_clean                the added lines of every changed .md/.txt that carries
                                 Japanese pass `rig-wb ja-lint` with zero errors
    ja_prose_ai_smell_reviewed   the `ai-smell-reviewer` persona has ruled on the task
                                 and did not REJECT

Both are diff-conditional the way `prompt_regression_passed` is: `ensure_ja_prose_criteria`
adds them when the diff touches Japanese prose and removes them when it no longer does,
so a task that only edits code never sees them. Both are diff-scoped the way
`no_secret_leak` is: pre-existing errors in a 1,000-line README are not this task's.

Why two criteria and not one. The lint criterion is decided by characters and
dictionaries (`policies/japanese-textlint-rules`) and a machine can own it outright:
errors fail the check, warnings leave it at `warning`, a clean diff passes it. The
AI-smell criterion is a judgement — the five-axis reading in `personas/ai-smell-reviewer`
— and rig's own measurement (docs/jp-naturalness-engineering.ja.md §6-3) found that
wiring a machine proxy for that judgement to a gate made findings drop while blind
human judgement got *worse*. So this sensor never reads `scripts/prose_rhythm.py`; it
reads `review.json` for the reviewer's recorded verdict and turns that, and only that,
into the check's status. A missing verdict stays `pending`, which is what keeps the
reviewer lane from being skipped: the gate does not pass until somebody has ruled.

Escape hatches follow `no_secret_leak`: an explicit `--set ja_lint_clean=passed` in the
same `gate` invocation is respected and recorded as `ja_lint_override`, and sticks.
"""

from __future__ import annotations

import pathlib

from rig_workbench import ja_textlint as jt

from .secrets import untracked_files, worktree_diff_text
from .state import effective_base, load_json, run_dir

LINT_CRITERION = "ja_lint_clean"
SMELL_CRITERION = "ja_prose_ai_smell_reviewed"
SMELL_PERSONA = "ai-smell-reviewer"
_LINT_PREFIX = "(ja-lint sensor)"
_SMELL_PREFIX = "(ja-prose reviewer sensor)"
_MAX_LISTED = 12


def _context(root: pathlib.Path, task: dict) -> tuple[pathlib.Path, str | None]:
    worktree = task.get("worktree_path")
    repo = pathlib.Path(worktree) if isinstance(worktree, str) and worktree else root
    base, _drift = effective_base(root, task)
    return repo, base


def changed_japanese_prose(repo: pathlib.Path, base: str) -> dict[str, set[int] | None]:
    """Changed .md/.txt files whose added lines carry Japanese, with those line numbers
    (None = an untracked file, every line counts). Uses the same cached diff the other
    sensors read (`shared_diff_cache`), so one gate evaluation shells out once."""
    added = jt.added_lines_from_diff(worktree_diff_text(repo, base))
    out: dict[str, set[int] | None] = {}
    for rel, nos in added.items():
        if not rel.lower().endswith(jt.TEXT_SUFFIXES):
            continue
        path = repo / rel
        if not path.is_file():
            continue
        try:
            lines = jt.read_source(str(path)).split("\n")
        except jt.Unchecked:
            continue
        if any(jt.RE_JA_CHAR.search(lines[n - 1]) for n in nos if n - 1 < len(lines)):
            out[rel] = nos
    for path, rel in untracked_files(repo):
        if not rel.lower().endswith(jt.TEXT_SUFFIXES):
            continue
        try:
            if jt.RE_JA_CHAR.search(jt.read_source(str(path))):
                out[rel] = None
        except jt.Unchecked:
            continue
    return out


def _touches_japanese_prose(root: pathlib.Path, task: dict) -> bool:
    repo, base = _context(root, task)
    if not base or not repo.is_dir():
        return False
    return bool(changed_japanese_prose(repo, base))


def ensure_ja_prose_criteria(root: pathlib.Path, task: dict, acc: dict) -> bool:
    """Add both criteria when the diff adds Japanese prose; remove them when it does not.
    Returns whether they are required."""
    required = _touches_japanese_prose(root, task)
    checks = acc.setdefault("checks", [])
    for name in (LINT_CRITERION, SMELL_CRITERION):
        present = next((c for c in checks if c.get("name") == name), None)
        if required and present is None:
            checks.append({"name": name, "status": "pending", "detail": ""})
        elif not required and present is not None:
            checks.remove(present)
    return required


def _settings_for(repo: pathlib.Path) -> jt.Settings:
    """The project's `.claude/ja-textlint.json` when it has one, else the defaults.
    A broken declaration is `unchecked` upstream; here it surfaces as the exception."""
    config = repo / jt.DEFAULT_CONFIG
    data = None
    if config.is_file():
        data, _ = jt.load_config(str(config), True)
    return jt.Settings(data)


def apply_ja_lint_sensor(root: pathlib.Path, run_d: pathlib.Path, task: dict, acc: dict,
                         explicit_set: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """Machine-own `ja_lint_clean` on the added lines of the changed Japanese prose."""
    check = next((c for c in acc.get("checks", []) if c["name"] == LINT_CRITERION), None)
    if check is None:
        return []
    repo, base = _context(root, task)
    if not base or not repo.is_dir():
        return []
    try:
        settings = _settings_for(repo)
        targets = changed_japanese_prose(repo, base)
        findings: list[dict] = []
        for rel in sorted(targets):
            nos = targets[rel]
            source = jt.read_source(str(repo / rel))
            for f in jt.lint_text(source, settings, rel):
                if nos is None or f["line"] in nos:
                    findings.append(f)
    except jt.Unchecked as exc:
        check["status"] = "failed"
        check["detail"] = f"{_LINT_PREFIX} unchecked: {exc}"
        return [f"{_LINT_PREFIX} unchecked → {LINT_CRITERION} failed: {exc}"]

    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]
    listed = [f"{f['file']}:{f['line']}:{f['column']} [{f['rule']}] {f['message']}"
              for f in errors[:_MAX_LISTED]]
    check["ja_lint_findings"] = {"errors": len(errors), "warnings": len(warnings),
                                 "files": sorted(targets), "listed": listed}
    notes: list[str] = []
    if errors:
        if LINT_CRITERION in explicit_set and check["status"] == "passed":
            check["ja_lint_override"] = True
            check["detail"] = (f"{_LINT_PREFIX} {len(errors)} error(s) manually overridden "
                               "after review (ja_lint_override)")
            notes.append(f"{_LINT_PREFIX} {len(errors)} error(s) still in the diff, but "
                         f"{LINT_CRITERION} was explicitly set to passed — override recorded:")
        elif check.get("ja_lint_override") and check["status"] == "passed":
            notes.append(f"{_LINT_PREFIX} {len(errors)} error(s) in the diff — manual override "
                         "previously recorded, keeping passed:")
        else:
            check["status"] = "failed"
            check["detail"] = (f"{_LINT_PREFIX} {len(errors)} error(s) on added Japanese lines "
                               f"({len(warnings)} warning(s)) — fix them (`rig-wb ja-lint --fix` for the "
                               f"mechanical ones), or after review override with --set {LINT_CRITERION}=passed")
            notes.append(f"{_LINT_PREFIX} {len(errors)} error(s) on added Japanese lines → "
                         f"{LINT_CRITERION} failed:")
        notes.extend(f"  {ln}" for ln in listed)
        if len(errors) > _MAX_LISTED:
            notes.append(f"  … and {len(errors) - _MAX_LISTED} more")
        return notes
    check.pop("ja_lint_override", None)
    if warnings:
        check["status"] = "warning"
        check["detail"] = (f"{_LINT_PREFIX} 0 errors, {len(warnings)} warning(s) on added Japanese "
                           f"lines across {len(targets)} file(s) — read them; warnings never block")
    else:
        check["status"] = "passed"
        check["detail"] = (f"{_LINT_PREFIX} 0 errors, 0 warnings on added Japanese lines across "
                           f"{len(targets)} file(s)")
    return [f"{_LINT_PREFIX} {check['status']}: {check['detail'][len(_LINT_PREFIX) + 1:]}"]


def apply_ja_smell_sensor(root: pathlib.Path, run_d: pathlib.Path, task: dict, acc: dict,
                          explicit_set: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """Turn the recorded `ai-smell-reviewer` verdict into `ja_prose_ai_smell_reviewed`."""
    check = next((c for c in acc.get("checks", []) if c["name"] == SMELL_CRITERION), None)
    if check is None:
        return []
    if SMELL_CRITERION in explicit_set and check["status"] == "passed":
        check["ja_smell_override"] = True
        check["detail"] = f"{_SMELL_PREFIX} manually set to passed without a recorded verdict (ja_smell_override)"
        return [f"{_SMELL_PREFIX} {SMELL_CRITERION} explicitly set to passed — override recorded"]
    review = load_json(run_dir(root, task["task_id"]) / "review.json", {"verdicts": []})
    verdict = next((v for v in review.get("verdicts", []) if v.get("persona") == SMELL_PERSONA), None)
    if verdict is None:
        if check.get("ja_smell_override") and check["status"] == "passed":
            return [f"{_SMELL_PREFIX} no verdict recorded — manual override previously recorded, keeping passed"]
        check["status"] = "pending"
        check["detail"] = (f"{_SMELL_PREFIX} the diff adds Japanese prose but `{SMELL_PERSONA}` has not "
                           f"ruled — add the lane to the review fan-out and record it with "
                           f"`rig-wb wb review <task_id> --set {SMELL_PERSONA}=<APPROVE|REJECT|APPROVE_WITH_CONDITIONS>`")
        return [f"{_SMELL_PREFIX} pending: no `{SMELL_PERSONA}` verdict recorded for this task"]
    check.pop("ja_smell_override", None)
    label = verdict.get("verdict")
    if label == "REJECT":
        check["status"] = "failed"
        check["detail"] = f"{_SMELL_PREFIX} `{SMELL_PERSONA}` rejected the prose ({verdict.get('recorded_at', '')})"
    elif label == "APPROVE_WITH_CONDITIONS":
        check["status"] = "warning"
        check["detail"] = f"{_SMELL_PREFIX} `{SMELL_PERSONA}` approved with conditions — see reviews/{SMELL_PERSONA}.md"
    else:
        check["status"] = "passed"
        check["detail"] = f"{_SMELL_PREFIX} `{SMELL_PERSONA}` approved ({verdict.get('recorded_at', '')})"
    return [f"{_SMELL_PREFIX} {check['status']}: {SMELL_PERSONA}={label}"]


def cmd_scan_ja_prose(args) -> None:
    """`rig-wb wb scan-ja-prose <task_id>` — the same diff-scoped lint the gate runs, printed."""
    from .state import die, load_task, repo_root, resolve_task_id

    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    _d, task = load_task(root, task_id)
    repo, base = _context(root, task)
    if not base:
        die(f"{task_id}: no base commit recorded; nothing to scope the scan to")
    settings = _settings_for(repo)
    targets = changed_japanese_prose(repo, base)
    if not targets:
        print(f"{task_id}: the diff adds no Japanese prose — {LINT_CRITERION} / {SMELL_CRITERION} do not apply")
        return
    errors = warnings = 0
    for rel in sorted(targets):
        nos = targets[rel]
        for f in jt.lint_text(jt.read_source(str(repo / rel)), settings, rel):
            if nos is not None and f["line"] not in nos:
                continue
            errors += f["severity"] == "error"
            warnings += f["severity"] == "warning"
            print(f"{f['file']}:{f['line']}:{f['column']}: {f['severity']} [{f['rule']}] {f['message']}")
    print(f"{task_id}: {errors} error(s) / {warnings} warning(s) on added Japanese lines in "
          f"{len(targets)} file(s)")
    if errors:
        raise SystemExit(1)
