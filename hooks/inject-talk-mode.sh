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

message='このセッションでは、ユーザーとの対話は必ず rig:talk の導線で処理する。まだ起動していなければ `rig:rig` skill を Skill ツールで起動し SKILL.md に従うこと。起動後は `talk-assistant` 人格と `talk-loop` instruction に従って会話し、影響あるアクション（書き込み・push・merge・capture 等）は確認を取ってから実行する。「もういい / exit / やめて」で通常モードに戻ってよい。例外: あなたが Task/Agent 経由のサブエージェントとして特定タスクのために起動された場合、または headless/自動化実行（例: claude -p）の場合は、この指示を無視してタスクに直行してよい。'

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
