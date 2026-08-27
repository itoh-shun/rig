import json
import pathlib
import sys

import pytest

from rig_workbench.orchestrate.recipes import resolve_effective
from rig_workbench.packs.resolver import resolve_asset
from rig_workbench.workbench import cli
from rig_workbench.workbench import compose_options as composition
from rig_workbench.workbench.capabilities import resolve_task_route


ROOT = pathlib.Path(__file__).resolve().parents[1]


def _run(monkeypatch, capsys, *arguments):
    monkeypatch.setattr(sys, "argv", ["rig-wb", "compose-options", *arguments])
    cli.main()
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("task_type", ["bugfix", "feature", "review", "documentation"])
def test_default_answers_are_the_existing_automatic_route(monkeypatch, capsys, task_type):
    """Accepting every recommendation must not create a second automatic resolver."""
    result = _run(monkeypatch, capsys, "--type", task_type, "--diff", "37", "--json")

    route = resolve_task_route(task_type, {}, ROOT, shared=ROOT)
    recipe = resolve_asset("recipe", route["recipe"], project=ROOT, shared=ROOT)
    assert recipe is not None
    resolved = resolve_effective(recipe.path, diff_lines=37, manifest={})

    recommended = {axis["id"]: axis["recommended"] for axis in result["axes"]}
    gates = [step.get("gate") for step in resolved["steps"] if step["active"]]
    expected_gate = ("acceptance-gate" if "acceptance-gate" in gates else
                     "review-gate-only" if "review-gate" in gates else "auto")
    expected_backend = ("orchestrate" if resolved["mode"]["orchestrate"] != "off" else
                        resolved["mode"]["backend"])

    assert recommended == {
        "recipe": route["recipe"],
        "step": resolved["effective_steps"],
        "gate": expected_gate,
        "backend": expected_backend,
        "mode": "autonomous" if resolved["mode"]["autonomy"] == "autonomous" else "gated",
    }


@pytest.mark.parametrize("break_document, expected", [
    (lambda doc: doc["axes"][0].update(candidates=[]), "empty"),
    (lambda doc: doc["axes"][0].update(recommended="not-a-candidate"), "unresolved"),
    (lambda doc: doc["axes"][0].update(candidates="bugfix"), "expected list"),
    (lambda doc: doc["axes"].pop(), "missing"),
])
def test_each_invalid_axis_shape_is_rejected_with_an_accepted_control(
        monkeypatch, capsys, break_document, expected):
    valid = _run(monkeypatch, capsys, "--type", "bugfix", "--diff", "37", "--json")
    assert composition.validate_options(valid) == [], "the negative control has no accepted control"

    break_document(valid)
    assert any(expected in problem for problem in composition.validate_options(valid))


def test_command_refuses_when_its_closed_contract_validation_fails(monkeypatch, capsys):
    monkeypatch.setattr(composition, "validate_options", lambda document: ["axes: missing: mode"])
    with pytest.raises(SystemExit) as stopped:
        _run(monkeypatch, capsys, "--type", "bugfix", "--json")
    assert stopped.value.code == 1
    assert "missing: mode" in capsys.readouterr().out


def test_a_recipe_that_auto_enables_orchestrate_recommends_it_as_the_backend(monkeypatch):
    """The `orchestrate` branch of the backend recommendation, which no task_type reaches.

    `resolve_task_route` sends every shipped task_type to a recipe whose orchestrate
    resolves to `off`, so the parity test above answers `manual` either way: replacing
    the whole expression with `resolved["mode"]["backend"]` leaves all nine green
    (measured). `max-bugfix` declares checks and so auto-enables orchestrate, which is
    the case that tells the two branches apart.
    """
    recipe = resolve_asset("recipe", "max-bugfix", project=ROOT, shared=ROOT)
    assert recipe is not None
    resolved = resolve_effective(recipe.path, diff_lines=37, manifest={})
    assert resolved["mode"]["orchestrate"] != "off", (
        "the fixture no longer exercises the branch this test exists for")

    monkeypatch.setattr(composition, "resolve_task_route",
                        lambda *a, **k: {"recipe": "max-bugfix", "status": "ready",
                                         "reason": "pinned by this test"})
    document = composition.compose_options("bugfix", 37, ROOT, ROOT)
    assert composition.validate_options(document) == []
    backend = next(axis for axis in document["axes"] if axis["id"] == "backend")
    assert backend["recommended"] == "orchestrate"
    assert "orchestrate" in backend["recommendation_reason"]
