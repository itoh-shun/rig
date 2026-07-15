#!/usr/bin/env python3
"""
rig SAST/DAST adapter (#276)

A thin adapter that converts JSON output from an external static-analysis
tool (Semgrep, etc.) into a single aggregated check
(`sast_findings_clear`) on rig's acceptance-gate. rig itself never runs the
analysis — install and run the tool yourself, then hand its output to this
adapter (no tool-specific option/ruleset knowledge is assumed here).

`workbench.py gate` can only record a verdict for a criterion name that's
already registered in `acceptance.json` (it's not designed to grow an
unbounded set of dynamic criterion names). So instead of one check per
finding, this is a **single worst-case-aggregated criterion**. A project
adds `"sast_findings_clear"` once to the relevant task_type (or `"*"`) in
`.rig/gates.json`'s `extra_criteria`, and this adapter can then record a
verdict for it (see "optional criteria" in
`facets/instructions/acceptance-check`).

Supported formats:
  semgrep --json output
    {"results": [{"check_id", "path", "start": {"line"},
                  "extra": {"severity", "message"}}, ...]}

Usage:
  python3 scripts/sast_adapter.py semgrep <semgrep-output.json>
      -> prints the aggregated result (status/detail/findings list) as JSON to
         stdout (no side effects)
  python3 scripts/sast_adapter.py semgrep <semgrep-output.json> --apply <task_id>
      -> actually calls `workbench.py gate <task_id> --set sast_findings_clear=...`
         (a task that hasn't registered `sast_findings_clear` in `.rig/gates.json`
         first gets rejected by workbench.py itself as "not part of this task's gate")

Exit code: 0=success (including zero findings) / 1=input error
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

CRITERION_NAME = "sast_findings_clear"

# Severity spelling varies by tool; normalize and add to this map as new tools are supported.
_SEVERITY_TO_STATUS = {
    "ERROR": "failed", "CRITICAL": "failed", "HIGH": "failed",
    "WARNING": "warning", "MEDIUM": "warning", "LOW": "warning",
    "INFO": "warning", "NOTE": "warning",
}
_STATUS_RANK = {"passed": 0, "warning": 1, "failed": 2}  # worst-case aggregation priority


def parse_semgrep(path: pathlib.Path) -> list[dict]:
    """Convert semgrep --json output into a normalized finding list (pure function)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    findings = []
    for r in data.get("results", []):
        sev = (r.get("extra", {}).get("severity") or "WARNING").upper()
        status = _SEVERITY_TO_STATUS.get(sev, "warning")
        line = (r.get("start") or {}).get("line", "?")
        loc = f"{r.get('path', '?')}:{line}"
        msg = (r.get("extra", {}).get("message") or r.get("check_id") or "").replace("\n", " ").strip()
        findings.append({"status": status, "text": f"{r.get('check_id', '?')} @ {loc}: {msg}"})
    return findings


ADAPTERS = {"semgrep": parse_semgrep}


def aggregate(findings: list[dict]) -> dict:
    """Finding list -> a single criterion (worst-case status + top-N detail)."""
    if not findings:
        return {"name": CRITERION_NAME, "status": "passed",
                "detail": "0 findings", "findings": []}
    worst = max((f["status"] for f in findings), key=lambda s: _STATUS_RANK[s])
    top = [f["text"] for f in findings[:5]]
    more = f" (and {len(findings) - 5} more)" if len(findings) > 5 else ""
    return {"name": CRITERION_NAME, "status": worst,
            "detail": (f"{len(findings)} findings: " + " / ".join(top) + more)[:400],
            "findings": findings}


def main() -> None:
    args = sys.argv[1:]
    if len(args) < 2 or args[0] not in ADAPTERS:
        print(f"[ERROR] usage: sast_adapter.py <{'|'.join(ADAPTERS)}> <output.json> [--apply <task_id>]",
              file=sys.stderr)
        sys.exit(1)

    tool, out_path = args[0], pathlib.Path(args[1])
    if not out_path.is_file():
        print(f"[ERROR] file not found: {out_path}", file=sys.stderr)
        sys.exit(1)

    try:
        findings = ADAPTERS[tool](out_path)
    except (json.JSONDecodeError, KeyError) as exc:
        print(f"[ERROR] could not parse as {tool} output: {exc}", file=sys.stderr)
        sys.exit(1)

    result = aggregate(findings)

    if "--apply" in args:
        task_id = args[args.index("--apply") + 1]
        wb = pathlib.Path(__file__).resolve().parent / "workbench.py"
        detail = result["detail"].replace('"', "'").replace(":", ";")
        # `workbench.py gate` exits non-zero when the overall gate ends failed/pending
        # (recording the criterion itself can succeed even so — the exit code reflects the
        # gate's overall verdict). Success/failure here is "did the record succeed", not
        # "did the gate pass", so this isn't run with check=True — output passes through as-is
        # and only a record failure (stderr) needs separate detection.
        subprocess.run([sys.executable, str(wb), "gate", task_id,
                       "--set", f"{CRITERION_NAME}={result['status']}:{detail}"])
        print(f"applied {CRITERION_NAME}={result['status']} to {task_id} ({len(findings)} findings)")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
