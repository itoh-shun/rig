"""workbench destructive: dangerous-command sensor backing `no_destructive_operation`
(issue #315).

Deterministic scan for destructive command patterns in everything the task
introduced — added diff lines plus untracked files, reusing the diff helpers
from secrets.py — plus a mass-deletion check on the diff itself. Evidence:
real-world agent incidents where a "clean up branches" request ended in
`git clean`-style destruction of unrelated data; an agent-authored script or
CI step carrying such a command is a time bomb the human reviewer must see.

Honest scope: this scans destructive commands **written into the diff**
(scripts, CI configs, migrations, docs). It does NOT intercept commands the
agent executes at run time — that is the host permission system's job, not
something a diff-time gate can do. The point is that the diff is the one
artifact rig fully controls, and a dangerous command landing in it is
machine-detectable.

Two grades:
  fail     unambiguous destroyers regardless of context: `rm -rf /` (root or
           /*), mkfs, `dd of=/dev/...`, DROP DATABASE.
  warning  context-dependent — needs a human look, not an automatic block:
           `rm -rf` on an absolute path / variable expansion / `~`,
           `git clean -f...`, `git reset --hard`, `git push --force`
           (without --force-with-lease), DROP TABLE / TRUNCATE,
           `chmod -R 777`. A relative-path `rm -rf build/` is deliberately
           NOT flagged (Makefile clean targets are everyday-legitimate; the
           risk profile this sensor exists for is absolute paths and
           empty-variable expansion).

Mass deletion: the diff removing >= MASS_DELETE_THRESHOLD files is
warning-grade — often intentional, always worth a second look.

Gate wiring mirrors secrets/injection: `cmd_gate` calls
apply_destructive_sensor() on every evaluation; findings are recorded on the
check under "destructive_findings"; explicit
`--set no_destructive_operation=passed` is the escape hatch, recorded as
destructive_override on the check, sticky across later evaluations.

CLI: `workbench.py scan-destructive [paths...]` scans files/trees;
`scan-destructive --diff <task-id>` scans the task worktree's diff vs its
base commit (added lines + untracked files + mass-deletion count).
"""

import argparse
import pathlib
import re
import sys

from .secrets import (MAX_FILE_BYTES, WALK_SKIP_DIRS, iter_added_lines,
                      untracked_files, worktree_diff_text)
from .state import die, git, load_task, repo_root

SENSOR_CRITERION = "no_destructive_operation"
MASS_DELETE_THRESHOLD = 20

# ── detectors ─────────────────────────────────────────────────────────────────
# (kind, grade, pattern). Order matters only for readability; every pattern is
# applied to every line. Patterns are deliberately narrow — a missed borderline
# case is recoverable via review, a noisy sensor gets overridden into deafness.
_RM_FLAGS = r"-[a-zA-Z]*[rR][a-zA-Z]*[fF][a-zA-Z]*|-[a-zA-Z]*[fF][a-zA-Z]*[rR][a-zA-Z]*"
PATTERNS: tuple[tuple[str, str, re.Pattern], ...] = (
    ("rm_root", "fail",
     re.compile(rf"\brm\s+(?:{_RM_FLAGS})\s+(?:\"|')?/(?:\*|\s|\"|'|$)")),
    ("mkfs", "fail", re.compile(r"\bmkfs(?:\.\w+)?\b")),
    ("dd_device", "fail", re.compile(r"\bdd\b[^\n]*\bof=(?:\"|')?/dev/")),
    ("drop_database", "fail", re.compile(r"\bDROP\s+DATABASE\b", re.IGNORECASE)),
    ("rm_absolute", "warning",
     re.compile(rf"\brm\s+(?:{_RM_FLAGS})\s+(?:\"|')?/\w")),
    ("rm_variable", "warning",
     re.compile(rf"\brm\s+(?:{_RM_FLAGS})\s+(?:\"|')?\$\{{?\w")),
    ("rm_home", "warning",
     re.compile(rf"\brm\s+(?:{_RM_FLAGS})\s+(?:\"|')?~")),
    ("git_clean_force", "warning", re.compile(r"\bgit\s+clean\b[^\n]*\s-[a-zA-Z]*f")),
    ("git_reset_hard", "warning", re.compile(r"\bgit\s+reset\s+--hard\b")),
    ("git_push_force", "warning",
     re.compile(r"\bgit\s+push\b[^\n]*--force(?!-with-lease)\b")),
    ("drop_table", "warning", re.compile(r"\bDROP\s+TABLE\b", re.IGNORECASE)),
    ("truncate_table", "warning", re.compile(r"\bTRUNCATE\s+(?:TABLE\s+)?\w", re.IGNORECASE)),
    ("chmod_777", "warning", re.compile(r"\bchmod\s+-R\s+777\b")),
)

EXCERPT_MAX = 60


def _excerpt(text: str, limit: int = EXCERPT_MAX) -> str:
    s = " ".join(text.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def scan_line(line: str, rel: str, lineno: int) -> list[dict]:
    """Scan one line for destructive command patterns.

    Findings: {"path", "line", "grade", "kind", "excerpt"}. A line is reported
    once per matching kind (not once per match) — the reviewer needs the line,
    not a count."""
    findings: list[dict] = []
    for kind, grade, rx in PATTERNS:
        if rx.search(line):
            findings.append({"path": rel, "line": lineno, "grade": grade,
                             "kind": kind, "excerpt": _excerpt(line)})
    return findings


def scan_file(path: pathlib.Path, rel: str | None = None) -> list[dict]:
    """Scan a file. Binary (NUL in the first 8 KiB) and oversized files are skipped."""
    rel = rel if rel is not None else str(path)
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return []
        raw = path.read_bytes()
    except OSError:
        return []
    if b"\0" in raw[:8192]:
        return []
    text = raw.decode("utf-8", errors="replace")
    findings: list[dict] = []
    for i, line in enumerate(text.splitlines(), start=1):
        findings.extend(scan_line(line, rel, i))
    return findings


def scan_paths(paths: list[pathlib.Path]) -> list[dict]:
    """Scan files and directory trees (vendored/VCS dirs skipped entirely)."""
    findings: list[dict] = []
    for p in paths:
        if p.is_file():
            findings.extend(scan_file(p))
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if not f.is_file():
                    continue
                if any(part in WALK_SKIP_DIRS for part in f.relative_to(p).parts):
                    continue
                findings.extend(scan_file(f))
        else:
            die(f"path '{p}' does not exist")
    return findings


def count_deleted_files(wt: pathlib.Path, base_commit: str) -> int:
    """How many files the task deletes relative to base (committed changes)."""
    proc = git(["diff", "--name-status", base_commit], cwd=wt, check=False)
    if proc.returncode != 0:
        return 0
    return sum(1 for ln in proc.stdout.splitlines() if ln.startswith("D"))


def scan_task_diff(wt: pathlib.Path, base_commit: str) -> tuple[list[dict], int]:
    """(pattern findings over added lines + untracked files, deleted-file count)."""
    findings: list[dict] = []
    for rel, lineno, text in iter_added_lines(worktree_diff_text(wt, base_commit)):
        findings.extend(scan_line(text, rel, lineno))
    for f, rel in untracked_files(wt):
        findings.extend(scan_file(f, rel))
    return findings, count_deleted_files(wt, base_commit)


def format_findings(findings: list[dict]) -> list[str]:
    return [f"{f['path']}:{f['line']} [{f['kind']}/{f['grade']}] {f['excerpt']}" for f in findings]


# ── the sensor (called from cmd_gate) ─────────────────────────────────────────
_SENSOR_DETAIL_PREFIX = "(destructive sensor)"


def apply_destructive_sensor(root: pathlib.Path, run_d: pathlib.Path, task: dict, acc: dict,
                             explicit_set: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """Machine-back `no_destructive_operation` with a diff-scoped command scan.

    Mutates `acc` in place (caller persists it) and returns printable notes.
    No `no_destructive_operation` in the gate, or no worktree/base → no-op.

    Any fail-grade finding → the check is set to **failed**. Warning-only
    findings (including a mass deletion) → **warning** (never overrides an
    explicit failed). Escape hatch: an explicit
    `--set no_destructive_operation=passed` in the current invocation is
    respected, recorded as destructive_override=True, sticky afterwards.
    """
    check = next((c for c in acc.get("checks", []) if c["name"] == SENSOR_CRITERION), None)
    if check is None:
        return []
    wt_path = task.get("worktree_path")
    base = task.get("base_commit")
    if not wt_path or not base:
        return []
    wt = pathlib.Path(wt_path)
    if not wt.is_dir():
        return []

    findings, n_deleted = scan_task_diff(wt, base)
    mass_delete = n_deleted >= MASS_DELETE_THRESHOLD
    if not findings and not mass_delete:
        # Danger gone from the diff: clear our state; un-flag only what WE flagged.
        if check.pop("destructive_findings", None) is not None:
            check.pop("destructive_override", None)
            if check["status"] in ("failed", "warning") and \
                    str(check.get("detail", "")).startswith(_SENSOR_DETAIL_PREFIX):
                check["status"] = "pending"
                check["detail"] = ""
                return [f"{_SENSOR_DETAIL_PREFIX} previously detected destructive patterns are no "
                        f"longer in the diff → {SENSOR_CRITERION} reset to pending"]
        return []

    lines = format_findings(findings)
    if mass_delete:
        lines.append(f"(mass deletion) {n_deleted} file(s) deleted vs base "
                     f"(threshold {MASS_DELETE_THRESHOLD}) — confirm this is intended")
    check["destructive_findings"] = lines
    n = len(lines)
    n_fail = sum(1 for f in findings if f["grade"] == "fail")
    notes: list[str] = []
    if SENSOR_CRITERION in explicit_set and check["status"] == "passed":
        check["destructive_override"] = True
        if str(check.get("detail", "")).startswith(_SENSOR_DETAIL_PREFIX):
            check["detail"] = (f"{_SENSOR_DETAIL_PREFIX} {n} finding(s) manually overridden "
                               "after review (destructive_override)")
        notes.append(f"{_SENSOR_DETAIL_PREFIX} {n} destructive pattern(s) still present, but "
                     f"{SENSOR_CRITERION} was explicitly set to passed — manual override recorded:")
    elif check.get("destructive_override") and check["status"] == "passed":
        notes.append(f"{_SENSOR_DETAIL_PREFIX} {n} destructive pattern(s) present — "
                     "manual override previously recorded, keeping passed:")
    elif n_fail:
        check["status"] = "failed"
        check["detail"] = (f"{_SENSOR_DETAIL_PREFIX} {n_fail} unambiguous destroyer(s) detected in "
                           f"the diff — remove them, or after review override with "
                           f"--set {SENSOR_CRITERION}=passed")
        notes.append(f"{_SENSOR_DETAIL_PREFIX} {n} destructive pattern(s) detected "
                     f"({n_fail} fail-grade) → {SENSOR_CRITERION} failed:")
    else:
        if check["status"] in ("pending", "passed", "warning"):
            check["status"] = "warning"
            if not check.get("detail") or str(check["detail"]).startswith(_SENSOR_DETAIL_PREFIX):
                check["detail"] = (f"{_SENSOR_DETAIL_PREFIX} {n} context-dependent destructive "
                                   f"pattern(s) — review them (override with "
                                   f"--set {SENSOR_CRITERION}=passed)")
        notes.append(f"{_SENSOR_DETAIL_PREFIX} {n} context-dependent destructive pattern(s) → "
                     f"{SENSOR_CRITERION} recorded as warning:")
    notes.extend(f"  {ln}" for ln in lines)
    return notes


# ── CLI ───────────────────────────────────────────────────────────────────────
def cmd_scan_destructive(args: argparse.Namespace) -> None:
    if args.diff and args.paths:
        die("give either paths or --diff <task-id>, not both")
    n_deleted = 0
    if args.diff:
        root = repo_root()
        _, task = load_task(root, args.diff)
        wt_path = task.get("worktree_path")
        base = task.get("base_commit")
        if not wt_path or not pathlib.Path(wt_path).is_dir():
            die(f"task '{args.diff}' has no worktree (created with --no-worktree, or already discarded)")
        if not base:
            die(f"task '{args.diff}' has no base_commit recorded")
        findings, n_deleted = scan_task_diff(pathlib.Path(wt_path), base)
        scope = f"diff of task {args.diff} (worktree vs {base[:12]})"
    else:
        paths = [pathlib.Path(p) for p in (args.paths or ["."])]
        findings = scan_paths(paths)
        scope = ", ".join(str(p) for p in paths)

    print(f"## scan-destructive: {scope}")
    mass_delete = n_deleted >= MASS_DELETE_THRESHOLD
    if not findings and not mass_delete:
        print("No destructive patterns found.")
        return
    n_fail = sum(1 for f in findings if f["grade"] == "fail")
    print(f"{len(findings)} destructive pattern(s) found "
          f"({n_fail} fail-grade, {len(findings) - n_fail} warning-grade):")
    for line in format_findings(findings):
        print(f"  {line}")
    if mass_delete:
        print(f"  (mass deletion) {n_deleted} file(s) deleted vs base "
              f"(threshold {MASS_DELETE_THRESHOLD})")
    sys.exit(1)
