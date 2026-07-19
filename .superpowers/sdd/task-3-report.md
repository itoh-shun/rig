# Task 3 Report: External Repository-Shaped Corpus

## Result

Implemented exactly 10 deterministic repository fixtures:

- Python: sibling authorization write, transaction rollback, renamed API
  compatibility, pagination boundaries, and parameterized SQL.
- TypeScript: sibling handler authorization, stale cache mutation, exported API
  compatibility, async error propagation, and unrelated generated-file changes.

Every task has `task.json`, `repo/`, external `hidden_check.py`, `narrow/`, and
`canonical/`. TypeScript public tests use `node:test` and no npm dependencies.

`rig_workbench.bench_tasks` now provides frozen `BenchTask` and `CheckResult`
objects, validated loading, repo-only materialization with a committed local Git
state, and separate public/hidden variant contract execution. Hidden checks run
from a separate temporary directory and receive the materialized repository as
an explicit path.

## TDD Evidence

### RED

- Loader API: `1 failed` with `ModuleNotFoundError: rig_workbench.bench_tasks`.
- Schema validation: `7 failed, 1 passed`; unknown schema, empty commands,
  traversal, missing files, duplicate IDs, and hidden-check leakage were
  accepted.
- Materialization: `1 failed` because `materialize` did not exist.
- Variant execution: `3 failed` because `run_variant_contract` did not exist.
- Python corpus: `6 failed`; all five Python task IDs were absent.
- TypeScript corpus: `6 failed`; all five TypeScript task IDs were absent.
- Self-review hardening: `2 failed, 5 passed`; traversal in a hidden command and
  task ID was still accepted.

### GREEN

- Loader schema cycle: `8 passed`.
- Loader/materializer/contract helper cycle: `12 passed`.
- Python corpus matrix: `6 passed, 12 deselected`.
- TypeScript corpus matrix: `6 passed, 18 deselected`.
- Hardened schema cycle: `7 passed`.
- Final Task 3 suite: `26 passed in 35.46s`.

## Verification

- `python -m pytest -q tests/test_bench_tasks.py`: `26 passed`.
- `python -m ruff check benchmarks rig_workbench/bench_tasks.py tests/test_bench_tasks.py`:
  all checks passed.
- `python -m ruff format --check rig_workbench/bench_tasks.py tests/test_bench_tasks.py`:
  both files already formatted.
- Mechanical corpus checks: no `hidden_check.py` under any `repo/`; no
  `package.json`, package lock, or `node_modules`.
- `git diff --cached --check`: clean.

The pre-existing Windows baseline is not green outside Task 3. The full suite
initially reported `533 passed, 86 failed, 15 errors`; the first failure was a
CP932 encoding error in unrelated orchestration output. With UTF-8 enabled,
`tests/test_bench.py` reports `16 passed, 4 failed` because its legacy built-in
tasks invoke unavailable `python3` on Windows. No provider, runner, legacy
benchmark, or orchestration modules were changed.

## Self-Review

- Added command-argument and task-ID traversal rejection found during review.
- Hardened Windows cleanup for read-only Git objects and guaranteed hidden
  scratch cleanup if workspace cleanup raises.
- Confirmed the staged scope contains only the user-owned implementation files.

## Review Fixes

### Security And Portability

- All `repo/`, `narrow/`, and `canonical/` trees are scanned without following
  links. Case-insensitive `.git` files/directories, symlinks, and Windows
  reparse points such as junctions are rejected during load and immediately
  before and during each copy.
- Schema paths are checked with both POSIX and Windows path semantics. POSIX
  absolute paths, drive-qualified paths, drive-relative paths, UNC paths,
  forward/backslash traversal, NULs, and resolved escapes are rejected for
  expected files and every public/hidden command token.
- Hidden artifacts are rejected independently of `hidden_command`. Reserved
  patterns cover hidden checks, specs, and tests with dot, underscore, or
  hyphen naming, including `python -m hidden_check` bypass attempts.
- All captured corpus subprocesses explicitly decode UTF-8 with
  `errors="replace"`. `CheckResult` streams are optional and output properties
  safely normalize missing streams to empty text.
- CI now runs on Python 3.10 and 3.12 with Node 22.18.0. The workflow contract
  verifies the declared floors and rejects paid-provider flags or credentials.

### Review RED Evidence

- Hostile source trees: `8 failed, 2 skipped`; nested `.git` files/directories
  and post-load injections were accepted. Link fixtures were unavailable under
  the current Windows account.
- Portable paths and hidden artifacts: `11 failed, 15 passed`; Windows drive
  paths and all command-independent hidden artifact patterns bypassed checks.
- Subprocess output: `2 failed` with two decoder-thread warnings; invalid bytes
  produced `None` streams and output-property `TypeError`.
- Runtime floor: `1 failed` with missing CI `strategy`; Node was not installed
  and Python 3.10 was not exercised.
- Command executables: `8 failed`; absolute, drive-qualified, UNC, and
  traversing executable tokens were accepted.

### Review GREEN Evidence

- Hostile source trees: `8 passed, 2 skipped`.
- Portable paths and hidden artifacts: `27 passed`.
- UTF-8 replacement and missing-stream handling: `2 passed` with no warnings.
- Runtime floor workflow contract: `1 passed`.
- Portable command executables: `8 passed`.
- Windows junction regression: `1 passed`.
- Final focused suite: `72 passed, 6 skipped in 39.02s`. The six skips are
  symlink fixture variants that require a link-capable account and run on
  Ubuntu CI; Windows junction rejection passed locally.
- Ruff: all checks passed.
- Ruff format check: both files already formatted.

## Final Review Fixes

### Race-Safe Copy

- Replaced `shutil.copytree` with recursive copying that accepts only regular
  files and directories and rejects `.git`, reserved hidden artifacts,
  symlinks, junctions, reparse points, and other special files at traversal.
- POSIX traversal uses directory-relative descriptors, `O_NOFOLLOW`, and
  device/inode identity checks. Windows traversal opens reparse points
  themselves and holds no-delete directory handles while enumerating.
- File content is copied from a verified source descriptor into a temporary
  regular file before atomic destination replacement. The completed
  destination is validated again before `git init`, `git add`, or `git commit`.
- A deterministic traversal hook regression swaps a validated directory to a
  POSIX symlink or Windows junction and proves outside content is not copied.

### Exact Node Floor

- `MIN_NODE_VERSION = (22, 18, 0)` documents the minimum runtime for direct,
  unflagged TypeScript execution.
- TypeScript contracts query `node --version` with explicit UTF-8 replacement
  decoding and reject versions below 22.18.0 before executing task commands.
- CI installs Node 22.18.0 exactly; the runtime contract rejects 22.17.9 and
  accepts 22.18.0, 22.19.1, and 23.0.0.

### Final RED Evidence

- Copy race: `1 failed`; the post-validation swap hook was never reached by
  `shutil.copytree`, so the expected link rejection did not occur.
- Node floor: `2 failed`; `MIN_NODE_VERSION` and the runtime guard did not
  exist, while CI still selected floating Node 22.x.

### Final GREEN Evidence

- Race regression and normal materialization: `2 passed`.
- Node runtime and CI floor boundary: `2 passed`.
- Focused corpus suite:
  `C:\Users\Succh\work\rig\.venv\Scripts\python.exe -m pytest tests/test_bench_tasks.py -q`
  reported `74 passed, 6 skipped in 38.26s`.
- `ruff check`: all checks passed.
- `ruff format --check`: both files already formatted.
- `git diff --check`: clean.
