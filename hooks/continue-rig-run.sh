#!/usr/bin/env sh
# rig run-continuity — Stop hook.
# Fires when the model tries to end its turn during a rig RUN and applies the stop /
# retry / continue table of SKILL.md §6 ⑤, stop rules first: a declared `▸ stop:`, a
# question, REJECT, stuck 2/2, the last step, a gated step boundary, a pending background
# task, an outward or irreversible next move, a missing capability and a repeated failure
# all let it stop. A transient failure is re-run once; only a silent mid-flow stop is
# pushed on. A second push needs the header to have moved. Every rule lives in
# rig_workbench/workbench/stop_continue.py.
#
# Silent no-op in a provider subprocess, with RIG_AUTO_CONTINUE=0, without python3, or on
# any error: a Stop hook must fail open.

[ -n "${RIG_PROVIDER_SUBPROCESS:-}" ] && exit 0
command -v python3 >/dev/null 2>&1 || exit 0

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -m rig_workbench.workbench.stop_continue 2>/dev/null
exit 0
