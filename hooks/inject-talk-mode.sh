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

message='このセッションでは、ユーザーとの対話は必ず rig:talk の導線で処理する。まだ起動していなければ `rig` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・context-minimal）に従うこと。起動後は `talk-assistant` 人格と `talk-loop` instruction に従って会話する: 雑談・質問はそのまま短く答え、rig アクション要求は正規化 → 利用可能な /rig:* を動的列挙して分類 → 起動文字列を一言確認 → 該当コマンド経由で engine に委譲 → 短い話し言葉で報告する。書き込み・push・merge・capture など影響あるアクションは確認必須、情報取得・--plan 等の低リスクは即応する。「もういい / exit / やめて」で通常モードに戻ってよい。例外: あなたが Task/Agent 経由のサブエージェントとして特定タスクのために起動された場合、または headless/自動化実行（例: claude -p）の場合は、この指示を無視してタスクに直行してよい。'

escape_for_json() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

escaped=$(escape_for_json "$message")

printf '{\n  "hookSpecificOutput": {\n    "hookEventName": "SessionStart",\n    "additionalContext": "%s"\n  }\n}\n' "$escaped"
