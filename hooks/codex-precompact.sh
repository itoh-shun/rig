#!/usr/bin/env sh
# Codex PreCompact hook.
# Codex requires common JSON output and does not consume plaintext stdout as
# compaction instructions. Actual re-anchoring is attempted by
# inject-run-continuity.sh on SessionStart(source=compact).

printf '%s\n' '{"continue":true}'
