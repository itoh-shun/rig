"""What rig's exit status promises a caller (#416 Phase 2).

The point of a machine interface is that a script can act on the answer without
reading the prose. Three answers are worth distinguishing, and rig already spelled
two of them consistently across its commands — `0` when the gate passed or a scan
found nothing, `1` when it found something, `2` when the command could not be run
at all. What was missing is the case nobody chooses: rig crashing.

An unhandled exception exits 1. That is the same code as "reviewed, and the answer
is no", so a traceback and a verdict are indistinguishable to the thing that has to
decide whether to merge. A CI job reading `1` as REJECT will merge nothing and
report a review that never happened; one reading it as "the tool is flaky" will
merge past a real rejection. Both readings are defensible, which is what makes the
collision a defect rather than a preference.

So: a crash is `2`, alongside the other ways rig fails to produce an answer. `1`
means rig ran, judged, and said no.

The reserved range is the other half of the promise. The shell and GNU coreutils
already own 124, 126, 127 and 128+N, and rig's provider layer already returns 124
and 127 with exactly their conventional meanings. Assigning rig semantics to any of
them would make `timeout 60 rig-wb ...` ambiguous in a way no caller could unpick.
"""

import signal

import pytest

from rig_workbench import exitcodes


def test_the_three_answers_a_caller_can_act_on():
    assert exitcodes.OK == 0
    assert exitcodes.REJECTED == 1
    assert exitcodes.ERROR == 2


def test_a_crash_is_not_a_verdict():
    """The whole reason this module exists: `1` has to mean rig judged and said no."""
    def boom():
        raise ValueError("the sort of thing nobody planned for")

    with pytest.raises(SystemExit) as raised:
        exitcodes.run_guarded(boom)
    assert raised.value.code == exitcodes.ERROR
    assert raised.value.code != exitcodes.REJECTED


def test_a_deliberate_exit_passes_through_untouched():
    """Commands already exit 1 for findings and 2 for usage, and argparse exits 2 on
    its own. The guard must not relabel a status somebody chose."""
    for code in (0, 1, 2, 3, 7):
        with pytest.raises(SystemExit) as raised:
            exitcodes.run_guarded(lambda code=code: (_ for _ in ()).throw(SystemExit(code)))
        assert raised.value.code == code


def test_a_clean_return_stays_a_return():
    """`sys.exit(main())` in the installed console script turns `None` into 0 on its
    own. Raising `SystemExit(OK)` here instead would buy nothing and would make every
    in-process caller — the test suite included — catch an exception to observe
    success."""
    assert exitcodes.run_guarded(lambda: None) is None
    assert exitcodes.run_guarded(lambda: "value") == "value"


def test_an_interrupt_reports_the_signal_the_way_a_shell_does():
    """128+SIGINT. Python's own default is 1, which would put Ctrl-C in the same
    bucket as a rejection."""
    def interrupted():
        raise KeyboardInterrupt

    with pytest.raises(SystemExit) as raised:
        exitcodes.run_guarded(interrupted)
    assert raised.value.code == 128 + signal.SIGINT == 130


def test_the_reserved_codes_are_left_to_the_shell():
    """`timeout` returns 124, a shell returns 126/127, and a signalled process
    returns 128+N. rig's provider layer already speaks 124 and 127 with those exact
    meanings, so none of them may carry a rig verdict."""
    assigned = {exitcodes.OK, exitcodes.REJECTED, exitcodes.ERROR}
    assert assigned.isdisjoint(exitcodes.RESERVED)
    assert {124, 126, 127}.issubset(exitcodes.RESERVED)
    assert all(128 + sig in exitcodes.RESERVED for sig in (signal.SIGINT, signal.SIGTERM))


@pytest.mark.parametrize("module_path", [
    "rig_workbench.cli",
    "rig_workbench.evidence",
    "rig_workbench.mission_control",
    "rig_workbench.mission_server",
    "rig_workbench.remote_mcp",
])
def test_every_installed_entry_point_is_guarded(module_path):
    """An entry point pyproject installs is a surface somebody scripts against. One
    that is not guarded still answers a crash with `1`, and the contract is only
    worth as much as its least careful command."""
    import importlib

    main = getattr(importlib.import_module(module_path), "main")
    assert getattr(main, "__rig_guarded__", False), (
        f"{module_path}:main is not wrapped by exitcodes.guard — an unhandled "
        "exception there exits 1, which this contract reserves for a verdict."
    )
