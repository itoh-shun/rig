"""workbench injection: prompt-injection-marker sensor backing `no_injection_markers`.

Deterministic scan for prompt-injection markers in (a) everything the task
introduced — added diff lines plus untracked files, reusing the diff helpers
from secrets.py — and (b) the repo-controlled prose surfaces that agents read
as instructions (`.claude/rig.md`, `.claude/rig/knowledge/**`, project persona
files under `.claude/rig/personas/**`, `.rig/recipes/*.md`). Evidence: the
Pillar "Rules-File-Backdoor" — invisible Unicode instructions planted in agent
config files survive human review because reviewers cannot see them.

Two detector classes:
  invisible_unicode  zero-width / bidi-control characters
                     (U+200B–200F, U+202A–202E, U+2060–2064, U+FEFF).
                     FAIL-grade: never legitimate in source or prose.
  override_phrase    instruction-override phrases ("ignore previous
                     instructions", "disregard … instructions", "system
                     prompt", "you are now", …; PHRASE_PATTERNS,
                     case-insensitive). WARNING-grade: docs ABOUT prompts are
                     legitimate false positives.

Gate wiring mirrors secrets.apply_secret_sensor: `cmd_gate` calls
apply_injection_sensor() on every evaluation; findings are recorded on the
check under "injection_findings" (bounded excerpts, invisible characters
rendered as <U+XXXX> escapes — never raw); explicit
`--set no_injection_markers=passed` is the escape hatch, recorded as
injection_override on the check, sticky across later evaluations.

CLI: `workbench.py scan-injection [paths...]` scans files/trees (default: the
repo's prose surfaces); `scan-injection --diff <task-id>` scans the task
worktree's diff + its prose surfaces, exactly what the gate sensor sees.
"""

import argparse
import pathlib
import re
import sys

from .secrets import (MAX_FILE_BYTES, WALK_SKIP_DIRS, iter_added_lines,
                      untracked_files, worktree_diff_text)
from .state import die, load_task, repo_root

SENSOR_CRITERION = "no_injection_markers"

# ── detectors ─────────────────────────────────────────────────────────────────
# Zero-width / bidi-control code points. Never legitimate in source or prose:
# zero-width spaces/joiners (U+200B–200F, U+2060–2064, U+FEFF/BOM) hide text
# from humans; bidi overrides (U+202A–202E) reorder what reviewers see.
INVISIBLE_RE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")

# Instruction-override phrases (case-insensitive). Warning-grade by design:
# documentation about prompts legitimately contains several of these.
PHRASE_PATTERNS: tuple[re.Pattern, ...] = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions",
    r"disregard\s+.{0,20}instructions",
    r"forget\s+(?:all\s+)?(?:previous|prior|above)\s+instructions",
    r"override\s+(?:all\s+)?(?:previous|prior|above)\s+instructions",
    r"system\s+prompt",
    r"you\s+are\s+now",
    r"new\s+instructions\s*:",
    r"do\s+not\s+(?:tell|inform|alert|reveal\s+to)\s+the\s+user",
))

# Repo-controlled prose surfaces agents ingest as instructions (scanned in
# full, not just the diff — a pre-existing backdoor is still a backdoor).
PROSE_SURFACE_FILES = (".claude/rig.md",)
PROSE_SURFACE_DIRS = (".claude/rig/knowledge", ".claude/rig/personas")
PROSE_SURFACE_GLOBS = (".rig/recipes/*.md",)

# Dependency trees (#320): supply-chain attacks have planted agent-directed
# instructions in third-party docs (the jqwik incident — a hidden instruction
# telling agents to delete their output). Scanning these is explicit opt-in
# (--deps) because the trees are huge and READMEs of AI-adjacent libraries
# legitimately contain prompt examples — override-phrase findings here are
# especially false-positive-prone (hence warning-grade as always), while
# invisible unicode stays fail-grade: it has zero legitimate uses, and is
# exactly the hiding mechanism such attacks use.
DEP_ROOTS = ("node_modules", "vendor", "third_party")
DEP_PROSE_SUFFIXES = (".md", ".markdown", ".rst", ".txt")

EXCERPT_MAX = 60


def _escape_invisible(s: str) -> str:
    return INVISIBLE_RE.sub(lambda m: f"<U+{ord(m.group(0)):04X}>", s)


def bounded_excerpt(text: str, limit: int = EXCERPT_MAX) -> str:
    """Single-line excerpt: invisible characters escaped, whitespace collapsed,
    hard-bounded. Findings never carry raw invisible characters."""
    s = " ".join(_escape_invisible(text).split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def scan_line(line: str, rel: str, lineno: int) -> list[dict]:
    """Scan one line of text for injection markers.

    Findings: {"path", "line", "grade", "kind", "excerpt"}. Invisible-unicode
    hits are grouped per line (one finding listing the distinct code points)."""
    findings: list[dict] = []
    invisible = INVISIBLE_RE.findall(line)
    if invisible:
        codepoints = sorted({f"U+{ord(ch):04X}" for ch in invisible})
        first = INVISIBLE_RE.search(line).start()
        ctx = bounded_excerpt(line[max(0, first - 15): first + 16], 45)
        findings.append({"path": rel, "line": lineno, "grade": "fail",
                         "kind": "invisible_unicode",
                         "excerpt": f"{len(invisible)} char(s) {','.join(codepoints)} — {ctx}"})
    for rx in PHRASE_PATTERNS:
        for m in rx.finditer(line):
            findings.append({"path": rel, "line": lineno, "grade": "warning",
                             "kind": "override_phrase",
                             "excerpt": f"\"{bounded_excerpt(m.group(0))}\""})
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


def prose_surface_paths(base_dir: pathlib.Path) -> list[tuple[pathlib.Path, str]]:
    """(absolute path, repo-relative path) of the prose surfaces that exist
    under `base_dir` (a repo root or a task worktree)."""
    out: list[tuple[pathlib.Path, str]] = []
    for rel in PROSE_SURFACE_FILES:
        f = base_dir / rel
        if f.is_file():
            out.append((f, rel))
    for rel in PROSE_SURFACE_DIRS:
        d = base_dir / rel
        if d.is_dir():
            for f in sorted(d.rglob("*")):
                if f.is_file():
                    out.append((f, f.relative_to(base_dir).as_posix()))
    for pattern in PROSE_SURFACE_GLOBS:
        for f in sorted(base_dir.glob(pattern)):
            if f.is_file():
                out.append((f, f.relative_to(base_dir).as_posix()))
    return out


def dep_prose_paths(root: pathlib.Path) -> list[tuple[pathlib.Path, str]]:
    """(absolute path, repo-relative path) of prose files (docs, not source)
    under the dependency roots that exist. Own walker on purpose: scan_paths'
    generic tree walk skips node_modules via WALK_SKIP_DIRS — correct for
    every other scan, overridden here explicitly."""
    out: list[tuple[pathlib.Path, str]] = []
    for dep_root in DEP_ROOTS:
        d = root / dep_root
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*")):
            if f.is_file() and f.suffix.lower() in DEP_PROSE_SUFFIXES:
                out.append((f, f.relative_to(root).as_posix()))
    return out


def scan_task_surfaces(wt: pathlib.Path, base_commit: str) -> list[dict]:
    """Everything the gate sensor looks at: added diff lines + untracked files
    (what the task introduced) AND the worktree's prose surfaces in full
    (what agents will ingest). Deduplicated — a changed prose surface would
    otherwise be reported twice."""
    findings: list[dict] = []
    for rel, lineno, text in iter_added_lines(worktree_diff_text(wt, base_commit)):
        findings.extend(scan_line(text, rel, lineno))
    for f, rel in untracked_files(wt):
        findings.extend(scan_file(f, rel))
    for f, rel in prose_surface_paths(wt):
        findings.extend(scan_file(f, rel))
    seen: set[tuple] = set()
    unique: list[dict] = []
    for f in findings:
        key = (f["path"], f["line"], f["kind"], f["excerpt"])
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def format_findings(findings: list[dict]) -> list[str]:
    return [f"{f['path']}:{f['line']} [{f['kind']}] {f['excerpt']}" for f in findings]


# ── the sensor (called from cmd_gate) ─────────────────────────────────────────
_SENSOR_DETAIL_PREFIX = "(injection sensor)"


def apply_injection_sensor(root: pathlib.Path, run_d: pathlib.Path, task: dict, acc: dict,
                           explicit_set: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """Machine-back `no_injection_markers` with a diff + prose-surface scan.

    Mutates `acc` in place (caller persists it) and returns printable notes.
    No `no_injection_markers` in the gate, or no worktree/base → no-op.

    Any invisible-unicode finding → the check is set to **failed** (invisible
    characters are never legitimate). Phrase-only findings → the check becomes
    **warning** (docs about prompts are plausible false positives; never
    overrides an explicit failed). Findings are recorded on the check under
    "injection_findings". Escape hatch: an explicit
    `--set no_injection_markers=passed` in the current invocation is respected
    and recorded as injection_override=True, sticky across later evaluations.
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

    findings = scan_task_surfaces(wt, base)
    if not findings:
        # Markers gone: clear our state; un-flag only what WE flagged.
        if check.pop("injection_findings", None) is not None:
            check.pop("injection_override", None)
            if check["status"] in ("failed", "warning") and \
                    str(check.get("detail", "")).startswith(_SENSOR_DETAIL_PREFIX):
                check["status"] = "pending"
                check["detail"] = ""
                return [f"{_SENSOR_DETAIL_PREFIX} previously detected injection markers are no "
                        f"longer present → {SENSOR_CRITERION} reset to pending"]
        return []

    lines = format_findings(findings)
    check["injection_findings"] = lines
    n = len(findings)
    n_fail = sum(1 for f in findings if f["grade"] == "fail")
    notes: list[str] = []
    if SENSOR_CRITERION in explicit_set and check["status"] == "passed":
        check["injection_override"] = True
        if str(check.get("detail", "")).startswith(_SENSOR_DETAIL_PREFIX):
            check["detail"] = (f"{_SENSOR_DETAIL_PREFIX} {n} finding(s) manually overridden "
                               "after review (injection_override)")
        notes.append(f"{_SENSOR_DETAIL_PREFIX} {n} injection marker(s) still present, but "
                     f"{SENSOR_CRITERION} was explicitly set to passed — manual override recorded:")
    elif check.get("injection_override") and check["status"] == "passed":
        notes.append(f"{_SENSOR_DETAIL_PREFIX} {n} injection marker(s) present — "
                     "manual override previously recorded, keeping passed:")
    elif n_fail:
        check["status"] = "failed"
        check["detail"] = (f"{_SENSOR_DETAIL_PREFIX} {n_fail} invisible-unicode marker(s) detected — "
                           f"remove them, or after review override with --set {SENSOR_CRITERION}=passed")
        notes.append(f"{_SENSOR_DETAIL_PREFIX} {n} injection marker(s) detected "
                     f"({n_fail} invisible-unicode, fail-grade) → {SENSOR_CRITERION} failed:")
    else:
        if check["status"] in ("pending", "passed", "warning"):
            check["status"] = "warning"
            if not check.get("detail") or str(check["detail"]).startswith(_SENSOR_DETAIL_PREFIX):
                check["detail"] = (f"{_SENSOR_DETAIL_PREFIX} {n} instruction-override phrase(s) "
                                   f"detected — review them (override with --set {SENSOR_CRITERION}=passed)")
        notes.append(f"{_SENSOR_DETAIL_PREFIX} {n} instruction-override phrase(s) detected → "
                     f"{SENSOR_CRITERION} recorded as warning:")
    notes.extend(f"  {ln}" for ln in lines)
    return notes


# ── CLI ───────────────────────────────────────────────────────────────────────
def cmd_scan_injection(args: argparse.Namespace) -> None:
    deps_mode = getattr(args, "deps", False)
    if sum(bool(x) for x in (args.diff, args.paths, deps_mode)) > 1:
        die("give paths, --diff <task-id>, or --deps — not a combination")
    if deps_mode:
        root = repo_root()
        surfaces = dep_prose_paths(root)
        findings = []
        for f, rel in surfaces:
            findings.extend(scan_file(f, rel))
        present = [d for d in DEP_ROOTS if (root / d).is_dir()]
        scope = (f"dependency prose surfaces of {root} ({len(surfaces)} file(s) under "
                 f"{', '.join(present)})" if present
                 else f"dependency prose surfaces of {root} (no {'/'.join(DEP_ROOTS)} present)")
        print(f"## scan-injection: {scope}")
        if not findings:
            print("No injection markers found.")
            return
        n_fail = sum(1 for f in findings if f["grade"] == "fail")
        print(f"{len(findings)} injection marker(s) found "
              f"({n_fail} invisible-unicode fail-grade, {len(findings) - n_fail} phrase warning-grade):")
        for line in format_findings(findings):
            print(f"  {line}")
        print("Recommended actions: review the finding in context (AI-library READMEs "
              "legitimately contain prompt examples — phrase findings here are "
              "false-positive-prone); if real, pin/quarantine the dependency and report upstream. "
              "Invisible unicode has no legitimate use and warrants immediate quarantine.")
        sys.exit(1)
    if args.diff:
        root = repo_root()
        _, task = load_task(root, args.diff)
        wt_path = task.get("worktree_path")
        base = task.get("base_commit")
        if not wt_path or not pathlib.Path(wt_path).is_dir():
            die(f"task '{args.diff}' has no worktree (created with --no-worktree, or already discarded)")
        if not base:
            die(f"task '{args.diff}' has no base_commit recorded")
        findings = scan_task_surfaces(pathlib.Path(wt_path), base)
        scope = f"diff + prose surfaces of task {args.diff} (worktree vs {base[:12]})"
    elif args.paths:
        paths = [pathlib.Path(p) for p in args.paths]
        findings = scan_paths(paths)
        scope = ", ".join(str(p) for p in paths)
    else:
        root = repo_root()
        surfaces = prose_surface_paths(root)
        findings = []
        for f, rel in surfaces:
            findings.extend(scan_file(f, rel))
        scope = (f"prose surfaces of {root} ({len(surfaces)} file(s))" if surfaces
                 else f"prose surfaces of {root} (none present)")

    print(f"## scan-injection: {scope}")
    if not findings:
        print("No injection markers found.")
        return
    n_fail = sum(1 for f in findings if f["grade"] == "fail")
    print(f"{len(findings)} injection marker(s) found "
          f"({n_fail} invisible-unicode fail-grade, {len(findings) - n_fail} phrase warning-grade):")
    for line in format_findings(findings):
        print(f"  {line}")
    sys.exit(1)
