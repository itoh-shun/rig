"""Diff-scoped mutation testing behind `changed_code_mutants_are_killed`.

Covers: the builtin AST engine (operator set, changed-line scoping, test-file
exclusion), the manifest opt-in (no `mutate:` → the criterion never appears),
and the gate integration in a scratch repo — surviving mutant fails the gate
and is named, a strengthened test flips it to passed, an edited diff makes the
recorded report stale, and `--set …=passed` is the recorded escape hatch.

The test command used by the scratch repos is a plain `python3` script rather
than pytest: this suite must not depend on pytest being importable from a
subprocess interpreter it does not control.
"""

import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench.mutation import (CRITERION, apply_mutation,
                                              changed_lines, collect_mutants,
                                              mutation_config,
                                              read_manifest_scalar)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"


def run_cli(args, cwd, env=None):
    return subprocess.run([sys.executable, str(WORKBENCH), *args],
                          capture_output=True, text=True, cwd=cwd, timeout=300,
                          env={**_base_env(), **(env or {})})


def _base_env():
    import os
    return {**os.environ, "RIG_ALLOW_PROJECT_MANIFEST": "1"}


def _ops(source, lines=None):
    lines = lines or set(range(1, len(source.splitlines()) + 1))
    return [m["operator"] for m in collect_mutants(source, lines)]


# ---- builtin engine: operator set --------------------------------------------

def test_comparison_operators_are_swapped():
    assert "`==` → `!=`" in _ops("x = a == b\n")
    assert "`<` → `>=`" in _ops("x = a < b\n")
    assert "`is not` → `is`" in _ops("x = a is not None\n")
    assert "`in` → `not in`" in _ops("x = a in b\n")


def test_boolean_arithmetic_not_and_constants_are_mutated():
    assert "`and` → `or`" in _ops("x = a and b\n")
    assert "`+` → `-`" in _ops("x = a + b\n")
    assert "`not X` → `X`" in _ops("x = not a\n")
    assert "`True` → `False`" in _ops("x = True\n")


def test_mutation_is_confined_to_the_changed_lines():
    source = "a = 1 == 2\nb = 3 == 4\n"
    assert len(collect_mutants(source, {1, 2})) == 2
    only_second = collect_mutants(source, {2})
    assert [m["line"] for m in only_second] == [2]


def test_applied_mutation_changes_the_source_and_stays_parseable():
    source = "def f(a, b):\n    return a == b\n"
    mutant = collect_mutants(source, {2})[0]
    mutated = apply_mutation(source, mutant["key"])
    assert "a != b" in mutated
    compile(mutated, "<mutant>", "exec")


def test_two_mutations_on_one_line_have_distinct_addresses():
    source = "x = a is not None and b is not None\n"
    keys = {m["key"] for m in collect_mutants(source, {1})}
    assert len(keys) == 3  # boolop + two comparisons, each individually addressable


def test_unparseable_source_yields_no_mutants_instead_of_raising():
    assert collect_mutants("def f(:\n", {1}) == []


# ---- manifest resolution ------------------------------------------------------

def _manifest(tmp_path, body):
    (tmp_path / ".claude").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".claude" / "rig.md").write_text(f"---\n{body}\n---\n# m\n", encoding="utf-8")


def test_manifest_scalar_handles_quotes_and_trailing_comments(tmp_path):
    _manifest(tmp_path, 'mutate: builtin  # the stdlib engine\ntest: "pytest -q"')
    assert read_manifest_scalar(tmp_path, "mutate") == "builtin"
    assert read_manifest_scalar(tmp_path, "test") == "pytest -q"
    assert read_manifest_scalar(tmp_path, "absent") == ""


def test_absent_manifest_means_opt_out(tmp_path):
    assert mutation_config(tmp_path)["declared"] is False


def test_tool_command_and_cap_are_read_from_the_manifest(tmp_path):
    _manifest(tmp_path, 'mutate: "mutmut run --paths {files}"\nmutate_max_mutants: 5')
    cfg = mutation_config(tmp_path)
    assert cfg["engine"] == "command"
    assert cfg["command"] == "mutmut run --paths {files}"
    assert cfg["max_mutants"] == 5


def test_mutate_test_overrides_test_for_the_builtin_engine(tmp_path):
    _manifest(tmp_path, 'mutate: builtin\ntest: "make test"\nmutate_test: "pytest tests/unit"')
    assert mutation_config(tmp_path)["test"] == "pytest tests/unit"


# ---- scratch repo -------------------------------------------------------------

SOURCE_WEAK = "def is_valid(user, token):\n    return user is not None and token is not None\n"
TEST_WEAK = ('import sys\nsys.path.insert(0, ".")\n'
             'from lib import is_valid\nassert is_valid(None, None) is False\n')
TEST_STRONG = ('import sys\nsys.path.insert(0, ".")\n'
               'from lib import is_valid\n'
               'assert is_valid(None, None) is False\n'
               'assert is_valid("u", None) is False\n'
               'assert is_valid(None, "t") is False\n'
               'assert is_valid("u", "t") is True\n')


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "lib.py").write_text("def is_valid(user, token):\n    return True\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "check.py").write_text("print('ok')\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def _new_task(repo):
    r = run_cli(["new", "validate credentials", "--type", "feature"], repo)
    assert r.returncode == 0, r.stdout + r.stderr
    task_id = next((repo / ".rig" / "runs").iterdir()).name
    task = json.loads((repo / ".rig" / "runs" / task_id / "task.json").read_text(encoding="utf-8"))
    return task_id, pathlib.Path(task["worktree_path"])


def _write_change(wt, source, test_body):
    (wt / "lib.py").write_text(source, encoding="utf-8")
    (wt / "tests" / "check.py").write_text(test_body, encoding="utf-8")


def _acceptance(repo, task_id):
    return json.loads((repo / ".rig" / "runs" / task_id / "acceptance.json").read_text(encoding="utf-8"))


def _check(repo, task_id):
    return next((c for c in _acceptance(repo, task_id)["checks"] if c["name"] == CRITERION), None)


def test_criterion_is_absent_without_the_manifest_optin(repo):
    task_id, wt = _new_task(repo)
    _write_change(wt, SOURCE_WEAK, TEST_WEAK)
    run_cli(["gate", task_id, "--set", "task_intent_satisfied=passed"], repo)
    assert _check(repo, task_id) is None


def test_criterion_is_absent_for_a_diff_with_no_source_change(repo):
    _manifest(repo, 'mutate: builtin\nmutate_test: "python3 tests/check.py"')
    task_id, wt = _new_task(repo)
    (wt / "README.md").write_text("docs only\n", encoding="utf-8")
    run_cli(["gate", task_id, "--set", "task_intent_satisfied=passed"], repo)
    assert _check(repo, task_id) is None


def test_criterion_is_pending_until_it_has_been_measured(repo):
    _manifest(repo, 'mutate: builtin\nmutate_test: "python3 tests/check.py"')
    task_id, wt = _new_task(repo)
    _write_change(wt, SOURCE_WEAK, TEST_WEAK)
    run_cli(["gate", task_id, "--set", "task_intent_satisfied=passed"], repo)
    check = _check(repo, task_id)
    assert check["status"] == "pending"
    assert "rig-wb wb mutate" in check["detail"]


def test_surviving_mutants_fail_the_gate_and_are_named(repo):
    _manifest(repo, 'mutate: builtin\nmutate_test: "python3 tests/check.py"')
    task_id, wt = _new_task(repo)
    _write_change(wt, SOURCE_WEAK, TEST_WEAK)

    r = run_cli(["mutate", task_id], repo)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "SURVIVED" in r.stdout
    assert "`and` → `or`" in r.stdout

    r = run_cli(["gate", task_id, "--set", "task_intent_satisfied=passed"], repo)
    assert r.returncode != 0
    check = _check(repo, task_id)
    assert check["status"] == "failed"
    assert any("lib.py:2" in ln for ln in check["mutation_survivors"])


def test_a_test_that_kills_every_mutant_passes_the_criterion(repo):
    _manifest(repo, 'mutate: builtin\nmutate_test: "python3 tests/check.py"')
    task_id, wt = _new_task(repo)
    _write_change(wt, SOURCE_WEAK, TEST_STRONG)

    r = run_cli(["mutate", task_id], repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ALL KILLED" in r.stdout

    run_cli(["gate", task_id, "--set", "task_intent_satisfied=passed"], repo)
    check = _check(repo, task_id)
    assert check["status"] == "passed"
    assert "mutation_survivors" not in check


def test_a_red_baseline_is_refused_rather_than_scored(repo):
    _manifest(repo, 'mutate: builtin\nmutate_test: "python3 tests/check.py"')
    task_id, wt = _new_task(repo)
    _write_change(wt, SOURCE_WEAK, 'import sys\nsys.exit(1)\n')

    r = run_cli(["mutate", task_id], repo)
    assert r.returncode == 1
    assert "BASELINE_FAILED" in r.stdout
    run_cli(["gate", task_id, "--set", "task_intent_satisfied=passed"], repo)
    assert _check(repo, task_id)["status"] == "failed"


def test_editing_the_diff_makes_the_recorded_report_stale(repo):
    _manifest(repo, 'mutate: builtin\nmutate_test: "python3 tests/check.py"')
    task_id, wt = _new_task(repo)
    _write_change(wt, SOURCE_WEAK, TEST_STRONG)
    run_cli(["mutate", task_id], repo)
    run_cli(["gate", task_id, "--set", "task_intent_satisfied=passed"], repo)
    assert _check(repo, task_id)["status"] == "passed"

    (wt / "lib.py").write_text(SOURCE_WEAK + "\ndef extra(a):\n    return a > 0\n", encoding="utf-8")
    run_cli(["gate", task_id, "--set", "task_intent_satisfied=passed"], repo)
    check = _check(repo, task_id)
    assert check["status"] == "pending"
    assert "predates the current diff" in check["detail"]


def test_explicit_pass_is_a_recorded_and_sticky_override(repo):
    _manifest(repo, 'mutate: builtin\nmutate_test: "python3 tests/check.py"')
    task_id, wt = _new_task(repo)
    _write_change(wt, SOURCE_WEAK, TEST_WEAK)
    run_cli(["mutate", task_id], repo)

    r = run_cli(["gate", task_id, "--set", f"{CRITERION}=passed"], repo)
    assert "manual override recorded" in r.stdout
    check = _check(repo, task_id)
    assert check["status"] == "passed"
    assert check["mutation_override"] is True

    run_cli(["gate", task_id, "--set", "task_intent_satisfied=passed"], repo)
    assert _check(repo, task_id)["status"] == "passed"


def test_the_cap_is_reported_not_silently_applied(repo):
    _manifest(repo, 'mutate: builtin\nmutate_test: "python3 tests/check.py"\nmutate_max_mutants: 1')
    task_id, wt = _new_task(repo)
    _write_change(wt, SOURCE_WEAK, TEST_STRONG)

    r = run_cli(["mutate", task_id], repo)
    assert "not evaluated" in r.stdout
    report = json.loads((repo / ".rig" / "runs" / task_id / "mutation.json").read_text(encoding="utf-8"))
    assert report["evaluated"] == 1
    assert report["not_evaluated"] == 2


def test_test_files_are_never_mutated(repo):
    task_id, wt = _new_task(repo)
    _write_change(wt, SOURCE_WEAK, TEST_WEAK)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                          text=True, check=True).stdout.strip()
    scope = changed_lines(wt, base)
    assert "lib.py" in scope
    assert not any(rel.startswith("tests/") for rel in scope)
