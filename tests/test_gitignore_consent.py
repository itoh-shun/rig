"""Who decides that rig may edit your `.gitignore` — §11 T7a, as a measurement.

`.gitignore` is the one file outside `.rig/` that registering a task touches, and it is
*tracked content*. Appending to it on rig's own authority put a line in somebody's next commit
that they never typed, in a repository rig had only been asked to start a task in. The consent
that replaced that has three branches and no fourth: a standing yes in the environment, one y/N
question on a terminal, and — anywhere without one — no write and one line naming the entry to
add.

`tests/test_first_run_cost.py` keeps the two §9 measurements that belong to it: what the first
run costs in a bare repository with stdin at `/dev/null`. This file is the consent mechanism
itself, at both call sites and at every branch a subprocess cannot reach. The terminal-prompt
test moved here from that file for the same reason: `isatty()` is the whole condition of the
branch, and a pipe can never make it true, so the test has to be in-process against the ports.

What each branch is worth pinning for:

    import       — `wb import` is the second call site. Deleting the call from
                   `workbench/import_task.py` left the suite green, which means the defect
                   could come back on half the surface without anything noticing.
    env value    — `RIG_ALLOW_GITIGNORE` grants consent at `"1"` and at nothing else. A truthy
                   read (`if env.get(...)`) would make `RIG_ALLOW_GITIGNORE=0` a yes.
    isatty       — the predicate, not a stub of it: the True path, and the failure path that
                   has to answer "no terminal" rather than raise.
"""

import subprocess
import sys

import pytest

from conftest import REPO_ROOT, run_git, subprocess_timeout
from rig_workbench.workbench import lifecycle

WORKBENCH_SCRIPT = REPO_ROOT / "scripts" / "workbench.py"

#: Same order of magnitude as `new` (measured at 0.60s wall), so the 30s floor applies.
MEASURED_SECONDS = 2.0


class _Console:
    """The `Presenter` port, recording instead of printing."""

    def __init__(self):
        self.lines: list[str] = []

    def out(self, text: str = "") -> None:
        self.lines.append(text)

    def err(self, text: str = "") -> None:
        self.lines.append(text)


class _Env:
    """The `Env` port, answering from a dict rather than from the process."""

    def __init__(self, **values):
        self._values = values

    def get(self, name, default=None):
        return self._values.get(name, default)


def _git_repo(tmp_path):
    """A directory rig will treat as git-managed. `.git` existing is the whole condition."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return repo


# ═════════════════════════════════════════════════════════════════════════════
# The terminal branch
# ═════════════════════════════════════════════════════════════════════════════
def test_on_a_terminal_the_write_happens_only_when_the_person_says_yes(tmp_path, monkeypatch):
    """Somebody is there, so rig asks — and takes no for no.

    Both answers are pinned: a yes writes, and a bare Enter, which is the default, leaves the
    file exactly as it was rather than reading silence as agreement.
    """
    repo = _git_repo(tmp_path)
    answers, asked = iter(["", "y"]), []

    def _fake_input(question):
        asked.append(question)
        return next(answers)

    # `input` is a builtin the module looks up in its own globals first, so `raising=False`
    # is the shadow, not a typo — there is no attribute to replace until this puts one there.
    monkeypatch.setattr(lifecycle, "input", _fake_input, raising=False)
    monkeypatch.setattr(lifecycle, "_stdin_is_a_terminal", lambda: True)

    console = _Console()
    declined = lifecycle.ensure_rig_gitignored(repo, env=_Env(), out=console)
    assert declined is False and not (repo / ".gitignore").exists(), (
        "a bare Enter wrote .gitignore. The default of a y/N question is N, and a prompt whose "
        "default is to do the thing anyway is not consent, it is a delay.")
    accepted = lifecycle.ensure_rig_gitignored(repo, env=_Env(), out=console)
    assert accepted is True, "answering y did not write .gitignore"
    assert ".rig/" in (repo / ".gitignore").read_text(encoding="utf-8")

    assert len(asked) == 2 and all("[y/N]" in question for question in asked), (
        f"expected one y/N question per call, got {asked}. Asking twice for one decision, or "
        "asking without showing which answer is the default, both push a person toward "
        "answering whatever gets the prompt out of the way.")


def test_a_prompt_cut_short_is_a_no_and_not_a_traceback(tmp_path, monkeypatch):
    """Ctrl-C and EOF arrive at the question, not at a convenient moment. Neither is a yes,
    and neither may escape as a traceback out of a command that had already done its work."""
    repo = _git_repo(tmp_path)
    monkeypatch.setattr(lifecycle, "_stdin_is_a_terminal", lambda: True)

    for interruption in (EOFError, KeyboardInterrupt):
        def _raise(question, exc=interruption):
            raise exc

        monkeypatch.setattr(lifecycle, "input", _raise, raising=False)
        console = _Console()
        assert lifecycle.ensure_rig_gitignored(repo, env=_Env(), out=console) is False, (
            f"{interruption.__name__} at the prompt was taken as consent")
        assert not (repo / ".gitignore").exists()
        assert console.lines and console.lines[0].startswith("\n"), (
            "an interrupted prompt leaves the cursor on the question's line; the reply owes "
            f"the terminal a newline first. Got {console.lines!r}")


# ═════════════════════════════════════════════════════════════════════════════
# Is there a terminal at all
# ═════════════════════════════════════════════════════════════════════════════
def test_the_terminal_predicate_reports_what_stdin_says(monkeypatch):
    class _Stdin:
        def __init__(self, answer):
            self._answer = answer

        def isatty(self):
            return self._answer

    monkeypatch.setattr(sys, "stdin", _Stdin(True))
    assert lifecycle._stdin_is_a_terminal() is True
    monkeypatch.setattr(sys, "stdin", _Stdin(False))
    assert lifecycle._stdin_is_a_terminal() is False


@pytest.mark.parametrize("failure", [ValueError, OSError, AttributeError])
def test_a_stdin_that_cannot_answer_is_not_a_terminal(monkeypatch, failure):
    """`sys.stdin` is replaceable, closeable and sometimes absent: pythonw has it at None, a
    closed stream raises ValueError, a detached descriptor raises OSError. Every one of those
    means there is nobody to ask, and guessing yes would hang the run on a prompt nobody sees.
    """
    class _Stdin:
        def isatty(self):
            raise failure("no")

    monkeypatch.setattr(sys, "stdin", _Stdin())
    assert lifecycle._stdin_is_a_terminal() is False

    monkeypatch.setattr(sys, "stdin", None)
    assert lifecycle._stdin_is_a_terminal() is False


# ═════════════════════════════════════════════════════════════════════════════
# Standing consent, and what does not count as it
# ═════════════════════════════════════════════════════════════════════════════
def test_standing_consent_is_the_value_one_and_nothing_else(tmp_path, monkeypatch):
    """`RIG_ALLOW_GITIGNORE=1`, exactly — the shape `packs/trust.py` uses for every other
    consent rig records. A truthy test would make `RIG_ALLOW_GITIGNORE=0` a yes, which is how
    a variable somebody exported to turn the thing *off* ends up turning it on."""
    monkeypatch.setattr(lifecycle, "_stdin_is_a_terminal", lambda: False)

    for value in ("0", "2", "true", "yes", "", " 1"):
        repo = _git_repo(tmp_path / value.strip().replace(" ", "_") or tmp_path / "empty")
        written = lifecycle.ensure_rig_gitignored(
            repo, env=_Env(RIG_ALLOW_GITIGNORE=value), out=_Console())
        assert written is False and not (repo / ".gitignore").exists(), (
            f"RIG_ALLOW_GITIGNORE={value!r} was taken as consent")

    repo = _git_repo(tmp_path / "granted")
    assert lifecycle.ensure_rig_gitignored(
        repo, env=_Env(RIG_ALLOW_GITIGNORE="1"), out=_Console()) is True
    assert ".rig/" in (repo / ".gitignore").read_text(encoding="utf-8")


# ═════════════════════════════════════════════════════════════════════════════
# Which status lines count as rig's own state
# ═════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("entry,expected", [
    ("?? .rig/", True),
    (" M .rig/context.jsonl", True),
    ("R  old.py -> .rig/new", True),
    ("?? notes.txt", False),
    # A file whose name contains the rename separator. Splitting every line on ` -> ` reads
    # this as a rename *into* `.rig/` and exempts a path that has nothing to do with rig; the
    # status code is what says whether a line is a rename at all.
    ("?? a -> .rig/b", False),
])
def test_only_a_rename_line_carries_a_destination(entry, expected):
    from rig_workbench.workbench import accept

    assert accept._names_state(entry) is expected, entry



def test_import_asks_the_same_question_new_does_and_writes_nothing_off_a_terminal(
        rig_git_repo):
    """`wb import` is the other call site, and it was the unpinned half.

    Deleting `ensure_rig_gitignored(root)` from `workbench/import_task.py` left the whole suite
    green, and so did making it append without asking — the defect could return on half the
    surface with nothing to notice. An imported change lands in a repository somebody else's
    tool produced work in, which is if anything a worse place to edit a tracked file unasked.
    """
    # An import measures a change against a base, so head and base must differ — otherwise
    # the command stops before it reaches anything this test is about.
    run_git(rig_git_repo, "checkout", "-q", "-b", "external")
    (rig_git_repo / "outside.py").write_text("VALUE = 1\n", encoding="utf-8")
    run_git(rig_git_repo, "add", "-A")
    run_git(rig_git_repo, "commit", "-q", "-m", "a change rig did not produce")
    head = run_git(rig_git_repo, "rev-parse", "HEAD").stdout.strip()
    run_git(rig_git_repo, "checkout", "-q", "main")

    result = subprocess.run(
        [sys.executable, str(WORKBENCH_SCRIPT), "import", "--head", head, "--type", "bugfix",
         "--producer", "an-outside-orchestrator"],
        cwd=str(rig_git_repo), stdin=subprocess.DEVNULL, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        env=_child_env(), timeout=subprocess_timeout(MEASURED_SECONDS))

    printed = result.stdout + result.stderr
    assert not (rig_git_repo / ".gitignore").exists(), (
        "`wb import` wrote .gitignore in a repository where nothing consented to it and "
        f"nothing could be asked (stdin at /dev/null).\n{printed}")
    assert ".rig/" in printed and ".gitignore" in printed, (
        "`wb import` neither wrote .gitignore nor said which line to add, so the reason "
        f"`.rig/` keeps turning up in `git status` is invisible on this path.\n{printed}")


def _child_env() -> dict:
    """The tree under test on PYTHONPATH, and no consent inherited from the developer."""
    import os
    env = dict(os.environ,
               PYTHONPATH=os.pathsep.join(
                   p for p in (str(REPO_ROOT), os.environ.get("PYTHONPATH")) if p),
               PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    env.pop(lifecycle.GITIGNORE_CONSENT_ENV, None)
    return env
