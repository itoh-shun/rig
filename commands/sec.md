---
description: "rig/sec — the white-hat pack. Audits code actively from an attacker's point of view, closes confirmed vulnerabilities with a PoC regression test (fix), and watches with scheduled rescans (monitor). The ethical boundary: your own product or an environment you are permitted to test, static analysis plus local verification only, DAST out of scope by default."
argument-hint: "[audit|fix|monitor] [a path, feature, or finding] [--plan] [--autonomous] [--until …|--times N]"
---

# rig/sec — security, from the attacker's side 🛡️🔍

**Start the `rig:engine` skill with the Skill tool first and follow its SKILL.md** (PARSE → RESOLVE → COMPOSE → RUN, context-minimal, facet ordering, knowledge-layer injection). This command is only the entry point; the engine lives in the skill and is not repeated here. It is the security pack: the same engine the other recipes use, pointed at defending by thinking like an attacker.

```
$ARGUMENTS
```

## The ethical boundary (confirm first; it is not negotiable)

- The target is **your own product's code**, or a **local or staging environment you have been explicitly permitted to test**. Never point these techniques at anything out of scope, at a third party, or at production.
- **Static analysis and local verification only.** Dynamic scanning that sends attack traffic at a running service (DAST) is out of scope by default — and where an allowlist exists in `.rig/security-targets.json`, only at those hosts.
- Where the scope is unclear, ask once before starting. Do not invent it.

## Sub-modes

| Argument | recipe | What it does |
|---|---|---|
| `audit` (the default) | `security-audit` | Audit existing code actively from an attacker's point of view: threat model, then the SAST, SCA, and secret sensors, then a hunt for an exploit. Only a path that actually lands is reported as Confirmed. Read-only. |
| `fix` | `pentest-fix` | Close the audit's Confirmed findings one at a time. Turn the PoC into a regression test asserting **that the attack fails**, make the canonical fix, and refuse to accept until re-exploiting fails. |
| `monitor` | `security-monitor` | Watch for vulnerabilities with scheduled rescans: re-run SAST, SCA, and the secret sensor, triage what is new, and (opt-in) kick a fix. A stopping condition and a ceiling are required. |

If the arguments begin with `audit`, `fix`, or `monitor`, use that; otherwise default to `audit` and PARSE the rest as the target.

## What it does

Hands the target to the chosen recipe. Each instruction is the source of truth for its procedure:
- `facets/instructions/security-audit` — declare the scope, threat-model, run the sensors, hunt for an exploit, aggregate
- `facets/instructions/pentest-fix` — PoC as a regression test, canonical fix, re-exploit, independent review, acceptance
- `facets/instructions/security-monitor` — rescan each tick, triage the difference, report, schedule the next tick

- **The real work is done by subagents** (context-minimal). Long code never reaches the parent.
- **Findings are never invented**: always separate Confirmed — shown to land — from Suspected, where there was not enough information. A high-severity call at low confidence is forbidden.
- **fix does not accept "it is fixed" on anyone's word**: the gate holds acceptance until the original PoC fails when re-run, meaning the hole is actually closed.

## Deterministic sensors (rig does not run your tools; it consumes their output)

**One command** (`run` does the whole thing: run it and ingest the result. Local static scanning only, no outbound traffic):
```
python3 scripts/sast_adapter.py run semgrep --path . --apply <task-id>   # SAST -> sast_findings_clear
python3 scripts/sast_adapter.py run pip-audit --apply <id>               # SCA  -> sca_findings_clear
python3 scripts/sast_adapter.py run npm-audit --apply <id>
python3 scripts/sast_adapter.py run trivy --path . --apply <id>
python3 scripts/sast_adapter.py run claude-security --apply <id>         # finds the newest CLAUDE-SECURITY-*/…jsonl -> deep_scan_findings_clear
```
(Tool-specific flags go after `-- <args>`. When a tool is not installed, it points you at the pipe-in form instead.)

**Pipe-in** (when you do not want rig running the tool, or CI runs it separately):
```
semgrep --json … > out.json ; python3 scripts/sast_adapter.py semgrep out.json --apply <id>
python3 scripts/sast_adapter.py sarif out.sarif --apply <id>            # SARIF (CodeQL, semgrep --sarif, managed export)
python3 scripts/sast_adapter.py claude-security CLAUDE-SECURITY-<ts>/CLAUDE-SECURITY-RESULTS.jsonl --apply <id>
```

`sast_findings_clear`, `sca_findings_clear`, `deep_scan_findings_clear`, and `exploit_reproduced_then_closed` are optional criteria: the gate demands them in projects that registered them under `extra_criteria` in `.rig/gates.json`. **`claude-security` looks at the whole repository across files**, which covers the blind spot a diff-scoped gated review is structurally unable to see — a defect in unchanged code that the change trusts. The `benchmarks/hard-tasks` measurements are where that blind spot showed up.

## Flags

- `--plan` — present the hunt or repair composition and stop. A dry run.
- `--autonomous` — only drops the per-step gate on what monitor and fix delegate to. The capture gate on accept is not lifted.
- `--until <condition>` / `--times N` — monitor's stopping condition. Required.

## Examples

```
/rig:sec audit src/auth            # audit authentication from an attacker's side
/rig:sec audit the payment handler
/rig:sec fix                       # gated repair of the last audit's Confirmed findings
/rig:sec monitor --times 7 new CVEs in dependencies, daily
/rig:sec --plan src/api            # see the hunt composition first
```

## Why route this through rig

Ask a bare model to "find and fix the vulnerabilities" and what tends to come back is a list of impressions, or a patch that satisfies the published test and nothing else — a narrow fix, a silent security defect that lands again the moment you shift the input slightly. The security pack separates Confirmed from Suspected through the `security-findings` contract, `pentest-fix` demands a PoC regression test that goes red before the fix and green after, and an independent verifier plus the acceptance gate ask mechanically whether it really lands and whether it is really closed. `benchmarks/security-tasks/` quantifies that difference as a silent-defect rate.

## run-continuity (SKILL.md §6)

While a RUN is active, restate this run-status header as a single line at the top of every turn. Do not drop it right after an interruption, a question, or tool output:

```
▸ rig | recipe: <security-audit|pentest-fix|security-monitor> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
