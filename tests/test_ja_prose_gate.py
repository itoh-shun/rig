"""The Japanese-prose gate: two diff-conditional criteria, one machine-owned, one a reviewer's.

`ja_lint_clean` and `ja_prose_ai_smell_reviewed` exist on a task's gate only while its
diff adds Japanese prose (like `prompt_regression_passed`), and judge only the added
lines (like `no_secret_leak`). The lint criterion is set by the sensor from
`rig-wb ja-lint` errors; the AI-smell criterion is transcribed from the
`ai-smell-reviewer` verdict in `review.json` and from nothing else — a missing verdict
stays pending, so the lane cannot be skipped by silence. Several tests here exist to
keep that boundary: no prose-rhythm number ever moves the second criterion.
"""

import json
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()


def _fixture(tmp_path, task_type="documentation"):
    from rig_workbench.workbench.state import build_acceptance

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "gate@test.invalid")
    _git(repo, "config", "user.name", "gate-test")
    (repo / "README.md").write_text("# base\n\nこれは元からある一文で、できないことはない。\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    task_id = "rig-20260909-ja-prose"
    task = {"task_id": task_id, "task_type": task_type, "base_commit": base,
            "worktree_path": str(repo), "status": "running"}
    run = repo / ".rig" / "runs" / task_id
    run.mkdir(parents=True)
    (run / "task.json").write_text(json.dumps(task), encoding="utf-8")
    acc = build_acceptance(task_id, task_type, repo)
    (run / "acceptance.json").write_text(json.dumps(acc), encoding="utf-8")
    return repo, run, task, acc


def _check(acc, name):
    return next((c for c in acc["checks"] if c["name"] == name), None)


def test_criteria_appear_only_when_the_diff_adds_japanese_prose(tmp_path):
    from rig_workbench.workbench import ja_prose

    repo, run, task, acc = _fixture(tmp_path)
    assert ja_prose.ensure_ja_prose_criteria(repo, task, acc) is False
    assert _check(acc, ja_prose.LINT_CRITERION) is None

    (repo / "src.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "notes.md").write_text("English only, no Japanese here.\n", encoding="utf-8")
    assert ja_prose.ensure_ja_prose_criteria(repo, task, acc) is False

    (repo / "notes.md").write_text("日本語の一文を足しました。\n", encoding="utf-8")
    assert ja_prose.ensure_ja_prose_criteria(repo, task, acc) is True
    assert _check(acc, ja_prose.LINT_CRITERION)["status"] == "pending"
    assert _check(acc, ja_prose.SMELL_CRITERION)["status"] == "pending"

    (repo / "notes.md").unlink()
    assert ja_prose.ensure_ja_prose_criteria(repo, task, acc) is False
    assert _check(acc, ja_prose.LINT_CRITERION) is None


def test_lint_criterion_is_diff_scoped_and_machine_owned(tmp_path):
    """The pre-existing double negative in README.md is not this task's; the added one is."""
    from rig_workbench.workbench import ja_prose

    repo, run, task, acc = _fixture(tmp_path)
    (repo / "README.md").write_text(
        "# base\n\nこれは元からある一文で、できないことはない。\n\n追記した一文は問題ない。\n",
        encoding="utf-8")
    ja_prose.ensure_ja_prose_criteria(repo, task, acc)
    notes = ja_prose.apply_ja_lint_sensor(repo, run, task, acc)
    check = _check(acc, ja_prose.LINT_CRITERION)
    assert check["status"] == "passed", notes
    assert check["ja_lint_findings"]["errors"] == 0

    (repo / "README.md").write_text(
        "# base\n\nこれは元からある一文で、できないことはない。\n\n追記した一文は読めないわけではありません。\n",
        encoding="utf-8")
    notes = ja_prose.apply_ja_lint_sensor(repo, run, task, acc)
    assert check["status"] == "failed"
    assert "no-double-negative-ja" in check["ja_lint_findings"]["listed"][0]
    assert any("README.md:5" in n for n in notes)


def test_warnings_leave_the_lint_criterion_at_warning_not_failed(tmp_path):
    from rig_workbench.workbench import ja_prose

    repo, run, task, acc = _fixture(tmp_path)
    (repo / "guide.md").write_text("映画を見れた。\n", encoding="utf-8")
    ja_prose.ensure_ja_prose_criteria(repo, task, acc)
    ja_prose.apply_ja_lint_sensor(repo, run, task, acc)
    assert _check(acc, ja_prose.LINT_CRITERION)["status"] == "warning"


def test_explicit_pass_is_the_recorded_escape_hatch_and_sticks(tmp_path):
    from rig_workbench.workbench import ja_prose

    repo, run, task, acc = _fixture(tmp_path)
    (repo / "guide.md").write_text("固有名詞の都合で、できないことはない。\n", encoding="utf-8")
    ja_prose.ensure_ja_prose_criteria(repo, task, acc)
    check = _check(acc, ja_prose.LINT_CRITERION)
    check["status"] = "passed"
    ja_prose.apply_ja_lint_sensor(repo, run, task, acc, explicit_set={ja_prose.LINT_CRITERION})
    assert check["status"] == "passed" and check["ja_lint_override"] is True
    ja_prose.apply_ja_lint_sensor(repo, run, task, acc)
    assert check["status"] == "passed"


def test_the_projects_config_is_honoured(tmp_path):
    from rig_workbench.workbench import ja_prose

    repo, run, task, acc = _fixture(tmp_path)
    (repo / ".claude").mkdir()
    (repo / ".claude" / "ja-textlint.json").write_text(
        json.dumps({"rules": {"no-double-negative-ja": False}}), encoding="utf-8")
    (repo / "guide.md").write_text("できないことはない。\n", encoding="utf-8")
    ja_prose.ensure_ja_prose_criteria(repo, task, acc)
    ja_prose.apply_ja_lint_sensor(repo, run, task, acc)
    assert _check(acc, ja_prose.LINT_CRITERION)["status"] == "passed"

    (repo / ".claude" / "ja-textlint.json").write_text('{"rulez": {}}', encoding="utf-8")
    ja_prose.apply_ja_lint_sensor(repo, run, task, acc)
    check = _check(acc, ja_prose.LINT_CRITERION)
    assert check["status"] == "failed" and "unchecked" in check["detail"]


def test_smell_criterion_only_transcribes_the_reviewers_verdict(tmp_path):
    from rig_workbench.workbench import ja_prose

    repo, run, task, acc = _fixture(tmp_path)
    (repo / "guide.md").write_text("問題ない。\n", encoding="utf-8")
    ja_prose.ensure_ja_prose_criteria(repo, task, acc)
    check = _check(acc, ja_prose.SMELL_CRITERION)

    ja_prose.apply_ja_smell_sensor(repo, run, task, acc)
    assert check["status"] == "pending" and "ai-smell-reviewer" in check["detail"]

    def record(verdict):
        (run / "review.json").write_text(json.dumps({"task_id": task["task_id"], "verdicts": [
            {"persona": "ai-smell-reviewer", "verdict": verdict, "recorded_at": "2026-09-09T00:00:00Z"}]}),
            encoding="utf-8")

    record("REJECT")
    ja_prose.apply_ja_smell_sensor(repo, run, task, acc)
    assert check["status"] == "failed"
    record("APPROVE_WITH_CONDITIONS")
    ja_prose.apply_ja_smell_sensor(repo, run, task, acc)
    assert check["status"] == "warning"
    record("APPROVE")
    ja_prose.apply_ja_smell_sensor(repo, run, task, acc)
    assert check["status"] == "passed"


def test_no_rhythm_score_reaches_the_gate():
    """rig measured that gating a rhythm proxy made blind judgement worse (§6-3). Keep it out."""
    source = (REPO_ROOT / "rig_workbench" / "workbench" / "ja_prose.py").read_text(encoding="utf-8")
    body = source.split('"""', 2)[2]  # everything after the module docstring
    assert "prose_rhythm" not in body


def test_gate_integration_adds_both_criteria_and_fails_on_an_added_error(tmp_path):
    repo, run, task, acc = _fixture(tmp_path)
    (repo / "guide.md").write_text("この機能は利用することができます。\n", encoding="utf-8")
    env = {"PYTHONPATH": str(REPO_ROOT), "RIG_HOME": str(REPO_ROOT), "PATH": "/usr/bin:/bin"}
    completed = subprocess.run(
        [sys.executable, str(WORKBENCH), "gate", task["task_id"]], cwd=repo, env=env,
        capture_output=True, text=True,
    )
    out = completed.stdout + completed.stderr
    assert "ja_lint_clean" in out and "ja_prose_ai_smell_reviewed" in out
    saved = json.loads((run / "acceptance.json").read_text(encoding="utf-8"))
    assert _check(saved, "ja_lint_clean")["status"] == "failed"
    assert _check(saved, "ja_prose_ai_smell_reviewed")["status"] == "pending"
    assert saved["status"] == "failed"

    (repo / "guide.md").write_text("この機能は利用できます。\n", encoding="utf-8")
    subprocess.run([sys.executable, str(WORKBENCH), "review", task["task_id"],
                    "--set", "ai-smell-reviewer=APPROVE"], cwd=repo, env=env, check=True,
                   capture_output=True, text=True)
    completed = subprocess.run(
        [sys.executable, str(WORKBENCH), "gate", task["task_id"]], cwd=repo, env=env,
        capture_output=True, text=True,
    )
    saved = json.loads((run / "acceptance.json").read_text(encoding="utf-8"))
    assert _check(saved, "ja_lint_clean")["status"] == "passed", completed.stdout
    assert _check(saved, "ja_prose_ai_smell_reviewed")["status"] == "passed"


def test_scan_ja_prose_subcommand_prints_the_same_diff_scoped_findings(tmp_path):
    repo, run, task, acc = _fixture(tmp_path)
    (repo / "guide.md").write_text("まず最初に確認する。\n", encoding="utf-8")
    env = {"PYTHONPATH": str(REPO_ROOT), "RIG_HOME": str(REPO_ROOT), "PATH": "/usr/bin:/bin"}
    completed = subprocess.run(
        [sys.executable, str(WORKBENCH), "scan-ja-prose", task["task_id"]], cwd=repo, env=env,
        capture_output=True, text=True,
    )
    assert completed.returncode == 1
    assert "guide.md:1" in completed.stdout and "ja-no-redundant-expression" in completed.stdout
