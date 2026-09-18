"""Opt-in executable deterministic lane; all effects cross StrictIO.

Only sequential generate steps with nonempty machine checks are supported. The
runner owns failure classification and transitions. AI supplies proposed diagnosis
and plans, never gate verdicts or limits. Interrupted external operations stop:
without an operation receipt, re-executing could duplicate an external effect.
Provider executables and directly named command files must be outside the writable
workspace. Operator-authored inline commands remain trusted: arbitrary code can
dynamically import files, which an argv inspection cannot prove safe.
"""
from __future__ import annotations

import copy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from .deterministic_io import StrictIO
from .gate_evidence import CheckEvidence, EvidenceContext, RequiredCheck, evaluate_gate
from .recovery_policy import FailureEvent, ReplanEvent, RecoveryLimits, decide_recovery
from .progress import notify
from . import lifecycle_runtime as lifecycle
from .lifecycle_policy import compile_steps, decision_digest, plan_digest, affected_units

_LIMITS = asdict(RecoveryLimits())
_TERMINAL = {"DONE", "BLOCKED", "AWAIT_DECISION", "ESCALATE", "DESIGN", "REQUIREMENTS"}
_PHASES = {"GENERATE", "CHECK", "VERIFY", "DIAGNOSE", "REPLAN", "FINAL_CHECK", "FINAL_VERIFY"}
_CONFIG_KEYS = {"generator", "verifier", "provider_cmd", "model", "timeout"}


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _text(value):
    return type(value) is str and bool(value.strip())


def _definition(state, runtime):
    return {"steps": state["steps"], "goal": state.get("goal"), "run_id": state["run_id"],
            "binding": state.get("deterministic_binding"), "workspace": runtime["workspace"],
            "state_path": runtime["state_path"], "provider_config": runtime["provider_config"],
            "resolved_executables": runtime["resolved_executables"],
            "limits": runtime["limits"], **({"lifecycle": {
                "plan": runtime['lifecycle']['plan'], "revision": runtime['lifecycle']['revision']}}
                if runtime.get('schema_version') == 2 else {})}


def _validate_contract(state, cfg, allow_draft=False):
    if state.get("no_orchestrate") or (isinstance(state.get("execution"), dict)
                                       and state["execution"].get("orchestratable") is False):
        raise ValueError("manual-only or nonexecutable run cannot enter strict execution")
    if type(cfg) is not dict or set(cfg) - _CONFIG_KEYS:
        raise ValueError("unsupported strict provider configuration")
    for role in ("generator", "verifier"):
        if cfg.get(role) not in ("cmd", "mock", "codex", "claude"):
            raise ValueError("unsupported strict provider")
    if "cmd" in (cfg["generator"], cfg["verifier"]) and not _text(cfg.get("provider_cmd")):
        raise ValueError("cmd requires provider_cmd")
    if type(cfg.get("timeout", 600)) is not int or cfg.get("timeout", 600) < 1:
        raise ValueError("timeout must be a positive integer")
    if "model" in cfg and cfg["model"] is not None and not _text(cfg["model"]):
        raise ValueError("model must be a string")
    steps = state.get("steps")
    if type(steps) is not list or not steps or not _text(state.get("run_id")):
        raise ValueError("strict run requires steps and run_id")
    ids = set()
    for step in steps:
        if type(step) is not dict or not _text(step.get("id")) or step["id"] in ids:
            raise ValueError("strict step IDs must be unique")
        ids.add(step["id"])
        for field in ("policies", "output_contract", "material_profiles", "pattern", "condition",
                      "auto_route", "actor", "personas", "model", "verifier_model"):
            if step.get(field):
                raise ValueError("unsupported strict step obligation: " + field)
        if step.get("needs") or step.get("executor", "generate") != "generate":
            raise ValueError("strict runtime supports sequential generate steps only")
        if step.get("human_gate") or step.get("gate") not in (None, "", "acceptance-gate", "review-gate"):
            raise ValueError("unsupported strict gate")
        checks = step.get("checks")
        if type(checks) is not list or (not checks and not allow_draft) or not all(_text(c) for c in checks):
            raise ValueError("each strict step requires nonempty machine checks")
        if len(set(checks)) != len(checks):
            raise ValueError("duplicate check definitions")
        acceptance = step.get("acceptance", [])
        if type(acceptance) is not list or not all(_text(a) for a in acceptance):
            raise ValueError("acceptance must be a list of strings")


def _events(unit):
    if type(unit) is not dict:
        raise ValueError("invalid recovery unit")
    events = unit.get("events")
    if type(events) is not list:
        raise ValueError("invalid recovery events")
    result = []
    for event in events:
        if type(event) is not dict:
            raise ValueError("invalid recovery event")
        payload = {k: v for k, v in event.items() if k != "kind"}
        try:
            cls = {"failure": FailureEvent, "replan": ReplanEvent}[event["kind"]]
            result.append(cls(**payload))
        except (KeyError, TypeError) as error:
            raise ValueError("invalid recovery event shape") from error
    if result:
        candidate = tuple(result)
        if type(result[-1]) is ReplanEvent:
            # Validate the final replan by asking the policy about a hypothetical
            # next failure; no event or counter is persisted by this probe.
            previous = next(e for e in reversed(result) if type(e) is FailureEvent)
            candidate += (FailureEvent(len(result) + 1, previous.check_id,
                                       previous.error_class, previous.target_id,
                                       previous.classification),)
        if decide_recovery(candidate).reason == "invalid_history":
            raise ValueError("invalid recovery transitions")
    return tuple(result)


def validate_state(state: dict) -> None:
    """Validate frozen contract and bounded state without performing I/O."""
    try:
        rt = state["deterministic_runtime"]
        if type(rt) is not dict or type(rt.get("schema_version")) is not int or rt["schema_version"] not in (1, 2):
            raise ValueError("unsupported deterministic runtime schema")
        if rt['schema_version'] == 2:
            if type(state.get('lifecycle_schema_version')) is not int or state['lifecycle_schema_version'] != 1:
                raise ValueError('missing lifecycle run marker')
            lifecycle.validate_lifecycle(state, rt)
            if rt.get('immutable_definition_digest') != _hash(lifecycle.immutable_definition(state, rt)):
                raise ValueError('immutable lifecycle run identity changed')
        elif 'lifecycle_schema_version' in state or 'lifecycle' in rt:
            raise ValueError('lifecycle runtime downgrade refused')
        _validate_contract(state, rt["provider_config"], rt['schema_version'] == 2)
        if type(rt["limits"]) is not dict or rt["limits"] != _LIMITS or any(type(v) is not int for v in rt["limits"].values()):
            raise ValueError("strict recovery limits changed")
        if rt["definition_digest"] != _hash(_definition(state, rt)):
            raise ValueError("strict run definition changed")
        if rt["definition"] != _definition(state, rt):
            raise ValueError("strict definition does not match run")
        phase = rt["phase"]
        if type(phase) is not str or phase not in _PHASES | _TERMINAL | ({'UPSTREAM'} if rt['schema_version'] == 2 else set()) | {p + "_INFLIGHT" for p in _PHASES}:
            raise ValueError("unknown strict runtime phase")
        if type(rt["step_index"]) is not int or not 0 <= rt["step_index"] < len(state["steps"]):
            raise ValueError("invalid strict step index")
        if type(rt["attempt"]) is not int or rt["attempt"] < 1:
            raise ValueError("invalid strict attempt")
        if type(rt["units"]) is not dict or set(rt["units"]) != {s["id"] for s in state["steps"]}:
            raise ValueError("strict unit set changed")
        if type(state["step_state"]) is not dict or set(state["step_state"]) != set(rt["units"]):
            raise ValueError("invalid strict step state")
        if type(state["done"]) is not bool or state["done"] != (phase == "DONE"):
            raise ValueError("strict completion status is inconsistent")
        if phase in _TERMINAL - {"DONE"}:
            if type(state["stopped"]) is not dict or state["stopped"].get("kind") != phase:
                raise ValueError("strict stop status is inconsistent")
        elif state["stopped"] is not None:
            raise ValueError("active strict run has a stop record")
        for sid, unit in rt["units"].items():
            events = _events(unit)
            failures = sum(type(e) is FailureEvent for e in events)
            replans = sum(type(e) is ReplanEvent for e in events)
            if type(state["step_state"][sid]) is not dict:
                raise ValueError("invalid unit step state")
            retries = state["step_state"][sid]["retries"]
            if type(retries) is not int or retries != failures:
                raise ValueError("strict retry counter disagrees with persisted history")
            if events and type(events[-1]) is FailureEvent:
                decision = decide_recovery(events)
                if decision.action not in ("REPAIR", "REPLAN") and phase not in _TERMINAL - {"DONE"}:
                    raise ValueError("strict history continued after terminal recovery decision")
            if not _text(unit.get("plan")) or type(unit.get("plan_history")) is not list:
                raise ValueError("invalid persisted plan")
            if len(unit["plan_history"]) != replans:
                raise ValueError("plan history disagrees with replan events")
        if type(rt["operations"]) is not list:
            raise ValueError("invalid operation history")
        for operation in rt["operations"]:
            if (type(operation) is not dict
                    or set(operation) != {"operation", "attempt", "output", "artifact_digest"}
                    or not _text(operation["operation"])
                    or type(operation["attempt"]) is not int or operation["attempt"] < 1
                    or type(operation["output"]) is not dict
                    or operation["artifact_digest"] != _hash(operation["output"])):
                raise ValueError("invalid retained operation artifact")
    except (KeyError, TypeError, StopIteration) as error:
        raise ValueError("malformed deterministic runtime state") from error


def initialize(state: dict, workspace: Path, state_path: Path, provider_config: dict, lifecycle_plan=None) -> None:
    """Freeze an explicit strict run before any provider is called."""
    if "deterministic_runtime" in state:
        raise ValueError("strict runtime is already initialized")
    if lifecycle_plan is not None:
        if state['steps'] != compile_steps(lifecycle_plan) or state.get('goal') != lifecycle_plan['objective']:
            raise ValueError('initial lifecycle compilation mismatch')
    _validate_contract(state, provider_config, lifecycle_plan is not None)
    if state.get("done") or state.get("stopped"):
        raise ValueError("cannot initialize a completed or stopped run")
    workspace, state_path = Path(workspace).resolve(), Path(state_path).absolute()
    rt = {"schema_version": 1, "workspace": str(workspace), "state_path": str(state_path),
          "provider_config": copy.deepcopy(provider_config), "limits": dict(_LIMITS),
          "phase": "GENERATE", "step_index": 0, "attempt": 1, "operations": [],
          "units": {s["id"]: {"events": [], "plan": s.get("instruction") or s["id"],
                               "plan_history": [], "diagnosis": None} for s in state["steps"]}}
    resolved = {}
    for provider in {provider_config["generator"], provider_config["verifier"]}:
        command = (shlex.split(provider_config["provider_cmd"])[0] if provider == "cmd"
                   else sys.executable if provider == "mock" else provider)
        executable = shutil.which(command)
        if not executable:
            raise ValueError("strict provider executable was not found: " + command)
        executable_path = Path(executable).resolve()
        if executable_path.is_relative_to(workspace):
            raise ValueError("provider executable must be outside writable workspace")
        resolved[provider] = str(executable_path)
    if "cmd" in resolved:
        inline_next = False
        for argument in shlex.split(provider_config["provider_cmd"])[1:]:
            if inline_next:
                inline_next = False
                continue
            if argument in ("-c", "--command", "-e", "--eval"):
                inline_next = True
                continue
            if argument.startswith("-") or "{" in argument:
                continue
            candidate = Path(argument)
            candidate = (workspace / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
            if (candidate.is_relative_to(workspace)
                    and (candidate.is_file() or "/" in argument or candidate.suffix in (".py", ".js", ".sh", ".mjs", ".cjs"))):
                raise ValueError("provider command files must be outside writable workspace")
    rt["resolved_executables"] = resolved
    if lifecycle_plan is not None:
        rt.update(schema_version=2, phase='UPSTREAM', upstream_resume_phase='GENERATE',
                  lifecycle={'schema_version': 1, 'revision': 1, 'plan': copy.deepcopy(lifecycle_plan),
                             'decisions': [], 'changes': []})
        state['lifecycle_schema_version'] = 1
        rt['immutable_definition_digest'] = _hash(lifecycle.immutable_definition(state, rt))
    rt["definition"] = copy.deepcopy(_definition(state, rt))
    rt["definition_digest"] = _hash(rt["definition"])
    state["deterministic_runtime"] = rt
    validate_state(state)
    io = StrictIO(workspace, state_path)
    with io.locked():
        try:
            io.load()
        except FileNotFoundError:
            pass
        else:
            raise ValueError("refusing to overwrite an existing strict run")
        io.preflight()
        io.save(state)


def _progress(state, observer, event, **metadata):
    rt = state["deterministic_runtime"]
    notify(observer, event, **{"run_id": state["run_id"],
           "step_id": state["steps"][rt["step_index"]]["id"], "attempt": rt["attempt"],
           "phase": rt["phase"], **metadata})


def _stop(state, io, action, reason, observer=None):
    rt = state["deterministic_runtime"]
    rt["phase"] = action
    state["done"] = False
    state["stopped"] = {"kind": action, "reason": reason,
                        "at": state["steps"][rt["step_index"]]["id"]}
    io.save(state)
    _progress(state, observer, "transition", outcome=action)
    return action


def _checks(steps):
    return [(f"{step['id']}:{index + 1}", command)
            for step in steps for index, command in enumerate(step["checks"])]


def _begin(state, io, phase):
    state["deterministic_runtime"]["phase"] = phase + "_INFLIGHT"
    io.save(state)


def _record(state, operation, result):
    rt = state["deterministic_runtime"]
    output = {"stdout": result.stdout or "", "stderr": result.stderr or "",
              "exit_code": result.returncode}
    rt["operations"].append({"operation": operation, "attempt": rt["attempt"],
                              "output": output, "artifact_digest": _hash(output)})
    return output


def _provider_argv(provider, role, cfg):
    if provider == "cmd":
        return [arg.replace("{role}", role).replace("{persona}", "strict").replace("{prompt}", "-")
                for arg in shlex.split(cfg["provider_cmd"])]
    if provider == "mock":
        source = (
            "import json,sys\nr=json.load(sys.stdin)\nop=r['operation']\n"
            "out={'status':'PASS','criteria':[{'id':i,'status':'PASS'} for i in r.get('criteria_ids',[])]}\n"
            "if op in ('DIAGNOSE','REPLAN'):\n"
            " out={'failure_id':r['failure_id'],'failed_check_ids':r['failed_check_ids'],"
            "'hypothesis':'Mock hypothesis','changes':['Mock change'],"
            "'verification_checks':r['failed_check_ids']}\n"
            " if op=='REPLAN': out['plan']='Mock revised plan '+str(r['attempt'])\n"
            "print(json.dumps(out))\n"
        )
        return [sys.executable, "-c", source]
    if provider == "codex":
        argv = ["codex", "exec", "--skip-git-repo-check", "--sandbox",
                "workspace-write" if role == "generator" else "read-only"]
        if cfg.get("model"):
            argv += ["-m", cfg["model"]]
        return argv + ["-"]
    argv = ["claude", "-p", "--output-format", "text", "--no-session-persistence"]
    argv += (["--permission-mode", "acceptEdits"] if role == "generator"
             else ["--allowedTools", "Read,Grep,Glob"])
    if cfg.get("model"):
        argv += ["--model", cfg["model"]]
    return argv


def _provider(state, io, operation, payload, writable=False, observer=None):
    rt = state["deterministic_runtime"]
    cfg = rt["provider_config"]
    role = "generator" if writable else "verifier"
    # Diagnosis and planning belong to the generator, under a read-only mount.
    provider = cfg["generator"] if operation in ("GENERATE", "DIAGNOSE", "REPLAN") else cfg["verifier"]
    request = {"operation": operation, "goal": state.get("goal"), "attempt": rt["attempt"],
               "instruction": "Return only the requested JSON for diagnosis/planning/review. "
                              "For GENERATE edit the workspace according to the fixed contract.",
               **lifecycle.context(state), **payload}
    _begin(state, io, operation)
    before = io.snapshot()
    argv = _provider_argv(provider, role, cfg)
    argv[0] = rt["resolved_executables"][provider]
    _progress(state, observer, "operation_started", phase=operation, provider=provider, role=role)
    outcome = "PROCESS_INTERRUPTED"
    try:
        result = io.run(argv, input=json.dumps(request),
                        timeout=cfg.get("timeout", 600), writable=writable,
                        network=provider in ("codex", "claude"))
        outcome = "PROCESS_EXITED" if result.returncode == 0 else "PROCESS_FAILED"
    finally:
        _progress(state, observer, "operation_finished", phase=operation, provider=provider, role=role, outcome=outcome)
    output = _record(state, operation, result)
    if not writable and io.snapshot() != before:
        raise ValueError("read-only provider changed verification subject")
    if result.returncode != 0:
        raise OSError("strict provider failed: " + str(result.returncode))
    return output


def _context(state, subject, final=False):
    rt = state["deterministic_runtime"]
    return EvidenceContext(state["run_id"], "__final__" if final else state["steps"][rt["step_index"]]["id"],
                           rt["attempt"], subject, rt["definition_digest"],
                           _hash({"platform": sys.platform, "python": sys.version,
                                  "provider_config": rt["provider_config"]}))


def _run_checks(state, io, final=False, observer=None):
    rt = state["deterministic_runtime"]
    if final and rt.get('lifecycle'):
        _archive_final(rt)
    phase = "FINAL_CHECK" if final else "CHECK"
    steps = state["steps"] if final else [state["steps"][rt["step_index"]]]
    declared = _checks(steps) + (lifecycle.final_checks(state) if final else [])
    _begin(state, io, phase)
    subject = io.snapshot()
    context = _context(state, subject, final)
    evidence, outputs, errors = [], [], []
    for check_id, command in declared:
        metadata = {"phase": phase, "step_id": check_id.rsplit(":", 1)[0], "check_id": check_id}
        _progress(state, observer, "operation_started", **metadata)
        outcome = "PROCESS_INTERRUPTED"
        try:
            result = io.run(["/bin/sh", "-c", command], timeout=rt["provider_config"].get("timeout", 600),
                            writable=False, network=False)
            output = _record(state, check_id, result)
            outcome = "PROCESS_EXITED" if result.returncode == 0 else "PROCESS_FAILED"
            status = "PASS" if result.returncode == 0 else "FAIL"
            if result.returncode in (126, 127):
                # Shell cannot execute/find a command. Intentional use of these
                # exit codes also conservatively stops rather than rewriting code.
                errors.append(check_id)
                status = "UNKNOWN"
        except (OSError, subprocess.TimeoutExpired) as error:
            output = {"stdout": "", "stderr": type(error).__name__, "exit_code": None}
            errors.append(check_id)
            status = "UNKNOWN"
        finally:
            _progress(state, observer, "operation_finished", **metadata, outcome=outcome)
        outputs.append({"check_id": check_id, "output": output})
        evidence.append(CheckEvidence(context, check_id, _hash(command), status,
                                      output["exit_code"], _hash(output)))
        io.save(state)  # still INFLIGHT: incomplete batches are never replayed on resume
    if io.snapshot() != subject:
        raise ValueError("verification subject changed during checks")
    required = tuple(RequiredCheck(cid, _hash(cmd)) for cid, cmd in declared)
    decision = evaluate_gate(context, required, tuple(evidence))
    bundle = {"context": asdict(context), "evidence": [asdict(e) for e in evidence], "outputs": outputs}
    if final:
        rt["final_evidence"] = bundle
    else:
        rt["units"][steps[0]["id"]]["evidence"] = bundle
    return decision.passed, [e.check_id for e in evidence if e.status != "PASS"], errors


def _json_object(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    value = json.loads(text, object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON constant")))
    if type(value) is not dict:
        raise ValueError("expected JSON object")
    return value


def _diagnosis(output, failure, replan):
    parsed = _json_object(output["stdout"])
    keys = {"failure_id", "failed_check_ids", "hypothesis", "changes", "verification_checks"}
    if replan:
        keys.add("plan")
    if set(parsed) != keys or parsed.get("failure_id") != failure["failure_id"]:
        raise ValueError("diagnosis must reference exact failure")
    for key in ("failed_check_ids", "verification_checks"):
        if (type(parsed[key]) is not list or not all(_text(v) for v in parsed[key])
                or sorted(parsed[key]) != sorted(failure["failed_check_ids"])):
            raise ValueError("diagnosis must cover every failed check exactly once")
    if not _text(parsed["hypothesis"]) or type(parsed["changes"]) is not list or not parsed["changes"] \
            or not all(_text(v) for v in parsed["changes"]):
        raise ValueError("diagnosis requires hypothesis and concrete changes")
    if replan and not _text(parsed["plan"]):
        raise ValueError("replan requires a changed plan")
    return parsed


def _criteria(steps):
    return [(f"{step['id']}:{i + 1}", criterion)
            for step in steps if step.get("gate") for i, criterion in enumerate(step.get("acceptance", []))]


def _validate_review(output, criteria):
    if (type(output) is not dict or set(output) != {"stdout", "stderr", "exit_code"}
            or type(output.get("stdout")) is not str or type(output.get("stderr")) is not str
            or type(output.get("exit_code")) is not int or output["exit_code"] != 0):
        raise ValueError("independent review process did not succeed")
    parsed = _json_object(output["stdout"])
    if set(parsed) != {"status", "criteria"} or parsed["status"] != "PASS" or type(parsed["criteria"]) is not list:
        raise ValueError("verifier did not provide a complete PASS")
    expected = {cid for cid, _ in criteria}
    seen = set()
    for item in parsed["criteria"]:
        if type(item) is not dict or set(item) != {"id", "status"} or item.get("status") != "PASS":
            raise ValueError("verifier criterion was not PASS")
        if type(item["id"]) is not str or item["id"] not in expected or item["id"] in seen:
            raise ValueError("unknown or duplicate verifier criterion")
        seen.add(item["id"])
    if seen != expected:
        raise ValueError("missing verifier criteria")


def _verify(state, io, final, observer=None):
    rt = state["deterministic_runtime"]
    steps = state["steps"] if final else [state["steps"][rt["step_index"]]]
    bundle = rt["final_evidence"] if final else rt["units"][steps[0]["id"]]["evidence"]
    if bundle["context"]["subject_digest"] != io.snapshot():
        raise ValueError("machine evidence became stale before independent review")
    criteria = _criteria(steps)
    output = _provider(state, io, "FINAL_VERIFY" if final else "VERIFY",
                       {"steps": steps, "criteria": criteria, "criteria_ids": [cid for cid, _ in criteria],
                        "response_schema": {"status": "PASS|FAIL|UNKNOWN", "criteria": [{"id": "exact ID", "status": "PASS|FAIL|UNKNOWN"}]}}, observer=observer)
    _validate_review(output, criteria)
    receipt = {"subject_digest": io.snapshot(), "output": output, "artifact_digest": _hash(output)}
    if final:
        rt["final_review"] = receipt
    else:
        rt["units"][steps[0]["id"]]["review"] = receipt


def _next_step(state):
    rt = state["deterministic_runtime"]
    sid = state["steps"][rt["step_index"]]["id"]
    state["step_state"][sid]["status"] = "passed"
    pending = [index for index, step in enumerate(state['steps'])
               if state['step_state'][step['id']]['status'] != 'passed']
    if pending:
        rt["step_index"] = pending[0]
        rt["attempt"] += 1
        rt["phase"] = rt['units'][state['steps'][pending[0]]['id']].pop('resume_phase', 'GENERATE')
    else:
        rt["phase"] = "FINAL_CHECK"


def _failed(state, failed, errors):
    rt = state["deterministic_runtime"]
    sid = state["steps"][rt["step_index"]]["id"]
    unit = rt["units"][sid]
    # Conservative unit granularity: alternating subsets cannot hide a check
    # which keeps failing. Any two unit failures trigger the replan cadence.
    unit["failure_granularity"] = "unit"
    event = FailureEvent(len(unit["events"]) + 1, "machine-checks:" + sid,
                         "CHECK_ERROR" if errors else "nonzero_exit", sid,
                         "CHECK_ERROR" if errors else "IMPLEMENTATION")
    history = _events(unit) + (event,)
    decision = decide_recovery(history)
    unit["events"].append({"kind": "failure", **asdict(event)})
    unit["current_failure"] = {"failure_id": decision.failure_id, "failed_check_ids": sorted(failed),
                               "evidence": unit["evidence"]}
    unit["decision"] = decision.action
    state["step_state"][sid]["retries"] = decision.total_failures
    return decision.action


def _done(state, io):
    rt = state["deterministic_runtime"]
    rt["phase"] = "DONE"
    state["done"], state["stopped"] = True, None
    state["cursor"] = len(state["steps"])
    io.save(state)


def run_strict(state: dict, state_path: Path, *, max_steps: int = 40, observer=None) -> str:
    """Execute bounded persisted phases. A bounded pause is resumable, not success."""
    validate_state(state)
    if type(max_steps) is not int or max_steps < 1:
        raise ValueError("max_steps must be a positive integer")
    rt = state["deterministic_runtime"]
    if str(Path(state_path).absolute()) != rt["state_path"]:
        raise ValueError("state path changed")
    io = StrictIO(Path(rt["workspace"]), Path(state_path))
    with io.locked():
        authoritative = io.load()
        validate_state(authoritative)
        if authoritative["deterministic_runtime"]["definition_digest"] != rt["definition_digest"]:
            raise ValueError("authoritative run definition changed")
        state.clear()
        state.update(authoritative)
        rt = state["deterministic_runtime"]
        io.preflight()
        for _ in range(max_steps):
            validate_state(state)
            phase = rt["phase"]
            if phase.endswith("_INFLIGHT"):
                return _stop(state, io, "BLOCKED", "interrupted operation has no completion receipt; refusing duplicate execution", observer)
            if phase in _TERMINAL:
                if phase == "DONE":
                    try:
                        _validate_final_evidence(state, io)
                    except (ValueError, OSError) as error:
                        return _stop(state, io, "BLOCKED", str(error), observer)
                _progress(state, observer, "transition", outcome=phase)
                return phase
            if rt.get('lifecycle'):
                target_phase = rt.get('upstream_resume_phase') if phase == 'UPSTREAM' else phase
                readiness = lifecycle.ready(state, target_phase in ('FINAL_CHECK', 'FINAL_VERIFY'))
                if not readiness['allowed']:
                    rt.update(phase='UPSTREAM', upstream_resume_phase=target_phase,
                              upstream_reasons=readiness['reasons'])
                    io.save(state)
                    _progress(state, observer, 'transition', outcome='UPSTREAM')
                    return 'UPSTREAM'
                if phase == 'UPSTREAM':
                    rt['phase'] = phase = target_phase
                    rt.pop('upstream_reasons', None)
                    io.save(state)
            step = state["steps"][rt["step_index"]]
            unit = rt["units"][step["id"]]
            transition_outcome = "READY"
            try:
                if phase == "GENERATE":
                    _provider(state, io, phase, {"step": step, "plan": unit["plan"], "diagnosis": unit["diagnosis"], **lifecycle.context(state)}, writable=True, observer=observer)
                    state["step_state"][step["id"]]["status"] = "running"
                    rt["phase"] = "CHECK"
                elif phase in ("CHECK", "FINAL_CHECK"):
                    final = phase == "FINAL_CHECK"
                    passed, failed, errors = _run_checks(state, io, final, observer)
                    transition_outcome = "MACHINE_GATE_PASSED" if passed else "MACHINE_GATE_FAILED"
                    if not passed:
                        if final:
                            if len(state["steps"]) != 1:
                                return _stop(state, io, "AWAIT_DECISION", "integration regression scope requires a decision", observer)
                            unit["evidence"] = rt["final_evidence"]
                        action = _failed(state, failed, errors)
                        if action not in ("REPAIR", "REPLAN"):
                            return _stop(state, io, action, "recovery policy: " + action, observer)
                        rt["phase"] = "DIAGNOSE" if action == "REPAIR" else "REPLAN"
                    elif final:
                        if any(s.get("gate") for s in state["steps"]):
                            rt["phase"] = "FINAL_VERIFY"
                        else:
                            _done(state, io)
                    elif step.get("gate"):
                        rt["phase"] = "VERIFY"
                    else:
                        _next_step(state)
                elif phase in ("DIAGNOSE", "REPLAN"):
                    failure = unit["current_failure"]
                    output = _provider(state, io, phase, {**failure, "previous_plan": unit["plan"],
                        "response_schema": {"failure_id": "exact ID", "failed_check_ids": ["all failed IDs"],
                        "hypothesis": "nonempty", "changes": ["concrete changes"],
                        "verification_checks": ["all failed IDs"], **({"plan": "changed plan"} if phase == "REPLAN" else {})}}, observer=observer)
                    try:
                        proposal = _diagnosis(output, failure, phase == "REPLAN")
                        if phase == "REPLAN":
                            proposal["plan"] = " ".join(proposal["plan"].split())
                            digest = _hash(proposal["plan"])
                            current_plan = " ".join(unit["plan"].split())
                            if digest in unit["plan_history"] or proposal["plan"] == current_plan:
                                raise ValueError("replan must change the plan")
                            unit["plan_history"].append(_hash(current_plan))
                            unit["plan"] = proposal["plan"]
                            unit["events"].append({"kind": "replan", **asdict(ReplanEvent(len(unit["events"]) + 1, failure["failure_id"]))})
                        unit["diagnosis"] = proposal
                    except (ValueError, TypeError) as error:
                        return _stop(state, io, "AWAIT_DECISION", str(error), observer)
                    transition_outcome = "PLAN_ACCEPTED" if phase == "REPLAN" else "DIAGNOSIS_ACCEPTED"
                    rt["attempt"] += 1
                    rt["phase"] = "GENERATE"
                elif phase in ("VERIFY", "FINAL_VERIFY"):
                    try:
                        _verify(state, io, phase == "FINAL_VERIFY", observer)
                    except ValueError as error:
                        return _stop(state, io, "AWAIT_DECISION", str(error), observer)
                    transition_outcome = "INDEPENDENT_REVIEW_PASSED"
                    if phase == "FINAL_VERIFY":
                        _done(state, io)
                    else:
                        _next_step(state)
                io.save(state)
                _progress(state, observer, "transition", outcome=transition_outcome)
            except (OSError, subprocess.TimeoutExpired, ValueError) as error:
                return _stop(state, io, "BLOCKED", type(error).__name__ + ": " + str(error), observer)
        return rt["phase"] if rt["phase"] in _TERMINAL else "PAUSED"


def _load_state(state_path):
    from .deterministic_io import load_state
    return load_state(Path(state_path))


def resume_strict(state_path: Path, max_steps: int = 40, *, observer=None) -> str:
    # The state loader itself enforces the protected state boundary before the
    # workspace is trusted. A placeholder workspace is not used to execute.
    from .deterministic_binding import resumed_task
    state = _load_state(state_path)
    validate_state(state)
    with resumed_task(state, state_path):
        return run_strict(state, Path(state_path), max_steps=max_steps, observer=observer)


def validate_acceptance(state_path: Path, workspace: Path) -> None:
    """Require current complete machine evidence, including retained artifacts."""
    io = StrictIO(Path(workspace), Path(state_path))
    with io.locked():
        io.preflight()
        state = io.load()
        validate_state(state)
        rt = state["deterministic_runtime"]
        if rt["workspace"] != str(Path(workspace).resolve()) or rt["state_path"] != str(Path(state_path).absolute()):
            raise ValueError("acceptance workspace/state binding mismatch")
        if not state.get("done") or state.get("stopped") or rt["phase"] != "DONE":
            raise ValueError("deterministic run has not completed")
        _validate_final_evidence(state, io)


def _validate_final_evidence(state, io):
    rt = state["deterministic_runtime"]
    if rt.get('lifecycle') and not lifecycle.ready(state, True)['allowed']:
        raise ValueError('current lifecycle approvals or completions are missing')
    subject = io.snapshot()
    bundle = rt.get("final_evidence")
    if type(bundle) is not dict:
        raise ValueError("missing final evidence")
    context = _context(state, subject, True)
    evidence = []
    try:
        outputs = bundle["outputs"]
        if type(outputs) is not list or len(outputs) != len(bundle["evidence"]):
            raise ValueError("missing evidence artifacts")
        by_id = {record["check_id"]: record["output"] for record in outputs}
        if len(by_id) != len(outputs):
            raise ValueError("duplicate evidence artifacts")
        if bundle["context"] != asdict(context):
            raise ValueError("stale evidence context")
        for record in bundle["evidence"]:
            output = by_id[record["check_id"]]
            if (type(output) is not dict or set(output) != {"stdout", "stderr", "exit_code"}
                    or type(output["stdout"]) is not str or type(output["stderr"]) is not str
                    or record["artifact_digest"] != _hash(output)
                    or type(output.get("exit_code")) is not int
                    or record["exit_code"] != output["exit_code"]):
                raise ValueError("evidence artifact changed")
            evidence.append(CheckEvidence(**{**record, "context": EvidenceContext(**record["context"])}))
        required = tuple(RequiredCheck(cid, _hash(cmd)) for cid, cmd in _checks(state["steps"]) + lifecycle.final_checks(state))
        if not evaluate_gate(context, required, tuple(evidence)).passed:
            raise ValueError("final gate evidence did not pass")
    except (KeyError, TypeError) as error:
        raise ValueError("malformed final evidence") from error
    if any(s.get("gate") for s in state["steps"]):
        review = rt.get("final_review") or {}
        if type(review) is not dict or review.get("subject_digest") != subject or review.get("artifact_digest") != _hash(review.get("output")):
            raise ValueError("missing or stale final independent review")
        _validate_review(review["output"], _criteria(state["steps"]))


def _archive_final(rt):
    retained = {key: rt.pop(key) for key in ('final_evidence', 'final_review') if key in rt}
    if retained:
        rt.setdefault('superseded_final_evidence', []).append(retained)


def _lifecycle_edit(state_path, edit):
    """Task then state lock, reload, edit and validate one atomic protected state."""
    from .deterministic_binding import resumed_task
    state_path = Path(state_path)
    initial = _load_state(state_path)
    validate_state(initial)
    with resumed_task(initial, state_path):
        io = StrictIO(Path(initial['deterministic_runtime']['workspace']), state_path)
        with io.locked():
            state = io.load()
            validate_state(state)
            rt = state['deterministic_runtime']
            if (rt.get('schema_version') != 2 or rt['state_path'] != str(state_path.absolute())
                    or rt.get('immutable_definition_digest') != initial['deterministic_runtime'].get('immutable_definition_digest')):
                raise ValueError('lifecycle run binding changed')
            if rt['phase'].endswith('_INFLIGHT'):
                raise ValueError('cannot revise an interrupted operation')
            for unit in rt['units'].values():
                events = _events(unit)
                if events and type(events[-1]) is FailureEvent and decide_recovery(events).action not in ('REPAIR', 'REPLAN'):
                    raise ValueError('terminal recovery history cannot be reset by upstream edits')
            if rt['phase'] in ('BLOCKED', 'ESCALATE', 'DESIGN', 'REQUIREMENTS'):
                raise ValueError('terminal run cannot be reset by upstream edits')
            edit(state)
            rt['definition'] = copy.deepcopy(_definition(state, rt))
            rt['definition_digest'] = _hash(rt['definition'])
            validate_state(state)
            io.save(state)
            return copy.deepcopy(rt['lifecycle'])


def _expect_revision(lc, revision):
    if type(revision) is not int or revision != lc['revision']:
        raise ValueError('stale lifecycle revision')


def _wait_upstream(state, phase=None):
    rt = state['deterministic_runtime']
    phase = phase or (rt.get('upstream_resume_phase') if rt['phase'] == 'UPSTREAM' else rt['phase'])
    if phase in ('DONE', 'AWAIT_DECISION'):
        phase = 'FINAL_CHECK' if all(s['status'] == 'passed' for s in state['step_state'].values()) else 'GENERATE'
    if phase == 'FINAL_VERIFY':
        phase = 'FINAL_CHECK'
    rt.update(phase='UPSTREAM', upstream_resume_phase=phase)
    state.update(done=False, stopped=None)


def lifecycle_decide(state_path, scope, stage, digest, revision, decision, actor, reason):
    """Record an explicit caller-authorized decision against current content."""
    def edit(state):
        rt = state['deterministic_runtime']
        lc = rt['lifecycle']
        _expect_revision(lc, revision)
        if digest != decision_digest(lc['plan'], scope, stage):
            raise ValueError('stale lifecycle decision digest')
        lc['decisions'].append({'id': str(len(lc['decisions']) + 1), 'scope': scope,
                               'stage': stage, 'digest': digest, 'decision': decision,
                               'actor': actor, 'reason': reason})
        _archive_final(rt)
        _wait_upstream(state)
    return _lifecycle_edit(state_path, edit)


def lifecycle_revise(state_path, plan, revision, digest, reason, actor):
    """Revise a fixed unit set, preserving failures and invalidating dependents."""
    def edit(state):
        rt = state['deterministic_runtime']
        lc = rt['lifecycle']
        _expect_revision(lc, revision)
        if digest != plan_digest(lc['plan']):
            raise ValueError('stale lifecycle plan digest')
        steps = compile_steps(plan)
        if [s['id'] for s in steps] != [s['id'] for s in state['steps']]:
            raise ValueError('lifecycle unit IDs and order are immutable')
        affected = affected_units(lc['plan'], plan)
        if plan == lc['plan']:
            raise ValueError('lifecycle revision must change the plan')
        lc['changes'].append({'revision': revision + 1, 'previous_plan': copy.deepcopy(lc['plan']),
                              'previous_digest': digest, 'actor': actor, 'reason': reason,
                              'affected_units': affected})
        lc.update(revision=revision + 1, plan=copy.deepcopy(plan))
        state['steps'], state['goal'] = steps, plan['objective']
        _archive_final(rt)
        current_id = steps[rt['step_index']]['id']
        previous_phase = rt.get('upstream_resume_phase') if rt['phase'] == 'UPSTREAM' else rt['phase']
        if (previous_phase in ('DIAGNOSE', 'REPLAN') or
                state['step_state'][current_id]['status'] != 'passed' and previous_phase in ('CHECK', 'VERIFY')):
            rt['units'][current_id]['resume_phase'] = 'CHECK' if previous_phase == 'VERIFY' else previous_phase
        for step in steps:
            sid = step['id']
            if sid not in affected:
                continue
            unit = rt['units'][sid]
            events = _events(unit)
            recovery_phase = unit.get('resume_phase')
            if (events and type(events[-1]) is FailureEvent and decide_recovery(events).action == 'REPLAN'):
                unit['resume_phase'] = recovery_phase = 'REPLAN'
            receipts = {key: unit.pop(key) for key in ('evidence', 'review') if key in unit}
            if receipts:
                unit.setdefault('superseded_evidence', []).append(receipts)
            if recovery_phase not in ('REPLAN', 'DIAGNOSE'):
                unit.update(plan=step.get('instruction') or sid, diagnosis=None)
                unit.pop('resume_phase', None)
            state['step_state'][sid]['status'] = 'pending'
        pending = [i for i, step in enumerate(steps) if state['step_state'][step['id']]['status'] != 'passed']
        if pending:
            rt['step_index'] = pending[0]
        rt['attempt'] += 1
        phase = rt['units'][steps[pending[0]]['id']].pop('resume_phase', 'GENERATE') if pending else 'FINAL_CHECK'
        _wait_upstream(state, phase)
    return _lifecycle_edit(state_path, edit)
