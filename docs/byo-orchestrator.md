# Bring Your Own Orchestrator

RIG does not have to be the thing that produced a change in order to be the thing that
decides whether it is acceptable. `workbench.py import` registers a change someone else
made — another harness, a CI job, a colleague with a branch — as an ordinary workbench
task, and `workbench.py contract` gives the caller a machine answer it can branch on.

```text
external orchestrator / CI / agent harness
        │  produces an immutable commit
        ▼
      RIG import ──► isolation · deterministic sensors · acceptance gate · governance
        │
        ▼
      RIG contract ──► acceptable / not-acceptable / pending / execution-error
```

## Why the import creates an ordinary task

The task branch is created **at the imported commit**, not at the base. From that point
`base..branch` is the external change, and `diff`, every sensor, `gate`, `govern`,
`accept`, the signed provenance record and the Assurance Receipt operate unchanged —
because there is nothing for them to tell apart.

That is the whole mechanism behind "an imported task cannot skip verification". There is
no second accept path to keep honest, and no per-producer branch anywhere in the gate.
`tests/test_byoo_contract.py` reads the accept, gate and governance sources and fails if
any of them so much as mentions the producer.

## Importing a change

```bash
python3 scripts/workbench.py import \
  --head 9f840a0e588cff65d0e9e0a942bae7d6e5e15b88 \
  --base main \
  --type feature \
  --producer some-orchestrator \
  --producer-runtime pi \
  --producer-run-id run-4711 \
  --producer-url https://ci.example.com/runs/4711 \
  --producer-claim tests=passed
```

`--head` accepts a branch or tag too. It is recorded as the movable name it is, and the
commit it resolved to is pinned — see *Freshness* below.

### The producer's own verdict is recorded, never applied

`--producer-claim tests=passed` lands in the task record and in the receipt with
`gate_effect: "none"` written into the claim itself. There is no code path from a claim
to `acceptance.json`. An orchestrator that grades its own work has told RIG something
worth keeping next to the verdict, not something that changes it.

### The diff summary

`accept` treats a missing diff summary as a structural precondition — not overridable
even with `--force` — because a change nobody described is a change nobody read. A
headless producer writes none, so `import` derives one from the imported commit messages
and `git diff --stat`, and labels it:

> **No reviewer wrote this**: it is the producer's own account of its work, restated.

Pass `--summary <file>` to supply an authored one; the receipt records which of the two
it got.

## Reading the verdict

```bash
python3 scripts/workbench.py contract <task-id> --json
```

```json
{
  "schema": "rig.assurance-contract/v1",
  "status": "acceptable",
  "task_id": "rig-20260818-124741-byoo-demo",
  "final_status": "acceptable",
  "verified_head": "9f840a0e588cff65d0e9e0a942bae7d6e5e15b88",
  "verified_head_immutable": true,
  "target_moved": {"applicable": true, "moved": false, "…": "…"},
  "producer": "some-orchestrator",
  "gate_status": "passed",
  "receipt": ".rig/runs/rig-20260818-124741-byoo-demo/assurance.json"
}
```

| status | exit | meaning |
|---|---|---|
| `acceptable` | 0 | RIG's gate cleared this change |
| `not-acceptable` | 1 | RIG looked and this did not clear |
| `execution-error` | 2 | RIG could not answer; the change has not been judged |
| `pending` | 3 | not decided yet — ask again later |

Four codes rather than three, because folding `pending` into either neighbour is a
specific and costly mistake: into `not-acceptable` and a poller reads "still running" as
"refused"; into `acceptable` and it merges something no gate has ruled on.

`execution-error` has its own code for the same reason. `die` exits 1 for a bad task id,
corrupt run state and an unmet gate alike, so a caller reading exit 1 from any other
subcommand cannot tell a refusal from an outage. `contract` never calls `die`.

A change a human accepted over a failed gate reports `not-acceptable`, with
`final_status: "accepted-over-failed-gate"`. The change was applied; RIG did not vouch
for it, and saying `acceptable` would record an assurance nobody gave.

## Freshness

The commit RIG verified is pinned at import, and two refs can drift away from it. Both
are checked, and `target_moved.checks` reports them separately:

| check | drifts when | matters because |
|---|---|---|
| `producer-ref` | the import named a branch or tag and the producing side kept working | the name the caller asked about no longer means what RIG looked at |
| `task-branch` | someone committed into (or rebased) the task worktree after the import | `accept` squash-merges the branch, so what lands is not what the receipt describes |

Either one makes `contract` report `not-acceptable` and `receipt --verify` report the
receipt as stale, naming both SHAs.

A digest cannot detect this — a ref moving rewrites no file — so it is checked by
re-resolving the refs rather than by comparing content. Handing RIG a commit SHA instead
of a branch removes the first failure mode entirely, and that check then reports
`applicable: false`; the second still runs, because `moved: false` has to mean the checks
ran and agreed rather than that they were skipped.

A branch that no longer resolves reports `applicable: false` with the reason rather than
`moved: false` — `accept` and `discard` remove the task branch, and an absence dressed as
a measurement is exactly what the Assurance Receipt refuses to produce.

Note that a legitimate rebase of the task branch also counts as drift. RIG is not judging
whether the change is still equivalent — it is reporting that the commit named in the
receipt is not the commit that will be applied. Re-import, or accept the drift knowingly.

## Accept still needs a clean main working tree

`accept` refuses to run while the main working tree has uncommitted changes, so that a
conflicting squash merge can be rolled back with `git reset --hard`. The first `import` in
a repository also appends `.rig/` to `.gitignore`, which is itself an uncommitted change —
so a fully headless flow should commit (or stash) before calling `accept`.

## What is deliberately not here

- RIG does not run the producer's workflow, interpret its DSL, or schedule anything.
- RIG does not verify *who* produced a change. `--producer` and friends are declarations,
  and the receipt reports them as declarations. Independence reads `declared-separate`,
  never `independent`.
- RIG's quality rules do not vary by caller. The same contract is what makes it reusable
  by the next one.
