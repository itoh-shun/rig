"""The exit codes rig actually returns, observed through the real CLI process (#416).

`tests/test_exit_code_contract.py` is the other half of this and stays as it is. That
file asserts the contract *in process*: it imports `rig_workbench.exitcodes`, checks
that `OK`/`REJECTED`/`ERROR` are 0/1/2, that `RESERVED` is left alone, and that every
installed entry point's `main` carries the `guard` decorator. All of that is true of
the source tree.

None of it is a statement about what a shell gets back. A decorator can be present and
the command underneath can still `sys.exit(1)` on a missing file; a constant can say
`ERROR = 2` and no command need ever return it. The gap between "the module defines
these numbers" and "running the command produces these numbers" is exactly the gap a CI
step falls into, because a CI step never imports anything — it reads `$?`.

So this file spends a subprocess per assertion. Every code here was produced by running
the real command against a real condition (a planted secret in a real worktree diff, a
real `PATH` with no `gh` on it, a real task parked on a human gate) and observing the
status, then pinning what was observed. Nothing is monkeypatched: a monkeypatched exit
code proves the function computed a number, which is what the in-process file already
covers. rig's internals are about to be rewritten, and after the rewrite these two files
fail for different reasons — the other one when the constants or the wiring move, this
one when the *behaviour a caller depends on* moves, whatever the internals now look like.

Two groups, selectable by name:

    pytest -k generic    the three core codes, one command each
    pytest -k specific   the command-specific codes (gh-check, contract, gate, govern,
                         the orchestrator's human gate, design-constraints, ja-lint, bench)

`NOT_PINNED` below records what could not be driven to a code without a production
change or the network, with the reason. It is deliberately a constant and not a
`skip`: a skipped test reads as "not run today", and these are "not reachable at all
from a test process", which is a different and more durable fact.
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest
from conftest import REPO_ROOT, subprocess_timeout

# WHY THE CODES BELOW ARE WRITTEN OUT AS INTEGERS, and not imported from
# `rig_workbench.exitcodes`:
#
# 0/1/2 are rig's published contract — the numbers a CI step, a Makefile, or another
# agent's harness branches on, and the numbers the README and `exitcodes`' own
# docstring promise. A caller outside this repo has the integers and nothing else.
# `assert result.returncode == exitcodes.OK` compares the source tree to itself: edit
# `OK = 0` to `OK = 7` and the assertion still passes, in the same commit, while every
# reader downstream breaks. That is the tautology tests/test_schema_registry.py was
# written to eliminate for schema ids (see its header), and it is the same tautology
# here. Writing the integers out is what makes changing one a deliberate act that has
# to come here and edit a number.
#
# The other half of the reason is mechanical: rig's internals are being rearchitected
# and the module moves. An import breaks this file at *collection* time — every test
# below, including the ones that have nothing to do with a constant, stops running for
# a reason that is not about behaviour. Observed exit codes do not have that problem.
#
# The command-specific codes further down (gh-check's 3 and 5, contract's 3, govern's
# 3, the orchestrator's parked 3) were already written out for exactly this reason.

#: rig ran and the answer is yes — gate passed, scan clean, nothing to report.
OK = 0

#: rig ran, judged, and the answer is no. A verdict, not a malfunction.
REJECTED = 1

#: rig could not produce an answer at all: bad usage, unreadable state, a crash.
ERROR = 2

#: The statuses whose meaning is fixed outside rig, written out for the same reason
#: as the three above (`exitcodes.RESERVED` is the frozenset this mirrors, deliberately
#: not imported). 124 is GNU `timeout` reporting that it killed the command; 126 is a
#: shell that found the command and could not execute it; 127 is a shell that could not
#: find it at all; and 128+N is how a shell reports "killed by signal N" — 129 SIGHUP,
#: 130 Ctrl-C, 137 SIGKILL, 143 SIGTERM, on up through the real-time signals. The band
#: here runs to 128+64, the highest a Linux box has; a platform with fewer signals makes
#: this a superset of its own reserved set, which can only make the assertion stricter.
SHELL_OWNED = frozenset({124, 126, 127} | set(range(128 + 1, 128 + 64 + 1)))

# EVERY 1 IN THIS FILE IS A VERDICT, AND EVERY `die()`-DERIVED CODE IS A 2.
#
# `rig_workbench/workbench/state.py` used to have `die()` hardcoded to `sys.exit(1)`, so
# every plain workbench failure — a task id that is not there, a worktree that is gone —
# surfaced as 1, the code reserved for "rig judged this and said no". That was a defect,
# and this file refused to pin any of those 1s rather than freeze it. `die()` now exits 2
# and `state.reject()` carries the verdicts, so those conditions are pinned here: see
# test_generic_a_workbench_command_that_cannot_find_its_task_exits_two, which drives the
# two traps this comment used to name (`wb status <nonexistent>` and
# `wb scan-secrets --diff <nonexistent>`).
#
# Every 1 asserted below is still a deliberate verdict path, and none of them changed:
# scan-secrets' own `sys.exit(1)` after printing its findings, `contract`'s
# `EXIT_CODE[NOT_ACCEPTABLE]`, design-constraints' and ja-lint's violation returns,
# bench's completed non-pass.

#: Codes this file could not produce from a test process, and why. Each entry is
#: (command, code, reason). Read this before adding a test for one of them — the
#: reason is a measurement, not a guess.
NOT_PINNED = (
    (
        "rig-wb bench",
        0,
        "`pass` requires a bare arm that actually produces silent defects: the "
        "score's validity rules want at least 10 tasks with 3 valid pairs each, and "
        "then a measurable rig-vs-bare reduction. Driven for real with the packaged "
        "corpus and `--provider mock` (10 tasks x 3 runs, 109s, 0% infrastructure "
        "errors) the verdict is `inconclusive` — 'bare silent-defect rate is zero; "
        "relative reduction is inconclusive' — and the exit is 1. The mock provider "
        "edits deterministically and never injects the defects the score is built to "
        "detect, so 0 needs a paid provider and the network. `--allow-paid-provider` "
        "exists precisely because that is not something a test suite may do.",
    ),
)

# The whole 15-criterion acceptance gate, as `wb gate` prints it for a `feature` task.
# Spelled out rather than derived, because deriving it from the same code the CLI uses
# would make this test agree with rig about what the gate is instead of pinning it.
_FEATURE_GATE_CRITERIA = (
    "task_intent_satisfied", "no_unrelated_diff", "diff_summary_written",
    "risk_summary_written", "tests_pass_or_explained", "no_type_errors_or_explained",
    "no_secret_leak", "no_gate_tampering", "no_injection_markers",
    "no_destructive_operation", "requirement_summary_written",
    "implementation_matches_requirement", "tests_added_or_explained",
    "public_api_changes_documented", "migration_or_backward_compatibility_considered",
)

# A fake AWS secret-access key: 40 characters of base64-ish noise, the shape rig's
# high-entropy rule is looking for. It is AWS's own published example value, so it
# unlocks nothing and no scanner anywhere has to treat this file as an incident.
_PLANTED_SECRET = "wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY"

# Measured on a developer machine, as conftest.subprocess_timeout's docstring asks:
# one bench task at `--runs 1` with the mock provider costs 3.7s wall. Every other
# command in this file is under a second and takes the 30s floor.
_BENCH_MEASURED_SECONDS = 3.7

# The shims below are `#!/bin/sh` scripts on the child's PATH. A Windows runner has no
# such thing, and faking one would test the fake.
_posix_only = pytest.mark.skipif(os.name != "posix",
                                 reason="the gh probe is driven by a #!/bin/sh PATH shim")


# ── helpers ──────────────────────────────────────────────────────────────────
def _git(repo, *args):
    """git inside `repo`, with the developer's own git configuration kept out.

    The same isolation `rig_git_repo` applies when it builds the repo: no system
    config, a global config pointed at a file that is not there, and the ambient
    GIT_AUTHOR_*/GIT_COMMITTER_* overrides dropped. Identity and `commit.gpgsign`
    live in the repo's local config, which the fixture already wrote, so commits made
    here behave exactly like the fixture's own.
    """
    repo = pathlib.Path(repo)
    env = dict(os.environ,
               GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=str(repo.parent / "absent-gitconfig"),
               GIT_TERMINAL_PROMPT="0")
    for leaked in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                   "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        env.pop(leaked, None)
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True, env=env,
                          timeout=subprocess_timeout(2.0))


def _ignore_rig_state(repo):
    """Ignore `.rig/`, as a rig repository does by the time anyone accepts anything.

    Not load-bearing any more, and kept because it is true to life: `wb new` only offers
    the entry now (and off a terminal declines and prints the line), while `wb accept`
    stopped counting an untracked `.rig/` as a dirty tree. What a real rig repository
    looks like on its second day is this, so the exit codes are measured against it.
    """
    (repo / ".gitignore").write_text(
        "# rig workbench state (task worktrees, telemetry, audit, locks)\n.rig/\n",
        encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ignore rig workbench state")


def _new_task(rig_cli, repo, slug):
    """`wb new`, returning (task_id, worktree path). Fails loudly if the CLI did not."""
    result = rig_cli("wb", "new", f"exit-code surface: {slug}", "--type", "feature",
                     "--slug", slug, cwd=repo)
    assert result.returncode == 0, result.stdout + result.stderr
    worktree = None
    for line in result.stdout.splitlines():
        if line.startswith("worktree: "):
            worktree = line[len("worktree: "):].split(" (branch:")[0].strip()
    assert worktree, f"`wb new` printed no worktree line:\n{result.stdout}"
    tasks = sorted(p.name for p in (repo / ".rig" / "runs").iterdir() if p.is_dir())
    assert len(tasks) == 1, tasks
    return tasks[0], pathlib.Path(worktree)


def _shim_dir(tmp_path, name, script=None):
    """A directory to put on the child's PATH, optionally holding a fake `gh`."""
    directory = tmp_path / name
    directory.mkdir()
    if script is not None:
        gh = directory / "gh"
        gh.write_text(script, encoding="utf-8")
        gh.chmod(0o755)
    return directory


#: A `gh` that answers every probe `check_gh` makes: version, extension list, auth.
#: `gh extension list` is where the two non-zero states are decided, so the two shims
#: differ only in what that one subcommand prints.
_GH_WITH_STACK = """#!/bin/sh
case "$1" in
  --version)  echo "gh version 2.88.0 (2026-03-10)" ;;
  extension)  printf 'gh stack\\tgithub/gh-stack\\tv0.1.0\\n' ;;
  auth)       echo "  Logged in to github.com account tester (keyring)" ;;
esac
exit 0
"""

_GH_WITHOUT_STACK = """#!/bin/sh
case "$1" in
  --version)  echo "gh version 2.88.0 (2026-03-10)"; exit 0 ;;
  extension)  exit 1 ;;
  auth)       echo "not logged in" >&2; exit 1 ;;
esac
exit 0
"""

# `gh-check` is the one command whose answer depends on the developer's own machine,
# so every one of its tests replaces PATH entirely rather than prepending to it, and
# drops the RIG_SKIP_GH_CHECK conftest sets for everybody. Replacing PATH is safe
# because `rig_cli` invokes the interpreter by absolute path (`sys.executable`).
_GH_ENV_UNSILENCED = {"RIG_SKIP_GH_CHECK": None}


# ── generic: the three codes every rig command shares ────────────────────────
def test_generic_a_clean_command_that_found_nothing_to_report_exits_zero(rig_cli, rig_cli_json,
                                                                        rig_git_repo):
    """`OK` through a process. `wb gates` only reads the preset definitions, so it is
    the cheapest command in rig that genuinely runs to completion — nothing about the
    repository can turn its answer into a verdict."""
    result = rig_cli("wb", "gates", cwd=rig_git_repo)
    assert result.returncode == OK, result.stdout + result.stderr

    # Same command, machine framing: a 0 that does not also carry a parseable envelope
    # is not the answer a `--json` caller acted on.
    payload = rig_cli_json("wb", "gates", "--json", cwd=rig_git_repo,
                           expect_returncode=OK)
    assert payload["schema"].startswith("rig.gates/")


def test_generic_a_rejection_is_a_verdict_the_same_command_exits_zero_on_clean_input(
        rig_cli, rig_git_repo):
    """`REJECTED` through a process, and the point of the contract in one test.

    One command, one task, two runs. The only thing that changes between them is a
    planted fake secret committed into the task's worktree. If the 1 could also mean
    "rig fell over", the clean run would not be a 0 — so running both halves is what
    makes this an assertion about a *verdict* rather than about a non-zero status."""
    task_id, worktree = _new_task(rig_cli, rig_git_repo, "planted-secret")

    clean = rig_cli("wb", "scan-secrets", "--diff", task_id, cwd=rig_git_repo)
    assert clean.returncode == OK, clean.stdout + clean.stderr
    assert "No potential secrets found" in clean.stdout

    (worktree / "creds.py").write_text(
        f'AWS_SECRET_ACCESS_KEY = "{_PLANTED_SECRET}"\n', encoding="utf-8")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-q", "-m", "plant a fake credential")

    judged = rig_cli("wb", "scan-secrets", "--diff", task_id, cwd=rig_git_repo)
    assert judged.returncode == REJECTED, judged.stdout + judged.stderr
    # The finding is in the report, and the excerpt is masked — a scanner that prints
    # the secret it found has turned a rejection into a second leak.
    assert "1 potential secret(s) found" in judged.stdout
    assert _PLANTED_SECRET not in judged.stdout


def test_generic_a_command_that_could_not_produce_an_answer_exits_two_not_one(
        rig_cli, rig_git_repo):
    """`ERROR` through a process. A task id that does not exist is the plainest way
    for a command to be unable to answer, and the assertion that matters is the
    second one: were this a 1, a caller could not tell it from the rejection the test
    above produces."""
    result = rig_cli("wb", "contract", "no-such-task-id-9999", cwd=rig_git_repo)
    assert result.returncode == ERROR, result.stdout + result.stderr
    assert result.returncode != REJECTED


def test_generic_a_workbench_command_that_cannot_find_its_task_exits_two(
        rig_cli, rig_git_repo):
    """The two conditions this file used to refuse to pin, driven for real.

    Both go through `state.die`, and both used to answer 1 — indistinguishable from the
    rejection two tests above. A task id that does not exist is not a verdict about
    anything; the command never reached one."""
    for argv in (("wb", "status", "no-such-task-id-9999"),
                 ("wb", "scan-secrets", "--diff", "no-such-task-id-9999")):
        result = rig_cli(*argv, cwd=rig_git_repo)
        assert result.returncode == ERROR, (
            f"`rig-wb {' '.join(argv)}` exited {result.returncode}\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}")
        assert result.returncode != REJECTED
        assert "not found" in result.stderr


def test_generic_no_command_in_this_sample_returns_a_status_the_shell_owns(
        rig_cli, rig_git_repo):
    """124 / 126 / 127 / 128+N belong to `timeout`, the shell, and the kernel.

    A command that assigned rig meaning to one of them would make
    `timeout 60 rig-wb ...` unreadable. The sample is deliberately mixed: a success,
    a usage error, a subcommand that does not exist, and a command run somewhere
    there is no repository at all — the shapes most likely to reach for an unusual
    status on the way out."""
    sample = (
        ("wb", "gates"),
        ("wb", "gates", "--json"),
        ("wb", "no-such-subcommand"),
        ("no-such-top-level-command",),
        ("gh-check", "--help"),
        ("version",),
    )
    observed = {}
    for argv in sample:
        result = rig_cli(*argv, cwd=rig_git_repo)
        observed[" ".join(argv)] = result.returncode
    reserved = {argv: code for argv, code in observed.items() if code in SHELL_OWNED}
    assert not reserved, f"these commands returned a status the shell owns: {reserved}"
    # And nothing wandered outside the small band rig documents, either.
    assert set(observed.values()) <= {0, 1, 2, 3}, observed


# ── specific: gh-check (0 / 3 / 5) ───────────────────────────────────────────
@_posix_only
def test_specific_gh_check_exits_zero_when_gh_and_the_stack_extension_are_both_there(
        rig_cli, tmp_path):
    shim = _shim_dir(tmp_path, "gh-ok", _GH_WITH_STACK)
    result = rig_cli("gh-check", env={**_GH_ENV_UNSILENCED, "PATH": str(shim)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "gh-stack v0.1.0" in result.stdout


@_posix_only
def test_specific_gh_check_exits_three_when_gh_is_not_on_path_at_all(rig_cli, tmp_path):
    """3 is `gh-missing`. PATH is replaced with an empty directory rather than having
    `gh` removed from it, because "removed from PATH" is not a thing a test can do
    portably and the probe only ever asks `shutil.which`."""
    empty = _shim_dir(tmp_path, "gh-absent")
    result = rig_cli("gh-check", env={**_GH_ENV_UNSILENCED, "PATH": str(empty)})
    assert result.returncode == 3, result.stdout + result.stderr
    assert "the GitHub CLI (`gh`) is not installed" in result.stderr


@_posix_only
def test_specific_gh_check_exits_five_when_gh_is_there_without_the_stack_extension(
        rig_cli, tmp_path):
    """5 is `extension-missing`, and it has to stay distinct from 3: the remedies are
    different commands, and a caller that collapsed them would tell someone with a
    working `gh` to go and install `gh`."""
    shim = _shim_dir(tmp_path, "gh-no-ext", _GH_WITHOUT_STACK)
    result = rig_cli("gh-check", env={**_GH_ENV_UNSILENCED, "PATH": str(shim)})
    assert result.returncode == 5, result.stdout + result.stderr
    assert "github/gh-stack` is not" in result.stderr


# ── specific: wb contract (0 / 1 / 2 / 3) ────────────────────────────────────
def test_specific_contract_exits_three_while_the_task_has_not_been_judged_yet(
        rig_cli, rig_git_repo):
    """3 is `pending`, and it is the status the other three are worth having. Folded
    into 1 a poller reads "still running" as "refused"; folded into 0 it merges
    something no gate has ruled on."""
    task_id, _ = _new_task(rig_cli, rig_git_repo, "still-running")
    result = rig_cli("wb", "contract", task_id, "--json", cwd=rig_git_repo)
    assert result.returncode == 3, result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "pending"


def test_specific_contract_exits_one_when_rig_looked_and_this_is_not_acceptable(
        rig_cli, rig_git_repo):
    """1 is `not-acceptable`. A discarded task is the cheapest change rig has genuinely
    ruled on — the work existed, and it is not going in."""
    task_id, _ = _new_task(rig_cli, rig_git_repo, "thrown-away")
    discarded = rig_cli("wb", "discard", task_id, "--yes", cwd=rig_git_repo)
    assert discarded.returncode == 0, discarded.stdout + discarded.stderr

    result = rig_cli("wb", "contract", task_id, "--json", cwd=rig_git_repo)
    assert result.returncode == 1, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "not-acceptable"
    assert payload["final_status"] == "discarded"


def test_specific_contract_exits_zero_only_after_the_gate_cleared_and_a_human_accepted(
        rig_cli, rig_git_repo):
    """0 is `acceptable`, and it is the expensive one to reach on purpose.

    Every step here is a real precondition `wb accept` enforces, in the order it
    enforces them: a real commit in the task's own worktree, all fifteen gate criteria
    recorded, the prose diff summary written, and a clean main working tree. Skipping
    any of them is how the other statuses happen, which is why this is driven rather
    than asserted against a hand-written receipt."""
    _ignore_rig_state(rig_git_repo)
    task_id, worktree = _new_task(rig_cli, rig_git_repo, "carried-through")

    (worktree / "NEW.md").write_text("a real change\n", encoding="utf-8")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-q", "-m", "add NEW.md")

    gate = rig_cli("wb", "gate", task_id,
                   *(arg for name in _FEATURE_GATE_CRITERIA
                     for arg in ("--set", f"{name}=passed")),
                   cwd=rig_git_repo)
    assert gate.returncode == 0, gate.stdout + gate.stderr

    (rig_git_repo / ".rig" / "runs" / task_id / "diff.md").write_text(
        "# diff summary\n\nAdds NEW.md.\n", encoding="utf-8")

    accepted = rig_cli("wb", "accept", task_id, cwd=rig_git_repo)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr

    result = rig_cli("wb", "contract", task_id, "--json", cwd=rig_git_repo)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "acceptable"
    assert payload["final_status"] == "acceptable"


def test_specific_contract_exits_two_with_an_execution_error_rather_than_a_verdict(
        rig_cli, rig_git_repo):
    """2 is `execution-error`, and this command is where the distinction was bought:
    the machinery underneath exits 1 for an unreadable task, and `contract` translates
    that into 2 so a caller is never told "no" by something that could not look."""
    result = rig_cli("wb", "contract", "no-such-task-id-9999", "--json", cwd=rig_git_repo)
    assert result.returncode == 2, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "execution-error"
    assert payload["receipt"] is None


# ── specific: wb gate (0 / 1 / 2 / 3) ────────────────────────────────────────
def test_specific_gate_keeps_judged_failed_and_not_yet_judged_apart(rig_cli, rig_git_repo):
    """Five gate states, one task, in the order an operator meets them.

      pending       3    no verdict: criteria nobody has answered
      failed        1    a verdict on the work
      skipped       3    no verdict: a gate everybody declined to answer
      pw/warnings   0    a verdict, with something unsettled in it
      passed        0    a verdict, clean

    3 is the code this test was written for: `gate` used to answer 0 while criteria were
    still `pending`, so a CI step or another agent branching on `$?` read "nobody has
    judged this yet" as "this passed" — the vacuous pass, arriving through the exit status
    instead of through acceptance.json. An all-`skipped` gate shares it because it is the
    same condition from the other side, and because `accept` refuses both: a 0 there would
    tell a caller the opposite of what the next command is about to say. It is the same
    meaning 3 already carries for `wb contract` (`pending`) and for the orchestrator (a
    step parked on a human gate).

    The fourth code, 2, is the refused `--set` of a sensor-backed criterion. It is driven
    in tests/test_gate_sensor_authority.py, which has the sensors' own fixtures, and it
    belongs to the operator's declaration rather than to the gate's verdict.
    """
    task_id, worktree = _new_task(rig_cli, rig_git_repo, "not-yet-judged")
    (worktree / "NEW.md").write_text("a real change\n", encoding="utf-8")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-q", "-m", "add NEW.md")

    # Nothing recorded: every criterion is pending.
    pending = rig_cli("wb", "gate", task_id, cwd=rig_git_repo)
    assert pending.returncode == 3, pending.stdout + pending.stderr
    assert "[PENDING]" in pending.stdout
    assert "criteria still pending" in pending.stdout
    # And it is not the verdict codes: a caller that folded 3 into either would act on a
    # judgement nobody made.
    assert pending.returncode != OK and pending.returncode != REJECTED

    # One criterion judged `failed`, the rest still pending: `failed` outranks `pending`,
    # and a verdict is a 1.
    failed = rig_cli("wb", "gate", task_id, "--set", "task_intent_satisfied=failed",
                     cwd=rig_git_repo)
    assert failed.returncode == REJECTED, failed.stdout + failed.stderr
    assert "[FAILED]" in failed.stdout

    # Every criterion declined: a gate that judged nothing, which is not a verdict either.
    skipped = rig_cli("wb", "gate", task_id,
                      *(arg for name in _FEATURE_GATE_CRITERIA
                        for arg in ("--set", f"{name}=skipped")),
                      cwd=rig_git_repo)
    assert skipped.returncode == 3, skipped.stdout + skipped.stderr
    assert "[SKIPPED]" in skipped.stdout

    # All fifteen recorded, one of them a warning: `passed_with_warnings` is a decided
    # gate and shares 0 with a clean pass, because a warning does not block accept.
    warned = rig_cli("wb", "gate", task_id,
                     *(arg for name in _FEATURE_GATE_CRITERIA
                       for arg in ("--set", f"{name}=passed")),
                     "--set", "task_intent_satisfied=warning:未確認",
                     cwd=rig_git_repo)
    assert warned.returncode == OK, warned.stdout + warned.stderr
    assert "[PASSED_WITH_WARNINGS]" in warned.stdout

    passed = rig_cli("wb", "gate", task_id,
                     *(arg for name in _FEATURE_GATE_CRITERIA
                       for arg in ("--set", f"{name}=passed")),
                     cwd=rig_git_repo)
    assert passed.returncode == OK, passed.stdout + passed.stderr
    assert "[PASSED]" in passed.stdout


# ── specific: govern can (0 / 3) ─────────────────────────────────────────────
def test_specific_govern_can_exits_zero_for_a_permission_the_actor_holds_and_three_otherwise(
        rig_cli, rig_git_repo):
    """One repository, one actor, two permissions. The starter policy `govern init`
    writes puts the caller in `developer` + `quality-owner` and keeps `policy.publish`
    with `policy-admin`, so both answers come from the same policy — which is what
    makes the 3 a denial rather than a differently-configured repository."""
    env = {"RIG_ACTOR": "rig test"}
    started = rig_cli("govern", "init", "--org", "acme", "--team", "team-a",
                      cwd=rig_git_repo, env=env)
    assert started.returncode == 0, started.stdout + started.stderr

    allowed = rig_cli("govern", "can", "accept", cwd=rig_git_repo, env=env)
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    assert "allowed" in allowed.stdout

    denied = rig_cli("govern", "can", "policy.publish", cwd=rig_git_repo, env=env)
    assert denied.returncode == 3, denied.stdout + denied.stderr
    assert "denied" in denied.stdout


# ── specific: the orchestrator's human gate (3) ──────────────────────────────
_HUMAN_GATE_RECIPE = """---
name: staged
description: two stages, the second gated on a person
scope: project
autonomy: interactive
steps:
  - id: implement
    instruction: implement
    gate: acceptance-gate
    acceptance: ["it builds"]
    checks: ["true"]
  - id: architecture_review
    instruction: verify
    actor: architect
    human_gate: true
    gate: acceptance-gate
    acceptance: ["ADR updated"]
    checks: ["true"]
---
"""

_HUMAN_GATE_POLICY = {
    "schema": "rig.policy/v2", "id": "acme", "scope": "org", "org": "acme",
    "roles": {"developer": ["task.new", "gate.set", "accept", "discard"],
              "architect": ["approve", "accept"]},
    "members": {"alice": ["developer"], "olivia": ["architect"]},
}


def test_specific_the_orchestrator_exits_three_when_a_step_parks_on_a_human_gate(
        rig_cli, rig_git_repo, tmp_path):
    """3 here means "parked", which is neither a pass nor a failure and must not be
    read as either: the run stopped on purpose and is waiting for a named person.

    Driven through `rig-wb`, not through `scripts/orchestrate.py` directly. That file
    is a four-line shim over `rig_workbench.orchestrate.cli:main` and `rig-wb` delegates
    `init` / `check` / `verdict` / `next` to the same `COMMANDS` table, so this exercises
    the same code by the spelling the installed CLI actually offers."""
    recipes = rig_git_repo / ".rig" / "recipes"
    recipes.mkdir(parents=True, exist_ok=True)
    (recipes / "staged.md").write_text(_HUMAN_GATE_RECIPE, encoding="utf-8")
    policy = rig_git_repo / ".rig" / "policy"
    policy.mkdir(parents=True, exist_ok=True)
    (rig_git_repo / ".rig" / "org.json").write_text(json.dumps(
        {"schema": "rig.org/v2", "org": "acme", "team": "team-a",
         "policy_layers": [".rig/policy/org.json"]}), encoding="utf-8")
    (policy / "org.json").write_text(json.dumps(_HUMAN_GATE_POLICY), encoding="utf-8")

    env = {"RIG_ACTOR": "alice", "RIG_ALLOW_PROJECT_RECIPES": "1",
           "RIG_TRUST_STORE": str(tmp_path / "trusted-recipes.json")}

    def orchestrate(*args):
        result = rig_cli(*args, cwd=rig_git_repo, env=env)
        return result

    # Drive the first stage to a pass, then start the gated one and pass it too. Each
    # of these is an ordinary 0; only the final `next` has anywhere unusual to go.
    for argv in (("init", ".rig/recipes/staged.md"),
                 ("check",),
                 ("verdict", "run-state.json", "--by", "test-verifier", "--pass",
                  "--criterion", "1=PASS"),
                 ("next",),
                 ("next",),
                 ("check",),
                 ("verdict", "run-state.json", "--by", "test-verifier", "--pass",
                  "--criterion", "1=PASS")):
        step = orchestrate(*argv)
        assert step.returncode == 0, f"{argv}\n{step.stdout}{step.stderr}"

    parked = orchestrate("next")
    assert parked.returncode == 3, parked.stdout + parked.stderr
    assert "AWAIT_APPROVAL" in parked.stdout

    # And it keeps parking: 3 is a standing state, not a one-off notification a poller
    # could miss and then read as progress.
    again = orchestrate("next")
    assert again.returncode == 3, again.stdout + again.stderr


# ── specific: design-constraints (0 / 1 / 2) ─────────────────────────────────
_CONSTRAINTS = {
    "version": 1,
    "tokens": {"color": {"brand": "#0a84ff"}},
    "prohibited": [{"pattern": "とりあえず", "why": "決めきっていない語を仕様に残さない"}],
}


def test_specific_design_constraints_keeps_clean_violating_and_unchecked_apart(
        rig_cli, tmp_path):
    """Three codes from one command, and the third is the reason the command exists:
    2 says the declaration is there and could not be checked, so nobody gets to read
    "it did not run" as "it passed"."""
    (tmp_path / "c.json").write_text(json.dumps(_CONSTRAINTS, ensure_ascii=False),
                                     encoding="utf-8")
    (tmp_path / "clean.md").write_text("Use the `brand` colour token only.\n", encoding="utf-8")
    (tmp_path / "dirty.md").write_text(
        "Header uses #ff0000 and the copy says とりあえず.\n", encoding="utf-8")
    (tmp_path / "broken.json").write_text("not json{", encoding="utf-8")

    clean = rig_cli("design-constraints", "--constraints", "c.json", "clean.md", cwd=tmp_path)
    assert clean.returncode == 0, clean.stdout + clean.stderr

    violating = rig_cli("design-constraints", "--constraints", "c.json", "dirty.md",
                        cwd=tmp_path)
    assert violating.returncode == 1, violating.stdout + violating.stderr
    assert "[raw-value]" in violating.stdout
    assert "[prohibited-expression]" in violating.stdout

    unchecked = rig_cli("design-constraints", "--constraints", "broken.json", "clean.md",
                        cwd=tmp_path)
    assert unchecked.returncode == 2, unchecked.stdout + unchecked.stderr

    # `--constraints` naming a file that is not there is also 2, and deliberately so:
    # a typo in the path is not "this project declares nothing".
    missing = rig_cli("design-constraints", "--constraints", "absent.json",
                      "--if-configured", "clean.md", cwd=tmp_path)
    assert missing.returncode == 2, missing.stdout + missing.stderr


def test_specific_design_constraints_exits_zero_when_the_project_declared_nothing(
        rig_cli, tmp_path):
    """not-configured is a third state, and it shares 0 with "clean" on purpose: a
    project that never declared design constraints has nothing to fail. It is only
    reachable behind `--if-configured`, so nobody gets it by accident."""
    (tmp_path / "artifact.md").write_text("Anything at all.\n", encoding="utf-8")
    result = rig_cli("design-constraints", "--if-configured", "artifact.md", cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "未設定" in result.stdout


# ── specific: ja-lint (0 / 1 / 2) ────────────────────────────────────────────
def test_specific_ja_lint_keeps_clean_error_and_unchecked_apart(rig_cli, tmp_path):
    """The same three-way split as design-constraints, on prose.

    The warning case is what makes the 0 worth asserting: a ら抜き finding is real and
    reported, and it still exits 0, because `ja-lint` reserves 1 for error-severity
    rules. A caller that took any output as failure would gate on style advice."""
    (tmp_path / "clean.md").write_text("これは短い文です。\n", encoding="utf-8")
    (tmp_path / "warned.md").write_text("この機能は食べれる人が来たら使えます。\n",
                                        encoding="utf-8")
    (tmp_path / "error.md").write_text("ｶﾀｶﾅが混ざっています。\n", encoding="utf-8")
    (tmp_path / "broken.json").write_text("not json{", encoding="utf-8")

    clean = rig_cli("ja-lint", "clean.md", cwd=tmp_path)
    assert clean.returncode == 0, clean.stdout + clean.stderr

    warned = rig_cli("ja-lint", "warned.md", cwd=tmp_path)
    assert warned.returncode == 0, warned.stdout + warned.stderr
    assert "warning" in warned.stdout

    failing = rig_cli("ja-lint", "error.md", cwd=tmp_path)
    assert failing.returncode == 1, failing.stdout + failing.stderr
    assert "no-hankaku-kana" in failing.stdout

    unchecked = rig_cli("ja-lint", "--config", "broken.json", "clean.md", cwd=tmp_path)
    assert unchecked.returncode == 2, unchecked.stdout + unchecked.stderr


def test_specific_ja_lint_exits_zero_when_there_is_nothing_configured_to_check(
        rig_cli, tmp_path):
    result = rig_cli("ja-lint", "--if-configured", cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "未設定" in result.stdout


# ── specific: bench (1 / 2; 0 is in NOT_PINNED) ──────────────────────────────
def test_specific_bench_exits_one_when_the_run_finished_without_reaching_a_pass(
        rig_cli, rig_git_repo):
    """1 here is `completed non-pass`: the benchmark ran, scored, and the verdict was
    not `pass`. One packaged task with `--provider mock` is enough to produce it — the
    score's validity rules want ten — and the assertion that matters is that a
    non-pass is reported as a verdict, with a full report, rather than as an error.

    Run inside a git repository on purpose: the arms snapshot their workspace with
    git, and outside one every arm is an infrastructure error, which reaches the same
    exit code by a route that says nothing about scoring."""
    result = rig_cli("bench", "--provider", "mock", "--tasks", "py-api-compat-rename",
                     "--runs", "1", cwd=rig_git_repo,
                     timeout=subprocess_timeout(_BENCH_MEASURED_SECONDS))
    assert result.returncode == 1, result.stdout[-2000:] + result.stderr[-2000:]
    report = json.loads(result.stdout)
    assert report["schema_version"] == 2
    assert report["score"]["verdict"] != "pass"
    assert report["score"]["infra_error_rate"] == 0.0


def test_specific_bench_exits_two_when_it_was_asked_for_something_it_cannot_run(
        rig_cli, rig_git_repo):
    """2 is `CLI/schema error` — no benchmark happened, so there is no verdict to
    read. Distinct from the 1 above, where there is a full report to look at."""
    result = rig_cli("bench", "--provider", "no-such-provider", cwd=rig_git_repo)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "invalid choice" in result.stderr


# ── the record of what could not be driven ───────────────────────────────────
def test_specific_the_unpinnable_codes_are_recorded_with_the_reason_they_are_unpinnable():
    """Not a behaviour assertion — a guard on the note above.

    `NOT_PINNED` is the honest part of this file: it names a documented exit code no
    test process can produce. It is worth an assertion only so that an entry cannot be
    left half-written, and so that anyone deleting one has to look at what it says
    first."""
    assert NOT_PINNED, "an empty NOT_PINNED means every documented code is pinned — say so"
    for command, code, reason in NOT_PINNED:
        assert command.startswith("rig-wb") or command.endswith(".py"), command
        assert isinstance(code, int)
        assert len(reason) > 80, f"{command} exit {code}: the reason has to be a reason"


def test_generic_this_suite_runs_the_checkout_and_not_some_other_installed_rig(tmp_path):
    """A guard on every assertion above.

    Each of them is worth exactly as much as the claim that the subprocess is running
    *this* checkout. `rig_cli` puts the repo root at the front of PYTHONPATH for that
    reason, and a machine with a released `rig-wb` installed is where a silent swap
    would happen — every exit code would still be plausible, and none of them would be
    about the tree under test."""
    probe = subprocess.run(
        [sys.executable, "-c",
         "import rig_workbench, pathlib; print(pathlib.Path(rig_workbench.__file__).parent)"],
        capture_output=True, text=True, cwd=str(tmp_path),
        env=dict(os.environ, PYTHONPATH=os.pathsep.join(
            p for p in (str(REPO_ROOT), os.environ.get("PYTHONPATH")) if p)),
        timeout=subprocess_timeout(2.0))
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert probe.stdout.strip() == str(REPO_ROOT / "rig_workbench")
