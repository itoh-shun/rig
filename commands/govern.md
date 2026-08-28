---
description: "rig/govern — the organisational governance pack. Treats a common policy (org -> team -> project, tightening only), permissions, an approval flow, waivers, and a tamper-evident audit ledger as first-class concepts, and measures conformance across teams. `rig-wb govern` is the source of truth for every judgement."
argument-hint: "[audit|init|policy|approve|waiver] [a path or task-id] [--all <dir>] [--plan]"
---

# rig/govern — organisational governance, the org layer of the AI Quality Operating System 🏛️📋

**Start the `rig:engine` skill with the Skill tool first and follow its SKILL.md** (PARSE → RESOLVE → COMPOSE → RUN, context-minimal, facet ordering, knowledge-layer injection). This command is only the entry point; the engine lives in the skill and is not repeated here. This is the govern pack: the same engine, widened from the quality of one repository to **the quality of an organisation**.

```
$ARGUMENTS
```

## What this layer is for

rig v1's assets — the acceptance gate, isolated worktrees, independent verification, force-proof accept — are **complete for one person and one repository**. They break only when the same thing is handed to teams A, B, and C: **criteria stop being in sync** (each repository's `.rig/gates.json` is its own), **permission is binary** (a list of who may accept), **approval is a conversation rather than a record**, and **the audit log can be edited**. v2 makes those four first-class.

```
team A ─┐
team B ─┼─→ common policy ─→ permissions → approvals → exceptions → audit
team C ─┘        (tightening only: a lower layer can never loosen)
```

## Sub-modes

| Argument | What it does |
|---|---|
| `audit` (the default) | Measure conformance (the `govern-audit` recipe). `--all <dir>` goes across teams. Read-only. |
| `init` | Bind the repository into an org and team and scaffold the common policy (with `migrate` offered first when `.rig/access.json` or `.rig/gates.json` already exist) |
| `policy` | Show the policy in effect, lint it (has a layer loosened what is above it?), and discuss amendments |
| `approve` | Grant or deny an approval, and show its state |
| `waiver` | Issue, list, or revoke an exception |

If the arguments begin with one of those words, use it; otherwise default to `audit` and PARSE the rest as the target.

## What it does

Hands the target to the `govern-audit` recipe. `facets/instructions/govern` is the source of truth for the procedure, `facets/knowledge/quality-operating-system` for the lenses, and `facets/output-contracts/conformance-report` for the output.

- **rig does not judge; it reads what `rig-wb govern` returns.** Never declare conformance in prose. A lens with no number is "not measured".
- **The real work is done by subagents** (context-minimal). Long policy JSON and ledgers never reach the parent.
- **Read-only.** Never rewrite a policy, a permission, or the ledger. Present the command that would change it and let a person run it.

## The deterministic runner (the source of truth for judgements and records)

```
rig-wb govern init --org acme --team team-a       # bind the repository to an org and team, scaffold a policy
rig-wb govern migrate --org acme                  # fold v1's access.json and gates.json into the policy layer
rig-wb govern policy show|lint                    # show the layers in effect / check for loosening (exit 3 = loosened)
rig-wb govern whoami                              # your role and permissions
rig-wb govern can accept.force                    # check one permission (exit 3 = denied)
rig-wb govern approve status|grant|deny <task-id> # the approval flow (the author's own approval never counts)
rig-wb govern waiver grant <id> --criterion <c> --reason "..." --expires YYYY-MM-DD
rig-wb govern audit log|verify|export --format csv   # read the ledger / verify the chain / export for audit
rig-wb govern conformance [--json]                # one repository's conformance (exit 3 = something FAILed)
rig-wb govern rollup --scan <dir> [--json]        # across teams: A, B, C against the common policy
```

**Inert by default**: in a repository with no `.rig/org.json`, these answer "ungoverned" and change nothing about how rig behaves. Solo development is identical to v1.

## The one design constraint on a common policy: it only tightens

The layers stack org → team → project, and **a lower layer can only tighten what is above it**.

| Allowed | Refused (`policy lint` names the layer and the field) |
|---|---|
| adding a criterion | removing a criterion an upper layer requires (omitting it inherits it) |
| raising a quorum | lowering a quorum |
| shortening an approval or waiver expiry | extending one |
| narrowing a role's permissions | adding a permission to a role, or creating a role with a permission the org never delegated |
| adding to `non_waivable` | disabling `required_for_force` or `audit.chain_required` |
| granting an unsealed role | adding yourself to `sealed_roles` |

**A broken policy fails closed**: v1's `.rig/access.json` fell back to "unrestricted" when it was broken, which is the safe side for one person. The policy layer **stops accept** instead. An organisation's rules disappearing quietly over one misplaced comma is the one failure this layer does not permit.

## Stage governance (v2.1)

Approval is not only about accept. A recipe's step can declare its owning role and a human gate:

```yaml
steps:
  - id: architecture_review
    actor: architect          # the organisational role that owns this stage (not an LLM persona)
    human_gate: true          # park until somebody qualified signs. Also writable as {quorum, roles, separation_of_duties, expires_hours}
```

```
rig-wb orchestrate next                                  # → AWAIT_APPROVAL, exit 3 (waiting on a person, not a failure)
rig-wb orchestrate approve architecture_review --note "the boundary is sound"
rig-wb orchestrate approve architecture_review --deny --note "there is no ADR"
```

- Even after the machine gate passes, the run **parks in `awaiting_approval`** until the approvals are in. That state persists in the run-state, across processes, sessions, and days.
- The arithmetic of approval is **the same implementation** as accept's: quorum, qualified roles, **separation of duties** (whoever ran the stage cannot sign for it), and **freshness** (bound to the commit that was approved).
- An org policy can impose approval on a step the recipe never asked for, through **`stage:<step-id>`** under `approvals`. Recipe and policy **compose to whichever is stricter** — the higher quorum, the union of roles, the shorter expiry — so a recipe cannot negotiate the org down.
- The decision lands in both the run-state's `step_state[].approvals` and the ledger's `stage.approve` and `stage.deny`.
- **`actor` does not block execution.** What rig can attest is that the owning role **signed**, not that the owning role **typed**. Refusing to execute would not make anything safer, only break CI, so execution outside the owning role is recorded as a WARN and in the history, and the enforcement lives at the gate.

## How this relates to accept (no second chokepoint)

Approval **adds to the acceptance gate; it does not replace it**. Where a policy exists, `accept` passes through the accept permission, the approval quorum (**separation of duties**: the author's approval does not count; **freshness**: it lapses when the branch moves), the `--force` permission, and the validity of any exception, before it reaches the squash merge. There is still exactly one chokepoint, accept — build a second and you have built a way around the first.

## Flags

- `--all <dir>` — audit every repository directly under that directory (`govern rollup --scan`). This is how teams are compared.
- `--plan` — present the audit composition and stop. A dry run.

## Examples

```
/rig:govern                          # measure this repository's conformance
/rig:govern audit --all ~/work/acme  # a score table across teams A, B, and C
/rig:govern init                     # bind an org and team and scaffold the common policy
/rig:govern policy                   # what is in effect, and whether any layer is loosening it
/rig:govern approve rig-20260807-... # see or grant an approval
/rig:govern waiver                   # the live exceptions — has one become permanent?
```

## Why route this through rig

Ask a bare model to "set up governance" and what comes back is a **template policy document**. A document is a claim, not a measurement. The govern pack turns the common policy into **an executable layer with tightening guaranteed**, asks about permissions, approvals, and exceptions mechanically inside accept, makes the audit ledger **one where edits are detectable**, and finally answers whether any of it is working with numbers: the force rate, how far the layers reach, and how many required criteria are actually implemented. The gap between writing a policy and a policy having effect is the whole of this pack.

## run-continuity (SKILL.md §6)

While a RUN is active, restate this run-status header as a single line at the top of every turn. Do not drop it right after an interruption, a question, or tool output:

```
▸ rig | recipe: govern-audit | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
