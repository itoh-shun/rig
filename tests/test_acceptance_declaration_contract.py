"""What a step is allowed to DECLARE about acceptance, and who checks it (#497 / #496).

One contract, and these are the parts of it something has to enforce:

* **C1** the presets are the only authority on what acceptance requires — `build_acceptance()`
  composes a task's gate from `GATE_PRESETS` and never reads a recipe;
* **C2** a recipe's `acceptance[]` is a WORK LIST (what this flow's steps produce evidence
  for), never the requirement list — pinned by `test_recipe_acceptance_criteria.py`;
* **C3** an entry is id-form (`criterion_id — 説明`, and the id must be one a preset defines)
  or prose-form (free text), and a step's list is entirely one or entirely the other;
* **C5** a step whose executor cannot produce a verdict declares no runtime gate and no
  `acceptance[]`.

Every rule below is driven against an input it MUST object to and against inputs it must
stay silent on. Simulated over the shipped catalogue the C3 rules add zero findings, which is
exactly why the positive controls are synthetic: a suite that only ran the 41 shipped recipe
files would report these guards as passing while checking nothing.
"""

import pathlib
import re

import pytest

from rig_workbench.orchestrate.gates import (
    VERDICTLESS_EXECUTORS,
    validate_executable_steps,
)
from rig_workbench.orchestrate.runstate import enforce_executable_state, new_state
from rig_workbench.validation import state as validation_state
from rig_workbench.validation.recipes import PRESET_CRITERION_IDS, check_recipe
from rig_workbench.workbench.config import GATE_PRESETS
from rig_workbench.workbench.state import build_acceptance

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
FACET = REPO_ROOT / "skills" / "engine" / "facets" / "instructions" / "acceptance-check.md"

_SYNTHETIC = """\
---
name: {name}
description: a synthetic recipe that exists only to drive check_recipe
scope: project
autonomy: interactive
steps:
  - id: inert
    instruction: implement
{step_keys}---

# {name}
"""


@pytest.fixture(autouse=True)
def _reset_validation_state():
    validation_state.results.clear()
    validation_state._pass = validation_state._warn = validation_state._fail = 0
    yield
    validation_state.results.clear()
    validation_state._pass = validation_state._warn = validation_state._fail = 0


def _recipe(tmp_path, name, **step_keys):
    body = "".join(f"    {key}: {value}\n" for key, value in step_keys.items())
    path = tmp_path / f"{name}.md"
    path.write_text(_SYNTHETIC.format(name=name, step_keys=body), encoding="utf-8")
    return path


def _fails():
    return [r for r in validation_state.results if r.startswith("[FAIL]")]


# ── C3: vocabulary and form ──────────────────────────────────────────────────


def test_the_vocabulary_this_checks_against_is_the_presets_themselves():
    """`pitfall_derive_each_layer_does_not_converge`: the validator must read the ids from
    `GATE_PRESETS`, not carry a second copy that drifts."""
    assert PRESET_CRITERION_IDS == {c for preset in GATE_PRESETS.values() for c in preset}
    assert len(PRESET_CRITERION_IDS) > 20, "the presets this checks against have moved"


def test_an_id_form_entry_naming_no_preset_criterion_fails(tmp_path):
    """The positive control for C3's vocabulary rule. `wb gate --set` refuses any name that
    is not already on the task's gate, so an invented id can never be recorded — it reads
    like an enforced rule and is not one."""
    path = _recipe(tmp_path, "synthetic-unknown-id",
                   gate="acceptance-gate",
                   acceptance='["no_such_criterion — invented"]')
    check_recipe(path)
    fails = [r for r in _fails() if "no_such_criterion" in r]
    assert len(fails) == 1, validation_state.results
    assert "no gate preset defines" in fails[0]


def test_a_real_criterion_id_is_not_objected_to(tmp_path):
    """The first thing that must not fire. Without it the rule above could be passing
    because it objects to everything."""
    path = _recipe(tmp_path, "synthetic-known-id",
                   gate="acceptance-gate",
                   acceptance='["task_intent_satisfied — 依頼の意図が満たされている"]')
    check_recipe(path)
    assert validation_state.results == ["[PASS] recipe synthetic-known-id: OK"]


def test_prose_form_entries_carry_no_vocabulary_constraint(tmp_path):
    """The second thing that must not fire, and the reason C3 has two forms at all: 23 of the
    30 acceptance lists in the shipped catalogue are prose. A rule demanding criterion ids
    everywhere would have rejected two thirds of it."""
    path = _recipe(tmp_path, "synthetic-prose",
                   gate="acceptance-gate",
                   acceptance='["4-way review に REJECT が無い", "関連テスト green"]')
    check_recipe(path)
    assert validation_state.results == ["[PASS] recipe synthetic-prose: OK"]


def test_a_mixed_form_list_fails(tmp_path):
    """A mixed list is checked as neither form: the prose entries look like unknown ids to a
    vocabulary check, and the id entries look like prose to a reader."""
    path = _recipe(tmp_path, "synthetic-mixed",
                   gate="acceptance-gate",
                   acceptance='["task_intent_satisfied — ok", "4-way review に REJECT が無い"]')
    check_recipe(path)
    fails = [r for r in _fails() if "mixes id-form and prose-form" in r]
    assert len(fails) == 1, validation_state.results
    assert "1 id-form, 1 prose-form" in fails[0]


def test_a_hyphen_is_not_the_id_form_separator(tmp_path):
    """The convention `acceptance-check.md` states is ` — ` (em dash). An ASCII hyphen makes
    the entry prose, which is a real distinction rather than a typo tolerance: prose entries
    are deliberately unconstrained, so silently treating `id - text` as id-form would start
    rejecting free text that happens to begin with a lowercase word."""
    path = _recipe(tmp_path, "synthetic-hyphen",
                   gate="acceptance-gate",
                   acceptance='["no_such_criterion - invented"]')
    check_recipe(path)
    assert validation_state.results == ["[PASS] recipe synthetic-hyphen: OK"]


# ── C5: a verdict-less executor declares no runtime gate ─────────────────────


def test_the_verdictless_set_is_declared_once(tmp_path):
    """The validator, the runtime preflight and these tests all read the same frozenset. A
    rule re-derived per layer stops agreeing with itself the first time the set changes."""
    assert VERDICTLESS_EXECUTORS == frozenset({"checks-only", "risk-assess"})


@pytest.mark.parametrize("executor", sorted(VERDICTLESS_EXECUTORS))
@pytest.mark.parametrize("declared", [
    {"gate": "acceptance-gate"},
    {"gate": "review-gate"},
    {"acceptance": '["task_intent_satisfied — ok"]'},
])
def test_a_verdictless_executor_may_declare_neither_a_runtime_gate_nor_criteria(
    tmp_path, executor, declared,
):
    """The positive control for C5, over every member of the set and every way to violate it."""
    path = _recipe(tmp_path, "synthetic-verdictless", executor=executor, **declared)
    check_recipe(path)
    fails = [r for r in _fails() if "cannot produce a verdict" in r]
    assert len(fails) == 1, validation_state.results


def test_a_verdictless_executor_with_only_checks_is_the_prescribed_shape(tmp_path):
    """C5 codifies existing practice rather than inventing a form: `fast-bugfix.implement`,
    `fast-bugfix.test`, `max-bugfix.implement` and `max-bugfix.test` already ship it."""
    path = _recipe(tmp_path, "synthetic-checks-only",
                   executor="checks-only", checks='["git diff --check"]')
    check_recipe(path)
    assert validation_state.results == ["[PASS] recipe synthetic-checks-only: OK"]


def test_the_runtime_refuses_the_shape_too_not_only_the_linter():
    """`rig-wb validate` globs the shipped tier alone (`skills/engine/recipes/*.md`), but
    `resolve_recipe` searches `<cwd>/.rig/recipes/` FIRST. A project-tier recipe carrying the
    forbidden shape would never reach the linter, and — once the gate correctly waits for a
    verdict — would park in AWAIT forever with no verifier that could ever arrive.

    So the refusal lives at load time, where every tier the runner accepts passes through.
    """
    steps = [{"id": "acceptance", "instruction": "acceptance-check", "gate": "acceptance-gate",
              "executor": "checks-only", "checks": ["git diff --check"], "acceptance": [],
              "personas": [], "needs": [], "pattern": None, "max_retries": 1,
              "output_contract": None}]
    state = new_state("project-tier-flow", steps, "fix")
    enforce_executable_state(state)
    assert state["stopped"]["kind"] == "BLOCKED"
    # Names the offending step, not just the reason: `at: —` sends the reader hunting.
    assert state["stopped"]["at"] == "acceptance"
    assert "cannot produce a verdict" in state["stopped"]["reason"]


def test_the_runtime_refusal_is_not_excused_by_no_orchestrate():
    """The combination is a false declaration on the page as well as an unreachable state in
    the runner, so `no_orchestrate: true` does not buy it."""
    result = validate_executable_steps(
        [{"id": "acceptance", "gate": "acceptance-gate", "executor": "checks-only"}],
        no_orchestrate=True,
    )
    assert any("cannot produce a verdict" in e for e in result["errors"])


def test_the_shipped_catalogue_is_clean_under_all_of_these():
    """Discipline: count the real data before changing a rule that rejects things. Every
    recipe file in the repository — not only the shipped tier the linter globs — is valid
    under C3 and C5, and this is where a new one that is not shows up."""
    from rig_workbench.orchestrate.recipes import parse_frontmatter

    files = sorted(p for p in REPO_ROOT.rglob("*.md") if "/recipes/" in str(p))
    assert len(files) > 30, "the recipe files this reads have moved"
    id_form = re.compile(r"^\s*([a-z][a-z0-9_]*)\s+—\s")
    offenders, mixed, unknown, lists = [], [], [], 0
    for path in files:
        frontmatter = parse_frontmatter(path)
        if isinstance(frontmatter, tuple):
            frontmatter = frontmatter[0]
        if not isinstance(frontmatter, dict):
            continue
        steps = frontmatter.get("steps") or []
        result = validate_executable_steps(
            steps, no_orchestrate=bool(frontmatter.get("no_orchestrate", False)))
        offenders += [(path.name, item) for item in result["verdictless_gates"]]
        for step in steps:
            entries = (step or {}).get("acceptance") or []
            if not entries:
                continue
            lists += 1
            forms = {"id" if id_form.match(str(e)) else "prose" for e in entries}
            if len(forms) > 1:
                mixed.append((path.name, step.get("id")))
            unknown += [str(e) for e in entries
                        if (m := id_form.match(str(e))) and m.group(1) not in PRESET_CRITERION_IDS]
    assert lists > 20, "no acceptance list was reached; this test is measuring nothing"
    assert offenders == []
    assert mixed == []
    assert unknown == []


# ── the third surface: the judge's own instruction ───────────────────────────


def test_the_facet_gives_a_judging_method_for_every_preset_criterion():
    """`acceptance-check.md` used to carry its own per-preset catalogue, and it had already
    drifted from `GATE_PRESETS` by eleven criteria — including `no_gate_tampering` and
    `no_injection_markers`, the two #497 reported as never set on any bugfix task. Nothing
    compared the two, so the drift was invisible: the instruction simply never told the judge
    how to reach a `--set` for them.

    "The sensors cover them" is not an answer. Sensors only fail or warn — they never write
    `passed` — so a criterion with no method here stays `pending` and `wb accept` refuses.
    """
    text = FACET.read_text(encoding="utf-8")
    missing = sorted(c for c in PRESET_CRITERION_IDS if f"`{c}`" not in text)
    assert missing == [], missing


def test_the_facet_does_not_re_enumerate_the_presets_it_would_drift_from():
    """The fix is not a re-synced copy — that is a fourth copy, which drifts again. The facet
    tells the judge to read the task's gate, and groups its methods by how a criterion is
    settled rather than by which preset it came from."""
    text = FACET.read_text(encoding="utf-8")
    assert "rig-wb wb gate <task_id>" in text, "the facet must point at the task's own gate"
    for preset in GATE_PRESETS:
        assert f"**{preset} プリセット" not in text, (
            f"the facet re-enumerates the `{preset}` preset; that copy is what drifted")


def test_the_facet_states_which_of_the_two_lists_is_the_requirement():
    """#497's first acceptance criterion: the relationship has to be written down where the
    step that judges reads it."""
    text = FACET.read_text(encoding="utf-8")
    assert "build_acceptance()" in text
    assert "作業一覧" in text and "recipe は一切参照しない" in text


# ── the fourth surface: what a recipe body claims about the gate ─────────────


def test_no_recipe_body_states_a_criterion_count_that_contradicts_its_gate():
    """`bugfix.md` said "13基準（standard 8 + bugfix 5）" while `build_acceptance` built 15,
    and `documentation.md` asserted "`standard` preset の8基準のみ" — a false claim about
    `GATE_PRESETS` itself, not about the work list. A count in prose is a copy of a number
    that lives somewhere else; the reliable repair is to delete it, and this keeps it deleted.
    """
    shipped = sorted((REPO_ROOT / "skills" / "engine" / "recipes").glob("*.md"))
    assert len(shipped) > 20, "the shipped recipes this reads have moved"
    claim = re.compile(r"(\d+)\s*(?:件|\s)?基準|基準\s*(\d+)\s*件")
    offenders = []
    for path in shipped:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "preset" not in line and "プリセット" not in line and "acceptance" not in line:
                continue
            if claim.search(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()[:120]}")
    assert offenders == [], offenders


def test_the_count_check_objects_to_the_claim_it_was_written_against():
    """The positive control, quoting the line as it shipped. Without it this passes on a
    repository where the pattern stopped matching anything."""
    claim = re.compile(r"(\d+)\s*(?:件|\s)?基準|基準\s*(\d+)\s*件")
    assert claim.search("7. **acceptance** — acceptance-check が13基準（standard 8 + bugfix 5）を判定し")
    assert claim.search("`standard` preset の8基準のみ")
    assert claim.search("- `acceptance`: 13 基準を維持しつつ")
    assert not claim.search("受け入れ基準を満たすまで収束させる")


#: The counts that REMAIN in a recipe body after the false gate-counts were deleted, and where
#: the number actually lives. `work-list` is the length of that step's own `acceptance[]`;
#: `gate` is the size of the gate `build_acceptance()` composes for that task type. Nothing
#: here re-types a number — each expectation is computed from the source, so a body that
#: drifts from what it describes fails instead of quietly becoming false. Deleting the counts
#: outright was the other option; three of them describe the step's own work list and
#: `max-bugfix`'s describes the gate the work list is NOT, which is the distinction #497 is
#: about, so they are worth keeping if they are checked.
_BODY_COUNTS = {
    # name: (the step whose work list the body describes, the task_type whose gate it may cite)
    "bugfix.md": ("acceptance", "bugfix"),
    "feature.md": ("acceptance", "feature"),
    "refactor.md": ("acceptance", "refactor"),
    "max-bugfix.md": ("acceptance", "bugfix"),
}
_COUNT_IN_PROSE = re.compile(r"(\d+)\s*件")


def _body_count_sources(name: str) -> dict[str, int]:
    """The two numbers a body of this recipe is allowed to state, both computed."""
    step_id, task_type = _BODY_COUNTS[name]
    frontmatter, _body = validation_state.parse_frontmatter(
        REPO_ROOT / "skills" / "engine" / "recipes" / name)
    step = next(s for s in frontmatter["steps"] if s.get("id") == step_id)
    return {
        "work list": len(step["acceptance"]),
        "gate": len(build_acceptance("t", task_type)["checks"]),
    }


@pytest.mark.parametrize("name", sorted(_BODY_COUNTS))
def test_every_count_left_in_a_recipe_body_is_a_number_the_code_computes(name):
    """`13基準（standard 8 + bugfix 5）` was deleted because it contradicted a 15-criterion
    gate. What replaced it still states a number — `この step の acceptance: に並ぶ13件`, and
    in `max-bugfix` the gate's own `15件` — so "the bodies no longer state a count" would be
    a false claim. A count in prose is a copy of a number that lives somewhere else; the
    repair is to delete it OR to check it, and this checks it.

    Every count in the body must be one of the two numbers this recipe can honestly cite:
    its step's own work list, or the gate `build_acceptance()` composes. Membership rather
    than "the expected number appears somewhere" — the loose form passes a body that states
    the right number once and a drifted one beside it, which is exactly what these bodies do
    (`13件` appears twice in `bugfix.md`).
    """
    body = validation_state.parse_frontmatter(
        REPO_ROOT / "skills" / "engine" / "recipes" / name)[1]
    sources = _body_count_sources(name)
    stated = {int(match) for match in _COUNT_IN_PROSE.findall(body)}
    assert stated, f"{name} states no count — delete this entry rather than checking nothing"
    assert stated <= set(sources.values()), (
        f"{name} states {sorted(stated)}; the only numbers it may cite are "
        f"{sources} — a count that matches neither is prose drifting from its source")


def test_the_body_count_check_would_catch_a_drifted_number():
    """The positive control: the check is driven against a body it MUST object to, built by
    corrupting the real one. Without this it would keep passing if the expectations stopped
    being derived from the frontmatter and the presets."""
    sources = _body_count_sources("bugfix.md")
    assert sources["work list"] != sources["gate"], (
        "the work list and the gate must be different numbers, or this check cannot tell "
        "which of the two a body is describing — that difference IS #497")
    body = validation_state.parse_frontmatter(
        REPO_ROOT / "skills" / "engine" / "recipes" / "bugfix.md")[1]
    drifted = body.replace(f"{sources['work list']}件", f"{sources['work list'] + 1}件", 1)
    assert drifted != body, "the control mutated nothing"
    stated = {int(match) for match in _COUNT_IN_PROSE.findall(drifted)}
    assert not stated <= set(sources.values()), (
        "a body stating a drifted count alongside a correct one still passed")


# ── the two `--validate` rules, stated exactly ───────────────────────────────


def _warns():
    return [r for r in validation_state.results if r.startswith("[WARN]")]


def test_the_two_validate_rules_do_not_contradict_each_other(tmp_path):
    """#496's second acceptance criterion, measured rather than asserted as "disjoint".

    Both rules CAN fire on one step: a `checks-only` step declaring `gate: acceptance-gate`
    and no list draws the WARN ("declare acceptance[]") and the C5 FAIL ("drop the gate")
    together. They still do not contradict, because the FAIL's remedy clears the WARN's
    precondition — drop the gate and the step is not an acceptance-gate step, so neither rule
    has anything left to say. Following the WARN instead would leave the FAIL standing, which
    is why the FAIL's message names dropping the gate rather than adding criteria. No VALID
    step reaches both.
    """
    check_recipe(_recipe(tmp_path, "both", executor="checks-only", gate="acceptance-gate"))
    assert [f for f in _fails() if "cannot produce a verdict" in f], _fails()
    assert [w for w in _warns() if "acceptance[] is undefined" in w], _warns()

    validation_state.results.clear()
    validation_state._pass = validation_state._warn = validation_state._fail = 0

    # The FAIL's own remedy, applied verbatim: gate and acceptance[] dropped, checks kept.
    check_recipe(_recipe(tmp_path, "remedied", executor="checks-only", checks='["true"]'))
    assert [f for f in _fails() if "cannot produce a verdict" in f] == [], _fails()
    assert [w for w in _warns() if "acceptance[] is undefined" in w] == [], _warns()
