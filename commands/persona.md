---
description: "rig/persona — generate a reviewer persona from a description. Saves it per product (the project layer, the default) or globally (the user layer, --user), after which --persona <name> puts it into a review. For example, \"a reviewer who understands eighties music\"."
argument-hint: "[\"<what kind of reviewer>\"] [--user] [--name <id>]"
---

# rig/persona — the persona generator

**Start the `rig:engine` skill with the Skill tool first and follow its SKILL.md** (context-minimal, facet ordering, persona tier resolution in §5). This command is only the entry point; the procedure itself lives in `facets/instructions/persona-gen` and is not repeated here.

Then follow `facets/instructions/persona-gen` to generate the persona:

```
$ARGUMENTS
```

When the description is empty, ask in one line what kind of reviewer they want. Do not invent one.

## What it does

Drafts a reviewer persona facet from the description, shows the draft and where it would go, and writes it **once confirmed**.

- **Where it goes**: `<repo>/.claude/rig/personas/<name>.md` by default (per project, per product). `--user` puts it in `~/.claude/rig/personas/<name>.md` — global, shared by every project.
- **The name**: without `--name`, propose a slug from the description ("eighties music…" → `music-era-80s-reviewer`).
- The generated persona can be put into a review with **`--persona <name>`**; tier resolution finds it by name.

## Flags

- `--user` — save globally, in the user layer. The default is the project.
- `--name <id>` — set the filename and persona name explicitly.

## Rules

- **Writes are confirmed, idempotent (never overwriting), and never invented.** Say plainly that a global write affects every project before making one. `--autonomous` does not lift the write confirmation.
- It generates a persona facet only. It does not create a native agent.

## Examples

```
/rig:persona "a reviewer who understands eighties music"      # → generated in the project
/rig:persona "a senior who is strict about security" --user   # → generated globally
/rig:persona "someone with taste in UX copy" --name ux-copy-taste
# then use it:
/rig:dev --only review --persona music-era-80s-reviewer
```
