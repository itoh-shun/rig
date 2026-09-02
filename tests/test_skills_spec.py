"""Every SKILL.md against the Agent Skills specification (#550).

The spec is the one format rig shares with the outside: `/rig:import` parses it and
`/rig:export` claims to emit it, and nothing checked either side. Every SKILL.md in the tree
conformed when the issue was filed — checked by a script written once and thrown away — so
what is tested here is that a departure is reported, not that today's files pass.
"""

import pytest

from rig_workbench.validation.config import ROOT
from rig_workbench.validation.skills_spec import (
    BODY_LINES_SHOULD,
    skill_files,
    skill_spec_findings,
)

GOOD = {"name": "my-skill", "description": "Does one thing."}


def fails(fm, body="# body\n", directory="my-skill"):
    return [msg for level, msg in skill_spec_findings(fm, body, directory) if level == "FAIL"]


def warns(fm, body="# body\n", directory="my-skill"):
    return [msg for level, msg in skill_spec_findings(fm, body, directory) if level == "WARN"]


def test_a_conforming_skill_has_no_findings():
    assert skill_spec_findings(GOOD, "# body\n", "my-skill") == []


def test_unparseable_or_non_mapping_frontmatter_is_a_fail():
    assert fails(None) == ["frontmatter cannot be parsed as YAML"]
    assert fails(["not", "a", "map"]) == ["frontmatter is not a mapping"]


@pytest.mark.parametrize("name,says", [
    (None, "name is required"),
    ("", "name is required"),
    ("My-Skill", "lowercase"),
    ("my_skill", "lowercase"),
    ("-my-skill", "leading, trailing or consecutive"),
    ("my-skill-", "leading, trailing or consecutive"),
    ("my--skill", "leading, trailing or consecutive"),
    ("a" * 65, "at most 64"),
])
def test_a_name_the_spec_forbids_is_a_fail(name, says):
    found = fails({**GOOD, "name": name}, directory=name if isinstance(name, str) else "x")
    assert any(says in msg for msg in found), found


def test_a_name_that_is_not_the_directory_name_is_a_fail():
    """The classic silent breakage: rename the directory, forget the frontmatter, and the
    skill still looks fine in a diff."""
    found = fails(GOOD, directory="renamed")
    assert found == ["name 'my-skill' does not match the parent directory 'renamed'; the spec "
                     "requires them to be equal"]


@pytest.mark.parametrize("description,says", [
    (None, "description is required"),
    ("", "description is required"),
    ("   ", "description is required"),
    (["a", "list"], "description is required"),
    ("d" * 1025, "at most 1024"),
])
def test_a_description_the_spec_forbids_is_a_fail(description, says):
    found = fails({**GOOD, "description": description})
    assert any(says in msg for msg in found), found


def test_optional_fields_are_checked_only_when_present():
    assert fails({**GOOD, "compatibility": "Claude Code 2.x", "metadata": {"a": "b"},
                  "license": "MIT", "allowed-tools": "Read Grep"}) == []
    assert any("compatibility" in m for m in fails({**GOOD, "compatibility": ""}))
    assert any("at most 500" in m for m in fails({**GOOD, "compatibility": "c" * 501}))
    assert any("metadata" in m for m in fails({**GOOD, "metadata": {"a": 1}}))
    assert any("metadata" in m for m in fails({**GOOD, "metadata": ["a"]}))
    assert any("license" in m for m in fails({**GOOD, "license": 1}))
    assert any("allowed-tools" in m for m in fails({**GOOD, "allowed-tools": ["Read"]}))


def test_a_long_body_is_a_warning_not_a_failure():
    """The 500-line limit is a recommendation. A check that fails on a judgement call
    teaches people to disable it, so it stays a warning — and the shipped engine, which is
    over it today, is measured as a number rather than blocked."""
    long_body = "line\n" * (BODY_LINES_SHOULD + 1)
    assert fails(GOOD, body=long_body) == []
    assert any(str(BODY_LINES_SHOULD) in msg for msg in warns(GOOD, body=long_body))
    assert warns(GOOD, body="line\n" * BODY_LINES_SHOULD) == []


def test_the_walk_finds_the_shipped_skills_and_skips_dependency_trees(tmp_path):
    (tmp_path / "skills" / "engine").mkdir(parents=True)
    (tmp_path / "skills" / "engine" / "SKILL.md").write_text("---\nname: engine\n---\n")
    (tmp_path / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "node_modules" / "x" / "SKILL.md").write_text("---\nname: x\n---\n")
    (tmp_path / ".git" / "y").mkdir(parents=True)
    (tmp_path / ".git" / "y" / "SKILL.md").write_text("---\nname: y\n---\n")
    assert skill_files(tmp_path) == [tmp_path / "skills" / "engine" / "SKILL.md"]
    shipped = {p.relative_to(ROOT).as_posix() for p in skill_files(ROOT)}
    assert "skills/engine/SKILL.md" in shipped
