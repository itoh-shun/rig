# Capability landscape and architectural non-goals

This is not a benchmark and not a scoreboard. It exists to answer one question about every
roadmap item:

> Does this make rig's assurance or governance stronger, or does it only grow rig into
> another category of product?

The relationship it protects:

```text
An orchestrator decides how work runs.
rig decides whether the result is trustworthy enough to accept.
```

rig having execution capability of its own (#416) does not move that line. "Can run
standalone" and "competes on orchestration feature count" are different claims, and only
the first is a goal.

## How to read the table

| value | meaning |
|---|---|
| `native` | implemented in this repository, exercised by its own test suite |
| `partial` | present but limited; the limit is named in the notes |
| `external` | reachable through an integration, not built here |
| `non-goal` | deliberately not pursued — see [Architectural non-goals](#architectural-non-goals) |
| `unknown` | **not verified.** No claim is made either way |

`unknown` is the honest default for every column except rig's. The rig column is filled
from this repository, and each row names the file or command that backs it, so a claim
here can be checked rather than believed. Nobody on this project has run the other tools
under the axes below, and a landscape filled in from marketing pages would be a set of
guesses wearing a table's authority — which is the same failure this repository's
assurance work exists to prevent, pointed outward.

Filling a competitor cell is therefore a deliberate act with a procedure, below. Until
someone performs it, the cell stays `unknown`.

## The landscape

Products compared: **rig**, TAKT, VNX Orchestration, Nexus Agents, Gas City / Gas Town,
Ruflo, Daintree, MCO, Session Orchestrator.

Competitor columns are collapsed into one `others` column because they all currently hold
the same value. Splitting them into eight columns of `unknown` would suggest eight
separate findings where there are none. As cells get filled, split the column.

### Execution / orchestration

| # | capability | rig | others | rig's basis |
|---|---|---|---|---|
| 1 | Standalone execution (no host IDE session required) | `partial` | `unknown` | `rig-wb` CLI + `scripts/orchestrate.py` run outside a Claude Code session; the standalone harness is still in progress (#416) |
| 2 | Provider abstraction (swap the model/runtime) | `native` | `unknown` | `rig_workbench/orchestrate/providers.py` — claude / codex / ollama / lmstudio / cmd / mock / nested rig |
| 3 | Workflow definition | `native` | `unknown` | `skills/engine/recipes/*.md` — recipes compose steps; expressiveness is explicitly not a competition (non-goal 5) |
| 4 | State machine / loops / retries | `native` | `unknown` | `scripts/orchestrate.py` (`init`/`next`/`check`/`verdict`/`run`), `skills/engine/patterns/autonomous-loop.md` |
| 5 | Parallel fan-out | `native` | `unknown` | review fan-out (`skills/engine/patterns/parallel-fanout.md`), `queue go --max-parallel` |
| 6 | Durable queue surviving a restart | `native` | `unknown` | `.rig/queue.json` + detached worker (`rig_workbench/mission_worker.py`) |
| 7 | Task dependency graph | `partial` | `unknown` | `queue add --depends-on` (`rig_workbench/orchestrate/dependencies.py`) — the edge is *acceptance*, not completion (#427). **Local queue backend only**: issue-tracker backends carry state in labels, which cannot hold an edge list, and the flag is refused there rather than dropped |
| 8 | Worktree isolation per task | `native` | `unknown` | `workbench.py new` creates a git worktree; `accept` is the only path back to the main tree |
| 9 | Remote / fleet execution across machines | `non-goal` | `unknown` | see non-goal 1 |
| 10 | Resume after interruption | `native` | `unknown` | `orchestrate resume` (`rig_workbench/orchestrate/commands.py`), run state in `.rig/runs/<id>/` |

### Verification / assurance

| # | capability | rig | others | rig's basis |
|---|---|---|---|---|
| 11 | Independent verifier (separate process from the generator) | `native` | `unknown` | generator and verifier are separate roles run as separate processes |
| 12 | Cross-provider verification (verifier ≠ generator's model) | `native` | `unknown` | `--verifier-provider`; the default flow can implement with one model and verify with another |
| 13 | Deterministic gates (not model judgment) | `native` | `unknown` | acceptance-gate criteria + 7 machine sensors (secret / injection / destructive / anti-tamper / schema-diff / prompt-regression / evidence-anchor) |
| 14 | Acceptance boundary that a flag cannot bypass | `native` | `unknown` | `rig_workbench/workbench/accept.py` — structural prerequisites are refused even with `--force` |
| 15 | Reviewer quality measured, not asserted | `native` | `unknown` | `/rig:drill` scores each reviewer persona's detection rate against injected known bugs |
| 16 | Mutation / adversarial drill corpus | `native` | `unknown` | `workbench.py drill-corpus` — 24 seed classes, 4 fixture cases with answer keys |
| 17 | Evidence freshness / invalidation | `native` | `unknown` | `workbench.py receipt --verify` recomputes content digests; a changed source reads `invalidated` |
| 18 | Immutable target identity | `native` | `unknown` | `workbench.py import --head <sha>` pins the verified commit; a moved ref invalidates (#429) |
| 19 | Portable assurance receipt | `native` | `unknown` | `rig.assurance-receipt/v1` (`rig_workbench/workbench/assurance.py`) — a projection that re-judges nothing |
| 20 | Machine verdict for an external caller | `native` | `unknown` | `workbench.py contract --json` — `acceptable` / `not-acceptable` / `pending` / `execution-error`, one exit code each |
| 21 | Unmeasured values reported as unmeasured | `native` | `unknown` | `{"observed": false, "reason": …}` throughout the receipt; never a blank, zero, or default |
| 22 | Prompt-surface change tied to approved evaluation cases | `native` | `unknown` | `rig-wb eval affected --ratchet`, CI-enforced on every PR |

### Governance / security

| # | capability | rig | others | rig's basis |
|---|---|---|---|---|
| 23 | Audit log of overrides | `native` | `unknown` | `.rig/audit.jsonl` — every `accept --force` recorded with what was bypassed |
| 24 | Signed provenance of the accept decision | `native` | `unknown` | `provenance.json`, HMAC-SHA256 (`workbench.py verify-provenance`). Same-machine tamper evidence, **not** third-party public verification |
| 25 | Approval flow / separation of duties | `native` | `unknown` | `rig-wb govern` — inert unless `.rig/org.json` exists, so solo use is unchanged |
| 26 | Force-bypass visibility | `native` | `unknown` | `forced: true` on the task, surfaced in the receipt and the audit ledger |
| 27 | Sandbox strength distinguished, not conflated | `native` | `unknown` | the receipt says `git-worktree` / `main-tree` and refuses to borrow the evaluation sandbox's `os-enforced` rank |
| 28 | Secret / prompt-injection / destructive-command sensors | `native` | `unknown` | `scan-secrets`, `scan-injection`, `scan-destructive` — diff-scoped and fail-grade |
| 29 | Expiring exceptions (waivers) | `native` | `unknown` | `rig-wb govern` waiver layer |
| 30 | Multi-repository policy | `partial` | `unknown` | org → team → project policy is monotonic strengthening; the ledger is per repository |

### UX / integration

| # | capability | rig | others | rig's basis |
|---|---|---|---|---|
| 31 | CLI | `native` | `unknown` | `rig-wb` (pip-installable), `scripts/workbench.py`, `scripts/orchestrate.py` |
| 32 | GUI / cockpit | `native` | `unknown` | Mission Control, localhost-only; the browser implements no acceptance, governance or approval rule of its own |
| 33 | Machine-readable JSON output | `native` | `unknown` | `--json` on `route` / `receipt` / `contract` / `log` / `cockpit` / `gates` |
| 34 | MCP | `native` | `unknown` | `rig_workbench/remote_mcp.py` (`rig-mcp`) and the stdlib-only `scripts/mcp_server.py` |
| 35 | External orchestrator integration | `native` | `unknown` | `workbench.py import` — Bring Your Own Orchestrator (#429), [`byo-orchestrator.md`](./byo-orchestrator.md) |
| 36 | CI integration | `native` | `unknown` | `.github/workflows/validate.yml`; `eval affected --ratchet` gates prompt-surface changes |
| 37 | IDE / workspace management | `non-goal` | `unknown` | see non-goal 2 |

### Operations

| # | capability | rig | others | rig's basis |
|---|---|---|---|---|
| 38 | Run telemetry | `native` | `unknown` | `.rig/runs.jsonl`, `.rig/runs/<id>/*.json` |
| 39 | Usage / invocation accounting | `native` | `unknown` | `rig-wb usage` (per project, `--global` across projects) |
| 40 | Production outcome tracking | `native` | `unknown` | `record-commit` → `record-outcome` → `trace-commit`, which compares the gate's prediction against what actually happened |
| 41 | Failure-mode classification | `native` | `unknown` | `failure_mode` (MAST-style taxonomy) on escalated/blocked runs |
| 42 | Host prerequisite verification | `native` | `unknown` | `rig-wb hostcheck` — an axis it cannot verify reports MISS, never OK |
| 43 | Test-suite detection power (mutation score) | `native` | `unknown` | `rig-wb mutation` reads the project's own mutation report; a drop becomes a warning-grade criterion |
| 44 | Multi-model answer aggregation / consensus | `non-goal` | `unknown` | see non-goal 4 |

## Architectural non-goals

Each of these is a category rig declines to compete in. They are not judgments about the
tools that do compete there — several are good at it, which is exactly why rig should
integrate with them rather than reimplement them.

1. **Large-scale agent fleet scheduling.** Placing hundreds of agents across machines,
   bin-packing resources, cluster coordination. rig's queue is sized for work a person
   stacks up, and its dependency edges exist to enforce an acceptance boundary, not to
   express an arbitrary DAG.
2. **IDE / worktree workspace management.** rig creates a worktree because isolation is a
   precondition for its gate, not because it wants to be where you manage your workspace.
   Presenting worktrees, diffs and sessions to a human is somebody else's job.
3. **General-purpose agent swarm platform.** Arbitrary agents talking to arbitrary agents,
   with rig as the substrate.
4. **Multi-model answer aggregation / consensus UI.** rig runs more than one model on
   purpose, but to make the verifier structurally independent of the generator — not to
   blend several answers into one.
5. **Workflow DSL expressiveness.** Recipes exist to compose the flow rig needs. Growing
   them into a language that can express any workflow is the business of workflow engines.

**What is emphatically a goal:** applying the same assurance contract to work those tools
produced. `workbench.py import` takes an external orchestrator's commit and runs rig's own
isolation, sensors, gate and governance over it, then answers with a stable machine verdict
— see [`byo-orchestrator.md`](./byo-orchestrator.md). Being able to say "we did not run
this, and here is why it is acceptable anyway" is worth more than being able to run it.

### When a roadmap item collides with a non-goal

State the exception and its reason in the issue. A non-goal is a default, not a
prohibition — but crossing one silently is how a product changes category without anyone
deciding to.

## Filling a competitor cell

The rule from the issue, kept: **an unverified capability is `unknown` and is not guessed
at.**

To change a cell:

1. Name the evidence — a version, a documentation page, a command that was run. A cell
   without one stays `unknown`.
2. Record when it was checked. Capability claims decay; an undated one implies a currency
   it does not have.
3. Use `partial` and say what the limit is. A bare `native` on someone else's product is
   a stronger claim than it looks.
4. Do not mix popularity with capability. Stars, downloads and momentum belong nowhere in
   this table.

The maintainers of a compared product are the best source for their own column. A
correction from them outranks anything inferred here.

## Maintenance

**Every path in the rig column must resolve from the repository root.** A basis that
cannot be opened is decoration, and the first review of this document found seven claims
resting on abbreviated or stale paths — the audit trail failing in exactly the way it was
written to prevent. Check them when the table is touched.

Review at a major roadmap or architecture change, not on a schedule — a landscape rewritten
on a timer accumulates churn rather than accuracy. When a roadmap issue adds a capability,
add the row in the same change so the table and the code do not drift apart.

Related: [`architecture.md`](./architecture.md), [`byo-orchestrator.md`](./byo-orchestrator.md).
