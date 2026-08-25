#!/usr/bin/env sh
# rig continuous instinct-learning layer (#306) — Stop hook.
#
# Fires when the agent finishes responding. This hook does NOT extract patterns
# itself — deciding what is durably useful versus one-off noise takes judgment,
# which is the model's job and not a shell script's. All it does is prompt the
# model to consider recording one.
#
# **Everything here is shaped by one fact: this hook blocks the stop.** That is
# not a passive note in the margin — it prevents the session from ending and
# spends a whole round-trip. Most sessions have no instinct worth recording, so
# almost every firing is spent saying "nothing this time". A blocking hook that
# fires when it should not is worse than one that occasionally stays silent when
# it could have spoken, and the earlier version had that backwards: every failure
# path — no session id, unparseable input, an unwritable marker directory —
# degraded to *firing on every single turn*, and it fired in sessions that never
# touched rig at all.
#
# So the rule is: fire only when every precondition is affirmatively true, and
# exit quietly the moment any of them cannot be established.
#
#   1. not already inside a stop-hook round-trip
#   2. the payload parses, and carries a session id and a readable transcript
#   3. this session actually used rig (the transcript says so)
#   4. the current directory is an adopted, Git-backed rig project
#   5. there is a runnable command to suggest
#   6. we can prove this is the first prompt of this session, by writing a marker
#
# (6) is the one that used to fail silently: the marker lived under $TMPDIR, so
# any environment handing out a per-invocation temp directory lost the
# once-per-session guarantee without a word. It now lives under XDG_STATE_HOME,
# which is stable for the life of a machine, and a marker we cannot write means
# we cannot promise to stay quiet afterwards — so we say nothing now.

input=$(cat)

# One python3 call, three lines out, and nothing inferred when it fails: an empty
# field is treated as "unknown", and unknown never fires.
state=$(printf '%s' "$input" | python3 -c '
import json
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    raise SystemExit(1)
if not isinstance(data, dict):
    raise SystemExit(1)


def text(key):
    value = data.get(key)
    return value if isinstance(value, str) and "\n" not in value else ""


print("true" if data.get("stop_hook_active") is True else "false")
print(text("session_id"))
print(text("transcript_path"))
' 2>/dev/null) || exit 0

stop_hook_active=$(printf '%s' "$state" | sed -n 1p)
session_id=$(printf '%s' "$state" | sed -n 2p)
transcript_path=$(printf '%s' "$state" | sed -n 3p)

[ "$stop_hook_active" = "true" ] && exit 0

# No session id means no way to fire once and then stay quiet. Firing anyway —
# which is what older versions did for "older clients" — turns every turn of
# every session into a blocked stop. Silence is the safe side of that trade.
[ -n "$session_id" ] || exit 0

[ -n "$transcript_path" ] || exit 0
[ -f "$transcript_path" ] || exit 0

# Did this session use rig? The instinct store is `.rig/instincts.jsonl`, so the
# question only makes sense in a session that engaged with rig — and the hook has
# no business interrupting a session that did not. Same technique as
# remind-rig-header.sh: read the transcript rather than guess from repository
# state, because repository state cannot distinguish this session from yesterday's.
if ! grep -q -e '▸ rig |' -e 'rig-wb ' -e 'workbench.py ' -e '/rig:' \
     "$transcript_path" 2>/dev/null; then
  exit 0
fi

# Setup and ad-hoc use outside a Git project can mention rig in the transcript,
# but there is no project-level instinct store for the reminder to update. The
# Stop hook is a project-learning feature, so do not let it interrupt those
# sessions. Keep both checks affirmative: a missing `.rig` or an unavailable
# Git root means this is not an adopted project session.
[ -d .rig ] || exit 0
git rev-parse --show-toplevel >/dev/null 2>&1 || exit 0

# The command has to be one the reader can actually run. The previous version
# hardcoded `python3 scripts/workbench.py`, a repo-relative path that does not
# exist in any project that installed rig — which is every project this hook
# ships to. Resolve it the way inject-instincts.sh already does.
if command -v rig-wb >/dev/null 2>&1; then
  instincts_cmd="rig-wb wb instincts"
else
  script_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." 2>/dev/null && pwd)
  workbench="$script_dir/scripts/workbench.py"
  [ -n "$script_dir" ] && [ -f "$workbench" ] || exit 0
  instincts_cmd="python3 $workbench instincts"
fi

# Once per session. Anything outside [A-Za-z0-9_-] becomes an underscore, so the
# name can never escape the marker directory; dots are folded too, since an id of
# ".." would resolve to the parent directory, which always exists and would
# silence the hook for good.
if [ -n "$XDG_STATE_HOME" ]; then
  marker_base="$XDG_STATE_HOME"
elif [ -n "$HOME" ]; then
  marker_base="$HOME/.local/state"
else
  marker_base="${TMPDIR:-/tmp}"
fi
safe_id=$(printf '%s' "$session_id" | tr -c 'A-Za-z0-9_-' '_')
marker_dir="$marker_base/rig/instinct-prompts"
marker="$marker_dir/$safe_id"

[ -e "$marker" ] && exit 0
mkdir -p "$marker_dir" 2>/dev/null || exit 0
: > "$marker" 2>/dev/null || exit 0

message=$(cat <<EOF
[rig instincts] Before ending: did this session surface a durably useful, project-specific pattern (a preferred style/tool, a fast way to search this codebase, a gotcha worth remembering) that isn't already captured in facets/knowledge? If so — and only if it's genuinely reusable, not a one-off — record it with:
  $instincts_cmd --add "<short, standalone statement>" --evidence "<why you believe this>" --task-id <id if applicable> --confidence <0.0-1.0>
If an existing instinct is now wrong or superseded, use --supersedes <id> so the old one is muted rather than left to contradict the new one. Do not propose one if nothing new and reusable came up — most sessions won't have one, and saying so in one line is the expected answer.
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
