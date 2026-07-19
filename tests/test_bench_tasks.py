from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest


PYTHON_TASK_IDS = (
    "py-auth-sibling-write",
    "py-transaction-rollback",
    "py-api-compat-rename",
    "py-pagination-boundary",
    "py-sql-construction",
)
TYPESCRIPT_TASK_IDS = (
    "ts-auth-sibling-handler",
    "ts-stale-cache-mutation",
    "ts-api-compat-export",
    "ts-async-error-propagation",
    "ts-generated-file-modification",
)


def _remove_tree(path: Path) -> None:
    def make_writable(function: object, blocked: str, _error: object) -> None:
        os.chmod(blocked, stat.S_IWRITE)
        function(blocked)

    shutil.rmtree(path, onerror=make_writable)


def _write_task(root: Path, directory: str = "sample", task_id: str = "sample-task") -> Path:
    task_root = root / directory
    repo = task_root / "repo"
    repo.mkdir(parents=True)
    (repo / "service.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "test_service.py").write_text(
        "from service import VALUE\n\n\ndef test_value():\n    assert VALUE > 0\n",
        encoding="utf-8",
    )
    (task_root / "hidden_check.py").write_text(
        "import pathlib\n"
        "import sys\n\n"
        "workspace = pathlib.Path(sys.argv[1])\n"
        "assert not (workspace / 'hidden_check.py').exists()\n"
        "import service\n"
        "assert service.VALUE == 2\n",
        encoding="utf-8",
    )
    for variant in ("canonical", "narrow"):
        variant_root = task_root / variant
        variant_root.mkdir()
        value = 2 if variant == "canonical" else 3
        (variant_root / "service.py").write_text(f"VALUE = {value}\n", encoding="utf-8")
    metadata = {
        "schema_version": 1,
        "id": task_id,
        "language": "python",
        "difficulty": "simple",
        "risk_domains": ["test"],
        "goal": "Set the service value to two.",
        "test_command": ["python", "-m", "pytest", "-q"],
        "hidden_command": ["python", "hidden_check.py"],
        "expected_files": ["service.py", "test_service.py"],
    }
    (task_root / "task.json").write_text(json.dumps(metadata), encoding="utf-8")
    return task_root


def _update_metadata(task_root: Path, **updates: object) -> None:
    metadata_path = task_root / "task.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata.update(updates)
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")


def test_load_tasks_returns_immutable_task_schema(tmp_path: Path) -> None:
    from rig_workbench.bench_tasks import load_tasks

    _write_task(tmp_path)

    tasks = load_tasks(tmp_path)

    task = tasks["sample-task"]
    assert task.id == "sample-task"
    assert task.risk_domains == ("test",)
    assert task.test_command == ("python", "-m", "pytest", "-q")
    assert task.expected_files == ("service.py", "test_service.py")
    with pytest.raises(FrozenInstanceError):
        task.goal = "changed"


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("schema", "schema_version"),
        ("public-command", "test_command"),
        ("hidden-command", "hidden_command"),
        ("traversal", "expected_files"),
        ("hidden-traversal", "hidden_command"),
        ("id-traversal", "id"),
        ("missing", "missing expected file"),
    ],
)
def test_load_tasks_rejects_invalid_schema(
    tmp_path: Path,
    case: str,
    message: str,
) -> None:
    from rig_workbench.bench_tasks import load_tasks

    task_root = _write_task(tmp_path)
    if case == "schema":
        _update_metadata(task_root, schema_version=2)
    elif case == "public-command":
        _update_metadata(task_root, test_command=[])
    elif case == "hidden-command":
        _update_metadata(task_root, hidden_command=[])
    elif case == "traversal":
        _update_metadata(task_root, expected_files=["../service.py"])
    elif case == "hidden-traversal":
        _update_metadata(task_root, hidden_command=["python", "../hidden_check.py"])
    elif case == "id-traversal":
        _update_metadata(task_root, id="../escape")
    elif case == "missing":
        (task_root / "repo" / "service.py").unlink()

    with pytest.raises(ValueError, match=message):
        load_tasks(tmp_path)


def test_load_tasks_rejects_duplicate_ids(tmp_path: Path) -> None:
    from rig_workbench.bench_tasks import load_tasks

    _write_task(tmp_path, directory="first", task_id="duplicate")
    _write_task(tmp_path, directory="second", task_id="duplicate")

    with pytest.raises(ValueError, match="duplicate task id"):
        load_tasks(tmp_path)


def test_load_tasks_rejects_hidden_check_inside_repo(tmp_path: Path) -> None:
    from rig_workbench.bench_tasks import load_tasks

    task_root = _write_task(tmp_path)
    (task_root / "repo" / "hidden_check.py").write_text("raise SystemExit(1)\n", encoding="utf-8")

    with pytest.raises(ValueError, match="hidden check"):
        load_tasks(tmp_path)


def test_materialize_copies_only_repo_and_commits_starting_state(tmp_path: Path) -> None:
    from rig_workbench.bench_tasks import load_tasks, materialize

    _write_task(tmp_path)
    task = load_tasks(tmp_path)["sample-task"]

    workspace = materialize(task)
    try:
        assert (workspace / "service.py").is_file()
        assert (workspace / "test_service.py").is_file()
        assert (workspace / ".git").is_dir()
        assert not (workspace / "task.json").exists()
        assert not (workspace / "hidden_check.py").exists()
        assert not (workspace / "canonical").exists()
        assert not (workspace / "narrow").exists()
        log = subprocess.run(
            ["git", "log", "--format=%s"],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )
        assert log.stdout.strip() == "benchmark starting state"
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )
        assert status.stdout == ""
    finally:
        _remove_tree(workspace)


@pytest.mark.parametrize(
    ("variant", "public_passed", "hidden_passed"),
    [
        ("original", True, False),
        ("narrow", True, False),
        ("canonical", True, True),
    ],
)
def test_run_variant_contract_enforces_public_and_hidden_outcomes(
    tmp_path: Path,
    variant: str,
    public_passed: bool,
    hidden_passed: bool,
) -> None:
    from rig_workbench.bench_tasks import load_tasks, run_variant_contract

    _write_task(tmp_path)
    task = load_tasks(tmp_path)["sample-task"]

    result = run_variant_contract(task, variant)

    assert result.variant == variant
    assert result.public_passed is public_passed, result.public_output
    assert result.hidden_passed is hidden_passed, result.hidden_output


def test_default_corpus_contains_five_python_repository_tasks() -> None:
    from rig_workbench.bench_tasks import load_tasks

    tasks = load_tasks()

    assert {task_id for task_id in tasks if task_id.startswith("py-")} == set(PYTHON_TASK_IDS)
    assert all(tasks[task_id].language == "python" for task_id in PYTHON_TASK_IDS)


@pytest.mark.parametrize("task_id", PYTHON_TASK_IDS)
def test_python_task_variant_contracts(task_id: str) -> None:
    from rig_workbench.bench_tasks import load_tasks, run_variant_contract

    task = load_tasks()[task_id]

    original = run_variant_contract(task, "original")
    narrow = run_variant_contract(task, "narrow")
    canonical = run_variant_contract(task, "canonical")

    assert original.public_passed, original.public_output
    assert not original.hidden_passed, original.hidden_output
    assert narrow.public_passed, narrow.public_output
    assert not narrow.hidden_passed, narrow.hidden_output
    assert canonical.public_passed, canonical.public_output
    assert canonical.hidden_passed, canonical.hidden_output


def test_default_corpus_contains_five_typescript_repository_tasks() -> None:
    from rig_workbench.bench_tasks import load_tasks

    tasks = load_tasks()

    assert {task_id for task_id in tasks if task_id.startswith("ts-")} == set(TYPESCRIPT_TASK_IDS)
    assert set(tasks) == set(PYTHON_TASK_IDS + TYPESCRIPT_TASK_IDS)
    assert all(tasks[task_id].language == "typescript" for task_id in TYPESCRIPT_TASK_IDS)


@pytest.mark.parametrize("task_id", TYPESCRIPT_TASK_IDS)
def test_typescript_task_variant_contracts(task_id: str) -> None:
    from rig_workbench.bench_tasks import load_tasks, run_variant_contract

    task = load_tasks()[task_id]

    original = run_variant_contract(task, "original")
    narrow = run_variant_contract(task, "narrow")
    canonical = run_variant_contract(task, "canonical")

    assert original.public_passed, original.public_output
    assert not original.hidden_passed, original.hidden_output
    assert narrow.public_passed, narrow.public_output
    assert not narrow.hidden_passed, narrow.hidden_output
    assert canonical.public_passed, canonical.public_output
    assert canonical.hidden_passed, canonical.hidden_output
