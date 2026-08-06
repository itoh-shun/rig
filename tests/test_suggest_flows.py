"""`wb suggest-flows` — deriving a project's flow set from what it has run.

The rules under test are the ones that keep /rig:init from scaffolding flows
nobody runs: cap the proposal at 3 and say what the cap dropped, never present
a layout guess as a finding, require ≥2 runs before a recipe counts as
evidence, and never promote a reviewer that has yet to object to anything.
"""

import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench.flows import (MIN_RUNS_FOR_EVIDENCE,
                                           manifest_fragment, recipe_evidence,
                                           suggest)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / ".rig").mkdir()
    return tmp_path


def _runs_jsonl(repo, records):
    (repo / ".rig" / "runs.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def _task(repo, task_id, recipe, checks=None, verdicts=None):
    d = repo / ".rig" / "runs" / task_id
    d.mkdir(parents=True)
    (d / "task.json").write_text(json.dumps(
        {"task_id": task_id, "input": task_id, "task_type": "feature", "recipe": recipe,
         "status": "gate_passed", "created_at": "2026-08-01T00:00:00+09:00"}), encoding="utf-8")
    if checks is not None:
        (d / "acceptance.json").write_text(json.dumps(
            {"task_id": task_id, "checks": checks}), encoding="utf-8")
    if verdicts is not None:
        (d / "review.json").write_text(json.dumps(
            {"task_id": task_id, "verdicts": verdicts}), encoding="utf-8")


def run_cli(args, cwd):
    return subprocess.run([sys.executable, str(WORKBENCH), *args],
                          capture_output=True, text=True, cwd=cwd, timeout=60)


# ---- evidence ----------------------------------------------------------------

def test_both_telemetry_records_are_folded_into_one_count(repo):
    _runs_jsonl(repo, [{"recipe": "bugfix", "final": "DONE"},
                       {"recipe": "bugfix", "final": "ESCALATE", "escalated_at": "verify"}])
    _task(repo, "rig-1-a", "bugfix", checks=[{"name": "x", "status": "passed"}])

    stats = recipe_evidence(repo)["bugfix"]
    assert stats["runs"] == 3
    assert stats["done"] == 1
    assert stats["escalated"] == 1
    assert stats["gate_passed"] == 1


def test_gate_failures_are_counted_separately(repo):
    _task(repo, "rig-1-a", "feature", checks=[{"name": "x", "status": "failed"}])
    _task(repo, "rig-1-b", "feature", checks=[{"name": "x", "status": "passed"}])
    stats = recipe_evidence(repo)["feature"]
    assert (stats["gate_passed"], stats["gate_failed"]) == (1, 1)


# ---- the proposal ------------------------------------------------------------

def test_a_single_run_is_an_anecdote_not_a_default(repo):
    _runs_jsonl(repo, [{"recipe": "hotfix", "final": "DONE"}])
    proposal = suggest(repo)
    assert proposal["flows"] == [] or proposal["flows"][0]["evidence"] == "unevidenced"
    assert [t["recipe"] for t in proposal["thin"]] == ["hotfix"]
    assert MIN_RUNS_FOR_EVIDENCE == 2


def test_proposals_are_capped_and_the_cap_is_reported(repo):
    records = []
    for name, n in (("bugfix", 5), ("feature", 4), ("refactor", 3), ("review-only", 2)):
        records += [{"recipe": name, "final": "DONE"}] * n
    _runs_jsonl(repo, records)

    proposal = suggest(repo, limit=3)
    assert [f["recipe"] for f in proposal["flows"]] == ["bugfix", "feature", "refactor"]
    assert [d["recipe"] for d in proposal["dropped"]] == ["review-only"]

    out = run_cli(["suggest-flows"], repo).stdout
    assert "Not proposed (beyond the cap of 3)" in out
    assert "review-only" in out


def test_no_history_yields_guesses_that_are_labelled_as_guesses(repo):
    (repo / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    proposal = suggest(repo)
    assert proposal["has_history"] is False
    assert proposal["stack"] == "python"
    assert all(f["evidence"] == "unevidenced" for f in proposal["flows"])

    out = run_cli(["suggest-flows"], repo).stdout
    assert "guesses from the project layout, not findings" in out


def test_no_history_and_no_recognised_stack_proposes_nothing(repo):
    proposal = suggest(repo)
    assert proposal["flows"] == []
    out = run_cli(["suggest-flows"], repo).stdout
    assert "Nothing to propose" in out


def test_recorded_runs_beat_layout_guesses(repo):
    (repo / "package.json").write_text("{}", encoding="utf-8")
    _runs_jsonl(repo, [{"recipe": "refactor", "final": "DONE"}] * 2)
    proposal = suggest(repo)
    assert [f["recipe"] for f in proposal["flows"]] == ["refactor"]
    assert proposal["flows"][0]["evidence"] == "recorded-runs"


# ---- personas ----------------------------------------------------------------

def test_only_personas_that_have_rejected_are_proposed(repo):
    _task(repo, "rig-1-a", "feature",
          verdicts=[{"persona": "security-reviewer", "verdict": "REJECT"},
                    {"persona": "docs-reviewer", "verdict": "APPROVE"}])
    proposal = suggest(repo)
    assert [p["persona"] for p in proposal["personas"]] == ["security-reviewer"]


def test_a_long_clean_record_is_flagged_as_a_rubber_stamp_not_promoted(repo):
    for i in range(6):
        _task(repo, f"rig-1-{i}", "feature",
              verdicts=[{"persona": "nodding-reviewer", "verdict": "APPROVE"}])
    proposal = suggest(repo)
    assert proposal["personas"] == []
    assert [p["persona"] for p in proposal["muted_personas"]] == ["nodding-reviewer"]

    out = run_cli(["suggest-flows"], repo).stdout
    assert "possible rubber-stamp" in out


# ---- manifest fragment --------------------------------------------------------

def test_fragment_sets_the_top_flow_as_default_recipe(repo):
    _runs_jsonl(repo, [{"recipe": "bugfix", "final": "DONE"}] * 3 +
                      [{"recipe": "feature", "final": "DONE"}] * 2)
    _task(repo, "rig-1-a", "bugfix",
          verdicts=[{"persona": "test-reviewer", "verdict": "REJECT"}])
    fragment = manifest_fragment(suggest(repo))
    assert 'default_recipe: "bugfix"' in fragment
    assert "# also in use: feature" in fragment
    assert 'default_personas: ["test-reviewer"]' in fragment


def test_the_command_writes_nothing(repo):
    (repo / "go.mod").write_text("module x\n", encoding="utf-8")
    before = {p.name for p in repo.iterdir()}
    r = run_cli(["suggest-flows"], repo)
    assert r.returncode == 0
    assert "Nothing was written" in r.stdout
    assert {p.name for p in repo.iterdir()} == before
    assert not (repo / ".claude").exists()


def test_json_output_is_the_same_proposal(repo):
    _runs_jsonl(repo, [{"recipe": "bugfix", "final": "DONE"}] * 2)
    data = json.loads(run_cli(["suggest-flows", "--json"], repo).stdout)
    assert data["flows"][0]["recipe"] == "bugfix"
    assert data["limit"] == 3
