"""`pack install` accepted a zip long before anything could make one.

The zip path in the installer is fully implemented — extraction, member limits,
compression-ratio checks — and `docs/packs.md` demonstrated it with
`rig-wb pack install ./dist/my-domain.zip`. No rig command ever wrote a file at that path:
`pack export` produces a standalone repository tree, and there was no other producer. The
documented example was an artifact rig could not build.

The round trip is the acceptance test, and reproducibility is the property that makes the
lock's pin worth anything.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import zipfile

import pytest

from rig_workbench.packs.bundler import bundle_pack
from rig_workbench.packs.cli import init_pack
from rig_workbench.packs.model import PackError
from rig_workbench.packs.sync import sync_manifest


def _pack(tmp_path: pathlib.Path) -> pathlib.Path:
    """A `knowledge` pack of pure resource files — the one shape that carries no prompt
    material, so the evaluation gate does not apply and it can reach a valid state."""
    pack = init_pack("hogepack", kind="project", type_="knowledge", root=tmp_path / "build")
    (pack / "resources/note.md").write_text("# hoge knowledge\n", encoding="utf-8")
    sync_manifest(pack)
    return pack


def test_a_bundle_installs(tmp_path):
    """The round trip, which is the only claim that matters: what this writes, `install`
    takes. Everything else here is a property of that artifact."""
    from rig_workbench.packs.installer import install_pack

    built = bundle_pack(_pack(tmp_path), to=tmp_path / "hogepack.zip")
    project = tmp_path / "project"
    (project / ".rig").mkdir(parents=True)

    result = install_pack(built["path"], scope="project", project=project,
                          allow_unverified=True)

    assert result.manifest["id"] == "hogepack"
    assert (project / ".rig/packs/hogepack/resources/note.md").read_text() == "# hoge knowledge\n"


def test_a_rebuild_after_the_files_are_re_dated_is_byte_identical(tmp_path):
    """`install` records the archive's sha256 and `pack.lock.json` pins it, so two bundles of
    an unchanged pack have to agree — otherwise the pin reports a change on every rebuild,
    which is the same as reporting nothing.

    The mtimes are moved between the two builds on purpose. Building twice in one run proves
    nothing: the files carry the same timestamps either way, so the test passes even against
    a bundler that stores them. A fresh clone is the real case, and it gives every file a new
    mtime while changing no content."""
    pack = _pack(tmp_path)
    first = bundle_pack(pack, to=tmp_path / "a.zip")

    for path in pack.rglob("*"):
        if path.is_file():
            os.utime(path, (86400, 86400))
    second = bundle_pack(pack, to=tmp_path / "b.zip")

    assert first["sha256"] == second["sha256"]
    assert (tmp_path / "a.zip").read_bytes() == (tmp_path / "b.zip").read_bytes()


def test_the_reported_digest_is_the_digest_of_the_file(tmp_path):
    """The printed sha256 is what a consumer would pin. A value computed over anything but
    the archive's own bytes would be a number that looks like a pin and is not one."""
    built = bundle_pack(_pack(tmp_path), to=tmp_path / "hogepack.zip")

    assert built["sha256"] == hashlib.sha256((tmp_path / "hogepack.zip").read_bytes()).hexdigest()


def test_the_archive_holds_the_manifest_pair_and_every_declared_asset_and_nothing_else(tmp_path):
    """Stated as an equality, not a subset. A missing asset makes an archive that installs
    into a broken pack; an extra file makes one that fails `validate_pack` on the far side,
    where the cause is hardest to see."""
    pack = _pack(tmp_path)
    built = bundle_pack(pack, to=tmp_path / "hogepack.zip")

    with zipfile.ZipFile(built["path"]) as archive:
        names = set(archive.namelist())

    assert names == {"pack.yaml", "compatibility.yaml", "resources/note.md"}


def test_walking_the_directory_would_give_the_same_set_because_validation_says_so(tmp_path):
    """The honest statement of what building from the declaration buys, pinned so the claim
    stays true. It is *not* that undeclared files are kept out — `validate_pack`, which runs
    first, already refuses a pack directory containing any. This asserts that equivalence
    holds, so if validation ever stopped enforcing it the difference would surface here
    rather than in somebody's installed pack."""
    pack = _pack(tmp_path)
    built = bundle_pack(pack, to=tmp_path / "hogepack.zip")

    on_disk = {path.relative_to(pack).as_posix() for path in pack.rglob("*") if path.is_file()}
    with zipfile.ZipFile(built["path"]) as archive:
        assert set(archive.namelist()) == on_disk


def test_an_invalid_pack_is_refused_rather_than_bundled(tmp_path):
    """An archive built from a pack that does not validate can only produce the same failure,
    discovered later and one machine away from the person who could fix it."""
    pack = _pack(tmp_path)
    (pack / "resources/undeclared.md").write_text("# stray\n", encoding="utf-8")

    with pytest.raises(PackError, match="drift"):
        bundle_pack(pack, to=tmp_path / "hogepack.zip")


def test_a_signature_travels_with_the_pack_it_signs(tmp_path):
    """A signed pack whose signature stayed behind would install as unverifiable, and the
    consent flag that unlocks that path would look like the normal way to install it."""
    pack = _pack(tmp_path)
    (pack / "pack.sig.json").write_text('{"signature":"x"}\n', encoding="utf-8")

    built = bundle_pack(pack, to=tmp_path / "hogepack.zip")

    with zipfile.ZipFile(built["path"]) as archive:
        assert "pack.sig.json" in archive.namelist()


def test_an_existing_bundle_is_not_silently_overwritten(tmp_path):
    """The output path is often a released artifact. Overwriting one in place would change
    what a published sha256 refers to, with nothing said."""
    pack = _pack(tmp_path)
    bundle_pack(pack, to=tmp_path / "hogepack.zip")

    with pytest.raises(PackError, match="already exists"):
        bundle_pack(pack, to=tmp_path / "hogepack.zip")


def test_the_default_output_path_is_the_one_the_documentation_demonstrates(tmp_path,
                                                                          monkeypatch):
    """`docs/packs.md` has told people to install `./dist/<name>.zip` since before anything
    could write one. The default puts the file where the documentation already says it is."""
    pack = _pack(tmp_path)
    monkeypatch.chdir(tmp_path)

    built = bundle_pack(pack)

    assert built["path"] == (tmp_path / "dist" / "hogepack-0.1.0.zip").resolve()
