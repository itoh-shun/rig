"""The orchestrator's one optional dependency, checked by taking it away.

`rig_workbench/orchestrate/recipes.py` opened with a `try: import yaml` whose
`except ImportError` set `yaml = None`, and `parse_frontmatter` checked that module
global before every use and printed `[ERROR] PyYAML not found. \\`pip install pyyaml\\`.`
before `sys.exit(1)`. Two things were wrong with it, and only one of them was the
`print`: a judgement module held a third-party import, and the `yaml is None` branch was
**unreachable from a running process with PyYAML installed**, because the name was bound
once at import. Nothing had ever executed it.

It is now `yaml_adapter.require_yaml()`, which raises `PyYAMLMissing`, and
`parse_frontmatter` turns that into the same line on the same stream and the same status
through the `Presenter` the shell built.

**The point of this file is that the error path runs rather than being reasoned about.**
PyYAML is installed here, so the absence is simulated: `sys.modules["yaml"] = None` is the
documented way to make `import yaml` raise `ImportError`, and
`test_the_simulated_absence_really_raises_importerror` asserts that mechanism directly, so
no assertion below can pass because the trap was never set.

`monkeypatch.setitem` restores the real entry at teardown, so the absence never outlives a
single test.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

from rig_workbench.orchestrate.recipes import parse_frontmatter
from rig_workbench.orchestrate.yaml_adapter import PyYAMLMissing, require_yaml
from rig_workbench.ports import Presenter

#: The line `recipes.py` printed before this change, character for character. It is the
#: contract this migration promised not to alter, so it is spelled once and compared to.
MESSAGE = "[ERROR] PyYAML not found. `pip install pyyaml`."


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


def test_require_yaml_raises_instead_of_deciding_for_its_caller(no_pyyaml: None) -> None:
    with pytest.raises(PyYAMLMissing) as caught:
        require_yaml()
    # The message is the payload: `parse_frontmatter` prefixes `[ERROR] ` and prints it.
    assert str(caught.value) == MESSAGE[len("[ERROR] "):]
    # Chained, so the original `ImportError` is still readable in a traceback.
    assert isinstance(caught.value.__cause__, ImportError)


def test_nothing_is_cached_between_calls(no_pyyaml: None) -> None:
    """The old `yaml = None` was a cache resolved at import; this one holds no state.

    Without this, the guard could be "reached" in a test only by reloading the module,
    which is precisely why the branch it replaces had never been executed.
    """
    with pytest.raises(PyYAMLMissing):
        require_yaml()
    sys.modules.pop("yaml")
    assert require_yaml().safe_load("b: 2") == {"b": 2}


def test_parse_frontmatter_reports_on_stdout_and_exits_one(
        no_pyyaml: None, tmp_path: pathlib.Path) -> None:
    """What a user sees, which is what did not change.

    Same line, same stream, same status — and produced before the file is read, so a
    missing PyYAML is one message rather than a traceback out of the parser.
    """
    page = tmp_path / "recipe.md"
    page.write_text("---\nname: r\n---\n\nbody\n", encoding="utf-8")
    seen = Recorder()
    with pytest.raises(SystemExit) as stopped:
        parse_frontmatter(page, out=seen)
    assert stopped.value.code == 1
    assert seen.out_lines == [MESSAGE]
    assert seen.err_lines == []


def test_the_default_presenter_still_writes_the_line_to_real_stdout(
        no_pyyaml: None, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Most callers do not forward a `Presenter`; the default has to behave as `print` did."""
    page = tmp_path / "recipe.md"
    page.write_text("---\nname: r\n---\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        parse_frontmatter(page)
    captured = capsys.readouterr()
    assert captured.out == MESSAGE + "\n"
    assert captured.err == ""


def test_recipes_no_longer_holds_the_third_party_name(no_pyyaml: None) -> None:
    """The import moved; a module global nobody can intercept did not come with it."""
    from rig_workbench.orchestrate import recipes

    assert not hasattr(recipes, "yaml")


def test_parse_frontmatter_still_parses_when_pyyaml_is_there(tmp_path: pathlib.Path) -> None:
    """The other side again, at the call site rather than at the adapter."""
    page = tmp_path / "recipe.md"
    page.write_text("---\nname: r\nscope: project\n---\n\nbody\n", encoding="utf-8")
    assert parse_frontmatter(page) == {"name": "r", "scope": "project"}
