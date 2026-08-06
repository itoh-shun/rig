import json
import pathlib

import pytest

from rig_workbench import coverage

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _shipped() -> dict:
    return coverage.load_map(ROOT / coverage.DEFAULT_MAP)


def test_shipped_map_is_consistent_with_the_repository():
    """Every path and command the map cites must still exist. This is the drift guard."""
    assert coverage.validate(_shipped(), ROOT) == []


def test_shipped_map_is_canonical_json():
    raw = (ROOT / coverage.DEFAULT_MAP).read_text(encoding="utf-8")
    assert raw.endswith("\n")
    expected = json.dumps(json.loads(raw), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    assert raw == expected


def test_every_item_declares_a_source_and_a_scope():
    for item in _shipped()["items"]:
        assert item["source"].startswith("02"), item["id"]
        assert item["scope"] in coverage.SCOPES


def test_status_never_upgrades_a_claim_by_description():
    """file-only evidence is 'declared'; only executable evidence reaches 'measured'."""
    declared = {"evidence": [{"kind": "file", "path": "README.md"}]}
    assert coverage.item_status(declared) == coverage.STATUS_DECLARED

    planned = {"evidence": [{"kind": "planned", "spec": "x" * 40}]}
    assert coverage.item_status(planned) == coverage.STATUS_PLANNED

    paid = {"evidence": [{"kind": "paid", "argv_hint": "x", "pass_condition": "y"}]}
    assert coverage.item_status(paid) == coverage.STATUS_PAID_ONLY

    measured = {"evidence": [{"kind": "file", "path": "README.md"},
                             {"kind": "pytest", "path": "tests/test_coverage_map.py"}]}
    assert coverage.item_status(measured) == coverage.STATUS_MEASURED


def test_outstanding_work_downgrades_measured_to_partial():
    """Running evidence does not absolve the rest of the requirement."""
    for outstanding in ({"kind": "planned", "spec": "x" * 40},
                        {"kind": "paid", "argv_hint": "x", "pass_condition": "y"}):
        item = {"evidence": [{"kind": "pytest", "path": "tests/test_coverage_map.py"}, outstanding]}
        assert coverage.item_status(item) == coverage.STATUS_PARTIAL


def test_rejects_unknown_evidence_kind(tmp_path):
    problems = coverage.validate(
        {"items": [{"id": "a", "source": "02a §1", "requirement": "r", "scope": "rig",
                    "evidence": [{"kind": "vibes"}]}]},
        tmp_path,
    )
    assert any("unknown evidence kind" in p for p in problems)


def test_rejects_command_outside_the_allowlist(tmp_path):
    problems = coverage.validate(
        {"items": [{"id": "a", "source": "02a §1", "requirement": "r", "scope": "rig",
                    "evidence": [{"kind": "command", "argv": ["curl", "https://example.com"]}]}]},
        tmp_path,
    )
    assert any("not allowlisted" in p for p in problems)


def test_rejects_path_escape(tmp_path):
    problems = coverage.validate(
        {"items": [{"id": "a", "source": "02a §1", "requirement": "r", "scope": "rig",
                    "evidence": [{"kind": "file", "path": "../etc/passwd"}]}]},
        tmp_path,
    )
    assert any("without '..'" in p for p in problems)


def test_rejects_missing_path(tmp_path):
    problems = coverage.validate(
        {"items": [{"id": "a", "source": "02a §1", "requirement": "r", "scope": "rig",
                    "evidence": [{"kind": "pytest", "path": "tests/test_nope.py"}]}]},
        tmp_path,
    )
    assert any("does not exist" in p for p in problems)


def test_planned_evidence_must_carry_a_real_spec(tmp_path):
    problems = coverage.validate(
        {"items": [{"id": "a", "source": "02a §1", "requirement": "r", "scope": "rig",
                    "evidence": [{"kind": "planned", "spec": "later"}]}]},
        tmp_path,
    )
    assert any("what would close it" in p for p in problems)


def test_rejects_duplicate_ids(tmp_path):
    item = {"id": "dup", "source": "02a §1", "requirement": "r", "scope": "rig",
            "evidence": [{"kind": "planned", "spec": "x" * 40}]}
    problems = coverage.validate({"items": [item, dict(item)]}, tmp_path)
    assert any("duplicate item id" in p for p in problems)


def test_rejects_unsupported_version(tmp_path):
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"coverage_map_version": 99, "items": []}), encoding="utf-8")
    with pytest.raises(coverage.MapError):
        coverage.load_map(path)


def test_summary_counts_every_item_once():
    data = _shipped()
    summary = coverage.summarise(data)
    assert summary["total"] == len(data["items"])
    assert sum(summary["by_status"].values()) == summary["total"]
    assert summary["host_scope"] >= 1


def test_markdown_render_lists_every_item():
    data = _shipped()
    rendered = coverage._render_markdown(data)
    for item in data["items"]:
        assert item["id"] in rendered


def test_check_mode_passes_on_the_shipped_map(capsys):
    assert coverage.cmd_coverage([]) == 0
    capsys.readouterr()
