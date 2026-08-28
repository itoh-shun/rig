---
description: "rig/catalog — the cross-tier registry. Walks every tier (shipped, global, project) and shows the map of domain x pack x persona x wiki x recipe, so you can see again who is doing what where. Read-only."
argument-hint: "[--domain <tag>] [--json] [--graph [--focus <name>]]"
---

# rig/catalog — the cross-tier registry

**Start the `rig:engine` skill with the Skill tool first and follow its SKILL.md** (context-minimal, tier resolution, the knowledge layer). This command is only the entry point; the procedure lives in `facets/instructions/catalog` and is not repeated here. It is equivalent to `--list --global`.

Then follow `facets/instructions/catalog` to walk every tier and print the map:

```
$ARGUMENTS
```

## What it does

Walks shipped, user (global), and project (`<repo>`), and maps **the packs, personas (and the wiki pages they inject), wiki pages, and recipes of each domain**, each labelled with **the tier it lives in**. The registry is never stored by hand: it is **derived by walking, every time**, so it cannot drift. **Read-only, no side effects.**

This is the view for when domains and products have multiplied and nobody can say who is doing what where any more.

## Flags

- `--domain <tag>` — show only that domain.
- `--json` — machine-readable output, for later graph visualisation. The default is a Markdown map.
- `--graph` — show the **typed brick graph** (implemented by `scripts/orchestrate.py graph`). Eleven kinds of relation — injects, extends, uses-*, gated-by, mirrors and the rest — are **derived** from frontmatter and `steps:` rather than written by hand, so they cannot rot. `--focus <name>` shows one hop around a brick (what it uses and who uses it); add `--json` for machine-readable output.

## Related

- `/rig:dev --validate --global` — cross-tier hygiene (orphans, broken links, missing references, duplicates).
- `/rig:persona` and `/rig:knowledge` — the generators that add the personas and wiki pages this map lists.

## Examples

```
/rig:catalog                 # the map of every domain
/rig:catalog --domain music  # only the music domain
/rig:catalog --json          # machine-readable
/rig:catalog --graph                            # a summary of the typed graph
/rig:catalog --graph --focus security-reviewer  # one hop: who uses it, what it injects
```
