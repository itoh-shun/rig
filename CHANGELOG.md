# Changelog

rig の変更履歴。バージョンは `.claude-plugin/plugin.json` に対応。
形式は [Keep a Changelog](https://keepachangelog.com/) に準拠（日付は JST）。

> リリースタグは GitHub 側で発行する（実行環境の都合でタグ push を別途行う運用）。

## [Unreleased]

### Added

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
