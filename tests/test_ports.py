"""Docker/port isolation for parallel `/rig` runs (ports.py).

Two isolated worktrees already can't step on each other's files
(patterns/isolated-worktree); these tests cover the two axes a worktree alone
doesn't isolate — host ports and `COMPOSE_PROJECT_NAME` — plus the mechanics
that make `.env.rig` safe to drop inside a tracked worktree without breaking
`accept`'s "worktree must be clean" precondition.
"""

import concurrent.futures
import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench import ports

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"


def run_cli(args, cwd):
    return subprocess.run([sys.executable, str(WORKBENCH), *args],
                          capture_output=True, text=True, cwd=cwd, timeout=60)


@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def _mark_run_exists(root: pathlib.Path, task_id: str) -> None:
    (root / ".rig" / "runs" / task_id).mkdir(parents=True, exist_ok=True)


# ---- compose_project_name ----------------------------------------------------

def test_compose_project_name_passes_through_a_valid_task_id():
    assert ports.compose_project_name("rig-20260813-153012-login-fix") == \
        "rig-20260813-153012-login-fix"


def test_compose_project_name_sanitizes_and_lowercases():
    assert ports.compose_project_name("Rig Task@123") == "rig-task-123"


def test_compose_project_name_never_returns_empty():
    assert ports.compose_project_name("!!!") == "rig-task"


# ---- allocate_ports / release_ports -------------------------------------------

def test_allocate_ports_returns_the_requested_count(tmp_path):
    _mark_run_exists(tmp_path, "task-a")
    got = ports.allocate_ports(tmp_path, "task-a", count=5)
    assert len(got) == 5
    assert len(set(got)) == 5
    assert all(ports.PORT_RANGE_START <= p <= ports.PORT_RANGE_END for p in got)


def test_two_tasks_get_disjoint_port_blocks(tmp_path):
    _mark_run_exists(tmp_path, "task-a")
    _mark_run_exists(tmp_path, "task-b")
    a = ports.allocate_ports(tmp_path, "task-a", count=4)
    b = ports.allocate_ports(tmp_path, "task-b", count=4)
    assert set(a).isdisjoint(b)


def test_allocation_is_recorded_in_ports_json(tmp_path):
    _mark_run_exists(tmp_path, "task-a")
    got = ports.allocate_ports(tmp_path, "task-a", count=3)
    state = json.loads(ports.ports_state_path(tmp_path).read_text(encoding="utf-8"))
    assert state["task-a"] == got


def test_release_ports_removes_the_reservation(tmp_path):
    _mark_run_exists(tmp_path, "task-a")
    ports.allocate_ports(tmp_path, "task-a", count=3)
    released = ports.release_ports(tmp_path, "task-a")
    assert len(released) == 3
    state = json.loads(ports.ports_state_path(tmp_path).read_text(encoding="utf-8"))
    assert "task-a" not in state


def test_release_ports_on_unknown_task_is_a_noop(tmp_path):
    assert ports.release_ports(tmp_path, "never-allocated") == []


def test_released_ports_can_be_reallocated_to_a_new_task(tmp_path):
    _mark_run_exists(tmp_path, "task-a")
    first = ports.allocate_ports(tmp_path, "task-a", count=4)
    ports.release_ports(tmp_path, "task-a")
    _mark_run_exists(tmp_path, "task-b")
    second = ports.allocate_ports(tmp_path, "task-b", count=4)
    # Freed ports are eligible again — the two blocks may legitimately overlap
    # now that task-a's reservation is gone.
    assert set(second) & set(first)


def test_stale_reservation_without_a_run_dir_is_pruned(tmp_path):
    # task-a never gets a .rig/runs/task-a dir (simulates a worktree torn down
    # by hand, bypassing `discard`) — its reservation must not survive forever.
    ports.allocate_ports(tmp_path, "task-a", count=4)
    _mark_run_exists(tmp_path, "task-b")
    ports.allocate_ports(tmp_path, "task-b", count=4)
    state = json.loads(ports.ports_state_path(tmp_path).read_text(encoding="utf-8"))
    assert "task-a" not in state
    assert "task-b" in state


def test_concurrent_allocations_never_collide(tmp_path):
    task_ids = [f"task-{i}" for i in range(8)]
    for tid in task_ids:
        _mark_run_exists(tmp_path, tid)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda tid: ports.allocate_ports(tmp_path, tid, count=4), task_ids))
    seen: set[int] = set()
    for block in results:
        assert set(block).isdisjoint(seen), "two concurrent tasks were handed the same port"
        seen |= set(block)


# ---- write_env_file -----------------------------------------------------------

def test_write_env_file_content(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    path = ports.write_env_file(wt, "rig-20260813-000000-x", [21000, 21001, 21002])
    text = path.read_text(encoding="utf-8")
    assert path == wt / ".env.rig"
    assert "COMPOSE_PROJECT_NAME=rig-20260813-000000-x" in text
    assert "RIG_TASK_ID=rig-20260813-000000-x" in text
    assert "RIG_PORT_BASE=21000" in text
    assert "RIG_PORT_0=21000" in text
    assert "RIG_PORT_1=21001" in text
    assert "RIG_PORT_2=21002" in text


def test_write_env_file_with_no_ports_still_writes_project_name(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    path = ports.write_env_file(wt, "rig-x", [])
    text = path.read_text(encoding="utf-8")
    assert "COMPOSE_PROJECT_NAME=rig-x" in text
    assert "RIG_PORT_BASE" not in text


# ---- ensure_env_rig_excluded ---------------------------------------------------

def test_env_rig_is_excluded_in_every_worktree_not_just_the_main_tree(git_repo):
    ports.ensure_env_rig_excluded(git_repo)
    wt = git_repo.parent / "wt1"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "wt1", str(wt), "HEAD"],
                   cwd=git_repo, check=True)
    (wt / ".env.rig").write_text("x\n", encoding="utf-8")
    status = subprocess.run(["git", "status", "--porcelain"], cwd=wt,
                            capture_output=True, text=True, check=True).stdout
    assert status.strip() == ""


def test_ensure_env_rig_excluded_is_idempotent(git_repo):
    ports.ensure_env_rig_excluded(git_repo)
    ports.ensure_env_rig_excluded(git_repo)
    exclude = (git_repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert exclude.count(".env.rig") == 1


# ---- end-to-end through the CLI ------------------------------------------------

def test_new_writes_env_rig_into_the_worktree_and_leaves_it_clean(git_repo):
    r = run_cli(["new", "add a widget", "--type", "feature"], git_repo)
    assert r.returncode == 0, r.stderr
    task_id = next((git_repo / ".rig" / "runs").iterdir()).name
    task = json.loads((git_repo / ".rig" / "runs" / task_id / "task.json").read_text(encoding="utf-8"))
    assert task["docker"]["compose_project"] == task_id
    assert len(task["docker"]["ports"]) == ports.DEFAULT_PORT_COUNT

    wt = pathlib.Path(task["worktree_path"])
    env_file = wt / ".env.rig"
    assert env_file.is_file()
    assert f"COMPOSE_PROJECT_NAME={task_id}" in env_file.read_text(encoding="utf-8")

    # The critical property: an untracked .env.rig must not make the worktree
    # read as dirty, or accept's "worktree must be clean" check would fail on
    # every single task.
    status = subprocess.run(["git", "status", "--porcelain"], cwd=wt,
                            capture_output=True, text=True, check=True).stdout
    assert status.strip() == ""


def test_discard_releases_the_task_ports(git_repo):
    run_cli(["new", "add a widget", "--type", "feature"], git_repo)
    task_id = next((git_repo / ".rig" / "runs").iterdir()).name
    state_before = json.loads((git_repo / ".rig" / "ports.json").read_text(encoding="utf-8"))
    assert task_id in state_before

    r = run_cli(["discard", task_id, "--yes"], git_repo)
    assert r.returncode == 0, r.stderr
    assert "Released reserved ports:" in r.stdout

    state_after = json.loads((git_repo / ".rig" / "ports.json").read_text(encoding="utf-8"))
    assert task_id not in state_after


def test_no_worktree_task_gets_no_docker_isolation(git_repo):
    r = run_cli(["new", "read only review", "--type", "review", "--no-worktree"], git_repo)
    assert r.returncode == 0, r.stderr
    task_id = next((git_repo / ".rig" / "runs").iterdir()).name
    task = json.loads((git_repo / ".rig" / "runs" / task_id / "task.json").read_text(encoding="utf-8"))
    assert task["docker"] is None
    assert not (git_repo / ".rig" / "ports.json").exists()
