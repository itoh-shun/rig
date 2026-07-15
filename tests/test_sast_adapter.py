"""SAST/DAST adapter: Semgrep -> a single acceptance-gate criterion (#276).

Unit tests for parse_semgrep()/aggregate() (pure functions), plus a
subprocess integration test of --apply against a real task with
sast_findings_clear registered in .rig/gates.json.
"""

import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ADAPTER = REPO_ROOT / "scripts" / "sast_adapter.py"
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"

_SPEC = importlib.util.spec_from_file_location("sast_adapter", ADAPTER)
sast_adapter = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sast_adapter)


def _semgrep_result(check_id, path, line, severity, message):
    return {"check_id": check_id, "path": path, "start": {"line": line},
            "extra": {"severity": severity, "message": message}}


def test_parse_semgrep_normalizes_findings(tmp_path):
    p = tmp_path / "out.json"
    p.write_text(json.dumps({"results": [
        _semgrep_result("rule.eval", "app.py", 42, "ERROR", "eval() detected"),
    ]}), encoding="utf-8")
    findings = sast_adapter.parse_semgrep(p)
    assert findings == [{"status": "failed", "text": "rule.eval @ app.py:42: eval() detected"}]


def test_parse_semgrep_no_results_is_empty(tmp_path):
    p = tmp_path / "out.json"
    p.write_text(json.dumps({"results": []}), encoding="utf-8")
    assert sast_adapter.parse_semgrep(p) == []


def test_aggregate_zero_findings_is_passed():
    result = sast_adapter.aggregate([])
    assert result == {"name": "sast_findings_clear", "status": "passed", "detail": "0 findings", "findings": []}


def test_aggregate_takes_worst_case_severity():
    findings = [{"status": "warning", "text": "a"}, {"status": "failed", "text": "b"}]
    result = sast_adapter.aggregate(findings)
    assert result["status"] == "failed"


def test_aggregate_truncates_detail_to_top_5():
    findings = [{"status": "warning", "text": f"finding-{i}"} for i in range(8)]
    result = sast_adapter.aggregate(findings)
    assert "(and 3 more)" in result["detail"]
    assert "finding-6" not in result["detail"]  # only the first 5 are inlined


def test_main_rejects_unknown_tool(capsys):
    with pytest.raises(SystemExit) as e:
        sys.argv = ["sast_adapter.py", "bogus-tool", "x.json"]
        sast_adapter.main()
    assert e.value.code == 1
    assert "usage:" in capsys.readouterr().err


def test_main_rejects_missing_file(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        sys.argv = ["sast_adapter.py", "semgrep", str(tmp_path / "nope.json")]
        sast_adapter.main()
    assert e.value.code == 1
    assert "file not found" in capsys.readouterr().err


# ---- --apply integration against a real task ---------------------------------

def run_cli(cmd, args, cwd):
    return subprocess.run([sys.executable, str(cmd), *args],
                          capture_output=True, text=True, cwd=cwd, timeout=60)


@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    (tmp_path / ".rig").mkdir()
    (tmp_path / ".rig" / "gates.json").write_text(
        json.dumps({"extra_criteria": {"feature": ["sast_findings_clear"]}}), encoding="utf-8")
    return tmp_path


@pytest.fixture
def task_id(git_repo):
    r = run_cli(WORKBENCH, ["new", "test sast", "--type", "feature", "--no-worktree"], git_repo)
    assert r.returncode == 0
    return next((git_repo / ".rig" / "runs").iterdir()).name


def test_apply_records_failed_finding_into_acceptance_json(git_repo, task_id):
    out = git_repo / "semgrep-output.json"
    out.write_text(json.dumps({"results": [
        _semgrep_result("rule.eval", "app.py", 42, "ERROR", "eval() detected"),
    ]}), encoding="utf-8")
    r = run_cli(ADAPTER, ["semgrep", "semgrep-output.json", "--apply", task_id], git_repo)
    assert r.returncode == 0
    assert f"applied sast_findings_clear=failed to {task_id}" in r.stdout

    acc = json.loads((git_repo / ".rig" / "runs" / task_id / "acceptance.json").read_text(encoding="utf-8"))
    check = next(c for c in acc["checks"] if c["name"] == "sast_findings_clear")
    assert check["status"] == "failed"
    assert check["origin"] == "project"


def test_apply_records_passed_when_no_findings(git_repo, task_id):
    out = git_repo / "semgrep-output.json"
    out.write_text(json.dumps({"results": []}), encoding="utf-8")
    r = run_cli(ADAPTER, ["semgrep", "semgrep-output.json", "--apply", task_id], git_repo)
    assert r.returncode == 0
    acc = json.loads((git_repo / ".rig" / "runs" / task_id / "acceptance.json").read_text(encoding="utf-8"))
    check = next(c for c in acc["checks"] if c["name"] == "sast_findings_clear")
    assert check["status"] == "passed"


def test_unregistered_criterion_is_rejected_by_workbench(git_repo):
    # A task_type that never registered sast_findings_clear in gates.json.
    r = run_cli(WORKBENCH, ["new", "test sast", "--type", "bugfix", "--no-worktree"], git_repo)
    assert r.returncode == 0
    task_id = sorted((git_repo / ".rig" / "runs").iterdir())[-1].name
    out = git_repo / "semgrep-output.json"
    out.write_text(json.dumps({"results": []}), encoding="utf-8")
    r = run_cli(ADAPTER, ["semgrep", "semgrep-output.json", "--apply", task_id], git_repo)
    assert "does not exist in this task's gate" in (r.stdout + r.stderr)
