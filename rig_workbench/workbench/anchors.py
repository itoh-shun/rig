"""workbench anchors: evidence-anchor extraction and resolution.

Every rig reviewer owes each of its three 根拠 an evidence anchor, and the
`review-verdict` output contract spells the code form as a backticked
`path/to/file.ext:42` (or `:10-18`). Nothing has ever checked that those
anchors point at a line that exists. This module is the deterministic half of
that check: pull the anchors out of a reviewer body, and decide whether each
one resolves.

Extraction is intentionally narrow: an anchor is a **whole** backticked span
that is nothing but `path:line[-line]`, whose left side looks like a path (a
`/`, or an extension). Three reasons. (a) There is no corpus of persisted
reviewer bodies yet, so widening the grammar on a guess trades a measurable
silence for an unmeasurable false accusation; a missed anchor degrades into
"no anchors found", which is the signal that says widen it. (b) The same
contract allows a short *quote* as the anchor for prose sources, and quotes are
frequently backticked code — a pasted `raise Err(f"{p}:{n}")` or a
`main.go:42:5: msg` compiler line *contains* something anchor-shaped.
Whole-span matching is what keeps those out; substring matching would not.
(c) A bare word plus a number (`exit:0`, `docs:12`) is prose that happens to
fit the shape, and treating it as an anchor invents a finding — the path
requirement is what stops the sensor from accusing a reviewer of an anchor
they never wrote.

Resolution runs worktree first, then the base commit. The base pass is
required, not a nicety: a reviewer legitimately cites a line in a file the
diff deleted or renamed, and failing those would be a false positive on a
gate. Base content is read with `git show <base>:<rel>` and a non-zero
returncode means "absent at base" (the `schema_diff.py` precedent — the
`check=True` form dies on a missing file).

Three outcomes, never two: RESOLVED, UNRESOLVED (a real broken anchor) and
SKIPPED (binary / generated / symlink / unreadable — not judged, but reported
with a reason). Skipping silently would repeat the failure this sensor exists
to prevent: proving compliance by not looking. The same reasoning gives a body
holding *no* anchor at all its own finding (`no_anchors`): a reviewer body with
nothing to check is not a body that checked out.

On top of that: the reporting layer (findings in the injection/destructive
shape), the `scan-anchors` subcommand and the opt-in gate criterion
`evidence_anchors_resolve`. The criterion is deliberately absent from every
GATE_PRESETS entry — a project opts in through `.rig/gates.json`
`extra_criteria` — because this is phase A of the design brief, whose job is
to produce a *number* for how often rig's reviewers break their anchors, not
to start failing every review before that number exists.
"""

import argparse
import dataclasses
import hashlib
import os
import pathlib
import re
import sys

from .injection import bounded_excerpt
from .secrets import MAX_FILE_BYTES
from .state import die, effective_base, git, load_task, repo_root

# ── extraction ────────────────────────────────────────────────────────────────
# A backticked span holding nothing but `path:line` or `path:start-end`. The
# path may not contain whitespace, a backtick or a colon, which also keeps
# `https://host:8080/x` and compiler triples (`main.go:42:5`) out.
ANCHOR_RE = re.compile(r"`([^\s`:]+):(\d+)(?:-(\d+))?`")

# …and the path has to look like a path. A bare word plus a number is prose,
# not an anchor: `exit:0` is an impossible line number, and `docs:12` — the
# name of a real *directory* — resolves far enough to become a fail-grade
# `not_a_file` finding against a reviewer who never wrote an anchor at all.
# "Looks like a path" is a `/` anywhere, or an extension on the last component,
# so a legitimate `README.md:12` and a dotfile `.gitignore:3` still count.
ANCHOR_PATH_EXT_RE = re.compile(r"\.[A-Za-z0-9_+-]{1,12}$")

# Machine outputs: an anchor into one is not a reviewer error, but neither is
# it evidence a human can check. Deliberately a short, unambiguous list — this
# is not `secrets.WALK_SKIP_DIRS`, which excludes `.rig` and reviewers do cite
# `.rig/gates.json:3`.
GENERATED_NAMES = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml",
                   "poetry.lock", "Cargo.lock", "uv.lock", "go.sum")
GENERATED_DIRS = ("dist", "build")
GENERATED_RE = re.compile(r"\.min\.[A-Za-z0-9]+$")

RESOLVED = "resolved"
UNRESOLVED = "unresolved"
SKIPPED = "skipped"


@dataclasses.dataclass(frozen=True)
class Anchor:
    """One evidence anchor as it appeared in a reviewer body.

    `body_line` / `body_offset` locate it in the *body* (1-based line, 0-based
    character offset of the backtick); `start` / `end` are the lines it claims
    in `path`. `end == start` for a single-line anchor.
    """
    raw: str
    path: str
    start: int
    end: int
    body_line: int
    body_offset: int


@dataclasses.dataclass(frozen=True)
class Resolution:
    """Verdict on one anchor.

    `status` is RESOLVED / UNRESOLVED / SKIPPED. `reason` is a stable machine
    code, empty only when resolved; `detail` is a short human sentence for a
    finding. `source` is the pass that produced the verdict ("worktree" or
    "base") — on a RESOLVED verdict that is where the anchor resolved, on the
    others it is only where the lookup gave up, and it is None for verdicts
    reached before any lookup (a reversed range, say). **Non-None `source` is
    not evidence of resolution; check `status`.**
    """
    anchor: Anchor
    status: str
    source: str | None = None
    reason: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == RESOLVED


def _is_pathish(path: str) -> bool:
    """Does the left side of an anchor look like a path at all? (ANCHOR_PATH_EXT_RE)"""
    return "/" in path or bool(ANCHOR_PATH_EXT_RE.search(path))


def extract_anchors(text: str) -> list[Anchor]:
    """Every `path:line[-line]` anchor in a reviewer body, in order of appearance.

    Duplicates are kept: two mentions of the same anchor are two occurrences,
    and the caller decides whether to report both. A backticked `word:12` whose
    left side is not path-shaped is not an anchor and is not returned.
    """
    if not text:
        return []
    # Offset of the start of each line, so a match offset maps to a body line
    # without re-scanning the text per match.
    line_starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            line_starts.append(i + 1)

    anchors: list[Anchor] = []
    for m in ANCHOR_RE.finditer(text):
        if not _is_pathish(m.group(1)):
            continue
        start = int(m.group(2))
        end = int(m.group(3)) if m.group(3) is not None else start
        offset = m.start()
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        anchors.append(Anchor(raw=m.group(0).strip("`"), path=m.group(1),
                              start=start, end=end,
                              body_line=lo + 1, body_offset=offset))
    return anchors


# ── resolution ────────────────────────────────────────────────────────────────
def _is_generated(rel: str) -> bool:
    parts = pathlib.PurePosixPath(rel).parts
    return (parts[-1] in GENERATED_NAMES
            or any(p in GENERATED_DIRS for p in parts[:-1])
            or bool(GENERATED_RE.search(parts[-1])))


def _escapes_worktree(rel: str) -> bool:
    """Anchors are resolved from the worktree root; anything that leaves it is
    not one. `Path(wt) / "/etc/passwd"` silently discards `wt`, so this has to
    be refused before the join, not after."""
    pp = pathlib.PurePosixPath(rel)
    return pp.is_absolute() or ".." in pp.parts or rel.startswith("~")


def _resolves_outside(wt: pathlib.Path, path: pathlib.Path) -> bool:
    """True when `path` leaves the worktree once every symlink is followed.

    `_escapes_worktree` only reads the anchor text, and a symlink at *any*
    component — an intermediate directory, not just the last one — leaves the
    tree without the anchor ever containing `..`. Both sides are realpath'd so
    a worktree that itself lives under a symlinked prefix (`/tmp` on macOS)
    still contains its own files.

    Decided from the resolved path alone, with no stat/read of the target, so
    the verdict is identical whether or not the outside target exists. That is
    the point: a verdict that varied would be an existence-and-size oracle for
    files outside the worktree, which is exactly what a resolved anchor's
    "N line(s)" detail would leak.
    """
    try:
        real = pathlib.Path(os.path.realpath(path))
        root = pathlib.Path(os.path.realpath(wt))
    except (OSError, ValueError):
        return True
    return not real.is_relative_to(root)


def _count_lines(text: str) -> int:
    return len(text.splitlines())


# The private readers return (line count, verdict) where `verdict` is a
# (status, reason, detail) triple — exactly one of the two is set.
def _read_worktree(path: pathlib.Path) -> tuple[int | None, tuple[str, str, str] | None]:
    """Line count of a readable text file, or the reason it is not judged."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None, (SKIPPED, "too_large", f"file larger than {MAX_FILE_BYTES} bytes")
        raw = path.read_bytes()
    except OSError as exc:
        return None, (SKIPPED, "unreadable", f"cannot read file: {exc.strerror}")
    if b"\0" in raw[:8192]:
        return None, (SKIPPED, "binary", "binary file (NUL byte)")
    return _count_lines(raw.decode("utf-8", errors="replace")), None


def _read_base(wt: pathlib.Path, base: str,
               rel: str) -> tuple[int | None, tuple[str, str, str] | None, bool]:
    """(line count, verdict, present-at-base) for `<base>:<rel>`.

    `git show` on a tree returns 0 and prints a directory listing, which would
    line-count and pass; the object type is checked first. Binary blobs make
    the text-mode `git()` helper raise UnicodeDecodeError, which `check=False`
    does not protect against.
    """
    kind = git(["cat-file", "-t", f"{base}:{rel}"], cwd=wt, check=False)
    if kind.returncode != 0:
        return None, None, False
    obj = kind.stdout.strip()
    if obj != "blob":
        return None, (UNRESOLVED, "not_a_file",
                      f"'{rel}' is a {obj} at the base commit, not a file"), True
    try:
        proc = git(["show", f"{base}:{rel}"], cwd=wt, check=False)
    except UnicodeDecodeError:
        return None, (SKIPPED, "binary", "binary file at the base commit"), True
    if proc.returncode != 0:
        return None, None, False
    if "\0" in proc.stdout[:8192]:
        return None, (SKIPPED, "binary", "binary file at the base commit (NUL byte)"), True
    # Same bound as the worktree pass: without it a file too large to judge in
    # the worktree would quietly resolve at base instead.
    if len(proc.stdout) > MAX_FILE_BYTES:
        return None, (SKIPPED, "too_large",
                      f"file at the base commit is larger than {MAX_FILE_BYTES} bytes"), True
    return _count_lines(proc.stdout), None, True


def resolve_anchor(worktree, base_commit: str, anchor: Anchor) -> Resolution:
    """Decide whether `anchor` points at a line that exists.

    Looks in the task worktree first, then at `base_commit` (pass "" to skip
    the base pass — worktree-less runs have no base). The base pass covers
    both a file the diff deleted or renamed and a line the diff truncated
    away: the same false-positive class, so the same rescue.
    """
    wt = pathlib.Path(worktree)
    rel = anchor.path

    def out(status: str, reason: str, detail: str, source: str | None = None) -> Resolution:
        return Resolution(anchor, status, source, reason, detail)

    # Syntactic checks first — no I/O can make these anchors valid.
    if anchor.start < 1:
        return out(UNRESOLVED, "bad_line_number", f"line number {anchor.start} is not positive")
    if anchor.end < anchor.start:
        return out(UNRESOLVED, "reversed_range",
                   f"range {anchor.start}-{anchor.end} ends before it starts")
    if _escapes_worktree(rel):
        return out(UNRESOLVED, "path_escapes_worktree",
                   f"'{rel}' is not a path inside the worktree")
    if _is_generated(rel):
        return out(SKIPPED, "generated", f"'{rel}' is a generated artifact")

    path = wt / rel
    # Containment, decided before any stat of the target (see `_resolves_outside`):
    # the lexical check above cannot see a symlinked directory component.
    if _resolves_outside(wt, path):
        return out(UNRESOLVED, "path_escapes_worktree",
                   f"'{rel}' resolves outside the worktree (a symbolic link leaves it)")
    wt_lines: int | None = None
    # is_symlink() before is_file(): is_file() follows the link and says True.
    if path.is_symlink():
        return out(SKIPPED, "symlink", f"'{rel}' is a symbolic link")
    if path.is_dir():
        return out(UNRESOLVED, "not_a_file", f"'{rel}' is a directory, not a file")
    if path.is_file():
        wt_lines, verdict = _read_worktree(path)
        if verdict is not None:
            return out(verdict[0], verdict[1], verdict[2], "worktree")
        if anchor.end <= wt_lines:
            return out(RESOLVED, "", f"'{rel}' has {wt_lines} line(s)", "worktree")

    # Worktree missed: the file is absent, or the diff truncated the cited line
    # away. Both are rescued by the base commit.
    base_lines: int | None = None
    at_base = False
    if base_commit:
        base_lines, verdict, at_base = _read_base(wt, base_commit, rel)
        if verdict is not None:
            return out(verdict[0], verdict[1], verdict[2], "base")
        if base_lines is not None and anchor.end <= base_lines:
            return out(RESOLVED, "", f"'{rel}' has {base_lines} line(s) at the base commit", "base")

    if wt_lines is None and not at_base:
        where = "the worktree or the base commit" if base_commit else "the worktree"
        return out(UNRESOLVED, "missing", f"'{rel}' does not exist in {where}")
    counts = []
    if wt_lines is not None:
        counts.append(f"{wt_lines} line(s) in the worktree")
    if base_lines is not None:
        counts.append(f"{base_lines} line(s) at the base commit")
    return out(UNRESOLVED, "line_out_of_range",
               f"line {anchor.end} is past the end of '{rel}' ({', '.join(counts)})")


# ── findings ──────────────────────────────────────────────────────────────────
SENSOR_CRITERION = "evidence_anchors_resolve"
REVIEWS_DIRNAME = "reviews"

# Grade split, measured rather than guessed. Running extraction+resolution over
# rig's own design briefs, 7 of 13 anchors are bare basenames (`streaming.py:67`
# for `rig_workbench/workbench/streaming.py`) — real prose, written by people who
# meant a real line. Making "I could not find that file" fail-grade would hard-
# fail essentially every review the day a project opts in, which is exactly what
# the brief forbids for phase A. So: fail only when the referent WAS located and
# the anchor is still wrong (line past the end of the file, a directory), or when
# the anchor is internally impossible (line 0, reversed range). Everything we
# merely failed to locate is warning-grade — visible, countable, not blocking.
FAIL_REASONS = frozenset({"bad_line_number", "reversed_range",
                          "line_out_of_range", "not_a_file"})

# A reviewer body with zero anchors. Its own `kind` rather than silence, because
# silence here IS the failure mode: a sensor that finds nothing to check and
# therefore reports green has proved only that it did not look. Warning-grade,
# not fail — phase A counts how often rig's reviewers skip their anchors before
# anything starts blocking on it — and never carries a `line` into a file,
# because the finding is about the body, not about a place in the code.
NO_ANCHORS_KIND = "no_anchors"
NO_ANCHORS_EXCERPT = ("no `path:line` evidence anchor in this body — calling it clean would be "
                      "a sensor passing on something it never inspected")

# Longer than the 60 of secrets/injection/destructive: an excerpt here is the
# anchor plus the reason it did not resolve ("line 99 is past the end of
# 'src/app.py' (5 line(s) in the worktree, 5 line(s) at the base commit)"), and
# truncating the reason away leaves a finding nobody can act on.
EXCERPT_MAX = 140


def _excerpt(text: str, limit: int = EXCERPT_MAX) -> str:
    """The injection sensor's renderer at this sensor's bound.

    Reviewer bodies are LLM output, so an excerpt of one is untrusted text on
    its way to a terminal and to acceptance.json: zero-width and bidi
    characters hide what a finding says, control characters rewrite the
    terminal printing it. `injection.bounded_excerpt` already neutralizes
    exactly that at exactly this point and is already public, so it is shared
    rather than copied — a second implementation is a second thing to forget.
    """
    return bounded_excerpt(text, limit)


@dataclasses.dataclass
class Scan:
    """One pass over a set of reviewer bodies.

    `findings` are the injection/destructive-shaped dicts
    {path, line, grade, kind, excerpt} — `path`/`line` locate the anchor in the
    *body* (run-dir-relative, e.g. `reviews/security.md`), `kind` is the
    resolution reason and `excerpt` is the anchor plus that reason.

    `skipped` deliberately carries no `grade` and is NOT a finding: it drives
    neither the exit code nor the gate status. A lockfile citation is not a
    reviewer error, and forcing it into the grade vocabulary would park a
    warning in acceptance.json until the prose changes. It is still printed and
    counted every time — by `scan-anchors`, and by the gate sensor on both its
    paths, including the one where a body holds nothing *but* skips and there
    is no finding to print alongside. That case reaching the gate in silence is
    what "never silently green" is about.
    """
    findings: list[dict] = dataclasses.field(default_factory=list)
    skipped: list[dict] = dataclasses.field(default_factory=list)
    n_bodies: int = 0
    n_anchors: int = 0
    n_resolved: int = 0
    n_bodies_without_anchors: int = 0

    def summary(self) -> str:
        # `findings` holds two populations — anchors that did not resolve, and
        # bodies with no anchor at all — so the "unresolved" count subtracts the
        # second rather than calling a missing anchor an unresolved one.
        n_none = self.n_bodies_without_anchors
        s = (f"{self.n_anchors} anchor(s) in {self.n_bodies} body file(s): "
             f"{self.n_resolved} resolved, {len(self.findings) - n_none} unresolved, "
             f"{len(self.skipped)} skipped")
        return s + f", {n_none} body file(s) with no anchors" if n_none else s


def _record(label: str, res: Resolution) -> dict:
    a = res.anchor
    entry = {"path": label, "line": a.body_line, "kind": res.reason,
             "excerpt": _excerpt(f"`{a.raw}` — {res.detail}")}
    if res.status == UNRESOLVED:
        entry["grade"] = "fail" if res.reason in FAIL_REASONS else "warning"
    return entry


def _describe(findings: list[dict], anchor_noun: str = "unresolved evidence anchor(s)") -> str:
    """Plain-language count of a finding set.

    Every message about `findings` has to survive the two populations sharing
    the list: an anchor that did not resolve, and a body with no anchor at all.
    Counting the second as an anchor would report on something that is not
    there — the same category error the `no_anchors` finding exists to catch.
    """
    n_none = sum(1 for f in findings if f["kind"] == NO_ANCHORS_KIND)
    n_anchor = len(findings) - n_none
    parts = []
    if n_anchor:
        parts.append(f"{n_anchor} {anchor_noun}")
    if n_none:
        parts.append(f"{n_none} review body file(s) with no evidence anchor at all")
    return " and ".join(parts)


def scan_body(text: str, label: str, worktree, base_commit: str,
              scan: Scan | None = None, memo: dict | None = None) -> Scan:
    """Resolve every anchor in one reviewer body, accumulating into `scan`.

    A body with no anchor at all is one `no_anchors` finding, not a quiet pass.
    It lives here rather than in `scan_bodies` on purpose: the unit is a body
    that exists, so a task with an empty `reviews/` directory (or none) stays a
    no-op instead of becoming a finding against a reviewer who never ran.

    `memo` caches verdicts per (path, start, end) across bodies: extraction
    keeps duplicate anchors on purpose and leaves de-duplication to the caller,
    and each distinct miss otherwise costs two subprocesses (`git cat-file` +
    `git show`) every time it is repeated.
    """
    scan = scan if scan is not None else Scan()
    memo = memo if memo is not None else {}
    scan.n_bodies += 1
    anchors = extract_anchors(text)
    if not anchors:
        scan.n_bodies_without_anchors += 1
        scan.findings.append({"path": label, "line": 0, "grade": "warning",
                              "kind": NO_ANCHORS_KIND, "excerpt": _excerpt(NO_ANCHORS_EXCERPT)})
        return scan
    for a in anchors:
        scan.n_anchors += 1
        key = (a.path, a.start, a.end)
        hit = memo.get(key)
        res = Resolution(a, *hit) if hit is not None else resolve_anchor(worktree, base_commit, a)
        if hit is None:
            memo[key] = (res.status, res.source, res.reason, res.detail)
        if res.status == RESOLVED:
            scan.n_resolved += 1
        elif res.status == SKIPPED:
            scan.skipped.append(_record(label, res))
        else:
            scan.findings.append(_record(label, res))
    return scan


def review_bodies(run_d) -> list[tuple[pathlib.Path, str]]:
    """(absolute path, run-dir-relative label) of the reviewer bodies recorded
    for a task by `review --body`.

    Whatever `reviews/` holds is the population, full stop: `--body` refuses
    persona names that cannot be a filename, so a recorded verdict may simply
    have no body. Cross-checking review.json would turn that documented
    limitation into a finding against the reviewer.

    `is_file()` follows symlinks, so a linked body is *listed* here; `scan_bodies`
    is where it is refused, as a reported skip rather than an absence.
    """
    d = pathlib.Path(run_d) / REVIEWS_DIRNAME
    if not d.is_dir():
        return []
    return [(f, f"{REVIEWS_DIRNAME}/{f.name}") for f in sorted(d.glob("*.md")) if f.is_file()]


def bodies_fingerprint(run_d) -> str:
    """Hash of the reviewer bodies recorded for a task — names and contents.

    For change detection by a poller (`stream-checks --watch`). Bodies live under
    the main repo's `.rig/runs/<task_id>/`, never inside the task worktree, so
    nothing derived from the worktree diff can observe them changing; a watcher
    that wants to re-run this sensor has to hash them itself. Names are hashed
    alongside contents so that renaming a body — or recording a second one with
    identical prose — still counts as a change.
    """
    h = hashlib.sha256()
    for f, label in review_bodies(run_d):
        h.update(label.encode("utf-8", "replace") + b"\0")
        try:
            h.update(f.read_bytes())
        except OSError as exc:
            h.update(f"<unreadable:{exc.errno}>".encode())
        h.update(b"\0")
    return h.hexdigest()


def scan_bodies(bodies: list[tuple[pathlib.Path, str]], worktree, base_commit: str) -> Scan:
    """Scan a set of (file, label) reviewer bodies against one worktree+base."""
    scan = Scan()
    memo: dict = {}
    for f, label in bodies:
        # `review_bodies` finds these with is_file(), which follows the link:
        # a symlink dropped into reviews/ would otherwise have its target read
        # as reviewer prose. Reported as a skip rather than dropped silently.
        if f.is_symlink():
            scan.n_bodies += 1
            scan.skipped.append({"path": label, "line": 0, "kind": "symlink_body",
                                 "excerpt": _excerpt("review body is a symbolic link — not read")})
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            scan.n_bodies += 1
            scan.skipped.append({"path": label, "line": 0, "kind": "unreadable",
                                 "excerpt": _excerpt(f"cannot read body: {exc.strerror}")})
            continue
        scan_body(text, label, worktree, base_commit, scan, memo)
    return scan


def scan_task_reviews(run_d, worktree, base_commit: str) -> Scan:
    """Everything the gate sensor looks at: the task's recorded reviewer bodies,
    resolved against the task's own worktree and base commit."""
    return scan_bodies(review_bodies(run_d), worktree, base_commit)


def format_findings(findings: list[dict]) -> list[str]:
    return [f"{f['path']}:{f['line']} [{f['kind']}/{f['grade']}] {f['excerpt']}" for f in findings]


def format_skipped(skipped: list[dict]) -> list[str]:
    return [f"{f['path']}:{f['line']} [{f['kind']}] {f['excerpt']}" for f in skipped]


# ── the sensor (called from cmd_gate) ─────────────────────────────────────────
_SENSOR_DETAIL_PREFIX = "(anchor sensor)"


def apply_anchor_sensor(root: pathlib.Path, run_d: pathlib.Path, task: dict, acc: dict,
                        explicit_set: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """Machine-back `evidence_anchors_resolve` with the task's own review bodies.

    Mutates `acc` in place (caller persists it) and returns printable notes.
    No `evidence_anchors_resolve` in the gate → silent no-op; the criterion is
    opt-in via `.rig/gates.json`, so on a default gate this is always the first
    branch, and a note there would be noise on every gate rig ever runs. Every
    other no-op (no worktree, no base, no recorded bodies) returns a note saying
    so, and so does a pass that found nothing wrong: `pending` reached in
    silence cannot be told apart from `pending` reached after looking.

    A fail-grade finding (an anchor that is impossible, or wrong about a file we
    found) → the check is set to **failed**. Warning-grade only — anchors we
    could not locate, and bodies with no anchor at all — → **warning**, matching
    `apply_injection_sensor`: a warning-grade-only result annotates the criterion
    and never fails it. Warning never overrides an explicit failed. Findings are
    recorded on the check
    under "anchor_findings". Escape hatch: an explicit
    `--set evidence_anchors_resolve=passed` in the current invocation is
    respected and recorded as anchor_override=True, sticky across later
    evaluations.
    """
    check = next((c for c in acc.get("checks", []) if c["name"] == SENSOR_CRITERION), None)
    # The one silence kept on purpose: without the criterion the project has not
    # opted in, and a note here would put this sensor in front of every default
    # gate in existence — the opposite of opt-in.
    if check is None:
        return []
    wt_path = task.get("worktree_path")
    # Live merge base, not the registration-time record (#312): a stale base
    # would rescue anchors the current diff no longer justifies.
    base, _drift = effective_base(root, task)
    wt = pathlib.Path(wt_path) if wt_path else None
    # Every remaining no-op says why. A criterion that stays `pending` in total
    # silence is indistinguishable from one that was checked and had nothing to
    # say — the failure class this sensor exists to catch.
    if not wt_path or (wt is not None and not wt.is_dir()):
        return [f"{_SENSOR_DETAIL_PREFIX} this task has no worktree, and anchors are resolved from "
                f"one → {SENSOR_CRITERION} not evaluated (left {check['status']}). `review` and "
                f"`security_review` tasks route without a worktree by design, so this criterion "
                f"never fires on them; put it on a preset used by worktree-bearing task types."]
    if not base:
        return [f"{_SENSOR_DETAIL_PREFIX} this task has no base commit, so an anchor into a file "
                f"the diff deleted could not be rescued → {SENSOR_CRITERION} not evaluated "
                f"(left {check['status']})"]

    scan = scan_task_reviews(run_d, wt, base)
    skip_note = ([f"{_SENSOR_DETAIL_PREFIX} {len(scan.skipped)} anchor(s) skipped (not judged): "
                  + "; ".join(f["kind"] for f in scan.skipped)] if scan.skipped else [])
    if not scan.n_bodies:
        return [f"{_SENSOR_DETAIL_PREFIX} no reviewer bodies recorded for this task — record them "
                f"with `review <task_id> --set <persona>=<verdict> --body <persona>=@<path>` → "
                f"{SENSOR_CRITERION} had nothing to check (left {check['status']})"]
    if not scan.findings:
        # Nothing to report *about the reviewers* — but the pass still happened,
        # and saying so is what keeps "criterion still pending" from meaning two
        # different things. Anchors fixed (or the bodies rewritten): clear our
        # state; un-flag only what WE flagged.
        notes = [f"{_SENSOR_DETAIL_PREFIX} {scan.summary()}"]
        if check.pop("anchor_findings", None) is not None:
            check.pop("anchor_override", None)
            if check["status"] in ("failed", "warning") and \
                    str(check.get("detail", "")).startswith(_SENSOR_DETAIL_PREFIX):
                check["status"] = "pending"
                check["detail"] = ""
                notes.append(f"{_SENSOR_DETAIL_PREFIX} previously reported evidence-anchor "
                             f"findings are no longer present → "
                             f"{SENSOR_CRITERION} reset to pending")
        return notes + skip_note

    lines = format_findings(scan.findings)
    check["anchor_findings"] = lines
    n = len(scan.findings)
    n_fail = sum(1 for f in scan.findings if f["grade"] == "fail")
    what = _describe(scan.findings)
    notes: list[str] = []
    if SENSOR_CRITERION in explicit_set and check["status"] == "passed":
        check["anchor_override"] = True
        if str(check.get("detail", "")).startswith(_SENSOR_DETAIL_PREFIX):
            check["detail"] = (f"{_SENSOR_DETAIL_PREFIX} {n} finding(s) manually overridden "
                               "after review (anchor_override)")
        notes.append(f"{_SENSOR_DETAIL_PREFIX} {what} still present, but "
                     f"{SENSOR_CRITERION} was explicitly set to passed — manual override recorded:")
    elif check.get("anchor_override") and check["status"] == "passed":
        notes.append(f"{_SENSOR_DETAIL_PREFIX} {what} present — "
                     "manual override previously recorded, keeping passed:")
    elif n_fail:
        check["status"] = "failed"
        check["detail"] = (f"{_SENSOR_DETAIL_PREFIX} {n_fail} evidence anchor(s) point at a line "
                           f"that cannot exist — correct them, or after review override with "
                           f"--set {SENSOR_CRITERION}=passed")
        notes.append(f"{_SENSOR_DETAIL_PREFIX} {what} "
                     f"({n_fail} fail-grade) → {SENSOR_CRITERION} failed:")
    else:
        # Warning-grade only: annotate, never fail — the `apply_injection_sensor`
        # convention for its phrase findings (injection.py:281-288).
        soft = _describe(scan.findings, "evidence anchor(s) that could not be located")
        if check["status"] in ("pending", "passed", "warning"):
            check["status"] = "warning"
            if not check.get("detail") or str(check["detail"]).startswith(_SENSOR_DETAIL_PREFIX):
                check["detail"] = (f"{_SENSOR_DETAIL_PREFIX} {soft} — review them "
                                   f"(override with --set {SENSOR_CRITERION}=passed)")
        notes.append(f"{_SENSOR_DETAIL_PREFIX} {soft} → "
                     f"{SENSOR_CRITERION} recorded as warning:")
    notes.extend(f"  {ln}" for ln in lines)
    return notes + skip_note


# ── CLI ───────────────────────────────────────────────────────────────────────
def _body_paths(paths: list[pathlib.Path]) -> list[tuple[pathlib.Path, str]]:
    """(file, label) for explicitly named bodies. A directory contributes its
    `*.md` files only — the other scanners walk every file because their
    detectors apply to source; an anchor lives in reviewer prose."""
    out: list[tuple[pathlib.Path, str]] = []
    for p in paths:
        if p.is_file():
            out.append((p, str(p)))
        elif p.is_dir():
            out.extend((f, str(f)) for f in sorted(p.rglob("*.md")) if f.is_file())
        else:
            die(f"path '{p}' does not exist")
    return out


def cmd_scan_anchors(args: argparse.Namespace) -> None:
    if args.diff and args.paths:
        die("give either paths or --diff <task-id>, not both")
    if not args.diff and not args.paths:
        # Deliberately no default scope, unlike the other scanners: anchors are
        # resolved from a worktree root, and every plausible default (the repo
        # tree, or every past task's bodies against today's tree) resolves prose
        # against a tree it was never written for and reports the mismatch as a
        # reviewer error.
        die("give paths to reviewer bodies, or --diff <task-id> to scan the "
            "bodies recorded for a task (.rig/runs/<task-id>/reviews/*.md)")
    if args.diff:
        root = repo_root()
        d, task = load_task(root, args.diff)
        wt_path = task.get("worktree_path")
        # Live merge base (#312) — the printed scope must name the sha actually used.
        base, _drift = effective_base(root, task)
        if not wt_path or not pathlib.Path(wt_path).is_dir():
            die(f"task '{args.diff}' has no worktree (created with --no-worktree, or already discarded)")
        if not base:
            die(f"task '{args.diff}' has no base_commit recorded")
        bodies = review_bodies(d)
        scan = scan_bodies(bodies, pathlib.Path(wt_path), base)
        scope = (f"review bodies of task {args.diff} ({len(bodies)} file(s), "
                 f"worktree vs {base[:12]})")
    else:
        root = repo_root()
        bodies = _body_paths([pathlib.Path(p) for p in args.paths])
        # No task, so no base commit: worktree-only resolution (resolve_anchor's
        # documented "" form). Naming the root keeps the scope honest — the same
        # body resolves differently from a different tree.
        scan = scan_bodies(bodies, root, "")
        scope = f"{', '.join(str(p) for p in args.paths)} (vs {root}, no base commit)"

    print(f"## scan-anchors: {scope}")
    if not bodies:
        print("No review bodies recorded.")
        return
    print(scan.summary())
    if scan.skipped:
        print(f"{len(scan.skipped)} anchor(s) skipped (not judged, not a finding):")
        for line in format_skipped(scan.skipped):
            print(f"  {line}")
    if not scan.findings:
        print("No unresolved evidence anchors found.")
        return
    n_fail = sum(1 for f in scan.findings if f["grade"] == "fail")
    print(f"{_describe(scan.findings)} "
          f"({n_fail} fail-grade, {len(scan.findings) - n_fail} warning-grade):")
    for line in format_findings(scan.findings):
        print(f"  {line}")
    sys.exit(1)
