import copy
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.baseline import (
    BaselineError,
    capture_baseline as _capture_baseline,
    compare_baseline as _compare_baseline,
    render_baseline,
)


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
TEST_NOW = dt.datetime(2026, 8, 5, tzinfo=dt.timezone.utc)


def capture_baseline(*args, now=None, **kwargs):
    return _capture_baseline(*args, now=now or TEST_NOW, **kwargs)


def compare_baseline(*args, now=None, **kwargs):
    return _compare_baseline(*args, now=now or TEST_NOW, **kwargs)


def live_generated(*, minutes=0):
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=minutes)).isoformat()


def _arm(outcome, *, elapsed=1.0, calls=1):
    return {
        "name": "arm",
        "attempts": [],
        "git_status": [],
        "changed_files": [],
        "public_test": {"passed": outcome in {"clean_pass", "silent_defect"}},
        "hidden_check": {"passed": outcome in {"clean_pass", "safe_stop"}},
        "elapsed_s": elapsed,
        "invocation_count": calls,
        "completed": outcome in {"clean_pass", "silent_defect"},
        "runner_state": None,
        "outcome": outcome,
    }


def bench_summary(*, provider="claude", bare_model="haiku", rig_model="sonnet", generated="2026-08-01T00:00:00+00:00"):
    return {
        "schema_version": 2,
        "generated": generated,
        "rig_wb_version": "1.2.3",
        "recipe": "adaptive-bugfix",
        "recipe_version": 1,
        "corpus_version": 1,
        "provider": provider,
        "model": rig_model,
        "bare_model": bare_model,
        "rig_model": rig_model,
        "provider_version": "provider 1",
        "runs_per_task": 1,
        "score": {"verdict": "pass"},
        "tasks": [{
            "task_id": "task-a",
            "language": "python",
            "difficulty": "medium",
            "risk_domains": ["correctness"],
            "runs": [{
                "pair_id": "task-a-1",
                "task_id": "task-a",
                "run": 1,
                "provider": provider,
                "model": rig_model,
                "bare_model": bare_model,
                "rig_model": rig_model,
                "arms": {
                    "bare": _arm("silent_defect", elapsed=1.0),
                    "rig": _arm("clean_pass", elapsed=2.0, calls=2),
                },
                "elapsed_s": 3.0,
            }],
        }],
    }


def with_samples(summary, count=3):
    runs = summary["tasks"][0]["runs"]
    source = runs[0]
    summary["tasks"][0]["runs"] = [
        {**copy.deepcopy(source), "pair_id": f"task-a-{index}", "run": index}
        for index in range(1, count + 1)
    ]
    summary["runs_per_task"] = count
    return summary


def test_capture_roundtrip_builds_versioned_identity_scorecard():
    baseline = capture_baseline(bench_summary(), source_path="bench.json")

    assert baseline["baseline_schema_version"] == 1
    assert baseline["source"]["bench_schema_version"] == 2
    assert baseline["source"]["path"] == "bench.json"
    assert baseline["source"]["provider"] == "claude"
    assert baseline["source"]["generated"] == "2026-08-01T00:00:00+00:00"
    identities = [(row["provider"], row["model"], row["mode"], row["task_id"])
                  for row in baseline["scorecard"]]
    assert identities == [
        ("claude", "haiku", "bare", "task-a"),
        ("claude", "sonnet", "rig", "task-a"),
    ]
    assert json.loads(json.dumps(baseline, sort_keys=True)) == baseline


def test_scorecard_marks_sample_shortage_as_insufficient():
    baseline = capture_baseline(bench_summary(), source_path="bench.json")

    assert {row["sample_status"] for row in baseline["scorecard"]} == {"insufficient"}


def test_compare_identical_sufficient_evidence_passes():
    source = with_samples(bench_summary())
    baseline = capture_baseline(source, source_path="bench.json")

    report = compare_baseline(baseline, source)

    assert report["status"] == "pass"
    assert report["regressions"] == []
    assert report["identity_count"] == 2


def test_compare_reports_task_success_and_silent_defect_regression():
    source = with_samples(bench_summary())
    baseline = capture_baseline(source, source_path="bench.json")
    current = with_samples(bench_summary(generated="2026-08-02T00:00:00+00:00"))
    current["tasks"][0]["runs"][0]["arms"]["rig"] = _arm("silent_defect", elapsed=2.0, calls=2)

    report = compare_baseline(baseline, current)

    assert report["status"] == "fail"
    metrics = {item["metric"] for item in report["regressions"]}
    assert metrics == {"task_success_rate", "silent_defect_rate"}


def test_mock_baseline_is_not_real_provider_quality_evidence():
    source = with_samples(bench_summary(provider="mock", bare_model="mock", rig_model="mock"))
    baseline = capture_baseline(source, source_path="mock.json")

    assert baseline["source"]["quality_evidence"] is False
    with pytest.raises(BaselineError, match="mock provider"):
        compare_baseline(baseline, source)


def test_compare_rejects_stale_baseline_from_evidence_timestamps():
    baseline = capture_baseline(with_samples(bench_summary()), source_path="bench.json")
    current = with_samples(bench_summary(generated="2026-08-02T00:00:00+00:00"))

    with pytest.raises(BaselineError, match="stale"):
        compare_baseline(baseline, current, now=dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc))


def test_cli_capture_and_show_roundtrip(tmp_path):
    source = tmp_path / "bench.json"
    output = tmp_path / "baseline.json"
    source.write_text(
        json.dumps(with_samples(bench_summary(generated=live_generated()))), encoding="utf-8"
    )
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))

    captured = subprocess.run(
        [sys.executable, "-m", "rig_workbench.cli", "baseline", "capture",
         "--input", str(source), "--output", str(output)],
        capture_output=True, text=True, cwd=tmp_path, env=env,
    )
    shown = subprocess.run(
        [sys.executable, "-m", "rig_workbench.cli", "baseline", "show", str(output)],
        capture_output=True, text=True, cwd=tmp_path, env=env,
    )

    assert captured.returncode == 0, captured.stderr
    assert output.exists()
    assert shown.returncode == 0, shown.stderr
    assert "claude / haiku / bare / task-a" in shown.stdout
    assert "samples=3" in shown.stdout


def test_cli_compare_metric_regression_exits_one_with_json(tmp_path):
    generated = live_generated()
    base_source = with_samples(bench_summary(generated=generated))
    baseline = _capture_baseline(base_source, source_path="bench.json")
    current = with_samples(bench_summary(generated=generated))
    current["tasks"][0]["runs"][0]["arms"]["rig"] = _arm("silent_defect", elapsed=2.0, calls=2)
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    current_path.write_text(json.dumps(current), encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))

    result = subprocess.run(
        [sys.executable, "-m", "rig_workbench.cli", "baseline", "compare",
         "--baseline", str(baseline_path), "--current", str(current_path), "--json"],
        capture_output=True, text=True, cwd=tmp_path, env=env,
    )

    assert result.returncode == 1
    assert json.loads(result.stdout)["status"] == "fail"


def test_cli_capture_invalid_schema_exits_two(tmp_path):
    source = tmp_path / "legacy.json"
    output = tmp_path / "baseline.json"
    source.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))

    result = subprocess.run(
        [sys.executable, "-m", "rig_workbench.cli", "baseline", "capture",
         "--input", str(source), "--output", str(output)],
        capture_output=True, text=True, cwd=tmp_path, env=env,
    )

    assert result.returncode == 2
    assert "schema_version 2" in result.stderr
    assert not output.exists()


def test_compare_rejects_provider_model_identity_mismatch():
    baseline = capture_baseline(with_samples(bench_summary()), source_path="bench.json")
    current = with_samples(bench_summary(rig_model="opus"))

    with pytest.raises(BaselineError, match="identity mismatch"):
        compare_baseline(baseline, current)


def test_scorecard_separates_invalid_samples_and_marks_cost_unmeasured():
    source = with_samples(bench_summary())
    source["tasks"][0]["runs"][0]["arms"]["rig"] = _arm("infra_error")

    baseline = capture_baseline(source, source_path="bench.json")
    rig = next(row for row in baseline["scorecard"] if row["mode"] == "rig")

    assert rig["samples"] == 3
    assert rig["valid_samples"] == 2
    assert rig["invalid_samples"] == 1
    assert rig["task_success_rate"] == 1.0
    assert rig["tokens"]["status"] == "unmeasured"
    assert rig["tokens"]["reason"]
    assert rig["cost"]["status"] == "unmeasured"
    assert rig["cost"]["reason"]


def test_scorecard_aggregates_optional_tokens_and_billed_cost():
    source = with_samples(bench_summary())
    for run in source["tasks"][0]["runs"]:
        run["arms"]["rig"]["token_usage"] = {
            "prompt_tokens": 100,
            "completion_tokens": 20,
        }
        run["arms"]["rig"]["cost_usd"] = 0.05

    baseline = capture_baseline(source, source_path="bench.json")
    rig = next(row for row in baseline["scorecard"] if row["mode"] == "rig")

    assert rig["tokens"] == {
        "status": "measured", "prompt_tokens_per_valid_sample": 100.0,
        "completion_tokens_per_valid_sample": 20.0, "total_tokens_per_valid_sample": 120.0,
    }
    assert rig["cost"] == {
        "status": "measured", "currency": "USD", "total_per_valid_sample": 0.05,
    }


def test_compare_reports_time_calls_tokens_and_cost_regressions():
    base = with_samples(bench_summary())
    current = with_samples(bench_summary(generated="2026-08-02T00:00:00+00:00"))
    for source, elapsed, calls, tokens, cost in (
        (base, 2.0, 2, 100, 0.05),
        (current, 3.0, 3, 150, 0.08),
    ):
        for run in source["tasks"][0]["runs"]:
            run["arms"]["rig"] = _arm("clean_pass", elapsed=elapsed, calls=calls)
            run["arms"]["rig"]["token_usage"] = {
                "prompt_tokens": tokens,
                "completion_tokens": 0,
            }
            run["arms"]["rig"]["cost_usd"] = cost
    baseline = capture_baseline(base, source_path="bench.json")

    report = compare_baseline(baseline, current)

    assert report["status"] == "fail"
    metrics = {item["metric"] for item in report["regressions"]}
    assert metrics == {
        "elapsed_s.p95", "calls.mean_per_valid_sample",
        "tokens.total_tokens_per_valid_sample", "cost.total_per_valid_sample",
    }


def test_compare_rejects_tampered_source_schema_metadata():
    source = with_samples(bench_summary())
    baseline = capture_baseline(source, source_path="bench.json")
    baseline["source"]["bench_schema_version"] = 1

    with pytest.raises(BaselineError, match="source bench schema"):
        compare_baseline(baseline, source)


def test_canonical_baseline_fixture_is_versioned_and_renderable():
    path = REPO_ROOT / "benchmarks" / "baselines" / "example.baseline.json"
    baseline = json.loads(path.read_text(encoding="utf-8"))

    assert baseline["baseline_schema_version"] == 1
    assert "mock / mock / rig / fixture-task" in render_baseline(baseline)


def test_compare_rejects_incomplete_threshold_schema():
    source = with_samples(bench_summary())
    baseline = capture_baseline(source, source_path="bench.json")
    baseline["thresholds"].pop("max_task_success_rate_drop")

    with pytest.raises(BaselineError, match="threshold"):
        compare_baseline(baseline, source)


def test_compare_rejects_nonpassing_source_score():
    source = with_samples(bench_summary())
    source["score"] = {"verdict": "inconclusive", "reasons": ["no bare defects"]}
    baseline = capture_baseline(source, source_path="bench.json")

    assert baseline["source"]["quality_evidence"] is False
    with pytest.raises(BaselineError, match="source score verdict"):
        compare_baseline(baseline, source)


def test_compare_rejects_nonpassing_current_score():
    source = with_samples(bench_summary())
    baseline = capture_baseline(source, source_path="bench.json")
    current = with_samples(bench_summary(generated="2026-08-02T00:00:00+00:00"))
    current["score"] = {"verdict": "invalid", "reasons": ["missing evidence"]}

    with pytest.raises(BaselineError, match="current source score verdict"):
        compare_baseline(baseline, current)


def test_compare_rejects_tampered_baseline_threshold():
    source = with_samples(bench_summary())
    baseline = capture_baseline(source, source_path="bench.json")
    baseline["thresholds"]["min_samples_per_identity"] = 5

    with pytest.raises(BaselineError, match="integrity"):
        compare_baseline(baseline, source)


def test_compare_reports_safe_stop_regression():
    source = with_samples(bench_summary())
    baseline = capture_baseline(source, source_path="bench.json")
    current = with_samples(bench_summary(generated="2026-08-02T00:00:00+00:00"))
    current["tasks"][0]["runs"][0]["arms"]["rig"] = _arm("safe_stop", elapsed=2.0, calls=2)

    report = compare_baseline(baseline, current)

    assert "safe_stop_rate" in {item["metric"] for item in report["regressions"]}


def test_invalid_sample_rate_has_dedicated_threshold():
    source = with_samples(bench_summary(), count=4)
    source["tasks"][0]["runs"][0]["arms"]["rig"] = _arm("infra_error")
    baseline = capture_baseline(source, source_path="bench.json")

    assert "max_invalid_sample_rate_increase" in baseline["thresholds"]
    assert "max_silent_defect_rate_increase" in baseline["thresholds"]


def test_capture_rejects_pair_provider_spoofing():
    source = with_samples(bench_summary())
    source["tasks"][0]["runs"][0]["provider"] = "mock"

    with pytest.raises(BaselineError, match="does not match top-level provider"):
        capture_baseline(source, source_path="spoofed.json")


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0])
def test_capture_rejects_nonfinite_and_negative_arm_metrics(bad):
    source = with_samples(bench_summary())
    source["tasks"][0]["runs"][0]["arms"]["rig"]["elapsed_s"] = bad

    with pytest.raises(BaselineError, match="finite non-negative"):
        capture_baseline(source, source_path="bad.json")


def test_capture_rejects_malformed_nested_objects_and_cli_normalizes_to_exit_two(tmp_path):
    source = with_samples(bench_summary())
    source["tasks"][0]["runs"][0]["arms"] = []
    input_path = tmp_path / "bad.json"
    output_path = tmp_path / "baseline.json"
    input_path.write_text(json.dumps(source), encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))

    result = subprocess.run(
        [sys.executable, "-m", "rig_workbench.cli", "baseline", "capture",
         "--input", str(input_path), "--output", str(output_path)],
        capture_output=True, text=True, cwd=tmp_path, env=env,
    )

    assert result.returncode == 2
    assert "arms" in result.stderr


def test_compare_uses_valid_samples_for_sufficiency():
    source = with_samples(bench_summary(), count=4)
    source["tasks"][0]["runs"][0]["arms"]["rig"] = _arm("infra_error")
    baseline = capture_baseline(source, source_path="bench.json")
    current = copy.deepcopy(source)
    current["generated"] = "2026-08-02T00:00:00+00:00"
    current["tasks"][0]["runs"][1]["arms"]["rig"] = _arm("invalid")

    with pytest.raises(BaselineError, match="insufficient valid samples"):
        compare_baseline(baseline, current)


def test_compare_rejects_future_timestamp_beyond_tolerance():
    now = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
    source = with_samples(bench_summary(generated="2026-08-01T00:04:00+00:00"))
    baseline = capture_baseline(source, source_path="bench.json", now=now)
    current = with_samples(bench_summary(generated="2026-08-01T00:06:00+00:00"))

    with pytest.raises(BaselineError, match="future"):
        compare_baseline(baseline, current, now=now)


def test_capture_rejects_future_timestamp_beyond_tolerance():
    source = with_samples(bench_summary(generated="2026-08-01T00:06:00+00:00"))

    with pytest.raises(BaselineError, match="future"):
        capture_baseline(
            source, source_path="bench.json",
            now=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc),
        )


def test_compare_normalizes_metrics_across_different_sample_counts():
    base = with_samples(bench_summary(), count=3)
    current = with_samples(bench_summary(generated="2026-08-02T00:00:00+00:00"), count=5)
    for source in (base, current):
        for run in source["tasks"][0]["runs"]:
            run["arms"]["rig"]["token_usage"] = {"prompt_tokens": 100, "completion_tokens": 20}
            run["arms"]["rig"]["cost_usd"] = 0.05

    assert compare_baseline(capture_baseline(base, source_path="bench.json"), current)["status"] == "pass"


def test_scorecard_reports_nearest_rank_p50_and_safe_stop_rate():
    source = with_samples(bench_summary())
    for run, elapsed in zip(source["tasks"][0]["runs"], [3.0, 1.0, 2.0]):
        run["arms"]["rig"] = _arm("safe_stop", elapsed=elapsed, calls=0)
    baseline = capture_baseline(source, source_path="bench.json")
    rig = next(row for row in baseline["scorecard"] if row["mode"] == "rig")

    assert rig["elapsed_s"]["p50"] == 2.0
    assert rig["safe_stop_rate"] == 1.0


def test_scorecard_marks_calls_and_elapsed_unmeasured_when_missing():
    source = with_samples(bench_summary())
    for run in source["tasks"][0]["runs"]:
        run["arms"]["rig"].pop("elapsed_s")
        run["arms"]["rig"].pop("invocation_count")
    rig = next(row for row in capture_baseline(source, source_path="bench.json")["scorecard"]
               if row["mode"] == "rig")

    assert rig["elapsed_s"]["status"] == "unmeasured"
    assert rig["calls"]["status"] == "unmeasured"


@pytest.mark.parametrize("target", ["source", "scorecard", "provenance", "quality"])
def test_compare_detects_baseline_tampering(target):
    source = with_samples(bench_summary())
    baseline = capture_baseline(source, source_path="bench.json")
    if target == "source":
        baseline["source"]["provider_version"] = "forged"
    elif target == "scorecard":
        baseline["scorecard"][0]["task_success_rate"] = 1.0
    elif target == "provenance":
        baseline["provenance"]["normalized_evidence"][0]["outcome"] = "clean_pass"
    else:
        baseline["source"]["quality_evidence"] = False

    with pytest.raises(BaselineError, match="integrity|scorecard|quality"):
        compare_baseline(baseline, source)


def test_render_baseline_includes_main_scorecard_metrics():
    text = render_baseline(capture_baseline(with_samples(bench_summary()), source_path="bench.json"))

    assert "elapsed-p50=" in text
    assert "elapsed-p95=" in text
    assert "calls/valid=" in text
    assert "tokens/valid=" in text
    assert "cost-usd/valid=" in text


def test_cli_show_json_rejects_tampered_baseline(tmp_path):
    baseline = capture_baseline(with_samples(bench_summary()), source_path="bench.json")
    baseline["scorecard"][0]["samples"] = 99
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(baseline), encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))

    result = subprocess.run(
        [sys.executable, "-m", "rig_workbench.cli", "baseline", "show", str(path), "--json"],
        capture_output=True, text=True, cwd=tmp_path, env=env,
    )

    assert result.returncode == 2
    assert "scorecard does not match provenance" in result.stderr
