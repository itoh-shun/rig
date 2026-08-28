"""
rig computational orchestrator (deterministic orchestration runner)

A thin runner where **code** interprets the recipe's step DAG and deterministically
enforces transitions, gates, stop conditions, and state persistence. It fills the
rig engine's (SKILL.md) weakness of "letting the model hold the control loop in
prose" — code holds the helm (engine unchanged, opt-in).

The model does each step's "work", but this runner decides "what happens next":
  plan   <recipe.md> [--json] [--with "<flags>"] [--diff-lines N | --diff-git]
                                     Compute the step state machine deterministically (no model needed).
                                     --json is the primary RESOLVE implementation: extends merge
                                     (remove/origin), badge/steps: derivation, condition evaluation,
                                     size classification, slicing, and flag precedence as machine output.
                                     --diff-git measures line counts from git diff HEAD automatically and
                                     applies the manifest's (.claude/rig.md) size_thresholds /
                                     default_orchestrate (selftest Q/R/S golden-verify this; the prose
                                     engine calls it during RESOLVE)
  init   <recipe.md> [--goal G]      Create the run-state and print the first action
  check  <state.json>                Run the current step's checks: (shell) and record pass/fail (machine sensor)
  verdict <state.json> --by N --pass|--fail [--criterion N=PASS|FAIL|UNKNOWN]...
                                     Record an independent judgment; declared acceptance criteria must be answered explicitly
  approve <step-id> [state.json]     Cast a human-gate decision on a step declaring `human_gate:` (or covered by the
    [--deny] [--note "..."]           org policy's `stage:<id>` rule). Quorum, qualifying roles, separation of duties
                                     and freshness come from the governance layer; the record lands in the run-state
                                     beside that step's checks/verdicts and in the tamper-evident ledger
  next   <state.json>                Deterministically compute, apply, and print the next transition
  resume <state.json>                Verify-first resume: print a digest, RE-RUN the current step's checks
                                     (refuse to advance if the world drifted), then continue via `next`
  status <state.json>                Print the current state
  runs   [--limit N] [--recipe R] [--personas] [--cost]  Run telemetry (.rig/runs.jsonl): listing, per-recipe aggregates,
                                     per-verifier vote tallies, and (--cost) per-recipe/provider token rollups for
                                     HTTP providers (ollama/lmstudio; claude/codex have no structured usage — #271/#296)
  perf   [--recipe R] [--limit N]    Where runs spend their time, by phase (#502). `--save-baseline <path>` records the
         [--check] [--baseline P]    current shape; `--check` is the regression gate (exit 1 past the tolerance, or on a
         [--budget P]                broken `perf_budget:` from the manifest). Provider latency is reported, never gated —
                                     a gate that failed on somebody else's network would be switched off within a month
  otel   [--recipe R] [--limit N]    Project recorded runs to OpenTelemetry and send them (#501). OTLP/HTTP JSON over
         [--endpoint URL] [--dry-run]  urllib — no SDK, no vendor in rig's core. A projection over .rig/runs.jsonl, which
                                       stays the source of truth; a failed export changes no verdict. Off unless
                                       --endpoint is given or [observability] enables it. --dry-run prints what would
                                       leave the machine. The projection is an allowlist: no prompt, response, diff,
                                       path or verdict prose is exported, and a new record field is absent until
                                       somebody decides it is safe
  run ... --reuse-session            Opt-in: let a CLI provider carry its conversation across steps instead of
                                     starting cold each time (#326). **Generator only** — a checker that inherited the
                                     generator's conversation is not an independent checker. The capability is read out
                                     of the CLI's own --help at runtime (session support is version-dependent), and a
                                     provider that cannot do it falls back to stateless with a SESSION_REUSE_FALLBACK
                                     line in the run history, never silently
  run ... --verifier-providers a,b,c Mixed-model quorum: run the same verification persona across different providers (votes are provider:persona)
  run ... --isolate                  Run isolated in a disposable git worktree. Only gate-green commits ff-merge back into the
                                     original branch; unmet/dirty/non-ff runs preserve the worktree and branch
                                     (the spatial version of determinism-by-gate).
                                     Verifier-role CLIs get read-only permissions pinned via argv (claude --allowedTools / codex --sandbox read-only)
  run ... --timeout SECONDS           Set the positive-integer timeout for each CLI or HTTP provider call (default: 600 seconds).
  run ... --goal-stdin               Read the goal once from bounded UTF-8 stdin. Required by recipes that declare
                                     secure-provider-execution; those recipes refuse goal text in parent argv.
  run ... --review-category C        Required for secure Japanese writing: general, incident_report, or support_reply.
                                     Bound into run-state; missing, unknown, or changed values fail before providers.
  run ... --material-profile P       Optional style material for secure Japanese writing: none (default), technical,
                                     or conversation. Bound into run-state; never inferred from goal text.
  ab <recipe1> <recipe2> ...          Run the same goal through multiple recipe variants concurrently and compare
    --provider <name> --goal G        speed/retries/results (#291). Each variant runs in its own isolated worktree
                                     (same path as --isolate), so variants never conflict.
  fleet --repos p1,p2,... [--anonymize] [--json]
                                     Aggregate multiple repositories' runs.jsonl/drill-results.jsonl across projects
                                     (#272). Read-only; compares per-persona detection rate across repositories.
  run ... --auto-route                For steps declaring auto_route.candidates ({model,cost_tier,max_size}), deterministically
                                     picks the cheapest candidate that covers the measured diff size (#264). A fallback only:
                                     runtime --step-model and the recipe's own model: both still win outright. The decision is
                                     recorded in run-state history and runs.jsonl's steps[].auto_route.
  run ... --auto-route-learn          Learns from runs.jsonl's track record (which model actually got used, did the step pass)
    [--auto-route-mode shadow|active] instead of only the static size thresholds (#305; frequency-based, no ML model). Defaults
    [--exploration-pct N]             to shadow mode: predictions are always recorded (steps[].learned_route) but only override
    [--exploration-date D]            the applied model under --auto-route-mode active. Insufficient samples/pass-rate fall back
                                     to #264's static auto-route, with every rejected candidate's reason recorded (no black box).
                                     --exploration-pct lets a deterministic fraction of runs try the next-cheapest candidate
                                     (hashed from --exploration-date + recipe/step, never randomness).
  graph  [--json | --focus <name>]   Derive a **typed graph** (11 relations: injects/extends/uses-*/mirrors, etc.) from shipped bricks.
                                     Never hand-written: frontmatter is the source of truth (validate check_graph enforces consistency in CI)
  mcp-scan [--json]                  Static threat analysis of scripts/mcp_server.py's tool definitions via three-layer adversarial
                                     reasoning (attacker/defender/auditor) for shell/network over-permission, secret exposure, and
                                     hook-injection risk (#303). Never executes anything; deterministic; wired into --validate.
  install-shim [--to PATH] [--force] Symlink the shim into ~/.local/bin/rig (cross-project entry point; run once)
  selftest                           Self-verification of determinism (proves same input -> same transitions)

Dependencies: Python3 + PyYAML (same as validate.py).
Exit code 0=success / 1=error or ESCALATE / 3=run parked at a human gate (`run` only; not a failure).
"""

import sys

from .. import context_meter
from ..gh_requirement import advise_gh
from .commands import (cmd_ab, cmd_approve, cmd_check, cmd_fleet, cmd_init, cmd_install_shim,
                       cmd_next, cmd_otel, cmd_perf, cmd_plan, cmd_resume, cmd_run, cmd_runs,
                       cmd_status,
                       cmd_verdict)
from .providers import cmd_models, cmd_probe
from .queueing import cmd_queue
from .graph import cmd_graph
from .mcp_scan import cmd_mcp_scan
from .selftest import cmd_selftest

# ── Entry point ───────────────────────────────────────────────────────────────
COMMANDS = {
    "plan": cmd_plan, "init": cmd_init, "check": cmd_check,
    "verdict": cmd_verdict, "approve": cmd_approve, "next": cmd_next, "status": cmd_status,
    "run": cmd_run, "models": cmd_models, "probe": cmd_probe, "queue": cmd_queue,
    "resume": cmd_resume,
    "runs": cmd_runs, "graph": cmd_graph, "perf": cmd_perf, "otel": cmd_otel,
    "install-shim": cmd_install_shim, "selftest": cmd_selftest,
    "mcp-scan": cmd_mcp_scan, "ab": cmd_ab, "fleet": cmd_fleet,
}


# Commands that mention a missing `gh` / github/gh-stack: the ones that start
# producing work, where someone might still want the stacked-PR helpers. It is
# one stderr line and never a refusal — the tools are optional (see
# rig_workbench/gh_requirement.py). `queue` only advises on its `go` verb —
# `queue add/list/done` are bookkeeping — and plan / status / next / runs /
# graph / party / probe / selftest stay quiet: a note there would be noise.
_GH_ADVISORY_COMMANDS = {"run", "init", "ab"}


def _advises_gh(cmd: str, rest: list[str]) -> bool:
    if cmd in _GH_ADVISORY_COMMANDS:
        return True
    return cmd == "queue" and bool(rest) and rest[0] == "go"


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(0 if len(sys.argv) < 2 else 1)
    cmd, rest = sys.argv[1], sys.argv[2:]
    # Count what this invocation prints at the parent session. context-minimal is
    # called a hard rule and was never measured; see rig_workbench/context_meter.
    context_meter.install(f"orchestrate {cmd}", rest)
    if _advises_gh(cmd, rest):
        advise_gh(f"orchestrate {cmd}")
    COMMANDS[cmd](rest)


if __name__ == "__main__":
    main()
