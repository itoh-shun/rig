"""Instincts converted into recipe `checks:` (T8).

The conversion runs off a table of recognized shapes, because deciding that a sentence
describes a mechanically detectable condition is judgment and code cannot do it from
free text. So the interesting properties are not "does it match" but: does it report
what it could not recognize, does it refuse to guess, and does editing a hand-written
recipe leave the rest of that file alone.
"""

import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench.check_synthesis import (RULES, RecipeEditError,
                                                     add_checks_to_recipe,
                                                     synthesize)
from rig_workbench.workbench.instincts import add_instinct, promote_instinct

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"

RECIPE = """---
name: demo
description: a hand-written recipe
scope: project
steps:
  - id: implement
    instruction: implement
    pattern: serial
  - id: verify
    instruction: verify
    pattern: serial
    checks:
      - "python3 -m pytest -q"
autonomy: interactive
---

# demo

Prose below the frontmatter, with a comment nobody wants reflowed.
"""


@pytest.fixture(autouse=True)
def isolated_host_tier(tmp_path, monkeypatch):
    monkeypatch.setenv("RIG_USER_HOME", str(tmp_path / "host-home"))


@pytest.fixture
def recipe(tmp_path):
    p = tmp_path / ".rig" / "recipes" / "demo.md"
    p.parent.mkdir(parents=True)
    p.write_text(RECIPE, encoding="utf-8")
    return p


# ---- synthesize ---------------------------------------------------------------

def test_a_recognized_instinct_produces_its_rule(tmp_path):
    add_instinct(tmp_path, "measurement subprocesses running claude -p must pass --safe-mode",
                 "observed", None, 0.9)

    matched, unmatched = synthesize(tmp_path)

    assert [m.rule.id for m in matched] == ["claude-p-needs-safe-mode"]
    assert unmatched == []


def test_an_unrecognized_instinct_is_returned_not_dropped(tmp_path):
    """Covering 6 of 40 candidates and printing only the 6 would read as
    "the other 34 were fine"."""
    add_instinct(tmp_path, "the deployment runbook lives in the ops wiki", "e", None, 0.9)

    matched, unmatched = synthesize(tmp_path)

    assert matched == []
    assert [r["text"] for r in unmatched] == ["the deployment runbook lives in the ops wiki"]


def test_low_confidence_instincts_are_left_out(tmp_path):
    add_instinct(tmp_path, "subprocess claude -p needs --safe-mode", "e", None, 0.3)

    matched, unmatched = synthesize(tmp_path)

    assert matched == [] and unmatched == []


def test_the_confidence_floor_is_adjustable(tmp_path):
    add_instinct(tmp_path, "subprocess claude -p needs --safe-mode", "e", None, 0.3)

    matched, _ = synthesize(tmp_path, min_confidence=0.2)

    assert [m.rule.id for m in matched] == ["claude-p-needs-safe-mode"]


def test_two_instincts_describing_one_condition_yield_one_check(tmp_path):
    """The cross-tier duplicate found in this repo's own store is exactly this shape."""
    add_instinct(tmp_path, "always pass --safe-mode when running claude -p", "e", None, 0.9)
    add_instinct(tmp_path, "claude -p without --safe-mode returns a hook message", "e", None, 0.85)

    matched, _ = synthesize(tmp_path)

    assert len(matched) == 1


def test_a_promoted_instinct_is_a_synthesis_candidate_too(tmp_path):
    add_instinct(tmp_path, "subprocess claude -p requires --safe-mode", "e", None, 0.9)
    promote_instinct(tmp_path, add_instinct(
        tmp_path, "gh pr merge --auto merges immediately here", "e", None, 0.9)["id"])

    matched, _ = synthesize(tmp_path)

    assert {m.rule.id for m in matched} == {"claude-p-needs-safe-mode", "no-gh-pr-merge-auto"}


def test_every_rule_is_a_command_that_passes_on_a_clean_tree(tmp_path):
    """The polarity `_run_step_checks` expects: exit 0 when the condition is absent.
    A rule that fails on an empty repository would fire on every run and be ignored."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("nothing interesting\n", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=tmp_path, check=True)

    for rule in RULES:
        if "CLAUDECODE" in rule.command:
            continue  # asserts on the ambient environment, not on the tree
        r = subprocess.run(rule.command, shell=True, cwd=tmp_path, capture_output=True)
        assert r.returncode == 0, f"{rule.id} fired on a clean tree: {r.stderr!r}"


def test_the_safe_mode_rule_actually_catches_the_condition(tmp_path):
    """The acceptance criterion from the plan: the generated check has to detect a real
    `claude -p` that is missing --safe-mode."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "measure.sh").write_text("claude -p 'hello' --output-format text\n", encoding="utf-8")
    subprocess.run(["git", "add", "measure.sh"], cwd=tmp_path, check=True)
    rule = next(r for r in RULES if r.id == "claude-p-needs-safe-mode")

    assert subprocess.run(rule.command, shell=True, cwd=tmp_path).returncode != 0

    (tmp_path / "measure.sh").write_text("claude -p 'hello' --safe-mode\n", encoding="utf-8")
    subprocess.run(["git", "add", "measure.sh"], cwd=tmp_path, check=True)

    assert subprocess.run(rule.command, shell=True, cwd=tmp_path).returncode == 0


# ---- add_checks_to_recipe -----------------------------------------------------

def test_checks_are_appended_to_an_existing_list(recipe):
    added = add_checks_to_recipe(recipe, "verify", ["test -f README.md"])

    body = recipe.read_text(encoding="utf-8")
    assert added == ["test -f README.md"]
    assert '      - "python3 -m pytest -q"\n      - "test -f README.md"' in body


def test_a_step_without_checks_gets_the_key_created(recipe):
    add_checks_to_recipe(recipe, "implement", ["test -d src"])

    body = recipe.read_text(encoding="utf-8")
    assert "  - id: implement\n    checks:\n      - \"test -d src\"" in body


def test_the_rest_of_the_file_is_left_alone(recipe):
    """A recipe is hand-written. Round-tripping the YAML would reflow every unrelated
    line and turn a two-line addition into a whole-file diff nobody can review."""
    before = recipe.read_text(encoding="utf-8")

    add_checks_to_recipe(recipe, "verify", ["test -f README.md"])

    after = recipe.read_text(encoding="utf-8")
    assert "Prose below the frontmatter, with a comment nobody wants reflowed." in after
    added_lines = [ln for ln in after.splitlines() if ln not in before.splitlines()]
    assert added_lines == ['      - "test -f README.md"']


def test_an_already_present_check_is_not_duplicated(recipe):
    assert add_checks_to_recipe(recipe, "verify", ["python3 -m pytest -q"]) == []


def test_the_last_step_is_the_default_target(recipe):
    add_checks_to_recipe(recipe, None, ["test -f README.md"])

    verify_block = recipe.read_text(encoding="utf-8").split("- id: verify")[1]
    assert "test -f README.md" in verify_block


def test_an_unknown_step_is_an_error_not_a_guess(recipe):
    with pytest.raises(RecipeEditError, match="nosuchstep"):
        add_checks_to_recipe(recipe, "nosuchstep", ["true"])


def test_a_missing_recipe_is_an_error_not_an_invention(tmp_path):
    with pytest.raises(RecipeEditError, match="not found"):
        add_checks_to_recipe(tmp_path / "nope.md", None, ["true"])


def test_a_command_needing_both_quote_styles_is_refused(recipe):
    with pytest.raises(RecipeEditError, match="both quote styles"):
        add_checks_to_recipe(recipe, "verify", ["""grep "a" 'b'"""])


def test_a_double_quoted_command_is_written_in_single_quotes(recipe):
    add_checks_to_recipe(recipe, "verify", ['test -n "$(git status)"'])

    assert """- 'test -n "$(git status)"'""" in recipe.read_text(encoding="utf-8")


# ---- CLI ----------------------------------------------------------------------

def run_cli(args, cwd):
    return subprocess.run([sys.executable, str(WORKBENCH), *args],
                          capture_output=True, text=True, cwd=cwd, timeout=60)


@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def test_cli_dry_run_writes_nothing(git_repo, recipe):
    run_cli(["instincts", "--add", "run claude -p with --safe-mode", "--confidence", "0.9"], git_repo)
    before = recipe.read_text(encoding="utf-8")

    r = run_cli(["instincts", "--generate-checks", "--recipe", "demo", "--dry-run"], git_repo)

    assert r.returncode == 0, r.stdout + r.stderr
    assert "claude-p-needs-safe-mode" in r.stdout
    assert recipe.read_text(encoding="utf-8") == before


def test_cli_writes_into_the_project_recipe(git_repo, recipe):
    run_cli(["instincts", "--add", "run claude -p with --safe-mode", "--confidence", "0.9"], git_repo)

    r = run_cli(["instincts", "--generate-checks", "--recipe", "demo", "--step", "verify"], git_repo)

    assert r.returncode == 0, r.stdout + r.stderr
    assert "--safe-mode" in recipe.read_text(encoding="utf-8")


def test_cli_reports_what_no_rule_recognized(git_repo, recipe):
    run_cli(["instincts", "--add", "the runbook lives in the ops wiki", "--confidence", "0.9"], git_repo)

    r = run_cli(["instincts", "--generate-checks", "--recipe", "demo", "--dry-run"], git_repo)

    assert "no rule matched (1)" in r.stdout


def test_cli_fails_loudly_on_a_missing_project_recipe(git_repo):
    run_cli(["instincts", "--add", "run claude -p with --safe-mode", "--confidence", "0.9"], git_repo)

    r = run_cli(["instincts", "--generate-checks", "--recipe", "nosuchrecipe"], git_repo)

    assert r.returncode != 0
    assert "recipe not found" in (r.stdout + r.stderr)


def test_cli_says_the_checks_come_from_unverified_records(git_repo, recipe):
    run_cli(["instincts", "--add", "run claude -p with --safe-mode", "--confidence", "0.9"], git_repo)

    r = run_cli(["instincts", "--generate-checks", "--recipe", "demo"], git_repo)

    assert "unverified" in r.stdout


def test_generated_checks_survive_the_recipe_parser(git_repo, recipe):
    """A written check that the recipe loader cannot read is worse than no check."""
    from rig_workbench.orchestrate.recipes import load_steps, parse_frontmatter

    run_cli(["instincts", "--add", "run claude -p with --safe-mode", "--confidence", "0.9"], git_repo)
    run_cli(["instincts", "--generate-checks", "--recipe", "demo", "--step", "verify"], git_repo)

    steps = load_steps(parse_frontmatter(recipe))
    verify = next(s for s in steps if s["id"] == "verify")
    assert any("--safe-mode" in c for c in verify["checks"])
    assert "python3 -m pytest -q" in verify["checks"]


def test_json_shape_of_an_instinct_is_untouched_by_synthesis(git_repo):
    """Synthesis reads; it must not mutate the store the way injection does."""
    run_cli(["instincts", "--add", "run claude -p with --safe-mode", "--confidence", "0.9"], git_repo)
    path = git_repo / ".rig" / "instincts.jsonl"
    before = json.loads(path.read_text(encoding="utf-8").splitlines()[0])

    run_cli(["instincts", "--generate-checks", "--recipe", "demo", "--dry-run"], git_repo)

    assert json.loads(path.read_text(encoding="utf-8").splitlines()[0]) == before


def test_no_instinct_text_ever_reaches_the_generated_command(git_repo, recipe):
    """Checks are executed with `shell=True` (providers._run_step_checks). The commands
    come from the fixed RULES table and are never built from an instinct's text, so a
    record cannot smuggle a shell fragment into a recipe by being worded a certain way.
    Losing this property would turn the instinct store into a remote-execution surface."""
    run_cli(["instincts", "--add",
             "run claude -p with --safe-mode; touch /tmp/rig-pwned-$$ #",
             "--confidence", "0.9"], git_repo)

    run_cli(["instincts", "--generate-checks", "--recipe", "demo", "--step", "verify"], git_repo)

    body = recipe.read_text(encoding="utf-8")
    assert "rig-pwned" not in body
    assert all(ln.strip().lstrip('- ').strip('"\'') in {r.command for r in RULES}
               | {"python3 -m pytest -q"}
               for ln in body.splitlines() if ln.strip().startswith("- \"") or ln.strip().startswith("- '"))
