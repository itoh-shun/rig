"""Signed provenance via HMAC-SHA256 on accept (#299).

accept() writes .rig/runs/<task_id>/provenance.json (a signed record of what was
accepted and the gate result it was based on); `workbench.py verify-provenance
<task_id>` checks the signature and exits 1 on mismatch or tamper.
"""

import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench.state import sign_provenance, verify_provenance

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


# ---- sign_provenance / verify_provenance (pure functions) --------------------

def test_valid_signature_verifies(tmp_path):
    record = {"task_id": "rig-1", "gate_status": "passed"}
    sig = sign_provenance(tmp_path, record)
    assert verify_provenance(tmp_path, record, sig) is True


def test_tampered_record_fails_verification(tmp_path):
    record = {"task_id": "rig-1", "gate_status": "passed"}
    sig = sign_provenance(tmp_path, record)
    tampered = {"task_id": "rig-1", "gate_status": "failed"}
    assert verify_provenance(tmp_path, tampered, sig) is False


def test_key_is_persisted_and_reused(tmp_path):
    record = {"task_id": "rig-1"}
    sig = sign_provenance(tmp_path, record)
    assert (tmp_path / ".rig" / "provenance.key").is_file()
    # A fresh call against the same root must reuse the persisted key, not mint a new one.
    assert verify_provenance(tmp_path, record, sig) is True


# ---- end-to-end via workbench.py accept / verify-provenance ------------------

def _make_acceptable_task(git_repo, task_id):
    d = git_repo / ".rig" / "runs" / task_id
    acc = json.loads((d / "acceptance.json").read_text(encoding="utf-8"))
    for c in acc["checks"]:
        c["status"] = "passed" if c["name"] in ("no_unrelated_diff",) else "skipped"
    (d / "acceptance.json").write_text(json.dumps(acc), encoding="utf-8")
    (d / "diff.md").write_text("## Summary\nx\n", encoding="utf-8")


def _commit_gitignore(git_repo):
    """`new` appends .rig/ to .gitignore but doesn't commit it; accept requires a
    clean root working tree, so tests that run a real accept must commit it first."""
    subprocess.run(["git", "add", "-A", "--", ".gitignore"], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "gitignore .rig/"], cwd=git_repo, check=True)


def test_accept_writes_provenance_and_verify_passes(git_repo):
    run_cli(["new", "test task", "--type", "feature"], git_repo)
    _commit_gitignore(git_repo)
    task_id = next((git_repo / ".rig" / "runs").iterdir()).name
    _make_acceptable_task(git_repo, task_id)

    task = json.loads((git_repo / ".rig" / "runs" / task_id / "task.json").read_text(encoding="utf-8"))
    wt = pathlib.Path(task["worktree_path"])
    (wt / "g.txt").write_text("change\n", encoding="utf-8")
    subprocess.run(["git", "add", "g.txt"], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "work"], cwd=wt, check=True)

    r = run_cli(["accept", task_id], git_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Provenance:" in r.stdout

    prov = json.loads((git_repo / ".rig" / "runs" / task_id / "provenance.json").read_text(encoding="utf-8"))
    assert prov["algo"] == "HMAC-SHA256"
    assert prov["record"]["task_id"] == task_id
    assert prov["record"]["gate_status"] in ("passed", "passed_with_warnings", "skipped")

    r = run_cli(["verify-provenance", task_id], git_repo)
    assert r.returncode == 0
    assert "✓ valid" in r.stdout


def test_verify_provenance_detects_tampering(git_repo):
    run_cli(["new", "test task", "--type", "feature"], git_repo)
    _commit_gitignore(git_repo)
    task_id = next((git_repo / ".rig" / "runs").iterdir()).name
    _make_acceptable_task(git_repo, task_id)

    task = json.loads((git_repo / ".rig" / "runs" / task_id / "task.json").read_text(encoding="utf-8"))
    wt = pathlib.Path(task["worktree_path"])
    (wt / "g.txt").write_text("change\n", encoding="utf-8")
    subprocess.run(["git", "add", "g.txt"], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "work"], cwd=wt, check=True)
    run_cli(["accept", task_id], git_repo)

    prov_path = git_repo / ".rig" / "runs" / task_id / "provenance.json"
    prov = json.loads(prov_path.read_text(encoding="utf-8"))
    prov["record"]["gate_status"] = "failed"  # tamper after the fact
    prov_path.write_text(json.dumps(prov), encoding="utf-8")

    r = run_cli(["verify-provenance", task_id], git_repo)
    assert r.returncode != 0
    assert "INVALID" in r.stdout


def test_verify_provenance_before_accept_errors(git_repo):
    run_cli(["new", "test task", "--type", "feature", "--no-worktree"], git_repo)
    task_id = next((git_repo / ".rig" / "runs").iterdir()).name
    r = run_cli(["verify-provenance", task_id], git_repo)
    assert r.returncode != 0
    assert "no provenance record" in (r.stdout + r.stderr)
