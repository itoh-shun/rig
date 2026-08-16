"""Real-world evidence ledger and Mission Control data model.

RIG already measures synthetic reviewer recall (drill), benchmark behavior, run
telemetry, production outcomes, and governance conformance.  This module joins
those pieces without pretending they prove more than they do:

* ``field-study.jsonl`` records observations from real project work.  The two
  arms are ``rig`` and ``bare``; incident, defect-catch, token and elapsed-time
  measurements are all optional except the outcome.  Missing measurements stay
  missing rather than becoming zero.
* production outcomes are read from the existing workbench ``outcome.json``
  files, so there is still one source of truth for RIG tasks.
* ``fleet.json`` is only a list of repositories to feed into the existing
  governance conformance/rollup engine.  There is no second policy evaluator.

The resulting ``mission_control_snapshot`` is presentation-neutral JSON.  The
HTML dashboard consumes the same object a future GUI/API can consume; UI code
must not become a second implementation of RIG's safety rules.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import statistics
import sys
from . import exitcodes
from typing import Any

FIELD_SCHEMA = "rig.field-study/v1"
FLEET_SCHEMA = "rig.fleet/v1"
CORE_STAGES = [
    {"id": "task", "label": "Task", "meaning": "state the work and acceptance intent"},
    {"id": "isolate", "label": "Isolate", "meaning": "keep generated changes away from the working tree"},
    {"id": "execute", "label": "Execute", "meaning": "run the selected recipe and implementation steps"},
    {"id": "verify", "label": "Verify", "meaning": "independent reviewers and deterministic gates measure the result"},
    {"id": "accept", "label": "Accept", "meaning": "apply only after the evidence is visible and policy permits it"},
]


def find_repo_root(start: pathlib.Path | None = None) -> pathlib.Path:
    p = (start or pathlib.Path.cwd()).resolve()
    for candidate in (p, *p.parents):
        if (candidate / ".rig").exists() or (candidate / ".git").exists():
            return candidate
    return p


def _load_json(path: pathlib.Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _load_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _parse_ts(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def _task_defaults(root: pathlib.Path, task_id: str) -> dict[str, Any]:
    run_dir = root / ".rig" / "runs" / task_id
    task = _load_json(run_dir / "task.json")
    if not task:
        raise ValueError(f"task not found: {task_id}")
    outcome = _load_json(run_dir / "outcome.json") or {}
    minutes: float | None = None
    created = task.get("created_at")
    accepted = task.get("accepted_at")
    if isinstance(created, str) and isinstance(accepted, str):
        try:
            minutes = max(0.0, (_parse_ts(accepted) - _parse_ts(created)).total_seconds() / 60.0)
        except (TypeError, ValueError):
            minutes = None
    return {
        "outcome": outcome.get("status"),
        "minutes": minutes,
        "project": str(root),
        "task_type": task.get("task_type"),
        "recipe": task.get("recipe"),
    }


def append_observation(
    root: pathlib.Path,
    *,
    arm: str,
    outcome: str | None,
    defects_caught: int | None = None,
    tokens: int | None = None,
    minutes: float | None = None,
    project: str | None = None,
    model: str | None = None,
    task_id: str | None = None,
    case: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Append one real-world observation and return the stored record.

    A RIG observation may reference a workbench task.  In that case production
    outcome and elapsed task time are inherited when they were already recorded.
    ``bare`` observations cannot point at a RIG task: that would make the arm
    label meaningless.
    """
    if arm not in {"rig", "bare"}:
        raise ValueError("arm must be 'rig' or 'bare'")
    if task_id and arm != "rig":
        raise ValueError("--task-id can only be used with --arm rig")

    inherited: dict[str, Any] = {}
    if task_id:
        inherited = _task_defaults(root, task_id)
    if outcome is None:
        outcome = inherited.get("outcome")
    if outcome not in {"ok", "incident"}:
        raise ValueError("outcome must be ok|incident (or be recorded on the referenced RIG task)")
    if defects_caught is not None and defects_caught < 0:
        raise ValueError("defects_caught must be >= 0")
    if tokens is not None and tokens < 0:
        raise ValueError("tokens must be >= 0")
    if minutes is not None and minutes < 0:
        raise ValueError("minutes must be >= 0")
    if minutes is None:
        minutes = inherited.get("minutes")
    if project is None:
        project = inherited.get("project") or str(root)

    record: dict[str, Any] = {
        "schema": FIELD_SCHEMA,
        "ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "arm": arm,
        "outcome": outcome,
        "project": project,
    }
    optional = {
        "defects_caught": defects_caught,
        "tokens": tokens,
        "minutes": round(minutes, 3) if minutes is not None else None,
        "model": model,
        "task_id": task_id,
        "case": case,
        "note": note,
        "task_type": inherited.get("task_type"),
        "recipe": inherited.get("recipe"),
    }
    record.update({key: value for key, value in optional.items() if value not in (None, "")})

    path = root / ".rig" / "field-study.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def field_observations(root: pathlib.Path, since: str | None = None) -> list[dict[str, Any]]:
    rows = [r for r in _load_jsonl(root / ".rig" / "field-study.jsonl")
            if r.get("schema") == FIELD_SCHEMA and r.get("arm") in {"rig", "bare"}]
    if since:
        rows = [r for r in rows if str(r.get("ts") or "")[:10] >= since]
    return rows


def _mean(values: list[float | int]) -> float | None:
    return round(float(statistics.mean(values)), 3) if values else None


def _arm_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    incidents = sum(1 for r in rows if r.get("outcome") == "incident")
    defects_rows = [r for r in rows if isinstance(r.get("defects_caught"), int)]
    token_rows = [r for r in rows if isinstance(r.get("tokens"), int)]
    minute_rows = [r for r in rows if isinstance(r.get("minutes"), (int, float))]
    joint = [r for r in rows if isinstance(r.get("defects_caught"), int)
             and isinstance(r.get("tokens"), int)]
    joint_defects = sum(int(r["defects_caught"]) for r in joint)
    joint_tokens = sum(int(r["tokens"]) for r in joint)
    return {
        "n": len(rows),
        "incidents": incidents,
        "incident_rate_pct": round(incidents / len(rows) * 100, 2) if rows else None,
        "defects_caught": (
            sum(int(r["defects_caught"]) for r in defects_rows) if defects_rows else None
        ),
        "defects_measured_n": len(defects_rows),
        "tokens_measured_n": len(token_rows),
        "tokens_total": sum(int(r["tokens"]) for r in token_rows) if token_rows else None,
        "tokens_mean": _mean([int(r["tokens"]) for r in token_rows]),
        "minutes_measured_n": len(minute_rows),
        "minutes_mean": _mean([float(r["minutes"]) for r in minute_rows]),
        "tokens_per_defect_caught": (
            round(joint_tokens / joint_defects, 1) if joint_defects > 0 else None
        ),
        "economics_joint_n": len(joint),
    }


def summarize_field_study(rows: list[dict[str, Any]]) -> dict[str, Any]:
    arms = {arm: _arm_summary([r for r in rows if r.get("arm") == arm])
            for arm in ("rig", "bare")}
    comparison: dict[str, Any] = {"available": False}
    if arms["rig"]["n"] and arms["bare"]["n"]:
        comparison = {
            "available": True,
            # Positive means fewer observed incidents in the RIG arm.
            "incident_rate_delta_pp_bare_minus_rig": round(
                float(arms["bare"]["incident_rate_pct"]) - float(arms["rig"]["incident_rate_pct"]), 2
            ),
            "tokens_mean_delta_rig_minus_bare": (
                round(float(arms["rig"]["tokens_mean"]) - float(arms["bare"]["tokens_mean"]), 3)
                if arms["rig"]["tokens_mean"] is not None and arms["bare"]["tokens_mean"] is not None
                else None
            ),
            "minutes_mean_delta_rig_minus_bare": (
                round(float(arms["rig"]["minutes_mean"]) - float(arms["bare"]["minutes_mean"]), 3)
                if arms["rig"]["minutes_mean"] is not None and arms["bare"]["minutes_mean"] is not None
                else None
            ),
        }

    case_arms: dict[str, set[str]] = {}
    for row in rows:
        case = row.get("case")
        if isinstance(case, str) and case:
            case_arms.setdefault(case, set()).add(str(row.get("arm")))
    matched = sorted(case for case, got in case_arms.items() if got == {"rig", "bare"})
    return {
        "schema": FIELD_SCHEMA,
        "observations": len(rows),
        "arms": arms,
        "comparison": comparison,
        "matched_cases": matched,
        "matched_case_count": len(matched),
        "claim": "observational-not-causal",
        "note": (
            "Field observations show association, not causation. Compare matched/controlled tasks "
            "or use `rig-wb bench` before attributing a difference to RIG."
        ),
    }


def production_outcomes(root: pathlib.Path) -> dict[str, Any]:
    base = root / ".rig" / "runs"
    accepted = 0
    recorded = 0
    ok = 0
    incidents = 0
    if base.is_dir():
        for run_dir in base.iterdir():
            if not run_dir.is_dir():
                continue
            task = _load_json(run_dir / "task.json")
            if not task or task.get("status") != "accepted":
                continue
            accepted += 1
            outcome = _load_json(run_dir / "outcome.json")
            if not outcome or outcome.get("status") not in {"ok", "incident"}:
                continue
            recorded += 1
            if outcome["status"] == "incident":
                incidents += 1
            else:
                ok += 1
    return {
        "accepted_tasks": accepted,
        "outcomes_recorded": recorded,
        "outcome_coverage_pct": round(recorded / accepted * 100, 2) if accepted else None,
        "ok": ok,
        "incidents": incidents,
        "incident_rate_pct": round(incidents / recorded * 100, 2) if recorded else None,
    }


def _resolve_fleet_projects(root: pathlib.Path, config: dict[str, Any]) -> list[pathlib.Path]:
    projects = config.get("projects")
    if not isinstance(projects, list):
        raise ValueError("fleet projects must be a list")
    resolved: list[pathlib.Path] = []
    for raw in projects:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("every fleet project must be a non-empty path string")
        path = pathlib.Path(raw).expanduser()
        resolved.append((path if path.is_absolute() else root / path).resolve())
    return resolved


def save_fleet_config(root: pathlib.Path, projects: list[str], since_days: int = 90) -> pathlib.Path:
    if not projects:
        raise ValueError("at least one --project is required")
    if since_days < 1:
        raise ValueError("since_days must be >= 1")
    config = {"schema": FLEET_SCHEMA, "projects": projects, "since_days": since_days}
    # Validate path shapes before persisting the config. Repositories need not all
    # exist yet; conformance will report the actual state when the fleet is read.
    _resolve_fleet_projects(root, config)
    path = root / ".rig" / "fleet.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def fleet_snapshot(root: pathlib.Path) -> dict[str, Any]:
    path = root / ".rig" / "fleet.json"
    if not path.is_file():
        return {"configured": False, "projects": 0, "teams": {}, "reports": []}
    config = _load_json(path)
    if config is None:
        return {"configured": True, "error": f"unreadable fleet config: {path}"}
    if config.get("schema") != FLEET_SCHEMA:
        return {"configured": True, "error": f"unsupported fleet schema: {config.get('schema')!r}"}
    try:
        roots = _resolve_fleet_projects(root, config)
        since_days = int(config.get("since_days", 90))
        if since_days < 1:
            raise ValueError("fleet since_days must be >= 1")
        from .govern import conformance

        result = conformance.rollup(roots, since_days=since_days).to_dict()
    except (OSError, TypeError, ValueError) as exc:
        return {"configured": True, "error": str(exc)}
    return {"configured": True, "since_days": since_days, **result}


def mission_control_snapshot(root: pathlib.Path, *, since: str | None = None) -> dict[str, Any]:
    rows = field_observations(root, since=since)
    return {
        "schema": "rig.mission-control/v1",
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "repo": str(root),
        "core": CORE_STAGES,
        "production": production_outcomes(root),
        "field_study": summarize_field_study(rows),
        "fleet": fleet_snapshot(root),
    }


def _print_summary(snapshot: dict[str, Any]) -> None:
    prod = snapshot["production"]
    field = snapshot["field_study"]
    print("## rig evidence")
    print("\nCore: " + " → ".join(stage["label"] for stage in snapshot["core"]))
    print("\nProduction outcomes")
    print(f"  accepted={prod['accepted_tasks']}  recorded={prod['outcomes_recorded']}  "
          f"incidents={prod['incidents']}  coverage={prod['outcome_coverage_pct']}")
    print("\nField study (observational)")
    for arm in ("rig", "bare"):
        s = field["arms"][arm]
        print(f"  {arm:<4} n={s['n']} incident_rate={s['incident_rate_pct']}% "
              f"defects_caught={s['defects_caught']} tokens_mean={s['tokens_mean']} "
              f"minutes_mean={s['minutes_mean']}")
    if field["comparison"]["available"]:
        print("  Δ incident-rate (bare - rig): "
              f"{field['comparison']['incident_rate_delta_pp_bare_minus_rig']} pp")
    print(f"  matched cases: {field['matched_case_count']}")
    print("  note: " + field["note"])
    fleet = snapshot["fleet"]
    print("\nFleet governance")
    if not fleet.get("configured"):
        print("  unconfigured (use `rig-wb evidence fleet-config --project <repo> ...`)")
    elif fleet.get("error"):
        print(f"  error: {fleet['error']}")
    else:
        print(f"  projects={fleet.get('projects', 0)} score={fleet.get('score', 0):.0%}")
        for team, info in sorted((fleet.get("teams") or {}).items()):
            print(f"  {team}: {info.get('projects', 0)} project(s), score={info.get('score', 0):.0%}, "
                  f"failing={', '.join(info.get('failing') or []) or '—'}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rig-wb evidence",
        description="real-project evidence: production outcomes, RIG-vs-bare field observations, and fleet rollup",
    )
    parser.add_argument("--repo", type=pathlib.Path, default=pathlib.Path.cwd(),
                        help="repository root (default: cwd, auto-detected)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("record", help="append one real-project RIG or bare observation")
    p.add_argument("--arm", choices=("rig", "bare"), required=True)
    p.add_argument("--outcome", choices=("ok", "incident"),
                   help="optional for a RIG --task-id that already has record-outcome")
    p.add_argument("--defects-caught", type=int,
                   help="defects caught before release; omit when unmeasured")
    p.add_argument("--tokens", type=int, help="tokens attributable to this observation; omit when unmeasured")
    p.add_argument("--minutes", type=float, help="elapsed minutes; RIG task can infer create→accept time")
    p.add_argument("--project", help="project label/path override")
    p.add_argument("--model", help="model/provider label")
    p.add_argument("--task-id", help="RIG workbench task to link and inherit outcome/time from")
    p.add_argument("--case", help="optional matched-case id shared by a RIG and bare observation")
    p.add_argument("--note")

    p = sub.add_parser("summary", help="show production + field evidence + configured fleet")
    p.add_argument("--since", help="only field observations since YYYY-MM-DD")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("fleet-config", help="save the repositories the Mission Control fleet view measures")
    p.add_argument("--project", dest="projects", action="append", required=True,
                   help="repo path, repeatable; relative paths resolve from this repo")
    p.add_argument("--since-days", type=int, default=90)

    p = sub.add_parser("fleet", help="run the saved multi-repository governance rollup")
    p.add_argument("--json", action="store_true")
    return parser


def cmd_evidence(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    root = find_repo_root(args.repo)
    try:
        if args.cmd == "record":
            record = append_observation(
                root, arm=args.arm, outcome=args.outcome, defects_caught=args.defects_caught,
                tokens=args.tokens, minutes=args.minutes, project=args.project, model=args.model,
                task_id=args.task_id, case=args.case, note=args.note,
            )
            print(json.dumps(record, ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "fleet-config":
            path = save_fleet_config(root, args.projects, since_days=args.since_days)
            print(f"saved fleet config: {path}")
            return 0
        if args.cmd == "fleet":
            result = fleet_snapshot(root)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                if not result.get("configured"):
                    print("fleet is not configured")
                elif result.get("error"):
                    print(f"fleet error: {result['error']}")
                else:
                    print(f"projects={result.get('projects', 0)} score={result.get('score', 0):.0%}")
                    for team, info in sorted((result.get("teams") or {}).items()):
                        print(f"{team}: {info.get('projects', 0)} project(s) score={info.get('score', 0):.0%} "
                              f"failing={', '.join(info.get('failing') or []) or '—'}")
            return 1 if result.get("error") else 0
        snapshot = mission_control_snapshot(root, since=args.since)
        if args.json:
            print(json.dumps(snapshot, ensure_ascii=False, indent=2))
        else:
            _print_summary(snapshot)
        return 0
    except (OSError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


@exitcodes.guard
def main() -> None:
    raise SystemExit(cmd_evidence(sys.argv[1:]))


if __name__ == "__main__":
    main()
