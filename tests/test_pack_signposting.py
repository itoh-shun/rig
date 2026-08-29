"""Three green checks used to greet a pack that did nothing.

`init` printed a path and stopped. `validate` said `valid`, `doctor` said `ok`, `test` said
`structural_only` — all true of a pack with every asset bucket empty, which cannot be invoked
and does nothing. The author had been told they were finished before they had started, and
the three hard errors waiting further along (the manifest they could not hand-edit, then the
evaluation case they did not know was required) were each announced only on arrival.

The gates are right. What was missing was any statement that the road existed.
"""

from __future__ import annotations

import pytest

from rig_workbench.packs.cli import init_next_steps, init_pack
from rig_workbench.packs.doctor import diagnose
from rig_workbench.packs.model import ASSET_DIRS, PACK_TYPES, PROMPT_KINDS, TYPE_ASSETS
from rig_workbench.packs.sync import sync_manifest


def _codes(report: dict) -> set[str]:
    return {finding["code"] for finding in report["findings"]}


def test_doctor_names_the_empty_pack_instead_of_reporting_ok(tmp_path):
    """The finding this file exists for. `ok` was not false — the schema is satisfied — but
    it was the wrong word for a pack that carries nothing."""
    pack = init_pack("empty-one", kind="project", type_="skill", root=tmp_path)

    report = diagnose(pack, project=tmp_path)

    assert "empty_pack" in _codes(report)
    assert report["status"] == "warning"


def test_an_empty_pack_is_a_warning_and_not_a_failure(tmp_path):
    """A scaffolded pack is a legitimate place to be standing. Failing here would make the
    first command an author runs report an error they have done nothing to cause."""
    pack = init_pack("empty-one", kind="project", type_="skill", root=tmp_path)

    report = diagnose(pack, project=tmp_path)

    assert report["status"] != "failed"
    assert all(finding.get("severity") == "warning" for finding in report["findings"]
               if finding["code"] == "empty_pack")


def test_the_finding_goes_away_once_the_pack_carries_something(tmp_path):
    """The positive control. A warning that never clears is noise, and an author who has
    finished step one needs to see that they have."""
    pack = init_pack("res-pack", kind="project", type_="knowledge", root=tmp_path)
    (pack / "resources/note.md").write_text("# note\n", encoding="utf-8")
    sync_manifest(pack)

    report = diagnose(pack, project=tmp_path)

    assert "empty_pack" not in _codes(report)


def test_the_next_steps_name_the_command_that_was_missing(tmp_path):
    """`pack sync` is the step the author could not have guessed at, because until recently
    it did not exist and the manifest is not a file anyone can reasonably hand-edit."""
    pack = init_pack("demo", kind="project", type_="skill", root=tmp_path)

    steps = "\n".join(init_next_steps(pack, type_="skill"))

    assert "pack sync" in steps
    assert "pack validate" in steps


@pytest.mark.parametrize("type_", sorted(PACK_TYPES))
def test_the_example_asset_is_one_this_pack_type_may_actually_carry(tmp_path, type_):
    """`TYPE_ASSETS` refuses a recipe in a `knowledge` pack. Printing one as the suggested
    first step would walk the author straight into a refusal that is correct and reads as
    arbitrary — the worst kind, because the tool proposed the thing it then rejected."""
    pack = init_pack("demo", kind="project", type_=type_, root=tmp_path)

    steps = "\n".join(init_next_steps(pack, type_=type_))
    suggested = {kind for kind, directory in ASSET_DIRS.items() if f"/{directory}/" in steps}

    assert suggested, "the next steps must suggest some asset directory"
    assert suggested <= TYPE_ASSETS[type_]


def test_every_pack_type_can_reach_the_evaluation_gate(tmp_path):
    """The fact the notice depends on, pinned separately because it is not obvious and is
    what makes an unconditional notice correct: `wiki` appears in both `_INERT_KINDS` and
    `PROMPT_KINDS`, and every type admits the inert kinds, so a `knowledge` pack of wiki
    pages is prompt-bearing exactly as a `skill` pack is.

    This is here because the first version of the notice was conditional on this expression.
    The branch could never be false, so the test written for it could never fail — found by
    mutation, not by reading. If a future type is genuinely exempt, this fails and the
    unconditional notice above becomes wrong at the same moment."""
    assert all(TYPE_ASSETS[type_] & PROMPT_KINDS for type_ in PACK_TYPES)


@pytest.mark.parametrize("type_", sorted(PACK_TYPES))
def test_the_evaluation_gate_is_announced_for_every_type(tmp_path, type_):
    """Every type can reach it, so every author is told about it. Leaving it out for some
    type would put that author in front of a hard refusal with no warning — which is the
    situation this whole file exists to end."""
    pack = init_pack("demo", kind="project", type_=type_, root=tmp_path)

    assert "evaluation case" in "\n".join(init_next_steps(pack, type_=type_))


def test_the_road_ends_at_something_installable(tmp_path):
    """An author's actual goal is a pack somebody else can install. Stopping at `validate`
    would leave the last step — the one that produces the artifact — undiscoverable again."""
    pack = init_pack("demo", kind="project", type_="skill", root=tmp_path)

    assert "pack bundle" in "\n".join(init_next_steps(pack, type_="skill"))


def test_doctor_exits_zero_on_a_warning_and_non_zero_on_a_failure(tmp_path, monkeypatch):
    """The report has three states; the exit code had two, and `warning` shared the failing
    one. That was survivable while the only warning was a migration hint. It stopped being so
    once the expected state right after `pack init` began producing one — the first command
    an author runs would have reported an error they had done nothing to cause."""
    from rig_workbench.packs.cli import cmd_pack

    root = tmp_path / "packs"
    assert cmd_pack(["init", "warn-pack", "--type", "skill", "--root", str(root)]) == 0
    monkeypatch.chdir(tmp_path)

    assert cmd_pack(["doctor", str(root / "warn-pack"), "--json"]) == 0

    (root / "warn-pack" / "recipes" / "undeclared.md").write_text("x\n", encoding="utf-8")
    assert cmd_pack(["doctor", str(root / "warn-pack"), "--json"]) == 1
