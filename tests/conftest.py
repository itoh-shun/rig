"""pytest bootstrap for the rig orchestrator suite.

- Inserts the repo root into sys.path so `rig_workbench` imports from any cwd.
- Pins RIG_HOME to the repo checkout *before* rig_workbench.orchestrate.config
  is first imported (config resolves RIG_HOME at import time).
- Sets RIG_SKIP_GH_CHECK so the suite does not depend on the developer's real
  `gh` state (see below).
- Provides tmp fixtures so no test touches the real repo's .rig/ state.
"""

import os
import pathlib
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Must happen before any rig_workbench import (config reads env at import time).
os.environ["RIG_HOME"] = str(REPO_ROOT)

# gh / gh-stack no longer gate anything (rig_workbench/gh_requirement.py), so
# this is not about letting the suite run — it is about stderr. `workbench new`
# and `orchestrate run` print a one-line note when gh-stack is absent, and that
# line would appear or not appear depending on whether the machine running pytest
# happens to have it, leaking into every test that asserts on a subprocess's
# stderr. Silencing it pins the suite to one behaviour;
# tests/test_gh_requirement.py owns the advisory and sets this per test.
os.environ["RIG_SKIP_GH_CHECK"] = "1"

# Every run that finishes is mirrored into ~/.rig/runs.jsonl for cross-project rollups
# (runstate.append_run_record). Tests finish runs, so without this the suite writes into
# the developer's own cross-project history and `rig-wb usage --global` starts counting
# fixtures as work. Only the global mirror is redirected: RIG_RUNS_PATH is deliberately
# left alone, because a test that runs the CLI in a tmp repo expects the per-project log
# to land in that repo's .rig/, and pinning it here would take that away.
# The host instinct tier (`~/.rig/instincts.jsonl`). `select_for_injection` writes to it —
# it bumps hit_count and refreshes last_seen on everything it picks — and the hook that
# calls it is exercised by tests that have nothing to do with instincts
# (test_codex_integration runs inject-instincts.sh with a copy of os.environ). A per-file
# fixture cannot cover those, so on a machine that has promoted even one instinct the
# suite would inflate its hit_count and push back its decay, invisibly.
os.environ.setdefault("RIG_USER_HOME",
                      tempfile.mkdtemp(prefix="rig-test-user-home-"))

os.environ.setdefault("RIG_GLOBAL_RUNS_PATH",
                      str(pathlib.Path(tempfile.mkdtemp(prefix="rig-test-global-runs-"))
                          / "runs.jsonl"))

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# How much slower than a developer machine a CI runner is allowed to be before a
# `subprocess.run(..., timeout=...)` in this suite gives up. Sized off a real
# failure, not off a guess: the jp-workflow dry-run costs ~16s of pure
# single-threaded CPU locally (`real 16.4s / user 15.4s`) and still blew a 30s
# budget on GitHub's 3.12 runner, so a factor of 2 is demonstrably too small.
# Six keeps ~3x headroom over the slowest run CI has actually shown us.
#
# The default has to be the safe value on its own: .github/workflows/validate.yml
# runs `pytest -q -n auto` and sets no environment, so CI always takes this number
# — and takes it under 4-way contention rather than serially, which eats into the
# headroom the factor was originally cut against. Measured rather than assumed:
# forcing the factor to 2 binds exactly one call site (the 30s floor below covers
# every measurement under 15s), and that site is the jp-workflow dry-run this
# comment was written about. Under `-n 4` at factor 2 the suite still passed with
# no timeout among its failures, so contended cost stays inside 2x measured and 6
# keeps roughly 3x headroom in parallel too.
# The override exists for the opposite case — a machine slower still, or a
# developer deliberately tightening the budget to hunt a hang.
CI_TIMEOUT_FACTOR = float(os.environ.get("RIG_TEST_TIMEOUT_FACTOR", "6"))

# Nothing gets less than this, however fast it measures: a subprocess that
# normally finishes in 50ms is not healthier for being killed at 300ms, and the
# floor is what keeps the fast call sites at the suite's existing 30s idiom.
MIN_SUBPROCESS_TIMEOUT = 30.0


def subprocess_timeout(measured_seconds: float) -> float:
    """Budget for a `subprocess.run(timeout=...)` from the call's measured cost.

    Pass what the subprocess actually costs on a developer machine — measure it,
    do not estimate it — and this scales it for a loaded CI runner. Recording the
    measurement at the call site is the point: a bare `timeout=30` says nothing
    about whether 30 is generous or one bad scheduling window from flaking.
    """
    return max(MIN_SUBPROCESS_TIMEOUT, measured_seconds * CI_TIMEOUT_FACTOR)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_ambient_worktree_runtime(monkeypatch):
    """Keep runtime selection independent of the developer's host session.

    Tests that exercise Orca set these variables themselves after this fixture runs, while
    every other test — including subprocesses spawned from it — gets the native default.
    Import the names from the detector so the suite follows the production environment
    contract instead of maintaining a second hard-coded list.
    """
    from rig_workbench.workbench import orca

    for name in (orca.WORKTREE_VAR, orca.WORKSPACE_VAR):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def step_factory():
    """Build a minimal step dict with the full key set new_state/compute_next expect."""
    from rig_workbench.orchestrate.config import DEFAULT_K

    def make(**k):
        return {
            "id": k["id"],
            "instruction": k.get("instruction", "x"),
            "gate": k.get("gate"),
            "pattern": k.get("pattern"),
            "personas": k.get("personas", []),
            "needs": k.get("needs", []),
            "acceptance": k.get("acceptance", []),
            "checks": k.get("checks", []),
            "max_retries": k.get("max_retries", DEFAULT_K),
            "output_contract": k.get("output_contract"),
            "actor": k.get("actor"),
            "human_gate": k.get("human_gate"),
        }

    return make


@pytest.fixture
def tmp_queue(tmp_path, monkeypatch):
    """Rebind queueing.QUEUE_PATH to a scratch file (mirrors the selftest pattern)."""
    from rig_workbench.orchestrate import queueing

    qpath = tmp_path / "queue.json"
    monkeypatch.setattr(queueing, "QUEUE_PATH", qpath)
    return qpath


@pytest.fixture
def recipe_dir(tmp_path):
    """Scratch directory for synthetic recipe .md files."""
    d = tmp_path / "recipes"
    d.mkdir()
    return d


@pytest.fixture
def write_recipe(recipe_dir):
    def write(name: str, body: str) -> pathlib.Path:
        p = recipe_dir / f"{name}.md"
        p.write_text(body, encoding="utf-8")
        return p

    return write
