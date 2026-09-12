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
node_modules/ or .git/. It also carries two content rules, independent of path,
each keyed on the value's OWN key rather than on a word near it: a 64/128-hex token
whose key is a digest key (sha256 / sha512 / *_sha256 / digest / checksum / …), and a
40-hex token whose key is either a digest key or a git-id key (commit / blob / tree /
object_id / oid) — 40 hex being a sha1 and a git object id alike. An unquoted
`key=value` is split back into the two before those rules run, since `=` is in the
token charset and would otherwise merge `api_key=<40 hex>` into one mixed-charset blob
that is neither hex nor entropic enough to report; the merged token keeps its own
score, so the split only ever adds a reason to report. The named patterns still run in
every case (a real token is a leak wherever it sits); only the entropy heuristic is
silenced.

CLI: `workbench.py scan-secrets [paths...]` scans files/trees;
`scan-secrets --diff <task-id>` scans only the task worktree's diff vs its
base commit (added lines + untracked files).

Gate wiring (mirrors schema_diff.apply_schema_sensor, but fail-grade):
`cmd_gate` calls apply_secret_sensor() on every evaluation. When the gate
contains `no_secret_leak` and the diff-scoped scan finds anything, the check
is set to **failed** — a secret in the diff must block accept. This is NOT
warning-grade like the schema sensor. The scan is the verdict: a
`gate <id> --set no_secret_leak=passed` that contradicts it is refused by
`cmd_gate` (lifecycle.sensor_contradictions, whose docstring carries the
reasoning), and a reviewed false positive is carried through `accept --force`,
where the bypass is audited, waived, and signed into the provenance record.
"""

import argparse
import contextlib
import math
import pathlib
import re
import sys

from .state import die, effective_base, git, load_task, record_sensor_status, repo_root

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

# `=` is in the token charset (it is base64's padding), so an unquoted `.env` line
# arrives as ONE token: `api_key=<40 hex>` is 48 characters of mixed charset, which
# fails HEX_RE.fullmatch and is then measured against the base64 threshold it cannot
# reach — the key's own letters are what drag the entropy down. A 40-hex credential
# written the way credentials are actually written was the one shape the detector
# could not see. This splits such a token back into the key and the value it labels.
#
# The `:` form needs no branch here: `:` is NOT in the token charset, so `sha256:<hex>`
# already arrives as a bare value with its key left on the line, which is exactly what
# the label rules read. Only `=` merges the two.
_KEY_VALUE_SEPARATOR = "="
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Entropy-detector allowlist: lockfile hashes / vendored trees are high-entropy
# by construction and never secrets. Named patterns are NOT silenced by these.
#
# `corpora` covers drill's planted-defect fixtures: a case that measures whether a
# reviewer spots a leaked credential has to contain something that looks like one.
# The value is fabricated and the tree is synthetic by construction. Only the
# entropy heuristic is silenced — a real vendor-formatted key (sk-ant-…, AKIA…)
# planted there is still reported, so this cannot hide an actual leak.
ALLOW_SUFFIXES = (".lock", ".sum")
ALLOW_BASENAMES = ("package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml")
ALLOW_DIR_PARTS = ("node_modules", ".git", "corpora")

# Anchored at the repository root rather than matched at any depth, unlike
# ALLOW_DIR_PARTS. `evidence` is too ordinary a directory name to silence wherever
# it appears; this silences the one tree whose whole purpose is to hold hashes.
#
# A signed evaluation result is hashes almost end to end — one digest per prompt
# surface (~200 of them), a sha256 for each captured stdout and stderr, the case
# hash, four commit ids, and the attestation signature. Those are what let anyone
# recompute the binding, so the format cannot avoid them, and the entropy detector
# cannot tell them from a credential. Committing one produced 223 findings and a
# failed `no_secret_leak`, which is not a one-off: every PR that lands evidence hits
# it, including the maintainer path `validate.yml` documents (#447). A criterion
# that must be overridden by hand every time is a criterion nobody reads.
#
# Only the entropy heuristic is silenced here. A real vendor-formatted credential
# (sk-ant-…, AKIA…, a PEM header) written into an evidence file is still reported.
ALLOW_PATH_PREFIXES = (("evals", "evidence"),)

# The one entropy exemption that reads content rather than path: a content digest
# written on a line that already names it as one. `"body_sha256": "<64 hex>"` is an
# attestation table, not a credential — the value is the output of a hash function
# over something public, published precisely so anyone can recompute it, and a format
# that binds a commit to its source cannot avoid carrying it. The entropy heuristic
# cannot tell it from a key; the key beside it can.
#
# A LABEL VOUCHES ONLY FOR ITS OWN VALUE. This started as "a digest word somewhere in
# the 40 characters before the token", and a security review measured what that let
# through: `prev_api_key`, `revenue_api_token` and `revoked_key` contain `rev`;
# `committee_api_key` contains `commit`; `street_service_key` contains `tree`;
# `blobstore_key` contains `blob`. Six ordinary key names, each vouching for a 40-hex
# value beside it — and 40 hex is a live credential shape (Datadog application keys,
# CircleCI tokens, legacy GitHub PATs) that no named pattern covers. So the label is
# no longer *near* the value: it must BE the value's own key, the identifier directly
# before the separator, matched whole. A substring of a longer identifier is not a
# label, and there is no window left for an unrelated word to reach across.
#
# Which keys count is a closed list, not a word family. `digest` and `checksum` name a
# digest; `digest_auth_secret` and `password_hash` do not, and both now report — bare
# `_hash` is gone entirely, because `secrets.token_hex(32)` produces exactly 64 hex
# and that is what an AES-256 key, an HMAC key, a session key and a Sentry auth token
# all look like. A `_`-joined PREFIX is allowed only before sha256/sha1/sha512/digest/
# checksum (`body_sha256`, `source_excerpt_sha256`), never a suffix after them — a
# suffix is how `digest_auth_secret` would have got in. And the prefix itself must name
# no key: `hmac_sha256` is a key, not a digest (_KEYISH_PREFIX_WORDS, below).
#
# Pure hex only, and only at digest lengths: 64 and 128 under a digest key, 40 under
# either class (a sha1 and a git object id are the same 40 hex). A base64 or
# mixed-charset token is never exempted however it is labelled — `"body_sha256":
# "<43 chars of base64>"` is exactly what hiding an API key behind a digest label
# would look like, and base64 of digest length is indistinguishable from base64 of key
# length. Charset is the part of this test an attacker cannot cheaply satisfy.
#
# Named patterns are NOT silenced: sk-ant-…, AKIA…, ghp_… under a `checksum:` key are
# still reported, because a credential is a leak wherever it is written.
DIGEST_HEX_LENGTHS = frozenset({64, 128})  # sha256 / sha512, in hex
SHARED_HEX_LENGTH = 40                     # sha1 — and a git object id, the same shape

# A KEY-SHAPED PREFIX DOES NOT MAKE A DIGEST OF IT. `sha256` names the function, not
# the input, so `<x>_sha256` reads "the sha256 of <x>" — until `<x>` is itself a key,
# where the same name reads "that key, keyed-hashed", or simply names the key.
# `hmac_sha256` is the ordinary spelling of an HMAC-SHA256 key, and an HMAC-SHA256 key
# is 64 hex: the same shape as the digest it is not. `api_key_sha256`, `secret_digest`,
# `token_checksum` and `session_key_sha256` are the same bargain with a different word.
#
# So these words, as whole `_`-separated words anywhere in the prefix, cost the prefix
# its vouch. Whole words only: `keystore_sha256`, `authority_digest` and
# `monkey_checksum` are untouched, because `keystore`, `authority` and `monkey` are not
# `key`, `auth` and `key`. The rule stays one-directional — still a prefix, never a
# suffix — so nothing the denylist misses gets in through `digest_auth_secret`.
_KEYISH_PREFIX_WORDS = r"hmac|key|secret|token|password|passwd|auth"

# The digest keys, whole. `sha3_256` / `blake2b` / `content_hash` stand alone; the
# five common ones take a `_`-joined prefix, and only a prefix that names no key.
_DIGEST_KEY = (
    r"(?:(?!(?:[A-Za-z0-9]+_)*(?:" + _KEYISH_PREFIX_WORDS + r")_)[A-Za-z0-9_]+_)?"
    r"(?:sha256|sha1|sha512|digest|checksum)"
    r"|sha3[_-]?\d+"
    r"|blake2[bs]?"
    r"|content_hash"
)

# The git-id keys, whole and exact — no prefix rule at all. `rev` and `sha` are gone:
# they are too short to be anything but a substring of something else, and they were
# how `prev_api_key` and `revoked_key` got their exemption.
_GIT_ID_KEY = (
    r"source_commit|git_commit|commit"
    r"|source_git_blob|git_blob|blob"
    r"|tree|object_id|oid"
)

# The anchor: `<key>` then the separator that introduces the value, ending exactly
# where the token begins. Left side must be a non-identifier character, so the key
# cannot be the tail of a longer one; the separator on the right means it cannot be
# the head of one either. Three accepted forms, and no fourth:
#   `"body_sha256": "`  /  `sha256 = "`   a key and its assignment or mapping
#   `sha256:`                             the colon form digests are quoted in
#   `sha256 `                             the prose/Markdown form, whole word adjacent
_LABEL_ANCHOR = r'(?:^|[^A-Za-z0-9_])(?:%s)(?:["\']?[ \t]*[:=][ \t]*["\']?|[ \t]+)$'
DIGEST_LABEL_RE = re.compile(_LABEL_ANCHOR % _DIGEST_KEY, re.IGNORECASE)
GIT_ID_LABEL_RE = re.compile(_LABEL_ANCHOR % _GIT_ID_KEY, re.IGNORECASE)

# Every form above ends in one of these, so the character touching the token decides
# in O(1) whether the anchor can match at all. Purely a cost guard on long lines — it
# accepts exactly what the patterns accept.
_SEPARATOR_TAIL = "\"' \t:="

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
    (lockfiles / checksum files / vendored or VCS trees / signed eval evidence)."""
    p = pathlib.PurePosixPath(rel.replace("\\", "/"))
    if p.suffix in ALLOW_SUFFIXES or p.name in ALLOW_BASENAMES:
        return True
    if any(part in ALLOW_DIR_PARTS for part in p.parts):
        return True
    # Compared as path components, not as a string prefix. Every caller here hands in
    # a repository-relative path produced by git, which never contains `..` — but a
    # prefix test would also accept `evals/evidence/../elsewhere/x.json`, and proving
    # that no caller can ever produce one is more expensive, and more fragile, than
    # not depending on it. Leading `./` and `\` separators fall out for free.
    parts = tuple(part for part in p.parts if part != ".")
    if ".." in parts:
        return False
    return any(parts[:len(prefix)] == prefix for prefix in ALLOW_PATH_PREFIXES)


def split_key_value(token: str) -> tuple[str, str] | None:
    """`api_key=<40 hex>` → `("api_key", "<40 hex>")`; None when the token is one value.

    Exactly one separator, so there is never a choice of where to cut; an
    identifier-shaped left part, so `<base64>=<base64>` is not a key and a value; and a
    right part still long enough to be a token in its own right.

    The left part must also be SHORTER than a token: a left part of token length is
    itself a candidate value, and a finding names the value, not the key — so those
    stay merged and keep the whole blob in the excerpt.

    This function only proposes the cut. Whether the cut may LOWER a verdict is the
    caller's rule, and the answer there is no: scan_line scores the merged token too.
    """
    if token.count(_KEY_VALUE_SEPARATOR) != 1:
        return None
    key, _, value = token.partition(_KEY_VALUE_SEPARATOR)
    if len(key) >= MIN_TOKEN_LEN or len(value) < MIN_TOKEN_LEN:
        return None
    if not _IDENTIFIER_RE.fullmatch(key):
        return None
    return key, value


def _key_before(rx: re.Pattern, line: str, start: int) -> bool:
    """True when the identifier directly before `start` on this line matches `rx`."""
    if start == 0 or line[start - 1] not in _SEPARATOR_TAIL:
        return False
    return rx.search(line[:start]) is not None


def digest_under_label(line: str, token: str, start: int) -> bool:
    """True when `token` is a hex digest whose own key on this line names it as one.

    Entropy-heuristic exemption only (see DIGEST_LABEL_RE): pure hex at 64/128 — or
    40, shared with the git-id class — under a whole digest key.
    """
    if not HEX_RE.fullmatch(token):
        return False
    if len(token) not in DIGEST_HEX_LENGTHS and len(token) != SHARED_HEX_LENGTH:
        return False
    return _key_before(DIGEST_LABEL_RE, line, start)


def git_id_under_label(line: str, token: str, start: int) -> bool:
    """True when `token` is a 40-hex value whose own key on this line names it a git id.

    Entropy-heuristic exemption only (see GIT_ID_LABEL_RE): exactly 40 hex — the
    length git addresses content at — under a whole git-id key.
    """
    if len(token) != SHARED_HEX_LENGTH or not HEX_RE.fullmatch(token):
        return False
    return _key_before(GIT_ID_LABEL_RE, line, start)


def over_entropy_threshold(token: str) -> bool:
    """True when `token` beats the threshold for its OWN charset (hex, else base64)."""
    threshold = HEX_ENTROPY_THRESHOLD if HEX_RE.fullmatch(token) else BASE64_ENTROPY_THRESHOLD
    return shannon_entropy(token) > threshold


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
        merged, start = m.group(0), m.start()
        tok = merged
        kv = split_key_value(merged)
        if kv is not None:
            # An unquoted `key=value`: the value is judged on its own charset, and the
            # key stays where the label rules already look — directly before it, with
            # the `=` as its separator. `body_sha256=<hex>` is still a digest under its
            # own key; `api_key=<hex>` is a 40-hex credential that no longer hides
            # behind the letters of its own name.
            start += len(kv[0]) + len(_KEY_VALUE_SEPARATOR)
            tok = kv[1]
        if digest_under_label(line, tok, start):
            continue  # a digest under a digest label is not a credential
        if git_id_under_label(line, tok, start):
            continue  # …nor is a git object id under a git-id label
        # EITHER verdict reports, and the exemptions above are the only thing that
        # silences both. Entropy per character is not monotone under taking a piece:
        # a short base64 value can score BELOW its threshold while `api_key=` + that
        # same value scores above it, because the key's own letters are characters the
        # value does not repeat. Scoring only the piece would have made the split a
        # detection regression for exactly the values it was written to catch — so the
        # merged token keeps the score it had before there was a split at all.
        if over_entropy_threshold(tok) or (kv is not None and over_entropy_threshold(merged)):
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


# ── shared diff cache (#321) ──────────────────────────────────────────────────
# One gate evaluation runs 4+ diff-scoped sensors, and each used to shell out
# for the identical `git diff` / `git ls-files` — measured at 8 redundant git
# subprocesses per evaluation. The cache is OPT-IN via the shared_diff_cache()
# context manager (cmd_gate wraps the sensor batch in it): direct calls stay
# uncached, so unit tests and any long-lived caller that mutates the worktree
# between calls structurally cannot see stale results.
_diff_memo: dict | None = None


@contextlib.contextmanager
def shared_diff_cache():
    """Within this context, worktree_diff_text/untracked_files results are
    memoized per (worktree, base) so the sensor batch of one gate evaluation
    shells out once instead of once per sensor. Never nest-sensitive: the memo
    is dropped on exit."""
    global _diff_memo
    _diff_memo = {}
    try:
        yield
    finally:
        _diff_memo = None


def worktree_diff_text(wt: pathlib.Path, base_commit: str) -> str:
    """Unified (-U0) diff of the worktree vs its base commit: committed +
    uncommitted changes. Empty string when git fails (e.g. worktree gone)."""
    key = ("diff", str(wt), base_commit)
    if _diff_memo is not None and key in _diff_memo:
        return _diff_memo[key]
    proc = git(["diff", "--unified=0", "--no-color", base_commit], cwd=wt, check=False)
    out = proc.stdout if proc.returncode == 0 else ""
    if _diff_memo is not None:
        _diff_memo[key] = out
    return out


def untracked_files(wt: pathlib.Path) -> list[tuple[pathlib.Path, str]]:
    """(absolute path, repo-relative path) of untracked files, minus
    vendored/VCS/state trees (WALK_SKIP_DIRS). Invisible to `git diff`."""
    key = ("untracked", str(wt))
    if _diff_memo is not None and key in _diff_memo:
        return _diff_memo[key]
    proc = git(["ls-files", "--others", "--exclude-standard"], cwd=wt, check=False)
    out: list[tuple[pathlib.Path, str]] = []
    for rel in proc.stdout.splitlines() if proc.returncode == 0 else []:
        f = wt / rel
        if f.is_file() and not any(part in WALK_SKIP_DIRS for part in pathlib.PurePosixPath(rel).parts):
            out.append((f, rel))
    if _diff_memo is not None:
        _diff_memo[key] = out
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
#: config.WRITER_OPERATOR's counterpart: this sensor as the writer of a status.
WRITER = "secret-sensor"


def apply_secret_sensor(root: pathlib.Path, run_d: pathlib.Path, task: dict,
                       acc: dict) -> list[str]:
    """Machine-back `no_secret_leak` with a diff-scoped secret scan.

    Mutates `acc` in place (caller persists it) and returns printable notes.
    No `no_secret_leak` in the gate, or no worktree/base → no-op.

    Findings → the check is set to **failed** (fail-grade: a secret in the
    diff must block accept), with the masked findings recorded on the check
    under "secret_findings". The scan is the verdict, and it is written over
    whatever the gate's `--set` put there; `cmd_gate` refuses an invocation
    whose hand-written status this contradicts rather than recording either one.
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

    findings = scan_worktree_diff(wt, base)
    if not findings:
        # Secret gone from the diff: clear our state; un-fail only what WE failed.
        if check.pop("secret_findings", None) is not None:
            if check["status"] == "failed" and str(check.get("detail", "")).startswith(_SENSOR_DETAIL_PREFIX):
                record_sensor_status(check, "pending", "", WRITER)
                return [f"{_SENSOR_DETAIL_PREFIX} previously detected secrets are no longer "
                        f"in the diff → {SENSOR_CRITERION} reset to pending"]
        return []

    lines = [f"{f['path']}:{f['line']} [{f['kind']}] {f['masked_excerpt']}" for f in findings]
    check["secret_findings"] = lines
    n = len(findings)
    notes: list[str] = []
    record_sensor_status(
        check, "failed",
        f"{_SENSOR_DETAIL_PREFIX} {n} potential secret(s) detected in the diff — remove them; "
        f"a reviewed false positive is carried by `accept --force`, which records the bypass",
        WRITER)
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
        # Live merge base (#312) — the printed scope must name the sha actually scanned.
        base, _drift = effective_base(root, task)
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
