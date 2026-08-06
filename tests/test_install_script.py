"""scripts/install.sh — the optional gh-stack step must not abort the install.

`gh` and github/gh-stack are optional for rig: they add stacked-PR publishing and
nothing gates on them. The installer runs under `set -euo pipefail`, so the
extension install has to be guarded explicitly — otherwise a network hiccup, an
expired token or a bad extension release takes down an unrelated `pip install`.

Exercised against a stub `gh` on PATH: no network, no real extension, and a stub
`rig-wb` so the run stops at the idempotent "already installed" branch instead of
touching pipx/pip.
"""

import os
import pathlib
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"

GH_STUB = """#!/bin/sh
if [ "$1" = "--version" ]; then echo "gh version 2.60.0 (2026-01-01)"; exit 0; fi
if [ "$1" = "auth" ]; then exit 0; fi
if [ "$1" = "extension" ] && [ "$2" = "list" ]; then exit 0; fi
if [ "$1" = "extension" ] && [ "$2" = "install" ]; then
  echo "$@" >> "$(dirname "$0")/calls.log"
  exit {exit_code}
fi
exit 0
"""

RIG_WB_STUB = '#!/bin/sh\necho "1.29.0"\n'


@pytest.fixture
def stub_bin(tmp_path):
    """A PATH whose `gh` reports the extension missing and whose `rig-wb` exists."""
    def build(gh_exit_code):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        for name, body in (("gh", GH_STUB.format(exit_code=gh_exit_code)),
                           ("rig-wb", RIG_WB_STUB)):
            path = bin_dir / name
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)
        return bin_dir
    return build


def _run_installer(bin_dir, tmp_path):
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", HOME=str(tmp_path))
    return subprocess.run(["bash", str(INSTALL_SH), "--yes"],
                          capture_output=True, text=True, env=env, cwd=tmp_path, timeout=120)


def test_install_script_has_valid_syntax():
    result = subprocess.run(["bash", "-n", str(INSTALL_SH)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_installer_continues_when_gh_stack_install_fails(tmp_path, stub_bin):
    """Codex reproduced an extension exit of 23 taking the whole installer with
    it. The rig install must outlive its optional companion."""
    bin_dir = stub_bin(23)
    result = _run_installer(bin_dir, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "gh-stack install failed" in result.stdout
    # It got past the gh section and reached the actual rig install.
    assert "rig-wb is already installed" in result.stdout


def test_installer_still_attempts_the_extension_install(tmp_path, stub_bin):
    """Tolerating failure must not turn into skipping the install."""
    bin_dir = stub_bin(0)
    result = _run_installer(bin_dir, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "gh-stack install failed" not in result.stdout
    assert "install github/gh-stack" in (bin_dir / "calls.log").read_text(encoding="utf-8")
