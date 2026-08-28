# Orca as a rig runtime

Orca is a **runtime**, not a provider. That distinction is the whole design, so it comes first.

| | decides | examples |
|---|---|---|
| **Provider** | *who writes the code* | claude, codex, ollama, lmstudio |
| **Runtime** | *where the work lives* | native git worktrees, Orca-managed worktrees |

These are unrelated questions. Folding them together would make "run this on Codex" and "run
this in an Orca workspace" the same kind of choice, and then neither could be made without the
other. Nothing in `rig_workbench/workbench/runtime.py` mentions a provider, nothing in
`rig_workbench/orchestrate/providers.py` mentions a runtime, and the test suite checks that
structurally — by parsing both modules' ASTs — rather than by trusting this paragraph.

The division of labour:

```text
Orca   — UI, workspace presentation, diff review, terminals
Rig    — classify, compose, enforce policy, verify independently, gate, accept/discard, audit
Agents — claude / codex / … write and review the code
```

> Orca decides where the work is visible. Rig decides how the work is done and whether it is
> acceptable.

## The flow

```text
Orca
  ↓  open a repository, start Claude Code
Claude Code
  ↓  "Rig でこのタスクを実行"
Rig
  ↓  runtime backend
Orca CLI
  ↓  orca worktree create --json
Orca-managed worktree
  ↓
claude (generator) / codex (read-only verifier) / tests / rig acceptance gate
  ↓
you review the diff in Orca; rig stays authoritative for accept and discard
```

## Setup

Nothing to configure. Rig works exactly as before on a machine with no Orca, and that is the
first thing its tests check.

```console
rig-wb wb new "fix the login redirect"                  # runtime: auto
rig-wb wb new "fix the login redirect" --runtime orca   # explicit
rig-wb wb new "fix the login redirect" --runtime native # explicit
```

`--runtime` is available on `wb new` and `wb import`, and takes `auto` (default), `native`, or
`orca`.

### What `auto` actually does

`auto` uses Orca only when **both** of these hold, and prints why it fell back when they do not:

1. **An Orca session is exported into this environment** — `ORCA_WORKTREE_ID` or
   `ORCA_WORKSPACE_ID`. Detection reads variables and returns; it starts no subprocess and
   opens no file, so choosing the default never asks another tool whether it is installed.
2. **The Orca CLI reports a ready, reachable runtime** — `orca status --json` exits zero,
   returns a JSON object with `ok: true`, and that object's `result.runtime` is in state
   `ready` with `reachable: true`. Valid JSON is not enough; the runtime has to say it is up.

A binary on PATH, an exported session variable, a zero exit, parseable output, and a runtime
that reports itself ready are five different facts. Any one of them missing sends `auto` back to
native git worktrees, with the reason on stderr.

### Explicit `--runtime orca` never downgrades

```console
$ rig-wb wb new "…" --runtime orca
[ERROR] runtime 'orca' is not available here: Orca CLI executable 'orca' was not found on PATH.
Rig will not quietly fall back to another one — re-run with --runtime auto if that is what you want
```

A silent downgrade would run the task somewhere you did not ask for and did not check, which is
the failure that makes an opt-in runtime not worth having.

## Troubleshooting

| symptom | what it means |
|---|---|
| `no active Orca session was detected; falling back to native` | Neither `ORCA_WORKTREE_ID` nor `ORCA_WORKSPACE_ID` is exported. Rig is not running inside an Orca session. |
| `Orca CLI executable 'orca' was not found on PATH` | The session variables are set but the CLI is not reachable from this process. |
| `Orca CLI status did not return valid JSON` | The CLI answered, but not with structured output. A version mismatch is the usual cause. |
| `Orca CLI status JSON did not report a ready, reachable runtime` | The CLI answered with valid JSON that did not say `ok: true` with a runtime in state `ready` and `reachable: true`. Orca is installed but its runtime is not up. |
| `Orca CLI status did not respond: …` / `failed with exit N: …` | The probe could not be run, or exited non-zero. The CLI's own diagnostic is passed through verbatim rather than summarised. |
| `Orca worktree create returned no safe stable worktree id; Rig will not invent one from its path` | Creation reported no id. Rig refuses to manufacture one, because a fabricated identifier would send a later `remove` at the wrong thing. |
| `this task's worktree belongs to the 'orca' runtime, which is not usable here` | You are discarding a task created under Orca on a machine where Orca is gone. See below. |

### Discarding a task when Orca is gone

Rig will not dispose of an Orca-managed worktree with a different runtime — that would delete a
directory rig no longer owns and report success. But a task nobody can discard is its own
failure, so there is an explicit way out:

```console
rig-wb wb discard <task-id> --yes --local-cleanup
```

That removes the checkout with git at your explicit request and records `cleanup_note` on the
task, so the audit shows the worktree was disposed of by something other than its owner. The run
log under `.rig/runs/<task-id>/` survives either way.

## What rig persists, and what it does not claim

A task created under Orca records its runtime, path, branch, and the Orca worktree id:

```json
{
  "worktree": {
    "runtime": "orca",
    "path": "/abs/path/to/worktree",
    "branch": "rig/rig-20260828-...",
    "ref": {"orca_worktree_id": "…"}
  }
}
```

`ref` holds identifiers only that runtime understands, which is what lets a later `remove` go
back to whoever created the worktree instead of guessing from a path.

Rig does **not** claim that Orca's checkout is a sandbox, that setup hooks completed, or that a
provider process started. It creates and removes the checkout and reports its path; the provider
layer remains responsible for processes, and the acceptance gate remains responsible for whether
the result is acceptable. A successful status probe is not a promise that a later call will
succeed either — every CLI call is checked on its own.

## Orca and IntelliJ IDEA

Orca is the AI work cockpit, not a replacement for a full IDE. The two coexist on the same
repository and the same worktree:

| Orca | IntelliJ IDEA |
|---|---|
| watch parallel tasks and their agents | deep Java/Kotlin semantic analysis |
| review AI-written diffs | debugger |
| browse and edit code | advanced refactoring |
| terminals and session state | framework-specific tooling |

An Orca-managed worktree is an ordinary directory with an ordinary git checkout in it, so
opening it in IntelliJ needs nothing from rig.

## Testing without Orca

Every scenario in the integration contract is covered without Orca installed, because the CLI is
reached through one seam that tests substitute: runtime selection (`auto` / `native` / `orca`),
fallback, structured-output parsing, malformed and deceptive CLI responses, creation, disposal,
disposal failure, resume against present and stale metadata, and the structural check that the
provider layer and the runtime layer do not reference each other.

See `tests/test_runtime_backend.py`, `tests/test_orca_runtime.py`,
`tests/test_orca_detection.py`, and `tests/test_orca_lifecycle.py`.
