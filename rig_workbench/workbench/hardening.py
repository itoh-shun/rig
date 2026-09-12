"""workbench hardening: anti-tamper sensor backing `no_gate_tampering`.

Deterministic scan of the task worktree's diff vs its base commit for
reward-hacking patterns (evidence: METR's reward-hacking observations — models
under an acceptance gate edit the tests or the scoring/config instead of doing
the task). Findings come in two grades:

FAIL-grade (block accept unless explicitly overridden):
  gate_config_modified   `.rig/gates.json` touched in the task diff
  recipe_modified        recipe files under `.rig/recipes/` touched
  ci_workflow_modified   CI workflow files (`.github/workflows/*`) touched
  A change that reshapes the gate/CI itself must never ride along silently.
  Exempt when the task_type (task.json "task_type") is explicitly about
  CI/release configuration (CI_CONFIG_TASK_TYPES).

WARNING-grade (surfaced, never block on their own):
  test_file_modified /   existing test files (present at base) modified or
  test_file_deleted      deleted — only for TEST_GUARD_TASK_TYPES
                         (bugfix/feature; task types that legitimately rework
                         tests, e.g. `test`, are exempt from ALL test-weakening
                         heuristics via TEST_EXEMPT_TASK_TYPES)
  asserts_removed        assert/expect lines removed from a test file, counted
                         per file (plain counts, no cleverness)
  skip_marker_added      skip markers added (@skip, xfail, .skip(, it.skip, …)

Gate wiring mirrors secrets.apply_secret_sensor: `cmd_gate` calls
apply_tamper_sensor() on every evaluation; findings are recorded on the check
in acceptance.json under "tamper_findings" (bounded excerpts only). The scan is
the verdict — a `--set no_gate_tampering=passed` that contradicts it is refused
by `cmd_gate` (lifecycle.sensor_contradictions, whose docstring carries the
reasoning). A finding reviewed and accepted anyway is carried by
`accept --force`, which audits, waives and signs the bypass. A gate criterion
that a declaration can talk past is precisely the reward-hacking surface this
module exists to close.
"""

import pathlib
import re

from .secrets import untracked_files, worktree_diff_text
from .state import effective_base, git, record_sensor_status

SENSOR_CRITERION = "no_gate_tampering"

# task_types whose job IS the CI/gate configuration → fail-grade findings do not apply.
CI_CONFIG_TASK_TYPES = ("release_support",)
# task_types where touching existing test files is itself suspicious (spec: bugfix/feature).
TEST_GUARD_TASK_TYPES = ("bugfix", "feature")
# task_types that legitimately rework tests → exempt from every warning-grade heuristic.
TEST_EXEMPT_TASK_TYPES = ("test",)

# Gate/CI configuration surfaces (fail-grade when touched by a non-exempt task).
GATE_CONFIG_PATH = ".rig/gates.json"
RECIPE_DIR_PREFIX = ".rig/recipes/"
CI_WORKFLOW_PREFIX = ".github/workflows/"

# Test-file heuristics.
TEST_DIR_PARTS = ("test", "tests", "__tests__", "spec", "specs")
TEST_BASENAME_RE = re.compile(
    r"^(?:test_.+|.+_test\.[^.]+|.+\.(?:test|spec)\.[^.]+|conftest\.py)$")

# Assertion-weakening: lines that carry an assertion (Python assert /
# unittest self.assert* / JS-style expect(...)). Counted per file, not judged.
ASSERT_RE = re.compile(r"^\s*(?:assert\b|self\.assert\w*\(|expect\s*\()")
# Skip markers added to test code (substring match on added lines).
SKIP_MARKERS = ("@skip", "xfail", ".skip(", "it.skip", "mark.skip")

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)")
_STATUS_WORD = {"A": "added", "M": "modified", "D": "deleted"}
EXCERPT_MAX = 60


def bounded_excerpt(text: str, limit: int = EXCERPT_MAX) -> str:
    """Single-line excerpt, stripped and hard-bounded (findings stay small)."""
    s = " ".join(text.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def is_test_path(rel: str) -> bool:
    """Heuristic: is `rel` a test file? Directory parts (tests/, spec/, …) or
    test-shaped basenames (test_*.py, *_test.go, *.spec.ts, conftest.py, …)."""
    p = pathlib.PurePosixPath(rel.replace("\\", "/"))
    if any(part.lower() in TEST_DIR_PARTS for part in p.parts[:-1]):
        return True
    return bool(TEST_BASENAME_RE.match(p.name))


def changed_files(wt: pathlib.Path, base_commit: str) -> list[tuple[str, str]]:
    """(status, repo-relative path) of everything the task changed vs base:
    `git diff --name-status` (committed + uncommitted) plus untracked files
    (reported as "A" — they are additions the diff cannot see)."""
    proc = git(["diff", "--name-status", "--no-renames", base_commit], cwd=wt, check=False)
    out: list[tuple[str, str]] = []
    for line in proc.stdout.splitlines() if proc.returncode == 0 else []:
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0]:
            out.append((parts[0][:1], parts[-1]))
    for _f, rel in untracked_files(wt):
        out.append(("A", rel))
    return out


def _config_tamper_kind(rel: str) -> str | None:
    posix = rel.replace("\\", "/")
    if posix == GATE_CONFIG_PATH:
        return "gate_config_modified"
    if posix.startswith(RECIPE_DIR_PREFIX):
        return "recipe_modified"
    if posix.startswith(CI_WORKFLOW_PREFIX):
        return "ci_workflow_modified"
    return None


def _diff_heuristics(diff_text: str) -> tuple[dict[str, int], dict[str, int], list[dict]]:
    """(removed asserts per file, added asserts per file, skip-marker findings)
    from a unified diff. Removed lines are attributed to the old path, added
    lines to the new path; skip-marker findings carry a bounded excerpt."""
    removed: dict[str, int] = {}
    added: dict[str, int] = {}
    skips: list[dict] = []
    old_rel: str | None = None
    new_rel: str | None = None
    lineno = 0
    for line in diff_text.splitlines():
        if line.startswith("--- "):
            t = line[4:].strip()
            old_rel = None if t == "/dev/null" else t[2:] if t.startswith("a/") else t
            continue
        if line.startswith("+++ "):
            t = line[4:].strip()
            new_rel = None if t == "/dev/null" else t[2:] if t.startswith("b/") else t
            continue
        m = _HUNK_RE.match(line)
        if m:
            lineno = int(m.group(1))
            continue
        if line.startswith("+") and not line.startswith("+++"):
            text = line[1:]
            if new_rel is not None:
                if ASSERT_RE.match(text):
                    added[new_rel] = added.get(new_rel, 0) + 1
                marker = next((mk for mk in SKIP_MARKERS if mk in text), None)
                if marker:
                    skips.append({"path": new_rel, "line": lineno, "marker": marker,
                                  "excerpt": bounded_excerpt(text)})
            lineno += 1
        elif line.startswith("-") and not line.startswith("---"):
            if old_rel is not None and ASSERT_RE.match(line[1:]):
                removed[old_rel] = removed.get(old_rel, 0) + 1
        elif line.startswith(" "):
            lineno += 1
    return removed, added, skips


def scan_tampering(wt: pathlib.Path, base_commit: str, task_type: str) -> list[dict]:
    """Scan the task worktree's diff vs base for tampering patterns.

    Findings: {"path", "grade" ("fail"|"warning"), "kind", "detail"}.
    Task-type exemptions (CI_CONFIG_TASK_TYPES / TEST_EXEMPT_TASK_TYPES /
    TEST_GUARD_TASK_TYPES) are applied here, so callers pass task_type as-is.
    """
    findings: list[dict] = []
    changes = changed_files(wt, base_commit)

    # ── fail-grade: gate/CI configuration touched by a non-CI task ────────────
    if task_type not in CI_CONFIG_TASK_TYPES:
        for status, rel in changes:
            kind = _config_tamper_kind(rel)
            if kind:
                findings.append({"path": rel, "grade": "fail", "kind": kind,
                                 "detail": f"{_STATUS_WORD.get(status, status)} in the task diff "
                                           f"(task_type '{task_type}' is not about CI/config)"})

    # ── warning-grade: test-weakening heuristics ──────────────────────────────
    if task_type in TEST_EXEMPT_TASK_TYPES:
        return findings

    if task_type in TEST_GUARD_TASK_TYPES:
        for status, rel in changes:
            if status in ("M", "D") and is_test_path(rel):
                word = _STATUS_WORD[status]
                findings.append({"path": rel, "grade": "warning",
                                 "kind": f"test_file_{word}",
                                 "detail": f"existing test file {word} in a {task_type} task"})

    removed, added, skips = _diff_heuristics(worktree_diff_text(wt, base_commit))
    for rel in sorted(removed):
        if is_test_path(rel):
            findings.append({"path": rel, "grade": "warning", "kind": "asserts_removed",
                             "detail": f"{removed[rel]} assert/expect line(s) removed, "
                                       f"{added.get(rel, 0)} added"})
    for s in skips:
        if is_test_path(s["path"]):
            findings.append({"path": f"{s['path']}:{s['line']}", "grade": "warning",
                             "kind": "skip_marker_added",
                             "detail": f"'{s['marker']}' — {s['excerpt']}"})
    return findings


def format_findings(findings: list[dict]) -> list[str]:
    return [f"{f['path']} [{f['kind']}] {f['detail']}" for f in findings]


# ── the sensor (called from cmd_gate) ─────────────────────────────────────────
_SENSOR_DETAIL_PREFIX = "(tamper sensor)"
#: config.WRITER_OPERATOR's counterpart: this sensor as the writer of a status.
WRITER = "tamper-sensor"


def apply_tamper_sensor(root: pathlib.Path, run_d: pathlib.Path, task: dict,
                       acc: dict) -> list[str]:
    """Machine-back `no_gate_tampering` with a diff-scoped tamper scan.

    Mutates `acc` in place (caller persists it) and returns printable notes.
    No `no_gate_tampering` in the gate, or no worktree/base → no-op.

    Any fail-grade finding → the check is set to **failed** (a gate/CI-config
    edit riding in a normal task diff must block accept). Warning-grade-only
    findings → the check becomes **warning** (never overrides an explicit
    failed). Findings are recorded on the check under "tamper_findings".
    The scan is the verdict, and it is written over whatever the gate's `--set`
    put there; `cmd_gate` refuses an invocation whose hand-written status this
    contradicts rather than recording either one.
    """
    check = next((c for c in acc.get("checks", []) if c["name"] == SENSOR_CRITERION), None)
    if check is None:
        return []
    wt_path = task.get("worktree_path")
    # Live merge base, not the registration-time record (#312): scanning a rebased
    # branch against a stale base would flag changes the task never made.
    base, _drift = effective_base(root, task)
    if not wt_path or not base:
        return []
    wt = pathlib.Path(wt_path)
    if not wt.is_dir():
        return []

    findings = scan_tampering(wt, base, task.get("task_type", ""))
    if not findings:
        # Tampering gone from the diff: clear our state; un-flag only what WE flagged.
        if check.pop("tamper_findings", None) is not None:
            if check["status"] in ("failed", "warning") and \
                    str(check.get("detail", "")).startswith(_SENSOR_DETAIL_PREFIX):
                record_sensor_status(check, "pending", "", WRITER)
                return [f"{_SENSOR_DETAIL_PREFIX} previously detected tampering patterns are no "
                        f"longer in the diff → {SENSOR_CRITERION} reset to pending"]
        return []

    lines = format_findings(findings)
    check["tamper_findings"] = lines
    n = len(findings)
    n_fail = sum(1 for f in findings if f["grade"] == "fail")
    notes: list[str] = []
    if n_fail:
        record_sensor_status(
            check, "failed",
            f"{_SENSOR_DETAIL_PREFIX} {n_fail} gate/CI-config change(s) in the diff — revert "
            f"them; a change that is genuinely part of the task is carried by `accept --force`, "
            f"which records the bypass", WRITER)
        notes.append(f"{_SENSOR_DETAIL_PREFIX} {n} tampering pattern(s) detected "
                     f"({n_fail} fail-grade) → {SENSOR_CRITERION} failed:")
    else:
        if check["status"] in ("pending", "passed", "warning"):
            # The detail goes with the status: this sensor just overwrote the status, so
            # leaving somebody else's words under it explains the wrong verdict.
            record_sensor_status(
                check, "warning",
                f"{_SENSOR_DETAIL_PREFIX} {n} test-weakening pattern(s) in the diff — "
                f"review them (a warning never blocks accept)", WRITER)
        notes.append(f"{_SENSOR_DETAIL_PREFIX} {n} warning-grade tampering pattern(s) detected → "
                     f"{SENSOR_CRITERION} recorded as warning:")
    notes.extend(f"  {ln}" for ln in lines)
    return notes
