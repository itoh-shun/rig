"""The coverage ratchet for the prompt evaluation gate.

`--require-cases` is correct as a destination and unreachable as a starting
point: with an empty `evals/cases/` it fails every change that touches a prompt
surface — including the change that would add the first case. A gate that fires
on everything reports nothing, and teaches people to merge past it, which is a
habit that then applies to the checks that *do* carry signal.

`--ratchet` states the same requirement as a direction. Not having written a
case yet is debt: counted, named, survivable. Taking away coverage somebody
already earned with a measured red→green run is a regression, and still fatal.
"""

import copy
import json
import pathlib
import subprocess

from test_eval_cases import valid_case


def _git(repo: pathlib.Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=repo, check=True,
                               capture_output=True, text=True)
    return completed.stdout.strip()


def _repo(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "eval@test.invalid")
    _git(repo, "config", "user.name", "eval-test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _write_case(repo: pathlib.Path, case_id: str, surfaces: list[str]) -> pathlib.Path:
    from rig_workbench.eval.cases import canonical_json

    case = copy.deepcopy(valid_case())
    case["id"] = case_id
    case["target_inputs"] = {"prompt_surface_fixture": f"binding for {case_id}"}
    case["prompt_surfaces"] = surfaces
    path = repo / "evals" / "cases" / case_id / "case.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(case), encoding="utf-8")
    return path


def _touch(repo: pathlib.Path, relative: str, text: str = "changed\n") -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit(repo: pathlib.Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


INSTRUCTION = "skills/engine/facets/instructions/login.md"
PERSONA = "skills/engine/facets/personas/reviewer.md"


def analyze(repo, base, head="working", **kwargs):
    from rig_workbench.eval.affected import analyze_affected

    return analyze_affected(repo, base=base, head=head, **kwargs)


# ── the bootstrap problem the ratchet exists to solve ────────────────────────
def test_strict_mode_blocks_a_surface_with_no_case_yet(tmp_path):
    repo, base = _repo(tmp_path)
    _touch(repo, INSTRUCTION)
    report = analyze(repo, base, require_cases=True)
    assert report["status"] == "uncovered"
    assert report["uncovered"] == [INSTRUCTION]


def test_the_ratchet_reports_the_same_surface_as_debt_and_lets_it_through(tmp_path):
    repo, base = _repo(tmp_path)
    _touch(repo, INSTRUCTION)
    report = analyze(repo, base, ratchet=True)
    assert report["status"] == "debt"
    assert report["coverage_debt"] == [INSTRUCTION]
    assert report["uncovered"] == []
    assert report["coverage_regressions"] == []


def test_debt_still_names_the_commits_that_created_it(tmp_path):
    """Survivable is not the same as invisible: the paths and their commits are
    still reported, which is what makes paying the debt down a visible task."""
    repo, base = _repo(tmp_path)
    _touch(repo, INSTRUCTION)
    head = _commit(repo, "touch the instruction")
    report = analyze(repo, base, head=head, ratchet=True)
    assert report["surface_commits"][INSTRUCTION] == [head[:7]] or \
        report["surface_commits"][INSTRUCTION][0].startswith(head[:7])


def test_a_covered_surface_passes_under_the_ratchet(tmp_path):
    repo, base = _repo(tmp_path)
    _write_case(repo, "login-case", ["instruction:login"])
    _touch(repo, INSTRUCTION)
    report = analyze(repo, base, ratchet=True)
    assert report["status"] == "pass"
    assert report["coverage_debt"] == [] and report["uncovered"] == []


def test_a_change_touching_no_prompt_surface_is_still_a_noop(tmp_path):
    repo, base = _repo(tmp_path)
    _touch(repo, "rig_workbench/whatever.py")
    assert analyze(repo, base, ratchet=True)["status"] == "noop"


# ── what stays fatal ─────────────────────────────────────────────────────────
def test_an_unregistered_surface_kind_still_fails_under_the_ratchet(tmp_path):
    """A file under a registered root whose kind the registry does not recognise is
    a surface nobody is tracking at all. A ratchet on an unmeasured thing is nothing."""
    repo, base = _repo(tmp_path)
    _touch(repo, "skills/engine/recipes/notes.txt")
    report = analyze(repo, base, ratchet=True)
    assert report["status"] == "uncovered"
    assert "skills/engine/recipes/notes.txt" in report["uncovered"]


def test_deleting_a_case_is_a_regression(tmp_path):
    repo, base = _repo(tmp_path)
    _write_case(repo, "login-case", ["instruction:login"])
    _touch(repo, INSTRUCTION)
    base = _commit(repo, "add coverage")
    (repo / "evals" / "cases" / "login-case" / "case.json").unlink()
    _touch(repo, INSTRUCTION, "changed again\n")
    head = _commit(repo, "remove the case")
    report = analyze(repo, base, head=head, ratchet=True)
    assert report["status"] == "uncovered"
    assert any("login-case" in item and "deleted" in item
               for item in report["coverage_regressions"])


def test_narrowing_a_cases_surfaces_is_a_regression(tmp_path):
    """The subtler way to lose coverage: keep the file, drop the binding."""
    repo, base = _repo(tmp_path)
    _write_case(repo, "wide-case", ["instruction:login", "persona:reviewer"])
    _touch(repo, INSTRUCTION)
    _touch(repo, PERSONA)
    base = _commit(repo, "add wide coverage")
    _write_case(repo, "wide-case", ["instruction:login"])
    _touch(repo, PERSONA, "changed again\n")
    head = _commit(repo, "narrow the case")
    report = analyze(repo, base, head=head, ratchet=True)
    assert report["status"] == "uncovered"
    assert any("persona:reviewer" in item for item in report["coverage_regressions"])


def test_widening_a_case_is_not_a_regression(tmp_path):
    repo, base = _repo(tmp_path)
    _write_case(repo, "growing-case", ["instruction:login"])
    _touch(repo, INSTRUCTION)
    base = _commit(repo, "add coverage")
    _write_case(repo, "growing-case", ["instruction:login", "persona:reviewer"])
    _touch(repo, PERSONA)
    head = _commit(repo, "widen the case")
    report = analyze(repo, base, head=head, ratchet=True)
    assert report["coverage_regressions"] == []
    assert report["status"] == "pass"


def test_adding_a_case_pays_debt_down_without_touching_the_others(tmp_path):
    """The motion the ratchet is for: debt shrinks by one, the rest is still reported."""
    repo, base = _repo(tmp_path)
    _touch(repo, INSTRUCTION)
    _touch(repo, PERSONA)
    assert sorted(analyze(repo, base, ratchet=True)["coverage_debt"]) == \
        sorted([INSTRUCTION, PERSONA])
    _write_case(repo, "login-case", ["instruction:login"])
    report = analyze(repo, base, ratchet=True)
    assert report["coverage_debt"] == [PERSONA]
    assert report["status"] == "debt"


# ── regressions are only claimed when they can be demonstrated ───────────────
def test_strict_mode_does_not_compute_regressions(tmp_path):
    """The two modes are independent: --require-cases keeps its exact old meaning."""
    repo, base = _repo(tmp_path)
    _write_case(repo, "login-case", ["instruction:login"])
    _touch(repo, INSTRUCTION)
    base = _commit(repo, "add coverage")
    (repo / "evals" / "cases" / "login-case" / "case.json").unlink()
    head = _commit(repo, "remove the case")
    report = analyze(repo, base, head=head, require_cases=True)
    assert report["coverage_regressions"] == []


def test_an_unreadable_base_tree_does_not_invent_a_regression(tmp_path):
    """`_coverage_at` returning None must read as "cannot tell", not "everything was
    deleted" — accusing a change of a regression that cannot be demonstrated is the
    one way this check could become the thing it replaced."""
    from rig_workbench.eval.affected import _regressions

    assert _regressions(None, {}) == []


def test_a_case_that_was_never_approved_is_not_counted_as_lost(tmp_path):
    repo, base = _repo(tmp_path)
    path = _write_case(repo, "draft-case", ["instruction:login"])
    value = json.loads(path.read_text(encoding="utf-8"))
    value["status"] = "draft"
    path.write_text(json.dumps(value), encoding="utf-8")
    _touch(repo, INSTRUCTION)
    base = _commit(repo, "add a draft")
    path.unlink()
    head = _commit(repo, "remove the draft")
    report = analyze(repo, base, head=head, ratchet=True)
    assert report["coverage_regressions"] == []


# ── the CLI contract CI depends on ───────────────────────────────────────────
def run_cli(repo, *args):
    import os
    import sys

    env = dict(os.environ)
    env["PYTHONPATH"] = str(pathlib.Path(__file__).parents[1])
    return subprocess.run([sys.executable, "-m", "rig_workbench.cli", "eval", "affected",
                           "--repo", str(repo), *args],
                          capture_output=True, text=True, timeout=60, env=env)


def test_debt_exits_zero_and_uncovered_exits_one(tmp_path):
    repo, base = _repo(tmp_path)
    _touch(repo, INSTRUCTION)
    _commit(repo, "touch the instruction")
    debt = run_cli(repo, "--base", base, "--head", "HEAD", "--ratchet")
    assert debt.returncode == 0
    assert json.loads(debt.stdout)["status"] == "debt"
    strict = run_cli(repo, "--base", base, "--head", "HEAD", "--require-cases")
    assert strict.returncode == 1
    assert json.loads(strict.stdout)["status"] == "uncovered"
