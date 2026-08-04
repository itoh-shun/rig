# Rig packs

Rig packs are self-contained, versioned prompt extensions. The filenames remain
`pack.yaml` and `compatibility.yaml` for ecosystem compatibility, but their canonical
portable representation is the JSON-compatible YAML subset: UTF-8 JSON, sorted keys,
compact separators, and one trailing newline. Parsing therefore stays standard-library-only
and never evaluates YAML tags.

```console
rig-wb pack init my-domain --kind domain --root .rig/packs
rig-wb pack validate .rig/packs/my-domain
rig-wb pack validate --global
rig-wb pack doctor .rig/packs/my-domain --json
```

`pack init` refuses overwrites and creates every standard directory. Add assets to the
manifest, record their SHA-256 hashes, and bind each prompt-bearing pack to at least one
approved evaluation case. Validation rejects unknown manifest fields, undeclared files,
ownership crossover, path traversal, symlinks, broken references, incompatible engine or
dependency ranges, cycles, collisions, unsafe secrets, invisible injection markers, and
unambiguous destructive content.

All prompt assets use one precedence order:

1. project `.rig/packs` and legacy `.rig` / `.claude/rig` overlays;
2. user `~/.rig/packs`;
3. organization `$RIG_ORG_HOME/packs`;
4. shipped official packs;
5. Rig core assets.

Resolution reports the selected tier/source and every shadowed candidate. Runtime recipe
and verifier-persona loading use this resolver. Project recipes, including recipes inside a
project pack, retain the existing explicit content-hash trust gate.

`pack doctor` is deterministic and read-only. It reports missing dependencies,
incompatibility, shadows, invalid/evaluation-drifted packs, and coexistence with legacy
overlays without modifying either representation.
