#!/usr/bin/env bash
# rig GitHub Action entrypoint (#265)
#
# Wraps `scripts/orchestrate.py run --isolate` for headless CI use. Does not
# reimplement any execution logic — the gate, isolation, and ff-merge/keep
# rules are exactly orchestrate.py's own (`setup_isolation`/`teardown_isolation`).
# This script only: (1) builds the CLI invocation from action inputs passed as
# env vars, (2) derives the final status from the run-state JSON, and
# (3) on a green gate + auto_pr, pushes the isolated branch and opens a PR via
# the `gh` CLI (pre-installed on GitHub-hosted runners).
#
# Subcommands:
#   run       — execute the task, write `final=<status>` to $GITHUB_OUTPUT
#   open-pr   — push the isolated branch and `gh pr create` (only called by
#               action.yml when the previous step's final == DONE)
set -euo pipefail

STATE_FILE="rig-action-state.json"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCHESTRATE="${SCRIPT_DIR}/orchestrate.py"

cmd_run() {
  : "${RIG_TASK:?RIG_TASK is required}"
  : "${RIG_RECIPE:?RIG_RECIPE is required}"
  local provider="${RIG_PROVIDER:-mock}"
  local args=(run "$RIG_RECIPE" --provider "$provider" --goal "$RIG_TASK" --isolate --out "$STATE_FILE")

  if [ -n "${RIG_VERIFIER_PROVIDER:-}" ]; then
    args+=(--verifier-provider "$RIG_VERIFIER_PROVIDER")
  fi
  if [ -n "${RIG_MODEL:-}" ]; then
    args+=(--model "$RIG_MODEL")
  fi
  if [ -n "${RIG_MAX_STEPS:-}" ]; then
    args+=(--max-steps "$RIG_MAX_STEPS")
  fi

  echo "◈ rig-action: python3 ${ORCHESTRATE} ${args[*]}"
  # orchestrate.py run exits 1 on ESCALATE/BLOCKED — capture that without
  # aborting this script (the state file is written either way; we derive
  # `final` from it, not from the exit code).
  python3 "$ORCHESTRATE" "${args[@]}" || true

  if [ ! -f "$STATE_FILE" ]; then
    echo "[ERROR] ${STATE_FILE} was not created (orchestrate.py may have failed before starting)" >&2
    exit 1
  fi

  local final
  final="$(python3 -c "
import json
s = json.load(open('${STATE_FILE}'))
if s.get('done'):
    print('DONE')
elif s.get('stopped'):
    print(s['stopped'].get('kind', 'ESCALATE'))
else:
    print('STOPPED')
")"
  echo "final=${final}" >> "${GITHUB_OUTPUT:-/dev/stdout}"
  echo "◈ rig-action: final=${final}"

  if [ "$final" != "DONE" ]; then
    echo "[FAIL] the gate did not go green (final=${final}). No PR will be created." >&2
    exit 1
  fi
}

cmd_open_pr() {
  : "${RIG_TASK:?RIG_TASK is required}"
  if [ ! -f "$STATE_FILE" ]; then
    echo "[ERROR] ${STATE_FILE} not found (run the 'run' subcommand first)" >&2
    exit 1
  fi

  local branch dir
  branch="$(python3 -c "import json;print(json.load(open('${STATE_FILE}'))['isolation']['branch'])")"
  dir="$(python3 -c "import json;print(json.load(open('${STATE_FILE}'))['isolation']['dir'])")"

  # teardown_isolation ff-merges and removes the worktree only on DONE+clean+committed,
  # so what we look at here is the "already-merged current branch" diff. If it was kept
  # (not merged), push from the worktree side instead.
  if [ -d "$dir" ]; then
    echo "◈ rig-action: worktree was preserved (kept). Pushing from $dir."
    git -C "$dir" push origin "HEAD:${branch}"
  else
    echo "◈ rig-action: already ff-merged. Pushing the current branch's changes as a new branch."
    git checkout -b "$branch"
    git push origin "$branch"
  fi

  local pr_url
  pr_url="$(gh pr create --title "rig: ${RIG_TASK}" \
    --body "Opened automatically by the rig GitHub Action after acceptance-gate passed (final=DONE). See workflow run for details." \
    --head "$branch" 2>&1 | tail -1)"
  echo "pr_url=${pr_url}" >> "${GITHUB_OUTPUT:-/dev/stdout}"
  echo "◈ rig-action: opened PR ${pr_url}"
}

case "${1:-}" in
  run) cmd_run ;;
  open-pr) cmd_open_pr ;;
  *)
    echo "[ERROR] usage: $0 <run|open-pr>" >&2
    exit 1
    ;;
esac
