"""Tests for `rig-wb githooks` (rig_workbench/githooks.py) — issue #298.

All tests run against a scratch git repo under tmp_path. No LLM, no network:
only git, bash, and the filesystem. RIG_HOME is pinned by conftest.py, so the
shipped templates in <repo>/hooks/git/ resolve regardless of cwd.
"""

import os
import pathlib
import stat
import subprocess

import pytest

from rig_workbench import githooks

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# A fake credential built from AWS's documented example access key id
# (AKIAIOSFODNN7EXAMPLE) — matches the hook's pattern but is not a real secret.
FAKE_AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"


def _git(repo, *args, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=e)


@pytest.fixture
def scratch_repo(tmp_path):
    """Fresh git repo with identity configured and one initial commit."""
    repo = tmp_path / "scratch"
    repo.mkdir()
    assert _git(repo, "init", "-q").returncode == 0
    _git(repo, "config", "user.email", "hooks@example.invalid")
    _git(repo, "config", "user.name", "rig hooks test")
    (repo / "README.md").write_text("scratch\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    assert _git(repo, "commit", "-q", "-m", "init").returncode == 0
    return repo


def _hooks_dir(repo):
    return repo / ".git" / "hooks"


# ── install ──────────────────────────────────────────────────────────────


def test_install_creates_executable_signed_hooks(scratch_repo):
    rc = githooks.install(scratch_repo)
    assert rc == 0
    for name in githooks.HOOK_NAMES:
        hook = _hooks_dir(scratch_repo) / name
        assert hook.is_file()
        assert hook.stat().st_mode & stat.S_IXUSR, f"{name} not executable"
        head = "\n".join(hook.read_text(encoding="utf-8").splitlines()[:5])
        assert githooks.SIGNATURE in head, f"{name} missing signature line"
        assert githooks.is_rig_hook(hook)


def test_install_is_idempotent_over_rig_hooks(scratch_repo):
    assert githooks.install(scratch_repo) == 0
    # Re-install without --force refreshes rig-managed hooks in place.
    assert githooks.install(scratch_repo) == 0
    for name in githooks.HOOK_NAMES:
        assert githooks.is_rig_hook(_hooks_dir(scratch_repo) / name)


def test_install_refuses_foreign_hook_without_force(scratch_repo):
    foreign = _hooks_dir(scratch_repo) / "pre-commit"
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text("#!/bin/sh\necho project-owned hook\n", encoding="utf-8")
    rc = githooks.install(scratch_repo)
    assert rc == 1
    # Foreign hook untouched; the non-conflicting hook still got installed.
    assert foreign.read_text(encoding="utf-8") == "#!/bin/sh\necho project-owned hook\n"
    assert githooks.is_rig_hook(_hooks_dir(scratch_repo) / "pre-push")


def test_install_force_overwrites_foreign_hook(scratch_repo):
    foreign = _hooks_dir(scratch_repo) / "pre-commit"
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text("#!/bin/sh\necho project-owned hook\n", encoding="utf-8")
    rc = githooks.install(scratch_repo, force=True)
    assert rc == 0
    assert githooks.is_rig_hook(foreign)


def test_install_outside_git_repo_fails(tmp_path):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    assert githooks.cmd_githooks(["install", "--repo", str(plain)]) == 1


# ── uninstall ────────────────────────────────────────────────────────────


def test_uninstall_removes_only_rig_hooks(scratch_repo):
    assert githooks.install(scratch_repo) == 0
    # Replace pre-push with a project-owned hook after install.
    foreign_body = "#!/bin/sh\necho mine\n"
    (_hooks_dir(scratch_repo) / "pre-push").write_text(foreign_body, encoding="utf-8")
    rc = githooks.uninstall(scratch_repo)
    assert rc == 0
    assert not (_hooks_dir(scratch_repo) / "pre-commit").exists()
    assert (_hooks_dir(scratch_repo) / "pre-push").read_text(encoding="utf-8") == foreign_body


def test_uninstall_on_clean_repo_is_a_noop(scratch_repo):
    assert githooks.uninstall(scratch_repo) == 0
    for name in githooks.HOOK_NAMES:
        assert not (_hooks_dir(scratch_repo) / name).exists()


# ── status ───────────────────────────────────────────────────────────────


def test_status_reflects_hook_states(scratch_repo, capsys):
    assert githooks.status(scratch_repo) == 1  # nothing installed yet
    assert githooks.install(scratch_repo) == 0
    capsys.readouterr()
    assert githooks.status(scratch_repo) == 0
    out = capsys.readouterr().out
    assert "pre-commit" in out and "pre-push" in out
    assert "rig-managed" in out


def test_hook_state_classification(scratch_repo):
    hooks = _hooks_dir(scratch_repo)
    hooks.mkdir(parents=True, exist_ok=True)
    absent = hooks / "pre-commit"
    assert githooks.hook_state(absent) == "absent"
    absent.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    assert githooks.hook_state(absent) == "foreign"
    githooks.install(scratch_repo, force=True)
    assert githooks.hook_state(absent) == "rig"


# ── CLI dispatch ─────────────────────────────────────────────────────────


def test_cli_dispatch_via_rig_wb(scratch_repo):
    env = dict(os.environ, RIG_HOME=str(REPO_ROOT), PYTHONPATH=str(REPO_ROOT))
    r = subprocess.run(
        ["python3", "-m", "rig_workbench.cli", "githooks", "install"],
        cwd=scratch_repo, capture_output=True, text=True, env=env, timeout=60,
    )
    assert r.returncode == 0, r.stderr
    assert githooks.is_rig_hook(_hooks_dir(scratch_repo) / "pre-commit")
    r = subprocess.run(
        ["python3", "-m", "rig_workbench.cli", "githooks", "status"],
        cwd=scratch_repo, capture_output=True, text=True, env=env, timeout=60,
    )
    assert r.returncode == 0, r.stderr


def test_cli_unknown_action_exits_2(scratch_repo):
    assert githooks.cmd_githooks(["frobnicate", "--repo", str(scratch_repo)]) == 2


# ── hook behavior (bash + git only; no LLM, no network) ──────────────────


def test_pre_commit_noop_without_manifest(scratch_repo):
    githooks.install(scratch_repo)
    (scratch_repo / "a.txt").write_text("hello\n", encoding="utf-8")
    _git(scratch_repo, "add", "a.txt")
    r = _git(scratch_repo, "commit", "-m", "no manifest")
    assert r.returncode == 0, r.stderr
    assert "no project manifest" in r.stderr


def _write_manifest(repo, lint='""', test='""', build='""'):
    d = repo / ".claude"
    d.mkdir(exist_ok=True)
    (d / "rig.md").write_text(
        "# rig manifest\n\n```yaml\n"
        f"build: {build}\n"
        f"lint:  {lint}\n"
        f"test:  {test}\n"
        "```\n",
        encoding="utf-8",
    )


def test_pre_commit_blocks_staged_secret(scratch_repo):
    githooks.install(scratch_repo)
    _write_manifest(scratch_repo)
    (scratch_repo / "config.py").write_text(
        f'ACCESS_KEY = "{FAKE_AWS_KEY}"\n', encoding="utf-8"
    )
    _git(scratch_repo, "add", ".")
    r = _git(scratch_repo, "commit", "-m", "leak")
    assert r.returncode != 0
    assert "secret-pattern match" in r.stderr


def test_pre_commit_blocks_pem_header(scratch_repo):
    githooks.install(scratch_repo)
    _write_manifest(scratch_repo)
    (scratch_repo / "key.pem").write_text(
        "-----BEGIN RSA PRIVATE KEY-----\nnotreal\n", encoding="utf-8"
    )
    _git(scratch_repo, "add", ".")
    r = _git(scratch_repo, "commit", "-m", "pem")
    assert r.returncode != 0


def test_pre_commit_skip_env_bypasses(scratch_repo):
    githooks.install(scratch_repo)
    _write_manifest(scratch_repo)
    (scratch_repo / "config.py").write_text(
        f'ACCESS_KEY = "{FAKE_AWS_KEY}"\n', encoding="utf-8"
    )
    _git(scratch_repo, "add", ".")
    r = _git(scratch_repo, "commit", "-m", "leak", env={"RIG_HOOK_SKIP": "1"})
    assert r.returncode == 0, r.stderr


def test_pre_commit_clean_commit_passes_and_runs_lint(scratch_repo):
    githooks.install(scratch_repo)
    _write_manifest(scratch_repo, lint='"echo LINT_RAN"')
    (scratch_repo / "clean.txt").write_text("nothing secret here\n", encoding="utf-8")
    _git(scratch_repo, "add", ".")
    r = _git(scratch_repo, "commit", "-m", "clean")
    assert r.returncode == 0, r.stderr
    assert "LINT_RAN" in r.stdout + r.stderr


def test_pre_commit_failing_lint_blocks(scratch_repo):
    githooks.install(scratch_repo)
    _write_manifest(scratch_repo, lint='"false"')
    (scratch_repo / "clean.txt").write_text("ok\n", encoding="utf-8")
    _git(scratch_repo, "add", ".")
    r = _git(scratch_repo, "commit", "-m", "lint fails")
    assert r.returncode != 0
    assert "lint FAILED" in r.stderr
    # Per-check skip lets it through.
    r = _git(scratch_repo, "commit", "-m", "lint skipped",
             env={"RIG_HOOK_SKIP_LINT": "1"})
    assert r.returncode == 0, r.stderr


def test_pre_push_runs_test_command(scratch_repo, tmp_path):
    githooks.install(scratch_repo)
    marker = tmp_path / "test_ran.marker"
    _write_manifest(scratch_repo, test=f'"touch {marker}"')
    _git(scratch_repo, "add", ".")
    assert _git(scratch_repo, "commit", "-m", "manifest").returncode == 0
    # Bare remote so `git push` triggers pre-push without any network.
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    _git(scratch_repo, "remote", "add", "origin", str(remote))
    r = _git(scratch_repo, "push", "origin", "HEAD")
    assert r.returncode == 0, r.stderr
    assert marker.exists(), "pre-push did not run the manifest test command"


def test_pre_push_failing_test_blocks_push(scratch_repo, tmp_path):
    githooks.install(scratch_repo)
    _write_manifest(scratch_repo, test='"false"')
    _git(scratch_repo, "add", ".")
    assert _git(scratch_repo, "commit", "-m", "manifest").returncode == 0
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    _git(scratch_repo, "remote", "add", "origin", str(remote))
    r = _git(scratch_repo, "push", "origin", "HEAD")
    assert r.returncode != 0
    assert "test FAILED" in r.stderr
    # Whole-hook skip lets the push through.
    r = _git(scratch_repo, "push", "origin", "HEAD", env={"RIG_HOOK_SKIP": "1"})
    assert r.returncode == 0, r.stderr
