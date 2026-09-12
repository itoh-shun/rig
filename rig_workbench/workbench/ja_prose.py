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

Both criteria follow `no_secret_leak` in that the sensor's answer is the verdict: these
two are written on every evaluation, over whatever a `--set` put there. They are not in
`lifecycle.REFUSABLE_CRITERIA`, so a contradicting `--set` is overwritten rather than
refused — and `record` / `record_missing_verdict` are where the one thing a declaration
may still do (be stricter than the machine) is kept.
"""

from __future__ import annotations

import pathlib

from rig_workbench import ja_textlint as jt

from .secrets import untracked_files, worktree_diff_text
from .state import effective_base, load_json, record_sensor_status, run_dir

LINT_CRITERION = "ja_lint_clean"
SMELL_CRITERION = "ja_prose_ai_smell_reviewed"
SMELL_PERSONA = "ai-smell-reviewer"
_LINT_PREFIX = "(ja-lint sensor)"
_SMELL_PREFIX = "(ja-prose reviewer sensor)"
#: config.WRITER_OPERATOR's counterpart: these sensors as the writer of a status.
_LINT_WRITER = "ja-lint-sensor"
_SMELL_WRITER = "ja-smell-sensor"
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


#: How much a status blocks acceptance (`state.gate_status`'s priority, as a number).
#: `pending` is deliberately absent: it means nothing has been recorded, not a lenient
#: judgement, so a sensor writes over it unconditionally.
_BLOCKS = {"skipped": 0, "passed": 0, "warning": 1, "failed": 2}


def ours(check: dict, writer: str, prefix: str) -> bool:
    """Did `writer` write this check's current status? Read from `check["by"]`, and only
    for a record written before that field existed from the detail text (see `record`)."""
    by = check.get("by")
    return by == writer if by else str(check.get("detail") or "").startswith(prefix)


def record(check: dict, status: str, detail: str, writer: str, prefix: str) -> bool:
    """Write this sensor's verdict onto `check`, unless a stricter status somebody else
    wrote is already there. Returns whether it was written.

    These two sensors differ from the diff-scoped ones (secrets, tamper, injection,
    destructive, anchors) in that they write on every evaluation rather than only when
    they find something — so "leave a clean criterion alone", which is how those four
    let `--set no_secret_leak=failed` stand, has no equivalent here and had to be said
    out loud. Without it `--set ja_lint_clean=failed:操作者判断` on a clean Japanese diff
    was silently downgraded to `passed`, which is the opposite of this module's job.

    A status this sensor itself wrote is always replaced — that is how a stale `failed`
    clears once the prose is fixed, the same "un-flag only what WE flagged" rule the
    diff-scoped sensors use. Ownership is read from `check["by"]`
    (config.WRITER_OPERATOR / this module's WRITER constants) and never inferred from
    the detail text: `gate --set ja_lint_clean=failed` with no `:detail` leaves the
    previous detail alone, so this sensor's own prefix sits under a status the operator
    has just written, and a prefix test calls that status mine and loosens it back. That
    is not a corner: after the first `gate` run the detail is always this sensor's.
    `prefix` is the fallback for a check written before `by` existed, where the text is
    the only evidence there is — a legacy record heals as soon as anything writes it.
    """
    current = check.get("status")
    if current != "pending" and not ours(check, writer, prefix) \
            and _BLOCKS.get(current, 0) > _BLOCKS.get(status, 0):
        return False
    record_sensor_status(check, status, detail, writer)
    return True


def record_missing_verdict(check: dict, detail: str, writer: str, prefix: str) -> bool:
    """Write `pending` because the reviewer has not ruled. Returns whether it was written.

    Not `record`, because `pending` is not a lenient judgement to be weighed against what
    is already there: it is this sensor's measurement that nothing has been recorded, and
    `ja_prose_ai_smell_reviewed` exists to keep that lane from being skipped by silence.
    Through `record` a hand-set `warning:未確認` — the exact wording `cmd_gate` suggests
    for a criterion the operator cannot judge — outranked `pending` and stood in for the
    reviewer: the gate reached `passed_with_warnings` and `accept` let it through, with no
    `--force` and no audit line, on a task where nobody had read the prose.

    A hand-written `failed` is the one status that survives, for the reason it survives
    everywhere else here: it is stricter than `pending`, and this rule is against a
    missing verdict being talked *down*, never against an operator being harder on the
    prose than the machine can be.
    """
    if check.get("status") == "failed" and not ours(check, writer, prefix):
        return False
    record_sensor_status(check, "pending", detail, writer)
    return True


def apply_ja_lint_sensor(root: pathlib.Path, run_d: pathlib.Path, task: dict,
                         acc: dict) -> list[str]:
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
        unclosed: list[dict] = []
        for rel in sorted(targets):
            nos = targets[rel]
            source = jt.read_source(str(repo / rel))
            for f in jt.lint_text(source, settings, rel, unclosed_out=unclosed):
                if nos is None or f["line"] in nos:
                    findings.append(f)
    except jt.Unchecked as exc:
        record(check, "failed", f"{_LINT_PREFIX} unchecked: {exc}", _LINT_WRITER, _LINT_PREFIX)
        return [f"{_LINT_PREFIX} unchecked → {LINT_CRITERION} failed: {exc}"]

    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]
    listed = [f"{f['file']}:{f['line']}:{f['column']} [{f['rule']}] {f['message']}"
              for f in errors[:_MAX_LISTED]]
    unclosed_at = [f"{u['file']}:{u['line']}" for u in unclosed]
    check["ja_lint_findings"] = {"errors": len(errors), "warnings": len(warnings),
                                 "files": sorted(targets), "listed": listed,
                                 "unclosed_disable": unclosed_at}
    notes: list[str] = []
    # An unclosed `disable` is checked before the findings, because while one is live the
    # finding list is not an answer: everything below the marker was suppressed, and this
    # criterion exists to catch exactly that silence. It fails whatever the count says.
    if unclosed_at:
        where = ", ".join(unclosed_at[:_MAX_LISTED])
        record(check, "failed",
               f"{_LINT_PREFIX} unclosed <!-- textlint-disable --> at {where} — it suppresses "
               f"every finding from that line to the end of the file, so this criterion cannot "
               f"answer for the lines below it. Close it with <!-- textlint-enable --> or use "
               f"<!-- textlint-disable-line -->; `accept --force` records the bypass",
               _LINT_WRITER, _LINT_PREFIX)
        notes.append(f"{_LINT_PREFIX} unclosed <!-- textlint-disable --> → "
                     f"{LINT_CRITERION} failed:")
        notes.extend(f"  {at}: suppresses to end of file" for at in unclosed_at[:_MAX_LISTED])
        if len(unclosed_at) > _MAX_LISTED:
            notes.append(f"  … and {len(unclosed_at) - _MAX_LISTED} more")
        return notes
    if errors:
        record(check, "failed",
               f"{_LINT_PREFIX} {len(errors)} error(s) on added Japanese lines "
               f"({len(warnings)} warning(s)) — fix them (`rig-wb ja-lint --fix` "
               f"for the mechanical ones); a reviewed finding is carried by "
               f"`accept --force`, which records the bypass", _LINT_WRITER, _LINT_PREFIX)
        notes.append(f"{_LINT_PREFIX} {len(errors)} error(s) on added Japanese lines → "
                     f"{LINT_CRITERION} failed:")
        notes.extend(f"  {ln}" for ln in listed)
        if len(errors) > _MAX_LISTED:
            notes.append(f"  … and {len(errors) - _MAX_LISTED} more")
        return notes
    if warnings:
        written = record(check, "warning",
                         f"{_LINT_PREFIX} 0 errors, {len(warnings)} warning(s) on added Japanese "
                         f"lines across {len(targets)} file(s) — read them; warnings never block",
                         _LINT_WRITER, _LINT_PREFIX)
    else:
        written = record(check, "passed",
                         f"{_LINT_PREFIX} 0 errors, 0 warnings on added Japanese lines across "
                         f"{len(targets)} file(s)", _LINT_WRITER, _LINT_PREFIX)
    if not written:
        return [f"{_LINT_PREFIX} 0 errors, {len(warnings)} warning(s) on added Japanese lines, "
                f"but {LINT_CRITERION} already carries a stricter recorded judgement "
                f"({check['status']}) — leaving it"]
    return [f"{_LINT_PREFIX} {check['status']}: {check['detail'][len(_LINT_PREFIX) + 1:]}"]


def apply_ja_smell_sensor(root: pathlib.Path, run_d: pathlib.Path, task: dict,
                          acc: dict) -> list[str]:
    """Turn the recorded `ai-smell-reviewer` verdict into `ja_prose_ai_smell_reviewed`.

    The recorded verdict is the verdict: without one the criterion goes back to `pending`
    rather than letting a `--set` stand in for the reviewer who has not ruled. A stricter
    hand-written status survives it — see `record`.
    """
    check = next((c for c in acc.get("checks", []) if c["name"] == SMELL_CRITERION), None)
    if check is None:
        return []
    review = load_json(run_dir(root, task["task_id"]) / "review.json", {"verdicts": []})
    verdict = next((v for v in review.get("verdicts", []) if v.get("persona") == SMELL_PERSONA), None)
    if verdict is None:
        written = record_missing_verdict(
            check,
            f"{_SMELL_PREFIX} the diff adds Japanese prose but `{SMELL_PERSONA}` has not "
            f"ruled — add the lane to the review fan-out and record it with "
            f"`rig-wb wb review <task_id> --set {SMELL_PERSONA}="
            f"<APPROVE|REJECT|APPROVE_WITH_CONDITIONS>`", _SMELL_WRITER, _SMELL_PREFIX)
        if not written:
            return [f"{_SMELL_PREFIX} no `{SMELL_PERSONA}` verdict recorded, and "
                    f"{SMELL_CRITERION} already carries a stricter recorded judgement "
                    f"({check['status']}) — leaving it"]
        return [f"{_SMELL_PREFIX} pending: no `{SMELL_PERSONA}` verdict recorded for this task"]
    label = verdict.get("verdict")
    if label == "REJECT":
        record(check, "failed", f"{_SMELL_PREFIX} `{SMELL_PERSONA}` rejected the prose "
               f"({verdict.get('recorded_at', '')})", _SMELL_WRITER, _SMELL_PREFIX)
    elif label == "APPROVE_WITH_CONDITIONS":
        record(check, "warning", f"{_SMELL_PREFIX} `{SMELL_PERSONA}` approved with conditions — "
               f"see reviews/{SMELL_PERSONA}.md", _SMELL_WRITER, _SMELL_PREFIX)
    elif label == "APPROVE":
        record(check, "passed", f"{_SMELL_PREFIX} `{SMELL_PERSONA}` approved "
               f"({verdict.get('recorded_at', '')})", _SMELL_WRITER, _SMELL_PREFIX)
    else:
        # Anything else is a verdict this sensor cannot read, and reading an unknown
        # word as approval is the one way for it to be wrong that matters: review.json
        # is an editable file, and `else: passed` turned any typo — or any string a
        # future verdict vocabulary adds — into a criterion that passes. Unreadable is
        # `pending`, the same answer as no verdict at all, with the word named.
        record_missing_verdict(
            check, f"{_SMELL_PREFIX} `{SMELL_PERSONA}` recorded {label!r}, which is not a "
            f"verdict this sensor reads (APPROVE / REJECT / APPROVE_WITH_CONDITIONS) — "
            f"record it again with `rig-wb wb review <task_id> --set {SMELL_PERSONA}=<verdict>`",
            _SMELL_WRITER, _SMELL_PREFIX)
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
    unclosed: list[dict] = []
    for rel in sorted(targets):
        nos = targets[rel]
        for f in jt.lint_text(jt.read_source(str(repo / rel)), settings, rel,
                              unclosed_out=unclosed):
            if nos is not None and f["line"] not in nos:
                continue
            errors += f["severity"] == "error"
            warnings += f["severity"] == "warning"
            print(f"{f['file']}:{f['line']}:{f['column']}: {f['severity']} [{f['rule']}] {f['message']}")
    notes = "".join(f"{u['file']}:{u['line']}: unclosed <!-- textlint-disable --> — suppresses "
                    f"to end of file; {LINT_CRITERION} fails on it\n" for u in unclosed)
    print(f"{notes}{task_id}: {errors} error(s) / {warnings} warning(s) on added Japanese lines in "
          f"{len(targets)} file(s)")
    if errors:
        raise SystemExit(1)
