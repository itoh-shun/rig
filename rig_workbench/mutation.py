"""Turn an external mutation-testing report into an acceptance-gate criterion.

`/rig:drill` measures whether a *reviewer* finds seeded defects. It says nothing
about whether the *test suite* would. That is the other half of "detection power",
and mature external answers already exist — Stryker for JS/TS/C#, mutmut for
Python — so rig does not reimplement mutation testing. It does what
`scripts/sast_adapter.py` does for security scanners: read the tool's report,
reduce it to one number with a stated meaning, and hand it to the gate.

What makes this a rig command rather than a script is that the operator does not
have to know any of the above. `rig-wb mutation` finds the report, works out which
of the three shapes it is, and scores it; `--run` also runs the project's mutation
tool first, when the project's own configuration says which one it uses.

Three report shapes are accepted, all produced by tools people already run:

    elements   the mutation-testing-elements JSON schema (Stryker and friends)
    mutmut     mutants/mutmut-cicd-stats.json, written by `mutmut export-cicd-stats`
    junit      JUnit XML — mutmut 2.x's `junitxml`, or any other JUnit producer

The mutmut split is a version boundary, not a preference: 3.x dropped `junitxml`
and replaced it with `export-cicd-stats`, which writes a counts summary instead of
one test case per mutant. Both are supported so the adapter does not force a
version upgrade on the project using it.

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
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import xml.etree.ElementTree as ET

from . import repo_paths

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


# mutmut 3.x status names, grouped the same way as the elements statuses.
# `suspicious` is neither: the mutant made the suite behave oddly (usually timing)
# without a verdict either way, so it is excluded rather than counted as a hole.
MUTMUT_DETECTED = ("killed", "timeout")
MUTMUT_UNDETECTED = ("survived", "no_tests")
MUTMUT_INVALID = ("skipped", "suspicious", "segfault", "check_was_interrupted_by_user")


def parse_mutmut(path: pathlib.Path) -> dict:
    """mutmut 3.x: `mutmut export-cicd-stats` → mutants/mutmut-cicd-stats.json."""
    try:
        data = json.loads(_read_text(path))
    except ValueError as exc:
        raise ReportError(f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or "killed" not in data or "survived" not in data:
        raise ReportError("no 'killed'/'survived' counts — is this a mutmut export-cicd-stats report?")
    counts = {"detected": 0, "undetected": 0, "invalid": 0}
    by_status: dict[str, int] = {}
    for key, value in data.items():
        if key == "total":
            continue
        if not isinstance(value, int) or value < 0:
            raise ReportError(f"count for {key!r} is not a non-negative integer: {value!r}")
        by_status[key] = value
        if key in MUTMUT_DETECTED:
            counts["detected"] += value
        elif key in MUTMUT_UNDETECTED:
            counts["undetected"] += value
        elif key in MUTMUT_INVALID:
            counts["invalid"] += value
        else:
            raise ReportError(f"unknown mutmut status: {key!r}")
    total = data.get("total")
    tallied = counts["detected"] + counts["undetected"] + counts["invalid"]
    if isinstance(total, int) and total != tallied:
        # A mismatch means a status this adapter does not know about was dropped;
        # scoring on a short denominator would silently inflate the result.
        raise ReportError(f"counts sum to {tallied} but the report says total={total}")
    return {"format": "mutmut", "by_status": by_status, **counts}


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


PARSERS = {"elements": parse_elements, "mutmut": parse_mutmut, "junit": parse_junit}


# ── finding the report without being told where it is ────────────────────
#
# Every entry is a path one of the supported tools writes by default. The format
# recorded here is only a hint: `sniff_format` reads the file and decides, so a
# report written to an unexpected name is still read correctly and a report whose
# name lies about its shape does not fool the parser.

REPORT_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("reports/mutation/mutation.json", "elements"),
    ("mutants/mutmut-cicd-stats.json", "mutmut"),
    ("mutation.json", "elements"),
    ("mutation-report.json", "elements"),
    ("mutmut-cicd-stats.json", "mutmut"),
    ("mutation.xml", "junit"),
)


def sniff_format(path: pathlib.Path) -> str | None:
    """Decide which of the three shapes a report is, by reading it."""
    try:
        text = _read_text(path)
    except ReportError:
        return None
    stripped = text.lstrip()
    if stripped.startswith("<"):
        return "junit"
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("files"), dict):
        return "elements"
    if "killed" in data and "survived" in data:
        return "mutmut"
    return None


def detect_report(root: pathlib.Path) -> tuple[pathlib.Path, str] | None:
    """Return the first conventional report present, with its sniffed format."""
    for relative, _hint in REPORT_CANDIDATES:
        candidate = root / relative
        if not candidate.is_file():
            continue
        fmt = sniff_format(candidate)
        if fmt:
            return candidate, fmt
    return None


# ── finding the tool the project already uses ────────────────────────────
#
# `--run` executes these. Detection is deliberately evidence-based: a marker file
# must actually name the tool, because guessing wrong here means running a long
# job the project never asked for. The commands are the tools' documented ones.

RUNNERS: tuple[dict, ...] = (
    {
        "tool": "stryker",
        "markers": ("stryker.conf.json", "stryker.conf.js", "stryker.conf.mjs",
                    "stryker.conf.cjs", "stryker.config.json", "stryker.config.mjs"),
        "content_markers": (("package.json", "@stryker-mutator"),),
        "commands": (["npx", "stryker", "run"],),
        "report": ("reports/mutation/mutation.json", "elements"),
    },
    {
        "tool": "mutmut",
        "markers": ("mutmut.toml", ".mutmut.toml"),
        "content_markers": (("setup.cfg", "[mutmut]"), ("pyproject.toml", "mutmut"),
                            ("tox.ini", "[mutmut]")),
        # `mutmut run` exits non-zero whenever mutants survive, which is the normal
        # case; the report, not the exit code, is what this reads.
        "commands": (["mutmut", "run"], ["mutmut", "export-cicd-stats"]),
        "report": ("mutants/mutmut-cicd-stats.json", "mutmut"),
    },
)


def detect_runner(root: pathlib.Path) -> dict | None:
    """Return the mutation runner this project is configured for, or None."""
    for runner in RUNNERS:
        for marker in runner["markers"]:
            if (root / marker).is_file():
                return {**runner, "why": f"{marker} is present"}
        for name, needle in runner["content_markers"]:
            path = root / name
            if not path.is_file():
                continue
            try:
                if needle in path.read_text(encoding="utf-8", errors="replace"):
                    return {**runner, "why": f"{name} mentions {needle}"}
            except OSError:
                continue
    return None


def run_tool(runner: dict, root: pathlib.Path, *, echo=print) -> None:
    """Run the project's mutation tool. Output is streamed, not captured."""
    for command in runner["commands"]:
        echo(f"$ {' '.join(command)}")
        try:
            subprocess.run(command, cwd=root, check=False, shell=False)  # noqa: S603
        except FileNotFoundError as exc:
            raise ReportError(
                f"{command[0]} is not on PATH — install {runner['tool']} in this project first"
            ) from exc


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


def _workbench_script() -> pathlib.Path:
    """Locate scripts/workbench.py — env override, then install source, then cwd
    (the shared search in `repo_paths`)."""
    script = repo_paths.find_script("workbench.py")
    if script:
        return script
    raise ReportError(
        "scripts/workbench.py not found — set RIG_HOME to the rig repo root to use --apply"
    )


def apply_to_gate(result: dict, counts: dict, task_id: str) -> int:
    workbench = _workbench_script()
    detail = result["detail"].replace('"', "'").replace(":", ";")
    completed = subprocess.run(  # noqa: S603
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
    if completed.returncode != 0:
        # 1.31.0 only recognised one failure string and reported success for every
        # other one — including a task id that does not exist, which then looked
        # like the criterion had reached a gate.
        return completed.returncode
    print(f"applied {CRITERION}={result['status']} to {task_id} "
          f"(detected {counts['detected']}, undetected {counts['undetected']})")
    return 0


def _report_text(report: pathlib.Path, counts: dict, result: dict, root: pathlib.Path) -> str:
    try:
        shown = report.relative_to(root)
    except ValueError:
        shown = report
    lines = [
        f"## mutation score — {shown} ({counts['format']})",
        "",
        f"  detected   {counts['detected']}",
        f"  undetected {counts['undetected']}",
        f"  invalid    {counts['invalid']}  (excluded from the denominator)",
        "",
        f"  {result['status']}: {result['detail']}",
    ]
    if result["score"] is not None:
        lines.append("")
        lines.append("  Only the direction is actionable: equivalent mutants put 100% out of")
        lines.append("  reach, so this is compared against the baseline, never against a target.")
    return "\n".join(lines)


def cmd_mutation(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="rig-wb mutation",
        description="Score an external mutation-testing report and hand it to the acceptance gate.",
    )
    # The positional pair is the 1.31.x `mutation_adapter.py <format> <report>` form,
    # kept so instructions written against it keep working. Both are optional now:
    # with neither, the report is found and its format read from the file itself.
    parser.add_argument("format", nargs="?", choices=sorted(PARSERS),
                        help="report format (default: read from the file)")
    parser.add_argument("report", nargs="?", help="path to the report (default: look for one)")
    parser.add_argument("--repo", default=".", help="project root to inspect (default: cwd)")
    parser.add_argument("--report", dest="report_flag", help="path to the report")
    parser.add_argument("--format", dest="format_flag", choices=sorted(PARSERS),
                        help="force the report format instead of reading it from the file")
    parser.add_argument("--run", action="store_true",
                        help="run the project's own mutation tool first (detected from its config)")
    parser.add_argument("--baseline", default=DEFAULT_BASELINE, help=f"baseline file (default: {DEFAULT_BASELINE})")
    parser.add_argument("--tolerance", type=float, default=0.0,
                        help="drop absorbed without warning, as a fraction (0.02 = two points)")
    parser.add_argument("--record-baseline", action="store_true",
                        help="write this score as the baseline to compare future runs against")
    parser.add_argument("--apply", metavar="TASK_ID",
                        help=f"record the result on a task's gate as {CRITERION}")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of the text report")
    args = parser.parse_args(argv)

    root = pathlib.Path(args.repo).resolve()

    if args.run:
        runner = detect_runner(root)
        if runner is None:
            print("[ERROR] no mutation tool detected in this project. rig does not run mutation\n"
                  "        testing itself — install Stryker or mutmut and configure it, or run\n"
                  "        your tool by hand and pass its report with --report.", file=sys.stderr)
            return 2
        print(f"[rig] {runner['tool']} detected ({runner['why']})")
        try:
            run_tool(runner, root)
        except ReportError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 2

    explicit = args.report_flag or args.report
    if explicit:
        report = pathlib.Path(explicit)
        if not report.is_file() and not report.is_absolute():
            report = root / report
        fmt = args.format_flag or args.format or sniff_format(report)
        if fmt is None:
            print(f"[ERROR] cannot tell what kind of report {report} is. Pass --format.", file=sys.stderr)
            return 2
    else:
        found = detect_report(root)
        if found is None:
            searched = "\n".join(f"          {path}" for path, _ in REPORT_CANDIDATES)
            print("[ERROR] no mutation report found. Looked for:\n" + searched
                  + "\n        Run your mutation tool first (`--run` does it for you when the\n"
                    "        project's config names one), or pass --report <path>.", file=sys.stderr)
            return 2
        report, sniffed = found
        fmt = args.format_flag or sniffed

    try:
        counts = PARSERS[fmt](report)
    except ReportError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    baseline_path = pathlib.Path(args.baseline)
    if not baseline_path.is_absolute():
        baseline_path = root / baseline_path
    result = evaluate(counts, load_baseline(baseline_path), args.tolerance)

    if args.record_baseline and result["score"] is not None:
        write_baseline(baseline_path, counts, result["score"])
        result["detail"] += f"; baseline written to {baseline_path}"

    if args.apply:
        return apply_to_gate(result, counts, args.apply)
    if args.json:
        print(json.dumps({"criterion": CRITERION, "report": str(report), **result, "counts": counts},
                         ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_report_text(report, counts, result, root))
    return 0


if __name__ == "__main__":
    sys.exit(cmd_mutation(sys.argv[1:]))
