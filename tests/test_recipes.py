"""Unit tests for rig_workbench.orchestrate.recipes (parse/RESOLVE, pure functions)."""

import hashlib

import pytest

from rig_workbench.orchestrate import config
from rig_workbench.orchestrate.graph import build_brick_graph
from rig_workbench.orchestrate.recipes import (
    auto_orchestrate,
    evaluate_condition,
    load_steps,
    parse_frontmatter,
    resolve_effective,
    resolve_extends,
    resolve_plan_json,
    size_class,
)

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


def test_load_steps_preserves_executor(write_recipe):
    path = write_recipe(
        "adaptive",
        """---
name: adaptive
steps:
  - id: assess
    instruction: adaptive-assess
    executor: risk-assess
---""",
    )
    assert resolve_plan_json(path)["steps"][0]["executor"] == "risk-assess"


def test_load_steps_defaults_only_a_missing_executor(write_recipe):
    path = write_recipe(
        "executor-defaults",
        """---
name: executor-defaults
steps:
  - id: omitted
    instruction: legacy-generate
  - id: explicit-empty
    instruction: invalid-adaptive
    executor: ""
---""",
    )

    steps = resolve_plan_json(path)["steps"]

    assert steps[0]["executor"] == "generate"
    assert steps[1]["executor"] == ""


def test_load_steps_retains_prompt_composition_references(write_recipe):
    path = write_recipe(
        "composed",
        """---
name: composed
steps:
  - id: plan
    instruction: task-plan
    personas: [planner]
    output_contract: task-plan
    policies: [risk-based-testing, ci-cost]
---""",
    )

    step = resolve_plan_json(path)["steps"][0]

    assert step["instruction"] == "task-plan"
    assert step["personas"] == ["planner"]
    assert step["output_contract"] == "task-plan"
    assert step["policies"] == ["risk-based-testing", "ci-cost"]


def test_resolved_steps_retain_the_declaring_recipe_source(write_recipe):
    path = write_recipe(
        "owned",
        """---
name: owned
steps:
  - id: write
    instruction: implement
---""",
    )

    resolved, _warnings = resolve_extends(parse_frontmatter(path), path)
    step = load_steps(resolved)[0]

    assert step["recipe_source"] == str(path.resolve())


def test_adaptive_bugfix_recipe_has_bounded_executor_flow():
    path = config.RECIPES / "adaptive-bugfix.md"
    plan = resolve_plan_json(path)

    assert [step["id"] for step in plan["steps"]] == [
        "implement",
        "assess",
        "targeted-review",
        "acceptance",
    ]
    assert [step["executor"] for step in plan["steps"]] == [
        "generate",
        "risk-assess",
        "targeted-review",
        "checks-only",
    ]
    assert plan["steps"][-1]["checks"] == ["git diff --check"]
    body = path.read_text(encoding="utf-8")
    assert "two-call normal" in body
    assert "three-call repair budget" in body
    assert "four-call multi-domain budget" in body
    assert "safe stop" in body
    assert "CLI `--check`" in body


def test_adaptive_bugfix_is_in_inventory_without_changing_list_default():
    skill_root = config.RECIPES.parent
    skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    list_spec = (skill_root / "facets/instructions/list.md").read_text(encoding="utf-8")

    assert "`recipes/adaptive-bugfix`" in skill
    assert "adaptive-bugfix" in list_spec
    release_entry = next(
        line for line in list_spec.splitlines() if line.strip().startswith("release-flow")
    )
    assert "★ default" in release_entry


def test_adaptive_bugfix_graph_references_are_resolved():
    adaptive_edges = [
        edge
        for edge in build_brick_graph()["edges"]
        if edge["from"] == "recipe:adaptive-bugfix"
    ]

    assert adaptive_edges
    assert all(edge["resolved"] for edge in adaptive_edges)


def test_existing_bugfix_recipe_bytes_are_unchanged():
    """Pin the bugfix recipes so a change to them is always deliberate.

    Not a freeze — it catches edits made *in passing* (e.g. while adding a
    neighbouring recipe). Changing these files on purpose means updating the
    hash in the same commit, which is exactly the moment to state why.

    Last intentional change: the `write-failing-test` step (test-first is part of
    the base flow, opt out with `--no-tdd`).
    """
    expected = {
        "bugfix.md": "a41e5395412f98c4038124f51739624ec5afb48cf5aa19775cbaae3799bcb415",
        "fast-bugfix.md": "a922f07ff1e94805d43b8589f7cb08a3e3d51277fc50e739a576c7ba584b345d",
    }
    actual = {
        name: hashlib.sha256((config.RECIPES / name).read_bytes()).hexdigest() for name in expected
    }
    assert actual == expected


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
    assert set(plan) >= {
        "recipe",
        "extends",
        "autonomy",
        "badges",
        "steps_field",
        "n_steps",
        "steps",
        "warnings",
    }
    assert plan["recipe"] == "base-flow"
    assert plan["extends"] is None
    assert plan["n_steps"] == 4
    assert [s["id"] for s in plan["steps"]] == ["intake", "design", "implement", "verify"]
    # condition abbreviation is a machine token derived from the flag name
    assert plan["steps_field"] == "intake, design?[--design|L+], implement, verify"
    assert "gated" in plan["badges"]  # acceptance-gate step present
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
    assert eff["mode"]["orchestrate"].startswith("auto")  # checks declared -> auto
    assert eff["mode"]["tdd"] is False
    off = resolve_effective(p, ["--no-orchestrate"], diff_lines=10)
    assert off["mode"]["orchestrate"] == "off"


@pytest.mark.parametrize(
    "lines,expected",
    [
        (None, "S"),
        (0, "S"),
        (100, "S"),
        (101, "M"),
        (200, "M"),
        (201, "L"),
        (400, "L"),
        (401, "XL"),
    ],
)
def test_size_class_default_thresholds(lines, expected):
    assert size_class(lines) == expected


def test_size_class_custom_thresholds():
    th = {"S_max": 10, "M_max": 20, "L_max": 40}
    assert size_class(15, th) == "M"
    assert size_class(41, th) == "XL"


def test_evaluate_condition_tokens():
    assert evaluate_condition(None, set(), "S")[0] is True  # empty condition always on
    on, _ = evaluate_condition("--design or size L+", {"--design"}, "S")
    assert on is True
    off, _ = evaluate_condition("--design or size L+", set(), "M")
    assert off is False
    size_on, _ = evaluate_condition("--design or size L+", set(), "XL")
    assert size_on is True
    garbage, _ = evaluate_condition("always maybe", set(), "XL")
    assert garbage is False  # uninterpretable condition is always OFF


def test_auto_orchestrate(step_factory):
    s = step_factory
    assert auto_orchestrate([s(id="v", checks=["true"])])[0] is True
    assert auto_orchestrate([s(id="a"), s(id="b", needs=["a"])])[0] is True
    assert auto_orchestrate([s(id="x")])[0] is False
    assert auto_orchestrate([s(id="x")], manifest_default=True)[0] is True


# ── negated flag conditions (`not --no-tdd`) — structural step with an opt-out ──

def test_negated_flag_condition_is_on_when_the_anti_flag_is_absent():
    on, why = evaluate_condition("not --no-tdd", set(), "S")
    assert on is True
    assert "not suppressed" in why


def test_negated_flag_condition_is_off_when_the_anti_flag_is_set():
    on, why = evaluate_condition("not --no-tdd", {"--no-tdd"}, "XL")
    assert on is False
    assert "--no-tdd" in why


def test_negated_flag_condition_ignores_size():
    # A structural step must not be silently dropped for small diffs (§4.4).
    for size in ("S", "M", "L", "XL"):
        assert evaluate_condition("not --no-tdd", set(), size)[0] is True


def test_bang_form_of_negation_is_equivalent():
    assert evaluate_condition("!--no-tdd", {"--no-tdd"}, "S")[0] is False
    assert evaluate_condition("!--no-tdd", set(), "S")[0] is True


def test_a_negated_flag_is_not_read_as_a_positive_flag():
    # The bug this syntax exists to avoid: matching --no-tdd positively would turn
    # the step ON exactly when the caller asked for it to be dropped.
    on, _ = evaluate_condition("not --no-tdd", {"--no-tdd"}, "S")
    assert on is False


def test_positive_and_negated_flags_can_coexist_in_one_condition():
    assert evaluate_condition("--tdd or not --no-tdd", {"--tdd"}, "S")[0] is True
    assert evaluate_condition("--tdd or not --no-tdd", {"--no-tdd"}, "S")[0] is False


def test_existing_size_and_flag_conditions_are_unaffected():
    assert evaluate_condition("--design or size L+", set(), "L")[0] is True
    assert evaluate_condition("--design or size L+", set(), "S")[0] is False
    assert evaluate_condition("--design or size L+", {"--design"}, "S")[0] is True
    assert evaluate_condition(None, set(), "S")[0] is True
    assert evaluate_condition("gibberish", set(), "S")[0] is False


# ── test-first is part of the base flow (feature / bugfix) ──

@pytest.mark.parametrize("recipe", ["feature", "bugfix"])
def test_base_recipes_write_a_failing_test_before_implementing(recipe):
    ids = [s["id"] for s in resolve_plan_json(config.RECIPES / f"{recipe}.md")["steps"]]
    assert "write-failing-test" in ids, f"{recipe} lost its test-first step"
    assert ids.index("write-failing-test") < ids.index("implement")


@pytest.mark.parametrize("recipe", ["feature", "bugfix"])
def test_test_first_step_is_on_by_default_at_every_size(recipe):
    for diff_lines in (1, 150, 900):
        resolved = resolve_effective(config.RECIPES / f"{recipe}.md", flags=[], diff_lines=diff_lines)
        step = next(s for s in resolved["steps"] if s["id"] == "write-failing-test")
        assert step["active"] is True, f"{recipe} dropped test-first at {diff_lines} lines"


@pytest.mark.parametrize("recipe", ["feature", "bugfix"])
def test_no_tdd_opts_out_of_the_test_first_step(recipe):
    resolved = resolve_effective(config.RECIPES / f"{recipe}.md", flags=["--no-tdd"], diff_lines=10)
    step = next(s for s in resolved["steps"] if s["id"] == "write-failing-test")
    assert step["active"] is False
    assert "--no-tdd" in step["why"]


@pytest.mark.parametrize("recipe", ["feature", "bugfix"])
def test_test_first_step_has_an_instruction_facet(recipe):
    steps = resolve_plan_json(config.RECIPES / f"{recipe}.md")["steps"]
    instr = next(s["instruction"] for s in steps if s["id"] == "write-failing-test")
    assert (config.RECIPES.parent / "facets" / "instructions" / f"{instr}.md").is_file()


def test_no_tdd_wins_over_tdd():
    resolved = resolve_effective(config.RECIPES / "feature.md",
                                 flags=["--tdd", "--no-tdd"], diff_lines=10)
    assert resolved["mode"]["tdd"] is False
    step = next(s for s in resolved["steps"] if s["id"] == "write-failing-test")
    assert step["active"] is False
    assert any("--no-tdd wins" in w for w in resolved["warnings"])
