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
  verdict<state.json> --by N --pass  Record an independent verifier's judgment (enforces grader != generator)
  next   <state.json>                Deterministically compute, apply, and print the next transition
  resume <state.json>                Verify-first resume: print a digest, RE-RUN the current step's checks
                                     (refuse to advance if the world drifted), then continue via `next`
  status <state.json>                Print the current state
  runs   [--limit N] [--recipe R] [--personas] [--cost]  Run telemetry (.rig/runs.jsonl): listing, per-recipe aggregates,
                                     per-verifier vote tallies, and (--cost) per-recipe/provider token rollups for
                                     HTTP providers (ollama/lmstudio; claude/codex have no structured usage — #271/#296)
  party                              Party roster screen (/rig:party): renders RPG-style stats from telemetry / measured drills
  run ... --verifier-providers a,b,c Mixed-model quorum: run the same verification persona across different providers (votes are provider:persona)
  run ... --isolate                  Run isolated in a disposable git worktree. Only gate-green commits ff-merge back into the
                                     original branch; unmet/dirty/non-ff runs preserve the worktree and branch
                                     (the spatial version of determinism-by-gate).
                                     Verifier-role CLIs get read-only permissions pinned via argv (claude --allowedTools / codex --sandbox read-only)
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
  install-shim [--to PATH] [--force] Symlink the shim into ~/.local/bin/rig (cross-project entry point; run once)
  selftest                           Self-verification of determinism (proves same input -> same transitions)

Dependencies: Python3 + PyYAML (same as validate.py). Exit code 0=success / 1=error or ESCALATE.
"""

import sys

from .commands import (cmd_check, cmd_init, cmd_install_shim, cmd_next, cmd_party,
                       cmd_plan, cmd_resume, cmd_run, cmd_runs, cmd_status, cmd_verdict)
from .providers import cmd_models, cmd_probe
from .queueing import cmd_queue
from .graph import cmd_graph
from .selftest import cmd_selftest

# ── Entry point ───────────────────────────────────────────────────────────────
COMMANDS = {
    "plan": cmd_plan, "init": cmd_init, "check": cmd_check,
    "verdict": cmd_verdict, "next": cmd_next, "status": cmd_status,
    "run": cmd_run, "models": cmd_models, "probe": cmd_probe, "queue": cmd_queue,
    "resume": cmd_resume,
    "runs": cmd_runs, "party": cmd_party, "graph": cmd_graph,
    "install-shim": cmd_install_shim, "selftest": cmd_selftest,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(0 if len(sys.argv) < 2 else 1)
    COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
