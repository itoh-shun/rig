"""A workflow may adapt to the risk; what it is trusted for may not (#432).

The module refuses rather than plans, so what these tests hold it to is the floor: that a
planner cannot shrink it by leaving a step out, cannot shrink it by relabelling one, and
cannot select a component nobody registered.
"""

import dataclasses
import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench import synthesis
from rig_workbench.workbench.synthesis import (OPERATOR_REQUESTED, PLANNER_PROPOSED,
                                              Required, check_floor,
                                              POLICY_REQUIRED, SCHEMA, load,
                                               floor, missing_floor, resolve, validate,
                                              weakened)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"
CATALOG = frozenset({"implement", "review-diff", "security-audit", "migration-dry-run"})


def _step(step_id, source="planner-proposed", reason="because"):
    return {"id": step_id, "source": source, "reason": reason}


def _workflow(*steps):
    return {"schema": SCHEMA, "steps": list(steps) or [_step("implement")]}


# ── the catalog is the catalog ───────────────────────────────────────────────
def test_a_component_nobody_registered_is_refused():
    """"the planner invented a step" and "the planner selected a step" look identical in a
    resolved workflow, and only one of them is what synthesis is for."""
    problems = validate(_workflow(_step("run-arbitrary-shell")), CATALOG)
    assert any("not a registered component" in p for p in problems), problems


def test_a_step_repeated_is_refused():
    problems = validate(_workflow(_step("implement"), _step("implement")), CATALOG)
    assert any("more than once" in p for p in problems), problems


def test_a_workflow_of_nothing_is_refused():
    """It passes every check it contains, which is none."""
    assert any("verifies nothing" in p
               for p in validate({"schema": SCHEMA, "steps": []}, CATALOG))


def test_a_step_without_a_reason_is_refused():
    """A workflow that cannot say why it contains what it contains is a list, and the
    resolved workflow is meant to be evidence."""
    problems = validate(_workflow({"id": "implement", "source": "planner-proposed"}), CATALOG)
    assert any("no reason" in p for p in problems), problems


def test_an_unknown_source_is_refused_and_names_what_exists():
    problems = validate(_workflow(_step("implement", source="felt-right")), CATALOG)
    assert any("policy-required" in p for p in problems), problems


def test_a_root_key_this_schema_does_not_define_is_refused_too():
    """The same rule one level up. A `floor_held: true` written into the proposal, or a
    `waivable`, would be accepted and then overwritten or discarded — the proposal saying one
    thing and the resolved workflow another, with nothing reporting the difference."""
    proposal = dict(_workflow(_step("review-diff", source=POLICY_REQUIRED, reason="policy")),
                    floor_held=True, waivable=True)
    problems = validate(proposal, CATALOG)
    assert any("'floor_held', 'waivable'" in p for p in problems), problems


def test_the_report_is_not_itself_a_proposal_and_says_so():
    """`resolve` returns a report *about* a workflow. Labelling it `rig.resolved-workflow/v1`
    would make its own `corrections` a key that schema does not define, so feeding the report
    back would be refused for a reason the caller could do nothing about. The workflow-shaped
    half is nested, and it round-trips."""
    report = resolve(_workflow(_step("implement")), CATALOG,
                     floor(**{"review-diff": "org policy"}))
    assert report["schema"] == synthesis.REPORT_SCHEMA != SCHEMA
    assert validate(report, CATALOG), "the report is not a proposal"
    assert validate(report["workflow"], CATALOG) == []
    assert resolve(report["workflow"], CATALOG,
                   floor(**{"review-diff": "org policy"}))["floor_held"] is True


def test_a_key_this_schema_does_not_define_is_refused_rather_than_dropped():
    """Accepting a key and discarding it is the module deciding a planner did not mean what it
    wrote. A `skip` nobody reads would pass validation, vanish in `load`, and leave
    `floor_held` true about a step that no longer says what the proposal said it said."""
    proposal = _workflow({"id": "review-diff", "source": POLICY_REQUIRED,
                          "reason": "org policy", "skip": True, "mode": "quick"})
    problems = validate(proposal, CATALOG)
    assert any("'mode', 'skip'" in p for p in problems), problems
    with pytest.raises(ValueError, match="not part of"):
        resolve(proposal, CATALOG, floor(**{"review-diff": "org policy"}))


def test_the_accepted_keys_are_the_ones_a_step_actually_has():
    """Derived rather than spelled out, so the set `validate` accepts, the one `load` reads and
    the one `weakened` compares cannot drift apart."""
    assert synthesis.STEP_FIELDS == {f.name for f in dataclasses.fields(synthesis.Step)}


def test_every_problem_is_reported_at_once():
    problems = validate({"schema": "wrong", "steps": [{"id": "nope", "source": "no"}]}, CATALOG)
    assert len(problems) >= 4, problems


# ── the floor is computed, never read from the proposal ──────────────────────
def test_a_mandatory_step_left_out_is_restored_and_reported():
    """Omission is the cheapest way for a planner to answer a question it is supposed to be
    constrained by."""
    steps = load(_workflow(_step("implement")), CATALOG)
    absent = missing_floor(steps, floor(**{"review-diff": "org policy requires review"}))
    assert [item["id"] for item in absent] == ["review-diff"]


def test_a_mandatory_step_relabelled_as_the_planners_idea_is_caught():
    """The subtler half. Dropping a step is the obvious way to shrink the floor; keeping it
    while calling it `planner-proposed` is the other — the step still runs, and every later
    reader is told it was optional."""
    steps = load(_workflow(_step("security-audit", source="risk-derived")), CATALOG)
    found = weakened(steps, floor(**{"security-audit": "org policy requires it for auth changes"}))
    assert [(w["id"], w["claimed"]) for w in found] == [("security-audit", "risk-derived")]


@pytest.mark.parametrize("claimed", ["risk-derived", "planner-proposed",
                                    "task-type-default", "operator-requested"])
def test_only_policy_required_is_the_right_label_for_a_policy_step(claimed):
    """`operator-requested` is mandatory too, so a check written against
    `MANDATORY_SOURCES` would let a policy step be relabelled as a human's request and call
    the floor held. It still runs — and every later reader is told a person asked for it,
    which is a waiver away from being dropped, where a policy requirement is not."""
    steps = load(_workflow(_step("review-diff", source=claimed)), CATALOG)
    found = weakened(steps, floor(**{"review-diff": "org policy"}))
    assert [w["id"] for w in found] == ["review-diff"], claimed
    resolved = resolve(_workflow(_step("review-diff", source=claimed)), CATALOG,
                       floor(**{"review-diff": "org policy"}))
    assert resolved["floor_held"] is False, claimed
    assert resolved["workflow"]["steps"][0]["source"] == POLICY_REQUIRED


def test_a_mandatory_step_reproduced_exactly_is_not_a_correction():
    steps = load(_workflow(_step("review-diff", source=POLICY_REQUIRED, reason="org policy")),
                 CATALOG)
    assert weakened(steps, floor(**{"review-diff": "org policy"})) == []
    assert missing_floor(steps, floor(**{"review-diff": "org policy"})) == []


def test_a_mandatory_step_kept_with_the_reason_rewritten_is_caught():
    """The third way to shrink the floor while appearing to hold it, after omitting the step
    and relabelling its source. The id and source match, so a field-by-field check calls the
    floor held — while the reason the workflow gives for the step is now the planner's, and a
    reader deciding whether the step may be skipped is reading the planner's opinion under the
    policy's name."""
    weak = "a quick glance is enough here"
    strong = "org policy: every change to an auth boundary gets a full review"
    steps = load(_workflow(_step("review-diff", source=POLICY_REQUIRED, reason=weak)), CATALOG)
    found = weakened(steps, floor(**{"review-diff": strong}))
    assert [(w["id"], w["claimed_reason"], w["reason"]) for w in found] == [
        ("review-diff", weak, strong)]

    result = resolve(_workflow(_step("review-diff", source=POLICY_REQUIRED, reason=weak)),
                     CATALOG, floor(**{"review-diff": strong}))
    assert result["floor_held"] is False
    assert result["workflow"]["steps"][0]["reason"] == strong


def test_nothing_on_a_mandatory_step_is_the_planners_to_author():
    """The rule the three escapes converged on, stated once. Comparing the whole step against
    the whole floor entry means a field added to `Step` later is covered without anyone
    remembering to extend a list."""
    entry = Required(id="review-diff", source=POLICY_REQUIRED, reason="org policy")
    # A different *valid* value per field — an invalid one would be refused by `validate`
    # before the floor was consulted, which proves nothing about the floor.
    alternatives = {"id": "implement", "source": OPERATOR_REQUESTED,
                    "reason": "the planner's own account"}
    assert set(alternatives) == {f.name for f in dataclasses.fields(synthesis.Step)}, (
        "a field was added to Step; give it a valid alternative so this rule still covers it")
    for name, value in alternatives.items():
        altered = dataclasses.replace(entry.as_step(), **{name: value})
        result = resolve(_workflow(altered.as_dict()), CATALOG, (entry,))
        assert result["floor_held"] is False, name
        assert entry.as_step().as_dict() in result["workflow"]["steps"], name


def test_resolving_restores_the_floor_without_dropping_the_proposal():
    result = resolve(_workflow(_step("implement", source="task-type-default", reason="code"),
                               _step("security-audit", reason="looked risky")),
                     CATALOG,
                     floor(**{"security-audit": "policy: auth changes",
                              "review-diff": "policy: all"}))
    ids = [s["id"] for s in result["workflow"]["steps"]]
    assert "implement" in ids                      # the planner's own step survives
    assert "review-diff" in ids                    # the omitted mandatory one is added
    assert result["floor_held"] is False
    audit = next(s for s in result["workflow"]["steps"] if s["id"] == "security-audit")
    assert audit["source"] == POLICY_REQUIRED      # the relabelled one is put back
    assert audit["reason"] == "policy: auth changes"


def test_a_proposal_that_already_holds_the_floor_is_left_alone():
    proposal = _workflow(_step("implement", source="task-type-default", reason="code"),
                         _step("review-diff", source=POLICY_REQUIRED, reason="policy: all"))
    result = resolve(proposal, CATALOG, floor(**{"review-diff": "policy: all"}))
    assert result["floor_held"] is True
    assert result["corrections"] == {"restored": [], "relabelled": []}
    assert [s["id"] for s in result["workflow"]["steps"]] == ["implement", "review-diff"]


OPERATOR_FLOOR = floor({"migration-dry-run": (OPERATOR_REQUESTED, "the operator asked for it")})


def test_an_operator_requested_step_the_planner_deleted_is_caught():
    """A planner deciding a human asked for too much is the same overreach wearing a
    friendlier name — and the one the floor has to be built to see.

    `Step.mandatory` cannot answer this. It describes steps that are *in* the proposal, and a
    deleted step has no source left to read: "the planner dropped what the operator asked
    for" and "nobody asked for it" become the same proposal. So the floor is built from the
    operator's instruction, not from the thing being checked.
    """
    steps = load(_workflow(_step("implement")), CATALOG)
    absent = missing_floor(steps, OPERATOR_FLOOR)
    assert [(item["id"], item["source"]) for item in absent] == [
        ("migration-dry-run", OPERATOR_REQUESTED)]


def test_a_deleted_operator_request_is_restored_under_the_operators_name():
    """Restoring it as `policy-required` would be a second falsehood: it would tell a later
    reader an organisation requires the step, when the truth is that a person asked for it and
    that person can withdraw it."""
    result = resolve(_workflow(_step("implement")), CATALOG, OPERATOR_FLOOR)
    assert result["floor_held"] is False
    restored = next(s for s in result["workflow"]["steps"] if s["id"] == "migration-dry-run")
    assert restored["source"] == OPERATOR_REQUESTED
    assert restored["reason"] == "the operator asked for it"


def test_an_operator_request_relabelled_as_the_planners_idea_is_caught():
    steps = load(_workflow(_step("migration-dry-run", source=PLANNER_PROPOSED,
                                 reason="seemed useful")), CATALOG)
    found = weakened(steps, OPERATOR_FLOOR)
    assert [(w["id"], w["claimed"], w["source"]) for w in found] == [
        ("migration-dry-run", PLANNER_PROPOSED, OPERATOR_REQUESTED)]


def test_an_operator_request_the_planner_kept_correctly_is_not_a_correction():
    steps = load(_workflow(_step("migration-dry-run", source=OPERATOR_REQUESTED,
                                 reason="the operator asked for it")), CATALOG)
    assert missing_floor(steps, OPERATOR_FLOOR) == []
    assert weakened(steps, OPERATOR_FLOOR) == []


def test_the_floor_is_not_read_from_the_proposal():
    """The whole point of the floor being an argument. A proposal that calls its own step
    `operator-requested` does not thereby put it on the floor — otherwise a planner could
    write its own floor and every check would pass by construction."""
    steps = load(_workflow(_step("migration-dry-run", source=OPERATOR_REQUESTED,
                                 reason="the planner said so")), CATALOG)
    assert missing_floor(steps, ()) == []
    assert resolve(_workflow(_step("implement")), CATALOG, ())["floor_held"] is True


# ── the floor is the stricter document, not the unchecked one ────────────────
def test_resolving_never_produces_a_workflow_this_module_would_reject():
    """The invariant, stated once rather than as three checks that happen to cover it.

    `resolve` writes floor entries straight into the resolved workflow, so anything the floor
    is allowed to hold is a way to get a step past `validate` by putting it on the floor
    instead of in the proposal — the one document that was supposed to be the stricter of the
    two.
    """
    for entry in ({"id": "invented", "source": POLICY_REQUIRED, "reason": "policy"},
                  {"id": "review-diff", "source": POLICY_REQUIRED, "reason": "   "},
                  {"id": "", "source": POLICY_REQUIRED, "reason": "policy"},
                  {"id": "review-diff", "source": PLANNER_PROPOSED, "reason": "seemed useful"}):
        with pytest.raises(ValueError):
            required = check_floor((Required(**entry),), CATALOG)
            resolve(_workflow(_step("implement")), CATALOG, required)


def test_a_floor_entry_with_no_step_id_is_refused_where_the_catalog_cannot_catch_it():
    """`floor()` is built before the catalog is loaded, so the catalog check is not there yet.
    A blank id survives as a requirement no step can ever satisfy: `missing_floor` reports it
    on every proposal, and the message names nothing the reader can act on."""
    for bad in ("", "   ", None):
        with pytest.raises(ValueError, match="no step id"):
            Required(id=bad, source=POLICY_REQUIRED, reason="policy")
    with pytest.raises(ValueError, match="no step id"):
        floor({"  ": (POLICY_REQUIRED, "policy")})


def test_every_unregistered_floor_component_is_named_in_a_stable_order():
    """One id at a time proves nothing about ordering, and a message whose order depends on
    the caller's container makes two runs of the same command look like different failures."""
    with pytest.raises(ValueError, match=r"nobody registered: aardvark, zebra"):
        resolve(_workflow(_step("implement")), CATALOG,
                (Required(id="zebra", source=POLICY_REQUIRED, reason="policy"),
                 Required(id="aardvark", source=POLICY_REQUIRED, reason="policy")))


def test_a_floor_may_not_add_to_the_catalog_either():
    """`validate` refuses a proposal naming an unregistered component. A floor that could name
    one would let the caller do through the floor exactly what the planner may not do."""
    with pytest.raises(ValueError, match="nobody registered"):
        resolve(_workflow(_step("implement")), CATALOG,
                floor(**{"invented": "policy says so"}))


def test_a_floor_source_the_planner_could_have_chosen_is_refused():
    """A floor entry sourced `planner-proposed` is the planner writing its own floor. The
    three non-mandatory sources are conclusions, and a conclusion cannot require anything."""
    for source in ("planner-proposed", "risk-derived", "task-type-default"):
        with pytest.raises(ValueError, match="conclusions the planner reached"):
            Required(id="review-diff", source=source, reason="because")


def test_two_authorities_requiring_one_step_is_refused_rather_than_resolved_by_order():
    """Keying by id would silently keep one of them, and which one depends on how the caller
    built the tuple — so whether a person may withdraw the step would turn on argument order.
    """
    with pytest.raises(ValueError, match="twice"):
        floor({"review-diff": (OPERATOR_REQUESTED, "the operator asked")},
              **{"review-diff": "org policy"})


def test_an_invalid_proposal_raises_rather_than_resolving():
    with pytest.raises(ValueError, match="not a resolved workflow"):
        resolve(_workflow(_step("run-arbitrary-shell")), CATALOG, ())


# ── it does not classify ─────────────────────────────────────────────────────
#: What the module is allowed to import at module scope. An allowlist, and read as an AST:
#: the first version of this test searched the source text and failed on its own docstring,
#: which says "It does not classify" — the same mistake #472 made twice before settling on
#: parsing. Prose naming a thing is not a dependency on it.
_ALLOWED_MODULE_IMPORTS = {"__future__", "annotations", "dataclasses"}


def test_the_module_reaches_for_no_model_and_no_process():
    """Deciding that a diff touches an authentication boundary is reading, judging and
    concluding. A module that called a model to do it would leave nothing a gate could check
    and nothing a mutation could falsify.

    Module-scope imports only: `cmd_synthesis` imports `json`, `pathlib` and `sys` inside
    itself, the way the other commands in this package do, and those are the CLI's business
    rather than the validator's.
    """
    import ast

    tree = ast.parse(pathlib.Path(synthesis.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in tree.body:                     # module scope only, not inside functions
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[-1])
            imported.update(a.name for a in node.names)
    assert imported <= _ALLOWED_MODULE_IMPORTS, imported - _ALLOWED_MODULE_IMPORTS

    called = {n.func.attr if isinstance(n.func, ast.Attribute)
              else n.func.id if isinstance(n.func, ast.Name)
              else f"<unreviewable: {type(n.func).__name__}>"
              for n in ast.walk(tree) if isinstance(n, ast.Call)}
    for forbidden in ("run", "Popen", "system", "post", "create", "generate"):
        assert forbidden not in called, forbidden


# ── the command exits with the answer ────────────────────────────────────────
def _run(tmp_path, proposal, required=None, catalog=None, json_out=False,
         required_text=None):
    files = {}
    for name, value in (("workflow", proposal),
                        ("catalog", sorted(CATALOG if catalog is None else catalog)),
                        ("required", required)):
        if value is None:
            continue
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        files[name] = str(path)
    if required_text is not None:
        # Written as text because `json.dumps` cannot produce the duplicate keys JSON allows.
        path = tmp_path / "required.json"
        path.write_text(required_text, encoding="utf-8")
        files["required"] = str(path)
    argv = ["synthesise", files["workflow"], files["catalog"]]
    if "required" in files:
        argv += ["--required", files["required"]]
    if json_out:
        argv.append("--json")
    return subprocess.run([sys.executable, str(WORKBENCH), *argv],
                          capture_output=True, text=True, cwd=REPO_ROOT, timeout=60)


@pytest.mark.parametrize("order", [
    '{"review-diff": "org policy", '
    '"review-diff": {"source": "operator-requested", "reason": "the operator asked"}}',
    '{"review-diff": {"source": "operator-requested", "reason": "the operator asked"}, '
    '"review-diff": "org policy"}',
])
def test_the_command_refuses_a_floor_naming_one_step_twice(tmp_path, order):
    """`check_floor` refuses two authorities for one step — but JSON allows a key twice and
    `json.loads` keeps the last one, so by the time a parsed dict reached that check the two
    had already become one, and which one survived was the order the file happened to be
    written in. The same file in the other order gave the other answer to "may a person
    withdraw this step"."""
    result = _run(tmp_path, _workflow(_step("implement")), required_text=order)
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert "twice" in json.loads(result.stdout)["error"], result.stdout


def test_the_command_refuses_a_proposal_naming_one_key_twice(tmp_path):
    """The floor was read this way from round 5; the proposal crosses the same lossy boundary
    and matters more. A step whose `reason` appears twice reaches the comparison saying only
    the last one, so a mandatory step can carry two authorships on disk and still compare equal
    to the floor."""
    text = ('{"schema": "%s", "steps": [{"id": "review-diff", "source": "policy-required", '
            '"reason": "a glance will do", "reason": "org policy"}]}' % SCHEMA)
    path = tmp_path / "workflow.json"
    path.write_text(text, encoding="utf-8")
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(sorted(CATALOG)), encoding="utf-8")
    result = subprocess.run([sys.executable, str(WORKBENCH), "synthesise",
                             str(path), str(catalog_path)],
                            capture_output=True, text=True, cwd=REPO_ROOT, timeout=60)
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    error = json.loads(result.stdout)["error"]
    assert "workflow names 'reason' twice" in error, error


@pytest.mark.parametrize("value", ["null", "true", "3", "[]"])
def test_a_short_form_floor_reason_that_is_not_a_string_is_refused(tmp_path, value):
    """`str(value)` would turn `null` into the reason `"None"` and then protect that as though
    the prose had been written in the file — a floor entry passing the "records why, or
    refuses" rule on a string nobody wrote."""
    result = _run(tmp_path, _workflow(_step("implement")),
                  required_text='{"review-diff": %s}' % value)
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert "expected a reason string" in json.loads(result.stdout)["error"], result.stdout


def test_a_structured_floor_entry_without_a_source_is_a_policy_requirement(tmp_path):
    """The short form's default, stated for the structured form too. Defaulting to
    `operator-requested` instead would make an unattributed floor entry withdrawable by a
    person who never asked for it."""
    result = _run(tmp_path, _workflow(_step("implement")),
                  required={"review-diff": {"reason": "org policy"}}, json_out=True)
    assert result.returncode == 1, (result.returncode, result.stdout, result.stderr)
    restored = json.loads(result.stdout)["corrections"]["restored"]
    assert [(c["id"], c["source"]) for c in restored] == [("review-diff", POLICY_REQUIRED)]


def test_a_structured_floor_entry_without_a_reason_is_refused(tmp_path):
    """Defaulting to anything readable would invent prose the file does not contain and then
    protect it as the reason a step is mandatory."""
    result = _run(tmp_path, _workflow(_step("implement")),
                  required={"review-diff": {"source": POLICY_REQUIRED}})
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert "no reason it is required" in json.loads(result.stdout)["error"], result.stdout


@pytest.mark.parametrize("text,why", [
    ('{"run-arbitrary-shell": false}', "an object registers its keys"),
    ('"review-diff"', "a string registers its characters"),
    ('[1, 2]', "a number is not a component id"),
    ('["review-diff", "  "]', "a blank is not a component id"),
])
def test_only_an_array_of_component_ids_registers_anything(tmp_path, text, why):
    """`set(json.loads(...))` takes whatever iterates, and the catalog is the whole basis for
    "the planner selected a step" rather than "the planner invented one". So registering a
    component has to be an act of registration, not a shape that iterates like one —
    `{"run-arbitrary-shell": false}` registers what it looks like it denies."""
    workflow_path = tmp_path / "workflow.json"
    workflow_path.write_text(json.dumps(_workflow(_step("implement"))), encoding="utf-8")
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(text, encoding="utf-8")
    result = subprocess.run([sys.executable, str(WORKBENCH), "synthesise",
                             str(workflow_path), str(catalog_path)],
                            capture_output=True, text=True, cwd=REPO_ROOT, timeout=60)
    assert result.returncode == 2, (why, result.returncode, result.stdout, result.stderr)
    assert "catalog:" in json.loads(result.stdout)["error"], (why, result.stdout)


def test_a_catalog_that_is_an_array_of_ids_is_what_registers_them():
    assert synthesis.load_catalog(["implement", "review-diff"]) == {"implement", "review-diff"}


def test_a_repository_that_has_registered_nothing_yet_refuses_the_proposal_not_the_catalog():
    """An empty array is a well-formed catalog saying nothing is registered, which is a
    different answer from "this file is not a catalog". A proposal against it is refused
    because no component exists — exit 1, the planner's problem — rather than exit 2, which
    would send the caller looking for a malformed file."""
    assert synthesis.load_catalog([]) == frozenset()


def test_the_command_refuses_a_proposal_against_an_empty_catalog_as_a_proposal(tmp_path):
    result = _run(tmp_path, _workflow(_step("implement")), catalog=set())
    assert result.returncode == 1, (result.returncode, result.stdout, result.stderr)
    assert "not a registered component" in result.stderr, result.stderr


def test_the_command_refuses_a_floor_entry_key_it_does_not_define(tmp_path):
    """Closed for the reason a proposed step's keys are: a `waivable` nobody reads would be
    accepted, dropped, and leave the caller believing it said something about the floor."""
    result = _run(tmp_path, _workflow(_step("implement")),
                  required={"review-diff": {"source": POLICY_REQUIRED, "reason": "policy",
                                            "waivable": True}})
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert "'waivable'" in json.loads(result.stdout)["error"], result.stdout


def test_an_execution_error_answers_in_the_schema_a_caller_is_parsing(tmp_path):
    """Exit 2 says the command could not run, and a caller reading stdout for that reason is
    parsing JSON. A payload whose schema is wrong is a failure the caller cannot read."""
    result = _run(tmp_path, _workflow(_step("implement")),
                  required={"invented": "policy says so"})
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    # Its own schema, not the workflow's: nothing was resolved, and `status` and `error` are
    # keys neither the workflow nor the resolution schema defines.
    assert payload["schema"] == synthesis.ERROR_SCHEMA
    assert payload["schema"] not in (SCHEMA, synthesis.REPORT_SCHEMA)
    assert validate(payload, CATALOG), "an error is not a workflow"
    assert payload["status"] == "execution-error"
    assert "nobody registered" in payload["error"]


def test_a_workflow_that_holds_the_floor_exits_zero(tmp_path):
    result = _run(tmp_path,
                  _workflow(_step("review-diff", source=POLICY_REQUIRED, reason="policy")),
                  required={"review-diff": "policy"})
    assert result.returncode == 0, result.stderr
    assert "floor held" in result.stdout


def test_the_command_reads_a_source_from_a_structured_floor_entry(tmp_path):
    """The short form is a policy step because that is the common case. An operator's request
    has to survive the file too, or the floor loses the distinction on the way in."""
    result = _run(tmp_path, _workflow(_step("implement")),
                  required={"migration-dry-run": {"source": OPERATOR_REQUESTED,
                                                  "reason": "the operator asked"}})
    assert result.returncode == 1, (result.returncode, result.stdout)
    assert OPERATOR_REQUESTED in result.stdout, result.stdout
    # The reason travels with the source or the line is half a report: a reader told only
    # that a person required the step cannot tell whether the request still applies.
    assert "the operator asked" in result.stdout, result.stdout


def test_the_human_report_shows_what_the_planner_proposed_next_to_what_is_required(tmp_path):
    """A line saying only what the step is required to say hides the interesting half. The
    person reading this is deciding whether to trust the planner, and that judgement is in the
    difference between the two."""
    weak = "a glance will do"
    result = _run(tmp_path,
                  _workflow(_step("review-diff", source=POLICY_REQUIRED, reason=weak)),
                  required={"review-diff": "org policy: full review"})
    assert result.returncode == 1, (result.returncode, result.stdout)
    assert "org policy: full review" in result.stdout, result.stdout
    assert weak in result.stdout, result.stdout


def test_the_json_output_carries_the_corrections_a_caller_would_act_on(tmp_path):
    """`--json` is what a caller parses; the human lines are what a person reads. A branch with
    no test is a documented option that can stop working without anything noticing."""
    weak = "a glance will do"
    result = _run(tmp_path,
                  _workflow(_step("review-diff", source=POLICY_REQUIRED, reason=weak),
                            _step("implement", source="task-type-default", reason="code")),
                  required={"review-diff": "org policy: full review",
                            "migration-dry-run": {"source": OPERATOR_REQUESTED,
                                                  "reason": "the operator asked"}},
                  json_out=True)
    assert result.returncode == 1, (result.returncode, result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    assert payload["schema"] == synthesis.REPORT_SCHEMA
    assert payload["workflow"]["schema"] == SCHEMA
    assert payload["floor_held"] is False
    assert [(c["id"], c["source"], c["reason"])
            for c in payload["corrections"]["restored"]] == [
        ("migration-dry-run", OPERATOR_REQUESTED, "the operator asked")]
    assert [(c["id"], c["claimed_reason"], c["reason"])
            for c in payload["corrections"]["relabelled"]] == [
        ("review-diff", weak, "org policy: full review")]
    review = next(s for s in payload["workflow"]["steps"] if s["id"] == "review-diff")
    assert review["reason"] == "org policy: full review"


def test_a_floor_the_command_cannot_use_is_an_execution_error_not_a_refusal(tmp_path):
    """Exit 1 says the planner produced something that cannot run as proposed. A floor naming
    an unregistered component says nothing about the planner — the caller built it wrong."""
    result = _run(tmp_path, _workflow(_step("implement")),
                  required={"invented": "policy says so"})
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert "nobody registered" in result.stdout, result.stdout


def test_a_workflow_that_went_below_the_floor_exits_nonzero(tmp_path):
    """Restoring the floor is not the same as accepting the proposal. A command that exited 0
    after correcting one would tell the caller the planner produced something runnable."""
    result = _run(tmp_path, _workflow(_step("implement")),
                  required={"review-diff": "org policy requires review"})
    assert result.returncode == 1, (result.returncode, result.stdout)
    assert "restored" in result.stdout


def test_a_refused_proposal_exits_nonzero_and_says_why(tmp_path):
    result = _run(tmp_path, _workflow(_step("run-arbitrary-shell")))
    assert result.returncode == 1
    assert "REJECTED" in result.stderr


def test_a_file_that_cannot_be_read_is_its_own_status(tmp_path):
    result = subprocess.run(
        [sys.executable, str(WORKBENCH), "synthesise", str(tmp_path / "absent.json"),
         str(tmp_path / "also-absent.json")],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=60)
    assert result.returncode == 2
    assert "execution-error" in result.stdout
