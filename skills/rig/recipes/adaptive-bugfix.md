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
