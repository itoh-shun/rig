"""Produce the zip that `pack install` already accepts.

`pack install` takes a directory, a zip, or a tar, and the zip path is fully implemented —
extraction, member limits, compression-ratio checks, the lot. `docs/packs.md` demonstrates it
with `rig-wb pack install ./dist/my-domain.zip`. Nothing in rig ever wrote a file at that
path: `pack export` produces a standalone *repository* tree, and there was no other producer.
The documented example was an artifact rig could not make.

Two properties decide the shape of this.

**What ships is what is declared.** The archive is built from the manifest's asset lists
rather than from a directory walk. To be precise about what that does and does not buy: the
pack is validated first, and `validate_pack` already refuses a directory holding any file it
has not declared, so for a bundleable pack the two would produce the same set. The
declaration is used because it states the intent directly and does not depend on a second
list — `validate_pack`'s own exclusions — staying in agreement with this one. The guarantee
that nothing undeclared ships comes from the validation, not from this choice.

**The bytes are reproducible.** `install` records `sha256` of the source archive, and
`pack.lock.json` pins it. If two bundles of an unchanged pack differed — and by default they
would, because a zip stores each member's mtime — that pin would say a pack had changed every
time it was rebuilt, which is the same as saying nothing. Entries are therefore sorted, dated
to the zip epoch, and given fixed permissions.
"""

from __future__ import annotations

import hashlib
import pathlib
import zipfile

from .manifest import safe_relative
from .model import PackError
from .validation import validate_pack

#: The zip format's own epoch. Any fixed value works; this one is conventional for
#: reproducible archives and is the earliest a zip can represent.
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

#: Regular file, 0644. Fixed for the same reason the timestamp is: the umask of whoever ran
#: the build is not a property of the pack.
FILE_ATTR = (0o100644 << 16)

#: Written beside the assets. `pack.sig.json` is included when it exists — a signature that
#: did not travel with the pack it signs would make every install unverifiable.
MANIFEST_FILES = ("pack.yaml", "compatibility.yaml", "pack.sig.json")


def bundle_paths(manifest: dict) -> list[str]:
    """Every file the archive should contain, sorted, taken from the declaration.

    Sorted because the archive's bytes are compared; taken from the manifest because a
    directory walk would ship undeclared files that `validate_pack` exists to refuse.
    """
    declared = {item for paths in manifest["assets"].values() for item in paths}
    return sorted(declared)


def bundle_pack(source: pathlib.Path | str, *, to: pathlib.Path | str | None = None) -> dict:
    """Write `source` out as an installable zip. Returns the path, its digest, and the count.

    The pack is validated first. Bundling an invalid pack would produce an archive whose only
    possible outcome is the same failure, discovered later and somewhere else.
    """
    root = pathlib.Path(source).resolve()
    manifest = validate_pack(root)
    destination = (pathlib.Path(to) if to is not None
                   else pathlib.Path("dist") / f"{manifest['id']}-{manifest['version']}.zip")
    destination = destination.resolve()
    if destination.exists():
        raise PackError(f"bundle already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    members: list[str] = [name for name in MANIFEST_FILES if (root / name).is_file()]
    members.extend(bundle_paths(manifest))
    try:
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in members:
                info = zipfile.ZipInfo(name, date_time=ZIP_EPOCH)
                info.external_attr = FILE_ATTR
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, (root / safe_relative(name)).read_bytes())
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise PackError(f"cannot write bundle: {exc}") from exc
    return {
        "path": destination,
        "id": manifest["id"],
        "version": manifest["version"],
        "members": len(members),
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
    }
