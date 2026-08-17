"""rig's exit status, as a promise to whatever is reading it (#416 Phase 2).

Every rig command is called by something that cannot read prose — a CI step, a
Makefile, another agent's harness. Its exit status is the whole of what that caller
gets, so the statuses have to mean distinct things and keep meaning them.

Three answers matter, and rig already spelled two of them the same way everywhere
before this module existed: `0` when the gate passed or a scan found nothing, `1`
when it found something, `2` when the command could not be run at all. This module
names them and closes the case that was missing.

**A crash is not a verdict.** An unhandled exception exits 1 by default, which is
the code for "rig reviewed this and the answer is no". A caller cannot tell those
apart, and both readings of the ambiguity are wrong in opposite directions: treat
`1` as a rejection and a traceback silently becomes a review nobody performed;
treat it as flakiness and a real rejection gets retried past. So an unplanned
exception is `2` — grouped with the other ways rig fails to produce an answer,
which is what a crash is.

**The reserved codes belong to the shell.** GNU `timeout` returns 124, a shell
returns 126 for a non-executable and 127 for a missing command, and a process
killed by signal N returns 128+N. rig's own provider layer already returns 124 and
127 with exactly those meanings. Giving any of them a rig meaning would make
`timeout 60 rig-wb ...` ambiguous in a way the caller has no way to unpick, so
`RESERVED` is checked by the test suite and never assigned.
"""

from __future__ import annotations

import functools
import signal
import sys
import traceback
from typing import Callable, TypeVar

#: rig ran and the answer is yes — gate passed, scan clean, nothing to report.
OK = 0

#: rig ran, judged, and the answer is no — gate failed, findings exist. A verdict
#: rig means, not a malfunction. Callers act on this; they do not retry it.
REJECTED = 1

#: rig could not produce an answer: bad usage, missing configuration, unreadable
#: state, or an unplanned exception. argparse already exits 2 for usage errors,
#: which is the same claim.
ERROR = 2

#: Statuses whose meaning is fixed outside rig. Never assign these.
RESERVED = frozenset(
    {124, 126, 127} | {128 + int(sig) for sig in signal.Signals}
)

T = TypeVar("T")


def run_guarded(fn: Callable[[], T], *args, **kwargs):
    """Run `fn` and exit with the status this contract promises.

    A `SystemExit` passes through untouched — commands choose 1 for a finding and 2
    for bad usage, argparse raises its own, and relabelling any of those would make
    the guard the thing that breaks the contract.

    A clean return is returned, not converted into `SystemExit(OK)`. The console
    scripts pyproject installs already run `sys.exit(main())`, so `None` becomes 0
    there for free — while in-process callers (the test suite among them) call these
    same functions and expect a return. Raising on success would make the guard the
    one thing that cannot be called normally.
    """
    try:
        return fn(*args, **kwargs)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        # 128+SIGINT, the way a shell reports it. Python's own default is 1, which
        # would file Ctrl-C under "rejected".
        print("\n[rig] interrupted", file=sys.stderr)
        raise SystemExit(128 + int(signal.SIGINT)) from None
    except BrokenPipeError:
        # `rig-wb ... | head` closes the pipe early. That is the caller's choice,
        # not a rejection and not a rig failure.
        raise SystemExit(OK) from None
    except Exception:
        traceback.print_exc()
        print("\n[rig] the command failed before it could reach a verdict; "
              f"exiting {ERROR} rather than {REJECTED} so this is not read as a "
              "rejection.", file=sys.stderr)
        raise SystemExit(ERROR) from None


def guard(fn):
    """Wrap an entry point's `main` so its crashes land on `ERROR`."""

    @functools.wraps(fn)
    def guarded(*args, **kwargs):
        return run_guarded(fn, *args, **kwargs)

    guarded.__rig_guarded__ = True
    guarded.__wrapped_main__ = fn
    return guarded
