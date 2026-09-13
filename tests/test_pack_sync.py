"""The manifest is machine-owned, so a machine has to own it.

`pack.yaml` declares every asset by path and by sha256, and `validate_pack` byte-compares the
file against `canonical()` — sorted keys, no separators, trailing newline. That form is right:
it is what makes a manifest hashable, and `read_json_yaml` parses only the JSON
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
from rig_workbench.packs.resolver import core_reference_ids

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
    and every lock entry computed over it — on a sync that changed nothing."""
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


def test_pack_sig_json_is_no_longer_excused_by_name(tmp_path):
    """`pack.sig.json` was a third non-asset, alongside the two manifest files.

    Sync knew the name twice: it skipped the file when scanning, and refused the sync
    outright because rewriting the manifest would have invalidated the signature over it.
    Nothing signs a pack now, so the name means nothing, and a file carrying it is what it
    looks like — a stray at the pack root. It is refused by the general rule above, not by a
    special case, and the point of this test is that the name buys no exemption: were it
    still in `NON_ASSETS`, sync would skip it, write a manifest that does not mention it,
    and call the pack clean.
    """
    pack = _scaffold(tmp_path)
    (pack / "pack.sig.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(PackError, match="no asset directory.*pack.sig.json"):
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


def test_a_resource_pack_validates_end_to_end_after_sync(tmp_path):
    """The whole authoring path, for the one pack shape that needs no evidence. A pack of
    pure `resource` files carries no prompt material, so the evaluation gate does not apply
    and `init` → add a file → `sync` → `validate` can actually complete. Nothing else in the
    CLI could reach this state before."""
    from rig_workbench.packs.validation import validate_pack

    pack = init_pack("res-pack", kind="project", type_="knowledge", root=tmp_path)
    (pack / "resources/note.md").write_text("# note\n", encoding="utf-8")

    sync_manifest(pack)

    assert validate_pack(pack, core_ids=core_reference_ids())["id"] == "res-pack"


def test_resource_metadata_is_derived_and_not_left_to_the_author(tmp_path):
    """`validate_pack` requires `resources` to cover the resource assets exactly. Deriving
    `assets` and `hashes` and stopping there — which is what the first version of sync did —
    left the pack failing on `pack resources must exactly cover resource assets`, one error
    further along and no more fixable by hand than the last one."""
    pack = init_pack("res-pack", kind="project", type_="knowledge", root=tmp_path)
    (pack / "resources/note.md").write_text("# note\n", encoding="utf-8")

    sync_manifest(pack)

    entry = _manifest(pack)["resources"]["resources/note.md"]
    assert entry["media_type"] == "text/markdown"
    assert entry["size"] == len("# note\n")
    assert entry["sha256"] == _manifest(pack)["hashes"]["resources/note.md"]


def test_a_deleted_resource_leaves_the_resources_table_too(tmp_path):
    """Same mirror rule as `assets`. A stale `resources` entry fails validation just as
    loudly as a stale declaration, and would be just as confusing."""
    pack = init_pack("res-pack", kind="project", type_="knowledge", root=tmp_path)
    asset = pack / "resources/note.md"
    asset.write_text("# note\n", encoding="utf-8")
    sync_manifest(pack)
    assert _manifest(pack)["resources"], "precondition: the entry was written in the first place"

    asset.unlink()
    sync_manifest(pack)

    assert _manifest(pack)["resources"] == {}


def test_a_resource_extension_with_no_known_media_type_is_named(tmp_path):
    """`validate_resource` refuses a MIME outside the allowlist, so guessing one here would
    only move the failure. The extension is named instead, with what is supported."""
    pack = init_pack("res-pack", kind="project", type_="knowledge", root=tmp_path)
    (pack / "resources/data.xyz").write_text("?\n", encoding="utf-8")

    with pytest.raises(PackError, match="media type|MIME"):
        sync_manifest(pack)


def test_an_executable_resource_extension_is_refused_by_sync_as_well(tmp_path):
    """The check exists in `validate_resource`; it has to exist here too. Sync writes the
    declaration, so a sync that happily described `payload.sh` would be the one place the
    rule could be walked around — declare it, and the pack ships an executable it claims is
    inert data."""
    pack = init_pack("res-pack", kind="project", type_="knowledge", root=tmp_path)
    (pack / "resources/payload.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    with pytest.raises(PackError, match="executable"):
        sync_manifest(pack)


# --- the installed pack ---------------------------------------------------------------
#
# Everything above syncs a tree the author owns. `pack sync` takes a path, so it can also be
# aimed at `.rig/packs/<id>` — a pack `pack install` put there and `pack.lock.json` pins by
# digest. That is not the same object, and it is what these pin.

PAGE = "# new\n\nbody\n"


def _installed(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """A project with one shipped domain pack actually installed, lock and all."""
    from rig_workbench.packs.installer import install_pack

    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    install_pack("domain:decision-humor", scope="project", project=project)
    return project, project / ".rig" / "packs" / "decision-humor"


def _stray(pack: pathlib.Path) -> pathlib.Path:
    """One undeclared file inside the installed pack: what a user adds before reaching
    for `pack sync` in the first place."""
    page = pack / "facets" / "knowledge" / "new-page.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(PAGE, encoding="utf-8")
    return page


def test_sync_refuses_the_installed_pack_a_lock_owns(tmp_path):
    """The lock records `manifest_sha256` over `pack.yaml`'s bytes and `validate_lock_root`
    recomputes it on every resolve. Rewriting the manifest under a lock that still names the
    old digest is not a sync, it is the drift the lock exists to catch — and because
    `resolved_collection` is fail-closed, it takes every persona, recipe and wiki lookup in
    the project down with it.

    Refused rather than repaired: updating the lock from whatever is on disk would make the
    lock re-bless local edits it was written to detect, and could not be done honestly
    anyway — `source.sha256` pins the artefact the pack was installed from and cannot be
    re-derived from a modified installed tree.
    """
    _project, pack = _installed(tmp_path)
    _stray(pack)
    before = (pack / "pack.yaml").read_bytes()

    with pytest.raises(PackError, match="installed"):
        sync_manifest(pack)

    assert (pack / "pack.yaml").read_bytes() == before, (
        "the refusal has to land before the write, or the lock is already in drift")


def test_the_refusal_leaves_a_failure_the_user_can_undo(tmp_path):
    """Why refusing beats writing, stated as the difference the user sees.

    Dropping a file into an installed pack already breaks resolution — `asset declaration
    drift` names the undeclared file, so the way out is to delete it. Syncing replaced that
    with `pack lock drift: manifest changed`, which names nothing to undo: the manifest's
    original bytes are gone and `pack remove` goes through the same lock check, so the pack
    cannot even be uninstalled. The refusal keeps the recoverable failure recoverable.
    """
    from rig_workbench.packs.resolver import resolved_collection

    project, pack = _installed(tmp_path)
    stray = _stray(pack)

    with pytest.raises(PackError) as refusal:
        sync_manifest(pack)
    assert "decision-humor" in str(refusal.value)

    with pytest.raises(PackError, match=r"asset declaration drift.*new-page\.md"):
        resolved_collection(project=project)
    stray.unlink()
    assert resolved_collection(project=project), "deleting the file has to be the way back"


def test_an_installed_pack_that_needs_no_sync_is_refused_and_keeps_resolving(tmp_path):
    """The unchanged case. Nothing here is broken yet, so this is the one that says the
    refusal is a refusal and not a crash: `pack sync` declines, the manifest is untouched,
    and the project resolves exactly as it did before the command was run."""
    from rig_workbench.packs.resolver import resolved_collection

    project, pack = _installed(tmp_path)
    before = (pack / "pack.yaml").read_bytes()
    assert resolved_collection(project=project), "precondition: the project resolved to start"

    with pytest.raises(PackError, match="installed"):
        sync_manifest(pack)

    assert (pack / "pack.yaml").read_bytes() == before
    assert resolved_collection(project=project)


def test_an_author_tree_in_the_same_place_is_still_synced(tmp_path):
    """The author side, aimed at the location the rule could have been written against.

    `.rig/packs/<id>` is where installed packs live, and it is also where `pack init`'s own
    signposting tells an author to scaffold one. What makes a pack untouchable is the lock
    that pins its bytes, not the directory it sits in — so a pack root with no lock in it
    syncs, here as anywhere else.
    """
    from rig_workbench.packs.lock import LOCK_NAME

    root = tmp_path / "project" / ".rig" / "packs"
    pack = init_pack("demo-pack", kind="project", type_="skill", root=root)
    assert not (root / LOCK_NAME).exists(), "precondition: nothing installed anything here"
    (pack / "facets/personas/hello.md").write_text(PERSONA, encoding="utf-8")

    result = sync_manifest(pack)

    assert result["added"] == ["facets/personas/hello.md"]
    assert _manifest(pack)["assets"]["persona"] == ["facets/personas/hello.md"]
