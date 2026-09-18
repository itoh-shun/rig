"""Shell adapter binding strict orchestrator runs to workbench acceptance.

Task metadata is outside the provider-writable worktree. Both marker copies must
agree; removing only one cannot downgrade a strict task to legacy acceptance.
Operators controlling all metadata remain trusted (this is not authentication).
"""
from contextlib import contextmanager
import json
from pathlib import Path
import re

from .batch_surface import STRICT_TASK_STORE
from .deterministic_io import StrictIO


def _binding(state_path, run_id):
    return {"schema_version": 1, "state_path": str(Path(state_path).resolve()), "run_id": run_id}


def _identity(root, task_id):
    return {"root": str(Path(root).resolve()), "task_id": task_id}


@contextmanager
def bound_task(task_id, cwd, state, state_path):
    """Lock the task for the complete run; bind intent before initialization."""
    if task_id is None:
        yield None
        return
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", task_id):
        raise ValueError("invalid deterministic task id")
    root = STRICT_TASK_STORE.root()
    with STRICT_TASK_STORE.lock(root, task_id):
        directory, task = STRICT_TASK_STORE.load(root, task_id)
        if task.get("status") in ("accepted", "discarded"):
            raise ValueError("deterministic task must be active")
        if not isinstance(task.get("worktree_path"), str) or not task["worktree_path"]:
            raise ValueError("deterministic task requires an existing isolated worktree")
        workspace = Path(task["worktree_path"]).resolve(strict=True)
        registered = {Path(line[len("worktree "):]).resolve()
                      for line in STRICT_TASK_STORE.worktrees(root).splitlines()
                      if line.startswith("worktree ")}
        if workspace == root.resolve() or workspace not in registered:
            raise ValueError("deterministic task requires a registered isolated worktree")
        output = Path(state_path).resolve()
        if output.is_relative_to(workspace):
            raise ValueError("deterministic state must be outside task workspace")
        sidecar = directory / "deterministic-binding.json"
        if "deterministic_binding" in task or sidecar.exists() or sidecar.is_symlink():
            raise ValueError("task already bound; resume the existing deterministic run")
        binding = _binding(output, state["run_id"])
        state["deterministic_binding"] = _identity(root, task_id)
        # Sidecar first: partial failure must fail acceptance closed.
        STRICT_TASK_STORE.write_json(directory / "deterministic-binding.json", binding)
        task["deterministic_binding"] = binding
        STRICT_TASK_STORE.save(directory, task)
        yield workspace


def validate_task_acceptance(root, task_id, directory, task, validator):
    """Additional, non-forceable acceptance condition; caller holds task lock."""
    sidecar = Path(directory) / "deterministic-binding.json"
    if "deterministic_binding" not in task and not (sidecar.exists() or sidecar.is_symlink()):
        return
    marker = task.get("deterministic_binding")
    if not isinstance(marker, dict) or not sidecar.is_file() or sidecar.is_symlink():
        raise ValueError("missing deterministic binding marker or sidecar")
    recorded = json.loads(sidecar.read_text(encoding="utf-8"))
    if marker != recorded or set(marker) != {"schema_version", "state_path", "run_id"}:
        raise ValueError("deterministic binding mismatch")
    if type(marker["schema_version"]) is not int or marker["schema_version"] != 1:
        raise ValueError("unsupported deterministic binding schema")
    if not isinstance(marker["state_path"], str) or not Path(marker["state_path"]).is_absolute():
        raise ValueError("deterministic state path must be absolute")
    if type(task.get("worktree_path")) is not str or not task["worktree_path"]:
        raise ValueError("invalid deterministic task workspace")
    state_path = Path(marker["state_path"])
    state = StrictIO(Path(task["worktree_path"]), state_path).load()
    if "deterministic_runtime" not in state:
        raise ValueError("bound state lost deterministic runtime")
    if state.get("run_id") != marker["run_id"] or state.get("deterministic_binding") != _identity(root, task_id):
        raise ValueError("deterministic task/run reciprocal binding mismatch")
    validator(state_path, Path(task["worktree_path"]))

@contextmanager
def resumed_task(state, state_path):
    """Keep a bound task locked while resuming; reject missing metadata copies."""
    identity = state.get("deterministic_binding")
    if identity is None:
        yield
        return
    if not isinstance(identity, dict) or set(identity) != {"root", "task_id"}:
        raise ValueError("invalid deterministic task identity")
    if type(identity["root"]) is not str or not identity["root"]:
        raise ValueError("invalid deterministic task root")
    root = Path(identity["root"])
    task_id = identity["task_id"]
    if not root.is_absolute() or not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", task_id):
        raise ValueError("invalid deterministic task identity")
    with STRICT_TASK_STORE.lock(root, task_id):
        directory, task = STRICT_TASK_STORE.load(root, task_id)
        expected = _binding(state_path, state["run_id"])
        sidecar = directory / "deterministic-binding.json"
        if sidecar.is_symlink() or not sidecar.is_file():
            raise ValueError("missing deterministic binding sidecar")
        if task.get("deterministic_binding") != expected or json.loads(sidecar.read_text(encoding="utf-8")) != expected:
            raise ValueError("deterministic binding mismatch on resume")
        if task.get("status") in ("accepted", "discarded"):
            raise ValueError("cannot resume completed workbench task")
        yield
