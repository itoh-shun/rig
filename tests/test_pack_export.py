"""Taking a bundled pack out to its own repository (#523, slice S5).

The end-to-end assertion is the one that matters: export a pack that ships in this repository
today, make a git repository out of the result, tag it, and install it back through the named
source path. Nothing short of that proves the migration is possible — each half can look right
while the seam between them does not work.

The seam turned out to be real. A pack directory may hold nothing it has not declared, so a
repository that exists to distribute one pack cannot put it at the root: its own README would
be an undeclared file. These tests pin the shape that resolves it and the ambiguity that is
still refused.
"""

import pathlib
import subprocess

import pytest

from rig_workbench.packs.cli import cmd_pack
from rig_workbench.packs.exporter import export_pack
from rig_workbench.packs.installer import install_pack
from rig_workbench.packs.inventory import info
from rig_workbench.packs.model import PackError
from rig_workbench.packs.sources import write_sources
from rig_workbench.packs.validation import validate_pack

SHIPPED = pathlib.Path(__file__).resolve().parents[1] / "packs" / "domain"


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def test_export_puts_the_pack_below_the_repository_and_keeps_it_valid(tmp_path):
    """The pack keeps its strict declaration; the repository gets a README it could not
    otherwise have carried."""
    exported = export_pack(SHIPPED / "japanese-writing", to=tmp_path / "repo")

    root = tmp_path / "repo"
    assert (root / "README.md").is_file()
    assert (root / "japanese-writing" / "pack.yaml").is_file()
    assert exported["tag"] == f"v{exported['version']}"
    assert exported["type"] == "skill"

    # Still a pack, byte for byte: an export that dropped a file is caught by whoever runs the
    # export rather than by their first consumer.
    manifest = validate_pack(root / "japanese-writing")
    assert manifest["hashes"] == validate_pack(SHIPPED / "japanese-writing")["hashes"]


def test_a_shipped_pack_survives_the_whole_migration(tmp_path):
    """Export, publish as a git repository, tag, declare a source, install by spec — the
    migration in one test, on a pack that ships in this repository today."""
    export_pack(SHIPPED / "japanese-writing", to=tmp_path / "rig-pack-japanese-writing")
    repo = tmp_path / "rig-pack-japanese-writing"
    _git(repo, "init", "--quiet", "-b", "main")
    _git(repo, "config", "user.email", "packs@example.invalid")
    _git(repo, "config", "user.name", "packs")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "japanese-writing 0.6.0")
    version = validate_pack(SHIPPED / "japanese-writing")["version"]
    _git(repo, "tag", f"v{version}")

    project = tmp_path / "project"
    project.mkdir()
    write_sources(project, {"product": {
        "scheme": "git+file", "url": str(tmp_path / "rig-pack-{pack}")}})
    result = install_pack(f"product:japanese-writing@{version}", scope="project",
                          project=project, allow_unverified=True)

    assert result.manifest["id"] == "japanese-writing"
    detail = info(result.path.parent, "japanese-writing")
    assert detail["source_id"] == "product"
    assert detail["revision"] == _git(repo, "rev-parse", f"v{version}^{{commit}}")
    # The repository's own files stay in the repository: only the pack directory is installed.
    assert not (result.path / "README.md").exists()
    assert (result.path / "pack.yaml").is_file()


def test_a_repository_distributing_two_packs_is_refused_rather_than_guessed(tmp_path):
    """Two candidates mean two packs or a mistake, and installing the first would be a guess
    dressed up as a default."""
    export_pack(SHIPPED / "japanese-writing", to=tmp_path / "repo")
    second = export_pack(SHIPPED / "sales", to=tmp_path / "second")
    (pathlib.Path(second["pack_path"])).rename(tmp_path / "repo" / "sales")

    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(PackError, match="2 pack roots"):
        install_pack(tmp_path / "repo", scope="project", project=project,
                     allow_unverified=True)


def test_a_source_with_no_pack_says_so(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    empty = tmp_path / "not-a-pack"
    (empty / "docs").mkdir(parents=True)
    (empty / "README.md").write_text("# nothing here\n", encoding="utf-8")
    with pytest.raises(PackError, match="no pack root"):
        install_pack(empty, scope="project", project=project, allow_unverified=True)


def test_export_refuses_a_target_that_already_has_something_in_it(tmp_path):
    """Exporting into a populated directory would interleave two packs' files and produce a
    tree whose validity depends on what was already there."""
    target = tmp_path / "repo"
    target.mkdir()
    (target / "README.md").write_text("mine\n", encoding="utf-8")
    with pytest.raises(PackError, match="not empty"):
        export_pack(SHIPPED / "sales", to=target)


def test_export_cli_prints_the_commands_that_finish_the_job(tmp_path, capsys):
    """The tool stops at the tree. Which forge, public or private, who may read it — those are
    the owner's calls, and this migration exists to hand them over rather than guess them."""
    assert cmd_pack(["export", str(SHIPPED / "sales"), "--to", str(tmp_path / "repo")]) == 0
    out = capsys.readouterr().out
    assert "exported: sales@" in out
    assert "git init" in out and "git remote add origin" in out and "git tag v" in out
