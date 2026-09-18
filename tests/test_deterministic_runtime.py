"""Runner tests use a controlled I/O port; separate StrictIO tests prove confinement."""
import copy
import hashlib
import json
from pathlib import Path
from subprocess import CompletedProcess
from contextlib import nullcontext

import pytest

from rig_workbench.orchestrate import deterministic_runtime as runtime


class FakeIO:
    saved = {}
    calls = []
    content = "initial"
    failing = 0
    bad_diagnosis = False
    interrupt = None

    def __init__(self, workspace, state_path):
        self.workspace, self.state_path = Path(workspace), Path(state_path)

    def preflight(self):
        pass

    def locked(self):
        return nullcontext()

    def snapshot(self):
        return hashlib.sha256(type(self).content.encode()).hexdigest()

    def save(self, state):
        type(self).saved[str(self.state_path)] = copy.deepcopy(state)

    def load(self):
        if str(self.state_path) not in type(self).saved:
            raise FileNotFoundError(self.state_path)
        return copy.deepcopy(type(self).saved[str(self.state_path)])

    def run(self, argv, input=None, timeout=600, writable=True, network=False):
        cls = type(self)
        request = json.loads(input) if input else {"operation": "CHECK"}
        op = request["operation"]
        cls.calls.append((op, writable, copy.deepcopy(self.load())))
        if cls.interrupt == op:
            raise KeyboardInterrupt()
        if op == "CHECK":
            if cls.failing > 0:
                cls.failing -= 1
                return CompletedProcess(argv, 1, "actual check failed", "")
            return CompletedProcess(argv, 0, "actual check passed", "")
        if op in ("DIAGNOSE", "REPLAN"):
            ids = request["failed_check_ids"]
            output = {"failure_id": request["failure_id"], "failed_check_ids": ids,
                      "hypothesis": "The assertion exposes the missing branch",
                      "changes": ["Implement missing branch"], "verification_checks": ids}
            if op == "REPLAN":
                output["plan"] = "New plan " + str(len(cls.calls))
            if cls.bad_diagnosis:
                output["verification_checks"] = []
            return CompletedProcess(argv, 0, json.dumps(output), "")
        if op in ("VERIFY", "FINAL_VERIFY"):
            return CompletedProcess(argv, 0, json.dumps({"status": "PASS", "criteria": [
                {"id": cid, "status": "PASS"} for cid in request["criteria_ids"]]}), "")
        cls.content += " changed"
        return CompletedProcess(argv, 0, "implemented", "")


@pytest.fixture
def prepared(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "StrictIO", FakeIO)
    monkeypatch.setattr(runtime, "_load_state", lambda path: copy.deepcopy(FakeIO.saved[str(path)]))
    FakeIO.saved, FakeIO.calls, FakeIO.content = {}, [], "initial"
    FakeIO.failing, FakeIO.bad_diagnosis, FakeIO.interrupt = 0, False, None
    workspace = tmp_path / "repo"
    workspace.mkdir()
    state_path = tmp_path / "private" / "run.json"
    state = {"run_id": "run-test", "goal": "Fix branch", "recipe": "test", "steps": [
        {"id": "implement", "instruction": "Implement", "checks": ["test actual"],
         "gate": None, "needs": [], "executor": "generate", "acceptance": []}],
        "step_state": {"implement": {"status": "pending", "retries": 0}},
        "cursor": 0, "stopped": None, "done": False, "history": []}
    runtime.initialize(state, workspace, state_path,
                       {"generator": "cmd", "verifier": "cmd", "provider_cmd": "/usr/bin/python3"})
    return state, state_path, workspace


def test_real_runner_rechecks_all_checks_before_done(prepared):
    state, path, workspace = prepared
    assert runtime.run_strict(state, path) == "DONE"
    assert [c[0] for c in FakeIO.calls] == ["GENERATE", "CHECK", "CHECK"]
    runtime.validate_acceptance(path, workspace)
    assert all(not writable for op, writable, _ in FakeIO.calls if op == "CHECK")


def test_failure_diagnosis_repair_loop(prepared):
    state, path, _ = prepared
    FakeIO.failing = 1
    assert runtime.run_strict(state, path) == "DONE"
    assert [c[0] for c in FakeIO.calls][:5] == ["GENERATE", "CHECK", "DIAGNOSE", "GENERATE", "CHECK"]
    assert state["deterministic_runtime"]["units"]["implement"]["events"][0]["classification"] == "IMPLEMENTATION"


def test_repeated_failure_requires_changed_plan_and_preserves_history(prepared):
    state, path, _ = prepared
    FakeIO.failing = 2
    assert runtime.run_strict(state, path) == "DONE"
    unit = state["deterministic_runtime"]["units"]["implement"]
    assert [event["kind"] for event in unit["events"]] == ["failure", "failure", "replan"]
    assert "REPLAN" in [c[0] for c in FakeIO.calls]


def test_two_replans_then_failure_escalates(prepared):
    state, path, _ = prepared
    FakeIO.failing = 10
    assert runtime.run_strict(state, path) == "ESCALATE"
    events = state["deterministic_runtime"]["units"]["implement"]["events"]
    assert sum(e["kind"] == "failure" for e in events) == 5
    assert sum(e["kind"] == "replan" for e in events) == 2


def test_invalid_diagnosis_never_runs_repair(prepared):
    state, path, _ = prepared
    FakeIO.failing, FakeIO.bad_diagnosis = 1, True
    assert runtime.run_strict(state, path) == "AWAIT_DECISION"
    assert [c[0] for c in FakeIO.calls].count("GENERATE") == 1


def test_interrupted_provider_is_not_called_again_on_resume(prepared):
    state, path, _ = prepared
    FakeIO.interrupt = "GENERATE"
    with pytest.raises(KeyboardInterrupt):
        runtime.run_strict(state, path)
    assert FakeIO.calls[0][2]["deterministic_runtime"]["phase"].endswith("_INFLIGHT")
    FakeIO.interrupt = None
    assert runtime.resume_strict(path) == "BLOCKED"
    assert len(FakeIO.calls) == 1


def test_completed_boundary_resume_preserves_counter(prepared):
    state, path, _ = prepared
    FakeIO.failing = 1
    assert runtime.run_strict(state, path, max_steps=2) == "PAUSED"
    assert runtime.resume_strict(path) == "DONE"
    assert [c[0] for c in FakeIO.calls].count("GENERATE") == 2


def test_definition_tampering_rejected(prepared):
    state, path, _ = prepared
    state["steps"][0]["checks"] = ["true"]
    with pytest.raises(ValueError):
        runtime.validate_state(state)
    with pytest.raises(ValueError):
        runtime.run_strict(state, path)
    assert not FakeIO.calls


def test_stale_done_and_artifact_tampering_cannot_be_accepted(prepared):
    state, path, workspace = prepared
    runtime.run_strict(state, path)
    FakeIO.content += " drift"
    with pytest.raises(ValueError):
        runtime.validate_acceptance(path, workspace)


@pytest.mark.parametrize("change", ["dag", "no_checks", "executor"])
def test_unsupported_shapes_refused_before_provider(prepared, change):
    state, path, workspace = prepared
    state.pop("deterministic_runtime")
    step = state["steps"][0]
    if change == "dag":
        step["needs"] = ["previous"]
    elif change == "no_checks":
        step["checks"] = []
    else:
        step["executor"] = "adaptive-review"
    with pytest.raises(ValueError):
        runtime.initialize(state, workspace, path, {"generator": "mock", "verifier": "mock"})
    assert not FakeIO.calls


def test_authoritative_state_loaded_under_lock_prevents_duplicate_resume(prepared):
    state, path, _ = prepared
    stale = copy.deepcopy(state)
    assert runtime.run_strict(state, path) == "DONE"
    count = len(FakeIO.calls)
    assert runtime.run_strict(stale, path) == "DONE"
    assert len(FakeIO.calls) == count


def test_existing_state_cannot_be_overwritten(prepared):
    state, path, workspace = prepared
    before = copy.deepcopy(FakeIO.saved[str(path)])
    state.pop("deterministic_runtime")
    with pytest.raises(ValueError, match="overwrite"):
        runtime.initialize(state, workspace, path, {"generator": "mock", "verifier": "mock"})
    assert FakeIO.saved[str(path)] == before


def test_changing_failure_subsets_cannot_evade_unit_replan(prepared):
    state, _, _ = prepared
    unit = state["deterministic_runtime"]["units"]["implement"]
    unit["evidence"] = {}
    assert runtime._failed(state, ["A"], []) == "REPAIR"
    assert runtime._failed(state, ["A", "B"], []) == "REPLAN"
    assert unit["failure_granularity"] == "unit"


def test_final_check_regression_in_single_step_is_repaired(prepared, monkeypatch):
    state, path, _ = prepared
    original = FakeIO.run
    checks = []
    def run(self, argv, **kwargs):
        if kwargs.get("input") is None:
            checks.append(1)
            if len(checks) == 2:
                return CompletedProcess(argv, 1, "final regression", "")
        return original(self, argv, **kwargs)
    monkeypatch.setattr(FakeIO, "run", run)
    assert runtime.run_strict(state, path) == "DONE"
    assert [c[0] for c in FakeIO.calls].count("GENERATE") == 2
    assert len(state["deterministic_runtime"]["units"]["implement"]["events"]) == 1


def test_missing_check_command_blocks_without_rewriting(prepared, monkeypatch):
    state, path, _ = prepared
    original = FakeIO.run
    def run(self, argv, **kwargs):
        if kwargs.get("input") is None:
            return CompletedProcess(argv, 127, "", "command not found")
        return original(self, argv, **kwargs)
    monkeypatch.setattr(FakeIO, "run", run)
    assert runtime.run_strict(state, path) == "BLOCKED"
    assert not any(c[0] == "DIAGNOSE" for c in FakeIO.calls)


def test_retained_output_exit_code_cannot_disagree_with_pass_evidence(prepared):
    state, path, workspace = prepared
    runtime.run_strict(state, path)
    saved = FakeIO.saved[str(path)]
    bundle = saved["deterministic_runtime"]["final_evidence"]
    bundle["outputs"][0]["output"]["exit_code"] = 1
    bundle["evidence"][0]["artifact_digest"] = runtime._hash(bundle["outputs"][0]["output"])
    with pytest.raises(ValueError):
        runtime.validate_acceptance(path, workspace)


@pytest.mark.parametrize("bad", [
    {"status": "PASSING", "criteria": []},
    {"status": "PASS", "criteria": [{"id": "a", "status": "UNKNOWN"}]},
    {"status": "PASS", "criteria": [{"id": "a", "status": "PASS"}, {"id": "a", "status": "PASS"}]},
    {"status": "PASS", "criteria": []},
])
def test_review_is_exact_fail_closed_json(bad):
    with pytest.raises(ValueError):
        runtime._validate_review({"stdout": json.dumps(bad), "stderr": "", "exit_code": 0}, [("a", "criterion")])


def test_real_isolated_pipeline_repairs_replans_and_accepts_current_code(tmp_path):
    import subprocess
    import shlex
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init", str(workspace)], check=True, capture_output=True)
    fixture = tmp_path / "provider.py"
    fixture.write_text('''import json,sys
from pathlib import Path
request=json.load(sys.stdin)
op=request['operation']
if op=='GENERATE':
    counter=Path('generation')
    count=int(counter.read_text())+1 if counter.exists() else 1
    counter.write_text(str(count))
    Path('result').write_text('good' if count>=3 else 'bad')
    print('implemented')
elif op in ('DIAGNOSE','REPLAN'):
    response={'failure_id':request['failure_id'], 'failed_check_ids':request['failed_check_ids'],
              'hypothesis':'Result is still bad', 'changes':['Write good result'],
              'verification_checks':request['failed_check_ids']}
    if op=='REPLAN': response['plan']='Change how the result is produced'
    print(json.dumps(response))
else:
    print(json.dumps({'status':'PASS','criteria':[{'id':cid,'status':'PASS'} for cid in request['criteria_ids']]}))
''')
    state = {"run_id": "real-run", "goal": "Make good result", "recipe": "test",
             "steps": [{"id": "fix", "instruction": "Fix", "checks": ["test \"$(cat result)\" = good"],
                        "gate": "acceptance-gate", "acceptance": ["Result is good"], "needs": []}],
             "step_state": {"fix": {"status": "pending", "retries": 0}},
             "done": False, "stopped": None, "history": [], "cursor": 0}
    state_path = tmp_path / "private" / "run.json"
    runtime.initialize(state, workspace, state_path,
                       {"generator": "cmd", "verifier": "cmd",
                        "provider_cmd": "/usr/bin/python3 -c " + shlex.quote(fixture.read_text())})
    assert runtime.run_strict(state, state_path, max_steps=2) == "PAUSED"
    assert runtime.resume_strict(state_path) == "DONE"
    assert (workspace / "generation").read_text() == "3"
    runtime.validate_acceptance(state_path, workspace)
    saved = json.loads(state_path.read_text())
    unit = saved["deterministic_runtime"]["units"]["fix"]
    assert [event["kind"] for event in unit["events"]] == ["failure", "failure", "replan"]
    assert saved["deterministic_runtime"]["final_review"]["output"]["exit_code"] == 0
    (workspace / "result").write_text("bad")
    with pytest.raises(ValueError, match="stale"):
        runtime.validate_acceptance(state_path, workspace)


@pytest.mark.parametrize("mutation", ["missing_step_state", "reset_retries", "bad_operation", "terminal_to_generate"])
def test_malformed_runtime_state_refused_before_more_execution(prepared, mutation):
    state, path, _ = prepared
    if mutation == "missing_step_state":
        state.pop("step_state")
    elif mutation == "reset_retries":
        FakeIO.failing = 1
        runtime.run_strict(state, path, max_steps=2)
        state["step_state"]["implement"]["retries"] = 0
    elif mutation == "bad_operation":
        state["deterministic_runtime"]["operations"].append({"output": "forged"})
    else:
        FakeIO.failing = 10
        runtime.run_strict(state, path)
        state["deterministic_runtime"]["phase"] = "GENERATE"
        state["stopped"] = None
    with pytest.raises(ValueError):
        runtime.validate_state(state)


def test_done_resume_refuses_current_subject_drift(prepared):
    state, path, _ = prepared
    runtime.run_strict(state, path)
    FakeIO.content += " external drift"
    assert runtime.resume_strict(path) == "BLOCKED"


@pytest.mark.parametrize("location", ["units", "unit", "step_state_entry"])
@pytest.mark.parametrize("value", [None, ["implement"], "implement", 1])
def test_primitive_recovery_containers_fail_with_value_error(prepared, location, value):
    state, _, _ = prepared
    if location == "units":
        state["deterministic_runtime"]["units"] = value
    elif location == "unit":
        state["deterministic_runtime"]["units"]["implement"] = value
    else:
        state["step_state"]["implement"] = value
    with pytest.raises(ValueError):
        runtime.validate_state(state)


@pytest.mark.parametrize("relative", [False, True])
def test_writable_provider_script_is_refused(prepared, relative):
    state, path, workspace = prepared
    state.pop("deterministic_runtime")
    script = workspace / "provider.py"
    script.write_text("print('fake pass')")
    command = "/usr/bin/python3 " + ("provider.py" if relative else str(script))
    with pytest.raises(ValueError, match="provider.*workspace"):
        runtime.initialize(state, workspace, path.with_name("new.json"),
                           {"generator": "cmd", "verifier": "cmd", "provider_cmd": command})


@pytest.mark.parametrize("field", ["policies", "output_contract", "material_profiles", "pattern", "condition", "auto_route", "actor", "personas", "model", "verifier_model"])
def test_unsupported_recipe_obligations_are_not_silently_ignored(prepared, field):
    state, path, workspace = prepared
    state.pop("deterministic_runtime")
    state["steps"][0][field] = "required-obligation"
    with pytest.raises(ValueError, match="unsupported strict step obligation"):
        runtime.initialize(state, workspace, path.with_name("new.json"),
                           {"generator": "mock", "verifier": "mock"})


def test_manual_only_run_cannot_enter_strict_lane(prepared):
    state, path, workspace = prepared
    state.pop("deterministic_runtime")
    state["no_orchestrate"] = True
    with pytest.raises(ValueError, match="manual-only"):
        runtime.initialize(state, workspace, path.with_name("new.json"),
                           {"generator": "mock", "verifier": "mock"})


def test_strict_progress_is_optional_and_does_not_change_frozen_contract(prepared, capsys):
    state, path, _ = prepared
    frozen = copy.deepcopy(state['deterministic_runtime']['definition'])
    events = []
    assert runtime.run_strict(state, path, observer=events.append) == 'DONE'
    assert state['deterministic_runtime']['definition'] == frozen
    assert any(e['event'] == 'operation_started' and e['phase'] == 'GENERATE' for e in events)
    assert any(e['event'] == 'operation_finished' and e['outcome'] == 'PROCESS_EXITED' for e in events)
    assert any(e['event'] == 'transition' and e['phase'] == 'DONE' for e in events)
    assert all('output' not in event and 'command' not in event and 'prompt' not in event for event in events)
    assert capsys.readouterr().out == ''


def test_broken_strict_observer_does_not_change_result(prepared):
    state, path, _ = prepared
    def broken(event):
        raise RuntimeError('UI disconnected')
    assert runtime.run_strict(state, path, observer=broken) == 'DONE'


def test_strict_failure_progress_separates_process_exit_from_gate_result(prepared):
    state, path, _ = prepared
    FakeIO.failing = 1
    events = []
    assert runtime.run_strict(state, path, observer=events.append) == 'DONE'
    assert any(e.get('check_id') == 'implement:1' and e.get('outcome') == 'PROCESS_FAILED' for e in events)
    assert any(e.get('phase') == 'DIAGNOSE' and e['event'] == 'transition' for e in events)
    assert any(e.get('outcome') == 'MACHINE_GATE_PASSED' for e in events)


def test_strict_default_has_no_progress_output(prepared, capsys):
    state, path, _ = prepared
    assert runtime.run_strict(state, path) == 'DONE'
    capture = capsys.readouterr()
    assert capture.out == capture.err == ''


def test_observation_does_not_modify_persisted_workflow_state(prepared):
    state, path, _ = prepared
    initial = copy.deepcopy(state)
    runtime.run_strict(state, path)
    expected = copy.deepcopy(FakeIO.saved[str(path)])
    FakeIO.saved[str(path)] = copy.deepcopy(initial)
    FakeIO.content = 'initial'
    observed = []
    runtime.run_strict(initial, path, observer=observed.append)
    assert observed
    assert FakeIO.saved[str(path)] == expected
