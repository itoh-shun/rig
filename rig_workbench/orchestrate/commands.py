"""orchestrate commands: remaining cmd_* entry points (split from scripts/orchestrate.py)."""

import sys
import os
import json
import hashlib
import time
import shlex
import pathlib
import subprocess
import concurrent.futures as futures
from collections import Counter
from functools import wraps

from .. import repo_paths
from . import config
from .recipes import (_record_trust, auto_orchestrate, git_diff_lines, load_manifest,
                      load_steps, parse_frontmatter, resolve_effective, resolve_extends,
                      resolve_plan_json, resolve_recipe)
from .runstate import compute_next, load_state, new_state, save_state, stage_gate_status
from .providers import (JAPANESE_MATERIAL_PROFILES, JAPANESE_WRITING_REVIEW_CATEGORIES,
                        resolve_japanese_material, parse_step_model_spec,
                        read_result_artifact, run_loop, unknown_step_model_ids)
from ..packs.model import PackError
from .isolate import setup_isolation, teardown_isolation
from .gates import validate_executable_recipe
from .secure_runtime import (SecureRuntimeError, close_secure_launchers,
                             load_pin_config, preflight_secure_runtime,
                             requires_secure_runtime)
from .secure_fs import (
    acquire_output_lock,
    atomic_write_bytes,
    prepare_output_target,
    release_output_lock,
)


_SECURE_PIN_FLAGS = {
    "--generator-executable": ("generator", "executable"),
    "--generator-executable-sha256": ("generator", "sha256"),
    "--generator-interpreter": ("generator", "interpreter"),
    "--generator-interpreter-sha256": ("generator", "interpreter_sha256"),
    "--verifier-executable": ("verifier", "executable"),
    "--verifier-executable-sha256": ("verifier", "sha256"),
    "--verifier-interpreter": ("verifier", "interpreter"),
    "--verifier-interpreter-sha256": ("verifier", "interpreter_sha256"),
}
_GOAL_STDIN_MAX_BYTES = 1024 * 1024


def _read_goal_stdin() -> str:
    """Read one bounded UTF-8 goal payload without normalizing its bytes."""
    try:
        interactive = sys.stdin.isatty()
    except OSError as error:
        raise SecureRuntimeError("--goal-stdin could not inspect stdin") from error
    if interactive:
        raise SecureRuntimeError("--goal-stdin refuses an interactive terminal")
    try:
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        payload = stream.read(_GOAL_STDIN_MAX_BYTES + 1)
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
    except (OSError, UnicodeError) as error:
        raise SecureRuntimeError("--goal-stdin could not read a private UTF-8 payload") from error
    if len(payload) == 0:
        raise SecureRuntimeError("--goal-stdin requires a nonempty payload")
    if len(payload) > _GOAL_STDIN_MAX_BYTES:
        raise SecureRuntimeError(
            f"--goal-stdin exceeds the {_GOAL_STDIN_MAX_BYTES}-byte limit"
        )
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SecureRuntimeError("--goal-stdin requires valid UTF-8") from error

# ── Commands ──────────────────────────────────────────────────────────────────
def render_plan(recipe: str, steps: list[dict], execution: dict | None = None) -> str:
    auto, why = auto_orchestrate(steps)
    lines = [f"## rig computational plan: {recipe}", "",
             f"Steps: {len(steps)} / transitions enforced by code (deterministic)",
             f"Auto orchestrate: {'auto ON' if auto else 'off'} ({why})", ""]
    for i, s in enumerate(steps):
        gate = s["gate"] or "none"
        sensor = (str(len(s["checks"])) + " machine sensor(s)"
                  if s["checks"] else
                  ("independent verdict required" if s["gate"] in ("acceptance-gate", "review-gate") else "—"))
        lines.append(f"  [{i}] {s['id']}  gate={gate}  K={s['max_retries']}  verify={sensor}")
        owned = s.get("actor")
        human = s.get("human_gate")
        if owned or human:
            detail = []
            if owned:
                detail.append(f"actor={owned}")
            if human:
                quorum = human.get("quorum", 1) if isinstance(human, dict) else 1
                detail.append(f"human gate (quorum {quorum})")
            lines.append("        " + "  ".join(detail))
    lines.append("")
    lines.append("Stop condition: each step escalates after K gate failures (no infinite loops).")
    if execution is not None:
        status = "executable" if execution["orchestratable"] else "nonexecutable"
        lines.extend(["", f"Execution: {status}", f"Execution reason: {execution['reason']}"])
        if execution["unsupported_gates"]:
            detail = ", ".join(
                f"{item['step']}={item['gate']}" for item in execution["unsupported_gates"]
            )
            lines.append(f"Unsupported gates: {detail}")
    return "\n".join(lines)


def _require_executable_recipe(fm: dict, label: str) -> dict:
    execution = validate_executable_recipe(fm)
    if execution["orchestratable"]:
        return execution
    prefix = "[ERROR]" if execution["errors"] else "[BLOCKED]"
    print(f"{prefix} recipe {label} is computationally nonexecutable: {execution['reason']}")
    for error in execution["errors"]:
        print(f"[ERROR] {error}")
    raise SystemExit(2)


def cmd_plan(args):
    path = resolve_recipe(args[0])
    with_flags: list[str] | None = None
    diff_lines: int | None = None
    use_git_diff = False
    i = 1
    while i < len(args):
        if args[i] == "--with" and i + 1 < len(args):
            with_flags = shlex.split(args[i + 1])
            i += 2
        elif args[i] == "--diff-lines" and i + 1 < len(args):
            diff_lines = int(args[i + 1])
            i += 2
        elif args[i] == "--diff-git":
            use_git_diff = True
            i += 1
        else:
            i += 1
    if use_git_diff and diff_lines is None:
        diff_lines = git_diff_lines()  # None if unavailable → size defaults to S (#185)
    if with_flags is not None or diff_lines is not None or use_git_diff:
        plan = resolve_effective(path, with_flags, diff_lines, manifest=load_manifest())
    else:
        plan = resolve_plan_json(path)
    if "--json" in args:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        if plan.get("errors"):
            sys.exit(1)  # same exit contract as the non-JSON path
        return
    print(render_plan(plan["recipe"], plan["steps"], plan.get("execution")))
    for w in plan.get("warnings", []):
        print(f"[WARN] {w}")
    for e in plan.get("errors", []):
        print(f"[ERROR] {e}")
    if plan.get("errors"):
        sys.exit(1)


def _state_path(args, default="run-state.json") -> pathlib.Path:
    return pathlib.Path(args[0]) if args else pathlib.Path(default)


def _locked_secure_state_mutation(path_from_args):
    """Hold the secure run lock across a command's complete mutation window."""
    def decorate(command):
        @wraps(command)
        def guarded(args):
            state_path = path_from_args(args)
            if state_path is None:
                return command(args)
            initial = load_state(state_path)
            if not initial.get("secure_runtime"):
                return command(args)
            try:
                descriptor = acquire_output_lock(state_path)
            except OSError as error:
                print(f"[BLOCKED] {error}")
                raise SystemExit(2) from error
            try:
                # Reload after locking: another short mutation may have completed
                # between the optimistic first read and our successful lock.
                return command(args)
            finally:
                release_output_lock(descriptor)

        return guarded
    return decorate


def cmd_init(args):
    path = resolve_recipe(args[0])
    fm, _warns = resolve_extends(parse_frontmatter(path), path)
    execution = _require_executable_recipe(fm, fm.get("name", path.stem))
    steps = load_steps(fm)
    goal = None
    out = pathlib.Path("run-state.json")
    i = 1
    while i < len(args):
        if args[i] == "--goal" and i + 1 < len(args):
            goal = args[i + 1]
            i += 2
        elif args[i] == "--out" and i + 1 < len(args):
            out = pathlib.Path(args[i + 1])
            i += 2
        else:
            i += 1
    state = new_state(fm.get("name", path.stem), steps, goal, execution=execution)
    save_state(state, out)
    print(render_plan(state["recipe"], steps, execution))
    print(f"\nrun-state: {out}")
    action, msg = compute_next(state)
    save_state(state, out)
    print(f"\n▶ {action}: {msg}")


def _current_running(state: dict):
    if state["cursor"] >= len(state["steps"]):
        return None, None
    step = state["steps"][state["cursor"]]
    st = state["step_state"][step["id"]]
    if st["status"] != "running":
        return None, None
    return step, st


def _refuse_blocked_state(state: dict) -> None:
    stopped = state.get("stopped") or {}
    if stopped.get("kind") == "BLOCKED":
        print(f"[BLOCKED] {stopped.get('reason', 'run-state is computationally nonexecutable')}")
        raise SystemExit(2)


def _run_checks(checks: list[str]) -> list[dict]:
    """Run each declared shell check in INVOCATION_CWD; return [{cmd, ok, rc}] records.

    The single source of truth for the machine-sensor subprocess loop: both `check`
    and `resume` call this so they stay byte-for-byte identical (same shell, cwd, and
    stdout/stderr suppression). Pure I/O — no printing, no state mutation.
    """
    results = []
    for cmd in checks:
        r = subprocess.run(cmd, shell=True, cwd=str(config.INVOCATION_CWD),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        results.append({"cmd": cmd, "ok": (r.returncode == 0), "rc": r.returncode})
    return results


@_locked_secure_state_mutation(_state_path)
def cmd_check(args):
    sp = _state_path(args)
    state = load_state(sp)
    _refuse_blocked_state(state)
    step, st = _current_running(state)
    if not step:
        print("[ERROR] no running step. START one with `next` first.")
        sys.exit(1)
    if not step["checks"]:
        print(f"step `{step['id']}` declares no checks: (no machine verification). Use verdict instead.")
        return
    print(f"## check: machine sensors for step `{step['id']}` ({len(step['checks'])} checks)")
    results = _run_checks(step["checks"])
    st["checks"] = [{"cmd": r["cmd"], "ok": r["ok"]} for r in results]
    all_ok = all(r["ok"] for r in results)
    for r in results:
        print(f"  [{'OK ' if r['ok'] else 'NG '}] {r['cmd']}  (exit {r['rc']})")
    save_state(state, sp)
    print(f"→ {'all OK' if all_ok else 'some NG'}. Compute the transition with `next`.")


def _fmt_duration(seconds: float) -> str:
    """Compact human duration (e.g. 2h05m, 3d04h) for the resume mtime-gap cue."""
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    mins, _ = divmod(rem, 60)
    if days:
        return f"{days}d{hours:02d}h"
    if hours:
        return f"{hours}h{mins:02d}m"
    return f"{mins}m"


@_locked_secure_state_mutation(_state_path)
def cmd_resume(args):
    """Verify-first resume ritual (session-startup ritual for long-running agents).

    Re-verifies the world before continuing a persisted run: prints a compact digest,
    RE-RUNS the current running step's declared machine checks, and only then computes
    the next action. If a previously-passing check now fails, the recorded state is stale
    ("world drifted") and we REFUSE to advance (exit non-zero). Side effects match
    `check` + `next` (state is written the same way); idempotent.
    """
    sp = _state_path(args)
    state = load_state(sp)
    _refuse_blocked_state(state)
    steps = state["steps"]
    total = len(steps)
    n_passed = sum(1 for st in state["step_state"].values() if st.get("status") == "passed")

    # ── Digest ───────────────────────────────────────────────────────────────
    print(f"## resume: {state['recipe']}  cursor={state['cursor']}/{total}  "
          f"done={n_passed}/{total}  stopped={bool(state['stopped'])}")
    for s in steps:
        st = state["step_state"][s["id"]]
        rejects = [v for v in st["verdicts"] if not v.get("ok")]
        tail = (f"  ⚠ {len(rejects)} REJECT (by {', '.join(str(v.get('by')) for v in rejects)})"
                if rejects else "")
        print(f"  {s['id']:<14} {st['status']:<18} "
              f"checks={sum(1 for c in st['checks'] if c['ok'])}/{len(st['checks'])} "
              f"verdicts={len(st['verdicts'])}{tail}")
        if st["status"] == "awaiting_approval":
            for line in _stage_gate_lines(s, st):
                print(f"      {line}")
    if state["stopped"]:
        print(f"  ⚠ ESCALATED: {state['stopped']['reason']} (at {state['stopped'].get('at')})")

    # ── mtime gap (informational only) ───────────────────────────────────────
    try:
        gap = time.time() - sp.stat().st_mtime
    except OSError:
        gap = 0.0
    if gap >= 3600:
        print(f"↺ resumed after ~{_fmt_duration(gap)} (run-state may predate a context "
              f"compaction; re-verifying before continuing)")

    # ── Verify-first: re-run the current running step's machine checks ────────
    step, st = _current_running(state)
    if step and step["checks"]:
        print(f"## re-verify: re-running {len(step['checks'])} machine check(s) for "
              f"current step `{step['id']}`")
        prior = {c["cmd"]: c["ok"] for c in st["checks"]}
        results = _run_checks(step["checks"])
        drifted = []
        for r in results:
            note = ""
            if prior.get(r["cmd"]) is True and not r["ok"]:
                note = "  ← DRIFT (was passing, now fails)"
                drifted.append(r["cmd"])
            print(f"  [{'OK ' if r['ok'] else 'NG '}] {r['cmd']}  (exit {r['rc']}){note}")
        # Persist the fresh sensor readings (same side effect as `check`).
        st["checks"] = [{"cmd": r["cmd"], "ok": r["ok"]} for r in results]
        save_state(state, sp)
        if drifted:
            print(f"✗ WORLD DRIFTED: {len(drifted)} previously-passing check(s) now fail. "
                  f"The recorded state is stale — REFUSING to advance. Re-run step "
                  f"`{step['id']}` before continuing.")
            sys.exit(1)
        print("✓ world still matches the recorded state.")

    # ── Continue seamlessly (identical to `next`) ────────────────────────────
    action, msg = compute_next(state)
    save_state(state, sp)
    print(f"▶ {action}: {msg}")
    if action == "ESCALATE":
        sys.exit(1)
    if action == "BLOCKED":
        sys.exit(2)
    if action == "AWAIT_APPROVAL":
        sys.exit(3)


@_locked_secure_state_mutation(_state_path)
def cmd_verdict(args):
    sp = _state_path(args)
    state = load_state(sp)
    _refuse_blocked_state(state)
    step, st = _current_running(state)
    if not step:
        print("[ERROR] no running step.")
        sys.exit(1)
    by, ok, note = None, None, ""
    i = 1
    while i < len(args):
        if args[i] == "--by" and i + 1 < len(args):
            by = args[i + 1]
            i += 2
        elif args[i] == "--pass":
            ok = True
            i += 1
        elif args[i] == "--fail":
            ok = False
            i += 1
        elif args[i] == "--note" and i + 1 < len(args):
            note = args[i + 1]
            i += 2
        else:
            i += 1
    if by is None or ok is None:
        print("[ERROR] --by <verifier-name> and --pass|--fail are required.")
        sys.exit(1)
    st["verdicts"].append({"by": by, "ok": ok, "note": note})
    save_state(state, sp)
    guard = " (independent)" if by.lower() not in ("self", "generator", "producer") else " (⚠ generator itself = invalid)"
    print(f"verdict recorded: step `{step['id']}` by={by}{guard} → {'PASS' if ok else 'FAIL'}. Proceed with `next`.")


@_locked_secure_state_mutation(_state_path)
def cmd_next(args):
    sp = _state_path(args)
    state = load_state(sp)
    _refuse_blocked_state(state)
    action, msg = compute_next(state)
    save_state(state, sp)
    print(f"▶ {action}: {msg}")
    if action == "ESCALATE":
        sys.exit(1)
    if action == "BLOCKED":
        # `_refuse_blocked_state` already exits 2 the *next* time this state is
        # loaded; exiting 0 on the transition that caused it reported a blocked run
        # as a successful one for exactly one invocation.
        sys.exit(2)
    if action == "AWAIT_APPROVAL":
        sys.exit(3)     # parked on a person, not failed


def cmd_status(args):
    sp = _state_path(args)
    state = load_state(sp)
    print(f"## run: {state['recipe']}  cursor={state['cursor']}/{len(state['steps'])}  "
          f"done={state['done']}  stopped={bool(state['stopped'])}")
    for s in state["steps"]:
        st = state["step_state"][s["id"]]
        print(f"  {s['id']:<14} {st['status']:<18} retries={st['retries']} "
              f"checks={sum(1 for c in st['checks'] if c['ok'])}/{len(st['checks'])} "
              f"verdicts={len(st['verdicts'])}"
              + (f" approvals={len(st.get('approvals') or [])}" if st.get("approvals") else ""))
        for line in _stage_gate_lines(s, st):
            print(f"      {line}")


def _stage_gate_lines(step: dict, st: dict) -> list[str]:
    """Human-gate detail for `status` / `approve`. Silent for ungoverned steps, and
    never raises — a status view that dies on a broken policy is useless exactly when
    it is needed (the run's own transitions still refuse to guess; see compute_next)."""
    try:
        status = stage_gate_status(step, st)
    except Exception as e:
        return [f"human gate: cannot be evaluated ({e})"]
    if status is None:
        return []
    lines = list(status.lines())
    if step.get("actor"):
        lines.insert(0, f"actor: {step['actor']}"
                        + (f"  (ran as {st['ran_as']})" if st.get("ran_as") else ""))
    return lines


def _approve_state_path(args) -> pathlib.Path | None:
    if not args:
        return None
    rest = args[1:]
    positional = []
    i = 0
    while i < len(rest):
        if rest[i] in ("--note", "--actor") and i + 1 < len(rest):
            i += 2
        elif rest[i] == "--deny":
            i += 1
        elif not rest[i].startswith("-"):
            positional.append(rest[i])
            i += 1
        else:
            i += 1
    return _state_path(positional)


@_locked_secure_state_mutation(_approve_state_path)
def cmd_approve(args):
    """Cast a human-gate decision on a step of a run (v2.1).

    `orchestrate approve <step-id> [state.json] [--deny] [--note "..."] [--actor NAME]`

    The decision arithmetic is govern.approval's, unchanged: quorum, qualifying
    roles, separation of duties, freshness. This command only decides *where* the
    record is stored (the run-state, beside that step's checks and verdicts) and
    mirrors it into the tamper-evident ledger.
    """
    if not args:
        print("[ERROR] usage: approve <step-id> [state.json] [--deny] [--note \"...\"] [--actor NAME]")
        sys.exit(1)
    sid = args[0]
    rest = args[1:]
    decision, note, actor_override = "approve", "", None
    positional = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--deny":
            decision = "deny"
            i += 1
        elif a == "--note" and i + 1 < len(rest):
            note = rest[i + 1]
            i += 2
        elif a == "--actor" and i + 1 < len(rest):
            actor_override = rest[i + 1]
            i += 2
        elif not a.startswith("-"):
            positional.append(a)
            i += 1
        else:
            i += 1
    sp = _state_path(positional)
    state = load_state(sp)
    step = next((s for s in state["steps"] if s["id"] == sid), None)
    if step is None:
        print(f"[ERROR] no step `{sid}` in this run (steps: "
              f"{', '.join(s['id'] for s in state['steps'])})")
        sys.exit(1)
    st = state["step_state"][sid]

    from ..govern import ledger
    from ..govern.approval import make_decision, upsert
    from ..govern.identity import current_actor, load_org_binding
    from ..govern.policy import PolicyError, effective_policy
    from ..govern.rbac import can, roles_of
    from ..govern.stage import stage_rule
    from .runstate import govern_root

    root = govern_root()
    try:
        eff = effective_policy(root)
    except PolicyError as e:
        print(f"[ERROR] policy layer does not load: {e}")
        sys.exit(1)
    if stage_rule(eff, step) is None:
        print(f"[ERROR] step `{sid}` declares no human gate, and no policy `stage:{sid}` rule "
              "applies — there is nothing to approve here")
        sys.exit(1)
    actor = actor_override or current_actor(root)
    if eff.active:
        allowed = can(eff, actor, "approve")
        if not allowed.allowed:
            print(f"[ERROR] not permitted to approve: {allowed.reason}")
            sys.exit(1)
    if st.get("ran_as") and st["ran_as"] == actor:
        print(f"[WARN] {actor} ran this step; separation of duties means this decision "
              "will not count toward the quorum")

    entry = make_decision(actor=actor, decision=decision, roles=roles_of(eff, actor),
                          head=_git_head(), note=note)
    st["approvals"] = upsert(st.get("approvals") or [], entry)
    binding = load_org_binding(root)
    ledger.append(root, f"stage.{decision}", actor=actor, subject=f"{state['recipe']}:{sid}",
                  org=binding.org, team=binding.team,
                  data={"recipe": state["recipe"], "step": sid, "note": note,
                        "state": str(sp)})

    action, msg = compute_next(state)
    save_state(state, sp)
    print(f"## approve: {state['recipe']}:{sid} — {decision} by {actor}")
    for line in _stage_gate_lines(step, st):
        print(f"  {line}")
    print(f"\n▶ {action}: {msg}")
    if action in ("ESCALATE", "BLOCKED"):
        sys.exit(1)
    sys.exit(3 if action == "AWAIT_APPROVAL" else 0)   # 3 = still parked (quorum unmet / denied)


def _git_head() -> str | None:
    """The current commit, so an approval is bound to what it approved."""
    proc = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                          cwd=str(config.INVOCATION_CWD))
    return proc.stdout.strip() or None if proc.returncode == 0 else None

def cmd_run(args):
    if not args:
        print("[ERROR] usage: run <recipe> --provider <name> [--verifier-provider <name>] "
              "[--provider-cmd \"...{prompt}...\"] [--step-model <step-id>=<model>] "
              "[--secure-provider-config /absolute/path/to/provider-pins.json | "
              "--generator-executable PATH --generator-executable-sha256 HEX "
              "[--generator-interpreter PATH --generator-interpreter-sha256 HEX] "
              "--verifier-executable PATH --verifier-executable-sha256 HEX "
              "[--verifier-interpreter PATH --verifier-interpreter-sha256 HEX]] "
              "[--max-steps N] [--goal G | --goal-stdin] [--check command] "
              "[--review-category general|incident_report|support_reply] "
              "[--material-profile none|technical|conversation] "
              "[--out f] [--isolate] [--auto-route] "
              "[--auto-route-learn [--auto-route-mode shadow|active] [--exploration-pct N] [--exploration-date D]]")
        sys.exit(1)
    path = resolve_recipe(args[0])
    fm, _warns = resolve_extends(parse_frontmatter(path), path)
    artifact_stdout = fm.get("name", path.stem) == "japanese-writing"

    def diagnostic(*items, **kwargs):
        print(*items, file=sys.stderr if artifact_stdout else sys.stdout, **kwargs)

    execution = _require_executable_recipe(fm, fm.get("name", path.stem))
    steps = load_steps(fm)
    gen = ver = None
    generators: list[str] = []
    goal = None
    goal_from_argv = False
    goal_stdin = False
    review_category = None
    material_profile = "none"
    out = pathlib.Path("run-state.json")
    out_explicit = False
    max_steps = 40
    max_parallel = 4
    quorum = "all"
    cfg: dict = {"_token_usage": {}}  # per-run token accumulator (#271/#296); never merged across runs
    step_models: dict[str, str] = {}
    cli_checks: list[str] = []
    i = 1
    while i < len(args):
        a = args[i]
        if a == "--provider" and i + 1 < len(args):
            gen = args[i + 1]
            i += 2
        elif a == "--generators" and i + 1 < len(args):
            generators = [g.strip() for g in args[i + 1].split(",") if g.strip()]
            i += 2
        elif a == "--verifier-provider" and i + 1 < len(args):
            ver = args[i + 1]
            i += 2
        elif a == "--verifier-providers" and i + 1 < len(args):
            ver = [v.strip() for v in args[i + 1].split(",") if v.strip()]
            i += 2
        elif a == "--provider-cmd" and i + 1 < len(args):
            cfg["provider_cmd"] = args[i + 1]
            i += 2
        elif a == "--secure-provider-config" and i + 1 < len(args):
            try:
                cfg["secure_pins"] = load_pin_config(args[i + 1])
            except SecureRuntimeError as error:
                diagnostic(f"[BLOCKED] {error}")
                raise SystemExit(2) from error
            i += 2
        elif a in _SECURE_PIN_FLAGS and i + 1 < len(args):
            role, field = _SECURE_PIN_FLAGS[a]
            cfg.setdefault("secure_pins", {}).setdefault(role, {})[field] = args[i + 1]
            i += 2
        elif a == "--model" and i + 1 < len(args):
            cfg["model"] = args[i + 1]
            i += 2
        elif a == "--step-model" and i + 1 < len(args):
            # Runtime per-step model override (repeatable; #293).
            # Precedence: --step-model > recipe frontmatter `model:` > global --model.
            parsed = parse_step_model_spec(args[i + 1])
            if parsed is None:
                diagnostic(f"[ERROR] --step-model expects <step-id>=<model> (e.g. plan=sonnet), got: {args[i + 1]}")
                sys.exit(1)
            step_models[parsed[0]] = parsed[1]
            i += 2
        elif a == "--base-url" and i + 1 < len(args):
            cfg["base_url"] = args[i + 1]
            i += 2
        elif a in ("--auto-model", "--auto-model-setting"):
            cfg["auto_model"] = True
            i += 1
        elif a == "--goal" and i + 1 < len(args):
            goal = args[i + 1]
            goal_from_argv = True
            i += 2
        elif a == "--goal-stdin":
            goal_stdin = True
            i += 1
        elif a == "--review-category" and i + 1 < len(args):
            review_category = args[i + 1]
            i += 2
        elif a == "--material-profile":
            if i + 1 >= len(args):
                diagnostic(
                    "[BLOCKED] --material-profile requires "
                    "none|technical|conversation"
                )
                raise SystemExit(2)
            material_profile = args[i + 1]
            i += 2
        elif a == "--check" and i + 1 < len(args):
            cli_checks.append(args[i + 1])
            i += 2
        elif a == "--out" and i + 1 < len(args):
            out = pathlib.Path(args[i + 1])
            out_explicit = True
            i += 2
        elif a == "--max-steps" and i + 1 < len(args):
            max_steps = int(args[i + 1])
            i += 2
        elif a == "--max-parallel" and i + 1 < len(args):
            max_parallel = int(args[i + 1])
            i += 2
        elif a == "--quorum" and i + 1 < len(args):
            quorum = args[i + 1]
            i += 2
        elif a == "--isolate":
            cfg["isolate"] = True
            i += 1
        elif a == "--allow-headless-in-cc":
            cfg["allow_headless_in_cc"] = True
            i += 1
        elif a == "--no-session-persistence":
            cfg["claude_no_session_persistence"] = True
            i += 1
        elif a == "--reuse-session":   # #326: generator-only CLI session reuse (opt-in)
            cfg["reuse_session"] = True
            i += 1
        elif a == "--auto-route":
            cfg["auto_route"] = True
            i += 1
        elif a == "--auto-route-learn":     # #305: learned route from historical data (default shadow mode)
            cfg["auto_route_learn"] = True
            i += 1
        elif a == "--auto-route-mode" and i + 1 < len(args):
            cfg["auto_route_mode"] = args[i + 1]  # shadow (default: record prediction only) | active (actually used)
            i += 2
        elif a == "--exploration-pct" and i + 1 < len(args):
            cfg["exploration_pct"] = int(args[i + 1])
            i += 2
        elif a == "--exploration-date" and i + 1 < len(args):
            cfg["exploration_date"] = args[i + 1]  # explicit date/bucket string, not randomness, for determinism
            i += 2
        else:
            i += 1
    if cli_checks:
        cfg["checks"] = list(cli_checks)
        for step in steps:
            if step.get("executor") == "checks-only" and step.get("gate") == "acceptance-gate":
                step["checks"].extend(cli_checks)
    # Unknown step ids abort the run before anything executes (no silent ignores; #293)
    unknown = unknown_step_model_ids(step_models, steps)
    if unknown:
        diagnostic(f"[ERROR] --step-model: unknown step id(s): {', '.join(unknown)} "
                   f"(recipe `{fm.get('name', path.stem)}` steps: {', '.join(s['id'] for s in steps)})")
        sys.exit(1)
    if step_models:
        cfg["step_models"] = step_models
    secure_required = requires_secure_runtime(fm.get("name", path.stem), steps)
    if (
        secure_required
        and fm.get("name", path.stem) == "japanese-writing"
        and review_category not in JAPANESE_WRITING_REVIEW_CATEGORIES
    ):
        diagnostic(
            "[BLOCKED] secure Japanese writing requires --review-category "
            "general|incident_report|support_reply"
        )
        raise SystemExit(2)
    if (
        secure_required
        and fm.get("name", path.stem) == "japanese-writing"
        and material_profile not in JAPANESE_MATERIAL_PROFILES
    ):
        diagnostic(
            "[BLOCKED] secure Japanese writing requires --material-profile "
            "none|technical|conversation"
        )
        raise SystemExit(2)
    material_text = None
    material_metadata = None
    if secure_required and fm.get("name", path.stem) == "japanese-writing":
        try:
            material_text, material_metadata = resolve_japanese_material(
                steps[0], material_profile
            )
        except PackError as error:
            diagnostic(f"[BLOCKED] {error}")
            raise SystemExit(2) from error
    if secure_required and goal_from_argv and goal:
        diagnostic(
            "[BLOCKED] secure-provider-execution refuses --goal because parent argv "
            "is long-lived; provide the goal with --goal-stdin"
        )
        raise SystemExit(2)
    if goal_from_argv and goal_stdin:
        diagnostic("[ERROR] --goal and --goal-stdin are mutually exclusive")
        raise SystemExit(2)
    if secure_required and not goal_stdin:
        diagnostic(
            "[BLOCKED] secure-provider-execution requires a private goal via --goal-stdin"
        )
        raise SystemExit(2)
    if goal_stdin:
        try:
            goal = _read_goal_stdin()
        except SecureRuntimeError as error:
            diagnostic(f"[BLOCKED] {error}")
            raise SystemExit(2) from error
    if not gen and generators:
        gen = generators[0]            # --generators alone is fine (first one as representative)
    if not gen:
        diagnostic("[ERROR] --provider <name> (or --generators a,b,c) is required"
                   " (rig|claude|codex|grok|ollama|lmstudio|cmd|mock). rig = launch each step as a rig harness (recommended)."
                   " ollama/lmstudio = local LLM (server required; pick a model with --model). Use mock for tests.")
        sys.exit(1)

    # ── Guard against accidental launches from inside Claude Code ────────────
    # Using `--provider claude` / `--provider rig` inside a Claude Code session
    # spawns `claude -p` as a subprocess. That counts separately from the already
    # running session and may land subscription usage in a different bucket, or
    # bill an API key if one is configured (environment-dependent).
    # Stop unless `--allow-headless-in-cc` is given explicitly.
    _cc_env = os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_SESSION_ID")
    _headless_claude = gen in ("claude", "rig") or ver in ("claude", "rig") or \
        any(p in ("claude", "rig") for p in generators) or \
        (isinstance(ver, list) and any(p in ("claude", "rig") for p in ver))
    if _cc_env and _headless_claude and not cfg.get("allow_headless_in_cc"):
        diagnostic(
            "[BLOCKED] Inside a Claude Code session, `--provider claude` / `--provider rig` "
            "spawns `claude -p` as a separate subprocess.\n"
            "\n"
            "You are already using Claude in this session, so this risks double-firing and "
            "billing to a different bucket. Switch to one of:\n"
            "\n"
            "  1. Use `/rig:rig \"<task>\"` (manual backend = via the Agent tool, same session)\n"
            "  2. `--provider ollama` / `--provider lmstudio` (local, no billing)\n"
            "  3. `--provider mock` (for tests)\n"
            "  4. If you really must run headless, pass `--allow-headless-in-cc` explicitly\n"
        )
        sys.exit(1)
    ver = ver or gen  # default to the same provider (but a separate process and role)
    if secure_required:
        if generators and (len(generators) != 1 or generators[0] != gen):
            diagnostic(
                "[BLOCKED] secure-provider-execution requires exactly one pinned generator"
            )
            raise SystemExit(2)
        if not out_explicit:
            out = pathlib.Path(".rig") / "secure-runs" / (
                f"run-{time.time_ns()}-{os.getpid()}.json"
            )
        try:
            prepare_output_target(out)
            cfg["_secure_output_lock"] = acquire_output_lock(out)
            material_snapshot = None
            if material_text is not None:
                snapshot_path = out.parent / f".{out.name}.material"
                snapshot_bytes = material_text.encode("utf-8")
                atomic_write_bytes(snapshot_path, snapshot_bytes)
                material_snapshot = {
                    "path": str(snapshot_path.absolute()),
                    "sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
                    "size_bytes": len(snapshot_bytes),
                }
            cfg["_secure_launchers"] = preflight_secure_runtime(gen, ver, cfg)
        except (OSError, SecureRuntimeError) as error:
            close_secure_launchers(cfg.pop("_secure_launchers", None))
            release_output_lock(cfg.pop("_secure_output_lock", None))
            diagnostic(f"[BLOCKED] {error}")
            raise SystemExit(2) from error
        cfg["secure_runtime"] = True
    state = new_state(fm.get("name", path.stem), steps, goal, execution=execution)
    if secure_required and fm.get("name", path.stem) == "japanese-writing":
        state["review_category"] = review_category
        state["material_profile"] = material_profile
        state["material_provenance"] = material_metadata
        state["material_snapshot"] = material_snapshot
        state["history"].append({
            "action": "BIND_REVIEW_CATEGORY",
            "category": review_category,
        })
    if cfg.get("secure_runtime"):
        state["secure_runtime"] = {
            "policy_version": 1,
            "prompt_transport": "stdin",
            **({"review_category": review_category}
               if state.get("recipe") == "japanese-writing" else {}),
            **({"material_profile": material_profile,
                "material_provenance": material_metadata,
                "material_snapshot": material_snapshot}
               if state.get("recipe") == "japanese-writing" else {}),
            "providers": {
                role: {
                    "provider": launcher.provider,
                    "launcher_sha256": list(launcher.launcher_hashes),
                }
                for role, launcher in cfg["_secure_launchers"].items()
            },
        }
    for sid, model in step_models.items():   # record runtime overrides in run-state (traceable later)
        state["history"].append({"action": "STEP_MODEL_OVERRIDE", "step": sid, "model": model})
    iso = None
    if cfg.get("isolate"):
        iso = setup_isolation(fm.get("name", path.stem))
        cfg["cwd"] = iso["dir"]
        state["isolation"] = iso
        diagnostic(f"◈ Isolated run: worktree={iso['dir']} / branch={iso['branch']}")
    diagnostic(render_plan(state["recipe"], steps, execution))
    panel = f" / judge-panel={','.join(generators)}" if len(generators) > 1 else ""
    if isinstance(ver, list):
        panel += f" / model-quorum={','.join(ver)}"
    dag = " / DAG-parallel" if any(s["needs"] for s in steps) else ""
    overrides = ("\nStep-model overrides: "
                 + ", ".join(f"{k}={v}" for k, v in step_models.items())) if step_models else ""
    diagnostic(f"\nAutonomous run: provider={gen} / verifier={'+'.join(ver) if isinstance(ver, list) else ver} / "
               f"max-steps={max_steps} / parallel={max_parallel} / quorum={quorum}{panel}{dag}{overrides}\n")
    try:
        final = run_loop(state, out, gen, ver, cfg, max_steps,
                         max_parallel=max_parallel, quorum=quorum,
                         generators=(generators or None), quiet=artifact_stdout)
        if iso:
            outcome = teardown_isolation(iso, final)
            state["isolation"]["outcome"] = outcome
            save_state(state, out)
            label = {
                "merged": f"gate green → ff-merged {iso['branch']} and removed the worktree",
                "clean-removed": "no changes → removed the worktree",
                "kept": f"worktree and branch preserved (please inspect): {iso['dir']}",
            }[outcome]
            diagnostic(f"◈ Isolated run outcome: {label}")
        diagnostic(f"\n=== Finished: {final} ===  run-state: {out}")
        artifact = state.get("result_artifact")
        if final == "DONE" and isinstance(artifact, dict) and artifact.get("path"):
            diagnostic(f"deliverable: {artifact['path']}")
            if state.get("recipe") == "japanese-writing":
                content = read_result_artifact(state, out)
                if content is None:
                    diagnostic("[ERROR] completed deliverable cannot be read safely")
                    sys.exit(1)
                sys.stdout.write(content)
        if final == "AWAIT_APPROVAL":
            # Parked on a person, not failed. A distinct code so CI can tell "waiting for
            # sign-off" from "the run broke" — reporting either as the other is wrong.
            diagnostic("The run is parked at a human gate. Approve with "
                       f"`rig-wb orchestrate approve <step-id> {out}`, then `resume`.")
            sys.exit(3)
        sys.exit(1 if final in ("ESCALATE", "BLOCKED") else 0)
    finally:
        close_secure_launchers(cfg.pop("_secure_launchers", None))
        release_output_lock(cfg.pop("_secure_output_lock", None))


def _run_ab_variant(recipe_path: pathlib.Path, goal: str | None, gen: str, ver: str,
                    cfg: dict, max_steps: int, max_parallel: int, quorum: str,
                    out_path: pathlib.Path, manifest_src: pathlib.Path | None = None,
                    label: str | None = None) -> dict:
    """Run one variant (recipe) in its own isolated worktree and return a comparison summary
    (#291's `ab` helper). Folds the same execution path as cmd_run
    (setup_isolation -> run_loop -> teardown_isolation) into one function so multiple variants
    can genuinely run concurrently from a ThreadPoolExecutor (each variant has its own worktree,
    so no file collisions; quiet=True avoids interleaved output).

    `manifest_src` (#317, manifest A/B): the given file is written into the
    variant worktree as `.claude/rig.md` and its content hash is recorded in
    the trust store (explicit CLI provision = consent, the same consent model
    --allow-project-manifest uses). Nested provider invocations running with
    cwd=worktree resolve THAT manifest; the main working tree is never touched.
    Honest scope: this parent orchestrate process's own load_manifest() calls
    (e.g. --auto-route size classing) still read the invoking repo's manifest —
    manifest A/B exercises what nested providers see."""
    fm, _warns = resolve_extends(parse_frontmatter(recipe_path), recipe_path)
    execution = _require_executable_recipe(fm, fm.get("name", recipe_path.stem))
    steps = load_steps(fm)
    state = new_state(
        fm.get("name", recipe_path.stem), steps, goal, execution=execution,
    )
    iso = setup_isolation(fm.get("name", recipe_path.stem))
    if manifest_src is not None:
        import hashlib
        dst = pathlib.Path(iso["dir"]) / ".claude" / "rig.md"
        dst.parent.mkdir(parents=True, exist_ok=True)
        content = manifest_src.read_bytes()
        dst.write_bytes(content)
        _record_trust(dst.resolve(), hashlib.sha256(content).hexdigest())
    variant_cfg = {**cfg, "cwd": iso["dir"], "_token_usage": {}}  # per-variant accumulator (#271/#296)
    state["isolation"] = iso
    t0 = time.monotonic()
    final = run_loop(state, out_path, gen, ver, variant_cfg, max_steps,
                     quiet=True, max_parallel=max_parallel, quorum=quorum)
    elapsed = round(time.monotonic() - t0, 1)
    outcome = teardown_isolation(iso, final)
    state["isolation"]["outcome"] = outcome
    save_state(state, out_path)
    retries = sum(st.get("retries", 0) for st in state["step_state"].values())
    return {
        "recipe": label or recipe_path.stem,
        "final": final,
        "elapsed_sec": elapsed,
        "retries": retries,
        "worktree_outcome": outcome,
        "worktree_dir": iso["dir"] if outcome == "kept" else None,
    }


def cmd_ab(args):
    """Run the same goal through multiple recipe variants concurrently and compare
    speed/retries/results (#291).

    Each variant runs in its own isolated worktree, exactly like `cmd_run --isolate` (no file
    collisions), so running them genuinely concurrently (ThreadPoolExecutor) is safe. --provider
    selects the generator/verifier role the same way `run` does — the comparison is about
    recipe differences, not model/provider differences.
    """
    if len(args) < 1:
        print("[ERROR] usage: ab <recipe1> <recipe2> [...] --provider <name> --goal G "
              "[--verifier-provider V] [--max-steps N] [--model M]\n"
              "       ab <recipe> --manifest-a <path> --manifest-b <path> --provider <name> --goal G")
        sys.exit(1)
    recipes: list[str] = []
    i = 0
    while i < len(args) and not args[i].startswith("--"):
        recipes.append(args[i])
        i += 1

    gen = ver = None
    goal = None
    max_steps = 40
    max_parallel = 4
    quorum = "all"
    cfg: dict = {}
    manifest_a = manifest_b = None
    while i < len(args):
        a = args[i]
        if a == "--provider" and i + 1 < len(args):
            gen = args[i + 1]
            i += 2
        elif a == "--verifier-provider" and i + 1 < len(args):
            ver = args[i + 1]
            i += 2
        elif a == "--goal" and i + 1 < len(args):
            goal = args[i + 1]
            i += 2
        elif a == "--max-steps" and i + 1 < len(args):
            max_steps = int(args[i + 1])
            i += 2
        elif a == "--model" and i + 1 < len(args):
            cfg["model"] = args[i + 1]
            i += 2
        elif a == "--manifest-a" and i + 1 < len(args):
            manifest_a = pathlib.Path(args[i + 1])
            i += 2
        elif a == "--manifest-b" and i + 1 < len(args):
            manifest_b = pathlib.Path(args[i + 1])
            i += 2
        else:
            i += 1

    manifest_mode = manifest_a is not None or manifest_b is not None
    if manifest_mode:
        # Rule A/B (#317): same recipe, two manifests. Everything else stays
        # identical so the measured difference is the rules', nothing else's.
        if not (manifest_a and manifest_b):
            print("[ERROR] manifest A/B needs BOTH --manifest-a and --manifest-b")
            sys.exit(1)
        if len(recipes) != 1:
            print("[ERROR] manifest A/B compares one recipe under two manifests — give exactly 1 recipe")
            sys.exit(1)
        for p in (manifest_a, manifest_b):
            if not p.is_file():
                print(f"[ERROR] manifest file '{p}' does not exist")
                sys.exit(1)
    elif len(recipes) < 2:
        print("[ERROR] specify 2 or more recipes to compare")
        sys.exit(1)
    if not gen:
        print("[ERROR] --provider <name> is required (rig|claude|codex|grok|ollama|lmstudio|anthropic|cmd|mock)")
        sys.exit(1)
    ver = ver or gen

    if manifest_mode:
        path = resolve_recipe(recipes[0])
        variants = [(path, manifest_a, f"A({manifest_a.stem})", pathlib.Path("ab-manifest-a-state.json")),
                    (path, manifest_b, f"B({manifest_b.stem})", pathlib.Path("ab-manifest-b-state.json"))]
        title = f"{recipes[0]} under {manifest_a.name} vs {manifest_b.name}"
    else:
        variants = [(resolve_recipe(r), None, None, None) for r in recipes]
        variants = [(p, m, lbl, pathlib.Path(f"ab-{p.stem}-state.json")) for p, m, lbl, _ in variants]
        title = " vs ".join(recipes)
    for path, _manifest, _label, _out_path in variants:
        fm, _warns = resolve_extends(parse_frontmatter(path), path)
        _require_executable_recipe(fm, fm.get("name", path.stem))
    results: list[dict | None] = [None] * len(variants)
    print(f"◈ A/B experiment: {title} (provider={gen} / {len(variants)} concurrent variants)\n")
    with futures.ThreadPoolExecutor(max_workers=len(variants)) as ex:
        fut_to_idx = {
            ex.submit(_run_ab_variant, path, goal, gen, ver, dict(cfg), max_steps, max_parallel, quorum,
                     out_path, manifest, lbl): idx
            for idx, (path, manifest, lbl, out_path) in enumerate(variants)
        }
        for fut in futures.as_completed(fut_to_idx):
            results[fut_to_idx[fut]] = fut.result()

    print(f"## rig ab — {title}\n")
    print(f"{'recipe':<20} {'final':<10} {'elapsed(s)':<12} {'retries':<8} worktree")
    for r in results:
        wt = r["worktree_dir"] or "-"
        print(f"{r['recipe']:<20} {r['final']:<10} {r['elapsed_sec']:<12} {r['retries']:<8} {wt}")
    kept = [r for r in results if r["worktree_outcome"] == "kept"]
    if kept:
        print(f"\n{len(kept)} worktree(s) were preserved (unmet/dirty). After inspecting, clean up with "
              f"`git worktree remove --force <dir>`.")


def _read_jsonl(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def cmd_fleet(args):
    """Aggregate multiple repositories' `.rig/runs.jsonl`/`drill-results.jsonl` across projects (#272).

    Read-only, no side effects — no repository's `.rig/` data is ever written to. Meant for
    orgs/consultancies with multiple projects/clients, to compare per-persona detection power
    across repositories. Repository paths are explicit only (no auto-discovery reaching out
    over a network for anything).
    """
    repos_arg = None
    anonymize = False
    as_json = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--repos" and i + 1 < len(args):
            repos_arg = args[i + 1]
            i += 2
        elif a == "--anonymize":
            anonymize = True
            i += 1
        elif a == "--json":
            as_json = True
            i += 1
        else:
            i += 1
    if not repos_arg:
        print("[ERROR] usage: fleet --repos <path1>,<path2>,... [--anonymize] [--json]")
        sys.exit(1)

    repo_paths = [pathlib.Path(p).expanduser().resolve() for p in repos_arg.split(",") if p.strip()]
    if not repo_paths:
        print("[ERROR] --repos has no valid paths")
        sys.exit(1)

    per_repo = []
    persona_totals: dict[str, dict] = {}
    persona_by_repo: dict[str, dict[str, dict]] = {}
    for idx, rp in enumerate(repo_paths):
        label = f"repo-{idx + 1}" if anonymize else str(rp)
        runs = _read_jsonl(rp / ".rig" / "runs.jsonl")
        drills = _read_jsonl(rp / ".rig" / "drill-results.jsonl")
        done = sum(1 for r in runs if r.get("final") == "DONE")
        repo_personas: dict[str, dict] = {}
        for d in drills:
            for s in d.get("scores", []):
                name = s.get("reviewer", "?")
                g = persona_totals.setdefault(name, {"detected": 0, "seeded": 0})
                g["detected"] += s.get("detected", 0)
                g["seeded"] += s.get("seeded", 0)
                r_ = repo_personas.setdefault(name, {"detected": 0, "seeded": 0})
                r_["detected"] += s.get("detected", 0)
                r_["seeded"] += s.get("seeded", 0)
        persona_by_repo[label] = repo_personas
        per_repo.append({"repo": label, "runs": len(runs), "done": done, "drills": len(drills),
                         "exists": (rp / ".rig").is_dir()})

    def _rate(a: dict) -> float | None:
        return round(a["detected"] / a["seeded"], 3) if a.get("seeded") else None

    result = {
        "repos": per_repo,
        "persona_totals": {name: {**a, "rate": _rate(a)} for name, a in persona_totals.items()},
        "persona_by_repo": {repo: {name: {**a, "rate": _rate(a)} for name, a in personas.items()}
                           for repo, personas in persona_by_repo.items()},
    }
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"## rig fleet — {len(repo_paths)} repos\n")
    print(f"{'repo':<40} {'runs':<8} {'done':<8} drills")
    for r in per_repo:
        note = "" if r["exists"] else "  (no .rig/)"
        print(f"{r['repo']:<40} {r['runs']:<8} {r['done']:<8} {r['drills']}{note}")

    if persona_totals:
        print("\nPer-persona detection rate (summed across all repos):")
        for name, a in sorted(result["persona_totals"].items(), key=lambda kv: -(kv[1]["rate"] or 0)):
            rate = f"{a['rate'] * 100:.0f}%" if a["rate"] is not None else "unmeasured"
            print(f"  {name}: {rate} ({a['detected']}/{a['seeded']})")
        print("\nPer-persona cross-repo comparison (which project detects more/less):")
        for name in sorted(persona_totals):
            per_repo_rates = []
            for repo, personas in persona_by_repo.items():
                a = personas.get(name)
                if a and a.get("seeded"):
                    per_repo_rates.append(f"{repo}={_rate(a) * 100:.0f}%")
            if per_repo_rates:
                print(f"  {name}: " + " / ".join(per_repo_rates))
    else:
        print("\nPer-persona detection rate: unmeasured (no /rig:drill runs in the target repos)")


def collect_auto_route_regret(rows: list) -> list[dict]:
    """Per routed step, how each candidate model actually fared (pure; #357).

    `learned_auto_route` already aggregates this to decide the *next* route, but
    the aggregate was never shown to anyone. Choosing a cheaper tier is a bet,
    and without seeing it settled there is no way to tell a saving from a false
    economy — which is what the README called the missing regret log.

    A regret is claimed only when the comparison is worth acting on: both models
    have enough observations to have earned an opinion, and the pricier one is
    clearly ahead. Reads recorded runs and nothing else.
    """
    from .recipes import _LEARNED_MIN_PASS_RATE, _LEARNED_MIN_SAMPLES, _learned_route_stats

    routed: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        recipe = row.get("recipe")
        if not recipe:
            continue
        for step in _run_steps(row):
            step_id = step.get("id")
            if not step_id:
                continue
            route = step.get("auto_route") or step.get("learned_route") or {}
            if not isinstance(route, dict) or not route.get("model"):
                continue
            chosen = routed.setdefault((recipe, step_id), [])
            if route["model"] not in chosen:
                chosen.append(route["model"])

    report = []
    for (recipe, step_id), chosen_models in sorted(routed.items()):
        stats = _learned_route_stats(rows, recipe, step_id)
        models = [
            {
                "model": model,
                "n": values["n"],
                "passed": values["passed"],
                "pass_rate": round(values["passed"] / values["n"], 4) if values["n"] else None,
                "chosen": model in chosen_models,
                # Declared order is cheapest-first, and stats preserve first-seen
                # order, so a later entry is the pricier bet.
                "rank": index,
            }
            for index, (model, values) in enumerate(stats.items())
        ]
        regrets = []
        for candidate in models:
            if not candidate["chosen"] or candidate["n"] < _LEARNED_MIN_SAMPLES:
                continue
            if candidate["pass_rate"] is None or candidate["pass_rate"] >= _LEARNED_MIN_PASS_RATE:
                continue
            for other in models:
                if (other["rank"] > candidate["rank"]
                        and other["n"] >= _LEARNED_MIN_SAMPLES
                        and other["pass_rate"] is not None
                        and other["pass_rate"] > candidate["pass_rate"]):
                    regrets.append({"chosen": candidate["model"], "better": other["model"]})
                    break
        report.append({
            "recipe": recipe, "step": step_id,
            "models": models, "regrets": regrets,
            "insufficient": all(item["n"] < _LEARNED_MIN_SAMPLES for item in models),
        })
    return report


def _print_auto_route_regret(rows: list) -> None:
    report = collect_auto_route_regret(rows)
    if not report:
        print("No auto-routed steps recorded yet. This report reads `auto_route` / `learned_route`\n"
              "entries appended by runs that used cost-tier routing; until one runs there is\n"
              "nothing to second-guess.")
        return
    print(f"## rig runs --auto-route-regret ({len(report)} routed step(s) across {len(rows)} runs)\n")
    for entry in report:
        print(f"  {entry['recipe']}.{entry['step']}")
        print(f"    {'model':28s} {'n':>4s} {'PASS':>5s} {'PASS%':>7s}")
        for item in entry["models"]:
            mark = "*" if item["chosen"] else " "
            rate = "—" if item["pass_rate"] is None else f"{item['pass_rate'] * 100:6.0f}%"
            print(f"  {mark} {item['model']:28s} {item['n']:4d} {item['passed']:5d} {rate:>7s}")
        if entry["insufficient"]:
            print("    (too few observations to compare — routing is still guessing)")
        for regret in entry["regrets"]:
            print(f"    possible regret: {regret['chosen']} was chosen but {regret['better']} "
                  f"passes more often on this step — the cheaper tier may be costing rework")
        print()
    print("  * = routed to at least once. Read-only: this reports recorded runs and changes no routing.")


# `--personas` counts anything that produced a verdict, but not everything that produces a
# verdict is a reviewer, and the three kinds cannot share a REJECT% column:
#
#   mechanism  a constant emitted by code, not a judgment. `providers._adaptive_budget_verdict`
#              is `ok=False` and only exists when the invocation budget is exhausted, so it
#              reads as 100% REJECT; the `adaptive-repair` verdict is `ok=True` and only exists
#              when a mechanical check exited zero, so it reads as 0% REJECT. Neither number
#              says anything about the code under review.
#   fixture    test scaffolding (`mock:*`), whose rates are whatever a test needed them to be.
#   reviewer   an actual lens whose PASS/REJECT spread is the signal worth reading.
#
# runs.jsonl keeps only `{by, ok}` per verdict (see runstate._verdict_summary), so the kind has
# to be recovered from the name — which also means this classification works on the runs already
# recorded, where the confusion happens.
_MECHANISM_VERIFIERS = frozenset({"adaptive-budget", "adaptive-repair"})
_FIXTURE_PREFIXES = ("mock:",)
_VERIFIER_KIND_HEADINGS = (
    ("reviewer", "reviewers (PASS/REJECT spread is the signal)"),
    ("mechanism", "mechanisms (constant by construction — not a review)"),
    ("fixture", "fixtures (test scaffolding)"),
)


def _run_steps(row: dict) -> list[dict]:
    """The `steps` of one telemetry row, skipping anything that isn't a step object.

    Per SKILL.md §6 the manual and workflow backends append their own lines to
    runs.jsonl, so this log is not written solely by `telemetry_append` — a hand-written
    record can carry `steps: ["review"]` where the schema wants
    `[{"id": ..., "status": ..., "verdicts": [...]}]`. That has already happened once in
    this repo's own log. Reading is best-effort for the same reason broken JSON lines are
    skipped above: one malformed record must not take down aggregation over thousands of
    good ones.
    """
    return [s for s in (row.get("steps") or []) if isinstance(s, dict)]


def _verifier_kind(by: str) -> str:
    if by in _MECHANISM_VERIFIERS:
        return "mechanism"
    if by.startswith(_FIXTURE_PREFIXES):
        return "fixture"
    return "reviewer"


def cmd_runs(args):
    """Run telemetry listing: runs [--limit N] [--recipe R] [--personas] [--html <path>] [--since YYYY-MM-DD].

    Reads .rig/runs.jsonl (appended by telemetry_append; the manual backend appends the same
    format per SKILL.md §6) and prints the latest N runs plus per-recipe aggregates (count,
    DONE rate, average retries, escalation count).
    --personas tallies votes per verifier (the verdict's by), providing input for pruning decisions.
    --auto-route-regret reports, per routed step, how each candidate model actually fared, so a
    cost tier that was chosen but underperformed a pricier one is visible after the fact.
    --html <path> delegates to scripts/dashboard.py to write an HTML dashboard (KPIs, sparkline,
    per-recipe bars, verifier votes, recent-run table in a single-file HTML with no external deps).
    Read-only (the same inspection mode as --list / --validate).
    """
    limit, recipe, personas_mode, html_out, since, cost_mode = 10, None, False, None, None, False
    regret_mode = False
    i = 0
    while i < len(args):
        if args[i] == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1])
            i += 2
        elif args[i] == "--recipe" and i + 1 < len(args):
            recipe = args[i + 1]
            i += 2
        elif args[i] == "--personas":
            personas_mode = True
            i += 1
        elif args[i] == "--cost":
            cost_mode = True
            i += 1
        elif args[i] == "--auto-route-regret":
            regret_mode = True
            i += 1
        elif args[i] == "--html" and i + 1 < len(args):
            html_out = args[i + 1]
            i += 2
        elif args[i] == "--since" and i + 1 < len(args):
            since = args[i + 1]
            i += 2
        else:
            i += 1
    if html_out:
        # Shared resolver: RIG_HOME, then the install source, then cwd. A path
        # computed from this file's parents lands inside site-packages once
        # installed, where there is no scripts/ at all.
        dash = repo_paths.find_script("dashboard.py")
        if dash is None:
            print(f"[ERROR] dashboard.py not found: {repo_paths.script_path('dashboard.py')}")
            sys.exit(1)
        cmd = [sys.executable, str(dash), "--repo", str(config.INVOCATION_CWD),
               "--out", html_out, "--limit", str(limit)]
        if recipe:
            cmd += ["--recipe", recipe]
        if since:
            cmd += ["--since", since]
        rc = subprocess.run(cmd).returncode
        sys.exit(rc)
    if not config.RUNS_PATH.exists():
        print(f"No run records yet ({config.RUNS_PATH}). They are appended by orchestrate run / "
              "queue go, or by completing a manual-backend flow (SKILL.md §6).")
        return
    rows = []
    for line in config.RUNS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # skip broken lines (resilience for an append-only log)
    if recipe:
        rows = [r for r in rows if r.get("recipe") == recipe]
    if not rows:
        print("No matching run records.")
        return

    if personas_mode:
        # Per-verifier tally: aggregate each run's steps[].verdicts[] by their by field,
        # split by kind — a single table mixes three things whose REJECT% mean different
        # things, and reading it as one column produces confident wrong conclusions.
        stats: dict[str, dict] = {}
        for r in rows:
            for st in _run_steps(r):
                for v in st.get("verdicts", []):
                    by = v.get("by") or "?"
                    a = stats.setdefault(by, {"votes": 0, "ok": 0, "reject": 0})
                    a["votes"] += 1
                    a["ok" if v.get("ok") else "reject"] += 1
        if not stats:
            print("No verdict records yet (they accumulate from runs that pass review-gate / acceptance-gate).")
            return
        by_kind: dict[str, list[str]] = {}
        for by in stats:
            by_kind.setdefault(_verifier_kind(by), []).append(by)
        print(f"## rig runs --personas (verifier votes across {len(rows)} runs)\n")
        for kind, heading in _VERIFIER_KIND_HEADINGS:
            names = by_kind.get(kind)
            if not names:
                continue
            print(f"  {heading}")
            print(f"  {'verifier':28s} {'votes':>6s} {'PASS':>6s} {'REJECT':>7s} {'REJECT%':>8s}")
            for by in sorted(names, key=lambda k: -stats[k]["votes"]):
                a = stats[by]
                print(f"  {by:28s} {a['votes']:6d} {a['ok']:6d} {a['reject']:7d} "
                      f"{a['reject'] / a['votes'] * 100:7.0f}%")
            print()
        # Only reviewers can rubber-stamp. A mechanism verdict is constant by construction
        # and a fixture is test scaffolding; flagging either as "no bite" reads as a finding
        # about review quality when it is a fact about the code that emits it.
        rubber = [by for by in by_kind.get("reviewer", ())
                  if stats[by]["votes"] >= 5 and stats[by]["reject"] == 0]
        if rubber:
            print("  Pruning hint: " + ", ".join(sorted(rubber))
                  + " cast 5+ votes without a single REJECT (possible rubber-stamping, or the lens"
                    " has no bite; consider dropping them or sharpening the lens)")
        if by_kind.keys() - {"reviewer"}:
            print("  Kinds are recovered from the verifier name (runs.jsonl keeps only {by, ok});"
                  " an unrecognized name counts as a reviewer, so a new lens is never hidden.")
        return

    if regret_mode:
        _print_auto_route_regret(rows)
        return

    if cost_mode:
        # Per-recipe, per-provider token rollup (#271/#296). CLI providers (claude/codex) don't
        # expose structured usage and stay "unmeasured" — only HTTP providers (ollama/lmstudio/
        # anthropic) are actually metered here.
        by_recipe: dict[str, dict[str, dict]] = {}
        any_usage = False
        fallback_count = refusal_count = 0
        for r in rows:
            tu = r.get("token_usage") or {}
            if tu:
                any_usage = True
                rc = by_recipe.setdefault(r.get("recipe", "?"), {})
                for provider, u in tu.items():
                    a = rc.setdefault(provider, {"prompt_tokens": 0, "completion_tokens": 0,
                                                 "cache_read_input_tokens": 0, "calls": 0})
                    a["prompt_tokens"] += u.get("prompt_tokens", 0)
                    a["completion_tokens"] += u.get("completion_tokens", 0)
                    a["cache_read_input_tokens"] += u.get("cache_read_input_tokens", 0)
                    a["calls"] += u.get("calls", 0)
            for s in _run_steps(r):                            # #297: Fable fallback/refusal occurrence count
                for ev in s.get("fable_events", []):
                    if ev.get("kind") == "fallback":
                        fallback_count += 1
                    elif ev.get("kind") == "refusal":
                        refusal_count += 1
        print(f"## rig runs --cost ({len(rows)} runs)\n")
        if not any_usage:
            print("No token usage recorded (unmeasured). HTTP providers (ollama/lmstudio/anthropic) are metered "
                  "automatically from the usage field. claude/codex run via CLI and "
                  "don't expose structured usage, so they're out of scope here — see Anthropic's Usage & "
                  "Cost Admin API for those instead of estimating.")
        else:
            for rcp, providers in sorted(by_recipe.items()):
                print(f"  {rcp}:")
                for provider, a in sorted(providers.items()):
                    total = a["prompt_tokens"] + a["completion_tokens"]
                    cache = f"  cache_read={a['cache_read_input_tokens']}" if a["cache_read_input_tokens"] else ""
                    print(f"    {provider:16s} calls={a['calls']:4d}  prompt={a['prompt_tokens']:8d}  "
                          f"completion={a['completion_tokens']:8d}  total={total:8d}{cache}")
            # Harness-context load (#319): per-provider prompt weight, derived from the
            # rollup above (no new metering). The prompt includes the user's own task
            # text, so this is an UPPER BOUND on harness overhead, not the overhead
            # itself — separating the injected step-contract/knowledge share would
            # need per-segment metering that doesn't exist yet.
            by_provider: dict[str, dict] = {}
            for providers in by_recipe.values():
                for provider, a in providers.items():
                    t = by_provider.setdefault(provider, {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0})
                    for k in t:
                        t[k] += a[k]
            print("\n  Harness-context load (upper bound — prompts include the task text itself):")
            for provider, t in sorted(by_provider.items()):
                if not t["calls"]:
                    continue
                per_call = t["prompt_tokens"] / t["calls"]
                ratio = (t["prompt_tokens"] / t["completion_tokens"]) if t["completion_tokens"] else float("inf")
                print(f"    {provider:16s} avg prompt/call={per_call:8.0f}  prompt:completion={ratio:.1f}:1")
        if fallback_count or refusal_count:
            print(f"\nFable 5 refusal-classifier (#297): fallback={fallback_count}  direct-refusal={refusal_count}  "
                  "(a fallback is treated as a transparent success and doesn't block the gate; cache_read is the "
                  "fallback-prefix token count billed at 10%)")
        return

    print(f"## rig runs (latest {min(limit, len(rows))} of {len(rows)})\n")
    for r in rows[-limit:]:
        esc = f" / escalated@{r['escalated_at']}" if r.get("escalated_at") else ""
        print(f"  {r.get('ts', '?'):25s} {r.get('recipe', '?'):20s} {r.get('final', '?'):9s} "
              f"steps {r.get('steps_passed', '?')}/{r.get('steps_total', '?')} "
              f"retries {r.get('retries') or 0}{esc}")

    agg: dict[str, dict] = {}
    for r in rows:
        a = agg.setdefault(r.get("recipe", "?"), {"n": 0, "done": 0, "retries": 0, "esc": 0})
        a["n"] += 1
        a["done"] += 1 if r.get("final") == "DONE" else 0
        # `or 0`, not a get() default: the workbench backend records `retries: null`
        # because it has no retry counter, and a key that is present with a null value
        # never reaches the default. dashboard.py:86 already reads it this way.
        a["retries"] += int(r.get("retries") or 0)
        a["esc"] += 1 if r.get("escalated_at") else 0
    print("\n## Per-recipe aggregates\n")
    print(f"  {'recipe':20s} {'runs':>5s} {'DONE%':>7s} {'avg-retry':>9s} {'esc':>4s}")
    for name in sorted(agg):
        a = agg[name]
        print(f"  {name:20s} {a['n']:5d} {a['done'] / a['n'] * 100:6.0f}% "
              f"{a['retries'] / a['n']:9.1f} {a['esc']:4d}")

    # Gap prescriptions: if the same (recipe, step) escalated twice or more, suggest acquiring capability
    # (telemetry → /rig:import --discover / /rig:forge = the entry to the self-completion loop; #268)
    gaps: dict[tuple, int] = {}
    gap_verifiers: dict[tuple, Counter] = {}
    for r in rows:
        esc_at = r.get("escalated_at")
        if not esc_at:
            continue
        key = (r.get("recipe", "?"), esc_at)
        gaps[key] = gaps.get(key, 0) + 1
        # Tally that step's verdicts (who rejected) so the /rig:forge draft can name names.
        for st in _run_steps(r):
            if st.get("id") != esc_at:
                continue
            c = gap_verifiers.setdefault(key, Counter())
            for v in st.get("verdicts", []):
                if not v.get("ok"):
                    c[(v.get("by") or "?").split(":", 1)[-1]] += 1
    hot = {k: v for k, v in gaps.items() if v >= 2}
    if hot:
        print("\n## Gap prescriptions (repeated escalations at the same step; #268)\n")
        for (rcp, sid), n in sorted(hot.items(), key=lambda kv: -kv[1]):
            rejecters = gap_verifiers.get((rcp, sid), Counter())
            who = ", ".join(name for name, _ in rejecters.most_common(3)) or "(no verdicts recorded)"
            forge_desc = (f"capability to resolve the recurring failure in the {sid} step of the "
                          f"{rcp} recipe (most rejections from: {who})")
            print(f"  {rcp} / {sid}: escalated {n} times — most rejections from: {who}")
            print(f"    draft request: /rig:forge \"{forge_desc}\"")
            print("    (after confirming forge's draft, re-measure with /rig:drill --replay)")
            print(f"    (to search for an external skill instead: /rig:import --discover \"skill to strengthen {sid}\")")

def cmd_install_shim(args):
    """Place the shim as a symlink at ~/.local/bin/rig (or the path given via --to).
    Run once; afterwards `rig <subcommand>` works from any directory."""
    target = pathlib.Path("~/.local/bin/rig").expanduser()
    force = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--to" and i + 1 < len(args):
            target = pathlib.Path(args[i + 1]).expanduser()
            i += 2
        elif a in ("--force", "-f"):
            force = True
            i += 1
        else:
            i += 1
    src = config.RIG_HOME / ".claude-plugin" / "bin" / "rig"
    if not src.exists():
        print(f"[ERROR] shim source not found: {src}")
        sys.exit(1)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        if not force:
            print(f"[ERROR] already exists: {target} (overwrite with --force)")
            sys.exit(1)
        target.unlink()
    target.symlink_to(src)
    print(f"✓ symlink: {target} → {src}")
    path_dirs = (os.environ.get("PATH") or "").split(os.pathsep)
    if str(target.parent) not in path_dirs:
        print(f"⚠ {target.parent} does not seem to be on $PATH. Add this:")
        print(f"    export PATH=\"{target.parent}:$PATH\"")
    print(f"Verify: `rig models` or `rig --help` (RIG_HOME={config.RIG_HOME})")
