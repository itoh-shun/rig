#!/usr/bin/env python3
"""Ingest an external mutation-testing report and turn it into a gate criterion.

`/rig:drill` measures whether a *reviewer* finds seeded defects. It says nothing
about whether the *test suite* would. That is the other half of "detection power",
and it has a mature external answer already — Stryker for JS/TS/C#, mutmut for
Python — so rig does not reimplement it. This adapter does what
`scripts/sast_adapter.py` does for security scanners: read the tool's report,
reduce it to one number with a stated meaning, and hand it to the acceptance gate.

Two report shapes are accepted, both produced by tools people already run:

    elements   the mutation-testing-elements JSON schema (Stryker and friends)
    junit      JUnit XML, which `mutmut junitxml` emits

Scoring follows the usual convention: a timeout counts as detected (the mutant
changed behaviour enough to hang), no-coverage counts as undetected (nothing even
executed it), and compile/runtime errors are excluded as invalid rather than
counted against the suite.

    score = (killed + timeout) / (killed + timeout + survived + no_coverage)

**The criterion is warning-grade and comparative, never a fail and never a target.**
Equivalent mutants — mutations that cannot change observable behaviour — are a
known, unbounded fraction of any corpus, so 100% is not reachable and chasing the
absolute number wastes effort. What is worth watching is the direction: a suite
whose score drops has lost detection power, and that is what the baseline is for.

The criterion `mutation_score_not_regressed` is not built in. Declare it per
project, which is additive and cannot weaken anything:

    // .rig/gates.json
    {"extra_criteria": {"standard": ["mutation_score_not_regressed"]}}

Usage:
    mutation_adapter.py elements report.json [--baseline F] [--tolerance 0.02]
                                            [--record-baseline] [--apply <task_id>]
    mutation_adapter.py junit report.xml    [same flags]
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import xml.etree.ElementTree as ET

CRITERION = "mutation_score_not_regressed"
DEFAULT_BASELINE = ".rig/mutation-baseline.json"
BASELINE_VERSION = 1
MAX_REPORT_BYTES = 20 * 1024 * 1024

# mutation-testing-elements statuses, grouped by what they say about the suite.
DETECTED_STATUSES = {"killed", "timeout"}
UNDETECTED_STATUSES = {"survived", "nocoverage"}
INVALID_STATUSES = {"compileerror", "runtimeerror", "ignored"}


class ReportError(Exception):
    """The report could not be read as the declared format."""


def _read_text(path: pathlib.Path) -> str:
    if not path.is_file():
        raise ReportError(f"file not found: {path}")
    if path.stat().st_size > MAX_REPORT_BYTES:
        raise ReportError(f"report larger than {MAX_REPORT_BYTES} bytes: {path}")
    return path.read_text(encoding="utf-8", errors="replace")


def parse_elements(path: pathlib.Path) -> dict:
    """mutation-testing-elements JSON (Stryker et al.)."""
    try:
        data = json.loads(_read_text(path))
    except ValueError as exc:
        raise ReportError(f"not valid JSON: {exc}") from exc
    files = data.get("files")
    if not isinstance(files, dict):
        raise ReportError("no 'files' object — is this a mutation-testing-elements report?")
    counts = {"detected": 0, "undetected": 0, "invalid": 0}
    by_status: dict[str, int] = {}
    for entry in files.values():
        for mutant in (entry or {}).get("mutants", []) or []:
            raw = str(mutant.get("status", "")).strip()
            key = raw.lower().replace(" ", "").replace("_", "")
            by_status[raw or "(missing)"] = by_status.get(raw or "(missing)", 0) + 1
            if key in DETECTED_STATUSES:
                counts["detected"] += 1
            elif key in UNDETECTED_STATUSES:
                counts["undetected"] += 1
            elif key in INVALID_STATUSES:
                counts["invalid"] += 1
            else:
                raise ReportError(f"unknown mutant status: {raw!r}")
    return {"format": "elements", "by_status": by_status, **counts}


def parse_junit(path: pathlib.Path) -> dict:
    """JUnit XML (`mutmut junitxml`): a survived mutant is a failing test case."""
    text = _read_text(path)
    # ElementTree expands entities; a report is untrusted input if it came from CI.
    # Doctype/entity declarations are not part of any JUnit writer's output, so
    # refusing them costs nothing and closes the expansion hole outright.
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise ReportError("report declares a DOCTYPE or ENTITY; refusing to parse")
    try:
        root = ET.fromstring(text)  # noqa: S314 - entity declarations rejected above
    except ET.ParseError as exc:
        raise ReportError(f"not valid XML: {exc}") from exc
    cases = root.iter("testcase")
    counts = {"detected": 0, "undetected": 0, "invalid": 0}
    by_status: dict[str, int] = {}

    def bump(label: str) -> None:
        by_status[label] = by_status.get(label, 0) + 1

    seen = False
    for case in cases:
        seen = True
        if case.find("error") is not None:
            counts["invalid"] += 1
            bump("error")
        elif case.find("failure") is not None:
            counts["undetected"] += 1
            bump("survived")
        elif case.find("skipped") is not None:
            counts["undetected"] += 1
            bump("skipped")
        else:
            counts["detected"] += 1
            bump("killed")
    if not seen:
        raise ReportError("no <testcase> elements — is this a JUnit report?")
    return {"format": "junit", "by_status": by_status, **counts}


PARSERS = {"elements": parse_elements, "junit": parse_junit}


def score_of(counts: dict) -> float | None:
    valid = counts["detected"] + counts["undetected"]
    return round(counts["detected"] / valid, 4) if valid else None


def load_baseline(path: pathlib.Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("score"), (int, float)) else None


def write_baseline(path: pathlib.Path, counts: dict, score: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mutation_baseline_version": BASELINE_VERSION,
        "score": score,
        "detected": counts["detected"],
        "undetected": counts["undetected"],
        "format": counts["format"],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def evaluate(counts: dict, baseline: dict | None, tolerance: float) -> dict:
    """Compare against the baseline. Warning-grade only — this never fails a gate."""
    score = score_of(counts)
    if score is None:
        return {"status": "warning", "score": None,
                "detail": "no valid mutants in the report (every one was invalid or the run was empty)"}
    percent = f"{score * 100:.1f}%"
    if baseline is None:
        return {"status": "passed", "score": score,
                "detail": f"mutation score {percent}; no baseline to compare against yet"}
    previous = float(baseline["score"])
    floor = previous - tolerance
    if score >= floor:
        return {"status": "passed", "score": score,
                "detail": f"mutation score {percent} vs baseline {previous * 100:.1f}% (tolerance {tolerance * 100:.1f}pt)"}
    return {"status": "warning", "score": score,
            "detail": (f"mutation score fell to {percent} from {previous * 100:.1f}% "
                       f"(tolerance {tolerance * 100:.1f}pt) — the suite detects less than it did")}


def apply_to_gate(result: dict, counts: dict, task_id: str) -> int:
    workbench = pathlib.Path(__file__).resolve().parent / "workbench.py"
    detail = result["detail"].replace('"', "'").replace(":", ";")
    completed = subprocess.run(
        [sys.executable, str(workbench), "gate", task_id,
         "--set", f"{CRITERION}={result['status']}:{detail}"],
        capture_output=True, text=True, check=False,
    )
    sys.stdout.write(completed.stdout)
    if completed.stderr:
        sys.stderr.write(completed.stderr)
    if "does not exist" in completed.stderr:
        print(
            f"\n[hint] {CRITERION} is not part of this task's gate. Declare it once per\n"
            '       project in .rig/gates.json (additive — it cannot weaken anything):\n'
            f'       {{"extra_criteria": {{"standard": ["{CRITERION}"]}}}}',
            file=sys.stderr,
        )
        return 1
    print(f"applied {CRITERION}={result['status']} to {task_id} "
          f"(detected {counts['detected']}, undetected {counts['undetected']})")
    return 0


def _flag(args: list[str], name: str, fallback: str | None = None) -> str | None:
    if name in args:
        index = args.index(name)
        if index + 1 < len(args):
            return args[index + 1]
    return fallback


def main() -> int:
    args = sys.argv[1:]
    if len(args) < 2 or args[0] not in PARSERS:
        print(f"[ERROR] usage: mutation_adapter.py <{'|'.join(PARSERS)}> <report> "
              "[--baseline F] [--tolerance 0.02] [--record-baseline] [--apply <task_id>]",
              file=sys.stderr)
        return 2
    fmt, report = args[0], pathlib.Path(args[1])
    try:
        counts = PARSERS[fmt](report)
    except ReportError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    baseline_path = pathlib.Path(_flag(args, "--baseline", DEFAULT_BASELINE))
    try:
        tolerance = float(_flag(args, "--tolerance", "0.0"))
    except ValueError:
        print("[ERROR] --tolerance must be a number (0.02 = two percentage points)", file=sys.stderr)
        return 2
    baseline = load_baseline(baseline_path)
    result = evaluate(counts, baseline, tolerance)

    if "--record-baseline" in args and result["score"] is not None:
        write_baseline(baseline_path, counts, result["score"])
        result["detail"] += f"; baseline written to {baseline_path}"

    task_id = _flag(args, "--apply")
    if task_id:
        return apply_to_gate(result, counts, task_id)
    print(json.dumps({"criterion": CRITERION, **result, "counts": counts},
                     ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
