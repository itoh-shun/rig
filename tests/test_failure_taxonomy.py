"""Tests for the MAST-style failure-mode taxonomy (skills/rig/patterns/failure-taxonomy.md).

Covers: classify_failure's deterministic from-state classification, telemetry_append
recording failure_mode into runs.jsonl (present for stopped runs, absent for clean ones),
and the dashboard failure-mode panel (renders with data + no-data note).
"""

import importlib.util
import json
import pathlib

from rig_workbench.orchestrate import config
from rig_workbench.orchestrate.runstate import (classify_failure, compute_next,
                                                new_state, telemetry_append)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _drive(state, script):
    for kind, payload in script:
        if kind == "next":
            compute_next(state)
        elif kind == "check":
            step = state["steps"][state["cursor"]]
            st = state["step_state"][step["id"]]
            st["checks"] = [{"cmd": c, "ok": payload} for c in step["checks"]]
        elif kind == "verdict":
            step = state["steps"][state["cursor"]]
            state["step_state"][step["id"]]["verdicts"].append(
                {"by": payload[0], "ok": payload[1], "note": ""})
    return state


# ── classify_failure (deterministic from-state best-guess) ───────────────────

def test_classify_self_graded(step_factory):
    state = _drive(new_state("t", [step_factory(id="r", gate="review-gate")], None),
                   [("next", None), ("verdict", ("self", True)), ("next", None)])  # BLOCKED
    assert classify_failure(state) == "verification:self-grading"


def test_classify_k_exhausted(step_factory):
    state = _drive(new_state("t", [step_factory(id="v", gate="acceptance-gate",
                                                checks=["false"], max_retries=2)], None),
                   [("next", None), ("check", False), ("next", None),
                    ("next", None), ("check", False), ("next", None)])  # ESCALATE
    assert state["stopped"] is not None
    assert classify_failure(state) == "verification:incorrect-implementation"


def test_classify_missing_verifier(step_factory):
    # A gated step escalated with no declared checks and no verdict recorded → no-verifier stall.
    state = new_state("t", [step_factory(id="r", gate="review-gate")], None)
    state["step_state"]["r"]["status"] = "running"
    state["stopped"] = {"reason": "stalled with no verifier", "at": "r"}
    assert classify_failure(state) == "verification:missing"


def test_classify_successful_run_is_none(step_factory):
    steps = [step_factory(id="design"),
             step_factory(id="verify", gate="acceptance-gate", checks=["true"]),
             step_factory(id="review", gate="review-gate")]
    state = _drive(new_state("t", steps, None),
                   [("next", None), ("next", None), ("next", None),
                    ("check", True), ("next", None), ("next", None),
                    ("verdict", ("reviewer", True)), ("next", None)])  # DONE
    assert state["done"] is True
    assert classify_failure(state) is None


# ── telemetry_append records failure_mode additively ─────────────────────────

def _read_jsonl(path):
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_telemetry_records_failure_mode_for_stopped_run(tmp_path, monkeypatch, step_factory):
    monkeypatch.setattr(config, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(config, "GLOBAL_RUNS_PATH", tmp_path / "global.jsonl")
    state = _drive(new_state("esc", [step_factory(id="v", gate="acceptance-gate",
                                                  checks=["false"], max_retries=2)], None),
                   [("next", None), ("check", False), ("next", None),
                    ("next", None), ("check", False), ("next", None)])
    telemetry_append(state, "ESCALATE")
    recs = _read_jsonl(config.RUNS_PATH)
    assert len(recs) == 1
    assert recs[0]["failure_mode"] == "verification:incorrect-implementation"


def test_telemetry_omits_failure_mode_for_clean_run(tmp_path, monkeypatch, step_factory):
    monkeypatch.setattr(config, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(config, "GLOBAL_RUNS_PATH", tmp_path / "global.jsonl")
    state = _drive(new_state("ok", [step_factory(id="a")], None),
                   [("next", None), ("next", None)])  # single no-gate step → DONE
    assert state["done"] is True
    telemetry_append(state, "DONE")
    recs = _read_jsonl(config.RUNS_PATH)
    assert len(recs) == 1
    assert "failure_mode" not in recs[0]


# ── dashboard failure-mode panel ─────────────────────────────────────────────

def _load_dashboard():
    spec = importlib.util.spec_from_file_location("rig_dashboard", REPO_ROOT / "scripts" / "dashboard.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dashboard_panel_renders_with_and_without_data():
    dash = _load_dashboard()
    runs = [
        {"final": "ESCALATE", "failure_mode": "verification:incorrect-implementation"},
        {"final": "BLOCKED", "failure_mode": "verification:self-grading"},
        {"final": "ESCALATE", "failure_mode": "verification:incorrect-implementation"},
        {"final": "DONE"},
    ]
    pairs = dash.failure_modes(runs)
    assert pairs[0] == ("verification:incorrect-implementation", 2)
    assert ("verification:self-grading", 1) in pairs

    html_with = dash.render_failure_modes(pairs)
    assert "verification:incorrect-implementation" in html_with

    html_without = dash.render_failure_modes([])
    assert "no data" in html_without
    assert "failure-taxonomy.md" in html_without
