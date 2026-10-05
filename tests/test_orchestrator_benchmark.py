"""Benchmark journals count each dispatch attempt once across the execution bridge."""
import json

import pytest

from rig_workbench.orchestrate import providers, runstate
from rig_workbench.orchestrate.orchestrators.base import AgentNotStarted
from rig_workbench.orchestrate.orchestrators.bridge import (
    AgentExecutionBridge, SaveCoordinator, selection_record,
)


@pytest.mark.parametrize("fallback,expected_calls", [(False, 1), (True, 2)])
def test_benchmark_records_each_native_or_prestart_fallback_attempt_once(
    tmp_path, monkeypatch, fallback, expected_calls,
):
    counter = tmp_path / "provider-calls.jsonl"
    monkeypatch.setenv("RIG_BENCH_CALL_COUNTER", str(counter))
    monkeypatch.delenv("RIG_BENCH_MOCK_SCENARIO", raising=False)
    state = runstate.new_state("benchmark", [{"id": "implement", "instruction": "work", "gate": None}], "goal")
    attempts = []
    dispatches = []

    class RejectingOrchestrator:
        name = "t3"

        def capabilities(self):
            return frozenset({"agent.run"})

        def spawn_agent(self, task, agent):
            attempts.append(task.invocation_id)
            assert len(counter.read_text().splitlines()) == 1
            raise AgentNotStarted("launch_rejected")

    if fallback:
        state["orchestrator"] = selection_record("t3", preferred="auto", fallback="native",
                                                  ref={"t3": {"threads": {}}})
        bridge = AgentExecutionBridge(RejectingOrchestrator(), coordinator=SaveCoordinator(
            state, tmp_path / "state.json", runstate.save_state))
    else:
        bridge = AgentExecutionBridge()

    run_subprocess = providers.SUBPROCESS.run

    def native_subprocess(argv, **kwargs):
        dispatches.append(argv)
        assert len(counter.read_text().splitlines()) == expected_calls
        return run_subprocess(argv, **kwargs)

    monkeypatch.setattr(providers.SUBPROCESS, "run", native_subprocess)
    returncode, output = providers.run_provider(
        "mock", "generator", "prompt", {"_orchestrator_bridge": bridge},
        persona="implementer", state=state, step_id="implement",
    )
    assert returncode == 0 and "STATUS: done" in output
    records = [json.loads(line) for line in counter.read_text().splitlines()]
    assert len(records) == expected_calls
    assert len(attempts) == int(fallback) and len(dispatches) == 1
    assert all({key: record[key] for key in ("provider", "role", "persona", "step_id")} == {
        "provider": "mock", "role": "generator", "persona": "implementer", "step_id": "implement",
    } for record in records)
