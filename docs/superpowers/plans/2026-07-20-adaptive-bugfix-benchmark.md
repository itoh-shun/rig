# Adaptive Bugfix Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in adaptive bugfix workflow and a fair, repository-shaped A/B benchmark that demonstrates quality gains within an average 2.5x LLM-call budget.

**Architecture:** A deterministic diff-risk analyzer selects one primary reviewer and an optional second high-risk reviewer. The existing orchestration runner gains explicit no-generator executors for risk assessment, targeted review, and mechanical acceptance, while the benchmark is split into corpus loading, provider execution, scoring, and reporting modules.

**Tech Stack:** Python 3.10+, PyYAML, pytest, stdlib subprocess/pathlib/json/html, existing Markdown recipe frontmatter, small Python and TypeScript fixture repositories.

## Global Constraints

- Keep `bugfix` and `fast-bugfix` behavior unchanged.
- Ship `adaptive-bugfix` as opt-in; do not change default routing.
- Use at least 10 repository-shaped tasks with at least 3 runs per task for a valid real-provider result.
- Pass only when rig reduces silent defects by at least 50%, safe stops are at most 20% of valid rig runs, and average rig calls are at most 2.5x bare calls.
- Report each provider/model separately; never pool providers.
- CI must not make paid LLM calls.
- Bare and rig arms receive the same goal, initial repository, public tests, model, and writable scratch permissions.
- Hidden checks must never be copied into either agent workspace.
- Unknown or failed states must never become `clean_pass`.

---

### Task 1: Deterministic Diff-Risk Analysis

**Files:**
- Create: `rig_workbench/orchestrate/adaptive.py`
- Create: `tests/test_adaptive_risk.py`

**Interfaces:**
- Produces: `RiskSignal(domain: str, severity: int, evidence: str)`
- Produces: `RiskAssessment(primary: str, secondary: str | None, signals: tuple[RiskSignal, ...], fallback_reason: str | None)`
- Produces: `analyze_diff(diff: str, changed_files: list[str]) -> RiskAssessment`
- Produces: `invocation_limit(assessment: RiskAssessment) -> int`

- [ ] **Step 1: Write failing risk-classification tests**

```python
from rig_workbench.orchestrate.adaptive import analyze_diff, invocation_limit


def test_authorization_change_selects_security():
    result = analyze_diff(
        "+ if current_user_id != requested_user_id:\n+     return None\n",
        ["profile_service.py"],
    )
    assert result.primary == "security-reviewer"
    assert any(s.domain == "security" and "requested_user_id" in s.evidence
               for s in result.signals)
    assert invocation_limit(result) == 3


def test_api_and_test_change_selects_two_high_risk_lenses():
    result = analyze_diff(
        "+ app.get('/v2/users', handler)\n+ describe('compat', () => {})\n",
        ["src/api.ts", "tests/api.test.ts"],
    )
    assert result.primary == "design-reviewer"
    assert result.secondary == "test-reviewer"
    assert invocation_limit(result) == 4


def test_unknown_change_falls_back_closed():
    result = analyze_diff("+opaque\n", ["data.unknown"])
    assert result.primary == "test-reviewer"
    assert result.fallback_reason == "no recognized risk signals"
```

- [ ] **Step 2: Run tests and verify the missing-module failure**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_adaptive_risk.py`

Expected: collection fails with `ModuleNotFoundError: rig_workbench.orchestrate.adaptive`.

- [ ] **Step 3: Implement immutable result types and deterministic signal rules**

```python
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class RiskSignal:
    domain: str
    severity: int
    evidence: str


@dataclass(frozen=True)
class RiskAssessment:
    primary: str
    secondary: str | None
    signals: tuple[RiskSignal, ...]
    fallback_reason: str | None = None


_RULES = (
    ("security", 3, re.compile(
        r"\b(auth|owner|permission|requested_user|current_user|sql|secret|token|"
        r"eval|exec|subprocess|shell)\b", re.I)),
    ("design", 2, re.compile(
        r"\b(api|route|schema|migration|dependency|config|public|export)\b", re.I)),
    ("test", 2, re.compile(
        r"\b(test|assert|boundary|error|exception|state|rollback|retry)\b", re.I)),
)
_PERSONAS = {
    "security": "security-reviewer",
    "design": "design-reviewer",
    "test": "test-reviewer",
}


def analyze_diff(diff: str, changed_files: list[str]) -> RiskAssessment:
    haystack = "\n".join([*changed_files, diff])
    signals = tuple(
        RiskSignal(domain, severity, match.group(0))
        for domain, severity, pattern in _RULES
        if (match := pattern.search(haystack))
    )
    if not signals:
        return RiskAssessment("test-reviewer", None, (), "no recognized risk signals")
    ranked = sorted(signals, key=lambda s: (-s.severity, s.domain))
    primary = _PERSONAS[ranked[0].domain]
    secondary = next(
        (_PERSONAS[s.domain] for s in ranked[1:]
         if s.severity >= 2 and s.domain != ranked[0].domain),
        None,
    )
    return RiskAssessment(primary, secondary, tuple(ranked))


def invocation_limit(assessment: RiskAssessment) -> int:
    return 4 if assessment.secondary else 3
```

- [ ] **Step 4: Add path-sensitive rules and stable JSON serialization**

Add `to_dict()` methods to both dataclasses. Treat changed test files as test
risk, dependency manifests and exported declarations as design risk, and
authentication/storage/query paths as security risk. Sort signals by severity,
domain, then evidence so identical diffs always serialize identically.

- [ ] **Step 5: Run focused tests**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_adaptive_risk.py`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add rig_workbench/orchestrate/adaptive.py tests/test_adaptive_risk.py
git commit -m "feat: add deterministic adaptive risk analysis"
```

### Task 2: Adaptive Orchestration Executors and Recipe

**Files:**
- Modify: `rig_workbench/orchestrate/recipes.py`
- Modify: `rig_workbench/orchestrate/providers.py`
- Modify: `rig_workbench/orchestrate/runstate.py`
- Create: `skills/rig/recipes/adaptive-bugfix.md`
- Create: `tests/test_adaptive_run.py`
- Modify: `tests/test_recipes.py`

**Interfaces:**
- Consumes: `analyze_diff()`, `invocation_limit()`
- Adds recipe step field: `executor: generate | risk-assess | targeted-review | checks-only`
- Adds run-state fields: `adaptive.assessment`, `adaptive.invocation_limit`, `adaptive.invocations`
- Produces: `execute_adaptive_review(...) -> list[dict]`
- Produces: `execute_informed_repair(...) -> bool`

- [ ] **Step 1: Write failing recipe and state tests**

```python
def test_load_steps_preserves_executor(write_recipe):
    path = write_recipe("adaptive", """---
name: adaptive
steps:
  - id: assess
    instruction: adaptive-assess
    executor: risk-assess
---""")
    assert resolve_plan_json(path)["steps"][0]["executor"] == "risk-assess"


def test_new_state_initializes_adaptive_budget(step_factory):
    state = new_state("adaptive-bugfix", [step_factory(id="implement")], "fix")
    assert state["adaptive"] == {
        "assessment": None,
        "invocation_limit": 3,
        "invocations": 0,
    }
```

- [ ] **Step 2: Run focused tests and verify failures**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_adaptive_run.py tests/test_recipes.py`

Expected: failures show missing `executor` and `adaptive` fields.

- [ ] **Step 3: Preserve `executor` in recipe loading and state**

In `load_steps`, add:

```python
"executor": s.get("executor") or "generate",
```

In `new_state`, add:

```python
"adaptive": {
    "assessment": None,
    "invocation_limit": 3,
    "invocations": 0,
},
```

Only increment `adaptive.invocations` around actual `run_provider` calls.

- [ ] **Step 4: Add no-generator executor dispatch**

At the start of `_execute_step`, dispatch by `step["executor"]`:

```python
executor = step.get("executor", "generate")
if executor == "risk-assess":
    diff = _git_diff_evidence(cfg) or ""
    assessment = analyze_diff(diff, _git_changed_files(cfg))
    state["adaptive"]["assessment"] = assessment.to_dict()
    state["adaptive"]["invocation_limit"] = invocation_limit(assessment)
    state["history"].append({
        "action": "RISK_ASSESS",
        "step": step["id"],
        "assessment": assessment.to_dict(),
    })
    return
if executor == "targeted-review":
    _execute_targeted_review(state, step, st, ver, cfg, max_parallel, log)
    return
if executor == "checks-only":
    _run_step_checks(step, st, cfg)
    return
```

`_git_changed_files(cfg)` runs `git diff --name-only HEAD` in `cfg["cwd"]`
and returns sorted non-empty paths. It returns an empty list on subprocess
failure and records the analyzer fallback.

- [ ] **Step 5: Implement targeted review with fail-closed budgeting**

`_execute_targeted_review` reads the stored assessment, invokes the primary
reviewer, and invokes the secondary reviewer only when present. Before every
call:

```python
if state["adaptive"]["invocations"] >= state["adaptive"]["invocation_limit"]:
    st["verdicts"] = [{
        "by": "adaptive-budget",
        "ok": False,
        "note": "invocation budget exhausted",
    }]
    return
```

The verifier prompt requires blocking findings to contain both
`REPRODUCTION:` and `MECHANICAL_CHECK:` lines. A FAIL lacking either line
remains a failing verdict and cannot trigger automatic repair. Persist persona,
risk evidence, output criteria, and the bounded note in `st["verdicts"]`.

- [ ] **Step 6: Implement one informed repair when the finding is verifiable**

After a failing primary review, parse `MECHANICAL_CHECK:` and accept it only
when it exactly matches one of the commands supplied through `--check`.
Otherwise retain the failing verdict and stop. For an allowed check, call the
generator once with the bounded reviewer finding as `previous_failure`, run
the allowed check, and replace the failing verdict with a passing
`adaptive-repair` verdict only when the check exits zero. Record
`INFORMED_REPAIR`, the check, and its exit status in history. Never execute a
reviewer-authored command that was not supplied by the user or task manifest.

- [ ] **Step 7: Add the opt-in recipe**

```yaml
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
```

The body documents two-call normal behavior, three-call repair budget,
four-call multi-domain budget, and safe-stop behavior.

- [ ] **Step 8: Test clean, high-risk, malformed, repair, and budget-exhausted paths**

Use monkeypatched `run_provider` outputs to assert:

- normal path calls generator once and primary reviewer once;
- two high-risk domains call one generator and two reviewers;
- malformed reviewer output fails closed;
- a blocking finding with an allowlisted check calls one informed repair and
  passes only after that check succeeds;
- a blocking finding with an unlisted check never executes that command;
- budget exhaustion stops without another provider call;
- existing `bugfix` and `fast-bugfix` plans are byte-for-byte unchanged.

- [ ] **Step 9: Run focused tests and commit**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_adaptive_run.py tests/test_adaptive_risk.py tests/test_recipes.py tests/test_retry_feedback.py`

Expected: all tests pass.

```powershell
git add rig_workbench/orchestrate skills/rig/recipes/adaptive-bugfix.md tests/test_adaptive_run.py tests/test_recipes.py
git commit -m "feat: add adaptive bugfix recipe"
```

### Task 3: External Repository-Shaped Corpus

**Files:**
- Create: `benchmarks/__init__.py`
- Create: `benchmarks/tasks/*/task.json`
- Create: `benchmarks/tasks/*/repo/**`
- Create: `benchmarks/tasks/*/hidden_check.py`
- Create: `benchmarks/tasks/*/canonical/**`
- Create: `benchmarks/tasks/*/narrow/**`
- Create: `rig_workbench/bench_tasks.py`
- Create: `tests/test_bench_tasks.py`

**Interfaces:**
- Produces: `BenchTask`
- Produces: `load_tasks(root: pathlib.Path | None = None) -> dict[str, BenchTask]`
- Produces: `materialize(task: BenchTask) -> pathlib.Path`
- Produces: `run_variant_contract(task: BenchTask, variant: str) -> CheckResult`

- [ ] **Step 1: Define and test the task schema**

Each `task.json` uses:

```json
{
  "schema_version": 1,
  "id": "auth-sibling-write",
  "language": "python",
  "difficulty": "security",
  "risk_domains": ["security", "test"],
  "goal": "Fix the reported cross-user profile read without changing existing tests.",
  "test_command": ["python", "-m", "pytest", "-q"],
  "hidden_command": ["python", "hidden_check.py"],
  "expected_files": ["profile_service.py", "test_profile_service.py"]
}
```

Tests reject path traversal, missing files, unknown schema versions, empty
commands, duplicate ids, and hidden files present under `repo/`.

- [ ] **Step 2: Implement the immutable loader**

```python
@dataclass(frozen=True)
class BenchTask:
    id: str
    language: str
    difficulty: str
    risk_domains: tuple[str, ...]
    goal: str
    test_command: tuple[str, ...]
    hidden_command: tuple[str, ...]
    root: pathlib.Path
    expected_files: tuple[str, ...]
```

`materialize` copies only `repo/`, initializes a local git repository, and
commits the starting state. Hidden checks run from a separate temporary
directory with the materialized repository inserted on `PYTHONPATH` or passed
as an explicit path argument.

- [ ] **Step 3: Add five Python tasks**

Create deterministic tasks for:

1. sibling authorization write;
2. transaction rollback/state consistency;
3. API compatibility after a rename;
4. boundary behavior in pagination;
5. unsafe SQL construction.

For each task, provide original, narrow, and canonical variants. The narrow
variant must pass public tests and fail hidden checks; the canonical variant
must pass both.

- [ ] **Step 4: Add five TypeScript tasks**

Create deterministic tasks for:

1. missing authorization in a sibling handler;
2. stale cache after mutation;
3. exported API compatibility;
4. async error propagation;
5. unrelated generated-file modification.

Use Node's built-in `node:test` and `node --test`; do not add npm dependencies.

- [ ] **Step 5: Run corpus contract tests**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_bench_tasks.py`

Expected: all 10 tasks satisfy original/narrow/canonical invariants and hidden
checks are absent from materialized workspaces.

- [ ] **Step 6: Commit**

```powershell
git add benchmarks rig_workbench/bench_tasks.py tests/test_bench_tasks.py
git commit -m "test: add repository-shaped benchmark corpus"
```

### Task 4: Fair Provider Adapters and Paired Runner

**Files:**
- Create: `rig_workbench/bench_providers.py`
- Rewrite: `rig_workbench/bench.py`
- Modify: `tests/test_bench.py`
- Create: `tests/test_bench_providers.py`

**Interfaces:**
- Produces: `ProviderAttempt`
- Produces: `run_bare(task, provider, model, workspace, options) -> ProviderAttempt`
- Produces: `run_rig(task, provider, model, workspace, options) -> ProviderAttempt`
- Consumes: `BenchTask`, `adaptive-bugfix`

- [ ] **Step 1: Write adapter contract tests**

```python
@pytest.mark.parametrize("provider", ["claude", "codex", "ollama", "lmstudio", "mock"])
def test_bare_adapter_is_writable_and_ephemeral(provider, tmp_path):
    attempt = build_bare_attempt(provider, "goal", tmp_path, model=None)
    assert attempt.cwd == tmp_path
    assert attempt.writable is True
    assert attempt.single_invocation is True
```

Assert Claude and Codex argv use writable scratch isolation, local providers
receive the same goal, and mock can simulate success, timeout, malformed
output, and partial edits.

- [ ] **Step 2: Implement the provider attempt model**

```python
@dataclass(frozen=True)
class ProviderAttempt:
    provider: str
    model: str | None
    returncode: int
    elapsed_s: float
    invocations: int
    stdout: str
    stderr: str
    infra_error: str | None
```

Classify missing executables, timeout, authentication failure, and endpoint
failure as `infra_error`. Count every attempted provider call.

- [ ] **Step 3: Replace fenced-code bare mode with one writable agent run**

Claude uses one headless writable invocation in the scratch repository. Codex
uses one `codex exec` invocation with `--sandbox workspace-write`, `--cd` set
to the scratch repository, `--ephemeral`, and the same goal. OpenAI-compatible
providers receive a strict tool-free patch contract; apply a returned unified
diff with `git apply` so multi-file edits are supported.

- [ ] **Step 4: Run rig with the same goal and explicit public check**

Extend `rig-wb run` with repeatable `--check <command>` for the adaptive
recipe. The runner applies these commands to the `checks-only` acceptance
step, records them in run state, and does not alter other recipes.

`run_rig` invokes:

```text
rig-wb run adaptive-bugfix --provider <provider> --goal <goal>
  --check <task test command> --out <workspace>/run-state.json
```

- [ ] **Step 5: Implement paired execution**

For every task/run, materialize independent bare and rig workspaces before
either arm executes. Store planned pair id, arm order, provider/model, all
attempts, git status, public-test result, hidden-check result, elapsed time,
and invocation count. Never reuse a modified workspace.

- [ ] **Step 6: Run adapter and smoke tests**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_bench_providers.py tests/test_bench.py`

Expected: all mock paired runs pass and both arms start from identical trees.

- [ ] **Step 7: Commit**

```powershell
git add rig_workbench/bench.py rig_workbench/bench_providers.py rig_workbench/orchestrate/commands.py tests/test_bench.py tests/test_bench_providers.py
git commit -m "feat: make benchmark provider comparison fair"
```

### Task 5: Scoring, Validity, and Reports

**Files:**
- Create: `rig_workbench/bench_score.py`
- Modify: `rig_workbench/bench.py`
- Modify: `tests/test_bench.py`
- Create: `tests/test_bench_score.py`

**Interfaces:**
- Produces: `classify_outcome(arm: ArmResult) -> str`
- Produces: `score_provider(pairs: list[PairResult]) -> ProviderScore`
- Produces: `render_html(summary: dict) -> str`

- [ ] **Step 1: Write failing threshold and denominator tests**

Cover:

- 50% relative silent-defect reduction;
- bare silent defects equal zero yields `inconclusive`;
- safe-stop denominator includes valid rig runs only;
- infrastructure error rate above 10% yields `invalid`;
- failed attempts still count toward invocation cost;
- fewer than 10 tasks or 3 valid pairs per task yields `invalid`;
- any unrelated diff or workspace leak fails the score.

- [ ] **Step 2: Implement explicit score models**

```python
@dataclass(frozen=True)
class ProviderScore:
    verdict: str  # pass | fail | invalid | inconclusive
    reasons: tuple[str, ...]
    bare_silent_defect_rate: float
    rig_silent_defect_rate: float
    relative_reduction: float | None
    rig_safe_stop_rate: float
    call_ratio: float
    infra_error_rate: float
```

Do not infer missing fields as success. Missing completion state, hidden check,
or invocation count makes the pair invalid.

- [ ] **Step 3: Add versioned JSON output**

Top-level metadata contains:

```json
{
  "schema_version": 2,
  "generated": "ISO-8601",
  "rig_wb_version": "1.x",
  "recipe": "adaptive-bugfix",
  "recipe_version": 1,
  "corpus_version": 1,
  "provider": "codex",
  "model": "model-name",
  "provider_version": "captured CLI or endpoint version"
}
```

Retain an upgrader/renderer path for schema-version-1 benchmark JSON.

- [ ] **Step 4: Rewrite HTML around outcome and acceptance metrics**

Show provider/model identity, validity, silent-defect delta, safe-stop rate,
call ratio, infra errors, unrelated diffs, per-task paired outcomes, and every
discarded/replacement attempt. Label mock reports `WIRING ONLY`.

- [ ] **Step 5: Run scoring and golden tests**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_bench_score.py tests/test_bench.py`

Expected: all tests pass, including old JSON rendering.

- [ ] **Step 6: Commit**

```powershell
git add rig_workbench/bench.py rig_workbench/bench_score.py tests/test_bench.py tests/test_bench_score.py
git commit -m "feat: score adaptive benchmark acceptance"
```

### Task 6: Packaging, CLI Documentation, and Compatibility

**Files:**
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `README.ja.md`
- Modify: `CHANGELOG.md`
- Modify: `rig_workbench/cli.py`
- Modify: `skills/rig/SKILL.md`
- Modify: `skills/rig/facets/instructions/list.md`
- Modify: `tests/test_cli_smoke.py`
- Modify: `tests/test_recipes.py`

**Interfaces:**
- `rig-wb bench --corpus <path> --tasks ... --provider ... --runs 3`
- `rig-wb plan adaptive-bugfix`

- [ ] **Step 1: Package benchmark resources**

Add `benchmarks*` to setuptools package discovery and include task JSON,
Python, TypeScript, and text fixtures as package data. Add an install-smoke
test that builds a wheel, installs it into a temporary venv, and loads all
task ids.

- [ ] **Step 2: Update CLI help**

Document external corpus selection, minimum valid run count, mock limitations,
schema version, explicit paid-provider opt-in, and exit codes:

- `0`: benchmark completed and thresholds pass;
- `1`: completed but failed, invalid, or inconclusive;
- `2`: CLI/schema error.

- [ ] **Step 3: Update English and Japanese docs**

Describe the adaptive two-call normal path, conditional extra review/repair,
fair writable bare baseline, hidden-check isolation, per-provider scoring, and
the exact agreed thresholds. State that default routing is unchanged.

- [ ] **Step 4: Update recipe catalogs and changelog**

Add `adaptive-bugfix` to shipped inventory and list output without changing
the existing default recipe. Record the benchmark schema break and old-report
rendering compatibility.

- [ ] **Step 5: Run compatibility tests and commit**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_cli_smoke.py tests/test_recipes.py tests/test_bench.py`

Expected: all tests pass.

```powershell
git add pyproject.toml README.md README.ja.md CHANGELOG.md rig_workbench/cli.py skills/rig tests
git commit -m "docs: document adaptive benchmark workflow"
```

### Task 7: Full Verification

**Files:**
- No implementation files should change unless verification exposes a defect.

- [ ] **Step 1: Run formatting and lint**

Run: `.\.venv\Scripts\python.exe -m ruff check .`

Expected: exit 0.

- [ ] **Step 2: Run focused benchmark and adaptive suites**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_adaptive_risk.py tests/test_adaptive_run.py tests/test_bench_tasks.py tests/test_bench_providers.py tests/test_bench_score.py tests/test_bench.py`

Expected: all tests pass.

- [ ] **Step 3: Run the full suite with UTF-8 enabled**

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: no new failures relative to the known Windows/POSIX baseline. Any
new failure must be fixed before proceeding.

- [ ] **Step 4: Run deterministic benchmark smoke**

```powershell
.\.venv\Scripts\rig-wb.exe bench --provider mock --runs 3 --out C:\tmp\rig-bench.json --html C:\tmp\rig-bench.html
```

Expected: 10 or more tasks, 3 paired runs each, schema version 2, mock report
marked `WIRING ONLY`, no infrastructure errors, unrelated diffs, or leaks.

- [ ] **Step 5: Verify recipe resolution**

Run: `.\.venv\Scripts\rig-wb.exe plan adaptive-bugfix --json`

Expected: implement, assess, targeted-review, and acceptance steps with their
explicit executors.

- [ ] **Step 6: Review final diff**

Run: `git diff HEAD~6 --check` and `git status --short`.

Expected: no whitespace errors and only intended files.
