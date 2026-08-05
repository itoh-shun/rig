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

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Must happen before any rig_workbench import (config reads env at import time).
os.environ["RIG_HOME"] = str(REPO_ROOT)

# `workbench new` / `orchestrate run` refuse to start without the gh + gh-stack
# requirement (rig_workbench/gh_requirement.py). Test runs are simulations, not
# rig runs, and must not pass or fail depending on whether the machine running
# pytest happens to have `gh` and the gh-stack extension. Set the documented
# escape hatch for the whole suite; tests/test_gh_requirement.py owns the
# requirement's behaviour and controls this variable explicitly per test.
os.environ["RIG_SKIP_GH_CHECK"] = "1"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

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
