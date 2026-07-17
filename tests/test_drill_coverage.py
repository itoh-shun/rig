"""Unit tests for rig_workbench.validation.drill (/rig:drill coverage check; #266).

The check is coverage guidance: it may WARN but must never FAIL. Fixtures are
synthetic recipes + a synthetic seed catalog so the tests do not depend on the
shipped tree.
"""

import pytest

from rig_workbench.validation import state
from rig_workbench.validation.drill import check_drill_coverage, parse_seed_perspectives

CATALOG = """# instruction: drill

| 種の class | 例 | 検出すべき観点 | 期待 severity | 期待 blocking |
|---|---|---|---|---|
| 認可漏れ | x | security | High | Blocking |
| N+1 | x | performance | Medium | Non-blocking |
| 過剰抽象 | x | design / lazy-senior | Low | Non-blocking |
"""


def recipe_md(name: str, steps_yaml: str) -> str:
    return (f"---\nname: {name}\ndescription: t\nscope: shipped\n"
            f"autonomy: interactive\nsteps:\n{steps_yaml}---\n\n# {name}\n")


@pytest.fixture
def catalog(tmp_path):
    p = tmp_path / "drill-catalog.md"
    p.write_text(CATALOG, encoding="utf-8")
    return p


@pytest.fixture
def emitted():
    """Return the [level] lines emitted by the check under test (state is module-global)."""
    start = len(state.results)
    return lambda: state.results[start:]


def test_seed_perspectives_parsed_and_split(catalog):
    assert parse_seed_perspectives(catalog) == {
        "security", "performance", "design", "lazy-senior",
    }
    assert parse_seed_perspectives(catalog.parent / "missing.md") == set()


def test_covered_review_gate_recipe_passes(write_recipe, catalog, emitted):
    path = write_recipe("covered", recipe_md("covered", (
        "  - id: review\n    instruction: parallel-review\n    gate: review-gate\n"
        "    personas: [security-reviewer, lazy-senior]\n"
    )))
    check_drill_coverage([path], drill_instruction=catalog)
    lines = emitted()
    assert not any(line.startswith(("[WARN]", "[FAIL]")) for line in lines)
    assert any(line.startswith("[PASS]") and "1/1" in line for line in lines)


def test_uncovered_reviewer_warns_but_never_fails(write_recipe, catalog, emitted):
    path = write_recipe("uncovered", recipe_md("uncovered", (
        "  - id: review\n    instruction: parallel-review\n    gate: review-gate\n"
        "    personas: [roast-reviewer]\n"
    )))
    check_drill_coverage([path], drill_instruction=catalog)
    lines = emitted()
    warns = [line for line in lines if line.startswith("[WARN]")]
    assert len(warns) == 1 and "roast-reviewer" in warns[0] and "uncovered" in warns[0]
    assert not any(line.startswith("[FAIL]") for line in lines)  # guidance, not schema


def test_gate_without_reviewers_warns_and_gateless_is_skipped(write_recipe, catalog, emitted):
    gated = write_recipe("no-reviewer", recipe_md("no-reviewer", (
        "  - id: verify\n    instruction: verify\n    gate: acceptance-gate\n"
        "    acceptance: [\"ok\"]\n    personas: [implementer]\n"
    )))
    gateless = write_recipe("gateless", recipe_md("gateless", (
        "  - id: implement\n    instruction: implement\n    personas: [implementer]\n"
    )))
    check_drill_coverage([gated, gateless], drill_instruction=catalog)
    lines = emitted()
    warns = [line for line in lines if line.startswith("[WARN]")]
    assert len(warns) == 1 and "no-reviewer" in warns[0] and "gateless" not in warns[0]
    assert any(line.startswith("[PASS]") and "0/1" in line for line in lines)


# ---- standard corpus (#270, #266) --------------------------------------------

GOOD_CORPUS = """# instruction: drill

corpus_version: 2

| 種の class | 例 | cwe/odc | 検出すべき観点 | 期待 severity | 期待 blocking |
|---|---|---|---|---|---|
| 認可漏れ | x | CWE-639 | security | High | Blocking |
| AI臭マーカー混入 | x | ODC-documentation | ai-smell | Medium | Non-blocking |
"""


def test_parse_seed_rows_returns_dicts(tmp_path):
    from rig_workbench.validation.drill import parse_seed_rows

    p = tmp_path / "drill.md"
    p.write_text(GOOD_CORPUS, encoding="utf-8")
    rows = parse_seed_rows(p)
    assert len(rows) == 2
    assert rows[0]["種の class"] == "認可漏れ"
    assert rows[1]["検出すべき観点"] == "ai-smell"


def test_corpus_integrity_passes_on_valid_corpus(tmp_path, emitted):
    from rig_workbench.validation.drill import check_corpus_integrity

    p = tmp_path / "drill.md"
    p.write_text(GOOD_CORPUS, encoding="utf-8")
    check_corpus_integrity(p)
    lines = emitted()
    assert any("standard corpus v2" in line and line.startswith("[PASS]") for line in lines)


def test_corpus_integrity_warns_on_missing_version_marker(tmp_path, emitted):
    from rig_workbench.validation.drill import check_corpus_integrity

    p = tmp_path / "drill.md"
    p.write_text(GOOD_CORPUS.replace("corpus_version: 2\n", ""), encoding="utf-8")
    check_corpus_integrity(p)
    assert any("corpus_version" in line and line.startswith("[WARN]") for line in emitted())


def test_corpus_integrity_warns_on_bad_severity(tmp_path, emitted):
    from rig_workbench.validation.drill import check_corpus_integrity

    p = tmp_path / "drill.md"
    p.write_text(GOOD_CORPUS.replace("| High |", "| Catastrophic |"), encoding="utf-8")
    check_corpus_integrity(p)
    assert any("severity" in line and line.startswith("[WARN]") for line in emitted())


def test_shipped_catalog_covers_prose_recipe_perspectives():
    """#266: the shipped seed catalog must cover the prose/design reviewers so
    de-ai-smell / design / sns-x-post / roast / deal-review become drillable."""
    import pathlib

    from rig_workbench.validation.config import FACETS

    drill_md = pathlib.Path(FACETS) / "instructions" / "drill.md"
    perspectives = parse_seed_perspectives(drill_md)
    for expected in ("ai-smell", "ux", "a11y", "sns-post", "engagement", "roast",
                     "hearing", "needs", "proposal", "closing", "next-action"):
        assert expected in perspectives, f"missing seed perspective: {expected}"


def test_aggregate_drill_confidence_corpus_filter(tmp_path):
    """#270: corpus filter separates standard vs project scores; rows without
    the field predate the distinction and count as standard."""
    import json

    from rig_workbench.workbench.confidence import aggregate_drill_confidence

    rig = tmp_path / ".rig"
    rig.mkdir()
    rows = [
        {"corpus": "standard", "scores": [{"reviewer": "sec", "detected": 2, "seeded": 2, "false_positives": 0}]},
        {"corpus": "project", "scores": [{"reviewer": "sec", "detected": 0, "seeded": 2, "false_positives": 1}]},
        {"scores": [{"reviewer": "sec", "detected": 1, "seeded": 1, "false_positives": 0}]},  # legacy row
    ]
    (rig / "drill-results.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    both = aggregate_drill_confidence(tmp_path)
    assert both["sec"] == {"detected": 3, "seeded": 5, "fp": 1}
    std = aggregate_drill_confidence(tmp_path, corpus="standard")
    assert std["sec"] == {"detected": 3, "seeded": 3, "fp": 0}  # legacy counts as standard
    proj = aggregate_drill_confidence(tmp_path, corpus="project")
    assert proj["sec"] == {"detected": 0, "seeded": 2, "fp": 1}
