"""Every fan-out tells the operator what it is about to do (#410).

Ten days of usage data behind v2.5.0 showed a third of sessions were security
reviews abandoned mid-exploration with zero findings: reviewers are subagents, so
nothing reached the operator until the barrier released, and a long silence is
indistinguishable from a hang. The fix was a first report — a few lines before
dispatch, from what the parent already holds.

`1232361` says it landed in four instructions. It landed in three.
`adversarial-review` fans out to two subagents through the same barrier and kept
the same silence, so the claim in the changelog was true of the intent and false
of the tree — which is the failure mode this repository keeps rediscovering.

The contract is what is pinned, not the wording: a report *before* dispatch, a
five-call ceiling on getting there, and permission to withdraw it. That last
clause is load-bearing. A preview nobody may retract becomes a verdict, and a
reviewer who has published a verdict before reading the code will defend it.
"""

import pathlib

import pytest

INSTRUCTIONS = pathlib.Path(__file__).resolve().parent.parent / "skills" / "engine" / "facets" / "instructions"

# Instructions that dispatch a fan-out of reviewer subagents and therefore owe the
# operator a first report. Adding a fan-out instruction without one fails here.
FANNING_OUT = ["parallel-review", "pr-review", "security-audit", "adversarial-review"]


@pytest.mark.parametrize("name", FANNING_OUT)
def test_a_fan_out_instruction_reports_before_it_goes_quiet(name):
    text = (INSTRUCTIONS / f"{name}.md").read_text(encoding="utf-8")
    assert "第一報" in text, (
        f"{name}.md dispatches subagents with no first report — the operator watches "
        "a silent barrier and cannot tell work from a hang."
    )


@pytest.mark.parametrize("name", FANNING_OUT)
def test_the_first_report_keeps_its_ceiling_and_its_right_to_be_wrong(name):
    """A preview that cannot be withdrawn is a verdict, and a verdict published
    before the reviewers have read anything is one its author will defend."""
    text = (INSTRUCTIONS / f"{name}.md").read_text(encoding="utf-8")
    assert "5回以内" in text, f"{name}.md states no tool-call ceiling for the first report"
    assert "撤回" in text, f"{name}.md does not say the first report may be withdrawn"
    assert "判定ではなく" in text or "Suspected" in text, (
        f"{name}.md does not mark the first report as a preview rather than a judgement")
