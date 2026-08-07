"""`runs --auto-route-regret`: was the cheap tier a saving or a false economy? (#357)

`learned_auto_route` already aggregates per-model pass rates to pick the *next*
route; nothing ever showed that aggregate to a human, so a cost tier chosen and
then underperforming a pricier one left no trace anyone would look at. This is
that report — recorded runs in, table out, no writes and no model calls.
"""

import json

import pytest

from rig_workbench.orchestrate.commands import (_print_auto_route_regret,
                                                collect_auto_route_regret)


def _run(recipe, step_id, model, status, routed=True, key="auto_route"):
    step = {"id": step_id, "model": model, "status": status}
    if routed:
        step[key] = {"model": model}
    return {"recipe": recipe, "steps": [step]}


def _runs(recipe, step_id, spec):
    """spec: [(model, passes, fails)] in declared (cheapest-first) order."""
    rows = []
    for model, passes, fails in spec:
        rows.extend(_run(recipe, step_id, model, "passed") for _ in range(passes))
        rows.extend(_run(recipe, step_id, model, "failed") for _ in range(fails))
    return rows


def test_no_routed_runs_reports_nothing_rather_than_erroring():
    assert collect_auto_route_regret([]) == []
    assert collect_auto_route_regret([{"recipe": "bugfix", "steps": [{"id": "implement"}]}]) == []


def test_a_step_routed_once_is_reported_with_its_counts():
    report = collect_auto_route_regret(_runs("bugfix", "implement", [("cheap", 3, 0)]))
    assert len(report) == 1
    entry = report[0]
    assert (entry["recipe"], entry["step"]) == ("bugfix", "implement")
    assert entry["models"][0] == {"model": "cheap", "n": 3, "passed": 3,
                                  "pass_rate": 1.0, "chosen": True, "rank": 0}


def test_too_few_observations_is_stated_rather_than_scored():
    """Two runs is not evidence; saying so beats implying a verdict."""
    report = collect_auto_route_regret(_runs("bugfix", "implement", [("cheap", 1, 1)]))
    assert report[0]["insufficient"] is True
    assert report[0]["regrets"] == []


def test_a_cheap_tier_losing_to_a_pricier_one_is_called_out():
    rows = _runs("bugfix", "implement", [("cheap", 2, 3), ("mid", 8, 0)])
    report = collect_auto_route_regret(rows)
    assert report[0]["regrets"] == [{"chosen": "cheap", "better": "mid"}]


def test_a_cheap_tier_that_is_doing_fine_is_not_second_guessed():
    rows = _runs("bugfix", "implement", [("cheap", 9, 1), ("mid", 5, 0)])
    assert collect_auto_route_regret(rows)[0]["regrets"] == []


def test_a_pricier_model_with_too_few_runs_cannot_convict_the_cheap_one():
    """One lucky expensive run is not grounds for calling the cheap tier a mistake."""
    rows = _runs("bugfix", "implement", [("cheap", 1, 4), ("mid", 1, 0)])
    assert collect_auto_route_regret(rows)[0]["regrets"] == []


def test_a_cheaper_model_doing_better_is_not_a_regret():
    """Regret means the saving cost something, not that the tiers differ."""
    rows = _runs("bugfix", "implement", [("cheap", 8, 0), ("mid", 2, 3)])
    assert collect_auto_route_regret(rows)[0]["regrets"] == []


def test_learned_route_records_count_the_same_as_auto_route():
    rows = [_run("bugfix", "implement", "cheap", "failed", key="learned_route")
            for _ in range(4)]
    rows += [_run("bugfix", "implement", "cheap", "passed", key="learned_route")]
    rows += _runs("bugfix", "implement", [("mid", 5, 0)])
    report = collect_auto_route_regret(rows)
    assert report[0]["regrets"] == [{"chosen": "cheap", "better": "mid"}]


def test_steps_are_reported_separately_per_recipe_and_step():
    rows = _runs("bugfix", "implement", [("cheap", 3, 0)])
    rows += _runs("feature", "implement", [("cheap", 3, 0)])
    rows += _runs("bugfix", "verify", [("cheap", 3, 0)])
    report = collect_auto_route_regret(rows)
    assert [(e["recipe"], e["step"]) for e in report] == [
        ("bugfix", "implement"), ("bugfix", "verify"), ("feature", "implement"),
    ]


def test_a_model_that_was_never_routed_to_is_shown_but_not_marked_chosen():
    rows = _runs("bugfix", "implement", [("cheap", 3, 0)])
    rows += [{"recipe": "bugfix",
              "steps": [{"id": "implement", "model": "mid", "status": "passed"}]}]
    models = {item["model"]: item["chosen"] for item in collect_auto_route_regret(rows)[0]["models"]}
    assert models == {"cheap": True, "mid": False}


def test_the_report_reads_and_writes_nothing(tmp_path, capsys):
    rows = _runs("bugfix", "implement", [("cheap", 2, 3), ("mid", 8, 0)])
    before = json.dumps(rows, sort_keys=True)
    _print_auto_route_regret(rows)
    out = capsys.readouterr().out
    assert "possible regret" in out
    assert "cheap" in out and "mid" in out
    assert json.dumps(rows, sort_keys=True) == before


def test_the_empty_report_explains_where_the_data_would_come_from(capsys):
    _print_auto_route_regret([])
    assert "auto_route" in capsys.readouterr().out


@pytest.mark.parametrize("status", ["failed", "escalated", None])
def test_only_passed_counts_as_a_pass(status):
    rows = [_run("bugfix", "implement", "cheap", status) for _ in range(4)]
    entry = collect_auto_route_regret(rows)[0]
    assert entry["models"][0]["passed"] == 0
    assert entry["models"][0]["n"] == 4
