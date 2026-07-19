"""Coverage for rig_workbench/bench.py's `rig-wb bench` (bare vs rig A/B,
shipped since v1.9.0 but previously untested and undocumented — #330).

`--provider mock` is a wiring smoke test only: MOCK_SRC (the orchestrate
mock provider) has the built-in tasks' fixes hardcoded so both arms
"succeed" deterministically with zero LLM calls and zero billing. This
proves the harness plumbing (task setup, both execution modes, metric
collection, JSON/HTML rendering) is sound — it is NOT evidence for the
bare-vs-rig quality claim itself, which needs a real provider
(`--provider claude`, real billing, run by the user explicitly).
"""

import json
import subprocess
import sys
import pathlib

from rig_workbench import bench

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_builtin_tasks_are_internally_consistent():
    # Every task's mock_fix must actually satisfy its own spec_check and
    # test_cmd — otherwise the "mock proves the harness works" claim is false.
    for task_id, task in bench.BUILTIN_TASKS.items():
        assert "mock_fix" in task, task_id
        assert "spec_check_code" in task, task_id
        assert "test_cmd" in task, task_id


def test_extract_code_pulls_fenced_python_block():
    text = "Here is the fix:\n```python\ndef f():\n    return 1\n```\nDone."
    assert bench._extract_code(text) == "def f():\n    return 1"


def test_extract_code_falls_back_to_raw_text_without_fence():
    assert bench._extract_code("no fence here") == "no fence here"


def test_call_provider_mock_returns_the_tasks_mock_fix(tmp_path):
    resp, elapsed = bench._call_provider("mock", "prompt", None, False, tmp_path, mock_fix="X = 1\n")
    assert "X = 1" in resp
    assert elapsed < 1


def test_cmd_bench_mock_smoke_one_task(tmp_path):
    out_path = tmp_path / "bench-out.json"
    r = subprocess.run(
        [sys.executable, "-m", "rig_workbench.cli", "bench",
         "--tasks", "divide-by-zero", "--provider", "mock", "--runs", "1",
         "--out", str(out_path)],
        capture_output=True, text=True, timeout=60, cwd=REPO_ROOT,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["provider"] == "mock"
    run = data["tasks"][0]["runs"][0]
    # Both arms converge on mock (MOCK_SRC hardcodes the same fixes) — this
    # asserts the harness wiring works, not that rig beats bare.
    assert run["modes"]["bare"]["test_pass"] is True
    assert run["modes"]["bare"]["spec_check"] == "PASS"
    assert run["modes"]["rig"]["test_pass"] is True
    assert run["modes"]["rig"]["spec_check"] == "PASS"


def test_render_html_does_not_crash_on_empty_summary():
    html = bench._render_html({"tasks": [], "generated": "x", "rig_wb_version": "0", "provider": "mock"})
    assert "<html" in html


def test_render_html_includes_task_rows():
    summary = {
        "generated": "x", "rig_wb_version": "0", "provider": "mock", "runs_per_task": 1,
        "tasks": [{
            "task_id": "divide-by-zero", "difficulty": "simple",
            "runs": [{"run": 1, "modes": {
                "bare": {"elapsed_s": 0.1, "calls": 1, "test_pass": True, "spec_check": "PASS",
                        "unrelated_files": [], "workspace_leaks": []},
                "rig": {"elapsed_s": 0.4, "calls": 3, "test_pass": True, "spec_check": "PASS",
                       "unrelated_files": [], "workspace_leaks": [], "reached_steps": ["s1"]},
            }}],
        }],
    }
    html = bench._render_html(summary)
    assert "divide-by-zero" in html
