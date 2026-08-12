#!/usr/bin/env sh
# Cross-host SessionStart(source=compact) run-continuity hook.
# The hooks config uses an exact `compact` matcher, so this script does not need
# to parse stdin. It can only re-anchor from state retained in the compacted
# context; it cannot reconstruct state the compactor omitted.

message='[rig run-continuity] Best-effort re-anchor after context compaction: if the compacted context contains an active rig harness run, recover and re-emit its run-status line (recipe, current step and position, gate, backend, mode) before doing any work. Recover the completed and remaining steps, then continue the current step under the acceptance contract already in force. Preserve unresolved REJECT or merge-blocking conditions, the user goal and key decisions, stuck-guard counters, and the context-minimal rule that real work is delegated while the parent dispatches, aggregates reports, and decides gates. Do not silently switch to direct, un-gated work. If no rig run is active, ignore this instruction. If the compacted context says this interactive session was using the rig:talk flow, keep applying it after compaction; this does not apply to a subagent/headless session already working a specific task directly.'

escape_for_json() {
  printf '%s' "$1" | awk '
    BEGIN { ORS = "" }
    {
      if (NR > 1) printf "\\n"
      line = $0
      gsub(/\\/, "\\\\", line)
      gsub(/"/, "\\\"", line)
      gsub(/\r/, "\\r", line)
      gsub(/\t/, "\\t", line)
      printf "%s", line
    }
  '
}

escaped=$(escape_for_json "$message")

printf '{\n  "hookSpecificOutput": {\n    "hookEventName": "SessionStart",\n    "additionalContext": "%s"\n  }\n}\n' "$escaped"
