from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
import yaml


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
REPO_ROOT = Path(__file__).parents[1]


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


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "/etc/passwd",
        "C:\\Windows\\win.ini",
        "C:drive-relative.py",
        "\\\\server\\share\\hidden_check.py",
        "//server/share/hidden_check.py",
        "..\\outside.py",
        "nested/../../outside.py",
    ],
)
@pytest.mark.parametrize("field", ["expected_files", "test_command", "hidden_command"])
def test_load_tasks_rejects_nonportable_or_escaping_schema_paths(
    tmp_path: Path,
    field: str,
    unsafe_path: str,
) -> None:
    from rig_workbench.bench_tasks import load_tasks

    task_root = _write_task(tmp_path)
    value = [unsafe_path] if field == "expected_files" else ["python", unsafe_path]
    _update_metadata(task_root, **{field: value})

    with pytest.raises(ValueError, match=rf"{field} contains unsafe path"):
        load_tasks(tmp_path)


@pytest.mark.parametrize("field", ["test_command", "hidden_command"])
@pytest.mark.parametrize(
    "unsafe_executable",
    [
        "/bin/sh",
        "C:\\tools\\python.exe",
        "\\\\server\\share\\runner.exe",
        "..\\runner.exe",
    ],
)
def test_load_tasks_rejects_nonportable_command_executables(
    tmp_path: Path,
    field: str,
    unsafe_executable: str,
) -> None:
    from rig_workbench.bench_tasks import load_tasks

    task_root = _write_task(tmp_path)
    _update_metadata(task_root, **{field: [unsafe_executable]})

    with pytest.raises(ValueError, match=rf"{field} contains unsafe path"):
        load_tasks(tmp_path)


def test_hidden_artifact_rejection_does_not_depend_on_command_form(tmp_path: Path) -> None:
    from rig_workbench.bench_tasks import load_tasks

    task_root = _write_task(tmp_path)
    _update_metadata(task_root, hidden_command=["python", "-m", "hidden_check"])
    (task_root / "repo" / "hidden_check.py").write_text("raise SystemExit(1)\n", encoding="utf-8")

    with pytest.raises(ValueError, match="hidden"):
        load_tasks(tmp_path)


@pytest.mark.parametrize(
    "artifact",
    [
        "hidden-check.mjs",
        "hidden_spec.ts",
        ".hidden_checks",
        "hidden_tests.py",
    ],
)
def test_load_tasks_rejects_reserved_hidden_artifact_patterns(
    tmp_path: Path,
    artifact: str,
) -> None:
    from rig_workbench.bench_tasks import load_tasks

    task_root = _write_task(tmp_path)
    artifact_path = task_root / "repo" / "nested" / artifact
    artifact_path.parent.mkdir()
    artifact_path.write_text("hidden material\n", encoding="utf-8")

    with pytest.raises(ValueError, match="hidden"):
        load_tasks(tmp_path)


@pytest.mark.parametrize("source_name", ["repo", "narrow", "canonical"])
@pytest.mark.parametrize("git_kind", ["directory", "file"])
def test_load_tasks_rejects_git_metadata_in_copyable_sources(
    tmp_path: Path,
    source_name: str,
    git_kind: str,
) -> None:
    from rig_workbench.bench_tasks import load_tasks

    task_root = _write_task(tmp_path)
    nested = task_root / source_name / "nested"
    nested.mkdir()
    git_path = nested / ".git"
    if git_kind == "directory":
        hooks = git_path / "hooks"
        hooks.mkdir(parents=True)
        (hooks / "pre-commit").write_text("hostile hook\n", encoding="utf-8")
    else:
        git_path.write_text("gitdir: ../../attacker\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Git metadata"):
        load_tasks(tmp_path)


def test_materialize_revalidates_repo_before_copy(tmp_path: Path) -> None:
    from rig_workbench.bench_tasks import load_tasks, materialize

    task_root = _write_task(tmp_path)
    task = load_tasks(tmp_path)["sample-task"]
    hooks = task_root / "repo" / ".git" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "post-checkout").write_text("hostile hook\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Git metadata"):
        materialize(task)


def test_variant_overlay_is_revalidated_before_copy(tmp_path: Path) -> None:
    from rig_workbench.bench_tasks import load_tasks, run_variant_contract

    task_root = _write_task(tmp_path)
    task = load_tasks(tmp_path)["sample-task"]
    (task_root / "canonical" / ".git").write_text(
        "gitdir: ../../attacker\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Git metadata"):
        run_variant_contract(task, "canonical")


@pytest.mark.parametrize("source_name", ["repo", "narrow", "canonical"])
@pytest.mark.parametrize("target_kind", ["file", "directory"])
def test_load_tasks_rejects_links_in_copyable_sources(
    tmp_path: Path,
    target_kind: str,
    source_name: str,
) -> None:
    from rig_workbench.bench_tasks import load_tasks

    task_root = _write_task(tmp_path)
    outside = tmp_path / "outside"
    if target_kind == "directory":
        outside.mkdir()
        (outside / "secret.txt").write_text("secret\n", encoding="utf-8")
    else:
        outside.write_text("secret\n", encoding="utf-8")
    link = task_root / source_name / "linked"
    try:
        link.symlink_to(outside, target_is_directory=target_kind == "directory")
    except OSError as error:
        pytest.skip(f"links are unavailable on this host: {error}")

    with pytest.raises(ValueError, match="link"):
        load_tasks(tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
def test_load_tasks_rejects_windows_junctions(tmp_path: Path) -> None:
    from rig_workbench.bench_tasks import load_tasks

    task_root = _write_task(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    junction = task_root / "repo" / "junction"
    created = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if created.returncode:
        pytest.skip(f"junctions are unavailable on this host: {created.stderr}")
    try:
        with pytest.raises(ValueError, match="link"):
            load_tasks(tmp_path)
    finally:
        junction.rmdir()


def test_copy_rejects_entry_swapped_to_link_after_initial_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rig_workbench.bench_tasks as bench_tasks

    source = tmp_path / "source"
    swapped = source / "swapped"
    swapped.mkdir(parents=True)
    (swapped / "benign.txt").write_text("benign\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret\n", encoding="utf-8")
    destination = tmp_path / "destination"
    injected = False

    def swap_entry(path: Path) -> None:
        nonlocal injected
        if injected or path != swapped:
            return
        injected = True
        shutil.rmtree(path)
        if os.name == "nt":
            created = subprocess.run(
                ["cmd.exe", "/c", "mklink", "/J", str(path), str(outside)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if created.returncode:
                pytest.skip(f"junctions are unavailable on this host: {created.stderr}")
        else:
            path.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(bench_tasks, "_before_copy_entry", swap_entry, raising=False)
    try:
        with pytest.raises(ValueError, match="link"):
            bench_tasks._copy_source(source, destination)
        assert injected
        assert not (destination / "swapped" / "secret.txt").exists()
    finally:
        if os.name == "nt" and injected and swapped.exists():
            swapped.rmdir()


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


def test_run_variant_contract_decodes_utf8_with_replacement(tmp_path: Path) -> None:
    from rig_workbench.bench_tasks import load_tasks, run_variant_contract

    task_root = _write_task(tmp_path)
    (task_root / "repo" / "emit.py").write_text(
        "import sys\nsys.stdout.buffer.write('public: \u2713\\n'.encode('utf-8') + b'\\x81')\n",
        encoding="utf-8",
    )
    (task_root / "hidden_check.py").write_text(
        "import sys\nsys.stdout.buffer.write('hidden: \u2713\\n'.encode('utf-8') + b'\\x81')\n",
        encoding="utf-8",
    )
    _update_metadata(task_root, test_command=["python", "emit.py"])
    task = load_tasks(tmp_path)["sample-task"]

    result = run_variant_contract(task, "original")

    assert result.public_passed
    assert result.hidden_passed
    assert result.public_output == "public: \u2713\n\ufffd"
    assert result.hidden_output == "hidden: \u2713\n\ufffd"


def test_check_result_output_properties_tolerate_missing_streams() -> None:
    from rig_workbench.bench_tasks import CheckResult

    result = CheckResult(
        variant="original",
        public_returncode=0,
        hidden_returncode=1,
        public_stdout=None,
        public_stderr="public error",
        hidden_stdout="hidden output",
        hidden_stderr=None,
    )

    assert result.public_output == "public error"
    assert result.hidden_output == "hidden output"


def test_node_runtime_floor_rejects_22_17_and_accepts_22_18(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rig_workbench.bench_tasks as bench_tasks

    monkeypatch.setattr(
        bench_tasks,
        "_installed_node_version",
        lambda: "v22.17.9",
        raising=False,
    )
    with pytest.raises(RuntimeError, match=r"Node >=22\.18\.0"):
        bench_tasks._require_supported_node()

    for accepted_version in ("v22.18.0", "v22.19.1", "v23.0.0"):
        monkeypatch.setattr(
            bench_tasks,
            "_installed_node_version",
            lambda version=accepted_version: version,
        )
        bench_tasks._require_supported_node()


def test_ci_exercises_declared_python_and_node_runtime_floors() -> None:
    from rig_workbench.bench_tasks import MIN_NODE_VERSION

    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.10"' in pyproject
    assert MIN_NODE_VERSION == (22, 18, 0)

    workflow_path = REPO_ROOT / ".github" / "workflows" / "validate.yml"
    workflow_text = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    validate_job = workflow["jobs"]["validate"]
    python_versions = {
        str(version) for version in validate_job["strategy"]["matrix"]["python-version"]
    }
    assert "3.10" in python_versions

    setup_node = next(
        step
        for step in validate_job["steps"]
        if step.get("uses", "").startswith("actions/setup-node@")
    )
    ci_node_version = tuple(int(part) for part in setup_node["with"]["node-version"].split("."))
    assert ci_node_version >= MIN_NODE_VERSION

    paid_markers = (
        "--provider claude",
        "--provider codex",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
    )
    assert not any(marker in workflow_text for marker in paid_markers)


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
