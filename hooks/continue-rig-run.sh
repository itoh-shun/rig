#!/usr/bin/env sh
# rig run-continuity — Stop hook.
# Fires when the model tries to end its turn. Blocks the stop (one push) only when a rig
# RUN is mid-flow and the turn ended without asking the person anything: the header shows
# step n/N with n < N, the gate is not REJECT, the stuck-guard is not at 2/2, a gated RUN
# has not reached a step boundary, and no background task is still due to notify. Pushes
# again only after the header moved, so it cannot loop. Every rule, and why the retired
# instinct Stop reminder is not what this is, lives in
# rig_workbench/workbench/stop_continue.py.
#
# Silent no-op in a provider subprocess, with RIG_AUTO_CONTINUE=0, without python3, or on
# any error: a Stop hook must fail open.

[ -n "${RIG_PROVIDER_SUBPROCESS:-}" ] && exit 0
command -v python3 >/dev/null 2>&1 || exit 0

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -m rig_workbench.workbench.stop_continue 2>/dev/null
exit 0
