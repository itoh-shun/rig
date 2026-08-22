"""#438 — learn which team works best, without optimisation weakening the boundary.

Grouped by what they hold: the record is a closed schema, an unmeasured provider is not a
good one, hard constraints outrank whatever the routing optimised for, and the module does
not choose.
"""

import dataclasses
import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench import team_routing
from rig_workbench.workbench.team_routing import (ARCHITECTURE_VERIFIER, ASSURANCE_ROLES,
                                                  DEVELOPER, JUDGE, MEASURED, PLANNER,
                                                  SCHEMA, SECURITY_VERIFIER, SHADOW,
                                                  UNMEASURED, Assignment, Constraints,
                                                  check, load, validate, violations)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"


def _measured(provider="acme/model-a", roles=None):
    """A policy that has measured `provider` for the assurance roles, so a test that is not
    about the measurement rule does not have to keep restating it."""
    return {role: frozenset({provider}) for role in (roles or ASSURANCE_ROLES)}


def _assign(role, provider="acme/model-a", confidence=MEASURED, evidence_count=42,
            reasons=("beat the alternatives on this task class",)):
    return {"role": role, "provider": provider, "confidence": confidence,
            "evidence_count": evidence_count,
            # Not `list(reasons)`: a test saying `reasons=None` means the field is null in the
            # document, and coercing it here would test the helper rather than the schema.
            "reasons": list(reasons) if isinstance(reasons, tuple) else reasons}


_DEFAULT = object()


def _record(*assignments, task="a-task", strategy="evidence-v3", **extra):
    """`_record()` is a valid one-assignment record; `_record(*[])` cannot say "empty", so
    `assignments=[]` is passed explicitly by the test that means it."""
    given = extra.pop("assignments", _DEFAULT)
    listed = (list(assignments) or [_assign(DEVELOPER)]) if given is _DEFAULT else list(given)
    return {"schema": SCHEMA, "task": task, "strategy": strategy,
            "assignments": listed, **extra}


# ── the record is closed ─────────────────────────────────────────────────────
def test_a_valid_record_has_no_problems():
    assert validate(_record(_assign(DEVELOPER), _assign(JUDGE, provider="other/model"))) == []


def test_a_routing_that_assigned_nothing_is_refused():
    assert any("routed nothing" in p for p in validate(_record(assignments=[])))


def test_a_role_nobody_defined_is_refused():
    assert any("is not one of" in p for p in validate(_record(_assign("vibes-checker"))))


def test_one_role_cannot_have_two_providers():
    """Two providers for one role is two answers to who is accountable for it."""
    problems = validate(_record(_assign(JUDGE, provider="a"), _assign(JUDGE, provider="b")))
    assert any("assigned more than once" in p for p in problems), problems


def test_a_key_this_schema_does_not_define_is_refused_rather_than_dropped():
    assert any("'cost'" in p for p in validate(_record(_assign(DEVELOPER) | {"cost": 3})))
    assert any("'budget'" in p for p in validate(_record(budget=100)))


def test_the_accepted_keys_are_the_ones_an_assignment_actually_has():
    assert team_routing.ASSIGNMENT_FIELDS == {f.name for f in dataclasses.fields(Assignment)}


def test_a_routing_that_does_not_say_which_strategy_chose_it_is_refused():
    """Without it a change in routing behaviour and a change in the evidence look the same
    afterwards, and neither can be attributed."""
    for strategy in ("", "   "):
        assert any("strategy" in p for p in validate(_record(strategy=strategy))), repr(strategy)


def test_a_selection_with_no_reason_is_a_default_wearing_the_shape_of_a_decision():
    assert any("gives no reason" in p for p in validate(_record(_assign(DEVELOPER, reasons=()))))


# ── an unmeasured provider is not a good one ─────────────────────────────────
def test_a_confidence_nobody_defined_is_refused():
    """Leaving it out would be the gap that reads as "fine" next to a measured competitor."""
    assert any("confidence" in p for p in validate(_record(_assign(DEVELOPER, confidence=None))))
    assert any("confidence" in p for p in validate(_record(_assign(DEVELOPER, confidence="ok"))))


@pytest.mark.parametrize("confidence", [MEASURED, SHADOW])
def test_a_confidence_that_implies_observation_is_refused_on_none(confidence):
    """Whatever `measured` and `shadow` mean, both mean something was observed — and
    `unmeasured` is the word for the other case. Zero observations rejected only for
    `measured` would let a judge claim `shadow` and take the seat anyway."""
    problems = validate(_record(_assign(DEVELOPER, confidence=confidence, evidence_count=0)))
    assert any("no observations" in p for p in problems), (confidence, problems)


@pytest.mark.parametrize("count", [None, -1, True, False, 2.5, "40"])
def test_an_evidence_count_that_is_not_a_count_is_refused(count):
    """`True` is an `int` in Python and would record one observation while reading as a flag
    somebody set."""
    assert any("evidence_count" in p
               for p in validate(_record(_assign(DEVELOPER, evidence_count=count)))), count


def test_zero_observations_is_a_number_and_not_a_missing_field():
    """The interesting value. "Chosen on nothing" and "chosen on four hundred runs" are the
    same sentence without it."""
    assert validate(_record(_assign(DEVELOPER, confidence=UNMEASURED, evidence_count=0))) == []


@pytest.mark.parametrize("confidence", [UNMEASURED, SHADOW])
@pytest.mark.parametrize("role", sorted(ASSURANCE_ROLES))
def test_a_provider_that_is_not_measured_may_not_take_an_assurance_role(role, confidence):
    """Trying it where being wrong is cheap is what shadow evaluation is for. Taking an
    assurance role on no measurement is a promotion nobody made."""
    found = violations(load(_record(_assign(role, provider="new/model",
                                            confidence=confidence, evidence_count=0
                                            if confidence == UNMEASURED else 7))),
                       Constraints())
    assert [v["reason"] for v in found] == [team_routing.NOT_MEASURED], (role, confidence)


def test_an_unmeasured_provider_may_take_a_role_where_being_wrong_is_cheap():
    for role in (PLANNER, DEVELOPER):
        assert violations(load(_record(_assign(role, confidence=UNMEASURED,
                                               evidence_count=0))), Constraints()) == [], role


def test_a_provider_still_being_evaluated_may_not_judge():
    """Shadow evaluation is what you do to a provider *before* trusting it with a verdict.
    Letting it judge while it is still being judged makes the evaluation a formality."""
    found = violations(load(_record(_assign(JUDGE, confidence=SHADOW, evidence_count=9))),
                       Constraints())
    assert [v["reason"] for v in found] == [team_routing.NOT_MEASURED]


def test_shadow_is_not_unmeasured_where_being_wrong_is_cheap():
    """The point of a shadow evaluation is that afterwards you know something. Collapsing it
    into "unmeasured" everywhere would make the evaluation pointless."""
    assert violations(load(_record(_assign(DEVELOPER, confidence=SHADOW, evidence_count=9))),
                      Constraints()) == []


def test_an_admissible_team_still_reports_what_the_record_called_unmeasured():
    """A reader deciding whether to trust the result should see it without having to ask — and
    should read it as the router's word, which is why the field says so and carries the role
    the provider was put in."""
    result = check(_record(_assign(DEVELOPER, provider="new/model", confidence=UNMEASURED,
                                   evidence_count=0)), Constraints())
    assert result["status"] == team_routing.ADMISSIBLE
    assert result["reported_unmeasured"] == [(DEVELOPER, "new/model")]


def test_one_provider_in_two_roles_is_reported_for_each_of_them():
    """Collapsing to a provider name would lose which seat the router was unsure about, and
    "unmeasured as a planner" and "unmeasured as a developer" are different facts."""
    result = check(_record(_assign(DEVELOPER, provider="new/model", confidence=UNMEASURED,
                                   evidence_count=0),
                           _assign(PLANNER, provider="new/model", confidence=UNMEASURED,
                                   evidence_count=0)), Constraints())
    assert result["reported_unmeasured"] == [(DEVELOPER, "new/model"), (PLANNER, "new/model")]


# ── hard constraints outrank whatever the routing optimised for ──────────────
def test_a_team_inside_every_constraint_is_admissible():
    result = check(_record(_assign(DEVELOPER, provider="acme/dev"),
                           _assign(JUDGE, provider="other/judge")),
                   Constraints(approved=frozenset({"acme/dev", "other/judge"}),
                   measured=_measured("other/judge"),
                               capable={JUDGE: frozenset({"other/judge"})}))
    assert result["status"] == team_routing.ADMISSIBLE
    assert result["violations"] == []
    assert result["strategy"] == "evidence-v3"


def test_a_provider_the_policy_did_not_approve_is_refused():
    """Cost and measured excellence are arguments about which approved provider to pick, never
    an argument for picking one that is not."""
    found = violations(load(_record(_assign(DEVELOPER, provider="cheap/model"))),
                       Constraints(approved=frozenset({"acme/model-a"})))
    assert [v["reason"] for v in found] == [team_routing.NOT_APPROVED]


def test_no_approved_set_constrains_nothing_rather_than_everything():
    """An empty policy is a policy that named no providers, not one that forbade all of them —
    reading it the other way would refuse every routing in a repository without the setting."""
    assert violations(load(_record(_assign(DEVELOPER))), Constraints()) == []


def test_a_provider_that_cannot_do_the_role_is_refused():
    """Capability is a fact about what it can run, not a preference to weigh."""
    found = violations(load(_record(_assign(SECURITY_VERIFIER, provider="text-only/model"))),
                       Constraints(capable={SECURITY_VERIFIER: frozenset({"tools/model"})},
                                   measured=_measured("text-only/model")))
    assert [v["reason"] for v in found] == [team_routing.NOT_CAPABLE]


def test_a_role_with_no_stated_capability_is_not_constrained_by_it():
    assert violations(load(_record(_assign(PLANNER))),
                      Constraints(capable={JUDGE: frozenset({"someone"})})) == []


@pytest.mark.parametrize("role", sorted(ASSURANCE_ROLES))
def test_a_verifier_that_wrote_the_change_is_not_a_verifier(role):
    """No evidence about how good a provider is makes its verdict about its own work
    independent."""
    found = violations(load(_record(_assign(DEVELOPER, provider="same/model"),
                                    _assign(role, provider="same/model"))),
                       Constraints(measured=_measured("same/model")))
    assert [v["reason"] for v in found] == [team_routing.NOT_INDEPENDENT], role


def test_the_same_provider_in_two_non_independent_roles_is_fine():
    assert violations(load(_record(_assign(PLANNER, provider="same/model"),
                                   _assign(DEVELOPER, provider="same/model"))),
                      Constraints(measured=_measured("same/model"))) == []


def test_independence_is_only_measured_against_a_developer_that_exists():
    """A review-only routing has no developer to be independent of, and refusing it for that
    would refuse the case the constraint was written to protect."""
    assert violations(load(_record(_assign(JUDGE, provider="same/model"))),
                      Constraints(measured=_measured("same/model"))) == []


def test_a_required_role_nobody_filled_is_refused():
    """An unfilled role is not a cheaper team, it is a missing one."""
    found = violations(load(_record(_assign(DEVELOPER))),
                       Constraints(required=frozenset({SECURITY_VERIFIER, JUDGE})))
    assert [(v["reason"], v["role"]) for v in found] == [
        (team_routing.ROLE_UNFILLED, JUDGE),
        (team_routing.ROLE_UNFILLED, SECURITY_VERIFIER)]


def test_every_violation_is_reported_not_the_first():
    """A router told only about the unapproved provider would swap it and meet the
    independence problem it was always going to meet."""
    found = violations(load(_record(_assign(DEVELOPER, provider="unapproved/model"),
                                    _assign(JUDGE, provider="unapproved/model",
                                            confidence=UNMEASURED, evidence_count=0))),
                       Constraints(approved=frozenset({"acme/model-a"}),
                                   capable={JUDGE: frozenset({"acme/model-a"})}))
    assert {v["reason"] for v in found} == {
        team_routing.NOT_APPROVED, team_routing.NOT_CAPABLE,
        team_routing.NOT_INDEPENDENT, team_routing.NOT_MEASURED}


def test_a_constraint_on_a_role_that_does_not_exist_is_refused():
    """A constraint on a role nothing can be assigned to constrains nothing, and a caller who
    misspelled one would otherwise believe it was in force."""
    for kwargs in ({"capable": {"securty-verifier": frozenset({"a"})}},
                   {"also_independent": frozenset({"judgement"})}):
        with pytest.raises(ValueError, match="do not exist"):
            Constraints(**kwargs)


def test_an_invalid_record_is_refused_without_being_checked():
    result = check({"schema": "wrong"}, Constraints())
    assert result["status"] == team_routing.REFUSED
    assert {v["reason"] for v in result["violations"]} == {"invalid-record"}
    assert result["strategy"] is None


# ── it does not choose ───────────────────────────────────────────────────────
def test_the_module_neither_runs_a_process_nor_calls_a_model():
    """Deciding a provider is right for an authentication review is reading evidence, weighing
    it and concluding. A module that did it would leave nothing a gate could check."""
    import ast
    tree = ast.parse((REPO_ROOT / "rig_workbench" / "workbench"
                      / "team_routing.py").read_text(encoding="utf-8"))
    reaching = {"subprocess", "socket", "http", "urllib", "requests", "os", "open"}
    judging = {"validate", "load", "violations", "check"}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name in judging):
            continue
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        names |= {a.name.split(".")[0] for n in ast.walk(node)
                  if isinstance(n, ast.Import) for a in n.names}
        assert not names & reaching, (node.name, sorted(names & reaching))
    module_level = {a.name.split(".")[0] for n in tree.body
                    if isinstance(n, ast.Import) for a in n.names}
    module_level |= {(n.module or "").split(".")[0] for n in tree.body
                     if isinstance(n, ast.ImportFrom)}
    assert not module_level & reaching, module_level


# ── the command exits with the answer ────────────────────────────────────────
def _run(tmp_path, record, constraints=None, json_out=False, record_text=None,
         constraints_text=None, no_constraints=False):
    routing = tmp_path / "routing.json"
    routing.write_text(record_text if record_text is not None else json.dumps(record),
                       encoding="utf-8")
    argv = ["route-team", str(routing)]
    if not no_constraints:
        path = tmp_path / "constraints.json"
        # `identity: {}` says the provider names are already canonical. Stated rather than
        # defaulted, for the reason the command requires the file at all.
        body = ({"identity": {}} | (constraints or {})) if constraints_text is None else None
        path.write_text(constraints_text if constraints_text is not None else json.dumps(body),
                        encoding="utf-8")
        argv += ["--constraints", str(path)]
    if json_out:
        argv.append("--json")
    return subprocess.run([sys.executable, str(WORKBENCH), *argv],
                          capture_output=True, text=True, cwd=REPO_ROOT, timeout=60)


def test_an_admissible_routing_exits_zero(tmp_path):
    result = _run(tmp_path, _record(_assign(DEVELOPER)))
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "admissible" in result.stdout


def test_a_refused_routing_exits_nonzero(tmp_path):
    """Exiting 0 after refusing would tell the shell the team was usable as recorded."""
    result = _run(tmp_path, _record(_assign(DEVELOPER, provider="cheap/model")),
                  constraints={"approved": ["acme/model-a"]})
    assert result.returncode == 1, (result.returncode, result.stdout)
    assert team_routing.NOT_APPROVED in result.stdout


def test_the_json_output_carries_what_a_caller_would_act_on(tmp_path):
    result = _run(tmp_path,
                  _record(_assign(DEVELOPER, provider="same/model"),
                          _assign(JUDGE, provider="same/model", confidence=UNMEASURED,
                                  evidence_count=0)),
                  constraints={"required": [ARCHITECTURE_VERIFIER]}, json_out=True)
    assert result.returncode == 1, (result.returncode, result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    assert payload["schema"] == SCHEMA
    assert payload["status"] == team_routing.REFUSED
    assert {v["reason"] for v in payload["violations"]} == {
        team_routing.NOT_INDEPENDENT, team_routing.NOT_MEASURED, team_routing.ROLE_UNFILLED}
    assert payload["strategy"] == "evidence-v3"
    assert payload["reported_unmeasured"] == [[JUDGE, "same/model"]]


def test_the_human_report_names_who_was_never_measured(tmp_path):
    result = _run(tmp_path, _record(_assign(DEVELOPER, provider="new/model",
                                            confidence=UNMEASURED, evidence_count=0)))
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "new/model" in result.stdout
    assert "evidence-v3" in result.stdout


def test_a_constraints_key_this_schema_does_not_define_is_refused(tmp_path):
    """Accepting `budget` and discarding it would leave the caller believing a budget was in
    force."""
    result = _run(tmp_path, _record(), constraints={"approved": ["acme/model-a"],
                                                    "budget": 100})
    assert result.returncode == 2, (result.returncode, result.stdout)
    assert "'budget'" in json.loads(result.stdout)["error"], result.stdout


def test_a_required_role_that_does_not_exist_is_an_execution_error(tmp_path):
    result = _run(tmp_path, _record(), constraints={"required": ["securty-verifier"]})
    assert result.returncode == 2, (result.returncode, result.stdout)
    assert "do not exist" in json.loads(result.stdout)["error"]


@pytest.mark.parametrize("which", ["record", "constraints"])
def test_a_document_naming_one_key_twice_is_refused(tmp_path, which):
    """JSON allows a key twice and `json.loads` keeps the last one silently, so a record whose
    `provider` appears twice reaches the constraint check naming only the last one."""
    duplicated = ('{"schema": "%s", "task": "t", "strategy": "s", "assignments": '
                  '[{"role": "developer", "provider": "unapproved/model", '
                  '"provider": "acme/model-a", "confidence": "measured", '
                  '"evidence_count": 4, "reasons": ["r"]}]}' % SCHEMA)
    if which == "record":
        result = _run(tmp_path, None, record_text=duplicated)
    else:
        result = _run(tmp_path, _record(),
                      constraints_text='{"identity": {}, "approved": ["a"], "approved": ["b"]}')
    assert result.returncode == 2, (result.returncode, result.stdout)
    assert "twice" in json.loads(result.stdout)["error"], result.stdout


def test_a_record_that_cannot_be_read_is_its_own_status(tmp_path):
    constraints = tmp_path / "constraints.json"
    constraints.write_text('{"identity": {}}', encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(WORKBENCH), "route-team", str(tmp_path / "absent.json"),
         "--constraints", str(constraints)],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=60)
    assert result.returncode == 2
    assert "execution-error" in result.stdout


def test_independence_can_be_widened_by_policy_but_defaults_to_the_assurance_roles(tmp_path):
    """A policy that wants the planner independent too can say so; one that says nothing gets
    the roles whose whole value is being other than what they judge."""
    record = _record(_assign(DEVELOPER, provider="same/model"),
                     _assign(PLANNER, provider="same/model"))
    assert _run(tmp_path, record).returncode == 0
    result = _run(tmp_path, record, constraints={"also_independent": [PLANNER]})
    assert result.returncode == 1, (result.returncode, result.stdout)
    assert team_routing.NOT_INDEPENDENT in result.stdout


# ── gaps the mutation sweep found ────────────────────────────────────────────
def test_every_role_whose_value_is_being_other_than_what_it_judges_is_an_assurance_role():
    """Narrowing this set is how an unmeasured provider gets into a verifier seat without any
    check firing, and a test that only exercises `judge` would not see it."""
    assert ASSURANCE_ROLES == {SECURITY_VERIFIER, ARCHITECTURE_VERIFIER, JUDGE}
    assert PLANNER not in ASSURANCE_ROLES and DEVELOPER not in ASSURANCE_ROLES


def test_a_record_that_does_not_say_what_it_is_is_refused():
    """The schema id is what tells a reader which vocabulary the roles belong to. Without it,
    `judge` could be this document's role or any other document's."""
    for schema in ("rig.something-else/v1", None, ""):
        assert any("schema" in p for p in validate(_record(schema=schema))), repr(schema)


@pytest.mark.parametrize("provider", ["", "   ", None, 42, ["acme/model-a"]])
def test_an_assignment_with_no_provider_is_refused(provider):
    """A role assigned to nothing is an unfilled role that looks filled — and the independence
    check compares providers, so a blank one would match another blank one."""
    problems = validate(_record(_assign(DEVELOPER, provider=provider)))
    assert any("has to name something" in p for p in problems), (provider, problems)


@pytest.mark.parametrize("reasons", ["a string", [42], ["", "   "], ["ok", 3], None])
def test_reasons_must_be_prose_a_human_can_read(reasons):
    """A `reasons` that is a bare string iterates as characters, and one holding numbers says
    nothing. Either would satisfy "is not empty" while justifying nothing."""
    problems = validate(_record(_assign(DEVELOPER, reasons=reasons)))
    assert any("reasons" in p for p in problems), (reasons, problems)


# ── what round 1 found: four ways to fail open ───────────────────────────────
@pytest.mark.parametrize("also", [frozenset(), frozenset({PLANNER})])
def test_a_policy_cannot_shrink_the_independence_floor(also):
    """`independent` used to replace the assurance roles rather than extend them, so a
    constraints file saying `[]` turned the developer into the judge. There is no longer a
    field that can be set to a smaller value than the floor."""
    constraints = Constraints(also_independent=also,
                              measured=_measured("same/model"))
    assert ASSURANCE_ROLES <= constraints.independent
    found = violations(load(_record(_assign(DEVELOPER, provider="same/model"),
                                    _assign(JUDGE, provider="same/model"))), constraints)
    assert [v["reason"] for v in found] == [team_routing.NOT_INDEPENDENT], also


def test_an_empty_allowlist_names_nobody_and_a_missing_one_states_nothing():
    """Opposite meanings. Reading an empty set as "everything is allowed" is fail-open in the
    exact case a caller most needs it closed: a policy that resolved to nothing."""
    record = load(_record(_assign(DEVELOPER, provider="anyone")))
    assert violations(record, Constraints(approved=None)) == []
    assert [v["reason"] for v in violations(record, Constraints(approved=frozenset()))] == [
        team_routing.NOT_APPROVED]


@pytest.mark.parametrize("provider", [" acme/m", "acme/m ", "\tacme/m"])
def test_a_provider_spelled_with_whitespace_is_refused(provider):
    """Independence compares providers by string, so `"acme/m"` and `" acme/m "` would be two
    providers here and one everywhere that trims."""
    problems = validate(_record(_assign(DEVELOPER, provider=provider)))
    assert any("exactly" in p for p in problems), (provider, problems)


# ── the constraints document is checked like everything else ─────────────────
def test_a_constraints_object_registers_its_keys_unless_it_is_checked():
    """`frozenset(...)` takes whatever iterates, and this is the document that says who is
    *allowed*. `{"evil/model": false}` would approve the provider it looks like it denies."""
    with pytest.raises(ValueError, match="must be an array"):
        team_routing.load_constraints({"identity": {}} | {"approved": {"evil/model": False}})
    with pytest.raises(ValueError, match="must be an array"):
        team_routing.load_constraints({"identity": {}} | {"approved": "acme/model-a"})


@pytest.mark.parametrize("payload,fragment", [
    ({"approved": [""]}, "not a provider name"),
    ({"approved": [" acme/m "]}, "not a provider name"),
    ({"approved": [42]}, "not a provider name"),
    ({"capable": {JUDGE: [42]}}, "not a provider name"),
    ({"capable": []}, "map a role to its providers"),
    ({"capable": {"securty-verifier": ["a"]}}, "'capable' names role"),
    ({"capable": {JUDGE: {"a": True}}}, "must be an array"),
    ({"also_independent": ["judgement"]}, "do not exist"),
    ({"required": {"judge": True}}, "must be an array"),
    ({"budget": 100}, "not part of a constraint set"),
    ([], "expected an object"),
])
def test_a_malformed_constraints_document_is_refused(payload, fragment):
    with pytest.raises(ValueError, match=fragment):
        team_routing.load_constraints(({"identity": {}} | payload)
                                      if isinstance(payload, dict) else payload)


@pytest.mark.parametrize("payload", [[], "a string", 42, None])
def test_a_routing_record_that_is_not_an_object_is_refused(payload):
    """The first thing `validate` reads. Without it every later `payload.get` is an
    AttributeError where the contract says a list of problems."""
    problems = validate(payload)
    assert any("expected an object" in p for p in problems), (payload, problems)


def test_a_well_formed_constraints_document_says_what_it_says():
    constraints = team_routing.load_constraints(
        {"identity": {"a": "backend-1", "b": "backend-2"},
         "approved": ["a", "b"], "capable": {JUDGE: ["b"]},
         "also_independent": [PLANNER], "required": [JUDGE]})
    assert constraints.approved == {"a", "b"}
    assert constraints.canonical("a") == "backend-1"
    assert constraints.canonical("never-heard-of-it") is None
    assert constraints.capable == {JUDGE: frozenset({"b"})}
    assert constraints.independent == ASSURANCE_ROLES | {PLANNER}
    assert constraints.canonical("a") == "backend-1"
    assert constraints.canonical("never-heard-of-it") is None
    assert constraints.required == {JUDGE}


def test_an_absent_allowlist_is_none_and_an_empty_one_is_empty():
    assert team_routing.load_constraints({"identity": {}}).approved is None
    assert team_routing.load_constraints({"identity": {}, "approved": []}).approved == frozenset()


def test_the_command_refuses_a_constraints_object_that_registers_its_keys(tmp_path):
    result = _run(tmp_path, _record(_assign(DEVELOPER, provider="evil/model")),
                  constraints={"approved": {"evil/model": False}})
    assert result.returncode == 2, (result.returncode, result.stdout)
    assert "must be an array" in json.loads(result.stdout)["error"]


# ── what round 2 found: three more ways to fail open ─────────────────────────
def test_the_command_will_not_run_without_a_policy(tmp_path):
    """A caller who forgot the file, or whose policy failed to resolve, would otherwise get an
    admission that means "nothing was enforced" — the same shape as every other fail-open here.
    """
    result = _run(tmp_path, _record(), no_constraints=True)
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert "--constraints" in result.stderr, result.stderr


def test_two_names_for_one_backend_are_one_backend():
    """Comparing the strings a router wrote would let `vendor/model-x` review what
    `vendor-alias/model-x` implemented, which is the same model grading its own work under a
    second name."""
    constraints = Constraints(identity={"vendor/model-x": "backend-7",
                                        "vendor-alias/model-x": "backend-7"},
                              measured=_measured("vendor-alias/model-x"))
    found = violations(load(_record(_assign(DEVELOPER, provider="vendor/model-x"),
                                    _assign(JUDGE, provider="vendor-alias/model-x"))),
                       constraints)
    assert [v["reason"] for v in found] == [team_routing.NOT_INDEPENDENT]
    assert "backend-7" in found[0]["detail"]


def test_two_backends_behind_one_policy_are_two_backends():
    constraints = Constraints(identity={"a/model": "backend-1", "b/model": "backend-2"},
                              measured=_measured("b/model"))
    assert violations(load(_record(_assign(DEVELOPER, provider="a/model"),
                                   _assign(JUDGE, provider="b/model"))), constraints) == []


@pytest.mark.parametrize("unknown_side", [DEVELOPER, JUDGE])
def test_a_provider_the_policy_cannot_resolve_refuses_the_independence_check(unknown_side):
    """Independence cannot be checked on a name nothing resolves, and answering "independent"
    there would be the check reporting a result it did not compute."""
    constraints = Constraints(
        identity={"known/model": "backend-1"},
        measured={role: frozenset({"known/model", "mystery/model"})
                  for role in ASSURANCE_ROLES})
    assignments = {DEVELOPER: "known/model", JUDGE: "known/model"}
    assignments[unknown_side] = "mystery/model"
    found = violations(load(_record(_assign(DEVELOPER, provider=assignments[DEVELOPER]),
                                    _assign(JUDGE, provider=assignments[JUDGE]))),
                       constraints)
    assert [v["reason"] for v in found] == [team_routing.IDENTITY_UNKNOWN], unknown_side


def test_an_empty_identity_map_says_the_names_are_already_canonical():
    """It has to be said rather than defaulted: silence would mean independence was compared on
    whatever the router felt like calling things."""
    assert Constraints(identity={}).canonical("anything") == "anything"
    with pytest.raises(ValueError, match="'identity' must map"):
        team_routing.load_constraints({"approved": ["a"]})


@pytest.mark.parametrize("payload,fragment", [
    ({"identity": []}, "'identity' must map"),
    ({"identity": {"a": ""}}, "have to name something"),
    ({"identity": {"a": " backend "}}, "have to name something"),
    ({"identity": {"a": 42}}, "have to name something"),
    ({"identity": {" a ": "backend"}}, "have to name something"),
])
def test_a_malformed_identity_map_is_refused(payload, fragment):
    with pytest.raises(ValueError, match=fragment):
        team_routing.load_constraints(payload)


@pytest.mark.parametrize("capable", [{JUDGE: None}, {JUDGE: "a"}, {JUDGE: {"a": True}},
                                    {JUDGE: 42}])
def test_a_capability_rule_that_cannot_be_read_is_not_no_rule(capable):
    """`violations` treats an absent capability entry as "unconstrained", so an unreadable one
    collapsing into the same thing is malformed policy becoming non-enforcement. A string
    iterates as characters and a dict as its keys, so neither is a set of providers."""
    with pytest.raises(ValueError, match="not a set of providers"):
        Constraints(capable=capable)


def test_a_capability_rule_written_as_a_list_is_a_capability_rule():
    """A programmatic list is unambiguous — unlike a dict or a string, there is nothing else it
    could have meant — so it is normalised rather than refused."""
    assert Constraints(capable={JUDGE: ["a", "b"]}).capable[JUDGE] == frozenset({"a", "b"})


@pytest.mark.parametrize("identity", [{"": "backend"}, {"  ": "backend"}, {42: "backend"},
                                      {"a": ""}, {"a": "   "}, {"a": None},
                                      {" a ": "backend"}, {"a": " backend"},
                                      {"a": "backend "}])
def test_a_programmatic_identity_map_is_checked_too(identity):
    """`load_constraints` guards the JSON path. A caller building `Constraints` directly gets
    the same rule, or the direct path is the weaker one — which is where the last four
    fail-opens lived."""
    with pytest.raises(ValueError, match="have to name something"):
        Constraints(identity=identity)


def test_a_backend_spelled_two_ways_cannot_make_one_model_independent_of_itself():
    """The laundering the provider names were already protected from, one layer in. Two
    canonical strings that differ only by whitespace are two backends to the comparison and
    one to anything downstream that trims."""
    with pytest.raises(ValueError, match="have to name something"):
        Constraints(identity={"dev/model": " backend-7", "judge/model": "backend-7"})


# ── what round 3 found: the programmatic path was the weaker one ─────────────
@pytest.mark.parametrize("approved", [{"evil/model": False}, "evil/model", ["evil/model"],
                                      frozenset({""}), frozenset({" acme/m "}),
                                      frozenset({42})])
def test_a_programmatic_allowlist_is_checked_like_the_document(approved):
    """`load_constraints` guards the JSON path and `Constraints` was the way around it —
    `{"evil/model": False}` iterates as its keys and approves what it looks like it denies."""
    with pytest.raises(ValueError, match="approved"):
        Constraints(approved=approved)


@pytest.mark.parametrize("field", ["also_independent", "required"])
@pytest.mark.parametrize("value", ["", "judge", ["judge"], {"judge": True}])
def test_programmatic_role_sets_are_checked_like_the_document(field, value):
    """A bare string iterates as characters and a dict as its keys, so either would become a
    role set nobody wrote. `required=""` used to be exactly "nothing is required"."""
    with pytest.raises(ValueError, match="not a set of roles"):
        Constraints(**{field: value})


def test_required_roles_live_inside_the_validated_object():
    """Passed alongside, they were a second way in that nothing checked. There is one object
    now, so there is one place that can be wrong."""
    assert "required" in {f.name for f in dataclasses.fields(Constraints)}
    found = violations(load(_record(_assign(DEVELOPER))),
                       Constraints(required=frozenset({JUDGE})))
    assert [v["reason"] for v in found] == [team_routing.ROLE_UNFILLED]


@pytest.mark.parametrize("task", [None, "", "   ", 42, ["a-task"]])
def test_a_record_that_does_not_say_which_task_it_routed_is_refused(task):
    """Two routings for different tasks are indistinguishable without it, and a reader
    attributing a team to the wrong change would be reading the wrong evidence."""
    problems = validate(_record(task=task))
    assert any("which task" in p for p in problems), (task, problems)


@pytest.mark.parametrize("field", ["also_independent", "required"])
def test_a_role_set_naming_a_role_that_does_not_exist_is_refused(field):
    """A constraint on a role nothing can be assigned to constrains nothing, and a caller who
    misspelled one would otherwise believe it was in force."""
    with pytest.raises(ValueError, match="do not exist"):
        Constraints(**{field: frozenset({"securty-verifier"})})


# ── what round 5 found ───────────────────────────────────────────────────────
def test_a_backend_that_is_itself_another_name_is_refused():
    """One hop is not resolution. With `dev-alias → model`, `judge-alias → backend` and
    `model → backend`, comparing one hop makes `model` and `backend` look like different
    backends while the policy itself says they are the same one — and a router that can read
    the policy can pick exactly that pair."""
    with pytest.raises(ValueError, match="not another name"):
        Constraints(identity={"dev-alias": "model", "judge-alias": "backend",
                              "model": "backend"})


def test_a_backend_may_name_itself():
    """A policy that lists every provider, canonical ones included, is the normal way to write
    one — and `model → model` is terminal."""
    constraints = Constraints(identity={"alias": "model", "model": "model"})
    assert constraints.canonical("alias") == constraints.canonical("model") == "model"


def test_the_command_refuses_a_chained_identity(tmp_path):
    result = _run(tmp_path, _record(_assign(DEVELOPER, provider="dev-alias"),
                                    _assign(JUDGE, provider="judge-alias")),
                  constraints={"identity": {"dev-alias": "model", "judge-alias": "backend",
                                            "model": "backend"}})
    assert result.returncode == 2, (result.returncode, result.stdout)
    assert "not another name" in json.loads(result.stdout)["error"]


def test_policy_a_caller_still_holds_cannot_be_weakened_after_it_was_checked():
    """`frozen=True` stops the fields being replaced, not the sets and dicts behind them from
    being emptied. What was validated has to be what gets compared."""
    approved = {"acme/model-a"}
    capable = {JUDGE: {"acme/model-a"}}
    identity = {"acme/model-a": "backend-1"}
    required = {JUDGE}
    constraints = Constraints(approved=approved, capable=capable, identity=identity,
                              required=required)
    approved.add("evil/model")
    capable[JUDGE] = {"evil/model"}
    identity.clear()
    required.clear()

    record = load(_record(_assign(DEVELOPER, provider="evil/model")))
    reasons = {v["reason"] for v in violations(record, constraints)}
    assert team_routing.NOT_APPROVED in reasons
    assert team_routing.ROLE_UNFILLED in reasons
    assert constraints.canonical("acme/model-a") == "backend-1"
    assert constraints.capable[JUDGE] == frozenset({"acme/model-a"})


# ── what round 6 found: the last of the two-paths class ──────────────────────
def test_two_developers_cannot_launder_a_judge_into_independence():
    """A dict comprehension keeps the last of two developers, so the judge would be compared
    against a developer it is not — while the first one, still in the loop, wrote the change.
    `validate` refuses this on the JSON path, and a caller assembling `Assignment`s reaches
    `violations` directly."""
    same = Assignment(role=DEVELOPER, provider="same/model", confidence=MEASURED,
                      evidence_count=9, reasons=("r",))
    other = Assignment(role=DEVELOPER, provider="other/model", confidence=MEASURED,
                       evidence_count=9, reasons=("r",))
    judge = Assignment(role=JUDGE, provider="same/model", confidence=MEASURED,
                       evidence_count=9, reasons=("r",))
    found = violations((same, judge, other), Constraints())
    assert [v["reason"] for v in found] == [team_routing.ROLE_TWICE]


def test_a_broken_set_of_assignments_gets_no_other_verdict():
    """Every check after this one reads `by_role`, and `by_role` cannot represent two providers
    for one role. A verdict computed from it would be about whichever of the two the dict
    happened to keep, offered next to the structural problem as though it were as reliable."""
    judges = (Assignment(role=JUDGE, provider="a/model", confidence=MEASURED,
                         evidence_count=9, reasons=("r",)),
              Assignment(role=JUDGE, provider="b/model", confidence=UNMEASURED,
                         evidence_count=0, reasons=("r",)))
    assert [v["reason"] for v in violations(judges, Constraints())] == [team_routing.ROLE_TWICE]


def test_a_role_outside_the_vocabulary_cannot_be_assigned_at_all():
    """Nothing would constrain it — not an assurance role, so the measurement rule skips it,
    and no capability rule names it — so it cannot be built rather than being caught later."""
    with pytest.raises(ValueError, match="is not one of"):
        Assignment(role="vibes-checker", provider="a", confidence=MEASURED,
                   evidence_count=1, reasons=("r",))


def test_a_well_formed_set_of_assignments_still_reports_every_other_violation():
    """The early return is for the case where `by_role` cannot be built. It must not swallow
    the checks that follow."""
    found = violations(load(_record(_assign(DEVELOPER, provider="same/model"),
                                    _assign(JUDGE, provider="same/model"))),
                       Constraints(approved=frozenset(),
                                   measured=_measured("same/model")))
    assert {v["reason"] for v in found} == {team_routing.NOT_APPROVED,
                                            team_routing.NOT_INDEPENDENT}


# ── what round 7 found, and the instrument change it prompted ────────────────
@pytest.mark.parametrize("kwargs,fragment", [
    ({"provider": " same/model "}, "exactly"),
    ({"provider": ""}, "exactly"),
    ({"provider": 42}, "exactly"),
    ({"confidence": "ok"}, "confidence"),
    ({"confidence": MEASURED, "evidence_count": 0}, "no observations"),
    ({"confidence": SHADOW, "evidence_count": 0}, "no observations"),
    ({"evidence_count": -1}, "evidence_count"),
    ({"evidence_count": True}, "evidence_count"),
    ({"evidence_count": "40"}, "evidence_count"),
    ({"reasons": ()}, "gives no reason"),
    ({"reasons": "a string"}, "non-empty strings"),
    ({"reasons": (42,)}, "non-empty strings"),
])
def test_an_assignment_cannot_be_built_in_a_state_the_document_would_be_refused_in(kwargs,
                                                                                   fragment):
    """Four review rounds found the same defect in four places, each time because the JSON path
    was checked and the object was not. The rule lives in one function now, and the object
    cannot exist in a state `validate` would reject — so there is no second path to forget."""
    fields = {"role": JUDGE, "provider": "acme/model-a", "confidence": MEASURED,
              "evidence_count": 9, "reasons": ("r",)} | kwargs
    with pytest.raises(ValueError, match=fragment):
        Assignment(**fields)


def test_both_paths_answer_with_the_same_rule():
    """Stated as an equivalence rather than two lists that happen to agree today."""
    bad = {"role": JUDGE, "provider": " x ", "confidence": MEASURED, "evidence_count": 0,
           "reasons": ()}
    from_document = [p for p in validate(_record(bad)) if p.startswith("assignments[0]")]
    direct = team_routing.assignment_problems(bad["role"], bad["provider"], bad["confidence"],
                                              bad["evidence_count"], bad["reasons"],
                                              "assignments[0]")
    assert from_document == direct != []


@pytest.mark.parametrize("field", ["capable", "identity"])
@pytest.mark.parametrize("value", [[], [("a", "b")], "ab", None, 42])
def test_a_policy_mapping_that_is_not_a_mapping_is_refused(field, value):
    """`dict(...)` turns a list of pairs into a policy nobody wrote and `[]` into "no policy at
    all" — the coercion this module refuses everywhere else, on the path that skips the
    document schema."""
    with pytest.raises(ValueError, match="not a mapping"):
        Constraints(**{field: value})


# ── what round 8 found: the record asserted its own eligibility ──────────────
@pytest.mark.parametrize("role", sorted(ASSURANCE_ROLES))
def test_the_router_does_not_get_to_say_it_is_measured(role):
    """`confidence="measured", evidence_count=1` used to unlock an assurance seat. A record
    that can assert the fact unlocking its own eligibility is stating its own constraint —
    the pattern this module rejects everywhere else."""
    found = violations(load(_record(_assign(role, provider="new/model", confidence=MEASURED,
                                            evidence_count=1))),
                       Constraints(measured=_measured("someone-else")))
    assert [v["reason"] for v in found] == [team_routing.NOT_MEASURED], role
    assert "the router's account" in found[0]["detail"]


@pytest.mark.parametrize("role", sorted(ASSURANCE_ROLES))
def test_a_role_nobody_is_listed_as_measured_for_admits_nobody(role):
    """For the reason an empty allowlist names nobody: absence of a measurement is not a
    measurement that passed."""
    found = violations(load(_record(_assign(role, provider="acme/model-a"))), Constraints())
    assert [v["reason"] for v in found] == [team_routing.NOT_MEASURED], role


@pytest.mark.parametrize("confidence", [UNMEASURED, SHADOW])
def test_the_policy_decides_even_when_the_record_admits_it_is_unmeasured(confidence):
    """The record's word is reported, not believed, in both directions. A provider the policy
    has measured is eligible whatever the router wrote about it."""
    assert violations(load(_record(_assign(JUDGE, provider="acme/model-a",
                                           confidence=confidence, evidence_count=0
                                           if confidence == UNMEASURED else 3))),
                      Constraints(measured=_measured())) == []


def test_a_measured_policy_naming_a_role_that_does_not_exist_is_refused():
    with pytest.raises(ValueError, match="do not exist"):
        Constraints(measured={"securty-verifier": frozenset({"a"})})


@pytest.mark.parametrize("measured", [{JUDGE: None}, {JUDGE: "a"}, {JUDGE: {"a": True}}])
def test_a_measurement_rule_that_cannot_be_read_is_not_no_rule(measured):
    with pytest.raises(ValueError, match="not a set of providers"):
        Constraints(measured=measured)


def test_the_command_reads_the_measured_policy(tmp_path):
    admissible = _run(tmp_path, _record(_assign(JUDGE, provider="acme/model-a")),
                      constraints={"measured": {JUDGE: ["acme/model-a"]}})
    assert admissible.returncode == 0, (admissible.stdout, admissible.stderr)
    refused = _run(tmp_path, _record(_assign(JUDGE, provider="acme/model-a")),
                   constraints={"measured": {JUDGE: ["someone-else"]}})
    assert refused.returncode == 1, (refused.returncode, refused.stdout)
    assert team_routing.NOT_MEASURED in refused.stdout


def test_an_assignment_serialises_to_exactly_what_the_schema_defines():
    """`as_dict` is what a caller writes back into a document, so it has to round-trip through
    JSON and back to the same assignment."""
    assignment = Assignment(role=JUDGE, provider="acme/model-a", confidence=MEASURED,
                            evidence_count=9, reasons=("beat the alternatives",))
    emitted = assignment.as_dict()
    assert set(emitted) == team_routing.ASSIGNMENT_FIELDS
    assert emitted["reasons"] == ["beat the alternatives"], "a tuple is not JSON"
    assert json.loads(json.dumps(emitted)) == emitted
    assert load(_record(emitted))[0] == assignment


# ── what round 9 found: the record picked its own constraints ────────────────
def test_constraints_chosen_for_another_task_say_nothing_about_this_one():
    """A router free to label an authentication change as a wording change picks which
    constraints apply to it — the decision this boundary exists to keep out of its hands."""
    result = check(_record(_assign(DEVELOPER), task="wording-change"),
                   Constraints(task="auth-boundary-change"))
    assert result["status"] == team_routing.REFUSED
    assert result["violations"][0]["reason"] == team_routing.NOT_THIS_TASK


def test_a_matching_task_is_not_a_violation():
    assert check(_record(_assign(DEVELOPER), task="t"),
                 Constraints(task="t"))["status"] == team_routing.ADMISSIBLE


def test_constraints_that_name_no_task_do_not_pretend_to_have_checked_one():
    """A caller that cannot state which task the constraints were chosen for gets everything
    else checked. Refusing here would refuse every policy that is not per-task; claiming the
    binding held would be worse."""
    assert check(_record(_assign(DEVELOPER)), Constraints())["status"] == \
        team_routing.ADMISSIBLE


@pytest.mark.parametrize("task", ["", "   ", 42, []])
def test_a_constraints_task_that_names_nothing_is_refused(task):
    with pytest.raises(ValueError, match="either name the task"):
        Constraints(task=task)


def test_the_task_mismatch_is_reported_before_everything_else(tmp_path):
    """It is the reason the rest of the answer is about the wrong question, so a reader should
    meet it first rather than after a list of constraint verdicts that do not apply."""
    result = _run(tmp_path, _record(_assign(DEVELOPER, provider="cheap/model"),
                                    task="wording-change"),
                  constraints={"task": "auth-boundary-change", "approved": ["acme/model-a"]},
                  json_out=True)
    assert result.returncode == 1, (result.returncode, result.stdout, result.stderr)
    reasons = [v["reason"] for v in json.loads(result.stdout)["violations"]]
    assert reasons[0] == team_routing.NOT_THIS_TASK
    assert team_routing.NOT_APPROVED in reasons
