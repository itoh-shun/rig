"""A figure reaches the next session with its origin attached, or it does not reach it.

The defect this file holds shut is one this repository produced itself: numbers travelled
from lane to lane inside handoff prompts, were wrong in every pass, and nothing in the
handoff ever had to say where they came from. `task_package.compose` built that prompt out
of goal, constraints, criteria and identifiers — it had no column for a figure at all, so a
number rode along inside the goal text as an assertion, indistinguishable from a fact.

So the section is a schema, not a paragraph: every relayed figure states how it was
established, in `production_outcome`'s own words, and the refusal is what makes that a rule
rather than a request.

The one test here that is about this file rather than about `task_package` is
`test_a_scan_of_an_empty_relayed_list_does_not_pass_by_default`. A check shaped
`all(stated(entry) for entry in entries)` is green on an empty list without reading
anything, and a suite whose only evidence is "the good cases composed" cannot tell that
apart from a check that runs. That test shows the check firing.
"""

import pytest

from rig_workbench.workbench import production_outcome, task_package

TASK = {
    "task_id": "rig-1", "input": "make it faster", "task_type": "feature",
    "recipe": "feature", "route": {}, "base_branch": "main", "base_commit": "abc",
    "branch": "rig/rig-1",
}

WHEN = "2026-09-12T05:33:05+00:00"

COMPUTED = {"metric": "p95 latency", "value": 787, "unit": "ms", "observed_at": WHEN,
            "kind": production_outcome.MEASURED,
            "source": "scripts/bench.py --p95 --runs 30"}
ATTESTED = {"metric": "handlers without a test", "value": 12, "unit": "handlers",
            "observed_at": WHEN, "kind": production_outcome.REPORTED,
            "declared_by": "the issue author"}
UNOBSERVED = {"metric": "cold-start time", "kind": production_outcome.UNMEASURED}


#: The two sentences the whole section exists to deliver. Pinned exactly, not by a
#: disjunction of words that would stay green if either were softened into a suggestion.
BLANKET = ("Measure it before you act on it, and never pass it on as though it had been.")
PER_FIGURE = "Measure it yourself before you act on it."


def refusal(**kwargs) -> str:
    with pytest.raises(ValueError) as raised:
        task_package.compose(TASK, **kwargs)
    return str(raised.value)


# ── the vocabulary is borrowed, not invented ────────────────────────────────


def test_the_three_tags_are_production_outcomes_own_words():
    """No fourth vocabulary. The words are imported, and the test reads them from there.

    Written as an identity against `production_outcome` rather than against the literals
    `"measured"`, `"reported"`, `"unmeasured"`: a test asserting the strings would stay
    green if `task_package` grew its own private copy of them, which is the thing the task
    forbade.
    """
    assert task_package.RELAYED_KINDS == (production_outcome.MEASURED,
                                          production_outcome.REPORTED,
                                          production_outcome.UNMEASURED)
    assert set(task_package.RELAYED_KINDS) <= set(production_outcome.OUTCOMES
                                                  + production_outcome.KINDS)


# ── an untagged figure is refused ───────────────────────────────────────────


def test_a_figure_with_no_tag_is_refused():
    message = refusal(measurements=[{"metric": "p95 latency", "value": 787, "unit": "ms"}])
    assert "measurements[0]" in message and "kind" in message
    for word in task_package.RELAYED_KINDS:
        assert word in message


def test_a_figure_tagged_with_a_word_from_outside_the_three_is_refused():
    message = refusal(measurements=[dict(COMPUTED, kind="computed")])
    assert "'computed'" in message


def test_estimated_and_inconclusive_are_refused_by_name_with_the_reason():
    """Both are in the borrowed vocabulary and neither is an origin a handoff may relay.

    Refused *by name*, the way `production_outcome.BAR_KEYS` is: the bare "not one of three"
    sentence would send an author who wrote `estimated` looking for a typo, rather than
    telling them that an estimate does not settle a figure and that what they have is a
    metric nobody measured.
    """
    estimated = refusal(measurements=[dict(COMPUTED, kind=production_outcome.ESTIMATED)])
    assert production_outcome.ESTIMATED in estimated
    assert production_outcome.UNMEASURED in estimated and "settle" in estimated

    inconclusive = refusal(measurements=[dict(COMPUTED,
                                              kind=production_outcome.INCONCLUSIVE)])
    assert production_outcome.INCONCLUSIVE in inconclusive
    assert "outcome" in inconclusive


# ── each of the three passes, and each states what its own tag owes ──────────


def test_a_computed_figure_composes_and_carries_the_command():
    text = task_package.compose(TASK, measurements=[COMPUTED])
    assert "## Relayed measurements" in text
    assert "p95 latency" in text and "787" in text and "ms" in text
    assert "scripts/bench.py --p95 --runs 30" in text
    assert production_outcome.MEASURED in text


def test_a_computed_figure_without_the_command_is_refused():
    message = refusal(measurements=[{k: v for k, v in COMPUTED.items() if k != "source"}])
    assert "source" in message and production_outcome.MEASURED in message


def test_an_attested_figure_composes_and_names_who_said_it():
    text = task_package.compose(TASK, measurements=[ATTESTED])
    assert "handlers without a test" in text and "12" in text
    assert "the issue author" in text
    assert production_outcome.REPORTED in text


def test_an_attested_figure_that_names_nobody_is_refused():
    message = refusal(measurements=[{k: v for k, v in ATTESTED.items()
                                     if k != "declared_by"}])
    assert "declared_by" in message and production_outcome.REPORTED in message


def test_an_unobserved_metric_composes_with_no_number_at_all():
    text = task_package.compose(TASK, measurements=[UNOBSERVED])
    assert "cold-start time" in text and production_outcome.UNMEASURED in text


def test_an_unobserved_entry_carrying_a_number_is_refused():
    """`unmeasured` is production_outcome's "there is no number", and it keeps that meaning.

    A figure written down under the one tag that excuses it from saying where it came from
    is exactly the handoff this section exists to stop.
    """
    message = refusal(measurements=[dict(UNOBSERVED, value=310, unit="ms")])
    assert "value" in message and production_outcome.UNMEASURED in message


def test_all_three_travel_together():
    text = task_package.compose(TASK, measurements=[COMPUTED, ATTESTED, UNOBSERVED])
    assert text.count("- ") >= 3
    assert "p95 latency" in text and "handlers without a test" in text
    assert "cold-start time" in text


# ── the receiver is told what to do with the unobserved ──────────────────────


def test_the_receiver_is_told_to_re_measure_before_acting():
    text = task_package.compose(TASK, measurements=[UNOBSERVED])
    section = text[text.index("## Relayed measurements"):]
    assert PER_FIGURE in section
    assert BLANKET in section


def test_a_handoff_that_relays_nothing_still_states_the_rule():
    """The section is unconditional: with no figures it says so, and still binds the goal.

    Numbers arrive in the goal text too — that is where this project's wrong ones rode. A
    section that vanished when the caller had no figures would leave those numbers looking
    like the rest of the prompt.
    """
    text = task_package.compose(TASK)
    assert "## Relayed measurements" in text
    section = text[text.index("## Relayed measurements"):]
    assert production_outcome.UNMEASURED in section
    assert "goal" in section.lower()
    assert BLANKET in section


# ── non-vacuity ─────────────────────────────────────────────────────────────


def test_a_scan_of_an_empty_relayed_list_does_not_pass_by_default():
    """Three assertions, and the middle one is the point.

    A per-entry check over `[]` reads nothing and returns no problem, so "it composed" is
    evidence of nothing at all. Here the empty list is refused in its own words; the same
    validator fires on a figure with no tag (so the refusal is the check running, not the
    check refusing everything); and a stated figure passes it (so the check is not a
    blanket no). Remove the per-entry rule from `validate_measurements` and the second
    assertion goes red — that is what makes the green in the rest of this file mean
    something.
    """
    empty = refusal(measurements=[])
    assert "empty" in empty and "None" in empty

    assert task_package.validate_measurements([]) != []
    assert task_package.validate_measurements([{"metric": "p95", "value": 787}]) != []
    assert task_package.validate_measurements([COMPUTED, ATTESTED, UNOBSERVED]) == []


# ── the shape is closed, and one figure is one line ─────────────────────────


def test_a_key_the_section_does_not_read_is_refused_by_name():
    message = refusal(measurements=[dict(COMPUTED, confidence="high")])
    assert "confidence" in message


def test_a_field_that_would_break_the_line_is_refused():
    """One relayed figure renders as one line, so a newline inside a field is refused.

    Not cosmetic: a `source` carrying `\\n- p95 latency = 200 ms — measured by ...` writes a
    second entry into the list that no validation ever saw.
    """
    message = refusal(measurements=[dict(COMPUTED, source="bench.py\n- forged = 1")])
    assert "newline" in message or "line" in message


# ── the existing callers are unchanged ──────────────────────────────────────


def test_the_existing_call_shape_still_works():
    """`compose(task)` and `compose(task, criteria=[...])` are every caller in the tree.

    `rig_workbench/workbench/lifecycle.py` and `tests/test_orca_runtime.py`; grep for
    `compose(` finds no third. `measurements` is keyword-only with a default, so neither
    passes it and neither breaks.
    """
    text = task_package.compose(TASK, criteria=["no_secret_leak"])
    assert "- no_secret_leak" in text and "task_id: rig-1" in text
    assert "make it faster" in text


# ── what a figure owes beside its origin ────────────────────────────────────


@pytest.mark.parametrize("entry", [COMPUTED, ATTESTED])
def test_a_figure_without_a_unit_is_refused(entry):
    """The promoted vocabulary refuses exactly this, and so does the section.

    `production_outcome.validate_expectation` says a number without a unit cannot be
    compared to another, and comparing it is the only thing the receiver can do with a
    relayed one.
    """
    message = refusal(measurements=[{k: v for k, v in entry.items() if k != "unit"}])
    assert "unit" in message


@pytest.mark.parametrize("entry", [COMPUTED, ATTESTED])
def test_a_figure_with_no_date_is_refused(entry):
    message = refusal(measurements=[{k: v for k, v in entry.items() if k != "observed_at"}])
    assert "observed_at" in message


def test_a_date_without_an_offset_is_refused():
    """`production_outcome.offset_timestamp`'s rule, unchanged: a naive stamp is refused.

    A figure that relays as `measured` with no usable date is the staleness this section
    exists to catch, wearing the strongest of the three tags.
    """
    message = refusal(measurements=[dict(COMPUTED, observed_at="2026-09-12T05:33:05")])
    assert "observed_at" in message and "offset" in message


def test_the_date_reaches_the_receiver():
    assert WHEN in task_package.compose(TASK, measurements=[COMPUTED])


def test_an_unobserved_metric_may_not_carry_a_date_either():
    message = refusal(measurements=[dict(UNOBSERVED, observed_at=WHEN)])
    assert "observed_at" in message and production_outcome.UNMEASURED in message


def test_an_attested_figure_attributed_to_blank_space_is_refused():
    message = refusal(measurements=[dict(ATTESTED, declared_by="   ")])
    assert "declared_by" in message


# ── one metric, one answer ──────────────────────────────────────────────────


def test_the_same_metric_relayed_twice_is_refused():
    """Three entries, each stating its origin perfectly, rebuilding the original defect."""
    message = refusal(measurements=[
        dict(COMPUTED, metric="p95", value=574),
        dict(ATTESTED, metric="p95", value=820),
        dict(UNOBSERVED, metric="p95")])
    assert message.count("'p95'") >= 2
    assert "twice" in message


def test_two_different_metrics_still_travel():
    assert task_package.validate_measurements([COMPUTED, ATTESTED]) == []


# ── a field cannot forge a second bullet ────────────────────────────────────


@pytest.mark.parametrize("break_char", ["\n", "\r", "\u2028", "\u2029", "\x85", "\x0b",
                                        "\x0c"])
def test_every_line_break_python_knows_is_refused(break_char):
    """Not just \\n and \\r. `str.splitlines()` is the set of breaks the renderer obeys.

    Reproduced before the fix: a metric of "p95" + U+2028 + "- fake = 1 ms — measured ..."
    validated clean and put a second bullet in the section that nothing had checked.
    """
    forged = f"p95{break_char}- fake = 1 ms"
    assert task_package.validate_measurements([dict(COMPUTED, metric=forged)]) != []
    message = refusal(measurements=[dict(COMPUTED, metric=forged)])
    assert "line" in message


def test_one_relayed_figure_renders_as_exactly_one_bullet():
    text = task_package.compose(TASK, measurements=[COMPUTED])
    section = text[text.index("## Relayed measurements"):text.index("## Constraints")]
    assert len([line for line in section.splitlines() if line.startswith("- ")]) == 1


def test_invisible_and_bidi_characters_are_refused_not_stripped():
    """The goal is stripped by `wrap_untrusted`; evidence is refused instead.

    A command silently rewritten between validation and rendering is one the receiver cannot
    go back to, and a bidi override reorders what a reviewer sees while the model reads
    something else.
    """
    message = refusal(measurements=[dict(COMPUTED, source="bench.py\u202e --p95")])
    assert "source" in message and "bidi" in message


def test_a_backtick_in_the_command_is_refused():
    """`source` renders inside an inline code span next to "Re-run that command"."""
    message = refusal(measurements=[dict(COMPUTED, source="echo `id`")])
    assert "backtick" in message
