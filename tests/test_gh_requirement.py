"""Tests for the `gh` + github/gh-stack advisory (rig_workbench/gh_requirement.py).

Every `gh` state is *simulated*: a fake `gh` shell script is placed in a sandbox
bin directory and PATH is pointed at it, so the suite behaves identically on a
machine with a real authenticated gh and on one with no gh at all. Nothing here
runs the real gh, installs an extension, or touches the developer's config.

The sandbox PATH also deliberately excludes the real gh, which is what makes the
"gh is not installed" state reproducible on a developer machine.

`gh` and gh-stack are **optional**: they are worth a one-line note and nothing
more, so the wiring tests assert that every entry point *proceeds* and merely
advises. The probe itself still reports exactly one state, and `gh-check` still
answers with its documented exit code. Authentication was never part of it —
`gh stack`'s local operations need neither auth nor a remote — so the
unauthenticated case is asserted to be a clean pass.
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from rig_workbench import gh_requirement as ghreq

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"
ORCHESTRATE = REPO_ROOT / "scripts" / "orchestrate.py"
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"

# Tools the sandboxed PATH keeps available. `gh` is never among them: it only
# ever appears as the fake script written by `fake_gh`.
_SANDBOX_TOOLS = ("bash", "sh", "git", "env", "uname", "awk", "grep", "head",
                  "sed", "cat", "tr", "cut", "sort", "mkdir", "rm", "ls", "dirname", "touch")

_FAKE_GH = r"""#!/bin/bash
# Fake `gh` for tests. Behaviour is fixed at write time; every invocation is
# appended to $GH_FAKE_LOG so a test can prove what was (not) run.
if [ -n "${GH_FAKE_LOG:-}" ]; then
  echo "$*" >> "$GH_FAKE_LOG"
fi
MARKER="__MARKER__"
case "$1" in
  --version)
    if [ "__VERSION_OK__" = "0" ]; then
      echo "gh: broken install" >&2
      exit 3
    fi
    echo "gh version __VERSION__ (2026-03-10)"
    echo "https://github.com/cli/cli/releases/tag/v__VERSION__"
    exit 0
    ;;
  auth)
    if [ "__AUTHED__" = "1" ]; then
      echo "github.com"
      echo "  ✓ Logged in to github.com account tester (keyring)"
      exit 0
    fi
    echo "You are not logged into any GitHub hosts. To log in, run: gh auth login" >&2
    exit 1
    ;;
  extension)
    case "$2" in
      list)
        if [ "__EXT__" = "1" ] || [ -f "$MARKER" ]; then
          printf 'gh stack\tgithub/gh-stack\tv0.1.0\n'
          exit 0
        fi
        echo "no extensions installed" >&2
        exit 1
        ;;
      install)
        touch "$MARKER"
        echo "✓ Installed extension github/gh-stack"
        exit 0
        ;;
    esac
    ;;
esac
echo "fake gh: unsupported invocation: $*" >&2
exit 1
"""


@pytest.fixture
def sandbox_bin(tmp_path):
    """A bin directory holding only the tools tests need — and never a real `gh`."""
    d = tmp_path / "sandbox-bin"
    d.mkdir()
    for tool in _SANDBOX_TOOLS:
        real = shutil.which(tool)
        if real:
            (d / tool).symlink_to(real)
    assert shutil.which("gh", path=str(d)) is None, "sandbox PATH must not expose a real gh"
    return d


@pytest.fixture
def fake_gh(sandbox_bin, tmp_path):
    """Write a fake `gh` into the sandbox in a chosen state. Returns the marker path."""

    def make(*, version="2.88.0", version_ok=True, authed=True, extension=True):
        marker = tmp_path / "gh-stack-installed"
        body = (_FAKE_GH
                .replace("__VERSION_OK__", "1" if version_ok else "0")
                .replace("__VERSION__", version)
                .replace("__AUTHED__", "1" if authed else "0")
                .replace("__EXT__", "1" if extension else "0")
                .replace("__MARKER__", str(marker)))
        path = sandbox_bin / "gh"
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
        return marker

    return make


@pytest.fixture
def use_sandbox(sandbox_bin, monkeypatch):
    """Point this process's PATH at the sandbox and clear the escape hatch."""
    monkeypatch.setenv("PATH", str(sandbox_bin))
    monkeypatch.delenv(ghreq.SKIP_ENV, raising=False)
    return sandbox_bin


def _child_env(sandbox_bin, tmp_path, *, skip=None, log=None):
    """Environment for a subprocess-level test: sandboxed PATH, no inherited hatch."""
    env = dict(os.environ)
    env.pop(ghreq.SKIP_ENV, None)
    env.update(PATH=str(sandbox_bin), HOME=str(tmp_path), RIG_HOME=str(REPO_ROOT),
               PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    if skip is not None:
        env[ghreq.SKIP_ENV] = skip
    if log is not None:
        env["GH_FAKE_LOG"] = str(log)
    return env


@pytest.fixture
def git_repo(tmp_path):
    """A scratch git repo so `workbench new` has somewhere to run."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "gh@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "gh test"], cwd=repo, check=True)
    (repo / "README.md").write_text("scratch\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


# ── the states ───────────────────────────────────────────────────────────────

def test_state_ok_reports_both_versions_and_the_account(use_sandbox, fake_gh):
    fake_gh()
    status = ghreq.check_gh()
    assert status.state == ghreq.STATE_OK
    assert status.ok is True
    assert status.exit_code == 0
    assert status.gh_version == "2.88.0"
    assert status.stack_version == "v0.1.0"
    assert status.authenticated is True
    assert status.account == "tester"
    assert "2.88.0" in status.summary() and "v0.1.0" in status.summary()
    assert "authenticated as tester" in status.summary()


def test_state_gh_missing(use_sandbox):
    # No fake gh written: the sandbox PATH has no gh at all.
    status = ghreq.check_gh()
    assert status.state == ghreq.STATE_GH_MISSING
    assert status.exit_code == 3
    assert "brew install gh" in status.remedy
    assert "gh extension install github/gh-stack" in status.remedy


def test_state_extension_missing(use_sandbox, fake_gh):
    fake_gh(extension=False)
    status = ghreq.check_gh()
    assert status.state == ghreq.STATE_EXTENSION_MISSING
    assert status.exit_code == 5
    assert status.gh_version == "2.88.0"
    assert status.remedy.strip() == ("Install the stacked-branch extension:\n"
                                     "    gh extension install github/gh-stack")


def test_gh_on_path_but_broken_counts_as_missing(use_sandbox, fake_gh):
    """`gh` present but unusable gets the install remedy, not some other one."""
    fake_gh(version_ok=False)
    status = ghreq.check_gh()
    assert status.state == ghreq.STATE_GH_MISSING
    assert status.exit_code == 3
    assert "gh --version" in status.detail


def test_each_failure_names_a_command_to_run(use_sandbox, fake_gh):
    """Every failure must tell the user exactly what to run."""
    for maker, expected in (
        (lambda: None, "brew install gh"),
        (lambda: fake_gh(extension=False), "gh extension install github/gh-stack"),
    ):
        maker()
        status = ghreq.check_gh()
        assert not status.ok
        assert expected in status.remedy
        assert expected in ghreq.format_report(status)


# ── authentication is informational, never a failure ─────────────────────────

def test_present_but_unauthenticated_passes(use_sandbox, fake_gh, capsys):
    """`gh stack` works locally with no auth and no remote, so this must PASS."""
    fake_gh(authed=False)
    status = ghreq.check_gh()
    assert status.state == ghreq.STATE_OK
    assert status.ok is True
    assert status.exit_code == 0
    assert status.stack_version == "v0.1.0"
    assert status.authenticated is False
    assert status.account is None
    assert status.remedy == "", "there is nothing to fix — auth is not required"

    # And it must not advise, nor emit anything.
    assert ghreq.advise_gh("workbench new").ok
    assert capsys.readouterr().err == ""


def test_unauthenticated_is_reported_and_scoped_to_remote_operations(use_sandbox, fake_gh):
    fake_gh(authed=False)
    status = ghreq.check_gh()
    assert status.auth_summary() == "not authenticated (only needed for push/submit/sync)"
    assert "not authenticated" in status.summary()
    assert "push/submit/sync" in status.summary()


def test_auth_state_never_changes_the_verdict(use_sandbox, fake_gh):
    """Same requirement outcome with and without auth, in both directions."""
    for extension, expected in ((True, ghreq.STATE_OK), (False, ghreq.STATE_EXTENSION_MISSING)):
        fake_gh(extension=extension, authed=True)
        authed = ghreq.check_gh()
        fake_gh(extension=extension, authed=False)
        anon = ghreq.check_gh()
        assert authed.state == anon.state == expected
        assert authed.exit_code == anon.exit_code
        assert authed.authenticated is True and anon.authenticated is False


def test_no_state_is_produced_for_authentication():
    """Auth must not be reachable as a pass/fail state at all."""
    assert set(ghreq.EXIT_CODES) == {ghreq.STATE_OK, ghreq.STATE_GH_MISSING,
                                     ghreq.STATE_EXTENSION_MISSING}
    assert not hasattr(ghreq, "STATE_GH_UNAUTHENTICATED")
    assert all("auth" not in state for state in ghreq.EXIT_CODES)


# ── exit-code contract ───────────────────────────────────────────────────────

def test_exit_code_contract_is_stable_and_distinct():
    assert ghreq.EXIT_CODES == {
        ghreq.STATE_OK: 0,
        ghreq.STATE_GH_MISSING: 3,
        ghreq.STATE_EXTENSION_MISSING: 5,
    }
    codes = list(ghreq.EXIT_CODES.values())
    assert len(set(codes)) == len(codes), "each state must be distinguishable by exit code"
    failing = [c for s, c in ghreq.EXIT_CODES.items() if s != ghreq.STATE_OK]
    assert all(c != 0 for c in failing), "a caller must be able to test 'not fine' as non-zero"
    assert 2 not in codes, "2 stays reserved for CLI usage errors (rig-wb convention)"
    assert 4 not in codes, "4 is retired (it meant 'not authenticated'), never reassigned"


def test_no_rig_command_exits_with_a_state_code(use_sandbox, fake_gh, capsys):
    """The codes stay scriptable for `gh-check`, but nothing blocks on them."""
    for maker, state in (
        (lambda: None, ghreq.STATE_GH_MISSING),
        (lambda: fake_gh(extension=False), ghreq.STATE_EXTENSION_MISSING),
    ):
        maker()
        status = ghreq.advise_gh("workbench new")  # must not raise
        assert status.state == state
        assert status.exit_code in (3, 5), "the contract still reports a code"
        err = capsys.readouterr().err
        assert "[ERROR]" not in err, "a missing optional tool is not an error"
        assert "[NOTE] workbench new:" in err


def test_advise_gh_is_silent_when_gh_is_present(use_sandbox, fake_gh, capsys):
    fake_gh()
    status = ghreq.advise_gh("workbench new")
    assert status.ok
    assert capsys.readouterr().err == "", "nothing to say when nothing is missing"


# ── the advisory ─────────────────────────────────────────────────────────────

def test_advisory_is_one_line_that_says_what_is_missing_and_how_to_end_it(
        use_sandbox, fake_gh, capsys):
    fake_gh(extension=False)
    ghreq.advise_gh("orchestrate run")
    err = capsys.readouterr().err
    assert err.count("\n") == 1, f"the advisory must be a single line, got:\n{err}"
    line = err.strip()
    assert line.startswith("[NOTE] orchestrate run:"), "it must name the entry point"
    assert "github/gh-stack` is not installed" in line, "what is missing"
    assert "gh stack" in line, "what it would enable"
    assert "rig does not need them" in line, "and that nothing is broken"
    assert "gh extension install github/gh-stack" in line, "how to fix it"
    assert f"silence: {ghreq.SKIP_ENV}=1" in line, "how to stop hearing it"


def test_advisory_covers_a_missing_gh_too(use_sandbox, capsys):
    ghreq.advise_gh("workbench new")
    line = capsys.readouterr().err.strip()
    assert line.count("\n") == 0
    assert "the GitHub CLI (`gh`) is not installed" in line
    assert "install gh" in line and "gh extension install github/gh-stack" in line


def test_advisory_repeats_on_every_invocation(use_sandbox, capsys):
    """No once-per-session state: a suppression that decays invisibly is how a
    diagnostic silently disappears. Repetition is the price of determinism, and
    the line names its own off switch."""
    for _ in range(3):
        ghreq.advise_gh("workbench new")
    assert capsys.readouterr().err.count("[NOTE]") == 3


def test_advisory_never_probes_authentication(use_sandbox, fake_gh, monkeypatch,
                                              tmp_path, capsys):
    """Auth is not in the advisory, so asking github.com for it is a network
    wait whose answer has no consumer. `gh-check` still asks, because it reports."""
    fake_gh(extension=False)
    log = tmp_path / "gh-calls.log"
    monkeypatch.setenv("GH_FAKE_LOG", str(log))

    ghreq.advise_gh("workbench new")
    capsys.readouterr()
    assert "auth status" not in log.read_text(encoding="utf-8")
    assert log.read_text(encoding="utf-8").count("extension list") == 1

    ghreq.cmd_gh_check([])
    capsys.readouterr()
    assert "auth status" in log.read_text(encoding="utf-8")


def test_silencer_suppresses_the_advisory_and_skips_the_probe(
        use_sandbox, monkeypatch, tmp_path, capsys):
    """Silenced means silent *and* free: an air-gapped machine runs no probe."""
    monkeypatch.setenv(ghreq.SKIP_ENV, "1")
    log = tmp_path / "gh-calls.log"
    monkeypatch.setenv("GH_FAKE_LOG", str(log))
    assert ghreq.advise_gh("workbench new") is None
    assert capsys.readouterr().err == ""
    assert not log.exists(), "the silenced path must not shell out at all"


def test_silencer_does_not_change_what_gh_check_reports(
        use_sandbox, fake_gh, monkeypatch, capsys):
    """`gh-check` is someone asking to be told: the silencer must not gag it."""
    fake_gh(extension=False)
    monkeypatch.delenv(ghreq.SKIP_ENV, raising=False)
    assert ghreq.cmd_gh_check([]) == 5
    loud = capsys.readouterr().err

    monkeypatch.setenv(ghreq.SKIP_ENV, "1")
    assert ghreq.cmd_gh_check([]) == 5
    silenced = capsys.readouterr().err
    assert "gh extension install github/gh-stack" in silenced
    assert silenced.startswith(loud.splitlines()[0])
    assert ghreq.SKIP_ENV in silenced, "it should say why runs stay quiet"


def test_silencer_is_not_triggered_by_falsey_values(use_sandbox, monkeypatch, capsys):
    """Only the parsing is under test here — what the advisory *says* belongs to
    the tests above, so this one does not assert on its text."""
    for value in ("", "0", "false", "no", "off", "FALSE"):
        monkeypatch.setenv(ghreq.SKIP_ENV, value)
        assert ghreq.silence_requested() is False
        assert ghreq.advise_gh("workbench new") is not None, "the probe must still run"
        capsys.readouterr()
    for value in ("1", "yes", "true", "air-gapped"):
        monkeypatch.setenv(ghreq.SKIP_ENV, value)
        assert ghreq.silence_requested() is True
        assert ghreq.advise_gh("workbench new") is None
        assert capsys.readouterr().err == ""


# ── `rig-wb gh-check` ────────────────────────────────────────────────────────

def test_gh_check_command_exit_codes_and_json(use_sandbox, fake_gh, capsys):
    fake_gh()
    assert ghreq.cmd_gh_check([]) == 0
    assert "✓" in capsys.readouterr().out

    fake_gh(extension=False)
    assert ghreq.cmd_gh_check([]) == 5
    assert "gh extension install github/gh-stack" in capsys.readouterr().err

    assert ghreq.cmd_gh_check(["--json"]) == 5
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == ghreq.STATE_EXTENSION_MISSING
    assert payload["ok"] is False
    assert payload["exit_code"] == 5

    assert ghreq.cmd_gh_check(["--nope"]) == 2, "unknown flag is a usage error, not a state"


def test_gh_check_reports_auth_without_failing_on_it(use_sandbox, fake_gh, capsys):
    fake_gh(authed=False)
    assert ghreq.cmd_gh_check([]) == 0
    out = capsys.readouterr().out
    assert out.startswith("✓")
    assert "not authenticated (only needed for push/submit/sync)" in out

    assert ghreq.cmd_gh_check(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == ghreq.STATE_OK
    assert payload["ok"] is True
    assert payload["authenticated"] is False
    assert payload["account"] is None


# ── wiring: which entry points advise, which stay quiet ──────────────────────

def test_workbench_new_proceeds_and_advises_without_gh(sandbox_bin, tmp_path, git_repo):
    """The task starts. The only trace of a missing gh is one note on stderr."""
    result = subprocess.run(
        [sys.executable, str(WORKBENCH), "new", "add a feature",
         "--type", "feature", "--no-worktree"],
        cwd=git_repo, capture_output=True, text=True,
        env=_child_env(sandbox_bin, tmp_path))
    assert result.returncode == 0, result.stderr
    assert (git_repo / ".rig" / "runs").is_dir(), "the run must actually be created"
    assert result.stderr.strip().count("\n") == 0, f"one line only:\n{result.stderr}"
    assert result.stderr.startswith("[NOTE] workbench new:")
    assert "gh extension install github/gh-stack" in result.stderr
    assert "[ERROR]" not in result.stderr


def test_workbench_new_runs_with_an_unauthenticated_gh(sandbox_bin, tmp_path, fake_gh, git_repo):
    """End-to-end: no auth, no remote, no escape hatch — the task still starts."""
    fake_gh(authed=False)
    result = subprocess.run(
        [sys.executable, str(WORKBENCH), "new", "add a feature",
         "--type", "feature", "--no-worktree"],
        cwd=git_repo, capture_output=True, text=True,
        env=_child_env(sandbox_bin, tmp_path))
    assert result.returncode == 0, result.stderr
    assert result.stderr == "", "an unauthenticated gh is not worth a single warning line"
    assert (git_repo / ".rig" / "runs").is_dir()


def test_workbench_new_is_completely_silent_when_the_advisory_is_off(
        sandbox_bin, tmp_path, git_repo):
    result = subprocess.run(
        [sys.executable, str(WORKBENCH), "new", "add a feature",
         "--type", "feature", "--no-worktree"],
        cwd=git_repo, capture_output=True, text=True,
        env=_child_env(sandbox_bin, tmp_path, skip="1"))
    assert result.returncode == 0, result.stderr
    assert result.stderr == "", f"the silencer must leave nothing behind:\n{result.stderr}"
    assert (git_repo / ".rig" / "runs").is_dir()


def test_workbench_read_only_commands_say_nothing_about_gh(sandbox_bin, tmp_path, git_repo):
    """board/log/gates are not where anyone chooses a PR flow — a note there is noise."""
    env = _child_env(sandbox_bin, tmp_path)
    for argv in (["board"], ["log"], ["gates"]):
        result = subprocess.run([sys.executable, str(WORKBENCH), *argv],
                                cwd=git_repo, capture_output=True, text=True, env=env)
        assert result.returncode == 0, f"{argv}: {result.stderr}"
        assert "[NOTE]" not in result.stderr
        assert "gh-stack" not in result.stderr


def test_orchestrate_run_proceeds_and_advises_without_gh(sandbox_bin, tmp_path, git_repo):
    """The run completes on the mock provider; the missing gh costs one line."""
    result = subprocess.run(
        [sys.executable, str(ORCHESTRATE), "run", "bugfix", "--provider", "mock"],
        cwd=git_repo, capture_output=True, text=True,
        env=_child_env(sandbox_bin, tmp_path))
    assert result.returncode == 0, result.stderr
    assert "=== Finished: DONE ===" in result.stdout, "the run really ran"
    assert result.stderr.startswith("[NOTE] orchestrate run:")
    assert "[ERROR]" not in result.stderr


def test_orchestrate_queue_go_advises_but_queue_list_stays_quiet(
        sandbox_bin, tmp_path, git_repo):
    env = _child_env(sandbox_bin, tmp_path)
    went = subprocess.run([sys.executable, str(ORCHESTRATE), "queue", "go"],
                          cwd=git_repo, capture_output=True, text=True, env=env)
    assert went.returncode == 0, went.stderr
    assert went.stderr.startswith("[NOTE] orchestrate queue:")
    assert "Queue is empty" in went.stdout, "it got past the old gate and did the work"

    listed = subprocess.run([sys.executable, str(ORCHESTRATE), "queue", "list"],
                            cwd=git_repo, capture_output=True, text=True, env=env)
    assert listed.returncode == 0, listed.stderr
    assert "[NOTE]" not in listed.stderr, "bookkeeping verbs stay quiet"


def test_orchestrate_read_only_commands_say_nothing_about_gh(sandbox_bin, tmp_path, git_repo):
    result = subprocess.run([sys.executable, str(ORCHESTRATE), "runs", "--limit", "1"],
                            cwd=git_repo, capture_output=True, text=True,
                            env=_child_env(sandbox_bin, tmp_path))
    assert result.returncode == 0, result.stderr
    assert "[NOTE]" not in result.stderr


# ── /rig:setup (scripts/install.sh) ──────────────────────────────────────────

def _fake_pipx(sandbox_bin):
    """A stand-in `pipx` so an install method is detected. `--check` only runs
    `command -v pipx`, so this is never executed."""
    path = sandbox_bin / "pipx"
    path.write_text("#!/bin/bash\necho 'pipx 1.0.0-test'\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _fake_rig_wb(sandbox_bin):
    """A stand-in `rig-wb` so install.sh takes its 'already installed' path and
    never runs a real pip/pipx install during a test."""
    path = sandbox_bin / "rig-wb"
    path.write_text("#!/bin/bash\necho 'rig-wb 9.9.9-test'\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_install_check_reports_a_missing_extension_and_installs_nothing(
        sandbox_bin, tmp_path, fake_gh):
    """--check detects only. Its exit code grades the *install method*, not gh:
    the sandbox has no pip/pipx/uv, and that — not the extension — is the 1."""
    fake_gh(extension=False)
    log = tmp_path / "gh.log"
    result = subprocess.run(["bash", str(INSTALL_SH), "--check"],
                            cwd=tmp_path, capture_output=True, text=True,
                            env=_child_env(sandbox_bin, tmp_path, log=log))
    assert "gh-stack:   NOT INSTALLED" in result.stdout
    assert "gh extension install github/gh-stack" in result.stdout
    assert "extension install" not in log.read_text(encoding="utf-8"), \
        "--check must detect only, never install"
    assert "install method: none" in result.stdout
    assert result.returncode == 1, result.stdout + result.stderr


def test_install_check_exit_code_ignores_gh_entirely(sandbox_bin, tmp_path, fake_gh):
    """With an install method available, --check exits 0 whether or not gh is there.

    The install method has to be present for this to discriminate: with none, both
    cases exit 1 for that reason and the assertion would hold no matter what gh
    did. gh-missing alone used to make this a 1.
    """
    _fake_pipx(sandbox_bin)
    fake_gh()
    with_gh = subprocess.run(["bash", str(INSTALL_SH), "--check"],
                             cwd=tmp_path, capture_output=True, text=True,
                             env=_child_env(sandbox_bin, tmp_path))
    assert with_gh.returncode == 0, with_gh.stdout + with_gh.stderr

    (sandbox_bin / "gh").unlink()
    without_gh = subprocess.run(["bash", str(INSTALL_SH), "--check"],
                                cwd=tmp_path, capture_output=True, text=True,
                                env=_child_env(sandbox_bin, tmp_path))
    assert "install method: pipx" in without_gh.stdout
    assert "gh:         NOT INSTALLED" in without_gh.stdout
    assert without_gh.returncode == 0, without_gh.stdout + without_gh.stderr


def test_install_check_passes_when_the_requirement_is_met(sandbox_bin, tmp_path, fake_gh):
    fake_gh()
    result = subprocess.run(["bash", str(INSTALL_SH), "--check"],
                            cwd=tmp_path, capture_output=True, text=True,
                            env=_child_env(sandbox_bin, tmp_path))
    assert "gh:         2.88.0" in result.stdout
    assert "gh-stack:   v0.1.0" in result.stdout
    assert "auth:       authenticated" in result.stdout
    assert "NOT INSTALLED" not in result.stdout


def test_install_reports_but_does_not_fail_on_a_missing_login(
        sandbox_bin, tmp_path, fake_gh):
    """An unauthenticated gh with the extension is a ready environment."""
    fake_gh(authed=False)
    _fake_rig_wb(sandbox_bin)
    log = tmp_path / "gh.log"
    result = subprocess.run(["bash", str(INSTALL_SH), "--check"],
                            cwd=tmp_path, capture_output=True, text=True,
                            env=_child_env(sandbox_bin, tmp_path, log=log))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "auth:       not authenticated (only needed for push/submit/sync)" in result.stdout
    assert "NOT INSTALLED" not in result.stdout
    assert "fix:" not in result.stdout
    assert "optional" in result.stdout.lower(), "the section must not read as a requirement"
    assert "auth login" not in log.read_text(encoding="utf-8"), \
        "the installer must never attempt to authenticate"


def test_install_yes_installs_the_extension_without_prompting(
        sandbox_bin, tmp_path, fake_gh):
    fake_gh(extension=False)
    _fake_rig_wb(sandbox_bin)
    log = tmp_path / "gh.log"
    result = subprocess.run(["bash", str(INSTALL_SH), "--yes"],
                            cwd=tmp_path, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                            env=_child_env(sandbox_bin, tmp_path, log=log))
    gh_calls = log.read_text(encoding="utf-8")
    assert "extension install github/gh-stack" in gh_calls
    # Re-detection after the install must see the extension, so the installer
    # reports a ready environment. Includes the gh call log on failure.
    assert result.returncode == 0, result.stdout + result.stderr + f"\ngh calls:\n{gh_calls}"


def test_install_prompts_before_installing_the_extension_and_a_no_is_not_fatal(
        sandbox_bin, tmp_path, fake_gh):
    """Declining an optional extra must skip it and carry on with the install."""
    fake_gh(extension=False)
    _fake_rig_wb(sandbox_bin)
    log = tmp_path / "gh.log"
    result = subprocess.run(["bash", str(INSTALL_SH)], input="n\n",
                            cwd=tmp_path, capture_output=True, text=True,
                            env=_child_env(sandbox_bin, tmp_path, log=log))
    assert "gh extension install github/gh-stack" in result.stdout
    assert "Skipped gh-stack" in result.stdout
    assert "Aborted." not in result.stdout
    assert result.returncode == 0, result.stdout + result.stderr
    assert "extension install" not in log.read_text(encoding="utf-8")


def test_install_force_reinstalls_an_already_present_extension(sandbox_bin, tmp_path, fake_gh):
    """--force reinstalls gh-stack even when it is already there.

    The sandbox has no python/pipx/uv, so install.sh stops at 'no install method'
    (exit 1) right after the gh section — which is exactly the part under test.
    """
    fake_gh()
    log = tmp_path / "gh.log"
    result = subprocess.run(["bash", str(INSTALL_SH), "--force", "--yes"],
                            cwd=tmp_path, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                            env=_child_env(sandbox_bin, tmp_path, log=log))
    assert "extension install --force github/gh-stack" in log.read_text(encoding="utf-8")
    assert result.returncode == 1
    assert "None of pip / pipx / uv found" in result.stderr


def test_install_reports_a_missing_gh_without_refusing_to_install(sandbox_bin, tmp_path):
    """No gh at all is a supported way to run rig: report it, install rig anyway."""
    _fake_rig_wb(sandbox_bin)
    result = subprocess.run(["bash", str(INSTALL_SH), "--yes"],
                            cwd=tmp_path, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                            env=_child_env(sandbox_bin, tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "gh:         NOT INSTALLED" in result.stdout
    assert "brew install gh" in result.stdout, "still says how to get it"
    assert "rig runs without it" in result.stdout
    assert "[ERROR]" not in result.stderr


def test_install_reports_the_same_whether_or_not_the_advisory_is_silenced(
        sandbox_bin, tmp_path):
    """RIG_SKIP_GH_CHECK silences the note inside rig runs. `/rig:setup` is a
    direct question about the environment, so it answers identically either way."""
    _fake_rig_wb(sandbox_bin)
    runs = {}
    for skip in (None, "1"):
        result = subprocess.run(["bash", str(INSTALL_SH), "--yes"],
                                cwd=tmp_path, capture_output=True, text=True,
                                stdin=subprocess.DEVNULL,
                                env=_child_env(sandbox_bin, tmp_path, skip=skip))
        runs[skip] = (result.returncode, result.stdout, result.stderr)
    assert runs[None] == runs["1"], "the silencer must not change the installer"
    assert runs["1"][0] == 0
    assert "gh:         NOT INSTALLED" in runs["1"][1]
