import json
import pathlib

import pytest

from rig_workbench import asvs

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _shipped() -> dict:
    return asvs.load_map(ROOT / asvs.DEFAULT_MAP)


def test_shipped_map_is_consistent_with_the_repository():
    """Referenced sensors, reviewers and drill classes must still exist."""
    assert asvs.validate(_shipped(), ROOT) == []


def test_shipped_map_covers_every_chapter():
    ids = [chapter["id"] for chapter in _shipped()["chapters"]]
    assert ids == list(asvs.EXPECTED_CHAPTERS)


def test_shipped_map_is_canonical_json():
    raw = (ROOT / asvs.DEFAULT_MAP).read_text(encoding="utf-8")
    assert raw == json.dumps(json.loads(raw), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def test_every_chapter_states_what_is_not_covered():
    """The gaps are the deliverable; a row without them is decoration."""
    for chapter in _shipped()["chapters"]:
        assert chapter["not_covered"].strip()


def test_blind_chapters_are_reported_rather_than_hidden():
    summary = asvs.summarise(_shipped())
    assert summary["by_strength"]["none"] == len(summary["blind"])
    assert summary["blind"], "a map with no blind chapters would mean rig sees all of ASVS"


def test_a_chapter_cannot_claim_coverage_without_a_mechanism(tmp_path):
    problems = asvs.validate(
        {"chapters": [{"id": "V1", "title": "t", "not_covered": "n",
                       "strength": "partial", "covered_by": []}]},
        tmp_path,
    )
    assert any("needs at least one covering mechanism" in p for p in problems)


def test_a_blind_chapter_cannot_list_mechanisms(tmp_path):
    problems = asvs.validate(
        {"chapters": [{"id": "V1", "title": "t", "not_covered": "n", "strength": "none",
                       "covered_by": [{"kind": "sensor", "ref": "x"}]}]},
        tmp_path,
    )
    assert any("cannot list covering mechanisms" in p for p in problems)


def test_a_drill_class_that_left_the_corpus_is_flagged(tmp_path):
    """This is the drift the check exists for: the seed is gone, the claim remains."""
    (tmp_path / "skills/engine/facets/instructions").mkdir(parents=True)
    (tmp_path / asvs.DRILL_CORPUS).write_text("| 認可漏れ | ... |", encoding="utf-8")
    problems = asvs.validate(
        {"chapters": [{"id": "V8", "title": "t", "not_covered": "n", "strength": "partial",
                       "covered_by": [{"kind": "drill-class", "ref": "存在しない種"}]}]},
        tmp_path,
    )
    assert any("not in the shipped corpus" in p for p in problems)


def test_a_missing_referenced_file_is_flagged(tmp_path):
    problems = asvs.validate(
        {"chapters": [{"id": "V8", "title": "t", "not_covered": "n", "strength": "partial",
                       "covered_by": [{"kind": "sast", "ref": "scripts/gone.py"}]}]},
        tmp_path,
    )
    assert any("does not exist" in p for p in problems)


def test_path_escape_is_refused(tmp_path):
    problems = asvs.validate(
        {"chapters": [{"id": "V8", "title": "t", "not_covered": "n", "strength": "partial",
                       "covered_by": [{"kind": "sast", "ref": "../../etc/passwd.py"}]}]},
        tmp_path,
    )
    assert any("without '..'" in p for p in problems)


def test_unknown_mechanism_kind_is_refused(tmp_path):
    problems = asvs.validate(
        {"chapters": [{"id": "V8", "title": "t", "not_covered": "n", "strength": "partial",
                       "covered_by": [{"kind": "vibes", "ref": "x"}]}]},
        tmp_path,
    )
    assert any("unknown mechanism kind" in p for p in problems)


def test_missing_chapters_are_reported(tmp_path):
    problems = asvs.validate(
        {"chapters": [{"id": "V1", "title": "t", "not_covered": "n", "strength": "none",
                       "covered_by": []}]},
        tmp_path,
    )
    assert any("chapters missing from the map" in p for p in problems)


def test_unsupported_version_is_refused(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"asvs_map_version": 99, "chapters": []}), encoding="utf-8")
    with pytest.raises(asvs.AsvsMapError):
        asvs.load_map(path)


def test_check_mode_passes_on_the_shipped_map(capsys):
    assert asvs.cmd_asvs(["--check"]) == 0
    capsys.readouterr()
