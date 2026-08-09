# Interactive Mission Control

RIG Mission Control v2 adds a localhost-only interactive surface on top of the existing RIG Core.
The browser does not implement acceptance, governance, or approval rules itself.

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

The server intentionally has no remote-bind option in v2. If you need to operate a remote machine, use an authenticated tunnel and keep Mission Control bound to loopback.

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
- command output when an action succeeds or RIG refuses it.

## Operations

### Register an isolated task

The **Register isolated task** form maps to:

```bash
rig-wb wb new "<task>" --type <task-type>
```

It registers the task and creates the same isolated worktree as the CLI. It is not a second task engine.

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

There is deliberately no GUI `--force` path. The workbench re-checks the gate, worktree consistency and governance requirements at execution time; a green button in the browser is never evidence that the operation is allowed.

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
5. Task ids are restricted before being used as filesystem selectors.
6. Commands are argv arrays executed with `shell=False`.
7. Browser payloads cannot choose a governance actor.
8. No GUI endpoint can produce `--force`.
9. Discard requires exact task-id confirmation.
10. The canonical CLI result (including refusal text) is returned to the UI.

The architecture is therefore:

```text
Browser
  ↓ localhost + CSRF
Mission Control command gateway
  ↓ argv, no shell
rig_workbench.cli
  ↓
WorkBench / Governance / Audit
  ↓
Git / .rig state
```

Mission Control is an operator console. RIG Core remains the authority.

## Intentionally not in v2

- force accept;
- waiver granting;
- arbitrary shell commands;
- remote network binding;
- browser-supplied actor impersonation;
- a second copy of gate/RBAC/approval logic;
- long-running model execution as an HTTP background job.

The last item matters: task registration is interactive now, but autonomous provider runs need a durable job model rather than hiding a long model process behind one browser request. That can be added as the next layer using RIG's persisted run state and queue instead of inventing an in-memory job system.
