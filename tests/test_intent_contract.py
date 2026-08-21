"""What was asked for, in a shape something can check against (#435).

The module refuses rather than generates, so what these tests hold it to is the refusals:
that a claim about what a human said has to say where they said it, that a guess is never
recorded as a request, and that "nothing checked this" stays a different answer from "this
failed".
"""

import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench import intent
from rig_workbench.workbench.intent import (FAILED, PASSED, SATISFIED, SCHEMA,
                                            UNSATISFIED, UNVERIFIABLE, load,
                                            status, undeclared, unverifiable,
                                            validate)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"


def _payload(**overrides) -> dict:
    base = {
        "schema": SCHEMA,
        "goal": "worktree の中でセッションを開けるようにしたい",
        "requirements": [
            {"text": "status works from the worktree", "origin": "explicit-user",
             "source": "#471", "evidence": ["test_a_task_is_visible"]},
        ],
        "non_goals": ["generating the contract from the goal"],
        "assumptions": [],
        "ambiguities": [],
    }
    base.update(overrides)
    return base


# ── the goal is the one thing nothing else derives from ──────────────────────
def test_a_contract_without_a_goal_is_refused():
    """Everything else here is an interpretation. Without the thing being interpreted there
    is no way to ask later whether the interpretation was right."""
    problems = validate(_payload(goal=""))
    assert any("goal" in p for p in problems), problems


def test_the_goal_is_kept_verbatim():
    """A paraphrase is already a reading. Storing one would quietly replace the sentence the
    whole artifact exists to be checked against."""
    goal = "  他ユーザーの reset token は利用不可  "
    assert load(_payload(goal=goal)).goal == goal


# ── a claim about what a human said has to be checkable ──────────────────────
@pytest.mark.parametrize("origin", ["explicit-user", "policy-required"])
def test_asserting_that_someone_said_it_requires_saying_where(origin):
    """`explicit-user` is the strongest claim in this vocabulary and `policy-required` binds
    an organisation to it. Both are assertions about a third party, and an assertion about a
    third party that names no source cannot be checked by anyone — including the person it is
    attributed to."""
    problems = validate(_payload(requirements=[
        {"text": "x", "origin": origin, "source": "   "}]))
    assert any("where" in p for p in problems), problems


@pytest.mark.parametrize("origin", ["inferred", "proposed", "repository-derived"])
def test_the_origins_that_claim_nothing_about_a_person_need_no_source(origin):
    """Symmetry check, and the reason the rule above is about *assertions* rather than about
    tidiness: rig saying what it concluded is not a claim someone else made."""
    assert validate(_payload(requirements=[{"text": "x", "origin": origin}])) == []


def test_an_unknown_origin_is_refused_and_names_what_exists():
    problems = validate(_payload(requirements=[{"text": "x", "origin": "because-i-said-so"}]))
    assert any("explicit-user" in p for p in problems), problems


# ── a guess is never a request ───────────────────────────────────────────────
def test_what_rig_concluded_is_not_what_the_user_asked_for():
    """The distinction `caller.Caller.declared` draws for callers, applied to requirements.
    Acting on an inferred requirement without saying so is how a run builds something correct
    against a specification nobody gave — which looks like success from the inside."""
    contract = load(_payload(requirements=[
        {"text": "asked for", "origin": "explicit-user", "source": "#435"},
        {"text": "rig's own reading", "origin": "inferred"},
        {"text": "rig's suggestion", "origin": "proposed"},
        {"text": "read from the repo", "origin": "repository-derived"},
    ]))
    assert [r.text for r in undeclared(contract)] == [
        "rig's own reading", "rig's suggestion", "read from the repo"]
    assert contract.requirements[0].declared is True


# ── an ambiguity nobody can close is a note ──────────────────────────────────
def test_an_ambiguity_must_say_what_would_settle_it():
    problems = validate(_payload(ambiguities=[{"question": "which branch is the base?"}]))
    assert any("settle" in p for p in problems), problems


def test_an_ambiguity_without_a_question_is_refused():
    """The other half. A resolution path attached to nothing is a note about a question
    someone forgot to write down, and it closes nothing when followed."""
    problems = validate(_payload(ambiguities=[{"resolved_by": "the user names one"}]))
    assert any("question" in p for p in problems), problems


def test_an_ambiguity_with_a_resolution_path_is_accepted():
    assert validate(_payload(ambiguities=[
        {"question": "which branch is the base?",
         "resolved_by": "the user names one, or --base is passed"}])) == []


# ── nothing checked it is not the same as it failed ──────────────────────────
def test_a_requirement_with_no_evidence_is_unverifiable_not_unsatisfied():
    """The third state is not a softer second. A caller that collapses them reads "nobody
    looked" as "it failed" or, worse, the other way round."""
    contract = load(_payload(requirements=[{"text": "x", "origin": "inferred"}]))
    assert unverifiable(contract) == contract.requirements
    assert status(contract, {})["status"] == UNVERIFIABLE


def test_evidence_nobody_looked_at_does_not_condemn_the_requirement():
    """The finding a review round caught, and the module's own stated discipline: a derived
    view copies decisions rather than remaking them. Taking a set of ids that passed made
    absence mean both "ran and failed" and "never ran", so a contract whose tests had not been
    run yet reported its requirements as unsatisfied — a verdict no evidence record had given.
    """
    contract = load(_payload(requirements=[
        {"text": "a", "origin": "explicit-user", "source": "#435", "evidence": ["t1"]}]))
    assert status(contract, {})["status"] == UNVERIFIABLE
    assert status(contract, {"t1": "unobserved"})["status"] == UNVERIFIABLE
    # Only a recorded failure condemns it.
    assert status(contract, {"t1": FAILED})["status"] == UNSATISFIED


@pytest.mark.parametrize("state", ["banana", "", "PASSED", True, 1, None])
def test_a_state_outside_the_vocabulary_is_not_a_verdict(state):
    """The same manufacture the parameter was changed to stop, one level down. Reading an
    unrecognised state as `unobserved` would turn a record that says nothing into a
    plausible-looking summary — and `unobserved` is itself a claim, that someone looked at
    the ledger and found no entry."""
    contract = load(_payload(requirements=[
        {"text": "a", "origin": "inferred", "evidence": ["t1"]}]))
    with pytest.raises(ValueError, match="evidence states"):
        status(contract, {"t1": state})


def test_states_for_evidence_nobody_asked_about_are_harmless():
    """The mapping may be a superset — a caller handing over everything it recorded should
    not have to filter it down to what this contract happens to name."""
    contract = load(_payload(requirements=[
        {"text": "a", "origin": "inferred", "evidence": ["t1"]}]))
    assert status(contract, {"t1": PASSED, "t_unrelated": FAILED})["status"] == SATISFIED


@pytest.mark.parametrize("field", ["question", "resolved_by"])
def test_an_ambiguity_field_that_is_not_a_string_is_refused(field):
    """`str(...)` coercion was closed on `source` and left open here — the same hole twice,
    caught the second time by review rather than by me.

    One field at a time, because a payload with both fields wrong is still refused when only
    one of the two checks is weakened: the first version of this test broke both and could not
    see either coercion come back.
    """
    ambiguity = {"question": "which branch?", "resolved_by": "the user names one"}
    ambiguity[field] = {"x": 1} if field == "question" else 7
    assert validate(_payload(ambiguities=[ambiguity])) != [], field


def test_the_summary_counts_what_it_says_it_counts():
    """The overall verdict is one field of six, and a caller reading the other five deserves
    them to be about what their names claim. A mixed contract, because a summary where every
    requirement falls in the same bucket cannot tell a count that discriminates from one that
    returns the total."""
    contract = load(_payload(requirements=[
        {"text": "held", "origin": "explicit-user", "source": "#435", "evidence": ["t_ok"]},
        {"text": "broke", "origin": "policy-required", "source": "org", "evidence": ["t_bad"]},
        {"text": "unchecked", "origin": "inferred"},
    ], ambiguities=[{"question": "q", "resolved_by": "r"}]))
    summary = status(contract, {"t_ok": PASSED, "t_bad": FAILED})
    assert summary["requirements"] == 3
    assert summary["satisfied"] == 1
    assert summary["unsatisfied"] == 1
    assert summary["unverifiable"] == 1
    assert summary["undeclared"] == 1       # only the inferred one
    assert summary["open_ambiguities"] == 1


def test_a_failing_requirement_outranks_an_unverifiable_one():
    """Something measured and wrong is worse news than something unmeasured, and the summary
    has to lead with the worse news."""
    contract = load(_payload(requirements=[
        {"text": "checked and wrong", "origin": "explicit-user", "source": "#435",
         "evidence": ["t_failed"]},
        {"text": "nothing checks it", "origin": "inferred"},
    ]))
    assert status(contract, {"t_failed": FAILED})["status"] == UNSATISFIED


def test_everything_checked_and_holding_is_satisfied():
    contract = load(_payload(requirements=[
        {"text": "a", "origin": "explicit-user", "source": "#435", "evidence": ["t1", "t2"]}]))
    assert status(contract, {"t1": PASSED, "t2": PASSED})["status"] == SATISFIED
    # One piece still unobserved withholds it — without claiming it failed.
    assert status(contract, {"t1": PASSED})["status"] == UNVERIFIABLE
    # One recorded failure is enough, whatever the rest say.
    assert status(contract, {"t1": PASSED, "t2": FAILED})["status"] == UNSATISFIED


def test_a_contract_with_no_requirements_is_not_quietly_satisfied():
    """An empty contract satisfies every requirement it has, which is nothing — and reporting
    that as `satisfied` would let a run claim the intent was met by never writing one down."""
    assert status(load(_payload(requirements=[])), {})["status"] == UNVERIFIABLE


def test_evidence_that_names_nothing_is_refused():
    """A blank id looks like a link and resolves to no record, so a requirement carrying one
    would read as checked while nothing checks it."""
    assert validate(_payload(requirements=[
        {"text": "a", "origin": "inferred", "evidence": ["  "]}])) != []
    assert validate(_payload(requirements=[
        {"text": "a", "origin": "inferred", "evidence": [1]}])) != []


def test_a_source_that_is_not_a_string_does_not_satisfy_the_source_rule():
    """A source check written as `str(value or "").strip()` accepts any object whose `repr`
    is non-empty, so the strongest claim in the vocabulary becomes unguarded by anything.

    The value has to be *truthy* to show it: an empty dict is falsy and gets refused either
    way, which is why the first version of this test passed against the very coercion it was
    written to catch."""
    for source in ({"issue": 435}, 435, ["#435"]):
        assert validate(_payload(requirements=[
            {"text": "a", "origin": "explicit-user", "source": source}])) != [], source


# ── every problem, not the first ─────────────────────────────────────────────
def test_all_the_problems_are_reported_at_once():
    """An author who fixes one and is refused for the next learns nothing from the second
    refusal that the first could not have told them."""
    problems = validate({"schema": "wrong", "goal": "",
                         "requirements": [{"origin": "invented"}]})
    assert len(problems) >= 4, problems
    assert any("schema" in p for p in problems)
    assert any("goal" in p for p in problems)


def test_a_broken_requirements_list_does_not_hide_the_fields_after_it():
    """The promise above, held at the one place it used to break. `requirements` failing its
    type check returned immediately, so an author with two problems saw one, fixed it, and
    was refused again — from the function that exists to stop precisely that."""
    problems = validate({"schema": SCHEMA, "goal": "g", "requirements": "not a list",
                         "non_goals": 7, "ambiguities": [{"question": "q"}]})
    assert any("requirements" in p for p in problems), problems
    assert any("non_goals" in p for p in problems), problems
    assert any("ambiguities" in p for p in problems), problems


# ── it does not generate ─────────────────────────────────────────────────────
def test_the_module_reaches_for_no_model_and_no_process():
    """Turning a sentence into requirements is reading, judging and deciding — an agent's
    work. A module that called a model to do it would leave nothing a gate could check and
    nothing a mutation could falsify, which is the whole reason this one only validates."""
    source = pathlib.Path(intent.__file__).read_text(encoding="utf-8")
    for token in ("subprocess", "requests", "urllib", "openai", "anthropic", "providers",
                  "completion", "prompt"):
        assert token not in source.replace("# ", ""), token


# ── the command exits with the status ────────────────────────────────────────
def _run(payload, tmp_path, *flags):
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return subprocess.run([sys.executable, str(WORKBENCH), "intent", str(path), *flags],
                          capture_output=True, text=True, timeout=60)


def test_a_valid_contract_exits_zero_and_says_what_is_open(tmp_path):
    """With one requirement of each kind, because a probe where every requirement looks the
    same cannot tell a count that discriminates from one that does not.

    The command reports *structure* — how much of the intent has anything checking it, and
    how much is still rig's own reading. It deliberately does not report `status()`: that
    needs a record of what each piece of evidence did, and this command runs nothing. Asking
    it anyway returned every evidenced requirement as `unverifiable`, which was true of that
    moment and false as a description of the contract — a requirement naming a test was
    reported as naming none.
    """
    result = _run(_payload(requirements=[
        {"text": "checked", "origin": "explicit-user", "source": "#435", "evidence": ["t1"]},
        {"text": "nothing checks it", "origin": "inferred"},
    ]), tmp_path, "--json")
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["requirements"] == 2
    assert summary["unchecked"] == 1, summary
    assert summary["undeclared"] == 1, summary


def test_the_command_reports_no_verdict_it_did_not_measure(tmp_path):
    """`status` on the summary is about the document, not about the work. A `satisfied` or
    `unsatisfied` here would be a verdict on evidence this command never looked at."""
    result = _run(_payload(), tmp_path, "--json")
    summary = json.loads(result.stdout)
    assert summary["status"] == "valid"
    assert "satisfied" not in summary and "unsatisfied" not in summary


def test_a_refused_contract_exits_nonzero(tmp_path):
    """The dispatcher calls subcommands for their effect and discards what they return, so a
    refusal that merely returned `1` would print its reasons and leave the shell believing the
    contract was fine."""
    result = _run(_payload(goal=""), tmp_path)
    assert result.returncode == 1, (result.returncode, result.stdout)
    assert "REJECTED" in result.stderr


def test_a_file_that_cannot_be_read_is_its_own_status(tmp_path):
    """Distinct from `invalid`: a caller told "not a contract" would go looking for a mistake
    in a document it never managed to open."""
    result = subprocess.run(
        [sys.executable, str(WORKBENCH), "intent", str(tmp_path / "absent.json")],
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 2
    assert "execution-error" in result.stdout


def test_the_round_trip_keeps_what_was_written(tmp_path):
    payload = _payload(ambiguities=[{"question": "q", "resolved_by": "r"}])
    assert load(payload).as_dict() == payload
