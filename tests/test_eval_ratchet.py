"""The coverage ratchet for the prompt evaluation gate.

`--require-cases` is correct as a destination and unreachable as a starting
point: with an empty `evals/cases/` it fails every change that touches a prompt
surface — including the change that would add the first case. A gate that fires
on everything reports nothing, and teaches people to merge past it, which is a
habit that then applies to the checks that *do* carry signal.

`--ratchet` states the same requirement as a direction. Not having written a
case yet is debt: counted, named, survivable. Taking away coverage somebody
already earned with a measured red→green run is a regression, and still fatal.
"""

import copy
import json
import pathlib
import subprocess

import pytest

from test_eval_cases import valid_case


def _git(repo: pathlib.Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=repo, check=True,
                               capture_output=True, text=True)
    return completed.stdout.strip()


def _repo(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "eval@test.invalid")
    _git(repo, "config", "user.name", "eval-test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _write_case(repo: pathlib.Path, case_id: str, surfaces: list[str]) -> pathlib.Path:
    from rig_workbench.eval.cases import canonical_json

    case = copy.deepcopy(valid_case())
    case["id"] = case_id
    case["target_inputs"] = {"prompt_surface_fixture": f"binding for {case_id}"}
    case["prompt_surfaces"] = surfaces
    path = repo / "evals" / "cases" / case_id / "case.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(case), encoding="utf-8")
    return path


def _touch(repo: pathlib.Path, relative: str, text: str = "changed\n") -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit(repo: pathlib.Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


INSTRUCTION = "skills/engine/facets/instructions/login.md"
PERSONA = "skills/engine/facets/personas/reviewer.md"


def analyze(repo, base, head="working", **kwargs):
    from rig_workbench.eval.affected import analyze_affected

    return analyze_affected(repo, base=base, head=head, **kwargs)


# ── the bootstrap problem the ratchet exists to solve ────────────────────────
def test_strict_mode_blocks_a_surface_with_no_case_yet(tmp_path):
    repo, base = _repo(tmp_path)
    _touch(repo, INSTRUCTION)
    report = analyze(repo, base, require_cases=True)
    assert report["status"] == "uncovered"
    assert report["uncovered"] == [INSTRUCTION]


def test_the_ratchet_reports_the_same_surface_as_debt_and_lets_it_through(tmp_path):
    repo, base = _repo(tmp_path)
    _touch(repo, INSTRUCTION)
    report = analyze(repo, base, ratchet=True)
    assert report["status"] == "debt"
    assert report["coverage_debt"] == [INSTRUCTION]
    assert report["uncovered"] == []
    assert report["coverage_regressions"] == []


def test_debt_still_names_the_commits_that_created_it(tmp_path):
    """Survivable is not the same as invisible: the paths and their commits are
    still reported, which is what makes paying the debt down a visible task."""
    repo, base = _repo(tmp_path)
    _touch(repo, INSTRUCTION)
    head = _commit(repo, "touch the instruction")
    report = analyze(repo, base, head=head, ratchet=True)
    assert report["surface_commits"][INSTRUCTION] == [head[:7]] or \
        report["surface_commits"][INSTRUCTION][0].startswith(head[:7])


def test_a_covered_surface_passes_under_the_ratchet(tmp_path):
    repo, base = _repo(tmp_path)
    _write_case(repo, "login-case", ["instruction:login"])
    _touch(repo, INSTRUCTION)
    report = analyze(repo, base, ratchet=True)
    assert report["status"] == "pass"
    assert report["coverage_debt"] == [] and report["uncovered"] == []


def test_a_change_touching_no_prompt_surface_is_still_a_noop(tmp_path):
    repo, base = _repo(tmp_path)
    _touch(repo, "rig_workbench/whatever.py")
    assert analyze(repo, base, ratchet=True)["status"] == "noop"


# ── what stays fatal ─────────────────────────────────────────────────────────
def test_an_unregistered_surface_kind_still_fails_under_the_ratchet(tmp_path):
    """A file under a registered root whose kind the registry does not recognise is
    a surface nobody is tracking at all. A ratchet on an unmeasured thing is nothing."""
    repo, base = _repo(tmp_path)
    _touch(repo, "skills/engine/recipes/notes.txt")
    report = analyze(repo, base, ratchet=True)
    assert report["status"] == "uncovered"
    assert "skills/engine/recipes/notes.txt" in report["uncovered"]


def test_deleting_a_case_is_a_regression(tmp_path):
    repo, base = _repo(tmp_path)
    _write_case(repo, "login-case", ["instruction:login"])
    _touch(repo, INSTRUCTION)
    base = _commit(repo, "add coverage")
    (repo / "evals" / "cases" / "login-case" / "case.json").unlink()
    _touch(repo, INSTRUCTION, "changed again\n")
    head = _commit(repo, "remove the case")
    report = analyze(repo, base, head=head, ratchet=True)
    assert report["status"] == "uncovered"
    assert any("login-case" in item and "deleted" in item
               for item in report["coverage_regressions"])


def test_narrowing_a_cases_surfaces_is_a_regression(tmp_path):
    """The subtler way to lose coverage: keep the file, drop the binding."""
    repo, base = _repo(tmp_path)
    _write_case(repo, "wide-case", ["instruction:login", "persona:reviewer"])
    _touch(repo, INSTRUCTION)
    _touch(repo, PERSONA)
    base = _commit(repo, "add wide coverage")
    _write_case(repo, "wide-case", ["instruction:login"])
    _touch(repo, PERSONA, "changed again\n")
    head = _commit(repo, "narrow the case")
    report = analyze(repo, base, head=head, ratchet=True)
    assert report["status"] == "uncovered"
    assert any("persona:reviewer" in item for item in report["coverage_regressions"])


def test_widening_a_case_is_not_a_regression(tmp_path):
    repo, base = _repo(tmp_path)
    _write_case(repo, "growing-case", ["instruction:login"])
    _touch(repo, INSTRUCTION)
    base = _commit(repo, "add coverage")
    _write_case(repo, "growing-case", ["instruction:login", "persona:reviewer"])
    _touch(repo, PERSONA)
    head = _commit(repo, "widen the case")
    report = analyze(repo, base, head=head, ratchet=True)
    assert report["coverage_regressions"] == []
    assert report["status"] == "pass"


def test_adding_a_case_pays_debt_down_without_touching_the_others(tmp_path):
    """The motion the ratchet is for: debt shrinks by one, the rest is still reported."""
    repo, base = _repo(tmp_path)
    _touch(repo, INSTRUCTION)
    _touch(repo, PERSONA)
    assert sorted(analyze(repo, base, ratchet=True)["coverage_debt"]) == \
        sorted([INSTRUCTION, PERSONA])
    _write_case(repo, "login-case", ["instruction:login"])
    report = analyze(repo, base, ratchet=True)
    assert report["coverage_debt"] == [PERSONA]
    assert report["status"] == "debt"


# ── regressions are only claimed when they can be demonstrated ───────────────
def test_strict_mode_does_not_compute_regressions(tmp_path):
    """The two modes are independent: --require-cases keeps its exact old meaning."""
    repo, base = _repo(tmp_path)
    _write_case(repo, "login-case", ["instruction:login"])
    _touch(repo, INSTRUCTION)
    base = _commit(repo, "add coverage")
    (repo / "evals" / "cases" / "login-case" / "case.json").unlink()
    head = _commit(repo, "remove the case")
    report = analyze(repo, base, head=head, require_cases=True)
    assert report["coverage_regressions"] == []


def test_an_unreadable_base_tree_does_not_invent_a_regression(tmp_path):
    """`_coverage_at` returning None must read as "cannot tell", not "everything was
    deleted" — accusing a change of a regression that cannot be demonstrated is the
    one way this check could become the thing it replaced."""
    from rig_workbench.eval.affected import _regressions

    assert _regressions(None, {}) == []


def test_a_case_that_was_never_approved_is_not_counted_as_lost(tmp_path):
    repo, base = _repo(tmp_path)
    path = _write_case(repo, "draft-case", ["instruction:login"])
    value = json.loads(path.read_text(encoding="utf-8"))
    value["status"] = "draft"
    path.write_text(json.dumps(value), encoding="utf-8")
    _touch(repo, INSTRUCTION)
    base = _commit(repo, "add a draft")
    path.unlink()
    head = _commit(repo, "remove the draft")
    report = analyze(repo, base, head=head, ratchet=True)
    assert report["coverage_regressions"] == []


# ── the comparison point: the base branch's tip, not the fork ────────────────
def _forked(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str, str]:
    """Two surfaces exist, then the base branch writes a case for one of them.

    Returns the repo, the fork point where neither surface has a case, and the base
    branch's tip. A branch forked at the first commit is behind on that case without
    having deleted anything, which is the state the fork-point comparison could not
    tell apart from "nobody has written this case yet".
    """
    repo, _root = _repo(tmp_path)
    _touch(repo, INSTRUCTION)
    _touch(repo, PERSONA)
    fork = _commit(repo, "two surfaces, no cases")
    _write_case(repo, "login-case", ["instruction:login"])
    return repo, fork, _commit(repo, "the base branch writes the case")


def test_a_case_written_on_the_base_branch_after_the_fork_is_still_owed(tmp_path):
    """The bypass: fork from before the case, edit only the prompt, carry no case.

    Against the fork point there was no coverage to lose and no case to match, so
    the surface came back as debt and exit 0 — and the merge then landed the edit
    next to the case it restores, which the base branch's own push refuses. The
    comparison is the tip now, so what the merge would land is what is judged.
    """
    repo, fork, base = _forked(tmp_path)
    _git(repo, "checkout", "-q", "-b", "evil", fork)
    _touch(repo, INSTRUCTION, "rewritten by a branch that carries no case\n")
    head = _commit(repo, "edit the covered prompt, carrying no case")

    report = analyze(repo, base, head=head, ratchet=True)
    assert report["status"] == "uncovered"
    assert any(INSTRUCTION in item and "login-case" in item
               for item in report["coverage_stale"]), report
    # Reported as exactly one of the two, and not as the survivable one.
    assert report["coverage_debt"] == []
    # Nothing was taken away: the branch never had the case to delete.
    assert report["coverage_regressions"] == []


def test_a_branch_behind_on_an_unrelated_case_is_not_charged_for_it(tmp_path):
    """What keeps the tip usable as the reference: only a case that covers a surface
    *this change edits* is owed. Every other PR open while a case lands is untouched,
    which is the difference between a ratchet and a branch-wide rebase demand."""
    repo, fork, base = _forked(tmp_path)
    _git(repo, "checkout", "-q", "-b", "feature", fork)
    _touch(repo, PERSONA, "an edit to the surface nobody covered\n")
    head = _commit(repo, "edit the uncovered surface only")

    report = analyze(repo, base, head=head, ratchet=True)
    assert report["status"] == "debt"
    assert report["coverage_debt"] == [PERSONA]
    assert report["coverage_stale"] == [] and report["coverage_regressions"] == []


def test_stale_coverage_through_a_recipe_names_only_the_paths_that_reach_it(tmp_path):
    """A surface is also covered when a case covers a recipe that composes it, and the
    stale reading has to follow the same edges — including the edge it must *not*
    follow. Charging every affected path for a recipe only one of them reaches names
    a recipe the author never touched and, worse, takes the other path out of
    `coverage_debt`, which is the count CI publishes as its warning."""
    repo, _root = _repo(tmp_path)
    recipe = repo / "skills/engine/recipes/auth.md"
    recipe.parent.mkdir(parents=True, exist_ok=True)
    recipe.write_text("---\nname: auth\nsteps:\n  - id: review\n"
                      "    personas: [reviewer]\n---\n", encoding="utf-8")
    # A second recipe reaching the same persona, which nobody covers. Without it the
    # debt branch of the recipe loop never runs, and "the same path is not counted as
    # both stale and debt" is never actually asked.
    _touch(repo, "skills/engine/recipes/other.md",
           "---\nname: other\nsteps:\n  - id: review\n    personas: [reviewer]\n---\n")
    _touch(repo, PERSONA, "---\nname: reviewer\n---\n")
    _touch(repo, INSTRUCTION)                       # reaches no recipe at all
    fork = _commit(repo, "two recipes, their persona, and an unrelated instruction")
    _write_case(repo, "auth-case", ["recipe:auth"])
    base = _commit(repo, "the base branch covers one of the recipes")

    _git(repo, "checkout", "-q", "-b", "evil", fork)
    _touch(repo, PERSONA, "---\nname: reviewer\nedited: yes\n---\n")
    _touch(repo, INSTRUCTION, "edited too\n")
    head = _commit(repo, "edit both, carrying no case")

    report = analyze(repo, base, head=head, ratchet=True)
    assert report["status"] == "uncovered"
    assert [item for item in report["coverage_stale"] if item.startswith(PERSONA)], report
    assert not [item for item in report["coverage_stale"]
                if item.startswith(INSTRUCTION)], report
    # The honest debt is still counted rather than swallowed by the recipe's failure,
    # and the persona is reported as exactly one of the two: the uncovered second
    # recipe charges every affected path to debt, and a stale path is taken back out.
    assert report["coverage_debt"] == [INSTRUCTION], report
    # The triage list covers what is blocking, not only what is survivable.
    assert PERSONA in report["surface_commits"], report


def test_the_landing_view_keeps_what_the_base_gained_and_drops_what_this_removed(tmp_path):
    """The three-way rule stated on its own, because both readings depend on it."""
    from rig_workbench.eval.affected import _landing_coverage, _regressions

    fork = {"kept": {"instruction:login"}}
    base = {"kept": {"instruction:login"}, "added-since": {"persona:reviewer"}}
    landing = _landing_coverage({}, base, fork)      # a branch carrying neither

    assert landing == {"added-since": {"persona:reviewer"}}
    lost = _regressions(base, landing)
    assert [item for item in lost if "kept" in item], lost
    assert not [item for item in lost if "added-since" in item], lost
    assert _landing_coverage({}, None, fork) is None
    assert _landing_coverage({}, base, None) is None


# ── the other half of "covered": the wiring, not the cases ───────────────────
UNWIRED = "---\nname: auth\nsteps: []\n---\n"
WIRED = "---\nname: auth\nsteps:\n  - id: review\n    personas: [reviewer]\n---\n"
RECIPE = "skills/engine/recipes/auth.md"


def _wired_after_the_fork(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str, str]:
    """A covered recipe, and a persona it starts out not referencing.

    Returns the repo, the fork point — where the persona is reachable from no
    recipe — and the base branch's tip, where the recipe references it and the case
    covering the recipe therefore covers the persona too.
    """
    repo, _root = _repo(tmp_path)
    _touch(repo, RECIPE, UNWIRED)
    _touch(repo, PERSONA, "---\nname: reviewer\n---\n")
    _write_case(repo, "auth-case", ["recipe:auth"])
    fork = _commit(repo, "a covered recipe, and a persona nothing references")
    _touch(repo, RECIPE, WIRED)
    return repo, fork, _commit(repo, "the base branch wires the persona in")


def test_a_reference_the_base_branch_added_after_the_fork_still_covers(tmp_path):
    """The bypass one layer out from the case set.

    Nothing is missing from this branch's cases — the case that covers the recipe
    is right there, and unchanged. What the branch is behind on is the *reference*
    that makes it answer for the persona, so a landing view reading the graph off
    the branch's tree agrees with the branch that the persona is covered by nobody.
    The merge restores a recipe the branch never touched.
    """
    repo, fork, base = _wired_after_the_fork(tmp_path)
    _git(repo, "checkout", "-q", "-b", "evil", fork)
    _touch(repo, PERSONA, "---\nname: reviewer\nedited: yes\n---\n")
    head = _commit(repo, "edit the persona only")
    assert _git(repo, "diff", "--name-only", fork, head) == PERSONA

    report = analyze(repo, base, head=head, ratchet=True)
    assert report["status"] == "uncovered"
    assert any(PERSONA in item and "auth-case" in item
               for item in report["coverage_stale"]), report
    assert report["coverage_debt"] == [] and report["coverage_regressions"] == []


def test_a_reference_this_branch_removes_is_not_added_back(tmp_path):
    """The direction the subtraction exists for.

    An edge the branch itself deletes is gone after the merge, so the persona it
    used to reach really is uncovered: debt, which is survivable, and not stale,
    which is not. Adding the base branch's edges without subtracting the fork's
    would turn every such branch into a fatal one.
    """
    repo, fork, base = _wired_after_the_fork(tmp_path)
    _git(repo, "checkout", "-q", "-b", "feature", base)
    _touch(repo, RECIPE, UNWIRED)                   # the branch takes the wiring out
    _touch(repo, PERSONA, "---\nname: reviewer\nedited: yes\n---\n")
    head = _commit(repo, "unwire the persona and edit it")

    report = analyze(repo, base, head=head, ratchet=True)
    assert report["coverage_stale"] == [], report
    assert report["coverage_debt"] == [PERSONA], report
    assert report["status"] == "debt"


def test_the_landing_graph_adds_what_the_base_wired_in_the_branchs_own_spelling(tmp_path):
    """The rule on its own, including the translation the walk depends on.

    `_graph` describes this repository through one reader and every other tree
    through another, and they do not spell every node id the same way. The base and
    fork readings go through the same reader so their difference is clean, but the
    edges that survive it are joined onto the branch's graph and walked from a node
    the branch names — so they have to arrive in the branch's dialect.
    """
    from rig_workbench.eval.affected import _landing_graph, _reachable_recipes, _surface

    head_nodes = {RECIPE: {"id": "recipe:auth", "kind": "recipe", "path": RECIPE},
                  PERSONA: {"id": "persona:reviewer", "kind": "persona", "path": PERSONA}}
    other_nodes = {**head_nodes,
                   PERSONA: {"id": "persona:facets/reviewer", "kind": "persona",
                             "path": PERSONA}}
    kept = {"from": "recipe:auth", "to": "persona:facets/reviewer"}
    fork_edges = [{"from": "recipe:auth", "to": "instruction:login"}]

    nodes, edges = _landing_graph((head_nodes, []), (other_nodes, [kept, *fork_edges]),
                                  (other_nodes, fork_edges))
    assert {"from": "recipe:auth", "to": "persona:reviewer"} in edges, edges
    assert kept not in edges, "an id the branch spells differently must be translated"
    # An edge the fork already had is not "gained", so a branch that removed it keeps
    # its removal; and a surface the branch does not have keeps the base's own node.
    assert [edge for edge in edges if edge["to"] == "instruction:login"] == []
    assert _reachable_recipes((nodes, edges), [_surface(PERSONA)])[PERSONA] == ["auth"]
    assert _landing_graph((head_nodes, []), None, (other_nodes, [])) is None
    assert _landing_graph((head_nodes, []), (other_nodes, []), None) is None


def test_the_two_graph_readers_agree_once_ids_are_translated():
    """This repository is the tree where the two readers actually differ.

    Every fixture above is read by one reader at all three revisions, so nothing in
    them exercises the translation. Here `_graph` answers the working tree through
    `build_brick_graph` and the revision through its adapter, and the ids for the
    wiki pages disagree — which is the case that would quietly attach the base
    branch's edges to nodes the walk never visits.
    """
    from rig_workbench.eval.affected import _graph, _graph_at, _landing_graph
    from rig_workbench.orchestrate import config

    root = pathlib.Path(__file__).resolve().parents[1]
    if subprocess.run(["git", "rev-parse", "--git-dir"], cwd=root,
                      capture_output=True).returncode != 0:
        pytest.skip("not a git checkout")
    if config.RIG_HOME.resolve() != root.resolve():
        # `_graph` only reaches `build_brick_graph` for the tree it calls home, and
        # an installed rig elsewhere takes that away — leaving one reader on both
        # sides, which is the case every fixture already covers.
        pytest.skip("this checkout is not the rig home, so both reads use one reader")
    head = _graph(root)
    at_head = _graph_at(root, "HEAD")
    assert at_head is not None
    renamed = {node["id"] for path, node in at_head[0].items()
               if path in head[0] and head[0][path]["id"] != node["id"]}
    assert renamed, "the readers agree here now — this test no longer proves anything"

    # An empty fork makes every edge the revision reader saw a gained one, so all of
    # them go through the translation.
    _nodes, edges = _landing_graph(head, at_head, ({}, []))
    assert not [edge for edge in edges
                if edge["from"] in renamed or edge["to"] in renamed], "untranslated ids"


def test_a_base_graph_that_cannot_be_read_is_named_rather_than_passed(tmp_path,
                                                                     monkeypatch):
    """Same stance as the coverage beside it, and for a sharper reason: `_graph`
    answers a tree it cannot parse with an empty graph, and an empty graph adds no
    edges — which is silently the behaviour the base-tip reading replaced."""
    from rig_workbench.eval import affected as module

    repo, base = _repo(tmp_path)
    _touch(repo, INSTRUCTION)
    head = _commit(repo, "edit a surface")
    monkeypatch.setattr(module, "_graph_at", lambda *args, **kwargs: None)

    report = analyze(repo, base, head=head, ratchet=True)
    assert report["coverage_base_unreadable"] is True
    assert report["status"] == "uncovered"
    strict = analyze(repo, base, head=head, require_cases=True)
    assert strict["coverage_base_unreadable"] is False


def test_a_graph_the_reader_gave_up_on_is_not_a_base_that_wired_nothing(tmp_path,
                                                                       monkeypatch):
    """How that refusal is actually reached, which is the part worth pinning.

    `_graph` reports a tree it cannot parse as a graph with nothing in it, and a
    graph with nothing in it adds no edges — the answer is indistinguishable from a
    base branch that wired nothing up, and identical to the behaviour this reading
    replaced. Surfaces in the tree and no nodes out is the signature.
    """
    from rig_workbench.eval import affected as module

    repo, fork, base = _forked(tmp_path)
    _git(repo, "checkout", "-q", "-b", "evil", fork)
    _touch(repo, INSTRUCTION, "rewritten by a branch that carries no case\n")
    head = _commit(repo, "edit the covered prompt")
    monkeypatch.setattr(module, "_graph", lambda *args, **kwargs: ({}, []))

    report = analyze(repo, base, head=head, ratchet=True)
    assert report["coverage_base_unreadable"] is True, report


def test_a_registry_root_the_base_branch_added_after_the_fork_is_not_a_narrowing(tmp_path):
    """The same three-way reading, and why the registry does not get a `stale`.

    Being behind on a root means the merge lands the base branch's *wider* field of
    view, so nothing stops being seen and there is nothing to demand. Being behind
    on a case means the merge lands somebody's case next to an unmeasured edit.
    """
    from rig_workbench.eval.affected import _landing_registry, _registry_narrowings

    def root(prefix, extensions=(".md",)):
        return {"prefix": prefix, "kind": prefix.strip("/"), "recursive": True,
                "extensions": list(extensions)}

    # `commands/` is where the base branch widened a root the branch already had:
    # a new extension, and it made it recursive, and it renamed its kind.
    narrow = {**root("commands/"), "recursive": False, "kind": "old"}
    fork = {"agents/": root("agents/"), "commands/": narrow}
    base = {"agents/": root("agents/", (".md", ".yaml")),
            "commands/": root("commands/", (".md", ".yaml")),
            "patterns/": root("patterns/")}
    behind = {"roots": [root("agents/"), dict(narrow)]}   # forked before all of it

    landing = _landing_registry(behind, base, fork)
    assert _registry_narrowings(base, landing) == []
    landed = {item["prefix"]: item for item in landing["roots"]}
    assert landed["commands/"]["recursive"] is True
    assert landed["commands/"]["kind"] == "commands"
    # And what this branch really does take away is still fatal.
    removed = _landing_registry({"roots": []}, base, fork)
    assert any("agents/" in item for item in _registry_narrowings(base, removed))


def test_a_registry_root_the_base_branch_removed_after_the_fork_is_not_charged_here(
        tmp_path):
    """The registry comparison read against the base tip, through `analyze_affected`.

    The helper above pins the rule; this pins that the analysis is wired to it. A
    root the *base branch* deleted after the fork is still in the fork point's
    registry, so comparing against the fork accused a branch that is merely behind
    of removing it — and a narrowing is fatal in both modes.
    """
    from rig_workbench.eval.affected import REGISTRY_REL, prompt_surface_registry

    repo, _root = _repo(tmp_path)
    declared = prompt_surface_registry()
    extra = {"prefix": "retired/", "kind": "retired", "recursive": True,
             "extensions": [".md"]}
    _touch(repo, REGISTRY_REL,
           json.dumps({**declared, "roots": [*declared["roots"], extra]}, indent=2))
    fork = _commit(repo, "a registry with a root that is about to be retired")
    _touch(repo, REGISTRY_REL, json.dumps(declared, indent=2))
    base = _commit(repo, "the base branch retires it")

    _git(repo, "checkout", "-q", "-b", "feature", fork)
    _touch(repo, REGISTRY_REL, json.dumps(declared, indent=2) + "\n")
    head = _commit(repo, "this branch touches the registry for its own reasons")

    report = analyze(repo, base, head=head, ratchet=True)
    assert report["registry_changed"] is True
    assert report["registry_narrowings"] == [], report
    assert report["status"] != "uncovered", report


def test_a_case_the_base_branch_already_deleted_is_not_this_branchs_regression(tmp_path):
    """Coverage regression is `(base & fork) - head`, not `fork - head`.

    Against the fork point alone, a branch that deletes a case the base branch has
    already deleted is charged for it — the coverage it "removed" is not on the base
    branch to remove. Fatal, and unclearable except by restoring a case somebody
    else retired.
    """
    repo, _root = _repo(tmp_path)
    _touch(repo, INSTRUCTION)
    _write_case(repo, "login-case", ["instruction:login"])
    fork = _commit(repo, "a covered surface")
    _git(repo, "rm", "-q", "-r", "evals/cases/login-case")
    base = _commit(repo, "the base branch retires the case")

    _git(repo, "checkout", "-q", "-b", "feature", fork)
    _git(repo, "rm", "-q", "-r", "evals/cases/login-case")
    _touch(repo, INSTRUCTION, "edited, with the case gone from both sides\n")
    head = _commit(repo, "retire the same case and edit the surface")

    report = analyze(repo, base, head=head, ratchet=True)
    assert report["coverage_regressions"] == [], report
    assert report["coverage_stale"] == [] and report["coverage_debt"] == [INSTRUCTION]


def test_a_base_coverage_that_cannot_be_read_is_named_rather_than_passed(tmp_path,
                                                                        monkeypatch):
    """`_regressions` shrugs at an unanswerable comparison because it is one guard
    among several. This one is the guard, so it says so instead."""
    from rig_workbench.eval import affected as module

    repo, base = _repo(tmp_path)
    _touch(repo, INSTRUCTION)
    head = _commit(repo, "edit a surface")
    monkeypatch.setattr(module, "_coverage_at", lambda *args, **kwargs: None)

    report = analyze(repo, base, head=head, ratchet=True)
    assert report["coverage_base_unreadable"] is True
    assert report["status"] == "uncovered"
    # Strict mode never asks the question: every uncovered surface already fails it.
    strict = analyze(repo, base, head=head, require_cases=True)
    assert strict["coverage_base_unreadable"] is False


# ── the CLI contract CI depends on ───────────────────────────────────────────
def run_cli(repo, *args):
    import os
    import sys

    env = dict(os.environ)
    env["PYTHONPATH"] = str(pathlib.Path(__file__).parents[1])
    return subprocess.run([sys.executable, "-m", "rig_workbench.cli", "eval", "affected",
                           "--repo", str(repo), *args],
                          capture_output=True, text=True, timeout=60, env=env)


def test_debt_exits_zero_and_uncovered_exits_one(tmp_path):
    repo, base = _repo(tmp_path)
    _touch(repo, INSTRUCTION)
    _commit(repo, "touch the instruction")
    debt = run_cli(repo, "--base", base, "--head", "HEAD", "--ratchet")
    assert debt.returncode == 0
    assert json.loads(debt.stdout)["status"] == "debt"
    strict = run_cli(repo, "--base", base, "--head", "HEAD", "--require-cases")
    assert strict.returncode == 1
    assert json.loads(strict.stdout)["status"] == "uncovered"


# ── the acceptance gate drives the same ratchet ──────────────────────────────
# `evaluate_gate` is the path the local `prompt_regression_passed` criterion runs.
# It used to be hard-wired to `require_cases=True` while CI drove `--ratchet`, so
# the same branch failed locally and passed in CI. These pin the parity.
def gate(repo, base, head="working", **kwargs):
    from rig_workbench.eval.gate import evaluate_gate

    return evaluate_gate(repo, base=base, head=head,
                         evidence_dir=repo / ".rig" / "evals" / "results", **kwargs)


def test_gate_ratchet_reports_debt_without_failing(tmp_path):
    repo, base = _repo(tmp_path)
    _touch(repo, INSTRUCTION)
    report, code = gate(repo, base, ratchet=True)
    assert code == 0 and report["status"] == "debt"
    assert report["coverage_debt"] == [INSTRUCTION]
    assert report["failures"] == []
    assert report["status"] != analyze(repo, base, require_cases=True)["status"]


def test_gate_without_ratchet_keeps_its_exact_old_meaning(tmp_path):
    """The default is untouched: `affected-run` and `eval gate` still run strict."""
    repo, base = _repo(tmp_path)
    _touch(repo, INSTRUCTION)
    report, code = gate(repo, base)
    assert code == 1 and report["status"] == "failed"
    assert report["failures"] == [f"uncovered:{INSTRUCTION}"]


def test_gate_ratchet_fails_on_a_coverage_regression_and_says_which(tmp_path):
    """A deleted case touches no prompt surface, so it never lands in `uncovered`.
    Reporting only those paths left this failing with an empty `failures` list — a
    verdict with no reason in it, which is the same silence the ratchet replaced."""
    repo, base = _repo(tmp_path)
    _write_case(repo, "login-case", ["instruction:login"])
    _touch(repo, INSTRUCTION)
    base = _commit(repo, "add coverage")
    (repo / "evals" / "cases" / "login-case" / "case.json").unlink()
    report, code = gate(repo, base, ratchet=True)
    assert code == 1 and report["status"] == "failed"
    assert any(item.startswith("coverage_regression:") and "login-case" in item
               for item in report["failures"])


def test_gate_ratchet_names_stale_coverage_and_what_covers_it(tmp_path):
    """Same empty-`failures` hole as a regression, and the same repair: a surface
    the base branch covers and this change does not touches nothing in `uncovered`,
    so the reason has to be named or the exit code stands alone."""
    repo, fork, base = _forked(tmp_path)
    _git(repo, "checkout", "-q", "-b", "evil", fork)
    _touch(repo, INSTRUCTION, "rewritten by a branch that carries no case\n")
    head = _commit(repo, "edit the covered prompt, carrying no case")

    report, code = gate(repo, base, head=head, ratchet=True)
    assert code == 1 and report["status"] == "failed"
    assert any(item.startswith(f"coverage_stale:{INSTRUCTION} ") and "login-case" in item
               for item in report["failures"]), report
    assert report["coverage_debt"] == []


def test_gate_ratchet_names_an_unreadable_base_rather_than_only_exiting_one(tmp_path,
                                                                           monkeypatch):
    """The report field is checked elsewhere; this is the sentence CI prints.

    Without it the gate fails with an empty `failures` list — the hole the comment
    beside this branch says it repaired — and the only thing left in the log is an
    exit code for a refusal whose remedy is nothing the author did.
    """
    from rig_workbench.eval import affected as module

    repo, base = _repo(tmp_path)
    _touch(repo, INSTRUCTION)
    head = _commit(repo, "edit a surface")
    monkeypatch.setattr(module, "_coverage_at", lambda *args, **kwargs: None)

    report, code = gate(repo, base, head=head, ratchet=True)
    assert code == 1 and report["status"] == "failed"
    assert "coverage_base_unreadable" in report["failures"], report


def test_the_measurement_path_refuses_a_stale_branch_and_names_why(tmp_path):
    """The maintainer's own command, which the fork handoff in the workflow names.

    `affected-run` is where evidence is produced, and it refuses to produce any for
    a tree the gate would reject — signing a measurement of a tree that cannot land
    is the failure mode both ratchets exist to prevent. Reached before a provider is
    started, so this needs none.
    """
    from rig_workbench.eval.affected_run import run_affected

    repo, fork, base = _forked(tmp_path)
    _git(repo, "checkout", "-q", "-b", "evil", fork)
    _touch(repo, INSTRUCTION, "rewritten by a branch that carries no case\n")
    _commit(repo, "edit the covered prompt, carrying no case")

    report, code, destination = run_affected(
        repo, base=base, head="HEAD", provider="command", model="fixture",
        judge_provider="command", judge_model="fixture",
        provider_command="false", judge_command="false", ratchet=True,
    )
    assert code == 1 and destination is None, report
    assert [item for item in report["failures"]
            if item.startswith(f"coverage_stale:{INSTRUCTION} ")], report


def test_gate_ratchet_still_fails_on_an_unregistered_surface_kind(tmp_path):
    repo, base = _repo(tmp_path)
    _touch(repo, "skills/engine/recipes/notes.txt")
    report, code = gate(repo, base, ratchet=True)
    assert code == 1 and report["status"] == "failed"
    assert report["failures"] == ["uncovered:skills/engine/recipes/notes.txt"]


def test_gate_ratchet_names_a_narrowed_registry(tmp_path):
    """The registry is judged by `_registry_narrowings`, not by `uncovered` — same
    empty-`failures` hole, reported the same way."""
    from rig_workbench.eval.affected import REGISTRY_REL

    repo, base = _repo(tmp_path)
    registry = repo / REGISTRY_REL
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps({
        "prompt_surface_registry_version": 2,
        "roots": [{"prefix": "retired/", "kind": "retired", "recursive": True,
                   "extensions": [".md"]}],
    }), encoding="utf-8")
    base = _commit(repo, "declare a root the code no longer has")
    registry.write_text(json.dumps({"prompt_surface_registry_version": 2, "roots": []}),
                        encoding="utf-8")
    report, code = gate(repo, base, ratchet=True)
    assert code == 1 and report["status"] == "failed"
    assert any(item.startswith("registry_narrowed:") and "retired/" in item
               for item in report["failures"])
