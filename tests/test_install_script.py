"""scripts/install.sh — the optional gh-stack step must not abort the install,
and a stale rig-wb must not pass for an installed one.

`gh` and github/gh-stack are optional for rig: they add stacked-PR publishing and
nothing gates on them. The installer runs under `set -euo pipefail`, so the
extension install has to be guarded explicitly — otherwise a network hiccup, an
expired token or a bad extension release takes down an unrelated `pip install`.

Presence on PATH is not the same as being current: an installed rig-wb keeps
loading this repo's scripts/*.py, so a stale one fails with import errors while
the installer reported "already installed ✓". The skip decision compares versions
and offers an update — it never installs over the user's tooling unasked.

Everything is exercised against stubs on PATH: no network, no real gh extension,
a stub `rig-wb` reporting a chosen version, and a stub `pipx` that records the
install command instead of running it.
"""

import os
import pathlib
import re
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"

# What `rig-wb version` prints for this checkout — the installer compares against it.
REPO_VERSION = re.search(
    r'^__version__ = "([^"]+)"',
    (REPO_ROOT / "rig_workbench" / "__init__.py").read_text(encoding="utf-8"),
    re.M,
).group(1)

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

RIG_WB_STUB = '#!/bin/sh\necho "rig-wb {version}"\n'

# Stands in for the chosen install method so the update path can be exercised
# without a network install. pipx wins the pipx > uv > pip preference order.
PIPX_STUB = """#!/bin/sh
echo "$@" >> "$(dirname "$0")/pipx-calls.log"
exit 0
"""


@pytest.fixture
def stub_bin(tmp_path):
    """A PATH whose `gh` reports the extension missing and whose `rig-wb` exists.

    `rig_wb_version` defaults to this checkout's version: a stub reporting anything
    else now means "stale install", which is a different code path entirely.
    """
    def build(gh_exit_code, rig_wb_version=REPO_VERSION, with_pipx=False):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        stubs = [("gh", GH_STUB.format(exit_code=gh_exit_code)),
                 ("rig-wb", RIG_WB_STUB.format(version=rig_wb_version))]
        if with_pipx:
            stubs.append(("pipx", PIPX_STUB))
        for name, body in stubs:
            path = bin_dir / name
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)
        return bin_dir
    return build


def _run_installer(bin_dir, tmp_path, args=("--yes",), stdin=""):
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", HOME=str(tmp_path))
    return subprocess.run(["bash", str(INSTALL_SH), *args], input=stdin,
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


# ── version skew ────────────────────────────────────────────────────────
# A rig-wb installed months ago stays on PATH and keeps loading the current
# repo's scripts/*.py. Presence alone said "already installed ✓" while the
# actual failure was a ModuleNotFoundError from the mismatched layout.


def test_matching_version_is_reported_as_already_installed(tmp_path, stub_bin):
    result = _run_installer(stub_bin(0), tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "rig-wb is already installed" in result.stdout
    assert "Version mismatch" not in result.stdout


def test_stale_install_is_detected_and_shows_both_versions(tmp_path, stub_bin):
    # Interactive run: the first stdin line answers the optional gh-stack prompt,
    # the second answers the update prompt.
    bin_dir = stub_bin(0, rig_wb_version="1.6.0")
    result = _run_installer(bin_dir, tmp_path, args=(), stdin="n\nn\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Version mismatch" in result.stdout
    assert "1.6.0" in result.stdout
    assert REPO_VERSION in result.stdout
    assert "rig-wb is already installed" not in result.stdout


def test_stale_install_declined_changes_nothing(tmp_path, stub_bin):
    """Detect, report, ask. Answering no must leave the global tooling alone."""
    bin_dir = stub_bin(0, rig_wb_version="1.6.0", with_pipx=True)
    result = _run_installer(bin_dir, tmp_path, args=(), stdin="n\nn\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Keeping 1.6.0" in result.stdout
    assert "◇ Installing" not in result.stdout
    assert not (bin_dir / "pipx-calls.log").exists()


def test_stale_install_accepted_updates(tmp_path, stub_bin):
    bin_dir = stub_bin(0, rig_wb_version="1.6.0", with_pipx=True)
    result = _run_installer(bin_dir, tmp_path, args=(), stdin="n\ny\n")
    assert result.returncode == 0, result.stdout + result.stderr
    calls = (bin_dir / "pipx-calls.log").read_text(encoding="utf-8")
    assert "install --force" in calls
    assert "github.com/itoh-shun/rig.git@master" in calls


def test_yes_updates_without_prompting(tmp_path, stub_bin):
    """--yes keeps its existing meaning: answer the prompts, do not add one."""
    bin_dir = stub_bin(0, rig_wb_version="1.6.0", with_pipx=True)
    result = _run_installer(bin_dir, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--yes: updating to" in result.stdout
    assert "install --force" in (bin_dir / "pipx-calls.log").read_text(encoding="utf-8")


def test_check_only_reports_skew_without_installing(tmp_path, stub_bin):
    """--check stays detection-only: no prompt, no install, documented exit codes."""
    bin_dir = stub_bin(0, rig_wb_version="1.6.0", with_pipx=True)
    result = _run_installer(bin_dir, tmp_path, args=("--check",))
    # The stub pipx makes an install method available, so --check's documented
    # "exit 0 = an install method exists" is pinned, not merely tolerated.
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Version mismatch" in result.stdout
    assert "Environment detection" in result.stdout
    assert "◇ Installing" not in result.stdout
    assert not (bin_dir / "pipx-calls.log").exists()


def test_uninstall_is_unaffected_by_a_version_skew(tmp_path, stub_bin):
    bin_dir = stub_bin(0, rig_wb_version="1.6.0")
    result = _run_installer(bin_dir, tmp_path, args=("--uninstall",))
    assert "◇ Uninstalling: currently rig-wb 1.6.0" in result.stdout
    assert "Version mismatch" not in result.stdout


def test_reported_version_matches_the_other_version_literals():
    """The installer compares against __init__.py; a drifting pyproject would ship a
    wheel whose recorded version is not the one `rig-wb version` prints, and a
    drifting plugin.json would misreport the same release to Claude Code."""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(rf'^version = "{re.escape(REPO_VERSION)}"$', pyproject, re.M), \
        "pyproject.toml [project] version and rig_workbench.__version__ disagree"
    plugin = (REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    assert f'"version": "{REPO_VERSION}"' in plugin, \
        ".claude-plugin/plugin.json version and rig_workbench.__version__ disagree"
