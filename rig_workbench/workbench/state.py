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


# ── gate construction / evaluation ───────────────────────────────────────────
def build_acceptance(task_id: str, task_type: str) -> dict:
    presets = TASK_TYPES[task_type]
    checks: list[dict] = []
    seen: set[str] = set()
    for preset in presets:
        for name in GATE_PRESETS[preset]:
            if name not in seen:
                seen.add(name)
                checks.append({"name": name, "status": "pending", "detail": ""})
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
