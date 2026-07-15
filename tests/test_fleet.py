"""Cross-repository fleet aggregation (#272).

orchestrate.py fleet --repos p1,p2,... reads runs.jsonl/drill-results.jsonl
from multiple repos read-only, aggregating run counts and per-persona
detection rate, plus a per-repo breakdown.
"""

import json
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ORCHESTRATE = REPO_ROOT / "scripts" / "orchestrate.py"


def run_cli(args):
    return subprocess.run([sys.executable, str(ORCHESTRATE), *args],
                          capture_output=True, text=True, timeout=30)


def _make_repo(base, name, runs=None, drills=None):
    d = base / name / ".rig"
    d.mkdir(parents=True)
    if runs is not None:
        (d / "runs.jsonl").write_text("\n".join(json.dumps(r) for r in runs) + "\n", encoding="utf-8")
    if drills is not None:
        (d / "drill-results.jsonl").write_text("\n".join(json.dumps(r) for r in drills) + "\n", encoding="utf-8")
    return base / name


def test_usage_error_without_repos_flag():
    r = run_cli(["fleet"])
    assert r.returncode != 0
    assert "usage: fleet --repos" in (r.stdout + r.stderr)


def test_missing_repo_is_reported_cleanly(tmp_path):
    r = run_cli(["fleet", "--repos", str(tmp_path / "does-not-exist")])
    assert r.returncode == 0
    assert "(no .rig/)" in r.stdout


def test_aggregates_runs_and_drills_across_two_repos(tmp_path):
    r1 = _make_repo(tmp_path, "repo1",
                    runs=[{"final": "DONE"}, {"final": "ESCALATE"}],
                    drills=[{"scores": [{"reviewer": "security-reviewer", "detected": 9, "seeded": 10}]}])
    r2 = _make_repo(tmp_path, "repo2",
                    runs=[{"final": "DONE"}],
                    drills=[{"scores": [{"reviewer": "security-reviewer", "detected": 3, "seeded": 10}]}])
    r = run_cli(["fleet", "--repos", f"{r1},{r2}"])
    assert r.returncode == 0
    assert "security-reviewer: 60% (12/20)" in r.stdout
    assert f"{r1}" in r.stdout and "2" in r.stdout  # 2 runs for repo1
    assert "security-reviewer: " in r.stdout and "=90%" in r.stdout and "=30%" in r.stdout


def test_anonymize_replaces_repo_paths_with_labels(tmp_path):
    r1 = _make_repo(tmp_path, "repo1", drills=[{"scores": [{"reviewer": "x", "detected": 1, "seeded": 1}]}])
    r2 = _make_repo(tmp_path, "repo2", drills=[{"scores": [{"reviewer": "x", "detected": 1, "seeded": 1}]}])
    r = run_cli(["fleet", "--repos", f"{r1},{r2}", "--anonymize"])
    assert r.returncode == 0
    assert "repo-1" in r.stdout and "repo-2" in r.stdout
    assert str(r1) not in r.stdout and str(r2) not in r.stdout


def test_json_output_is_valid_and_matches_text_aggregation(tmp_path):
    r1 = _make_repo(tmp_path, "repo1", drills=[{"scores": [{"reviewer": "x", "detected": 4, "seeded": 8}]}])
    r = run_cli(["fleet", "--repos", str(r1), "--json"])
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["persona_totals"]["x"]["rate"] == 0.5
    assert data["repos"][0]["exists"] is True


def test_no_drill_data_reports_unmeasured(tmp_path):
    r1 = _make_repo(tmp_path, "repo1", runs=[{"final": "DONE"}])
    r = run_cli(["fleet", "--repos", str(r1)])
    assert r.returncode == 0
    assert "unmeasured (no /rig:drill runs" in r.stdout


def test_read_only_never_writes_to_rig_dirs(tmp_path):
    r1 = _make_repo(tmp_path, "repo1", drills=[{"scores": [{"reviewer": "x", "detected": 1, "seeded": 1}]}])
    before = sorted((r1 / ".rig").iterdir())
    run_cli(["fleet", "--repos", str(r1), "--json"])
    after = sorted((r1 / ".rig").iterdir())
    assert before == after
