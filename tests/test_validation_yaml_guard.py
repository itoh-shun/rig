"""The validator's one optional dependency, checked by taking it away.

`rig_workbench/validation/state.py` used to open with a `try: import yaml` whose
`except ImportError` printed `[ERROR] PyYAML not found …` and called `sys.exit(1)`. Two
effects at import time, in a module every other module in the pillar imports: merely
naming `rig_workbench.validation.state` could write to a caller's stdout and end their
interpreter, and no caller — not `cli.main()`, not a test — had any way to see the
condition, because the decision was already made by the time the name was bound.

It is now `yaml_adapter.require_yaml()`, which raises `PyYAMLMissing`, and `cmd_validate`
turns that into one line through the `Presenter` the shell built and a status of 1.

**The whole point of this file is that the error path is executed rather than reasoned
about.** PyYAML is installed here, so the absence is simulated: `sys.modules["yaml"] =
None` is the documented way to make `import yaml` raise `ImportError`
(`ImportError: import of yaml halted; None in sys.modules`), and
`test_the_simulated_absence_really_raises_importerror` asserts that mechanism directly, so
no assertion below can pass because the trap was never set. Without that, this change
would be a rewrite of a branch nobody had ever run.

`monkeypatch.setitem` restores the real entry at teardown, so the absence never outlives a
single test.
"""

from __future__ import annotations

import importlib
import pathlib
import sys

import pytest

from rig_workbench.ports import Presenter
from rig_workbench.validation.cli import cmd_validate
from rig_workbench.validation.state import parse_frontmatter
from rig_workbench.validation.yaml_adapter import PyYAMLMissing, require_yaml

#: The line `state.py` printed before this change, character for character. It is the
#: contract this migration promised not to alter, so it is spelled once and compared to.
MESSAGE = "[ERROR] PyYAML not found. Install it with `pip install pyyaml`."


class Recorder:
    """A `Presenter` that keeps the lines instead of printing them."""

    def __init__(self) -> None:
        self.out_lines: list[str] = []
        self.err_lines: list[str] = []

    def out(self, text: str = "") -> None:
        self.out_lines.append(text)

    def err(self, text: str = "") -> None:
        self.err_lines.append(text)


@pytest.fixture
def no_pyyaml(monkeypatch: pytest.MonkeyPatch) -> None:
    """PyYAML absent for the length of one test."""
    monkeypatch.setitem(sys.modules, "yaml", None)


def test_the_recorder_is_a_presenter() -> None:
    """Otherwise this file would be injecting something the shell only tolerates."""
    assert isinstance(Recorder(), Presenter)


def test_the_simulated_absence_really_raises_importerror(no_pyyaml: None) -> None:
    """The self-check: the mechanism every assertion below depends on, on its own.

    If `None` in `sys.modules` ever stopped producing `ImportError`, the tests below would
    quietly start exercising the *present* path and keep passing while saying nothing.
    """
    with pytest.raises(ImportError):
        import yaml  # noqa: F401


def test_require_yaml_answers_the_module_when_it_is_there() -> None:
    """The other side of the boundary, so the guard is not simply always raising."""
    assert require_yaml().safe_load("a: 1") == {"a": 1}


def test_require_yaml_raises_instead_of_ending_the_process(no_pyyaml: None) -> None:
    with pytest.raises(PyYAMLMissing) as caught:
        require_yaml()
    # The message is the payload: `cmd_validate` prefixes `[ERROR] ` and prints it.
    assert str(caught.value) == MESSAGE[len("[ERROR] "):]
    # Chained, so the original `ImportError` is still readable in a traceback.
    assert isinstance(caught.value.__cause__, ImportError)


def test_importing_state_no_longer_ends_the_process(no_pyyaml: None,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """The behaviour change this commit is: the module imports, and nothing is said.

    A fresh import of `state` with PyYAML absent used to raise `SystemExit(1)` after
    printing. `monkeypatch.delitem` puts the real module object back afterwards, so the
    shared counters other modules hold a reference to are the ones that survive.
    """
    monkeypatch.delitem(sys.modules, "rig_workbench.validation.state")
    fresh = importlib.import_module("rig_workbench.validation.state")
    assert fresh.results == []


def test_parse_frontmatter_raises_where_a_caller_can_catch_it(
        no_pyyaml: None, tmp_path: pathlib.Path) -> None:
    """The one function that needs `yaml`, reached with it gone."""
    page = tmp_path / "page.md"
    page.write_text("---\nslug: page\n---\n\nbody\n", encoding="utf-8")
    with pytest.raises(PyYAMLMissing):
        parse_frontmatter(page)


def test_the_shell_reports_it_on_stdout_and_exits_one(no_pyyaml: None) -> None:
    """What a user sees, which is what did not change.

    Same line, same stream, same status — and produced before any check runs, so a missing
    PyYAML is still one message rather than a report full of parse failures.
    """
    seen = Recorder()
    assert cmd_validate([], out=seen) == 1
    assert seen.out_lines == [MESSAGE]
    assert seen.err_lines == []


def test_an_unknown_flag_is_refused_before_the_dependency_guard(no_pyyaml: None) -> None:
    """The ORDER of the two refusals, which nothing else in the suite can see.

    `--bogus` and a missing PyYAML are both true here, and only one answer can come back.
    The unknown flag is checked first, so it is 2 (bad usage) and not 1 (this machine
    cannot validate): a caller who misspelt a flag gets told that, on any machine. Move
    the check below `require_yaml()` and every other assertion in this repository still
    passes — the report is skipped either way — while this one flips to 1 and says which
    of the two guards moved.

    The line also goes to the stream the refusal uses, not the one the dependency message
    uses, so the two are told apart by more than their status.
    """
    seen = Recorder()
    assert cmd_validate(["--bogus"], out=seen) == 2
    assert seen.out_lines == []
    assert len(seen.err_lines) == 1
    assert seen.err_lines[0].startswith("[ERROR] validate: unknown flag")
    assert MESSAGE not in seen.err_lines


def test_the_selftest_verb_is_refused_the_same_way(no_pyyaml: None) -> None:
    """`validate selftest` parses frontmatter too, so the guard is in front of it."""
    seen = Recorder()
    assert cmd_validate(["selftest"], out=seen) == 1
    assert seen.out_lines == [MESSAGE]
