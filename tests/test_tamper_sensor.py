"""Anti-tamper sensor backing `no_gate_tampering` (reward-hacking hardening).

Covers: fail-grade detection of gate/CI-config edits in the task diff
(.rig/gates.json / .rig/recipes/ / .github/workflows/), the CI/config
task-type exemption, warning-grade test-weakening heuristics (existing test
files modified/deleted on bugfix/feature, assert-removal counts, skip
markers) with the `test` task-type exemption, clean-run no-op, the explicit
`--set no_gate_tampering=passed` escape hatch (tamper_override, sticky), the
reset-to-pending path, preset wiring, and the fail-grade gate integration in
a scratch repo through the CLI.
"""

import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

from rig_workbench.workbench.config import GATE_PRESETS
from rig_workbench.workbench.hardening import (apply_tamper_sensor,
                                               changed_files, is_test_path,
                                               scan_tampering)
from rig_workbench.workbench.state import build_acceptance

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   cwd=repo, check=True, capture_output=True, text=True)


def make_repo(tmp_path, extra_files=()):
    """Scratch repo whose base commit contains app.py, a test file, a CI
    workflow, and a recipe — the surfaces the sensor guards."""
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".rig" / "recipes").mkdir(parents=True)
    _git(repo, "init", "-q")
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "tests" / "test_app.py").write_text(
        "def test_x():\n    assert 1 + 1 == 2\n    assert True\n", encoding="utf-8")
    (repo / ".github" / "workflows" / "ci.yml").write_text(
        "name: ci\non: push\n", encoding="utf-8")
    (repo / ".rig" / "recipes" / "dev.md").write_text("# dev recipe\n", encoding="utf-8")
    for rel, content in extra_files:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                         capture_output=True, text=True).stdout.strip()
    return repo, sha


def commit(repo, msg="edit"):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)


def make_state(repo, sha, task_type="feature"):
    task = {"worktree_path": str(repo), "base_commit": sha, "task_type": task_type}
    acc = {"checks": [{"name": "no_gate_tampering", "status": "pending", "detail": ""}]}
    return task, acc


# ── preset wiring ─────────────────────────────────────────────────────────────
def test_criterion_wired_into_standard_preset_and_composed_gates():
    assert "no_gate_tampering" in GATE_PRESETS["standard"]
    for task_type in ("feature", "bugfix", "refactor", "documentation"):
        assert "no_gate_tampering" in [c["name"] for c in build_acceptance("t", task_type)["checks"]]
    # review gates produce no diff and do not include standard
    assert "no_gate_tampering" not in [c["name"] for c in build_acceptance("t", "review")["checks"]]


# ── path heuristics ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("rel,expected", [
    ("tests/test_app.py", True),
    ("src/pkg/spec/thing.js", True),
    ("src/app_test.go", True),
    ("web/button.spec.ts", True),
    ("web/button.test.tsx", True),
    ("tests/conftest.py", True),
    ("src/app.py", False),
    ("docs/testing.md", False),          # prose about tests is not a test file
    ("contest.py", False),
])
def test_is_test_path(rel, expected):
    assert is_test_path(rel) is expected


# ── fail-grade: gate/CI-config edits ──────────────────────────────────────────
def test_gates_json_edit_is_fail_grade(tmp_path):
    repo, sha = make_repo(tmp_path)
    (repo / ".rig" / "gates.json").write_text('{"extra_criteria": {}}\n', encoding="utf-8")
    commit(repo)
    findings = scan_tampering(repo, sha, "feature")
    assert [(f["grade"], f["kind"]) for f in findings] == [("fail", "gate_config_modified")]

    task, acc = make_state(repo, sha)
    notes = apply_tamper_sensor(repo, tmp_path, task, acc)
    check = acc["checks"][0]
    assert check["status"] == "failed"
    assert any(".rig/gates.json" in ln for ln in check["tamper_findings"])
    assert any("no_gate_tampering failed" in n for n in notes)


def test_recipe_and_workflow_edits_are_fail_grade(tmp_path):
    repo, sha = make_repo(tmp_path)
    (repo / ".rig" / "recipes" / "dev.md").write_text("# weakened\n", encoding="utf-8")
    (repo / ".github" / "workflows" / "ci.yml").write_text("name: ci\non: workflow_dispatch\n",
                                                           encoding="utf-8")
    commit(repo)
    kinds = {f["kind"] for f in scan_tampering(repo, sha, "bugfix")}
    assert kinds == {"recipe_modified", "ci_workflow_modified"}
    assert all(f["grade"] == "fail" for f in scan_tampering(repo, sha, "bugfix"))


def test_new_workflow_file_also_flagged(tmp_path):
    repo, sha = make_repo(tmp_path)
    (repo / ".github" / "workflows" / "auto-approve.yml").write_text("name: sneaky\n",
                                                                     encoding="utf-8")
    commit(repo)
    findings = scan_tampering(repo, sha, "feature")
    assert [f["kind"] for f in findings] == ["ci_workflow_modified"]


def test_ci_config_task_type_exemption(tmp_path):
    repo, sha = make_repo(tmp_path)
    (repo / ".github" / "workflows" / "ci.yml").write_text("name: release\n", encoding="utf-8")
    commit(repo)
    assert scan_tampering(repo, sha, "release_support") == []  # exempt: task IS about CI/config
    task, acc = make_state(repo, sha, task_type="release_support")
    assert apply_tamper_sensor(repo, tmp_path, task, acc) == []
    assert acc["checks"][0]["status"] == "pending"


# ── warning-grade: test-weakening heuristics ──────────────────────────────────
def test_test_deletion_on_bugfix_is_warning(tmp_path):
    repo, sha = make_repo(tmp_path)
    (repo / "tests" / "test_app.py").unlink()
    commit(repo)
    findings = scan_tampering(repo, sha, "bugfix")
    kinds = {f["kind"] for f in findings}
    assert "test_file_deleted" in kinds
    assert all(f["grade"] == "warning" for f in findings)

    task, acc = make_state(repo, sha, task_type="bugfix")
    apply_tamper_sensor(repo, tmp_path, task, acc)
    assert acc["checks"][0]["status"] == "warning"  # warning-grade: surfaces, never blocks


def test_test_modification_on_feature_is_warning(tmp_path):
    repo, sha = make_repo(tmp_path)
    (repo / "tests" / "test_app.py").write_text(
        "def test_x():\n    assert 1 + 1 == 2\n    assert True\n    pass\n", encoding="utf-8")
    commit(repo)
    kinds = {f["kind"] for f in scan_tampering(repo, sha, "feature")}
    assert "test_file_modified" in kinds


def test_test_task_type_exempt_from_all_test_heuristics(tmp_path):
    repo, sha = make_repo(tmp_path)
    (repo / "tests" / "test_app.py").write_text(
        "import pytest\n\n@pytest.mark.skip\ndef test_x():\n    pass\n", encoding="utf-8")
    commit(repo)
    assert scan_tampering(repo, sha, "test") == []  # test tasks legitimately rework tests


def test_test_modification_not_warned_for_refactor(tmp_path):
    # the modified/deleted heuristic is scoped to bugfix/feature by spec
    repo, sha = make_repo(tmp_path)
    (repo / "tests" / "test_app.py").write_text(
        "def test_x():\n    assert 1 + 1 == 2\n    assert True\n    pass\n", encoding="utf-8")
    commit(repo)
    kinds = {f["kind"] for f in scan_tampering(repo, sha, "refactor")}
    assert "test_file_modified" not in kinds


def test_assert_removal_reported_with_counts(tmp_path):
    repo, sha = make_repo(tmp_path)
    (repo / "tests" / "test_app.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    commit(repo)
    findings = scan_tampering(repo, sha, "refactor")
    weakened = [f for f in findings if f["kind"] == "asserts_removed"]
    assert len(weakened) == 1
    assert weakened[0]["grade"] == "warning"
    assert "2 assert/expect line(s) removed, 0 added" in weakened[0]["detail"]


def test_skip_marker_added_is_warning(tmp_path):
    repo, sha = make_repo(tmp_path)
    (repo / "tests" / "test_app.py").write_text(
        "import pytest\n\n@pytest.mark.skip(reason='later')\ndef test_x():\n"
        "    assert 1 + 1 == 2\n    assert True\n", encoding="utf-8")
    commit(repo)
    findings = [f for f in scan_tampering(repo, sha, "refactor") if f["kind"] == "skip_marker_added"]
    assert len(findings) == 1
    assert findings[0]["grade"] == "warning"
    assert "skip" in findings[0]["detail"]


def test_assert_removed_in_product_code_not_flagged(tmp_path):
    # counts are per TEST file — product-code assert changes are ordinary work
    repo, sha = make_repo(tmp_path)
    (repo / "app.py").write_text("x = 1\nassert x == 1\n", encoding="utf-8")
    commit(repo, "add assert")
    sha2 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    commit(repo, "drop assert")
    assert scan_tampering(repo, sha2, "refactor") == []


# ── clean run / uncommitted+untracked visibility ──────────────────────────────
def test_clean_diff_has_no_findings_and_sensor_noop(tmp_path):
    repo, sha = make_repo(tmp_path)
    (repo / "feature.py").write_text("def f():\n    return 42\n", encoding="utf-8")
    commit(repo)
    assert scan_tampering(repo, sha, "feature") == []
    task, acc = make_state(repo, sha)
    assert apply_tamper_sensor(repo, tmp_path, task, acc) == []
    assert acc["checks"][0]["status"] == "pending"
    assert "tamper_findings" not in acc["checks"][0]


def test_uncommitted_workflow_edit_and_untracked_workflow_are_seen(tmp_path):
    repo, sha = make_repo(tmp_path)
    (repo / ".github" / "workflows" / "ci.yml").write_text("name: edited\n", encoding="utf-8")
    (repo / ".github" / "workflows" / "new.yml").write_text("name: new\n", encoding="utf-8")
    changes = {rel: status for status, rel in changed_files(repo, sha)}
    assert changes[".github/workflows/ci.yml"] == "M"   # uncommitted edit
    assert changes[".github/workflows/new.yml"] == "A"  # untracked file
    kinds = [f["kind"] for f in scan_tampering(repo, sha, "feature")]
    assert sorted(kinds) == ["ci_workflow_modified", "ci_workflow_modified"]


# ── escape hatch / reset ──────────────────────────────────────────────────────
def test_explicit_pass_is_recorded_and_sticks(tmp_path):
    repo, sha = make_repo(tmp_path)
    (repo / ".rig" / "gates.json").write_text("{}\n", encoding="utf-8")
    commit(repo)
    task, acc = make_state(repo, sha)
    acc["checks"][0]["status"] = "passed"
    notes = apply_tamper_sensor(repo, tmp_path, task, acc, explicit_set={"no_gate_tampering"})
    assert acc["checks"][0]["status"] == "passed"
    assert acc["checks"][0]["tamper_override"] is True
    assert any("manual override" in n for n in notes)
    # ...and the override survives later evaluations without --set
    apply_tamper_sensor(repo, tmp_path, task, acc)
    assert acc["checks"][0]["status"] == "passed"


def test_sensor_resets_its_own_failure_when_tampering_reverted(tmp_path):
    repo, sha = make_repo(tmp_path)
    (repo / ".rig" / "gates.json").write_text("{}\n", encoding="utf-8")
    commit(repo)
    task, acc = make_state(repo, sha)
    apply_tamper_sensor(repo, tmp_path, task, acc)
    assert acc["checks"][0]["status"] == "failed"
    _git(repo, "revert", "-n", "HEAD")
    commit(repo, "revert tamper")
    apply_tamper_sensor(repo, tmp_path, task, acc)
    assert acc["checks"][0]["status"] == "pending"
    assert "tamper_findings" not in acc["checks"][0]


def test_sensor_noop_without_criterion_or_worktree(tmp_path):
    repo, sha = make_repo(tmp_path)
    acc = {"checks": [{"name": "tests_pass_or_explained", "status": "pending", "detail": ""}]}
    assert apply_tamper_sensor(repo, tmp_path,
                               {"worktree_path": str(repo), "base_commit": sha,
                                "task_type": "feature"}, acc) == []
    task, acc = make_state(repo, sha)
    assert apply_tamper_sensor(repo, tmp_path,
                               {"worktree_path": None, "base_commit": sha,
                                "task_type": "feature"}, acc) == []


# ── end to end through the CLI (scratch repo, real worktree) ──────────────────
def cli(repo, wt_root, *args):
    env = dict(os.environ, RIG_WORKTREE_ROOT=str(wt_root))
    return subprocess.run([sys.executable, str(WORKBENCH), *args],
                          cwd=repo, capture_output=True, text=True, timeout=60, env=env)


def test_gate_integration_gates_json_edit_fails_no_gate_tampering(tmp_path):
    repo, _sha = make_repo(tmp_path)
    wt_root = tmp_path / "wt"

    r = cli(repo, wt_root, "new", "add config", "--type", "feature", "--slug", "add-config")
    assert r.returncode == 0, r.stderr
    task_id = re.search(r"task_id: (\S+)", r.stdout).group(1)
    wt = wt_root / task_id
    assert wt.is_dir()

    (wt / ".rig" / "gates.json").write_text('{"extra_criteria": {}}\n', encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "tamper with the gate")

    r = cli(repo, wt_root, "gate", task_id)
    assert r.returncode == 1  # fail-grade: gate is FAILED
    assert "no_gate_tampering" in r.stdout
    assert "gate_config_modified" in r.stdout

    acc = json.loads((repo / ".rig" / "runs" / task_id / "acceptance.json").read_text(encoding="utf-8"))
    check = next(c for c in acc["checks"] if c["name"] == "no_gate_tampering")
    assert check["status"] == "failed"
    assert check["tamper_findings"]

    # findings surface in status rendering with the distinctive prefix
    r = cli(repo, wt_root, "status", task_id)
    assert r.returncode == 0, r.stderr
    assert "tamper: .rig/gates.json [gate_config_modified]" in r.stdout

    # documented escape hatch: explicit --set no_gate_tampering=passed after review
    r = cli(repo, wt_root, "gate", task_id, "--set", "no_gate_tampering=passed")
    assert r.returncode == 0, r.stdout + r.stderr
    acc = json.loads((repo / ".rig" / "runs" / task_id / "acceptance.json").read_text(encoding="utf-8"))
    check = next(c for c in acc["checks"] if c["name"] == "no_gate_tampering")
    assert check["status"] == "passed" and check.get("tamper_override") is True


def test_gate_integration_test_deletion_on_bugfix_is_warning_not_failed(tmp_path):
    repo, _sha = make_repo(tmp_path)
    wt_root = tmp_path / "wt"

    r = cli(repo, wt_root, "new", "fix the bug", "--type", "bugfix", "--slug", "fix-bug")
    assert r.returncode == 0, r.stderr
    task_id = re.search(r"task_id: (\S+)", r.stdout).group(1)
    wt = wt_root / task_id

    (wt / "tests" / "test_app.py").unlink()
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "delete the failing test")

    r = cli(repo, wt_root, "gate", task_id)
    assert r.returncode == 0, r.stdout + r.stderr  # warning-grade never fails the gate on its own
    assert "test_file_deleted" in r.stdout
    acc = json.loads((repo / ".rig" / "runs" / task_id / "acceptance.json").read_text(encoding="utf-8"))
    check = next(c for c in acc["checks"] if c["name"] == "no_gate_tampering")
    assert check["status"] == "warning"
    assert any("tests/test_app.py" in ln for ln in check["tamper_findings"])
