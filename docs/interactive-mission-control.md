# Interactive Mission Control

RIG Mission Control v2 adds a localhost-only interactive surface on top of the existing RIG Core.
The browser does not implement acceptance, governance, approval, queue, or provider rules itself.

## Start

Install the checkout (editable install is convenient during development):

```bash
python -m pip install -e .
```

From the project you want to operate:

```bash
rig-mission-control-live
```

The default endpoint is:

```text
http://127.0.0.1:8765/
```

It opens the browser automatically. To print the URL without opening it:

```bash
rig-mission-control-live --no-open
```

A different local port is fine:

```bash
rig-mission-control-live --port 9876
```

The server intentionally has no remote-bind option. If you need to operate a remote machine, use an authenticated tunnel and keep Mission Control bound to loopback.

The original snapshot generator remains available and read-only:

```bash
rig-mission-control
rig-mission-control --json
```

## Live UI

The live page polls RIG state every two seconds and shows:

- `Task → Isolate → Execute → Verify → Accept` as the stable Core contract;
- active task, gate, token, production-outcome and outcome-coverage metrics;
- all workbench tasks in the local `.rig/runs/` history;
- a selected task's workbench steps and acceptance criteria;
- the persistent AI queue and detached worker state;
- the tail of the worker log;
- command output when an action succeeds or RIG refuses it.

## Autonomous AI Run

The **Start AI Run** form is durable. It does not keep an HTTP request open while Claude/Codex/etc. work.

The path is:

```text
Browser
  ↓ POST /api/jobs
Mission Control
  ↓ short command
rig-wb queue add "<task>" --backend local
  ↓ persisted
.rig/queue.json
  ↓
detached mission worker
  ↓ canonical command
rig-wb queue go --backend local --provider ... --verifier-provider ...
  ↓
provider process → isolated RIG worktree → verifier → gate
```

The local queue is the task source of truth. Mission Control does not create a second scheduler or duplicate queue state.

### Browser/server lifetime is not worker lifetime

The worker is detached from the Mission Control HTTP server. You can:

1. press **Start AI Run**;
2. close the browser;
3. stop `rig-mission-control-live`;
4. reopen it later;
5. see the same `.rig/queue.json` item and worker/result state.

Worker metadata is only lifecycle information:

```text
.rig/mission-control/worker.json
.rig/mission-control/worker.log
```

`worker.json` records provider, verifier, PID, start/end state and exit status. It is not a replacement for the queue.

### Queue drain behavior

`queue go` snapshots the currently queued batch. A task may be added while that batch is still running. The detached worker therefore re-checks `.rig/queue.json` after every batch and runs another cycle until no `queued` items remain.

This means new work can be stacked while the worker is busy without being forgotten when the first batch completes.

### Provider consistency while a worker is active

The existing local queue does not store a provider per item. Therefore Mission Control refuses a silent provider switch while one worker is draining it.

Example:

```text
active worker: rig → codex, parallel=2
new request:   claude → codex, parallel=2
```

The new request is rejected **before it is persisted**. Otherwise it could be picked up by the active `rig` worker despite the user having selected `claude`.

Once that worker drains and exits, a new provider configuration can be started.

### Supported GUI providers

Mission Control exposes named providers already understood by RIG:

- `rig`
- `claude`
- `codex`
- `grok`
- `lmstudio`
- `ollama`
- `mock`

The arbitrary `cmd` provider is intentionally not exposed from the browser.

## Workbench operations

### Register an isolated task without starting a model

The **Register isolated task** form maps to:

```bash
rig-wb wb new "<task>" --type <task-type>
```

It creates the same isolated worktree as the CLI. This remains useful when execution will happen separately.

### View Diff

**View Diff** maps to:

```bash
rig-wb wb diff <task-id>
```

### Accept

**Accept** maps to:

```bash
rig-wb wb accept <task-id>
```

There is deliberately no GUI force-bypass path. The workbench re-checks the gate, worktree consistency and governance requirements at execution time; a green button in the browser is never evidence that the operation is allowed.

### Discard

**Discard** requires typing the exact task id before the browser sends the request, then maps to:

```bash
rig-wb wb discard <task-id> --yes
```

The exact confirmation is checked again by the server before `--yes` is added.

### Approve / Deny

The approval buttons map to:

```bash
rig-wb govern approve grant <task-id> --note "..."
rig-wb govern approve deny  <task-id> --note "..."
```

The browser cannot provide `--actor`. Identity is resolved by RIG's existing governance layer (`RIG_ACTOR`, `RIG_USER`, git identity) on the host where Mission Control is running.

### Production outcome

For accepted tasks the UI can record:

```bash
rig-wb wb record-outcome <task-id> --status ok
rig-wb wb record-outcome <task-id> --status incident --note "..."
```

This feeds the same production-evidence files already used by Mission Control.

## Security boundary

The live server is intentionally small and local:

1. It binds only to `127.0.0.1`.
2. Every process creates a random CSRF token; every POST must send it.
3. Cross-origin POSTs are rejected and the server emits no CORS allowlist.
4. Requests are JSON and capped at 64 KiB.
5. Task ids and queue ids are restricted before being used as selectors.
6. Commands are argv arrays executed with `shell=False`.
7. Browser payloads cannot choose a governance actor.
8. No GUI endpoint can request a force accept.
9. Discard requires exact task-id confirmation.
10. Arbitrary provider commands are not available in the GUI provider list.
11. A running shared queue worker cannot silently change provider configuration.
12. The canonical CLI result (including refusal text) is returned to the UI.

The architecture is therefore:

```text
Browser
  ↓ localhost + CSRF
Mission Control command gateway
  ↓ argv, no shell
RIG CLI / local queue
  ↓
Detached queue worker
  ↓
Workbench / Governance / Audit
  ↓
Git / .rig state
```

Mission Control is an operator console. RIG Core and its queue remain the authority.

## Intentionally not in this iteration

- force accept;
- waiver granting;
- arbitrary shell commands;
- remote network binding;
- browser-supplied actor impersonation;
- a second copy of gate/RBAC/approval logic;
- killing a running model process from the browser.

Cancellation needs explicit semantics for provider child processes and worktree state; it should not be added as a generic PID kill button.
