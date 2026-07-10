"""Contracts tightened after the 1.11.0 test-suite findings."""

import json
import pathlib
import subprocess
import sys

from rig_workbench.orchestrate import config, queueing, runstate

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_queue_set_status_reports_unknown_id(tmp_queue):
    queueing.queue_add("local", "task one", {})
    assert queueing.queue_set_status("local", "no-such-id", "done", "", {}) is False
    known = queueing.queue_list("local", {})[0]["id"]
    assert queueing.queue_set_status("local", known, "done", "", {}) is True


def test_telemetry_global_mirror_is_rebindable(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_PATH", tmp_path / "local" / "runs.jsonl")
    monkeypatch.setattr(config, "GLOBAL_RUNS_PATH", tmp_path / "global" / "runs.jsonl")
    state = {"recipe": "r", "steps": [], "step_state": {}}
    runstate.telemetry_append(state, "DONE")
    local = (tmp_path / "local" / "runs.jsonl").read_text().strip().splitlines()
    mirrored = (tmp_path / "global" / "runs.jsonl").read_text().strip().splitlines()
    assert len(local) == 1 and len(mirrored) == 1
    assert json.loads(mirrored[0])["project"] == str(config.INVOCATION_CWD)
    assert "project" not in json.loads(local[0])


def test_plan_json_exits_nonzero_on_errors(tmp_path):
    recipe = tmp_path / "flow.md"
    recipe.write_text(
        "---\nname: flow\nsteps:\n  - id: s1\n    instruction: x\n---\n", encoding="utf-8"
    )
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "orchestrate.py"),
         "plan", str(recipe), "--json", "--with", "--only nosuch"],
        capture_output=True, text=True, cwd=tmp_path,
    )
    assert r.returncode == 1
    plan = json.loads(r.stdout)
    assert plan["errors"]


def test_recipes_module_imports_without_yaml(tmp_path):
    """Importing the package must not sys.exit when PyYAML is absent."""
    code = (
        "import sys, types\n"
        "sys.modules['yaml'] = None\n"          # simulate ImportError on `import yaml`
        "import importlib\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from rig_workbench.orchestrate import recipes\n"
        "print('imported-ok')\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert "imported-ok" in r.stdout, r.stderr
