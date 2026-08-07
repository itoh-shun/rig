#!/usr/bin/env sh
# rig continuous instinct-learning layer (#306) — Stop hook.
# Fires when the agent finishes responding. This hook does NOT extract
# patterns itself — pattern extraction requires judgment (what's durably
# useful vs. one-off noise), which is the model's job, not a shell script's.
# All this does is remind the model to consider proposing one, and only if
# something genuinely reusable was learned this session (not every session
# has one — don't manufacture noise to fill a quota).
#
# Fires at most ONCE per session. The reminder blocks the stop, so firing on
# every turn costs a full round-trip each time — and since most sessions have
# nothing to record, almost all of those round-trips are spent saying "nothing
# this time". One prompt is enough to make the model consider it; the rest is
# noise that crowds out the work. A marker file keyed by session_id enforces it.
# Sessions with no session_id (older clients) fall back to firing every time.

input=$(cat)
state=$(printf '%s' "$input" | python3 -c '
import json
import sys

try:
    data = json.load(sys.stdin)
except (json.JSONDecodeError, OSError):
    raise SystemExit(0)

print("true" if data.get("stop_hook_active") is True else "false")
sid = data.get("session_id")
print(sid if isinstance(sid, str) else "")
' 2>/dev/null)

stop_hook_active=$(printf '%s' "$state" | sed -n 1p)
session_id=$(printf '%s' "$state" | sed -n 2p)

[ "$stop_hook_active" = "true" ] && exit 0

# Once per session: a marker whose name is the session id. Anything outside
# [A-Za-z0-9_-] becomes an underscore, so the name can never escape the marker
# directory. Dots are folded too — an id of ".." would otherwise resolve to the
# parent directory, which always exists and would silence the hook for good.
if [ -n "$session_id" ]; then
  safe_id=$(printf '%s' "$session_id" | tr -c 'A-Za-z0-9_-' '_')
  marker_dir="${TMPDIR:-/tmp}/rig-instinct-hook"
  marker="$marker_dir/$safe_id"
  if [ -e "$marker" ]; then
    exit 0
  fi
  mkdir -p "$marker_dir" 2>/dev/null && : > "$marker" 2>/dev/null
fi

message=$(cat <<'EOF'
[rig instincts] Before ending: did this session surface a durably useful, project-specific pattern (a preferred style/tool, a fast way to search this codebase, a gotcha worth remembering) that isn't already captured in facets/knowledge? If so — and only if it's genuinely reusable, not a one-off — record it with:
  python3 scripts/workbench.py instincts --add "<short, standalone statement>" --evidence "<why you believe this>" --task-id <id if applicable> --confidence <0.0-1.0>
If an existing instinct is now wrong or superseded, use --supersedes <id> so the old one is muted rather than left to contradict the new one. Do not propose one if nothing new and reusable came up — most sessions won't have one.
EOF
)

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

printf '{\n  "decision": "block",\n  "reason": "%s"\n}\n' "$escaped"
