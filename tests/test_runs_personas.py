"""`orchestrate runs --personas`: verdict kinds, and surviving a hand-written record.

Two properties the single-table version could not hold:

  * Not every producer of a verdict is a reviewer. `adaptive-budget` is `ok=False`
    whenever the invocation budget is exhausted and `adaptive-repair` is `ok=True`
    whenever a mechanical check exited zero, so their REJECT% is fixed by the code
    that emits them, not by the code under review. Averaging them into one column
    with real lenses is what let "123 votes, 100% REJECT" read as a degenerate
    reviewer when it was a budget notice.
  * runs.jsonl is not written solely by `telemetry_append` — SKILL.md §6 has the
    manual and workflow backends append their own lines — so a record can arrive
    with the wrong shape. One malformed row must not take out the aggregate.
"""

import json
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ORCHESTRATE = REPO_ROOT / "scripts" / "orchestrate.py"


def run_cli(args, cwd):
    return subprocess.run([sys.executable, str(ORCHESTRATE), *args],
                          capture_output=True, text=True, cwd=cwd, timeout=120)


def write_runs(repo: pathlib.Path, rows: list[dict]) -> None:
    path = repo / ".rig" / "runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def run_row(verdicts: list[dict], recipe: str = "r") -> dict:
    return {"ts": "2026-08-14T00:00:00+09:00", "recipe": recipe, "backend": "orchestrate",
            "final": "DONE", "steps_total": 1, "steps_passed": 1, "retries": 0,
            "steps": [{"id": "review", "status": "passed", "retries": 0, "verdicts": verdicts}]}


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def section(out: str, heading_word: str) -> str:
    """The block of output under one kind heading, up to the next blank line."""
    lines = out.splitlines()
    start = next(i for i, ln in enumerate(lines) if heading_word in ln)
    body = []
    for ln in lines[start + 1:]:
        if not ln.strip():
            break
        body.append(ln)
    return "\n".join(body)


def test_mechanisms_are_listed_apart_from_reviewers(repo):
    write_runs(repo, [run_row([{"by": "adaptive-budget", "ok": False},
                               {"by": "adaptive-repair", "ok": True},
                               {"by": "reviewer:security-reviewer", "ok": False}])])

    r = run_cli(["runs", "--personas"], repo)

    assert r.returncode == 0, r.stdout + r.stderr
    mechanisms = section(r.stdout, "mechanisms")
    reviewers = section(r.stdout, "reviewers")
    assert "adaptive-budget" in mechanisms and "adaptive-repair" in mechanisms
    assert "adaptive-budget" not in reviewers and "adaptive-repair" not in reviewers
    assert "reviewer:security-reviewer" in reviewers


def test_mock_verifiers_are_listed_as_fixtures(repo):
    write_runs(repo, [run_row([{"by": "mock:test-reviewer", "ok": False},
                               {"by": "reviewer:design-reviewer", "ok": True}])])

    r = run_cli(["runs", "--personas"], repo)

    assert "mock:test-reviewer" in section(r.stdout, "fixtures")
    assert "mock:test-reviewer" not in section(r.stdout, "reviewers")


def test_unknown_names_count_as_reviewers(repo):
    """A lens nobody has taught this classifier about must stay visible where the
    signal is read, not disappear into a bucket that is skimmed past."""
    write_runs(repo, [run_row([{"by": "house-authenticity", "ok": True}])])

    r = run_cli(["runs", "--personas"], repo)

    assert "house-authenticity" in section(r.stdout, "reviewers")


def test_pruning_hint_ignores_mechanisms_and_fixtures(repo):
    """`adaptive-repair` never rejects because it only exists when a check passed,
    and a fixture's rate is whatever a test needed. Calling either one rubber-stamping
    states a finding about review quality that the evidence does not support."""
    rows = [run_row([{"by": "adaptive-repair", "ok": True},
                     {"by": "mock:design-reviewer", "ok": True}]) for _ in range(6)]
    write_runs(repo, rows)

    r = run_cli(["runs", "--personas"], repo)

    assert "Pruning hint" not in r.stdout


def test_pruning_hint_still_fires_for_a_reviewer_that_never_rejects(repo):
    write_runs(repo, [run_row([{"by": "rig:security-reviewer", "ok": True}]) for _ in range(6)])

    r = run_cli(["runs", "--personas"], repo)

    assert "Pruning hint" in r.stdout
    assert "rig:security-reviewer" in r.stdout.split("Pruning hint")[1]


def test_a_hand_written_record_does_not_break_the_aggregate(repo):
    """SKILL.md §6 assigns the manual-backend append to the model, in prose. This is
    the shape that assignment actually produced in this repo's own log."""
    hand_written = {"ts": "2026-08-14T13:51:25+09:00", "recipe": "brainstorm",
                    "backend": "manual", "final": "DONE", "steps": ["brainstorm"],
                    "steps_passed": 1, "steps_total": 1, "retries": 0}
    write_runs(repo, [hand_written, run_row([{"by": "reviewer:test-reviewer", "ok": False}])])

    r = run_cli(["runs", "--personas"], repo)

    assert r.returncode == 0, r.stdout + r.stderr
    assert "reviewer:test-reviewer" in r.stdout


def test_a_hand_written_record_does_not_break_the_default_listing(repo):
    hand_written = {"ts": "2026-08-14T13:51:25+09:00", "recipe": "brainstorm",
                    "backend": "manual", "final": "DONE", "steps": ["brainstorm"],
                    "steps_passed": 1, "steps_total": 1, "retries": 0}
    write_runs(repo, [hand_written])

    r = run_cli(["runs"], repo)

    assert r.returncode == 0, r.stdout + r.stderr
    assert "brainstorm" in r.stdout
