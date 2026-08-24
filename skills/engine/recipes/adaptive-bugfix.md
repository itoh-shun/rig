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
  - id: acceptance
    instruction: acceptance-check
    executor: checks-only
    gate: acceptance-gate
    checks:
      - "git diff --check"
    max_retries: 1
    acceptance:
      - "task_intent_satisfied — 依頼の意図が満たされている"
      - "no_unrelated_diff — 依頼と無関係な差分が含まれていない"
      - "fix_is_minimal — 修正が最小限である"
      - "no_unrelated_refactor — 依頼にない広範なリファクタが混ざっていない"
      - "no_secret_leak — secret の混入がない"
      - "no_destructive_operation — 破壊的操作を含まない"
      - "no_injection_markers — プロンプトインジェクション・マーカーが無い"
      - "no_gate_tampering — ゲートそのものを緩めていない"
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

## What the acceptance gate asks for

**A criterion belongs here when a step of this flow produces the evidence it
names.** Four steps produce two kinds: the diff (implement, then a
risk-selected reviewer reading it) and the deterministic sensors that run on it.
So the gate asks for what a diff and a sensor can settle:

| criterion | what settles it |
|---|---|
| `task_intent_satisfied` | the reviewer, reading the diff against the task |
| `no_unrelated_diff` | the diff |
| `fix_is_minimal` | the diff — and it is this recipe's stated design |
| `no_unrelated_refactor` | the diff |
| `no_secret_leak` | the secret-scan sensor |
| `no_destructive_operation` | the destructive-command sensor |
| `no_injection_markers` | the injection-marker sensor |
| `no_gate_tampering` | the anti-tamper sensor |

Everything else in `standard` + `bugfix` names evidence no step here produces.
`diff_summary_written` and `risk_summary_written` want prose from a step that
writes it; `no_type_errors_or_explained` wants a type check; `bug_cause_identified`
wants the reproduce and plan steps `bugfix` has and this does not — which is why
`fast-bugfix`, also lacking them, leaves it out too. `tests_pass_or_explained`,
`regression_test_added_or_explained` and `existing_behavior_preserved` all want a
test run: `bugfix` and `fast-bugfix` each have a `test` step, and this flow has
none, so its whole budget of two to four calls can complete without a test ever
running.

Neither shipped bugfix recipe lists `no_injection_markers` or `no_gate_tampering`,
and they are here anyway. The rule is what evidence a step produces, not what a
sibling recipe happens to list, and both of those have a deterministic sensor
running on this same diff — the same reason `no_secret_leak` is here. Following
the convention instead would have left two criteria out on no evidential ground
at all.

A criterion nothing in the flow can satisfy does not make the gate stricter. It
makes it a rubber stamp or a deadlock, and either way the gate stops meaning what
its name says. Eight criteria that the evidence reaches beat thirteen that it does
not. Every id comes from `scripts/workbench.py gates` — a criterion invented in a
recipe is one no sensor measures and no other recipe shares.

**Where these are judged, and what they do not narrow.** `--validate` reads this
list, and `facets/instructions/acceptance-check` judges a run against it and
records the results with `workbench.py gate`.

It does not narrow that gate. `build_acceptance` seeds a task's `acceptance.json`
from the `standard` + `bugfix` presets — fifteen criteria — without consulting
any recipe, so the seven this list leaves out stay `pending` and the gate reads
`pending` until a run answers them too. That is true of every shipped recipe:
`bugfix` declares thirteen of fifteen, `fast-bugfix` six. Listing all fifteen
here would not fix it either; it would only move the problem, by claiming
criteria this flow's evidence cannot reach. The divergence between the two
sources of truth is tracked separately.

The deterministic runner is different again: `executor: checks-only` runs the
`checks` commands above and returns, so under `orchestrate run` these criteria
are a declared contract rather than something that pass runs. That gap is tracked
separately too; neither is something this list closes on its own.
