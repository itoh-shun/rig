---
description: "rig/harness — audit a project's agent development harness on a 2x2 (computational or inferential, guide or sensor). Surfaces the empty quadrants and the assets that exist but do not bite — lint and tests outside the loop, rules that stop at prose. Answers with connect, enforce, and thin out rather than add."
argument-hint: "[target (defaults to the current repository)] [--plan]"
---

# rig/harness — harness audit 🧭

**Start the `rig:engine` skill with the Skill tool first and follow its SKILL.md** (PARSE → RESOLVE → COMPOSE → RUN, context-minimal, facet ordering, knowledge-layer injection). This command is only the entry point; the engine lives in the skill and is not repeated here.

Then PARSE the following as the target, with `--recipe harness-audit` as the default:

```
$ARGUMENTS
```

With no argument, the target is the current repository.

## What it does

Hands the target — the repository plus its AI development setup — to the `harness-audit` recipe. The procedure (fix the target, inject `harness-taxonomy`, inventory, classify on the 2x2 and find the holes, propose moves, structure the result as a `harness-map`) is in `facets/instructions/harness-audit`.

- **Inventory on the 2x2**: computational guides (types, scaffolds, CLIs); computational sensors (lint, types, tests, build, CI); inferential guides (CLAUDE.md, skills, personas); inferential sensors (AI review, review-gate). Make **the empty quadrants** visible.
- **Separate "exists" from "bites"**: the first thing to pick up is a test or a linter that is merely present and wired to no hook and no acceptance gate — it applies no back pressure to the loop. A rule that stops at prose counts as unenforced.
- **Connect, enforce, and thin out before adding**: a new rule comes last. Well-meant additions can make things worse — watch for context rot. Computational sensors first, inferential review second.
- The audit is read-only. Fixes go to `/rig:dev`, to hook configuration, to `acceptance-gate` criteria, and to `/rig:goal`'s independent verification.

## Flags

- `--plan` — present what the audit would inventory, then stop. A dry run.

## Examples

```
/rig:harness                  # audit the current repository's harness
/rig:harness --plan           # see what it would inventory first
/rig:harness ./packages/api   # target one directory
```
