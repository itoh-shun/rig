"""Golden-fixture coverage for rig-wb sensor-bench (#330, deterministic
machine-sensor catch-rate benchmark). Locks the fixed corpus's results so a
sensor regression (or a corpus edit that silently drifts recall) shows up as
a test failure, not a stale claim."""

import json
import subprocess
import sys

from rig_workbench import sensor_bench


def test_every_positive_case_is_caught():
    result = sensor_bench.run_all()
    assert result["overall"]["recall"] == 1.0


def test_no_false_positives_on_the_negative_corpus():
    result = sensor_bench.run_all()
    assert result["overall"]["false_positives"] == 0


def test_every_sensor_category_is_present():
    result = sensor_bench.run_all()
    assert set(result["sensors"]) == {"secrets", "injection", "destructive"}


def test_run_corpus_flags_a_mismatch_as_incorrect():
    # A sensor that never catches a documented-positive case must be caught by
    # the harness itself, not silently averaged away.
    def never_catches(line, rel, lineno):
        return []
    r = sensor_bench.run_corpus(never_catches, (("known_bad", "irrelevant", True),))
    assert r["recall"] == 0.0
    assert r["cases"][0]["correct"] is False


def test_cli_json_output_matches_run_all():
    out = subprocess.run([sys.executable, "-m", "rig_workbench.cli", "sensor-bench", "--json"],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0
    data = json.loads(out.stdout)
    assert data["overall"]["recall"] == 1.0


def test_cli_text_report_mentions_zero_llm_calls():
    out = subprocess.run([sys.executable, "-m", "rig_workbench.cli", "sensor-bench"],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0
    assert "No LLM calls" in out.stdout
    assert "overall: recall 10/10" in out.stdout
