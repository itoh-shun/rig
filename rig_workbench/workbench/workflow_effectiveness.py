"""Where a recorded workflow was ineffective, and no wider claim (#433 §1–2).

This module reads the two histories this repository actually writes.  Orchestrate's
``.rig/runs.jsonl`` records a finished run's recipe, retries and stopping step.  Workbench's
``.rig/runs/*/{task,acceptance}.json`` records a task class, creation time and final gate
check.  The report aggregates those facts; it does not replay a run or judge its quality.

**The caller defines every pattern boundary.**  A record does not say that two occurrences
are recurring, that one retry is excessive, or that ``review`` is late.  Those values arrive
in a closed query document.  Missing, empty, wrong-typed and unsupported constraints are all
refused; absence is never permission to choose a convenient default.

**Unobservable is not zero.**  These records do not contain finding counts, dismissals,
cost, step redundancy, or a production repair link.  Their report entries say
``unobservable`` and carry no numeric substitute.  An empty token-usage object likewise
means no token count was measured, not that the run used zero tokens.

Elapsed time was in that list until #502 began writing a ``perf`` block into the same
``runs.jsonl`` this module reads.  It is now reported from what was measured, with rig's own
share separated from its providers' — the only half a workflow change can move.  A run whose
``perf`` withheld that subtraction (one untimed provider call is enough) counts as unmeasured
here rather than as zero overhead, because reading an absent field as zero downstream would
reintroduce exactly the fabrication ``perf`` refused upstream.

**Patterns are views, not new verdicts.**  A late-failure finding is only a count of stopped
runs whose recorded step is in the caller's list.  An excessive-loop finding is only a count
above the caller's threshold.  Neither recommends a new step, removes an existing one,
generates a candidate workflow, evaluates it, promotes it, or claims a quality improvement.
Those are outside this first stage of #433.
"""

from __future__ import annotations

import datetime
import json
import pathlib

SCHEMA = "rig.workflow-effectiveness/v1"
QUERY_SCHEMA = "rig.workflow-effectiveness-query/v1"

QUERY_KEYS = frozenset({"schema", "patterns"})
PATTERN_KEYS = {
    "late-stage-failure": frozenset({"kind", "minimum_occurrences", "late_steps"}),
    "excessive-repair-loops": frozenset(
        {"kind", "minimum_occurrences", "repair_cycles_above"}),
    "task-gate-failure": frozenset({"kind", "minimum_occurrences", "gate_statuses"}),
}
FAILURE_STATUSES = frozenset({"failed", "warning"})


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def validate_query(payload: object) -> list[str]:
    """Every way a query fails to supply the boundaries it asks this module to apply."""
    if not isinstance(payload, dict):
        return [f"query: expected an object, got {type(payload).__name__}"]
    problems: list[str] = []
    unknown = sorted(str(key) for key in payload if key not in QUERY_KEYS)
    if unknown:
        problems.append(f"query: unknown key(s) {', '.join(unknown)}")
    if payload.get("schema") != QUERY_SCHEMA:
        problems.append(f"schema: expected {QUERY_SCHEMA!r}, got {payload.get('schema')!r}")
    patterns = payload.get("patterns")
    if not isinstance(patterns, list):
        problems.append("patterns: expected a list of caller-defined pattern constraints")
        return problems
    if not patterns:
        problems.append("patterns: expected at least one constraint; an empty query gives "
                        "the detector no caller-supplied boundary")
    seen: set[str] = set()
    for index, item in enumerate(patterns):
        where = f"patterns[{index}]"
        if not isinstance(item, dict):
            problems.append(f"{where}: expected an object, got {type(item).__name__}")
            continue
        kind = item.get("kind")
        if kind not in PATTERN_KEYS:
            problems.append(f"{where}.kind: {kind!r} is not supported; supported patterns are "
                            f"{', '.join(sorted(PATTERN_KEYS))}")
            continue
        if kind in seen:
            problems.append(f"{where}.kind: {kind!r} appears more than once")
        seen.add(kind)
        unknown_item = sorted(str(key) for key in item if key not in PATTERN_KEYS[kind])
        if unknown_item:
            problems.append(f"{where}: unknown key(s) {', '.join(unknown_item)}")
        if not _positive_int(item.get("minimum_occurrences")):
            problems.append(f"{where}.minimum_occurrences: expected a positive integer")
        if kind == "late-stage-failure":
            late = item.get("late_steps")
            if not isinstance(late, list):
                problems.append(f"{where}.late_steps: expected a list of step ids")
            elif not late:
                problems.append(f"{where}.late_steps: expected at least one caller-defined "
                                "late step")
            elif any(not isinstance(step, str) or not step.strip() for step in late):
                problems.append(f"{where}.late_steps: every step id must be a non-blank string")
            elif len(set(late)) != len(late):
                problems.append(f"{where}.late_steps: a step id appears more than once")
        elif kind == "excessive-repair-loops":
            if not _nonnegative_int(item.get("repair_cycles_above")):
                problems.append(f"{where}.repair_cycles_above: expected a non-negative integer")
        else:
            statuses = item.get("gate_statuses")
            if not isinstance(statuses, list):
                problems.append(f"{where}.gate_statuses: expected a list")
            elif not statuses:
                problems.append(f"{where}.gate_statuses: expected at least one status")
            elif any(status not in FAILURE_STATUSES for status in statuses):
                problems.append(f"{where}.gate_statuses: values must be one of "
                                f"{', '.join(sorted(FAILURE_STATUSES))}")
            elif len(set(statuses)) != len(statuses):
                problems.append(f"{where}.gate_statuses: a status appears more than once")
    return problems


def read_query(path: pathlib.Path | str) -> dict:
    """Read one query, refusing duplicate JSON keys before one can overwrite another."""
    from .synthesis import _no_duplicate_keys

    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"),
                      object_pairs_hook=_no_duplicate_keys("workflow-effectiveness query"))


def _read_jsonl(path: pathlib.Path, root: pathlib.Path) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    unreadable: list[str] = []
    if not path.exists():
        return rows, unreadable
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows, [str(path.relative_to(root))]
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("record is not an object")
            rows.append(value)
        except (ValueError, TypeError):
            unreadable.append(f"{path.relative_to(root)}:{number}")
    return rows, unreadable


def _read_workbench(root: pathlib.Path) -> tuple[list[dict], list[str]]:
    tasks: list[dict] = []
    unreadable: list[str] = []
    runs = root / ".rig" / "runs"
    if not runs.exists():
        return tasks, unreadable
    try:
        directories = sorted(path for path in runs.iterdir() if path.is_dir())
    except OSError:
        return tasks, [str(runs.relative_to(root))]
    for directory in directories:
        task_path, gate_path = directory / "task.json", directory / "acceptance.json"
        try:
            task = json.loads(task_path.read_text(encoding="utf-8"))
            gate = json.loads(gate_path.read_text(encoding="utf-8"))
            if not isinstance(task, dict) or not isinstance(gate, dict):
                raise ValueError("record is not an object")
            tasks.append({"task": task, "gate": gate})
        except (OSError, ValueError, TypeError):
            # A pair is one measurement source.  Naming the directory avoids pretending the
            # readable half supplied the fact that lives in the unreadable half.
            unreadable.append(str(directory.relative_to(root)))
    return tasks, unreadable


def _unobservable(reason: str) -> dict:
    return {"status": "unobservable", "value": None, "reason": reason}


def _time_to_assurance(tasks: list[dict]) -> dict:
    by_type: dict[str, dict] = {}
    measured = 0
    for pair in tasks:
        task, gate = pair["task"], pair["gate"]
        if gate.get("status") not in ("passed", "passed_with_warnings"):
            continue
        try:
            start = datetime.datetime.fromisoformat(task["created_at"])
            end = datetime.datetime.fromisoformat(gate["checked_at"])
            seconds = int((end - start).total_seconds())
            if seconds < 0:
                raise ValueError("negative duration")
            task_type = task["task_type"]
            if not isinstance(task_type, str) or not task_type:
                raise ValueError("missing task type")
        except (KeyError, TypeError, ValueError):
            continue
        entry = by_type.setdefault(task_type, {"runs": 0, "total": 0})
        entry["runs"] += 1
        entry["total"] += seconds
        measured += 1
    if not tasks:
        return _unobservable("no workbench task records were found")
    return {"status": "observed" if measured else "unobservable",
            "measured_tasks": measured, "unmeasured_tasks": len(tasks) - measured,
            "by_task_type": dict(sorted(by_type.items())),
            **({} if measured else {"value": None,
               "reason": "no task has both a successful gate and usable creation/check times"})}


def _runtime(runs: list[dict]) -> dict:
    """How long runs took, and how much of it was rig's own (#433 §1, from #502's `perf`).

    This was reported as unobservable, on the grounds that `runs.jsonl` carries a finish
    timestamp and no start. That stopped being true when #502 began recording a `perf` block
    into the same records this module already reads, and a metric that keeps saying "cannot be
    measured" while the measurement sits in the file it is reading is worse than one that was
    never offered.

    The split is the point rather than a detail. #433 exists to improve the *process*, and the
    only half a workflow change can move is rig's own — a run that got slower because a
    provider had a bad afternoon says nothing about whether the workflow is any good.

    `perf`'s own refusals are carried through rather than papered over. It withholds
    `rig_overhead_ms` whenever a provider call went untimed, because overhead is a subtraction
    and one missed call would silently become rig's time; a run in that state counts towards
    `unmeasured_overhead_runs` here and contributes nothing to the total. Reading the field as
    absent-means-zero would reintroduce downstream exactly the fabrication `perf` refuses.
    """
    total_ms = 0.0
    overhead_ms = 0.0
    measured = 0
    measured_overhead = 0
    for run in runs:
        perf = run.get("perf")
        if not isinstance(perf, dict):
            continue
        elapsed = perf.get("total_ms")
        if not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool) or elapsed < 0:
            continue
        total_ms += float(elapsed)
        measured += 1
        overhead = perf.get("rig_overhead_ms")
        if isinstance(overhead, (int, float)) and not isinstance(overhead, bool) and overhead >= 0:
            overhead_ms += float(overhead)
            measured_overhead += 1
    if not runs:
        return _unobservable("no orchestrate run records were found")
    if not measured:
        return {**_unobservable("no run record carries a perf block with a usable total_ms"),
                "measured_runs": 0, "unmeasured_runs": len(runs)}
    report = {"status": "observed", "measured_runs": measured,
              "unmeasured_runs": len(runs) - measured,
              "total_ms": round(total_ms, 3)}
    if measured_overhead:
        report["rig_overhead_ms"] = round(overhead_ms, 3)
        report["measured_overhead_runs"] = measured_overhead
        report["unmeasured_overhead_runs"] = measured - measured_overhead
    else:
        # Elapsed time is known and rig's share is not. Said outright, because a reader who saw
        # only `total_ms` would reasonably assume the split was available and simply omitted.
        report["rig_overhead"] = _unobservable(
            "no measured run separated rig's time from its providers' "
            "(perf withholds the subtraction when a provider call went untimed)")
    return report


def _token_usage(runs: list[dict]) -> dict:
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
    measured = 0
    for run in runs:
        usage = run.get("token_usage")
        if not isinstance(usage, dict) or not usage:
            continue
        providers = list(usage.values())
        if not providers or any(not isinstance(item, dict) for item in providers):
            continue
        values = [item.get(name) for item in providers for name in totals]
        if any(not _nonnegative_int(value) for value in values):
            continue
        for item in providers:
            for name in totals:
                totals[name] += item[name]
        measured += 1
    if not runs:
        return _unobservable("no orchestrate run records were found")
    if not measured:
        return {**_unobservable("token_usage was absent or empty on every run"),
                "measured_runs": 0, "unmeasured_runs": len(runs)}
    return {"status": "observed", "measured_runs": measured,
            "unmeasured_runs": len(runs) - measured, **totals}


def _metrics(runs: list[dict], tasks: list[dict]) -> dict:
    repairs = [run.get("retries") for run in runs if _nonnegative_int(run.get("retries"))]
    locations: dict[str, int] = {}
    for run in runs:
        location = run.get("escalated_at")
        if isinstance(location, str) and location:
            locations[location] = locations.get(location, 0) + 1
    gate_by_type: dict[str, dict[str, int]] = {}
    for pair in tasks:
        task_type, status = pair["task"].get("task_type"), pair["gate"].get("status")
        if isinstance(task_type, str) and isinstance(status, str):
            group = gate_by_type.setdefault(task_type, {})
            group[status] = group.get(status, 0) + 1
    no_runs = _unobservable("no orchestrate run records were found")
    return {
        "repair_cycle_count": ({"status": "observed", "runs": len(repairs),
                                "unmeasured_runs": len(runs) - len(repairs),
                                "total": sum(repairs)} if repairs else no_runs),
        "time_to_assurance_seconds": _time_to_assurance(tasks),
        "gate_failure_location": ({"status": "observed", "by_step": dict(sorted(locations.items())),
                                   "runs_without_failure_location": len(runs) - sum(locations.values())}
                                  if runs else no_runs),
        "gate_status_by_task_type": ({"status": "observed", "groups": {
            key: dict(sorted(value.items())) for key, value in sorted(gate_by_type.items())}}
            if tasks else _unobservable("no workbench task records were found")),
        "token_usage": _token_usage(runs),
        "reviewer_finding_yield": _unobservable(
            "run verdicts record pass/fail, not finding counts or which findings were actionable"),
        "reviewer_drill_detection_rate": _unobservable(
            "no reviewer drill result store is present in the records read by this module"),
        "false_positive_or_dismissed_finding_rate": _unobservable(
            "the records do not link findings to dismissal decisions"),
        "production_rework": _unobservable(
            "outcome records do not link an accepted task to a later repair"),
        "cost": _unobservable("the run records contain no monetary cost field"),
        "runtime": _runtime(runs),
    }


def _patterns(runs: list[dict], tasks: list[dict], constraints: list[dict]) -> list[dict]:
    findings: list[dict] = []
    by_recipe: dict[str, list[dict]] = {}
    for run in runs:
        recipe = run.get("recipe")
        if isinstance(recipe, str) and recipe:
            by_recipe.setdefault(recipe, []).append(run)
    for constraint in constraints:
        if constraint["kind"] == "task-gate-failure":
            grouped: dict[str, list[dict]] = {}
            for pair in tasks:
                task_type = pair["task"].get("task_type")
                if isinstance(task_type, str) and task_type:
                    grouped.setdefault(task_type, []).append(pair)
            for task_type, group in sorted(grouped.items()):
                checks: dict[str, int] = {}
                for pair in group:
                    raw = pair["gate"].get("checks")
                    if not isinstance(raw, list):
                        continue
                    for check in raw:
                        if (isinstance(check, dict)
                                and check.get("status") in constraint["gate_statuses"]
                                and isinstance(check.get("name"), str)
                                and check["name"]):
                            checks[check["name"]] = checks.get(check["name"], 0) + 1
                for name, count in sorted(checks.items()):
                    if count >= constraint["minimum_occurrences"]:
                        findings.append({"kind": constraint["kind"],
                                         "group": {"task_type": task_type}, "check": name,
                                         "occurrences": count, "sample_size": len(group),
                                         "gate_statuses": constraint["gate_statuses"]})
            continue
        for recipe, group in sorted(by_recipe.items()):
            if constraint["kind"] == "late-stage-failure":
                late = constraint["late_steps"]
                count = sum(run.get("escalated_at") in late for run in group)
                detail = {"late_steps": late}
            else:
                threshold = constraint["repair_cycles_above"]
                count = sum(_nonnegative_int(run.get("retries"))
                            and run["retries"] > threshold for run in group)
                detail = {"repair_cycles_above": threshold}
            if count >= constraint["minimum_occurrences"]:
                findings.append({"kind": constraint["kind"], "group": {"recipe": recipe},
                                 "occurrences": count, "sample_size": len(group), **detail})
    return findings


def analyse(root: pathlib.Path | str, query: dict) -> dict:
    """Derive the report from repository records and caller-supplied boundaries only."""
    problems = validate_query(query)
    if problems:
        raise ValueError("not a workflow-effectiveness query:\n  " + "\n  ".join(problems))
    root = pathlib.Path(root)
    runs, unreadable_runs = _read_jsonl(root / ".rig" / "runs.jsonl", root)
    tasks, unreadable_tasks = _read_workbench(root)
    return {
        "schema": SCHEMA,
        "records": {"orchestrate_runs": len(runs), "workbench_tasks": len(tasks),
                    "unreadable": unreadable_runs + unreadable_tasks},
        "metrics": _metrics(runs, tasks),
        "patterns": _patterns(runs, tasks, query["patterns"]),
        "unobservable_patterns": ["redundant-repeated-steps", "low-yield-verifier",
                                  "high-yield-verifier", "missing-early-analysis-step",
                                  "risk-class-patterns"],
        "does_not_guarantee": [
            "that a recorded failure was caused by the workflow",
            "that changing the workflow would improve quality",
            "candidate generation, evaluation, promotion, or rollback",
            "that absent or unreadable records represent successful runs",
        ],
    }


COMPLETE, REFUSED, EXECUTION_ERROR = 0, 1, 2


def cmd_workflow_effectiveness(args) -> "NoReturn":  # noqa: F821
    """Print recorded metrics and constrained patterns; never turn an error into a finding."""
    import sys

    from .state import repo_root

    try:
        query = read_query(args.query)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"schema": SCHEMA, "status": "execution-error",
                          "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        sys.exit(EXECUTION_ERROR)
    problems = validate_query(query)
    if problems:
        print("\n".join(f"[REJECTED] {problem}" for problem in problems), file=sys.stderr)
        sys.exit(REFUSED)
    try:
        report = analyse(repo_root(), query)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"schema": SCHEMA, "status": "execution-error",
                          "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        sys.exit(EXECUTION_ERROR)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        records = report["records"]
        print(f"workflow effectiveness: {records['orchestrate_runs']} orchestrate run(s), "
              f"{records['workbench_tasks']} workbench task(s), "
              f"{len(records['unreadable'])} unreadable record(s)")
        for name, metric in report["metrics"].items():
            print(f"  {name}: {metric['status']}")
        for item in report["patterns"]:
            group = ", ".join(f"{name}={value}" for name, value in item["group"].items())
            print(f"  pattern {item['kind']} [{group}]: "
                  f"{item['occurrences']} of {item['sample_size']}")
        for guarantee in report["does_not_guarantee"]:
            print(f"  does not guarantee: {guarantee}")
    sys.exit(COMPLETE)
