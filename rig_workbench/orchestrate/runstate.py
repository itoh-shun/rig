"""orchestrate run-state: state + gate evaluation (split from scripts/orchestrate.py)."""

import os
import json
import datetime
import pathlib

from . import config

# ── run-state ────────────────────────────────────────────────────────────────
def new_state(recipe: str, steps: list[dict], goal: str | None) -> dict:
    return {
        "recipe": recipe,
        "goal": goal,
        "steps": steps,
        "cursor": 0,
        "step_state": {s["id"]: {"status": "pending", "retries": 0, "checks": [], "verdicts": []}
                       for s in steps},
        "stopped": None,
        "done": False,
        "history": [],
    }


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
    it" mapping live in `skills/rig/patterns/failure-taxonomy.md` (adapted from MAST,
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
                                    for v in ss[s["id"]].get("verdicts", [])]}
                      for s in state["steps"]],
        }
        # Failure-mode taxonomy (additive; absent for successful runs). Deterministic best-guess
        # from state — see classify_failure / skills/rig/patterns/failure-taxonomy.md.
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
    return json.loads(path.read_text(encoding="utf-8"))


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
    needs_verdict = gate in ("acceptance-gate", "review-gate") and not declared
    if needs_verdict and not verdicts:
        return "incomplete"            # awaiting the independent verifier's judgment

    # Enforce grader != generator (prevents self-grading bias; policies/independent-verification)
    if any(str(v.get("by", "")).lower() in ("", "self", "generator", "producer") for v in verdicts):
        return "self-graded"
    if any(not v["ok"] for v in verdicts):
        return "fail"

    return "pass"


def compute_next(state: dict) -> tuple[str, str]:
    """Deterministically compute and apply the next action from the state (mutates state).
    Returns: (action_code, message)
    """
    if state["stopped"]:
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
        state["history"].append({"action": "START", "step": sid})
        gate = step["gate"] or "none"
        need = []
        if step["checks"]:
            need.append(f"check ({len(step['checks'])} machine checks)")
        if step["gate"] in ("acceptance-gate", "review-gate") and not step["checks"]:
            need.append("verdict (independent verifier judgment; grader != generator)")
        need_s = " → ".join(need) if need else "(no gate: just run next after the work)"
        return "START", (f"Run step `{sid}` (instruction: {step['instruction']} / gate: {gate}). "
                         f"Delegate the work, finish {need_s}, then `next`.")

    # status == "running"
    outcome = gate_outcome(step, st)
    if outcome == "incomplete":
        return "AWAIT", f"step `{sid}` awaits gate evaluation. Run `check` / `verdict`, then `next`."
    if outcome == "self-graded":
        return "BLOCKED", (f"step `{sid}`: a verdict from the generator itself (by=self/generator) is invalid. "
                           f"An independent verifier's `verdict` is required (grader != generator).")
    if outcome == "pass":
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
    state["history"].append({"action": "FAIL", "step": sid, "try": st["retries"]})
    if st["retries"] >= K:
        state["stopped"] = {"reason": f"step `{sid}` failed the gate {K} times → escalating", "at": sid}
        return "ESCALATE", state["stopped"]["reason"] + " (no infinite loops; hand off to the user)."
    # Retry: redo this step (records are reset)
    st["status"] = "pending"
    st["checks"] = []
    st["verdicts"] = []
    return "RETRY", f"step `{sid}` failed → retrying (try {st['retries']+1}/{K}). Address the findings and rerun."

