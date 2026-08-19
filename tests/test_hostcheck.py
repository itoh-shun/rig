import json
import os
import pathlib
import subprocess

import pytest

from rig_workbench import gh_requirement, hostcheck

GH_REMOTE = "origin\tgit@github.com:example/repo.git (fetch)\n"

# Captured before the autouse fixture below stubs it out, for the one test that
# exercises the real probe's plumbing rather than a verdict.
_REAL_IMPORT_PROBE = hostcheck.installed_import_probe
_REAL_GIT_REMOTES = hostcheck.git_remotes


def _repo(tmp_path: pathlib.Path) -> pathlib.Path:
    (tmp_path / ".claude").mkdir()
    return tmp_path


def _gh(output: str, *, installed: bool = True, returncode: int = 0) -> dict:
    return {"installed": installed, "returncode": returncode, "output": output}


@pytest.fixture(autouse=True)
def _no_host_probes(monkeypatch):
    """Keep `run_all` off the host by default.

    Two of the checks shell out (`gh auth status`, an out-of-tree import). Left
    live, every `run_all` test would take seconds, hit the network, and answer
    differently on the machine running it. Tests that care about a probe inject
    it explicitly.
    """
    # A *successful* `git remote -v` that listed nothing: (rc, stdout, stderr).
    # The rc matters — a failed one is a different answer entirely, pinned below.
    monkeypatch.setattr(hostcheck, "git_remotes", lambda root: (0, "", ""))
    monkeypatch.setattr(
        hostcheck, "gh_auth_probe",
        lambda: pytest.fail("gh_auth_probe must not run: no GitHub remote in this fixture"))
    monkeypatch.setattr(
        hostcheck, "installed_import_probe",
        lambda modules=hostcheck.INSTALLED_IMPORT_MODULES: {
            "installed": False, "interpreter": None,
            "interpreter_source": "rig-wb not on PATH",
            "returncode": None, "payload": None, "stderr": ""})


def test_deny_rules_missing_when_settings_absent(tmp_path):
    result = hostcheck.check_deny_rules(_repo(tmp_path))
    assert result["ok"] is False
    assert result["sources"] == []
    assert "permissions.deny" in result["remedy"]


def test_deny_rules_found_and_counted(tmp_path):
    root = _repo(tmp_path)
    (root / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"deny": ["Bash(rm -rf:*)", "Bash(git push --force:*)"]}}),
        encoding="utf-8",
    )
    result = hostcheck.check_deny_rules(root)
    assert result["ok"] is True
    assert result["sources"] == [{"path": ".claude/settings.json", "rules": 2}]


def test_deny_rules_reject_empty_and_malformed(tmp_path):
    root = _repo(tmp_path)
    (root / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"deny": []}}), encoding="utf-8"
    )
    assert hostcheck.check_deny_rules(root)["ok"] is False

    (root / ".claude" / "settings.local.json").write_text("{not json", encoding="utf-8")
    assert hostcheck.check_deny_rules(root)["ok"] is False


def test_state_ignored_accepts_the_usual_spellings(tmp_path):
    root = _repo(tmp_path)
    for pattern in (".rig/", "/.rig", ".rig", ".rig/runs/"):
        (root / ".gitignore").write_text(f"node_modules/\n{pattern}\n", encoding="utf-8")
        assert hostcheck.check_state_ignored(root)["ok"] is True, pattern


def test_state_ignored_is_not_fooled_by_a_similar_prefix(tmp_path):
    root = _repo(tmp_path)
    (root / ".gitignore").write_text(".rigging/\n", encoding="utf-8")
    assert hostcheck.check_state_ignored(root)["ok"] is False


def test_isolation_reports_declared_devcontainer_config(tmp_path):
    root = _repo(tmp_path)
    (root / ".devcontainer").mkdir()
    (root / ".devcontainer" / "devcontainer.json").write_text("{}", encoding="utf-8")
    result = hostcheck.check_isolation(root)
    assert result["declared_config"] == [".devcontainer/devcontainer.json"]


def test_isolation_signal_comes_from_the_environment_not_the_config(tmp_path):
    root = _repo(tmp_path)
    assert hostcheck.check_isolation(root, env={}, signals=[])["ok"] is False

    result = hostcheck.check_isolation(root, env={"REMOTE_CONTAINERS": "true"}, signals=[])
    assert result["ok"] is True
    assert "env:REMOTE_CONTAINERS" in result["signals"]


def test_run_all_collects_missing_ids(tmp_path):
    result = hostcheck.run_all(_repo(tmp_path))
    assert set(result["missing"]) >= {"deny_rules", "state_ignored"}
    assert result["ok"] is False
    assert [check["id"] for check in result["checks"]] == [
        "process_isolation",
        "deny_rules",
        "state_ignored",
        "umask",
        "gh_auth_scopes",
        "installed_import",
    ]


def test_run_all_separates_not_applicable_from_satisfied(tmp_path):
    """A skipped check is reported as skipped, not folded into "all present"."""
    result = hostcheck.run_all(_repo(tmp_path))
    assert set(result["skipped"]) == {"gh_auth_scopes", "installed_import"}
    for check in result["checks"]:
        if check["id"] in result["skipped"]:
            assert check["ok"] is True and check["applicable"] is False


def test_exit_code_is_advisory_unless_strict(tmp_path, capsys):
    assert hostcheck.cmd_hostcheck(["--repo", str(tmp_path)]) == 3
    assert hostcheck.cmd_hostcheck(["--repo", str(tmp_path), "--strict"]) == 1
    capsys.readouterr()


def test_json_output_is_parseable(tmp_path, capsys):
    hostcheck.cmd_hostcheck(["--repo", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["root"] == str(tmp_path.resolve())
    assert len(payload["checks"]) == len(hostcheck.CHECKS)


# ── fixed-corpus measurement (--bench) ──────────────────────────────────


def test_bench_detects_every_positive_and_flags_no_negative():
    """The corpus is the check's own yardstick: recall 100%, false positives 0."""
    result = hostcheck.run_bench()
    assert result["ok"] is True
    assert result["overall"]["detected"] == result["overall"]["positives"]
    assert result["overall"]["false_positives"] == 0


def test_bench_includes_a_declared_but_unused_container_config():
    """A committed devcontainer.json says the team intended isolation, not that it has it."""
    labels = {case[0] for case in hostcheck.ISOLATION_CORPUS}
    assert "declared_but_not_running" in labels
    case = next(c for c in hostcheck.ISOLATION_CORPUS if c[0] == "declared_but_not_running")
    assert case[2] is False


def test_bench_negatives_outnumber_nothing_and_cover_near_misses():
    for name, (_check, corpus) in hostcheck.BENCH_CORPORA.items():
        negatives = [case for case in corpus if not case[2]]
        assert negatives, f"{name} has no negative cases — recall alone proves little"


def test_bench_is_independent_of_the_host_running_it(monkeypatch):
    """Every isolation case supplies its own signals, so the result cannot drift by machine."""
    monkeypatch.setattr(hostcheck, "host_signals", lambda: ["/.dockerenv", "cgroup:docker"])
    assert hostcheck.run_bench()["ok"] is True
    monkeypatch.setattr(hostcheck, "host_signals", list)
    assert hostcheck.run_bench()["ok"] is True


def test_bench_never_reaches_the_network_or_the_installed_package(monkeypatch):
    """Make every host probe fatal: a corpus that still passes never consulted the host."""

    def explode(*args, **kwargs):
        raise AssertionError("--bench must inject every probe, not run it")

    monkeypatch.setattr(hostcheck, "gh_auth_probe", explode)
    monkeypatch.setattr(hostcheck, "installed_import_probe", explode)
    monkeypatch.setattr(hostcheck, "git_remotes", explode)
    monkeypatch.setattr(hostcheck, "_run", explode)
    assert hostcheck.run_bench()["ok"] is True


def test_bench_pins_the_failure_that_actually_shipped():
    """`ModuleNotFoundError: rig_workbench.workbench` from an installed wheel."""
    case = next(c for c in hostcheck.INSTALLED_IMPORT_CORPUS
                if c[0] == "wheel_missing_subpackage")
    assert case[2] is False
    assert "rig_workbench.workbench" in case[1]["probe"]["payload"]["errors"]


def test_bench_pins_a_token_whose_scopes_cannot_be_read():
    """Authenticated-but-unreadable is a negative: the corpus is what keeps it one."""
    case = next(c for c in hostcheck.GH_AUTH_CORPUS
                if c[0] == "fine_grained_token_hides_scopes")
    assert case[2] is False
    assert "Token scopes" not in case[1]["gh"]["output"]


def test_isolation_accepts_injected_environment_and_signals(tmp_path):
    assert hostcheck.check_isolation(tmp_path, env={}, signals=[])["ok"] is False
    assert hostcheck.check_isolation(tmp_path, env={}, signals=["/.dockerenv"])["ok"] is True
    assert hostcheck.check_isolation(tmp_path, env={"CODESPACES": "1"}, signals=[])["ok"] is True


# ── gh authentication and token scopes ──────────────────────────────────


def test_gh_scopes_ok_when_the_token_carries_what_rig_writes_with(tmp_path):
    result = hostcheck.check_gh_auth_scopes(
        tmp_path, remotes=GH_REMOTE,
        probe=_gh("  ✓ Logged in to github.com account tester (keyring)\n"
                  "  - Token scopes: 'gist', 'read:org', 'repo', 'workflow'"))
    assert result["ok"] is True
    assert result["state"] == "ok"
    assert result["account"] == "tester"
    assert result["scopes"] == ["gist", "read:org", "repo", "workflow"]
    assert result["missing_scopes"] == []


def test_gh_scopes_miss_names_the_scope_and_the_command_that_grants_it(tmp_path):
    result = hostcheck.check_gh_auth_scopes(
        tmp_path, remotes=GH_REMOTE,
        probe=_gh("  - Token scopes: 'gist', 'read:org'"))
    assert result["ok"] is False
    assert result["state"] == "scopes-missing"
    assert result["missing_scopes"] == ["repo"]
    assert "gh auth refresh" in result["remedy"] and "repo" in result["remedy"]


def test_gh_scopes_does_not_accept_public_repo_in_place_of_repo(tmp_path):
    """`public_repo` opens PRs on public repos and fails on the first private one."""
    result = hostcheck.check_gh_auth_scopes(
        tmp_path, remotes=GH_REMOTE, probe=_gh("  - Token scopes: 'public_repo'"))
    assert result["ok"] is False
    assert result["missing_scopes"] == ["repo"]


def test_gh_scopes_treats_repo_as_granting_its_narrower_siblings(tmp_path):
    result = hostcheck.check_gh_auth_scopes(
        tmp_path, remotes=GH_REMOTE, probe=_gh("  - Token scopes: 'repo'"),
        required=("repo", "public_repo", "repo:status"))
    assert result["ok"] is True


def test_gh_unreadable_scopes_are_not_verified_and_must_not_pass(tmp_path):
    """A fine-grained PAT prints no scope line. Unverifiable is MISS, never OK."""
    result = hostcheck.check_gh_auth_scopes(
        tmp_path, remotes=GH_REMOTE,
        probe=_gh("  ✓ Logged in to github.com account tester (GH_TOKEN)\n"
                  "  - Token: github_pat_***"))
    assert result["ok"] is False
    assert result["state"] == "scopes-unknown"
    assert result["account"] == "tester"
    assert "cannot read scopes" in result["remedy"]


def test_gh_distinguishes_missing_binary_from_missing_login(tmp_path):
    absent = hostcheck.check_gh_auth_scopes(
        tmp_path, remotes=GH_REMOTE, probe=_gh("", installed=False, returncode=127))
    assert absent["ok"] is False
    assert absent["state"] == "gh-missing"
    assert "Install the GitHub CLI" in absent["remedy"]

    logged_out = hostcheck.check_gh_auth_scopes(
        tmp_path, remotes=GH_REMOTE,
        probe=_gh("You are not logged into any GitHub hosts.", returncode=1))
    assert logged_out["ok"] is False
    assert logged_out["state"] == "not-authenticated"
    assert logged_out["remedy"] == "gh auth login"


def test_gh_check_is_skipped_when_the_repo_has_no_github_remote(tmp_path):
    """Not applicable is its own state — `ok` alone would read as "checked and fine"."""
    result = hostcheck.check_gh_auth_scopes(
        tmp_path, remotes="origin\tgit@gitlab.com:example/repo.git (fetch)\n")
    assert result["state"] == "no-github-remote"
    assert result["applicable"] is False


def test_github_remote_is_matched_on_the_host_not_the_line():
    """A GitLab repo named `github-tools` must not turn the gh check on."""
    assert hostcheck.has_github_remote("origin\tgit@github.com:a/b.git (fetch)") is True
    assert hostcheck.has_github_remote("origin\thttps://github.com/a/b.git (push)") is True
    # GitHub Enterprise hosts are usually named github.<company>; `gh` works there too.
    assert hostcheck.has_github_remote(
        "origin\tssh://git@github.enterprise.example/a/b.git (fetch)") is True
    assert hostcheck.has_github_remote("origin\tgit@github.corp.net:a/b.git (fetch)") is True
    assert hostcheck.has_github_remote("origin\tgit@bitbucket.org:a/b.git (fetch)") is False
    assert hostcheck.has_github_remote(
        "origin\tgit@gitlab.com:a/github-tools.git (fetch)") is False
    assert hostcheck.has_github_remote("") is False


def test_gh_scope_parsing_separates_none_from_not_reported():
    assert hostcheck.parse_token_scopes("  - Token scopes: none") == []
    assert hostcheck.parse_token_scopes("  - Token: gho_x") is None
    assert hostcheck.parse_token_scopes("  - Token scopes: 'repo', 'workflow'") == [
        "repo", "workflow"]


# ── one host's scopes are not another's ─────────────────────────────────

_MULTI_HOST_STATUS = """github.com
  ✓ Logged in to github.com account tester (keyring)
  - Active account: true
  - Token scopes: 'gist', 'read:org'

ghe.example.com
  ✓ Logged in to ghe.example.com account admin (keyring)
  - Token scopes: 'repo', 'workflow'"""


def test_scopes_are_read_from_the_remote_host_block_not_unioned(tmp_path):
    """A GHE token with `repo` must not satisfy a github.com remote that lacks it."""
    result = hostcheck.check_gh_auth_scopes(
        tmp_path, remotes=GH_REMOTE, probe=_gh(_MULTI_HOST_STATUS))
    assert result["ok"] is False
    assert result["state"] == "scopes-missing"
    assert result["scopes"] == ["gist", "read:org"]
    assert result["missing_scopes"] == ["repo"]
    # And the account is the one on this host, not whoever is logged in elsewhere.
    assert result["account"] == "tester"


def test_the_remote_host_decides_which_block_is_read(tmp_path):
    """Same output, a GHE remote: now the GHE block is the one that counts."""
    result = hostcheck.check_gh_auth_scopes(
        tmp_path, remotes="origin\tgit@ghe.example.com:a/b.git (fetch)\n",
        probe=_gh(_MULTI_HOST_STATUS))
    assert result["state"] == "no-github-remote"

    # ...and with a github.<company> GHE remote, which `has_github_remote` accepts:
    ghe = _MULTI_HOST_STATUS.replace("ghe.example.com", "github.example.com")
    result = hostcheck.check_gh_auth_scopes(
        tmp_path, remotes="origin\tgit@github.example.com:a/b.git (fetch)\n", probe=_gh(ghe))
    assert result["ok"] is True
    assert result["scopes"] == ["repo", "workflow"]
    assert result["account"] == "admin"
    assert "github.example.com" in result["detail"][0]


def test_being_logged_in_somewhere_else_is_not_being_logged_in_here(tmp_path):
    result = hostcheck.check_gh_auth_scopes(
        tmp_path, remotes=GH_REMOTE,
        probe=_gh("ghe.example.com\n  ✓ Logged in to ghe.example.com account admin\n"
                  "  - Token scopes: 'repo'"))
    assert result["ok"] is False
    assert result["state"] == "not-authenticated"
    assert result["remedy"] == "gh auth login -h github.com"


def test_the_refresh_remedy_names_the_host_it_has_to_refresh(tmp_path):
    """`gh auth refresh -h github.com` on a GHE remote refreshes the wrong token."""
    result = hostcheck.check_gh_auth_scopes(
        tmp_path, remotes="origin\tgit@github.corp.net:a/b.git (fetch)\n",
        probe=_gh("github.corp.net\n  - Token scopes: 'gist'"))
    assert result["remedy"] == "gh auth refresh -h github.corp.net -s repo"


def test_scope_parsing_is_scoped_to_a_host_when_asked_and_whole_when_not():
    assert hostcheck.parse_token_scopes(_MULTI_HOST_STATUS, "github.com") == [
        "gist", "read:org"]
    assert hostcheck.parse_token_scopes(_MULTI_HOST_STATUS, "ghe.example.com") == [
        "repo", "workflow"]
    # A host `gh` said nothing about: no scope line for it, which is not `[]`.
    assert hostcheck.parse_token_scopes(_MULTI_HOST_STATUS, "other.example.com") is None
    # Headerless output describes one token and nothing to attribute it to but the
    # host being asked about — the deliberate fallback, not an accident.
    assert hostcheck.parse_token_scopes("  - Token scopes: 'repo'", "github.com") == ["repo"]
    assert hostcheck.gh_hosts(_MULTI_HOST_STATUS) == ["github.com", "ghe.example.com"]
    assert hostcheck.gh_hosts("  - Token scopes: 'repo'") == []


_MULTI_ACCOUNT_STATUS = """github.com
  ✓ Logged in to github.com account tester (keyring)
  - Active account: true
  - Token scopes: 'gist', 'read:org'

  ✓ Logged in to github.com account robot (GITHUB_TOKEN)
  - Active account: false
  - Token scopes: 'repo', 'workflow'"""


def test_an_inactive_accounts_scopes_are_not_this_runs_scopes(tmp_path):
    """One host, two accounts: only the active one signs what the run pushes."""
    result = hostcheck.check_gh_auth_scopes(
        tmp_path, remotes=GH_REMOTE, probe=_gh(_MULTI_ACCOUNT_STATUS))
    assert result["ok"] is False
    assert result["scopes"] == ["gist", "read:org"]
    assert result["missing_scopes"] == ["repo"]
    assert result["account"] == "tester"


def test_the_active_account_is_found_wherever_gh_prints_it(tmp_path):
    """Order is not the marker — `Active account: true` is."""
    swapped = """github.com
  ✓ Logged in to github.com account robot (GITHUB_TOKEN)
  - Active account: false
  - Token scopes: 'gist'

  ✓ Logged in to github.com account tester (keyring)
  - Active account: true
  - Token scopes: 'repo'"""
    result = hostcheck.check_gh_auth_scopes(tmp_path, remotes=GH_REMOTE, probe=_gh(swapped))
    assert result["ok"] is True
    assert result["scopes"] == ["repo"]
    assert result["account"] == "tester"


def test_a_single_account_block_is_read_whole_however_it_is_marked(tmp_path):
    """`Active account` is absent from older `gh` and from short captures."""
    for marker in ("", "  - Active account: true\n"):
        result = hostcheck.check_gh_auth_scopes(
            tmp_path, remotes=GH_REMOTE,
            probe=_gh("github.com\n  ✓ Logged in to github.com account tester (keyring)\n"
                      f"{marker}  - Token scopes: 'repo'"))
        assert result["ok"] is True, repr(marker)
        assert result["scopes"] == ["repo"]


def test_a_stray_unindented_word_does_not_open_a_host_block():
    """`error:` is not a host. Reading it as one would hide the block after it."""
    output = "error:\ngithub.com\n  - Token scopes: 'repo'"
    assert hostcheck.gh_hosts(output) == ["github.com"]
    assert hostcheck.parse_token_scopes(output, "github.com") == ["repo"]
    assert hostcheck.gh_hosts(
        "You are not logged into any GitHub hosts. To log in, run: gh auth login") == []


# ── a probe that could not run is not a probe that found nothing ────────


def test_unreadable_remotes_are_not_reported_as_having_no_github_remote(monkeypatch, tmp_path):
    """`git remote -v` exiting 128 with empty stdout is "we did not look".

    Run against the real `git`, in a directory that is not a repository: the
    failure mode is the one an operator meets, not one this test invented.
    """
    monkeypatch.setattr(hostcheck, "git_remotes", _REAL_GIT_REMOTES)
    rc, out, err = hostcheck.git_remotes(tmp_path)
    assert (rc, out) != (0, "") and err, "expected git to fail outside a repository"

    result = hostcheck.check_gh_auth_scopes(tmp_path)
    assert result["state"] == "remotes-unknown"
    assert result["ok"] is False
    # Not `applicable: false` — that would say the subject does not exist here,
    # and whether it exists is exactly what could not be established.
    assert result["applicable"] is True
    assert "not a git repository" in " ".join(result["detail"]).lower()


def test_a_timed_out_probe_is_not_diagnosed_as_a_logout(tmp_path):
    """`gh auth login` is the wrong remedy for a network that did not answer."""
    result = hostcheck.check_gh_auth_scopes(
        tmp_path, remotes=GH_REMOTE,
        probe=_gh("gh auth status timed out after 5s", returncode=124))
    assert result["ok"] is False
    assert result["state"] == "probe-timed-out"
    assert "gh auth login" not in result["remedy"]


def test_required_scopes_stay_derived_from_what_rig_actually_runs():
    """`gh project` appears nowhere in rig, so `read:project` must not be required."""
    assert hostcheck.GH_REQUIRED_SCOPES == ("repo",)


# ── the installed rig_workbench, imported from outside the checkout ─────


def _import_probe(errors: dict | None = None, **overrides) -> dict:
    probe = {
        "installed": True, "returncode": 0, "stderr": "",
        "interpreter": "/opt/venv/bin/python",
        "interpreter_source": "shebang of /usr/local/bin/rig-wb",
        "payload": {"package": "/opt/venv/lib/python3.13/site-packages/rig_workbench/__init__.py",
                    "errors": errors or {}},
    }
    probe.update(overrides)
    return probe


def test_installed_import_ok_names_the_interpreter_it_used(tmp_path):
    result = hostcheck.check_installed_import(tmp_path, probe=_import_probe())
    assert result["ok"] is True
    assert result["state"] == "ok"
    assert result["interpreter"] == "/opt/venv/bin/python"
    assert any("/opt/venv/bin/python" in line for line in result["detail"])


def test_installed_import_catches_the_wheel_that_dropped_a_subpackage(tmp_path):
    result = hostcheck.check_installed_import(tmp_path, probe=_import_probe(
        {"rig_workbench.workbench":
         "ModuleNotFoundError: No module named 'rig_workbench.workbench'"}))
    assert result["ok"] is False
    assert result["state"] == "import-failed"
    assert result["failed_modules"] == ["rig_workbench.workbench"]
    assert "reinstall" in result["remedy"].lower()


def test_installed_import_reports_an_unusable_probe_instead_of_passing(tmp_path):
    unresolved = hostcheck.check_installed_import(tmp_path, probe=_import_probe(
        interpreter=None, payload=None,
        interpreter_source="/usr/local/bin/rig-wb has no shebang"))
    assert unresolved["ok"] is False
    assert unresolved["state"] == "interpreter-unknown"

    silent = hostcheck.check_installed_import(
        tmp_path, probe=_import_probe(returncode=1, payload=None, stderr="Segmentation fault"))
    assert silent["ok"] is False
    assert silent["state"] == "probe-failed"


def test_installed_import_is_skipped_when_nothing_is_installed(tmp_path):
    result = hostcheck.check_installed_import(
        tmp_path, probe={"installed": False, "interpreter": None,
                         "interpreter_source": "rig-wb not on PATH",
                         "returncode": None, "payload": None, "stderr": ""})
    assert result["state"] == "not-installed"
    assert result["applicable"] is False


def test_editable_install_of_this_checkout_is_not_verified_and_must_not_pass(tmp_path):
    """An editable install cannot reproduce a packaging omission, so it proves nothing.

    This used to be `ok: True` with the caveat in the prose — the check reported a
    green for an axis it had not exercised. Unverifiable is MISS, the same answer
    `scopes-unknown` gives a token whose scopes cannot be read.
    """
    package = tmp_path / "rig_workbench" / "__init__.py"
    package.parent.mkdir(parents=True)
    package.write_text("", encoding="utf-8")
    result = hostcheck.check_installed_import(
        tmp_path, probe=_import_probe(payload={"package": str(package), "errors": {}}))
    assert result["ok"] is False
    assert result["applicable"] is True
    assert result["state"] == "editable-install"
    assert any("this checkout" in line for line in result["detail"])


def test_editable_install_of_another_checkout_is_caught_too(tmp_path):
    """The question is "is this a built install", not "is this path under my root".

    An editable install pointing at some *other* source tree is exactly as unable
    to show a packaging omission, and the root-relative test used to let it pass
    without even the caveat.
    """
    result = hostcheck.check_installed_import(
        tmp_path, probe=_import_probe(
            payload={"package": "/home/tester/src/rig/rig_workbench/__init__.py", "errors": {}}))
    assert result["ok"] is False
    assert result["state"] == "editable-install"


def test_a_wheel_in_site_packages_still_passes(tmp_path):
    """The regression that matters in the other direction: normal installs stay green."""
    for install_dir in ("site-packages", "dist-packages"):
        result = hostcheck.check_installed_import(
            tmp_path, probe=_import_probe(payload={
                "package": f"/opt/venv/lib/python3.13/{install_dir}/rig_workbench/__init__.py",
                "errors": {}}))
        assert result["ok"] is True, install_dir
        assert result["state"] == "ok"


def test_an_import_that_names_no_file_is_not_a_pass(tmp_path):
    """`__file__` of None leaves no way to tell an install from a source tree."""
    result = hostcheck.check_installed_import(
        tmp_path, probe=_import_probe(payload={"package": None, "errors": {}}))
    assert result["ok"] is False
    assert result["state"] == "package-path-unknown"


def test_installed_import_probe_runs_outside_the_repo_without_pythonpath(monkeypatch, tmp_path):
    """The probe's whole value is where it runs: repo cwd + PYTHONPATH would fake a pass."""
    seen = {}

    def fake_run(argv, *, cwd=None, env=None, timeout=None):
        seen["argv"] = argv
        seen["cwd"] = cwd
        seen["env"] = env
        return 0, json.dumps({"package": "/opt/rig_workbench/__init__.py", "errors": {}}), ""

    monkeypatch.setattr(hostcheck, "_run", fake_run)
    monkeypatch.setattr(hostcheck, "_installed_interpreter",
                        lambda: ("/opt/venv/bin/python", "shebang"))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    result = _REAL_IMPORT_PROBE(("rig_workbench.workbench",))
    assert result["payload"]["errors"] == {}
    assert "PYTHONPATH" not in seen["env"]
    assert "PYTHONHOME" not in seen["env"]
    assert not str(seen["cwd"]).startswith(str(tmp_path))
    assert seen["argv"][0] == "/opt/venv/bin/python"
    assert seen["argv"][-1] == "rig_workbench.workbench"


def test_installed_interpreter_reads_the_console_script_shebang(tmp_path, monkeypatch):
    """`sys.executable` is not the answer: the pipx/uv venv python is."""
    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")
    script = tmp_path / "rig-wb"
    script.write_text(f"#!{venv_python}\nimport sys\n", encoding="utf-8")
    monkeypatch.setattr(hostcheck.shutil, "which", lambda name: str(script))
    assert hostcheck._installed_interpreter() == (str(venv_python), f"shebang of {script}")

    script.write_text("not a script\n", encoding="utf-8")
    interpreter, reason = hostcheck._installed_interpreter()
    assert interpreter is None
    assert "shebang" in reason


def test_env_style_shebang_resolves_instead_of_missing_forever(tmp_path, monkeypatch):
    """`#!/usr/bin/env python3` names an interpreter, not a path.

    Treating the bare name as a path made `os.path.exists("python3")` false and
    the check a permanent `interpreter-unknown` MISS — a check that can never pass
    on such an install is not measuring anything.
    """
    real_python = tmp_path / "bin" / "python3"
    real_python.parent.mkdir(parents=True)
    real_python.write_text("", encoding="utf-8")
    script = tmp_path / "rig-wb"
    script.write_text("#!/usr/bin/env python3\nimport sys\n", encoding="utf-8")
    monkeypatch.setattr(
        hostcheck.shutil, "which",
        lambda name: str(script) if name == "rig-wb" else str(real_python))
    interpreter, reason = hostcheck._installed_interpreter()
    assert interpreter == str(real_python)
    # The source names what it did: a green whose interpreter nobody can name
    # proves nothing, and "resolved on PATH" is a weaker claim than an absolute
    # shebang, so it has to be visible.
    assert "resolved on PATH" in reason

    monkeypatch.setattr(hostcheck.shutil, "which",
                        lambda name: str(script) if name == "rig-wb" else None)
    interpreter, reason = hostcheck._installed_interpreter()
    assert interpreter is None
    assert "not on PATH" in reason


# ── untrusted text rendered to a terminal and into --json ───────────────
# `gh auth status` output, an import probe's stderr and a console script's
# shebang are all outside text. Rendered raw they carry terminal escapes (an OSC
# sequence retitles the window of whoever ran hostcheck) and zero-width / bidi
# characters — the exact code points `workbench/injection.py` grades FAIL — into
# both surfaces, and `--json` uses `ensure_ascii=False`, so the serializer is not
# a backstop. Written as escape sequences on purpose: a raw U+200B in this file
# would make rig's own injection sensor fail the diff that adds the test.

_ESC = "\x1b"
_BEL = "\x07"
_ZWSP = "\u200b"  # zero-width space, written escaped on purpose
_RLO = "\u202e"  # right-to-left override, likewise
RAW_MARKERS = (_ESC, _BEL, _ZWSP, _RLO)

_HOSTILE_STATUS = (
    "github.com\n"
    f"  Logged in to github.com account {_ESC}]0;pwned{_BEL}ev{_ZWSP}il{_RLO}tester (keyring)\n"
    f"  - Token scopes: 'repo', 'g{_ESC}]0;pwned{_BEL}ist{_ZWSP}', '{_RLO}gro'\n"
)
_HOSTILE_PROBE_STDERR = {
    "installed": True, "interpreter": f"/opt/{_ESC}]0;pwned{_BEL}venv{_ZWSP}/bin/python",
    "interpreter_source": "shebang of /usr/local/bin/rig-wb",
    "returncode": 1, "payload": None,
    "stderr": f"{_ESC}]0;pwned{_BEL}Segmentation{_ZWSP}fault{_RLO}",
}
_HOSTILE_PROBE_ERRORS = {
    "installed": True, "interpreter": "/opt/venv/bin/python",
    "interpreter_source": "shebang of /usr/local/bin/rig-wb",
    "returncode": 0, "stderr": "",
    "payload": {"package": f"/opt/{_ESC}]0;pwned{_BEL}rig{_ZWSP}/__init__.py",
                "errors": {"rig_workbench.workbench":
                           f"ModuleNotFoundError: {_ESC}]0;pwned{_BEL}gone{_RLO}"}},
}


def _hostile_result() -> dict:
    checks = [
        hostcheck.check_gh_auth_scopes(
            pathlib.Path("/nonexistent"),
            probe=_gh(_HOSTILE_STATUS), remotes=GH_REMOTE),
        hostcheck.check_gh_auth_scopes(
            pathlib.Path("/nonexistent"),
            probe=_gh(f"{_ESC}]0;pwned{_BEL}not logged in{_ZWSP}{_RLO}\nsecond line",
                      returncode=1),
            remotes=GH_REMOTE),
        hostcheck.check_installed_import(pathlib.Path("/nonexistent"),
                                         probe=_HOSTILE_PROBE_STDERR),
        hostcheck.check_installed_import(pathlib.Path("/nonexistent"),
                                         probe=_HOSTILE_PROBE_ERRORS),
    ]
    return {"root": "/nonexistent", "checks": checks,
            "missing": [c["id"] for c in checks if not c["ok"]], "skipped": [], "ok": False}


def test_hostile_probe_output_never_reaches_the_terminal_raw(capsys):
    """An OSC sequence in `gh auth status` output must not retitle the reader's terminal."""
    hostcheck._print_report(_hostile_result())
    printed = capsys.readouterr().out
    for marker in RAW_MARKERS:
        assert marker not in printed, f"raw {marker!r} reached the terminal report"
    # Escaped, not dropped: a fix that deleted the fields would pass the check above.
    assert "<U+001B>" in printed and "<U+200B>" in printed and "<U+202E>" in printed


def test_hostile_probe_output_never_reaches_the_json_raw():
    """`--json` serializes with ensure_ascii=False (hostcheck.cmd_hostcheck), so the
    JSON encoder escapes control characters but passes U+200B/U+202E straight through."""
    payload = json.dumps(_hostile_result(), ensure_ascii=False, indent=2, sort_keys=True)
    for marker in RAW_MARKERS:
        assert marker not in payload, f"raw {marker!r} reached the --json output"
    assert "<U+200B>" in payload and "<U+202E>" in payload


def test_hostile_text_is_escaped_in_the_fields_not_only_in_the_rendering():
    """Escaping at construction is what makes --json safe; print-time would not."""
    result = hostcheck.check_gh_auth_scopes(
        pathlib.Path("/nonexistent"), probe=_gh(_HOSTILE_STATUS), remotes=GH_REMOTE)
    assert not any(m in result["account"] for m in RAW_MARKERS)
    assert all(not any(m in scope for m in RAW_MARKERS) for scope in result["scopes"])
    # A hostile scope string is still not the `repo` scope, so the verdict holds.
    assert "repo" in result["scopes"]
    assert result["ok"] is True

    imported = hostcheck.check_installed_import(pathlib.Path("/nonexistent"),
                                                probe=_HOSTILE_PROBE_ERRORS)
    assert not any(m in imported["package"] for m in RAW_MARKERS)
    assert not any(m in imported["interpreter"] for m in RAW_MARKERS)
    assert imported["ok"] is False


def test_account_capture_is_bounded():
    """An unbounded `\\S+` lets one run of non-whitespace become the whole account name."""
    output = "  Logged in to github.com account " + "A" * 500 + "\n  - Token scopes: 'repo'"
    result = hostcheck.check_gh_auth_scopes(
        pathlib.Path("/nonexistent"), probe=_gh(output), remotes=GH_REMOTE)
    assert len(result["account"]) <= hostcheck.ACCOUNT_MAX


def test_gitignore_patterns_are_escaped_before_being_echoed(tmp_path, capsys):
    """`.gitignore` is repo content, and repo content is not this code's own words."""
    (tmp_path / ".gitignore").write_text(f".rig/{_ESC}]0;pwned{_BEL}{_ZWSP}\n", encoding="utf-8")
    result = hostcheck.check_state_ignored(tmp_path)
    assert result["ok"] is True
    assert not any(m in result["patterns"][0] for m in RAW_MARKERS)


def test_bench_case_without_its_own_probe_refuses_to_run_instead_of_asking_the_host():
    """`probe=None` means "go ask the host" — under a header that says "no network"."""
    with pytest.raises(ValueError, match="live host"):
        hostcheck.run_case(hostcheck.check_gh_auth_scopes,
                           ("no_gh_key", {}, True), pathlib.Path("/nonexistent"))
    with pytest.raises(ValueError, match="live host"):
        hostcheck.run_case(hostcheck.check_installed_import,
                           ("no_probe_key", {}, True), pathlib.Path("/nonexistent"))


def test_probes_never_inherit_stdin(monkeypatch):
    """A probe that decides to prompt would otherwise hang on the session's terminal."""
    import subprocess as _subprocess

    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(hostcheck.subprocess, "run", fake_run)
    hostcheck._run(["gh", "auth", "status"])
    assert seen["stdin"] is _subprocess.DEVNULL


# ── the probe hostcheck delegates to ────────────────────────────────────
# `gh_requirement.probe_auth_status` lives in the module that owns gh probing,
# but hostcheck is its only caller and every property below is one hostcheck
# depends on — so the tests that pin it live next to the code that reads it.


def test_probe_auth_status_reports_a_missing_binary_without_running_anything(monkeypatch):
    monkeypatch.setattr(gh_requirement.shutil, "which", lambda name: None)
    monkeypatch.setattr(gh_requirement, "_run",
                        lambda *a, **k: pytest.fail("must not shell out with no `gh`"))
    assert gh_requirement.probe_auth_status() == {
        "installed": False, "returncode": 127, "output": ""}


def test_probe_auth_status_keeps_the_return_code_and_both_streams(monkeypatch):
    """hostcheck reads scopes off `output` and diagnoses off `returncode`.

    `gh auth status` prints its report to stderr on some versions and stdout on
    others, so dropping either stream loses the scope line on half the fleet.
    """
    monkeypatch.setattr(gh_requirement.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(gh_requirement, "_run",
                        lambda argv, timeout=None: (1, "on stdout", "on stderr"))
    result = gh_requirement.probe_auth_status()
    assert result["installed"] is True
    assert result["returncode"] == 1
    assert "on stdout" in result["output"] and "on stderr" in result["output"]


def test_probe_auth_status_surfaces_a_timeout_as_124_not_as_success(monkeypatch):
    """The distinction hostcheck turns into `probe-timed-out` rather than a logout."""
    monkeypatch.setattr(gh_requirement.shutil, "which", lambda name: "/usr/bin/gh")

    def hang(argv, timeout=None, **kwargs):
        raise subprocess.TimeoutExpired(argv, timeout or 0)

    monkeypatch.setattr(gh_requirement.subprocess, "run", hang)
    result = gh_requirement.probe_auth_status()
    assert result["returncode"] == 124
    assert "timed out" in result["output"]


def test_probe_auth_status_is_bounded(monkeypatch):
    """Unbounded, this is a hang at the start of every session that runs hostcheck."""
    seen = {}
    monkeypatch.setattr(gh_requirement.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(gh_requirement, "_run",
                        lambda argv, timeout=None: seen.update(argv=argv, timeout=timeout) or (0, "", ""))
    gh_requirement.probe_auth_status()
    assert seen["argv"] == ["gh", "auth", "status"]
    assert seen["timeout"] == gh_requirement.AUTH_PROBE_TIMEOUT_SECONDS


# ── umask (#467) ─────────────────────────────────────────────────────────────
def _umask_result(value, tmp_path):
    previous = os.umask(value)
    try:
        return hostcheck.check_umask(tmp_path)
    finally:
        os.umask(previous)


def test_a_umask_that_masks_group_and_other_write_is_the_one_rig_needs(tmp_path):
    result = _umask_result(0o022, tmp_path)
    assert result["ok"] is True
    assert result["signals"][0]["new_file_mode"] == "0644"


def test_a_group_writable_umask_is_reported_with_the_mode_it_will_produce(tmp_path):
    """0664 is the mode `git worktree add` gave every file in the tree where #467 was
    found, and naming it is the point: the operator sees the number the refusal will
    later quote, before a run has failed."""
    result = _umask_result(0o002, tmp_path)
    assert result["ok"] is False
    assert result["signals"][0]["umask"] == "0002"
    assert result["signals"][0]["new_file_mode"] == "0664"
    assert result["signals"][0]["group_or_other_writable"] is True


def test_an_other_writable_umask_is_caught_too(tmp_path):
    """Group and other are separate bits and rig refuses on either. A check that only
    watched the group bit would pass a tree the world can write."""
    assert _umask_result(0o020, tmp_path)["ok"] is False


def test_reading_the_umask_puts_back_the_one_it_found(tmp_path):
    """There is no getter, so the check sets a umask to read one. Leaving the process
    with a different umask than it started with would change the permissions of every
    file created after `hostcheck` ran — including, in a rig run, the worktree."""
    os.umask(0o027)
    try:
        hostcheck.check_umask(tmp_path)
        restored = os.umask(0o022)
        assert restored == 0o027
    finally:
        os.umask(0o022)


def test_the_umask_check_is_part_of_the_suite_that_runs(tmp_path):
    assert "umask" in {check(tmp_path)["id"] for check in hostcheck.CHECKS}


def test_the_probe_mask_is_the_strict_one_and_the_real_one_goes_back(monkeypatch):
    """Reading a umask means setting one, and *which* one is set in that window matters.

    Written the usual way — `os.umask(0)` — the window is one where the process masks
    nothing, so a signal arriving inside it leaves every later file world-writable. With
    `0o022` the same interruption leaves the process stricter than it was. Neither choice
    changes what the check reports, so nothing else in this file can tell them apart.
    """
    calls = []
    real = os.umask

    def recording(value):
        calls.append(value)
        return real(value)

    monkeypatch.setattr(os, "umask", recording)
    real(0o007)
    try:
        assert hostcheck._read_umask() == 0o007
    finally:
        real(0o022)
    assert calls == [0o022, 0o007]
