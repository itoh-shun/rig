---
description: "rig/knowledge — generate domain knowledge as LLM-wiki pages. Drafts one canonical page per concept, from a description or from --auto (analysing the repo), and saves it globally (the default, shared by every product) or as a project overlay (--project). Personas reference them with inject: [[slug]]."
argument-hint: "[--research \"<topic>\"] [--graph] [\"<description>\" | --auto] [--project] [--name <slug>]"
---

# rig/knowledge — the domain-knowledge generator (wiki)

**Start the `rig:engine` skill with the Skill tool first and follow its SKILL.md** (context-minimal, knowledge-layer injection in §5, `facets/knowledge/_wiki`). This command is only the entry point; the procedure lives in `facets/instructions/knowledge-gen` and is not repeated here.

Then follow `facets/instructions/knowledge-gen` to generate the wiki page:

```
$ARGUMENTS
```

## What it does

Drafts domain knowledge as a **wiki page** — one canonical page per concept, cross-linked with `[[slug]]` — proposes it, writes it **once confirmed**, and updates `INDEX.md`.

- **Modes**: draft from `"<description>"`; `--auto` analyses the repo and distils it (ubiquitous language, the domain model, conventions, ADR-shaped decisions); `--graph` distils the repo's **typed knowledge graph** (entities plus relations: calls, depends-on, part-of, is-a, stores-in, emits, reads-from) into the wiki page `[[codebase-graph]]` (a project overlay by default, capped context-minimal at 40 entities and 80 relations); `--research` synthesises from web research.
- **Where it goes**: `~/.claude/rig/knowledge/wiki/<slug>.md` by default — **global, shared by every product**. `--project` writes `<repo>/.claude/rig/knowledge/wiki/<slug>.md` as an overlay.
- A persona references the generated page with **`inject: ["[[<slug>]]"]`** rather than embedding the facts, so the knowledge does not become tacit.

## Flags

- `--auto` — analyse the repo and generate domain knowledge from it, grounded in the real code and docs. Nothing invented.
- `--graph` — distil the repo's typed knowledge graph into `[[codebase-graph]]`, and propose the `inject:` for reviewers, so relations can be traced instead of the whole thing being read. For rig's own network of bricks, use `/rig:catalog --graph`, which derives it rather than writing it by hand.
- `--project` — save as a project overlay. The default is global.
- `--name <slug>` — set the slug for a single page explicitly.

## Rules

- **Writes are confirmed, idempotent (an existing slug is never overwritten), and never invented — grounds go in `sources`.** Say plainly that a global write affects every product. `--autonomous` does not lift the confirmation.
- One concept, one canonical page. Where a page for the same idea exists, add to it or link it; do not create a duplicate.

## Examples

```
/rig:knowledge "the forms and invariants of nineties house production"
/rig:knowledge --auto                                          # this repo's domain knowledge
/rig:knowledge "the bounded context around payments" --project  # an overlay for this product
/rig:knowledge --graph                                         # typed knowledge graph -> [[codebase-graph]]
# then reference it from a persona:
#   # persona: house-authenticity
#   inject: ["[[genre-house]]", "[[music-era-90s]]"]
```
