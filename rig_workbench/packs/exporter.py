"""Export a pack as a standalone repository (#523, slice S5).

The platform's end state is that rig ships no domain packs and each team owns its own
repository. The mechanical half of getting there is this: take a pack that currently lives
inside another repository and write it out as the root of its own, validated, with the
release tag its version implies.

What this deliberately does not do is create the repository, push it, or delete the original.
Those are the owner's calls — which forge, public or private, who has access — and a tool
that made them would be guessing at exactly the decisions the migration exists to hand over.
This produces the tree and prints the three commands that finish the job.
"""

from __future__ import annotations

import pathlib
import shutil

from .model import PackError
from .validation import validate_pack

README = """# {display_name}

A [rig](https://github.com/itoh-shun/rig) pack: `{pack_id}` ({type_}).

{description}

## Install

```console
rig-wb pack source add {suggested_source} --scheme git+ssh --url <this repository's URL>
rig-wb pack install {suggested_source}:{pack_id}@{version}
```

`--scheme git+https` works the same way; rig never holds a credential, so authentication is
whatever `git` on that machine already uses.

## Release

The version in `pack.yaml` and the tag have to agree — `rig-wb pack update` refuses a tag
whose manifest declares a different version:

```console
rig-wb pack validate {pack_id}
git tag v{version} && git push origin v{version}
```
"""


def export_pack(source: pathlib.Path | str, *, to: pathlib.Path | str) -> dict:
    """Write `source` out as a standalone repository tree at `to`.

    The pack lands one level down, with the repository's own files at the root:

        <to>/README.md
        <to>/<pack-id>/pack.yaml, recipes/, facets/, ...

    That nesting is not cosmetic. A pack directory may contain nothing it has not declared —
    `validate_pack` refuses undeclared files, which is what makes the type rules enforceable
    rather than advisory — so a README, a licence, or a CI workflow cannot live beside
    `pack.yaml`. Putting the repository's furniture at the root keeps both properties: the
    pack stays strictly declared, and the repository stays a normal repository. Only the pack
    directory is copied to whoever installs it.

    The pack is validated where it stands first. Exporting an invalid pack would move the
    problem into a fresh repository, where whoever has to fix it knows less than the person
    exporting it does.
    """
    pack = pathlib.Path(source).expanduser().resolve()
    manifest = validate_pack(pack)
    destination = pathlib.Path(to).expanduser().resolve()
    if destination.exists() and any(destination.iterdir()):
        raise PackError(f"export target is not empty: {destination}")
    inner = destination / manifest["id"]
    inner.mkdir(parents=True)

    for item in sorted(pack.iterdir()):
        target = inner / item.name
        if item.is_dir():
            shutil.copytree(item, target, symlinks=False)
        else:
            shutil.copy2(item, target)

    (destination / "README.md").write_text(README.format(
        display_name=manifest.get("display_name", manifest["id"]),
        pack_id=manifest["id"], type_=manifest["type"],
        description=manifest.get("description", ""),
        version=manifest["version"],
        suggested_source=_suggested_source(manifest),
    ), encoding="utf-8")

    # The copy has to still be a valid pack. Validating it here means an export that dropped
    # or corrupted a file is caught by the person doing the export, not by their first
    # consumer.
    exported = validate_pack(inner)
    if exported["hashes"] != manifest["hashes"]:
        raise PackError("export changed the pack's asset hashes")
    return {
        "id": manifest["id"], "version": manifest["version"], "type": manifest["type"],
        "path": str(destination), "pack_path": str(inner),
        "tag": f"v{manifest['version']}",
        "suggested_source": _suggested_source(manifest),
    }


def _suggested_source(manifest: dict) -> str:
    """A source name to suggest, from the pack's own kind. Only a suggestion: the source name
    is the consumer's word for where their packs come from, not the pack's word for itself."""
    return "product" if manifest["kind"] in {"domain", "project"} else "official"
