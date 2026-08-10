# Evidence & Mission Control

RIG's core contract is intentionally small even when the surrounding quality system is not:

**Task → Isolate → Execute → Verify → Accept**

Everything in this document exists to answer a harder question than “did the command run?”:

> Did RIG improve the outcome enough to justify the extra review, tokens and elapsed time — and is the same quality bar really reaching every repository?

## 1. Real-project evidence ledger

Synthetic benchmarks and `/rig:drill` are controlled evidence. They are not production outcomes. The field ledger keeps those categories separate.

```bash
# A RIG task whose production outcome was already recorded with
# `rig-wb wb record-outcome ...` can inherit outcome + create→accept elapsed time.
rig-evidence record \
  --arm rig \
  --task-id rig-20260809-101500-checkout-fix \
  --defects-caught 2 \
  --tokens 18420 \
  --case checkout-null

# Record comparable work completed without RIG.
rig-evidence record \
  --arm bare \
  --outcome incident \
  --defects-caught 0 \
  --tokens 9100 \
  --minutes 17 \
  --case checkout-null

rig-evidence summary
rig-evidence summary --json
```

Observations are appended to `.rig/field-study.jsonl` with schema `rig.field-study/v1`.

### Missing data stays missing

`tokens`, `minutes`, and `defects_caught` are optional measurements. If a provider does not expose structured token usage, do not enter zero. If elapsed time is unknown, omit it. Mission Control renders the field as **unmeasured**.

### The comparison is observational

The summary reports observed incident rates and RIG-vs-bare deltas, but marks the evidence as `observational-not-causal`. Real project tasks differ in complexity, developer, model, timing and risk. Use the optional `--case` field for paired work when possible, and use the existing `rig-wb bench` controlled benchmark before claiming that RIG caused a difference.

## 2. Production outcome coverage

RIG already stores accepted workbench tasks in `.rig/runs/<task-id>/task.json` and real outcomes in `outcome.json` via:

```bash
rig-wb wb record-outcome <task-id> --status ok
rig-wb wb record-outcome <task-id> --status incident --note "..."
```

Mission Control reports both:

- incident rate **among recorded outcomes**;
- outcome coverage **among accepted tasks**.

Those are deliberately separate. One incident among two recorded outcomes must not be presented as “50% of all accepted work” when eight other accepted tasks were never followed up.

## 3. Quality / Cost frontier

For each field-study arm RIG reports:

- observed incident rate;
- defects caught before release;
- mean token use when measured;
- mean elapsed minutes when measured;
- tokens per caught defect only for observations where **both** values exist.

This is the beginning of a Quality / Cost frontier, not a single “RIG score”. A route that catches more defects but costs 4× the tokens is a different product decision from one that catches the same defects for 1.2×.

## 4. Multi-repository governance proof

`rig-wb govern rollup` remains the one governance evaluator. Mission Control only saves a repository list and calls that existing engine.

```bash
rig-evidence fleet-config \
  --project ../payments-api \
  --project ../customer-web \
  --project ../admin-console \
  --since-days 90

rig-evidence fleet
rig-evidence fleet --json
```

The config is `.rig/fleet.json` (`rig.fleet/v1`). Relative project paths resolve from the repository that owns the Mission Control view. The resulting panel shows project count, per-team conformance, failing projects, and the measured conformance findings.

A broken fleet config is displayed as an error. It is never rendered as “0 projects / healthy”.

## 5. Read-only GUI

Generate the self-contained local page:

```bash
rig-mission-control
# writes <repo>/.rig/mission-control.html

rig-mission-control --out /tmp/rig-control.html
rig-mission-control --json
```

The JSON form is schema `rig.mission-control/v1` and is the presentation-neutral contract for future GUI clients.

The first GUI is deliberately read-only. It visualizes:

- the five-stage RIG Core contract;
- active task and gate state;
- drill-measured reviewer confidence;
- token telemetry when providers expose it;
- production outcome rate **and** outcome coverage;
- RIG-vs-bare field evidence and Quality / Cost measures;
- multi-repository governance conformance;
- recorded force-bypass count.

It does **not** add Accept, Discard, Approve, or Waiver buttons. Those mutations remain in the existing CLI paths where policy, approval, separation-of-duties, freshness and audit enforcement already live. A later interactive UI should invoke those same paths rather than reimplementing their rules in JavaScript or a web server.

## 6. What this proves — and what it does not

The new evidence layer can prove that RIG is measuring the things it claims to care about across real work: production outcomes, defect catches, cost, elapsed time, and fleet conformance. It can expose missing evidence instead of silently treating it as success.

It does not, by itself, prove causality. Strong claims still require controlled/matched tasks, sufficient sample size, and repeatable benchmark evidence. That distinction is part of the product contract, not a disclaimer to remove once the dashboard looks good.
