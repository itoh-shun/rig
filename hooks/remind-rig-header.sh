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
[rig run-continuity] rig ハーネスの RUN が進行中の可能性を検知しました。SKILL.md §6 に従い、応答の冒頭に次のフォーマットで run-status ヘッダを1行必ず再掲してください（中断・質疑・tool 出力の直後でも省かない）:

▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>

RUN が既に終了 (「もういい / exit / やめて」で通常モードに戻った・フロー完了レポート済み) しているならこの指示は無視してよい。talk 自身の地の会話ターン (フローに委譲していない短い雑談) も例外。
EOF
fi

exit 0
