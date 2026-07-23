# Changelog

## [1.23.0] - 2026-07-23

Adds a **model-invariance metric** — the first step toward rig's "strongest =
results not swayed by the model" goal, and true to rig's own rule that gate
efficacy is *measured, not asserted*.

The claim behind rig is that the accepted result's quality is bounded by the
**gate**, not by the **model**. `rig-wb bench-invariance` turns that into a
number: it runs the existing paired benchmark once per model in a panel (both
arms driven by that model, reusing `bench.run_benchmark` unchanged), then
measures — per arm — how much the *terminal outcome* varies across the panel.

- **agreement**: fraction of (model × run) samples that reached the same
  outcome for a task; 1.0 = the outcome does not depend on the model.
- **panel silent-defect rate**: did any model, on any run, ship a
  passes-public-but-fails-hidden result. Must be 0 for a model-invariant *and
  safe* harness — a nonzero rig rate forces an `unsafe` verdict regardless of
  agreement.

The headline `model_invariance_score` is the rig arm's mean agreement, reported
next to the bare arm's so the honest comparison (does rig converge outcomes the
bare model splits?) is visible. Infra/invalid samples are excluded from
agreement so CI flakiness cannot masquerade as model sensitivity.

Note on interpretation: on an easy corpus where every model already succeeds
bare, both arms score ~1.0 (trivially invariant) — the metric only *discriminates*
on tasks where bare outcomes diverge by model. Building that harder corpus is
tracked as follow-up; this ships the instrument, verified by unit tests on the
pure scorer (`tests/test_bench_invariance.py`).

This release also adds the first *mechanism* for raising invariance, not just
measuring it: an opt-in **convergence budget**. rig already feeds a failed
step's distilled findings back into the next attempt (#333 `previous_failure`);
`RIG_CONVERGENCE_K=<n>` raises the per-step retry cap so a run keeps iterating on
that feedback for more attempts before escalating. A weaker model thus gets more
feedback-guided chances to converge on a gate-passing result instead of stopping
— extending the range of models whose *accepted* outcome matches a stronger
model's. It only ever raises a step's K (never lowers an explicit recipe
`max_retries`) and is a complete no-op when unset, so all existing behavior is
unchanged unless a run opts in.

### Added

- `rig-wb bench-invariance --provider <p> --models m1,m2,m3 [--corpus ...]
  [--runs N] [--agreement-threshold 0.8] [--out ...] [--html ...]`: model-panel
  invariance report (JSON + HTML). Paid providers require the same
  `--allow-paid-provider` opt-in as `rig-wb bench`.
- `rig_workbench/bench_invariance.py`: pure scorer (`score_invariance`,
  `classify_arm_dict`) plus the panel runner and HTML renderer.
- `RIG_CONVERGENCE_K` convergence budget: `config.effective_k()` raises a step's
  retry cap to the budget when set (`> 0`), plumbed through `recipes.load_steps`.
  Verified by `tests/test_convergence_budget.py`.

## [1.22.0] - 2026-07-23

Adds a **security (white-hat) pack**: an attacker-perspective layer that
proactively hunts vulnerabilities in existing code, proves them with a PoC,
drives a gated fix verified to actually close the hole, and can run on a
scan-only monitoring loop — plus a dedicated benchmark corpus that quantifies
the with-vs-without-rig difference on security work.

The value framing is the same as the rest of rig, applied to security: a bare
agent asked to "fix this vulnerability" tends to write a plausible band-aid
(a denylist, a specific-payload block) that passes the visible tests while the
hole stays open — a *silent security defect*. The pack refuses to ship that.
Findings must separate Confirmed (a PoC actually landed) from Suspected
(insufficient information), fixes must turn the PoC into a red-then-green
regression test, and `accept` stays blocked until the re-exploit fails.

Ethical boundary is built in and non-negotiable: scope is the user's own
product or an explicitly authorized local/staging environment, and the pack is
**static analysis + local verification only** — it never sends attack traffic
to a live service. Dynamic scanning (DAST) is deliberately out of scope and
left behind an authorized-target allowlist.

### Added

- **`/rig:sec` command** with three sub-modes: `audit` (recipe
  `security-audit`, read-only attacker-perspective sweep of existing code),
  `fix` (recipe `pentest-fix`, PoC→regression-test→canonical-fix→re-exploit,
  gated by `exploit_reproduced_then_closed`), and `monitor` (recipe
  `security-monitor`, periodic SAST/SCA/secret re-scan on
  `patterns/autonomous-loop`, scan-only).
- **Personas** `security/exploit-researcher`, `security/threat-modeler`,
  `security/remediation-engineer` (reusing the existing `security-reviewer` as
  the independent verifier), knowledge wiki `attack-catalog` (extends
  `appsec-checklist` with exploitation technique lenses and the ethical
  boundary), and output-contract `security-findings` (Confirmed/Suspected
  split; attack scenario, PoC, `file:line`, root cause, and a *canonical* fix
  are all mandatory; low-confidence Critical/High is forbidden).
- **SCA support in `scripts/sast_adapter.py`**: `pip-audit`, `npm audit`, and
  `trivy fs` JSON now fold into a `sca_findings_clear` acceptance-gate
  criterion, alongside the existing semgrep → `sast_findings_clear`. rig still
  never runs the tool — you pipe its output in (#276 design).
- **Security benchmark corpus** `benchmarks/security-tasks/` (6 stdlib-only
  Python tasks: absolute-path traversal, shell command injection, SSRF via
  private/metadata IPs, unsalted password hashing, seedable-PRNG reset tokens,
  IDOR). Each ships a `narrow` variant (the plausible fix that passes the
  public suite but fails the hidden exploit) and a `canonical` variant (the
  real fix). Runs on the existing paired runner —
  `rig-wb bench --corpus benchmarks/security-tasks` — and reports the bare-vs-rig
  silent-defect rate and relative reduction. Guarded by
  `tests/test_security_bench_tasks.py`.
## [1.21.3] - 2026-07-23

Closes #341: `scripts/validate.py` (CI) previously had no manifest checks at
all, despite `facets/instructions/validate.md` §2 specifying them in detail —
a malformed `.claude/rig.md` (e.g. `default_backend: "manul"`, a typo) was
silently swallowed at RESOLVE/COMPOSE time and never caught by CI, only by a
human remembering to run `/rig:dev --validate` by hand.

### Added

- `rig_workbench/validation/manifest.py` (`check_manifest()`): CI-checks the
  5 manifest value keys that are mechanically type/enum/ordering-determinable
  — `default_backend` (`manual`/`workflow`), `default_budget` (`low`/`mid`),
  `default_orchestrate` (boolean), `worktree.enabled` (boolean), and
  `size_thresholds` (positive-integer subkeys, ascending
  `S_max < M_max < L_max` with generic defaults substituted for unset
  subkeys). Silently skips when `.claude/rig.md` doesn't exist (manifest is
  optional) or has none of these 5 keys set. Wired into `validate.py`'s
  `main()` alongside the other checks.
- 11 synthetic positive/negative fixtures in `scripts/validate.py selftest`
  covering all 5 checks, plus a standalone `tests/test_manifest_check.py`
  (24 tests) exercising `check_manifest()` directly.
- `default_recipe`/`default_personas[]` (tier reference resolution) and
  `knowledge.*` (path existence) remain unimplemented in CI — they need the
  same project→user→shipped resolver COMPOSE uses, a different scope from
  this issue's 5 self-contained value checks. `validate.md` §2 now notes the
  CI-implemented/unimplemented split explicitly.

## [1.21.2] - 2026-07-23

Documentation-only sync closing two discoverability gaps (#337, #327).

### Fixed

- README.md / README.ja.md's Positioning section still called the MCP server
  "proposed but not shipped," contradicting §7's own shipped documentation of
  `scripts/mcp_server.py` (#263). Updated to point at §7 instead (#337).
- `stream-checks` / `stale-refs` / `scan-destructive` / `instincts` (all
  implemented and tested `workbench.py` subcommands) were missing from every
  summary listing: `workbench-ops.md`'s opening line, `SKILL.md` §2's
  workbench pack row, `SKILL.md` §10's reference table, and `commands/go.md`
  (frontmatter `description`/`argument-hint` and the ① subcommand table).
  Added to all of them (#327).

## [1.21.1] - 2026-07-23

A real `--bare-model fable --rig-model sonnet` cross-model comparison (all 10
tasks x 3 runs, `--allow-paid-provider`) passed the recipe's own acceptance
gate: rig(sonnet)'s silent-defect rate is 3.3% vs bare(fable)'s 10.0% (66.7%
relative reduction), rig safe-stop 10.0%, call ratio 2.37x. Honest caveat: most
of that delta traces to one task (`ts-api-compat-export`) where fable-bare
produced a silent defect in all 3 runs while rig(sonnet) recovered to
clean_pass in 2 of 3 via its review/repair loop — this is evidence that rig's
review path caught one systematic model blind spot, not a general "cheap
model + rig beats a stronger model" result.

Investigating the run's 3 safe-stops (with a second opinion from Codex
gpt-5.6-sol, read-only) surfaced and fixed a real bug (#342):
`_execute_targeted_review()` only ever inspected `verdicts[0]` (the primary
reviewer), so when a second high-risk domain adds a secondary reviewer and
the primary passes, a repairable FAIL from the *secondary* reviewer was never
attempted — it escalated unconditionally instead. Fixed to attempt repair on
the single failing verdict regardless of primary/secondary position.

### Fixed

- `_execute_targeted_review` (`rig_workbench/orchestrate/providers.py`) now
  finds all failing verdicts and attempts `execute_informed_repair` when
  exactly one verdict failed, instead of only ever checking `verdicts[0]`.
  All existing safety conditions (allowlist match, diff-changed, generator
  success, post-repair check, invocation budget) are unchanged (#342).

## [1.21.0] - 2026-07-23

Adds `--bare-model`/`--rig-model` to `rig-wb bench`, a per-arm model override
for a third benchmark question the same-model pairing (1.20.0's Claim B)
can't answer: can a cheaper model driven by rig approach a stronger model's
bare output? Both flags default to `--model` when omitted, so the historical
same-model-both-arms behavior and its `score_provider` single-identity
invariant are unchanged unless a run explicitly opts in. The JSON report now
carries `bare_model`/`rig_model` alongside the existing `model` field (which
stays the rig arm's model for backward compatibility), and the HTML report's
provider/model card shows both when they differ.

### Added

- `rig-wb bench --bare-model <model> --rig-model <model>`: override the model
  for a single arm. `run_benchmark`/`run_pair` resolve each arm's model
  independently only when at least one override is given (otherwise a single
  resolution is reused, preserving the prior local-provider discovery
  behavior and call count).

## [1.20.1] - 2026-07-23

Closes the TypeScript follow-up left open by 1.20.0 (#338): the backtick/quote
`MECHANICAL_CHECK` unwrap fix was re-verified with a real
`--allow-paid-provider` Codex (gpt-5.5) run across all 5 TypeScript benchmark
tasks (`ts-api-compat-export`, `ts-async-error-propagation`,
`ts-auth-sibling-handler`, `ts-generated-file-modification`,
`ts-stale-cache-mutation`), 3 runs each. Result: 15/15 valid pairs, 0%
rig safe-stop, 0% rig silent defects (one transient `provider_failure` infra
error was retried to reach 3 valid runs on `ts-auth-sibling-handler`). The
Codex safe-stop regression from 1.20.0 is now confirmed resolved on both the
Python and TypeScript halves of the corpus.

### Fixed

- No code change in this release — this is a re-verification-only entry
  confirming the 1.20.0 `execute_informed_repair` backtick/quote unwrap fix
  also holds on the TypeScript half of the benchmark corpus (previously
  untested because the Codex account hit its usage limit mid-verification).

## [1.20.0] - 2026-07-21

A real paired bare-vs-rig benchmark run (10 tasks x 3 runs x 2 providers,
`--allow-paid-provider`) on `adaptive-bugfix` surfaced and drove the fix of a
real bug (see Fixed): `cfg["cwd"]` was never set outside `--isolate`, so risk
assessment always saw an empty diff (permanent fallback to `test-reviewer`,
security/design routing never fired) and informed-repair's diff-changed check
always compared `""` to `""`. Post-fix, Claude passes the recipe's own
acceptance gate outright: 0% rig silent defects vs 3.3% bare (100% relative
reduction), 0% safe-stop (was 60% pre-fix), 2.33x call ratio. Codex's
wrong-default-value silent-defect regression is fully resolved (0% both arms,
was 6.9% rig vs 0% bare pre-fix). Codex's safe-stop rate had also risen to
27.6% (over the 20% threshold) now that risk assessment sees real diffs;
tracing showed gpt-5.5, unlike sonnet, reliably wraps an otherwise
well-formed, repair-eligible `MECHANICAL_CHECK` value in backticks, which
broke the exact byte-for-byte allowlist match and made repair permanently
unrepairable. Stripping one symmetric layer of backtick/quote wrapping before
the allowlist comparison (never enough to make an unrelated string match —
the stripped result still has to equal an allowlisted command exactly)
eliminated safe-stop on every Python task re-verified with a real
`--allow-paid-provider` Codex run (0/3, 0/3, 0/3 across the three previously
worst-affected tasks). Full re-verification of the TypeScript half is
follow-up work: the Codex account hit its usage limit mid-verification.

### Added

- Added the opt-in `adaptive-bugfix` recipe. Its normal path uses two model
  calls (implementation and one targeted review); deterministic diff-risk
  assessment selects the reviewer, with a second review or one bounded repair
  call only when risk or failed checks justify it. Existing default recipe
  routing is unchanged.
- Rebuilt `rig-wb bench` around 10 repository-shaped Python and TypeScript
  tasks, paired writable bare/rig workspaces, externally isolated hidden
  checks, exact provider-call journals, and provider/model-scoped scoring.
  Acceptance requires at least 3 valid pairs for each of at least 10 tasks,
  at least 50% fewer rig silent defects, no more than 20% rig safe stops,
  average rig calls no more than 2.5x bare, and no more than 10% infrastructure
  errors. A zero bare silent-defect count is inconclusive, not a pass.
- Benchmark JSON is now schema version 2 and records corpus, provider, concrete
  model, validity, outcomes, calls, infrastructure errors, unrelated diffs,
  and workspace leaks. The HTML renderer retains compatibility with schema-v1
  reports. Mock results are labeled `WIRING ONLY`.
- Real Claude/Codex benchmark execution now requires the explicit
  `--allow-paid-provider` opt-in. Benchmark CLI exit codes are `0` for a
  passing result, `1` for completed fail/invalid/inconclusive results, and `2`
  for CLI or schema errors.

### Fixed

- `adaptive-bugfix`'s risk assessment and informed-repair diff detection
  (`_git_diff_evidence` / `_git_changed_files` in
  `rig_workbench/orchestrate/providers.py`) silently analyzed an empty diff
  on every real (non-`--isolate`) headless run, because `cfg["cwd"]` is only
  ever set inside the `--isolate` branch of `cmd_run`. This both defeated
  security/design risk routing (permanent fallback to `test-reviewer`) and
  made `execute_informed_repair`'s diff-changed check always `False`
  regardless of what the repair generator actually wrote to disk. Both now
  fall back to `config.INVOCATION_CWD`, matching the fallback the mechanical
  check subprocess already used. The same gap independently no-opped the
  local-provider (ollama/lmstudio) generator dispatch; claude/codex were
  accidentally unaffected there since their subprocess `cwd=None` inherits
  the parent process's cwd.
- The implement step's blanket "do not change tests" rule made any reviewer
  FAIL that asked for missing test coverage permanently unrepairable (no
  mechanical check can ever be "add a test"). It now permits adding exactly
  one narrowly-scoped verification test: on first pass when the fix's
  correctness depends on an unstated default/edge-case value, or during the
  one-shot informed-repair pass when the reviewer named the input/behavior
  via an allowlisted mechanical check.
- `execute_informed_repair`'s `MECHANICAL_CHECK` allowlist match failed
  whenever a reviewer wrapped an otherwise-correct command in backticks or
  quotes (`` `/usr/bin/python3 -m pytest -q` ``) — a formatting habit gpt-5.5
  reliably exhibits in this contract but sonnet does not, which is why the
  same recipe/prompt safe-stopped far more on Codex. `_unwrap_inline_markup`
  strips one symmetric layer of such wrapping before the comparison; the
  result must still match an allowlisted command byte-for-byte, so this can
  only fix a false-negative match, never let an unrelated string through.

rig の変更履歴。バージョンは `.claude-plugin/plugin.json` に対応。
形式は [Keep a Changelog](https://keepachangelog.com/) に準拠（日付は JST）。

> リリースタグは GitHub 側で発行する（実行環境の都合でタグ push を別途行う運用）。

## [1.19.0] - 2026-07-19

Measurement-driven release: two benchmarks were built to answer "is rig
worth using?", and the live (real-provider) runs they enabled uncovered
four real defects in the headless pipeline — each filed, fixed, and
re-measured in this release. Final measured state on the adversarial
bench task (9 rig runs vs 9 bare runs): silent defects bare 1/9,
rig 0/9; unnecessary escalations on correct code eliminated (2→0);
correct code now passes the 3-way review unanimously on the first vote
while genuinely defective fixes still get stopped.

### Added

- **Two benchmarks answering "is rig worth using?" (#330)**: the claim
  splits into two, and only one is provable without spending money.
  New `rig-wb sensor-bench` (zero LLM calls, zero billing, fully
  deterministic) runs the secrets/injection/destructive machine
  sensors against a fixed corpus of known-bad lines and safe
  near-misses — current result: 10/10 known-bad lines caught, 0/7
  false positives. The point isn't the number itself, it's that a
  bare LLM loop has **no number here at all** — nothing runs these
  checks unless something wires them in, so its guaranteed catch rate
  on this corpus is 0% by construction. This is a floor, not a
  ceiling: it says nothing about judgment-requiring defects (that's
  `/rig:drill`'s and `rig-wb bench`'s territory). Separately, `rig-wb
  bench` (bare-vs-rig with a hidden spec-check per task, shipped
  since v1.9.0) turned out to have **shipped with zero tests and zero
  README mention** — it's now documented in both READMEs and covered
  by a mock-mode smoke-test suite. Honest scope, stated explicitly in
  code and docs: `--provider mock` only proves the harness plumbing
  works (MOCK_SRC hardcodes the built-in tasks' fixes) — it is *not*
  evidence for the bare-vs-rig quality claim; only a real-provider run
  is, and that costs real money, so this repo doesn't run or publish
  it automatically.

  A real (`--provider claude`) run of the original 4 tasks under
  `fast-bugfix` also surfaced #331 (see Fixed below) and, once fixed,
  showed bare and rig converging on identical spec-check-passing
  output — no quality delta on that task set, only cost (rig ~20-40x
  slower, 3x more calls for the same result). That's an honest, small
  result, not a generalizable one: those 4 tasks are single-file and
  self-evidently specified, and `fast-bugfix` deliberately skips
  review-diff — rig's most likely source of differentiation on harder
  tasks was never exercised. `rig-wb bench` gained a 5th task,
  `auth-bypass-sibling`, built to test exactly that gap: the bug
  report names one method (`get_profile`) with a missing
  ownership check; a sibling method (`update_profile`) has the
  identical bug but is never mentioned in the goal or the
  deliberately-weak visible tests. A narrow fix of only what was
  asked passes the visible tests and fails the hidden spec — locked
  in by a regression test so the corpus can't silently drift.

  bench results now carry an asymmetric outcome classification
  (`classify_outcome`), because "failed" means opposite things in the
  two arms: `silent_defect` (claimed done, hidden spec broken — the
  worst outcome, nothing signals a human to look) vs `safe_stop`
  (rig-only: escalated to a human although the code was actually
  right — over-conservative but honest) vs `stopped_wrong` /
  `clean_pass`. The live hard-task run produced exactly this split
  (bare: 1 silent defect in 3 runs; rig: 0 silent defects, 2 safe
  stops), so the report format now names it instead of burying it in
  exit codes. Surfaced per-run in stdout, as HTML KPI tiles, and as
  per-task outcome columns; old JSON without the field still renders.

- **grok-build host adapter + `--provider grok` (#328)**: grok-build
  (xAI's terminal coding agent) documents full Claude Code
  compatibility — plugins/skills/hooks/MCP/CLAUDE.md auto-load with
  zero configuration — so `scripts/host_adapters.py` gains a
  `grok-build` entry as a **native passthrough**: canonical hook-event
  names pass through unchanged and `hooks/hooks.json` is reused
  verbatim (no host-specific copy). Every capability is `unverified`
  (the compat claim is theirs; no grok CLI exists in this environment
  to exercise it live). `orchestrate` gains a `grok` provider
  (`grok -p <prompt> --output-format plain`, per-step `-m` model
  support) with one gap declared honestly: grok headless documents no
  read-only/sandbox flag, so the verifier role's read-only stance
  rests on the prompt contract alone — one enforcement layer thinner
  than `claude` (`--allowedTools`) or `codex` (`--sandbox read-only`).
  `--always-approve` is never passed (auto-approves tool executions;
  a generator that wants it opts in via `--provider-cmd`). Covered by
  host-adapter golden-fixture tests and selftest argv probes; existing
  providers untouched.

- **Size-based auto-tiering at routing (#324)**: after task-type
  classification, the workbench routing estimates a size tier from the
  input — S (single file / few lines / self-evident fix) steers bugfix
  to `recipes/fast-bugfix` (no reviewer fan-out, minimal gate), M
  stays on the standard recipes, L turns the design/review steps on.
  What gets lighter is step and verifier count, never the safety
  machinery: the isolated worktree and acceptance-gate hold on every
  tier ("small" is not an exemption from isolation). The tier and its
  reason surface in the routing banner, explicit `--recipe`/`--only`
  always beat the auto-tier, and a mis-judged S escalates via the
  stuck-guard ("2 stalls → propose re-running under the full recipe")
  with the misjudgment logged as calibration material.

### Changed

- **First-run positioning sharpened (#325, from external review)**:
  README §1 now states rig's honest self-definition — it does not
  automatically produce quality; it makes the AI unable to ignore the
  quality bar you define, and it deliberately trades speed and tokens
  for that safety. §2 adds a directly-vs-through-rig comparison table
  (failed attempts / "it's done" / review quality / what happened) and
  states the zero-configuration property explicitly. No new concepts
  or sections — density up, count unchanged.

### Fixed

- **Headless verify verdicts had no blocking/non-blocking distinction —
  advisory findings rounded up to FAIL and deadlocked quorum=all
  (#334, discovered by the post-#332 re-measurement)**: the
  interactive review-verdict contract has always had
  `APPROVE_WITH_CONDITIONS`, but `_build_verify_prompt` forced a
  binary `VERDICT: PASS|FAIL`. Once #332 gave each reviewer its real
  lens, lens-faithful advisory findings ("no regression test in the
  diff" — on a task whose goal *forbade* touching tests) had no
  conditional-approve outlet, so first-round votes went 0/3 on
  objectively correct code and every run escalated. Fix ports the
  conditional-approve semantics to the headless path: the verify
  prompt now instructs FAIL is ONLY for a blocking defect statable as
  a one-line concrete failure/attack scenario, non-blocking findings
  go to reasoning + `VERDICT: PASS_WITH_CONDITIONS`, and
  `_verdict_ok` recognizes the new token explicitly (`_PASS_TOKENS` —
  it previously passed only by accident of prefix matching). Not a
  weakening: blocking defects still FAIL (the live run where
  reviewers caught a genuinely-narrow fix and stopped it —
  `stopped_wrong`, defect NOT shipped — is the behavior being
  preserved). The bench task's goal also stopped forbidding new test
  files ("do not modify the *existing* tests"), removing a
  structurally unsatisfiable reviewer demand.

- **Gate-failure RETRY was blind — reviewer findings were discarded
  before the retry generator ever saw them (#333, discovered by a live
  #330 bench run)**: `compute_next`'s RETRY path reset
  `st["verdicts"]` (the reviewers' evidence-anchored findings) and
  nothing wrote `last_failure` for gate-verdict failures, so the
  retried generator received only `attempt: 2` — no idea what the
  reviewers rejected. rig paid for 3 independent reviews, then threw
  them away and re-rolled the dice; the observed "1/3 PASS → retry →
  1/3 PASS → escalate on objectively-correct code" is exactly what
  blind retries look like. Fix: `_distill_failures` summarizes failed
  checks + dissenting verdicts (bounded: 240 chars/finding, 800
  total) BEFORE the reset; the summary lands in `st["last_failure"]`
  (feeding the pre-existing `previous_failure:` line in the next
  attempt's step contract) and on the FAIL history entry (so
  ESCALATE leaves an audit trail of why). Honest scope: this informs
  the retry, it does not guarantee the retry converges — the effect
  is measured by the #330 bench, not assumed.

- **headless review-diff's 3-way review was 3 identical samples of one
  question, not 3 distinct lenses (#332, discovered by a live #330
  bench run)**: a hard-task bench run showed the 3 reviewers
  (security/design/test) disagreeing (1/3, 2/3 PASS) on code that was
  already objectively correct. Root cause: `run_verifiers_parallel`
  recorded each `persona` for telemetry but never put it in the
  prompt — `build_argv`'s real (`claude`/`codex`/`rig`/`grok`)
  branches ignore the `persona` argument entirely, so every reviewer
  received the exact same generic verify prompt. The interactive
  "manual backend" (`/rig` skill via the Agent tool) was never
  affected — there, each persona file genuinely is a distinct
  subagent's system prompt; only the headless CLI provider path had
  this gap. Fix: `run_verifiers_parallel` now prefixes each verifier's
  prompt with its resolved `facets/personas/<name>.md` brief (e.g.
  security-reviewer's explicit "authorization / IDOR" axis — directly
  relevant to the bug that exposed this) when one resolves; falls back
  to the unchanged generic prompt otherwise (no silent no-op
  injection). `mock`'s existing deterministic persona-based pass/fail
  is untouched (verified by test — it never reads prompt content).

- **`--provider claude`/`rig` generator couldn't actually edit files in
  headless mode (#331, discovered by a live #330 bench run)**:
  `build_argv`'s generator branch for `claude` and `rig` set no
  permission flags. Headless `claude -p` has no one to approve
  Edit/Write tool calls, so an unpermissioned generator asks for
  approval it can never receive and silently writes nothing — this
  environment's first-ever real (non-mock) `claude` provider run hit
  it immediately: every task's `implement` step failed its
  `git diff` check twice and escalated. Confirmed live: the exact
  `claude -p "<edit prompt>"` call left a target file untouched; the
  identical call with `--permission-mode acceptEdits` applied the
  edit. Fix adds `--permission-mode acceptEdits` to the generator
  role only (minimum-privilege — edits allowed, nothing else
  blanket-bypassed; not `--dangerously-skip-permissions`). Verifier
  argv (`--allowedTools Read,Grep,Glob`) is untouched — still
  read-only. `codex`'s generator already had its own write mechanism
  (`--sandbox workspace-write`) and was never affected.

- **Trust-store write race (#329)**: `_record_trust` was an unlocked
  read-modify-write with a non-atomic write; manifest A/B (#317)
  records trust from parallel variant threads, so entries could be
  lost under contention (the intermittent `test_manifest_ab` failure).
  Now serialized behind a module lock and written via atomic
  `os.replace` (readers can never observe a half-written store).
  Honest scope: cross-process simultaneous writers remain
  last-writer-wins over the whole store — out of scope because the
  only concurrent writers in practice are variant threads inside one
  orchestrate process. Regression test hammers the store from 8
  threads × 32 entries.

## [1.18.0] - 2026-07-17

### Added

- **Host-native skill lanes (Claude Code built-ins as measured
  reviewers)**: rig's review fan-out (`parallel-review` ②) gains an
  optional native lane — when the live Claude Code session exposes the
  built-in `/code-review` (and `/security-review` for security_review
  tasks), it joins the fan-out as ONE additional vote, its findings
  translated into the existing `review-verdict`/`review-findings`
  contract and its verdict recorded under the persona name
  `native-code-review`. Two disciplines are structural: the native
  lane is subject to the same measurement as every persona (stats
  rubber-stamp detection; in-session `/rig:drill` can include it in
  the fan-out and measure its detection rate — no unmeasured
  reviewers), and it supplements rather than replaces the persona
  quorum (it runs on the session's own model, so making it the only
  lane would collapse independent verification back into same-model
  self-review). `verify` ②-b similarly delegates to the built-in
  `/verify` skill when present — an extra layer that catches
  green-tests-but-broken-flow changes. Headless runs (orchestrate.py
  providers, CI, MCP) have no built-in skills; the lanes are skipped
  silently and the flow structure is unchanged. SKILL.md §8
  (Native-first) now names host built-ins as part of the inventory to
  check, with both disciplines spelled out. The same rule extends to
  host agent types: read-only codebase exploration dispatches use the
  host's **Explore agent type** when available (structurally
  write-incapable — safe for the investigation stage, faster and
  cheaper than a general subagent; `intake` ①), falling back to a
  normal subagent otherwise.

## [1.17.0] - 2026-07-17

### Changed

- **Response-speed pass (#321)** — behavior-identical, measured:
  one gate evaluation went from 23 git subprocesses / 114ms to
  6 / 76ms (sensors now share one diff fetch via an opt-in
  `shared_diff_cache()` scoped to the evaluation, and the schema
  sensor batch-probes its 12 OpenAPI candidates with a single
  `ls-tree` instead of one `cat-file -e` each); `orchestrate.py`
  startup dropped 170ms → 143ms by returning `urllib.request` to
  function-local imports; the plugin description was rewritten from
  6,283 to 1,454 chars (77% less context loaded per session). All
  497 tests pass unchanged — sensor verdicts and outputs are
  identical.

### Added

- **Streaming gate — mid-implementation lightweight checks (#302)**:
  `workbench.py stream-checks <task_id> [--watch --interval N]` runs
  the fast machine sensors (secret / injection / destructive —
  diff-scoped, no LLM, tens of milliseconds) against the task worktree
  on demand, printing findings as hints. The issue's core requirements
  are enforced by shape, not promise: the command never reads or
  writes acceptance.json and always exits 0, so it structurally cannot
  block the final gate — the same detectors run again at gate time,
  where pass/fail is actually decided; streaming is a preview of that
  verdict. Opt-in (nothing calls it automatically; implement.md
  suggests it at natural checkpoints on L/XL implementations), and
  diff-scoped so cost is bounded by the change. `--watch` re-scans
  only when the diff hash changes.
- **Standard drill corpus + prose/design seed classes (#270, #266)**:
  the seed catalog in `facets/instructions/drill.md` is now formally
  the **standard corpus** (`corpus_version: 2`, 27 seed classes) — the
  same language-agnostic yardstick on any repository. `--corpus
  standard|project|all` selects the seed source (project =
  `.claude/rig/drill-corpus.md`, same table schema); each
  drill-results.jsonl run row carries `corpus`/`corpus_version` so
  standard and project-specific scores never blend (rows without the
  field predate the distinction and count as standard), and
  `aggregate_drill_confidence()` gains a corpus filter. v2 adds 9
  prose/design seed classes — AI-smell markers, UX-heuristic and
  WCAG violations, unsourced hype in posts, engagement-structure
  defects, over-the-line attacks (roast), and sales-flow gaps
  (hearing/proposal/closing) — making de-ai-smell, design,
  design-audit, sns-x-post, scenario, roast, and deal-review
  drillable: coverage went from 9/25 to 16/25 gate-bearing recipes,
  clearing all 7 per-recipe validate WARNs (the remaining WARN —
  gate-bearing recipes with no reviewer personas at all — is
  structural: drill measures reviewers, and stats' rubber-stamp
  detection covers those recipes instead). `validate.py` gains
  `check_corpus_integrity` (version marker present, every row carries
  class/provenance/perspective, severity/blocking in range) so corpus
  rot is machine-caught.
- **Manifest A/B — rule changes measured, not guessed (#317)**:
  `orchestrate.py ab <recipe> --manifest-a <path> --manifest-b <path>`
  runs the same recipe concurrently under two manifests — additive rule
  changes can't be evaluated statically, only by running real tasks
  under both. Each variant's worktree gets its manifest written as
  `.claude/rig.md` (the main working tree is never touched) with its
  content hash trust-recorded (explicit CLI provision = consent, the
  `--allow-project-manifest` consent model). Comparison rows are
  labeled `A(<stem>)`/`B(<stem>)`. Honest scope: the variant manifest
  takes effect for nested provider invocations running inside the
  worktree (cwd-based resolution); the parent orchestrate process's
  own `load_manifest()` still reads the invoking repo's manifest.
  Recipe/provider/model stay identical across variants — the measured
  difference is the rules'. The existing recipe-comparison mode is
  unchanged.
- **`scan-injection --deps` — dependency-tree hidden-instruction scan
  (#320)**: explicit opt-in scan of prose files (`*.md`/`*.rst`/
  `*.txt`, never source) under `node_modules`/`vendor`/`third_party`
  for agent-directed injection markers — countering supply-chain
  attacks that plant hidden instructions in third-party docs. Never
  part of the default surfaces (huge trees; AI-library READMEs
  legitimately contain prompt examples, making phrase findings
  especially false-positive-prone there). Invisible unicode stays
  fail-grade — zero legitimate uses, and exactly the hiding mechanism
  such attacks rely on. Recommended actions (review in context; if
  real, pin/quarantine and report upstream) print with the findings.
- **Harness-context load in `runs --cost` (#319)**: the per-recipe
  rollup now closes with a per-provider summary of prompt weight —
  average prompt tokens per call and the prompt:completion ratio —
  derived entirely from the existing token_usage telemetry (no new
  metering). The output states the honest caveat inline: prompts
  include the user's own task text, so this is an upper bound on
  harness overhead, not the overhead itself; separating the injected
  step-contract/knowledge share would need per-segment metering that
  doesn't exist yet.
- **prose_rhythm v2 — burstiness, paragraph-CV, field-measurement
  corrections (#318)**: `low-burstiness` catches a locally flat beat
  (mean adjacent sentence-length delta / mean length) that
  document-wide CV misses — a slow short-to-long drift has variance
  but no alternation. `uniform-para` relaxes from exact sentence-count
  equality to a CV threshold, catching the "every paragraph is 2-3
  sentences" template tic. `taigendome_ratio` is reported
  informationally (never flagged): independent field measurement
  (7 models × 406 documents vs a 137-document human corpus) found
  AI-generated Japanese uses 体言止め at near-zero rates while humans
  mix it in — the *absence* is the signal, reversing the folk belief.
  The same measurement's corrections land in ai-writing-smells
  (attributed): sentence-initial repetition is a *human* habit (93% of
  human documents), and rhythm monotony varies sharply by model family
  ("clean vocabulary, monotone rhythm" exists — grounds for rejecting
  on the rhythm layer even when every vocabulary marker passes). The
  thresholds' honest status (uncalibrated heuristics; mora-based
  measurement impossible stdlib-only) is now stated in the docstring.
- **Stale path-reference check for the manifest/knowledge layer (#316)**:
  `workbench.py stale-refs [paths…]` scans `.claude/rig.md` and the
  project knowledge layer for backtick-quoted relative path references
  whose target no longer exists — the direct rot signal, next to the
  time-proxy freshness stamps (wiki `reviewed_at`, instinct decay).
  Deliberately conservative extraction (two-plus segments, extension or
  trailing slash, no URLs/absolute/placeholder tokens, code fences
  skipped; bare prose paths are out of scope by design) and
  ancestor-walk resolution (a doc may speak relative to any contextual
  root between its own directory and the repo root), so the
  false-positive rate on real docs is near zero. WARN-only, exit 0 —
  fixing or deleting a reference stays a judgment call. `validate.py`
  applies the same logic to rig's own 201 shipped docs via
  `check_stale_refs` (curated example-namespace excludes for paths that
  describe user projects or other repos), with a clean baseline.
- **Destructive-command sensor backing `no_destructive_operation` (#315)**:
  deterministic scan of the task diff (added lines + untracked files)
  for destructive command patterns, wired into every `gate` evaluation
  the same way the secret/injection sensors are. Unambiguous destroyers
  (`rm -rf /`, `mkfs`, `dd of=/dev/...`, `DROP DATABASE`) are
  fail-grade; context-dependent patterns (absolute-path/variable/`~`
  `rm -rf`, `git clean -f`, `git reset --hard`, `git push --force`
  without `--force-with-lease`, `DROP TABLE`/`TRUNCATE`,
  `chmod -R 777`) and mass deletions (>= 20 files vs base) are
  warning-grade. Relative-path `rm -rf build/` is deliberately not
  flagged (everyday-legitimate in clean targets). Explicit
  `--set no_destructive_operation=passed` is the recorded escape hatch
  (`destructive_override`). Standalone CLI: `scan-destructive`. Honest
  scope: detects destructive commands written into the diff — it does
  not intercept commands the agent executes at run time (that is the
  host permission system's job).

## [1.16.0] - 2026-07-16

### Added — issue-backlog sweep (#263–#307) + writing-quality layer

- **Cognitive-rhythm grounding + deterministic prose-rhythm sensor**:
  `knowledge/ai-writing-smells` gains a summarized (own-wording,
  attributed) cognitive-rhythm principle — dense prose reads as boring
  when the reader's cognitive mode never switches — plus compact
  practice rules (opening tension, section bridges, list landing,
  tension ledger, density waves, topic test). `scripts/prose_rhythm.py`
  (stdlib-only, deterministic) machine-measures the surface proxies:
  long-sentence runs, uniform sentence-length variance, ending
  repetition, uniform paragraph shapes, progress-narration phrases
  (topic-test deletion candidates), and connective density. Advisory
  by design — exit code never gates; the semantic judgment stays with
  `ai-smell-reviewer`, which now gets numbers instead of impressions
  (wired into de-ai-smell's detection step as an optional pre-pass).
- **`/rig:rig cockpit` — read-only Mission Control dashboard (#307)**:
  Aggregates run timeline, gate radar, drill-measured reviewer
  confidence, a cost meter, and a force-bypass safety strip onto one
  screen by reusing `board`/`stats`/`audit`/`confidence`'s existing
  aggregation functions (`read_all_tasks`, `gate_status_counts`,
  `aggregate_drill_confidence`, `force_bypass_counter`) — no new
  persistence, no duplicated logic. The cost meter reads the same
  `.rig/runs.jsonl` token-usage telemetry `orchestrate.py runs --cost`
  already produces (#271/#296, which didn't exist yet when this
  feature was originally designed). v1 is read-only: accept/discard
  stay in the existing commands, cockpit only recommends. Missing data
  (no drill run, no token usage recorded) is shown as "Unmeasured"
  rather than a blank that could be misread as healthy.
- **Continuous cross-session instinct-learning layer (#306)**:
  `workbench.py` gains an `instincts` subcommand managing
  `.rig/instincts.jsonl` (id/text/evidence/source_task_ids/confidence/
  first_seen/last_seen/hit_count/decay_reason/status/supersedes) —
  completely separate from `facets/knowledge`'s verified wiki.
  `--add` rejects secrets/tokens/absolute home-directory paths/
  `ENV_VAR=value`-shaped candidates outright, with the reason shown
  (never a silent drop). `--decay` lowers confidence by 0.1 for active
  instincts unused for 30+ days, expiring below 0.2 — implicit
  knowledge rots by design rather than accumulating forever. Conflict
  resolution is explicit, not inferred: recognizing two instincts
  contradict each other needs judgment, so `--supersedes <old-id>` is
  how the model declares it, which mutes the old one. Only confidence
  >= 0.7 gets selected for injection, capped at 500 chars total
  (`select_for_injection`), keeping context-minimal intact.
  `hooks/suggest-instincts.sh` (Stop) reminds the model to consider
  proposing a pattern — it doesn't extract one itself, since deciding
  what's durably useful is a judgment call the hook can't make; most
  sessions won't have anything worth recording.
  `hooks/inject-instincts.sh` (SessionStart) injects the selected
  instincts as `additionalContext`. Both wired into `hooks/hooks.json`
  without touching the existing inject-talk-mode.sh/
  preserve-rig-state.sh/remind-rig-header.sh hooks. Verified end-to-end
  in a disposable repo: secret-pattern rejection, supersedes-based
  muting excluding the old instinct from `--inject-preview`, decay
  after backdating `last_seen`, and CLI-level mute/expire/decay/list.
  Honest scope: automatic semantic contradiction *detection* isn't
  implemented — only the mechanical *resolution* once a contradiction
  is explicitly declared via `--supersedes`. Pattern extraction itself
  is left entirely to the model's judgment.
- **Read-only VS Code extension for rig board (#286)**:
  `vscode-extension/` shows `.rig/runs/` task/gate state in an Explorer
  sidebar panel, refreshed via a FileSystemWatcher. It's read-only by
  construction — no accept/discard or any other write command is
  registered. The state-parsing logic (`rigState.ts`) has no dependency
  on the `vscode` module, so it's unit-tested with plain Node; the
  gate-status priority order is ported to match `workbench.py`'s
  `gate_status()` exactly. Compiles cleanly against `@types/vscode`;
  actually loading it inside a live VS Code Extension Host is
  unverified in this environment (no VS Code GUI available here).
- **Experimental Managed Agents API backend for review fan-out (#295)**:
  `run_managed_agents_fanout()`, an opt-in alternative to the existing
  subprocess + ThreadPoolExecutor review-gate fan-out, delegates to
  Anthropic's Managed Agents API (coordinator/worker beta,
  `managed-agents-2026-04-01`) via raw urllib calls (no SDK dependency,
  consistent with orchestrate.py's stdlib-only stance). One worker
  agent per persona, a judgment-only coordinator, polled via
  `threads.list` until all workers report in. Returns the same shape as
  `run_verifiers_parallel` so `_execute_step`'s pass/fail logic is
  unchanged. Only used when `cfg["parallel_backend"] ==
  "managed-agents"` — the existing default path is completely
  untouched. Honest scope: the REST endpoint paths are inferred from
  the documented Python SDK method names, not confirmed against an
  official REST reference. Verified against a mock HTTP server
  reproducing the full call sequence (worker/coordinator creation,
  session creation, event send, threads polling, aggregation,
  environment_id-missing error path, unreported-worker timeout,
  connection failure) — not connected to the real API.
- **Signed provenance via HMAC-SHA256 on accept (#299)**:
  `accept` now writes `.rig/runs/<task_id>/provenance.json`
  (task_type/recipe/base/gate status/checks) signed with a
  locally-generated HMAC-SHA256 key (`.rig/provenance.key`,
  gitignored). `workbench.py verify-provenance <task_id>` checks the
  signature and exits 1 on mismatch or tamper. Scoped deliberately to
  HMAC rather than asymmetric signing (Ed25519/SLSA) to keep
  workbench.py's stdlib-only dependency policy — this gives
  same-machine tamper-evidence, not third-party public verification.
  Documented clearly in code and workbench-ops.md so it isn't mistaken
  for the heavier guarantee. Verified end-to-end in a throwaway repo:
  sign, verify (valid), tamper the record, re-verify (INVALID, exit 1).
- **Gap prescription now drafts a concrete `/rig:forge` request (#268)**:
  `orchestrate runs`' existing hot-gap detection (same recipe+step
  escalating 2+ times) now cross-references that step's recorded
  verdicts to name the top rejecting reviewers, and prints a
  ready-to-paste `/rig:forge "..."` request describing exactly what's
  failing (in addition to the existing `/rig:import --discover`
  suggestion). `orchestrate.py` doesn't invoke forge itself — that
  needs an LLM — it closes the gap between "detected" and "actionable"
  as far as a deterministic script can. Verified via `orchestrate
  selftest` with synthetic `runs.jsonl` data: two escalations on the
  same step with a rejecting reviewer produce a prompt naming that
  reviewer.
- **RBAC for accept and time/cost budget warnings (#282, #281)**:
  `.rig/access.json` (opt-in) restricts `accept` to an allowlist per
  task_type, identity resolved via the `RIG_USER` env var or `git
  config user.name`. Absent file = unrestricted, same as before.
  `--budget-minutes` on `workbench.py new`: `status`/`board` show a
  ⚠ marker past the estimate. Advisory only, never blocks. Both are
  additive and default to today's unrestricted behavior when their
  config is absent. Verified end-to-end in a throwaway repo: the
  budget marker shows in status/board, and RBAC blocks/allows accept
  correctly by identity.
- **Security/quality batch — secret masking, SAST adapter, rescan,
  flaky, observability bridge (#273, #274, #275, #276, #277, #278,
  #279)**:
  - `implement.md`: pre-generation secret scan before subagent
    dispatch, masking existing secrets rather than letting them into
    context.
  - `acceptance-check.md`: documents `no_suspicious_code_similarity`
    and `dependency_license_and_cve_checked` as opt-in criteria
    (enabled via `.rig/gates.json`'s `extra_criteria`), plus
    `sast_findings_clear` tied to the new adapter below.
  - `scripts/sast_adapter.py`: converts Semgrep-style JSON into a
    single worst-case-aggregated acceptance criterion (`workbench.py
    gate` rejects unregistered criterion names, so per-finding checks
    don't fit its model — one aggregate check does). Verified
    end-to-end in a throwaway repo.
  - `skill-import.md`: new `--rescan` mode re-scans already-imported
    bricks against the injection-patterns catalog independent of
    upstream diffs.
  - `verify.md`: distinguishes known-flaky test failures (rerun/CI
    history) from genuine regressions before marking
    `tests_pass_or_explained`.
  - `observability-reviewer.md` + `implement.md`: findings now carry
    concrete instrumentation suggestions, bridged into an implement
    step scoped to this task's diff only (no unrelated-code
    instrumentation sweeps).
- **Slack/Teams webhook notifications (#287)**: `scripts/notify.py`
  posts to Slack/Teams incoming webhooks via urllib only (no SDK
  dependency) — `--format slack|teams`, `--dry-run` to inspect the
  payload without sending, `RIG_NOTIFY_WEBHOOK` env var support.
  Verified against a local HTTP server for both formats plus dry-run
  and the no-webhook error path. Deciding whether/when an event
  warrants a notification stays the caller's job (the instruction
  layer), not this script's.
- **Cross-repository fleet aggregation (#272)**: `orchestrate.py
  fleet --repos p1,p2,...` reads `runs.jsonl` and
  `drill-results.jsonl` from multiple repos read-only, aggregating run
  counts and per-persona detection rate across projects, plus a
  per-repo breakdown to see where a given reviewer persona performs
  better or worse. `--anonymize` swaps repo paths for `repo-N` labels.
  No repo's `.rig/` data is written to.
- **Dogfooding section in the README (#284)**: documents how a
  maintainer measures rig's own gate efficacy with the existing
  `workbench.py digest --period month` / `stats` / `/rig:drill
  --replay` commands — no new tooling. Honest scope note: this repo
  doesn't auto-publish those numbers (no CI job regenerating a badge
  on merge); today "dogfooding" means running the commands locally.
- **Talk-mode structured logging and deja-vu detection (#292, #290)**:
  `talk-loop.md` step 7 captures decisions/confirmed-assumptions/
  open-questions from the requirement negotiation into `talk-log.md`
  (an unapproved log, same tier as `diff.md`) once a task-id exists.
  `workbench.py new`'s `find_similar_tasks()` scores past task inputs
  by Jaccard overlap on a rough tokenization (no embeddings/search
  engine), surfacing a "Similar tasks" section in the routing banner
  above a similarity threshold. Verified: a paraphrased duplicate task
  is caught, an unrelated task isn't.
- **Multi-recipe A/B experiment mode and streaming-gate guidance
  (#291, #302)**: `orchestrate.py ab <recipe1> <recipe2>...` runs the
  same goal through multiple recipe variants concurrently
  (ThreadPoolExecutor), each in its own isolated worktree via the
  existing `setup_isolation`/`teardown_isolation` path so variants
  never conflict. Reports elapsed time, retry count, and final status
  per variant; incomplete/dirty variants keep their worktree for
  inspection, same rule as `--isolate`. `implement.md` gains an
  opt-in note on streaming lightweight checks (type/lint only) at
  natural checkpoints during large (size L/XL) implementations, to
  reduce end-of-verify pileup — final pass/fail still comes from the
  normal acceptance-gate.
- **GitHub Action for headless CI usage (#265)**: `action.yml`
  (composite) wraps `orchestrate.py run --isolate` for workflows
  without a live Claude Code session. `scripts/rig-action-entrypoint.sh`
  derives the final status from the run-state JSON (`done`/`stopped`
  fields, the same logic `orchestrate.py` itself uses) and only pushes
  a branch + opens a PR via `gh pr create` when the gate resolved
  `DONE` — a failing or pending gate fails the job and creates
  nothing. Honest scope: verified the `run` path end-to-end locally
  with `--provider mock`; the `open-pr` path (branch push + `gh pr
  create`) needs a live GitHub Actions runner and isn't exercised
  here.
- **Static threat scan for rig's own MCP tools (#303)**: adds
  `orchestrate.py mcp-scan [--json]`, which statically analyzes
  `scripts/mcp_server.py`'s TOOLS definitions across three adversarial
  lenses (attacker/defender/auditor) for shell/network
  over-permission, plaintext secret exposure, and hook-injection risk.
  Never executes anything — reads the TOOLS dict and source text only,
  deterministic, no side effects. Module-level findings cover the
  shared subprocess/secret path; tool-level findings classify each
  tool as read/write and flag `rig_orchestrate_run` as MEDIUM severity
  since it can affect the main working tree directly when `--isolate`
  isn't set — with the concrete mitigation (`isolate: true`) spelled
  out. `validate.py`'s new `check_mcp_scan()` wires the overall
  verdict into CI (HIGH->FAIL, MEDIUM->WARN, LOW->PASS), silently
  skipping when `mcp_server.py` isn't present.
- **AST-based semantic diff summary for Python (#280)**:
  `scripts/ast_diff.py` compares Python source with the stdlib `ast`
  module (top-level/class-level def/class comparison) to distinguish
  signature changes, body-only changes, additions/removals, and
  cosmetic-only edits (identical AST despite differing text).
  `workbench.py diff` now inserts a "Semantic diff (Python)" section
  for Modified `*.py` files, augmenting rather than replacing
  `diff.md`'s prose Summary. Non-Python/unparseable files simply don't
  get this section and fall back to the existing text diff.
- **Confidence-weighted gate via drill detection rate (#301)**: new
  `workbench.py confidence [<task_id>]` surfaces drill-measured
  detection rate per reviewer as a supplementary signal alongside the
  existing pass/fail gate — task-scoped calls record
  `reviewer_confidence` into `acceptance.json` without touching gate
  logic itself. Below a 70% threshold it's flagged low-confidence and
  an additional reviewer is suggested, never auto-dispatched.
  Unmeasured personas stay "unmeasured" rather than a fabricated score.
  `aggregate_drill_confidence()` is a shared, pure aggregation function
  so nothing re-derives it independently.
- **Fable 5 refusal-classifier and server-side fallback handling
  (#297)**: a new `anthropic` provider (`run_anthropic_provider`) calls
  the Anthropic Messages API directly over HTTP — the `claude`/`rig`
  CLI providers use `--output-format text` and never expose a
  structured `stop_reason`, so this is a separate code path. On
  `stop_reason: "refusal"` with no fallback content block, records
  `FABLE_REFUSAL` (category/explanation) in state history and returns
  rc=1 (not a silent failure). When a `{"type": "fallback"}` content
  block is present (the `server-side-fallback-2026-06-01` beta
  succeeded), records `FABLE_FALLBACK` and treats the step as a normal
  success — the gate is not blocked. Usage
  (input/output/`cache_read_input_tokens`) is normalized into the
  existing #271/#296 token_usage accumulator and surfaced in `runs
  --cost`, alongside a fallback/refusal occurrence count.
  `agents/security-reviewer.md` and `commands/orchestrate.md` now warn
  that assigning Fable 5 to attack-technique-focused personas via
  `--step-model` (#293) requires setting `fallback_model`. Honest
  scope: verified against a mock HTTP server reproducing the Anthropic
  Messages API's response shape — not connected to the real Anthropic
  API (billing/live-traffic risk).
- **Host adapter layer generalizing native-layer integration beyond
  Codex (#304)**: `scripts/host_adapters.py` centralizes per-host
  differences (hook event names, skill path conventions, capability
  matrix, degrade behavior) into a single `HOSTS` dict, so adding a new
  host means adding one entry rather than touching rig's core. Cursor
  was added as the second host to validate the design (researched
  against Cursor's official docs): hook event names are camelCase
  (`PreCompact` -> `preCompact`), Cursor reads `.agents/skills/` for
  legacy compatibility so Codex's existing `SKILL.md` works unmodified,
  and `preCompact` is documented as observational-only — `cursor/hooks.json`
  declares this as an honest degrade instead of pretending it works.
  Claude Code's and Codex's existing files are unchanged; the adapter
  only maps to what's already shipped.
- **Codex CLI native-layer integration (#294)**: `codex/skills/rig/SKILL.md`
  (Codex's `.agents/skills/<name>/SKILL.md` convention, a thin pointer to
  the existing `workbench.py`/`orchestrate.py` — no new engine),
  `codex/hooks.json` (`PreCompact` wired to the existing
  `hooks/preserve-rig-state.sh`, reused as-is), and
  `.codex/agents/security-reviewer.toml` (a Codex-native subagent
  mirroring `agents/security-reviewer.md`'s review axes and output
  contract, with `sandbox_mode = "read-only"` layered on top of
  `orchestrate.py`'s existing argv-level read-only enforcement — defense
  in depth, not a replacement). Honest scope: this environment has no
  codex CLI, so none of it has been exercised live — hooks.json validates
  as JSON and the TOML parses with `tomllib` using only documented
  fields, but actual skill loading, hook firing, sandbox enforcement, and
  MCP connection are unverified. The existing stateless `--provider
  codex` path is untouched.
- **Production outcome feedback loop (#289, #300)**: `accept` lands a
  staged diff, so workbench never sees the final commit SHA a human
  creates. `workbench.py record-commit <task_id> [<sha>]` links
  task_id -> sha explicitly. `record-outcome <task_id> --status
  ok|incident` logs what actually happened in production — the
  real-world counterpart to drill's synthetic detection rate.
  `trace-commit <sha>` reverse-looks-up a sha to its task, shows the
  original gate prediction plus any recorded outcome, and drafts a
  revert plan (command + PR title/body) when the outcome is
  "incident" — it never creates the PR or runs the revert itself,
  that stays a human/GH-tool step.
- **Learned auto-router from historical run data (#305)**:
  `learned_auto_route()` aggregates `.rig/runs.jsonl`'s track record
  (which model actually got used per recipe/step, and did the step
  pass) and picks the cheapest static `--auto-route` (#264) candidate
  meeting a pass-rate/sample threshold — frequency-based, no ML model.
  Defaults to shadow mode: predictions are always recorded
  (`LEARNED_ROUTE_PREDICTION` in history, `steps[].learned_route` in
  telemetry) but only applied when `--auto-route-mode active` is set.
  Insufficient samples or low pass rate falls back to the static
  auto-route, with every rejected candidate and its reason recorded
  (counterfactuals) so the choice stays auditable.
  `--exploration-pct`/`--exploration-date` let a deterministic fraction
  of runs try the next-cheapest candidate (hash-based, no randomness).
  Honest scope: regret logging (auto-calibrating "too cheap"/"too
  expensive" picks) is not implemented.
- **Cost-tier auto-routing (#264)**: recipe steps can declare
  `auto_route.candidates` (`{model, cost_tier, max_size}`, cheapest
  first). `orchestrate.py run --auto-route` resolves the first candidate
  whose `max_size` covers the measured diff size (reusing the existing
  `size_class`/`git_diff_lines`/manifest machinery), falling back to the
  most capable candidate if none fit. It's a fallback only — runtime
  `--step-model` and the recipe's own `model:` both still win outright.
  The decision is recorded in run-state history and `runs.jsonl`'s
  `steps[].auto_route`. `resolve_auto_route()` is a pure, tested function
  proving determinism (same input -> same choice).
- **MCP server (#263)**: `scripts/mcp_server.py` implements a minimal MCP
  stdio transport (JSON-RPC 2.0, line-delimited) without depending on the
  `mcp` SDK, matching workbench.py/orchestrate.py's stdlib-only stance. It
  exposes 14 tools (`rig_task_*`, `rig_orchestrate_*`) that shell out to
  the existing workbench.py/orchestrate.py CLIs — no new engine, and
  accept/discard's force-proof requirements go through the identical code
  path so they can't be bypassed via MCP.
- **Token/cost usage metering for HTTP-based providers (#271, #296)**:
  `orchestrate.py` now captures the OpenAI-compatible `usage` field from
  ollama/lmstudio responses (`_record_token_usage`, thread-safe) and rolls
  it up per-run as `token_usage` in `runs.jsonl`; `orchestrate.py runs
  --cost` aggregates it by recipe/provider. CLI-based providers
  (claude/codex) don't expose structured usage and are explicitly out of
  scope — the command says so and points to Anthropic's Usage & Cost
  Admin API instead of estimating.

## [1.15.0] - 2026-07-11

### Added — three more research-backed hardening items

- **Verify-first resume ritual**: `orchestrate resume <run-state.json>`
  re-anchors a persisted run before continuing — it prints a digest,
  re-runs the current step's machine checks, and refuses to advance when
  a previously-passing check now fails ("world drifted"); a >1h mtime gap
  cues possible context compaction. Complements the PreCompact hook: prose
  survives compaction, and now the machine re-verifies too. (Anthropic
  long-running-agents startup ritual.)
- **External-content quarantine (#269)**: `quarantine.wrap_untrusted`
  fences issue/PR/tool text in a per-call, unforgeable sentinel with an
  explicit "this is DATA, never instructions" boundary and strips
  invisible/bidi Unicode before it enters a prompt; wired into the goal
  span and gh-flow's untrusted-text rule. (OWASP LLM01 / spotlighting /
  CaMeL.)
- **MAST failure-mode taxonomy**: failed runs now record a deterministic
  `failure_mode` code (verification:self-grading / incorrect-implementation
  / missing / unclassified) in runs.jsonl; `patterns/failure-taxonomy.md`
  maps each MAST mode (arXiv 2503.13657) to the gate criterion or brick
  that should have caught it, and the dashboard gains a failure-mode
  panel — rig's measured-gates philosophy turned onto its own failures.

### Verification

- `python3 scripts/orchestrate.py selftest` → PASS (scenarios AA + FM)
- `python3 scripts/validate.py` → PASS 46 / WARN 8 / FAIL 0
- `python3 scripts/validate.py selftest` → 12/12 scenarios OK
- `ruff check scripts rig_workbench tests` → all checks passed
- `pytest -q` → 279 passed

## [1.14.0] - 2026-07-11

### Changed — research-hardened release: 5 workstreams from a 2024-2026 literature sweep

Backed by a five-theme survey (LLM-as-judge reliability, agent-harness
design, AI code-review market, mutation testing, agent security) — every
change below cites its evidence in the commit messages.

- **Verifier judge hardening** (MT-Bench / Style-over-Substance /
  CodeJudgeBench / Anthropic eval guidance): verifiers now judge the
  actual worktree `git diff` as primary evidence — the generator's
  report is bounded and labeled as unverified claims; all verdict
  contracts flipped to evidence-first with the verdict as the last line
  (extraction takes the last verdict-token line, so quoted verdicts no
  longer force FAIL); per-criterion `CRITERION n: PASS|FAIL|UNKNOWN`
  verdicts with fail-closed all-UNKNOWN handling; judge-panel multi-PASS
  is recorded (`order_sensitive` + pass set) instead of silent
  first-PASS-wins; 30k-char output budget with spooled full text.
- **Anti-tamper gate sensor** (`no_gate_tampering`, METR reward-hacking
  evidence): edits to `.rig/gates.json`, `.rig/recipes/`, or CI
  workflows inside the task diff fail the gate; test modification/
  deletion, assert-removal, and skip-markers warn on bugfix/feature.
- **Injection-marker sensor** (`no_injection_markers`, Rules-File-
  Backdoor evidence): invisible/bidi Unicode fails (rendered only as
  U+XXXX escapes), instruction-override phrases warn; scans the diff
  plus repo prose surfaces; `scan-injection` standalone subcommand.
- **Manifest consent gate**: `.claude/rig.md` (repo-controlled, drives
  hook-eval'd commands and recipe search tiers) now uses the recipe
  trust store — soft-degrade to "no manifest" when untrusted; the git
  hooks verify the hash before eval; `githooks install` records consent.
- **Drill science** (selective-mutation literature): clean no-bug
  control diffs measure per-persona `clean_fp_rate`; finding-verifier
  screens seeds for the equivalent-mutant problem (`invalid_seeds`);
  seed catalog gains CWE/ODC provenance and 8 rows (XSS, path traversal,
  hard-coded secret, deserialization, missing authn, resource
  exhaustion, off-by-one, TOCTOU); Wilson 95% intervals for n<10 and
  history-aggregated persona-update triggers.
- **Review market mechanisms** (Bugbot dismissal-learning 52->80%,
  Anthropic Code Review knobs): `.rig/review-suppressions.jsonl`
  records verifier-refuted findings as injectable non-issues (an UPHELD
  finding always beats a suppression); severity-gated comment policy
  (nit cap 5 + rollup, Pre-existing marker, Important-only re-reviews).

### Verification

- `python3 scripts/orchestrate.py selftest` → PASS (incl. new scenario Y)
- `python3 scripts/validate.py` → PASS 46 / WARN 8 / FAIL 0
- `python3 scripts/validate.py selftest` → 12/12 scenarios OK
- `ruff check scripts rig_workbench tests` → all checks passed
- `pytest -q` → 241 passed

## [1.13.0] - 2026-07-11

### Added — issue-backlog sweep: 6 features from the roadmap triage

- **Project-level custom gate criteria** (#283): `.rig/gates.json` lets a repo
  add criteria to gate presets/task types — additive only (removal-shaped keys
  are rejected as a security posture), hard-erroring on typos before any run
  state exists. Project criteria carry a `[project]` tag in status/gates.
- **OpenAPI schema-diff sensor** (#288): `public_api_changes_documented` is now
  machine-backed — base-vs-worktree operation diff (paths/methods/params/
  responses, stdlib-only), warning-grade when diff.md is silent about a
  changed API, clean skip when no schema exists.
- **Deterministic secret scanner** (#273, scanner core): `workbench.py
  scan-secrets [paths|--diff <task-id>]` detects AWS/PEM/GitHub/Slack/OpenAI/
  Anthropic/Google/JWT patterns plus an entropy heuristic with a lockfile
  allowlist; findings are always masked. The gate sensor fails
  `no_secret_leak` on findings in the task diff (untracked files included);
  `--set no_secret_leak=passed` is the recorded escape hatch.
- **Git-hook distribution of machine sensors** (#298): `rig-wb githooks
  install|uninstall|status` ships signed pre-commit (manifest lint + staged
  secret scan) and pre-push (build + test) hooks; foreign hooks are never
  overwritten without --force; `RIG_HOOK_SKIP*` env bypasses per check.
- **Telemetry digest** (#285): `workbench.py digest [--period week|month]`
  renders a Markdown digest (runs, gate pass/fail + most-failed criteria,
  force accepts, rubber-stamp suspects, drill detection rate), reusing the
  stats helpers.
- **Per-step model override** (#293): `orchestrate run ... --step-model
  <step-id>=<model>` (repeatable); precedence runtime > recipe `model:` >
  `--model`; unknown step ids abort pre-run; the actually-used model is
  recorded in run-state and runs.jsonl for cost attribution.
- **Drill coverage check + gate-efficacy panels** (#266, scoped): the
  validator now WARNs for gate-bearing shipped recipes whose reviewers
  /rig:drill cannot exercise (16 flagged today); the dashboard gains a
  detection-rate sparkline and a per-criterion gate-failure table.
- **Positioning docs** (#267) and **gc/audit routing** (#261, #262): the
  implemented-but-unroutable workbench subcommands are wired into the command
  docs; §1 documents rig's thin-layer + external-control-plane positioning.

### Housekeeping

- Closed 20 stale auto-filed issues (#213-#250 range) after verifying each
  against the current code with file:line evidence — all had been implemented
  between v0.97.0 and v1.12.0.

### Verification

- `python3 scripts/orchestrate.py selftest` → PASS (96 [OK] incl. scenario Z)
- `python3 scripts/validate.py` → PASS 46 / WARN 8 / FAIL 0 (8 WARNs = new
  drill-coverage findings, intentionally surfaced)
- `python3 scripts/validate.py selftest` → 12/12 scenarios OK
- `ruff check scripts rig_workbench tests` → all checks passed
- `pytest -q` → 158 passed

## [1.12.0] - 2026-07-10

### Changed — the remaining self-application debt from 1.11.0

- **workbench.py and validate.py split**: both monoliths (1,195 and 975 lines)
  now follow the same package pattern as orchestrate — `scripts/*.py` are
  21-line shims; implementations live in `rig_workbench/workbench/` (six
  modules) and `rig_workbench/validation/` (eight modules). Outputs verified
  byte-identical, including an end-to-end scratch-repo accept/discard flow.
- **CHANGELOG slimmed**: pre-1.10 entries (111 sections, ~197KB) moved to
  `docs/CHANGELOG-archive.md`; the top-level file keeps the current line.

### Fixed

- **skills-lock provenance**: all 18 hyperframes imports now record
  `importedAs: facets/instructions/render-hyperframes.md` + `mode: delegate`,
  per the family-level delegation documented in skill-import.md §3 and
  CHANGELOG v0.36.0. Resolves the 18 standing validate WARNs — the validator
  now reports WARN 0.
- **API contracts** (found while writing the 1.11.0 test suite):
  importing `recipes.py` without PyYAML no longer exits at import time;
  the cross-project telemetry mirror path is `config.GLOBAL_RUNS_PATH`
  (rebindable instead of an unavoidable `~/.rig` write); `plan --json`
  exits 1 on plan errors like the non-JSON path; `queue_set_status`
  returns whether the item was found instead of silently no-oping.

### Verification

- `python3 scripts/orchestrate.py selftest` → PASS (90 [OK])
- `python3 scripts/validate.py` → PASS 45 / WARN 0 / FAIL 0
- `python3 scripts/validate.py selftest` → 9/9 scenarios OK
- `ruff check scripts rig_workbench tests` → all checks passed
- `pytest -q` → 58 passed

## [1.11.0] - 2026-07-10

### Changed — self-application release: the quality bar rig sells now applies to rig itself

- **Monolith split**: `scripts/orchestrate.py` (2,781 lines) is now a 21-line
  compatibility shim; the implementation lives in `rig_workbench/orchestrate/`
  as ten cohesive modules (config / recipes / runstate / providers / isolate /
  queueing / graph / selftest / commands / cli). Selftest output is
  byte-identical to the pre-split baseline.
- **English everywhere in code**: all CLI output, comments, docstrings, help
  text, installer messages, CI workflow comments, and hook-injected directives
  are now English. The Japanese review-verdict protocol token and
  full-width-colon condition regexes are preserved as escaped literals — they
  are live wire-format contracts.
- **Main entry renamed to `/rig:go`**: `/rig:rig` remains as a compatibility
  alias. Experimental commands (magi, sage, roast, coin, duck, pre-mortem,
  party, movie, scenario) are now marked `[experimental]` in their
  descriptions. `plugin.json`'s description shrank from 2,192 to 384 chars.

### Added

- **Project-recipe trust gate**: recipes under `<cwd>/.rig/recipes/` (which can
  overlay shipped recipes and whose `checks:` run as shell commands) now
  require one-time explicit consent — `--allow-project-recipes` or
  `RIG_ALLOW_PROJECT_RECIPES=1` — recorded as a content hash in
  `~/.claude/rig/trusted-recipes.json` (`RIG_TRUST_STORE` overrides). An
  edited file re-requires consent. Covers name resolution, explicit overlay
  paths, and `extends`-chain parents.
- **pytest suite**: 54 unit tests (`tests/`) covering recipe resolution,
  run-state/gate transitions, queue backends, brick-graph shape, CLI smoke,
  and the trust gate — all asserts on machine tokens, sandboxed via tmp_path.
- **CI hardening**: `validate.yml` now also runs `ruff check` (0 findings,
  down from 65) and the pytest suite. `validate.py` gained a version-sync
  check across plugin.json / pyproject.toml / `rig_workbench/__init__.py`.

### Verification

- `python3 scripts/orchestrate.py selftest` → PASS (90 [OK], byte-stable)
- `python3 scripts/validate.py` → PASS 45 / WARN 18 / FAIL 0
- `python3 scripts/validate.py selftest` → 9/9 scenarios OK
- `ruff check scripts rig_workbench tests` → all checks passed
- `pytest -q` → 54 passed

## [1.10.6] - 2026-07-08

### Fixed — verifier が review-verdict 契約を解釈できるようにした

- **`scripts/orchestrate.py` の verifier パースを両契約対応に**：`_verdict_ok` を新設し、machine verdict (`VERDICT: PASS/FAIL`) に加えて review-verdict contract (`判定: APPROVE / APPROVE_WITH_CONDITIONS / REJECT`) を正しく解釈するようにした。これまで reviewer 系ペルソナ（security-reviewer / design-reviewer / test-reviewer 等）の判定が machine verdict しか見ていない grep で全て FAIL 扱いになっていた不具合を解消。`_build_verify_prompt` も「最後の1行だけに `VERDICT:` を出す」よう厳格化してパース揺れを減らした。
- **`resolve_http_model` の endpoint 不整合**：`--base-url` 明示時に、別 endpoint の保存 default が返ってしまう不具合を修正。endpoint が一致するときだけ保存 default を使う。
- **`build_argv` の codex verifier で `--sandbox read-only` を二重指定していたのを削除**：上流で既に `read-only` を明示しているので `_READONLY_ENFCE` の追加は冗長だった。
- **`run_provider` は非0 exit 時に stderr もマージして返す**：verifier note に原因が残るようにしてデバッグ性を上げた。
- **`cmd_selftest` の期待値を更新**：codex generator が `workspace-write` サンドボックスを明示するようになった状態に selftest を追従。

### Added — `max-bugfix` の acceptance を機械チェックで締める

- **`max-bugfix` recipe の `acceptance` に `checks:` を追加**：`.py` diff 存在確認、`git diff --check`、`pytest` を機械強制。LLM の受け入れ判定に加えて計算的センサーで締めるようにした。

### Verification

- `python3 scripts/validate.py` → PASS
- `python3 -m compileall scripts/orchestrate.py` → PASS
- `python3 scripts/orchestrate.py selftest` → PASS

## [1.10.5] - 2026-07-07

### Added — mock benchmark の再現性と retry 学習の改善

- **`scripts/orchestrate.py` の prompt に retry 文脈を注入**：`attempt`、直近の `history`、前回失敗理由を step prompt に含めるようにした。再試行が同じ指示の繰り返しになりにくくなり、`max-bugfix` での収束が安定する。
- **`mock` provider を task-aware な oracle として拡張**：`implement` step では task ごとに対象ファイルへ実際の修正を入れるようにし、`max-bugfix` の built-in mock benchmark で `spec=PASS` を再現可能にした。

### Verification

- `python3 scripts/validate.py` → PASS
- `python3 -m compileall scripts/orchestrate.py` → PASS
- `python3 -m rig_workbench.cli bench --provider mock --mode both --rig-recipe max-bugfix --tasks all --runs 1 --max-steps 14 --out /tmp/rig-max-bugfix-both-recheck.json --html /tmp/rig-max-bugfix-both-recheck.html` → `rig` 側が全タスク `spec=PASS` を再現

## [1.10.4] - 2026-07-07

### Added — retry 文脈を prompt に注入

- **`scripts/orchestrate.py` の implement/test 再試行を強化**：各 step の `attempt`、直近の `history`、前回失敗理由を prompt に含めるようにして、再試行が同じ文面の繰り返しにならないようにした。`fast-bugfix` / `max-bugfix` で no-op を止めた後、次の改善点である「失敗から学ぶ」層を足す。

### Verification

- `python3 scripts/validate.py` → PASS
- `python3 -m compileall scripts/orchestrate.py` → PASS
- `python3 -m rig_workbench.cli bench --provider mock --mode rig --rig-recipe max-bugfix --tasks divide-by-zero --runs 1 --max-steps 4 --out /tmp/rig-max-bugfix-rerun2.json --html /tmp/rig-max-bugfix-rerun2.html` → `calls=2` で no-op 防止が維持されることを確認

## [1.10.3] - 2026-07-07

### Added — `max-bugfix` の shipped 追加

- **`max-bugfix` recipe を追加**：`bugfix` を土台に、`implement` で diff と `git diff --check` を強制し、`test` で `pytest` を強制する、より堅い修正フローを shipped した。`fast-bugfix` は速さ優先のまま残し、確実性を最優先したいときの選択肢を分けた。
- **`skills/rig` の目録を更新**：dev-core recipe を 8 件に更新し、新しい強い既定を catalog に載せた。

### Verification

- `python3 scripts/validate.py` → PASS
- `python3 -m compileall scripts rig_workbench` → PASS
- `python3 -m rig_workbench.cli bench --provider mock --mode rig --rig-recipe max-bugfix --tasks all --runs 1 --max-steps 4 --out /tmp/rig-max-bugfix-mock-all.json --html /tmp/rig-max-bugfix-mock-all.html` → `calls=2` で `inspect→reproduce` まで進み、`implement` の no-op は素通りしないことを確認

## [1.10.2] - 2026-07-07

### Fixed — fast-bugfix の no-op 実行と gate 判定

- **`fast-bugfix` に step-level checks を追加**：`implement` は `.py` の差分が出ないと合格しない、`test` は `pytest` を実行しないと合格しないようにした。これで「読んで終わり」の空振りを止める。
- **`scripts/orchestrate.py` の gate 判定を修正**：`gate` が無い step でも `checks` を優先して評価するようにし、計算的センサーを持つ step が素通りしないようにした。

### Verification

- `python3 scripts/validate.py` → PASS
- `python3 -m compileall scripts/orchestrate.py` → PASS
- `python3 -m rig_workbench.cli bench --provider mock --mode rig --rig-recipe fast-bugfix --tasks all --runs 1 --max-steps 4 --out /tmp/rig-fast-bugfix-mock-final.json --html /tmp/rig-fast-bugfix-mock-final.html` → `calls=1` まで落ち、implement の no-op が停止することを確認

## [1.10.1] - 2026-07-07

### Added — bench と shipped recipe の強化

- **`rig-wb bench` に leak 検出を追加**：bare / rig の両モードについて、実行前後の git status を比較して task 外の変更を `workspace_leaks` として記録するようにした。`--leak-check-root` で比較対象ルートを切り替えられる。
- **`rig-wb bench` の rig 実行を scratch cwd 対応に整理**：Codex / Claude の provider 呼び出しを scratch task ディレクトリで走らせ、rig 側は repo root を `PYTHONPATH` に足して `python -m rig_workbench.cli` を scratch から呼べるようにした。これで bare vs rig の比較条件を揃えやすくした。
- **`fast-bugfix` recipe を shipped 追加**：小粒バグ修正用の軽量フローを足し、`skills/rig` の目録にも反映した。既存の heavy な dev-flow ではなく、implement → test → acceptance だけに絞った最短経路の入口。
- **`scripts/orchestrate.py` の step 契約を強化**：implement / test / acceptance で期待する行動と報告項目を分け、少なくとも「読むだけ」で終わりにくい prompt にした。Codex generator には `workspace-write` sandbox を明示し、実際にファイルを書ける前提を揃えた。

### Verification

- `python3 scripts/validate.py` → PASS
- `python3 /home/itoshun/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/rig` → PASS
- `python3 -m compileall rig_workbench scripts` → PASS
- `python3 -m rig_workbench.cli bench --provider codex --mode rig --rig-recipe fast-bugfix --tasks divide-by-zero --runs 1 --max-steps 4` → runner exit 0 / leaks 0 / task 側の修正は未達（次の改善対象）

## [1.10.0] - 2026-07-07

### Added — `rig-wb bench` のタスク拡張と HTML dashboard

- **組み込み bench task を 4 件へ拡張**：既存の `divide-by-zero` / `order-dedup` に加えて、security 観点の `sql-inject` と refactor 観点の `dry-refactor` を追加。単純な test pass だけでは拾いにくい SPEC 準拠を測る。
- **`--max-steps` 既定を 14 に変更**：bugfix recipe が review / acceptance 側まで届きやすい設定に寄せた。旧既定 7 は実装途中で切れやすかった。
- **`--html <path>` を追加**：bench 結果 JSON から単一 HTML dashboard を生成。平均 elapsed / calls / test pass 率 / spec pass 率と、task 別の bare vs rig 比較表を表示する。外部依存なし。
- **Codex skill としての入口を追加**：`skills/rig` を `~/.codex/skills/rig` から読ませる運用を明記し、`$rig` を `/rig:rig` 相当として使えるよう `SKILL.md` / `agents/openai.yaml` / README を更新。

### Verification

- `python3 /home/itoshun/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/rig` → PASS
- `python3 /home/itoshun/.codex/skills/.system/skill-creator/scripts/quick_validate.py /home/itoshun/.codex/skills/rig` → PASS
- `python3 scripts/validate.py` → PASS 41 / WARN 18 / FAIL 0
- `python3 -m compileall rig_workbench scripts` → PASS
- `python3 -m rig_workbench.cli bench --mode bare --provider mock --tasks all --out /tmp/rig-bench-1.10.0.json --html /tmp/rig-bench-1.10.0.html` → 4/4 tasks test PASS / spec PASS

---

Older entries (1.9.0 and earlier) live in [docs/CHANGELOG-archive.md](./docs/CHANGELOG-archive.md).
