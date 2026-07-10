"""pytest bootstrap for the rig orchestrator suite.

- Inserts the repo root into sys.path so `rig_workbench` imports from any cwd.
- Pins RIG_HOME to the repo checkout *before* rig_workbench.orchestrate.config
  is first imported (config resolves RIG_HOME at import time).
- Provides tmp fixtures so no test touches the real repo's .rig/ state.
"""

import os
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Must happen before any rig_workbench import (config reads env at import time).
os.environ["RIG_HOME"] = str(REPO_ROOT)

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
