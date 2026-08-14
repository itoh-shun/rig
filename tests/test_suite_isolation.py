"""The suite must not write into the developer's own cross-project rig state.

Two stores live outside any repository and are written as a side effect of ordinary
runs: `~/.rig/runs.jsonl` (every finished run is mirrored there) and
`~/.rig/instincts.jsonl` (`select_for_injection` bumps hit_count and refreshes
last_seen on everything it picks). Tests finish runs and tests exercise the hook that
triggers injection — including tests that have nothing to do with either feature — so
the redirection has to be process-wide, in conftest, not a fixture each file remembers
to add.

Asserted on the resolved paths rather than on a file's absence: "nothing was written"
is also what a machine that has never used the feature looks like, so absence proves
nothing. This file deliberately declares no fixture of its own — one would answer the
question before it is asked.
"""

import os
import pathlib

import pytest

from rig_workbench.orchestrate import config
from rig_workbench.workbench.instincts import _host_instincts_path


def test_the_host_instinct_tier_is_pinned_away_from_the_real_home():
    assert os.environ.get("RIG_USER_HOME"), "conftest must pin RIG_USER_HOME for the whole suite"

    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("RIG_USER_HOME", raising=False)
        unpinned = _host_instincts_path()

    assert _host_instincts_path() != unpinned
    assert pathlib.Path.home() not in _host_instincts_path().parents


def test_the_global_run_mirror_is_pinned_away_from_the_real_home():
    assert os.environ.get("RIG_GLOBAL_RUNS_PATH"), \
        "conftest must pin RIG_GLOBAL_RUNS_PATH for the whole suite"
    assert pathlib.Path.home() not in config.GLOBAL_RUNS_PATH.parents


def test_the_per_project_run_log_is_left_alone():
    """The counterpart: `RIG_RUNS_PATH` is deliberately *not* pinned, because a test that
    runs the CLI in a tmp repo expects the log to land in that repo's `.rig/`. Pinning it
    would take that away, so this records the choice rather than leaving it to be
    rediscovered."""
    assert "RIG_RUNS_PATH" not in os.environ
