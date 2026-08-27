"""An external anomaly event is investigation-ready only when its citations support it."""

import json
import pathlib
import subprocess
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = ROOT / "scripts" / "workbench.py"
DELETE = object()


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def _evidence(**changes):
    value = {
        "schema": "rig.production-anomaly-evidence/v1",
        "records": [{
            "id": "sample-123",
            "source": {"system": "external-monitor", "event": "evt-123"},
            "observed_at": "2026-08-27T09:14:00+00:00",
            "observations": ["checkout 5xx rate was 4.2 pct"],
            "comparisons": ["checkout 5xx rate baseline was 0.4 pct"],
            "environments": ["production"],
            "components": ["checkout-api"],
        }],
    }
    value.update(changes)
    return value


def _event(**changes):
    value = {
        "schema": "rig.production-anomaly-event/v1",
        "id": "checkout-api-2026-08-27T09:15:00Z",
        "source": {"system": "external-monitor", "event": "evt-123"},
        "detected_at": "2026-08-27T09:16:00+00:00",
        "window": {
            "opens": "2026-08-27T09:10:00+00:00",
            "closes": "2026-08-27T09:15:00+00:00",
        },
        "signal": {
            "kind": "error-rate-regression",
            "observation": "checkout 5xx rate was 4.2 pct",
            "comparison": "checkout 5xx rate baseline was 0.4 pct",
        },
        "scope": {"environment": "production", "components": ["checkout-api"]},
        "evidence": [{"path": "anomaly-evidence.json", "record": "sample-123"}],
        "severity": "high",
        "confidence": 0.82,
    }
    value.update(changes)
    return value


def _run(tmp_path, event):
    event_path = tmp_path / "anomaly-event.json"
    _write(event_path, event)
    return _run_path(event_path)


def _run_path(event_path):
    return subprocess.run(
        [sys.executable, str(WORKBENCH), "anomaly-trigger", str(event_path), "--json"],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )


def _accepted(tmp_path, event=None):
    _write(tmp_path / "anomaly-evidence.json", _evidence())
    result = _run(tmp_path, event or _event())
    assert result.returncode == 0, (result.stdout, result.stderr)
    report = json.loads(result.stdout)
    assert report["status"] == "ready"
    return report


def _change(value, path, replacement):
    target = value
    parts = path.split(".")
    for part in parts[:-1]:
        target = target[int(part)] if isinstance(target, list) else target[part]
    leaf = parts[-1]
    if replacement is DELETE:
        target.pop(int(leaf)) if isinstance(target, list) else target.pop(leaf)
    elif isinstance(target, list):
        target[int(leaf)] = replacement
    else:
        target[leaf] = replacement
    return value


def test_command_positive_control_reaches_the_checker_and_separates_source_claims(tmp_path):
    report = _accepted(tmp_path)
    assert report["evidence"] == {"cited": 1, "readable": 1, "supporting": 1}
    assert report["event"]["source"] == {
        "claimed": {"system": "external-monitor", "event": "evt-123"},
        "verified": None,
    }
    assert report["claims"] == {
        "kind": {"claimed": "error-rate-regression", "verified": None},
        "severity": {"claimed": "high", "verified": None},
        "confidence": {"claimed": 0.82, "verified": None},
    }
    assert report["guarantee"] == (
        "the event declares the minimum investigation material and every cited record "
        "explicitly supports it")
    assert "real anomaly or regression" in report["does_not_guarantee"]


@pytest.mark.parametrize("replacement,status,says", [
    (None, 1, "evidence"),
    ([], 1, "at least one"),
    ("anomaly-evidence.json", 1, "expected a list"),
    ([{"path": "missing.json", "record": "sample-123"}], 2, "unobservable"),
])
def test_missing_empty_wrong_typed_and_unresolvable_evidence_are_refused_with_a_control(
        tmp_path, replacement, status, says):
    _accepted(tmp_path)
    event = _event()
    if replacement is None:
        event.pop("evidence")
    else:
        event["evidence"] = replacement
    result = _run(tmp_path, event)
    assert result.returncode == status
    assert says in result.stderr or says in result.stdout
    if status == 1:
        assert "[REJECTED]" in result.stderr


@pytest.mark.parametrize("path,replacement,says", [
    ("schema", DELETE, "schema"),
    ("id", "", "non-blank"),
    ("source", [], "expected an object"),
    ("source.system", " monitor ", "non-blank"),
    ("source.event", DELETE, "source.event"),
    ("detected_at", "2026-08-27T09:16:00", "timezone offset"),
    ("window", "five minutes", "expected an object"),
    ("window.opens", DELETE, "window.opens"),
    ("window.closes", "", "timezone offset"),
    ("signal", None, "expected an object"),
    ("signal.kind", "", "non-blank"),
    ("signal.observation", DELETE, "signal.observation"),
    ("signal.comparison", 4.2, "non-blank"),
    ("scope", [], "expected an object"),
    ("scope.environment", "", "non-blank"),
    ("scope.components", [], "non-empty"),
    ("severity", 3, "non-blank"),
    ("confidence", True, "number between 0 and 1"),
    ("confidence", 1.1, "number between 0 and 1"),
    ("evidence.0.path", "/tmp/evidence.json", "relative path"),
    ("evidence.0.record", "", "non-blank"),
])
def test_every_required_event_element_is_refused_without_losing_the_cli_control(
        tmp_path, path, replacement, says):
    _accepted(tmp_path)
    result = _run(tmp_path, _change(_event(), path, replacement))
    assert result.returncode == 1
    assert "[REJECTED]" in result.stderr
    assert says in result.stderr


@pytest.mark.parametrize("path", [
    "schema", "id", "source", "detected_at", "window", "signal", "scope", "evidence",
    "severity", "confidence", "source.system", "source.event", "window.opens", "window.closes",
    "signal.kind", "signal.observation", "signal.comparison", "scope.environment",
    "scope.components", "evidence.0.path", "evidence.0.record",
])
def test_absence_of_each_required_event_field_is_rejected(tmp_path, path):
    _accepted(tmp_path)
    result = _run(tmp_path, _change(_event(), path, DELETE))
    assert result.returncode == 1
    assert "[REJECTED]" in result.stderr
    assert path.split(".")[-1] in result.stderr


@pytest.mark.parametrize("path", ["", "source", "window", "signal", "scope", "evidence.0"])
def test_unknown_event_keys_at_every_object_boundary_are_refused(tmp_path, path):
    _accepted(tmp_path)
    event = _event()
    target = event
    if path:
        for part in path.split("."):
            target = target[int(part)] if isinstance(target, list) else target[part]
    target["conclusion"] = "confirmed regression"
    result = _run(tmp_path, event)
    assert result.returncode == 1
    assert "unknown key" in result.stderr


@pytest.mark.parametrize("path,replacement,says", [
    ("window.closes", "2026-08-27T09:09:00+00:00", "opens must be at or before closes"),
    ("detected_at", "2026-08-27T09:09:00+00:00", "at or after window.opens"),
    ("scope.components", ["checkout-api", "checkout-api"], "unique"),
    ("evidence", [
        {"path": "anomaly-evidence.json", "record": "sample-123"},
        {"path": "anomaly-evidence.json", "record": "sample-123"},
    ], "duplicate citation"),
])
def test_event_relations_and_uniqueness_are_refused(tmp_path, path, replacement, says):
    _accepted(tmp_path)
    result = _run(tmp_path, _change(_event(), path, replacement))
    assert result.returncode == 1
    assert says in result.stderr


@pytest.mark.parametrize("path,replacement,says", [
    ("schema", DELETE, "schema"),
    ("records", "sample-123", "expected a list"),
    ("records", [], "at least one"),
    ("records.0.id", "", "non-blank"),
    ("records.0.source", [], "expected an object"),
    ("records.0.source.system", DELETE, "source.system"),
    ("records.0.source.event", "", "non-blank"),
    ("records.0.observed_at", "2026-08-27T09:14:00", "timezone offset"),
    ("records.0.observations", [], "non-empty"),
    ("records.0.comparisons", "baseline", "non-empty"),
    ("records.0.environments", ["production", "production"], "unique"),
    ("records.0.components", [3], "non-blank"),
])
def test_invalid_evidence_shape_is_unobservable_with_a_control(
        tmp_path, path, replacement, says):
    _accepted(tmp_path)
    _write(tmp_path / "anomaly-evidence.json", _change(_evidence(), path, replacement))
    result = _run(tmp_path, _event())
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["status"] == "unobservable"
    assert report["unmet"] == []
    assert says in " ".join(report["unobservable"])


@pytest.mark.parametrize("path", [
    "schema", "records", "records.0.id", "records.0.source", "records.0.observed_at",
    "records.0.observations", "records.0.comparisons", "records.0.environments",
    "records.0.components", "records.0.source.system", "records.0.source.event",
])
def test_absence_of_each_required_evidence_field_is_unobservable(tmp_path, path):
    _accepted(tmp_path)
    _write(tmp_path / "anomaly-evidence.json", _change(_evidence(), path, DELETE))
    result = _run(tmp_path, _event())
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["status"] == "unobservable"
    assert path.split(".")[-1] in " ".join(report["unobservable"])


@pytest.mark.parametrize("path", ["", "records.0", "records.0.source"])
def test_unknown_evidence_keys_at_every_object_boundary_are_unobservable(tmp_path, path):
    _accepted(tmp_path)
    evidence = _evidence()
    target = evidence
    if path:
        for part in path.split("."):
            target = target[int(part)] if isinstance(target, list) else target[part]
    target["verified"] = True
    _write(tmp_path / "anomaly-evidence.json", evidence)
    result = _run(tmp_path, _event())
    assert result.returncode == 2
    assert "unknown key" in result.stdout


@pytest.mark.parametrize("path,replacement,says", [
    ("records.0.source.system", "different-monitor", "source"),
    ("records.0.observed_at", "2026-08-27T09:09:59+00:00", "observed_at"),
    ("records.0.observations", ["5xx increased"], "signal.observation"),
    ("records.0.comparisons", ["no baseline"], "signal.comparison"),
    ("records.0.environments", ["staging"], "scope.environment"),
    ("records.0.components", ["catalog-api"], "scope.components"),
])
def test_each_readable_support_mismatch_is_unmet_not_unobservable(
        tmp_path, path, replacement, says):
    _accepted(tmp_path)
    _write(tmp_path / "anomaly-evidence.json", _change(_evidence(), path, replacement))
    result = _run(tmp_path, _event())
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["status"] == "unmet"
    assert report["unobservable"] == []
    assert report["unmet"] and says in " ".join(report["unmet"])
    assert report["guarantee"] is None


@pytest.mark.parametrize("evidence,record,says", [
    ("missing", "sample-123", "could not be read"),
    ({"schema": "wrong", "records": []}, "sample-123", "schema"),
    (_evidence(), "not-present", "exactly once"),
    ({"schema": "rig.production-anomaly-evidence/v1", "records": [
        _evidence()["records"][0], _evidence()["records"][0],
    ]}, "sample-123", "duplicate"),
])
def test_unreadable_invalid_and_unresolved_records_are_unobservable(
        tmp_path, evidence, record, says):
    _accepted(tmp_path)
    if evidence == "missing":
        (tmp_path / "anomaly-evidence.json").unlink()
    else:
        _write(tmp_path / "anomaly-evidence.json", evidence)
    event = _event(evidence=[{"path": "anomaly-evidence.json", "record": record}])
    result = _run(tmp_path, event)
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["status"] == "unobservable"
    assert report["unmet"] == []
    assert says in " ".join(report["unobservable"])


def test_readable_mismatch_outranks_unreadable_citation_without_hiding_either(tmp_path):
    _accepted(tmp_path)
    evidence = _evidence()
    evidence["records"][0]["comparisons"] = ["no baseline"]
    _write(tmp_path / "anomaly-evidence.json", evidence)
    event = _event(evidence=[
        {"path": "anomaly-evidence.json", "record": "sample-123"},
        {"path": "missing.json", "record": "sample-456"},
    ])
    result = _run(tmp_path, event)
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["status"] == "unmet"
    assert report["unmet"] and report["unobservable"]


def test_event_rejection_enumerates_all_shape_problems(tmp_path):
    _accepted(tmp_path)
    event = _event(id="", severity=None, confidence="high")
    result = _run(tmp_path, event)
    assert result.returncode == 1
    assert result.stderr.count("[REJECTED]") == 3
    assert all(field in result.stderr for field in ("id", "severity", "confidence"))


def test_duplicate_json_keys_are_never_silently_accepted(tmp_path):
    _accepted(tmp_path)
    event_path = tmp_path / "duplicate-event.json"
    event_path.write_text(json.dumps(_event()).replace(
        '"severity": "high"', '"severity": "low", "severity": "high"') + "\n",
        encoding="utf-8")
    event_result = _run_path(event_path)
    assert event_result.returncode == 1
    assert "[REJECTED]" in event_result.stderr
    assert "names 'severity' twice" in event_result.stderr

    evidence_path = tmp_path / "anomaly-evidence.json"
    evidence_path.write_text(json.dumps(_evidence()).replace(
        '"observed_at":', '"observed_at": "2026-08-27T09:13:00+00:00", "observed_at":', 1)
        + "\n", encoding="utf-8")
    evidence_result = _run(tmp_path, _event())
    assert evidence_result.returncode == 2
    assert "names 'observed_at' twice" in evidence_result.stdout
