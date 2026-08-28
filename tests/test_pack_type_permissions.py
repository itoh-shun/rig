"""A pack's `type` decides what it may carry and run (#523, slice S1).

The issue's requirement is that adding somebody's domain knowledge must not hand them
arbitrary command execution. That is two rules, and they are checked in two places because
they are two different claims:

* the manifest's declared asset kinds must be ones the type permits, and
* a recipe may declare `checks:` — host commands the orchestrator runs — only in a `tool`
  pack, which the manifest cannot say either way, so the recipe file is read.

What makes the first sound is that `validate_pack` already refuses any file the manifest
does not declare and hashes every file it does: the declaration is the pack's whole contents,
so dropping `commands/` from it to slip past the type check trades one refusal for another.
These tests pin that pairing — without it the type check would be advice a hand-edited
manifest walks past.
"""

import copy
import json
import pathlib

import pytest

from rig_workbench import __version__
from rig_workbench.packs.cli import init_pack
from rig_workbench.packs.manifest import PACK_SCHEMA_VERSION, canonical, digest
from rig_workbench.packs.model import (ASSET_DIRS, PACK_TYPES, RECIPE_CHECKS_TYPES,
                                       TYPE_ASSETS, PackError)
from rig_workbench.packs.validation import declares_recipe_checks, validate_pack
from test_eval_cases import valid_case

RECIPE = """---
name: demo
description: demo
scope: project
steps:
  - id: work
    instruction: implement
---

# demo
"""

RECIPE_WITH_CHECKS = """---
name: demo
description: demo
scope: project
steps:
  - id: work
    instruction: implement
    checks:
      - pytest -q
---

# demo
"""


def _write(pack: pathlib.Path, relative: str, text: str) -> str:
    target = pack / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return relative


def _pack(root: pathlib.Path, type_: str, files: dict[str, str],
          *, surface: str | None = None) -> pathlib.Path:
    """A minimal valid pack of `type_` carrying exactly `files` ({asset kind: relative path}).

    A prompt-bearing pack already has to ship an evaluation case, so one is added whenever
    the pack carries prompt material — otherwise every fixture here would fail that older
    rule before reaching the type rule under test.
    """
    pack = root / "demo-pack"
    pack.mkdir(parents=True, exist_ok=True)
    assets = {kind: [] for kind in ASSET_DIRS}
    for kind, relative in files.items():
        assets[kind] = [relative]
    if surface is not None:
        case = copy.deepcopy(valid_case())
        case["id"] = "demo-case"
        case["prompt_surfaces"] = [surface]
        _write(pack, "evals/cases/demo-case/case.json", canonical(case))
        assets["eval-case"] = ["evals/cases/demo-case/case.json"]
    manifest = {
        "pack_schema_version": PACK_SCHEMA_VERSION, "id": "demo-pack", "type": type_,
        "version": "1.0.0", "kind": "project", "engine": "*", "dependencies": [],
        "assets": assets,
        "hashes": {item: digest(pack / item) for paths in assets.values() for item in paths},
        "provenance": {"source": "test", "created_at": "2026-08-27T00:00:00+00:00"},
    }
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    (pack / "compatibility.yaml").write_text(canonical({
        "compatibility_schema_version": 1, "pack_id": "demo-pack", "pack_version": "1.0.0",
        "engine": "*", "platforms": ["any"],
    }), encoding="utf-8")
    return pack


def test_a_knowledge_pack_may_carry_knowledge(tmp_path):
    """Positive control for the refusals below: the permitted shape actually validates, so a
    failure there is the type rule and not a broken fixture."""
    pack = tmp_path / "demo-pack"
    pack.mkdir()
    wiki = _write(pack, "facets/knowledge/domain.md", "# domain\n\nfacts.\n")
    assert validate_pack(_pack(tmp_path, "knowledge", {"wiki": wiki},
                                surface="wiki:domain"))["type"] == "knowledge"


def test_a_knowledge_pack_may_not_carry_a_command(tmp_path):
    """The issue's case: installing somebody's domain knowledge must not also install a
    command surface."""
    pack = tmp_path / "demo-pack"
    pack.mkdir()
    command = _write(pack, "commands/do-it.md", "# do it\n")
    with pytest.raises(PackError, match="knowledge pack may not carry command assets"):
        validate_pack(_pack(tmp_path, "knowledge", {"command": command}))


def test_dropping_the_asset_from_the_declaration_does_not_get_it_installed(tmp_path):
    """The manifest check is only as good as the declaration being the whole truth. Hand-edit
    the manifest to hide the command and the pack fails as drift instead — the two refusals
    together are what close the hole, which is why neither is enough alone."""
    pack = tmp_path / "demo-pack"
    pack.mkdir()
    command = _write(pack, "commands/do-it.md", "# do it\n")
    _pack(tmp_path, "knowledge", {"command": command})
    manifest = json.loads((pack / "pack.yaml").read_text())
    manifest["assets"]["command"] = []
    manifest["hashes"] = {}
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")

    with pytest.raises(PackError, match="asset declaration drift"):
        validate_pack(pack)


def test_a_skill_pack_may_not_ship_a_recipe_that_runs_host_commands(tmp_path):
    """`checks:` are shell commands the orchestrator runs. The manifest cannot declare them,
    so the recipe is read — a type check that stopped at the manifest would miss the one
    thing in a pack that actually executes."""
    pack = tmp_path / "demo-pack"
    pack.mkdir()
    recipe = _write(pack, "recipes/demo.md", RECIPE_WITH_CHECKS)
    with pytest.raises(PackError, match="may not ship a recipe declaring `checks:`"):
        validate_pack(_pack(tmp_path, "skill", {"recipe": recipe}, surface="recipe:demo"))

    # The same pack as a tool is allowed — the rule is about which type may run things, not
    # about forbidding checks outright.
    assert "tool" in RECIPE_CHECKS_TYPES
    assert validate_pack(_pack(tmp_path, "tool", {"recipe": recipe},
                                surface="recipe:demo"))["type"] == "tool"


def test_a_skill_pack_may_ship_a_recipe_without_checks(tmp_path):
    """Positive control: the refusal above is about `checks:`, not about recipes."""
    pack = tmp_path / "demo-pack"
    pack.mkdir()
    recipe = _write(pack, "recipes/demo.md", RECIPE)
    assert validate_pack(_pack(tmp_path, "skill", {"recipe": recipe}, surface="recipe:demo"))["type"] == "skill"


@pytest.mark.parametrize("frontmatter,expected", [
    ("---\nchecks:\n  - pytest -q\n---\n", True),
    ("---\nsteps:\n  - id: a\n    checks:\n      - pytest -q\n---\n", True),
    ('---\nchecks: ["pytest -q"]\n---\n', True),
    ("---\nchecks: []\n---\n", False),
    ("---\nchecks:\n---\n", False),
    ("---\nname: demo\n---\n", False),
    ("# no frontmatter\n\nchecks: pytest -q\n", False),
    ("---\nname: demo\n---\n\nThe recipe declares checks: none of them run here.\n", False),
])
def test_checks_detection_reads_the_frontmatter_and_not_the_prose(tmp_path, frontmatter,
                                                                  expected):
    """The word appears in recipe prose. Refusing a pack over a sentence would teach people
    to route around the rule, so only the frontmatter block is scanned."""
    recipe = tmp_path / "demo.md"
    recipe.write_text(frontmatter, encoding="utf-8")
    assert declares_recipe_checks(recipe) is expected


def test_a_manifest_without_a_type_is_refused_rather_than_defaulted(tmp_path):
    """Guessing a type is guessing a permission. The safe-looking guess breaks working packs
    and the permissive one hands out reach nobody granted, so neither is taken."""
    pack = tmp_path / "demo-pack"
    pack.mkdir()
    wiki = _write(pack, "facets/knowledge/domain.md", "# domain\n")
    _pack(tmp_path, "knowledge", {"wiki": wiki})
    manifest = json.loads((pack / "pack.yaml").read_text())
    del manifest["type"]
    manifest["pack_schema_version"] = 1
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")

    with pytest.raises(PackError, match="schema fields/version are invalid"):
        validate_pack(pack)


def test_init_requires_a_type_and_writes_the_current_schema(tmp_path):
    """`pack init` has no default type for the same reason: a default hands the permission
    decision to whoever did not make it."""
    with pytest.raises(PackError, match="pack type must be one of"):
        init_pack("typeless-pack", kind="project", type_="", root=tmp_path)

    pack = init_pack("scaffolded-pack", kind="project", type_="knowledge", root=tmp_path)
    manifest = json.loads((pack / "pack.yaml").read_text())
    assert manifest["type"] == "knowledge"
    assert manifest["pack_schema_version"] == PACK_SCHEMA_VERSION
    assert manifest["engine"] == f">={__version__}"


def test_the_type_table_covers_every_type_and_only_real_asset_kinds():
    """A type with no entry would raise a KeyError inside validation rather than refusing the
    pack, and a table naming a kind that does not exist would permit nothing."""
    assert set(TYPE_ASSETS) == set(PACK_TYPES)
    for type_, permitted in TYPE_ASSETS.items():
        assert permitted <= set(ASSET_DIRS), type_
        assert permitted, type_
    assert RECIPE_CHECKS_TYPES <= set(PACK_TYPES)
    # `tool` is the widest set — nothing is permitted to it that another type may not have,
    # so a pack can always be re-declared upward rather than needing a kind no type allows.
    for permitted in TYPE_ASSETS.values():
        assert permitted <= TYPE_ASSETS["tool"]
