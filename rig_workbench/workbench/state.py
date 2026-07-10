"""workbench state: git/worktree helpers, run-state I/O, locks, audit, gate evaluation
(split from scripts/workbench.py)."""

import contextlib
import datetime
import json
import pathlib
import re
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
    """
    try:
        p = audit_path(root)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
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


# ── gate construction / evaluation ───────────────────────────────────────────
def build_acceptance(task_id: str, task_type: str, root: pathlib.Path | None = None) -> dict:
    """Compose the acceptance gate for a task_type from GATE_PRESETS, plus any
    project-level extra criteria from `.rig/gates.json` when `root` is given.
    Custom criteria start pending like built-ins and carry origin="project" so
    displays can tell them apart."""
    presets = TASK_TYPES[task_type]
    project = load_project_gates(root) if root is not None else {}
    extra = project.get("extra_criteria", {})
    descriptions = project.get("descriptions", {})
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


def _diff_lines(root: pathlib.Path, task: dict) -> tuple[list[str], str, list[str]]:
    """Return (name-status lines, shortstat, uncommitted worktree lines)."""
    wt = pathlib.Path(task["worktree_path"]) if task.get("worktree_path") else None
    if wt and wt.is_dir():
        base = task["base_commit"]
        names = git(["diff", "--name-status", f"{base}...HEAD"], cwd=wt).stdout.splitlines()
        stat = git(["diff", "--shortstat", f"{base}...HEAD"], cwd=wt).stdout.strip()
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
