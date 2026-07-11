"""workbench secrets: deterministic secret scanner backing `no_secret_leak`
(issue #273, scoped).

Issue #273 ultimately wants secrets masked out of the AI context *before*
generation; this module implements the scanner core that everything hangs off:
a pattern set for well-known credential formats plus a generic high-entropy
detector, producing findings that never contain the secret itself (the middle
is always masked).

Patterns covered (kind → shape):
  aws_access_key     AKIA…/ASIA… 20-char AWS access key ids
  private_key_pem    -----BEGIN … PRIVATE KEY----- block headers
  github_token       ghp_/gho_/ghu_/ghs_/ghr_ classic and github_pat_ fine-grained
  slack_token        xoxb-/xoxa-/xoxp-/xoxr-/xoxs- tokens
  anthropic_api_key  sk-ant-…
  openai_api_key     sk-… (non-Anthropic)
  google_api_key     AIza…
  jwt                three dot-separated base64url segments starting with eyJ
  high_entropy       generic base64/hex strings ≥ 32 chars whose Shannon
                     entropy exceeds a per-charset threshold

The entropy detector carries a path allowlist for the obvious false-positive
factories — lockfile hashes and vendored trees: *.lock, *.sum,
package-lock.json / npm-shrinkwrap.json / pnpm-lock.yaml, and anything under
node_modules/ or .git/. The named patterns still run there (a real token is a
leak wherever it sits); only the entropy heuristic is silenced.

CLI: `workbench.py scan-secrets [paths...]` scans files/trees;
`scan-secrets --diff <task-id>` scans only the task worktree's diff vs its
base commit (added lines + untracked files).

Gate wiring (mirrors schema_diff.apply_schema_sensor, but fail-grade):
`cmd_gate` calls apply_secret_sensor() on every evaluation. When the gate
contains `no_secret_leak` and the diff-scoped scan finds anything, the check
is set to **failed** — a secret in the diff must block accept. This is NOT
warning-grade like the schema sensor. Escape hatch: after reviewing a false
positive, the user can run `gate <id> --set no_secret_leak=passed` — an
explicit pass in the same invocation is respected (recorded as
secret_override on the check) and sticks across later evaluations, exactly
how manual overrides already work for every criterion.
"""

import argparse
import math
import pathlib
import re
import sys

from .state import die, git, load_task, repo_root

SENSOR_CRITERION = "no_secret_leak"

# ── pattern set ───────────────────────────────────────────────────────────────
PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("private_key_pem", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255})\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("anthropic_api_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    # `-` excluded from the body so sk-ant-… stops at "ant" (3 < 20) and never double-matches
    ("openai_api_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
)

# ── generic high-entropy detector ─────────────────────────────────────────────
ENTROPY_TOKEN_RE = re.compile(r"[A-Za-z0-9+/=_-]{32,}")
HEX_RE = re.compile(r"[0-9a-fA-F]{32,}")
BASE64_ENTROPY_THRESHOLD = 4.5  # bits/char over the base64 charset
HEX_ENTROPY_THRESHOLD = 3.0     # bits/char over the hex charset
MIN_TOKEN_LEN = 32

# Entropy-detector allowlist: lockfile hashes / vendored trees are high-entropy
# by construction and never secrets. Named patterns are NOT silenced by these.
ALLOW_SUFFIXES = (".lock", ".sum")
ALLOW_BASENAMES = ("package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml")
ALLOW_DIR_PARTS = ("node_modules", ".git")

# Tree-walk skips (never worth scanning at all) and binary/size guards.
WALK_SKIP_DIRS = ("node_modules", ".git", ".rig", "__pycache__")
MAX_FILE_BYTES = 1_000_000


def shannon_entropy(s: str) -> float:
    """Shannon entropy in bits per character of the observed string."""
    if not s:
        return 0.0
    n = len(s)
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def mask(secret: str) -> str:
    """Mask the middle of a secret: only the first 4 / last 2 chars survive.

    Findings NEVER carry the raw secret — this is the only rendering.
    """
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}…{'*' * min(len(secret) - 6, 8)}…{secret[-2:]}"


def entropy_allowlisted(rel: str) -> bool:
    """True when `rel` is a known high-entropy-but-harmless location
    (lockfiles / checksum files / vendored or VCS trees)."""
    p = pathlib.PurePosixPath(rel.replace("\\", "/"))
    if p.suffix in ALLOW_SUFFIXES or p.name in ALLOW_BASENAMES:
        return True
    return any(part in ALLOW_DIR_PARTS for part in p.parts)


def _finding(rel: str, lineno: int, kind: str, secret: str) -> dict:
    return {"path": rel, "line": lineno, "kind": kind, "masked_excerpt": mask(secret)}


def scan_line(line: str, rel: str, lineno: int, skip_entropy: bool | None = None) -> list[dict]:
    """Scan one line of text. Findings carry masked excerpts only."""
    if skip_entropy is None:
        skip_entropy = entropy_allowlisted(rel)
    findings: list[dict] = []
    spans: list[tuple[int, int]] = []
    for kind, rx in PATTERNS:
        for m in rx.finditer(line):
            findings.append(_finding(rel, lineno, kind, m.group(0)))
            spans.append(m.span())
    if skip_entropy:
        return findings
    for m in ENTROPY_TOKEN_RE.finditer(line):
        if any(m.start() < e and s < m.end() for s, e in spans):
            continue  # already reported by a named pattern
        tok = m.group(0)
        if HEX_RE.fullmatch(tok):
            threshold = HEX_ENTROPY_THRESHOLD
        else:
            threshold = BASE64_ENTROPY_THRESHOLD
        if shannon_entropy(tok) > threshold:
            findings.append(_finding(rel, lineno, "high_entropy", tok))
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
    skip_entropy = entropy_allowlisted(rel)
    findings: list[dict] = []
    for i, line in enumerate(text.splitlines(), start=1):
        findings.extend(scan_line(line, rel, i, skip_entropy=skip_entropy))
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


# ── diff-scoped scan (used by --diff and the gate sensors) ────────────────────
# The helpers here are deliberately generic (they carry no secret semantics) so
# the other diff-scoped gate sensors (hardening.py, injection.py) reuse them
# instead of re-parsing unified diffs.
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)")


def iter_added_lines(diff_text: str):
    """Yield (rel, lineno, text) for every ADDED line of a unified diff.

    Line numbers refer to the NEW file; lines of deleted files (new side is
    /dev/null) are not yielded."""
    rel: str | None = None
    lineno = 0
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            rel = None if target == "/dev/null" else target[2:] if target.startswith("b/") else target
            continue
        m = _HUNK_RE.match(line)
        if m:
            lineno = int(m.group(1))
            continue
        if line.startswith("+") and not line.startswith("+++"):
            if rel is not None:
                yield rel, lineno, line[1:]
            lineno += 1
        elif line.startswith(" "):
            lineno += 1  # context line (absent with -U0, handled for safety)


def worktree_diff_text(wt: pathlib.Path, base_commit: str) -> str:
    """Unified (-U0) diff of the worktree vs its base commit: committed +
    uncommitted changes. Empty string when git fails (e.g. worktree gone)."""
    proc = git(["diff", "--unified=0", "--no-color", base_commit], cwd=wt, check=False)
    return proc.stdout if proc.returncode == 0 else ""


def untracked_files(wt: pathlib.Path) -> list[tuple[pathlib.Path, str]]:
    """(absolute path, repo-relative path) of untracked files, minus
    vendored/VCS/state trees (WALK_SKIP_DIRS). Invisible to `git diff`."""
    proc = git(["ls-files", "--others", "--exclude-standard"], cwd=wt, check=False)
    out: list[tuple[pathlib.Path, str]] = []
    for rel in proc.stdout.splitlines() if proc.returncode == 0 else []:
        f = wt / rel
        if f.is_file() and not any(part in WALK_SKIP_DIRS for part in pathlib.PurePosixPath(rel).parts):
            out.append((f, rel))
    return out


def scan_diff_text(diff_text: str) -> list[dict]:
    """Scan only the ADDED lines of a unified diff; line numbers refer to the new file."""
    findings: list[dict] = []
    for rel, lineno, text in iter_added_lines(diff_text):
        findings.extend(scan_line(text, rel, lineno))
    return findings


def scan_worktree_diff(wt: pathlib.Path, base_commit: str) -> list[dict]:
    """Everything the task introduced on top of base: committed + uncommitted
    changes (`git diff <base>`) plus untracked files (invisible to git diff)."""
    findings = scan_diff_text(worktree_diff_text(wt, base_commit))
    for f, rel in untracked_files(wt):
        findings.extend(scan_file(f, rel))
    return findings


# ── the sensor (called from cmd_gate) ─────────────────────────────────────────
_SENSOR_DETAIL_PREFIX = "(secret sensor)"


def apply_secret_sensor(root: pathlib.Path, run_d: pathlib.Path, task: dict, acc: dict,
                        explicit_set: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """Machine-back `no_secret_leak` with a diff-scoped secret scan.

    Mutates `acc` in place (caller persists it) and returns printable notes.
    No `no_secret_leak` in the gate, or no worktree/base → no-op.

    Findings → the check is set to **failed** (fail-grade: a secret in the
    diff must block accept), with the masked findings recorded on the check
    under "secret_findings". Escape hatch: an explicit
    `--set no_secret_leak=passed` in the current invocation is respected and
    recorded as secret_override=True, which keeps later evaluations from
    re-failing the check while the findings stay visible.
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

    findings = scan_worktree_diff(wt, base)
    if not findings:
        # Secret gone from the diff: clear our state; un-fail only what WE failed.
        if check.pop("secret_findings", None) is not None:
            check.pop("secret_override", None)
            if check["status"] == "failed" and str(check.get("detail", "")).startswith(_SENSOR_DETAIL_PREFIX):
                check["status"] = "pending"
                check["detail"] = ""
                return [f"{_SENSOR_DETAIL_PREFIX} previously detected secrets are no longer "
                        f"in the diff → {SENSOR_CRITERION} reset to pending"]
        return []

    lines = [f"{f['path']}:{f['line']} [{f['kind']}] {f['masked_excerpt']}" for f in findings]
    check["secret_findings"] = lines
    n = len(findings)
    notes: list[str] = []
    if SENSOR_CRITERION in explicit_set and check["status"] == "passed":
        check["secret_override"] = True
        if str(check.get("detail", "")).startswith(_SENSOR_DETAIL_PREFIX):
            # replace our stale failure instruction (keep any user-supplied detail)
            check["detail"] = (f"{_SENSOR_DETAIL_PREFIX} {n} finding(s) manually overridden "
                               "after review (secret_override)")
        notes.append(f"{_SENSOR_DETAIL_PREFIX} {n} potential secret(s) still in the diff, but "
                     f"{SENSOR_CRITERION} was explicitly set to passed — manual override recorded:")
    elif check.get("secret_override") and check["status"] == "passed":
        notes.append(f"{_SENSOR_DETAIL_PREFIX} {n} potential secret(s) in the diff — "
                     "manual override previously recorded, keeping passed:")
    else:
        check["status"] = "failed"
        check["detail"] = (f"{_SENSOR_DETAIL_PREFIX} {n} potential secret(s) detected in the diff — "
                           f"remove them, or after review override with --set {SENSOR_CRITERION}=passed")
        notes.append(f"{_SENSOR_DETAIL_PREFIX} {n} potential secret(s) detected in the diff → "
                     f"{SENSOR_CRITERION} failed:")
    notes.extend(f"  {ln}" for ln in lines)
    return notes


# ── CLI ───────────────────────────────────────────────────────────────────────
def cmd_scan_secrets(args: argparse.Namespace) -> None:
    if args.diff and args.paths:
        die("give either paths or --diff <task-id>, not both")
    if args.diff:
        root = repo_root()
        _, task = load_task(root, args.diff)
        wt_path = task.get("worktree_path")
        base = task.get("base_commit")
        if not wt_path or not pathlib.Path(wt_path).is_dir():
            die(f"task '{args.diff}' has no worktree (created with --no-worktree, or already discarded)")
        if not base:
            die(f"task '{args.diff}' has no base_commit recorded")
        findings = scan_worktree_diff(pathlib.Path(wt_path), base)
        scope = f"diff of task {args.diff} (worktree vs {base[:12]})"
    else:
        paths = [pathlib.Path(p) for p in (args.paths or ["."])]
        findings = scan_paths(paths)
        scope = ", ".join(str(p) for p in paths)

    print(f"## scan-secrets: {scope}")
    if not findings:
        print("No potential secrets found.")
        return
    print(f"{len(findings)} potential secret(s) found (excerpts are masked):")
    for f in findings:
        print(f"  {f['path']}:{f['line']} [{f['kind']}] {f['masked_excerpt']}")
    sys.exit(1)
