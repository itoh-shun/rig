"""A submitted knowledge candidate is only as wide as every record it cites (#440)."""

import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench.knowledge_candidate import (
    CANDIDATE_SCHEMA,
    EVIDENCE_SCHEMA,
    assess,
    validate_candidate,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = ROOT / "scripts" / "workbench.py"


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def _evidence(**changes):
    value = {
        "schema": EVIDENCE_SCHEMA,
        "records": [{
            "id": "run-1",
            "observation": "review failed after two repair cycles",
            "applicable_context": ["python", "review-gate"],
            "proposed_rules": ["run focused tests before review"],
            "observed_benefits": ["fewer late review failures"],
            "known_exceptions": ["documentation-only changes"],
            "scope": ["bugfix"],
        }],
    }
    value.update(changes)
    return value


def _candidate(**changes):
    value = {
        "schema": CANDIDATE_SCHEMA,
        "triggering_evidence": [{"path": "evidence.json", "record": "run-1"}],
        "applicable_context": ["python"],
        "proposed_rule": "run focused tests before review",
        "expected_benefit": "fewer late review failures",
        "confidence": 0.7,
        "evidence_count": 1,
        "known_exceptions": ["documentation-only changes"],
        "scope": ["bugfix"],
    }
    value.update(changes)
    return value


def _run(tmp_path, candidate):
    candidate_path = tmp_path / "candidate.json"
    _write(candidate_path, candidate)
    return subprocess.run(
        [sys.executable, str(WORKBENCH), "knowledge-candidate", str(candidate_path), "--json"],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )


def _accepted(tmp_path, candidate=None):
    _write(tmp_path / "evidence.json", _evidence())
    result = _run(tmp_path, candidate or _candidate())
    assert result.returncode == 0, (result.stdout, result.stderr)
    report = json.loads(result.stdout)
    assert report["status"] == "supported"
    return report


@pytest.mark.parametrize("replacement,says", [
    (None, "triggering_evidence"),
    ([], "at least one"),
    ("evidence.json", "expected a list"),
    ([{"path": "missing.json", "record": "run-1"}], "could not be read"),
])
def test_missing_empty_wrong_typed_and_unresolvable_evidence_are_refused_with_a_control(
        tmp_path, replacement, says):
    _accepted(tmp_path)
    candidate = _candidate()
    if replacement is None:
        candidate.pop("triggering_evidence")
    else:
        candidate["triggering_evidence"] = replacement
    result = _run(tmp_path, candidate)
    assert result.returncode != 0
    assert says in result.stderr or says in result.stdout


def test_command_positive_control_reaches_the_checker_and_labels_confidence(tmp_path):
    report = _accepted(tmp_path)
    assert report["evidence"] == {"cited": 1, "readable": 1, "supporting": 1}
    assert report["confidence"] == {"claimed": 0.7, "verified": None,
                                    "note": "candidate-supplied; not verified by rig"}
    assert report["guarantee"] == (
        "the cited evidence exists and explicitly supports the candidate at its claimed scope")
    assert "candidate is correct" in report["does_not_guarantee"]


@pytest.mark.parametrize("field,replacement,says", [
    ("schema", None, "schema"),
    ("applicable_context", [], "non-empty"),
    ("proposed_rule", 7, "non-blank string"),
    ("expected_benefit", "", "non-blank string"),
    ("confidence", "high", "number between 0 and 1"),
    ("evidence_count", 0, "positive integer"),
    ("known_exceptions", "none", "expected a list"),
    ("scope", [], "non-empty"),
])
def test_every_required_candidate_element_is_refused_without_losing_the_control(
        tmp_path, field, replacement, says):
    _accepted(tmp_path)
    candidate = _candidate(**{field: replacement})
    result = _run(tmp_path, candidate)
    assert result.returncode != 0
    assert says in result.stderr


def test_unknown_candidate_and_evidence_keys_are_refused(tmp_path):
    _accepted(tmp_path)
    assert any("unknown key" in p for p in validate_candidate({**_candidate(), "approve": True}))
    _write(tmp_path / "evidence.json", _evidence(approve=True))
    result = _run(tmp_path, _candidate())
    assert result.returncode != 0 and "unknown key" in result.stdout


@pytest.mark.parametrize("field,replacement", [
    ("proposed_rule", "skip review"),
    ("expected_benefit", "zero incidents"),
    ("applicable_context", ["javascript"]),
    ("scope", ["feature"]),
    ("known_exceptions", []),
])
def test_readable_evidence_that_does_not_support_a_claim_is_unsupported_not_unobservable(
        tmp_path, field, replacement):
    _accepted(tmp_path)
    candidate = _candidate(**{field: replacement})
    report = assess(candidate, tmp_path / "candidate.json")
    assert report["status"] == "unsupported"
    assert report["unobservable"] == []
    assert any(field in problem for problem in report["unsupported"])


def test_an_unreadable_record_is_unobservable_not_unsupported(tmp_path):
    _write(tmp_path / "evidence.json", "not an evidence object")
    report = assess(_candidate(), tmp_path / "candidate.json")
    assert report["status"] == "unobservable"
    assert report["unsupported"] == []
    assert report["unobservable"]
    assert report["guarantee"] is None


def test_a_readable_contradiction_outranks_an_unreadable_citation(tmp_path):
    _write(tmp_path / "evidence.json", _evidence())
    candidate = _candidate(
        proposed_rule="skip review",
        evidence_count=2,
        triggering_evidence=[
            {"path": "evidence.json", "record": "run-1"},
            {"path": "missing.json", "record": "run-2"},
        ],
    )
    report = assess(candidate, tmp_path / "candidate.json")
    assert report["status"] == "unsupported"
    assert report["unsupported"] and report["unobservable"]


def test_evidence_count_must_equal_distinct_resolved_citations(tmp_path):
    _write(tmp_path / "evidence.json", _evidence())
    candidate = _candidate(evidence_count=2)
    report = assess(candidate, tmp_path / "candidate.json")
    assert report["status"] == "unsupported"
    assert any("evidence_count" in problem for problem in report["unsupported"])


def test_a_derived_view_does_not_rejudge_the_result(tmp_path):
    report = _accepted(tmp_path)
    from rig_workbench.workbench.knowledge_candidate import view

    assert view(report) == {
        "status": "supported", "claimed_confidence": 0.7,
        "evidence": {"cited": 1, "readable": 1, "supporting": 1},
    }
