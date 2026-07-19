# Task 2 Report: Adaptive Orchestration Executors and Recipe

## Summary

Implemented the opt-in adaptive bugfix runner integration:

- Recipe steps preserve `executor` with a legacy-safe `generate` default.
- New run state initializes deterministic adaptive assessment and invocation fields.
- `risk-assess` and `checks-only` execute without provider calls.
- `targeted-review` runs the deterministic primary reviewer and optional secondary reviewer.
- Provider calls are counted at the call boundary for adaptive runs.
- One informed repair is allowed only for an explicit blocking finding whose exact
  `MECHANICAL_CHECK` appears in the user/task allowlist.
- Malformed output, nonzero reviewer exit, failed repair checks, unlisted commands, and
  exhausted budgets fail closed.
- The shipped `adaptive-bugfix` recipe documents normal, repair, multi-domain, and safe-stop
  behavior.

## RED Evidence

### Recipe Executor and State

Command:

`..\..\.venv\Scripts\python.exe -m pytest -q tests/test_adaptive_run.py tests/test_recipes.py`

Result before production changes:

`2 failed, 19 passed`

The failures were the required missing fields:

- `KeyError: 'adaptive'`
- `KeyError: 'executor'`

### Executor Dispatch

Command:

`..\..\.venv\Scripts\python.exe -m pytest -q tests/test_adaptive_run.py::test_normal_path_uses_one_generator_and_one_targeted_reviewer`

Result before dispatch:

`1 failed`

The legacy runner generated for all four steps, so the actual call list contained extra
generator calls instead of one implementer and one `test-reviewer`.

### Informed Repair

Command:

`..\..\.venv\Scripts\python.exe -m pytest -q tests/test_adaptive_run.py`

Result before repair implementation:

`2 failed, 6 passed`

The allowlisted repair path stopped with `ESCALATE`, and the failed-check path had no
`INFORMED_REPAIR` history entry. Secondary review, malformed output, unlisted-command
rejection, and budget exhaustion already passed.

### Recipe

Command:

`..\..\.venv\Scripts\python.exe -m pytest -q tests/test_recipes.py::test_adaptive_bugfix_recipe_has_bounded_executor_flow tests/test_recipes.py::test_existing_bugfix_recipe_bytes_are_unchanged`

Result before adding the recipe:

`1 failed, 1 passed`

The adaptive recipe failed with `FileNotFoundError`; the byte hashes for `bugfix.md` and
`fast-bugfix.md` passed unchanged.

### Security Hardening

Command:

`..\..\.venv\Scripts\python.exe -m pytest -q tests/test_adaptive_run.py::test_allowlisted_blocking_finding_gets_one_informed_repair tests/test_adaptive_run.py::test_reproduction_and_check_without_explicit_fail_cannot_trigger_repair`

Result before hardening:

`2 failed`

The trusted allowlist was absent from the reviewer prompt, and output with reproduction/check
lines but no explicit final `VERDICT: FAIL` reached `subprocess.run`.

Command:

`..\..\.venv\Scripts\python.exe -m pytest -q tests/test_adaptive_run.py::test_nonzero_reviewer_exit_cannot_pass`

Result before exit-status enforcement:

`1 failed`

A reviewer process exiting `1` while printing `VERDICT: PASS` incorrectly completed the run.

## GREEN Evidence

The corresponding focused GREEN results were:

- Executor/state fields: `21 passed`
- Normal two-call path: `1 passed`
- Adaptive path matrix after repair: `8 passed`
- Allowlist/malformed hardening: `2 passed`
- Nonzero reviewer exit: `1 passed`

Final required command:

`..\..\.venv\Scripts\python.exe -m pytest -q tests/test_adaptive_run.py tests/test_adaptive_risk.py tests/test_recipes.py tests/test_retry_feedback.py`

Result:

`52 passed in 0.60s`

Ruff command:

`..\..\.venv\Scripts\python.exe -m ruff check rig_workbench/orchestrate/recipes.py rig_workbench/orchestrate/providers.py rig_workbench/orchestrate/runstate.py tests/test_adaptive_run.py tests/test_recipes.py`

Result:

`All checks passed!`

`git diff --check` exited zero with only Git's existing LF-to-CRLF working-copy warnings.

## Security Review

- Reviewer commands are parsed as data and cannot execute unless the exact command is present
  in `cfg["checks"]`, `cfg["check_allowlist"]`, or a recipe-declared check.
- Repair requires nonempty `REPRODUCTION` and `MECHANICAL_CHECK` fields plus an exact final
  `VERDICT: FAIL`.
- The allowlist is shown to the reviewer, but exact membership is checked again immediately
  before `shell=True`.
- A nonzero reviewer process exit cannot produce a passing verdict.
- Budget checks happen before targeted review and repair provider calls.
- A nonzero or failed mechanical check retains the original failing reviewer verdict.

## Self-Review

- Existing `bugfix.md` SHA-256 remains
  `bbf216319c3056819198df84a34e35bcff51ae476b11966ec2ab47e9197a8d8b`.
- Existing `fast-bugfix.md` SHA-256 remains
  `398447decc09a69432f7a63efae704eb3f1ed3dc752c04dfd82d5fd6555dd45e`.
- Legacy steps receive only the specified default `executor: generate`; their recipe source
  bytes and execution dispatch remain unchanged.
- Public interfaces `execute_adaptive_review(...) -> list[dict]` and
  `execute_informed_repair(...) -> bool` are present in `providers.py`.
- No files outside Task 2 ownership and this report were modified.

## Concern

A broader legacy sample

`tests/test_runstate.py tests/test_judge_hardening.py tests/test_auto_route.py tests/test_step_model.py tests/test_persona_briefs.py`

produced `3 failed, 44 passed` because the pre-existing mock provider invokes `python3`, which
is not installed on this Windows host. Direct evidence was `RC 127` with
`[provider not found: mock]`. Task 2 did not change `MOCK_SRC`, `build_argv`, or that executable
selection, so no out-of-scope compatibility change was made.
