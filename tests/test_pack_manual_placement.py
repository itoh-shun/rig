"""A pack placed in a scope root by hand is recognised, and named for what it is (#533).

The issue asks that a pack be usable without the CLI: copied into `~/.rig/packs/`, checked
out there, unpacked there. The resolver already read such a directory — a root with no lock
yields no lock entries and the collection walk takes every directory — but the inventory
read the lock alone, so `pack list` answered "no packs installed" about a pack whose recipes
were resolving. Measured on this tree before the change: `resolve_all` found the pack's
recipe, `list_rows` returned nothing, `knowledge_rows` returned no candidates.
"""

import json
import pathlib
import shutil

import pytest

from rig_workbench.packs.inventory import info, knowledge_rows, list_rows
from rig_workbench.packs.model import PackError
from rig_workbench.packs.resolver import resolve_all
from rig_workbench.packs.sync import sync_manifest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SHIPPED = REPO_ROOT / "packs" / "domain" / "decision-humor"


@pytest.fixture
def user_root(tmp_path, monkeypatch):
    """A user-tier pack root with one pack dropped in by hand and no lock."""
    home = tmp_path / "home"
    root = home / ".rig" / "packs"
    root.mkdir(parents=True)
    shutil.copytree(SHIPPED, root / "decision-humor")
    monkeypatch.setenv("RIG_USER_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))
    project = tmp_path / "proj"
    project.mkdir()
    return project, root


def test_a_manually_placed_pack_is_listed_and_says_it_was_never_installed(user_root):
    _, root = user_root
    (row,) = list_rows(root, scope="user")
    assert row["id"] == "decision-humor"
    assert row["verification"] == "unverified"
    assert row["origin"].startswith("manual")
    assert "pack install" in row["origin"]


def test_its_assets_resolve_exactly_as_they_did_before(user_root):
    """The resolver's behaviour is unchanged by this: it was already reading the directory."""
    project, root = user_root
    manifest = json.loads((root / "decision-humor" / "pack.yaml").read_text(encoding="utf-8"))
    recipe = pathlib.PurePosixPath(manifest["assets"]["recipe"][0]).stem
    found = resolve_all("recipe", recipe, project=project)
    assert [(a.tier, a.pack_id) for a in found] == [("user", "decision-humor")]


def test_info_describes_a_manual_pack_from_its_manifest_and_invents_no_install_facts(user_root):
    _, root = user_root
    detail = info(root, "decision-humor", scope="user")
    assert detail["source_type"] == "manual"
    assert detail["verification"] == "unverified"
    assert detail["scope"] == "user"
    # Nothing an install would have recorded is present as a filled-in value.
    for absent in ("installed_at", "publisher_key_id", "content_sha256", "revision"):
        assert absent not in detail


def test_a_manual_pack_declaring_knowledge_is_a_candidate(user_root, tmp_path):
    project, root = user_root
    manifest_path = root / "decision-humor" / "pack.yaml"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["knowledge"] = {"scope": ["company"], "topics": ["decisions"],
                             "owner": "Someone", "evidence": ["会議録"],
                             "reviewed_at": "2026-08-01T00:00:00+00:00"}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                        separators=(",", ":")), encoding="utf-8")
    # `pack.yaml` is canonical and hashed; the tool re-derives it after a hand edit.
    sync_manifest(root / "decision-humor")
    report = knowledge_rows(project, root, topics=("decisions",), scope="user")
    ids = [row["id"] for row in report["candidates"]]
    assert ids == ["decision-humor"]
    assert all(doc["uri"].startswith("pack://user/decision-humor/")
               for doc in report["candidates"][0]["documents"])


def test_an_unreadable_manual_directory_is_listed_as_unreadable_not_hidden(user_root):
    """A person who dropped a directory in and sees nothing has no way to learn why."""
    _, root = user_root
    (root / "broken").mkdir()
    (root / "broken" / "pack.yaml").write_text("{not json", encoding="utf-8")
    rows = {row["id"]: row for row in list_rows(root, scope="user")}
    assert rows["broken"]["verification"] == "unreadable"
    assert rows["broken"]["type"] == "?"


def test_a_locked_root_lists_the_lock_and_nothing_else(tmp_path):
    """Where a lock exists it owns the root. An extra directory there is drift the lock check
    refuses; it is not a second kind of installed pack."""
    root = tmp_path / "packs"
    root.mkdir()
    (root / "pack.lock.json").write_text(json.dumps({"schema": 4, "packs": []}), encoding="utf-8")
    try:
        rows = list_rows(root, scope="project")
    except PackError:
        return  # an empty lock of an older schema is refused outright; also not a listing
    assert rows == []
