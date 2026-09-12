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


def _operator_set(check, status, detail=None):
    """What `gate --set <criterion>=<status>[:detail]` does to a check, exactly: the status
    and the writer always, the detail only when the pair carried one (lifecycle.cmd_gate).
    Leaving the writer out here is what made the first version of these tests agree with a
    bug the CLI had — the tests below that drive `workbench.py gate` are the ones that
    cannot drift like that."""
    from rig_workbench.workbench.config import WRITER_OPERATOR

    check["status"], check["by"] = status, WRITER_OPERATOR
    if detail is not None:
        check["detail"] = detail


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


def test_sensor_writes_its_verdict_over_a_passed_check(tmp_path):
    """A `passed` on the check — including one this invocation's `--set` just wrote — is
    not an answer the lint defers to; the gate refuses the contradicting declaration
    (tests/test_gate_sensor_authority.py)."""
    from rig_workbench.workbench import ja_prose

    repo, run, task, acc = _fixture(tmp_path)
    (repo / "guide.md").write_text("固有名詞の都合で、できないことはない。\n", encoding="utf-8")
    ja_prose.ensure_ja_prose_criteria(repo, task, acc)
    check = _check(acc, ja_prose.LINT_CRITERION)
    check["status"] = "passed"
    ja_prose.apply_ja_lint_sensor(repo, run, task, acc)
    assert check["status"] == "failed"
    assert "ja_lint_override" not in check


def test_a_stricter_hand_written_status_survives_a_clean_lint(tmp_path):
    """The other direction, and the one these two sensors used to get wrong. They write on
    every evaluation, so a clean diff overwrote whatever was there — including a `failed`
    an operator had just recorded with a reason the lint cannot see. A sensor may tighten
    a hand-written status; it may never loosen one."""
    from rig_workbench.workbench import ja_prose

    repo, run, task, acc = _fixture(tmp_path)
    (repo / "guide.md").write_text("これは普通の文です。\n", encoding="utf-8")
    ja_prose.ensure_ja_prose_criteria(repo, task, acc)
    check = _check(acc, ja_prose.LINT_CRITERION)

    # clean diff, nothing recorded yet → the sensor records its pass
    ja_prose.apply_ja_lint_sensor(repo, run, task, acc)
    assert check["status"] == "passed"

    # A bare `gate --set ja_lint_clean=failed` — no `:detail`, so the detail left on the
    # check is the sensor's own text from the run above. That is the ordinary case after
    # the first evaluation, and reading ownership out of the detail called this status the
    # sensor's and loosened it straight back to `passed`.
    _operator_set(check, "failed")
    assert check["detail"].startswith(ja_prose._LINT_PREFIX)
    notes = ja_prose.apply_ja_lint_sensor(repo, run, task, acc)
    assert check["status"] == "failed"
    assert any("stricter recorded judgement" in n for n in notes)

    # ...and so does one that carries its own reason
    _operator_set(check, "failed", "操作者判断")
    ja_prose.apply_ja_lint_sensor(repo, run, task, acc)
    assert (check["status"], check["detail"]) == ("failed", "操作者判断")

    # but the sensor still clears a stale verdict of its OWN making
    check["status"], check["by"] = "failed", ja_prose._LINT_WRITER
    check["detail"] = f"{ja_prose._LINT_PREFIX} 3 error(s) on added Japanese lines"
    ja_prose.apply_ja_lint_sensor(repo, run, task, acc)
    assert check["status"] == "passed"

    # and a record written before `by` existed still heals: the detail is the only
    # evidence there is, and it is the sensor's
    check.pop("by")
    check["status"] = "failed"
    check["detail"] = f"{ja_prose._LINT_PREFIX} 3 error(s) on added Japanese lines"
    ja_prose.apply_ja_lint_sensor(repo, run, task, acc)
    assert check["status"] == "passed"


def test_a_stricter_hand_written_status_survives_the_smell_sensor(tmp_path):
    """Same rule for the reviewer-verdict criterion: an APPROVE recorded by the
    ai-smell-reviewer does not overwrite an operator's `failed`, while a missing verdict
    still drags a hand-written `passed` back to pending."""
    from rig_workbench.workbench import ja_prose

    repo, run, task, acc = _fixture(tmp_path)
    (repo / "guide.md").write_text("これは普通の文です。\n", encoding="utf-8")
    ja_prose.ensure_ja_prose_criteria(repo, task, acc)
    check = _check(acc, ja_prose.SMELL_CRITERION)

    # No verdict recorded: nothing an operator can declare stands in for the reviewer.
    # `warning:未確認` is the wording cmd_gate itself suggests for a criterion one cannot
    # judge, and it is exactly how this lane was skipped: warning outranked pending, the
    # gate reached passed_with_warnings, and accept let it through.
    for status, detail in (("passed", "見た"), ("warning", "未確認"), ("skipped", "見送り")):
        _operator_set(check, status, detail)
        ja_prose.apply_ja_smell_sensor(repo, run, task, acc)
        assert check["status"] == "pending", status

    # a hand-written `failed` is the one that stands: stricter than pending, and this rule
    # is against a missing verdict being talked down, not against a harder judgement
    _operator_set(check, "failed", "散文が読めていない")
    notes = ja_prose.apply_ja_smell_sensor(repo, run, task, acc)
    assert (check["status"], check["detail"]) == ("failed", "散文が読めていない")
    assert any("stricter recorded judgement" in n for n in notes)

    # ...and the sensor still clears a `failed` of its own making
    check["status"], check["by"] = "failed", ja_prose._SMELL_WRITER
    ja_prose.apply_ja_smell_sensor(repo, run, task, acc)
    assert check["status"] == "pending"

    (run / "review.json").write_text(json.dumps(
        {"verdicts": [{"persona": ja_prose.SMELL_PERSONA, "verdict": "APPROVE",
                       "recorded_at": "2026-09-12T00:00:00+00:00"}]}), encoding="utf-8")
    _operator_set(check, "failed", "操作者判断")
    ja_prose.apply_ja_smell_sensor(repo, run, task, acc)
    assert (check["status"], check["detail"]) == ("failed", "操作者判断")


def test_an_unreadable_verdict_label_is_pending_not_approval(tmp_path):
    """review.json is an editable file, and the sensor used to map everything that was
    not REJECT or APPROVE_WITH_CONDITIONS to `passed` — so a typo, or a label a future
    verdict vocabulary adds, approved the prose nobody had approved."""
    from rig_workbench.workbench import ja_prose

    repo, run, task, acc = _fixture(tmp_path)
    (repo / "guide.md").write_text("これは普通の文です。\n", encoding="utf-8")
    ja_prose.ensure_ja_prose_criteria(repo, task, acc)
    check = _check(acc, ja_prose.SMELL_CRITERION)

    for label in ("APPROVE_", "approve", "LGTM", ""):
        (run / "review.json").write_text(json.dumps(
            {"verdicts": [{"persona": ja_prose.SMELL_PERSONA, "verdict": label}]}), encoding="utf-8")
        check["status"], check["by"] = "pending", ja_prose._SMELL_WRITER
        notes = ja_prose.apply_ja_smell_sensor(repo, run, task, acc)
        assert check["status"] == "pending", label
        assert repr(label) in check["detail"], label
        assert notes

    # the one spelling that is an approval still is
    (run / "review.json").write_text(json.dumps(
        {"verdicts": [{"persona": ja_prose.SMELL_PERSONA, "verdict": "APPROVE"}]}), encoding="utf-8")
    ja_prose.apply_ja_smell_sensor(repo, run, task, acc)
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


def test_gate_integration_a_bare_set_survives_the_sensor_that_wrote_the_detail(tmp_path):
    """The two scenarios through the real CLI, which is where this went wrong.

    `cmd_gate` writes `detail` only when the `--set` pair carries one, so after the first
    evaluation a bare `--set ja_lint_clean=failed` sits on top of the sensor's own detail
    text. Inferring "did I write this?" from that text answered yes and loosened the
    operator's `failed` back to `passed` — in the same invocation, silently. Ownership is
    a field now (`check["by"]`), and only a unit test that stamps it the way cmd_gate does,
    plus this one, can tell the two apart.
    """
    repo, run, task, acc = _fixture(tmp_path)
    (repo / "guide.md").write_text("これは普通の文です。\n", encoding="utf-8")
    env = {"PYTHONPATH": str(REPO_ROOT), "RIG_HOME": str(REPO_ROOT), "PATH": "/usr/bin:/bin"}

    def gate(*extra):
        return subprocess.run([sys.executable, str(WORKBENCH), "gate", task["task_id"], *extra],
                              cwd=repo, env=env, capture_output=True, text=True)

    def lint():
        saved = json.loads((run / "acceptance.json").read_text(encoding="utf-8"))
        return _check(saved, "ja_lint_clean")

    # run 1: clean prose, nothing recorded → the sensor writes `passed` and its own detail
    gate()
    assert lint()["status"] == "passed"
    assert lint()["detail"].startswith("(ja-lint sensor)")

    # run 2: the operator overrules it with a bare `--set`, for a reason the lint cannot
    # see. It must stand — through this evaluation and every later one.
    gate("--set", "ja_lint_clean=failed")
    assert lint()["status"] == "failed"
    gate()
    assert lint()["status"] == "failed"

    # the inverse, on the same criterion: a `failed` the SENSOR wrote clears itself once
    # the prose is fixed, which is what the ownership rule must not cost.
    (repo / "guide.md").write_text("この機能は利用することができます。\n", encoding="utf-8")
    gate()
    assert lint()["status"] == "failed"
    assert lint()["detail"].startswith("(ja-lint sensor)")
    (repo / "guide.md").write_text("この機能は利用できます。\n", encoding="utf-8")
    gate()
    assert lint()["status"] == "passed"


def test_gate_integration_the_suggested_unknown_wording_cannot_pass_the_reviewer_lane(tmp_path):
    """`cmd_gate` tells an operator who cannot judge a criterion to record
    `warning:未確認`. On `ja_prose_ai_smell_reviewed` that is not a judgement they are
    allowed to make — nobody has read the prose — and it must not produce a gate that
    `accept` lets through. Driven through the CLI with the exact suggested wording."""
    repo, run, task, acc = _fixture(tmp_path)
    (repo / "guide.md").write_text("これは普通の文です。\n", encoding="utf-8")
    env = {"PYTHONPATH": str(REPO_ROOT), "RIG_HOME": str(REPO_ROOT), "PATH": "/usr/bin:/bin"}

    completed = subprocess.run(
        [sys.executable, str(WORKBENCH), "gate", task["task_id"],
         "--set", "ja_prose_ai_smell_reviewed=warning:未確認"],
        cwd=repo, env=env, capture_output=True, text=True)

    saved = json.loads((run / "acceptance.json").read_text(encoding="utf-8"))
    check = _check(saved, "ja_prose_ai_smell_reviewed")
    assert check["status"] == "pending", completed.stdout + completed.stderr
    # `pending`, not `passed_with_warnings`: the gate statuses `accept` treats as met are
    # passed / passed_with_warnings / skipped (accept.py's `gate_ok`), and this is none of
    # them. The task does not move to `gate_passed` either — `cmd_gate` transitions only
    # on a decided gate — so nothing downstream reads this lane as answered.
    assert saved["status"] == "pending"
    task_saved = json.loads((run / "task.json").read_text(encoding="utf-8"))
    assert task_saved["status"] == "running"

    # and the lane opens the only way it can: the reviewer rules
    subprocess.run([sys.executable, str(WORKBENCH), "review", task["task_id"],
                    "--set", "ai-smell-reviewer=APPROVE"], cwd=repo, env=env, check=True,
                   capture_output=True, text=True)
    subprocess.run([sys.executable, str(WORKBENCH), "gate", task["task_id"]],
                   cwd=repo, env=env, capture_output=True, text=True)
    saved = json.loads((run / "acceptance.json").read_text(encoding="utf-8"))
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
