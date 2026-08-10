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

# What a `@master` install would land on: behind this checkout, which is the
# situation that made the old "compare the checkout, install master" prompt
# unsatisfiable.
MASTER_VERSION = "0.0.1"

# Stands in for the chosen install method so the update path can be exercised
# without a network install (pipx wins the pipx > uv > pip preference order).
# It records the call *and* emulates the install: the `rig-wb` stub is rewritten
# to report the version the handed-over spec actually contains. That is what
# lets a test ask the only question that matters — after saying yes, does the
# installed version equal the one the installer compared against?
PIPX_STUB = """#!/bin/sh
echo "$@" >> "$(dirname "$0")/pipx-calls.log"
spec=""
for a in "$@"; do spec="$a"; done
case "$spec" in
  *@*) v="{master_version}" ;;
  /*)  v=$(sed -n 's/^__version__ *= *"\\([^"]*\\)".*/\\1/p' "$spec/rig_workbench/__init__.py") ;;
  *)   v="0.0.0" ;;
esac
printf '#!/bin/sh\\necho "rig-wb %s"\\n' "$v" > "$(dirname "$0")/rig-wb"
chmod 755 "$(dirname "$0")/rig-wb"
exit 0
"""


@pytest.fixture
def stub_bin(tmp_path):
    """A PATH whose `gh` reports the extension missing and whose `rig-wb` exists.

    `rig_wb_version` defaults to this checkout's version: a stub reporting anything
    else now means "stale install", which is a different code path entirely.
    """
    def build(gh_exit_code, rig_wb_version=REPO_VERSION, with_pipx=False,
              rig_wb_body=None):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        stubs = [("gh", GH_STUB.format(exit_code=gh_exit_code)),
                 ("rig-wb", rig_wb_body or RIG_WB_STUB.format(version=rig_wb_version))]
        if with_pipx:
            stubs.append(("pipx", PIPX_STUB.format(master_version=MASTER_VERSION)))
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
    """The update installs the very checkout it compared against — not `@master`,
    which is a different artefact and (this branch being ahead of master) could
    not satisfy the comparison that produced the prompt."""
    bin_dir = stub_bin(0, rig_wb_version="1.6.0", with_pipx=True)
    result = _run_installer(bin_dir, tmp_path, args=(), stdin="n\ny\n")
    assert result.returncode == 0, result.stdout + result.stderr
    calls = (bin_dir / "pipx-calls.log").read_text(encoding="utf-8")
    assert "install --force" in calls
    assert str(REPO_ROOT) in calls
    assert "rig.git@" not in calls


def test_accepted_update_converges_on_the_repo_version(tmp_path, stub_bin):
    """The point of the prompt: after answering yes, the installed version *is*
    the compared one, and the next run has nothing to say.

    Comparing against the checkout while installing `@master` shipped green under
    a test that only pinned which command ran. Here the pipx stub installs what it
    is handed, so the installer's own verify step is the assertion — and a second
    run proves the prompt does not come back."""
    bin_dir = stub_bin(0, rig_wb_version="1.6.0", with_pipx=True)
    first = _run_installer(bin_dir, tmp_path)
    assert first.returncode == 0, first.stdout + first.stderr
    assert f"Install complete: rig-wb {REPO_VERSION}" in first.stdout, first.stdout

    calls_after_update = (bin_dir / "pipx-calls.log").read_text(encoding="utf-8")
    second = _run_installer(bin_dir, tmp_path)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "rig-wb is already installed" in second.stdout
    assert "Version mismatch" not in second.stdout
    assert (bin_dir / "pipx-calls.log").read_text(encoding="utf-8") == calls_after_update


def test_yes_updates_without_prompting(tmp_path, stub_bin):
    """--yes keeps its existing meaning: answer the prompts, do not add one.
    It still names what it is replacing and where the replacement comes from —
    silence is only acceptable when nothing changes."""
    bin_dir = stub_bin(0, rig_wb_version="1.6.0", with_pipx=True)
    result = _run_installer(bin_dir, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"--yes: updating rig-wb 1.6.0 → {REPO_VERSION}" in result.stdout
    assert str(REPO_ROOT) in result.stdout
    assert "install --force" in (bin_dir / "pipx-calls.log").read_text(encoding="utf-8")


# ── ordering, and unreadable versions ───────────────────────────────────
# Equality is not enough: it made "installed newer than the checkout" look
# exactly like "installed older", so a newer global CLI was announced as an
# update and, under --yes, replaced silently.


def test_newer_install_is_never_downgraded(tmp_path, stub_bin):
    bin_dir = stub_bin(0, rig_wb_version="99.0.0", with_pipx=True)
    result = _run_installer(bin_dir, tmp_path, args=(), stdin="n\ny\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No update to offer" in result.stdout
    assert "99.0.0" in result.stdout and REPO_VERSION in result.stdout
    assert "--force" in result.stdout          # the escape hatch is named
    assert not (bin_dir / "pipx-calls.log").exists()


def test_yes_does_not_downgrade_silently(tmp_path, stub_bin):
    """`--yes` is designated for automated in-skill runs, so this fires unattended."""
    bin_dir = stub_bin(0, rig_wb_version="99.0.0", with_pipx=True)
    result = _run_installer(bin_dir, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No update to offer" in result.stdout
    assert "◇ Installing..." not in result.stdout   # the rig install banner
    assert not (bin_dir / "pipx-calls.log").exists()


def test_force_still_installs_this_checkout_over_a_newer_one(tmp_path, stub_bin):
    """Refusing a downgrade is a default, not a wall."""
    bin_dir = stub_bin(0, rig_wb_version="99.0.0", with_pipx=True)
    result = _run_installer(bin_dir, tmp_path, args=("--yes", "--force"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert str(REPO_ROOT) in (bin_dir / "pipx-calls.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("body,label", [
    ('#!/bin/sh\necho "boom" >&2\nexit 1\n', "non-zero exit"),
    ('#!/bin/sh\necho "warning: something odd"\necho "rig-wb 1.6.0"\n', "leading warning line"),
    ('#!/bin/sh\necho "usage: rig-wb <cmd> ... see --help for all"\n', "non-version output"),
])
def test_unreadable_version_is_undetermined_not_stale(tmp_path, stub_bin, body, label):
    """Taking the last token of whatever came out invented versions ("?", "odd",
    "all") and declared a mismatch against every one of them. Unparseable means
    undetermined: report it, and fall back to presence-only."""
    bin_dir = stub_bin(0, with_pipx=True, rig_wb_body=body)
    result = _run_installer(bin_dir, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Version mismatch" not in result.stdout, label
    assert "version undetermined" in result.stdout, label
    assert not (bin_dir / "pipx-calls.log").exists(), label


# ── explicit --ref ──────────────────────────────────────────────────────
# A ref names something on GitHub whose version this script cannot know without
# fetching it, so the comparison — and the update offer — is off in that mode.


def test_explicit_ref_is_not_compared_against_the_checkout(tmp_path, stub_bin):
    bin_dir = stub_bin(0, rig_wb_version="1.6.0", with_pipx=True)
    result = _run_installer(bin_dir, tmp_path, args=("--yes", "--ref", "v1.2.3"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Version mismatch" not in result.stdout
    assert "rig-wb is already installed" in result.stdout
    assert not (bin_dir / "pipx-calls.log").exists()


def test_explicit_ref_still_installs_that_ref_from_github(tmp_path, stub_bin):
    bin_dir = stub_bin(0, rig_wb_version="1.6.0", with_pipx=True)
    result = _run_installer(bin_dir, tmp_path, args=("--yes", "--force", "--ref", "v1.2.3"))
    assert result.returncode == 0, result.stdout + result.stderr
    calls = (bin_dir / "pipx-calls.log").read_text(encoding="utf-8")
    assert "github.com/itoh-shun/rig.git@v1.2.3" in calls


def test_ref_without_a_value_is_rejected(tmp_path, stub_bin):
    """`--ref` with nothing after it used to build a bare `…rig.git@` spec."""
    result = _run_installer(stub_bin(0), tmp_path, args=("--ref",))
    assert result.returncode == 2, result.stdout + result.stderr


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
