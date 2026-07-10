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
