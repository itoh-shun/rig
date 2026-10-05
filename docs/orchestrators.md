# Agent execution orchestrators

Rig can choose how to run an agent independently of its workspace and provider.

| Axis | Decides | Values and CLI |
|---|---|---|
| **Orchestrator** | *how agents run* | `native`, experimental `t3`; `rig-wb run --orchestrator auto\|native\|t3` |
| **Runtime** | *where the work lives* | native or Orca-managed worktrees; `rig-wb wb new --runtime auto\|native\|orca` |
| **Provider** | *who generates or verifies* | Claude, Codex and the existing provider adapters |

Rig keeps the workflow, retries, independent verification, machine checks, human gates and
acceptance decisions. An orchestrator returning a completed agent is only an execution
result. Rig still decides whether that result passes the step's gate.

See [Orca as a rig runtime](orca.md) for workspace ownership and accept/discard rules.

## Native only

Native is built in and needs no additional dependency. It wraps the existing provider
dispatcher, retaining its commands, HTTP providers, mock runs, timeouts, output, session
reuse and progress reporting. The adapter being available does not mean every provider is
installed or authenticated.

```console
rig-wb run feature --provider mock --verifier-provider mock --orchestrator native
```

With no T3 connection settings, the default `auto` selects Native and prints its reason to
stderr. Explicit `native` skips T3 detection and probing even when T3 is configured. Neither
path imports the T3 adapter or MCP SDK. Existing callers that use the runner directly
without an injected orchestrator also use Native.

## Selection and fallback

Selection follows **CLI > trusted manifest > default auto**. The CLI changes `preferred`
only; `fallback` comes from the manifest and defaults to `native`.

```yaml
orchestrator:
  preferred: auto
  fallback: native
```

| Request | T3 unavailable or incompatible | T3 compatible |
|---|---|---|
| `native` | Native; no T3 probe | Native; no T3 probe |
| `auto`, `fallback: native` | Native, with a reason | T3 |
| `auto`, `fallback: none` | Stop before agent launch, exit 2 | T3 |
| `t3`, either fallback value | Stop before agent launch, exit 2 | T3 |

Explicit `t3` never downgrades to Native. With `auto` and `fallback: native`, an execution
can fall back once for a particular call only when it is certain that T3 did not start
that call. Lost launch responses and failures after launch stop the run instead.

An invalid trusted manifest stops `run` with exit 2, including when the CLI requests
Native. Unknown orchestrator subkeys are errors; `token`, `headers`, `tools` and `args`
cannot be supplied through the manifest. Omitted keys use defaults; YAML `null` is not
the string `none`.

## Connecting T3

T3 is experimental. **Its contract is verified with a fake client; execution against a
real T3 server is unverified.** There is currently no verified server build or revision.
The production MCP client has `contract_verified=False`. Even after connecting and
accepting the fake fixture's schemas, the probe returns `unverified_contract`, so T3
cannot be selected in this release. `auto` with the default `fallback: native` returns
to Native; explicit `t3` or `fallback: none` stops before launch with exit 2. Installing
the SDK or setting an endpoint does not enable an unverified contract.

The optional streamable-HTTP transport uses the existing MCP extra:

```console
pip install 'rig-workbench[mcp]'
```

Connection values are resolved as follows:

| Value | Source |
|---|---|
| Endpoint | `RIG_T3_MCP_URL`, then trusted manifest `orchestrator.t3.url` |
| Bearer token | `RIG_T3_MCP_TOKEN` only |
| Project | `RIG_T3_PROJECT_ID`, then trusted manifest `orchestrator.t3.project_id` |

Obtain these values from the T3 server operator. When the project is omitted, the
adapter requires an unambiguous project from the server's contract response. The endpoint
must use HTTPS or loopback HTTP. A manifest endpoint must also be loopback; remote
manifest URLs return `manifest_endpoint_not_loopback` without sending the token. Set
`RIG_T3_MCP_URL` explicitly to authorize a remote endpoint. Credentials in the URL, query strings and fragments are
rejected.

For example, a trusted `.claude/rig.md` frontmatter can contain public connection settings:

```yaml
orchestrator:
  preferred: auto
  fallback: native
  t3:
    url: http://127.0.0.1:3773/mcp/external-control
    project_id: project-123
```

The endpoint and project above are examples, not a verified T3 deployment. Supply the
token through the process environment using your credential manager; do not put it in the
manifest or run state. Rig rereads credentials on reconnect. Tokens are excluded from
state, telemetry, diagnostics and provider subprocess environments.

The sole tool/schema binding table is
[`rig_workbench/orchestrate/orchestrators/t3_contract.py`](../rig_workbench/orchestrate/orchestrators/t3_contract.py).
It describes the `rig-t3-v1` fake contract, including pinned thread/run identities and
complete result pages. It is not a claim that a current T3 release implements those tools.
Required tools with incompatible or missing schemas fail the probe. The read-only probe
has a five-second overall deadline and does not launch, configure or interrupt threads.

## Capabilities and limits

| Backend | Capabilities |
|---|---|
| Native | `agent.run`, `agent.parallel`; handles are local to the process |
| T3 with a verified compatible contract | `agent.run`, `agent.cancel`, `thread.durable`, `thread.resume` |

T3's `agent.parallel` is always false in v0.1. Its calls are serialized while Rig retains
DAG dependencies and gate order. Sequential steps, DAG steps and verifier/judge panels
use the same agent execution boundary.

Strict and secure execution remain Native only. An `auto` request with Native fallback
uses Native with a reason; explicit T3 or `auto` with `fallback: none` stops before
execution. The existing strict receipts and secure executable restrictions still apply.

T3 must confirm the same checkout, requested cwd, provider/model, role and constraints
before launching. Verifiers need their provider's existing read-only confinement. Unknown
identity, ambiguous provider instances and automatic provider/model substitution are
refused. Each call gets a fresh thread: a verifier does not inherit the generator's
conversation. Different thread IDs alone do not establish reviewer independence;
Rig's existing provider alias and model rules still decide it.

## `rig-wb doctor`

```console
rig-wb doctor
rig-wb doctor --json
rig-wb doctor --orchestrator native
```

Doctor shares the new-run selection logic and reports Native, T3 and the Orca workspace
runtime. `Active orchestrator` means the selection for a **new** run under this
configuration; it does not observe a running agent. Doctor has no provider or recipe
input, so it cannot establish individual role/cwd compatibility.

`MCP connected` describes a connection and the reported API contract, not successful
agent execution or Rig acceptance. Explicit Native reports `not probed (native requested)`.
Orca session detection and CLI reachability are separate axes: environment variables can
show a session, while `CLI not probed` leaves reachability unobserved. They do not prove
Orca is available.

Doctor is read-only and always returns exit 0, including invalid arguments or configuration,
missing SDK, failed authentication, timeout and unavailable backends. It records these in
the report instead of acting as a gate. It does not run providers, mutate threads, create
worktrees, update trust records, or write state, telemetry or diagnostic caches. Only
manifests whose content matches a saved trust record are read; untrusted or changed
manifests are ignored with a reason, without prompting or recording new consent.

`--json` writes one JSON object to stdout. Schema version 1 contains `execution_backends`
(`native` and `t3`), `workspace_runtimes.orca`, `selection`, `errors` and `reasons`.
Availability can be `true`, `false` or `null` (not probed). `selection` includes
`preferred`, `selected_by`, `active`, `fallback` and `effective_fallback`; `active` is
`null` when selection cannot succeed. Explicit T3 reports its fallback as disabled.

## Failure and recovery

| Situation | What Rig does |
|---|---|
| Unconfigured or failed compatibility probe | Follow the selection table; no agent starts through the probe |
| Confirmed failure before launch | Only `auto` with Native fallback retries that same call once on Native |
| Launch response lost or incomplete | Save any known IDs and stop `BLOCKED`; missing IDs do not prove nothing started |
| Wait/read failure after launch | Preserve IDs and stop `BLOCKED`, with no Native retry |
| Timeout or interruption | Request cancellation when supported; an unconfirmed stop remains unresolved |
| Agent nonzero return code, verifier FAIL or failed checks | Apply Rig's existing retry and gate rules, without transport fallback |

When execution becomes uncertain, Rig stops new T3 launches and preserves the worktree.
It does not run checks or advance gates while external execution is unresolved. T3
execution requires a persistent state path. A failed snapshot stops execution and fallback;
known external IDs are retained for diagnosis.

```console
python3 scripts/orchestrate.py resume path/to/run-state.json
```

The existing checkout entrypoint above resumes an orchestrate state. Resume reconnects
the **recorded** orchestrator. It does not reselect based on current
`auto`, and `resume --orchestrator ...` is refused with exit 2. Changing the endpoint or
project also stops reconnect; thread IDs must not be searched on another server.
Credentials can be renewed for the same endpoint and project.

In v0.1 resume only reports reconnect state and known external IDs:

| Reconnect state | Meaning |
|---|---|
| `ready` | The recorded backend and agent were found; status is reported |
| `orchestrator_unavailable` | The recorded backend, SDK, endpoint or authentication is unavailable |
| `agent_missing` | The namespace was queried successfully but the saved agent was absent |
| `unknown` | Launch or result remains uncertain, including process-local Native fallback handles |

Any unresolved invocation in a T3 run stops resume with exit 2, even if its external agent
reports completion. Resume does not collect or apply that result, start a new thread,
retry through Native, or run workspace checks. With no unresolved invocation, the existing
verify-first resume checks the workspace and reports the next action. Automatic result
recovery is a follow-up; editing `orchestrator.name` in state is not a recovery procedure.

## State and telemetry

Top-level `orchestrator` metadata records the run's selection, capabilities, invocation
ledger and backend-specific `ref`. T3 endpoint, project, contract version and external
thread/run IDs live in `ref.t3`. A call that falls back records `native` in its invocation
and adds an `ORCHESTRATOR_FALLBACK` history event; the run's selected name remains `t3`.

Native-only runs keep empty invocation and reference mappings. Old state without an
`orchestrator` key means legacy Native and is not rewritten just by reading it. Malformed
metadata or unknown schema versions stop the run rather than being treated as legacy.
The `execution` policy dictionary is unchanged.

T3 snapshots use a process-local lock and atomic persistence (temporary write, fsync,
rename and directory fsync). There is no cross-process ownership lease in v0.1. A saved state
does not authorize two processes to operate the same run simultaneously.

Telemetry retains `"backend": "orchestrate"`. Valid new metadata adds `orchestrator` and
a fallback count when applicable; legacy records do not acquire invented measurements.
External IDs, endpoint and token are not copied into telemetry. Workbench `task.json` and
workspace runtime ownership are outside this change.

## Verification and follow-up

The fake client verifies selection, API bindings, state snapshots, fault handling,
serialization, identity checks and Rig's acceptance boundary. Fake SDK tests cover
session ownership, deadlines and cleanup. The standard selftest pins Native and mock
providers without probing T3.

**Fake client contract tests are verified; a real T3 server is unverified.** No live T3
smoke is included in v0.1. A future opt-in smoke must record server revision, SDK version,
public tool schemas, launch, collect, interrupt, reconnect, provider identity and role/cwd
constraints before a live build is declared supported. Orca/T3 combinations are also
unmeasured.

Cross-process leases, parallel T3 execution and batch cancellation, automatic resume
result application, lineage recovery, child delegation, provider switching, fork/merge,
scheduling, remote/mobile operation and trace evidence normalization remain follow-ups.
