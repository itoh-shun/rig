"""workbench ports: per-task Docker/port isolation for parallel `/rig` runs.

Two `/rig "<task>"` runs already get separate git worktrees (patterns/isolated-worktree),
but a worktree only isolates *files*. A project's `docker-compose.yml` binds host ports
and (when a service sets `container_name:`) container names directly — neither is
namespaced by which directory `docker compose` was invoked from, so two parallel task
worktrees running the same compose file collide: "port is already allocated" or
"container name already in use". That collision is what makes parallel rig runs feel
unsafe even though the file-level isolation is already there.

This module extends the same spatial isolation to those two axes. `allocate_ports`
reserves a block of currently-free host ports for one task, recorded under
`.rig/ports.json` (repo-wide state, one entry per task_id) so two `workbench.py new`
processes racing each other never hand out the same port twice — the file is guarded by
an flock the same way `state.task_lock` guards `.rig/runs/<task_id>/`, just keyed by the
shared resource instead of one task. `write_env_file` drops those ports, plus a
per-task `COMPOSE_PROJECT_NAME`, into `.env.rig` at the worktree root; a project's
compose file opts in with `${RIG_PORT_0:-3000}:3000`-style interpolation (see
skills/engine/patterns/isolated-worktree.md). `release_ports` frees the block again
when the task is discarded, so the range doesn't monotonically fill up over a long
session.

This is best-effort, not a lock on the port: the actual bind still happens later, when
`docker compose up` runs, so another process on the machine can in principle still grab
a reserved port in between. The bindability probe narrows that window to "this port was
free the instant it was chosen" rather than eliminating it — nothing short of holding
the socket open for the run's whole lifetime could do more, and that would mean rig
itself becoming a long-lived daemon.
"""

from __future__ import annotations

import contextlib
import pathlib
import re
import socket

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]  # Windows fallback (locking disabled)

from .state import git, load_json, now_iso, runs_dir, save_json, warn

ENV_FILENAME = ".env.rig"
PORTS_STATE_REL = ".rig/ports.json"
DEFAULT_PORT_COUNT = 8
# High enough to stay clear of common dev-server defaults (3000, 5432, 6379, 8080, ...)
# so a task's reserved block never collides with a fixed port a project's own compose
# file already hardcodes, low enough to stay well under the ephemeral-port range the
# OS hands out for outgoing connections.
PORT_RANGE_START = 20000
PORT_RANGE_END = 39999

_COMPOSE_NAME_RE = re.compile(r"[^a-z0-9_-]")


def compose_project_name(task_id: str) -> str:
    """A `COMPOSE_PROJECT_NAME` derived from task_id (already lowercase alnum + '-').

    Compose project names must be lowercase and start with a letter or digit;
    task_id (`rig-YYYYMMDD-HHMMSS-<slug>`, from state.make_task_id/make_slug)
    already satisfies that, but this stays defensive rather than assuming it —
    task_id is read from task.json, which is repo-local but not code the way this
    module is.
    """
    name = _COMPOSE_NAME_RE.sub("-", task_id.lower()).strip("-")
    return name or "rig-task"


def ports_state_path(root: pathlib.Path) -> pathlib.Path:
    return root / ".rig" / "ports.json"


@contextlib.contextmanager
def _ports_lock(root: pathlib.Path):
    """Mutual exclusion around `.rig/ports.json` (mirrors state.task_lock, but for
    the one shared file rather than a per-task one). Blocking, not non-blocking:
    unlike task_lock's "someone else is already operating on this task" (a
    conflict worth surfacing), two `workbench.py new` calls racing for a port
    block are cooperating, not colliding, so the second one should just wait its
    turn instead of dying.
    """
    if fcntl is None:
        yield
        return
    lock_dir = root / ".rig" / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / "ports.lock"
    with lock_file.open("a") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


def _load_state(root: pathlib.Path) -> dict:
    return load_json(ports_state_path(root), {})


def _save_state(root: pathlib.Path, state: dict) -> None:
    save_json(ports_state_path(root), state)


def _prune_stale(root: pathlib.Path, state: dict) -> dict:
    """Drop entries for tasks whose run directory no longer exists.

    Reservations are only meant to outlive the worktree they were made for;
    `discard` releases them explicitly (the normal path), but a worktree can also
    disappear by hand (`git worktree remove`, a wiped `.rig/`) without going
    through it. Without this, those ports stay reserved forever and the range
    slowly fills up over a long-lived repo.
    """
    base = runs_dir(root)
    live = {task_id: ports for task_id, ports in state.items() if (base / task_id).is_dir()}
    return live


def _is_bindable(port: int) -> bool:
    """Is this TCP port free to bind right now, on every interface?

    Bound to `""` (INADDR_ANY / 0.0.0.0) rather than `127.0.0.1`: that's the
    address Docker's own default port publishing binds, and on Linux a wildcard
    bind and a same-port loopback bind conflict with each other anyway, so
    checking the wildcard directly is both the more accurate probe and the
    simpler one. IPv6 is deliberately not probed here — a host with IPv6
    disabled at the kernel/namespace level (containers commonly are) would make
    `socket.AF_INET6` itself fail, which is a property of the host, not of the
    port, and treating it as "not bindable" would exclude every port in the
    range and leave `allocate_ports` unable to find any.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", port))
    except OSError:
        return False
    return True


def allocate_ports(
    root: pathlib.Path,
    task_id: str,
    count: int = DEFAULT_PORT_COUNT,
    *,
    start: int = PORT_RANGE_START,
    end: int = PORT_RANGE_END,
) -> list[int]:
    """Reserve `count` currently-free host ports for `task_id`, recorded so no other
    task (including one registered concurrently) is handed the same ones."""
    with _ports_lock(root):
        state = _prune_stale(root, _load_state(root))
        claimed = {p for ports in state.values() for p in ports}
        chosen: list[int] = []
        for port in range(start, end + 1):
            if port in claimed:
                continue
            if _is_bindable(port):
                chosen.append(port)
                claimed.add(port)
                if len(chosen) == count:
                    break
        if len(chosen) < count:
            warn(f"only found {len(chosen)}/{count} free ports in {start}-{end} for '{task_id}' "
                 "(the range is nearly exhausted — discard old tasks to free reservations)")
        state[task_id] = chosen
        _save_state(root, state)
        return chosen


def release_ports(root: pathlib.Path, task_id: str) -> list[int]:
    """Drop `task_id`'s port reservation (called on discard). Best-effort: a task
    that never had ports allocated (--no-worktree runs, or a corrupted state file)
    is a no-op, not an error — this is bookkeeping for a convenience, not a safety
    property discard depends on."""
    try:
        with _ports_lock(root):
            state = _load_state(root)
            released = state.pop(task_id, [])
            if released:
                _save_state(root, state)
            return released
    except Exception:
        return []


def write_env_file(worktree: pathlib.Path, task_id: str, ports: list[int]) -> pathlib.Path:
    """Write `.env.rig` at the worktree root: `COMPOSE_PROJECT_NAME` plus the
    reserved ports as `RIG_PORT_0`.. `RIG_PORT_<N-1>` (and `RIG_PORT_BASE` as an
    alias for the first one, for projects that only need a single offset). Docker
    Compose reads a file named `.env` automatically but never `.env.rig` — that's
    deliberate, so this never shadows a project's own `.env`; a project opts in
    with `docker compose --env-file .env.rig ...` or an `env_file:` entry, and
    references the ports in its own compose file via
    `${RIG_PORT_0:-<its normal default>}`.
    """
    lines = [
        "# Generated by `rig workbench new` — unique per task, so parallel /rig runs",
        "# never collide on Docker container/network names or host ports.",
        "# See skills/engine/patterns/isolated-worktree.md.",
        f"COMPOSE_PROJECT_NAME={compose_project_name(task_id)}",
        f"RIG_TASK_ID={task_id}",
    ]
    if ports:
        lines.append(f"RIG_PORT_BASE={ports[0]}")
        lines.extend(f"RIG_PORT_{i}={port}" for i, port in enumerate(ports))
    path = worktree / ENV_FILENAME
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def ensure_env_rig_excluded(root: pathlib.Path) -> None:
    """Make sure `.env.rig` is locally ignored everywhere in this repo — the main
    tree and every linked worktree — without touching the project's own tracked
    `.gitignore`.

    `.env.rig` is written *inside* each task's linked worktree, the same tree
    `accept` later squash-merges into the main branch. Left untracked it would
    fail accept's "worktree must be clean" precondition on every single task
    (an untracked file is exactly what `git status --porcelain` flags as dirty);
    left tracked it would leak a per-task port reservation into the project's
    history on the very first accept. `$GIT_DIR/info/exclude` is the right tool
    for this, not `.gitignore`: it is local-only (never committed, never shows
    up in a diff or a PR) and `info/exclude` resolves to the *common* git dir —
    linked worktrees share it rather than getting their own — so writing it once
    from the main tree covers every worktree this repo has, present and future,
    with no tracked file touched at all.
    """
    common = git(["rev-parse", "--git-common-dir"], cwd=root, check=False)
    if common.returncode != 0 or not common.stdout.strip():
        return
    common_dir = pathlib.Path(common.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = root / common_dir
    exclude_path = common_dir / "info" / "exclude"
    existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
    if any(line.strip() == ENV_FILENAME for line in existing.splitlines()):
        return
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    with exclude_path.open("a", encoding="utf-8") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write(f"{ENV_FILENAME}\n")


def docker_isolation(root: pathlib.Path, worktree: pathlib.Path, task_id: str,
                      count: int = DEFAULT_PORT_COUNT) -> dict:
    """Allocate + write in one step; the shape `cmd_new` stores under `task["docker"]`."""
    ensure_env_rig_excluded(root)
    ports = allocate_ports(root, task_id, count)
    env_path = write_env_file(worktree, task_id, ports)
    return {
        "compose_project": compose_project_name(task_id),
        "ports": ports,
        "env_file": env_path.name,
        "allocated_at": now_iso(),
    }
