"""rig-wb coverage — which documented requirements have evidence, and which only have prose.

rig's own thesis is that an unmeasured gate is a wish. The same standard applies to
the claims rig makes about itself: "we implement X" and "X demonstrably works" are
different statements, and a hand-maintained table in a document drifts away from the
repository within a release or two.

This command reads `evals/coverage-map.json` — one entry per documented requirement,
each naming the evidence that backs it — and does two things:

* `--check` (default, free): verifies the map itself. Every referenced test file,
  path and command must exist and be allowlisted; ids must be unique; a `planned`
  entry must carry a written spec. This is the CI-safe mode: it catches a map that
  claims evidence which is no longer there.
* `--run`: executes the deterministic evidence (repo tests and allowlisted commands,
  no LLM and no billing) and reports pass/fail per requirement.

Evidence kinds are deliberately unequal, because the underlying claims are:

    pytest / command   deterministic, runs here, proves the property. `expect_exit`
                       widens the passing exit codes where a non-zero code is the
                       reported finding rather than a failure (hostcheck exits 3 when
                       a host prerequisite is absent — the check worked, the host did
                       not, and those are different things)
    file               the mechanism exists in the tree; existence is not effect
    paid               needs a real provider and real billing; the pass condition
                       is recorded so the result cannot be graded after the fact
    planned            not built yet; the spec says exactly what would close it

Nothing here upgrades a claim by describing it more confidently. An item with only
`file` evidence reports as `declared`, not as `measured`, and that is the point.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

MAP_VERSION = 1
DEFAULT_MAP = "evals/coverage-map.json"
EVIDENCE_KINDS = ("pytest", "command", "file", "paid", "planned")
SCOPES = ("rig", "host")
COMMAND_TIMEOUT_S = 900

# Allowlisted argv prefixes. Evidence runs with shell=False; anything outside these
# prefixes is rejected by --check, so the map cannot smuggle in arbitrary execution.
ALLOWED_COMMAND_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("python3", "-m", "rig_workbench.cli"),
    ("python3", "-m", "pytest"),
    ("python3", "scripts/validate.py"),
    ("python3", "scripts/orchestrate.py"),
    ("python3", "scripts/workbench.py"),
)

STATUS_MEASURED = "measured"
STATUS_PARTIAL = "partial"
STATUS_DECLARED = "declared"
STATUS_PAID_ONLY = "paid-only"
STATUS_PLANNED = "planned"


class MapError(Exception):
    """The coverage map is inconsistent with the repository."""


def repo_root(start: pathlib.Path | None = None) -> pathlib.Path:
    here = (start or pathlib.Path(__file__).resolve().parent).resolve()
    for candidate in (here, *here.parents):
        if (candidate / DEFAULT_MAP).exists():
            return candidate
    return here


def load_map(path: pathlib.Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise MapError(f"coverage map not readable: {path} ({exc})") from exc
    except ValueError as exc:
        raise MapError(f"coverage map is not valid JSON: {path} ({exc})") from exc
    if not isinstance(data, dict):
        raise MapError("coverage map must be a JSON object")
    version = data.get("coverage_map_version")
    if version != MAP_VERSION:
        raise MapError(f"unsupported coverage_map_version: {version!r} (expected {MAP_VERSION})")
    if not isinstance(data.get("items"), list) or not data["items"]:
        raise MapError("coverage map must carry a non-empty 'items' array")
    return data


def _validate_evidence(item_id: str, evidence: object, root: pathlib.Path) -> list[str]:
    problems: list[str] = []
    if not isinstance(evidence, dict):
        return [f"{item_id}: evidence entries must be objects"]
    kind = evidence.get("kind")
    if kind not in EVIDENCE_KINDS:
        return [f"{item_id}: unknown evidence kind {kind!r}"]
    if kind in ("pytest", "file"):
        rel = evidence.get("path")
        if not isinstance(rel, str) or not rel:
            problems.append(f"{item_id}: {kind} evidence needs a 'path'")
        elif rel.startswith("/") or ".." in pathlib.PurePosixPath(rel).parts:
            problems.append(f"{item_id}: path must be repo-relative without '..': {rel!r}")
        elif not (root / rel).exists():
            problems.append(f"{item_id}: referenced path does not exist: {rel}")
    if kind == "command":
        argv = evidence.get("argv")
        if not isinstance(argv, list) or not all(isinstance(part, str) for part in argv) or not argv:
            problems.append(f"{item_id}: command evidence needs a non-empty argv array of strings")
        elif not any(tuple(argv[: len(prefix)]) == prefix for prefix in ALLOWED_COMMAND_PREFIXES):
            problems.append(f"{item_id}: command argv is not allowlisted: {argv}")
    expect_exit = evidence.get("expect_exit")
    if expect_exit is not None:
        if kind not in ("pytest", "command"):
            problems.append(f"{item_id}: 'expect_exit' only applies to executable evidence")
        elif (not isinstance(expect_exit, list) or not expect_exit
              or not all(isinstance(code, int) for code in expect_exit)):
            problems.append(f"{item_id}: 'expect_exit' must be a non-empty list of integers")
    if kind == "paid":
        for field in ("argv_hint", "pass_condition"):
            if not isinstance(evidence.get(field), str) or not evidence[field].strip():
                problems.append(f"{item_id}: paid evidence needs a non-empty {field!r}")
    if kind == "planned":
        spec = evidence.get("spec")
        if not isinstance(spec, str) or len(spec.strip()) < 20:
            problems.append(f"{item_id}: planned evidence needs a 'spec' saying what would close it")
    if not isinstance(evidence.get("proves", ""), str):
        problems.append(f"{item_id}: 'proves' must be a string when present")
    return problems


def validate(data: dict, root: pathlib.Path) -> list[str]:
    """Return every inconsistency between the map and the repository."""
    problems: list[str] = []
    seen: set[str] = set()
    for raw in data["items"]:
        if not isinstance(raw, dict):
            problems.append("items must be objects")
            continue
        item_id = raw.get("id")
        if not isinstance(item_id, str) or not item_id:
            problems.append("every item needs a non-empty 'id'")
            continue
        if item_id in seen:
            problems.append(f"duplicate item id: {item_id}")
        seen.add(item_id)
        for field in ("source", "requirement"):
            if not isinstance(raw.get(field), str) or not raw[field].strip():
                problems.append(f"{item_id}: missing {field!r}")
        if raw.get("scope") not in SCOPES:
            problems.append(f"{item_id}: scope must be one of {SCOPES}")
        evidence = raw.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            problems.append(f"{item_id}: needs at least one evidence entry")
            continue
        for entry in evidence:
            problems.extend(_validate_evidence(item_id, entry, root))
    return problems


def item_status(item: dict) -> str:
    """Status is derived, never stored — a claim cannot be upgraded by asserting it.

    An item that already runs evidence *and* still carries an outstanding `planned`
    or `paid` entry reports as `partial`, not `measured`: part of the requirement is
    demonstrated and part is not, and collapsing those two into one word is exactly
    the drift this map exists to prevent.
    """
    kinds = {entry.get("kind") for entry in item["evidence"]}
    if kinds & {"pytest", "command"}:
        return STATUS_PARTIAL if kinds & {"planned", "paid"} else STATUS_MEASURED
    if "file" in kinds:
        return STATUS_DECLARED
    if "paid" in kinds:
        return STATUS_PAID_ONLY
    return STATUS_PLANNED


def _evidence_argv(entry: dict) -> list[str] | None:
    if entry["kind"] == "pytest":
        return ["python3", "-m", "pytest", entry["path"], "-q"]
    if entry["kind"] == "command":
        return list(entry["argv"])
    return None


def run_evidence(data: dict, root: pathlib.Path, *, only: str | None = None) -> dict:
    """Execute every deterministic evidence entry. No LLM, no network, no billing."""
    results: list[dict] = []
    for item in data["items"]:
        if only and item["id"] != only:
            continue
        checks: list[dict] = []
        for entry in item["evidence"]:
            argv = _evidence_argv(entry)
            if argv is None:
                checks.append({"kind": entry["kind"], "ran": False})
                continue
            expected = entry.get("expect_exit", [0])
            completed = subprocess.run(  # noqa: S603 - argv is allowlisted, shell=False
                argv,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_S,
                check=False,
            )
            checks.append(
                {
                    "kind": entry["kind"],
                    "ran": True,
                    "argv": argv,
                    "passed": completed.returncode in expected,
                    "expected_exit": list(expected),
                    "returncode": completed.returncode,
                    "tail": completed.stdout.strip().splitlines()[-3:],
                }
            )
        ran = [c for c in checks if c["ran"]]
        results.append(
            {
                "id": item["id"],
                "requirement": item["requirement"],
                "status": item_status(item),
                "checks": checks,
                "passed": all(c["passed"] for c in ran) if ran else None,
            }
        )
    failed = [r["id"] for r in results if r["passed"] is False]
    return {"items": results, "failed": failed, "ok": not failed}


def summarise(data: dict) -> dict:
    counts = {
        STATUS_MEASURED: 0,
        STATUS_PARTIAL: 0,
        STATUS_DECLARED: 0,
        STATUS_PAID_ONLY: 0,
        STATUS_PLANNED: 0,
    }
    host = 0
    for item in data["items"]:
        counts[item_status(item)] += 1
        if item.get("scope") == "host":
            host += 1
    return {"total": len(data["items"]), "by_status": counts, "host_scope": host}


def _render_markdown(data: dict) -> str:
    lines = [
        "# rig coverage map",
        "",
        "One row per documented requirement. `measured` means deterministic evidence runs",
        "in this repository and nothing is outstanding; `partial` means some of it runs and",
        "some is still planned or needs a paid run; `declared` means the mechanism exists but",
        "its effect is not measured; `paid-only` needs a real provider; `planned` is not built yet.",
        "",
        "| id | source | requirement | scope | status | evidence |",
        "|---|---|---|---|---|---|",
    ]
    for item in data["items"]:
        evidence = ", ".join(
            entry["kind"] + (f" ({entry['path']})" if entry.get("path") else "")
            for entry in item["evidence"]
        )
        lines.append(
            f"| {item['id']} | {item['source']} | {item['requirement']} | "
            f"{item['scope']} | {item_status(item)} | {evidence} |"
        )
    summary = summarise(data)
    lines += ["", "## summary", ""]
    for status, count in summary["by_status"].items():
        lines.append(f"- {status}: {count}/{summary['total']}")
    lines.append(f"- host scope (rig reports, operator owns): {summary['host_scope']}/{summary['total']}")
    return "\n".join(lines) + "\n"


def _print_text(data: dict) -> None:
    print("## rig coverage map — documented requirement vs the evidence behind it\n")
    for item in data["items"]:
        status = item_status(item)
        print(f"[{status:<10}] {item['id']}  ({item['source']}, scope={item['scope']})")
        print(f"             {item['requirement']}")
        for entry in item["evidence"]:
            detail = entry.get("path") or " ".join(entry.get("argv", [])) or entry.get("argv_hint") or entry.get("spec", "")
            print(f"             - {entry['kind']}: {detail}")
        print()
    summary = summarise(data)
    parts = ", ".join(f"{status} {count}" for status, count in summary["by_status"].items())
    print(f"## {summary['total']} requirements — {parts}")
    print(f"   host scope (rig reports it, the operator owns it): {summary['host_scope']}")


def cmd_coverage(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="rig-wb coverage",
        description="Verify (and optionally execute) the evidence behind each documented requirement.",
    )
    parser.add_argument("--map", default=None, help=f"path to the coverage map (default: {DEFAULT_MAP})")
    parser.add_argument("--run", action="store_true", help="execute the deterministic evidence, not just verify the map")
    parser.add_argument("--only", default=None, help="restrict --run to a single requirement id")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of the text report")
    parser.add_argument("--markdown", action="store_true", help="emit the map as a Markdown table")
    args = parser.parse_args(argv)

    root = repo_root(pathlib.Path.cwd())
    map_path = pathlib.Path(args.map) if args.map else root / DEFAULT_MAP
    try:
        data = load_map(map_path)
    except MapError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    problems = validate(data, root)
    if problems:
        print("[ERROR] coverage map is inconsistent with the repository:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    if args.run:
        result = run_evidence(data, root, only=args.only)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            for item in result["items"]:
                verdict = {True: "PASS", False: "FAIL", None: "----"}[item["passed"]]
                print(f"[{verdict}] {item['id']}  ({item['status']})")
                for check in item["checks"]:
                    if check["ran"] and not check["passed"]:
                        print(f"        failed: {' '.join(check['argv'])} (exit {check['returncode']})")
            print()
            if result["ok"]:
                print("All deterministic evidence passed.")
            else:
                print(f"Failed: {', '.join(result['failed'])}")
        return 0 if result["ok"] else 1

    if args.json:
        print(json.dumps({"summary": summarise(data), "items": data["items"]}, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.markdown:
        print(_render_markdown(data), end="")
    else:
        _print_text(data)
    return 0
