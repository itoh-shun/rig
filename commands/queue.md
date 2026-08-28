---
description: "rig/queue — stack tasks up and GO on all of them. cancel takes one back before it ever runs. The queue lives in an issue tracker (GitHub or GitLab) or locally; go runs every task in parallel, each through its gate, and writes the results back to the issue."
argument-hint: "<add \"task\" | list | go | done id | retry id | cancel id> [--depends-on ID] [--backend local|github|gitlab] [--repo owner/repo] [--provider rig] [--max-parallel N]"
---

# rig/queue — a task queue: stack them up, then GO 📋

**Start the `rig:engine` skill with the Skill tool first and follow its SKILL.md** (context-minimal, computational orchestration in §4.3). The queue itself is `scripts/orchestrate.py queue`, the deterministic runner that is the GO engine.

```
$ARGUMENTS
```

## What it does

Moves you from "one request at a time" to **stack them up and run the lot**.

```
orchestrate queue add "<what to do>"      # stack one
orchestrate queue list                    # check (failure reasons and completion comments show as a note at the end of the line)
orchestrate queue go --provider rig --max-parallel 3   # GO on all of them
orchestrate queue done <id>               # mark one complete by hand
orchestrate queue retry <id>              # put a failed item (one that failed verification) back to queued for the next GO
orchestrate queue cancel <id>             # stacked but never run: take it back unrun (#459)
orchestrate queue add "<what to do>" --depends-on <id> [--depends-on <id> ...]   # add a dependency (#427)
```

- **go** runs every stacked task: independent ones **in parallel, in separate processes**, each through the gate of generation then **independent verification (grader ≠ generator)**, with one report at the end. Underneath it is the existing orchestrate — parallel, multi-provider, local LLMs — used as the GO engine.
- Providers are `rig` (each task through the rig harness; recommended), `claude`, `codex`, `ollama`, `lmstudio`, `cmd`, and `mock`.
- **`--provider rig` (the default) dispatches each item through `/rig:go "<task>"`** — `patterns/isolated-worktree` puts every task in its own worktree, so **parallel headless processes never fight over the same files**. The queue's verifier only judges whether the gate was reached and whether the work stayed inside the isolated worktree instead of writing to the main tree; it **does not accept** (the queue is the layer of isolation, execution, and gating; landing the change is your explicit move).
- **`queue list` shows only active items — queued, running, failed — never done** (on `local`, `github`, and `gitlab` alike), so finished work does not bloat the listing.
- **`queue cancel <id>` and `queue done <id>` are different things.** `done` records "this ran and finished", and it counts towards the throughput `cockpit` reports. Marking a typo, a duplicate, or something you no longer want as `done` means **work you threw away is counted as work you completed**. `cancel` is a status for "stacked, never run": it disappears from `queue list` like `done`, but `cockpit` **counts them separately**, as in `Nothing pending (3 done, 1 cancelled)`.
  - **Only `queued`, `waiting`, `blocked`, and `failed` can be cancelled.** The check and the write are a **compare-and-set inside one lock**. Split them and a `queue go` claim slips between: it sees queued, claims, the cancel writes `cancelled`, the provider overwrites it, and **the cancellation silently did nothing** while the person who asked for it believes it took.
  - **`running` is refused**: a live provider owns that item and will write `done` or `failed` when it ends, erasing the `cancelled`. **`done` is refused too** — rewriting something that ran and finished as "never run" is a lie about the past. `failed` can be cancelled, and its note and output use **different wording** that makes clear it did run: telling an audit that something which ran was never run corrupts the very thing this status exists to protect. Neither wording says "this cannot be undone", because it can be retried, and saying so would talk somebody out of an operation that works.
  - **A cancelled item can be retried**, through `queue retry <id>` or Mission Control's Retry. Allow only one and the CLI and the screen disagree about the same item.
  - **`cancel` is local-backend only** — deliberately out of scope in #459. An issue label has no state meaning "never run", and `queue_set_status` neither labels nor closes but only comments, so **an item you meant to cancel would stay queued**.
  - To a dependency (#427), **cancelled is terminal**. It will never become `done`, so anything downstream of a cancelled item is `blocked`, not `waiting`.
- **`queue retry <id>`** returns an item that `failed` verification to `queued` so the next `queue go` picks it up — a way to retry a task that fell over on, say, a provider timeout, without retyping the task and getting a new id and a new issue.

## Dependencies: making acceptance the edge (#427)

```
/rig:queue add "DB migration"                              # → #1
/rig:queue add "API implementation" --depends-on 1         # → #2
/rig:queue add "Release candidate" --depends-on 2 --depends-on 3
```

**What starts the next item is not "the previous agent finished" but "the previous output crossed rig's acceptance boundary".** That is the one essential difference, and it is why a queue item reaching `done` does not satisfy a dependency: `done` means the gate settled, not that anybody applied it (`queue go`'s verifier does not accept). Dependencies read the workbench task's `status`.

So **nothing downstream becomes ready inside a single `queue go`**. Accept is a human action and GO does not wait for it. GO runs what is ready, leaves the rest `waiting` with a reason, and says so. You accept, then run GO again.

| State | Meaning |
|---|---|
| `queued` | no dependencies, or all of them accepted. In scope for the next GO |
| `waiting` | a dependency is not accepted yet. Accepting it frees this at the next GO |
| `blocked` | a dependency was discarded, failed, or does not exist, or there is a cycle. `queue list` gives the reason |

`waiting` and `blocked` are **persisted statuses, not a filter**, for two reasons: they have to survive a restart, and a detached worker spins until `queued` is empty — leaving a dependency-blocked item in `queued` would have **the worker busy-looping several times a second**.

- **Refused outright** (nothing is saved): a dependency on an id that does not exist, a self-reference, an undefined `dependency_policy`. The CLI cannot create a cycle — ids increase monotonically and a new item can only reference existing ones, so every edge points backwards — but a cycle in a hand-edited `.rig/queue.json` is detected and the item is `blocked`.
- **`--depends-on` is local-backend only.** GitHub and GitLab hold state in issue labels, which cannot carry a list of edges. Dropping it silently would run the item immediately as though it had no dependency, so it is **refused with an error**.
- **There is exactly one policy, `accepted`.** Turning edge conditions into a vocabulary makes a DAG language, and that is a non-goal for rig. An accept that crossed a failed gate with `--force` **does satisfy** the dependency, but — as on the receipt — `forced` and `gate_status` are recorded alongside rather than hidden.
- GO's exit code still means "did this batch's items succeed". Held items are **not this batch's items** — they are work that never properly started — so they do not count as failures.
- **`queue retry` breaks the `task_id` link.** A retry declares that this item will produce **a different** output, so keeping the old link would release downstream items against an output that is being replaced. On top of that, an edge is only read **while the dependency item is `done`**: a recorded id answers what was produced, but only the item's own status answers whether it is still producing it.
- **An item is claimed exactly once.** GO used to write `running` unconditionally at dispatch, so two concurrent `queue go` processes could run the same item twice — a property that predates #427. Dependencies make the damage worse: two runs create two workbench tasks, only one of which is linked, so downstream items are released against an output nobody kept. It is a compare-and-set now. Behaviour when GO dies mid-run is unchanged: only what was claimed stays `running`.

Mission Control can fetch `rig.queue-dependencies/v1` (nodes and edges, carrying no colours, coordinates, or classes) from `durable_snapshot`.

## Running several tasks at once without opening more terminals

```
/rig:queue add "fix the bug on the login screen"
/rig:queue add "add search to the inventory list"
/rig:queue go --provider rig --max-parallel 3   # three dispatched in parallel, each in its own worktree

/rig:go board       # where every task has got to, in one command
/rig:go diff <id>   # check one diff, then /rig:go accept <id> to land it
```

The problem of opening several terminals and losing track of which was doing what goes away because `/rig:go board` is the single source of truth.

## Backends (where the queue lives)

| backend | What it is | How state is held |
|---|---|---|
| `local` (the default) | `<repo>/.rig/queue.json` | a status in the JSON |
| `github` | GitHub Issues (the `gh` CLI) | labels `rig-queue` → `rig-running` → `rig-done`, results in comments, closed when done |
| `gitlab` | GitLab Issues (the `glab` CLI) | the same |

`--backend github --repo owner/repo` connects it to issues, which makes the queue **a backlog the team shares and that persists**; rig pulls from it, runs, and writes results back. It needs an authenticated `gh` or `glab` (a missing CLI produces an error, not a crash).

> **Concurrent updates on `local` (#360)**: updates to `queue.json` are **serialised by flock across processes and a threading.Lock across `queue go`'s threads**, and written atomically through a temporary file and `os.replace`. `queue go` writes statuses in parallel at the default `--max-parallel 3`, and without this, updates are lost: "GO says DONE but `queue list` still shows running", and "an item that rolled back to `queued` runs twice at the next GO". When a status cannot be recorded it prints `[WARN] #<id>: could not record status ...` rather than **dropping it silently**. When `queue.json` is corrupt and unreadable it **stops with an error instead of recreating it empty** — writing back an empty file is the loss of the backlog itself. `github` and `gitlab` hold state in issue labels and are unaffected.

## How it joins the other flows

- `/rig:brainstorm` → `/rig:tasks` splits the work, each task goes in with **queue add**, then `queue go` runs the lot.
- Stacking up **work that finishes** is a different axis from `/rig:goal` (converge on an outcome) and `/rig:loop` (repeat).

## Examples

```
/rig:queue add "add JWT refresh"
/rig:queue add "fix the N+1 in search"
/rig:queue go --provider rig --max-parallel 3
/rig:queue go --backend github --repo itoh-shun/rig    # pull from issues, run, write back
```

## run-continuity (SKILL.md §6)

While a RUN is active, restate this run-status header as a single line at the top of every turn. Do not drop it right after an interruption, a question, or tool output — the visibility is the evidence that the harness is driving:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
