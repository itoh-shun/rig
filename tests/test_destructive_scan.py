"""Destructive-command sensor backing `no_destructive_operation` (#315).

Covers: fail/warning pattern classification (including the deliberate
non-flagging of relative-path rm -rf and --force-with-lease), mass-deletion
detection, and the gate integration in a scratch repo (dangerous line in the
task diff → check fails/warns; explicit --set passed is the escape hatch).
"""

import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench.destructive import (MASS_DELETE_THRESHOLD,
                                                 scan_line)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"


def run_cli(args, cwd):
    return subprocess.run([sys.executable, str(WORKBENCH), *args],
                          capture_output=True, text=True, cwd=cwd, timeout=60)


def _kinds(line):
    return {(f["kind"], f["grade"]) for f in scan_line(line, "x.sh", 1)}


# ---- pattern classification --------------------------------------------------

def test_rm_rf_root_is_fail_grade():
    assert ("rm_root", "fail") in _kinds("rm -rf /")
    assert ("rm_root", "fail") in _kinds('rm -rf "/"')
    assert ("rm_root", "fail") in _kinds("rm -rf /*")


def test_mkfs_dd_dropdb_are_fail_grade():
    assert ("mkfs", "fail") in _kinds("mkfs.ext4 /dev/sda1")
    assert ("dd_device", "fail") in _kinds("dd if=image.iso of=/dev/sda bs=4M")
    assert ("drop_database", "fail") in _kinds("drop database production;")


def test_rm_rf_absolute_variable_home_are_warning_grade():
    assert ("rm_absolute", "warning") in _kinds("rm -rf /opt/app/releases")
    assert ("rm_variable", "warning") in _kinds('rm -rf "$BUILD_DIR"')
    assert ("rm_variable", "warning") in _kinds("rm -rf ${TMPDIR}/cache")
    assert ("rm_home", "warning") in _kinds("rm -rf ~/old-project")


def test_relative_rm_rf_is_deliberately_not_flagged():
    assert not scan_line("rm -rf build/", "Makefile", 1)
    assert not scan_line("rm -rf node_modules dist", "Makefile", 1)


def test_git_patterns_warning_grade():
    assert ("git_clean_force", "warning") in _kinds("git clean -fdx")
    assert ("git_reset_hard", "warning") in _kinds("git reset --hard origin/main")
    assert ("git_push_force", "warning") in _kinds("git push --force origin main")


def test_force_with_lease_is_not_flagged():
    assert not scan_line("git push --force-with-lease origin main", "x.sh", 1)


def test_sql_and_chmod_warning_grade():
    assert ("drop_table", "warning") in _kinds("DROP TABLE users;")
    assert ("truncate_table", "warning") in _kinds("TRUNCATE TABLE sessions;")
    assert ("chmod_777", "warning") in _kinds("chmod -R 777 /var/www")


def test_plain_prose_is_clean():
    assert not scan_line("The cleanup step removes stale entries from the cache.", "doc.md", 1)


# ---- gate integration --------------------------------------------------------

@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def _new_task(git_repo):
    r = run_cli(["new", "test task", "--type", "feature"], git_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    task_id = next((git_repo / ".rig" / "runs").iterdir()).name
    task = json.loads((git_repo / ".rig" / "runs" / task_id / "task.json").read_text(encoding="utf-8"))
    return task_id, pathlib.Path(task["worktree_path"])


def test_fail_grade_finding_fails_the_gate(git_repo):
    task_id, wt = _new_task(git_repo)
    (wt / "deploy.sh").write_text("#!/bin/sh\nrm -rf /\n", encoding="utf-8")

    r = run_cli(["gate", task_id, "--set", "task_intent_satisfied=passed"], git_repo)
    assert r.returncode != 0  # gate failed → exit 1
    assert "destructive sensor" in r.stdout
    acc = json.loads((git_repo / ".rig" / "runs" / task_id / "acceptance.json").read_text(encoding="utf-8"))
    check = next(c for c in acc["checks"] if c["name"] == "no_destructive_operation")
    assert check["status"] == "failed"
    assert any("rm_root" in ln for ln in check["destructive_findings"])


def test_warning_grade_finding_warns_not_fails(git_repo):
    task_id, wt = _new_task(git_repo)
    (wt / "ci.sh").write_text("git clean -fdx\n", encoding="utf-8")

    run_cli(["gate", task_id, "--set", "task_intent_satisfied=passed"], git_repo)
    acc = json.loads((git_repo / ".rig" / "runs" / task_id / "acceptance.json").read_text(encoding="utf-8"))
    check = next(c for c in acc["checks"] if c["name"] == "no_destructive_operation")
    assert check["status"] == "warning"


def test_explicit_pass_is_recorded_override(git_repo):
    task_id, wt = _new_task(git_repo)
    (wt / "ci.sh").write_text("git clean -fdx\n", encoding="utf-8")

    r = run_cli(["gate", task_id, "--set", "no_destructive_operation=passed"], git_repo)
    assert "manual override recorded" in r.stdout
    acc = json.loads((git_repo / ".rig" / "runs" / task_id / "acceptance.json").read_text(encoding="utf-8"))
    check = next(c for c in acc["checks"] if c["name"] == "no_destructive_operation")
    assert check["status"] == "passed"
    assert check["destructive_override"] is True

    # Override sticks across later evaluations.
    run_cli(["gate", task_id, "--set", "task_intent_satisfied=passed"], git_repo)
    acc = json.loads((git_repo / ".rig" / "runs" / task_id / "acceptance.json").read_text(encoding="utf-8"))
    check = next(c for c in acc["checks"] if c["name"] == "no_destructive_operation")
    assert check["status"] == "passed"


def test_clean_diff_resets_previous_sensor_flag(git_repo):
    task_id, wt = _new_task(git_repo)
    bad = wt / "ci.sh"
    bad.write_text("git clean -fdx\n", encoding="utf-8")
    run_cli(["gate", task_id, "--set", "task_intent_satisfied=passed"], git_repo)

    bad.unlink()
    r = run_cli(["gate", task_id, "--set", "task_intent_satisfied=passed"], git_repo)
    assert "reset to pending" in r.stdout
    acc = json.loads((git_repo / ".rig" / "runs" / task_id / "acceptance.json").read_text(encoding="utf-8"))
    check = next(c for c in acc["checks"] if c["name"] == "no_destructive_operation")
    assert check["status"] == "pending"


def test_mass_deletion_warns(git_repo):
    # Seed base with enough files, then delete them all in the worktree.
    for i in range(MASS_DELETE_THRESHOLD):
        (git_repo / f"m{i}.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=git_repo, check=True)

    task_id, wt = _new_task(git_repo)
    for i in range(MASS_DELETE_THRESHOLD):
        (wt / f"m{i}.txt").unlink()
    subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "wipe"], cwd=wt, check=True)

    run_cli(["gate", task_id, "--set", "task_intent_satisfied=passed"], git_repo)
    acc = json.loads((git_repo / ".rig" / "runs" / task_id / "acceptance.json").read_text(encoding="utf-8"))
    check = next(c for c in acc["checks"] if c["name"] == "no_destructive_operation")
    assert check["status"] == "warning"
    assert any("mass deletion" in ln for ln in check["destructive_findings"])


# ---- CLI ---------------------------------------------------------------------

def test_cli_scan_paths(tmp_path):
    (tmp_path / "bad.sh").write_text("dd if=x of=/dev/sda\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(WORKBENCH), "scan-destructive", str(tmp_path)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1
    assert "dd_device/fail" in r.stdout


def test_cli_clean_scan_exits_zero(tmp_path):
    (tmp_path / "ok.sh").write_text("echo hello\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(WORKBENCH), "scan-destructive", str(tmp_path)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    assert "No destructive patterns found." in r.stdout
