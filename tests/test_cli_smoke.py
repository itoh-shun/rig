"""Subprocess smoke tests for the scripts/orchestrate.py shim (CLI level only).

Runs from a tmp cwd with RIG_HOME pinned to the repo, so shipped recipes resolve
while nothing is read from or written to the real repo's .rig/ state.
"""

import base64
import csv
import datetime as dt
import hashlib
import importlib.metadata
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import venv
import zipfile

import pytest
from packaging.requirements import Requirement

from rig_workbench import cli
from rig_workbench import __version__

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ORCHESTRATE = REPO_ROOT / "scripts" / "orchestrate.py"
BENCH_RESOURCE_SUFFIXES = {".json", ".py", ".ts", ".txt"}
ISOLATED_ENV_KEYS = {
    "COMSPEC",
    "HOME",
    "LOCALAPPDATA",
    "APPDATA",
    "LANG",
    "LC_ALL",
    "NUMBER_OF_PROCESSORS",
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "WINDIR",
}


def run_cli(args, tmp_path):
    env = dict(
        os.environ,
        RIG_HOME=str(REPO_ROOT),
        PYTHONIOENCODING="utf-8",
        PYTHONUTF8="1",
    )
    return subprocess.run(
        [sys.executable, str(ORCHESTRATE), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=tmp_path,
        env=env,
        timeout=60,
    )


def run_rig_wb(args, tmp_path):
    env = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join(filter(None, [str(REPO_ROOT), os.environ.get("PYTHONPATH")])),
    )
    return subprocess.run(
        [sys.executable, "-m", "rig_workbench.cli", *args],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        timeout=60,
    )


def _venv_python(root):
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _venv_rig_wb(root):
    return root / ("Scripts/rig-wb.exe" if os.name == "nt" else "bin/rig-wb")


def _provision_distributions_offline(root, requirements):
    """Copy the wheel-declared dependency closure from the host's offline environment."""
    destination_site = (
        root / "Lib" / "site-packages"
        if os.name == "nt"
        else root / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    )
    pending = [Requirement(requirement) for requirement in requirements]
    copied = set()
    while pending:
        requirement = pending.pop()
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        name = requirement.name
        normalized = name.casefold().replace("-", "_")
        if normalized in copied:
            continue
        copied.add(normalized)
        distribution = importlib.metadata.distribution(name)
        pending.extend(Requirement(item) for item in distribution.requires or ())
        for relative in distribution.files or ():
            relative_path = pathlib.Path(str(relative))
            if ".." in relative_path.parts:
                continue
            source = pathlib.Path(distribution.locate_file(relative))
            if not source.is_file():
                continue
            destination = destination_site / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


def _isolated_env():
    env = {key: value for key, value in os.environ.items() if key in ISOLATED_ENV_KEYS}
    env.update(PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INDEX="1")
    return env


def _build_wheel_offline(root):
    """Stage configured packages with setuptools, then wrap them as a wheel."""
    build_root = root / "build"
    package_root = build_root / "packages"
    egg_root = build_root / "egg"
    egg_root.mkdir(parents=True)
    build = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from setuptools import setup; "
                "setup(script_args=['egg_info', '--egg-base', "
                f"{str(egg_root)!r}, 'build_py', '--build-lib', {str(package_root)!r}])"
            ),
        ],
        cwd=REPO_ROOT,
        env=_isolated_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert build.returncode == 0, build.stdout + build.stderr

    wheel = root / f"rig_workbench-{__version__}-py3-none-any.whl"
    dist_info = f"rig_workbench-{__version__}.dist-info"
    egg_info = next(egg_root.glob("*.egg-info"))
    package_metadata = (egg_info / "PKG-INFO").read_text(encoding="utf-8")
    runtime_requirements = []
    for line in (egg_info / "requires.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("["):
            break
        if line:
            runtime_requirements.append(line)
    metadata_headers, separator, metadata_body = package_metadata.partition("\n\n")
    dependency_headers = "".join(
        f"\nRequires-Dist: {requirement}" for requirement in runtime_requirements
    )
    package_metadata = (
        metadata_headers + dependency_headers + separator + metadata_body
    ).encode("utf-8")
    generated = {
        f"{dist_info}/METADATA": package_metadata,
        f"{dist_info}/WHEEL": (
            "Wheel-Version: 1.0\n"
            "Generator: rig-workbench offline packaging smoke\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n"
        ).encode(),
        f"{dist_info}/entry_points.txt": (
            "[console_scripts]\n"
            "rig-wb = rig_workbench.cli:main\n"
        ).encode(),
    }
    records = []
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(path for path in package_root.rglob("*") if path.is_file()):
            name = source.relative_to(package_root).as_posix()
            data = source.read_bytes()
            archive.writestr(name, data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest())
            records.append((name, f"sha256={digest.rstrip(b'=').decode()}", str(len(data))))
        for name, data in generated.items():
            archive.writestr(name, data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest())
            records.append((name, f"sha256={digest.rstrip(b'=').decode()}", str(len(data))))

        record_name = f"{dist_info}/RECORD"
        record_stream = io.StringIO()
        writer = csv.writer(record_stream, lineterminator="\n")
        writer.writerows([*records, (record_name, "", "")])
        archive.writestr(record_name, record_stream.getvalue().encode())
    return wheel


def _baseline_benchmark_document():
    generated = dt.datetime.now(dt.timezone.utc).isoformat()

    def arm(mode):
        outcome = "silent_defect" if mode == "bare" else "clean_pass"
        return {
            "outcome": outcome, "elapsed_s": 1.0, "invocation_count": 1,
            "completed": True, "public_test": {"passed": True},
            "hidden_check": {"passed": mode == "rig"},
        }

    runs = []
    for run in range(1, 4):
        runs.append({
            "pair_id": f"wheel-task-{run}", "task_id": "wheel-task", "run": run,
            "provider": "claude", "bare_model": "haiku", "rig_model": "sonnet",
            "arms": {"bare": arm("bare"), "rig": arm("rig")}, "elapsed_s": 2.0,
        })
    return {
        "schema_version": 2, "generated": generated, "provider": "claude",
        "provider_version": "fixture", "model": "sonnet", "bare_model": "haiku",
        "rig_model": "sonnet", "score": {"verdict": "pass"},
        "tasks": [{"task_id": "wheel-task", "runs": runs}],
    }


def test_installed_wheel_runs_stdlib_only_baseline_full_flow_without_dependencies(tmp_path):
    """Capture, show, and compare must work in a clean install without PyYAML."""
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    wheel = _build_wheel_offline(wheel_dir)
    environment = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = _venv_python(environment)
    install = subprocess.run(
        [str(python), "-m", "pip", "install", "--no-deps", str(wheel)],
        capture_output=True, text=True, env=_isolated_env(), timeout=120,
    )
    assert install.returncode == 0, install.stdout + install.stderr

    source = tmp_path / "bench.json"
    baseline = tmp_path / "baseline.json"
    source.write_text(json.dumps(_baseline_benchmark_document()), encoding="utf-8")
    commands = (
        ["capture", "--input", str(source), "--output", str(baseline)],
        ["show", str(baseline)],
        ["compare", "--baseline", str(baseline), "--current", str(source), "--json"],
    )
    results = [
        subprocess.run(
            [str(python), "-m", "rig_workbench.cli", "baseline", *command],
            cwd=tmp_path, capture_output=True, text=True, env=_isolated_env(), timeout=60,
        )
        for command in commands
    ]

    assert [result.returncode for result in results] == [0, 0, 0], [
        result.stdout + result.stderr for result in results
    ]
    assert baseline.exists()
    assert "claude / sonnet / rig / wheel-task" in results[1].stdout
    assert json.loads(results[2].stdout)["status"] == "pass"


def test_installed_wheel_runs_stdlib_only_eval_capture_validate_list(tmp_path):
    wheel_dir = tmp_path / "wheel-eval"
    wheel_dir.mkdir()
    wheel = _build_wheel_offline(wheel_dir)
    environment = tmp_path / "venv-eval"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = _venv_python(environment)
    install = subprocess.run(
        [str(python), "-m", "pip", "install", "--no-deps", str(wheel)],
        capture_output=True, text=True, env=_isolated_env(), timeout=120,
    )
    assert install.returncode == 0, install.stdout + install.stderr

    repo = tmp_path / "eval-repo"
    outside = tmp_path / "outside"
    outside.mkdir()
    task_id = "rig-20260805-wheel-eval"
    run = repo / ".rig" / "runs" / task_id
    run.mkdir(parents=True)
    (run / "task.json").write_text(json.dumps({
        "task_id": task_id, "input": "Wheel evaluation capture", "task_type": "bugfix",
        "base_commit": "f" * 40,
    }), encoding="utf-8")

    capture = subprocess.run(
        [str(python), "-m", "rig_workbench.cli", "eval", "capture", task_id,
         "--repo", str(repo)], cwd=outside, capture_output=True, text=True,
        env=_isolated_env(), timeout=60,
    )
    case_path = repo / ".rig" / "evals" / "drafts" / task_id / "case.json"
    validate = subprocess.run(
        [str(python), "-m", "rig_workbench.cli", "eval", "validate", str(case_path)],
        cwd=outside, capture_output=True, text=True, env=_isolated_env(), timeout=60,
    )
    listing = subprocess.run(
        [str(python), "-m", "rig_workbench.cli", "eval", "list", "--repo", str(repo)],
        cwd=outside, capture_output=True, text=True, env=_isolated_env(), timeout=60,
    )

    assert [capture.returncode, validate.returncode, listing.returncode] == [0, 0, 0], [
        capture.stdout + capture.stderr, validate.stdout + validate.stderr,
        listing.stdout + listing.stderr,
    ]
    assert task_id in listing.stdout and case_path.is_file()


def test_installed_wheel_runs_stdlib_only_pack_cli_outside_source_tree(tmp_path):
    wheel_dir = tmp_path / "wheel-pack"
    wheel_dir.mkdir()
    wheel = _build_wheel_offline(wheel_dir)
    with zipfile.ZipFile(wheel) as archive:
        assert "packs/domain/sns-x/pack.yaml" in archive.namelist()
        assert "packs/domain/sns-x/recipes/sns-x-post.md" in archive.namelist()
        assert "packs/domain/sales/pack.yaml" in archive.namelist()
        assert "packs/domain/sales/recipes/deal-review.md" in archive.namelist()
        assert "packs/domain/video-storytelling/pack.yaml" in archive.namelist()
        assert "packs/domain/video-storytelling/recipes/release-movie.md" in archive.namelist()
        assert "packs/domain/video-storytelling/facets/output-contracts/scenario-verdict.md" in archive.namelist()
        assert "packs/domain/video-storytelling/resources/launch-film.html" in archive.namelist()
        assert "packs/domain/decision-humor/pack.yaml" in archive.namelist()
        assert "packs/domain/decision-humor/recipes/magi.md" in archive.namelist()
        assert "packs/domain/decision-humor/evals/cases/coin-high-stakes-refusal/case.json" in archive.namelist()
    environment = tmp_path / "venv-pack"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = _venv_python(environment)
    install = subprocess.run(
        [str(python), "-m", "pip", "install", "--no-deps", str(wheel)],
        capture_output=True, text=True, env=_isolated_env(), timeout=120,
    )
    assert install.returncode == 0, install.stdout + install.stderr
    outside = tmp_path / "pack-outside"
    outside.mkdir()
    source_root = outside / "sources"
    initialized = subprocess.run(
        [str(python), "-m", "rig_workbench.cli", "pack", "init", "wheel-pack",
         "--root", str(source_root)], cwd=outside, capture_output=True, text=True,
        env=_isolated_env(), timeout=60,
    )
    validated = subprocess.run(
        [str(python), "-m", "rig_workbench.cli", "pack", "validate",
         str(source_root / "wheel-pack")], cwd=outside, capture_output=True, text=True,
        env=_isolated_env(), timeout=60,
    )
    installed = subprocess.run(
        [str(python), "-m", "rig_workbench.cli", "pack", "install",
         str(source_root / "wheel-pack"), "--scope", "project"], cwd=outside,
        capture_output=True, text=True, env=_isolated_env(), timeout=60,
    )
    doctor = subprocess.run(
        [str(python), "-m", "rig_workbench.cli", "pack", "doctor", "--json"], cwd=outside,
        capture_output=True, text=True, env=_isolated_env(), timeout=60,
    )
    tested = subprocess.run(
        [str(python), "-m", "rig_workbench.cli", "pack", "test", "wheel-pack", "--json"],
        cwd=outside, capture_output=True, text=True, env=_isolated_env(), timeout=60,
    )
    removed = subprocess.run(
        [str(python), "-m", "rig_workbench.cli", "pack", "remove", "wheel-pack",
         "--scope", "project", "--yes"], cwd=outside, capture_output=True, text=True,
        env=_isolated_env(), timeout=60,
    )
    builtin_installed = subprocess.run(
        [str(python), "-m", "rig_workbench.cli", "pack", "install", "domain:sns-x",
         "--scope", "project", "--allow-unverified"], cwd=outside,
        capture_output=True, text=True, env=_isolated_env(), timeout=60,
    )
    builtin_resolved = subprocess.run(
        [str(python), "-c",
         "from rig_workbench.packs.resolver import resolve_asset; "
         "item=resolve_asset('recipe','sns-x-post'); "
         "print(item.pack_id if item else 'missing')"],
        cwd=outside, capture_output=True, text=True, env=_isolated_env(), timeout=60,
    )
    builtin_tested = subprocess.run(
        [str(python), "-m", "rig_workbench.cli", "pack", "test", "sns-x", "--json"],
        cwd=outside, capture_output=True, text=True, env=_isolated_env(), timeout=60,
    )
    builtin_removed = subprocess.run(
        [str(python), "-m", "rig_workbench.cli", "pack", "remove", "sns-x",
         "--scope", "project", "--yes"], cwd=outside, capture_output=True, text=True,
        env=_isolated_env(), timeout=60,
    )
    sales_installed = subprocess.run(
        [str(python), "-m", "rig_workbench.cli", "pack", "install", "domain:sales",
         "--scope", "project", "--allow-unverified"], cwd=outside,
        capture_output=True, text=True, env=_isolated_env(), timeout=60,
    )
    sales_resolved = subprocess.run(
        [str(python), "-c",
         "from rig_workbench.packs.resolver import resolve_asset; "
         "r=resolve_asset('recipe','deal-review'); c=resolve_asset('command','sales'); "
         "print(f'{r.pack_id if r else \"missing\"}:{c.pack_id if c else \"missing\"}')"],
        cwd=outside, capture_output=True, text=True, env=_isolated_env(), timeout=60,
    )
    sales_tested = subprocess.run(
        [str(python), "-m", "rig_workbench.cli", "pack", "test", "sales", "--json"],
        cwd=outside, capture_output=True, text=True, env=_isolated_env(), timeout=60,
    )
    sales_removed = subprocess.run(
        [str(python), "-m", "rig_workbench.cli", "pack", "remove", "sales",
         "--scope", "project", "--yes"], cwd=outside, capture_output=True, text=True,
        env=_isolated_env(), timeout=60,
    )
    video_installed = subprocess.run(
        [str(python), "-m", "rig_workbench.cli", "pack", "install",
         "domain:video-storytelling", "--scope", "project", "--allow-unverified"],
        cwd=outside, capture_output=True, text=True, env=_isolated_env(), timeout=60,
    )
    video_resolved = subprocess.run(
        [str(python), "-c",
         "from rig_workbench.packs.resolver import resolve_asset; "
         "names=[('recipe','movie'),('recipe','release-movie'),('recipe','scenario'),"
         "('command','movie'),('persona','video-content-safety-reviewer'),"
         "('output-contract','scenario-verdict')]; "
         "print(':'.join(resolve_asset(k,n).pack_id if resolve_asset(k,n) else 'missing' "
         "for k,n in names))"],
        cwd=outside, capture_output=True, text=True, env=_isolated_env(), timeout=60,
    )
    video_tested = subprocess.run(
        [str(python), "-m", "rig_workbench.cli", "pack", "test",
         "video-storytelling", "--json"], cwd=outside, capture_output=True, text=True,
        env=_isolated_env(), timeout=60,
    )
    video_removed = subprocess.run(
        [str(python), "-m", "rig_workbench.cli", "pack", "remove",
         "video-storytelling", "--scope", "project", "--yes"], cwd=outside,
        capture_output=True, text=True, env=_isolated_env(), timeout=60,
    )
    assert [initialized.returncode, validated.returncode, installed.returncode,
            doctor.returncode, tested.returncode, removed.returncode,
            builtin_installed.returncode, builtin_resolved.returncode,
            builtin_tested.returncode, builtin_removed.returncode,
            sales_installed.returncode, sales_resolved.returncode,
            sales_tested.returncode, sales_removed.returncode,
            video_installed.returncode, video_resolved.returncode,
            video_tested.returncode, video_removed.returncode] == [0] * 18, (
        initialized.stderr + validated.stderr + installed.stderr + doctor.stderr
        + tested.stderr + removed.stderr + builtin_installed.stderr
        + builtin_resolved.stderr + builtin_tested.stderr + builtin_removed.stderr
        + sales_installed.stderr + sales_resolved.stderr + sales_tested.stderr
        + sales_removed.stderr + video_installed.stderr + video_resolved.stderr
        + video_tested.stderr + video_removed.stderr
    )
    assert json.loads(doctor.stdout)["status"] == "ok"
    assert json.loads(tested.stdout)["status"] == "structural_only"
    assert builtin_resolved.stdout.strip() == "sns-x"
    assert json.loads(builtin_tested.stdout)["status"] == "structural_only"
    assert sales_resolved.stdout.strip() == "sales:sales"
    assert json.loads(sales_tested.stdout)["status"] == "structural_only"
    assert video_resolved.stdout.strip() == ":".join(["video-storytelling"] * 6)
    assert json.loads(video_tested.stdout)["status"] == "structural_only"
    assert not (outside / ".rig/packs/wheel-pack").exists()
    assert not (outside / ".rig/packs/sns-x").exists()
    assert not (outside / ".rig/packs/sales").exists()
    assert not (outside / ".rig/packs/video-storytelling").exists()


def test_installed_wheel_runs_stdlib_only_eval_mock_run_compare(tmp_path):
    wheel_dir = tmp_path / "wheel-eval-run"
    wheel_dir.mkdir()
    wheel = _build_wheel_offline(wheel_dir)
    environment = tmp_path / "venv-eval-run"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = _venv_python(environment)
    install = subprocess.run(
        [str(python), "-m", "pip", "install", "--no-deps", str(wheel)],
        capture_output=True, text=True, env=_isolated_env(), timeout=120,
    )
    assert install.returncode == 0, install.stdout + install.stderr

    repo = tmp_path / "eval-run-repo"
    outside = tmp_path / "eval-run-outside"
    outside.mkdir()
    case_id = "wheel-eval-run"
    timestamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    case = {
        "case_schema_version": 1, "id": case_id, "version": 1,
        "title": "Wheel eval run", "status": "draft", "incident": True,
        "provenance": {"source_task_id": "rig-wheel-eval-run", "source_commit": "a" * 40,
                       "source_hashes": {"task.json": "b" * 64}, "captured_at": timestamp},
        "surfaces": ["cli"], "suite": "wheel", "tags": ["smoke"],
        "provider_policy": {"mode": "allowlist", "allowed": ["mock"]},
        "repeat": 3, "red_thresholds": {"max_success_rate": 1 / 3},
        "green_thresholds": {"min_success_rate": 1.0},
        "deterministic_checks": ["contains:scenario"],
        "semantic_rubric": [
            {"id": "correct", "description": "Output is correct", "weight": 1.0}
        ],
        "target_inputs": {"scenario": "target"},
        "clean_controls": {"scenario": "clean"},
        "missing_requirements": [],
        "failure_summary": "Captured incident", "created_at": timestamp, "updated_at": timestamp,
    }
    draft = repo / ".rig" / "evals" / "drafts" / case_id / "case.json"
    draft.parent.mkdir(parents=True)
    draft.write_text(json.dumps(case, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":")) + "\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "eval@test.invalid"], cwd=repo,
                   check=True)
    subprocess.run(["git", "config", "user.name", "eval-test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "eval fixture"], cwd=repo, check=True)
    eval_env = _isolated_env()
    eval_env["RIG_EVAL_ATTESTATION_KEY"] = "wheel-test-attestation-key-at-least-32-bytes"
    common = [case_id, "--provider", "mock", "--model", "fixture", "--repeat", "3",
              "--repo", str(repo)]
    judge_command = (
        'python3 -c "import json; print(json.dumps({\'status\':\'measured\','
        '\'criteria\':[{\'id\':\'correct\',\'status\':\'pass\',\'score\':1.0}]}))"'
    )
    judge_args = ["--judge-provider", "command", "--judge-model", "fixture",
                  "--judge-command", judge_command]
    baseline = subprocess.run(
        [str(python), "-m", "rig_workbench.cli", "eval", "run", *common,
         "--phase", "baseline", *judge_args],
        cwd=outside, capture_output=True, text=True, env=eval_env, timeout=60,
    )
    current = subprocess.run(
        [str(python), "-m", "rig_workbench.cli", "eval", "run", *common,
         "--phase", "current", *judge_args],
        cwd=outside, capture_output=True, text=True, env=eval_env, timeout=60,
    )
    assert baseline.returncode == current.returncode == 0, baseline.stderr + current.stderr
    compared = subprocess.run(
        [str(python), "-m", "rig_workbench.cli", "eval", "compare",
         "--baseline", baseline.stdout.strip().splitlines()[-1],
         "--current", current.stdout.strip().splitlines()[-1], "--repo", str(repo)],
        cwd=outside, capture_output=True, text=True, env=eval_env, timeout=60,
    )

    assert compared.returncode == 0, compared.stdout + compared.stderr
    assert json.loads(compared.stdout)["status"] == "pass"
    promoted = subprocess.run(
        [str(python), "-m", "rig_workbench.cli", "eval", "promote", case_id,
         "--baseline", baseline.stdout.strip().splitlines()[-1],
         "--current", current.stdout.strip().splitlines()[-1], "--repo", str(repo)],
        cwd=outside, capture_output=True, text=True, env=eval_env, timeout=60,
    )
    assert promoted.returncode == 0, promoted.stdout + promoted.stderr
    assert (repo / "evals" / "cases" / case_id / "case.json").is_file()
    assert draft.is_file()
    (repo / "ordinary.py").write_text("print('non-prompt')\n", encoding="utf-8")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    affected = subprocess.run(
        [str(python), "-m", "rig_workbench.cli", "eval", "affected",
         "--base", base, "--head", "working", "--require-cases", "--json",
         "--repo", str(repo)], cwd=outside, capture_output=True, text=True,
        env=eval_env, timeout=60,
    )
    assert affected.returncode == 0, affected.stdout + affected.stderr
    assert json.loads(affected.stdout)["status"] == "noop"
    gated = subprocess.run(
        [str(python), "-m", "rig_workbench.cli", "eval", "gate",
         "--base", base, "--head", "working", "--evidence-dir",
         str(repo / ".rig" / "evals" / "results"), "--repo", str(repo)],
        cwd=outside, capture_output=True, text=True, env=eval_env, timeout=60,
    )
    assert gated.returncode == 0, gated.stdout + gated.stderr
    assert json.loads(gated.stdout)["status"] == "noop"


def test_installed_wheel_runs_plan_and_mock_benchmark_outside_source_tree(tmp_path):
    assert not tmp_path.resolve().is_relative_to(REPO_ROOT.resolve())
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    wheel = _build_wheel_offline(wheel_dir)

    expected_resources = sorted(
        path.relative_to(REPO_ROOT / "benchmarks").as_posix()
        for path in (REPO_ROOT / "benchmarks" / "tasks").rglob("*")
        if path.is_file() and path.suffix in BENCH_RESOURCE_SUFFIXES
    )
    with zipfile.ZipFile(wheel) as archive:
        wheel_resources = sorted(
            name.removeprefix("benchmarks/")
            for name in archive.namelist()
            if name.startswith("benchmarks/tasks/")
            and pathlib.PurePosixPath(name).suffix in BENCH_RESOURCE_SUFFIXES
        )
        metadata_name = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        metadata = archive.read(metadata_name).decode("utf-8")
    assert wheel_resources == expected_resources
    requires_dist = [
        line.removeprefix("Requires-Dist:").strip()
        for line in metadata.splitlines()
        if line.startswith("Requires-Dist:")
    ]
    assert any(requirement.lower().startswith("pytest") for requirement in requires_dist)

    install_root = tmp_path / "installed"
    venv.EnvBuilder(with_pip=True).create(install_root)
    _provision_distributions_offline(install_root, requires_dist)
    python = _venv_python(install_root)
    install = subprocess.run(
        [str(python), "-m", "pip", "install", "--no-deps", str(wheel)],
        env=_isolated_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert install.returncode == 0, install.stdout + install.stderr

    probe = subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            (
                "import json, pathlib, sysconfig, benchmarks, rig_workbench; "
                "from rig_workbench.bench_tasks import load_tasks; "
                "root = pathlib.Path(benchmarks.__file__).parent; "
                "site = pathlib.Path(sysconfig.get_path('purelib')).resolve(); "
                "resources = sorted(p.relative_to(root).as_posix() "
                "for p in (root / 'tasks').rglob('*') if p.is_file() "
                f"and p.suffix in {BENCH_RESOURCE_SUFFIXES!r}); "
                "print(json.dumps({'tasks': sorted(load_tasks()), 'resources': resources, "
                "'site': str(site), 'benchmarks': str(pathlib.Path(benchmarks.__file__).resolve()), "
                "'rig_workbench': str(pathlib.Path(rig_workbench.__file__).resolve())}))"
            ),
        ],
        cwd=tmp_path,
        env=_isolated_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    installed = json.loads(probe.stdout)
    expected_ids = sorted(
        path.parent.name for path in (REPO_ROOT / "benchmarks/tasks").glob("*/task.json")
    )
    site_packages = pathlib.Path(installed["site"])
    assert pathlib.Path(installed["benchmarks"]).is_relative_to(site_packages)
    assert pathlib.Path(installed["rig_workbench"]).is_relative_to(site_packages)
    assert installed["tasks"] == expected_ids
    assert installed["resources"] == expected_resources

    outside = tmp_path / "outside"
    outside.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=outside, check=True)
    rig_wb = _venv_rig_wb(install_root)
    plan_result = subprocess.run(
        [str(rig_wb), "plan", "adaptive-bugfix", "--json"],
        cwd=outside,
        env=_isolated_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert plan_result.returncode == 0, plan_result.stdout + plan_result.stderr
    plan = json.loads(plan_result.stdout)
    assert [step["executor"] for step in plan["steps"]] == [
        "generate",
        "risk-assess",
        "targeted-review",
        "checks-only",
    ]

    bench_output = outside / "bench.json"
    bench_result = subprocess.run(
        [
            str(rig_wb),
            "bench",
            "--provider",
            "mock",
            "--tasks",
            "py-auth-sibling-write",
            "--runs",
            "1",
            "--out",
            str(bench_output),
        ],
        cwd=outside,
        env=_isolated_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert bench_result.returncode == 1, bench_result.stdout + bench_result.stderr
    summary = json.loads(bench_output.read_text(encoding="utf-8"))
    paired_runs = summary["tasks"][0]["runs"]
    assert summary["provider"] == "mock"
    assert len(paired_runs) == 1
    assert set(paired_runs[0]["arms"]) == {"bare", "rig"}
    assert all(
        arm["attempts"][0]["infra_error"] is None
        for arm in paired_runs[0]["arms"].values()
    )


def test_bench_help_documents_evidence_and_exit_contract(tmp_path):
    result = run_rig_wb(["bench", "--help"], tmp_path)

    assert result.returncode == 0
    help_text = result.stdout
    for expected in (
        "--corpus",
        "external corpus",
        "--runs",
        "planned pairs per task",
        "3 valid pairs",
        "10 tasks",
        "schema v2",
        "WIRING ONLY",
        "--allow-paid-provider",
        "0=pass",
        "1=completed non-pass",
        "2=CLI/schema error",
        "--bare-model",
        "--rig-model",
    ):
        assert expected in help_text


def test_paid_benchmark_provider_requires_explicit_opt_in_before_validation(tmp_path):
    result = run_rig_wb(
        ["bench", "--provider", "claude", "--tasks", "not-a-real-task"],
        tmp_path,
    )

    assert result.returncode == 2
    assert "--allow-paid-provider" in result.stderr


def test_bench_help_precedes_paid_provider_validation(tmp_path):
    result = run_rig_wb(["bench", "--provider", "claude", "--help"], tmp_path)

    assert result.returncode == 0
    assert "--allow-paid-provider" in result.stdout


def test_duplicate_provider_options_are_rejected_before_benchmark(monkeypatch):
    from rig_workbench import bench

    called = False

    def unexpected_benchmark(_argv):
        nonlocal called
        called = True

    monkeypatch.setattr(bench, "cmd_bench", unexpected_benchmark)

    with pytest.raises(SystemExit) as error:
        cli._run_bench(["--provider", "mock", "--provider", "claude"])

    assert error.value.code == 2
    assert called is False


def test_benchmark_verdict_maps_to_documented_exit_codes():
    assert cli._benchmark_exit_code({"schema_version": 2, "score": {"verdict": "pass"}}) == 0
    for verdict in ("fail", "invalid", "inconclusive"):
        assert cli._benchmark_exit_code({"schema_version": 2, "score": {"verdict": verdict}}) == 1
    with pytest.raises(ValueError, match="schema v2"):
        cli._benchmark_exit_code({"schema_version": 1})


def test_completed_nonpassing_benchmark_exits_one(tmp_path):
    output = tmp_path / "bench.json"
    result = run_rig_wb(
        [
            "bench",
            "--tasks",
            "py-auth-sibling-write",
            "--provider",
            "mock",
            "--runs",
            "1",
            "--out",
            str(output),
        ],
        tmp_path,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["score"]["verdict"] == "invalid"


def test_bench_accepts_distinct_bare_and_rig_models(tmp_path):
    output = tmp_path / "bench.json"
    result = run_rig_wb(
        [
            "bench",
            "--tasks",
            "py-auth-sibling-write",
            "--provider",
            "mock",
            "--bare-model",
            "weaker-mock",
            "--rig-model",
            "stronger-mock",
            "--runs",
            "1",
            "--out",
            str(output),
        ],
        tmp_path,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    summary = json.loads(output.read_text(encoding="utf-8"))
    assert summary["bare_model"] == "weaker-mock"
    assert summary["rig_model"] == "stronger-mock"
    pair = summary["tasks"][0]["runs"][0]
    assert pair["arms"]["bare"]["attempts"][0]["model"] == "weaker-mock"
    assert pair["arms"]["rig"]["attempts"][0]["model"] == "stronger-mock"


def test_malformed_external_corpus_exits_two(tmp_path):
    task_root = tmp_path / "external-corpus" / "broken-task"
    task_root.mkdir(parents=True)
    (task_root / "task.json").write_text("{}", encoding="utf-8")

    result = run_rig_wb(
        [
            "bench",
            "--corpus",
            str(task_root.parent),
            "--provider",
            "mock",
        ],
        tmp_path,
    )

    assert result.returncode == 2
    assert "schema" in result.stderr.lower()


def test_plan_json_review_only(tmp_path):
    r = run_cli(["plan", "review-only", "--json"], tmp_path)
    assert r.returncode == 0
    plan = json.loads(r.stdout)
    assert set(plan) >= {"recipe", "badges", "steps_field", "n_steps", "steps", "warnings"}
    assert plan["recipe"] == "review-only"
    assert plan["n_steps"] == 1
    assert plan["steps"][0]["id"] == "review"
    assert plan["steps"][0]["gate"] == "review-gate"


def test_plan_json_adaptive_bugfix(tmp_path):
    result = run_cli(["plan", "adaptive-bugfix", "--json"], tmp_path)

    assert result.returncode == 0
    plan = json.loads(result.stdout)
    assert plan["recipe"] == "adaptive-bugfix"
    assert [step["id"] for step in plan["steps"]] == [
        "implement",
        "assess",
        "targeted-review",
        "acceptance",
    ]
    assert [step["executor"] for step in plan["steps"]] == [
        "generate",
        "risk-assess",
        "targeted-review",
        "checks-only",
    ]


def test_plan_json_with_flags_returns_effective_resolution(tmp_path):
    r = run_cli(["plan", "release-flow", "--json", "--diff-lines", "50"], tmp_path)
    assert r.returncode == 0
    plan = json.loads(r.stdout)
    assert set(plan) >= {"effective_steps", "slice", "mode", "size", "flags", "errors"}
    assert plan["size"] == {"diff_lines": 50, "class": "S"}
    assert isinstance(plan["effective_steps"], list) and plan["effective_steps"]
    assert plan["errors"] == []


def test_unknown_command_exits_nonzero(tmp_path):
    r = run_cli(["no-such-command"], tmp_path)
    assert r.returncode != 0


def test_no_args_prints_usage_and_exits_zero(tmp_path):
    r = run_cli([], tmp_path)
    assert r.returncode == 0
    assert r.stdout.strip()  # usage text emitted (wording not asserted)
