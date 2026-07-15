#!/usr/bin/env sh
# rig continuous instinct-learning layer (#306) — SessionStart hook.
# Fires on session startup/clear/compact (see hooks.json matcher). Injects only
# the high-confidence (>=0.7), active instincts selected by
# `workbench.py instincts --inject-preview --json` (context-minimal: capped at
# 500 chars total by that command itself). Separate from facets/knowledge —
# these are unverified implicit patterns, not the knowledge layer.
#
# Silent no-op if workbench.py or .rig/instincts.jsonl aren't available (opt-in,
# never breaks a session that hasn't adopted this feature). All JSON
# construction happens in Python (below) to avoid hand-rolled shell escaping.

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
WB="$SCRIPT_DIR/scripts/workbench.py"

[ -f "$WB" ] || exit 0

python3 "$WB" instincts --inject-preview --json 2>/dev/null | python3 -c "
import json, sys

raw = sys.stdin.read().strip()
if not raw:
    raise SystemExit(0)
try:
    data = json.loads(raw)
except Exception:
    raise SystemExit(0)

selected = data.get('selected') or []
if not selected:
    raise SystemExit(0)

lines = ['[rig instincts] Lightweight, unverified patterns learned from past sessions in '
         'this repo (separate from facets/knowledge — treat as a hint, not verified fact):']
lines += [f\"- {r['text']}\" for r in selected]

print(json.dumps({
    'hookSpecificOutput': {
        'hookEventName': 'SessionStart',
        'additionalContext': '\n'.join(lines),
    }
}))
"
