"""Every shipped `acceptance:` line names the gate criterion that observes it, or says none does.

`workbench/lifecycle.py` prints the other half of the rule this file exercises: a recipe's
`acceptance:` is that flow's WORK LIST, and the criteria `wb accept` requires are built by
`build_acceptance()` from `GATE_PRESETS`, which never reads a recipe. So a recipe line could
say anything at all and no code would ever look at it — 26 shipped recipes did exactly that,
in prose, and nothing anywhere said which of those lines a gate would actually judge.

`acceptance_binding[]` is that missing sentence, one entry per `acceptance[]` entry: the
criterion id that observes the line, or the literal `unobserved` when no criterion honestly
does. The `unobserved` half is the load-bearing one — it is *written down*, so an unjudged
line looks unjudged in the recipe instead of looking like a criterion because it sits under
a key called `acceptance`.

**The judgement is the validator's, not this file's.** Every assertion below runs
`validation.recipes.check_recipe` — the same function `rig-wb validate` runs — and reads the
FAILs it emits. A second copy of the rule here would be a second answer to "is this recipe
well-formed", free to pass what CI refuses or the reverse.

The honest-match rule the shipped bindings follow, stated so a later editor applies the same
one: a line binds to a criterion only when the criterion names the same subject the line
asks about, so that recording that criterion at the gate answers the line. A line that asks
about two independent subjects, or about a subject no preset names (build success, lint
count, a verdict existing, AI-smell, layout overflow, ja-lint), is `unobserved`. Widening
`GATE_PRESETS` later and rebinding is fine; stretching one of the 34 to cover a line it does
not name is what this rule exists to stop.

**Non-vacuity.** A walk over a glob is a test that passes hardest when it finds nothing, so
`collect_acceptance()` refuses an empty result instead of returning one, and
`test_empty_recipe_set_is_not_vacuous` drives it against a directory with no recipe in it
and against a directory whose recipe declares no `acceptance:` — the two shapes that would
otherwise turn this whole file green by finding nothing to check.
"""

import pathlib

import pytest

from rig_workbench.validation import state
from rig_workbench.validation.recipes import (
    ACCEPTANCE_UNOBSERVED,
    ROUTED_GATE_CRITERIA,
    check_recipe,
)
from rig_workbench.validation.rig_surfaces import GATE_PRESETS
from rig_workbench.validation.state import parse_frontmatter

ROOT = pathlib.Path(__file__).resolve().parent.parent
RECIPES = ROOT / "skills" / "engine" / "recipes"

#: Every criterion id any preset can put on a task's gate, taken from the workbench's own
#: mapping rather than re-typed. A copy here would drift with the recipes it checks.
CRITERION_IDS = frozenset(c for preset in GATE_PRESETS.values() for c in preset)


def check_fails(path: pathlib.Path) -> list[str]:
    """The FAIL lines `check_recipe` emits for one recipe file.

    `validation.state` accumulates results in module globals, so the window is taken by
    index rather than by clearing it — a test that reset the shared list would erase what a
    sibling test recorded.
    """
    start = len(state.results)
    check_recipe(path)
    return [line for line in state.results[start:] if line.startswith("[FAIL]")]


def binding_fails(path: pathlib.Path) -> list[str]:
    return [line for line in check_fails(path) if "acceptance_binding" in line]


def collect_acceptance(recipes_dir: pathlib.Path) -> list[tuple[str, str, list, object]]:
    """Every acceptance-bearing step under `recipes_dir`, as (recipe, step, lines, binding).

    Measurement only — the pass/fail judgement belongs to `check_recipe`. Raises
    `AssertionError` when the walk finds no acceptance-bearing step at all: the caller asked
    for the shipped catalogue's acceptance lines, and "there are none" is a broken walk
    (wrong directory, renamed key, moved recipes), not a catalogue that passes.
    """
    found: list[tuple[str, str, list, object]] = []
    for path in sorted(recipes_dir.glob("*.md")):
        frontmatter, _ = parse_frontmatter(path)
        for step in (frontmatter or {}).get("steps") or []:
            if not isinstance(step, dict):
                continue
            lines = step.get("acceptance")
            if not lines:
                continue
            found.append((path.stem, str(step.get("id")), lines, step.get("acceptance_binding")))
    assert found, (
        f"no recipe under {recipes_dir} declares acceptance[] — this walk checks nothing."
        " Either the recipes moved or the key was renamed; an empty scan is a failure here,"
        " never a pass."
    )
    return found


def _write_recipe(directory: pathlib.Path, body: str, name: str = "probe") -> pathlib.Path:
    path = directory / f"{name}.md"
    path.write_text(
        f"---\nname: {name}\ndescription: t\nscope: shipped\nautonomy: interactive\n"
        f"steps:\n{body}---\n",
        encoding="utf-8",
    )
    return path


# ── the shipped catalogue ────────────────────────────────────────────────────
def test_every_shipped_acceptance_line_is_bound_or_marked_unobserved() -> None:
    """All 26 acceptance-bearing recipes, through the validator that gates CI."""
    problems: list[str] = []
    for recipe, *_ in collect_acceptance(RECIPES):
        problems += binding_fails(RECIPES / f"{recipe}.md")
    assert not problems, "\n".join(problems)


def test_the_catalogue_still_has_lines_no_criterion_observes() -> None:
    """`unobserved` is load-bearing, not a key nothing uses.

    A catalogue that came out entirely bound would mean lines were bound to criteria that do
    not name them — the stretching this binding exists to prevent — and a catalogue entirely
    `unobserved` would mean the key stopped being a judgement. Either is a reason to re-read
    the bindings rather than to relax this test.
    """
    collected = collect_acceptance(RECIPES)
    entries = [e for *_, binding in collected if isinstance(binding, list) for e in binding]
    unobserved = sum(1 for e in entries if e == ACCEPTANCE_UNOBSERVED)
    bound = len(entries) - unobserved
    assert unobserved > 0 and bound > 0, f"bound={bound} unobserved={unobserved}"


def test_unobserved_is_not_smuggled_in_as_a_criterion_id() -> None:
    """If a preset ever defined `unobserved`, every unbound line would read as a bound one."""
    assert ACCEPTANCE_UNOBSERVED not in CRITERION_IDS


# ── non-vacuity ──────────────────────────────────────────────────────────────
def test_empty_recipe_set_is_not_vacuous(tmp_path: pathlib.Path) -> None:
    """A walk that finds nothing fails; it does not report a clean catalogue.

    Both shapes are driven, because they fail differently: no files at all (a wrong or
    emptied directory) and files with no `acceptance:` in them (the key renamed out from
    under this check).
    """
    empty = tmp_path / "no-recipes"
    empty.mkdir()
    with pytest.raises(AssertionError, match="an empty scan is a failure"):
        collect_acceptance(empty)

    no_acceptance = tmp_path / "no-acceptance"
    no_acceptance.mkdir()
    _write_recipe(no_acceptance, "  - id: implement\n    instruction: implement\n", name="quiet")
    with pytest.raises(AssertionError, match="an empty scan is a failure"):
        collect_acceptance(no_acceptance)

    # …and the same walk over a directory that does carry one returns it, so the guard
    # above refuses emptiness rather than refusing everything.
    populated = tmp_path / "one-recipe"
    populated.mkdir()
    _write_recipe(populated, _STEP.format(binding="    acceptance_binding: [no_secret_leak]\n"))
    assert len(collect_acceptance(populated)) == 1


#: A prose-form line, for the fixtures where an id-form one would trip the contradiction
#: check before the rule under test is reached.
_PROSE_STEP = (
    "  - id: acceptance\n"
    "    instruction: acceptance-check\n"
    '    acceptance: ["回帰テストが赤から緑になった"]\n'
    "{binding}"
)

_STEP = (
    "  - id: acceptance\n"
    "    instruction: acceptance-check\n"
    '    acceptance: ["no_secret_leak — secret の混入がない"]\n'
    "{binding}"
)


# ── the validator's refusals ─────────────────────────────────────────────────
def test_missing_binding_fails(tmp_path: pathlib.Path) -> None:
    fails = binding_fails(_write_recipe(tmp_path, _STEP.format(binding="")))
    assert len(fails) == 1 and "no acceptance_binding[]" in fails[0]


def test_unknown_criterion_id_fails(tmp_path: pathlib.Path) -> None:
    body = _STEP.format(binding="    acceptance_binding: [no_such_criterion]\n")
    fails = binding_fails(_write_recipe(tmp_path, body))
    assert len(fails) == 1 and "which no gate preset defines" in fails[0]


def test_unobserved_is_accepted(tmp_path: pathlib.Path) -> None:
    """The escape hatch really is one: an honestly unbound line passes the validator."""
    body = (
        "  - id: verify\n"
        "    instruction: verify\n"
        '    acceptance: ["build が成功"]\n'
        f"    acceptance_binding: [{ACCEPTANCE_UNOBSERVED}]\n"
    )
    assert binding_fails(_write_recipe(tmp_path, body)) == []


def test_length_mismatch_fails(tmp_path: pathlib.Path) -> None:
    body = _STEP.format(binding=f"    acceptance_binding: [no_secret_leak, {ACCEPTANCE_UNOBSERVED}]\n")
    fails = binding_fails(_write_recipe(tmp_path, body))
    assert len(fails) == 1 and "positional" in fails[0]


def test_binding_that_contradicts_an_id_form_line_fails(tmp_path: pathlib.Path) -> None:
    """An `id — 説明` line and its binding cannot name two different criteria."""
    body = _STEP.format(binding="    acceptance_binding: [no_unrelated_diff]\n")
    fails = binding_fails(_write_recipe(tmp_path, body))
    assert len(fails) == 1 and "One line cannot claim two" in fails[0]


def test_non_list_binding_fails(tmp_path: pathlib.Path) -> None:
    body = _STEP.format(binding="    acceptance_binding: no_secret_leak\n")
    fails = binding_fails(_write_recipe(tmp_path, body))
    assert len(fails) == 1 and "is not a list" in fails[0]


def test_a_non_list_acceptance_fails_cleanly(tmp_path: pathlib.Path) -> None:
    """`acceptance: 5` is truthy and unmeasurable — it must not reach a `len()`.

    `validation/cli.py` catches anything `check_recipe` raises into a FAIL carrying a full
    traceback and absolute paths, and abandons the rest of that recipe's steps. That is
    fail-closed but unreadable, and the one real FAIL (acceptance is not a list) is the one
    the reader needs.
    """
    path = _write_recipe(
        tmp_path,
        "  - id: acceptance\n    instruction: acceptance-check\n    acceptance: 5\n",
    )
    fails = check_fails(path)
    assert any("acceptance value is not a list" in f for f in fails), fails
    assert not any("Traceback" in f for f in fails), fails
    assert binding_fails(path) == []


def test_a_non_list_acceptance_with_a_binding_fails_cleanly(tmp_path: pathlib.Path) -> None:
    """The same with a binding present: the guard sits above every `len()`, not beside one."""
    path = _write_recipe(
        tmp_path,
        "  - id: acceptance\n    instruction: acceptance-check\n    acceptance: true\n"
        "    acceptance_binding: [no_secret_leak]\n",
    )
    fails = check_fails(path)
    assert any("acceptance value is not a list" in f for f in fails), fails
    assert not any("Traceback" in f for f in fails), fails


def test_a_binding_no_route_to_the_recipe_can_record_fails(tmp_path: pathlib.Path) -> None:
    """A criterion that exists is not therefore on *this* recipe's gate.

    `regression_test_added_or_explained` lives only in the bugfix preset. Every route to
    `design` carries task_type `design`, whose gate is `standard` alone, so the binding could
    never be recorded — `wb gate --set` refuses a name that is not already on the task's gate.
    The same binding on `bugfix.md`, which task_type bugfix does route to, is correct, and the
    pair is what keeps this from being a check that objects to everything.
    """
    criterion = "regression_test_added_or_explained"
    assert criterion not in ROUTED_GATE_CRITERIA["design"]
    assert criterion in ROUTED_GATE_CRITERIA["bugfix"]

    refused = _write_recipe(tmp_path, _PROSE_STEP.format(
        binding=f"    acceptance_binding: [{criterion}]\n"), name="design")
    fails = binding_fails(refused)
    assert len(fails) == 1 and "no route to this recipe puts on a task's gate" in fails[0]

    allowed = _write_recipe(tmp_path, _PROSE_STEP.format(
        binding=f"    acceptance_binding: [{criterion}]\n"), name="bugfix")
    assert binding_fails(allowed) == []


def test_an_unrouted_recipe_keeps_the_full_preset_vocabulary(tmp_path: pathlib.Path) -> None:
    """The limit, stated as a test: nothing says which task type `pentest-fix` runs under.

    Guessing one would invent a gate; the union of the presets is what the check can honestly
    hold a recipe to when no route reaches it.
    """
    assert "pentest-fix" not in ROUTED_GATE_CRITERIA
    path = _write_recipe(tmp_path, _PROSE_STEP.format(
        binding="    acceptance_binding: [regression_test_added_or_explained]\n"),
        name="pentest-fix")
    assert binding_fails(path) == []


def test_an_id_form_line_called_unobserved_fails(tmp_path: pathlib.Path) -> None:
    """The contradiction check is symmetric: modesty is a disagreement too."""
    body = _STEP.format(binding=f"    acceptance_binding: [{ACCEPTANCE_UNOBSERVED}]\n")
    fails = binding_fails(_write_recipe(tmp_path, body))
    assert len(fails) == 1 and "mark it unobserved only by rewriting" in fails[0]


def test_binding_without_acceptance_fails(tmp_path: pathlib.Path) -> None:
    body = (
        "  - id: verify\n"
        "    instruction: verify\n"
        "    acceptance_binding: [no_secret_leak]\n"
    )
    fails = binding_fails(_write_recipe(tmp_path, body))
    assert len(fails) == 1 and "no acceptance[]" in fails[0]
