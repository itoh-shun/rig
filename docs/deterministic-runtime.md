# Deterministic runtime (explicit opt-in)

`run --deterministic` executes a sequential, fail-closed lane. The legacy runner
remains the default. This lane requires Linux, `/usr/bin/bwrap`, usable user
namespaces and a successful write-isolation preflight. A failed preflight runs no
provider and exits nonzero; it never falls back to ordinary execution.

## A minimal recipe

Save this recipe outside the worktree being edited, for example `/home/me/strict-fix.md`:

```yaml
---
name: strict-fix
steps:
  - id: implement
    instruction: implement
    gate: acceptance-gate
    acceptance:
      - "The requested behavior is implemented and the declared checks cover it."
    checks: []
---
```

From the target repository, run:

```sh
rig-wb run /home/me/strict-fix.md --deterministic --isolate \
  --provider codex --verifier-provider codex \
  --goal 'Implement the requested behavior' \
  --check 'python3 -m pytest -p no:cacheprovider' \
  --out /home/me/rig-runs/fix-state.json
```

The state output must be outside the isolated worktree. `--check` is repeatable
and appends to the final step. Every step must have nonempty machine checks;
therefore an ordinary multi-step recipe cannot be made strict merely by adding
one final `--check`. Sequential `generate` steps are supported. DAG dependencies,
adaptive executors, human gates, model panels, automatic routing and unsupported
CLI options are refused. The initial lane supports `cmd`, `mock`, `codex` and
`claude`; `mock` exercises plumbing only, not substantive review quality.

Step policies, output contracts, material profiles, patterns, conditions,
auto-routing, actors/personas and per-step model overrides are unsupported and
refused when present. Manual-only recipes are also refused. Use a dedicated
minimal strict recipe rather than dropping obligations from a richer recipe.

A successful isolated run preserves its branch and worktree for inspection. It
does not merge, accept or remove them. Exit zero means DONE; other outcomes are
nonzero. Timeouts bound individual processes; costs are not currently measured.

## Bind an existing workbench task

To add this mandatory gate to an existing isolated workbench task, run from that
repository with `--deterministic-task TASK_ID` instead of `--isolate`. This option
also selects deterministic mode. It holds the workbench task lock throughout the
run and uses the task's registered worktree. Tasks without an isolated worktree,
already completed tasks and already bound tasks are refused.

The task marker and `deterministic-binding.json` sidecar are written before
initialization. If initialization fails, the task remains bound and acceptance
fails closed. `workbench accept`, including `--force`, requires both copies to
match the current run and the reciprocal task identity. It then requires current
DONE evidence in addition to all existing workbench acceptance conditions.
Deleting just one marker does not restore legacy acceptance.

## Resume and reject handling

```sh
rig-wb resume /home/me/rig-runs/fix-state.json
```

The saved strict contract and provider configuration drive resume. Manual `next`,
`check`, `verdict` and `approve` cannot modify strict runs. Unknown schemas,
changed contracts and stale evidence are refused rather than interpreted by the
legacy runner. Interrupted external operations without a completion receipt stop
instead of automatically repeating a possibly completed side effect.

The runner retains structured failed-check evidence and cumulative failure and
replan events. A diagnosis must name the actual failed checks and propose changes
and verification checks. Repeated failures require a changed plan; replanning
does not reset the cumulative limits. The initial runtime groups machine failures
conservatively at unit level: two failures since the last replan require REPLAN,
even when different checks failed. Six total failures, or a new failure after two
completed replans, escalate. Detailed failed-check IDs remain in diagnosis records.
AI review cannot override machine failure. A rejected or malformed AI review
stops at AWAIT_DECISION; it is not automatically classified as an implementation
defect. Shell exit 126/127 is BLOCKED (check could not execute). A final check
failure in a single-step run enters recovery; a multi-step final failure stops at
AWAIT_DECISION because the runner cannot safely infer the responsible unit.
All checks run again at final verification, and acceptance rechecks the current
subject against the evidence. The two upper-stage lifecycle modes are separate
from this runtime foundation and are not implemented by this flag.

## Trust and execution scope

The parent runner and host operator are trusted. Bubblewrap provides filesystem
write isolation: generators write only the selected workspace; checks, diagnosis,
planning and review run with the workspace read-only. Runner state, Git metadata
and workbench binding records are outside that writable scope. Checks which need
build/cache output in the source tree must redirect it to private `/tmp` or fail;
the runtime does not silently grant writes.

This is not confidentiality isolation. Provider subprocesses can read permitted
host files, and network-enabled remote providers need their credentials. Host
administrators and host IPC services are trusted. Evidence comparison and frozen
contracts do not authenticate a hostile operator who can rewrite all runner
metadata. Snapshots cover Git tracked and nonignored untracked files, not ignored
inputs. The environment digest currently covers platform/Python identity and the
frozen provider configuration, not complete installed dependencies or toolchain
binaries. The automated integration fixtures use local `cmd` providers; they do
not establish compatibility or review quality for live Codex/Claude services.
This lane does not prove requirements are correct or that a passing test suite is
complete. Those remain review and evaluation concerns.

Provider executables and directly named provider scripts must be outside the
writable workspace. Operator-configured inline or shell code and indirect imports
remain trusted inputs; argument inspection does not prove those programs safe.
Check command definitions are frozen, but test source files in the workspace can
change during implementation. Their correctness and any weakening of assertions
still require review. The runtime does not provide an immutable external test oracle.
