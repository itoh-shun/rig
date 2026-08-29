"""The manifest is machine-owned, so a machine has to own it.

`pack.yaml` declares every asset by path and by sha256, and `validate_pack` byte-compares the
file against `canonical()` — sorted keys, no separators, trailing newline. That form is right:
it is what makes a manifest hashable and signable, and `read_json_yaml` parses only the JSON
subset so a manifest cannot execute a YAML tag.

What was missing was the writer. An author who added one persona file got `asset declaration
drift`, and the only route past it was to hand-edit minified JSON and hand-compute a digest.
Nothing in the CLI wrote either field. These tests pin the writer, and — more importantly —
the two things it refuses to do quietly.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from rig_workbench.packs.cli import init_pack
from rig_workbench.packs.model import PackError
from rig_workbench.packs.sync import scan_assets, sync_manifest

PERSONA = "---\nname: hello\ndescription: demo\n---\n\n# persona: hello\n"


def _manifest(pack: pathlib.Path) -> dict:
    return json.loads((pack / "pack.yaml").read_text(encoding="utf-8"))


def _scaffold(tmp_path: pathlib.Path, *, type_: str = "skill") -> pathlib.Path:
    return init_pack("demo-pack", kind="project", type_=type_, root=tmp_path)


def test_an_added_asset_is_declared_and_hashed(tmp_path):
    """The whole point. Before this, the author's second action — adding their first file —
    had no supported way to reach the manifest."""
    pack = _scaffold(tmp_path)
    (pack / "facets/personas/hello.md").write_text(PERSONA, encoding="utf-8")

    result = sync_manifest(pack)

    assert result["added"] == ["facets/personas/hello.md"]
    manifest = _manifest(pack)
    assert manifest["assets"]["persona"] == ["facets/personas/hello.md"]
    assert len(manifest["hashes"]["facets/personas/hello.md"]) == 64


def test_the_written_manifest_is_the_canonical_form_validate_demands(tmp_path):
    """`validate_pack` compares bytes, not parsed values, so a manifest that is merely
    *equivalent* is still refused. Writing anything but `canonical()` here would leave the
    author exactly as stuck as before, one error message further along."""
    from rig_workbench.packs.manifest import canonical

    pack = _scaffold(tmp_path)
    (pack / "facets/personas/hello.md").write_text(PERSONA, encoding="utf-8")
    sync_manifest(pack)

    raw = (pack / "pack.yaml").read_text(encoding="utf-8")
    assert raw == canonical(json.loads(raw))


def test_a_removed_asset_leaves_the_manifest(tmp_path):
    """Sync is a mirror of the directory, not an append log. A stale declaration would fail
    validation with `missing=[...]` and send the author looking for a file they deleted."""
    pack = _scaffold(tmp_path)
    asset = pack / "facets/personas/hello.md"
    asset.write_text(PERSONA, encoding="utf-8")
    sync_manifest(pack)

    asset.unlink()
    result = sync_manifest(pack)

    assert result["removed"] == ["facets/personas/hello.md"]
    assert _manifest(pack)["assets"]["persona"] == []
    assert _manifest(pack)["hashes"] == {}


def test_syncing_twice_without_editing_produces_identical_bytes(tmp_path):
    """A manifest whose bytes depend on filesystem iteration order would churn the digest —
    and every signature and lock entry computed over it — on a sync that changed nothing."""
    pack = _scaffold(tmp_path)
    for name in ("b", "a", "c"):
        (pack / f"facets/personas/{name}.md").write_text(PERSONA, encoding="utf-8")

    sync_manifest(pack)
    first = (pack / "pack.yaml").read_bytes()
    sync_manifest(pack)

    assert (pack / "pack.yaml").read_bytes() == first


def test_a_file_in_no_asset_directory_is_named_and_not_dropped(tmp_path):
    """The dangerous alternative. Silently skipping it would leave a file inside the pack
    that the manifest does not mention and no hash covers, and `validate_pack` — which only
    compares the manifest against the asset directories — would then call the pack clean."""
    pack = _scaffold(tmp_path)
    (pack / "NOTES.md").write_text("scratch\n", encoding="utf-8")

    with pytest.raises(PackError, match="no asset directory"):
        sync_manifest(pack)


def test_a_kind_the_pack_type_forbids_is_refused(tmp_path):
    """`TYPE_ASSETS` is the permission table: a knowledge pack may not carry a recipe. Sync
    writes the manifest, so sync is a place that table can be circumvented — declaring the
    file here would smuggle it past the check that reads the declaration."""
    pack = _scaffold(tmp_path, type_="knowledge")
    (pack / "recipes/anything.md").write_text("---\nname: x\n---\n", encoding="utf-8")

    with pytest.raises(PackError, match="may not carry"):
        sync_manifest(pack)


def test_a_signed_pack_is_refused_rather_than_silently_invalidated(tmp_path):
    """Rewriting the manifest breaks any signature over it. Proceeding would move the failure
    from here — where the author can see what caused it — to the next `verify`, somewhere far
    from the edit. Re-signing needs their key, so it is their call to make."""
    pack = _scaffold(tmp_path)
    (pack / "pack.sig.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(PackError, match="signed"):
        sync_manifest(pack)


def test_sibling_facet_directories_are_told_apart(tmp_path):
    """Three facet kinds live under one parent and must not be merged. This passes under
    either prefix rule today — no asset directory currently nests inside another — so it is
    a guard on the mapping being reversed correctly, not on the tie-break below."""
    pack = _scaffold(tmp_path)
    (pack / "facets/personas/who.md").write_text(PERSONA, encoding="utf-8")
    (pack / "facets/knowledge/what.md").write_text("# what\n", encoding="utf-8")
    (pack / "facets/instructions/how.md").write_text("# how\n", encoding="utf-8")

    grouped = scan_assets(pack)

    assert grouped["persona"] == ["facets/personas/who.md"]
    assert grouped["wiki"] == ["facets/knowledge/what.md"]
    assert grouped["instruction"] == ["facets/instructions/how.md"]


def test_a_directory_nested_inside_another_resolves_to_the_inner_one(monkeypatch):
    """The tie-break, exercised against the condition it exists for — which `ASSET_DIRS` does
    not currently contain. That is exactly why this is here: written against today's table it
    would pass with the rule deleted, and the day someone adds `evals/` beside `evals/cases`
    the mis-filing would be silent. Verified by mutation: reverting to first-match fails
    this and nothing else."""
    from rig_workbench.packs import sync as sync_module

    monkeypatch.setattr(sync_module, "_KIND_BY_DIR",
                        {"evals": "outer", "evals/cases": "eval-case"})

    assert sync_module._kind_of("evals/cases/a/case.json") == "eval-case"
    assert sync_module._kind_of("evals/loose.json") == "outer"


def test_sync_does_not_touch_what_the_author_owns(tmp_path):
    """Version, description and entrypoints are decisions sync has no basis for making. It
    derives the two fields that describe the directory, and nothing else."""
    pack = _scaffold(tmp_path)
    before = _manifest(pack)
    (pack / "facets/personas/hello.md").write_text(PERSONA, encoding="utf-8")

    sync_manifest(pack)
    after = _manifest(pack)

    assert {k: v for k, v in after.items() if k not in {"assets", "hashes"}} == \
           {k: v for k, v in before.items() if k not in {"assets", "hashes"}}
