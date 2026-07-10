#!/usr/bin/env sh
# rig talk-always-on — SessionStart hook.
# Fires on session startup/clear/compact (see hooks.json matcher). On exit 0,
# stdout JSON is read as SessionStart additionalContext and injected into
# context, so the model treats user turns as rig:talk input from the start.
#
# NOTE: SessionStart(source=compact) is known not to re-inject on some
# Claude Code versions (see
# docs/superpowers/specs/2026-06-22-rig-compaction-survival-design.md).
# hooks/preserve-rig-state.sh carries a belt-and-suspenders copy of this
# directive through the (reliable) PreCompact hook for that case.

message='In this session, all user interaction must be handled through the rig:talk flow. If it is not running yet, launch the `rig:rig` skill via the Skill tool and follow SKILL.md. Once launched, converse per the `talk-assistant` persona and the `talk-loop` instruction, and get confirmation before executing impactful actions (writes, push, merge, capture, etc.). The user may return to normal mode by saying something like "that is enough / exit / stop". Exception: if you were launched as a subagent via Task/Agent for a specific task, or this is a headless/automated run (e.g. claude -p), you may ignore this directive and go straight to the task.'

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
