"""`wb autonomy` — the record and the brakes of an `--autonomous` run.

`--autonomous` removes the step gate: the confirmation a person would otherwise give after
every step. That is what makes it worth using and also what makes it unsafe to use blind,
because every one of those confirmations was two things at once — a chance to stop the run,
and a moment where somebody saw what it had just decided. Taking the gate away takes both
away. This module puts them back without putting the person back in the loop.

**The record.** Every judgement the gate would have shown a person is appended to
`.rig/autonomy/<run>.jsonl` as it is made: the gate that was skipped, the decision taken in
its place, the assumption it rests on, the question deferred to the end instead of asked
mid-run, the recovery tried before escalating, the checkpoint to roll back to. `report`
renders that record as the hand-over a person reads when the run ends — what was decided
for them, what they still need to confirm, and where to go back to if they disagree.

**The brakes.** `check` is called at every step boundary and before every scheduled
wake-up, and answers one question from the record alone: may the run continue. It says no
(exit 1) when

  * a kill switch exists — `.rig/STOP` for every run in this repository, or
    `.rig/autonomy/<run>.stop` for one (`stop` writes the latter);
  * a hard stop was recorded — the operations `--autonomous` never decides alone
    (`HARD_STOPS`, the same five a development loop escalates on);
  * a ceiling was reached — steps done, minutes elapsed, recoveries tried in total, or
    recoveries tried on the step currently running.

Ceilings are fixed by `start` and read back from the record, not from the caller of
`check`, so a run cannot raise its own limits half-way by passing bigger numbers.

**What this does not do.** It does not run the loop, decide anything, or intercept a
command — the agent decides, and the host permission system is what can refuse a command
at run time. It does not judge whether the result is good; that is the acceptance gate,
which `--autonomous` never lifts. A run that never calls `log` leaves an empty record and
an empty report, and the report says so rather than reading as a clean run.

This module records and judges; it neither prints nor exits. `cmd_autonomy`, the shell that
turns its answers into output and an exit code, lives in `context_report` beside
`cmd_wakeups`. Stdlib and `ports` only.
"""

from __future__ import annotations

import datetime
import json
import pathlib
import re

from ..ports import Clock, FileStore, GitRepo
from ..ports.local import GIT, LOCAL_FILES, SYSTEM_CLOCK

SCHEMA = "rig.autonomy-journal/v1"

#: What a journal line can record. Closed: an unknown kind would be stored, rendered under
#: no heading, and leave the reader believing the record is complete.
GATE_SKIPPED = "gate-skipped"
DECISION = "decision"
ASSUMPTION = "assumption"
DEFERRED_QUESTION = "deferred-question"
RECOVERY = "recovery"
CHECKPOINT = "checkpoint"
STEP_DONE = "step-done"
HARD_STOP = "hard-stop"
FINISH = "finish"
KINDS = (GATE_SKIPPED, DECISION, ASSUMPTION, DEFERRED_QUESTION, RECOVERY, CHECKPOINT,
         STEP_DONE, HARD_STOP, FINISH)
ACTIONS = ("start", "log", "check", "stop", "report")

#: Why a run must stop and hand over to a person, whatever `--autonomous` says. Mirrors
#: `assurance.development_loop.ESCALATIONS`, so the two loops escalate on the same things.
HARD_STOPS = ("destructive-operation", "ambiguous-requirement", "policy-requires-approval",
              "budget-exhausted", "capability-missing")

#: Defaults chosen to bound a long unattended run, not a short one: a recipe has fewer than
#: ten steps, a goal loop rarely needs more than a handful of rounds.
DEFAULT_LIMITS = {
    "max_steps": 30,
    "max_minutes": 240,
    "max_recoveries": 6,
    "max_recoveries_per_step": 2,
}

RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
MAX_SUMMARY = 500
GLOBAL_STOP = ".rig/STOP"


class UsageError(ValueError):
    """The caller asked for something this module cannot record — rig could not answer."""


def journal_path(root: pathlib.Path, run: str) -> pathlib.Path:
    return root / ".rig" / "autonomy" / f"{run}.jsonl"


def stop_path(root: pathlib.Path, run: str) -> pathlib.Path:
    return root / ".rig" / "autonomy" / f"{run}.stop"


def check_run_id(run: str | None) -> str:
    # A run id names a file, so it must not be able to name one anywhere else.
    if not RUN_ID.fullmatch(run or "") or ".." in run:
        raise UsageError("--run must be 1-64 characters of letters, digits, '.', '_' or '-'")
    return run


def summary_line(text: str | None, *, required: bool = True) -> str:
    text = " ".join((text or "").split())
    if required and not text:
        raise UsageError("--summary is required")
    if len(text) > MAX_SUMMARY:
        raise UsageError(f"--summary is at most {MAX_SUMMARY} characters (got {len(text)})")
    return text


def read_journal(root: pathlib.Path, run: str, *, files: FileStore = LOCAL_FILES) -> list[dict]:
    path = journal_path(root, run)
    if not files.is_file(path):
        return []
    records = []
    for line in files.read_text(path).splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            # Kept, not skipped: a line nobody can read is still a line the report must not
            # pretend was never written.
            rec = None
        records.append(rec if isinstance(rec, dict) else {"kind": "unreadable"})
    return records


def _append(root: pathlib.Path, run: str, record: dict, files: FileStore) -> None:
    path = journal_path(root, run)
    files.mkdir(path.parent)
    files.append_line(path, json.dumps(record, ensure_ascii=False))


def start_record(records: list[dict]) -> dict | None:
    return next((r for r in records if r.get("kind") == "start"), None)


def start(root: pathlib.Path, run: str, limits: dict, summary: str | None = None, *,
          files: FileStore = LOCAL_FILES, clock: Clock = SYSTEM_CLOCK) -> dict:
    """Open a journal with its ceilings. The ceilings are fixed here and only here."""
    if start_record(read_journal(root, run, files=files)):
        raise UsageError(f"run '{run}' already started; use a new --run id for a new run")
    fixed = dict(DEFAULT_LIMITS)
    for key, value in limits.items():
        if value is None:
            continue
        if value < 1:
            raise UsageError(f"--{key.replace('_', '-')} must be 1 or more")
        fixed[key] = value
    record = {"schema": SCHEMA, "kind": "start", "started_at": clock.stamp(),
              "summary": summary_line(summary, required=False), "limits": fixed}
    _append(root, run, record, files)
    return record


def log(root: pathlib.Path, run: str, kind: str | None, summary: str | None, *,
        step: str | None = None, reason: str | None = None, ref: str | None = None,
        cwd: pathlib.Path | None = None, files: FileStore = LOCAL_FILES,
        clock: Clock = SYSTEM_CLOCK, git: GitRepo = GIT) -> dict:
    """Append one entry. A checkpoint without `ref` records the commit HEAD names in `cwd`."""
    if not start_record(read_journal(root, run, files=files)):
        raise UsageError(f"run '{run}' has no journal; start it with `wb autonomy start --run {run}`")
    if kind not in KINDS:
        raise UsageError(f"--kind must be one of: {', '.join(KINDS)}")
    if kind == HARD_STOP and reason not in HARD_STOPS:
        raise UsageError(f"a hard-stop needs --reason, one of: {', '.join(HARD_STOPS)}")
    record = {"kind": kind, "at": clock.stamp(), "summary": summary_line(summary)}
    if step:
        record["step"] = step
    if reason:
        record["reason"] = reason
    if kind == CHECKPOINT and not ref:
        ref = git.head(cwd=cwd)
        if not ref:
            raise UsageError("a checkpoint needs --ref, and HEAD could not be read here")
    if ref:
        record["ref"] = ref
    _append(root, run, record, files)
    return record


def set_stop(root: pathlib.Path, run: str, summary: str | None = None, *,
             files: FileStore = LOCAL_FILES) -> pathlib.Path:
    path = stop_path(root, run)
    files.mkdir(path.parent)
    files.write_text(path, summary_line(summary, required=False) + "\n")
    return path


def _minutes_since(started_at: str, now: datetime.datetime) -> float | None:
    try:
        started = datetime.datetime.fromisoformat(started_at)
    except (TypeError, ValueError):
        return None
    if started.tzinfo is None:
        return None
    return (now - started).total_seconds() / 60


def evaluate(root: pathlib.Path, run: str, records: list[dict], *,
             files: FileStore = LOCAL_FILES, clock: Clock = SYSTEM_CLOCK) -> dict:
    """Whether the run may continue, from the record and the kill switches alone."""
    start_rec = start_record(records) or {}
    limits = {**DEFAULT_LIMITS, **start_rec.get("limits", {})}
    steps = sum(1 for r in records if r.get("kind") == STEP_DONE)
    recoveries = sum(1 for r in records if r.get("kind") == RECOVERY)

    # The step currently running is the step named by the latest line that names one, unless
    # that line closed it. Recoveries count against a step only since it last finished, so a
    # goal loop that runs `implement` again next round starts that round's ladder from zero.
    named = [r for r in records if r.get("step")]
    current = named[-1]["step"] if named and named[-1].get("kind") != STEP_DONE else None
    on_step = 0
    for r in records:
        if current is None or r.get("step") != current:
            continue
        if r.get("kind") == STEP_DONE:
            on_step = 0
        elif r.get("kind") == RECOVERY:
            on_step += 1
    elapsed = _minutes_since(start_rec.get("started_at", ""), clock.now())

    reasons = []
    if files.is_file(root / GLOBAL_STOP):
        reasons.append(f"kill switch: {GLOBAL_STOP} exists")
    if files.is_file(stop_path(root, run)):
        reasons.append(f"kill switch: .rig/autonomy/{run}.stop exists")
    for r in records:
        if r.get("kind") == HARD_STOP:
            reasons.append(f"hard stop ({r.get('reason', '?')}): {r.get('summary', '')}")
    if any(r.get("kind") == FINISH for r in records):
        reasons.append("the run already finished")
    if steps >= limits["max_steps"]:
        reasons.append(f"max steps reached ({steps}/{limits['max_steps']})")
    if elapsed is None:
        # An unreadable start time is not "no time has passed": the minute ceiling would
        # silently stop applying, so the run stops instead.
        reasons.append("start time unreadable; the minute ceiling cannot be checked")
    elif elapsed >= limits["max_minutes"]:
        reasons.append(f"max minutes reached ({int(elapsed)}/{limits['max_minutes']})")
    if recoveries >= limits["max_recoveries"]:
        reasons.append(f"max recoveries reached ({recoveries}/{limits['max_recoveries']})")
    if current and on_step >= limits["max_recoveries_per_step"]:
        reasons.append(f"recovery ladder exhausted on step '{current}' "
                       f"({on_step}/{limits['max_recoveries_per_step']})")

    return {
        "schema": SCHEMA,
        "run": run,
        "continue": not reasons,
        "reasons": reasons,
        "usage": {
            "steps": steps,
            "minutes": None if elapsed is None else round(elapsed, 1),
            "recoveries": recoveries,
            "recoveries_on_current_step": on_step,
            "current_step": current,
        },
        "limits": limits,
    }


_SECTIONS = (
    (DEFERRED_QUESTION, "Questions deferred to you"),
    (ASSUMPTION, "Assumptions to confirm"),
    (DECISION, "Decisions taken without asking"),
    (GATE_SKIPPED, "Step gates skipped"),
    (RECOVERY, "Recoveries tried"),
    (CHECKPOINT, "Checkpoints (roll back here)"),
    (HARD_STOP, "Hard stops"),
)


def render_report(run: str, records: list[dict], verdict: dict) -> str:
    """The hand-over: what was decided for the reader, what they still have to confirm."""
    lines = [f"## rig autonomous report: {run}", ""]
    start_rec = start_record(records)
    if not start_rec:
        lines.append("No journal for this run — nothing was recorded, so nothing here says "
                     "the run was clean.")
        return "\n".join(lines)
    usage, limits = verdict["usage"], verdict["limits"]
    finish = next((r for r in reversed(records) if r.get("kind") == FINISH), None)
    if finish:
        status = f"finished ({finish['summary']})" if finish.get("summary") else "finished"
    else:
        status = "running" if verdict["continue"] else "stopped"
    lines += [
        f"status: {status} | goal: {start_rec.get('summary') or '-'}",
        f"usage: steps {usage['steps']}/{limits['max_steps']} · "
        f"minutes {usage['minutes']}/{limits['max_minutes']} · "
        f"recoveries {usage['recoveries']}/{limits['max_recoveries']}",
    ]
    if verdict["reasons"] and not finish:
        lines.append("stopped because: " + "; ".join(verdict["reasons"]))
    for kind, title in _SECTIONS:
        rows = [r for r in records if r.get("kind") == kind]
        if not rows:
            continue
        lines += ["", f"### {title} ({len(rows)})"]
        for r in rows:
            where = f"[{r['step']}] " if r.get("step") else ""
            extra = [x for x in (r.get("reason"), f"ref {r['ref']}" if r.get("ref") else None) if x]
            tail = f" ({', '.join(extra)})" if extra else ""
            lines.append(f"- {where}{r.get('summary', '')}{tail}")
    if not any(r.get("kind") in KINDS for r in records):
        lines += ["", "Nothing was logged after start — a run that skipped gates without "
                      "recording them is not a run that decided nothing."]
    unreadable = sum(1 for r in records if r.get("kind") == "unreadable")
    if unreadable:
        lines += ["", f"[WARN] {unreadable} journal line(s) could not be read; the record "
                      "above is incomplete."]
    return "\n".join(lines)
