---
name: adaptive-bugfix
description: Adaptive bugfix flow with deterministic risk routing and targeted review.
scope: shipped
autonomy: interactive
steps:
  - id: implement
    instruction: implement
    executor: generate
    personas: [implementer]
    policies: [risk-based-testing, ci-cost]
  - id: assess
    instruction: adaptive-assess
    executor: risk-assess
    pattern: serial
  - id: targeted-review
    instruction: parallel-review
    executor: targeted-review
    gate: review-gate
    pattern: serial
    max_retries: 1
    acceptance:
      - "task_intent_satisfied — 依頼の意図が満たされている"
      - "no_unrelated_diff — 依頼と無関係な差分が含まれていない"
      - "fix_is_minimal — 修正が最小限である"
      - "no_unrelated_refactor — 依頼にない広範なリファクタが混ざっていない"
  - id: acceptance
    instruction: acceptance-check
    executor: checks-only
    checks:
      - "git diff --check"
    max_retries: 1
---

# adaptive-bugfix

The two-call normal behavior uses one implementer and one deterministic,
risk-selected reviewer.

The three-call repair budget permits one informed repair only when a blocking
review finding provides both a reproduction and a mechanical command that
exactly matches a command supplied through the CLI `--check` allowlist. The
recipe's own acceptance checks cannot authorize semantic repair. A finding
that names a missing regression test for a specific input/behavior may still
cite an allowlisted check as its mechanical command: the repair pass may add
exactly one narrowly-scoped test pinning that input/behavior (never a broader
test change), and re-running the same allowlisted command then exercises it.

The four-call multi-domain budget permits a second independent reviewer when
the deterministic assessment finds two high-risk domains. Risk assessment and
mechanical checks do not consume provider invocations.

Malformed review output, an unverifiable blocking finding, a non-allowlisted
reviewer command, a failed post-repair check, or an exhausted invocation budget
causes a safe stop. Reviewer-authored commands are never executed unless they
exactly match the CLI `--check` allowlist.

## What this flow judges, and what accepting the task still requires

**Two different lists, and only one of them is the requirement.**

* The **task's gate** — `build_acceptance()` seeds `acceptance.json` from the
  `standard` + `bugfix` presets (fifteen criteria) without reading any recipe.
  That set is what `rig-wb wb accept` refuses on. No recipe can add to it or
  take anything out of it.
* This recipe's `acceptance:` list — a **work list**. It names only the criteria
  *a step of this flow produces evidence for*. Answering it exactly is expected
  to leave the rest of the gate `pending`, and `wb accept` will then say so.

`targeted-review` carries the list because it is the step that produces a
verdict here: a risk-selected reviewer reading the actual diff. Four criteria
are what a diff and a reviewer can settle:

| criterion | what settles it |
|---|---|
| `task_intent_satisfied` | the reviewer, reading the diff against the task |
| `no_unrelated_diff` | the diff |
| `fix_is_minimal` | the diff — and it is this recipe's stated design |
| `no_unrelated_refactor` | the diff |

The `acceptance` step declares no gate and no list. Its executor is
`checks-only`, which runs `git diff --check` and returns without ever calling a
provider — it cannot produce a verdict, so a gate on it would be a stamp with
nothing behind it. That is the shape `fast-bugfix.implement`,
`fast-bugfix.test`, `max-bugfix.implement` and `max-bugfix.test` already use,
and declaring a runtime gate on a verdict-less executor is now rejected before
the runner starts.

**Why the four sensor-backed criteria are not declared here.** `no_secret_leak`,
`no_destructive_operation`, `no_injection_markers` and `no_gate_tampering` are
settled by deterministic sensors that report through `rig-wb wb gate` — and
`orchestrate run`, the only runner this flow has, never calls `wb gate`. Under
`orchestrate run` nothing in this flow produces their evidence, so under the
rule above they do not belong on a step's list. They remain binding on the
task's gate; a person or the interactive acceptance step still has to answer
them before `wb accept` will pass.

Everything else in `standard` + `bugfix` names evidence no step here produces.
`diff_summary_written` and `risk_summary_written` want prose from a step that
writes it; `no_type_errors_or_explained` wants a type check; `bug_cause_identified`
wants the reproduce and plan steps `bugfix` has and this does not — which is why
`fast-bugfix`, also lacking them, leaves it out too. `tests_pass_or_explained`,
`regression_test_added_or_explained` and `existing_behavior_preserved` all want a
test run: `bugfix` and `fast-bugfix` each have a `test` step, and this flow has
none, so its whole budget of two to four calls can complete without a test ever
running.

A criterion nothing in the flow can satisfy does not make the gate stricter. It
makes it a rubber stamp or a deadlock, and either way the step stops meaning what
it says. Four criteria that the evidence reaches beat thirteen that it does not.
Every id comes from `rig-wb wb gates` — an id spelled only in a recipe is one no
sensor measures and no other recipe shares, and `rig-wb validate` now rejects it.

**Reaching the last step is not acceptance.** `orchestrate run` finishing `DONE`
means every step of this flow passed its own gate. It writes no task record and
calls no `wb gate`, so the fifteen-criterion gate is untouched by it. Acceptance
is `rig-wb wb accept` on a workbench task, and it refuses while any criterion is
`pending` or `failed`.
