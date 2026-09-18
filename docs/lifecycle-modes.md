# Waterfall and iterative lifecycle gates

These modes are explicit additions to the strict sequential runner. Ordinary recipes,
`plan` (recipe expansion), and `approve` (legacy human gates) keep their meanings.
Use Linux with working bubblewrap, as described in [the strict runtime contract](deterministic-runtime.md).
This is not a full Scrum system: sprint scheduling, velocity and ceremonies are outside the contract.

## Start from a proposal

From the project checkout, with Rig installed in the current Python environment:

```sh
python3 -m rig_workbench.orchestrate.cli lifecycle template --mode waterfall --unit login --unit search > plan.json
# Edit plan.json before approval; the template deliberately contains incomplete drafts.
python3 -m rig_workbench.orchestrate.cli lifecycle init plan.json --out /home/me/rig-runs/run.json --isolate --provider cmd --verifier-provider cmd --provider-cmd '/usr/bin/python3 /home/me/trusted-provider.py'
python3 -m rig_workbench.orchestrate.cli lifecycle status /home/me/rig-runs/run.json --json
```

Replace paths with your own. The output state must be outside the writable worktree;
its storage and the configured provider must satisfy the strict runtime contract.
The `cmd` program is operator-controlled and uses the strict JSON request protocol;
it is not an arbitrary conversational shell command. `codex`, `claude` and `mock`
are also provider choices. Live service compatibility must be checked in your environment.
Initialization freezes configuration and performs isolation checks, but calls no provider.
Use `--deterministic-task TASK_ID` instead of `--isolate` to bind an existing isolated task.

The plan contains `schema_version`, `mode`, `objective`, `shared`, `units`, and
`integration_checks`. Shared fields are `constraints`, `interfaces`, `open_questions`.
Each unit has an `id`, `depends_on`, `requirements`, `design`, `checks`, and
`open_questions`. A requirement has `id`, `text`, and acceptance entries with `id`,
`text`, `check_ids`. Design has `summary` and `requirement_ids`. Checks have `id`
and `command`. IDs and references are validated by code; an empty draft can be saved
but cannot be treated as ready work. A shell check's existence does not prove that
it tests the requirement well: reviewing the requirements and check definitions
remains an operator responsibility.
Unit IDs `shared` and `integration` are reserved; colons are disallowed in unit IDs
to keep machine-evidence namespaces distinct.

Waterfall waits until all units have complete requirements, design, checks and
required decisions. Iterative permits an incomplete independent later unit, while
the selected unit, its prerequisites and the shared contract must be ready.
Both require integration verification after unit verification.
Execution remains sequential in the declared unit order: resume considers the first
unfinished unit and does not skip it to run a later ready unit. Put prerequisites
before their dependents. The per-unit readiness display is separate from permission
to resume a stopped or interrupted run; status names those cases for inspection.

## Make a decision about exactly the displayed version

Status prints the revision, whole-plan digest and separate stage digests, together
with machine-readable readiness reasons. Review the proposal before recording a decision:
Each stage also shows its latest decision and ID, whether its digest still matches,
and the operator's reason as a safe single line capped at 320 characters. The full
reason remains in the decision record; raw requirements and provider output stay out
of the status projection.

```sh
python3 -m rig_workbench.orchestrate.cli lifecycle decide /home/me/rig-runs/run.json --scope shared --stage shared --decision approve --revision 1 --digest SHARED_STAGE_DIGEST --actor operator --reason 'Reviewed shared constraints and interfaces'
python3 -m rig_workbench.orchestrate.cli lifecycle decide /home/me/rig-runs/run.json --scope login --stage requirements --decision approve --revision 1 --digest LOGIN_REQUIREMENTS_DIGEST --actor operator --reason 'Reviewed requirements and acceptance checks'
python3 -m rig_workbench.orchestrate.cli lifecycle decide /home/me/rig-runs/run.json --scope login --stage design --decision approve --revision 1 --digest LOGIN_DESIGN_DIGEST --actor operator --reason 'Reviewed design and requirement coverage'
python3 -m rig_workbench.orchestrate.cli resume /home/me/rig-runs/run.json --progress
```

Replace every digest placeholder with the matching value from status; approval of
one stage does not approve another. Repeat unit decisions for other units when ready.
`--decision reject` records rejection with the same explicit binding. Missing or
stale decisions cannot be repaired by a model's PASS verdict. These records identify
the trusted local operator; `--actor` is an audit label, not authenticated identity.
The CLI requires the current revision to reject stale requests. Stored decision
records bind content digests and ordered IDs; this initial schema does not repeat
the revision on each decision record.

In conversation, ask Rig to prepare and explain the proposal, show its revision and
scope, and wait for your decision. A response such as “OK” applies only to the concrete
proposal just shown. The agent must not invent approval or claim the template is approved.
After recording your decision, use the code's status result to decide whether to resume.

## Change the plan without erasing history

```sh
python3 -m rig_workbench.orchestrate.cli lifecycle revise /home/me/rig-runs/run.json --plan changed-plan.json --revision 1 --digest CURRENT_WHOLE_PLAN_DIGEST --actor operator --reason 'Change shared interface and recheck dependent work'
python3 -m rig_workbench.orchestrate.cli status /home/me/rig-runs/run.json --json
```

The revision and digest name the current stored plan, not the proposed replacement.
Unit IDs and order are fixed at initialization in this version. Revise their contracts
and dependency references in place; adding, removing or reordering units requires a
separate explicitly initialized lifecycle and does not transfer old approval or evidence.
A new revision keeps change and failure history, invalidates affected approvals and
evidence, and returns to upstream review. Shared-contract changes invalidate dependent
work. A mode change is a plan revision, never an implicit runner choice. Inspect status,
review the new stage digests, record decisions, then resume. Do not edit run-state JSON
to unlock execution or use legacy `approve`, `next` or `verdict` on a lifecycle run.
An upstream edit does not count as completion of a pending recovery REPLAN: the
runtime must still execute and validate that recovery operation before repair continues.
For AWAIT_DECISION, inspect the rejection and explicitly record an operator decision
with `decide` against the current plan, or submit `revise`, before revalidation.
An AI PASS does not unlock this wait. Repeated manual decisions are not subject to
a guaranteed total time or monetary budget in this version. BLOCKED, ESCALATE and
uncertain interrupted operations require inspection and cannot be cleared by these
upstream edits.

Status reports a saved snapshot: process liveness remains unknown and evidence is not
revalidated by reading it. `--progress` observes real phases and waiting; it does not
create gate evidence or promise percent completion. A paused upstream gate is incomplete,
not successful release completion. Unit PASS does not replace the final integration gate.

The CLI fixture tests establish transition and evidence behavior, not the quality of
AI requirements/design or the experience of a live Claude Code session.
