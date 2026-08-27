"""User-configurable provider timeout for ``orchestrate run`` (#525)."""

import json
import subprocess
import sys
import urllib.request

import pytest

from rig_workbench.orchestrate import commands
from rig_workbench.orchestrate import providers
from rig_workbench.orchestrate import config
from rig_workbench.orchestrate.recipes import load_steps
from rig_workbench.orchestrate.runstate import new_state


def _recipe(write_recipe):
    return write_recipe(
        "timeout-flow",
        """---
name: timeout-flow
steps:
  - id: implement
    instruction: implement
---
""",
    )


def test_timeout_requires_a_value_and_positive_control_is_accepted(
    write_recipe, monkeypatch, tmp_path, capsys
):
    recipe = _recipe(write_recipe)
    seen = []
    monkeypatch.setattr(
        commands,
        "run_loop",
        lambda state, out, gen, ver, cfg, max_steps, **kwargs: seen.append(cfg.copy())
        or "DONE",
    )

    with pytest.raises(SystemExit) as refused:
        commands.cmd_run([str(recipe), "--provider", "mock", "--timeout"])

    assert refused.value.code != 0
    assert "--timeout" in capsys.readouterr().out
    assert seen == []

    with pytest.raises(SystemExit) as accepted:
        commands.cmd_run([
            str(recipe), "--provider", "mock", "--timeout", "1",
            "--out", str(tmp_path / "state.json"),
        ])

    assert accepted.value.code == 0
    assert seen[-1]["timeout"] == 1


@pytest.mark.parametrize("invalid", ["", "many", "0", "-1"])
def test_timeout_rejects_invalid_value_and_positive_control_is_accepted(
    invalid, write_recipe, monkeypatch, tmp_path, capsys
):
    recipe = _recipe(write_recipe)
    seen = []
    monkeypatch.setattr(
        commands,
        "run_loop",
        lambda state, out, gen, ver, cfg, max_steps, **kwargs: seen.append(cfg.copy())
        or "DONE",
    )

    with pytest.raises(SystemExit) as refused:
        commands.cmd_run([
            str(recipe), "--provider", "mock", "--timeout", invalid,
            "--out", str(tmp_path / "refused.json"),
        ])

    assert refused.value.code != 0
    assert "positive integer" in capsys.readouterr().out
    assert seen == []

    with pytest.raises(SystemExit) as accepted:
        commands.cmd_run([
            str(recipe), "--provider", "mock", "--timeout", "2",
            "--out", str(tmp_path / "accepted.json"),
        ])

    assert accepted.value.code == 0
    assert seen[-1]["timeout"] == 2


@pytest.mark.parametrize("cfg, expected", [({}, 600), ({"timeout": 7}, 7)])
def test_cli_provider_uses_configured_timeout_and_preserves_default(
    cfg, expected, monkeypatch
):
    seen = []

    def completed(argv, **kwargs):
        seen.append(kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 0, "STATUS: done", "")

    monkeypatch.setattr(providers.subprocess, "run", completed)

    assert providers.run_provider("mock", "generator", "work", cfg)[0] == 0
    assert seen == [expected]


@pytest.mark.parametrize("cfg, expected", [({}, 600), ({"timeout": 7}, 7)])
def test_http_provider_uses_configured_timeout_and_preserves_default(
    cfg, expected, monkeypatch
):
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": "STATUS: done"}}]
            }).encode()

    def urlopen(_request, timeout):
        seen.append(timeout)
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    assert providers.run_http_provider("ollama", "work", cfg)[0] == 0
    assert seen == [expected]


def test_real_short_cli_timeout_stops_with_duration(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(config, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(config, "GLOBAL_RUNS_PATH", tmp_path / "global-runs.jsonl")
    steps = load_steps({"steps": [{"id": "implement", "instruction": "work"}]})
    state = new_state("timeout-flow", steps, None)
    sleeper = f'{sys.executable} -c "import time; time.sleep(2)"'

    final = providers.run_loop(
        state,
        tmp_path / "state.json",
        "cmd",
        "cmd",
        {"provider_cmd": sleeper, "timeout": 1},
        1,
        quiet=True,
    )

    assert final == "BLOCKED"
    assert state["stopped"]["reason"] == "provider timed out after 1 seconds"


def test_http_timeout_reports_configured_duration(monkeypatch):
    def timed_out(_request, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", timed_out)

    rc, output = providers.run_http_provider("ollama", "work", {"timeout": 3})

    assert rc == 124
    assert output == "[provider timed out after 3 seconds]"


def test_anthropic_http_timeout_reports_configured_duration(monkeypatch):
    def timed_out(_request, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", timed_out)

    rc, output = providers.run_anthropic_provider("work", {"timeout": 4})

    assert rc == 124
    assert output == "[provider timed out after 4 seconds]"
