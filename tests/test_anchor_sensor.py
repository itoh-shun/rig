"""Evidence-anchor gate sensor backing `evidence_anchors_resolve` (opt-in).

Covers: the criterion's absence from every default preset and every composed
gate (the whole point — a project opts in through `.rig/gates.json`
extra_criteria, and default gate behaviour is unchanged), activation through
that file, the fail/warning grade split, the reset-to-pending path, the
no-op cases (criterion absent — the one that stays silent — plus no worktree,
no base commit and no bodies, which explain themselves), the explicit
`--set evidence_anchors_resolve=passed` escape hatch (anchor_override,
sticky), and the fail-grade gate integration in a scratch repo through the
CLI — the only path that proves the call actually sits inside cmd_gate.
"""

import json
import os
import pathlib
import re
import subprocess
import sys

from rig_workbench.workbench.anchors import (SENSOR_CRITERION,
                                             apply_anchor_sensor,
                                             review_bodies, scan_task_reviews)
from rig_workbench.workbench.config import GATE_PRESETS, TASK_TYPES
from rig_workbench.workbench.state import build_acceptance

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   cwd=repo, check=True, capture_output=True, text=True)


def make_repo(tmp_path, extra_files=()):
    """Scratch repo whose base commit holds the files a reviewer would cite."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    _git(repo, "init", "-q")
    (repo / "src" / "app.py").write_text("".join(f"line {i}\n" for i in range(1, 6)),
                                         encoding="utf-8")
    for rel, content in extra_files:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                         capture_output=True, text=True).stdout.strip()
    return repo, sha


def make_state(repo, sha, criterion=SENSOR_CRITERION):
    task = {"worktree_path": str(repo), "base_commit": sha, "task_type": "feature"}
    acc = {"checks": [{"name": criterion, "status": "pending", "detail": ""}]}
    return task, acc


def write_body(run_d, persona, text):
    """Record a reviewer body the way `review --body` does."""
    d = pathlib.Path(run_d) / "reviews"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{persona}.md").write_text(text, encoding="utf-8")


# ── (a) opt-in: in no default preset, in no composed default gate ─────────────
def test_criterion_is_absent_from_every_default_preset_and_gate():
    for preset, criteria in GATE_PRESETS.items():
        assert SENSOR_CRITERION not in criteria, preset
    for task_type in TASK_TYPES:
        names = [c["name"] for c in build_acceptance("t", task_type)["checks"]]
        assert SENSOR_CRITERION not in names, task_type


# ── (b) activation through .rig/gates.json extra_criteria ─────────────────────
def test_project_gates_extra_criteria_activates_the_criterion(tmp_path):
    (tmp_path / ".rig").mkdir()
    (tmp_path / ".rig" / "gates.json").write_text(
        json.dumps({"extra_criteria": {"standard": [SENSOR_CRITERION]},
                    "descriptions": {SENSOR_CRITERION: "reviewer file:line anchors resolve"}}),
        encoding="utf-8")
    acc = build_acceptance("t", "feature", tmp_path)
    check = next(c for c in acc["checks"] if c["name"] == SENSOR_CRITERION)
    assert check["status"] == "pending"
    assert check["origin"] == "project"
    # ...and it stays out of the gate of a repo that did not opt in
    assert SENSOR_CRITERION not in [c["name"] for c in build_acceptance("t", "feature")["checks"]]


# ── grades: located-and-wrong fails, could-not-locate warns ───────────────────
def test_line_past_the_end_of_a_found_file_fails_the_criterion(tmp_path):
    repo, sha = make_repo(tmp_path)
    write_body(tmp_path, "security", "1. 握りつぶし（`src/app.py:99`）\n")
    task, acc = make_state(repo, sha)
    notes = apply_anchor_sensor(repo, tmp_path, task, acc)
    check = acc["checks"][0]
    assert check["status"] == "failed"
    assert check["anchor_findings"] == [
        "reviews/security.md:1 [line_out_of_range/fail] `src/app.py:99` — line 99 is past "
        "the end of 'src/app.py' (5 line(s) in the worktree, 5 line(s) at the base commit)"]
    assert any(f"{SENSOR_CRITERION} failed" in n for n in notes)


def test_a_file_that_cannot_be_located_is_only_a_warning(tmp_path):
    """Measured on rig's own briefs, most anchors in real prose are bare
    basenames. Phase A counts those; it does not block on them."""
    repo, sha = make_repo(tmp_path)
    write_body(tmp_path, "design", "1. 別件（`app.py:2`）\n")
    task, acc = make_state(repo, sha)
    notes = apply_anchor_sensor(repo, tmp_path, task, acc)
    assert acc["checks"][0]["status"] == "warning"
    assert "[missing/warning]" in acc["checks"][0]["anchor_findings"][0]
    assert any("recorded as warning" in n for n in notes)


def test_a_body_with_no_anchors_annotates_the_criterion_without_failing_it(tmp_path):
    """Warning-grade only → warning, the `apply_injection_sensor` convention for
    its phrase findings. The point is that it is not *silent*: an anchorless body
    used to leave the criterion untouched, i.e. a sensor reporting on a review it
    never actually inspected."""
    repo, sha = make_repo(tmp_path)
    write_body(tmp_path, "design", "判定: APPROVE\n根拠:\n1. 特に問題なし\n")
    task, acc = make_state(repo, sha)
    notes = apply_anchor_sensor(repo, tmp_path, task, acc)
    check = acc["checks"][0]
    assert check["status"] == "warning"
    assert check["anchor_findings"] == [
        "reviews/design.md:0 [no_anchors/warning] no `path:line` evidence anchor in this body — "
        "calling it clean would be a sensor passing on something it never inspected"]
    assert any("1 review body file(s) with no evidence anchor at all" in n for n in notes)
    # ...and it is not reported as an anchor that failed to resolve
    assert not any("1 evidence anchor(s) that could not be located" in n for n in notes)
    # fixing the body clears the status this sensor set
    write_body(tmp_path, "design", "1. （`src/app.py:3`）\n")
    apply_anchor_sensor(repo, tmp_path, task, acc)
    assert check["status"] == "pending"


def test_a_fail_grade_anchor_outranks_an_anchorless_body(tmp_path):
    repo, sha = make_repo(tmp_path)
    write_body(tmp_path, "design", "判定: APPROVE\n")
    write_body(tmp_path, "security", "1. （`src/app.py:99`）\n")
    task, acc = make_state(repo, sha)
    notes = apply_anchor_sensor(repo, tmp_path, task, acc)
    assert acc["checks"][0]["status"] == "failed"
    assert any("1 unresolved evidence anchor(s) and 1 review body file(s) with no evidence "
               "anchor at all (1 fail-grade)" in n for n in notes)


def test_resolving_anchors_leave_the_criterion_pending(tmp_path):
    repo, sha = make_repo(tmp_path)
    write_body(tmp_path, "security", "1. （`src/app.py:3`）\n2. （`src/app.py:1-5`）\n")
    task, acc = make_state(repo, sha)
    notes = apply_anchor_sensor(repo, tmp_path, task, acc)
    assert acc["checks"][0]["status"] == "pending"
    assert "anchor_findings" not in acc["checks"][0]
    # pending, but pending *after looking* — the counts say so out loud, so this
    # cannot be mistaken for the criterion nobody evaluated
    assert len(notes) == 1
    assert "2 anchor(s) in 1 body file(s): 2 resolved, 0 unresolved, 0 skipped" in notes[0]


def test_skipped_anchors_are_counted_but_never_a_finding(tmp_path):
    """A body holding *nothing but* skips has no finding to print alongside, and
    used to reach the gate in total silence with the criterion left pending —
    the sensor proving compliance by not looking."""
    repo, sha = make_repo(tmp_path)
    (repo / "package-lock.json").write_text('{"x": 1}\n', encoding="utf-8")
    write_body(tmp_path, "security", "1. （`package-lock.json:1`）\n")
    task, acc = make_state(repo, sha)
    notes = apply_anchor_sensor(repo, tmp_path, task, acc)
    assert acc["checks"][0]["status"] == "pending"
    assert "anchor_findings" not in acc["checks"][0]  # nothing to report on the check...
    assert any("1 anchor(s) skipped (not judged): generated" in n for n in notes)  # ...but said
    assert any("0 resolved, 0 unresolved, 1 skipped" in n for n in notes)
    scan = scan_task_reviews(tmp_path, repo, sha)
    assert [s["kind"] for s in scan.skipped] == ["generated"]


# ── (c) findings disappearing resets the status this sensor set ───────────────
def test_sensor_resets_its_own_verdict_when_the_anchors_are_fixed(tmp_path):
    repo, sha = make_repo(tmp_path)
    write_body(tmp_path, "security", "1. （`src/app.py:99`）\n")
    task, acc = make_state(repo, sha)
    apply_anchor_sensor(repo, tmp_path, task, acc)
    assert acc["checks"][0]["status"] == "failed"
    write_body(tmp_path, "security", "1. （`src/app.py:3`）\n")
    notes = apply_anchor_sensor(repo, tmp_path, task, acc)
    assert acc["checks"][0]["status"] == "pending"
    assert acc["checks"][0]["detail"] == ""
    assert "anchor_findings" not in acc["checks"][0]
    assert any("reset to pending" in n for n in notes)


def test_reset_leaves_a_failure_this_sensor_did_not_write(tmp_path):
    """Only the status carrying our detail prefix is ours to clear: a failure
    somebody else recorded on the same check must survive."""
    repo, sha = make_repo(tmp_path)
    write_body(tmp_path, "security", "1. （`src/app.py:99`）\n")
    task, acc = make_state(repo, sha)
    apply_anchor_sensor(repo, tmp_path, task, acc)
    acc["checks"][0]["detail"] = "manually failed by the reviewer"
    write_body(tmp_path, "security", "1. （`src/app.py:3`）\n")
    apply_anchor_sensor(repo, tmp_path, task, acc)
    assert acc["checks"][0]["status"] == "failed"  # not ours to clear
    assert acc["checks"][0]["detail"] == "manually failed by the reviewer"
    assert "anchor_findings" not in acc["checks"][0]


def test_the_same_broken_anchor_twice_is_two_findings(tmp_path):
    """Resolution verdicts are memoized per (path, start, end) so a body with 40
    anchors does not cost 80 subprocesses — but each occurrence is still its own
    finding, at its own body line."""
    repo, sha = make_repo(tmp_path)
    write_body(tmp_path, "security",
               "1. （`src/app.py:99`）\n2. 別の主張だが同じ行（`src/app.py:99`）\n")
    task, acc = make_state(repo, sha)
    apply_anchor_sensor(repo, tmp_path, task, acc)
    findings = acc["checks"][0]["anchor_findings"]
    assert len(findings) == 2
    assert findings[0].startswith("reviews/security.md:1 [line_out_of_range/fail]")
    assert findings[1].startswith("reviews/security.md:2 [line_out_of_range/fail]")
    # identical verdict text — the memo hands the cached verdict to both
    assert findings[0].split("] ", 1)[1] == findings[1].split("] ", 1)[1]


# ── (d) no-ops ────────────────────────────────────────────────────────────────
def test_a_gate_without_the_criterion_is_the_one_silent_noop(tmp_path):
    """The opt-in guarantee: a project that did not add the criterion must not
    hear from this sensor at all. Every *other* no-op explains itself."""
    repo, sha = make_repo(tmp_path)
    write_body(tmp_path, "security", "1. （`src/app.py:99`）\n")
    task, acc = make_state(repo, sha, criterion="tests_pass_or_explained")
    assert apply_anchor_sensor(repo, tmp_path, task, acc) == []
    assert acc["checks"][0]["status"] == "pending"


def test_every_other_noop_says_why_it_did_not_evaluate(tmp_path):
    """A criterion left `pending` in silence is indistinguishable from one that
    was checked and had nothing to say. The worktree-less case is not
    hypothetical: `review`/`security_review` tasks route without a worktree, so
    the sensor can never fire on them and has to say so."""
    repo, sha = make_repo(tmp_path)
    write_body(tmp_path, "security", "1. （`src/app.py:99`）\n")
    _task, acc = make_state(repo, sha)

    notes = apply_anchor_sensor(repo, tmp_path, {"worktree_path": None, "base_commit": sha}, acc)
    assert any("no worktree" in n and "security_review" in n for n in notes)
    assert acc["checks"][0]["status"] == "pending"  # explains, never judges

    notes = apply_anchor_sensor(repo, tmp_path,
                                {"worktree_path": str(repo), "base_commit": ""}, acc)
    assert any("no base commit" in n for n in notes)

    # a worktree that was recorded but is gone (discarded)
    notes = apply_anchor_sensor(repo, tmp_path,
                                {"worktree_path": str(tmp_path / "gone"), "base_commit": sha}, acc)
    assert any("no worktree" in n for n in notes)

    # criterion opted in, but no reviewer body was ever recorded
    task, acc = make_state(repo, sha)
    notes = apply_anchor_sensor(repo, tmp_path / "empty-run", task, acc)
    assert any("no reviewer bodies recorded" in n and "--body" in n for n in notes)
    assert acc["checks"][0]["status"] == "pending"


def test_a_verdict_without_a_body_file_is_not_a_finding(tmp_path):
    """`--body` refuses persona names that cannot be a filename, so a recorded
    verdict may have no body. reviews/ is the population; review.json is not
    consulted."""
    run_d = tmp_path / "run"
    run_d.mkdir()
    (run_d / "review.json").write_text(
        json.dumps({"verdicts": [{"persona": "rig:security-reviewer", "verdict": "APPROVE"}]}),
        encoding="utf-8")
    assert review_bodies(run_d) == []
    repo, sha = make_repo(tmp_path)
    task, acc = make_state(repo, sha)
    notes = apply_anchor_sensor(repo, run_d, task, acc)
    assert acc["checks"][0]["status"] == "pending"  # not a finding against anyone…
    assert any("no reviewer bodies recorded" in n for n in notes)  # …but not silent either


# ── (e) explicit --set passed is recorded and sticks ──────────────────────────
def test_explicit_pass_is_recorded_and_sticks(tmp_path):
    repo, sha = make_repo(tmp_path)
    write_body(tmp_path, "security", "1. （`src/app.py:99`）\n")
    task, acc = make_state(repo, sha)
    acc["checks"][0]["status"] = "passed"
    notes = apply_anchor_sensor(repo, tmp_path, task, acc, explicit_set={SENSOR_CRITERION})
    assert acc["checks"][0]["status"] == "passed"
    assert acc["checks"][0]["anchor_override"] is True
    assert any("manual override" in n for n in notes)
    # ...and the override survives later evaluations without --set
    notes = apply_anchor_sensor(repo, tmp_path, task, acc)
    assert acc["checks"][0]["status"] == "passed"
    assert any("manual override previously recorded" in n for n in notes)


# ── end to end through cmd_gate (scratch repo, real worktree, real CLI) ───────
def cli(repo, wt_root, *args):
    env = dict(os.environ, RIG_WORKTREE_ROOT=str(wt_root))
    return subprocess.run([sys.executable, str(WORKBENCH), *args],
                          cwd=repo, capture_output=True, text=True, timeout=60, env=env)


GATES_JSON = json.dumps({"extra_criteria": {"standard": [SENSOR_CRITERION]}}) + "\n"


def test_gate_integration_opt_in_criterion_fails_on_a_broken_anchor(tmp_path):
    repo, _sha = make_repo(tmp_path, extra_files=((".rig/gates.json", GATES_JSON),))
    wt_root = tmp_path / "wt"

    r = cli(repo, wt_root, "new", "add a thing", "--type", "feature", "--slug", "thing")
    assert r.returncode == 0, r.stderr
    task_id = re.search(r"task_id: (\S+)", r.stdout).group(1)

    body = tmp_path / "security.md"
    body.write_text("判定: REJECT\n1. 実在（`src/app.py:3`）\n2. 行超過（`src/app.py:99`）\n",
                    encoding="utf-8")
    r = cli(repo, wt_root, "review", task_id, "--set", "security=REJECT",
            "--body", f"security=@{body}")
    assert r.returncode == 0, r.stderr

    r = cli(repo, wt_root, "gate", task_id)
    assert r.returncode == 1, r.stdout + r.stderr  # fail-grade: gate is FAILED
    assert SENSOR_CRITERION in r.stdout
    assert "line_out_of_range" in r.stdout

    acc = json.loads((repo / ".rig" / "runs" / task_id / "acceptance.json").read_text(encoding="utf-8"))
    check = next(c for c in acc["checks"] if c["name"] == SENSOR_CRITERION)
    assert check["status"] == "failed"
    assert any("reviews/security.md:3" in ln for ln in check["anchor_findings"])

    # findings surface in status rendering with the distinctive prefix
    r = cli(repo, wt_root, "status", task_id)
    assert r.returncode == 0, r.stderr
    assert "anchor: reviews/security.md:3 [line_out_of_range/fail]" in r.stdout

    # documented escape hatch, through the CLI
    r = cli(repo, wt_root, "gate", task_id, "--set", f"{SENSOR_CRITERION}=passed")
    assert r.returncode == 0, r.stdout + r.stderr
    acc = json.loads((repo / ".rig" / "runs" / task_id / "acceptance.json").read_text(encoding="utf-8"))
    check = next(c for c in acc["checks"] if c["name"] == SENSOR_CRITERION)
    assert check["status"] == "passed" and check.get("anchor_override") is True


def test_gate_integration_default_repo_never_sees_the_criterion(tmp_path):
    """The same scenario without `.rig/gates.json`: the gate must not contain
    the criterion, so the broken anchor changes nothing."""
    repo, _sha = make_repo(tmp_path)
    wt_root = tmp_path / "wt"
    r = cli(repo, wt_root, "new", "add a thing", "--type", "feature", "--slug", "thing")
    task_id = re.search(r"task_id: (\S+)", r.stdout).group(1)
    body = tmp_path / "security.md"
    body.write_text("1. 行超過（`src/app.py:99`）\n", encoding="utf-8")
    cli(repo, wt_root, "review", task_id, "--set", "security=REJECT", "--body", f"security=@{body}")

    r = cli(repo, wt_root, "gate", task_id)
    assert r.returncode == 0, r.stdout + r.stderr
    assert SENSOR_CRITERION not in r.stdout
    acc = json.loads((repo / ".rig" / "runs" / task_id / "acceptance.json").read_text(encoding="utf-8"))
    assert SENSOR_CRITERION not in [c["name"] for c in acc["checks"]]
