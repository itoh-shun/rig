---
description: "rig/export — give back what rig grew: write a persona, recipe, or pack out as a standalone Claude Code skill. Strips rig's own vocabulary so the result is self-contained, and carries provenance and licence with it. The counterpart to import: this returns things to the network."
argument-hint: "[--persona <name> | --recipe <name> | --pack <name>] [--to <dir>] [--dry-run]"
---

# rig/export — write a brick out as a skill 📤

**Start the `rig:engine` skill with the Skill tool first and follow its SKILL.md** (PARSE → RESOLVE → COMPOSE → RUN, the §2 brick inventory, context-minimal). This command is only the entry point; the procedure itself lives in `facets/instructions/skill-export` and is not repeated here.

Then follow `facets/instructions/skill-export` to write the brick out:

```
$ARGUMENTS
```

## What it does

The counterpart to `/rig:import`. It converts a persona, recipe, or pack that rig grew into a Claude Code skill repository — SKILL.md, README, `references/`, LICENSE — that **somebody who has never heard of rig can use as it is**.

- **Made self-contained**: output contracts are expanded inline, wiki `inject:` targets ship as files, gates are translated into prose. No rig-specific vocabulary or reference is left behind.
- **The chain of provenance is not broken**: re-exporting something that came in through import checks the upstream provenance and its licence obligations first, and stops with a report when redistribution is not allowed.
- **The export-import loop**: put the written skill on GitHub and another rig user can take it with `/rig:import <owner>/<repo>`.
- Writes are confirmed and idempotent; `--dry-run` previews without writing.

## Examples

```
/rig:export --persona house-authenticity --dry-run   # preview the layout only
/rig:export --persona house-authenticity             # one persona as a skill
/rig:export --recipe strict-tdd --to ~/skills-out    # a recipe you grew
/rig:export --pack my-domain                         # a whole pack as a skill
```
