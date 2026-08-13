"""Dependency-acceptance-gate sensor backing `no_unvetted_dependency_update` (opt-in).

Covers: manifest/lockfile parsing (npm package-lock.json v1/v2/v3, requirements.txt,
Cargo.lock), diff-scoped new/bumped dependency detection (committed changes and
untracked new manifests), the install_script/fresh_release/known_vulnerability/
known_malicious_package grading (network signals mocked — never hit the real
network in a test), the criterion's absence from every default preset (opt-in,
same convention as evidence_anchors_resolve), the reset-to-pending and explicit
`--set no_unvetted_dependency_update=passed` escape hatch, RIG_DEP_GATE_OFFLINE=1
skipping network signals outright, and the gate integration in a scratch repo
through the CLI (offline, so it's deterministic).
"""

import datetime as dt
import json
import os
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench.config import GATE_PRESETS, TASK_TYPES
from rig_workbench.workbench.dependency_gate import (
    SENSOR_CRITERION,
    DependencyChange,
    apply_dependency_sensor,
    changed_dependencies,
    evaluate_change,
    manifest_ecosystem,
    parse_manifest,
)
from rig_workbench.workbench.state import build_acceptance

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"


def run_cli(args, cwd, env=None):
    full_env = dict(os.environ, **(env or {}))
    return subprocess.run([sys.executable, str(WORKBENCH), *args], check=False,
                          capture_output=True, text=True, cwd=cwd, timeout=60, env=full_env)


def npm_lock_v3(packages: dict) -> str:
    return json.dumps({"lockfileVersion": 3,
                       "packages": {"": {"name": "proj", "version": "0.0.0"}, **packages}})


# ---- manifest_ecosystem --------------------------------------------------------

def test_manifest_ecosystem_matches_known_basenames():
    assert manifest_ecosystem("package-lock.json") == "npm"
    assert manifest_ecosystem("Cargo.lock") == "cargo"
    assert manifest_ecosystem("requirements.txt") == "pip"
    assert manifest_ecosystem("requirements-dev.txt") == "pip"
    assert manifest_ecosystem("package.json") is None
    assert manifest_ecosystem("random.txt") is None


# ---- parsing --------------------------------------------------------------------

def test_parse_npm_lock_v3_excludes_root_and_reads_install_script():
    text = npm_lock_v3({
        "node_modules/lodash": {"version": "4.17.21"},
        "node_modules/left-pad": {"version": "1.3.0", "hasInstallScript": True},
    })
    out = parse_manifest("npm", text)
    assert out[("lodash", "4.17.21")] is False
    assert out[("left-pad", "1.3.0")] is True
    assert ("proj", "0.0.0") not in out


def test_parse_npm_lock_v1_has_no_install_script_field():
    text = json.dumps({"dependencies": {
        "lodash": {"version": "4.17.21", "dependencies": {
            "nested": {"version": "2.0.0"},
        }},
    }})
    out = parse_manifest("npm", text)
    assert out[("lodash", "4.17.21")] is None
    assert out[("nested", "2.0.0")] is None


def test_parse_npm_lock_invalid_json_is_empty():
    assert parse_manifest("npm", "{not json") == {}


def test_parse_requirements_only_exact_pins():
    text = "requests==2.31.0\n# a comment\nnumpy>=1.20\n\nflask == 3.0.0  # inline comment\n"
    out = parse_manifest("pip", text)
    assert out == {("requests", "2.31.0"): None, ("flask", "3.0.0"): None}


def test_parse_cargo_lock():
    text = (
        '[[package]]\nname = "serde"\nversion = "1.0.200"\nsource = "registry+..."\n\n'
        '[[package]]\nname = "libc"\nversion = "0.2.150"\n'
    )
    out = parse_manifest("cargo", text)
    assert out == {("serde", "1.0.200"): None, ("libc", "0.2.150"): None}


# ---- diff-scoped change detection ------------------------------------------------

def _git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   cwd=repo, check=True, capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path
    _git(repo, "init", "-q")
    (repo / "package-lock.json").write_text(npm_lock_v3({
        "node_modules/lodash": {"version": "4.17.20"},
    }), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                         capture_output=True, text=True).stdout.strip()
    return repo, sha


def test_changed_dependencies_detects_version_bump_and_new_package(git_repo):
    repo, sha = git_repo
    (repo / "package-lock.json").write_text(npm_lock_v3({
        "node_modules/lodash": {"version": "4.17.21"},  # bumped
        "node_modules/left-pad": {"version": "1.3.0", "hasInstallScript": True},  # new
    }), encoding="utf-8")
    changes = {(c.name, c.version): c for c in changed_dependencies(repo, sha)}
    assert changes[("lodash", "4.17.21")].is_new is False
    assert changes[("left-pad", "1.3.0")].is_new is True
    assert changes[("left-pad", "1.3.0")].has_install_script is True


def test_changed_dependencies_ignores_untouched_package(git_repo):
    repo, sha = git_repo
    (repo / "package-lock.json").write_text(npm_lock_v3({
        "node_modules/lodash": {"version": "4.17.20"},  # unchanged
    }), encoding="utf-8")
    assert changed_dependencies(repo, sha) == []


def test_changed_dependencies_sees_a_brand_new_untracked_manifest(git_repo):
    repo, sha = git_repo
    (repo / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")
    changes = changed_dependencies(repo, sha)
    assert any(c.name == "requests" and c.version == "2.31.0" and c.is_new for c in changes)


# ---- grading (network mocked) ----------------------------------------------------

NOW = dt.datetime(2026, 8, 13, tzinfo=dt.timezone.utc)


def _change(**overrides):
    base = {"path": "package-lock.json", "ecosystem": "npm", "name": "left-pad",
            "version": "1.3.0", "is_new": True, "has_install_script": False}
    base.update(overrides)
    return DependencyChange(**base)


def test_install_script_is_warning_grade_and_needs_no_network(monkeypatch):
    monkeypatch.setattr("rig_workbench.workbench.dependency_gate.release_published_at",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network called")))
    monkeypatch.setattr("rig_workbench.workbench.dependency_gate.osv_advisories",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network called")))
    change = _change(has_install_script=True)
    findings = evaluate_change(change, now=NOW, offline=True)
    assert [(f["kind"], f["grade"]) for f in findings] == [("install_script", "warning")]


def test_offline_skips_network_signals_entirely(monkeypatch):
    called = []
    monkeypatch.setattr("rig_workbench.workbench.dependency_gate.release_published_at",
                        lambda *a, **k: called.append("published") or NOW)
    monkeypatch.setattr("rig_workbench.workbench.dependency_gate.osv_advisories",
                        lambda *a, **k: called.append("osv") or [])
    evaluate_change(_change(), now=NOW, offline=True)
    assert called == []


def test_fresh_release_is_warning_grade(monkeypatch):
    monkeypatch.setattr("rig_workbench.workbench.dependency_gate.release_published_at",
                        lambda *a, **k: NOW - dt.timedelta(hours=1))
    monkeypatch.setattr("rig_workbench.workbench.dependency_gate.osv_advisories",
                        lambda *a, **k: [])
    findings = evaluate_change(_change(), now=NOW, offline=False)
    assert [(f["kind"], f["grade"]) for f in findings] == [("fresh_release", "warning")]


def test_release_outside_cooldown_is_not_flagged(monkeypatch):
    monkeypatch.setattr("rig_workbench.workbench.dependency_gate.release_published_at",
                        lambda *a, **k: NOW - dt.timedelta(days=30))
    monkeypatch.setattr("rig_workbench.workbench.dependency_gate.osv_advisories",
                        lambda *a, **k: [])
    assert evaluate_change(_change(), now=NOW, offline=False) == []


def test_known_vulnerability_is_warning_grade(monkeypatch):
    monkeypatch.setattr("rig_workbench.workbench.dependency_gate.release_published_at",
                        lambda *a, **k: None)
    monkeypatch.setattr("rig_workbench.workbench.dependency_gate.osv_advisories",
                        lambda *a, **k: [{"id": "GHSA-xxxx-yyyy", "summary": "prototype pollution"}])
    findings = evaluate_change(_change(), now=NOW, offline=False)
    assert [(f["kind"], f["grade"]) for f in findings] == [("known_vulnerability", "warning")]


def test_malicious_package_advisory_is_fail_grade(monkeypatch):
    monkeypatch.setattr("rig_workbench.workbench.dependency_gate.release_published_at",
                        lambda *a, **k: None)
    monkeypatch.setattr("rig_workbench.workbench.dependency_gate.osv_advisories",
                        lambda *a, **k: [{"id": "MAL-2024-1234", "summary": "malicious code"}])
    findings = evaluate_change(_change(), now=NOW, offline=False)
    assert [(f["kind"], f["grade"]) for f in findings] == [("known_malicious_package", "fail")]


def test_malicious_alias_also_counts(monkeypatch):
    monkeypatch.setattr("rig_workbench.workbench.dependency_gate.release_published_at",
                        lambda *a, **k: None)
    monkeypatch.setattr("rig_workbench.workbench.dependency_gate.osv_advisories",
                        lambda *a, **k: [{"id": "GHSA-abcd", "aliases": ["MAL-2024-9999"]}])
    findings = evaluate_change(_change(), now=NOW, offline=False)
    assert findings[0]["kind"] == "known_malicious_package"


# ---- sensor wiring ---------------------------------------------------------------

def make_state(repo, sha, criterion=SENSOR_CRITERION):
    task = {"worktree_path": str(repo), "base_commit": sha, "task_type": "feature"}
    acc = {"checks": [{"name": criterion, "status": "pending", "detail": ""}]}
    return task, acc


def test_criterion_is_absent_from_every_default_preset_and_gate():
    for preset, criteria in GATE_PRESETS.items():
        assert SENSOR_CRITERION not in criteria, preset
    for task_type in TASK_TYPES:
        names = [c["name"] for c in build_acceptance("t", task_type)["checks"]]
        assert SENSOR_CRITERION not in names, task_type


def test_project_gates_extra_criteria_activates_the_criterion(tmp_path):
    (tmp_path / ".rig").mkdir()
    (tmp_path / ".rig" / "gates.json").write_text(
        json.dumps({"extra_criteria": {"standard": [SENSOR_CRITERION]}}), encoding="utf-8")
    acc = build_acceptance("t", "feature", tmp_path)
    check = next(c for c in acc["checks"] if c["name"] == SENSOR_CRITERION)
    assert check["status"] == "pending"
    assert check["origin"] == "project"


def test_a_gate_without_the_criterion_is_a_silent_noop(git_repo):
    repo, sha = git_repo
    (repo / "package-lock.json").write_text(npm_lock_v3({
        "node_modules/left-pad": {"version": "1.3.0", "hasInstallScript": True},
    }), encoding="utf-8")
    task, acc = make_state(repo, sha, criterion="tests_pass_or_explained")
    assert apply_dependency_sensor(repo, repo, task, acc) == []
    assert acc["checks"][0]["status"] == "pending"


def test_sensor_sets_warning_on_install_script(monkeypatch, git_repo):
    repo, sha = git_repo
    monkeypatch.setenv("RIG_DEP_GATE_OFFLINE", "1")
    (repo / "package-lock.json").write_text(npm_lock_v3({
        "node_modules/left-pad": {"version": "1.3.0", "hasInstallScript": True},
    }), encoding="utf-8")
    task, acc = make_state(repo, sha)
    notes = apply_dependency_sensor(repo, repo, task, acc)
    check = acc["checks"][0]
    assert check["status"] == "warning"
    assert any("install_script" in ln for ln in check["dependency_findings"])
    assert any(f"{SENSOR_CRITERION} recorded as warning" in n for n in notes)


def test_sensor_fails_on_malicious_package(monkeypatch, git_repo):
    repo, sha = git_repo
    monkeypatch.setattr("rig_workbench.workbench.dependency_gate.release_published_at",
                        lambda *a, **k: None)
    monkeypatch.setattr("rig_workbench.workbench.dependency_gate.osv_advisories",
                        lambda *a, **k: [{"id": "MAL-2024-0001"}])
    (repo / "package-lock.json").write_text(npm_lock_v3({
        "node_modules/left-pad": {"version": "1.3.0"},
    }), encoding="utf-8")
    task, acc = make_state(repo, sha)
    notes = apply_dependency_sensor(repo, repo, task, acc)
    check = acc["checks"][0]
    assert check["status"] == "failed"
    assert any("known_malicious_package" in ln for ln in check["dependency_findings"])
    assert any(f"{SENSOR_CRITERION} failed" in n for n in notes)


def test_sensor_resets_to_pending_when_dependency_change_disappears(monkeypatch, git_repo):
    repo, sha = git_repo
    monkeypatch.setenv("RIG_DEP_GATE_OFFLINE", "1")
    (repo / "package-lock.json").write_text(npm_lock_v3({
        "node_modules/left-pad": {"version": "1.3.0", "hasInstallScript": True},
    }), encoding="utf-8")
    task, acc = make_state(repo, sha)
    apply_dependency_sensor(repo, repo, task, acc)
    assert acc["checks"][0]["status"] == "warning"

    (repo / "package-lock.json").write_text(npm_lock_v3({
        "node_modules/lodash": {"version": "4.17.20"},
    }), encoding="utf-8")
    notes = apply_dependency_sensor(repo, repo, task, acc)
    assert acc["checks"][0]["status"] == "pending"
    assert "dependency_findings" not in acc["checks"][0]
    assert any("reset to pending" in n for n in notes)


def test_explicit_pass_is_recorded_and_sticks(monkeypatch, git_repo):
    repo, sha = git_repo
    monkeypatch.setenv("RIG_DEP_GATE_OFFLINE", "1")
    (repo / "package-lock.json").write_text(npm_lock_v3({
        "node_modules/left-pad": {"version": "1.3.0", "hasInstallScript": True},
    }), encoding="utf-8")
    task, acc = make_state(repo, sha)
    acc["checks"][0]["status"] = "passed"
    notes = apply_dependency_sensor(repo, repo, task, acc, explicit_set={SENSOR_CRITERION})
    assert acc["checks"][0]["status"] == "passed"
    assert acc["checks"][0]["dependency_override"] is True
    assert any("manual override recorded" in n for n in notes)
    notes = apply_dependency_sensor(repo, repo, task, acc)
    assert acc["checks"][0]["status"] == "passed"
    assert any("manual override previously recorded" in n for n in notes)


# ---- end-to-end through cmd_gate (scratch repo, offline, real CLI) --------------

def _new_task(repo, env):
    r = run_cli(["new", "bump a dependency", "--type", "feature"], repo, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    task_id = next((repo / ".rig" / "runs").iterdir()).name
    task = json.loads((repo / ".rig" / "runs" / task_id / "task.json").read_text(encoding="utf-8"))
    return task_id, pathlib.Path(task["worktree_path"])


def test_gate_integration_opt_in_criterion_warns_offline(git_repo):
    repo, _sha = git_repo
    (repo / ".rig").mkdir(exist_ok=True)
    (repo / ".rig" / "gates.json").write_text(
        json.dumps({"extra_criteria": {"standard": [SENSOR_CRITERION]}}), encoding="utf-8")
    env = {"RIG_DEP_GATE_OFFLINE": "1"}
    task_id, wt = _new_task(repo, env)
    (wt / "package-lock.json").write_text(npm_lock_v3({
        "node_modules/left-pad": {"version": "1.3.0", "hasInstallScript": True},
    }), encoding="utf-8")

    r = run_cli(["gate", task_id, "--set", "task_intent_satisfied=passed"], repo, env=env)
    assert r.returncode == 0, r.stdout + r.stderr  # warning-grade only, gate does not fail
    assert SENSOR_CRITERION in r.stdout
    acc = json.loads((repo / ".rig" / "runs" / task_id / "acceptance.json").read_text(encoding="utf-8"))
    check = next(c for c in acc["checks"] if c["name"] == SENSOR_CRITERION)
    assert check["status"] == "warning"
    assert any("install_script" in ln for ln in check["dependency_findings"])


def test_gate_integration_default_repo_never_sees_the_criterion(git_repo):
    repo, _sha = git_repo
    env = {"RIG_DEP_GATE_OFFLINE": "1"}
    task_id, wt = _new_task(repo, env)
    (wt / "package-lock.json").write_text(npm_lock_v3({
        "node_modules/left-pad": {"version": "1.3.0", "hasInstallScript": True},
    }), encoding="utf-8")

    r = run_cli(["gate", task_id, "--set", "task_intent_satisfied=passed"], repo, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert SENSOR_CRITERION not in r.stdout
    acc = json.loads((repo / ".rig" / "runs" / task_id / "acceptance.json").read_text(encoding="utf-8"))
    assert SENSOR_CRITERION not in [c["name"] for c in acc["checks"]]


# ---- CLI (scan-dependencies) -----------------------------------------------------

def test_cli_scan_offline_reports_install_script(tmp_path):
    (tmp_path / "package-lock.json").write_text(npm_lock_v3({
        "node_modules/left-pad": {"version": "1.3.0", "hasInstallScript": True},
    }), encoding="utf-8")
    r = run_cli(["scan-dependencies", str(tmp_path)], tmp_path, env={"RIG_DEP_GATE_OFFLINE": "1"})
    assert r.returncode == 1
    assert "install_script/warning" in r.stdout
    assert "RIG_DEP_GATE_OFFLINE=1" in r.stdout


def test_cli_clean_scan_exits_zero(tmp_path):
    (tmp_path / "package-lock.json").write_text(npm_lock_v3({
        "node_modules/lodash": {"version": "4.17.21"},
    }), encoding="utf-8")
    r = run_cli(["scan-dependencies", str(tmp_path)], tmp_path, env={"RIG_DEP_GATE_OFFLINE": "1"})
    assert r.returncode == 0
    assert "No dependency-update signals found." in r.stdout
