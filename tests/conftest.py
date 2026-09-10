"""pytest bootstrap for the rig orchestrator suite.

- Inserts the repo root into sys.path so `rig_workbench` imports from any cwd.
- Pins RIG_HOME to the repo checkout *before* rig_workbench.orchestrate.config
  is first imported (config resolves RIG_HOME at import time).
- Sets RIG_SKIP_GH_CHECK so the suite does not depend on the developer's real
  `gh` state (see below).
- Provides tmp fixtures so no test touches the real repo's .rig/ state.
"""

import json
import os
import pathlib
import subprocess
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Must happen before any rig_workbench import (config reads env at import time).
os.environ["RIG_HOME"] = str(REPO_ROOT)

# gh / gh-stack no longer gate anything (rig_workbench/gh_requirement.py), so
# this is not about letting the suite run — it is about stderr. `workbench new`
# and `orchestrate run` print a one-line note when gh-stack is absent, and that
# line would appear or not appear depending on whether the machine running pytest
# happens to have it, leaking into every test that asserts on a subprocess's
# stderr. Silencing it pins the suite to one behaviour;
# tests/test_gh_requirement.py owns the advisory and sets this per test.
os.environ["RIG_SKIP_GH_CHECK"] = "1"

# Every run that finishes is mirrored into ~/.rig/runs.jsonl for cross-project rollups
# (runstate.append_run_record). Tests finish runs, so without this the suite writes into
# the developer's own cross-project history and `rig-wb usage --global` starts counting
# fixtures as work. Only the global mirror is redirected: RIG_RUNS_PATH is deliberately
# left alone, because a test that runs the CLI in a tmp repo expects the per-project log
# to land in that repo's .rig/, and pinning it here would take that away.
# The host instinct tier (`~/.rig/instincts.jsonl`). `select_for_injection` writes to it —
# it bumps hit_count and refreshes last_seen on everything it picks — and the hook that
# calls it is exercised by tests that have nothing to do with instincts
# (test_codex_integration runs inject-instincts.sh with a copy of os.environ). A per-file
# fixture cannot cover those, so on a machine that has promoted even one instinct the
# suite would inflate its hit_count and push back its decay, invisibly.
os.environ.setdefault("RIG_USER_HOME",
                      tempfile.mkdtemp(prefix="rig-test-user-home-"))

os.environ.setdefault("RIG_GLOBAL_RUNS_PATH",
                      str(pathlib.Path(tempfile.mkdtemp(prefix="rig-test-global-runs-"))
                          / "runs.jsonl"))

# Pack- and recipe-trust grants (rig_workbench/packs/trust.py::_store_path,
# rig_workbench/orchestrate/recipes.py::_trust_store_path). Both default to a path
# under the *real* Path.home() — ~/.rig/trusted-pack-assets.json and
# ~/.claude/rig/trusted-recipes.json — and both are written, not just read: approving
# a project-tier asset records its hash there. Individual tests already redirect these
# per test, and those overlays still win (monkeypatch.setenv and the `rig_cli` `env`
# overlay are both applied after this); this is the fail-safe for the test that forgets
# — without it, one missing overlay silently grants trust in the developer's own home
# and the next real run trusts a fixture. Not a convenience: do not remove as redundant.
#
# Known property, so it is not a surprise later: packs/trust.py reads
# RIG_PACK_TRUST_STORE *or* RIG_TRUST_STORE, so setting only the latter in a test no
# longer redirects pack trust the way it did before this default existed — the
# fail-safe below shadows that fallback. Redirect both, or accept the shared session
# store. Nothing depends on the old behaviour today; identities are keyed on resolved
# path plus content hash, which are tmp_path-unique.
os.environ.setdefault("RIG_PACK_TRUST_STORE",
                      str(pathlib.Path(tempfile.mkdtemp(prefix="rig-test-pack-trust-"))
                          / "trusted-pack-assets.json"))

os.environ.setdefault("RIG_TRUST_STORE",
                      str(pathlib.Path(tempfile.mkdtemp(prefix="rig-test-recipe-trust-"))
                          / "trusted-recipes.json"))

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# How much slower than a developer machine a CI runner is allowed to be before a
# `subprocess.run(..., timeout=...)` in this suite gives up. Sized off a real
# failure, not off a guess: the jp-workflow dry-run costs ~16s of pure
# single-threaded CPU locally (`real 16.4s / user 15.4s`) and still blew a 30s
# budget on GitHub's 3.12 runner, so a factor of 2 is demonstrably too small.
# Six keeps ~3x headroom over the slowest run CI has actually shown us.
#
# The default has to be the safe value on its own: .github/workflows/validate.yml
# runs `pytest -q -n auto` and sets no environment, so CI always takes this number
# — and takes it under 4-way contention rather than serially, which eats into the
# headroom the factor was originally cut against. Measured rather than assumed:
# forcing the factor to 2 binds exactly one call site (the 30s floor below covers
# every measurement under 15s), and that site is the jp-workflow dry-run this
# comment was written about. Under `-n 4` at factor 2 the suite still passed with
# no timeout among its failures, so contended cost stays inside 2x measured and 6
# keeps roughly 3x headroom in parallel too.
# The override exists for the opposite case — a machine slower still, or a
# developer deliberately tightening the budget to hunt a hang.
CI_TIMEOUT_FACTOR = float(os.environ.get("RIG_TEST_TIMEOUT_FACTOR", "6"))

# Nothing gets less than this, however fast it measures: a subprocess that
# normally finishes in 50ms is not healthier for being killed at 300ms, and the
# floor is what keeps the fast call sites at the suite's existing 30s idiom.
MIN_SUBPROCESS_TIMEOUT = 30.0


def subprocess_timeout(measured_seconds: float) -> float:
    """Budget for a `subprocess.run(timeout=...)` from the call's measured cost.

    Pass what the subprocess actually costs on a developer machine — measure it,
    do not estimate it — and this scales it for a loaded CI runner. Recording the
    measurement at the call site is the point: a bare `timeout=30` says nothing
    about whether 30 is generous or one bad scheduling window from flaking.
    """
    return max(MIN_SUBPROCESS_TIMEOUT, measured_seconds * CI_TIMEOUT_FACTOR)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_ambient_worktree_runtime(monkeypatch):
    """Keep runtime selection independent of the developer's host session.

    Tests that exercise Orca set these variables themselves after this fixture runs, while
    every other test — including subprocesses spawned from it — gets the native default.
    Import the names from the detector so the suite follows the production environment
    contract instead of maintaining a second hard-coded list.
    """
    from rig_workbench.workbench import orca

    for name in (orca.WORKTREE_VAR, orca.WORKSPACE_VAR):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def step_factory():
    """Build a minimal step dict with the full key set new_state/compute_next expect."""
    from rig_workbench.orchestrate.config import DEFAULT_K

    def make(**k):
        return {
            "id": k["id"],
            "instruction": k.get("instruction", "x"),
            "gate": k.get("gate"),
            "pattern": k.get("pattern"),
            "personas": k.get("personas", []),
            "needs": k.get("needs", []),
            "acceptance": k.get("acceptance", []),
            "checks": k.get("checks", []),
            "max_retries": k.get("max_retries", DEFAULT_K),
            "output_contract": k.get("output_contract"),
            "actor": k.get("actor"),
            "human_gate": k.get("human_gate"),
        }

    return make


@pytest.fixture
def tmp_queue(tmp_path, monkeypatch):
    """Rebind queueing.QUEUE_PATH to a scratch file (mirrors the selftest pattern)."""
    from rig_workbench.orchestrate import queueing

    qpath = tmp_path / "queue.json"
    monkeypatch.setattr(queueing, "QUEUE_PATH", qpath)
    return qpath


@pytest.fixture
def recipe_dir(tmp_path):
    """Scratch directory for synthetic recipe .md files."""
    d = tmp_path / "recipes"
    d.mkdir()
    return d


@pytest.fixture
def write_recipe(recipe_dir):
    def write(name: str, body: str) -> pathlib.Path:
        p = recipe_dir / f"{name}.md"
        p.write_text(body, encoding="utf-8")
        return p

    return write


# ── contract-test fixtures ───────────────────────────────────────────────────
# Stage 1 pins the externally visible CLI contract, so these go through the real
# process rather than through an import: `python -m rig_workbench.cli` is what a
# user's shell reaches, and an in-process call would not see argument parsing,
# exit codes, or stdout framing at all.

# Measured, not guessed, as subprocess_timeout's docstring asks: `wb gates --json`
# costs 0.22s on a developer machine and `wb new` in a scratch repo — the heaviest
# thing a contract test does — costs 0.56s. Both sit far below
# MIN_SUBPROCESS_TIMEOUT, so in practice every call gets the 30s floor; the number
# is recorded here so a caller that grows a genuinely slow command has something
# to raise instead of a bare literal.
CLI_MEASURED_SECONDS = 5.0

# `git init` + one commit, measured the same way: milliseconds, floor applies.
GIT_MEASURED_SECONDS = 2.0


@pytest.fixture
def rig_cli(tmp_path):
    """Run the real CLI in a subprocess; return the CompletedProcess unjudged.

    Deliberately does *not* raise on a non-zero exit (no `check=True`): exit codes
    are part of the contract these tests assert on, so the caller has to be able to
    see them. Text is decoded as UTF-8 with `errors="replace"` and the child is
    pinned to UTF-8 I/O, because rig prints Japanese and a Windows runner's default
    code page would otherwise turn a passing assertion into a decode error
    (tests/test_cli_smoke.py's run_cli learned this first).

        run(*args, cwd=None, env=None, timeout=None) -> subprocess.CompletedProcess

    cwd defaults to `tmp_path`, the suite's idiom for "wherever this test is
    working"; pass `rig_git_repo` (or any path) to work somewhere else. `env` is an
    overlay on the inherited environment — `{"RIG_ALLOW_PROJECT_PACKS": "1"}` — and
    a value of None *removes* a variable, which is how a test unsets something
    this conftest set for everybody (RIG_SKIP_GH_CHECK, say).
    """

    def run(*args, cwd=None, env=None, timeout=None):
        child_env = dict(
            os.environ,
            # The repo root, not an install: the subprocess must import the tree
            # under test even when a released rig-wb is on the machine.
            PYTHONPATH=os.pathsep.join(
                p for p in (str(REPO_ROOT), os.environ.get("PYTHONPATH")) if p),
            PYTHONIOENCODING="utf-8",
            PYTHONUTF8="1",
        )
        for key, value in (env or {}).items():
            if value is None:
                child_env.pop(key, None)
            else:
                child_env[key] = str(value)
        return subprocess.run(
            [sys.executable, "-m", "rig_workbench.cli", *(str(a) for a in args)],
            cwd=str(cwd if cwd is not None else tmp_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env,
            timeout=(subprocess_timeout(CLI_MEASURED_SECONDS) if timeout is None else timeout),
        )

    return run


@pytest.fixture
def rig_cli_json(rig_cli):
    """`rig_cli`, plus `json.loads` on stdout — what most `--json` callers want.

        payload = rig_cli_json("wb", "gates", "--json", cwd=repo)

    Takes the same keyword arguments as `rig_cli`. It forms no opinion about the
    exit code by default (a rejected gate emits a valid envelope *and* exits 1), so
    assert on it explicitly with `expect_returncode=` here, or use `rig_cli` when
    the CompletedProcess itself is the thing under test.

    A stdout that is not JSON fails with the exit code, the actual stdout and the
    stderr in the message. A bare `json.JSONDecodeError` from inside a fixture says
    only "Expecting value: line 1 column 1" and costs the next person an afternoon
    working out that the command printed a usage error instead.
    """

    def run(*args, expect_returncode=None, **kwargs):
        result = rig_cli(*args, **kwargs)
        argv = " ".join(str(a) for a in args)
        if expect_returncode is not None and result.returncode != expect_returncode:
            pytest.fail(
                f"`rig-wb {argv}` exited {result.returncode}, expected "
                f"{expect_returncode}\n--- stdout ---\n{result.stdout}"
                f"\n--- stderr ---\n{result.stderr}", pytrace=False)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            # Raised outside the handler, without a traceback: the useful part is
            # what the command printed, not json/decoder.py's own frames.
            reason = (f"`rig-wb {argv}` (exit {result.returncode}) did not print JSON "
                      f"on stdout: {exc}\n--- stdout ---\n{result.stdout}"
                      f"\n--- stderr ---\n{result.stderr}")
        pytest.fail(reason, pytrace=False)

    return run


@pytest.fixture
def rig_git_repo(tmp_path):
    """A tmp git repo with one commit, ready to be a rig workbench target.

    `wb new` cuts a worktree off HEAD, so an empty `git init` is not enough — HEAD
    has to resolve. Identity is set in the repo's *own* config rather than
    globally, so the commits the CLI makes later carry it too, and the developer's
    real git config is kept out entirely: no system config, a global config pointed
    at a file that does not exist, and the GIT_AUTHOR_*/GIT_COMMITTER_* overrides
    dropped from the environment. Otherwise a machine with `commit.gpgsign = true`
    or a signing key it cannot reach fails the suite for reasons that have nothing
    to do with rig.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    env = dict(os.environ,
               GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=str(tmp_path / "absent-gitconfig"),
               GIT_TERMINAL_PROMPT="0")
    for leaked in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                   "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        env.pop(leaked, None)

    def git(*args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                       text=True, env=env, timeout=subprocess_timeout(GIT_MEASURED_SECONDS))

    git("-c", "init.defaultBranch=main", "init", "-q")
    git("config", "--local", "user.name", "rig test")
    git("config", "--local", "user.email", "rig-test@example.invalid")
    git("config", "--local", "commit.gpgsign", "false")
    (repo / "README.md").write_text("rig contract-test repository\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "initial commit")
    return repo


def run_git(repo, *args, check=True):
    """git inside `repo`, with the developer's own git configuration kept out.

    The same hygiene `rig_git_repo` builds its repository under, lifted out so a test
    that commits *into* that repo (or into a task worktree cut from it) does not have to
    restate it: no system config, a global config pointed at a file that does not exist,
    and the ambient GIT_AUTHOR_*/GIT_COMMITTER_* overrides dropped. Identity and
    `commit.gpgsign` live in the repository's own local config, which the fixture already
    wrote, so a commit made here behaves exactly like the fixture's own — and a host with
    `commit.gpgsign = true` and an unreachable key does not fail the suite for a reason
    that has nothing to do with rig.

        run_git(repo, "add", "-A")
        head = run_git(repo, "rev-parse", "HEAD").stdout.strip()

    Returns the CompletedProcess (captured, text) rather than a string, because the three
    private `_git` copies this consolidates disagree on that: one returns stripped stdout,
    one the CompletedProcess, one nothing. The superset is the process object; callers
    that want the output add `.stdout.strip()`. `check=True` by default, matching all
    three; pass `check=False` to inspect a failure instead of raising.
    """
    repo = pathlib.Path(repo)
    env = dict(os.environ,
               GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=str(repo.parent / "absent-gitconfig"),
               GIT_TERMINAL_PROMPT="0")
    for leaked in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                   "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        env.pop(leaked, None)
    return subprocess.run(["git", *args], cwd=str(repo), check=check, capture_output=True,
                          text=True, env=env,
                          timeout=subprocess_timeout(GIT_MEASURED_SECONDS))
