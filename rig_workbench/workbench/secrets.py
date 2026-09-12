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

from rig_workbench import gitroot

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
# Matched at ANY DEPTH, which is a rule only two directory names earn. `node_modules`
# and `.git` are reserved by the tools that create them: a directory of either name is
# that tool's tree wherever it sits, and its contents are not the repository's prose.
# A name a project might choose for itself does not belong here — see
# ALLOW_PATH_PREFIXES.
ALLOW_SUFFIXES = (".lock", ".sum")
ALLOW_BASENAMES = ("package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml")
ALLOW_DIR_PARTS = ("node_modules", ".git")

# Anchored at the repository root rather than matched at any depth, unlike
# ALLOW_DIR_PARTS. `evidence` and `corpora` are too ordinary a directory name to
# silence wherever they appear; these silence the exact trees whose whole purpose is
# to hold hashes and fabricated credentials.
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
#
# `skills/engine/corpora` is drill's planted-defect fixtures: a case that measures
# whether a reviewer spots a leaked credential has to contain something that looks like
# one. The value is fabricated and the tree is synthetic by construction. This used to
# be a bare `corpora` in ALLOW_DIR_PARTS, matched at any depth — so ANY directory
# anyone named `corpora`, anywhere in any repository rig scans, stopped the entropy
# heuristic reporting under it, and a real leak parked in `some/project/corpora/`
# went unseen. The corpora rig ships are at one address (`corpus_root()` and
# `validation/drill.py` both build `skills/engine/corpora/fixture`), so that is the
# address the exemption is written at.
#
# Only the entropy heuristic is silenced under either prefix. A real vendor-formatted
# credential (sk-ant-…, AKIA…, a PEM header) written into one is still reported.
ALLOW_PATH_PREFIXES = (("evals", "evidence"), ("skills", "engine", "corpora"))

#: Files that together say "this checkout is rig itself".
#:
#: A NAME IS NOT AN ADDRESS — the same critique this module makes of a bare `corpora`
#: applies to `evals/evidence` and `skills/engine/corpora` the moment the scanner is
#: pointed at somebody else's repository. Both entries above are facts about THIS
#: repository's layout, and nothing stops another project from having a top-level
#: `evals/evidence/` of its own; there, the entry is not a considered exemption, it is a
#: coincidence of naming that silences the heuristic over a whole tree.
#:
#: So the prefixes apply only inside a rig checkout, identified by two files that have to
#: be there together: the engine's brick inventory and this scanner's own source. One
#: marker alone is a file a project could plausibly have; both, at both addresses, is rig.
#: A nested foreign checkout inside rig (`some/project/` with its own `.git`) is answered
#: by this too — `invocation_worktree` answers with the INNERMOST repository, so scanning
#: it resolves the toplevel to `some/project`, and `some/project/evals/evidence/leak.txt`
#: would arrive as `evals/evidence/leak.txt` and be silenced by a prefix that was never
#: about that repository. It is not a rig checkout, so no prefix applies and the leak
#: reports.
#:
#: Outside a rig checkout the answer is always "no exemption", never "some other
#: exemption": over-reporting is the safe direction for a scanner whose findings block an
#: accept. `node_modules` and `.git` are unaffected — those names are reserved by the
#: tools that make them, in any repository.
#:
#: THE MARKER IS COST, NOT PROOF: two empty files with these names make
#: `is_rig_checkout` true, and anyone who can create them in a scanned tree already has
#: the commit rights that buy the path shape anyway. It raises the price of the
#: coincidence — a project does not grow `skills/engine/BRICKS.md` and
#: `rig_workbench/workbench/secrets.py` by accident — and it is not an authenticity check.
RIG_CHECKOUT_MARKERS = ("skills/engine/BRICKS.md", "rig_workbench/workbench/secrets.py")


def is_rig_checkout(root: pathlib.Path) -> bool:
    """True when `root` is a checkout of rig itself — see RIG_CHECKOUT_MARKERS."""
    return all((root / marker).is_file() for marker in RIG_CHECKOUT_MARKERS)


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


def entropy_allowlisted(rel: str, *, rig_checkout: bool = False) -> bool:
    """True when `rel` is a known high-entropy-but-harmless location
    (lockfiles / checksum files / vendored or VCS trees / signed eval evidence).

    `rig_checkout` says whether the repository `rel` is relative to is rig's own. When it
    is false, ALLOW_PATH_PREFIXES does not apply at all — those two prefixes are facts
    about this repository, not about repositories in general (RIG_CHECKOUT_MARKERS).

    IT DEFAULTS TO FALSE, and the default is the whole safety property. Knowing which
    repository a path belongs to takes a filesystem question, so a caller that holds only
    a string cannot answer it — and the answer it gets by staying silent is the narrow
    one: more findings, never fewer. A caller that means rig's prefixes has to say so.
    `scan_paths` (through `scan_root`) and `scan_worktree_diff` are the two that measure
    it, and they pass what they measured; nobody else can widen an exemption by omission.

    A path that walks upward is refused BEFORE any rule is consulted, not after the last
    one. Every caller hands in a repository-relative path (git on the diff side,
    `scan_root` on the tree side) and none can produce a `..` today, but a rule whose
    correctness depends on being asked in the right order is one edit away from being
    wrong: `node_modules/../secrets.env` should not be exempt because its first component
    is in ALLOW_DIR_PARTS. Leading `./` and Windows separators fall out for free.
    """
    p = pathlib.PurePosixPath(rel.replace("\\", "/"))
    parts = tuple(part for part in p.parts if part != ".")
    if ".." in parts:
        return False
    if p.suffix in ALLOW_SUFFIXES or p.name in ALLOW_BASENAMES:
        return True
    if any(part in ALLOW_DIR_PARTS for part in parts):
        return True
    if not rig_checkout:
        return False
    # Compared as path components, not as a string prefix, so that `evals/evidencex/`
    # is not `evals/evidence`.
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


def scan_line(line: str, rel: str, lineno: int, skip_entropy: bool | None = None, *,
              rig_checkout: bool = False) -> list[dict]:
    """Scan one line of text. Findings carry masked excerpts only."""
    if skip_entropy is None:
        skip_entropy = entropy_allowlisted(rel, rig_checkout=rig_checkout)
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


def scan_file(path: pathlib.Path, rel: str | None = None, *,
              rig_checkout: bool = False) -> list[dict]:
    """Scan a file. Binary (NUL in the first 8 KiB) and oversized files are skipped.

    `rig_checkout` goes straight through to entropy_allowlisted, and false — the default
    — means the path prefixes describe another repository's layout and none apply here.
    """
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
    skip_entropy = entropy_allowlisted(rel, rig_checkout=rig_checkout)
    findings: list[dict] = []
    for i, line in enumerate(text.splitlines(), start=1):
        findings.extend(scan_line(line, rel, i, skip_entropy=skip_entropy))
    return findings


def scan_root(p: pathlib.Path) -> tuple[str | None, bool]:
    """Where the scanned root `p` sits, and whether its repository is rig's own.

    Returns `(prefix, rig_checkout)`. `prefix` is `p`'s location inside its repository as
    a posix path (`""` or `"."` at the root), and **None when `p` is not inside a
    repository at all** — then there is nothing to be relative to and names stay as the
    caller addressed them.

    THE ALLOWLIST READS A PATH, so the path it reads cannot depend on how the scan was
    addressed. `rel` used to be `str(path)`, which is whatever the caller typed: the same
    tree scanned as `.` produced `evals/evidence/run.json` and scanned as `/home/me/rig`
    produced `/home/me/rig/evals/evidence/run.json`. Only the first has
    `("evals", "evidence")` as its leading components — the second begins with `/` — so
    `ALLOW_PATH_PREFIXES` silently stopped matching and the whole signed-evidence tree
    reported: 264 findings became 501, the 237 extra all under `evals/evidence/`. An
    allowlist that holds only when the operator types a relative path is not an allowlist.

    So every scanned file is named repository-relative before any allowlist check, which
    is also what the diff-scoped callers already hand in (git speaks repo-relative), and
    the two scan entry points finally agree on one vocabulary.

    The repository is asked of `rig_workbench.gitroot` — `invocation_worktree`, the
    caller's own working tree, not `main_worktree`: a task worktree's `evals/evidence/` is
    at *its* root, and naming it relative to the main checkout would put `../` in front of
    everything. `invocation_worktree` answers with the INNERMOST repository, and that is
    what makes the second half of this answer necessary: a nested checkout re-roots the
    prefix, so the prefixes are spent only where they are true (RIG_CHECKOUT_MARKERS).

    WITH NO REPOSITORY THE MARKERS STILL ANSWER. git says where the root is; it does
    not say whose the tree is, and those are separate questions. A `git archive` extract
    of this repository, a release tarball, a vendored copy — same bytes, same layout, no
    `.git` — used to get a hardcoded `False` here and lose the exemption entirely:
    scanning one produced 574 findings against a checkout's 264, the extra 237 under
    `evals/evidence/` and 73 under `skills/engine/corpora/`. That is the false-positive
    pile the prefixes exist to remove, arriving through a second door. So the no-repo
    branches ask `is_rig_checkout` about the scanned root, which is the one thing still
    answerable without git.

    The root asked about is the SCANNED ROOT, not the nearest marker-bearing ancestor.
    Outside a repository there is no toplevel to walk up to, and the alternative — an
    unbounded climb toward `/` looking for markers — would let a rig checkout somewhere
    above an unrelated tree lend it the exemption, which is the coincidence
    RIG_CHECKOUT_MARKERS exists to price out. The cost is an asymmetry, recorded rather
    than hidden: inside a repository, scanning `<root>/evals` still anchors at the
    toplevel and is exempt; outside one it is not, because `evals` is then the root and
    carries no markers. That direction over-reports, which is the direction this scanner
    errs in.

    Asked once per scanned root rather than once per file: a whole-tree scan walks
    thousands of files and this is a subprocess.
    """
    base = p if p.is_dir() else p.parent
    top = gitroot.invocation_worktree(base)
    if top is None:
        return None, is_rig_checkout(base)
    top = top.resolve()
    try:
        prefix = base.resolve().relative_to(top).as_posix()
    except ValueError:          # a toplevel the scanned root is somehow not under
        return None, is_rig_checkout(base)
    return prefix, is_rig_checkout(top)


def _under(prefix: str | None, rel: str) -> str:
    """`rel` re-rooted under `prefix`; `.`, `""` and None are all an empty prefix."""
    return rel if prefix in (None, "", ".") else f"{prefix}/{rel}"


def _as_addressed(p: pathlib.Path) -> str:
    """A single file outside any repository, named as the caller named it.

    Not its basename: `scan-secrets deep/nested/conf.json` reports the file the operator
    asked about, and a finding that says `conf.json` has thrown away the half of the
    path that says which one.
    """
    return p.as_posix().removeprefix("./")


def scan_paths(paths: list[pathlib.Path]) -> list[dict]:
    """Scan files and directory trees (vendored/VCS dirs skipped entirely).

    Findings name files repository-relative, so scanning a tree by its relative and by
    its absolute path yields identical findings — see scan_root.
    """
    findings: list[dict] = []
    for p in paths:
        if p.is_file():
            prefix, rig = scan_root(p)
            rel = _under(prefix, p.name) if prefix is not None else _as_addressed(p)
            findings.extend(scan_file(p, rel, rig_checkout=rig))
        elif p.is_dir():
            prefix, rig = scan_root(p)
            for f in sorted(p.rglob("*")):
                if not f.is_file():
                    continue
                rel = f.relative_to(p)
                if any(part in WALK_SKIP_DIRS for part in rel.parts):
                    continue
                findings.extend(scan_file(f, _under(prefix, rel.as_posix()),
                                          rig_checkout=rig))
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


def scan_diff_text(diff_text: str, *, rig_checkout: bool = False) -> list[dict]:
    """Scan only the ADDED lines of a unified diff; line numbers refer to the new file."""
    findings: list[dict] = []
    for rel, lineno, text in iter_added_lines(diff_text):
        findings.extend(scan_line(text, rel, lineno, rig_checkout=rig_checkout))
    return findings


def scan_worktree_diff(wt: pathlib.Path, base_commit: str) -> list[dict]:
    """Everything the task introduced on top of base: committed + uncommitted
    changes (`git diff <base>`) plus untracked files (invisible to git diff).

    The worktree is the repository git names these paths relative to, so it is also what
    decides whether ALLOW_PATH_PREFIXES applies: a task run against somebody else's
    project gets `node_modules`/`.git` and the content rules, not rig's two prefixes.
    """
    rig = is_rig_checkout(wt)
    findings = scan_diff_text(worktree_diff_text(wt, base_commit), rig_checkout=rig)
    for f, rel in untracked_files(wt):
        findings.extend(scan_file(f, rel, rig_checkout=rig))
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
