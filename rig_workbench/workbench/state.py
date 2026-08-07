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

try:
    import fcntl  # POSIX: mutual exclusion for concurrent task operations (task_lock)
except ImportError:
    fcntl = None  # type: ignore[assignment]  # Windows fallback (locking disabled)

from .config import GATE_PRESETS, TASK_TYPES


def now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def die(msg: str) -> "NoReturn":  # noqa: F821
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def warn(msg: str) -> None:
    print(f"[WARN] {msg}")


# ── git helpers ───────────────────────────────────────────────────────────────
def git(args: list[str], cwd: pathlib.Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        die(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc


def repo_root() -> pathlib.Path:
    proc = git(["rev-parse", "--show-toplevel"], check=False)
    if proc.returncode != 0:
        die("Run this inside a git repository")
    return pathlib.Path(proc.stdout.strip())


def maybe_repo_root() -> pathlib.Path | None:
    """Like repo_root(), but returns None outside a git repository instead of dying."""
    proc = git(["rev-parse", "--show-toplevel"], check=False)
    return pathlib.Path(proc.stdout.strip()) if proc.returncode == 0 else None


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
    """
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
            ledger.append(root, f"audit.{event.get('action', 'event')}",
                          actor=current_actor(root), subject=str(event.get("task_id") or ""),
                          org=binding.org, team=binding.team, data=event)
    except Exception:
        pass


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


def load_or_create_provenance_key(root: pathlib.Path) -> bytes:
    """The HMAC-SHA256 signing key (#299). Lives under `.rig/` (gitignored), so it never
    enters the repo. Deliberately HMAC rather than asymmetric signing (Ed25519/SLSA) to
    keep workbench.py stdlib-only. This gives same-machine tamper-evidence — proof a
    provenance record hasn't been edited after the fact on a machine holding the key —
    not third-party public verification the way SLSA/Ed25519 provide."""
    p = _provenance_key_path(root)
    if p.is_file():
        return p.read_bytes()
    key = secrets.token_bytes(32)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(key)
    try:
        p.chmod(0o600)
    except Exception:
        pass
    return key


def _provenance_payload(record: dict) -> bytes:
    return json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf-8")


def sign_provenance(root: pathlib.Path, record: dict) -> str:
    key = load_or_create_provenance_key(root)
    return hmac.new(key, _provenance_payload(record), hashlib.sha256).hexdigest()


def verify_provenance(root: pathlib.Path, record: dict, signature: str) -> bool:
    key = load_or_create_provenance_key(root)
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


def gate_status(acc: dict) -> str:
    """Evaluate with priority: failed > pending > (skipped if all skipped) > warning > passed."""
    statuses = [c["status"] for c in acc["checks"]]
    if not statuses:
        return "skipped"
    if any(s == "failed" for s in statuses):
        return "failed"
    if any(s == "pending" for s in statuses):
        return "pending"
    if all(s == "skipped" for s in statuses):
        return "skipped"
    if any(s == "warning" for s in statuses):
        return "passed_with_warnings"
    return "passed"


# ── worktree ─────────────────────────────────────────────────────────────────
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
