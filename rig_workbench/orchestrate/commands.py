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
from typing import Protocol

from .. import repo_paths
from ..ports import Clock, Env, Presenter, ProcessRunner
from ..ports.local import CONSOLE, OS_ENV, SUBPROCESS, SYSTEM_CLOCK
from . import config
from . import otel
from . import perf
from .recipes import (_record_trust, auto_orchestrate, git_diff_lines, load_manifest,
                      load_steps, parse_frontmatter, resolve_effective, resolve_extends,
                      resolve_plan_json, resolve_recipe)
from .runstate import compute_next, load_state, new_state, save_state, stage_gate_status
from .providers import metering_note
from .secure_runtime import JAPANESE_WRITING_RECIPES
from .providers import (JAPANESE_MATERIAL_PROFILES, JAPANESE_WRITING_REVIEW_CATEGORIES,
                        record_verdicts, resolve_japanese_material, parse_step_model_spec,
                        read_result_artifact, run_loop, unknown_step_model_ids)
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
from .batch_surface import KNOWN_PROJECTS
from .govern_surfaces import GOVERN_SURFACES
from .pack_surfaces import PackError


class StageGovernance(Protocol):
    """What `approve` needs of the governance layer.

    Casting a decision on a step asks governance six things: does the policy layer load,
    does anything gate this step, who is casting, may they, what record does that make, and
    where does it get mirrored. Not one of those is the orchestrator's arithmetic — quorum,
    qualifying roles, separation of duties and freshness are `govern.approval`'s, the
    org→team→project tightening is `govern.policy`'s, and the hash chain that makes the
    audit trail tamper-evident is `govern.ledger`'s. This command decides only *where* the
    decision is stored: in the run-state, beside that step's checks and verdicts.

    Stated as a protocol rather than imported, because the import is what
    `tests/test_layering_contract.py` forbids: a judgement module may reach the standard
    library, its own pillar and the six ports, and six `govern` modules are none of those.
    They were reached from inside this command's body, which hid the edges rather than
    removing them. `govern_surfaces.GOVERN_SURFACES` satisfies this shape and is what every
    shipped caller passes.

    **The policy crosses as a token.** `policy()` hands back a value this module holds and
    hands straight back; every question about it is a method here. So no signature in this
    file is written in another pillar's vocabulary, and `PolicyError` — which used to stand
    in an `except` clause, where a class is an edge exactly as much as a function in a call
    — stops at the adapter, arriving instead as the refusal message this command was going
    to print anyway.
    """

    def policy(self, root: pathlib.Path) -> tuple[object, str | None]:
        """The effective policy at `root`, or `(None, why it does not load)`."""
        ...

    def has_stage_rule(self, policy: object, step: dict) -> bool:
        """Whether any approval rule governs this step."""
        ...

    def current_actor(self, root: pathlib.Path) -> str:
        """The identity performing this action."""
        ...

    def refusal(self, policy: object, actor: str, permission: str) -> str | None:
        """Why `actor` may not exercise `permission`, or None when they may."""
        ...

    def decision(self, policy: object, *, actor: str, decision: str, head: str | None,
                 note: str) -> dict:
        """One decision record, carrying the roles this actor holds."""
        ...

    def upsert(self, decisions: list[dict], entry: dict) -> list[dict]:
        """`entry` added, replacing any earlier decision by the same actor."""
        ...

    def record(self, root: pathlib.Path, action: str, *, actor: str, subject: str,
               data: dict) -> None:
        """Mirror one governance event into the tamper-evident ledger."""
        ...


class ProjectIndex(Protocol):
    """What `fleet --discovered` needs of the cross-project run log.

    One question: which repositories has rig actually run in. The answer is a projection of
    `~/.rig/runs.jsonl`, which is the workbench's file with the workbench's rules about
    truncated lines and collapsed records — reading it here would be a second reader of one
    log, and the second reader is the one that goes stale.

    Stated as a protocol rather than imported, because the import is what
    `tests/test_layering_contract.py` forbids: a judgement module may reach the standard
    library, its own pillar and the six ports, and `workbench.run_index` is none of those.
    It was reached from inside this command's body, which hid the edge rather than removing
    it. `batch_surface.KNOWN_PROJECTS` satisfies this shape and is what every shipped caller
    passes.
    """

    def __call__(self) -> list[str]:
        """Every repository that has recorded a run, newest first."""
        ...


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


class Refusal(Exception):
    """A refusal decided here and reported by the command the caller invoked.

    The `validation` pillar's shape, for the same reason it was adopted there. Three
    judgement helpers below — `_require_executable_recipe`, `_refuse_blocked_state` and
    the lock guard in `_locked_secure_state_mutation` — used to `print` and then
    `raise SystemExit`, which is a decision about a user's stdout taken where no caller
    can see it, let alone intercept it. They raise this instead; `_reports_refusals`
    turns it back into the same lines on the same stream and the same exit status, at
    the one place that holds the `Presenter` the shell built.

    `lines` is a list rather than one string because `_require_executable_recipe` emits
    a heading and then one line per structural error, and `Presenter.out` is a line at a
    time. `code` travels with the refusal because the exit status is part of what is
    being refused, not a property of whoever catches it.
    """

    def __init__(self, lines, code: int = 2) -> None:
        self.lines = list(lines)
        self.code = code
        super().__init__(self.lines[0] if self.lines else "")


def _reports_refusals(command):
    """Report a `Refusal` through this command's `Presenter`, then exit as before.

    Applied outside `_locked_secure_state_mutation` so that the lock guard's own refusal
    is reported too, and so the lock is released on the way out — the guard's `finally`
    runs while the exception is still travelling.

    `kwargs.get("out", CONSOLE)` rather than a required parameter: `packs/cli.py` calls
    `commands.cmd_run([...])` positionally and `selftest.py` calls `cmd_resume` /
    `cmd_runs` the same way, and both must keep printing exactly what they printed.
    """
    @wraps(command)
    def reporting(args, **kwargs):
        try:
            return command(args, **kwargs)
        except Refusal as refusal:
            reporter: Presenter = kwargs.get("out") or CONSOLE
            for line in refusal.lines:
                reporter.out(line)
            raise SystemExit(refusal.code) from refusal

    return reporting


def _require_executable_recipe(fm: dict, label: str) -> dict:
    execution = validate_executable_recipe(fm)
    if execution["orchestratable"]:
        return execution
    prefix = "[ERROR]" if execution["errors"] else "[BLOCKED]"
    raise Refusal(
        [f"{prefix} recipe {label} is computationally nonexecutable: {execution['reason']}",
         *(f"[ERROR] {error}" for error in execution["errors"])],
        code=2,
    )


def cmd_plan(args, *, out: Presenter = CONSOLE):
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
        out.out(json.dumps(plan, ensure_ascii=False, indent=2))
        if plan.get("errors"):
            sys.exit(1)  # same exit contract as the non-JSON path
        return
    out.out(render_plan(plan["recipe"], plan["steps"], plan.get("execution")))
    for w in plan.get("warnings", []):
        out.out(f"[WARN] {w}")
    for e in plan.get("errors", []):
        out.out(f"[ERROR] {e}")
    if plan.get("errors"):
        sys.exit(1)


def _state_path(args, default="run-state.json") -> pathlib.Path:
    return pathlib.Path(args[0]) if args else pathlib.Path(default)


def _locked_secure_state_mutation(path_from_args):
    """Hold the secure run lock across a command's complete mutation window."""
    def decorate(command):
        @wraps(command)
        def guarded(args, **kwargs):
            state_path = path_from_args(args)
            if state_path is None:
                return command(args, **kwargs)
            initial = load_state(state_path)
            if not initial.get("secure_runtime"):
                return command(args, **kwargs)
            try:
                descriptor = acquire_output_lock(state_path)
            except OSError as error:
                raise Refusal([f"[BLOCKED] {error}"], code=2) from error
            try:
                # Reload after locking: another short mutation may have completed
                # between the optimistic first read and our successful lock.
                return command(args, **kwargs)
            finally:
                release_output_lock(descriptor)

        return guarded
    return decorate


@_reports_refusals
def cmd_init(args, *, out: Presenter = CONSOLE):
    path = resolve_recipe(args[0])
    fm, _warns = resolve_extends(parse_frontmatter(path, out=out), path)
    execution = _require_executable_recipe(fm, fm.get("name", path.stem))
    steps = load_steps(fm)
    goal = None
    out_path = pathlib.Path("run-state.json")
    i = 1
    while i < len(args):
        if args[i] == "--goal" and i + 1 < len(args):
            goal = args[i + 1]
            i += 2
        elif args[i] == "--out" and i + 1 < len(args):
            out_path = pathlib.Path(args[i + 1])
            i += 2
        else:
            i += 1
    state = new_state(fm.get("name", path.stem), steps, goal, execution=execution)
    save_state(state, out_path)
    out.out(render_plan(state["recipe"], steps, execution))
    out.out(f"\nrun-state: {out_path}")
    action, msg = compute_next(state)
    save_state(state, out_path)
    out.out(f"\n▶ {action}: {msg}")


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
        raise Refusal(
            [f"[BLOCKED] {stopped.get('reason', 'run-state is computationally nonexecutable')}"],
            code=2,
        )


def _run_checks(checks: list[str]) -> list[dict]:
    """Run each declared shell check in INVOCATION_CWD; return [{cmd, ok, rc}] records.

    The single source of truth for the machine-sensor subprocess loop: both `check`
    and `resume` call this so they stay byte-for-byte identical (same shell, cwd, and
    stdout/stderr suppression). Pure I/O — no printing, no state mutation.
    """
    results = []
    for cmd in checks:
        # noqa below is permanent: `ProcessRunner` has no `shell=` and always captures.
        # Piping a user-specified check instead of discarding it would make rig hold that
        # command's unbounded output in memory for a return code it is the only thing read.
        r = subprocess.run(cmd, shell=True, cwd=str(config.INVOCATION_CWD),  # noqa: TID251
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        results.append({"cmd": cmd, "ok": (r.returncode == 0), "rc": r.returncode})
    return results


@_reports_refusals
@_locked_secure_state_mutation(_state_path)
def cmd_check(args, *, out: Presenter = CONSOLE):
    sp = _state_path(args)
    state = load_state(sp)
    _refuse_blocked_state(state)
    step, st = _current_running(state)
    if not step:
        out.out("[ERROR] no running step. START one with `next` first.")
        sys.exit(1)
    if not step["checks"]:
        out.out(f"step `{step['id']}` declares no checks: (no machine verification). Use verdict instead.")
        return
    out.out(f"## check: machine sensors for step `{step['id']}` ({len(step['checks'])} checks)")
    results = _run_checks(step["checks"])
    st["checks"] = [{"cmd": r["cmd"], "ok": r["ok"]} for r in results]
    all_ok = all(r["ok"] for r in results)
    for r in results:
        out.out(f"  [{'OK ' if r['ok'] else 'NG '}] {r['cmd']}  (exit {r['rc']})")
    save_state(state, sp)
    out.out(f"→ {'all OK' if all_ok else 'some NG'}. Compute the transition with `next`.")


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


@_reports_refusals
@_locked_secure_state_mutation(_state_path)
def cmd_resume(args, *, out: Presenter = CONSOLE, clock: Clock = SYSTEM_CLOCK):
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
    out.out(f"## resume: {state['recipe']}  cursor={state['cursor']}/{total}  "
            f"done={n_passed}/{total}  stopped={bool(state['stopped'])}")
    for s in steps:
        st = state["step_state"][s["id"]]
        rejects = [v for v in st["verdicts"] if not v.get("ok")]
        tail = (f"  ⚠ {len(rejects)} REJECT (by {', '.join(str(v.get('by')) for v in rejects)})"
                if rejects else "")
        out.out(f"  {s['id']:<14} {st['status']:<18} "
                f"checks={sum(1 for c in st['checks'] if c['ok'])}/{len(st['checks'])} "
                f"verdicts={len(st['verdicts'])}{tail}")
        if st["status"] == "awaiting_approval":
            for line in _stage_gate_lines(s, st):
                out.out(f"      {line}")
    if state["stopped"]:
        out.out(f"  ⚠ ESCALATED: {state['stopped']['reason']} (at {state['stopped'].get('at')})")

    # ── mtime gap (informational only) ───────────────────────────────────────
    try:
        gap = clock.now().timestamp() - sp.stat().st_mtime
    except OSError:
        gap = 0.0
    if gap >= 3600:
        out.out(f"↺ resumed after ~{_fmt_duration(gap)} (run-state may predate a context "
                f"compaction; re-verifying before continuing)")

    # ── Verify-first: re-run the current running step's machine checks ────────
    step, st = _current_running(state)
    if step and step["checks"]:
        out.out(f"## re-verify: re-running {len(step['checks'])} machine check(s) for "
                f"current step `{step['id']}`")
        prior = {c["cmd"]: c["ok"] for c in st["checks"]}
        results = _run_checks(step["checks"])
        drifted = []
        for r in results:
            note = ""
            if prior.get(r["cmd"]) is True and not r["ok"]:
                note = "  ← DRIFT (was passing, now fails)"
                drifted.append(r["cmd"])
            out.out(f"  [{'OK ' if r['ok'] else 'NG '}] {r['cmd']}  (exit {r['rc']}){note}")
        # Persist the fresh sensor readings (same side effect as `check`).
        st["checks"] = [{"cmd": r["cmd"], "ok": r["ok"]} for r in results]
        save_state(state, sp)
        if drifted:
            out.out(f"✗ WORLD DRIFTED: {len(drifted)} previously-passing check(s) now fail. "
                    f"The recorded state is stale — REFUSING to advance. Re-run step "
                    f"`{step['id']}` before continuing.")
            sys.exit(1)
        out.out("✓ world still matches the recorded state.")

    # ── Continue seamlessly (identical to `next`) ────────────────────────────
    action, msg = compute_next(state)
    save_state(state, sp)
    out.out(f"▶ {action}: {msg}")
    if action == "ESCALATE":
        sys.exit(1)
    if action == "BLOCKED":
        sys.exit(2)
    if action == "AWAIT_APPROVAL":
        sys.exit(3)


@_reports_refusals
@_locked_secure_state_mutation(_state_path)
def cmd_verdict(args, *, out: Presenter = CONSOLE):
    sp = _state_path(args)
    state = load_state(sp)
    _refuse_blocked_state(state)
    step, st = _current_running(state)
    if not step:
        out.out("[ERROR] no running step.")
        sys.exit(1)
    by, ok, note = None, None, ""
    criteria = []
    seen_criteria = set()

    def reject(message):
        out.out(f"[ERROR] {message}")
        sys.exit(1)

    i = 1
    while i < len(args):
        if args[i] == "--by":
            if i + 1 >= len(args) or args[i + 1].startswith("--"):
                reject("--by requires a verifier name.")
            by = args[i + 1]
            i += 2
        elif args[i] == "--pass":
            ok = True
            i += 1
        elif args[i] == "--fail":
            ok = False
            i += 1
        elif args[i] == "--note":
            if i + 1 >= len(args) or args[i + 1].startswith("--"):
                reject("--note requires text.")
            note = args[i + 1]
            i += 2
        elif args[i] == "--criterion":
            if i + 1 >= len(args) or args[i + 1].startswith("--"):
                reject("--criterion requires <integer>=PASS|FAIL|UNKNOWN.")
            answer = args[i + 1]
            if "=" not in answer:
                reject("--criterion requires <integer>=PASS|FAIL|UNKNOWN.")
            raw_n, raw_verdict = answer.split("=", 1)
            if not raw_n.isascii() or not raw_n.isdecimal():
                reject(f"criterion number must be an integer: {raw_n!r}.")
            n = int(raw_n)
            verdict = raw_verdict.upper()
            if verdict not in ("PASS", "FAIL", "UNKNOWN"):
                reject(f"criterion {n} answer must be PASS, FAIL, or UNKNOWN.")
            declared = step.get("acceptance") or []
            if not 1 <= n <= len(declared):
                reject(f"criterion {n} is out of range; step declares 1..{len(declared)}.")
            if n in seen_criteria:
                reject(f"duplicate criterion {n}.")
            seen_criteria.add(n)
            criteria.append({"n": n, "verdict": verdict, "anchor": ""})
            i += 2
        else:
            reject(f"unknown verdict argument: {args[i]}")
    if by is None or ok is None:
        out.out("[ERROR] --by <verifier-name> and --pass|--fail are required.")
        sys.exit(1)
    record_verdicts(step, st, [{"by": by, "ok": ok, "note": note,
                                "criteria": criteria}])
    save_state(state, sp)
    guard = " (independent)" if by.lower() not in ("self", "generator", "producer") else " (⚠ generator itself = invalid)"
    out.out(f"verdict recorded: step `{step['id']}` by={by}{guard} → {'PASS' if ok else 'FAIL'}. Proceed with `next`.")


@_reports_refusals
@_locked_secure_state_mutation(_state_path)
def cmd_next(args, *, out: Presenter = CONSOLE):
    sp = _state_path(args)
    state = load_state(sp)
    _refuse_blocked_state(state)
    action, msg = compute_next(state)
    save_state(state, sp)
    out.out(f"▶ {action}: {msg}")
    if action == "ESCALATE":
        sys.exit(1)
    if action == "BLOCKED":
        # `_refuse_blocked_state` already exits 2 the *next* time this state is
        # loaded; exiting 0 on the transition that caused it reported a blocked run
        # as a successful one for exactly one invocation.
        sys.exit(2)
    if action == "AWAIT_APPROVAL":
        sys.exit(3)     # parked on a person, not failed


def cmd_status(args, *, out: Presenter = CONSOLE):
    sp = _state_path(args)
    state = load_state(sp)
    out.out(f"## run: {state['recipe']}  cursor={state['cursor']}/{len(state['steps'])}  "
            f"done={state['done']}  stopped={bool(state['stopped'])}")
    for s in state["steps"]:
        st = state["step_state"][s["id"]]
        out.out(f"  {s['id']:<14} {st['status']:<18} retries={st['retries']} "
                f"checks={sum(1 for c in st['checks'] if c['ok'])}/{len(st['checks'])} "
                f"verdicts={len(st['verdicts'])}"
                + (f" approvals={len(st.get('approvals') or [])}" if st.get("approvals") else ""))
        for line in _stage_gate_lines(s, st):
            out.out(f"      {line}")


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


@_reports_refusals
@_locked_secure_state_mutation(_approve_state_path)
def cmd_approve(args, *, out: Presenter = CONSOLE,
                governance: StageGovernance = GOVERN_SURFACES):
    """Cast a human-gate decision on a step of a run (v2.1).

    `orchestrate approve <step-id> [state.json] [--deny] [--note "..."] [--actor NAME]`

    The decision arithmetic is govern.approval's, unchanged: quorum, qualifying
    roles, separation of duties, freshness. This command only decides *where* the
    record is stored (the run-state, beside that step's checks and verdicts) and
    mirrors it into the tamper-evident ledger.
    """
    if not args:
        out.out("[ERROR] usage: approve <step-id> [state.json] [--deny] [--note \"...\"] [--actor NAME]")
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
        out.out(f"[ERROR] no step `{sid}` in this run (steps: "
                f"{', '.join(s['id'] for s in state['steps'])})")
        sys.exit(1)
    st = state["step_state"][sid]

    from .runstate import govern_root

    root = govern_root()
    eff, unloadable = governance.policy(root)
    if unloadable is not None:
        out.out(f"[ERROR] policy layer does not load: {unloadable}")
        sys.exit(1)
    if not governance.has_stage_rule(eff, step):
        out.out(f"[ERROR] step `{sid}` declares no human gate, and no policy `stage:{sid}` rule "
                "applies — there is nothing to approve here")
        sys.exit(1)
    actor = actor_override or governance.current_actor(root)
    refused = governance.refusal(eff, actor, "approve")
    if refused is not None:
        out.out(f"[ERROR] not permitted to approve: {refused}")
        sys.exit(1)
    if st.get("ran_as") and st["ran_as"] == actor:
        out.out(f"[WARN] {actor} ran this step; separation of duties means this decision "
                "will not count toward the quorum")

    entry = governance.decision(eff, actor=actor, decision=decision,
                                head=_git_head(), note=note)
    st["approvals"] = governance.upsert(st.get("approvals") or [], entry)
    governance.record(root, f"stage.{decision}", actor=actor,
                      subject=f"{state['recipe']}:{sid}",
                      data={"recipe": state["recipe"], "step": sid, "note": note,
                            "state": str(sp)})

    action, msg = compute_next(state)
    save_state(state, sp)
    out.out(f"## approve: {state['recipe']}:{sid} — {decision} by {actor}")
    for line in _stage_gate_lines(step, st):
        out.out(f"  {line}")
    out.out(f"\n▶ {action}: {msg}")
    if action in ("ESCALATE", "BLOCKED"):
        sys.exit(1)
    sys.exit(3 if action == "AWAIT_APPROVAL" else 0)   # 3 = still parked (quorum unmet / denied)


def _git_head(*, proc: ProcessRunner = SUBPROCESS) -> str | None:
    """The current commit, so an approval is bound to what it approved.

    `GitRepo.head()` answers exactly this question and is **deliberately not** used here.
    It goes through `gitroot._git`, which strips `GIT_DIR` / `GIT_WORK_TREE` /
    `GIT_COMMON_DIR` first — so swapping it in would change which repository this reads
    whenever those are set, which is the #471 fix and not a port swap.
    `rig_workbench/ports/local.py`'s `GitCli` docstring says so in as many words: that
    change belongs in the commit that makes it, with its own test. This one keeps asking
    the question the way it is asked today, through the runner rather than around it.
    """
    result = proc.run(["git", "rev-parse", "HEAD"], cwd=str(config.INVOCATION_CWD))
    return result.stdout.strip() or None if result.returncode == 0 else None

@_reports_refusals
def cmd_run(args, *, out: Presenter = CONSOLE, env: Env = OS_ENV):
    if not args:
        out.out("[ERROR] usage: run <recipe> --provider <name> [--verifier-provider <name>] "
                "[--provider-cmd \"...{prompt}...\"] [--step-model <step-id>=<model>] "
                "[--secure-provider-config /absolute/path/to/provider-pins.json | "
                "--generator-executable PATH --generator-executable-sha256 HEX "
                "[--generator-interpreter PATH --generator-interpreter-sha256 HEX] "
                "--verifier-executable PATH --verifier-executable-sha256 HEX "
                "[--verifier-interpreter PATH --verifier-interpreter-sha256 HEX]] "
                "[--max-steps N] [--goal G | --goal-stdin] [--check command] "
                "[--review-category general|incident_report|support_reply] "
                "[--material-profile none|technical|conversation] "
                "[--out f] [--timeout seconds] [--isolate] [--auto-route] "
                "[--auto-route-learn [--auto-route-mode shadow|active] [--exploration-pct N] [--exploration-date D]]")
        sys.exit(1)
    path = resolve_recipe(args[0])
    fm, _warns = resolve_extends(parse_frontmatter(path, out=out), path)
    artifact_stdout = fm.get("name", path.stem) in JAPANESE_WRITING_RECIPES

    def diagnostic(text: str = "") -> None:
        """Progress and errors, on whichever stream is not carrying the artifact.

        A Japanese-writing recipe puts the finished text on stdout and nothing else, so
        everything the run says about itself has to move aside; every other recipe says it
        on stdout as always. That is the whole of the conditional, and it is the reason
        this is a call and not a `print(..., file=...)`: `Presenter` has `out` and `err`
        and deliberately no `warn()`, so the choice of stream stays visible here rather
        than being smuggled into the port.
        """
        (out.err if artifact_stdout else out.out)(text)

    execution = _require_executable_recipe(fm, fm.get("name", path.stem))
    steps = load_steps(fm)
    gen = ver = None
    generators: list[str] = []
    goal = None
    goal_from_argv = False
    goal_stdin = False
    review_category = None
    material_profile = "none"
    out_path = pathlib.Path("run-state.json")
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
        elif a == "--timeout":
            if i + 1 >= len(args):
                diagnostic("[ERROR] --timeout requires a positive integer number of seconds")
                raise SystemExit(1)
            try:
                timeout = int(args[i + 1])
            except ValueError:
                diagnostic("[ERROR] --timeout requires a positive integer number of seconds")
                raise SystemExit(1)
            if timeout <= 0:
                diagnostic("[ERROR] --timeout requires a positive integer number of seconds")
                raise SystemExit(1)
            cfg["timeout"] = timeout
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
            out_path = pathlib.Path(args[i + 1])
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
        elif a == "--auto-route":
            cfg["auto_route"] = True
            i += 1
        elif a == "--reuse-session":       # #326: generator-only CLI conversation reuse (opt-in)
            cfg["reuse_session"] = True
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
        # Was `executor == "checks-only" and gate == "acceptance-gate"`. That pair is now
        # refused before the run starts (a verdict-less executor cannot carry a runtime
        # gate), so the old condition matched nothing and `--check` would have been
        # accepted, echoed, and silently dropped on the floor.
        #
        # Narrowing the condition fixed one silent drop and left the other half of it:
        # a recipe with no `checks-only` step swallowed `--check` without a word.
        # Neither `feature` nor `bugfix` has such a step, so `--check "pytest -q"` on
        # the two most-used recipes attaches to nothing. `cfg["checks"]` still carries
        # the commands, but its only readers are the adaptive executors
        # (`_adaptive_check_allowlist`), so on a recipe with no executor steps the flag
        # genuinely does nothing.
        #
        # This warns rather than aborting. `--step-model` below exits because an unknown
        # step id is a typo — the run cannot mean what was typed. `--check` on an
        # agent-step recipe is a category mismatch, not a mistyped name, and a passing
        # test pins the current exit behaviour, so the fix here is to stop being silent
        # rather than to change what the option does.
        checks_only = [s for s in steps if s.get("executor") == "checks-only"]
        if not checks_only:
            diagnostic(f"[WARN] --check: recipe `{fm.get('name', path.stem)}` has no step "
                       f"with `executor: checks-only`, so {len(cli_checks)} command(s) "
                       f"will not be run as a step check "
                       f"(steps: {', '.join(s['id'] for s in steps)})")
        for step in checks_only:
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
        and fm.get("name", path.stem) in JAPANESE_WRITING_RECIPES
        and review_category not in JAPANESE_WRITING_REVIEW_CATEGORIES
    ):
        diagnostic(
            "[BLOCKED] secure Japanese writing requires --review-category "
            "general|incident_report|support_reply"
        )
        raise SystemExit(2)
    if (
        secure_required
        and fm.get("name", path.stem) in JAPANESE_WRITING_RECIPES
        and material_profile not in JAPANESE_MATERIAL_PROFILES
    ):
        diagnostic(
            "[BLOCKED] secure Japanese writing requires --material-profile "
            "none|technical|conversation"
        )
        raise SystemExit(2)
    material_text = None
    material_metadata = None
    if secure_required and fm.get("name", path.stem) in JAPANESE_WRITING_RECIPES:
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
    _cc_env = env.get("CLAUDECODE") or env.get("CLAUDE_CODE_SESSION_ID")
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
            # Not a `Clock` read, and deliberately still not one. The clock is the
            # *uniqueness source* here — pid plus nanoseconds is what keeps two secure runs
            # from naming the same sealed artifact, which is then exclusively locked. A port
            # method for this is one a substituted or frozen clock turns into a collision,
            # which is the opposite of what a port is for. `runstate.make_run_id` shows the
            # shape that does belong on the port: the clock for ordering, `secrets` for
            # uniqueness — and adopting it here would change this filename, which is a
            # decision about an artifact path and not a migration.
            out_path = pathlib.Path(".rig") / "secure-runs" / (
                f"run-{time.time_ns()}-{os.getpid()}.json"
            )
        try:
            prepare_output_target(out_path)
            cfg["_secure_output_lock"] = acquire_output_lock(out_path)
            material_snapshot = None
            if material_text is not None:
                snapshot_path = out_path.parent / f".{out_path.name}.material"
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
        # Same signal the reviewer's requirement is derived from. Keying the supply
        # off the file's name while the requirement reads the step's instruction
        # made a project overlay under another filename require a draft it could
        # never be handed — fail-closed, but on a mismatch nobody would look for.
        if steps and isinstance(steps[0], dict) \
                and steps[0].get("instruction") == "japanese-revise-draft":
            cfg["_source_draft"] = goal
    state = new_state(fm.get("name", path.stem), steps, goal, execution=execution)
    # Which providers this run was configured with, as data on the run rather than a fact
    # that lived only in argv. `runs.jsonl` had no provider field at all (#501): the OTel
    # projection could not label a run by who generated or who verified it, and the only
    # trace of the verifier was the `provider:persona` inside each verdict.
    state["providers"] = {
        "generator": gen,
        **({"generators": list(generators)} if generators else {}),
        "verifier": list(ver) if isinstance(ver, list) else ver,
        **({"model": cfg["model"]} if cfg.get("model") else {}),
    }
    if secure_required and fm.get("name", path.stem) in JAPANESE_WRITING_RECIPES:
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
            **({"goal_sha256": hashlib.sha256(goal.encode("utf-8")).hexdigest()}
               if isinstance(goal, str) else {}),
            **({"review_category": review_category}
               if state.get("recipe") in JAPANESE_WRITING_RECIPES else {}),
            **({"material_profile": material_profile,
                "material_provenance": material_metadata,
                "material_snapshot": material_snapshot}
               if state.get("recipe") in JAPANESE_WRITING_RECIPES else {}),
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
    # A declared perf budget (#502) rides on cfg so run_loop can warn the moment a run breaks
    # it. Read from the manifest rather than a flag: a budget is a property of the project, and
    # one that had to be passed on the command line would only ever be checked deliberately.
    manifest_budget = load_manifest().get("perf_budget")
    if isinstance(manifest_budget, dict):
        cfg["perf_budget"] = manifest_budget
    diagnostic(render_plan(state["recipe"], steps, execution))
    panel = f" / judge-panel={','.join(generators)}" if len(generators) > 1 else ""
    if isinstance(ver, list):
        panel += f" / model-quorum={','.join(ver)}"
    dag = " / DAG-parallel" if any(s["needs"] for s in steps) else ""
    overrides = ("\nStep-model overrides: "
                 + ", ".join(f"{k}={v}" for k, v in step_models.items())) if step_models else ""
    diagnostic(f"\nAutonomous run: provider={gen} / verifier={'+'.join(ver) if isinstance(ver, list) else ver} / "
               f"max-steps={max_steps} / parallel={max_parallel} / quorum={quorum}{panel}{dag}{overrides}")
    # Whether this run's spend will be measured, said where somebody decides to start it (#532).
    # Rig could always answer this, but only if you went and asked `runs --cost` afterwards.
    diagnostic(metering_note([*(generators or [gen]), *(ver if isinstance(ver, list) else [ver])])
               + "\n")
    try:
        final = run_loop(state, out_path, gen, ver, cfg, max_steps,
                         max_parallel=max_parallel, quorum=quorum,
                         generators=(generators or None), quiet=artifact_stdout)
        if iso:
            outcome = teardown_isolation(iso, final)
            state["isolation"]["outcome"] = outcome
            save_state(state, out_path)
            label = {
                "merged": f"gate green → ff-merged {iso['branch']} and removed the worktree",
                "clean-removed": "no changes → removed the worktree",
                "kept": f"worktree and branch preserved (please inspect): {iso['dir']}",
            }[outcome]
            diagnostic(f"◈ Isolated run outcome: {label}")
        diagnostic(f"\n=== Finished: {final} ===  run-state: {out_path}")
        # Repeated on the way out rather than assumed remembered: the run that just spent the
        # money is the one whose report gets read.
        diagnostic(metering_note([*(generators or [gen]),
                                  *(ver if isinstance(ver, list) else [ver])]))
        artifact = state.get("result_artifact")
        if final == "DONE" and isinstance(artifact, dict) and artifact.get("path"):
            diagnostic(f"deliverable: {artifact['path']}")
            if state.get("recipe") in JAPANESE_WRITING_RECIPES:
                content = read_result_artifact(state, out_path)
                if content is None:
                    diagnostic("[ERROR] completed deliverable cannot be read safely")
                    sys.exit(1)
                sys.stdout.write(content)
        if final == "AWAIT_APPROVAL":
            # Parked on a person, not failed. A distinct code so CI can tell "waiting for
            # sign-off" from "the run broke" — reporting either as the other is wrong.
            diagnostic("The run is parked at a human gate. Approve with "
                       f"`rig-wb orchestrate approve <step-id> {out_path}`, then `resume`.")
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


@_reports_refusals
def cmd_ab(args, *, out: Presenter = CONSOLE):
    """Run the same goal through multiple recipe variants concurrently and compare
    speed/retries/results (#291).

    Each variant runs in its own isolated worktree, exactly like `cmd_run --isolate` (no file
    collisions), so running them genuinely concurrently (ThreadPoolExecutor) is safe. --provider
    selects the generator/verifier role the same way `run` does — the comparison is about
    recipe differences, not model/provider differences.
    """
    if len(args) < 1:
        out.out("[ERROR] usage: ab <recipe1> <recipe2> [...] --provider <name> --goal G "
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
            out.out("[ERROR] manifest A/B needs BOTH --manifest-a and --manifest-b")
            sys.exit(1)
        if len(recipes) != 1:
            out.out("[ERROR] manifest A/B compares one recipe under two manifests — give exactly 1 recipe")
            sys.exit(1)
        for p in (manifest_a, manifest_b):
            if not p.is_file():
                out.out(f"[ERROR] manifest file '{p}' does not exist")
                sys.exit(1)
    elif len(recipes) < 2:
        out.out("[ERROR] specify 2 or more recipes to compare")
        sys.exit(1)
    if not gen:
        out.out("[ERROR] --provider <name> is required (rig|claude|codex|grok|ollama|lmstudio|anthropic|cmd|mock)")
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
        fm, _warns = resolve_extends(parse_frontmatter(path, out=out), path)
        _require_executable_recipe(fm, fm.get("name", path.stem))
    results: list[dict | None] = [None] * len(variants)
    out.out(f"◈ A/B experiment: {title} (provider={gen} / {len(variants)} concurrent variants)\n")
    with futures.ThreadPoolExecutor(max_workers=len(variants)) as ex:
        fut_to_idx = {
            ex.submit(_run_ab_variant, path, goal, gen, ver, dict(cfg), max_steps, max_parallel, quorum,
                     out_path, manifest, lbl): idx
            for idx, (path, manifest, lbl, out_path) in enumerate(variants)
        }
        for fut in futures.as_completed(fut_to_idx):
            results[fut_to_idx[fut]] = fut.result()

    out.out(f"## rig ab — {title}\n")
    out.out(f"{'recipe':<20} {'final':<10} {'elapsed(s)':<12} {'retries':<8} worktree")
    for r in results:
        wt = r["worktree_dir"] or "-"
        out.out(f"{r['recipe']:<20} {r['final']:<10} {r['elapsed_sec']:<12} {r['retries']:<8} {wt}")
    kept = [r for r in results if r["worktree_outcome"] == "kept"]
    if kept:
        out.out(f"\n{len(kept)} worktree(s) were preserved (unmet/dirty). After inspecting, clean up with "
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


def cmd_fleet(args, *, out: Presenter = CONSOLE,
              projects: ProjectIndex = KNOWN_PROJECTS):
    """Aggregate multiple repositories' `.rig/runs.jsonl`/`drill-results.jsonl` across projects (#272).

    Read-only, no side effects — no repository's `.rig/` data is ever written to. Meant for
    orgs/consultancies with multiple projects/clients, to compare per-persona detection power
    across repositories.

    Repositories are named explicitly with `--repos`, or discovered with `--discovered` from
    `~/.rig/runs.jsonl` — the log every backend already mirrors, which records the project of
    every run. That is still not auto-discovery in the sense this command has always refused:
    nothing is scanned and no network is touched; rig is reading where it has actually been.
    Discovery stays opt-in because the two produce different answers — `--repos` says "compare
    these", `--discovered` says "show me everywhere I have run", and a default that silently
    became the second would change what an existing invocation means.
    """
    repos_arg = None
    discovered = False
    anonymize = False
    as_json = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--repos" and i + 1 < len(args):
            repos_arg = args[i + 1]
            i += 2
        elif a == "--discovered":
            discovered = True
            i += 1
        elif a == "--anonymize":
            anonymize = True
            i += 1
        elif a == "--json":
            as_json = True
            i += 1
        else:
            i += 1
    if repos_arg and discovered:
        # Silently unioning them would make the report's scope depend on which flag the reader
        # noticed first, and there is no answer here that is not a guess at what was meant.
        out.out("[ERROR] fleet: --repos and --discovered choose the repository list two "
                "different ways; pass one")
        sys.exit(1)
    if not repos_arg and not discovered:
        # Keeps the existing usage line's opening intact — an added option is no reason to
        # change what an existing message says, and a test has been pinning this text.
        out.out("[ERROR] usage: fleet --repos <path1>,<path2>,... | fleet --discovered  "
                "[--anonymize] [--json]")
        sys.exit(1)

    if discovered:
        names = projects()
    else:
        names = [p for p in repos_arg.split(",") if p.strip()]
    repo_paths = [pathlib.Path(p).expanduser().resolve() for p in names]
    if not repo_paths:
        source = "no project has recorded a run yet" if discovered else "--repos has no valid paths"
        out.out(f"[ERROR] fleet: nothing to report ({source})")
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
        out.out(json.dumps(result, ensure_ascii=False, indent=2))
        return

    out.out(f"## rig fleet — {len(repo_paths)} repos\n")
    out.out(f"{'repo':<40} {'runs':<8} {'done':<8} drills")
    for r in per_repo:
        note = "" if r["exists"] else "  (no .rig/)"
        out.out(f"{r['repo']:<40} {r['runs']:<8} {r['done']:<8} {r['drills']}{note}")

    if persona_totals:
        out.out("\nPer-persona detection rate (summed across all repos):")
        for name, a in sorted(result["persona_totals"].items(), key=lambda kv: -(kv[1]["rate"] or 0)):
            rate = f"{a['rate'] * 100:.0f}%" if a["rate"] is not None else "unmeasured"
            out.out(f"  {name}: {rate} ({a['detected']}/{a['seeded']})")
        out.out("\nPer-persona cross-repo comparison (which project detects more/less):")
        for name in sorted(persona_totals):
            per_repo_rates = []
            for repo, personas in persona_by_repo.items():
                a = personas.get(name)
                if a and a.get("seeded"):
                    per_repo_rates.append(f"{repo}={_rate(a) * 100:.0f}%")
            if per_repo_rates:
                out.out(f"  {name}: " + " / ".join(per_repo_rates))
    else:
        out.out("\nPer-persona detection rate: unmeasured (no /rig:drill runs in the target repos)")


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


def _print_auto_route_regret(rows: list, *, out: Presenter = CONSOLE) -> None:
    report = collect_auto_route_regret(rows)
    if not report:
        out.out("No auto-routed steps recorded yet. This report reads `auto_route` / `learned_route`\n"
                "entries appended by runs that used cost-tier routing; until one runs there is\n"
                "nothing to second-guess.")
        return
    out.out(f"## rig runs --auto-route-regret ({len(report)} routed step(s) across {len(rows)} runs)\n")
    for entry in report:
        out.out(f"  {entry['recipe']}.{entry['step']}")
        out.out(f"    {'model':28s} {'n':>4s} {'PASS':>5s} {'PASS%':>7s}")
        for item in entry["models"]:
            mark = "*" if item["chosen"] else " "
            rate = "—" if item["pass_rate"] is None else f"{item['pass_rate'] * 100:6.0f}%"
            out.out(f"  {mark} {item['model']:28s} {item['n']:4d} {item['passed']:5d} {rate:>7s}")
        if entry["insufficient"]:
            out.out("    (too few observations to compare — routing is still guessing)")
        for regret in entry["regrets"]:
            out.out(f"    possible regret: {regret['chosen']} was chosen but {regret['better']} "
                    f"passes more often on this step — the cheaper tier may be costing rework")
        out.out()
    out.out("  * = routed to at least once. Read-only: this reports recorded runs and changes no routing.")


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


# ── Performance budgets and regression gates (#502) ─────────────────────────────
def _perf_summaries(rows: list[dict], recipe: str | None, limit: int) -> list[dict]:
    """The `perf` blocks of the most recent runs, newest first.

    Filtered by recipe on purpose, and by default not at all: phase timings are only
    comparable within a recipe (a seven-step bugfix and a one-step review share no shape), so
    a caller that means to compare has to say which recipe it means.
    """
    picked = [row["perf"] for row in rows
              if isinstance(row.get("perf"), dict)
              and (recipe is None or row.get("recipe") == recipe)]
    return picked[-limit:][::-1]


def _perf_budget(explicit: str | None) -> dict:
    """The declared budget: an explicit JSON file, else `perf_budget:` in the manifest.

    The manifest is the default home because a budget has to be committed to be a gate, and
    `.rig/` is gitignored — a budget living there would pass on every machine that had never
    seen it.
    """
    if explicit:
        return json.loads(pathlib.Path(explicit).read_text(encoding="utf-8"))
    budget = load_manifest().get("perf_budget")
    return budget if isinstance(budget, dict) else {}


def cmd_perf(args, *, out: Presenter = CONSOLE):
    """Where runs spend their time, and whether that is still within budget (#502).

        perf [--recipe R] [--limit N]                 phase breakdown of recent runs
        perf --save-baseline <path> [--recipe R]      record the current shape as a baseline
        perf --check [--baseline <path>] [--budget <path>] [--tolerance-pct P]

    `--check` is the regression gate: exit 1 when a phase grew past the tolerance against the
    baseline, or when a declared budget was broken. It reports an unenforceable limit as a
    failure rather than a pass — a budget naming a figure the runs did not measure is not a
    limit that held, and letting it read green is how a gate quietly stops gating.

    Provider latency is reported but never gated. A gate that failed on somebody else's
    network would be deleted within a month, and it would deserve to be.
    """
    recipe = baseline_path = save_path = budget_path = None
    limit, tolerance, check = 20, 20.0, False
    i = 0
    while i < len(args):
        if args[i] == "--recipe" and i + 1 < len(args):
            recipe, i = args[i + 1], i + 2
        elif args[i] == "--limit" and i + 1 < len(args):
            limit, i = int(args[i + 1]), i + 2
        elif args[i] == "--baseline" and i + 1 < len(args):
            baseline_path, i = args[i + 1], i + 2
        elif args[i] == "--save-baseline" and i + 1 < len(args):
            save_path, i = args[i + 1], i + 2
        elif args[i] == "--budget" and i + 1 < len(args):
            budget_path, i = args[i + 1], i + 2
        elif args[i] == "--tolerance-pct" and i + 1 < len(args):
            tolerance, i = float(args[i + 1]), i + 2
        elif args[i] == "--check":
            check, i = True, i + 1
        else:
            i += 1

    summaries = _perf_summaries(_read_jsonl(config.RUNS_PATH), recipe, limit)
    current = perf.aggregate(summaries)
    if current is None:
        # Not an error on its own — but never a silent pass under --check: a gate with no
        # measurements to judge has not judged anything.
        out.out(f"[perf] no timed runs in {config.RUNS_PATH}"
                + (f" for recipe {recipe}" if recipe else ""))
        sys.exit(1 if check else 0)

    if save_path:
        pathlib.Path(save_path).write_text(
            json.dumps({"recipe": recipe, "tolerance_pct": tolerance, **current},
                       indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        out.out(f"[perf] baseline written: {save_path} ({current['runs']} run(s))")
        return

    out.out(f"[perf] {current['runs']} run(s)"
            + (f", recipe={recipe}" if recipe else "") + "  — median ms per phase")
    for name, entry in current["phases"].items():
        thin = "" if entry["runs"] == current["runs"] else f"  ({entry['runs']} of {current['runs']} runs)"
        # Three decimals, not one: a phase that really took 19 microseconds should not print as
        # `0.0` beside the ones this module says are never rendered as zero.
        gated = "" if name in perf.PROVIDER_PHASES else "  *"
        out.out(f"  {name:<20} {entry['ms']:>12.3f}{gated}{thin}")
    out.out("  (* = rig's own time, what --check gates on; the rest is provider latency)")
    if current["unmeasured"]:
        out.out("  not measured in any run: " + ", ".join(current["unmeasured"]))
    for key in perf.SCALARS:
        if current.get(key) is not None:
            value = current[key]
            shown = (f"{value:,.0f}" if key.endswith("_bytes_emitted") or key.endswith("_tokens")
                     else f"{value:.3f}")
            out.out(f"  {key:<20} {shown:>12}  ({current[f'{key}_runs']} run(s))")

    if not check:
        return

    failures: list[str] = []
    budget = _perf_budget(budget_path)
    if budget:
        failures += perf.check_budget(current, budget)
    if baseline_path:
        baseline = json.loads(pathlib.Path(baseline_path).read_text(encoding="utf-8"))
        comparison = perf.compare(baseline, current,
                                  tolerance_pct=baseline.get("tolerance_pct", tolerance))
        out.out("")
        for line in perf.render(comparison):
            out.out(line)
        failures += [f"{item['phase']}: +{item['delta_pct']}% over baseline "
                     f"({item['baseline_ms']}ms → {item['current_ms']}ms)"
                     for item in comparison["regressed"]]
        # A phase that stopped being timed is a gate failure, not a note. Whatever it used to
        # cost has not gone anywhere; only the measurement has, and every total that included
        # it now reads as an improvement.
        failures += [f"{name}: no longer measured" for name in comparison.get("stopped_being_measured", [])]
        failures += perf.check_regression(comparison, budget)
    elif any(key in budget for key in perf.REGRESSION_LIMITS):
        # A percentage limit is a statement about a change, and there is nothing here to have
        # changed from. Saying so beats letting the declaration sit in the manifest unchecked.
        failures += [f"{key}={budget[key]} needs --baseline to mean anything"
                     for key in perf.REGRESSION_LIMITS if key in budget]
    if not budget and not baseline_path:
        failures.append("--check with no budget and no --baseline: nothing to check against")

    if failures:
        out.out("")
        for line in failures:
            out.out(f"[perf] FAIL {line}")
        sys.exit(1)
    out.out("\n[perf] within budget")


# ── OpenTelemetry export (#501) ─────────────────────────────────────────────────
def cmd_otel(args, *, out: Presenter = CONSOLE):
    """Project recorded runs to OpenTelemetry and send them (#501).

        otel [--recipe R] [--limit N] [--dry-run]
             [--endpoint URL] [--service-name NAME] [--traces-only | --metrics-only]

    A projection over `.rig/runs.jsonl`, which stays the source of truth: nothing here
    re-judges anything, and a failed export changes no verdict and no exit code beyond this
    command's own. `--dry-run` prints the payloads instead of sending them, which is also how
    you check what would leave the machine before pointing it anywhere.

    Off unless asked: the endpoint comes from `--endpoint` or from `[observability]` in the
    manifest with `enabled = true`. Telemetry that started flowing because a file was mistyped
    would be a data-egress incident, so anything ambiguous sends nothing.
    """
    recipe = endpoint = service = None
    limit, dry_run, traces, metrics = 50, False, True, True
    i = 0
    while i < len(args):
        if args[i] == "--recipe" and i + 1 < len(args):
            recipe, i = args[i + 1], i + 2
        elif args[i] == "--limit" and i + 1 < len(args):
            limit, i = int(args[i + 1]), i + 2
        elif args[i] == "--endpoint" and i + 1 < len(args):
            endpoint, i = args[i + 1], i + 2
        elif args[i] == "--service-name" and i + 1 < len(args):
            service, i = args[i + 1], i + 2
        elif args[i] == "--dry-run":
            dry_run, i = True, i + 1
        elif args[i] == "--traces-only":
            metrics, i = False, i + 1
        elif args[i] == "--metrics-only":
            traces, i = False, i + 1
        else:
            i += 1

    configured = otel.settings(load_manifest())
    if endpoint is None:
        if not configured["enabled"] and not dry_run:
            reason = configured.get("reason") or "observability.enabled is not true"
            out.out(f"[otel] nothing sent: {reason} (and no --endpoint given)")
            sys.exit(0)
        endpoint = configured.get("otlp_endpoint")
        traces = traces and configured.get("export_traces", True)
        metrics = metrics and configured.get("export_metrics", True)
    service = service or configured.get("service_name") or "rig"

    rows = [row for row in _read_jsonl(config.RUNS_PATH)
            if recipe is None or row.get("recipe") == recipe][-limit:]
    if not rows:
        out.out(f"[otel] no runs in {config.RUNS_PATH}"
                + (f" for recipe {recipe}" if recipe else ""))
        sys.exit(0)

    payloads = []
    if traces:
        payloads.append(("traces", otel.project_traces(rows, service_name=service)))
    if metrics:
        payloads.append(("metrics", otel.project_metrics(rows, service_name=service)))

    if dry_run or not endpoint:
        for signal, payload in payloads:
            out.out(f"--- {signal} ---")
            out.out(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    failures = []
    for signal, payload in payloads:
        error = otel.export(payload, endpoint, signal)
        if error:
            failures.append(f"{signal}: {error}")
        else:
            out.out(f"[otel] {signal} sent to {endpoint} ({len(rows)} run(s))")
    for line in failures:
        # A warning, never a raise. Every path that reaches an exporter has already decided the
        # run, and telemetry that could fail a task would eventually fail one for no reason.
        out.out(f"[otel] WARN export failed — {line}")


def cmd_runs(args, *, out: Presenter = CONSOLE):
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
            out.out(f"[ERROR] dashboard.py not found: {repo_paths.script_path('dashboard.py')}")
            sys.exit(1)
        cmd = [sys.executable, str(dash), "--repo", str(config.INVOCATION_CWD),
               "--out", html_out, "--limit", str(limit)]
        if recipe:
            cmd += ["--recipe", recipe]
        if since:
            cmd += ["--since", since]
        # noqa is permanent: this hands the dashboard's own stdout/stderr through to
        # the terminal, and `ProcessRunner` requires capture. Capturing would break it.
        rc = subprocess.run(cmd).returncode  # noqa: TID251
        sys.exit(rc)
    if not config.RUNS_PATH.exists():
        out.out(f"No run records yet ({config.RUNS_PATH}). They are appended by orchestrate run / "
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
        out.out("No matching run records.")
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
            out.out("No verdict records yet (they accumulate from runs that pass review-gate / acceptance-gate).")
            return
        by_kind: dict[str, list[str]] = {}
        for by in stats:
            by_kind.setdefault(_verifier_kind(by), []).append(by)
        out.out(f"## rig runs --personas (verifier votes across {len(rows)} runs)\n")
        for kind, heading in _VERIFIER_KIND_HEADINGS:
            names = by_kind.get(kind)
            if not names:
                continue
            out.out(f"  {heading}")
            out.out(f"  {'verifier':28s} {'votes':>6s} {'PASS':>6s} {'REJECT':>7s} {'REJECT%':>8s}")
            for by in sorted(names, key=lambda k: -stats[k]["votes"]):
                a = stats[by]
                out.out(f"  {by:28s} {a['votes']:6d} {a['ok']:6d} {a['reject']:7d} "
                        f"{a['reject'] / a['votes'] * 100:7.0f}%")
            out.out()
        # Only reviewers can rubber-stamp. A mechanism verdict is constant by construction
        # and a fixture is test scaffolding; flagging either as "no bite" reads as a finding
        # about review quality when it is a fact about the code that emits it.
        rubber = [by for by in by_kind.get("reviewer", ())
                  if stats[by]["votes"] >= 5 and stats[by]["reject"] == 0]
        if rubber:
            out.out("  Pruning hint: " + ", ".join(sorted(rubber))
                    + " cast 5+ votes without a single REJECT (possible rubber-stamping, or the lens"
                      " has no bite; consider dropping them or sharpening the lens)")
        if by_kind.keys() - {"reviewer"}:
            out.out("  Kinds are recovered from the verifier name (runs.jsonl keeps only {by, ok});"
                    " an unrecognized name counts as a reviewer, so a new lens is never hidden.")
        return

    if regret_mode:
        _print_auto_route_regret(rows, out=out)
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
        out.out(f"## rig runs --cost ({len(rows)} runs)\n")
        if not any_usage:
            out.out("No token usage recorded (unmeasured). HTTP providers (ollama/lmstudio/anthropic) are metered "
                    "automatically from the usage field. claude/codex run via CLI and "
                    "don't expose structured usage, so they're out of scope here — see Anthropic's Usage & "
                    "Cost Admin API for those instead of estimating.")
        else:
            for rcp, providers in sorted(by_recipe.items()):
                out.out(f"  {rcp}:")
                for provider, a in sorted(providers.items()):
                    total = a["prompt_tokens"] + a["completion_tokens"]
                    cache = f"  cache_read={a['cache_read_input_tokens']}" if a["cache_read_input_tokens"] else ""
                    out.out(f"    {provider:16s} calls={a['calls']:4d}  prompt={a['prompt_tokens']:8d}  "
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
            out.out("\n  Harness-context load (upper bound — prompts include the task text itself):")
            for provider, t in sorted(by_provider.items()):
                if not t["calls"]:
                    continue
                per_call = t["prompt_tokens"] / t["calls"]
                ratio = (t["prompt_tokens"] / t["completion_tokens"]) if t["completion_tokens"] else float("inf")
                out.out(f"    {provider:16s} avg prompt/call={per_call:8.0f}  prompt:completion={ratio:.1f}:1")
        if fallback_count or refusal_count:
            out.out(f"\nFable 5 refusal-classifier (#297): fallback={fallback_count}  direct-refusal={refusal_count}  "
                    "(a fallback is treated as a transparent success and doesn't block the gate; cache_read is the "
                    "fallback-prefix token count billed at 10%)")
        return

    out.out(f"## rig runs (latest {min(limit, len(rows))} of {len(rows)})\n")
    for r in rows[-limit:]:
        esc = f" / escalated@{r['escalated_at']}" if r.get("escalated_at") else ""
        out.out(f"  {r.get('ts', '?'):25s} {r.get('recipe', '?'):20s} {r.get('final', '?'):9s} "
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
    out.out("\n## Per-recipe aggregates\n")
    out.out(f"  {'recipe':20s} {'runs':>5s} {'DONE%':>7s} {'avg-retry':>9s} {'esc':>4s}")
    for name in sorted(agg):
        a = agg[name]
        out.out(f"  {name:20s} {a['n']:5d} {a['done'] / a['n'] * 100:6.0f}% "
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
        out.out("\n## Gap prescriptions (repeated escalations at the same step; #268)\n")
        for (rcp, sid), n in sorted(hot.items(), key=lambda kv: -kv[1]):
            rejecters = gap_verifiers.get((rcp, sid), Counter())
            who = ", ".join(name for name, _ in rejecters.most_common(3)) or "(no verdicts recorded)"
            forge_desc = (f"capability to resolve the recurring failure in the {sid} step of the "
                          f"{rcp} recipe (most rejections from: {who})")
            out.out(f"  {rcp} / {sid}: escalated {n} times — most rejections from: {who}")
            out.out(f"    draft request: /rig:forge \"{forge_desc}\"")
            out.out("    (after confirming forge's draft, re-measure with /rig:drill --replay)")
            out.out(f"    (to search for an external skill instead: /rig:import --discover \"skill to strengthen {sid}\")")

def cmd_install_shim(args, *, out: Presenter = CONSOLE, env: Env = OS_ENV):
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
        out.out(f"[ERROR] shim source not found: {src}")
        sys.exit(1)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        if not force:
            out.out(f"[ERROR] already exists: {target} (overwrite with --force)")
            sys.exit(1)
        target.unlink()
    target.symlink_to(src)
    out.out(f"✓ symlink: {target} → {src}")
    path_dirs = (env.get("PATH") or "").split(os.pathsep)
    if str(target.parent) not in path_dirs:
        out.out(f"⚠ {target.parent} does not seem to be on $PATH. Add this:")
        out.out(f"    export PATH=\"{target.parent}:$PATH\"")
    out.out(f"Verify: `rig models` or `rig --help` (RIG_HOME={config.RIG_HOME})")
