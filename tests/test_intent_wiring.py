"""#476 — what an intent contract is allowed to decide elsewhere, and what it is not.

Grouped by the three paths: a contract's declared requirements can put steps on a workflow's
floor, can ask for a gate that passed, and can be read back beside what the gate ruled on.
Each group holds the refusals more than the grants.
"""

import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench import intent, intent_wiring
from rig_workbench.workbench.intent_wiring import (floor_from, projection, target_from,
                                                   unaskable, unmatched)
from rig_workbench.workbench.synthesis import OPERATOR_REQUESTED, POLICY_REQUIRED

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"
CATALOG = frozenset({"review-diff", "security-audit", "implement"})
CRITERIA = frozenset({"tests_pass_or_explained", "no_secret_leak"})


def _requirement(text="reviewed by somebody else", origin=intent.EXPLICIT_USER,
                 source="issue #1", evidence=("review-diff",)):
    return {"text": text, "origin": origin, "source": source, "evidence": list(evidence)}


def _contract(*requirements, goal="ship the thing", **extra):
    return {"schema": intent.SCHEMA, "goal": goal,
            "requirements": list(requirements) or [_requirement()], **extra}


def _loaded(*requirements, **extra):
    return intent.load(_contract(*requirements, **extra))


# ── a declared requirement can put a step on the floor ───────────────────────
def test_a_users_requirement_naming_a_step_puts_it_on_the_floor_as_theirs():
    """A person can withdraw what they asked for and a policy requirement is not theirs to
    withdraw, so which of the two it was has to survive the trip."""
    [item] = floor_from(_loaded(_requirement(evidence=("review-diff",))), CATALOG)
    assert (item.id, item.source) == ("review-diff", OPERATOR_REQUESTED)
    assert item.reason == "reviewed by somebody else"


def test_a_policy_requirement_stays_a_policy_requirement():
    [item] = floor_from(_loaded(_requirement(origin=intent.POLICY_REQUIRED,
                                             source="org policy",
                                             evidence=("security-audit",))), CATALOG)
    assert (item.id, item.source) == ("security-audit", POLICY_REQUIRED)


@pytest.mark.parametrize("origin", [intent.INFERRED, intent.PROPOSED,
                                    intent.REPOSITORY_DERIVED])
def test_a_conclusion_cannot_create_a_requirement(origin):
    """`synthesise` refuses a floor built from the proposal it is checking; the same rule
    reaches back one step. Recording what rig inferred is the point of having the field — it
    is not the point of the floor."""
    assert floor_from(_loaded(_requirement(origin=origin, source="",
                                           evidence=("review-diff",))), CATALOG) == ()


def test_evidence_naming_something_that_is_not_a_component_grants_nothing():
    """A requirement may name a test id or a query, and neither is a step."""
    assert floor_from(_loaded(_requirement(evidence=("test_login", "a query"))), CATALOG) == ()


def test_a_requirement_naming_several_steps_puts_all_of_them_on_the_floor():
    floor = floor_from(_loaded(_requirement(evidence=("review-diff", "security-audit"))),
                       CATALOG)
    assert sorted(item.id for item in floor) == ["review-diff", "security-audit"]


def test_two_declarations_disagreeing_about_who_requires_a_step_is_refused():
    """`check_floor` refuses that rather than picking by order, and building it here would
    only move the same collision earlier."""
    with pytest.raises(ValueError, match="One authority per step"):
        floor_from(_loaded(_requirement(evidence=("review-diff",)),
                           _requirement(text="policy wants it too",
                                        origin=intent.POLICY_REQUIRED, source="org",
                                        evidence=("review-diff",))), CATALOG)


def test_two_declarations_agreeing_about_a_step_are_one_floor_entry():
    floor = floor_from(_loaded(_requirement(evidence=("review-diff",)),
                               _requirement(text="also this", evidence=("review-diff",))),
                       CATALOG)
    assert [item.id for item in floor] == ["review-diff"]


def test_the_floor_it_builds_is_one_synthesise_would_accept():
    """Built through `check_floor` rather than beside it: a floor this module assembled and
    `synthesise` then refused would be a second set of rules about the same object."""
    from rig_workbench.workbench import synthesis

    floor = floor_from(_loaded(), CATALOG)
    assert synthesis.check_floor(floor, CATALOG) == floor


def test_a_catalog_without_the_named_step_yields_an_empty_floor():
    """Not a refusal: evidence naming something outside the catalog is a test id as far as
    anything here can tell, and a contract written before a component existed is a contract,
    not an error. `unmatched` is where a caller looks for the case that was meant to be a
    step."""
    assert floor_from(_loaded(_requirement(evidence=("review-diff",))),
                      frozenset({"implement"})) == ()


# ── a misspelled step looks exactly like a test id ───────────────────────────
def test_a_name_that_differs_only_in_case_is_reported_as_a_candidate():
    """Nothing here can tell a typo from a test id, so this reports and refuses nothing."""
    [item] = unmatched(_loaded(_requirement(evidence=("Review-Diff",))), CATALOG)
    assert (item["evidence"], item["did_you_mean"]) == ("Review-Diff", "review-diff")


def test_a_name_that_is_nothing_like_a_component_is_not_a_candidate():
    assert unmatched(_loaded(_requirement(evidence=("test_login",))), CATALOG) == ()


def test_a_name_that_matched_is_not_also_a_candidate():
    assert unmatched(_loaded(_requirement(evidence=("review-diff",))), CATALOG) == ()


# ── a contract does not get to name what it cannot see ───────────────────────
def test_a_declared_requirement_resting_on_a_gate_criterion_asks_for_a_gate_that_passed():
    target = target_from(_loaded(_requirement(evidence=("tests_pass_or_explained",))), CRITERIA)
    assert target["axes"] == {"gate": "passed"}
    assert intent_wiring.resting_on(_loaded(_requirement(
        evidence=("tests_pass_or_explained",))), CRITERIA) == ("tests_pass_or_explained",)


def test_a_contract_resting_on_nothing_the_gate_rules_asks_for_nothing():
    """Silence rather than a default. A target with a `gate` nobody's requirement rested on
    would be this module writing the author's assurance target for them."""
    assert target_from(_loaded(_requirement(evidence=("test_login",))), CRITERIA) is None


@pytest.mark.parametrize("origin", [intent.INFERRED, intent.PROPOSED,
                                    intent.REPOSITORY_DERIVED])
def test_a_conclusion_does_not_ask_for_a_gate_either(origin):
    assert target_from(_loaded(_requirement(origin=origin, source="",
                                            evidence=("no_secret_leak",))), CRITERIA) is None


def test_it_never_fills_in_an_axis_no_requirement_could_speak_to():
    """Reading "production quality" out of a goal and filling in four axes is what
    `assurance_target.VAGUE` refuses, and generating it here would route around that refusal
    by writing the words for the author."""
    target = target_from(_loaded(_requirement(evidence=("tests_pass_or_explained",)),
                                 goal="production quality, obviously"), CRITERIA)
    assert set(target["axes"]) == {"gate"}, "the goal's words reached nothing"


def test_the_axes_it_never_speaks_to_are_named_rather_than_left_implied():
    """A reader of a two-key target may reasonably wonder whether the others were considered
    and dropped, or never in scope."""
    from rig_workbench.workbench.assurance_target import AXES

    assert set(unaskable(_loaded())) == set(AXES) - {"gate"}


def test_the_target_it_builds_is_one_assurance_target_would_accept():
    from rig_workbench.workbench import assurance_target

    target = target_from(_loaded(_requirement(evidence=("tests_pass_or_explained",))), CRITERIA)
    assert assurance_target.validate(target) == []


def test_a_contract_asking_for_nothing_produces_no_document_rather_than_an_empty_one():
    """`assurance_target.validate` refuses an empty target: one that requires nothing is met
    by everything, which is a way of saying the run was unconstrained while looking like it
    was constrained."""
    from rig_workbench.workbench import assurance_target

    assert target_from(_loaded(_requirement(evidence=("test_login",))), CRITERIA) is None
    assert assurance_target.validate({"schema": assurance_target.SCHEMA, "axes": {}}) != []


# ── the projection copies ────────────────────────────────────────────────────
def _gates(*criteria):
    return {"observed": True, "criteria": [dict(c) for c in criteria]}


def test_it_reports_what_the_gate_ruled_on_and_stops():
    """Whether the criterion passing *satisfies* the requirement is `intent.status`'s question
    and a human's after that; answering it here would put a second verdict on a page whose
    value is that it holds none."""
    result = projection(_contract(_requirement(evidence=("tests_pass_or_explained",))),
                        _gates({"name": "tests_pass_or_explained", "status": "passed"}))
    [requirement] = result["requirements"]
    assert requirement["checked_by"] == [{"criterion": "tests_pass_or_explained",
                                          "status": "passed", "overridden": False,
                                          "ambiguous": False}]
    assert "satisfied" not in json.dumps(result)


def test_an_overridden_criterion_says_so():
    """The single most important thing on the receipt's gate page stays visible here."""
    result = projection(_contract(_requirement(evidence=("no_secret_leak",))),
                        _gates({"name": "no_secret_leak", "status": "passed",
                                "overridden": True}))
    assert result["requirements"][0]["checked_by"][0]["overridden"] is True


def test_a_requirement_nothing_checked_is_reported_with_an_empty_list():
    """"Nothing checks this" is the fact `intent.unverifiable` exists to surface, and a
    projection that dropped those rows would make a contract look better than it is."""
    result = projection(_contract(_requirement(text="nobody wired this", evidence=())),
                        _gates())
    assert result["requirements"][0]["checked_by"] == []
    assert result["unverifiable"] == ["nobody wired this"]


def test_evidence_the_gate_did_not_rule_on_is_still_shown():
    """So a reader can see that a requirement rested on a test nobody wired to this gate
    rather than on nothing."""
    result = projection(_contract(_requirement(evidence=("test_login",))), _gates())
    assert result["requirements"][0]["evidence"] == ["test_login"]
    assert result["requirements"][0]["checked_by"] == []


def test_a_task_with_no_contract_says_so_rather_than_looking_like_one_with_no_goal():
    result = projection(None, _gates())
    assert result["observed"] is False
    assert "no intent.json" in result["reason"]


def test_a_contract_nobody_can_read_is_not_a_contract_with_nothing_in_it():
    """A reader has to be able to tell "no contract" from "a contract nobody can read"."""
    result = projection({"schema": "wrong"}, _gates())
    assert result["observed"] is False
    assert "is not a contract" in result["reason"]


def test_it_carries_what_the_contract_left_undeclared_and_unresolved():
    result = projection(_contract(_requirement(origin=intent.INFERRED, source="",
                                               text="rig guessed this"),
                                  goal="ship it",
                                  ambiguities=[{"question": "which users?",
                                                "resolved_by": "asking"}]),
                        _gates())
    assert result["undeclared"] == ["rig guessed this"]
    assert result["ambiguities"] == [{"question": "which users?", "resolved_by": "asking"}]


# ── the command exits with the answer ────────────────────────────────────────
def _run(tmp_path, contract, against, *flags, contract_text=None):
    path = tmp_path / "contract.json"
    path.write_text(contract_text if contract_text is not None else json.dumps(contract),
                    encoding="utf-8")
    catalog = tmp_path / "against.json"
    catalog.write_text(json.dumps(sorted(against)), encoding="utf-8")
    return subprocess.run([sys.executable, str(WORKBENCH), "intent-derive", str(path),
                           "--against", str(catalog), *flags],
                          capture_output=True, text=True, cwd=REPO_ROOT, timeout=60)


def test_deriving_a_floor_exits_zero(tmp_path):
    result = _run(tmp_path, _contract(), CATALOG, "--floor")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "review-diff" in result.stdout and OPERATOR_REQUESTED in result.stdout


def test_the_floor_json_is_what_synthesise_takes(tmp_path):
    """The `--required` file `synthesise` reads is `{id: {"source": …, "reason": …}}`, so a
    caller can pipe one into the other rather than translating between two shapes."""
    result = _run(tmp_path, _contract(_requirement(origin=intent.POLICY_REQUIRED,
                                                   source="org policy")),
                  CATALOG, "--floor", "--json")
    assert result.returncode == 0, (result.stdout, result.stderr)
    floor = json.loads(result.stdout)["floor"]
    assert floor == {"review-diff": {"source": POLICY_REQUIRED,
                                     "reason": "reviewed by somebody else"}}

    from rig_workbench.workbench import synthesis

    entries = tuple(synthesis._floor_entry(step, value) for step, value in floor.items())
    assert synthesis.check_floor(entries, CATALOG) == entries


def test_deriving_a_target_exits_zero_even_when_it_asks_for_nothing(tmp_path):
    """Asking for nothing is an answer about the contract, not a failure to read it."""
    result = _run(tmp_path, _contract(_requirement(evidence=("test_login",))),
                  CRITERIA, "--target")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "nothing this contract can ask for" in result.stdout


def test_the_target_report_names_the_axes_it_says_nothing_about(tmp_path):
    result = _run(tmp_path, _contract(_requirement(evidence=("tests_pass_or_explained",))),
                  CRITERIA, "--target")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "gate passed" in result.stdout
    assert "isolation" in result.stdout and "provenance" in result.stdout


def test_a_contract_that_is_not_one_is_not_derivable(tmp_path):
    """Exit 1 rather than 2: the file was read, and it is not a contract."""
    result = _run(tmp_path, {"schema": "wrong"}, CATALOG, "--floor")
    assert result.returncode == 1, (result.returncode, result.stdout, result.stderr)
    assert "[REJECTED]" in result.stderr


def test_two_declarations_colliding_is_not_derivable(tmp_path):
    result = _run(tmp_path, _contract(_requirement(),
                                      _requirement(text="policy too",
                                                   origin=intent.POLICY_REQUIRED,
                                                   source="org")),
                  CATALOG, "--floor")
    assert result.returncode == 1, (result.returncode, result.stdout, result.stderr)
    assert "One authority per step" in result.stderr


def test_a_contract_that_cannot_be_read_is_its_own_status(tmp_path):
    catalog = tmp_path / "against.json"
    catalog.write_text(json.dumps(sorted(CATALOG)), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(WORKBENCH), "intent-derive", str(tmp_path / "absent.json"),
         "--against", str(catalog), "--floor"],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=60)
    assert result.returncode == 2
    assert "execution-error" in result.stdout


def test_a_contract_naming_one_key_twice_is_refused(tmp_path):
    """JSON allows a key twice and `json.loads` keeps the last one silently, so a requirement
    whose `origin` appears twice reaches the floor saying only the last one."""
    text = ('{"schema": "%s", "goal": "g", "requirements": [{"text": "t", '
            '"origin": "inferred", "origin": "explicit-user", "source": "s", '
            '"evidence": ["review-diff"]}]}' % intent.SCHEMA)
    result = _run(tmp_path, None, CATALOG, "--floor", contract_text=text)
    assert result.returncode == 2, (result.returncode, result.stdout)
    assert "twice" in json.loads(result.stdout)["error"]


def test_the_catalog_has_to_be_an_array_of_names(tmp_path):
    """`set(json.loads(...))` takes whatever iterates, and this file decides which evidence
    names become floor entries."""
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(_contract()), encoding="utf-8")
    catalog = tmp_path / "against.json"
    catalog.write_text('{"review-diff": false}', encoding="utf-8")
    result = subprocess.run([sys.executable, str(WORKBENCH), "intent-derive", str(path),
                             "--against", str(catalog), "--floor"],
                            capture_output=True, text=True, cwd=REPO_ROOT, timeout=60)
    assert result.returncode == 2, (result.returncode, result.stdout)
    assert "catalog:" in json.loads(result.stdout)["error"]


# ── it derives; it does not conclude ─────────────────────────────────────────
def test_the_module_neither_runs_a_process_nor_calls_a_model():
    """Turning a goal into requirements is reading, judging and concluding. A module that did
    it would leave nothing a gate could check and nothing a mutation could falsify."""
    import ast

    tree = ast.parse((REPO_ROOT / "rig_workbench" / "workbench"
                      / "intent_wiring.py").read_text(encoding="utf-8"))
    reaching = {"subprocess", "socket", "http", "urllib", "requests", "os", "open"}
    judging = {"floor_from", "unmatched", "resting_on", "target_from", "unaskable",
               "projection"}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name in judging):
            continue
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        names |= {a.name.split(".")[0] for n in ast.walk(node)
                  if isinstance(n, ast.Import) for a in n.names}
        assert not names & reaching, (node.name, sorted(names & reaching))


# ── gaps the mutation sweep found ────────────────────────────────────────────
def test_the_floor_is_ordered_so_two_readings_of_one_contract_agree():
    """The entries are a tuple a caller may print or compare; leaving them in insertion order
    would make the same contract produce two different-looking floors depending on which
    requirement was written first."""
    forward = floor_from(_loaded(_requirement(evidence=("security-audit",)),
                                 _requirement(text="and review", evidence=("review-diff",))),
                         CATALOG)
    backward = floor_from(_loaded(_requirement(text="and review", evidence=("review-diff",)),
                                  _requirement(evidence=("security-audit",))), CATALOG)
    assert [item.id for item in forward] == ["review-diff", "security-audit"]
    assert [item.id for item in forward] == [item.id for item in backward]


def test_the_first_declaration_naming_a_step_is_the_reason_it_is_on_the_floor():
    """Two requirements from the same authority both wanting a step is not a collision, and
    the reason a reader sees should be stable rather than whichever was read last."""
    floor = floor_from(_loaded(_requirement(text="first said so", evidence=("review-diff",)),
                               _requirement(text="second said so",
                                            evidence=("review-diff",))), CATALOG)
    assert [item.reason for item in floor] == ["first said so"]


def test_a_gate_that_was_never_evaluated_checks_nothing():
    """`assurance._gates` returns an `unobserved` block when there is no acceptance.json.
    Reading `criteria` off it anyway would report every requirement as checked by nothing in a
    way indistinguishable from a gate that ran and ruled on nothing."""
    result = projection(_contract(_requirement(evidence=("tests_pass_or_explained",))),
                        {"observed": False, "reason": "no acceptance.json"})
    assert result["requirements"][0]["checked_by"] == []


def test_the_projection_says_which_origin_each_requirement_had():
    """"A user asked for this" and "rig concluded it" are the distinction the contract exists
    to draw, and a page that reported both as declared would erase it."""
    result = projection(_contract(_requirement(text="asked", origin=intent.EXPLICIT_USER),
                                  _requirement(text="guessed", origin=intent.INFERRED,
                                               source="")),
                        {"observed": False, "reason": "not evaluated"})
    by_text = {r["text"]: (r["origin"], r["declared"]) for r in result["requirements"]}
    assert by_text == {"asked": (intent.EXPLICIT_USER, True),
                       "guessed": (intent.INFERRED, False)}


def test_the_report_names_evidence_that_looks_like_a_misspelled_component(tmp_path):
    result = _run(tmp_path, _contract(_requirement(evidence=("Review-Diff",))),
                  CATALOG, "--floor")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "did you mean review-diff" in result.stdout, result.stdout


def test_the_observed_flag_decides_and_not_whether_criteria_happen_to_be_there():
    """`assurance.unobserved` is the receipt's way of saying "nobody measured this", and a
    consumer that read the data next to it would be treating a leftover as a measurement.
    The flag is the authority."""
    result = projection(_contract(_requirement(evidence=("tests_pass_or_explained",))),
                        {"observed": False, "reason": "no acceptance.json",
                         "criteria": [{"name": "tests_pass_or_explained",
                                       "status": "passed"}]})
    assert result["requirements"][0]["checked_by"] == []


# ── what round 1 found ───────────────────────────────────────────────────────
def _run_dir(tmp_path, contract_text):
    """A run directory the receipt would read, holding just the contract."""
    run = tmp_path / ".rig" / "runs" / "a-task"
    run.mkdir(parents=True)
    (run / "intent.json").write_text(contract_text, encoding="utf-8")
    return run


def test_the_receipt_reads_the_contract_with_the_same_refusals_the_command_does(tmp_path):
    """JSON allows a key twice and `json.loads` keeps the last one silently, so a duplicated
    `origin` would turn an inferred requirement into a declared one — and the receipt would
    present that parser choice as what the contract recorded."""
    from rig_workbench.workbench import assurance

    text = ('{"schema": "%s", "goal": "g", "requirements": [{"text": "t", '
            '"origin": "inferred", "origin": "explicit-user", "source": "s", '
            '"evidence": []}]}' % intent.SCHEMA)
    run = _run_dir(tmp_path, text)
    assert assurance._read_contract(run / "intent.json") is assurance.UNREADABLE


@pytest.mark.parametrize("text,why", [
    ("not json", "not json at all"),
    ("[]", "a list"),
    ('"a contract"', "a scalar"),
])
def test_a_contract_that_is_there_and_unreadable_is_not_a_contract_that_is_absent(tmp_path,
                                                                                  text, why):
    """The file is there — its digest is in `sources` — and nobody can read it, which is a
    different situation with a different next step."""
    from rig_workbench.workbench import assurance

    run = _run_dir(tmp_path, text)
    assert assurance._read_contract(run / "intent.json") is assurance.UNREADABLE, why

    result = projection(assurance.UNREADABLE, {"observed": False, "reason": "x"})
    assert result["observed"] is False
    assert "there and cannot be read" in result["reason"]


def test_an_absent_contract_reads_as_absent(tmp_path):
    from rig_workbench.workbench import assurance

    run = tmp_path / ".rig" / "runs" / "a-task"
    run.mkdir(parents=True)
    assert assurance._read_contract(run / "intent.json") is None
    assert "no contract was recorded" in projection(None, {"observed": False})["reason"]


def test_a_readable_contract_reads_as_itself(tmp_path):
    from rig_workbench.workbench import assurance

    run = _run_dir(tmp_path, json.dumps(_contract()))
    assert assurance._read_contract(run / "intent.json") == _contract()


def test_the_projection_carries_the_assumptions_it_claims_to_copy():
    """They qualify what the work was taken to mean, so a view that dropped them would be
    copying most of the contract."""
    result = projection(_contract(assumptions=["the API is stable this quarter"]),
                        {"observed": False, "reason": "x"})
    assert result["assumptions"] == ["the API is stable this quarter"]


def test_the_candidate_report_is_case_only_and_says_so():
    """Wider similarity would need a rule about how close is close enough, and a contract
    author told "did you mean review-diff?" about a test id they wrote on purpose learns to
    stop reading these."""
    assert unmatched(_loaded(_requirement(evidence=("review-dif",))), CATALOG) == ()
    assert unmatched(_loaded(_requirement(evidence=("security_audit",))), CATALOG) == ()
    assert len(unmatched(_loaded(_requirement(evidence=("REVIEW-DIFF",))), CATALOG)) == 1


# ── what round 2 found: the contract's own schema was not closed ─────────────
@pytest.mark.parametrize("payload,fragment", [
    ({"budget": 100}, "not part of a rig.intent-contract/v1"),
    ({"requirements": [dict(_requirement(), mandatory=True)]}, "not part of a requirement"),
    ({"requirements": [dict(_requirement(), axis="isolation")]}, "not part of a requirement"),
    ({"ambiguities": [{"question": "q", "resolved_by": "r", "guess": "maybe"}]},
     "not part of an ambiguity"),
])
def test_a_key_this_schema_does_not_define_is_refused_rather_than_dropped(payload, fragment):
    """A key `validate` accepts and `load` drops leaves the author believing the contract says
    something it no longer says — and a receipt claiming to copy the contract copying most of
    it. `mandatory` and `axis` are the sharp cases: a contract could look like it spoke to an
    assurance axis while nothing here reads that."""
    problems = intent.validate(dict(_contract(), **payload))
    assert any(fragment in p for p in problems), (payload, problems)


@pytest.mark.parametrize("where", ["contract", "requirement", "ambiguity"])
def test_a_key_that_is_not_a_string_is_reported_rather_than_raised(where):
    """`sorted` on mixed key types raises, and `validate` promises a list of problems. JSON
    cannot produce such a key; a caller building the dict can."""
    payload = _contract(ambiguities=[{"question": "q", "resolved_by": "r"}])
    if where == "contract":
        payload[42] = "x"
    elif where == "requirement":
        payload["requirements"][0][42] = "x"
    else:
        payload["ambiguities"][0][42] = "x"
    assert any("is not a key" in p for p in intent.validate(payload)), where


def test_the_accepted_keys_are_the_ones_a_requirement_actually_has():
    """Derived, so a field added to `Requirement` is accepted by `validate` without anyone
    remembering to."""
    import dataclasses

    assert intent.REQUIREMENT_KEYS == {f.name for f in dataclasses.fields(intent.Requirement)}


def test_the_receipt_reports_a_contract_with_an_unknown_field_rather_than_copying_part_of_it():
    result = projection(dict(_contract(), waivable=True), {"observed": False, "reason": "x"})
    assert result["observed"] is False
    assert "is not a contract" in result["reason"]


# ── what round 3 found: a third reader, and a load that copied by hand ───────
DUPLICATED = ('{"schema": "%s", "goal": "g", "requirements": [{"text": "t", '
              '"origin": "inferred", "origin": "explicit-user", "source": "s", '
              '"evidence": []}]}' % intent.SCHEMA)


def test_every_entry_point_that_reads_a_contract_reads_it_the_same_way(tmp_path):
    """Three read contracts from disk — `intent`, `intent-derive`, and the receipt — and each
    was written with its own parser until one of them reported a duplicated `origin` as a
    valid declaration. A rule each caller has to remember is a rule one of them will not."""
    from rig_workbench.workbench import assurance

    path = tmp_path / "contract.json"
    path.write_text(DUPLICATED, encoding="utf-8")

    with pytest.raises(ValueError, match="twice"):
        intent.read(path)
    assert assurance._read_contract(path) is assurance.UNREADABLE


def test_the_intent_command_refuses_it_too(tmp_path):
    path = tmp_path / "contract.json"
    path.write_text(DUPLICATED, encoding="utf-8")
    result = subprocess.run([sys.executable, str(WORKBENCH), "intent", str(path)],
                            capture_output=True, text=True, cwd=REPO_ROOT, timeout=60)
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert "twice" in result.stdout + result.stderr


def test_every_requirement_field_survives_validate_then_load_then_back():
    """`REQUIREMENT_KEYS` accepts every field the dataclass has, so a field added there and
    copied by hand in `load` would be validated and then dropped — the failure the closed
    schema exists to prevent, reintroduced one function later."""
    import dataclasses

    raw = _requirement(text="t", origin=intent.EXPLICIT_USER, source="issue #1",
                       evidence=("review-diff", "test_login"))
    assert intent.validate(_contract(raw)) == []
    [loaded] = intent.load(_contract(raw)).requirements
    assert loaded.as_dict() == raw
    assert set(raw) == {f.name for f in dataclasses.fields(intent.Requirement)}


def test_a_requirement_leaving_an_optional_field_out_still_loads():
    """The defaults are the dataclass's, so leaving `source` out of an inferred requirement is
    the same as writing an empty one — which is what `origin` already decides."""
    [loaded] = intent.load(_contract({"text": "t", "origin": intent.INFERRED})).requirements
    assert (loaded.source, loaded.evidence) == ("", ())


def test_a_loaded_contract_cannot_be_changed_through_the_payload_it_came_from():
    """`frozen=True` stops the fields being replaced and not a list behind one from being
    appended to. What was validated has to be what gets read."""
    payload = _contract(_requirement(evidence=["review-diff"]))
    contract = intent.load(payload)
    payload["requirements"][0]["evidence"].append("security-audit")
    assert contract.requirements[0].evidence == ("review-diff",)
    assert floor_from(contract, CATALOG)[0].id == "review-diff"
    assert len(floor_from(contract, CATALOG)) == 1


# ── what round 4 found: two more places a field had to be remembered ─────────
def test_as_dict_carries_every_field_the_dataclass_has():
    """`REQUIREMENT_KEYS` and `load` derive from `Requirement`; a third place copying four
    fields by hand would let a field be accepted, loaded, and then vanish on the way out."""
    import dataclasses

    one = intent.Requirement(text="t", origin=intent.INFERRED, evidence=("a",))
    assert set(one.as_dict()) == {f.name for f in dataclasses.fields(intent.Requirement)}
    assert one.as_dict()["evidence"] == ["a"], "tuples come back as JSON lists"


def test_the_projection_starts_from_as_dict_rather_than_naming_fields_again():
    """One rule for four places instead of four places that have to agree."""
    result = projection(_contract(_requirement()), {"observed": False, "reason": "x"})
    [row] = result["requirements"]
    assert set(intent.Requirement(text="t", origin=intent.INFERRED).as_dict()) <= set(row)


def test_an_ambiguity_cannot_be_changed_after_it_was_validated():
    """`frozen=True` protects the tuple and not the dicts inside it, so a caller could
    otherwise replace a question with something `validate` would have refused — either
    through the contract or through the payload it was built from, which is why the view is
    over a copy rather than over the caller's dict."""
    payload = _contract(ambiguities=[{"question": "q", "resolved_by": "r"}])
    contract = intent.load(payload)
    with pytest.raises(TypeError):
        contract.ambiguities[0]["question"] = "something else"
    payload["ambiguities"][0]["question"] = "changed behind its back"
    assert contract.ambiguities[0]["question"] == "q"


def test_the_projection_still_reads_a_frozen_ambiguity():
    result = projection(_contract(ambiguities=[{"question": "q", "resolved_by": "r"}]),
                        {"observed": False, "reason": "x"})
    assert result["ambiguities"] == [{"question": "q", "resolved_by": "r"}]


# ── what round 5 found: the contract level was still enumerated by hand ──────
def test_every_contract_field_survives_validate_load_serialise_and_project():
    """The requirement level was derived in round 3; the contract level was still spelled out
    in four places, so a field added to `IntentContract` could be refused, dropped or omitted
    depending on which one was forgotten."""
    import dataclasses

    payload = _contract(_requirement(), non_goals=["not the parser"],
                        assumptions=["the API is stable"],
                        ambiguities=[{"question": "q", "resolved_by": "r"}])
    assert intent.validate(payload) == []

    contract = intent.load(payload)
    fields = {f.name for f in dataclasses.fields(intent.IntentContract)}
    assert set(contract.as_dict()) == fields | {"schema"}
    assert contract.as_dict() == payload, "what went in comes back out"

    projected = projection(payload, {"observed": False, "reason": "x"})
    assert fields <= set(projected), "and reaches the receipt"


def test_the_accepted_contract_keys_are_the_ones_the_dataclass_has():
    import dataclasses

    assert intent.CONTRACT_KEYS == {"schema"} | {
        f.name for f in dataclasses.fields(intent.IntentContract)}


# ── what round 6 found: five rounds of deriving each layer still left layers ─────
# The answer is not a sixth derivation. Every round had the same shape — a rule somebody had
# to remember — and deriving one more place only moves where the next person forgets. These
# check the two mechanisms that make forgetting fail loudly instead: a field nobody said how
# to read cannot be read, and a field nobody said whether to print cannot be left off.
def test_a_contract_field_nobody_said_how_to_read_refuses_at_import():
    """Not a test of the five fields we have. A test of what happens to the sixth.

    The check runs at import — `_gap` at module scope — so it cannot be skipped, deselected,
    or simply not run by the person adding the field. This calls the same function with a
    field it was never told about, because a guard nothing exercises is a guard nobody knows
    still works.
    """
    assert intent._codec_gaps(intent._CONTRACT_FIELDS, intent._CODEC) is None

    gap = intent._codec_gaps(intent._CONTRACT_FIELDS | {"deadline"}, intent._CODEC)
    assert gap is not None
    assert "deadline" in gap and "dropped by load" in gap


def test_a_converter_for_a_field_that_no_longer_exists_refuses_too():
    """The other direction. A table that describes a shape the record no longer has is how it
    starts being read as though it did."""
    gap = intent._codec_gaps(intent._CONTRACT_FIELDS - {"non_goals"}, intent._CODEC)
    assert gap is not None and "non_goals name no field" in gap


def test_load_reads_every_field_through_the_codec():
    """`load` used to name its five fields. Now it asks `_CODEC`, so the guard above is not
    advice — it is the thing that decides whether a field can be loaded at all."""
    import dataclasses

    calls = []

    def watched(value):
        calls.append(value)
        return tuple(value)

    original = intent._CODEC["non_goals"]
    intent._CODEC["non_goals"] = watched
    try:
        contract = intent.load(_contract(_requirement(), non_goals=["not the parser"]))
    finally:
        intent._CODEC["non_goals"] = original
    assert calls == [["not the parser"]], "load went through the table, not around it"
    assert contract.non_goals == ("not the parser",)
    assert {f.name for f in dataclasses.fields(intent.IntentContract)} == set(intent._CODEC)


def test_a_criterion_recorded_twice_is_not_a_verdict():
    """Indexing the gate's criteria by name kept whichever record came last, so a criterion
    ruled on twice gave this page a verdict that depended on the order two records sat in.

    Any repeat, not only a disagreeing one: a gate that ruled on one criterion twice did not
    produce a record a single verdict can be read out of, whatever the two rulings say.
    """
    gates = {"observed": True, "criteria": [
        {"name": "tests_pass_or_explained", "status": "passed",
         "name_recorded_more_than_once": True},
        {"name": "tests_pass_or_explained", "status": "failed",
         "name_recorded_more_than_once": True}]}
    result = projection(_contract(_requirement(evidence=("tests_pass_or_explained",))), gates)
    [checked] = result["requirements"][0]["checked_by"]
    assert checked == {"criterion": "tests_pass_or_explained", "status": None,
                       "overridden": False, "ambiguous": True}
    assert "passed" not in json.dumps(checked) and "failed" not in json.dumps(checked)


# ── and the mutation sweep found the same thing one level up ─────────────────────
# The two guards above were checked by calling them. Nothing checked that either is *installed*
# — replacing `_gap = _codec_gaps(...)` with `_gap = None` left every test passing, which is
# the sixth-place failure applied to the mechanism meant to end it. These re-run each module's
# real source with a field it has never been told about, so what is under test is the guard
# in the position it actually occupies.
def _reexec(path, source, package="rig_workbench.workbench"):
    """Run a module's source as that module, from a fresh namespace.

    Not an AST walk looking for the call. This repository has already paid for approximating
    Python's own rules with a parser once; running the code is the only reading of "does this
    fire at import" that cannot be off by a language feature nobody thought of.
    """
    import types

    name = f"{package}._probe"
    module = types.ModuleType(name)
    module.__file__ = str(path)
    module.__package__ = package
    # Registered before the exec: `@dataclasses.dataclass` resolves a field's annotation by
    # looking its defining module up in `sys.modules`, and a module that is not there raises
    # before the guard under test ever runs.
    sys.modules[name] = module
    try:
        exec(compile(source, str(path), "exec"), module.__dict__)
    finally:
        sys.modules.pop(name, None)
    return module


def test_the_codec_check_fires_at_import_not_only_when_called():
    """A guard nothing installs is a guard that is not there."""
    import pytest

    path = pathlib.Path(intent.__file__)
    source = path.read_text(encoding="utf-8")
    added = source.replace("    ambiguities: tuple[dict, ...] = ()\n",
                           "    ambiguities: tuple[dict, ...] = ()\n"
                           "    deadline: str = ''\n", 1)
    assert added != source, "the injection point moved — this test is no longer testing it"

    _reexec(path, source)  # unchanged source imports cleanly
    with pytest.raises(RuntimeError) as raised:
        _reexec(path, added)
    assert "deadline" in str(raised.value)


def test_the_page_check_fires_at_import_too():
    """Same guard, other module. `assurance` reads the contract's fields off the live
    dataclass, so the future field is added there rather than to its own source."""
    import dataclasses

    import pytest

    from rig_workbench.workbench import assurance

    path = pathlib.Path(assurance.__file__)
    source = path.read_text(encoding="utf-8")
    _reexec(path, source)

    fields = intent.IntentContract.__dataclass_fields__
    field = dataclasses.field(default="")
    field.name, field.type = "deadline", "str"
    # `dataclasses.fields()` skips anything whose `_field_type` is not `_FIELD`, so a field
    # object without it would be invisible and this test would pass for the wrong reason.
    field._field_type = dataclasses._FIELD
    fields["deadline"] = field
    assert "deadline" in {f.name for f in dataclasses.fields(intent.IntentContract)}
    try:
        with pytest.raises(RuntimeError) as raised:
            _reexec(path, source)
    finally:
        del fields["deadline"]
    assert "deadline" in str(raised.value)


def test_a_field_cannot_be_both_rendered_and_withheld():
    """The shape this check takes when somebody silences it: withhold what is still printed
    and every field is accounted for by nothing."""
    from rig_workbench.workbench import assurance

    gap = assurance._unrendered({"goal"}, {"goal"}, {"goal": "a stated reason"})
    assert gap is not None and "both rendered and withheld" in gap
