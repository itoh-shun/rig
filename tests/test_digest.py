"""Telemetry digest (issue #285): `workbench.py digest [--period week|month] [--out]`.

Covers: aggregation of synthetic `.rig/` data (orchestrate run finals, workbench
task/gate counts, most-failed criteria, force-accept count, rubber-stamp
suspects via the reused stats helpers, drill detection rate), the week/month
window, --out file output, and the graceful empty-data path.
"""

import argparse
import datetime
import json
import pathlib

from rig_workbench.workbench.digest import build_digest, cmd_digest


def iso(days_ago: float) -> str:
    dt = datetime.datetime.now().astimezone() - datetime.timedelta(days=days_ago)
    return dt.isoformat(timespec="seconds")


def write_jsonl(path: pathlib.Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def make_rig(root: pathlib.Path) -> None:
    """Synthetic .rig telemetry: 3 recent orchestrate runs (+1 outside the week),
    2 gated tasks, 5 rubber-stamp review tasks, 1 force accept, 1 drill run."""
    rig = root / ".rig"

    write_jsonl(rig / "runs.jsonl", [
        {"ts": iso(1), "recipe": "mq", "final": "DONE"},
        {"ts": iso(2), "recipe": "mq", "final": "DONE"},
        {"ts": iso(3), "recipe": "fix", "final": "STOPPED"},
        {"ts": iso(15), "recipe": "mq", "final": "DONE"},  # in month, not in week
    ])

    def task(tid: str, status: str, checks: list[dict] | None = None,
             verdicts: list[dict] | None = None) -> None:
        d = rig / "runs" / tid
        d.mkdir(parents=True, exist_ok=True)
        (d / "task.json").write_text(json.dumps(
            {"task_id": tid, "input": "x", "task_type": "feature", "status": status,
             "created_at": iso(1)}), encoding="utf-8")
        if checks is not None:
            (d / "acceptance.json").write_text(json.dumps(
                {"task_id": tid, "status": "-", "checks": checks}), encoding="utf-8")
        if verdicts is not None:
            (d / "review.json").write_text(json.dumps(
                {"task_id": tid, "verdicts": verdicts}), encoding="utf-8")

    task("t-accepted", "accepted",
         checks=[{"name": "task_intent_satisfied", "status": "passed", "detail": ""}])
    task("t-failed", "gate_failed",
         checks=[{"name": "tests_pass_or_explained", "status": "failed", "detail": ""},
                 {"name": "no_secret_leak", "status": "failed", "detail": ""}])
    for i in range(5):  # one persona, 5 runs, 0 rejects → rubber-stamp suspect
        task(f"t-review-{i}", "accepted",
             verdicts=[{"persona": "stamper", "verdict": "APPROVE", "recorded_at": iso(1)}])

    write_jsonl(rig / "audit.jsonl", [
        {"ts": iso(1), "action": "accept_force", "task_id": "t-failed",
         "bypassed": ["acceptance_gate_not_failed"], "gate_status": "failed"},
    ])

    write_jsonl(rig / "drill-results.jsonl", [
        {"ts": iso(1), "seeds": 3,
         "scores": [{"reviewer": "sec", "detected": 2, "seeded": 3}]},
    ])


def test_digest_contains_expected_counts(tmp_path):
    make_rig(tmp_path)
    out = build_digest(tmp_path, "week")

    assert "# rig digest" in out and "week" in out
    assert "Orchestrate runs (`.rig/runs.jsonl`): 3" in out  # 15-day-old run excluded
    assert "- DONE: 2" in out
    assert "- STOPPED: 1" in out
    assert "Workbench tasks (`.rig/runs/`): 7" in out
    assert "- accepted: 6" in out
    assert "- gate_failed: 1" in out
    # gate rates: t-accepted passed, t-failed failed, 5 review tasks skipped (no checks)
    assert "passed 1 (50%), failed 1 (50%)" in out
    assert "- tests_pass_or_explained: 1" in out
    assert "- no_secret_leak: 1" in out
    assert "`accept --force` in period: 1" in out
    assert "- bypassed acceptance_gate_not_failed: 1" in out
    assert "stamper has 0 rejects across 5 runs" in out
    assert "66.7% (2/3 seeds across 1 drill run(s))" in out


def test_month_period_widens_the_window(tmp_path):
    make_rig(tmp_path)
    out = build_digest(tmp_path, "month")
    assert "Orchestrate runs (`.rig/runs.jsonl`): 4" in out  # 15-day-old run now counted


def test_empty_data_is_graceful(tmp_path):
    out = build_digest(tmp_path, "week")
    assert "No runs in period" in out
    out = build_digest(tmp_path, "month")
    assert "No runs in period" in out


def test_no_drill_file_means_no_drill_section(tmp_path):
    make_rig(tmp_path)
    (tmp_path / ".rig" / "drill-results.jsonl").unlink()
    out = build_digest(tmp_path, "week")
    assert "Drill detection rate" not in out


def test_cmd_digest_stdout_and_out_file(tmp_path, monkeypatch, capsys):
    make_rig(tmp_path)
    monkeypatch.chdir(tmp_path)  # not a git repo → digest falls back to cwd

    cmd_digest(argparse.Namespace(period="week", out=None))
    stdout = capsys.readouterr().out
    assert "# rig digest" in stdout and "stamper has 0 rejects" in stdout

    target = tmp_path / "digests" / "2026-W28.md"
    cmd_digest(argparse.Namespace(period="week", out=str(target)))
    assert "digest written" in capsys.readouterr().out
    assert "# rig digest" in target.read_text(encoding="utf-8")


def test_malformed_jsonl_lines_are_skipped(tmp_path):
    make_rig(tmp_path)
    with (tmp_path / ".rig" / "runs.jsonl").open("a", encoding="utf-8") as f:
        f.write("{not json\n\n[1,2]\n")
    out = build_digest(tmp_path, "week")
    assert "Orchestrate runs (`.rig/runs.jsonl`): 3" in out
