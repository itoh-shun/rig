#!/usr/bin/env sh
# rig continuous instinct-learning layer (#306) — Stop hook.
# Fires when the agent finishes responding. This hook does NOT extract
# patterns itself — pattern extraction requires judgment (what's durably
# useful vs. one-off noise), which is the model's job, not a shell script's.
# All this does is remind the model to consider proposing one, and only if
# something genuinely reusable was learned this session (not every session
# has one — don't manufacture noise to fill a quota).

cat <<'EOF'
[rig instincts] Before ending: did this session surface a durably useful, project-specific pattern (a preferred style/tool, a fast way to search this codebase, a gotcha worth remembering) that isn't already captured in facets/knowledge? If so — and only if it's genuinely reusable, not a one-off — record it with:
  python3 scripts/workbench.py instincts --add "<short, standalone statement>" --evidence "<why you believe this>" --task-id <id if applicable> --confidence <0.0-1.0>
If an existing instinct is now wrong or superseded, use --supersedes <id> so the old one is muted rather than left to contradict the new one. Do not propose one if nothing new and reusable came up — most sessions won't have one.
EOF
