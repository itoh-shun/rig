"""Subprocess smoke tests for the scripts/orchestrate.py shim (CLI level only).

Runs from a tmp cwd with RIG_HOME pinned to the repo, so shipped recipes resolve
while nothing is read from or written to the real repo's .rig/ state.
"""

import json
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ORCHESTRATE = REPO_ROOT / "scripts" / "orchestrate.py"


def run_cli(args, tmp_path):
    import os
    env = dict(os.environ, RIG_HOME=str(REPO_ROOT))
    return subprocess.run([sys.executable, str(ORCHESTRATE), *args],
                          capture_output=True, text=True, cwd=tmp_path, env=env, timeout=60)


def test_plan_json_review_only(tmp_path):
    r = run_cli(["plan", "review-only", "--json"], tmp_path)
    assert r.returncode == 0
    plan = json.loads(r.stdout)
    assert set(plan) >= {"recipe", "badges", "steps_field", "n_steps", "steps", "warnings"}
    assert plan["recipe"] == "review-only"
    assert plan["n_steps"] == 1
    assert plan["steps"][0]["id"] == "review"
    assert plan["steps"][0]["gate"] == "review-gate"


def test_plan_json_with_flags_returns_effective_resolution(tmp_path):
    r = run_cli(["plan", "release-flow", "--json", "--diff-lines", "50"], tmp_path)
    assert r.returncode == 0
    plan = json.loads(r.stdout)
    assert set(plan) >= {"effective_steps", "slice", "mode", "size", "flags", "errors"}
    assert plan["size"] == {"diff_lines": 50, "class": "S"}
    assert isinstance(plan["effective_steps"], list) and plan["effective_steps"]
    assert plan["errors"] == []


def test_unknown_command_exits_nonzero(tmp_path):
    r = run_cli(["no-such-command"], tmp_path)
    assert r.returncode != 0


def test_no_args_prints_usage_and_exits_zero(tmp_path):
    r = run_cli([], tmp_path)
    assert r.returncode == 0
    assert r.stdout.strip()  # usage text emitted (wording not asserted)
