# Task 1 Report: Deterministic Diff-Risk Analysis

## RED Evidence

Added the required adaptive-risk tests before production code. The focused command:

`..\\..\\.venv\\Scripts\\python.exe -m pytest -q tests/test_adaptive_risk.py`

failed during collection with:

`ModuleNotFoundError: No module named 'rig_workbench.orchestrate.adaptive'`

After adding the changed-file design test, the focused suite also failed as expected:

`1 failed, 6 passed`

The failure was `test_design_file_changes_are_reviewed_without_matching_diff_text`, with the
fallback `test-reviewer` returned before path-based design detection was implemented.

## GREEN Evidence

After the minimal implementation and path-rule addition:

`..\\..\\.venv\\Scripts\\python.exe -m pytest -q tests/test_adaptive_risk.py`

Result: `7 passed in 0.06s`

`..\\..\\.venv\\Scripts\\python.exe -m ruff check rig_workbench/orchestrate/adaptive.py tests/test_adaptive_risk.py`

Result: `All checks passed!`

## Files Changed

- `rig_workbench/orchestrate/adaptive.py`: frozen value objects, deterministic regex-based
  signal extraction, severity/domain/evidence ordering, reviewer selection, closed fallback,
  and invocation limits.
- `tests/test_adaptive_risk.py`: required examples plus serialization, frozen behavior,
  ordering, representative risk families, and changed-file design coverage.
- `.superpowers/sdd/task-1-report.md`: this implementation and verification report.

## Self-Review

- Ranking is explicit and stable: descending severity, then ascending domain, then ascending
  evidence.
- Secondary selection requires a distinct domain and severity at least 2.
- Unknown input always returns `test-reviewer`, no secondary, and the exact fallback reason.
- The implementation uses no provider or external state and preserves existing modules.
- Type syntax and Ruff target are compatible with Python 3.10.
- Both dataclasses are frozen and expose deterministic dictionary shapes.

## Concerns

The analyzer intentionally uses conservative, text-based heuristics rather than a parser;
future workflows may need additional vocabulary as new diff shapes appear. The full repository
pytest command was also attempted with a 120-second timeout and produced numerous failures before
timing out; those failures were outside the two owned implementation/test files, while the
focused Task 1 suite and Ruff checks passed.
