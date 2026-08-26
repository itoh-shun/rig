"""Did the numbers somebody declared this change would move actually move (#437).

What these tests hold the module to is that it never answers "yes" about a number nobody
measured, and never quietly drops a number that disagreed:

* the observation document cannot state the bar it is judged against, on either level;
* an expectation that requires nothing to move cannot reach `achieved`, whether it says so
  with `metrics: []` or with nothing but guardrails;
* `unmeasured` and `inconclusive` are their own outcomes, ranked the way both sibling modules
  rank the same pair of ideas — a measured negative outranks a cannot-look;
* an observation outside the window settles nothing **and is still counted**, because a report
  in which three measured regressions vanished is byte-for-byte a report of one clean run;
* `change` is a git object, not a name, and `--task` refuses only when two records disagree
  about which object it is — not when rig simply cannot tell.

Every refusal asserts on the *reason*, not merely that something was refused: a refusal for
the wrong reason is a check not looking where it claims to. And the valid documents are
asserted to be accepted, because a validator that refused everything would pass every refusal
test in this file.
"""

import copy
import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench import intent, provenance_graph
from rig_workbench.workbench.production_outcome import (
    ACHIEVED, CONFIRMED, DECLARED, ESTIMATED, EXPECTATION, GUARDRAIL, INCONCLUSIVE,
    MEASURED, NOT_ACHIEVED, OBJECTIVE, OBSERVATION, OUTCOMES, PARTIALLY_ACHIEVED, PRECEDENCE,
    RECORD_NAME, REGRESSED, ROLE_KEYS, SCHEMA, UNMEASURED, UNOBSERVABLE, _compare_one,
    _vocabulary_gaps, change_cross_check, compare, recorded, validate_expectation,
    validate_observation)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"

SHA = "a" * 40
OTHER_SHA = "b" * 40
AS_OF_CLOSED = "2026-08-20T00:00:00+00:00"
AS_OF_OPEN = "2026-08-10T00:00:00+00:00"


def _expectation(**over) -> dict:
    payload = {
        "schema": EXPECTATION,
        "change": SHA,
        "declared_by": "explicit-user",
        "declared_at": "2026-07-30T00:00:00+00:00",
        "source": "#437 acceptance criteria, line 3",
        "window": {"opens": "2026-08-01T00:00:00+00:00",
                   "closes": "2026-08-15T00:00:00+00:00"},
        "metrics": [
            {"id": "p95_latency_ms", "role": OBJECTIVE, "unit": "ms",
             "direction": "decrease", "baseline": 820.0, "target": 574.0},
            {"id": "error_rate_pct", "role": GUARDRAIL, "unit": "pct",
             "direction": "increase", "limit": 0.5},
        ],
    }
    payload.update(over)
    return payload


def _observation(**over) -> dict:
    payload = {
        "schema": OBSERVATION,
        "change": SHA,
        "observations": [_entry(), _entry(metric="error_rate_pct", value=0.22, unit="pct")],
    }
    payload.update(over)
    return payload


def _entry(**over) -> dict:
    payload = {"metric": "p95_latency_ms", "value": 787.0, "unit": "ms",
               "observed_at": "2026-08-14T09:00:00+00:00", "kind": MEASURED,
               "source": "apm-export-0814"}
    payload.update(over)
    return payload


def _metric(**over) -> dict:
    payload = copy.deepcopy(_expectation()["metrics"][0])
    payload.update(over)
    return payload


def _without(payload: dict, key: str) -> dict:
    return {k: v for k, v in payload.items() if k != key}


def _reasons(problems: list[str], needle: str) -> list[str]:
    return [p for p in problems if needle in p]


# ── positive controls ────────────────────────────────────────────────────────

def test_the_valid_documents_are_accepted():
    """A validator that refused everything would pass every refusal test in this file."""
    assert validate_expectation(_expectation()) == []
    assert validate_observation(_observation()) == []
    report = compare(_expectation(), _observation(), AS_OF_CLOSED)
    assert report["status"] == PARTIALLY_ACHIEVED
    assert report["final"] is True
    assert report["claim"] == "observational-not-causal"


def test_the_import_time_vocabulary_check_is_looking_where_it_claims(monkeypatch):
    """The check itself, not just its current answer: a broken pairing has to be found."""
    assert _vocabulary_gaps() == []
    monkeypatch.setattr("rig_workbench.workbench.production_outcome.PRECEDENCE",
                        (REGRESSED, NOT_ACHIEVED, UNMEASURED, INCONCLUSIVE, ACHIEVED))
    assert _reasons(_vocabulary_gaps(), "'partially-achieved' is in OUTCOMES")


# ── the vocabulary, ordered once ─────────────────────────────────────────────

def test_precedence_is_the_literal_order_its_two_siblings_use():
    """Written out by hand, so a reordering fails rather than silently re-ranking.

    The sibling rule this pins: `intent.py` ranks `unsatisfied` above `unverifiable` and
    `assurance_target.evaluate` ranks `unmet` above `unobservable` — a measured negative wins
    the headline over a cannot-look. Both are if/elif chains rather than a readable constant,
    so the ordering is asserted here as a literal and the rule is named in prose.
    """
    assert PRECEDENCE == ("regressed", "not-achieved", "partially-achieved",
                          "unmeasured", "inconclusive", "achieved")
    assert PRECEDENCE.index(NOT_ACHIEVED) < PRECEDENCE.index(UNMEASURED)
    assert PRECEDENCE.index(REGRESSED) < PRECEDENCE.index(INCONCLUSIVE)
    assert PRECEDENCE[-1] == ACHIEVED
    assert sorted(OUTCOMES) == sorted(PRECEDENCE)


def test_a_measured_shortfall_outranks_a_metric_nobody_measured():
    expectation = _expectation(metrics=[
        _metric(),
        {"id": "error_rate_pct", "role": OBJECTIVE, "unit": "pct", "direction": "decrease",
         "baseline": 1.0, "target": 0.5}])
    report = compare(expectation, _observation(observations=[_entry(value=820.0)]),
                     AS_OF_CLOSED)
    assert report["status"] == NOT_ACHIEVED
    assert report["metrics"]["error_rate_pct"]["outcome"] == UNMEASURED


def test_an_unmeasured_objective_is_not_carried_to_green_by_a_guardrail_that_held():
    report = compare(_expectation(),
                     _observation(observations=[_entry(metric="error_rate_pct", value=0.22,
                                                       unit="pct")]),
                     AS_OF_CLOSED)
    assert report["status"] == UNMEASURED
    assert report["metrics"]["error_rate_pct"]["outcome"] == ACHIEVED
    assert report["counts"][OBJECTIVE][UNMEASURED] == 1


def test_role_keys_are_closed_per_role_and_written_out_by_hand():
    assert ROLE_KEYS == {
        "objective": frozenset({"id", "role", "unit", "direction", "baseline", "target"}),
        "guardrail": frozenset({"id", "role", "unit", "direction", "limit"}),
    }
    # `partially-achieved` is structurally impossible for a guardrail: there is no baseline
    # to be partway from, and the schema is what makes that true rather than a comment.
    assert "baseline" not in ROLE_KEYS[GUARDRAIL]


# ── the comparison ───────────────────────────────────────────────────────────

DECREASING = {"role": OBJECTIVE, "direction": "decrease", "baseline": 820.0, "target": 574.0}
INCREASING = {"role": OBJECTIVE, "direction": "increase", "baseline": 100.0, "target": 140.0}
HARM_UP = {"role": GUARDRAIL, "direction": "increase", "limit": 0.5}
HARM_DOWN = {"role": GUARDRAIL, "direction": "decrease", "limit": 99.9}


@pytest.mark.parametrize("metric,value,expected", [
    (DECREASING, 500.0, ACHIEVED),
    (DECREASING, 574.0, ACHIEVED),            # reaching it exactly counts as reaching it
    (DECREASING, 575.0, PARTIALLY_ACHIEVED),  # …and one unit short does not
    (DECREASING, 819.9, PARTIALLY_ACHIEVED),
    (DECREASING, 820.0, NOT_ACHIEVED),        # the baseline itself is no movement
    (DECREASING, 820.1, REGRESSED),
    (DECREASING, 900.0, REGRESSED),
    (INCREASING, 150.0, ACHIEVED),
    (INCREASING, 140.0, ACHIEVED),
    (INCREASING, 139.9, PARTIALLY_ACHIEVED),
    (INCREASING, 100.0, NOT_ACHIEVED),
    (INCREASING, 99.0, REGRESSED),
    (HARM_UP, 0.1, ACHIEVED),
    (HARM_UP, 0.5, ACHIEVED),
    (HARM_UP, 0.500001, REGRESSED),
    (HARM_DOWN, 99.99, ACHIEVED),
    (HARM_DOWN, 99.9, ACHIEVED),
    (HARM_DOWN, 99.8, REGRESSED),
])
def test_the_comparison_table_including_both_boundary_values(metric, value, expected):
    assert _compare_one(metric, value) == expected


def test_final_is_a_separate_field_from_status():
    observations = _observation(observations=[_entry(value=500.0),
                                              _entry(metric="error_rate_pct", value=0.22,
                                                     unit="pct")])
    interim = compare(_expectation(), observations, AS_OF_OPEN)
    assert (interim["status"], interim["final"]) == (ACHIEVED, False)
    settled = compare(_expectation(), observations, AS_OF_CLOSED)
    assert (settled["status"], settled["final"]) == (ACHIEVED, True)


# ── what settles, what is carried, what is counted ───────────────────────────

def test_out_of_window_measurements_are_counted_and_their_sources_named():
    """The pair, not the status alone: asserting only `achieved` passes on an implementation
    that deleted three measured regressions from the record."""
    report = compare(_expectation(), _observation(observations=[
        _entry(value=500.0, observed_at="2026-08-10T00:00:00+00:00", source="apm-0810"),
        _entry(value=1500.0, observed_at="2026-08-16T00:00:00+00:00", source="apm-0816"),
        _entry(value=1600.0, observed_at="2026-08-17T00:00:00+00:00", source="apm-0817"),
        _entry(value=1700.0, observed_at="2026-07-30T00:00:00+00:00", source="apm-0730"),
        _entry(metric="error_rate_pct", value=0.22, unit="pct")]), AS_OF_CLOSED)
    entry = report["metrics"]["p95_latency_ms"]
    assert report["status"] == ACHIEVED
    assert entry["discarded_out_of_window"] == 3
    assert entry["discarded_sources"] == ["apm-0730", "apm-0816", "apm-0817"]


def test_an_estimate_inside_the_window_is_carried_and_one_outside_is_counted_apart():
    report = compare(_expectation(), _observation(observations=[
        _entry(value=500.0),
        _entry(value=505.0, kind=ESTIMATED, source="extrapolation"),
        _entry(value=9.0, kind=ESTIMATED, observed_at="2026-08-30T00:00:00+00:00",
               source="late extrapolation"),
        _entry(metric="error_rate_pct", value=0.22, unit="pct")]), AS_OF_CLOSED)
    entry = report["metrics"]["p95_latency_ms"]
    assert (entry["carried_estimates"], entry["discarded_out_of_window"]) == (1, 1)
    assert entry["outcome"] == ACHIEVED and entry["value"] == 500.0


def test_a_metric_whose_only_in_window_observation_is_an_estimate_is_inconclusive():
    report = compare(_expectation(), _observation(observations=[
        _entry(kind=ESTIMATED, value=500.0),
        _entry(metric="error_rate_pct", value=0.22, unit="pct")]), AS_OF_CLOSED)
    entry = report["metrics"]["p95_latency_ms"]
    assert entry["outcome"] == INCONCLUSIVE and entry["value"] is None
    assert "an estimate does not settle" in entry["reason"]
    assert report["status"] == INCONCLUSIVE


def test_a_metric_observed_only_outside_the_window_is_inconclusive_not_not_achieved():
    report = compare(_expectation(), _observation(observations=[
        _entry(value=900.0, observed_at="2026-08-16T00:00:00+00:00")]), AS_OF_CLOSED)
    entry = report["metrics"]["p95_latency_ms"]
    assert entry["outcome"] == INCONCLUSIVE
    assert entry["discarded_out_of_window"] == 1
    assert "falls outside the declared window" in entry["reason"]


def test_a_reported_value_settles_exactly_as_a_measured_one_does():
    """Stated in the docstring, so it is pinned: the observation side narrows nothing."""
    report = compare(_expectation(), _observation(observations=[
        _entry(value=500.0, kind="reported", source="the on-call engineer"),
        _entry(metric="error_rate_pct", value=0.22, unit="pct")]), AS_OF_CLOSED)
    assert report["status"] == ACHIEVED
    assert report["metrics"]["p95_latency_ms"]["kind"] == "reported"


def test_a_misspelled_metric_id_shows_on_both_sides_of_the_report():
    """The pair. Asserting only `unrequested` passes on an implementation that let the
    declared metric settle from the misspelled one."""
    report = compare(_expectation(), _observation(observations=[
        _entry(metric="p95_latency", value=540.0),
        _entry(metric="error_rate_pct", value=0.22, unit="pct")]), AS_OF_CLOSED)
    assert report["status"] == UNMEASURED
    assert report["metrics"]["p95_latency_ms"]["outcome"] == UNMEASURED
    assert report["unrequested"] == ["p95_latency"]


def test_two_settling_observations_of_one_metric_are_refused_never_averaged():
    with pytest.raises(ValueError) as exc:
        compare(_expectation(), _observation(observations=[
            _entry(source="apm-0810", observed_at="2026-08-10T00:00:00+00:00"),
            _entry(source="apm-0814")]), AS_OF_CLOSED)
    assert "does not choose between them and does not average them" in str(exc.value)


def test_a_unit_the_module_would_have_to_convert_is_refused():
    with pytest.raises(ValueError) as exc:
        compare(_expectation(), _observation(observations=[_entry(unit="s", value=0.787)]),
                AS_OF_CLOSED)
    assert "rig does not convert units" in str(exc.value)


def test_observations_about_a_different_change_are_evidence_about_something_else():
    with pytest.raises(ValueError) as exc:
        compare(_expectation(), _observation(change=OTHER_SHA), AS_OF_CLOSED)
    assert "evidence about something else" in str(exc.value)


def test_as_of_is_required_to_be_a_timestamp_rig_did_not_choose():
    with pytest.raises(ValueError) as exc:
        compare(_expectation(), _observation(), "yesterday")
    assert "is not an ISO 8601 timestamp with an offset" in str(exc.value)


def test_compare_refuses_an_invalid_document_rather_than_answering():
    with pytest.raises(ValueError) as exc:
        compare(_expectation(declared_by="inferred"), _observation(), AS_OF_CLOSED)
    assert "a conclusion cannot create a requirement" in str(exc.value)


# ── the expectation's refusals ───────────────────────────────────────────────

def test_an_expectation_that_is_not_an_object():
    assert validate_expectation(["p95"]) == ["expectation: expected an object, got list"]


def test_the_schema_and_unknown_keys_are_both_refused():
    problems = validate_expectation(_expectation(schema=None, rollback="on regress"))
    assert _reasons(problems, "schema: expected 'rig.expected-outcome/v1', got None")
    assert _reasons(problems, "rollback is not part of a rig.expected-outcome/v1 document")


@pytest.mark.parametrize("change", ["main", "HEAD", "the tuesday deploy", "a" * 12, "A" * 40])
def test_a_change_that_is_a_name_or_an_abbreviation_is_refused(change):
    problems = validate_expectation(_expectation(change=change))
    assert _reasons(problems, "is not a full git object id")
    assert _reasons(problems, "expectation about a branch name")


def test_a_change_that_is_missing_or_blank_is_refused():
    for value in (None, "", "   "):
        problems = validate_expectation(_expectation(change=value))
        assert _reasons(problems, "has to name the immutable change it is about")


def test_the_object_id_rule_is_imported_rather_than_restated():
    """One declaration site. `provenance_graph` already refuses object *shape* before it will
    ask git, and a second copy of the pattern is a second place for it to be wrong."""
    from rig_workbench.workbench import production_outcome

    assert production_outcome.OBJECT_ID is provenance_graph.OBJECT_ID


def test_the_declared_origins_are_intents_own_set_rather_than_a_second_copy():
    assert DECLARED is intent.DECLARED


@pytest.mark.parametrize("origin", ["inferred", "proposed", "repository-derived", None, ""])
def test_declared_by_admits_only_a_declaration(origin):
    problems = validate_expectation(_expectation(declared_by=origin))
    assert _reasons(problems, "a conclusion cannot create a requirement")
    assert _reasons(problems, "which has 'proposed' for it")


def test_a_declaration_has_to_say_where_it_was_made():
    assert _reasons(validate_expectation(_expectation(source="  ")),
                    "says someone declared this, so it has to say where")


def test_declared_at_is_required_because_a_field_that_is_absent_checks_nothing():
    problems = validate_expectation(_without(_expectation(), "declared_at"))
    assert _reasons(problems, "declared_at: None is not an ISO 8601 timestamp")
    assert _reasons(problems, "checkable claim rather than a word in a field")


def test_a_bar_declared_after_the_window_opened_was_chosen_with_the_answer_in_hand():
    problems = validate_expectation(_expectation(declared_at="2026-08-10T00:00:00+00:00"))
    assert _reasons(problems, "is after the window opens")
    assert _reasons(problems, "a floor written from a conclusion is not a floor")


def test_a_naive_timestamp_is_not_a_timestamp():
    problems = validate_expectation(_expectation(
        window={"opens": "2026-08-01T00:00:00", "closes": "2026-08-15T00:00:00+00:00"}))
    assert _reasons(problems, "window.opens: '2026-08-01T00:00:00' is not an ISO 8601")


def test_a_window_that_closes_before_it_opens_holds_nothing():
    problems = validate_expectation(_expectation(
        window={"opens": "2026-08-15T00:00:00+00:00", "closes": "2026-08-01T00:00:00+00:00"}))
    assert _reasons(problems, "closes at or before it opens")


def test_a_window_carrying_a_key_nothing_reads():
    problems = validate_expectation(_expectation(
        window={"opens": "2026-08-01T00:00:00+00:00", "closes": "2026-08-15T00:00:00+00:00",
                "grace_days": 3}))
    assert _reasons(problems, "grace_days is not part of an observation window")


def test_an_expectation_that_requires_nothing_is_met_by_everything():
    assert _reasons(validate_expectation(_expectation(metrics=[])),
                    "an expectation that requires nothing is met by everything")
    assert _reasons(validate_expectation(_expectation(metrics={})), "metrics: expected a list")


def test_an_expectation_of_only_guardrails_declares_nothing_that_had_to_move():
    problems = validate_expectation(_expectation(metrics=[
        {"id": "error_rate_pct", "role": GUARDRAIL, "unit": "pct", "direction": "increase",
         "limit": 0.5}]))
    assert _reasons(problems, "declares no objective")
    assert _reasons(problems, "'achieved' would mean only that nothing got worse")


def test_two_bars_for_one_metric_is_two_answers():
    assert _reasons(validate_expectation(_expectation(metrics=[_metric(), _metric()])),
                    "declares 'p95_latency_ms' twice")


def test_a_role_outside_the_vocabulary_does_not_hide_the_rest_of_the_metric():
    """The collected-rejections rule: an author who fixes `role` must not then be refused
    four more times for things this refusal could have told them."""
    problems = validate_expectation(_expectation(metrics=[
        {"id": "p95", "role": "kpi", "unit": "", "direction": "sideways", "baseline": "820"}]))
    assert _reasons(problems, "role 'kpi' is not one of objective, guardrail")
    assert _reasons(problems, "direction 'sideways' is not one of")
    assert _reasons(problems, "has no unit")
    assert _reasons(problems, "baseline '820' is not a finite number")


def test_a_key_that_belongs_to_the_other_role_is_refused_by_name():
    problems = validate_expectation(_expectation(metrics=[
        _metric(),
        {"id": "error_rate_pct", "role": GUARDRAIL, "unit": "pct", "direction": "increase",
         "limit": 0.5, "baseline": 0.2}]))
    assert _reasons(problems, "baseline is not part of a guardrail metric")


@pytest.mark.parametrize("value", [True, False, None, "820", float("nan"), float("inf"), []])
def test_a_baseline_that_is_not_a_finite_number(value):
    """`true` arrives as the number 1 and `NaN` compares False against everything, so a
    metric carrying one would land on `regressed` with a straight face."""
    problems = validate_expectation(_expectation(metrics=[_metric(baseline=value)]))
    assert _reasons(problems, "is not a finite number")


def test_a_target_already_cleared_before_the_change_makes_achieved_free():
    problems = validate_expectation(_expectation(metrics=[_metric(target=900.0)]))
    assert _reasons(problems, "is not beyond baseline")
    problems = validate_expectation(_expectation(metrics=[
        _metric(direction="increase", baseline=820.0, target=574.0)]))
    assert _reasons(problems, "is not beyond baseline")


# ── the observation's refusals ───────────────────────────────────────────────

@pytest.mark.parametrize("key", ["status", "target", "baseline", "limit", "window"])
def test_the_observation_document_may_not_state_the_bar(key):
    problems = validate_observation(_observation(**{key: "whatever"}))
    assert _reasons(problems, f"{key} is not part of a rig.production-observation/v1 document")
    assert _reasons(problems, "an observation states a value, not the bar it is measured")


@pytest.mark.parametrize("key", ["target", "baseline", "limit", "status", "role", "direction"])
def test_an_observation_entry_may_not_state_the_bar_either_and_says_why(key):
    """The entry-level refusal carries the reason too — a bare unknown-key message would
    leave the author to guess that the whole idea was wrong, not the spelling."""
    problems = validate_observation(_observation(observations=[_entry(**{key: 1})]))
    assert _reasons(problems, f"observations[0]: {key} is not part of an observation")
    assert _reasons(problems, "an observation states a value, not the bar it is measured")


def test_a_declaration_key_on_an_observation_is_refused_without_the_bar_reason():
    """`declared_at` belongs to the expectation, but it is not a bar — an adapter that wrote
    one was confused about which document owns a declaration, and the bar sentence would
    explain that mistake wrongly."""
    problems = validate_observation(_observation(declared_at="2026-08-01T00:00:00+00:00"))
    assert _reasons(problems, "declared_at is not part of a rig.production-observation/v1")
    assert not _reasons(problems, "not the bar it is measured")


def test_observations_have_to_name_the_change_as_an_object():
    assert _reasons(validate_observation(_observation(change="main")),
                    "is not a full git object id")
    assert _reasons(validate_observation(_observation(change=None)),
                    "observations have to name the immutable change")


@pytest.mark.parametrize("value", ["787", True, None, float("nan"), float("-inf")])
def test_an_observed_value_that_is_not_a_finite_number(value):
    assert _reasons(validate_observation(_observation(observations=[_entry(value=value)])),
                    "is not a finite number")


def test_an_unattributed_number_is_not_evidence():
    problems = validate_observation(_observation(observations=[_without(_entry(), "source")]))
    assert _reasons(problems, "does not say where the number came from")


def test_a_kind_outside_the_vocabulary_is_not_a_weaker_kind():
    assert _reasons(validate_observation(_observation(observations=[_entry(kind="guess")])),
                    "kind 'guess' is not one of measured, reported, estimated")


def test_an_observed_at_rig_would_have_to_interpret():
    for value in ("last tuesday", "2026-08-14T09:00:00", None):
        problems = validate_observation(_observation(observations=[
            _entry(observed_at=value)]))
        assert _reasons(problems, "is not an ISO 8601 timestamp with an offset")


def test_an_observation_document_that_is_not_one():
    assert validate_observation("nope") == ["observation: expected an object, got str"]
    assert _reasons(validate_observation(_observation(observations={})),
                    "observations: expected a list")


# ── the cross-check against a run's receipt ──────────────────────────────────

def _receipt(head: dict, final_status="accepted", task="t-1") -> dict:
    return {"task": {"id": task}, "target": {"head": head}, "final_status": final_status}


def test_a_receipt_with_no_commit_linked_is_unobservable_not_a_refusal():
    """`record-commit` is what writes `commit_sha` and nothing in the flow runs it, so this
    is the ordinary case — and "rig cannot tell" is not "the records disagree"."""
    result = change_cross_check(
        _receipt({"observed": False, "reason": "no commit is linked to this task"}), SHA)
    assert result["outcome"] == UNOBSERVABLE
    assert result["reason"] == "no commit is linked to this task"


def test_a_receipt_recording_an_abbreviation_is_unobservable():
    """`record-commit <task> <sha>` writes whatever it is given, and a prefix that matches is
    not the same fact as an object that is the same."""
    result = change_cross_check(
        _receipt({"observed": True, "commit": SHA[:12], "resolvable": True,
                  "source": "record-commit"}), SHA)
    assert result["outcome"] == UNOBSERVABLE
    assert "abbreviation rather than a full object id" in result["reason"]


def test_a_recorded_commit_git_cannot_resolve_is_unobservable():
    result = change_cross_check(
        _receipt({"observed": True, "commit": OTHER_SHA, "resolvable": False,
                  "source": "import"}), SHA)
    assert result["outcome"] == UNOBSERVABLE
    assert "git cannot resolve it" in result["reason"]
    assert result["source"] == "import"


def test_a_receipt_naming_a_different_object_is_the_one_case_that_refuses():
    with pytest.raises(ValueError) as exc:
        change_cross_check(_receipt({"observed": True, "commit": OTHER_SHA,
                                     "resolvable": True, "source": "record-commit"}), SHA)
    assert "two records disagreeing about which change this is" in str(exc.value)
    assert "t-1" in str(exc.value)


def test_a_receipt_naming_this_object_confirms_it():
    result = change_cross_check(_receipt({"observed": True, "commit": SHA, "resolvable": True,
                                          "source": "record-commit"}), SHA)
    assert result == {"outcome": CONFIRMED, "commit": SHA, "source": "record-commit",
                      "reason": None}


# ── the seam ─────────────────────────────────────────────────────────────────

def test_the_comparison_is_made_in_exactly_one_place():
    """`assurance_wiring`'s rule: two readers of one record eventually disagree. Every view
    of this question copies `projection`'s answer, so `compare` has one caller."""
    source = (REPO_ROOT / "rig_workbench").rglob("*.py")
    callers = [path for path in source
               if "compare(" in path.read_text(encoding="utf-8")
               and path.name != "production_outcome.py"]
    assert callers == []
    body = (REPO_ROOT / "rig_workbench" / "workbench"
            / "production_outcome.py").read_text(encoding="utf-8")
    assert body.count("= compare(") == 1


def test_recorded_is_none_when_nothing_was_recorded(tmp_path):
    """`None` and "nothing was achieved" are different facts, and a caller that defaulted one
    to the other would report a run nobody measured as a run that failed."""
    assert recorded(tmp_path) is None
    (tmp_path / RECORD_NAME).write_text("{not json", encoding="utf-8")
    assert recorded(tmp_path) is None


# ── exit codes, through the real CLI ─────────────────────────────────────────

def _run_cli(args, cwd):
    return subprocess.run([sys.executable, str(WORKBENCH), *args], capture_output=True,
                          text=True, cwd=cwd, timeout=120)


@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


@pytest.fixture
def head_sha(git_repo):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=git_repo, capture_output=True,
                          text=True, check=True).stdout.strip()


def _files(git_repo, expectation, observation):
    (git_repo / "exp.json").write_text(json.dumps(expectation), encoding="utf-8")
    (git_repo / "obs.json").write_text(json.dumps(observation), encoding="utf-8")
    return ["exp.json", "--observed", "obs.json"]


def _invoke(git_repo, expectation, observation, *extra, as_of=AS_OF_CLOSED):
    args = _files(git_repo, expectation, observation)
    return _run_cli(["expected-outcome", args[0], "--observed", args[2], "--as-of", as_of,
                     *extra], git_repo)


def test_exit_zero_needs_the_target_reached_and_the_window_closed(git_repo, head_sha):
    expectation = _expectation(change=head_sha)
    observation = _observation(change=head_sha, observations=[
        _entry(value=500.0), _entry(metric="error_rate_pct", value=0.22, unit="pct")])
    reached = _invoke(git_repo, expectation, observation)
    assert reached.returncode == 0, reached.stdout + reached.stderr
    assert "achieved" in reached.stdout
    interim = _invoke(git_repo, expectation, observation, as_of=AS_OF_OPEN)
    assert interim.returncode == 1
    assert "not final" in interim.stdout


def test_a_shortfall_and_an_invalid_document_both_exit_one(git_repo, head_sha):
    short = _invoke(git_repo, _expectation(change=head_sha), _observation(change=head_sha))
    assert short.returncode == 1 and "partially-achieved" in short.stdout
    invalid = _invoke(git_repo, _expectation(change=head_sha, declared_by="inferred"),
                      _observation(change=head_sha))
    assert invalid.returncode == 1
    assert "[REJECTED]" in invalid.stderr and "conclusion cannot create" in invalid.stderr


def test_a_comparison_that_cannot_be_set_up_exits_two(git_repo, head_sha):
    result = _invoke(git_repo, _expectation(change=head_sha),
                     _observation(change=head_sha, observations=[_entry(unit="s", value=0.7)]))
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "execution-error"


def test_a_change_this_repository_does_not_hold_is_an_execution_error(git_repo):
    result = _invoke(git_repo, _expectation(change="d" * 40),
                     _observation(change="d" * 40, observations=[]))
    assert result.returncode == 2
    assert "git cannot resolve" in json.loads(result.stdout)["error"]


def test_a_key_written_twice_is_refused_by_the_one_parser(git_repo, head_sha):
    (git_repo / "obs.json").write_text(json.dumps(_observation(change=head_sha)),
                                       encoding="utf-8")
    (git_repo / "exp.json").write_text(
        json.dumps(_expectation(change=head_sha))[:-1]
        + ', "change": "' + OTHER_SHA + '"}', encoding="utf-8")
    result = _run_cli(["expected-outcome", "exp.json", "--observed", "obs.json",
                       "--as-of", AS_OF_CLOSED], git_repo)
    assert result.returncode == 2
    assert "twice" in json.loads(result.stdout)["error"]


def test_with_a_task_the_report_is_recorded_and_the_two_verdicts_stay_apart(git_repo,
                                                                           head_sha):
    created = _run_cli(["new", "measure p95", "--type", "feature", "--no-worktree"], git_repo)
    assert created.returncode == 0, created.stderr
    task = next((git_repo / ".rig" / "runs").iterdir()).name
    _run_cli(["record-outcome", task, "--status", "ok"], git_repo)
    _run_cli(["record-commit", task, head_sha], git_repo)

    result = _invoke(git_repo, _expectation(change=head_sha),
                     _observation(change=head_sha, observations=[
                         _entry(value=900.0),
                         _entry(metric="error_rate_pct", value=0.22, unit="pct")]),
                     "--task", task)
    assert result.returncode == 1, result.stdout + result.stderr
    # `record-outcome` says the run was fine; the numbers say it regressed. Both words, and
    # no third word combining them.
    assert "regressed" in result.stdout and "ok" in result.stdout

    report = recorded(git_repo / ".rig" / "runs" / task)
    assert report["schema"] == SCHEMA and report["status"] == REGRESSED
    assert report["change_cross_check"]["outcome"] == CONFIRMED
    assert report["recorded_outcome"]["status"] == "ok"
    assert report["assurance"]["final_status"]["value"] == "in-progress"
    assert report["inputs"]["expectation"].endswith("exp.json")


def test_with_a_task_that_never_ran_record_commit_the_metrics_still_decide(git_repo,
                                                                          head_sha):
    """The ordinary case: nothing in the flow runs `record-commit`, and an execution error
    here would make `--task`'s two copied fields unreachable for an ordinary task."""
    created = _run_cli(["new", "measure p95", "--type", "feature", "--no-worktree"], git_repo)
    assert created.returncode == 0, created.stderr
    task = next((git_repo / ".rig" / "runs").iterdir()).name
    result = _invoke(git_repo, _expectation(change=head_sha),
                     _observation(change=head_sha, observations=[
                         _entry(value=500.0),
                         _entry(metric="error_rate_pct", value=0.22, unit="pct")]),
                     "--task", task)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "change cross-check: unobservable" in result.stdout
    assert "record-commit" in result.stdout


def test_a_receipt_naming_a_different_change_stops_the_run(git_repo, head_sha):
    created = _run_cli(["new", "measure p95", "--type", "feature", "--no-worktree"], git_repo)
    assert created.returncode == 0, created.stderr
    task = next((git_repo / ".rig" / "runs").iterdir()).name
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "second"], cwd=git_repo,
                   check=True)
    other = subprocess.run(["git", "rev-parse", "HEAD"], cwd=git_repo, capture_output=True,
                           text=True, check=True).stdout.strip()
    _run_cli(["record-commit", task, other], git_repo)
    result = _invoke(git_repo, _expectation(change=head_sha),
                     _observation(change=head_sha), "--task", task)
    assert result.returncode == 2
    assert "disagreeing about which change" in json.loads(result.stdout)["error"]


def test_the_receipt_is_read_and_never_written(git_repo, head_sha):
    """The docstring claims the receipt carries no production_outcome block. Pinned, so the
    claim cannot quietly stop being true."""
    created = _run_cli(["new", "measure p95", "--type", "feature", "--no-worktree"], git_repo)
    assert created.returncode == 0, created.stderr
    task = next((git_repo / ".rig" / "runs").iterdir()).name
    _invoke(git_repo, _expectation(change=head_sha), _observation(change=head_sha),
            "--task", task)
    receipt = json.loads(_run_cli(["receipt", task, "--json"], git_repo).stdout)
    assert "production_outcome" not in receipt
    assert not [s for s in receipt["sources"] if RECORD_NAME in s["path"]]
