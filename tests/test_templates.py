"""The shipped operator templates must stay valid and stay consistent with what reads them.

A template that drifts from the thing consuming it is worse than no template: it looks
applied. These tests check that each one parses, carries the fields it exists for, and
agrees with its consumer — hostcheck's env marker, and the inputs action.yml declares.
"""
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEVCONTAINER = ROOT / "docs/templates/devcontainer.json"
SCHEDULED = ROOT / "docs/templates/rig-scheduled.yml"


def _yaml():
    return pytest.importorskip("yaml", reason="pyyaml is a CI dependency")


def test_devcontainer_template_is_valid_json_with_an_image():
    data = json.loads(DEVCONTAINER.read_text(encoding="utf-8"))
    assert data["name"] == "rig"
    assert data["image"]


def test_devcontainer_template_declares_the_marker_hostcheck_looks_for():
    """hostcheck reads DEVCONTAINER from the environment; the template must set it."""
    from rig_workbench import hostcheck

    data = json.loads(DEVCONTAINER.read_text(encoding="utf-8"))
    assert data["remoteEnv"]["DEVCONTAINER"] == "true"
    assert "DEVCONTAINER" in hostcheck.CONTAINER_ENV_VARS


def test_scheduled_workflow_is_valid_yaml_with_a_schedule_and_manual_trigger():
    yaml = _yaml()
    data = yaml.safe_load(SCHEDULED.read_text(encoding="utf-8"))
    # PyYAML parses the unquoted `on:` key as the boolean True.
    triggers = data[True] if True in data else data["on"]
    assert triggers["schedule"][0]["cron"]
    assert "workflow_dispatch" in triggers


def test_scheduled_workflow_only_passes_inputs_the_action_declares():
    yaml = _yaml()
    action = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
    workflow = yaml.safe_load(SCHEDULED.read_text(encoding="utf-8"))
    step = next(
        s for s in workflow["jobs"]["run"]["steps"]
        if str(s.get("uses", "")).startswith("itoh-shun/rig")
    )
    unknown = set(step["with"]) - set(action["inputs"])
    assert unknown == set(), f"workflow passes inputs action.yml does not declare: {unknown}"


def test_scheduled_workflow_does_not_open_pull_requests_by_default():
    """A scheduled run that opens PRs before anyone has read a run log is a bad default."""
    yaml = _yaml()
    workflow = yaml.safe_load(SCHEDULED.read_text(encoding="utf-8"))
    step = next(
        s for s in workflow["jobs"]["run"]["steps"]
        if str(s.get("uses", "")).startswith("itoh-shun/rig")
    )
    assert step["with"]["auto_pr"] == "false"


def test_scheduled_workflow_serialises_overlapping_runs():
    yaml = _yaml()
    workflow = yaml.safe_load(SCHEDULED.read_text(encoding="utf-8"))
    assert workflow["concurrency"]["group"]
    assert workflow["jobs"]["run"]["timeout-minutes"] > 0
