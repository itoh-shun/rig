"""The installed layout has no `scripts/` sibling — the package must not need one.

`rig_workbench/workbench/accept.py` used to reach `<repo>/scripts` through
`sys.path` to `import ast_diff`. That works in a checkout and breaks for every
`rig-wb wb <anything>` once installed, because the same computation lands on a
`site-packages/scripts` that does not exist and is not shipped. The module now
lives at `rig_workbench/ast_diff.py`, and `scripts/ast_diff.py` is a launcher that
keeps the documented direct-script entry point.

These tests simulate the installed layout by copying `rig_workbench/` alone into a
tmp dir — no repo root, no `scripts/` anywhere on the path.
"""

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def installed_layout(tmp_path_factory):
    """A directory holding only the `rig_workbench` package, as site-packages would."""
    root = tmp_path_factory.mktemp("site-packages")
    shutil.copytree(REPO_ROOT / "rig_workbench", root / "rig_workbench",
                    ignore=shutil.ignore_patterns("__pycache__"))
    assert not (root / "scripts").exists()
    return root


def _import_probe(installed_layout, module: str, tmp_path):
    """Import `module` from the copied package, from a cwd outside any rig checkout."""
    env = dict(os.environ, PYTHONPATH=str(installed_layout))
    env.pop("RIG_HOME", None)
    return subprocess.run([sys.executable, "-c", f"import {module}"],
                          capture_output=True, text=True, env=env, cwd=tmp_path, timeout=120)


@pytest.mark.parametrize("module", [
    "rig_workbench.ast_diff",
    "rig_workbench.workbench.accept",
    "rig_workbench.workbench.cli",   # imports accept; every `wb` subcommand goes through it
    "rig_workbench.cli",
])
def test_package_imports_without_a_scripts_dir(installed_layout, tmp_path, module):
    proc = _import_probe(installed_layout, module, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "ModuleNotFoundError" not in proc.stderr


def test_package_never_puts_a_scripts_dir_on_sys_path(installed_layout, tmp_path):
    """The import-time sys.path hack is what broke; keep it from coming back."""
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys, rig_workbench.workbench.cli; "
         "print([p for p in sys.path if p.rstrip('/').endswith('scripts')])"],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(installed_layout)}, cwd=tmp_path, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "[]", proc.stdout


def test_scripts_ast_diff_still_runs_as_a_direct_script(tmp_path):
    """`python3 scripts/ast_diff.py <base.py> <new.py>` is a documented entry point."""
    base = tmp_path / "base.py"
    new = tmp_path / "new.py"
    base.write_text("def f(a):\n    return a\n", encoding="utf-8")
    new.write_text("def f(a, b=1):\n    return a + b\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "ast_diff.py"), str(base), str(new)],
        capture_output=True, text=True, cwd=tmp_path, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "signature changed: f" in proc.stdout


# ── version skew notice ─────────────────────────────────────────────────
# The same skew from the other side: an old rig-wb on PATH drives a newer
# checkout's scripts/*.py and fails with an import error from a layout that
# release never had. One stderr line names both versions; it never blocks.


def _fake_repo(tmp_path, version):
    """A directory that `_rig_home()` accepts, declaring `version`."""
    root = tmp_path / "fake-rig"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "orchestrate.py").write_text("", encoding="utf-8")
    (root / "rig_workbench").mkdir()
    (root / "rig_workbench" / "__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8")
    return root


def _run_cli_version(tmp_path, **env_extra):
    # cwd is a tmp dir, so the package has to come from PYTHONPATH; RIG_HOME then
    # decides which checkout the CLI thinks it is driving.
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT), **env_extra)
    return subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.argv = ['rig-wb', 'version']; "
         "from rig_workbench.cli import main; main()"],
        capture_output=True, text=True, env=env, cwd=tmp_path, timeout=120)


def test_version_skew_is_reported_on_one_stderr_line(tmp_path):
    proc = _run_cli_version(tmp_path, RIG_HOME=str(_fake_repo(tmp_path, "9.9.9")))
    assert proc.returncode == 0, proc.stderr
    skew = [ln for ln in proc.stderr.splitlines() if "version skew" in ln]
    assert len(skew) == 1, proc.stderr
    assert "9.9.9" in skew[0]
    assert proc.stdout.strip().startswith("rig-wb "), proc.stdout


def test_version_skew_notice_is_silenceable(tmp_path):
    proc = _run_cli_version(tmp_path, RIG_HOME=str(_fake_repo(tmp_path, "9.9.9")),
                            RIG_SKIP_VERSION_CHECK="1")
    assert proc.returncode == 0, proc.stderr
    assert "version skew" not in proc.stderr


def test_no_version_skew_notice_when_the_checkout_matches(tmp_path):
    proc = _run_cli_version(tmp_path, RIG_HOME=str(REPO_ROOT))
    assert proc.returncode == 0, proc.stderr
    assert "version skew" not in proc.stderr


def test_ast_diff_is_shipped_by_the_package_finder():
    """`scripts/` is not in pyproject's include list, so the module must not live there."""
    assert (REPO_ROOT / "rig_workbench" / "ast_diff.py").is_file()
    include = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"scripts' not in include, "scripts/ must not become an installed top-level package"
