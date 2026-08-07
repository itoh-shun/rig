"""orchestrate run-state: state + gate evaluation (split from scripts/orchestrate.py)."""

import os
import json
import datetime
import pathlib

from . import config
from .gates import is_runtime_gate, validate_executable_steps

EXECUTION_POLICY_VERSION = 1
_EXECUTION_FIELDS = (
    "structurally_valid", "orchestratable", "manual_only",
    "unsupported_gates", "errors", "reason",
)
_EXECUTION_STATE_FIELDS = frozenset({
    "execution_policy_version", "no_orchestrate", "execution",
})


def _exact_equal(left: object, right: object) -> bool:
    """Compare JSON-shaped values without bool/int coercion or extra fields."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _exact_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _exact_equal(a, b) for a, b in zip(left, right)
        )
    return left == right


def _state_no_orchestrate(state: dict) -> tuple[object, str | None]:
    """Read persisted policy while allowing safe pre-policy run-state files."""
    present = _EXECUTION_STATE_FIELDS.intersection(state)
    if not present:
        return False, None  # legacy state: only code-backed gates remain executable
    if present != _EXECUTION_STATE_FIELDS:
        return None, "execution policy schema is incomplete"
    version = state["execution_policy_version"]
    if type(version) is not int or version != EXECUTION_POLICY_VERSION:
        return None, f"unsupported execution policy version: {version!r}"
    if type(state["no_orchestrate"]) is not bool:
        return None, "execution policy no_orchestrate must be an exact boolean"
    return state["no_orchestrate"], None


def _invalidate_execution(execution: dict, error: str) -> dict:
    report = dict(execution)
    report["structurally_valid"] = False
    report["orchestratable"] = False
    report["errors"] = [*execution["errors"], error]
    report["reason"] = error
    return report


def enforce_executable_state(state: dict) -> dict:
    """Recompute execution safety from steps plus persisted manual-only provenance."""
    no_orchestrate, policy_error = _state_no_orchestrate(state)
    persisted = state.get("execution")
    execution = validate_executable_steps(
        state.get("steps"), no_orchestrate=no_orchestrate,
    )
    if policy_error:
        execution = _invalidate_execution(execution, policy_error)
    elif state.get("execution_policy_version") == EXECUTION_POLICY_VERSION:
        expected = {field: execution[field] for field in _EXECUTION_FIELDS}
        if not isinstance(persisted, dict) or not _exact_equal(persisted, expected):
            execution = _invalidate_execution(
                execution, "persisted execution provenance is inconsistent with run-state policy",
            )
    state["execution"] = execution
    if execution["orchestratable"]:
        return execution
    first = execution["unsupported_gates"][0] if execution["unsupported_gates"] else None
    at = first["step"] if first else "—"
    state["stopped"] = {
        "reason": f"computationally nonexecutable: {execution['reason']}",
        "kind": "BLOCKED",
        "at": at,
    }
    return execution

# ── run-state ────────────────────────────────────────────────────────────────
def new_state(
    recipe: str, steps: list[dict], goal: str | None, execution: dict | None = None,
) -> dict:
    if execution is None:
        execution = validate_executable_steps(steps)
    no_orchestrate = execution.get("manual_only") if isinstance(execution, dict) else None
    state = {
        "recipe": recipe,
        "goal": goal,
        "steps": steps,
        "cursor": 0,
        # `approvals` holds the human-gate decisions for a step (v2.1), symmetric with
        # `verdicts` (a model's judgment) and `checks` (a machine's). Empty for every
        # step that declares no human gate, which is all of them by default.
        "step_state": {s["id"]: {"status": "pending", "retries": 0, "checks": [],
                                 "verdicts": [], "approvals": []}
                       for s in steps},
        "adaptive": {
            "assessment": None,
            "invocation_limit": 3,
            "invocations": 0,
        },
        "stopped": None,
        "done": False,
        "history": [],
        "execution_policy_version": EXECUTION_POLICY_VERSION,
        "no_orchestrate": no_orchestrate,
        "execution": execution,
    }
    enforce_executable_state(state)
    return state


def save_state(state: dict, path: pathlib.Path) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _verdict_summary(v: dict) -> dict:
    """One verdict's runs.jsonl summary (pure). Additive: per-criterion verdicts and the
    judge-panel multi-PASS record (order_sensitive + pass_set) only appear when present,
    so old-format verdicts keep the historical {by, ok} shape."""
    rec = {"by": v.get("by"), "ok": bool(v.get("ok"))}
    if v.get("criteria"):
        rec["criteria"] = [{"n": c.get("n"), "verdict": c.get("verdict"),
                            "anchor": c.get("anchor", "")} for c in v["criteria"]]
    if v.get("order_sensitive"):
        rec["order_sensitive"] = True
        rec["pass_set"] = v.get("pass_set", [])
    return rec


def classify_failure(state: dict) -> str | None:
    """Best-guess a MAST-style failure-mode code for a stopped / escalated / blocked run.

    Pure and deterministic: derives a taxonomy code purely from signals already present in
    `state` (no model call). The vocabulary and the "which rig gate/brick should have caught
    it" mapping live in `skills/engine/patterns/failure-taxonomy.md` (adapted from MAST,
    arXiv 2503.13657 — 3 categories / 14 modes). Returns a code string, or None when the state
    shows no failure (a successful or still-running run — successful runs carry no failure_mode).

    Design note (MODEL-suggested-but-deterministically-stored): this is the deterministic
    best-guess from state. A richer classification could be model-supplied later (e.g. an
    escalation reviewer emitting a code); a future caller may pass that through instead, but
    the value telemetry records here is always the reproducible from-state one.

    Signal rules (first match wins):
      - a verdict from the generator itself (by=self/generator/producer/"") → `verification:self-grading`
        (self-graded gate; the BLOCKED path — grader != generator was violated).
      - escalated (stopped) on a step whose declared machine `checks` ran and failed →
        `verification:incorrect-implementation` (K retries exhausted; the sensor kept catching a bad impl).
      - escalated on a gated step (acceptance/review-gate) with no declared checks and no verdict →
        `verification:missing` (no independent verification was ever produced — a no-verifier stall).
      - any other stopped run → `unclassified` (a code exists but the signal is ambiguous; never silently dropped).
      - no failure signal → None.
    """
    ss = state.get("step_state") or {}

    # Self-grading can be present without `stopped` (compute_next returns BLOCKED without stopping).
    for st in ss.values():
        for v in st.get("verdicts") or []:
            if str(v.get("by", "")).lower() in ("", "self", "generator", "producer"):
                return "verification:self-grading"

    stopped = state.get("stopped")
    if not stopped:
        return None  # successful or in-progress run — no failure mode

    sid = stopped.get("at")
    step = next((s for s in (state.get("steps") or []) if s.get("id") == sid), None)
    st = ss.get(sid, {})
    if step is not None:
        declared = step.get("checks") or []
        ran = st.get("checks") or []
        if declared and any(not c.get("ok") for c in ran):
            return "verification:incorrect-implementation"
        if step.get("gate") in ("acceptance-gate", "review-gate") and not declared and not st.get("verdicts"):
            return "verification:missing"
    return "unclassified"


def telemetry_append(state: dict, final: str) -> None:
    """Append a one-line JSON summary of a single RUN to .rig/runs.jsonl (run telemetry).

    An execution log on par with run-state.json, not the knowledge layer (no approval needed;
    .rig/ is already gitignored). Aggregation is the `runs` subcommand. A write failure must
    not break the RUN result (best-effort).
    """
    try:
        ss = state["step_state"]
        # step id -> most recent auto-route decision (#264; a step retried keeps its last decision)
        auto_routed: dict[str, dict] = {}
        learned_predictions: dict[str, dict] = {}
        fable_events: dict[str, list] = {}
        for h in state.get("history", []):
            if h.get("action") == "AUTO_ROUTE":
                auto_routed[h["step"]] = {"model": h.get("model"), "reason": h.get("reason")}
            elif h.get("action") == "LEARNED_ROUTE_PREDICTION":
                learned_predictions[h["step"]] = {k: v for k, v in h.items() if k not in ("action", "step")}  # #305
            elif h.get("action") in ("FABLE_REFUSAL", "FABLE_FALLBACK"):
                fable_events.setdefault(h["step"], []).append(
                    {k: v for k, v in h.items() if k not in ("action", "step")} |
                    {"kind": "refusal" if h["action"] == "FABLE_REFUSAL" else "fallback"})  # #297
        rec = {
            "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "recipe": state["recipe"],
            "backend": "orchestrate",
            "invoker": os.environ.get("RIG_INVOKER") or "direct",
            "final": final,
            "steps_total": len(state["steps"]),
            "steps_passed": sum(1 for st in ss.values() if st.get("status") == "passed"),
            "retries": sum(st.get("retries", 0) for st in ss.values()),
            "escalated_at": (state.get("stopped") or {}).get("at") if state.get("stopped") else None,
            "token_usage": state.get("token_usage") or {},  # #271/#296: provider -> {prompt/completion_tokens, calls}
            "steps": [{"id": s["id"], "status": ss[s["id"]].get("status"),
                       "retries": ss[s["id"]].get("retries", 0),
                       "model": ss[s["id"]].get("model"),  # actually-used generator model (#293; None = provider default)
                       "verdicts": [_verdict_summary(v)
                                    for v in ss[s["id"]].get("verdicts", [])],
                       **({"auto_route": auto_routed[s["id"]]} if s["id"] in auto_routed else {}),
                       **({"learned_route": learned_predictions[s["id"]]} if s["id"] in learned_predictions else {}),
                       **({"fable_events": fable_events[s["id"]]} if s["id"] in fable_events else {})}
                      for s in state["steps"]],
        }
        # Failure-mode taxonomy (additive; absent for successful runs). Deterministic best-guess
        # from state — see classify_failure / skills/engine/patterns/failure-taxonomy.md.
        failure_mode = classify_failure(state)
        if failure_mode is not None:
            rec["failure_mode"] = failure_mode
        config.RUNS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with config.RUNS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass

    # ── Mirror into the global index (~/.rig/runs.jsonl) as well ─────────────
    # Keep the per-project log (cwd/.rig) while enabling cross-project aggregation of
    # how much rig-wb is used overall. The `project` field preserves provenance.
    # Write failures are swallowed (best-effort; the cwd-side record is primary).
    try:
        global_path = config.GLOBAL_RUNS_PATH
        global_path.parent.mkdir(parents=True, exist_ok=True)
        # After the cwd record is finalized (rec fully built), copy it with project attached
        global_rec = dict(rec)
        global_rec["project"] = str(config.INVOCATION_CWD)
        with global_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(global_rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def load_state(path: pathlib.Path) -> dict:
    state = json.loads(path.read_text(encoding="utf-8"))
    enforce_executable_state(state)
    return state


def _distill_failures(st: dict) -> str | None:
    """Distill this attempt's failed checks and dissenting verdicts into one short string
    for the retry generator (#333: reviewer findings must survive the RETRY record reset,
    or the retry is blind — it never sees why the previous attempt failed).

    Pure: reads only st["checks"] / st["verdicts"] (as populated for the just-failed attempt,
    before compute_next resets them). Returns None when there is nothing to report.
    """
    parts = []
    for c in st.get("checks") or []:
        if not c.get("ok"):
            parts.append(f"check failed: {c.get('cmd')}")
    for v in st.get("verdicts") or []:
        if not v.get("ok"):
            note = str(v.get("note", ""))[:240]
            parts.append(f"{v.get('by')}: {note}")
    if not parts:
        return None
    joined = "; ".join(parts)
    if len(joined) > 800:
        joined = joined[:800] + "…"
    return joined


# ── Gate evaluation (deterministic, pure functions) ──────────────────────────
def gate_outcome(step: dict, st: dict) -> str:
    """Deterministically judge the current step's pass/fail.
    Returns: pass | fail | incomplete | self-graded
    """
    declared = step["checks"]
    ran = st["checks"]
    verdicts = st["verdicts"]

    # Machine sensors (checks) — if declared, the primary evidence. Require all run and all ok.
    if declared:
        if len(ran) < len(declared):
            return "incomplete"        # not yet checked
        if any(not c["ok"] for c in ran):
            return "fail"

    # Inferential verification (verdict) — acceptance-gate/review-gate require an independent judgment (when no checks declared).
    gate = step["gate"]
    if not gate:
        return "pass"  # gate-less steps pass through (when checks are empty)
    if gate and not is_runtime_gate(gate):
        return "unsupported"
    needs_verdict = is_runtime_gate(gate) and not declared
    if needs_verdict and not verdicts:
        return "incomplete"            # awaiting the independent verifier's judgment

    # Enforce grader != generator (prevents self-grading bias; policies/independent-verification)
    if any(str(v.get("by", "")).lower() in ("", "self", "generator", "producer") for v in verdicts):
        return "self-graded"
    if any(not v["ok"] for v in verdicts):
        return "fail"

    return "pass"


def govern_root() -> pathlib.Path:
    """Where `.rig/` lives for governance lookups — the nearest ancestor holding one,
    falling back to the invocation cwd. Same walk-up rule the rig-wb CLI uses, so a
    run started from a subdirectory still sees its org policy."""
    cwd = config.INVOCATION_CWD
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".rig").is_dir():
            return candidate
    return cwd


def stage_gate_status(step: dict, st: dict):
    """This step's human-gate status, or None when it has no human gate (v2.1).

    Resolved live against the effective policy rather than baked into the
    run-state: an org that tightens `stage:<id>` while a run is parked has
    tightened it for that run too, which is the whole point of the policy layer.

    A policy that fails to load is *not* treated as "no gate" — that would let a
    broken document silently open a stage. It raises, and the caller surfaces it.
    """
    try:
        from ..govern.policy import effective_policy
        from ..govern.stage import evaluate_stage
    except ImportError:                                    # pragma: no cover - packaging guard
        return None
    eff = effective_policy(govern_root())
    return evaluate_stage(eff, step, st.get("approvals") or [],
                          author=st.get("ran_as") or "")


def _record_actor(step: dict, st: dict) -> tuple[str | None, str | None]:
    """Stamp the running identity onto the step. Returns (blocking reason, advisory note).

    `ran_as` is what separation of duties compares an approver against, so it has
    to be recorded when the work happens, not when the approval arrives. A broken
    policy blocks (the same fail-closed rule as accept); an execution that does not
    hold the step's owning role only warns — see govern.stage.actor_mismatch.
    """
    try:
        from ..govern.identity import current_actor, org_binding_path
        from ..govern.policy import effective_policy
        from ..govern.stage import actor_mismatch
    except ImportError:                                    # pragma: no cover - packaging guard
        return None, None
    root = govern_root()
    # Cheap exit for the overwhelmingly common case. Resolving an identity shells
    # out to `git config`, and doing that at every step START of every run — in
    # repositories with no policy and no step that declares an owner — would be a
    # subprocess per step to record a value nothing reads.
    if not (step.get("actor") or step.get("human_gate") or org_binding_path(root).is_file()):
        return None, None
    try:
        actor = current_actor(root)
        eff = effective_policy(root)
        note = actor_mismatch(eff, step, actor)
    except Exception as e:
        return f"step `{step.get('id')}`: governance cannot be evaluated: {e}", None
    st["ran_as"] = actor
    return None, note


def compute_next(state: dict) -> tuple[str, str]:
    """Deterministically compute and apply the next action from the state (mutates state).
    Returns: (action_code, message)
    """
    enforce_executable_state(state)
    if state["stopped"]:
        if state["stopped"].get("kind") == "BLOCKED":
            return "BLOCKED", f"Blocked: {state['stopped']['reason']}"
        return "STOPPED", f"Stopped: {state['stopped']['reason']}"
    steps = state["steps"]
    if state["cursor"] >= len(steps):
        state["done"] = True
        return "DONE", "All steps complete."

    step = steps[state["cursor"]]
    sid = step["id"]
    st = state["step_state"][sid]

    if st["status"] == "pending":
        st["status"] = "running"
        # Who this attempt runs as. Recorded here, not at approval time, because it is
        # what separation of duties compares an approver against (v2.1).
        blocked, note = _record_actor(step, st)
        if blocked:
            state["stopped"] = {"reason": blocked, "kind": "BLOCKED", "at": sid}
            return "BLOCKED", blocked
        start_entry = {"action": "START", "step": sid}
        if note:
            start_entry["actor_note"] = note
        state["history"].append(start_entry)
        gate = step["gate"] or "none"
        need = []
        if step["checks"]:
            need.append(f"check ({len(step['checks'])} machine checks)")
        if step["gate"] in ("acceptance-gate", "review-gate") and not step["checks"]:
            need.append("verdict (independent verifier judgment; grader != generator)")
        if step.get("human_gate"):
            need.append("human sign-off (`orchestrate approve`)")
        need_s = " → ".join(need) if need else "(no gate: just run next after the work)"
        owner = f" / actor: {step['actor']}" if step.get("actor") else ""
        warning = f"\n  [WARN] {note}" if note else ""
        return "START", (f"Run step `{sid}` (instruction: {step['instruction']} / gate: {gate}{owner}). "
                         f"Delegate the work, finish {need_s}, then `next`.{warning}")

    # status == "running" or "awaiting_approval"
    outcome = gate_outcome(step, st)
    if outcome == "incomplete":
        return "AWAIT", f"step `{sid}` awaits gate evaluation. Run `check` / `verdict`, then `next`."
    if outcome == "self-graded":
        return "BLOCKED", (f"step `{sid}`: a verdict from the generator itself (by=self/generator) is invalid. "
                           f"An independent verifier's `verdict` is required (grader != generator).")
    if outcome == "unsupported":
        return "BLOCKED", (f"step `{sid}` uses unsupported executable gate `{step['gate']}`. "
                           "Use the manual engine for this recipe; custom prompt patterns "
                           "must not pass through as executable gates.")
    if outcome == "pass":
        # The machine is satisfied; a human gate, if declared, still is not.
        try:
            approval = stage_gate_status(step, st)
        except Exception as e:                    # policy/human_gate is unusable → refuse to guess
            state["stopped"] = {"reason": f"step `{sid}`: human gate cannot be evaluated: {e}",
                                "kind": "BLOCKED", "at": sid}
            return "BLOCKED", state["stopped"]["reason"]
        if approval is not None and not approval.satisfied:
            if st["status"] != "awaiting_approval":
                st["status"] = "awaiting_approval"
                state["history"].append({"action": "AWAIT_APPROVAL", "step": sid})
            if approval.denials:
                who = ", ".join(d.get("actor", "?") for d in approval.denials)
                note = next((d.get("note") for d in approval.denials if d.get("note")), "")
                return "AWAIT_APPROVAL", (
                    f"step `{sid}` was rejected by {who}"
                    + (f": {note}" if note else "")
                    + ". Address it, then ask for a fresh approval (`orchestrate approve`).")
            return "AWAIT_APPROVAL", (
                f"step `{sid}` passed its gate and awaits human sign-off "
                f"({approval.counted}/{approval.required}"
                + (f", from {', '.join(approval.rule['roles'])}" if approval.rule.get("roles") else "")
                + "). Approve with `orchestrate approve " + sid + "`.")
        st["status"] = "passed"
        state["cursor"] += 1
        state["history"].append({"action": "PASS", "step": sid})
        if state["cursor"] >= len(steps):
            state["done"] = True
            return "DONE", f"step `{sid}` passed. All steps complete."
        nxt = steps[state["cursor"]]["id"]
        return "ADVANCE", f"step `{sid}` passed → next is step `{nxt}`. Start it with `next`."
    # fail
    st["retries"] += 1
    K = step["max_retries"]
    # Distill BEFORE the reset below (or an ESCALATE too) wipes checks/verdicts — reviewer
    # findings must survive the record reset or the retry is blind (#333).
    findings = _distill_failures(st)
    fail_entry = {"action": "FAIL", "step": sid, "try": st["retries"]}
    if findings is not None:
        fail_entry["findings"] = findings
    state["history"].append(fail_entry)
    if st["retries"] >= K:
        state["stopped"] = {"reason": f"step `{sid}` failed the gate {K} times → escalating", "at": sid}
        return "ESCALATE", state["stopped"]["reason"] + " (no infinite loops; hand off to the user)."
    # Retry: redo this step (records are reset), but carry the distilled findings forward
    # via last_failure so _build_step_contract's previous_failure: line still shows them.
    st["status"] = "pending"
    st["checks"] = []
    st["verdicts"] = []
    if findings is not None:
        st["last_failure"] = findings
    return "RETRY", f"step `{sid}` failed → retrying (try {st['retries']+1}/{K}). Address the findings and rerun."
