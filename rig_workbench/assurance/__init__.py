"""The assurance pillar: what an external orchestrator is handed about a change rig
already judged.

Eighteen `rig-wb wb` verbs live here — receipt, contract, import, intent,
intent-derive, assurance-target, assurance-derive, synthesise, dev-loop, route-team,
budget-plan, provenance, expected-outcome, effectiveness, knowledge-candidate,
change-graph, anomaly-trigger and compose-options — and every one of them obeys the
single rule `skills/engine/BRICKS.md` states for this family: **it judges nothing; it
copies from the records of what was judged.** A receipt is a projection of the gate,
provenance and approvals already written down, not a fresh verdict on them; an intent
contract's *declared* requirements raise a floor and its inferred ones do not; an
observation handed in from production cannot declare its own baseline; `unobservable`
is never folded into `unmet`, because "not measured" and "measured and short" are
different facts. What the caller declares and what rig verified stay in separate
columns all the way out to the JSON. The pillar therefore reads run state and writes
answers, and the floors, constraints and axis maps it checks against are always built
by the caller rather than read out of the thing under test.

Split out of `rig_workbench/workbench/` by design brief §11 T11 so that the workbench
is the task lifecycle again.

Each of the nineteen `rig_workbench/workbench/<name>.py` files is now a re-export shim,
kept for one major and removed in 4.0.0, and each raises a `DeprecationWarning` at import
naming the new module and that version. One rule decides what a shim lists, written here
so it can be checked rather than eyeballed: every name the moved module exposes that is
neither a dunder nor an imported module object. That is the whole of what an old path
could ever hand back — privates and re-exported constants included — so "every historical
import still resolves" is a claim about all 602 names rather than about the public ones.
`tests/test_assurance_shims.py` re-derives the rule against the live modules and fails if
a shim drifts from it.

Substituting a name is the one thing a re-export cannot carry, exactly as
`orchestrate/providers.py` records for its own bridge. The functions that read these
names resolve them in `rig_workbench.assurance.<name>`'s globals, so
`monkeypatch.setattr("rig_workbench.workbench.assurance.build_receipt", …)` patches the
shim's copy and nothing calls it. Patch the new path. Nothing in this repository reaches
the bridge — `rig_workbench/`, `scripts/`, `tests/` and `benchmarks/` were all moved to
the new path in the same commit — so deleting the shims can never be what breaks a
release, and they carry no effect sites for the ratchet in
`tests/test_architecture_inventory.py` to count twice.
"""
