# Deterministic gate foundation

The first stage of the [flow design](superpowers/specs/2026-09-18-deterministic-flow-design.md) provides two pure Python APIs for evaluating check evidence and choosing recovery actions. The [opt-in deterministic runtime](deterministic-runtime.md) connects these APIs to execution and task acceptance. [Explicit lifecycle modes](lifecycle-modes.md) add upstream waterfall/iterative gates. Legacy runs retain their behavior.

## Evaluate machine evidence

`rig_workbench.orchestrate.gate_evidence.evaluate_gate` requires an immutable verification context, a nonempty tuple of required checks, and a tuple of observations. It matches IDs and definition digests rather than counting records. A check passes only when its context matches, its status is `PASS`, its exit code is zero, and an artifact digest is present. Missing, duplicate, unknown, failed and stale observations reject the gate. The returned issues are sorted and deduplicated, so input order does not affect the decision.

```python
from rig_workbench.orchestrate.gate_evidence import (
    CheckEvidence, EvidenceContext, RequiredCheck, evaluate_gate,
)

# Illustrative digests only. Production callers must derive these from the
# actual snapshot, baseline, environment, check definition and output artifact.
context = EvidenceContext("run-1", "login", 1, "a" * 64, "b" * 64, "c" * 64)
check = RequiredCheck("login-tests", "d" * 64)
record = CheckEvidence(context, check.check_id, check.definition_digest,
                       "PASS", 0, "e" * 64)
assert evaluate_gate(context, (check,), (record,)).passed
assert not evaluate_gate(context, (check,), ()).passed
```

The typed constructors reject malformed fields with `ValueError`. Invalid contracts also raise `ValueError`; malformed evidence supplied to the evaluator produces a rejected decision. A well-formed `PASS` record with a nonzero or absent exit code is not successful. An AI review cannot override a failed check because this API takes no AI verdict.

**The caller is the trust boundary.** Digest equality does not authenticate evidence. These functions do not read artifacts, compute snapshots, detect changes during verification, enforce process permissions, or establish who wrote the records. A process allowed to forge all inputs could supply consistent false inputs. Runtime integration must collect and retain observations outside the implementation agent's write access and bind them to the actual target. Do not use this module alone as proof that a change is safe to accept.

## Choose recovery from recorded failures

`rig_workbench.orchestrate.recovery_policy.decide_recovery` consumes an ordered tuple of `FailureEvent` and `ReplanEvent` values. Sequence numbers start at one. It derives counters from the supplied history instead of accepting an agent's retry count. `stable_failure_id` hashes a versioned JSON triple of check ID, error class and target ID; no prose, timestamp or attempt number participates.

```python
from rig_workbench.orchestrate.recovery_policy import (
    FailureEvent, ReplanEvent, decide_recovery,
)

first = FailureEvent(1, "login-tests", "assertion", "login", "IMPLEMENTATION")
second = FailureEvent(2, "login-tests", "assertion", "login", "IMPLEMENTATION")
assert decide_recovery((first,)).action == "REPAIR"
rejected = decide_recovery((first, second))
assert rejected.action == "REPLAN"
replanned = ReplanEvent(3, rejected.failure_id)
third = FailureEvent(4, "login-tests", "assertion", "login", "IMPLEMENTATION")
after = decide_recovery((first, second, replanned, third))
assert after.action == "REPAIR"
assert after.total_failures == 3
assert after.replans == 1
```

The default repeat threshold is two occurrences since that failure's last replan. The lifetime failure and replan counts remain intact. Six failures in a unit, or any failure after two completed replans, escalates. These are initial configurable limits, not empirically optimized values. A threshold of one requests replanning immediately.

| Classification | Action before a cumulative limit is reached |
|---|---|
| `IMPLEMENTATION` | `REPAIR`; repeated failure requests `REPLAN` |
| `DESIGN` | `DESIGN` |
| `REQUIREMENT` | `REQUIREMENTS` |
| `ENVIRONMENT`, `CHECK_ERROR` | `BLOCKED` |
| `UNKNOWN` | `AWAIT_DECISION` |

Invalid history yields `AWAIT_DECISION` with reason `invalid_history`; invalid limits raise `ValueError`. A replan event must follow a `REPLAN` decision and name the immediately preceding failure. The policy refuses histories that skip that event or continue past a stopped decision. This version has no authorized resume event for environment recovery, human decisions or upstream revisions, so those continuations must not be represented as an ordinary next failure.

The classification and stable identity components must come from a runner-owned check registry or an authorized decision. The API does not infer root causes from prose or prove that a replan occurred. It also cannot detect truncated history or changed limits: the runtime must persist complete events and fixed policy outside agent write access. Time and cost budget enforcement is not implemented here.

## Verification and next boundary

The focused tests exercise exact evidence binding, false-pass attempts, permutation invariance, malformed values, failure identity, transition legality and lifetime bounds. Existing retry-feedback, development-loop and layering tests check that this foundation respects the current package boundaries. Tests run under Linux/WSL because the existing test bootstrap imports POSIX `fcntl`; no shim is added to imply Windows runtime support.

Stage 2 connects the APIs to the runner, snapshot collection, protected event persistence, actual repair/replan execution, resume, and `accept`. Stage 3 adds upstream requirements/design contracts and the two explicit lifecycle modes, including integration checks. Those integrations have their own runtime and CLI tests; unit tests for the pure APIs alone do not establish them. Stage 4 outcome measurement on fixed and held-out tasks remains separate and is not established by these tests.
