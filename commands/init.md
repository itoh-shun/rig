---
description: "rig/init — set a repository up for rig. Scaffolds the manifest (.claude/rig.md), the knowledge-layer directories, and the \"Compact Instructions\" section of CLAUDE.md. Every write is confirmed first, and it is idempotent (it never overwrites what is already there)."
argument-hint: "[--autonomous has no effect: init always confirms before writing]"
---

# rig/init — scaffold a repository

**Start the `rig:engine` skill with the Skill tool first and follow its SKILL.md** (context-minimal, the knowledge layer, §6 run-continuity). This command is only the entry point; the procedure itself lives in `facets/instructions/init` and is not repeated here.

Then follow `facets/instructions/init` to **scaffold**:

```
$ARGUMENTS
```

## What it creates (each written only after confirmation)

1. **The manifest**, `<repo>/.claude/rig.md` — built from `manifests/_template`, with build, lint, test, and the default branch detected and filled in.
2. **The knowledge-layer directories**, `<repo>/.claude/rig/knowledge/{domain,accumulated}/` — where domain knowledge and captured learnings live.
3. **The "Compact Instructions" section of CLAUDE.md** — the text that keeps rig's run state in the summary when the context is compacted. It says the same thing as the PreCompact hook in §6 run-continuity ④, by a second route that applies on every compaction.

## Rules

- **A write is a consequential action. Always show the proposal — what goes where — and get confirmation before writing. `--autonomous` does not lift that confirmation for init.**
- **Idempotent and non-destructive**: existing files are never overwritten; only what is missing is created or appended.
- init only scaffolds. It does not run an implementation or a review — that is what `/rig:dev` and its siblings are for.

## Example

```
/rig:init            # propose the manifest, knowledge layer, and Compact Instructions, then confirm and write
```

Afterwards, `/rig:dev` starts work and `/rig:dev --validate` checks the bricks for consistency.
