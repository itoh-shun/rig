#!/usr/bin/env sh
# rig run-continuity — PreCompact hook.
# Fires just before context compaction (manual /compact or automatic).
# On exit 0, stdout is appended to the compaction as custom instructions,
# so the harness preserves the active rig run-state in the summary.
# It cannot read the live conversation; it tells the compaction WHAT to keep.

cat <<'EOF'
[rig run-continuity] If a rig harness run is active, the compaction summary MUST preserve:
- the rig run-status line: recipe (or ad-hoc), current step id + position (n/N), gate state, backend, mode (gated/autonomous);
- the active recipe's step list — which steps are done, which remain, and the current step id;
- the acceptance contract in force (acceptance-gate criteria / the goal-loop goal) and any unresolved REJECT or merge-blocking conditions;
- the user's goal/intent, key decisions made, and any stuck-guard counters (no-progress rounds);
- the context-minimal discipline: real work is delegated to subagents; the parent only dispatches, aggregates structured reports, and makes gate decisions.
After compaction, on the first work turn, re-emit the rig run-status header and re-anchor to the current step BEFORE doing any work. Do not silently switch to direct, un-gated work (see SKILL.md §6 run-continuity).

[rig talk-always-on] If this session was operating under the talk-always-on directive (interactive user turns routed through rig:talk — see hooks/inject-talk-mode.sh), the compaction summary MUST preserve that directive so it keeps applying after compaction, since SessionStart(source=compact) may not re-inject it. This does not apply to a subagent/headless session already working a specific task directly.
EOF
