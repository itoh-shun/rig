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
# runs bare `pytest -q` and sets no environment, so CI always takes this number.
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
