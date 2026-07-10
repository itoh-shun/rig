#!/usr/bin/env sh
# rig run-continuity — UserPromptSubmit hook.
# Fires when the user submits a prompt.
# Detects an active rig harness RUN by looking for the run-status header
# signature ("▸ rig |") in the recent transcript. If found, injects a
# reminder to re-emit the header at the top of the model's response.
# This backstops the SKILL.md §6 directive which loses recency across many
# turns / tool calls / subagent dispatches.

input=$(cat)
transcript_path=$(printf '%s' "$input" | sed -n 's/.*"transcript_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

[ -z "$transcript_path" ] && exit 0
[ ! -f "$transcript_path" ] && exit 0

# `▸ rig |` is the distinctive signature of a run-status header. It only
# appears in RUN turns per SKILL.md §6, so recent presence indicates active RUN.
if tail -n 200 "$transcript_path" 2>/dev/null | grep -q '▸ rig |'; then
  cat <<'EOF'
[rig run-continuity] A rig harness RUN may be in progress. Per SKILL.md §6, you must restate the run-status header as a single line at the top of your response in the following format (do not omit it even right after an interruption, a question, or tool output):

▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>

If the RUN has already ended (the user returned to normal mode with "that is enough / exit / stop", or the flow completion report was delivered), you may ignore this directive. Plain conversational turns by talk itself (short chit-chat not delegated to a flow) are also exempt.
EOF
fi

exit 0
