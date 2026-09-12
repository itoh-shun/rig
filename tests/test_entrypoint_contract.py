"""That the five installed console entry points actually start (Stage 1).

`pyproject.toml`'s `[project.scripts]` is the only thing standing between a user
typing `rig-mcp` and a "command not found". Everything else in this repo can be
refactored freely; that table cannot, because pip has already written those five
names into people's `$PATH` and a rename is a broken install on the next upgrade.

tests/test_exit_code_contract.py::test_every_installed_entry_point_is_guarded
already imports each declared module and checks that its `main` is wrapped in the
exit-code guard. That is a statement about the source. It is not a statement about
the program: a module can import cleanly, expose a guarded `main`, and still fail
to start — a top-level import of a missing optional dependency, an argparse
`prog=` that no longer matches, a `main()` that binds a socket before it looks at
`argv`. This file is the complement, and it goes through a real process precisely
because that is the part an import cannot see.

Two things are pinned, not one:

  * the *set* of entry-point names, so dropping or renaming one is a deliberate
    edit to this file rather than a silent break in somebody's shell; and
  * for each name, a line of its own `--help` that identifies *that* tool. A test
    that only asserted "exit 0 and some output" would pass just as happily with
    two entry points wired to the same module, which is the exact refactor
    accident this stage exists to catch. IDENTIFYING_LINE is therefore checked
    both ways: present in its own tool's help, and absent from all four others.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from conftest import REPO_ROOT, subprocess_timeout

try:                                        # 3.11+ ships it; the dev extra backfills below
    import tomllib
except ModuleNotFoundError:                 # pragma: no cover - exercised on 3.10 only
    import tomli as tomllib

PYPROJECT = REPO_ROOT / "pyproject.toml"

#: The frozen contract, spelled out here rather than read from pyproject, so that the
#: assertion below compares two independent statements of it. Values included as well as
#: keys: `rig-mcp` still resolving to *a* module is not the promise — it resolving to the
#: MCP adapter is.
FROZEN_ENTRY_POINTS = {
    "rig-wb": "rig_workbench.cli:main",
    "rig-evidence": "rig_workbench.evidence:main",
    "rig-mission-control": "rig_workbench.mission_control:main",
    "rig-mission-control-live": "rig_workbench.mission_server:main",
    "rig-mcp": "rig_workbench.remote_mcp:main",
}

#: A fragment of `--help` output unique to each tool. Taken from the argparse
#: `description=` (or, for rig-wb, its hand-rolled banner) rather than from `prog=`,
#: because prog names nest: "rig-mission-control" is a substring of
#: "rig-mission-control-live", so a usage-line match would let the live server
#: impersonate the static one. No version numbers either — those move every release
#: and would make this file a merge conflict rather than a contract.
IDENTIFYING_LINE = {
    "rig-wb": "quality-gated AI workbench",
    "rig-evidence": "real-project evidence",
    "rig-mission-control": "read-only RIG Mission Control",
    "rig-mission-control-live": "interactive localhost RIG Mission Control",
    "rig-mcp": "Rig remote MCP adapter",
}

#: The two entry points whose `main()` is *designed* never to return: mission_server
#: calls `serve_forever()` on a loopback HTTP server, remote_mcp calls `mcp.run()`. They
#: are probed here anyway because both parse `argv` before they bind anything, so
#: argparse's `--help` raises SystemExit(0) from inside `parse_args` and the process is
#: gone long before a socket exists — verified, not assumed, by
#: `test_a_long_running_server_answers_help_instead_of_binding` below.
#:
#: The limitation this leaves on record: `--help` is the *only* bounded probe these two
#: have. There is no `--version`, no dry-run, no "start and exit" flag, so this file
#: cannot prove that their serving path works, only that the program starts, reads its
#: arguments and identifies itself. If a future change ever moves the bind above
#: `parse_args`, HELP_TIMEOUT_SECONDS is what stops this test hanging a CI runner: the
#: probe fails with the tool's name instead of blocking, see `_run_help`.
LONG_RUNNING_ENTRY_POINTS = ("rig-mission-control-live", "rig-mcp")

#: Measured, as conftest.subprocess_timeout's docstring asks, not estimated: the five
#: `--help` calls cost 0.04s, 0.05s, 0.08s, 0.10s and 0.08s on a developer machine.
#: MIN_SUBPROCESS_TIMEOUT means every one of them gets the suite's 30s floor in practice;
#: the measurement is recorded so a successor who makes a startup path genuinely
#: expensive has a number to raise rather than a bare literal to guess at.
HELP_MEASURED_SECONDS = 0.5
HELP_TIMEOUT_SECONDS = subprocess_timeout(HELP_MEASURED_SECONDS)


def _module_of(entry_point: str) -> str:
    return FROZEN_ENTRY_POINTS[entry_point].split(":", 1)[0]


def _run_help(entry_point: str) -> subprocess.CompletedProcess:
    """`python -m <module> --help` as a real process, bounded and reproducible.

    `sys.executable`, not a literal `python3`: the child has to be the interpreter
    running the suite, or a tox/venv run would probe whatever `python3` happens to be
    first on PATH. PYTHONPATH is the repo root for the same reason the `rig_cli` fixture
    sets it — a machine with a released rig-wb installed must still exercise the tree
    under test. COLUMNS is pinned because argparse wraps its description to the terminal
    width, and an inherited COLUMNS from a CI runner would decide whether
    IDENTIFYING_LINE survives on one line.
    """
    env = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join(
            p for p in (str(REPO_ROOT), os.environ.get("PYTHONPATH")) if p),
        PYTHONIOENCODING="utf-8",
        PYTHONUTF8="1",
        COLUMNS="80",
    )
    try:
        return subprocess.run(
            [sys.executable, "-m", _module_of(entry_point), "--help"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=HELP_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        # Never re-raise as a hang: a blocked `--help` is the defect this file is
        # watching for, so it has to arrive as a named failure.
        pytest.fail(
            f"`{entry_point}` (python -m {_module_of(entry_point)} --help) did not return "
            f"within {HELP_TIMEOUT_SECONDS:.0f}s. `--help` must be answered before the "
            f"program binds a socket or enters its serve loop; see "
            f"LONG_RUNNING_ENTRY_POINTS.", pytrace=False)


@pytest.fixture(scope="module")
def help_output():
    """`entry point name -> CompletedProcess`, computed once for the whole module."""
    return {name: _run_help(name) for name in FROZEN_ENTRY_POINTS}


def test_the_installed_entry_points_are_exactly_the_frozen_five():
    """Adding, dropping or retargeting a console script is an edit to this file.

    Set equality rather than a subset check on purpose: a *new* entry point is as much
    of a contract change as a deleted one, because from the release onward users will
    have it and rig will have to keep it.
    """
    declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["scripts"]

    assert set(declared) == set(FROZEN_ENTRY_POINTS), (
        "pyproject's [project.scripts] no longer matches the frozen set. "
        f"only in pyproject: {sorted(set(declared) - set(FROZEN_ENTRY_POINTS))}; "
        f"only in this test: {sorted(set(FROZEN_ENTRY_POINTS) - set(declared))}")
    retargeted = {k: v for k, v in declared.items() if FROZEN_ENTRY_POINTS.get(k) != v}
    assert declared == FROZEN_ENTRY_POINTS, (
        f"an entry point still exists but points somewhere else: {retargeted}")


@pytest.mark.parametrize("entry_point", sorted(FROZEN_ENTRY_POINTS))
def test_every_entry_point_starts_and_says_which_tool_it_is(entry_point, help_output):
    """Exit 0 *and* a self-identifying line. Either alone is not evidence."""
    result = help_output[entry_point]
    marker = IDENTIFYING_LINE[entry_point]

    assert result.returncode == 0, (
        f"`{entry_point}` failed to start: python -m {_module_of(entry_point)} --help "
        f"exited {result.returncode}\n--- stdout ---\n{result.stdout}"
        f"\n--- stderr ---\n{result.stderr}")

    lines = result.stdout.splitlines()
    assert any(marker in line for line in lines), (
        f"`{entry_point}` started but did not identify itself: no line of its --help "
        f"contains {marker!r}. Either the tool was rewired to another module or its "
        f"description changed, in which case update IDENTIFYING_LINE deliberately."
        f"\n--- stdout ---\n{result.stdout}")


def test_no_two_entry_points_answer_with_the_same_identity(help_output):
    """The reason the markers are checked negatively too.

    Wiring two console scripts at one module is a plausible refactor slip and every
    per-tool assertion above would still pass, because each would find its own marker in
    a help text it merely shares. Requiring each marker to be *absent* from the other
    four makes the five outputs pairwise distinguishable, which is the property that
    actually says "five programs".
    """
    for owner, marker in IDENTIFYING_LINE.items():
        impostors = [name for name, result in help_output.items()
                     if name != owner and marker in result.stdout]
        assert not impostors, (
            f"{marker!r} identifies `{owner}`, but it also appears in the --help of "
            f"{impostors}. Two entry points are answering as the same tool.")


@pytest.mark.parametrize("entry_point", LONG_RUNNING_ENTRY_POINTS)
def test_a_long_running_server_answers_help_instead_of_binding(entry_point, help_output):
    """`rig-mission-control-live` and `rig-mcp` serve forever once started.

    That makes them the two entry points a naive smoke test cannot probe at all — and
    the two most worth probing, since a startup failure in a server is otherwise only
    discovered by a user waiting for a port that never opens. Both answer `--help` from
    inside `parse_args`, above the bind, so the process exits on its own; `_run_help`'s
    timeout is the backstop if that order is ever inverted.

    The assertion is that nothing from the serving path ran: mission_server prints its
    URL and `repo:` banner the moment it has a socket, remote_mcp's FastMCP prints its
    own startup lines, so their absence is what distinguishes "printed help and exited"
    from "started serving and got killed".
    """
    assert entry_point in FROZEN_ENTRY_POINTS, (
        f"LONG_RUNNING_ENTRY_POINTS names `{entry_point}`, which is not in "
        f"FROZEN_ENTRY_POINTS. The two tables have drifted apart inside this file.")
    result = help_output[entry_point]

    assert result.returncode == 0, (
        f"`{entry_point}` did not exit 0 from --help (got {result.returncode})"
        f"\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}")

    combined = (result.stdout + result.stderr).lower()
    for started_serving in ("http://127.0.0.1", "serving", "uvicorn", "running on"):
        assert started_serving not in combined, (
            f"`{entry_point} --help` looks like it started serving before printing help "
            f"({started_serving!r} in its output). Help must be answered before the bind."
            f"\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}")

    assert "usage:" in result.stdout.lower(), (
        f"`{entry_point} --help` exited 0 but printed no usage line, so there is no "
        f"evidence it got as far as its argument parser.\n--- stdout ---\n{result.stdout}")
