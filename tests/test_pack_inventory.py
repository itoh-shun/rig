"""Reading back what is installed, and moving a pin (#523, slice S3).

The lock has recorded a pack's source, version, and integrity since long before packs came
from anywhere but a local directory — nothing read it back. These are the commands that make
that record answerable, plus the one command that changes it.

The source is a real git repository for the same reason as slice S2: what `outdated` and
`update` have to get right is git's behaviour, and a fake remote would let both pass while
the real path stayed broken.
"""

import copy
import pathlib
import subprocess

import pytest

from rig_workbench.packs.cli import cmd_pack
from rig_workbench.packs.installer import install_pack, update_pack
from rig_workbench.packs.inventory import available_versions, explain, info, list_rows, outdated
from rig_workbench.packs.lock import read_lock
from rig_workbench.packs.manifest import PACK_SCHEMA_VERSION, canonical, digest
from rig_workbench.packs.model import ASSET_DIRS, PackError
from rig_workbench.packs.sources import write_sources
from test_eval_cases import valid_case

RECIPE = "---\nname: hello\nsteps: []\n---\n"


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _write_pack(pack: pathlib.Path, pack_id: str, version: str) -> None:
    for directory in ASSET_DIRS.values():
        (pack / directory).mkdir(parents=True, exist_ok=True)
    (pack / "recipes" / "hello.md").write_text(RECIPE, encoding="utf-8")
    case = copy.deepcopy(valid_case())
    case["id"] = "hello-case"
    case["prompt_surfaces"] = ["recipe:hello"]
    case_path = pack / "evals" / "cases" / "hello-case" / "case.json"
    case_path.parent.mkdir(parents=True, exist_ok=True)
    case_path.write_text(canonical(case), encoding="utf-8")
    assets = {kind: [] for kind in ASSET_DIRS}
    assets["recipe"] = ["recipes/hello.md"]
    assets["eval-case"] = ["evals/cases/hello-case/case.json"]
    (pack / "pack.yaml").write_text(canonical({
        "pack_schema_version": PACK_SCHEMA_VERSION, "id": pack_id, "type": "skill",
        "version": version, "kind": "project", "engine": "*", "dependencies": [],
        "assets": assets,
        "hashes": {item: digest(pack / item) for paths in assets.values() for item in paths},
        "provenance": {"source": "test", "created_at": "2026-08-27T00:00:00+00:00"},
    }), encoding="utf-8")
    (pack / "compatibility.yaml").write_text(canonical({
        "compatibility_schema_version": 1, "pack_id": pack_id, "pack_version": version,
        "engine": "*", "platforms": ["any"],
    }), encoding="utf-8")


@pytest.fixture
def remote(tmp_path):
    """A repository publishing joypla at v1.4.0 and v1.5.0."""
    repo = tmp_path / "remote" / "rig-pack-joypla"
    repo.mkdir(parents=True)
    _git(repo, "init", "--quiet", "-b", "main")
    _git(repo, "config", "user.email", "packs@example.invalid")
    _git(repo, "config", "user.name", "packs")
    for version in ("1.4.0", "1.5.0"):
        _write_pack(repo, "joypla", version)
        _git(repo, "add", "-A")
        _git(repo, "commit", "--quiet", "-m", f"pack {version}")
        _git(repo, "tag", f"v{version}")
    return repo


@pytest.fixture
def installed(tmp_path, remote):
    """A project with joypla@1.4.0 installed from that repository."""
    project = tmp_path / "project"
    project.mkdir()
    write_sources(project, {"product": {
        "scheme": "git+file", "url": str(remote.parent / "rig-pack-{pack}")}})
    result = install_pack("product:joypla@1.4.0", scope="project", project=project,
                          allow_unverified=True)
    return project, result.path.parent


def test_list_says_where_each_pack_came_from_without_printing_a_url(installed):
    project, root = installed
    row, = list_rows(root)
    assert row["id"] == "joypla"
    assert row["version"] == "1.4.0"
    assert row["type"] == "skill"
    assert row["origin"].startswith("product:joypla@1.4.0 @")
    assert "://" not in row["origin"] and "rig-pack-joypla" not in row["origin"]


def test_info_answers_source_version_and_integrity_together(installed, remote):
    """AC 7 in one call: what it is, where it came from, and what would prove it unchanged."""
    _project, root = installed
    detail = info(root, "joypla")
    assert detail["version"] == "1.4.0"
    assert detail["type"] == "skill"
    assert detail["source_type"] == "git"
    assert detail["source_id"] == "product"
    assert detail["revision"] == _git(remote, "rev-parse", "v1.4.0^{commit}")
    assert len(detail["content_sha256"]) == 64 and len(detail["manifest_sha256"]) == 64
    assert detail["assets"]["recipe"] == 1
    assert detail["eval_cases"] == 1


def test_info_reads_the_type_from_disk_because_the_lock_never_carried_one(installed):
    """The lock predates `type`. Reading the installed manifest keeps the answer true for a
    pack installed before the field existed, and describes the pack that is actually there."""
    _project, root = installed
    entry, = read_lock(root)["packs"]
    assert "type" not in entry
    assert info(root, "joypla")["type"] == "skill"


def test_explain_says_whether_a_packs_assets_actually_reach_a_prompt(installed):
    """A pack can be installed, valid, and entirely shadowed. That is the state somebody is
    looking for when they ask why their override did nothing, and `info` cannot see it —
    only the tier resolver can."""
    project, root = installed
    rows = explain(project, root, "joypla")
    recipe = next(row for row in rows if row["kind"] == "recipe")
    assert recipe["name"] == "hello"
    assert recipe["effective"] is True
    assert recipe["provided_by"] == "joypla"


def test_outdated_reports_a_newer_version_and_names_the_reason_per_row(installed):
    project, root = installed
    row, = outdated(project, root)
    assert row == {"id": "joypla", "current": "1.4.0", "latest": "1.5.0",
                   "reason": "outdated"}


def test_outdated_reports_an_unreadable_source_on_its_row_instead_of_raising(installed):
    """One unreachable remote should not hide the rest of the inventory."""
    project, root = installed
    write_sources(project, {"product": {"scheme": "git+file", "url": "/nowhere/{pack}"}})
    row, = outdated(project, root)
    assert row["reason"] == "source-unreachable"
    assert row["latest"] is None


def test_available_versions_ignores_tags_that_are_not_releases(remote):
    """`outdated` must not push somebody onto a hand-made tag its author never released."""
    _git(remote, "tag", "nightly")
    _git(remote, "tag", "v2.0.0-rc.1")
    source = {"scheme": "git+file", "url": str(remote.parent / "rig-pack-{pack}")}
    assert available_versions(source, "joypla") == ["1.4.0", "1.5.0"]


def test_update_moves_the_pin_and_the_content_together(installed, remote):
    project, root = installed
    result = update_pack("joypla", to="1.5.0", scope="project", project=project,
                         allow_unverified=True)
    assert result.manifest["version"] == "1.5.0"

    entry, = read_lock(root)["packs"]
    assert entry["version"] == "1.5.0"
    assert entry["source"]["path"] == "product:joypla@1.5.0"
    assert entry["source"]["revision"] == _git(remote, "rev-parse", "v1.5.0^{commit}")
    assert outdated(project, root)[0]["reason"] == "ok"


def test_a_failed_update_leaves_the_old_version_installed(installed):
    """Not remove-then-install: a failure in the second half of that would strand the project
    with neither version. The swap is last, so a refusal earlier changes nothing."""
    project, root = installed
    before = read_lock(root)["packs"]

    with pytest.raises(PackError, match="no tag v9.9.9"):
        update_pack("joypla", to="9.9.9", scope="project", project=project,
                    allow_unverified=True)

    assert (root / "joypla" / "pack.yaml").is_file()
    assert read_lock(root)["packs"] == before


def test_update_refuses_a_pack_that_has_no_source_to_ask(tmp_path, remote):
    """A pack installed from a directory has no version to resolve. Guessing where the newer
    copy lives would be inventing provenance."""
    project = tmp_path / "local-project"
    project.mkdir()
    source = tmp_path / "local-pack"
    source.mkdir()
    _write_pack(source, "joypla", "1.4.0")
    install_pack(source, scope="project", project=project, allow_unverified=True)
    with pytest.raises(PackError, match="which has no version to resolve"):
        update_pack("joypla", to="1.5.0", scope="project", project=project,
                    allow_unverified=True)


def test_update_refuses_when_the_tag_and_the_manifest_disagree(installed, remote):
    """A tag that carries a different version than the manifest inside it would install
    `1.6.0` under whatever the manifest happens to say. The two have to agree."""
    _write_pack(remote, "joypla", "1.4.0")
    _git(remote, "add", "-A")
    _git(remote, "commit", "--quiet", "-m", "mislabelled")
    _git(remote, "tag", "v1.6.0")
    project, _root = installed
    with pytest.raises(PackError, match="the tag and the manifest disagree"):
        update_pack("joypla", to="1.6.0", scope="project", project=project,
                    allow_unverified=True)


def test_inventory_cli_round_trip(installed, monkeypatch, capsys):
    project, _root = installed
    monkeypatch.chdir(project)
    assert cmd_pack(["list"]) == 0
    assert "joypla@1.4.0" in capsys.readouterr().out
    assert cmd_pack(["info", "joypla", "--json"]) == 0
    assert '"source_id": "product"' in capsys.readouterr().out
    assert cmd_pack(["explain", "joypla"]) == 0
    assert "recipe:hello" in capsys.readouterr().out
    # A newer version is available, so `outdated` reports non-zero — it is meant to be usable
    # as a check, not only as a listing.
    assert cmd_pack(["outdated"]) == 1
    assert cmd_pack(["update", "joypla", "--to", "1.5.0", "--allow-unverified"]) == 0
    assert cmd_pack(["outdated"]) == 0
