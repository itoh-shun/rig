import json

import pytest

from rig_workbench import mutation as adapter


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


# ── mutmut 3.x (`mutmut export-cicd-stats`) ─────────────────────────────
# Shape captured from a real run: mutmut 3.7.0 on a 22-mutant corpus.


def _mutmut(**overrides) -> str:
    counts = {"killed": 5, "survived": 17, "total": 22, "no_tests": 0, "skipped": 0,
              "suspicious": 0, "timeout": 0, "check_was_interrupted_by_user": 0, "segfault": 0}
    counts.update(overrides)
    return json.dumps(counts)


def test_mutmut_reads_the_real_cicd_stats_shape(tmp_path):
    path = tmp_path / "mutmut-cicd-stats.json"
    path.write_text(_mutmut(), encoding="utf-8")
    counts = adapter.parse_mutmut(path)
    assert (counts["detected"], counts["undetected"], counts["invalid"]) == (5, 17, 0)
    assert adapter.score_of(counts) == 0.2273


def test_mutmut_counts_timeout_as_detected_and_no_tests_as_undetected(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(_mutmut(killed=4, survived=2, timeout=1, no_tests=3, total=10), encoding="utf-8")
    counts = adapter.parse_mutmut(path)
    assert (counts["detected"], counts["undetected"]) == (5, 5)


def test_mutmut_excludes_suspicious_and_segfault_from_the_denominator(tmp_path):
    """Neither verdict: the mutant confused the run rather than escaping the suite."""
    path = tmp_path / "s.json"
    path.write_text(_mutmut(killed=5, survived=5, suspicious=2, segfault=1, total=13), encoding="utf-8")
    counts = adapter.parse_mutmut(path)
    assert counts["invalid"] == 3
    assert adapter.score_of(counts) == 0.5


def test_mutmut_refuses_a_report_whose_counts_do_not_add_up(tmp_path):
    """A status this adapter does not know about would shorten the denominator silently."""
    path = tmp_path / "s.json"
    path.write_text(_mutmut(killed=5, survived=5, total=99), encoding="utf-8")
    with pytest.raises(adapter.ReportError, match="counts sum to"):
        adapter.parse_mutmut(path)


def test_mutmut_rejects_an_unknown_status(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"killed": 1, "survived": 1, "vibed": 1}), encoding="utf-8")
    with pytest.raises(adapter.ReportError, match="unknown mutmut status"):
        adapter.parse_mutmut(path)


def test_mutmut_rejects_a_negative_or_non_integer_count(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"killed": 1, "survived": -2}), encoding="utf-8")
    with pytest.raises(adapter.ReportError, match="non-negative integer"):
        adapter.parse_mutmut(path)


def test_mutmut_rejects_a_report_that_is_not_cicd_stats(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"schemaVersion": "1.0", "files": {}}), encoding="utf-8")
    with pytest.raises(adapter.ReportError, match="export-cicd-stats"):
        adapter.parse_mutmut(path)


def test_all_three_formats_are_selectable_from_the_cli():
    assert set(adapter.PARSERS) == {"elements", "mutmut", "junit"}


def test_elements_ignores_the_extra_fields_a_real_stryker_report_carries(tmp_path):
    """Shape captured from Stryker 9.6.1: mutants carry more than status, and top-level
    keys (config, framework, testFiles, thresholds, projectRoot) sit beside `files`."""
    path = tmp_path / "mutation.json"
    path.write_text(json.dumps({
        "schemaVersion": "1.0",
        "config": {"testRunner": "command"},
        "framework": {"name": "Stryker", "version": "9.6.1"},
        "projectRoot": "/tmp/demo",
        "testFiles": {},
        "thresholds": {"high": 80, "low": 60, "break": None},
        "files": {"src/pricing.js": {"language": "javascript", "source": "x", "mutants": [
            {"id": "0", "location": {"start": {"line": 2, "column": 3}},
             "mutatorName": "EqualityOperator", "replacement": "quantity < 0",
             "status": "Killed", "killedBy": ["1"], "statusReason": "expected",
             "testsCompleted": 1},
            {"id": "1", "location": {"start": {"line": 12, "column": 10}},
             "mutatorName": "ConditionalExpression", "replacement": "true",
             "status": "Survived", "testsCompleted": 3},
        ]}},
    }), encoding="utf-8")
    counts = adapter.parse_elements(path)
    assert (counts["detected"], counts["undetected"], counts["invalid"]) == (1, 1, 0)
    assert adapter.score_of(counts) == 0.5


# ── finding the report and the tool without being told ──────────────────
# What makes this a rig command rather than a script: the operator names neither
# the format nor the path, so a wrong guess here is a wrong answer, not a usage error.


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")


def test_sniff_reads_the_shape_from_the_file_not_the_name(tmp_path):
    """A stryker report saved under a mutmut-ish name must still parse as elements."""
    path = tmp_path / "mutmut-cicd-stats.json"
    _write(path, _elements(["Killed"]))
    assert adapter.sniff_format(path) == "elements"

    xml = tmp_path / "r.json"
    _write(xml, _junit(["killed"]))
    assert adapter.sniff_format(xml) == "junit"


def test_sniff_returns_none_for_something_that_is_not_a_report(tmp_path):
    path = tmp_path / "r.json"
    _write(path, {"coverage": 91})
    assert adapter.sniff_format(path) is None


def test_detect_report_finds_the_stryker_default_location(tmp_path):
    _write(tmp_path / "reports/mutation/mutation.json", _elements(["Killed", "Survived"]))
    found = adapter.detect_report(tmp_path)
    assert found is not None
    path, fmt = found
    assert path.name == "mutation.json"
    assert fmt == "elements"


def test_detect_report_finds_the_mutmut_default_location(tmp_path):
    _write(tmp_path / "mutants/mutmut-cicd-stats.json", _mutmut())
    path, fmt = adapter.detect_report(tmp_path)
    assert fmt == "mutmut"
    assert path.parent.name == "mutants"


def test_detect_report_skips_a_candidate_that_is_not_a_report(tmp_path):
    """An unrelated mutation.json must not be scored as if it were one."""
    _write(tmp_path / "mutation.json", {"unrelated": True})
    _write(tmp_path / "mutants/mutmut-cicd-stats.json", _mutmut())
    path, fmt = adapter.detect_report(tmp_path)
    assert fmt == "mutmut"


def test_detect_report_returns_none_on_a_project_with_no_report(tmp_path):
    assert adapter.detect_report(tmp_path) is None


def test_detect_runner_requires_evidence_not_a_guess(tmp_path):
    """A bare pyproject.toml is not consent to run a long mutation job."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    assert adapter.detect_runner(tmp_path) is None


def test_detect_runner_recognises_stryker_from_its_config(tmp_path):
    (tmp_path / "stryker.conf.json").write_text("{}", encoding="utf-8")
    runner = adapter.detect_runner(tmp_path)
    assert runner["tool"] == "stryker"
    assert runner["commands"] == (["npx", "stryker", "run"],)
    assert "stryker.conf.json" in runner["why"]


def test_detect_runner_recognises_stryker_from_the_dependency(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"devDependencies": {"@stryker-mutator/core": "^9.0.0"}}), encoding="utf-8"
    )
    assert adapter.detect_runner(tmp_path)["tool"] == "stryker"


def test_detect_runner_recognises_mutmut_and_exports_stats_after_running(tmp_path):
    (tmp_path / "setup.cfg").write_text("[mutmut]\npaths_to_mutate=src/\n", encoding="utf-8")
    runner = adapter.detect_runner(tmp_path)
    assert runner["tool"] == "mutmut"
    assert runner["commands"][-1] == ["mutmut", "export-cicd-stats"]


def test_run_tool_says_which_binary_is_missing(tmp_path):
    runner = {"tool": "stryker", "commands": (["definitely-not-a-real-binary"],)}
    with pytest.raises(adapter.ReportError, match="not on PATH"):
        adapter.run_tool(runner, tmp_path, echo=lambda _: None)


# ── the command itself ──────────────────────────────────────────────────


def test_cli_scores_a_report_it_found_by_itself(tmp_path, capsys):
    _write(tmp_path / "reports/mutation/mutation.json", _elements(["Killed", "Killed", "Survived"]))
    assert adapter.cmd_mutation(["--repo", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "66.7%" in out
    assert "elements" in out


def test_cli_explains_where_it_looked_when_there_is_no_report(tmp_path, capsys):
    assert adapter.cmd_mutation(["--repo", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "reports/mutation/mutation.json" in err
    assert "mutants/mutmut-cicd-stats.json" in err


def test_cli_still_accepts_the_1_31_x_positional_form(tmp_path, capsys):
    path = tmp_path / "r.json"
    _write(path, _elements(["Killed", "Survived"]))
    assert adapter.cmd_mutation(["elements", str(path), "--repo", str(tmp_path)]) == 0
    assert "50.0%" in capsys.readouterr().out


def test_cli_records_and_then_compares_against_the_baseline(tmp_path, capsys):
    strong = tmp_path / "strong.json"
    _write(strong, _elements(["Killed", "Killed", "Killed", "Survived"]))
    assert adapter.cmd_mutation(["--repo", str(tmp_path), "--report", str(strong),
                                 "--record-baseline"]) == 0
    capsys.readouterr()

    weak = tmp_path / "weak.json"
    _write(weak, _elements(["Killed", "Survived", "Survived", "Survived"]))
    assert adapter.cmd_mutation(["--repo", str(tmp_path), "--report", str(weak), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "warning"
    assert "fell to" in payload["detail"]


def test_cli_refuses_to_guess_the_format_of_an_unreadable_report(tmp_path, capsys):
    path = tmp_path / "r.json"
    _write(path, {"nothing": "recognisable"})
    assert adapter.cmd_mutation(["--repo", str(tmp_path), "--report", str(path)]) == 2
    assert "--format" in capsys.readouterr().err


def test_cli_run_without_a_detected_tool_does_not_invent_one(tmp_path, capsys):
    assert adapter.cmd_mutation(["--repo", str(tmp_path), "--run"]) == 2
    err = capsys.readouterr().err
    assert "does not run mutation" in err


def test_apply_does_not_claim_success_when_the_workbench_failed(tmp_path, monkeypatch, capsys):
    """1.31.0 matched one failure string and reported success for every other one."""
    class _Failed:
        returncode = 1
        stdout = ""
        stderr = "[ERROR] task 'RIG-NOPE' not found\n"

    monkeypatch.setattr(adapter, "_workbench_script", lambda: tmp_path / "workbench.py")
    monkeypatch.setattr(adapter.subprocess, "run", lambda *a, **k: _Failed())
    counts = {"detected": 1, "undetected": 1, "invalid": 0, "format": "elements"}
    result = {"status": "warning", "score": 0.5, "detail": "d"}
    assert adapter.apply_to_gate(result, counts, "RIG-NOPE") == 1
    assert "applied" not in capsys.readouterr().out
