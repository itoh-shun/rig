# Task 5 Handoff Report

## Status

DONE

Task 5 scoring, validity, JSON metadata, and HTML reporting are implemented.
The previous implementer's uncommitted changes were audited and retained rather
than discarded. Two uncovered behavior gaps were fixed with regression tests
first.

## Requirement Audit

### Scoring and validity

- `classify_outcome(ArmResult)` distinguishes `clean_pass`,
  `silent_defect`, `safe_stop`, `stopped_wrong`, `infra_error`, and invalid
  evidence without converting missing completion, hidden-check, or invocation
  data into success.
- `ProviderScore` contains the required verdict, reasons, absolute
  silent-defect rates, relative reduction, rig safe-stop rate, call ratio, and
  infrastructure-error rate.
- A 50% relative silent-defect reduction passes at the boundary; lower
  reduction fails.
- Zero bare silent defects produces `inconclusive`.
- Silent-defect and invocation-cost comparisons use valid paired runs.
- Rig safe-stop rate uses all valid rig arms, including a valid rig arm whose
  paired bare arm had an infrastructure failure.
- Infrastructure errors use all planned arms as the denominator and invalidate
  the provider result only above 10%.
- Every retained provider attempt contributes to invocation cost, including
  failed attempts.
- At least 10 tasks with at least 3 valid pairs each are required.
- Provider/model groups cannot be pooled, and the model must be concrete.
- Any unrelated diff or workspace leak produces a failing acceptance result.

### JSON output

- New benchmark output uses schema version 2.
- Top-level output includes `generated`, `rig_wb_version`, `recipe`,
  `recipe_version`, `corpus_version`, `provider`, `model`, and
  `provider_version`.
- Provider version capture supports an explicit captured value, built-in mock
  identity, Claude/Codex CLI version capture, and endpoint identity fallback.
- The serialized summary includes the complete provider score and per-arm
  unrelated-file/workspace-leak evidence.
- Schema-version-1 benchmark JSON remains renderable through the legacy
  `modes` path with legacy outcome inference.

### HTML report

- The report shows provider/model identity, provider version, validity and
  reasons, bare and rig silent-defect rates, relative silent-defect delta,
  safe-stop rate, call ratio, and infrastructure-error rate.
- It lists every paired outcome, unrelated diff, workspace leak, and retained
  attempt, including discarded/replacement attempts and their error detail.
- Mock reports are prominently labeled `WIRING ONLY`.
- Report content is HTML-escaped and remains usable on narrow viewports.

## TDD Fixes

1. Added
   `test_safe_stop_denominator_includes_valid_rig_arm_when_bare_arm_is_infra`.
   It failed at `0.166666...` versus the required `6 / 31`, proving that the
   implementation incorrectly reused paired validity for a rig-only metric.
   The scorer now tracks valid rig arms separately.
2. Extended
   `test_schema_v2_html_shows_acceptance_evidence_and_every_attempt` to require
   both absolute silent-defect rates. It failed because the HTML only exposed
   relative reduction. The report now renders both absolute rates and the
   delta.

## Verification

- Baseline focused suite:
  `C:\Users\Succh\work\rig\.venv\Scripts\python.exe -m pytest -q tests/test_bench_score.py tests/test_bench.py`
  -> 37 passed in 35.90s.
- Safe-stop regression RED:
  `C:\Users\Succh\work\rig\.venv\Scripts\python.exe -m pytest -q tests/test_bench_score.py::test_safe_stop_denominator_includes_valid_rig_arm_when_bare_arm_is_infra`
  -> 1 failed with the expected denominator mismatch.
- Safe-stop regression GREEN plus score suite:
  `C:\Users\Succh\work\rig\.venv\Scripts\python.exe -m pytest -q tests/test_bench_score.py::test_safe_stop_denominator_includes_valid_rig_arm_when_bare_arm_is_infra tests/test_bench_score.py`
  -> 17 passed in 0.21s.
- HTML regression RED:
  `C:\Users\Succh\work\rig\.venv\Scripts\python.exe -m pytest -q tests/test_bench_score.py::test_schema_v2_html_shows_acceptance_evidence_and_every_attempt`
  -> 1 failed because the absolute-rate labels were absent.
- HTML regression GREEN:
  the same targeted command -> 1 passed in 0.17s.
- Final focused suite:
  `C:\Users\Succh\work\rig\.venv\Scripts\python.exe -m pytest -q tests/test_bench_score.py tests/test_bench.py`
  -> 38 passed in 37.64s.
- Ruff:
  `C:\Users\Succh\work\rig\.venv\Scripts\python.exe -m ruff check rig_workbench/bench.py rig_workbench/bench_score.py tests/test_bench.py tests/test_bench_score.py`
  -> all checks passed.
- Ruff formatting:
  `C:\Users\Succh\work\rig\.venv\Scripts\python.exe -m ruff format --check rig_workbench/bench.py rig_workbench/bench_score.py tests/test_bench.py tests/test_bench_score.py`
  -> 4 files already formatted.
- `git diff --check` exited successfully. Git emitted only the repository's
  configured LF-to-CRLF conversion warnings.

## Changed Files

- `rig_workbench/bench_score.py`: explicit score model, validity-aware scoring,
  schema-v1/v2 report handling, and acceptance-focused HTML.
- `rig_workbench/bench.py`: score integration, schema-v2 metadata and provider
  version, unrelated-diff/workspace-leak evidence, and report delegation.
- `tests/test_bench_score.py`: threshold, denominator, validity, cost, grouping,
  safety-evidence, and HTML regressions.
- `tests/test_bench.py`: runner evidence, schema metadata, workspace-leak,
  unrelated-diff, and schema-v1 renderer coverage.

## Self-Review

- Re-read the Task 5 brief and the design's scoring section after the final
  test run.
- Confirmed comparative quality and cost remain paired, while rig safety uses
  the explicitly required rig-arm validity domain.
- Confirmed infrastructure errors remain visible and use planned-arm counts.
- Confirmed missing required evidence cannot yield `clean_pass`.
- Confirmed output remains grouped by one provider/model and old JSON remains
  renderable.
- Confirmed only the four owned Task 5 implementation/test files and this
  report were changed; prior Tasks 1-4 were not reverted.

## Commits

- `5fc83018e0afc7a350144a1834dd9d995c87d9ae`
  (`feat: score adaptive benchmark acceptance`) contains the four Task 5
  implementation and test files.
- This report is committed separately so it can reference the implementation
  commit exactly.

## Concerns

None. No paid or live provider benchmark was run; Task 5 verification used the
focused mock/unit suites required by the brief.
