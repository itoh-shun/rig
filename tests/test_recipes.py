"""Unit tests for rig_workbench.orchestrate.recipes (parse/RESOLVE, pure functions)."""

import pytest

from rig_workbench.orchestrate.recipes import (auto_orchestrate, evaluate_condition,
                                               parse_frontmatter, resolve_effective,
                                               resolve_plan_json, size_class)

BASE = """---
name: base-flow
description: t
scope: shipped
autonomy: interactive
steps:
  - id: intake
    instruction: intake
  - id: design
    instruction: design
    condition: "--design or size L+"
  - id: implement
    instruction: implement
  - id: verify
    instruction: verify
    gate: acceptance-gate
    checks: ["true"]
---
body text
"""

CHILD = """---
name: child-flow
description: t
scope: project
autonomy: autonomous
extends: base-flow
tdd: true
steps:
  - id: design
    remove: true
  - id: verify
    instruction: verify
    gate: acceptance-gate
    checks: ["true"]
  - id: pr
    instruction: pr
---
"""


def test_parse_frontmatter_roundtrip(write_recipe):
    p = write_recipe("base-flow", BASE)
    fm = parse_frontmatter(p)
    assert fm["name"] == "base-flow"
    assert fm["autonomy"] == "interactive"
    assert [s["id"] for s in fm["steps"]] == ["intake", "design", "implement", "verify"]
    assert fm["steps"][3]["gate"] == "acceptance-gate"


def test_parse_frontmatter_missing_or_unterminated(tmp_path):
    no_fm = tmp_path / "plain.md"
    no_fm.write_text("just prose, no frontmatter\n", encoding="utf-8")
    assert parse_frontmatter(no_fm) == {}
    broken = tmp_path / "broken.md"
    broken.write_text("---\nname: x\nno closing fence\n", encoding="utf-8")
    assert parse_frontmatter(broken) == {}


def test_resolve_plan_json_structure(write_recipe):
    p = write_recipe("base-flow", BASE)
    plan = resolve_plan_json(p)
    assert set(plan) >= {"recipe", "extends", "autonomy", "badges", "steps_field",
                         "n_steps", "steps", "warnings"}
    assert plan["recipe"] == "base-flow"
    assert plan["extends"] is None
    assert plan["n_steps"] == 4
    assert [s["id"] for s in plan["steps"]] == ["intake", "design", "implement", "verify"]
    # condition abbreviation is a machine token derived from the flag name
    assert plan["steps_field"] == "intake, design?[--design|L+], implement, verify"
    assert "gated" in plan["badges"]           # acceptance-gate step present
    assert "orchestrate(auto)" in plan["badges"]  # checks declared


def test_extends_merge_remove_override_added(write_recipe):
    write_recipe("base-flow", BASE)
    child = write_recipe("child-flow", CHILD)
    plan = resolve_plan_json(child)
    assert plan["extends"] == "base-flow"
    assert [s["id"] for s in plan["steps"]] == ["intake", "implement", "verify", "pr"]
    assert [s["origin"] for s in plan["steps"]] == ["inherited", "inherited", "override", "added"]
    # leaf frontmatter wins for top-level keys
    assert plan["autonomy"] == "autonomous"
    assert plan["badges"][0] == "tdd"
    # deterministic: same input -> same output
    assert resolve_plan_json(child) == plan


def test_extends_unresolvable_parent_warns_but_keeps_steps(write_recipe):
    p = write_recipe("orphan", CHILD.replace("extends: base-flow", "extends: nowhere"))
    plan = resolve_plan_json(p)
    assert len(plan["warnings"]) >= 1
    # falls back to the leaf's own steps (remove marker survives untouched)
    assert plan["n_steps"] == 3


def test_resolve_effective_condition_and_size(write_recipe):
    p = write_recipe("base-flow", BASE)
    small = resolve_effective(p, [], diff_lines=50)
    assert small["effective_steps"] == ["intake", "implement", "verify"]
    assert small["size"] == {"diff_lines": 50, "class": "S"}
    flagged = resolve_effective(p, ["--design"], diff_lines=50)
    assert flagged["effective_steps"] == ["intake", "design", "implement", "verify"]
    large = resolve_effective(p, [], diff_lines=500)
    assert large["size"]["class"] == "XL"
    assert "design" in large["effective_steps"]


def test_resolve_effective_slices_and_errors(write_recipe):
    p = write_recipe("base-flow", BASE)
    only = resolve_effective(p, ["--only", "verify"], diff_lines=10)
    assert only["effective_steps"] == ["verify"]
    assert only["slice"]["only"] == "verify"
    rng = resolve_effective(p, ["--from", "implement", "--to", "verify"], diff_lines=10)
    assert rng["effective_steps"] == ["implement", "verify"]
    # reversed range and unknown id are errors (assert presence, not wording)
    assert resolve_effective(p, ["--from", "verify", "--to", "implement"], diff_lines=10)["errors"]
    assert resolve_effective(p, ["--only", "nope"], diff_lines=10)["errors"]
    # --only a condition-OFF step is an error
    assert resolve_effective(p, ["--only", "design"], diff_lines=10)["errors"]
    # --skip an acceptance-gate step warns but does not error
    gate = resolve_effective(p, ["--skip", "verify"], diff_lines=10)
    assert gate["warnings"] and not gate["errors"]
    assert "verify" not in gate["effective_steps"]


def test_resolve_effective_mode_summary(write_recipe):
    p = write_recipe("base-flow", BASE)
    eff = resolve_effective(p, [], diff_lines=10)
    assert eff["mode"]["autonomy"] == "interactive"
    assert eff["mode"]["orchestrate"].startswith("auto")   # checks declared -> auto
    assert eff["mode"]["tdd"] is False
    off = resolve_effective(p, ["--no-orchestrate"], diff_lines=10)
    assert off["mode"]["orchestrate"] == "off"


@pytest.mark.parametrize("lines,expected", [
    (None, "S"), (0, "S"), (100, "S"), (101, "M"), (200, "M"),
    (201, "L"), (400, "L"), (401, "XL"),
])
def test_size_class_default_thresholds(lines, expected):
    assert size_class(lines) == expected


def test_size_class_custom_thresholds():
    th = {"S_max": 10, "M_max": 20, "L_max": 40}
    assert size_class(15, th) == "M"
    assert size_class(41, th) == "XL"


def test_evaluate_condition_tokens():
    assert evaluate_condition(None, set(), "S")[0] is True   # empty condition always on
    on, _ = evaluate_condition("--design or size L+", {"--design"}, "S")
    assert on is True
    off, _ = evaluate_condition("--design or size L+", set(), "M")
    assert off is False
    size_on, _ = evaluate_condition("--design or size L+", set(), "XL")
    assert size_on is True
    garbage, _ = evaluate_condition("always maybe", set(), "XL")
    assert garbage is False   # uninterpretable condition is always OFF


def test_auto_orchestrate(step_factory):
    s = step_factory
    assert auto_orchestrate([s(id="v", checks=["true"])])[0] is True
    assert auto_orchestrate([s(id="a"), s(id="b", needs=["a"])])[0] is True
    assert auto_orchestrate([s(id="x")])[0] is False
    assert auto_orchestrate([s(id="x")], manifest_default=True)[0] is True
