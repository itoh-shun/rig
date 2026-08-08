"""Making a run legible to somebody who does not already know the recipe.

The registration banner named the chosen recipe and stopped there. `bugfix` is seven
steps, fans out to three reviewers at step six and judges fifteen criteria at step
seven, and none of that appeared anywhere on the path anybody takes — it was one
`orchestrate plan` away, which by rig's own taxonomy is an asset that exists and is not
connected.

Three properties are load-bearing and each is pinned here:

- **the denominator is real** — `steps.json` is seeded from the resolved recipe at
  registration, so `3/7` is the recipe's own count and not a number invented for the
  display;
- **shape decides the display** — twelve shipped recipes have exactly one step, and
  `[▸] 1/1` is a progress bar over a single item, so those show their fan-out and gate
  instead of a position; and
- **a recipe that cannot be read degrades to silence**, never to a failed registration.
  Progress is display metadata; it is never an input to the accept decision.
"""

import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench.flow_view import render_flow, render_transition
from rig_workbench.workbench.progress import (BAR_CURRENT, BAR_DONE, BAR_RETRY,
                                              BAR_SKIP, BAR_TODO, compute,
                                              from_state, load_recipe_steps,
                                              next_action)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"


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


def _step(name, status="pending", **extra):
    return {"name": name, "status": status, "updated_at": None, **extra}


# ── seeding the denominator ──────────────────────────────────────────────────
def test_seeding_reads_the_recipes_declared_steps():
    steps = load_recipe_steps("bugfix")
    assert [s["name"] for s in steps] == ["inspect", "reproduce", "plan", "implement",
                                          "test", "review-diff", "acceptance"]
    assert all(s["status"] == "pending" for s in steps)


def test_seeding_carries_the_fields_the_display_needs():
    by_name = {s["name"]: s for s in load_recipe_steps("bugfix")}
    assert by_name["acceptance"]["gate"] == "acceptance-gate"
    assert by_name["review-diff"]["personas"]


@pytest.mark.parametrize("recipe", ["", "no-such-recipe-anywhere", "../etc/passwd"])
def test_an_unreadable_recipe_seeds_nothing_instead_of_failing(recipe):
    """`new` is where the user asked for a task, not for a recipe audit. A broken
    recipe must cost the progress display and nothing else — `--validate` and `--plan`
    are the loud paths."""
    assert load_recipe_steps(recipe) == []


# ── position ─────────────────────────────────────────────────────────────────
def test_current_is_the_first_unfinished_step_not_the_last_reported():
    """A step reported out of order, or twice, must not make a task look further along
    than it is. `board` used to echo the last name it was told, which reads as a
    position and is not one."""
    steps = [_step("a", "passed"), _step("b"), _step("c", "passed")]
    progress = compute(steps)
    assert progress.current["name"] == "b"
    assert progress.done == 2 and progress.total == 3


def test_skipped_counts_as_done_and_is_marked_distinctly():
    """A conditional step that did not apply is not outstanding work, but it also did
    not run, and a bar that shows both as ✓ hides which."""
    progress = compute([_step("a", "skipped"), _step("b", "passed"), _step("c")])
    assert progress.bar == BAR_SKIP + BAR_DONE + BAR_CURRENT
    assert progress.done == 2


def test_a_failed_step_is_shown_as_a_retry_not_as_progress():
    progress = compute([_step("a", "failed"), _step("b")])
    assert progress.bar == BAR_RETRY + BAR_TODO
    assert progress.done == 0
    assert progress.current["name"] == "a"


def test_all_steps_through_leaves_no_current():
    progress = compute([_step("a", "passed"), _step("b", "passed")])
    assert progress.current is None and progress.nxt is None
    assert progress.label() == "2/2 done"


def test_progress_is_unknown_without_a_seeded_recipe():
    """Runs registered before seeding existed have no denominator. Callers fall back to
    the old display rather than being handed a fabricated one."""
    progress = compute([])
    assert progress.known is False
    assert progress.label() == "-"


def test_an_unseeded_run_never_acquires_a_denominator_by_reporting_steps():
    """The failure this whole flag exists for.

    An unseeded run grows its step list from whatever the model reports, so after one
    ad-hoc report the list holds exactly one entry. Deriving the denominator from
    length would then announce "1/1 — all steps complete" about a run whose step count
    nobody knows, which is a stronger claim than the seeded display ever makes.
    """
    reported = {"steps": [{"name": "adhoc", "status": "passed"}]}
    assert from_state(reported).known is False
    assert from_state({"steps": reported["steps"], "seeded": True}).known is True


def test_from_state_reads_the_seeding_fact_rather_than_guessing():
    seeded = {"steps": [_step("a"), _step("b")], "seeded": True}
    assert from_state(seeded).total == 2
    assert from_state({"steps": [], "seeded": True}).known is False


def test_label_truncates_so_a_column_stays_aligned():
    progress = compute([_step("an-extremely-long-step-identifier")])
    assert progress.label(width=14) == "0/1 an-extremely-…"[:len("0/1 ") + 14]


# ── whose move is it ─────────────────────────────────────────────────────────
def test_next_action_puts_your_own_decision_ahead_of_everything_else():
    progress = compute([_step("a", "passed")])
    assert next_action({"status": "gate_passed"}, progress, "passed").startswith("→")


def test_next_action_names_the_signer_when_a_human_gate_is_current():
    progress = compute([_step("sign", human_gate=True, actor="architect")])
    action = next_action({"status": "running"}, progress, "-")
    assert action.startswith("⏸") and "architect" in action


def test_a_human_gate_outranks_a_settled_gate():
    """Both can be true at once. The signature is the one that stops the run, so it is
    the one the reader needs; showing "accept" next to a parked run invites a person to
    go looking for a diff that is not coming."""
    progress = compute([_step("sign", human_gate=True)])
    assert next_action({"status": "running"}, progress, "passed").startswith("⏸")


def test_every_step_through_but_no_verdict_is_not_reported_as_running():
    """"7/7" beside "rig 実行中" reads as a stuck run. It is a stage, and it has a name."""
    progress = compute([_step("a", "passed")])
    assert next_action({"status": "running"}, progress, "-") == "… ゲート評価待ち"


def test_a_failed_gate_asks_for_the_unmet_criteria_not_for_accept():
    progress = compute([_step("a", "passed")])
    assert "discard" in next_action({"status": "gate_failed"}, progress, "failed")


@pytest.mark.parametrize("status", ["accepted", "discarded"])
def test_a_settled_task_asks_for_nothing(status):
    assert next_action({"status": status}, compute([]), "-").startswith("済")


# ── the map ──────────────────────────────────────────────────────────────────
def test_a_multi_step_recipe_renders_its_steps_and_its_stops():
    out = "\n".join(render_flow(load_recipe_steps("bugfix"), {"checks": [{}] * 15}))
    assert "flow: 7 steps" in out
    assert "acceptance" in out and "implement" in out
    assert "最終ゲートは 15 基準" in out
    assert "◆" in out                                    # the hard stops are marked
    assert "作業ツリーは無傷" in out                        # and discard is stated as free


def test_a_one_step_recipe_shows_its_fan_out_instead_of_a_position():
    """Twelve shipped recipes land here. `[▸] 1/1` is the literal definition of a
    number carrying no information; what is complex about these runs is inside the
    step."""
    out = "\n".join(render_flow(load_recipe_steps("review-only"), {"checks": []}))
    assert "flow: 1 step" in out
    assert "1/1" not in out
    assert "並列" in out or "担当" in out


def test_a_conditional_step_says_it_may_not_run():
    steps = [_step("a"), _step("b", condition="has_tests")]
    out = "\n".join(render_flow(steps, {"checks": []}))
    assert "has_tests" in out and "スキップ" in out


def test_an_unreadable_recipe_renders_no_map_at_all():
    assert render_flow([], {"checks": []}) == []


# ── the transition ───────────────────────────────────────────────────────────
def test_a_transition_says_where_the_run_is_and_what_is_next():
    steps = [_step("a", "passed"), _step("b"), _step("c")]
    out = "\n".join(render_transition(compute(steps)))
    assert "1/3 → b" in out
    assert "次: c" in out


def test_a_retry_is_distinguished_from_a_stalled_run():
    """A bar that will not move looks stuck; the reason it is not moving is the
    attempt, and that has to be on the line."""
    out = "\n".join(render_transition(compute([_step("a", "failed"), _step("b")])))
    assert "↻" in out and "やり直し" in out


def test_the_last_transition_points_at_the_decision():
    out = "\n".join(render_transition(compute([_step("a", "passed")])))
    assert "全 step 完了" in out and "accept" in out


def test_no_transition_without_a_denominator():
    assert render_transition(compute([])) == []


# ── end to end ───────────────────────────────────────────────────────────────
def test_registration_prints_the_flow_of_the_chosen_recipe(git_repo):
    result = run_cli(["new", "fix the login crash", "--type", "bugfix"], git_repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "flow:" in result.stdout

    task_id = sorted(p.name for p in (git_repo / ".rig" / "runs").iterdir())[-1]
    steps = json.loads((git_repo / ".rig" / "runs" / task_id / "steps.json")
                       .read_text(encoding="utf-8"))["steps"]
    assert [s["name"] for s in steps] == [s["name"] for s in load_recipe_steps("bugfix")]


def test_reporting_a_step_prints_the_transition(git_repo):
    assert run_cli(["new", "fix the login crash", "--type", "bugfix"], git_repo).returncode == 0
    task_id = sorted(p.name for p in (git_repo / ".rig" / "runs").iterdir())[-1]

    result = run_cli(["step", task_id, "--set", "inspect=passed"], git_repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1/7" in result.stdout
    assert "reproduce" in result.stdout


def test_the_board_shows_a_position_and_who_is_waiting(git_repo):
    assert run_cli(["new", "fix the login crash", "--type", "bugfix"], git_repo).returncode == 0
    task_id = sorted(p.name for p in (git_repo / ".rig" / "runs").iterdir())[-1]
    run_cli(["step", task_id, "--set", "inspect=passed"], git_repo)

    result = run_cli(["board"], git_repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1/7" in result.stdout
    assert "あなた待ち" in result.stdout


def test_a_run_without_seeded_steps_keeps_the_old_display(git_repo):
    """Tasks registered by an older rig are still on disk. They lose the bar and keep
    exactly what they had — nothing regresses into a fabricated denominator."""
    assert run_cli(["new", "review something", "--type", "bugfix"], git_repo).returncode == 0
    task_id = sorted(p.name for p in (git_repo / ".rig" / "runs").iterdir())[-1]
    steps_path = git_repo / ".rig" / "runs" / task_id / "steps.json"
    steps_path.write_text(json.dumps({"steps": []}), encoding="utf-8")

    result = run_cli(["step", task_id, "--set", "adhoc=passed"], git_repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "adhoc=passed" in result.stdout

    board = run_cli(["board"], git_repo)
    assert "adhoc(passed)" in board.stdout
