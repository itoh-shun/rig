"""What the first run costs — §9 of docs/v3-architecture-design-brief.ja.md, as a measurement.

§9（会話を正本にし、最初の1回に何も要求しない）names the weakness every rig version has carried:
not what it does, but how much a person has to do before it does anything at all. The documented
way in opens with `/rig:setup` (install the `rig-wb` CLI) and `/rig:init` (scaffold
`.claude/rig.md`), and an investigation established that neither is actually required. In a bare
`git init` repository — no manifest, no `rig-wb` anywhere on PATH, no pack trust recorded —
`python3 scripts/workbench.py new "<task>" --type bugfix` exits 0 and produces a run. The barrier
§9 measures is documentary, not technical: the guidance asks for steps the code does not.

The true requirements are two: be a git repository, and have `python3`.

That was an observation somebody made once. This file is the same claim as a measurement, so a
prerequisite creeping back into the first run fails a test rather than quietly becoming true
again. §9 asks for exactly that discipline — 「主張ではなく測定に置く」.

Three groups, selectable with `-k`:

    pytest tests/test_first_run_cost.py                 # the zero-step first run, end to end
    pytest tests/test_first_run_cost.py -k degrade      # each prerequisite, separately
    pytest tests/test_first_run_cost.py -k gitignore    # the one tracked file it may write

The consent *mechanism* — the terminal prompt, what counts as standing consent, and the
`wb import` call site — lives in tests/test_gitignore_consent.py. What stays here is what §9
measures: the cost of the first run, in the environment §9 names.

The first drives the real command in an environment built to be hostile to a hidden
prerequisite: `rig-wb` unreachable on PATH (verified with `shutil.which` inside the child, not
assumed), every consent variable removed from the environment, and stdin at /dev/null so a
question meant for a human fails loudly instead of hanging a CI runner forever.

The three `degrade` tests take the prerequisites one at a time, so a regression names which one
came back instead of reporting only "the first run broke":

    tier      — routing resolves in the shipped `core` tier, which is *why* no pack trust is
                demanded; project and user tier are where consent is asked for.
    hostcheck — advisory (exit 0, or 3 for a missing prerequisite), never the `--strict`
                failure (exit 1). See rig_workbench/hostcheck.py's docstring for the codes.
    manifest  — an unconsented `.claude/rig.md` degrades to one warning on stderr (the
                `require=False` path in rig_workbench/orchestrate/recipes.py), rather than the
                exit-2 refusal with consent instructions that the `require=True` path prints.

The `gitignore` pair is the other half of the same discipline. `new` used to append `.rig/`
to the repository's `.gitignore` on its own, which is not a step the person has to take — it is
a change they never asked for, in a file they own. Consent moved that write behind a question
on a terminal and behind `RIG_ALLOW_GITIGNORE=1` off one; these two pin both ends, so neither
the silent write nor a prompt on the CI path can come back unnoticed.

If something here fails, a step has come back into the first run. That may even be right — a
real security gate can be worth a step — but it is a decision about §9's target, so make it in
review and move the measurement on purpose.
"""

import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

from conftest import REPO_ROOT, subprocess_timeout

# The entry path §9 measured: the script, not the installed console script, because the whole
# point is that the console script does not have to exist yet.
WORKBENCH_SCRIPT = REPO_ROOT / "scripts" / "workbench.py"

#: The first run, verbatim from §9's investigation.
FIRST_RUN_ARGV = ("new", "fix the login bug", "--type", "bugfix")

#: Every consent variable rig reads (`grep -o 'RIG_ALLOW_[A-Z_]*' rig_workbench scripts`). All of
#: them are removed from the child's environment: a first run that only works because the
#: developer's shell already carries one of these is not a zero-step first run.
CONSENT_VARIABLES = (
    "RIG_ALLOW_PROJECT_PACKS",
    "RIG_ALLOW_PROJECT_MANIFEST",
    "RIG_ALLOW_PROJECT_RECIPES",
    # Consent to append `.rig/` to the repository's `.gitignore` — the one tracked file `new`
    # writes. It joined this list when the write stopped being silent: with the variable set,
    # `new` writes without asking, and a first run that only passes because the developer's
    # shell carries it would be measuring the consented path, not the zero-step one.
    "RIG_ALLOW_GITIGNORE",
)

# Measured, not guessed, as conftest.subprocess_timeout asks: `new` in a scratch repo costs
# 0.60s wall on a developer machine (`real 0m0.604s`), so every call here takes the 30s floor.
# The number is recorded so a future slower first run has something real to scale from.
FIRST_RUN_MEASURED_SECONDS = 1.0

# `new` reports where it put the run; these read it back. Discovering the id this way rather
# than globbing `.rig/runs/*` or rebuilding the `rig-<date>-<slug>` shape is deliberate: the id
# format is not this file's contract, and a test that guesses it would fail on a format change
# that is nobody's regression.
TASK_ID_LINE = re.compile(r"^task_id:[ \t]*(\S+)[ \t]*$", re.MULTILINE)
STATE_LINE = re.compile(r"^state:[ \t]*(\S+)[ \t]*$", re.MULTILINE)

#: Shapes a question for a human takes on a terminal. Checked case-insensitively against both
#: streams. Today no `input()` call exists anywhere in rig_workbench, so this is a tripwire for
#: the first one to appear on the `new` path rather than a check of current behaviour.
PROMPT_SHAPES = ("[y/n]", "(y/n)", "[yes/no]", "press enter", "press return",
                 "type yes", "enter your", "continue? ")

#: What a prompt actually does when stdin is /dev/null: `input()` raises, and the traceback
#: reaches stderr. Cheaper and more certain than pattern-matching the prompt text itself.
STDIN_FAILURE_MARKERS = ("EOFError", "Traceback (most recent call last)")


def _holds_rig_wb(directory: str) -> bool:
    """True if `rig-wb` would be found in this PATH entry (any platform's spelling)."""
    for name in ("rig-wb", "rig-wb.exe", "rig-wb.cmd", "rig-wb.bat"):
        try:
            if (pathlib.Path(directory) / name).exists():
                return True
        except OSError:
            # An unreadable or malformed PATH entry cannot hold a reachable executable
            # either; treating it as clean keeps a broken $PATH from failing this file.
            continue
    return False


def _path_that_cannot_reach_rig_wb() -> str:
    """The ambient PATH minus every directory holding a `rig-wb`.

    Subtractive rather than a hand-built sandbox of symlinks: `new` shells out to `git`, and a
    minimal bin/ that has to enumerate every tool the run might reach would fail on the machine
    whose git lives somewhere the list did not predict. Removing exactly what must not be
    reachable keeps everything else exactly as the developer has it — and the child verifies the
    result instead of trusting it (see `first_run_env`).
    """
    return os.pathsep.join(entry for entry in os.environ.get("PATH", "").split(os.pathsep)
                           if entry and not _holds_rig_wb(entry))


@pytest.fixture
def first_run_env():
    """The environment §9's claim is made in, verified before any test spends time on it.

    An overlay on the inherited environment, so conftest's isolation (RIG_HOME, the redirected
    trust stores and global runs log) survives — none of those grant anything, they only keep
    the suite out of the developer's real home. What this removes is what a first run must not
    need: the CLI on PATH and every consent variable.

        env = first_run_env()                       # hostile, verified
        env = first_run_env(RIG_TRUST_STORE=path)   # ...plus a per-test override

    The verification is a real child process with this exact environment, because the failure it
    guards against is silent: if `rig-wb` were still reachable, every test below would pass while
    measuring nothing.
    """
    sandbox_path = _path_that_cannot_reach_rig_wb()

    def build(**overlay) -> dict:
        env = dict(os.environ,
                   # The tree under test, not an install — same reason as conftest's `rig_cli`.
                   PYTHONPATH=os.pathsep.join(
                       p for p in (str(REPO_ROOT), os.environ.get("PYTHONPATH")) if p),
                   PYTHONIOENCODING="utf-8",
                   PYTHONUTF8="1",
                   PATH=sandbox_path)
        for variable in CONSENT_VARIABLES:
            env.pop(variable, None)
        for key, value in overlay.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = str(value)
        return env

    probe = subprocess.run(
        [sys.executable, "-c",
         "import json, shutil; print(json.dumps({'rig-wb': shutil.which('rig-wb'), "
         "'git': shutil.which('git')}))"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=build(), stdin=subprocess.DEVNULL,
        timeout=subprocess_timeout(FIRST_RUN_MEASURED_SECONDS))
    reachable = json.loads(probe.stdout)
    assert reachable["rig-wb"] is None, (
        "the environment these tests claim to measure in is not the one they built: the child "
        f"can still reach the rig-wb CLI at {reachable['rig-wb']}. Every assertion below about "
        "the first run needing no CLI install would pass without measuring anything. "
        "_holds_rig_wb() no longer recognises how rig-wb is installed on this machine.")
    assert reachable["git"] is not None, (
        "removing the rig-wb directories from PATH also removed git, so the first run below "
        "would fail on a missing git rather than on anything §9 is about. rig-wb is installed "
        "into the same directory as git on this machine; _path_that_cannot_reach_rig_wb() needs "
        "to keep that directory and hide the executable another way.")
    return build


def _first_run(repo, env, *argv):
    """`python3 scripts/workbench.py <argv>` in `repo`, with stdin closed off.

    stdin at /dev/null is load-bearing, not hygiene: it is what turns "the first run asks a
    human something" from a CI job that hangs until the runner's own timeout into an EOFError
    this test can read. Never `check=True` — the exit code is the measurement.
    """
    return subprocess.run(
        [sys.executable, str(WORKBENCH_SCRIPT), *argv],
        cwd=str(repo), stdin=subprocess.DEVNULL, capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env,
        timeout=subprocess_timeout(FIRST_RUN_MEASURED_SECONDS))


def _both_streams(result) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _reported(pattern, label, result) -> str:
    """Read one `<label>: <value>` line out of the command's own report."""
    match = pattern.search(result.stdout)
    assert match, (
        f"`workbench.py new` no longer reports its `{label}:` line, so this test cannot find "
        "the run it just created without guessing the id format — which is exactly what it "
        "refuses to do. The command's report is the interface a person reads too: if the line "
        f"moved, a human following the output has lost the same thing.\n{_both_streams(result)}")
    return match.group(1)


# ═════════════════════════════════════════════════════════════════════════════
# The zero-step first run
# ═════════════════════════════════════════════════════════════════════════════
def test_a_first_run_in_a_bare_git_repository_needs_no_cli_install_no_manifest_and_no_human(
        rig_git_repo, first_run_env):
    """§9's decisive observation, re-measured: git plus python3 is the whole prerequisite list.

    `rig_git_repo` is a `git init` with one commit and nothing else — no `.claude/rig.md`, no
    `.rig/`, no pack trust — which is the state of the repository a person has when they first
    hear about rig. The environment removes the CLI and every consent variable, and closes
    stdin. What is left is what §9 says must be enough.
    """
    env = first_run_env()
    result = _first_run(rig_git_repo, env, *FIRST_RUN_ARGV)

    assert result.returncode == 0, (
        f"the first run now costs a step. `python3 scripts/workbench.py {' '.join(FIRST_RUN_ARGV)}` "
        f"exited {result.returncode} in a plain `git init` repository with no rig-wb on PATH, no "
        ".claude/rig.md, no RIG_ALLOW_* consent variable and stdin at /dev/null. §9 of "
        "docs/v3-architecture-design-brief.ja.md declares the first run free of preparation; "
        "whatever the command demands below is the prerequisite that came back.\n"
        f"{_both_streams(result)}")

    for marker in STDIN_FAILURE_MARKERS:
        assert marker not in result.stderr, (
            f"the first run tried to read from stdin ({marker} on stderr). Something on the "
            "`new` path now asks a human to decide before rig will start — a step §9 requires "
            "the first run not to have — and with stdin at /dev/null it cannot even be "
            f"answered.\n{_both_streams(result)}")
    printed = (result.stdout + result.stderr).lower()
    asked = [shape for shape in PROMPT_SHAPES if shape in printed]
    assert not asked, (
        f"the first run printed a question for a human: {asked}. Nothing in rig_workbench "
        "called input() when this test was written, so a prompt on the `new` path is new. §9 "
        "counts 「実行前に人が決めなければならない項目の数」 — this raises it from zero.\n"
        f"{_both_streams(result)}")

    # The run id comes from the command's own report; this file pins no id format.
    task_id = _reported(TASK_ID_LINE, "task_id", result)
    reported_state_dir = rig_git_repo / _reported(STATE_LINE, "state", result)
    task_json = rig_git_repo / ".rig" / "runs" / task_id / "task.json"
    assert task_json.is_file(), (
        f"the first run exited 0 and reported task_id {task_id}, but wrote no "
        f"{task_json.relative_to(rig_git_repo)}. Exit 0 without a run on disk is worse than a "
        "refusal: the person is told the first run worked and has nothing to continue from.\n"
        f"{_both_streams(result)}")
    assert reported_state_dir.resolve() == task_json.parent.resolve(), (
        f"`new` reported its state at {reported_state_dir} while the run it created is at "
        f"{task_json.parent}. A person following the output is being sent to the wrong "
        f"directory.\n{_both_streams(result)}")

    recorded = json.loads(task_json.read_text(encoding="utf-8"))
    assert recorded.get("task_id") == task_id, (
        f"task.json records task_id {recorded.get('task_id')!r} while the command reported "
        f"{task_id!r}. The first run produced a run nobody can address by the id they were "
        "given.")


# ═════════════════════════════════════════════════════════════════════════════
# The one tracked file the first run can write — `-k gitignore`
# ═════════════════════════════════════════════════════════════════════════════
def test_the_first_run_leaves_gitignore_alone_when_there_is_nobody_to_ask(
        rig_git_repo, first_run_env):
    """No consent, no write: `.gitignore` is the operator's file, and stdin is /dev/null here.

    Everything else `new` writes lives under `.rig/`, which is rig's own state directory.
    `.gitignore` is tracked content, so appending to it silently puts a line in somebody's next
    commit that they never typed — in a repository rig was only asked to register a task in.
    Off a terminal there is no one to ask, and a question that cannot be answered is either a
    hang or a yes nobody gave, so the run says which line to add and writes nothing.

    Said here rather than in a `new`-specific file because this is also §9's territory: the
    line has to be *advice*, not a prompt, or the zero-step first run has grown a step.
    """
    env = first_run_env()
    before = (rig_git_repo / ".gitignore").exists()
    result = _first_run(rig_git_repo, env, *FIRST_RUN_ARGV)

    assert result.returncode == 0, _both_streams(result)
    assert (rig_git_repo / ".gitignore").exists() is before, (
        "the first run wrote .gitignore in a repository where nothing consented to it and "
        "nothing could be asked (stdin at /dev/null). The next `git status` in that repository "
        f"shows a change its owner did not make.\n{_both_streams(result)}")
    printed = result.stdout + result.stderr
    assert ".rig/" in printed and ".gitignore" in printed, (
        "the run neither wrote .gitignore nor said anything about it, so the reason `.rig/` is "
        "still turning up in `git status` is now invisible. Refusing to write silently is the "
        f"same defect as writing silently, pointed the other way.\n{_both_streams(result)}")


def test_standing_consent_writes_gitignore_without_asking_anything(
        rig_git_repo, first_run_env):
    """`RIG_ALLOW_GITIGNORE=1` is the answer given in advance — the shape every other consent
    rig records already has (`RIG_ALLOW_PROJECT_PACKS` and its kin in
    rig_workbench/packs/trust.py). It has to be enough on its own: an escape hatch that still
    stops to ask is one people route around with something worse.
    """
    env = first_run_env(RIG_ALLOW_GITIGNORE="1")
    result = _first_run(rig_git_repo, env, *FIRST_RUN_ARGV)

    assert result.returncode == 0, _both_streams(result)
    ignored = (rig_git_repo / ".gitignore").read_text(encoding="utf-8")
    assert ".rig/" in ignored, (
        "RIG_ALLOW_GITIGNORE=1 was set and .rig/ still is not ignored. Standing consent that "
        f"does not act is indistinguishable from no consent at all.\n{_both_streams(result)}")
    for marker in STDIN_FAILURE_MARKERS:
        assert marker not in result.stderr, (
            f"consent was already given and the run asked anyway ({marker} on stderr).\n"
            f"{_both_streams(result)}")


# ═════════════════════════════════════════════════════════════════════════════
# Each prerequisite's absence, separately — `-k degrade`
# ═════════════════════════════════════════════════════════════════════════════
def test_the_first_run_degrades_to_the_shipped_core_tier_instead_of_demanding_pack_trust(
        rig_cli_json, rig_git_repo, first_run_env):
    """Why no consent is asked for: the default route never leaves the tier that needs none.

    Pack trust is per-asset and per-tier — a project- or user-tier asset has to be approved
    (`--allow-project-packs`, `RIG_ALLOW_PROJECT_PACKS=1`) before it is used. The first run
    escapes that not because the gate is lenient but because `bugfix` resolves inside the
    shipped `core` tier, where there is nothing to approve. Route through anything else by
    default and §9's 「パックの信頼承認」 row comes straight back into the first run.
    """
    payload = rig_cli_json("wb", "route", "--type", "bugfix", "--json",
                           cwd=rig_git_repo, env=first_run_env(), expect_returncode=0)

    assert payload.get("tier") == "core", (
        f"the default bugfix route now resolves in the {payload.get('tier')!r} tier, not "
        "'core'. Pack trust comes back with it: an asset outside the shipped tier has to be "
        "consented to before the run may use it (--allow-project-packs, or "
        "RIG_ALLOW_PROJECT_PACKS=1, recorded per content hash), and that consent is a step "
        "before the first useful result — the one §9 rules out.\n"
        f"full route: {json.dumps(payload, indent=2, sort_keys=True)}")


def test_hostcheck_degrades_to_advisory_in_a_first_run_and_never_exits_the_strict_failure(
        rig_cli, rig_git_repo, first_run_env):
    """Advisory, not a gate: exit 0 or 3, and 1 only when somebody passed `--strict`.

    rig_workbench/hostcheck.py's docstring fixes the codes — 0 = every prerequisite present,
    3 = at least one missing (advisory), and 1 = a missing prerequisite under `--strict`, for
    CI to block on. A first run is exactly the situation where prerequisites *are* missing: no
    devcontainer, no deny rules, no gh. If that ever exits 1 without --strict, a machine
    checklist has become the first step of using rig.
    """
    result = rig_cli("hostcheck", cwd=rig_git_repo, env=first_run_env())

    assert result.returncode in (0, 3), (
        f"`hostcheck` exited {result.returncode} in a fresh repository. It is advisory: 0 when "
        "every prerequisite is present, 3 when one is missing. "
        + ("Exit 1 is the --strict failure, and nothing here passed --strict — a missing host "
           "prerequisite is now blocking rather than reporting, which puts host setup in front "
           "of the first run."
           if result.returncode == 1 else
           "That code is outside hostcheck's documented set entirely (see the exit-code "
           "paragraph in rig_workbench/hostcheck.py's module docstring).")
        + f"\n{_both_streams(result)}")


def test_an_unconsented_project_manifest_degrades_to_one_warning_rather_than_stopping_the_run(
        rig_cli, rig_git_repo, first_run_env, tmp_path):
    """The soft half of the consent gate: ignore the manifest, say so once, keep going.

    `ensure_manifest_trusted` in rig_workbench/orchestrate/recipes.py has two paths. With
    `require=True` it exits 2 and prints consent instructions, which is right where a person
    explicitly asked for manifest-driven behaviour. The default is `require=False`: warn once
    on stderr, ignore the file, continue on built-in defaults. §9's 「manifest 無しで完走できる
    か」 needs the second one, and needs it for a manifest that merely *exists* as well as for
    one that is absent — a repository someone cloned has a manifest they have never consented
    to, and that must not be a wall on the way to a first result.

    The trust store is a fresh file per test, so "not consented" is a fact about this run
    rather than an accident of what the developer's store happens to hold.
    """
    manifest = rig_git_repo / ".claude" / "rig.md"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("---\ndefault_flags: []\n---\n\n# project manifest\n", encoding="utf-8")
    env = first_run_env(RIG_TRUST_STORE=tmp_path / "no-manifest-consented.json")

    # The first run itself, with the unconsented manifest sitting in the repository.
    created = _first_run(rig_git_repo, env, *FIRST_RUN_ARGV)
    assert created.returncode == 0, (
        f"an unconsented .claude/rig.md now stops the first run (exit {created.returncode}). A "
        "manifest the person has never approved is the normal state of a repository they just "
        "cloned; refusing to start until they approve it puts /rig:init — or a consent flag — "
        "back in front of the first result, which is the barrier §9 exists to remove.\n"
        f"{_both_streams(created)}")

    # ...and a command that actually reads the manifest, where the warn path lives.
    read_it = rig_cli("wb", "compose-options", "--type", "bugfix", "--json",
                      cwd=rig_git_repo, env=env)
    assert read_it.returncode == 0, (
        f"`wb compose-options` exited {read_it.returncode} on an unconsented manifest. Exit 2 "
        "is the `require=True` refusal in ensure_manifest_trusted(); reaching it from a plain "
        "read means the hard path has become the default, and consent "
        "(--allow-project-manifest / RIG_ALLOW_PROJECT_MANIFEST=1) is now demanded before rig "
        f"will answer at all.\n{_both_streams(read_it)}")

    warnings = [line for line in read_it.stderr.splitlines()
                if "untrusted project manifest" in line]
    assert len(warnings) == 1, (
        f"expected exactly one warning that the manifest was ignored, got {len(warnings)}: "
        f"{warnings}. Zero means the manifest was used without consent, which is the gate "
        "failing open — repo-controlled content driving recipe search paths and the hooks' "
        "lint/build/test commands. More than one is the same fact said repeatedly, which "
        f"reads like an error and teaches people to expect a wall.\n{_both_streams(read_it)}")
    assert str(manifest.resolve()) in warnings[0], (
        f"the warning does not name the file it ignored: {warnings[0]!r}. A person cannot act "
        f"on it without knowing which manifest is meant ({manifest}).")

    try:
        json.loads(read_it.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"the degraded run's stdout is no longer valid JSON: {exc}. The consent warning "
            "belongs on stderr — stdout is the data channel, and a diagnostic on it breaks "
            f"every caller that pipes `--json` into a parser.\n{_both_streams(read_it)}",
            pytrace=False)
