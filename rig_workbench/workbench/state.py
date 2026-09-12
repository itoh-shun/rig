"""workbench state: git/worktree helpers, run-state I/O, locks, audit, gate evaluation
(split from scripts/workbench.py)."""

import contextlib
import datetime
import hashlib
import hmac
import json
import os
import pathlib
import re
import secrets
import subprocess
import sys
import tempfile

try:
    import fcntl  # POSIX: mutual exclusion for concurrent task operations (task_lock)
except ImportError:
    fcntl = None  # type: ignore[assignment]  # Windows fallback (locking disabled)

from rig_workbench import gitroot
from rig_workbench.exitcodes import ERROR, REJECTED

from .config import GATE_PRESETS, TASK_TYPES


def now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


# `die` and `reject` are the two ways a workbench command stops early, and they are two
# functions rather than one because the caller reading `$?` cannot ask a follow-up
# question. `exitcodes.REJECTED` (1) is "rig judged this and the answer is no" — a verdict
# to act on. `exitcodes.ERROR` (2) is "rig could not produce an answer" — bad usage, state
# that is not there, a git command that failed. `die` used to end in a bare `sys.exit(1)`,
# which reported every plumbing failure as a verdict, so a script branching on 1 could not
# tell a failed gate from a task id with a typo in it.
#
# Neither takes a code, and there is no default to inherit: a call site chooses by which
# function it calls, so "is this a judgement?" is answered where the answer is known.


def die(msg: str) -> "NoReturn":  # noqa: F821
    """rig could not produce an answer. Exits `exitcodes.ERROR` (2).

    The overwhelming majority of stops: a task that is not there, an unreadable file, a
    flag that does not parse, a git command that failed. Nothing was judged — and where
    something was, it is not what this reports: `cmd_gate` ends here when a `--set`
    contradicts the sensor backing that criterion, after the gate has been evaluated and
    written. What is refused there is the operator's declaration, never the work, which
    is exactly why it must not come back as `reject`'s 1.
    """
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(ERROR)


def reject(msg: str) -> "NoReturn":  # noqa: F821
    """rig judged the work and the answer is no. Exits `exitcodes.REJECTED` (1).

    Only for a verdict rig actually reached — an unmet acceptance gate, a governance
    policy that blocks, an actor who is not permitted to accept. A caller acts on this
    and does not retry it, which is exactly what makes it wrong for a missing file.
    """
    print(f"[REJECTED] {msg}", file=sys.stderr)
    sys.exit(REJECTED)


def warn(msg: str) -> None:
    print(f"[WARN] {msg}")


# ── git helpers ───────────────────────────────────────────────────────────────
def git(args: list[str], cwd: pathlib.Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        die(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc


def _main_worktree() -> pathlib.Path | None:
    """Delegated to `rig_workbench.gitroot`, which `orchestrate` asks the same question of.

    Both subsystems needed to know where this repository keeps what it keeps once, and each
    had grown its own answer — this one asked `--show-toplevel`, the other took the process's
    initial working directory. One definition is the point (#471).
    """
    return gitroot.main_worktree()


def repo_root() -> pathlib.Path:
    """Where rig's state lives, from anywhere inside the repository — task worktrees included.

    **Per repository, not per working tree.** There is one `.rig/runs/<task_id>/` for a
    task no matter where that task's work happens to sit. `--show-toplevel`, which this
    used to ask, answers a different question — *which working tree am I standing in* — and
    that answer sent `workbench.py status` inside a task's own worktree looking for
    `<worktree>/.rig/runs/`, where the task it was asking about has never been written
    (#471). Every gate criterion, `accept`, and every sensor reads that state, so the whole
    flow had to be driven from the main checkout while the work happened somewhere else.
    """
    root = _main_worktree()
    if root is None:
        die("Run this inside a git repository")
    return root


def invocation_root() -> pathlib.Path:
    """The working tree the caller is standing in — which is not where rig's state lives.

    `repo_root()` answers *where does rig keep this repository's state*; this answers *what
    is the operator looking at*. Both used to be the same call, and separating them is the
    whole of #471: `HEAD`, the current branch, and "the commit I just made" are per working
    tree, so answering them from the main checkout would record another tree's branch as
    this task's base and another tree's HEAD as the commit a task shipped. State is shared;
    what the caller is looking at is not.
    """
    root = gitroot.invocation_worktree()
    if root is None:
        die("Run this inside a git repository")
    return root


def maybe_repo_root() -> pathlib.Path | None:
    """Like repo_root(), but returns None outside a git repository instead of dying."""
    return _main_worktree()


def current_branch(root: pathlib.Path) -> str:
    return git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root).stdout.strip()


def runs_dir(root: pathlib.Path) -> pathlib.Path:
    return root / ".rig" / "runs"


def audit_path(root: pathlib.Path) -> pathlib.Path:
    return root / ".rig" / "audit.jsonl"


def locks_dir(root: pathlib.Path) -> pathlib.Path:
    return root / ".rig" / "locks"


@contextlib.contextmanager
def task_lock(root: pathlib.Path, task_id: str):
    """Per-task mutual exclusion (prevents concurrent `accept`/`discard`/`gate`/`step`/`review`).

    Non-blocking acquisition via fcntl.flock. If acquisition fails, another
    process is definitely operating on the same task, so `die` with an explicit
    error (never race silently). The lock is released automatically on process
    exit (flock is fd-tied, so it doesn't linger even on kill). Without fcntl
    (e.g. Windows) this is a no-op — the safety net applies to parallel
    rig:queue go on WSL/Linux. Lock files are left in place (`.rig/` is
    gitignored; the files are empty).
    """
    if fcntl is None:
        yield
        return
    ld = locks_dir(root)
    ld.mkdir(parents=True, exist_ok=True)
    lock_file = ld / f"{task_id}.lock"
    with lock_file.open("a") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            die(f"task '{task_id}' is being operated on by another process ({lock_file.relative_to(root)}). "
                "Wait for it to finish, or inspect the process if it appears stuck")
        try:
            yield
        finally:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


def audit_append(root: pathlib.Path, event: dict) -> None:
    """Append a single JSON line to `.rig/audit.jsonl`.

    Permanent record of "--force overrides of an unmet gate", complementing the
    force-proof of accept_requirements. Evidence log that makes the physical
    strength of the differentiator visible. Read via `workbench.py audit`.
    Write failures are swallowed silently (best-effort, like telemetry).

    The file keeps its v1 shape — `workbench audit`, `digest` and every existing
    reader depend on it. Under a policy the same event is *also* chained into
    `.rig/ledger.jsonl`, where deleting it is detectable (govern.ledger).

    **Repeats of one event are bounded, and each file bounds itself.** This file has no
    chain to protect it and had no cap of its own, so a caller that can make the same event
    happen repeatedly grew it a line at a time for as long as it liked. The rule
    `govern.ledger` applies to the chain applies here — every field but the time of day
    compared, plus the date, `REPEAT_CAP` lines written and one more carrying `collapsed`,
    and the rest of that day not written.

    **The cap decides this file's write and nothing else.** It used to return before the
    ledger mirror, which handed an unsigned, hand-writable file authority over what the
    chain records: four look-alike lines pasted into `.rig/audit.jsonl` suppressed a real
    `accept_force` from the chain (measured, audit 4 → 4 and ledger 0 → 0), and the chain's
    own run was judged against this file's tail rather than its own (measured, a
    `policy.init` in between broke the ledger's run and the next repeat still wrote
    nothing). The mirror is now unconditional and `ledger.append` applies its own cap
    against its own tail — which is why the mirrored payload drops `ts` and `collapsed`:
    those are this file's bookkeeping, and leaving them in made every mirror unique so the
    chain could never recognise a repeat of its own.

    Line shape is unchanged, which is why `cmd_audit` and the rest keep reading it; the two
    readers that must not mistake a collapsed line for a single event say so themselves
    (`govern.ledger.collapsed_note`, `audit_event_weights`).
    """
    # INSIDE the swallow, and defaulting to "append anyway". The historic write was an
    # append with no read; the cap gave this function a read, and a read can fail where an
    # append cannot — a `.rig/audit.jsonl` holding a single 0xff byte raised
    # `UnicodeDecodeError` out of `_load_audit`, straight through `audit_append` and out of
    # `accept`, which by then has already squashed and written `status: accepted`. A forced
    # bypass then applied with no record in either file. A corrupt or unreadable audit log
    # must cost at most the cap, never the record.
    try:
        suppressed = _audit_repeat_suppressed(root, event)
    except Exception:
        suppressed = False
    if not suppressed:
        try:
            p = audit_path(root)
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        except Exception:
            pass
    try:
        from ..govern import ledger
        from ..govern.identity import current_actor, load_org_binding

        binding = load_org_binding(root)
        if binding.bound:
            mirrored = {k: v for k, v in event.items() if k not in ("ts", "collapsed")}
            ledger.append(root, f"audit.{event.get('action', 'event')}",
                          actor=current_actor(root), subject=str(event.get("task_id") or ""),
                          org=binding.org, team=binding.team, data=mirrored)
    except Exception:
        pass


def _audit_repeat_suppressed(root: pathlib.Path, event: dict) -> bool:
    """Whether this event repeats the tail of `.rig/audit.jsonl` often enough to stop.

    Mutates `event` to carry `collapsed` — that day's line count for it — on the entry that
    closes the cap; returns True for the repeats after it. Reading the file to write one
    line is the cost of the bound, and it is the same cost `govern.ledger.append` has always
    paid to find `prev`. Every failure here is "not suppressed": the caller swallows what
    escapes anyway, and both layers answer the same way, because a log this cannot read is a
    reason to write more rather than less.
    """
    from ..govern import ledger          # function-local, as the mirror below already is

    try:
        existing = _load_audit(root)
    except Exception:
        return False
    on_record = _audit_event_total(existing, event)
    if on_record > ledger.REPEAT_CAP:
        return True
    if on_record == ledger.REPEAT_CAP:
        event["collapsed"] = on_record + 1
    return False


def _audit_event_total(existing: list[dict], event: dict) -> int:
    """How many lines this event already has on today's record in `.rig/audit.jsonl`.

    The same walk `govern.ledger._event_total` does over the chain, over this file's own
    shape: every occurrence anywhere, not only a consecutive run, so that alternating two
    events does not escape the cap; a `collapsed` entry sets the running count rather than
    adding to it, because it already accounts for every line before it.
    """
    key = _audit_repeat_key(event)
    total = 0
    for previous in existing:
        if _audit_repeat_key(previous) != key:
            continue
        carried = previous.get("collapsed")
        total = carried if isinstance(carried, int) and carried > 0 else total + 1
    return total


def audit_event_weights(events: list[dict]) -> list[tuple[dict, int]]:
    """Each audit event with the number of lines it accounts for.

    One, except on an entry the cap closed: that one carries that day's line count for the
    event, and what it adds is that count minus what is already counted for the same event.
    Over a whole file this is 1 everywhere and the sum is the line count — the weighting is
    for a *window*, a `--last 7d` that begins after the plain lines and holds only the capped
    one, where counting it as a single event would report less than the file already shows.
    It does not recover the run: how many there were is not recorded anywhere, and
    `REPEAT_CAP` says why.

    Counted per event and not per consecutive run, because that is how the cap counts
    (`govern.ledger._event_total`); the key is `_audit_repeat_key`, the same one the write
    uses, so the two can never disagree about what "the same event" means.

    **And clamped, because `collapsed` arrives from an unsigned file.** `.rig/audit.jsonl`
    is plain JSON anyone with the checkout can edit — the premise the whole reconciliation
    rests on — so a single hand-written line saying `"collapsed": 1000000` would otherwise
    report a million forced accepts to `stats`, `digest` and `cockpit`. The clamp is not
    charity toward whoever wrote the line — the premise here is that they may be the forger
    — it is the ceiling of what the code that writes this file could have produced: the cap
    never writes more than `REPEAT_CAP + 1` for one event on one day, so a larger number is
    not evidence of more events, only of an edit.
    """
    from ..govern import ledger

    out: list[tuple[dict, int]] = []
    counted: dict[str, int] = {}
    for event in events:
        key = _audit_repeat_key(event)
        so_far = counted.get(key, 0)
        carried = event.get("collapsed")
        weight = (min(max(carried - so_far, 1), ledger.REPEAT_CAP + 1)
                  if isinstance(carried, int) and carried > 0 else 1)
        counted[key] = so_far + weight
        out.append((event, weight))
    return out


def _audit_repeat_key(event: dict) -> str:
    """What makes two audit events the same one: every field but the time of day, plus the
    date, and never `collapsed` (the capping line has to read as one more of the event it
    caps). The date is in it for the reason `govern.ledger.REPEAT_CAP` gives — the cap is per
    event per day, so it never silences an event that recurs next week, and a campaign that
    runs for days stays visible as days."""
    body = {k: v for k, v in event.items() if k not in ("ts", "collapsed")}
    body["_day"] = str(event.get("ts") or "")[:10]
    return json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)


def _load_audit(root: pathlib.Path) -> list[dict]:
    p = audit_path(root)
    if not p.exists():
        return []
    events: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


# ── run-state I/O ────────────────────────────────────────────────────────────
def run_dir(root: pathlib.Path, task_id: str) -> pathlib.Path:
    d = runs_dir(root) / task_id
    if not d.is_dir():
        die(f"task '{task_id}' not found ({d.relative_to(root)}). List tasks with `workbench.py log`")
    return d


def load_json(path: pathlib.Path, default: dict | None = None) -> dict:
    if not path.exists():
        if default is not None:
            return default
        die(f"{path} does not exist")
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: pathlib.Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_task(root: pathlib.Path, task_id: str) -> tuple[pathlib.Path, dict]:
    d = run_dir(root, task_id)
    return d, load_json(d / "task.json")


def save_task(d: pathlib.Path, task: dict) -> None:
    task["updated_at"] = now_iso()
    save_json(d / "task.json", task)


def latest_task_id(root: pathlib.Path) -> str | None:
    base = runs_dir(root)
    if not base.is_dir():
        return None
    candidates = sorted((p.name for p in base.iterdir() if (p / "task.json").exists()), reverse=True)
    return candidates[0] if candidates else None


def resolve_task_id(root: pathlib.Path, given: str | None) -> str:
    if given:
        return given
    tid = latest_task_id(root)
    if not tid:
        die("No run history (.rig/runs/ is empty). Run `/rig \"<task>\"` first")
    return tid


# ── task-id / slug ───────────────────────────────────────────────────────────
def make_slug(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text)
    slug = "-".join(w.lower() for w in words)[:32].strip("-")
    return slug or "task"


def make_task_id(slug: str) -> str:
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"rig-{ts}-{slug}"


# ── project-level gate extensions (.rig/gates.json; issue #283) ──────────────
PROJECT_GATES_REL = ".rig/gates.json"
_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_GATES_ALLOWED_KEYS = ("extra_criteria", "descriptions", "openapi_paths")
# Any of these keys signal an attempt to remove/weaken built-in criteria — rejected outright.
_GATES_REMOVAL_KEYS = ("remove", "remove_criteria", "removals", "disable",
                       "disable_criteria", "override", "overrides")


def project_gates_path(root: pathlib.Path) -> pathlib.Path:
    return root / ".rig" / "gates.json"


def load_project_gates(root: pathlib.Path) -> dict:
    """Load and validate `.rig/gates.json` — project-level acceptance-gate extensions.

    The file is JSON on purpose: gate config must always be parseable with the
    standard library alone, so YAML (an optional third-party parser) is
    deliberately avoided here.

    Accepted shape (all keys optional; absent file → {} = no-op):
      {
        "extra_criteria": {"<preset-or-task_type>": ["slug_criterion", ...]},
        "descriptions":   {"slug_criterion": "human description"},
        "openapi_paths":  ["api/openapi.json", ...]   # schema_diff sensor (issue #288)
      }

    Shape errors are hard errors (die), never warnings: a silently ignored gate
    criterion is the worst possible failure mode for this file. Config is
    additive only — removal/override keys are rejected because letting repo
    config weaken built-in criteria would undermine the gate's security posture.
    """
    p = project_gates_path(root)
    if not p.exists():
        return {}
    rel = PROJECT_GATES_REL
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"{rel} is not valid JSON: {e}")
    if not isinstance(data, dict):
        die(f"{rel} must be a JSON object, got {type(data).__name__}")

    for key in data:
        if key in _GATES_REMOVAL_KEYS:
            die(f"{rel}: key '{key}' is not allowed. Project gate config is additive only — "
                "removing or weakening built-in criteria is not supported (security posture: "
                "a repo file must never be able to lower the gate)")
        if key not in _GATES_ALLOWED_KEYS:
            die(f"{rel}: unknown key '{key}' (allowed: {', '.join(_GATES_ALLOWED_KEYS)})")

    extra = data.get("extra_criteria", {})
    if not isinstance(extra, dict):
        die(f"{rel}: 'extra_criteria' must be an object mapping preset/task_type → list of criteria")
    declared: set[str] = set()
    for target, crits in extra.items():
        if target not in GATE_PRESETS and target not in TASK_TYPES:
            die(f"{rel}: extra_criteria key '{target}' is neither a gate preset "
                f"({', '.join(GATE_PRESETS)}) nor a task_type ({', '.join(TASK_TYPES)})")
        if not isinstance(crits, list) or not all(isinstance(c, str) for c in crits):
            die(f"{rel}: extra_criteria['{target}'] must be a list of criterion id strings")
        for c in crits:
            if not _SLUG_RE.match(c):
                die(f"{rel}: criterion id '{c}' in extra_criteria['{target}'] is not a slug "
                    "(expected ^[a-z][a-z0-9_]*$, max 64 chars)")
            declared.add(c)

    descs = data.get("descriptions", {})
    if not isinstance(descs, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in descs.items()):
        die(f"{rel}: 'descriptions' must be an object mapping criterion id → string")
    builtin = {name for crits in GATE_PRESETS.values() for name in crits}
    for k in descs:
        if k not in declared and k not in builtin:
            die(f"{rel}: descriptions key '{k}' matches no declared extra criterion "
                "and no built-in criterion (typo would be silently ignored otherwise)")

    openapi = data.get("openapi_paths", [])
    if not isinstance(openapi, list) or not all(isinstance(s, str) and s for s in openapi):
        die(f"{rel}: 'openapi_paths' must be a list of non-empty relative path strings")
    for s in openapi:
        if s.startswith("/") or ".." in pathlib.PurePosixPath(s).parts:
            die(f"{rel}: openapi_paths entry '{s}' must be a repo-relative path "
                "(no absolute paths, no '..')")

    return data


# ── RBAC (.rig/access.json; issue #282) ──────────────────────────────────────
def load_access_control(root: pathlib.Path) -> dict:
    """Read `.rig/access.json` (the allowlist of identities permitted to `accept`, #282).

    Shape: `{"default": ["alice","bob"], "<task_type>": [...]}` (`default` is the
    fallback when there's no key for the specific task_type). Absent file means
    unrestricted (backward compatible — solo use behaves exactly as before). A
    malformed file never blocks a run; it falls back to unrestricted."""
    p = root / ".rig" / "access.json"
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        warn(f"{p} does not parse as JSON. Ignoring RBAC (running unrestricted)")
        return {}


def current_identity(root: pathlib.Path) -> str:
    """The identity performing `accept`. Resolved via the RIG_USER env var, then `git config user.name`."""
    env = os.environ.get("RIG_USER")
    if env:
        return env
    proc = git(["config", "user.name"], cwd=root, check=False)
    return proc.stdout.strip() or "unknown"


# ── time/cost budget warnings (issue #281) ───────────────────────────────────
def budget_status(task: dict) -> tuple[float, float | None, bool]:
    """(elapsed minutes, budget minutes or None, over-budget) for a task (#281). A task
    with no `budget_minutes` set is never over-budget — a task that never declared an
    estimate shouldn't get a false warning."""
    created = datetime.datetime.fromisoformat(task["created_at"])
    elapsed_min = (datetime.datetime.now().astimezone() - created).total_seconds() / 60.0
    budget = task.get("budget_minutes")
    over = bool(budget) and elapsed_min > budget
    return elapsed_min, budget, over


# ── signed provenance (issue #299) ───────────────────────────────────────────
def _provenance_key_path(root: pathlib.Path) -> pathlib.Path:
    return root / ".rig" / "provenance.key"


def provenance_key(root: pathlib.Path) -> bytes | None:
    """The signing key as it is, or `None`. **Reads. Never creates, never replaces.**

    The half `verify_provenance` needs, and the reason there are two functions instead of a
    flag: verification used to call the creating loader, so checking a record on a
    repository whose key was unusable *replaced that key* — 32 fresh bytes over the file,
    every existing record permanently unverifiable, and tamper and rotation left
    indistinguishable. A read path that can destroy what it is reading is not a read path,
    and `wb.verify-provenance` is declared `effect_class="read-only"` in the capability
    registry, which that made false.

    `None` means this repository has no key to check against — absent, or present and not a
    key (`govern.ledger.usable_key` is the one rule, shared with the ledger). A caller that
    cannot verify says so; it does not go and make one.
    """
    from ..govern.ledger import usable_key

    p = _provenance_key_path(root)
    try:
        return usable_key(p.read_bytes() if p.is_file() else None)
    except OSError:
        return None


def _set_unusable_key_aside(p: pathlib.Path) -> pathlib.Path | None:
    """Rename whatever is at the key path out of the way, and say where it went.

    **Moved, not overwritten, and never followed.** The first shape of this wrote the new
    key straight over the old file, which is a destructive answer to a diagnosis: an
    8-byte key is a 64-bit secret nobody brute-forces, and replacing it in place made every
    record it signed unverifiable with no copy left to restore. Worse, `Path.is_file()`
    follows symlinks, so `.rig/provenance.key` pointing at a file outside the repository had
    *that* file overwritten with 32 random bytes. `rename` acts on the link itself, so a
    symlink is moved aside and its target is never touched — the same refusal
    `eval/attestation.py` makes, taken here as "do not write through it" rather than as an
    error, because this path has to leave the repository able to sign.

    **This function does not decide that the file is unusable, and it says nothing about
    it.** Its caller read the file and found it short; by the time the rename runs another
    process may have replaced it with a perfectly good key, and the caller checks for that
    after the fact rather than asserting anything here.
    """
    # The next number after the highest one present, not the first free one: reusing
    # `.unusable` after an operator deletes it gives the newest file the oldest name, and
    # then only mtime says which is which. `isdecimal` and not `isdigit`, which is True for
    # a superscript and then raises out of `int()` — in a directory somebody else names.
    used: set[int] = set()
    for sibling in p.parent.glob(f"{p.name}.unusable*"):
        suffix = sibling.name[len(p.name) + len(".unusable"):]
        used.add(int(suffix[1:]) if suffix.startswith("-") and suffix[1:].isdecimal() else 1)
    n = max(used, default=0) + 1
    aside = p.with_name(f"{p.name}.unusable" if n == 1 else f"{p.name}.unusable-{n}")
    # No "if it exists, try the next one" loop after this: every existing sibling is in
    # `used`, so max-plus-one is free by construction and the loop that used to be here
    # could not run. What it never protected against is the case it looked like it covered
    # — two processes allocating the same name between the glob and the rename, where a
    # check before the rename is the same race one line earlier. That collision is the
    # unlocked-concurrency residual the changelog records, not something this loop closed.
    try:
        p.rename(aside)
    except FileNotFoundError:
        # It is already gone: a sibling process moved or replaced it between this process
        # reading it and getting here. Nothing to set aside and nothing to report — the
        # caller re-reads and takes whatever is there now.
        return None
    except OSError as e:
        # The caller decides what to do; what this function will not do is fall through to
        # overwriting the file it just refused to use. `accept` acquires the key *before*
        # the squash precisely so this can be a refusal rather than a crash after the point
        # of no return — see `cmd_accept`'s "(2)-c".
        raise OSError(f"{p} could not be moved aside ({e}), and it will not be overwritten. "
                      "Re-run; if it persists, move or delete the file yourself") from e
    return aside


def _create_key_if_absent(p: pathlib.Path, key: bytes) -> None:
    """Put `key` at `p` if and only if nothing is there — atomically, for other processes.

    **Why a temporary file and `os.link`, and not `write_bytes` or `O_EXCL` alone.** Two
    accepts in one repository share no lock, and this path is what they collide on. Measured
    on the previous shape, four concurrent creators per repository: with no key at all the
    processes ended up holding different keys in 3 of 40 races, so a record signed by one
    was verified against another's key; and with a short key on disk, 7 good keys were moved
    aside across 60 races, because one process read "too short", a sibling wrote a real key,
    and the first renamed *that* away. `O_EXCL` alone fixes only half of it: the file exists
    from the moment it is created and is empty until the write lands, so a sibling reading in
    that window sees zero bytes and — by this module's own rule — calls it unusable. The
    bytes are therefore written into a temporary file first and linked into place complete,
    which is exactly what `eval/attestation.py` does with its own key, and the loser of the
    race takes the winner's key rather than clobbering it.
    """
    p.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".provenance-key.", dir=p.parent)
    temporary = pathlib.Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, key)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.link(temporary, p)
    except FileExistsError:
        pass                      # a sibling got there first; its key is the repository's
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


#: How many passes `load_or_create_provenance_key` makes before it refuses.
#:
#: A pass is a read plus at most one action, not a retry: each one either returns a key or
#: changes the directory (a file set aside, a key linked in). **The ordinary cases are not
#: free of them** — a repository that already has a key spends one, and a fresh one spends
#: two (the first creates, the second reads back what is there) — so four leaves two spare
#: for contention rather than four. The bound is here so that a pathological loop refuses
#: rather than spins, and it refuses *before* the squash, where refusing is free.
_KEY_SETTLE_PASSES = 4


def load_or_create_provenance_key(root: pathlib.Path) -> bytes:
    """The HMAC-SHA256 signing key (#299), created if this repository has none. Lives under
    `.rig/` (gitignored), so it never enters the repo. Deliberately HMAC rather than
    asymmetric signing (Ed25519/SLSA) to keep workbench.py stdlib-only. This gives
    same-machine tamper-evidence — proof a provenance record hasn't been edited after the
    fact on a machine holding the key — not third-party public verification the way
    SLSA/Ed25519 provide.

    **The creating half, and only callers that must sign may use it.** `sign_provenance`
    does; `verify_provenance` must not, and `provenance_key` above is what it reads.

    What counts as a key is `govern.ledger.usable_key`, the same rule the ledger applies to
    the same file: `p.read_bytes()` used to be returned whatever it held, so a zero-byte
    `.rig/provenance.key` signed provenance records with an empty secret — measured,
    `sign_provenance` produced a signature, `verify_provenance` returned True, and a record
    rewritten to a different `accepted_by` and re-signed under the same nothing verified as
    well, so `workbench.py verify-provenance` printed valid and untampered over it.

    A file that is present and not a key is **set aside, not replaced**: signing has to go
    on working, the old bytes are kept where an operator can find them, and the warning says
    which file is which. What it cannot do is repair records already signed with the old
    file — those stay unverifiable, which is the cost of a key that was never a key.

    **Everything here is written for a sibling process doing the same thing.** Two accepts in
    one repository share no lock. So: the key is created atomically (`_create_key_if_absent`),
    a file is only set aside when *this* process read it and found it short, and after the
    rename the moved file is read back — if it turns out to be a usable key, a sibling wrote
    it in the interval and it is put back into service rather than being called unusable.
    That last check is what keeps the warning from stating something false about somebody
    else's good key, which is the one thing this function must never do.
    """
    from ..govern.ledger import MIN_KEY_BYTES

    p = _provenance_key_path(root)
    for _ in range(_KEY_SETTLE_PASSES):
        existing = provenance_key(root)
        if existing is not None:
            return existing
        if p.is_symlink() or p.exists():
            observed, kind = _observe_key_file(p)
            aside = _set_unusable_key_aside(p)
            if aside is None:
                continue
            rescued = _usable(_read_key_bytes(aside))
            if rescued is not None:
                # We moved a key that WAS a key: between this process reading the file and
                # renaming it, a sibling replaced it. Saying "not a usable signing key" over
                # those bytes would be false, and this whole change is about records that
                # claim more than they know. Put it back into service instead.
                _create_key_if_absent(p, rescued)
                # …and do not leave the copy behind. Until this line, the rescue left
                # `provenance.key.unusable` byte-identical to the live key: a second copy of
                # the active secret under a name asserting it is dead. Mode 0600 means
                # nothing is newly exposed, but the name is false about the bytes, and a
                # spare copy of a signing key is not something to keep by accident. Removed
                # only once the live file holds those same bytes, so nothing is discarded
                # that is not already in place.
                kept = _read_key_bytes(p) == rescued
                if kept:
                    aside.unlink(missing_ok=True)
                warn(f"{p} was replaced with a usable key while this process was setting the "
                     "previous one aside; that key is what the repository now uses"
                     + (". The copy this process had set aside has been removed"
                        if kept else f". The copy is at {aside.name}")
                     + ". No key has been discarded")
                continue
            # What this process saw. Three messages, because *what the operator should do
            # next* differs and a line that blurs them gives one of them bad advice. Four
            # observations map onto the three: `"unknown"` joins `"regular"`, because the
            # two lines differ in whether they claim anything, and a claim needs a `stat`
            # that answered.
            #
            # `held N byte(s)` is the only one that measured anything, and it is the only
            # one that may end "anything signed with the moved file no longer verifies":
            # the bytes were counted, they are under the floor, and every reader refuses
            # that file forever, so nothing signed with it will ever verify again.
            #
            # A regular file this process could not open — a mode it may not read, an owner
            # who is not us — was not measured at all. A genuine 32-byte key arrives here
            # with its bytes whole, so the "no longer verifies" clause would be a claim
            # about a file nobody looked at, told to the operator deciding whether to
            # delete it. It is dropped rather than made conditional on the file being a
            # key, because that condition is unanswerable from here: the read that would
            # answer it is the read that just failed, and it failed again on the moved file
            # a few lines up (`rescued`). Reading it is what the operator must do, and only
            # they can.
            #
            # A path a `stat` came back on as not a regular file — a FIFO, a directory, a
            # device, a symlink to nothing or to itself — is the third, and it used to
            # share the second's wording and so its advice. Telling somebody to read a FIFO
            # before deleting it is worse than saying nothing: `cat` on it blocks until
            # something writes. Neither reader of this path has ever accepted a non-regular
            # file (`state.provenance_key` and `ledger._key` both gate on `is_file`), so
            # unlike the second shape this one is not withholding a key — which is what
            # lets it say so, and what a failed `stat` must never be allowed to borrow.
            if observed is not None:
                seen = (f"held {len(observed)} byte(s) when this process read it, below the "
                        f"{MIN_KEY_BYTES} a signing key must have")
                consequence = "anything signed with the moved file no longer verifies"
            elif kind == "other":
                seen = ("could not be read as a key by this process (it is not a regular "
                        "file, such as a FIFO, a directory, a device, or a symlink that "
                        "resolves to nothing)")
                consequence = ("a path of that kind is never read as a key, so nothing was "
                               "signed with what was moved; identify it rather than opening "
                               "it, because reading a FIFO blocks until something writes")
            else:
                # "regular" and "unknown" both land here, and that is the safe direction:
                # this branch claims nothing about the contents, it asks the operator to go
                # and look. The branch above does make a claim, so it needs a `stat` that
                # actually answered.
                seen = ("could not be read as a key by this process (the permissions may "
                        "not allow it)")
                consequence = ("its contents are unread, so whether anything signed with it "
                               "still verifies is unknown — read it before deleting it")
            warn(f"{p} {seen}. It has been moved to {aside.name} and a new key "
                 f"generated; {consequence}")
        _create_key_if_absent(p, secrets.token_bytes(32))
    raise OSError(f"{p} could not be settled into a usable signing key after "
                  f"{_KEY_SETTLE_PASSES} attempts (another process may be creating it). "
                  "Re-run")


#: What `_observe_key_file` was able to establish about the path.
#:
#: `"other"` is the only one that asserts anything about the *kind* of the file, and the
#: set-aside warning says "nothing was signed with what was moved" on the strength of it.
#: It is what `p.is_file()` answering `False` means, and that is a slightly wider thing
#: than "a `stat` said not-a-regular-file": `pathlib` turns `ENOENT`, `ENOTDIR`, `EBADF`
#: and `ELOOP` into `False` as well. All four are safe to put here, and for the same
#: reason the honest ones are — no such path, a non-directory in the way, a bad
#: descriptor and a symlink loop are each a thing that cannot be a key and that both
#: readers of this path already refuse. What is *not* safe is the errno `pathlib`
#: re-raises: `EACCES` on the `stat` establishes nothing at all, so it is `"unknown"` and
#: routes with `"regular"` to the branch that assumes a key may be in there. Sending it
#: the other way prints "nothing was signed with what was moved" over a live key — a
#: symlink to a real 32-byte key through a directory this process may not traverse is the
#: reproducible case, and there is no reading it from here to find out.
_KeyFileKind = str  # "regular" | "other" | "unknown"


def _read_key_bytes(p: pathlib.Path) -> bytes | None:
    """The bytes, or `None` for every way this process did not get them."""
    return _observe_key_file(p)[0]


def _observe_key_file(p: pathlib.Path) -> tuple[bytes | None, _KeyFileKind]:
    """The bytes this process read, and what it could establish about the path's kind.

    `_read_key_bytes` (which is now this function with the kind dropped, so the two cannot
    drift) collapses every failure into one `None`, and the set-aside warning has to tell
    them apart: a regular file we may not open can be a whole key and wants reading, a FIFO
    cannot be a key and must not be read, and a path we could not even `stat` is neither
    known.

    **`p.is_file()` is inside the `try`.** It swallows `ENOENT`, `ENOTDIR`, `EBADF` and
    `ELOOP` and re-raises everything else, so a key symlinked through a directory this
    process may not traverse raised `PermissionError` out of the loader — a state that
    warns and recovers, turned fatal, `accept` refusing with "the key could not be
    prepared".

    **This narrows the sibling race; it does not close it.** The `stat` and the `read` are
    two calls, and the `rename` at the call site is a third: a sibling that changes the
    kind in between is reported under the kind seen first. The sharpest form, reproduced:
    a sibling unlinks the path before the `stat` and writes a regular file before the
    `rename`, and the warning prints the not-a-regular-file line — with its claim that
    nothing was signed with what was moved — over a moved regular file. That is the
    residual, and it is written down rather than claimed away; closing it wants the file
    held open across the rename, which is a different change.
    """
    try:
        regular = p.is_file()
    except OSError:
        return None, "unknown"
    if not regular:
        return None, "other"
    try:
        return p.read_bytes(), "regular"
    except OSError:
        return None, "regular"


def _usable(raw: bytes | None) -> bytes | None:
    from ..govern.ledger import usable_key

    return usable_key(raw)


def _provenance_payload(record: dict) -> bytes:
    return json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf-8")


def sign_provenance(root: pathlib.Path, record: dict, *, key: bytes | None = None) -> str:
    """Sign one record. `key` is the bytes the caller already holds, if it holds them.

    **Why the parameter exists, and it is the whole point of it.** This is called from
    `accept` *after* the squash has been applied and the ledger written, and resolving the
    key there means touching the filesystem after the point of no return: measured, an
    immutable `.rig/` raised `PermissionError` out of here, and four concurrent accepts
    renaming an unusable key from under each other raised `FileNotFoundError` in one trial
    of twenty-five — each one a landed accept with no provenance record and no explanation,
    which is the shape `audit_append`'s swallow-all was written to stop. `accept` now
    acquires the key before the squash, where a failure is a refusal that costs nothing,
    and hands the bytes here; with them, this function does no I/O at all.
    """
    if key is None:
        key = load_or_create_provenance_key(root)
    return hmac.new(key, _provenance_payload(record), hashlib.sha256).hexdigest()


def verify_provenance(root: pathlib.Path, record: dict, signature: str) -> bool:
    """Whether this record still matches its signature. Reads the key; writes nothing.

    No key — absent, or present and not a key — is `False`: unverifiable is not verified,
    and the alternative this replaced was worse than wrong, because making a key here meant
    the check destroyed the evidence it was called to check.
    """
    key = provenance_key(root)
    if key is None:
        return False
    expected = hmac.new(key, _provenance_payload(record), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# ── gate construction / evaluation ───────────────────────────────────────────
def load_policy_criteria(root: pathlib.Path | None, task_type: str,
                         presets: list[str]) -> tuple[list[str], dict[str, str]]:
    """Criteria the org/team policy layers require for this task_type (v2).

    This is how a common policy reaches a gate: the org states that every
    `feature` must carry `threat_model_reviewed`, and every project under that
    policy gets the criterion whether or not its own `.rig/gates.json` mentions
    it. Same additive-only semantics as the project file — a policy can add
    criteria to the gate, never take built-ins away.

    A malformed or unreachable policy is swallowed here and reported loudly by
    `accept` instead: gate *construction* runs on `new`, and failing there would
    strand a task before it starts, while `accept` is the point where refusing is
    both safe and meaningful.
    """
    if root is None:
        return [], {}
    try:
        from ..govern.policy import effective_policy

        eff = effective_policy(root)
    except Exception:
        return [], {}
    if not eff.active:
        return [], {}
    return eff.required_criteria_for(task_type, presets), dict(eff.descriptions)


def build_acceptance(task_id: str, task_type: str, root: pathlib.Path | None = None) -> dict:
    """Compose the acceptance gate for a task_type from GATE_PRESETS, plus any
    project-level extra criteria from `.rig/gates.json` and any criteria the
    org/team policy requires (v2) when `root` is given. Custom criteria start
    pending like built-ins and carry origin="project" / origin="policy" so
    displays can tell them apart."""
    presets = TASK_TYPES[task_type]
    project = load_project_gates(root) if root is not None else {}
    extra = project.get("extra_criteria", {})
    descriptions = dict(project.get("descriptions", {}))
    policy_criteria, policy_descriptions = load_policy_criteria(root, task_type, presets)
    for name, text in policy_descriptions.items():
        descriptions.setdefault(name, text)
    checks: list[dict] = []
    seen: set[str] = set()

    def add(name: str, origin: str | None = None) -> None:
        if name in seen:
            return
        seen.add(name)
        check = {"name": name, "status": "pending", "detail": ""}
        if origin:
            check["origin"] = origin
            if name in descriptions:
                check["description"] = descriptions[name]
        checks.append(check)

    for preset in presets:
        for name in GATE_PRESETS[preset]:
            add(name)
        for name in extra.get(preset, []):
            add(name, origin="project")
    for name in extra.get(task_type, []):
        add(name, origin="project")
    for name in policy_criteria:
        add(name, origin="policy")
    return {"task_id": task_id, "task_type": task_type, "presets": presets,
            "status": "pending", "checks": checks, "checked_at": None}


def record_sensor_status(check: dict, status: str, detail: str, writer: str) -> None:
    """A sensor taking a criterion's status. It owns `status`, `detail` and `by`; it never
    touches `note`, except to drop one the status it is replacing has taken with it.

    The two fields exist because a sentence and a verdict are different things. `detail`
    explains the status underneath it, so whoever writes the status writes the detail —
    words that explain a different verdict explain this one wrongly, which is how a
    refused `--set no_secret_leak=passed:"false positive"` came to sit over a failure.
    `note` is the operator's own, written only by `cmd_gate` from the `:DETAIL` half of a
    `--set`, and it is the durable half: an operator who records
    `--set no_gate_tampering=warning:"the test moved to tests/test_new.py"` is saying why
    about the very finding the sensor then grades `warning`, and that sentence has to
    outlive any number of later evaluations. It does, for exactly as long as the status
    it was attached to does: a sensor changing the status takes the note with it, because
    a claim about a status that is gone is a claim about nothing.
    """
    # Only the status decides. A note survives a re-evaluation that finds MORE under the
    # same status — two test-weakening patterns becoming four — on purpose: the operator
    # wrote it about the status they declared, which still stands, and what changed is
    # already in `detail` and in the findings list beside it. Dropping it there would
    # delete a standing reason every time a sensor counted again.
    if check.get("status") != status:
        check.pop("note", None)
    check["status"], check["detail"], check["by"] = status, detail, writer


def gate_status(acc: dict) -> str:
    """Evaluate with priority: failed > pending > (skipped if all skipped) > warning-or-skip > passed.

    A SKIPPED CRITERION NEVER REACHES `passed`. `skipped` means "not judged", and a gate
    that reports `passed` with one of them in it says the whole set was judged and cleared,
    which is the one thing it is not. `accept` refuses an all-skipped gate, but that only
    ever covered the whole-gate case: one criterion declared `passed` and the other fourteen
    `skipped` scored `passed` outright and bought an accept nothing recorded as unusual.
    Declining to judge is now a warning-grade fact instead — `passed_with_warnings`, which
    `accept` still lets through without `--force` (a warning has never blocked accept) but
    which carries the skipped names into the gate's summary, into accept's own output and
    into the signed provenance record, where a reader of the record sees them.
    """
    statuses = [c["status"] for c in acc["checks"]]
    if not statuses:
        return "skipped"
    if any(s == "failed" for s in statuses):
        return "failed"
    if any(s == "pending" for s in statuses):
        return "pending"
    if all(s == "skipped" for s in statuses):
        return "skipped"
    if any(s in ("warning", "skipped") for s in statuses):
        return "passed_with_warnings"
    return "passed"


# ── worktree ─────────────────────────────────────────────────────────────────
def task_head(root: pathlib.Path, task: dict) -> str | None:
    """The commit this task's work currently sits on: the worktree's HEAD, or the main
    tree's when the task has none (`--no-worktree`).

    Two callers, and they are two halves of one fact. `cmd_gate` records the answer into
    acceptance.json as `evaluated_head` — the commits the verdict was measured against —
    and `accept` asks again before squashing, so a gate that judged an older tip cannot be
    spent on a newer one. `None` (git could not answer) is not a head and never compares
    equal to one: the caller treats it as unknown rather than as a match.
    """
    wt = task.get("worktree_path")
    cwd = pathlib.Path(wt) if wt and pathlib.Path(wt).is_dir() else root
    proc = git(["rev-parse", "HEAD"], cwd=cwd, check=False)
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def task_branch_tip(root: pathlib.Path, task: dict) -> str | None:
    """The commit `accept` will actually squash: the tip of the task's own branch.

    Resolved in the MAIN tree, not in the worktree, because that is where
    `git merge --squash <branch>` runs and what it resolves. The distinction is the whole
    point of the function: a worktree can be detached at one commit while the branch it was
    cut for points at another, and a check that asked the worktree what it was sitting on
    would answer about a commit nothing is going to merge.

    `None` when the task records no branch (a `--no-worktree` run has none) or when the
    name no longer resolves — both "unknown", never a match.
    """
    branch = task.get("branch")
    if not branch:
        return None
    proc = git(["rev-parse", "--verify", f"{branch}^{{commit}}"], cwd=root, check=False)
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def default_worktree_path(root: pathlib.Path, task_id: str) -> pathlib.Path:
    import os
    wt_root = os.environ.get("RIG_WORKTREE_ROOT")
    base = pathlib.Path(wt_root) if wt_root else root.parent / "rig-worktrees" / root.name
    return base / task_id


def worktree_dirty(wt: pathlib.Path) -> list[str]:
    proc = git(["status", "--porcelain"], cwd=wt)
    return [line for line in proc.stdout.splitlines() if line.strip()]


# ── base drift (#312) ────────────────────────────────────────────────────────
# `task.json`'s `base_commit` is a snapshot taken at registration and is never
# updated. Rebasing a task branch onto a newer base — legitimate and common —
# makes it stale, and `git diff <stale>...HEAD` then *silently widens*: three-dot
# resolves to merge-base(stale, HEAD), which after a rebase onto a descendant is
# still the stale commit, so everything that landed on the base in between gets
# counted as the task's own work. No conflict, no error, just a bigger diff that
# `accept` would re-apply on top of itself. Every range therefore has to be
# recomputed live from the refs as they are *now*.
def effective_base(root: pathlib.Path, task: dict) -> tuple[str, str | None]:
    """Return (base commit the task diff must be computed against, drifted-from).

    The live value is merge-base(base_branch, task branch) as they stand now.
    `drifted_from` is the recorded `base_commit` when it differs from that live
    value, else None — callers use it to surface the drift.

    Falls back to the recorded value (and no drift) whenever the live value
    cannot be established: no worktree, no recorded base_branch/base_commit, a
    base_branch that no longer resolves, or unrelated histories. Never edits
    task.json; the record stays as the historical fact it is.
    """
    recorded = task.get("base_commit") or ""
    base_branch = task.get("base_branch") or ""
    wt = pathlib.Path(task["worktree_path"]) if task.get("worktree_path") else None
    # Worktree-less runs have no branch to rebase: their diff is taken against the
    # main working tree's HEAD, never against base_commit.
    if not recorded or not base_branch or base_branch == "HEAD" or not (wt and wt.is_dir()):
        return recorded, None
    # Resolve the task tip to a sha first: a *symbolic* ref like "HEAD" resolves
    # per worktree, so passing one to a merge-base run elsewhere would silently
    # compare the wrong commits.
    if task.get("branch"):
        tip_proc = git(["rev-parse", "--verify", f"{task['branch']}^{{commit}}"], cwd=root, check=False)
    else:
        tip_proc = git(["rev-parse", "--verify", "HEAD^{commit}"], cwd=wt, check=False)
    base_proc = git(["rev-parse", "--verify", f"{base_branch}^{{commit}}"], cwd=root, check=False)
    if tip_proc.returncode != 0 or base_proc.returncode != 0:
        return recorded, None
    proc = git(["merge-base", base_proc.stdout.strip(), tip_proc.stdout.strip()], cwd=root, check=False)
    live = proc.stdout.strip()
    if proc.returncode != 0 or not live:
        return recorded, None
    return live, (recorded if live != recorded else None)


def drift_lines(task: dict, drifted_from: str | None, effective: str, indent: str = "") -> list[str]:
    """Printable drift notice. Empty list when there is no drift (the normal case) —
    this must not become a wall of text on every run."""
    if not drifted_from:
        return []
    return [
        f"{indent}[WARN] base drift: this branch was rebased since it was registered.",
        f"{indent}  recorded base_commit {drifted_from[:12]} → current merge base with "
        f"{task.get('base_branch')} {effective[:12]}",
        f"{indent}  The diff is computed against the current merge base; the recorded value is "
        f"kept as-is (nothing to edit).",
    ]


def _diff_lines(root: pathlib.Path, task: dict) -> tuple[list[str], str, list[str]]:
    """Return (name-status lines, shortstat, uncommitted worktree lines)."""
    wt = pathlib.Path(task["worktree_path"]) if task.get("worktree_path") else None
    if wt and wt.is_dir():
        base, _drift = effective_base(root, task)
        # Two-dot against the live merge base: three-dot here would re-open the
        # widening hole the moment `base` were stale again.
        names = git(["diff", "--name-status", base, "HEAD"], cwd=wt).stdout.splitlines()
        stat = git(["diff", "--shortstat", base, "HEAD"], cwd=wt).stdout.strip()
        dirty = worktree_dirty(wt)
        return names, stat, dirty
    # Worktree-less runs (reviews etc.) diff against the current state of the main working tree
    names = git(["diff", "--name-status", "HEAD"], cwd=root).stdout.splitlines()
    stat = git(["diff", "--shortstat", "HEAD"], cwd=root).stdout.strip()
    return names, stat, []


# ── structured diff.md parser ────────────────────────────────────────────────
def parse_diff_md(text: str) -> dict[str, str]:
    """Split diff.md, delimited by `## <heading>`, into a section dict (lowercase keys)."""
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = m.group(1).strip().lower()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections
