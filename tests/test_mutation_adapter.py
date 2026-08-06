import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("mutation_adapter", ROOT / "scripts/mutation_adapter.py")
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


def _elements(statuses: list[str]) -> str:
    return json.dumps({
        "schemaVersion": "1.0",
        "files": {"src/a.ts": {"language": "typescript", "source": "x",
                               "mutants": [{"id": str(i), "status": s} for i, s in enumerate(statuses)]}},
    })


def _junit(entries: list[str]) -> str:
    cases = []
    for index, kind in enumerate(entries):
        inner = "" if kind == "killed" else f"<{kind} message='m'/>"
        cases.append(f"<testcase classname='mutmut' name='{index}'>{inner}</testcase>")
    return f"<testsuites><testsuite name='mutmut'>{''.join(cases)}</testsuite></testsuites>"


def test_elements_counts_timeout_as_detected_and_nocoverage_as_undetected(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(_elements(["Killed", "Timeout", "Survived", "NoCoverage"]), encoding="utf-8")
    counts = adapter.parse_elements(path)
    assert (counts["detected"], counts["undetected"], counts["invalid"]) == (2, 2, 0)
    assert adapter.score_of(counts) == 0.5


def test_elements_excludes_invalid_mutants_from_the_denominator(tmp_path):
    """A mutant that would not compile is not a hole in the suite."""
    path = tmp_path / "r.json"
    path.write_text(_elements(["Killed", "CompileError", "Ignored", "RuntimeError"]), encoding="utf-8")
    counts = adapter.parse_elements(path)
    assert counts["invalid"] == 3
    assert adapter.score_of(counts) == 1.0


def test_elements_rejects_an_unknown_status(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(_elements(["Vibed"]), encoding="utf-8")
    with pytest.raises(adapter.ReportError, match="unknown mutant status"):
        adapter.parse_elements(path)


def test_elements_rejects_a_report_without_files(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"schemaVersion": "1.0"}), encoding="utf-8")
    with pytest.raises(adapter.ReportError, match="no 'files' object"):
        adapter.parse_elements(path)


def test_junit_maps_failure_to_survived_and_skipped_to_uncovered(tmp_path):
    path = tmp_path / "r.xml"
    path.write_text(_junit(["killed", "killed", "failure", "skipped", "error"]), encoding="utf-8")
    counts = adapter.parse_junit(path)
    assert (counts["detected"], counts["undetected"], counts["invalid"]) == (2, 2, 1)
    assert counts["by_status"] == {"killed": 2, "survived": 1, "skipped": 1, "error": 1}


def test_junit_refuses_entity_declarations(tmp_path):
    """Report files come from CI; entity expansion is not a feature we need."""
    path = tmp_path / "r.xml"
    path.write_text(
        '<?xml version="1.0"?><!DOCTYPE t [<!ENTITY a "aaaa">]>'
        "<testsuites><testsuite><testcase name='1'/></testsuite></testsuites>",
        encoding="utf-8",
    )
    with pytest.raises(adapter.ReportError, match="DOCTYPE or ENTITY"):
        adapter.parse_junit(path)


def test_junit_rejects_a_report_with_no_test_cases(tmp_path):
    path = tmp_path / "r.xml"
    path.write_text("<testsuites></testsuites>", encoding="utf-8")
    with pytest.raises(adapter.ReportError, match="no <testcase>"):
        adapter.parse_junit(path)


def test_oversized_report_is_refused(tmp_path, monkeypatch):
    path = tmp_path / "r.json"
    path.write_text(_elements(["Killed"]), encoding="utf-8")
    monkeypatch.setattr(adapter, "MAX_REPORT_BYTES", 1)
    with pytest.raises(adapter.ReportError, match="larger than"):
        adapter.parse_elements(path)


def test_missing_report_is_refused(tmp_path):
    with pytest.raises(adapter.ReportError, match="file not found"):
        adapter.parse_elements(tmp_path / "nope.json")


def test_first_run_passes_and_says_there_is_nothing_to_compare():
    counts = {"detected": 7, "undetected": 3, "invalid": 0, "format": "elements"}
    result = adapter.evaluate(counts, None, 0.0)
    assert result["status"] == "passed"
    assert "no baseline" in result["detail"]


def test_a_drop_is_a_warning_never_a_failure():
    """Equivalent mutants make the absolute number noisy; only the direction is actionable."""
    counts = {"detected": 6, "undetected": 4, "invalid": 0, "format": "elements"}
    result = adapter.evaluate(counts, {"score": 0.7}, 0.0)
    assert result["status"] == "warning"
    assert "fell to" in result["detail"]
    assert result["status"] != "failed"


def test_tolerance_absorbs_a_small_drop():
    counts = {"detected": 68, "undetected": 32, "invalid": 0, "format": "elements"}
    assert adapter.evaluate(counts, {"score": 0.70}, 0.05)["status"] == "passed"
    assert adapter.evaluate(counts, {"score": 0.70}, 0.0)["status"] == "warning"


def test_an_improvement_passes():
    counts = {"detected": 9, "undetected": 1, "invalid": 0, "format": "elements"}
    assert adapter.evaluate(counts, {"score": 0.7}, 0.0)["status"] == "passed"


def test_a_report_with_no_valid_mutants_warns_instead_of_scoring_zero():
    counts = {"detected": 0, "undetected": 0, "invalid": 4, "format": "elements"}
    result = adapter.evaluate(counts, {"score": 0.9}, 0.0)
    assert result["status"] == "warning"
    assert result["score"] is None


def test_baseline_round_trip(tmp_path):
    path = tmp_path / "baseline.json"
    counts = {"detected": 7, "undetected": 3, "invalid": 0, "format": "elements"}
    adapter.write_baseline(path, counts, 0.7)
    assert adapter.load_baseline(path)["score"] == 0.7
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_malformed_baseline_is_treated_as_absent(tmp_path):
    path = tmp_path / "baseline.json"
    path.write_text("{not json", encoding="utf-8")
    assert adapter.load_baseline(path) is None
    path.write_text(json.dumps({"score": "high"}), encoding="utf-8")
    assert adapter.load_baseline(path) is None
