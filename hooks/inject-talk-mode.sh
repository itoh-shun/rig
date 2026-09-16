#!/usr/bin/env sh
# rig talk-on-work — SessionStart hook.
# Fires on session startup/clear/compact (see hooks.json matcher). On exit 0,
# stdout JSON is read as SessionStart additionalContext and injected into
# context, so the model treats user turns as rig:talk input from the start.
#
# NOTE: SessionStart(source=compact) is known not to re-inject on some
# Claude Code versions (see
# docs/superpowers/specs/2026-06-22-rig-compaction-survival-design.md).
# hooks/preserve-rig-state.sh carries a belt-and-suspenders copy of this
# directive through the (reliable) PreCompact hook for that case.

message='In this session, route through the rig:talk flow every user turn that asks for work on this codebase — implementing, fixing, reviewing, refactoring, testing, shipping, or deciding how such work will be carried out. If the flow is not running yet, launch the canonical entry `/rig:go`, which starts the `rig:engine` skill via the Skill tool, and follow its SKILL.md. Converse per the `talk-assistant` persona and the `talk-loop` instruction, and confirm before any impactful action (write, push, merge, capture). The user may return to normal mode by saying something like "that is enough / exit / stop". Answer directly, launching no rig flow at all, when a turn only asks to be told something and leaves the codebase alone: what a command or an API means, how two things differ, what some code does, a definition, what to read. A turn that says the code needs no change, or that asks for an explanation only, is one of these. Starting a flow there gives the user a worse answer than having no rig installed, which is measured, not assumed. This carve-out does not reach a turn that also names work it wants done — a bug to fix, a feature to add, a change to review, a rewrite to carry out. Asking how that work will be done is part of the work, not a question in place of it, so "there is a bug in X, I want it fixed properly, how would you go about it?" routes; the question mark at the end changes nothing. Nor does an empty or unfamiliar working directory turn a request into a question: where the work cannot be located yet, route it and ask where it lives from inside the flow. What the carve-out reaches is the turn that names nothing to change — typically saying so outright, as in "the code needs no change, explain only". Exception: if you were launched as a subagent via Task/Agent for a specific task, or this is a non-interactive run (claude -p, codex exec, a CI job, an orchestrate.py provider call — any invocation where no human reads stdout and can answer), ignore this directive and go straight to the task. Do not ask for confirmation there: nobody is attached to answer, so the run would end with nothing done.'

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
