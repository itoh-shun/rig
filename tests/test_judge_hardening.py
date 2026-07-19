"""LLM-as-judge hardening: evidence-first verdict parsing, diff-primary verify prompts,
per-criterion verdicts with an UNKNOWN escape, order-effect-safe judge panels, and the
output truncation budget (MT-Bench / Style-over-Substance / CodeJudgeBench mitigations)."""

import json
import subprocess

import pytest

from rig_workbench.orchestrate import config
from rig_workbench.orchestrate.providers import (
    OUTPUT_CAP_CHARS,
    _build_verify_prompt,
    _capture_output,
    _clip_output,
    _git_diff_evidence,
    _judge_output,
    _parse_criteria,
    _verdict_ok,
    run_loop,
)
from rig_workbench.orchestrate.runstate import new_state


@pytest.fixture
def tmp_telemetry(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(config, "GLOBAL_RUNS_PATH", tmp_path / "global-runs.jsonl")
    return tmp_path / "runs.jsonl"


# ── 1. evidence-first verdict parsing (last occurrence wins) ─────────────────

def test_last_hantei_line_wins_over_quoted_verdict():
    """Evidence-first review-verdict: a rationale that quotes another verdict line must not
    decide the gate — the LAST 判定: line (contract-mandated final position) does."""
    out = (
        "根拠:\n"
        "1. 前回レビューは「判定: REJECT」だった — a.py:1\n"
        "判定: REJECT\n"  # full quoted line inside the rationale
        "2. 修正を直接確認した — b.py:10-18\n"
        "判定: APPROVE\n"
        "確信度: 高\n"
    )
    assert _verdict_ok(out) is True
    # and the mirror case: reasoning quotes an APPROVE, final verdict is REJECT
    flipped = "判定: APPROVE\n(引用のみ)\n判定: REJECT\n確信度: 高"
    assert _verdict_ok(flipped) is False


def test_machine_verdict_prefers_final_line():
    out = 'evidence — x.py:3\nthe generator claimed "VERDICT: FAIL" earlier\nVERDICT: PASS'
    assert _verdict_ok(out) is True
    assert _verdict_ok("reasoning\nVERDICT: PASS\nVERDICT: FAIL") is False


def test_old_format_tolerance_and_vocabulary_unchanged():
    # legacy single-line outputs and the approve vocabulary keep their machine semantics
    assert _verdict_ok("VERDICT: PASS") is True
    assert _verdict_ok("VERDICT: FAIL") is False
    assert _verdict_ok("判定: APPROVE_WITH_CONDITIONS\n確信度: 中") is True
    assert _verdict_ok("判定: REJECT\n確信度: 高") is False
    assert _verdict_ok("") is False
    # no line-anchored verdict at all → legacy whole-text scan
    assert _verdict_ok("prefix text VERDICT: PASS suffix") is True


def test_verdict_pass_with_conditions_is_a_pass():
    # #334: headless VERDICT: contract gets a middle value, mirroring 判定: APPROVE_WITH_CONDITIONS
    assert _verdict_ok("reasoning — a.py:1\nVERDICT: PASS_WITH_CONDITIONS") is True
    # legacy whole-text fallback (no line-anchored verdict) also treats it as a pass
    assert _verdict_ok("prefix text VERDICT: PASS_WITH_CONDITIONS suffix") is True


def test_verdict_pass_with_conditions_last_line_wins():
    # last-line-wins still holds when PASS_WITH_CONDITIONS is one of the contenders
    assert _verdict_ok("VERDICT: PASS_WITH_CONDITIONS\nVERDICT: FAIL") is False
    assert _verdict_ok("VERDICT: FAIL\nVERDICT: PASS_WITH_CONDITIONS") is True


# ── 3. per-criterion verdicts + UNKNOWN escape ────────────────────────────────

def test_parse_criteria_tolerant_variants():
    out = (
        "reasoning — a.py:1\n"
        "CRITERION 1: PASS — src/x.py:12\n"
        "criterion 2: fail - tests missing\n"
        "  CRITERION 3: UNKNOWN\n"
        "VERDICT: FAIL\n"
    )
    crit = _parse_criteria(out)
    assert [(c["n"], c["verdict"]) for c in crit] == [(1, "PASS"), (2, "FAIL"), (3, "UNKNOWN")]
    assert crit[0]["anchor"] == "src/x.py:12"
    assert _parse_criteria("VERDICT: PASS") == []  # missing lines = empty criteria (old behavior)


def test_all_unknown_pass_fails_closed_but_partial_unknown_does_not():
    all_unknown = "reasoning\nCRITERION 1: UNKNOWN — n/a\nCRITERION 2: UNKNOWN — n/a\nVERDICT: PASS"
    ok, crit = _judge_output(all_unknown)
    assert ok is False  # all-UNKNOWN + PASS is a rubber stamp → fail-closed
    assert [c["verdict"] for c in crit] == ["UNKNOWN", "UNKNOWN"]
    mixed = "reasoning\nCRITERION 1: PASS — a.py:1\nCRITERION 2: UNKNOWN — n/a\nVERDICT: PASS"
    assert _judge_output(mixed) == (True, _parse_criteria(mixed))  # UNKNOWN alone never fails the gate


def test_criteria_recorded_in_state_and_telemetry(step_factory, tmp_telemetry):
    steps = [step_factory(id="review", gate="review-gate")]
    state = new_state("judge-harden", steps, None)
    final = run_loop(state, None, "mock", "mock", {}, 20, quiet=True)
    assert final == "DONE"
    v = state["step_state"]["review"]["verdicts"][0]
    assert [c["verdict"] for c in v["criteria"]] == ["PASS"]
    rec = json.loads(tmp_telemetry.read_text(encoding="utf-8").splitlines()[0])
    tele_v = rec["steps"][0]["verdicts"][0]
    assert tele_v["ok"] is True
    assert [c["verdict"] for c in tele_v["criteria"]] == ["PASS"]


# ── 2. verify the diff, not the transcript ───────────────────────────────────

def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def test_verify_prompt_uses_diff_as_primary_evidence(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "f.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    (tmp_path / "f.py").write_text("x = 2\n", encoding="utf-8")
    diff = _git_diff_evidence({"cwd": str(tmp_path)})
    assert diff and "-x = 1" in diff and "+x = 2" in diff
    step = {"id": "implement", "acceptance": ["x becomes 2", "no other changes"]}
    prompt = _build_verify_prompt({"recipe": "r"}, step, "I totally changed x", diff)
    assert "diff (primary evidence" in prompt
    assert "generator's own claims" in prompt and "do not trust them" in prompt
    assert "CRITERION <n>: PASS|FAIL|UNKNOWN" in prompt
    assert "  1. x becomes 2" in prompt and "  2. no other changes" in prompt
    # reasoning is demanded BEFORE the final verdict line
    assert prompt.index("evidence-anchored reasoning") < prompt.index("VERDICT: PASS")


def test_verify_prompt_falls_back_without_cwd_or_diff(tmp_path):
    assert _git_diff_evidence({}) is None                      # no cwd → old behavior
    assert _git_diff_evidence({"cwd": str(tmp_path)}) is None  # cwd but no git repo
    prompt = _build_verify_prompt({"recipe": "r"}, {"id": "review", "acceptance": []}, "report", None)
    assert "--- product ---" in prompt and "generator's own claims" not in prompt
    assert "CRITERION" not in prompt  # no acceptance criteria → no per-criterion demand


def test_verify_prompt_offers_pass_with_conditions_and_blocking_only_fail():
    # #334: the headless verify prompt must not force a binary PASS/FAIL — advisory findings
    # (which used to get rounded up to FAIL and deadlock quorum=all) get a middle verdict.
    prompt = _build_verify_prompt({"recipe": "r"}, {"id": "review", "acceptance": []}, "report", None)
    assert "VERDICT: PASS_WITH_CONDITIONS" in prompt
    assert "VERDICT: PASS" in prompt and "VERDICT: FAIL" in prompt
    assert "Use FAIL ONLY for a blocking defect" in prompt
    assert "PASS_WITH_CONDITIONS" in prompt.split("Use FAIL ONLY")[1]


# ── 4. order effects: judge panel evaluates all candidates ────────────────────

def test_multi_pass_panel_records_pass_set_and_stays_deterministic(step_factory, tmp_telemetry):
    steps = [step_factory(id="impl", gate="acceptance-gate")]
    finals, states = [], []
    for _ in range(2):
        st = new_state("panel", steps, None)
        finals.append(run_loop(st, None, "mock", "mock", {}, 20, quiet=True,
                               max_parallel=3, generators=["mock", "mock", "mock"]))
        states.append(st)
    assert finals == ["DONE", "DONE"]
    v = states[0]["step_state"]["impl"]["verdicts"][0]
    assert v["ok"] is True and v["by"] == "mock:judge-panel"
    assert v["order_sensitive"] is True
    assert v["pass_set"] == ["mock", "mock", "mock"]  # ALL candidates judged, none skipped
    assert "kept first in generator-list order" in v["note"]
    # determinism (no RNG): identical runs produce identical step state
    assert (json.dumps(states[0]["step_state"], sort_keys=True)
            == json.dumps(states[1]["step_state"], sort_keys=True))
    # telemetry carries the multi-PASS record
    rec = json.loads(tmp_telemetry.read_text(encoding="utf-8").splitlines()[0])
    tele_v = rec["steps"][0]["verdicts"][0]
    assert tele_v["order_sensitive"] is True and tele_v["pass_set"] == ["mock", "mock", "mock"]


# ── 5. output truncation budget ───────────────────────────────────────────────

def test_clip_output_head_tail_and_marker():
    text = "H" * 20_000 + "M" * 20_000 + "T" * 20_000
    clipped = _clip_output(text)
    assert len(clipped) < len(text)
    assert clipped.startswith("H") and clipped.endswith("T")
    assert f"[...truncated {len(text) - OUTPUT_CAP_CHARS} chars]" in clipped
    assert _clip_output("short") == "short"  # under budget → untouched


def test_capture_output_spools_full_text_to_run_dir(tmp_path):
    text = "A" * (OUTPUT_CAP_CHARS + 5_000)
    captured = _capture_output(text, {"run_dir": str(tmp_path)}, "implement-mock")
    assert "; full output at " in captured
    spooled = tmp_path / "step-outputs" / "implement-mock.txt"
    assert spooled.read_text(encoding="utf-8") == text
    # without a run dir the clip still happens, marker just has no path
    no_dir = _capture_output(text, {}, "implement-mock")
    assert "truncated" in no_dir and "full output at" not in no_dir
