import json
import pathlib

import pytest


def _recipe(path: pathlib.Path, *, manual: object = None, gate: str = "custom-vote") -> pathlib.Path:
    manual_line = "" if manual is None else f"no_orchestrate: {manual}\n"
    path.write_text(
        "---\nname: preflight\ndescription: test\nscope: project\n"
        f"autonomy: interactive\n{manual_line}steps:\n"
        "  - id: vote\n    instruction: test\n"
        f"    gate: {gate}\n---\n",
        encoding="utf-8",
    )
    return path


def test_pure_preflight_distinguishes_manual_custom_invalid_and_runtime_gate():
    from rig_workbench.orchestrate.gates import (
        validate_executable_recipe, validate_executable_steps,
    )

    steps = [{"id": "vote", "gate": "custom-vote"}]
    manual = validate_executable_steps(steps, no_orchestrate=True)
    assert manual["structurally_valid"] is True
    assert manual["orchestratable"] is False
    assert manual["manual_only"] is True
    assert manual["unsupported_gates"] == [{"step": "vote", "gate": "custom-vote"}]
    invalid = validate_executable_steps(steps, no_orchestrate=False)
    assert invalid["structurally_valid"] is False
    assert "unsupported executable gate" in invalid["errors"][0]
    exact_bool = validate_executable_recipe({"no_orchestrate": "true", "steps": steps})
    assert exact_bool["structurally_valid"] is False
    assert "exact boolean" in exact_bool["errors"][0]
    known = validate_executable_steps(
        [{"id": "accept", "gate": "acceptance-gate"}], no_orchestrate=False
    )
    assert known["orchestratable"] is True and known["errors"] == []


def test_plan_json_and_text_show_manual_only_execution_without_running(tmp_path, monkeypatch, capsys):
    from rig_workbench.orchestrate import commands
    from rig_workbench.orchestrate.recipes import resolve_effective, resolve_plan_json

    recipe = _recipe(tmp_path / "manual.md", manual="true")
    plan = resolve_plan_json(recipe)
    assert plan["execution"]["structurally_valid"] is True
    assert plan["execution"]["orchestratable"] is False
    assert plan["errors"] == []
    forced = resolve_effective(recipe, ["--orchestrate"], diff_lines=0)
    assert forced["mode"]["orchestrate"] == "on"
    assert forced["execution"]["orchestratable"] is False
    nonmanual = _recipe(tmp_path / "nonmanual.md")
    sliced = resolve_effective(
        nonmanual, ["--only", "vote"], diff_lines=0
    )
    assert sliced["execution"]["orchestratable"] is False
    assert "unsupported executable gate" in sliced["errors"][0]
    manual_flag = resolve_effective(nonmanual, ["--no-orchestrate"], diff_lines=0)
    assert manual_flag["execution"]["structurally_valid"] is True
    assert manual_flag["execution"]["manual_only"] is True

    monkeypatch.setattr(commands, "resolve_recipe", lambda _name: recipe)
    commands.cmd_plan(["manual"])
    text = capsys.readouterr().out
    assert "Execution: nonexecutable" in text
    assert "custom gates are manual-only" in text or "no_orchestrate: true" in text
    commands.cmd_plan(["manual", "--json"])
    document = json.loads(capsys.readouterr().out)
    assert document["execution"]["manual_only"] is True


def test_init_run_and_ab_preflight_before_state_worktree_or_provider(
    tmp_path, monkeypatch, capsys,
):
    from rig_workbench.orchestrate import commands

    recipe = _recipe(tmp_path / "manual.md", manual="true")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(commands, "resolve_recipe", lambda _name: recipe)
    monkeypatch.setattr(
        commands, "new_state", lambda *_args, **_kwargs: pytest.fail("state created")
    )
    monkeypatch.setattr(
        commands, "setup_isolation", lambda *_args, **_kwargs: pytest.fail("worktree created")
    )
    monkeypatch.setattr(
        commands, "run_loop", lambda *_args, **_kwargs: pytest.fail("provider loop called")
    )
    for call, args in (
        (commands.cmd_init, ["manual", "--out", "blocked.json"]),
        (commands.cmd_run, ["manual", "--provider", "mock", "--out", "blocked.json", "--isolate"]),
        (commands.cmd_ab, ["one", "two", "--provider", "mock"]),
    ):
        with pytest.raises(SystemExit) as stopped:
            call(args)
        assert stopped.value.code == 2
        assert "computationally nonexecutable" in capsys.readouterr().out
    assert not (tmp_path / "blocked.json").exists()
    assert not list(tmp_path.glob("ab-*-state.json"))


def test_tampered_resume_state_blocks_before_provider_and_without_retry(
    tmp_path, monkeypatch, step_factory,
):
    from rig_workbench.orchestrate import commands, providers
    from rig_workbench.orchestrate.runstate import load_state, new_state, save_state

    path = tmp_path / "run-state.json"
    state = new_state("tampered", [step_factory(id="later")], None)
    save_state(state, path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["steps"][0]["gate"] = "custom-vote"
    path.write_text(json.dumps(document), encoding="utf-8")
    loaded = load_state(path)
    assert loaded["stopped"]["kind"] == "BLOCKED"
    monkeypatch.setattr(
        commands, "_run_checks", lambda *_args: pytest.fail("resume check executed")
    )
    monkeypatch.setattr(
        commands, "save_state", lambda *_args: pytest.fail("blocked resume wrote state")
    )
    with pytest.raises(SystemExit) as stopped:
        commands.cmd_resume([str(path)])
    assert stopped.value.code == 2
    calls = []
    monkeypatch.setattr(providers, "_execute_step", lambda *_args: calls.append("provider"))
    monkeypatch.setattr(
        providers,
        "telemetry_append",
        lambda *_args: pytest.fail("blocked preflight wrote telemetry"),
    )
    final = providers.run_loop(loaded, None, "mock", "mock", {}, 4)
    assert final == "BLOCKED" and calls == []
    assert loaded["step_state"]["later"]["retries"] == 0


def test_manual_only_provenance_blocks_known_gate_new_load_resume_and_run_loop(
    tmp_path, monkeypatch, step_factory,
):
    from rig_workbench.orchestrate import commands, providers
    from rig_workbench.orchestrate.gates import validate_executable_steps
    from rig_workbench.orchestrate.runstate import load_state, new_state, save_state

    marker = tmp_path / "SIDE_EFFECT"
    step = step_factory(
        id="accept", gate="acceptance-gate", checks=[f"touch {marker}"],
    )
    manual = validate_executable_steps([step], no_orchestrate=True)
    state = new_state("manual-known", [step], None, execution=manual)
    assert state["no_orchestrate"] is True
    assert state["execution"]["manual_only"] is True
    assert state["stopped"]["kind"] == "BLOCKED"

    # Reproduce a crafted persisted running state: execution cannot be restored
    # from its known gate alone; the exact manual-only policy is authoritative.
    state["stopped"] = None
    state["step_state"]["accept"]["status"] = "running"
    path = tmp_path / "manual-state.json"
    save_state(state, path)
    loaded = load_state(path)
    assert loaded["execution"]["manual_only"] is True
    assert loaded["stopped"]["kind"] == "BLOCKED"

    monkeypatch.setattr(
        commands, "_run_checks", lambda *_args: pytest.fail("manual check subprocess ran"),
    )
    monkeypatch.setattr(
        commands, "save_state", lambda *_args: pytest.fail("blocked resume wrote state"),
    )
    with pytest.raises(SystemExit) as stopped:
        commands.cmd_resume([str(path)])
    assert stopped.value.code == 2
    assert not marker.exists()

    monkeypatch.setattr(
        providers, "_execute_step", lambda *_args: pytest.fail("provider was called"),
    )
    monkeypatch.setattr(
        providers, "telemetry_append", lambda *_args: pytest.fail("telemetry was written"),
    )
    assert providers.run_loop(loaded, None, "mock", "mock", {}, 4) == "BLOCKED"
    assert not marker.exists()


def test_execution_policy_schema_allows_safe_legacy_and_blocks_missing_provenance(
    tmp_path, step_factory,
):
    from rig_workbench.orchestrate.runstate import load_state, new_state, save_state

    path = tmp_path / "state.json"
    state = new_state("safe", [step_factory(id="plain")], None)
    legacy = dict(state)
    legacy.pop("execution_policy_version")
    legacy.pop("no_orchestrate")
    legacy.pop("execution")
    save_state(legacy, path)
    assert load_state(path)["execution"]["orchestratable"] is True

    state.pop("no_orchestrate")
    save_state(state, path)
    blocked = load_state(path)
    assert blocked["execution"]["orchestratable"] is False
    assert "schema is incomplete" in blocked["execution"]["reason"]


@pytest.mark.parametrize(
    "keep",
    [
        {"execution_policy_version"},
        {"no_orchestrate"},
        {"execution"},
        {"execution_policy_version", "no_orchestrate"},
        {"execution_policy_version", "execution"},
        {"no_orchestrate", "execution"},
    ],
)
def test_each_partial_execution_policy_schema_is_blocked(tmp_path, step_factory, keep):
    from rig_workbench.orchestrate.runstate import load_state, new_state, save_state

    state = new_state("partial", [step_factory(id="plain")], None)
    for field in {"execution_policy_version", "no_orchestrate", "execution"} - keep:
        state.pop(field)
    path = tmp_path / "partial.json"
    save_state(state, path)
    blocked = load_state(path)
    assert blocked["execution"]["orchestratable"] is False
    assert "schema is incomplete" in blocked["execution"]["reason"]


@pytest.mark.parametrize("version", [True, 1.0, "1"])
def test_execution_policy_version_requires_exact_integer(
    tmp_path, step_factory, version,
):
    from rig_workbench.orchestrate.runstate import load_state, new_state, save_state

    state = new_state("version", [step_factory(id="plain")], None)
    state["execution_policy_version"] = version
    path = tmp_path / "version.json"
    save_state(state, path)
    blocked = load_state(path)
    assert blocked["execution"]["orchestratable"] is False
    assert "unsupported execution policy version" in blocked["execution"]["reason"]


@pytest.mark.parametrize(
    ("field", "value"),
    [("structurally_valid", 1), ("orchestratable", 1), ("manual_only", 0)],
)
def test_execution_report_rejects_bool_like_integers(
    tmp_path, step_factory, field, value,
):
    from rig_workbench.orchestrate.runstate import load_state, new_state, save_state

    state = new_state("report-types", [step_factory(id="plain")], None)
    state["execution"][field] = value
    path = tmp_path / "report-types.json"
    save_state(state, path)
    blocked = load_state(path)
    assert blocked["execution"]["orchestratable"] is False
    assert "provenance is inconsistent" in blocked["execution"]["reason"]


def test_execution_report_rejects_contradiction_and_extra_schema_field(
    tmp_path, step_factory,
):
    from rig_workbench.orchestrate.runstate import load_state, new_state, save_state

    path = tmp_path / "contradictory.json"
    for mutation in ("contradiction", "extra"):
        state = new_state("report-schema", [step_factory(id="plain")], None)
        if mutation == "contradiction":
            state["execution"]["manual_only"] = True
        else:
            state["execution"]["untrusted_extra"] = False
        save_state(state, path)
        blocked = load_state(path)
        assert blocked["execution"]["orchestratable"] is False
        assert "provenance is inconsistent" in blocked["execution"]["reason"]


def test_dag_rechecks_before_later_wave_and_never_runs_newly_unsupported_step(
    monkeypatch, step_factory,
):
    from rig_workbench.orchestrate import providers
    from rig_workbench.orchestrate.runstate import new_state

    first = step_factory(id="first")
    later = step_factory(id="later", needs=["first"])
    state = new_state("dag", [first, later], None)
    calls = []

    def execute(state_arg, step, *_args):
        calls.append(step["id"])
        if step["id"] == "first":
            state_arg["steps"][1]["gate"] = "custom-vote"

    monkeypatch.setattr(providers, "_execute_step", execute)
    monkeypatch.setattr(providers, "telemetry_append", lambda *_args: None)
    final = providers.run_loop(state, None, "mock", "mock", {}, 4)
    assert final == "BLOCKED"
    assert calls == ["first"]
    assert state["step_state"]["later"]["status"] == "pending"
    assert state["step_state"]["later"]["retries"] == 0
    assert state["stopped"]["kind"] == "BLOCKED"
