"""A recipe that declares `gate: acceptance-gate` has to say what the gate judges (#486).

`adaptive-bugfix` — the newest and most actively developed flagship recipe, with its own
benchmark spec — declared the gate and no `acceptance:` list at all, so its gate had nothing
to converge on. `patterns/acceptance-gate` calls that gate the core of rig's
determinism-by-gate; a gate with no criteria is the one shape that cannot deliver it.
"""

import pathlib
import re

import pytest
import yaml

from rig_workbench.workbench.config import GATE_PRESETS
from rig_workbench.workbench.state import build_acceptance, gate_status

RECIPES = pathlib.Path(__file__).resolve().parent.parent / "skills" / "engine" / "recipes"
ALL_RECIPES = sorted(RECIPES.glob("*.md"))

#: The recipes whose acceptance entries are written as `criterion_id — 説明`, so their ids can
#: be checked against the presets. Named rather than detected: most shipped recipes write
#: their criteria as free prose for a reviewer to judge (`4-way review に REJECT が無い`,
#: `関連テスト green`), which is a different and equally valid form — a check that assumed the
#: id form everywhere would report two thirds of the catalogue as broken. Deriving the list by
#: "entries that happen to parse as an id" would be the check picking its own coverage.
#:
#: It was three names while only three were being looked at. Measured over all 41 recipe files
#: in the repository, the id form is used by exactly these seven (29 acceptance lists in all:
#: 7 id-form, 22 prose-form, 0 mixed); `feature`, `refactor`, `documentation` and `max-bugfix`
#: were spelling criterion ids and nothing was checking them.
_RECIPES_USING_CRITERION_IDS = (
    "bugfix", "fast-bugfix", "adaptive-bugfix", "max-bugfix",
    "feature", "refactor", "documentation",
)

KNOWN = {criterion for preset in GATE_PRESETS.values() for criterion in preset}


def _steps(path):
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return []
    return (yaml.safe_load(text.split("---\n", 2)[1]) or {}).get("steps") or []


def _named_ids(step):
    return {match.group(1)
            for match in (re.match(r"\s*([a-z][a-z0-9_]*)", str(entry))
                          for entry in step.get("acceptance") or [])
            if match is not None}


def test_the_files_this_reads_are_where_it_thinks_they_are():
    """A guard aimed at nothing passes every test written about its logic."""
    assert len(ALL_RECIPES) > 15, "the recipes this checks have moved"
    assert len(KNOWN) > 20, "the gate presets this checks against have moved"
    assert all((RECIPES / f"{name}.md").is_file() for name in _RECIPES_USING_CRITERION_IDS)


def _steps_gating_on_nothing(steps):
    """The steps that declare the acceptance gate and give it nothing to judge."""
    return [step.get("id") for step in steps
            if step.get("gate") == "acceptance-gate" and not step.get("acceptance")]


def test_the_invariant_objects_to_a_gate_with_no_criteria():
    """The negative control, first. Without it, every case below could pass on a repository
    where the front matter stopped parsing or the gate comparison stopped matching — the
    parametrised tests would then be reporting on nothing at all."""
    assert _steps_gating_on_nothing(
        [{"id": "acceptance", "gate": "acceptance-gate"}]) == ["acceptance"]
    assert _steps_gating_on_nothing(
        [{"id": "acceptance", "gate": "acceptance-gate", "acceptance": []}]) == ["acceptance"]
    assert _steps_gating_on_nothing(
        [{"id": "acceptance", "gate": "acceptance-gate", "acceptance": ["x"]}]) == []
    assert _steps_gating_on_nothing([{"id": "review", "gate": "review-gate"}]) == []


def test_the_shipped_recipes_actually_reach_that_branch():
    """A guard that never enters its own branch passes everything. Some shipped recipe has to
    declare the gate, or the parametrised test below is measuring nothing."""
    declaring = [path.stem for path in ALL_RECIPES
                 for step in _steps(path) if step.get("gate") == "acceptance-gate"]
    assert len(declaring) > 5, f"almost no recipe declares the gate: {declaring}"


@pytest.mark.parametrize("path", ALL_RECIPES, ids=lambda p: p.stem)
def test_an_acceptance_gate_step_declares_what_it_gates_on(path):
    """The invariant #486 was a violation of. It holds for every shipped recipe, so a new one
    that ships without criteria is caught here rather than by running with a gate that passes
    whatever it is given."""
    empty = _steps_gating_on_nothing(_steps(path))
    assert not empty, (f"{path.name} steps {empty} declare gate: acceptance-gate with no "
                       f"acceptance[] — the gate has nothing to judge")


@pytest.mark.parametrize("name", _RECIPES_USING_CRITERION_IDS)
def test_a_recipe_written_in_criterion_ids_uses_ones_the_gate_defines(name):
    """`scripts/workbench.py gates` is the source of truth for these. An id spelled only in a
    recipe is one no sensor measures and no other recipe shares: it reads like an enforced
    rule and is not one."""
    unknown = [entry
               for step in _steps(RECIPES / f"{name}.md")
               for entry in step.get("acceptance") or []
               if (re.match(r"\s*([a-z][a-z0-9_]*)", str(entry)) or [None])
               and re.match(r"\s*([a-z][a-z0-9_]*)", str(entry)).group(1) not in KNOWN]
    assert not unknown, f"{name} names criteria the gate does not define: {unknown}"


def test_the_id_check_objects_to_an_invented_criterion():
    """The positive control. Without it, the test above passes on a repository where the
    parsing has quietly stopped matching anything."""
    entries = ["task_intent_satisfied — fine", "no_such_criterion — invented"]
    matched = [re.match(r"\s*([a-z][a-z0-9_]*)", e).group(1) for e in entries]
    assert [m for m in matched if m not in KNOWN] == ["no_such_criterion"]


#: What settles each criterion `adaptive-bugfix` asks for. The rule the recipe states is that
#: a criterion belongs there when a step of the flow produces the evidence it names, and this
#: table is that rule written as data: `diff` for what a reviewer reading the diff can settle,
#: `sensor` for what a deterministic scan settles.
_ADAPTIVE_BUGFIX_EVIDENCE = {
    "task_intent_satisfied": "diff",
    "no_unrelated_diff": "diff",
    "fix_is_minimal": "diff",
    "no_unrelated_refactor": "diff",
}

#: Why each of the rest is absent, named individually so that adding one back has to come with
#: a step that produces its evidence. `test-step` criteria all need a test run and this flow
#: has none; `written-artifact` ones need prose from a step that writes it. Every reason here
#: names evidence the flow does not produce — "a sibling recipe leaves it out" is not one of
#: them.
#:
#: `no-sensor-under-orchestrate` is the reason added by #497. The four criteria under it ARE
#: settled by deterministic sensors — but the sensors report through `rig-wb wb gate`, and
#: `orchestrate run`, the only runner this orchestrate-only flow has, never calls `wb gate`.
#: Under this runner nothing in the flow produces their evidence, which is the same rule the
#: rest of the table applies. They stay binding on the task's gate; what changes is only that
#: no step of this flow claims to answer them.
_ADAPTIVE_BUGFIX_OMITTED = {
    "diff_summary_written": "written-artifact",
    "risk_summary_written": "written-artifact",
    "no_type_errors_or_explained": "type-check-step",
    "bug_cause_identified": "reproduce-or-plan-step",
    "tests_pass_or_explained": "test-step",
    "regression_test_added_or_explained": "test-step",
    "existing_behavior_preserved": "test-step",
    "no_secret_leak": "no-sensor-under-orchestrate",
    "no_destructive_operation": "no-sensor-under-orchestrate",
    "no_injection_markers": "no-sensor-under-orchestrate",
    "no_gate_tampering": "no-sensor-under-orchestrate",
}


def test_adaptive_bugfix_gates_on_exactly_what_its_evidence_reaches():
    """#486. Both directions: nothing asked for that the flow cannot settle, and nothing the
    flow can settle left out. A criterion nothing satisfies does not make a gate stricter —
    it makes it a rubber stamp or a deadlock.

    The list is read from `targeted-review`, not from `acceptance`. #497/#496: `acceptance`
    has `executor: checks-only`, which runs its commands and returns without ever calling a
    provider, so it cannot produce the verdict its old `gate: acceptance-gate` promised.
    `targeted-review` is the step of this flow that actually judges the diff, so it is the
    step that can carry — and answer — the four diff-settled criteria.
    """
    steps = {step["id"]: step for step in _steps(RECIPES / "adaptive-bugfix.md")}
    assert _named_ids(steps["targeted-review"]) == set(_ADAPTIVE_BUGFIX_EVIDENCE)
    # And the verdict-less step carries neither a runtime gate nor a list to answer.
    assert steps["acceptance"].get("gate") is None
    assert not steps["acceptance"].get("acceptance")


def test_the_evidence_table_accounts_for_every_criterion_of_its_presets():
    """No criterion is silently unconsidered: every id in `standard` + `bugfix` is either
    asked for with a reason, or omitted with one."""
    presets = set(GATE_PRESETS["standard"]) | set(GATE_PRESETS["bugfix"])
    accounted = set(_ADAPTIVE_BUGFIX_EVIDENCE) | set(_ADAPTIVE_BUGFIX_OMITTED)
    assert accounted == presets, sorted(accounted ^ presets)


def test_the_flow_has_no_step_that_runs_tests_or_reproduces_the_bug():
    """The reason seven criteria are omitted, asserted rather than assumed. `bugfix` and
    `fast-bugfix` both have a `test` step and both ask for `existing_behavior_preserved`;
    `bugfix` alone has `reproduce`/`plan` and alone asks for `bug_cause_identified`. This
    recipe has neither kind of step, and if one is ever added the omissions have to be
    revisited — which is what this failing will say."""
    steps = {step["id"]: step for step in _steps(RECIPES / "adaptive-bugfix.md")}
    assert set(steps) == {"implement", "assess", "targeted-review", "acceptance"}

    # The acceptance step runs mechanical checks only, and the one it declares is a syntax
    # check — no test command reaches the flow unless a caller supplies one via `--check`.
    # (`--check` is routed to the `checks-only` step; it used to be routed to a step that
    # was both `checks-only` and `acceptance-gate`, a pair that is now refused.)
    assert steps["acceptance"]["executor"] == "checks-only"
    assert steps["acceptance"]["checks"] == ["git diff --check"]
    assert not any("test" in str(step.get("checks") or []) for step in steps.values()), (
        "a step now runs tests; re-derive whether the test-evidence criteria belong")


def test_every_omission_names_evidence_the_flow_does_not_produce():
    """A criterion is omitted because no step of THIS flow settles it under the runner this
    flow has, never because a sibling recipe leaves it out. This is what makes the reason
    itself checkable rather than a comment."""
    assert set(_ADAPTIVE_BUGFIX_OMITTED.values()) == {
        "written-artifact", "type-check-step", "reproduce-or-plan-step", "test-step",
        "no-sensor-under-orchestrate"}


def test_the_sensor_reason_names_a_bridge_that_does_not_exist():
    """The ground for `no-sensor-under-orchestrate`, asserted rather than assumed: the
    workbench gate and the orchestrate runner do not read each other. If a bridge is ever
    built, this fails and the four criteria have to be reconsidered."""
    workbench = pathlib.Path(__file__).resolve().parent.parent / "rig_workbench" / "workbench"
    orchestrate = (pathlib.Path(__file__).resolve().parent.parent
                   / "rig_workbench" / "orchestrate")
    workbench_text = "\n".join(p.read_text(encoding="utf-8")
                               for p in sorted(workbench.rglob("*.py")))
    orchestrate_text = "\n".join(p.read_text(encoding="utf-8")
                                 for p in sorted(orchestrate.rglob("*.py")))
    assert "run_state" not in workbench_text and "run-state.json" not in workbench_text, (
        "the workbench now reads orchestrate run-state — re-derive the omission reason")
    assert "build_acceptance" not in orchestrate_text, (
        "orchestrate now builds a task gate — re-derive the omission reason")


def test_the_precedents_ask_for_what_their_extra_steps_settle():
    """The rule is not invented for this recipe. `fast-bugfix` has no reproduce or plan step
    and omits `bug_cause_identified`; `bugfix` has both and asks for it. Read from the shipped
    files, so a change to either precedent shows up here rather than silently making this
    recipe's reasoning stale."""
    def criteria(name):
        return {criterion
                for step in _steps(RECIPES / f"{name}.md")
                for criterion in _named_ids(step)}

    def step_ids(name):
        return {step["id"] for step in _steps(RECIPES / f"{name}.md")}

    assert {"reproduce", "plan"} <= step_ids("bugfix")
    assert "bug_cause_identified" in criteria("bugfix")
    assert not ({"reproduce", "plan"} & step_ids("fast-bugfix"))
    assert "bug_cause_identified" not in criteria("fast-bugfix")
    assert "test" in step_ids("fast-bugfix")
    assert "existing_behavior_preserved" in criteria("fast-bugfix")

    # And the sensor-backed pair, asserted rather than left implicit: no bugfix-family
    # recipe lists them. The interactive flows leave them to the sensors that report through
    # `rig-wb wb gate`; `adaptive-bugfix` cannot list them because it never reaches that
    # command at all. Either way they stay binding on the task's gate and unclaimed by a step.
    assert not ({"no_injection_markers", "no_gate_tampering"}
                & (criteria("bugfix") | criteria("fast-bugfix")
                   | criteria("adaptive-bugfix")))


# ── the work list vs the requirement list (#497) ─────────────────────────────
def _criteria(name):
    return {criterion for step in _steps(RECIPES / f"{name}.md") for criterion in _named_ids(step)}


#: Every id-form recipe with the task_type whose gate it runs under, so the two invariants
#: below are asserted over the whole surface rather than over the three that were looked at.
_ID_FORM_ROUTES = [
    ("bugfix", "bugfix"), ("fast-bugfix", "bugfix"), ("adaptive-bugfix", "bugfix"),
    ("max-bugfix", "bugfix"), ("feature", "feature"), ("refactor", "refactor"),
    ("documentation", "documentation"),
]


@pytest.mark.parametrize("recipe,task_type", _ID_FORM_ROUTES)
def test_a_recipes_list_is_a_subset_of_the_gate_it_runs_under(recipe, task_type):
    """`build_acceptance` seeds a task's gate from the presets and never reads a recipe, so a
    recipe naming a criterion outside them would declare something the gate has no slot for —
    the result would be recorded nowhere."""
    gate = {check["name"] for check in build_acceptance("t", task_type)["checks"]}
    assert _criteria(recipe) <= gate, sorted(_criteria(recipe) - gate)


@pytest.mark.parametrize("recipe,task_type", _ID_FORM_ROUTES)
def test_declaring_a_recipes_criteria_does_not_by_itself_pass_the_gate(recipe, task_type):
    """This is the contract, not a defect awaiting repair — and #497 landing is the reason
    the wording changed rather than the test.

    A recipe's `acceptance[]` is a WORK LIST: the criteria this flow's own steps produce
    evidence for. The requirement list is the task's gate, which `build_acceptance()` builds
    from the presets without ever reading a recipe. The two are deliberately not aligned, so
    answering exactly what a recipe declares is EXPECTED to leave the rest `pending` and
    `rig-wb wb accept` then refuses and names what is missing.

    What this test therefore pins is that the gap stays visible. It would fail if someone
    made `build_acceptance` read a recipe (the presets would stop being a floor) or padded a
    recipe's list out to the full preset (which #486's rule forbids: a criterion belongs on a
    step's list only when a step of that flow produces the evidence it names, and listing one
    nothing can satisfy buys a rubber stamp or a deadlock).
    """
    acceptance = build_acceptance("t", task_type)
    declared = _criteria(recipe)
    assert declared, f"{recipe} declares no id-form criteria; this asserts nothing"
    for check in acceptance["checks"]:
        if check["name"] in declared:
            check["status"] = "passed"
    assert gate_status(acceptance) == "pending", (
        f"{recipe}'s own list now passes the task's gate by itself. That is the two lists "
        f"being made one — either `build_acceptance` started reading recipes, or this "
        f"recipe was padded to the full preset. Re-read C1/C2 in "
        f"skills/engine/facets/instructions/acceptance-check.md before changing this")

    for check in acceptance["checks"]:
        check["status"] = "passed"
    assert gate_status(acceptance) == "passed", "the positive control for the line above"
