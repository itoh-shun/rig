---
description: "rig/forge — build rig's own bricks and packs from a description: recipes, instructions, personas, output contracts, commands. Self-extension. \"I want a flow / a review lens / a mode like this\" becomes something generated to rig's conventions and validated. The engine does not change; packs go on top."
argument-hint: "[\"<what you want>\"] [--type recipe|persona|knowledge|pack] [--name <id>] [--user]"
---

# rig/forge — writing skills 🧱✨

**Start the `rig:engine` skill with the Skill tool first and follow its SKILL.md** (PARSE → RESOLVE → COMPOSE → RUN, the §2 brick inventory, the §3.5 recipe schema, §5 tier resolution, context-minimal). This command is only the entry point; the procedure lives in `facets/instructions/skill-author` and is not repeated here.

Then follow `facets/instructions/skill-author` to generate the brick or pack:

```
$ARGUMENTS
```

## What it does

rig **extends itself**. It takes a description, works out which bricks are needed, generates them to rig's conventions, validates them, and saves them.

- **Deciding what to build**: a review lens goes to `/rig:persona`; domain knowledge goes to `/rig:knowledge`; a new flow or mode is a recipe plus an instruction, built here; a whole capability is a pack.
- **The shape of a pack**: a persona is judgement; knowledge is a catalogue of what to look at; an instruction is routing (native-first); a recipe is a bundle of steps with gates; an output contract is the output format; a command is the way in.
- **The engine does not change; packs go on top**: do not invent new control machinery. Compose the existing patterns — acceptance-gate, review-gate, parallel-fanout, autonomous-loop — and the existing facet types.
- **Finished means validated**: after generating, run rig's `--validate` (`python3 scripts/validate.py` inside rig itself) to check for broken references and schema drift, and fix every FAIL before finishing. Do not leave a broken brick behind.
- **Writes are confirmed and idempotent.** Never silently overwrite an existing brick.

## Where things are saved (tier)

| Scope | Path |
|---|---|
| project (the default, per product) | `<repo>/.claude/rig/...` |
| user (`--user`, global) | `~/.claude/rig/...` |
| shipped (working on rig itself, `--shipped`) | `skills/engine/...` plus the SKILL.md §2 inventory |

## Examples

```
/rig:forge "a flow that brings commit messages in line with our convention"
/rig:forge --type pack "a mode that judges tanka on five lenses"
/rig:forge "a reviewer specialising in accessibility"            # → delegated to /rig:persona
/rig:forge --user "my own pre-release checklist"                 # saved in the user layer
```

A generated brick in the project or user layer shows up in `--list` and `/rig:catalog`, and is usable immediately through `/rig:dev --recipe <name>` and friends.
