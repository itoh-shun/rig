#!/usr/bin/env python3
"""
rig SAST/SCA/DAST adapter (#276)

A thin adapter that converts JSON output from an external security tool into a
single aggregated check on rig's acceptance-gate. rig itself never runs the
analysis — install and run the tool yourself, then hand its output to this
adapter (no tool-specific option/ruleset knowledge is assumed here).

Three criteria are produced, one per tool family:
  - static analysis (SAST)      -> `sast_findings_clear`       (semgrep, sarif)
  - dependency / CVE scan (SCA) -> `sca_findings_clear`        (pip-audit, npm audit, trivy fs)
  - AI deep scan                -> `deep_scan_findings_clear`  (claude-security)

The `claude-security` and `sarif` inputs are the answer to a limit of diff-scoped
review: an LLM reviewing a change sees only the changed lines, so it misses a
flaw in trusted, *unchanged* code the change relies on. The Claude Security
plugin (and any whole-repo analyzer emitting SARIF) scans the whole tree, so its
findings — folded into the gate here — cover exactly that blind spot.

`workbench.py gate` can only record a verdict for a criterion name that's
already registered in `acceptance.json` (it's not designed to grow an
unbounded set of dynamic criterion names). So instead of one check per
finding, this is a **single worst-case-aggregated criterion** per family. A
project adds `"sast_findings_clear"` / `"sca_findings_clear"` once to the
relevant task_type (or `"*"`) in `.rig/gates.json`'s `extra_criteria`, and this
adapter can then record a verdict for it (see "optional criteria" in
`facets/instructions/acceptance-check`).

DAST (dynamic scanning of a running target) is deliberately **out of scope**
here: this adapter only ingests the output of a scan you already ran against a
target you are authorized to test. Wiring a live scanner is tracked separately
and stays behind an explicit authorized-target allowlist.

Supported formats:
  semgrep --json
    {"results": [{"check_id", "path", "start": {"line"}, "extra": {"severity", "message"}}, ...]}
  sarif (SARIF 2.1.0: CodeQL, `semgrep --sarif`, Claude Security managed export, …)
    {"runs": [{"results": [{"ruleId", "level", "message": {"text"}, "locations": [...]}]}]}
  pip-audit --format json
    {"dependencies": [{"name", "version", "vulns": [{"id", "fix_versions", "description"}]}]}
  npm audit --json (npm >= 7)
    {"vulnerabilities": {"<pkg>": {"severity", "via": [...], "name"}}}
  trivy fs --format json
    {"Results": [{"Target", "Vulnerabilities": [{"VulnerabilityID", "PkgName", "Severity", "Title"}]}]}
  claude-security (CLAUDE-SECURITY-<ts>/CLAUDE-SECURITY-RESULTS.jsonl; one finding per line)
    {"id": "F1", "severity": "HIGH", "cwe": "CWE-863", "file", "line", "impact", ...}
    (key names are tolerant aliases — the plugin's schema is not published as fixed)

Usage:
  python3 scripts/sast_adapter.py <tool> <output.json>
      -> prints the aggregated result (status/detail/findings list) as JSON to
         stdout (no side effects)
  python3 scripts/sast_adapter.py <tool> <output.json> --apply <task_id>
      -> actually calls `workbench.py gate <task_id> --set <criterion>=...`
         (a task that hasn't registered the criterion in `.rig/gates.json`
         first gets rejected by workbench.py itself as "not part of this task's gate")

Exit code: 0=success (including zero findings) / 1=input error
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

CRITERION_NAME = "sast_findings_clear"
SCA_CRITERION_NAME = "sca_findings_clear"
DEEP_SCAN_CRITERION_NAME = "deep_scan_findings_clear"

# Severity spelling varies by tool; normalize and add to this map as new tools are supported.
_SEVERITY_TO_STATUS = {
    "ERROR": "failed", "CRITICAL": "failed", "HIGH": "failed",
    "WARNING": "warning", "MEDIUM": "warning", "LOW": "warning",
    "INFO": "warning", "NOTE": "warning", "MODERATE": "warning",
    "UNKNOWN": "warning", "": "warning",
}
_STATUS_RANK = {"passed": 0, "warning": 1, "failed": 2}  # worst-case aggregation priority


def _clean(text: object) -> str:
    return str(text or "").replace("\n", " ").strip()


def _first(obj: dict, keys: tuple[str, ...]) -> str:
    """First present, non-empty value among candidate keys (schema-tolerant)."""
    for key in keys:
        if obj.get(key) not in (None, "", [], {}):
            return _clean(obj[key])
    return ""


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


def parse_pip_audit(path: pathlib.Path) -> list[dict]:
    """Convert pip-audit --format json into a normalized finding list.

    A known advisory against a dependency is fail-grade regardless of the CVSS
    band pip-audit reports (it often reports none): a shippable vulnerability is
    exactly what SCA exists to stop.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    # pip-audit emits {"dependencies": [...]} in recent versions and a bare list in older ones.
    dependencies = data.get("dependencies", data) if isinstance(data, dict) else data
    findings = []
    for dep in dependencies or []:
        name, version = _clean(dep.get("name")), _clean(dep.get("version"))
        for vuln in dep.get("vulns", []) or []:
            fix = ", ".join(vuln.get("fix_versions", []) or []) or "no fix listed"
            vid = _clean(vuln.get("id")) or "?"
            findings.append({
                "status": "failed",
                "text": f"{vid} @ {name} {version}: fix={fix}",
            })
    return findings


def parse_npm_audit(path: pathlib.Path) -> list[dict]:
    """Convert `npm audit --json` (npm >= 7) into a normalized finding list."""
    data = json.loads(path.read_text(encoding="utf-8"))
    findings = []
    for pkg, entry in (data.get("vulnerabilities") or {}).items():
        sev = _clean(entry.get("severity")).upper()
        status = _SEVERITY_TO_STATUS.get(sev, "warning")
        via = entry.get("via", []) or []
        titles = [v.get("title") for v in via if isinstance(v, dict) and v.get("title")]
        detail = _clean(titles[0]) if titles else sev.lower()
        findings.append({"status": status, "text": f"{pkg} ({sev.lower()}): {detail}"})
    return findings


def parse_trivy(path: pathlib.Path) -> list[dict]:
    """Convert `trivy fs --format json` into a normalized finding list."""
    data = json.loads(path.read_text(encoding="utf-8"))
    findings = []
    for result in data.get("Results", []) or []:
        for vuln in result.get("Vulnerabilities", []) or []:
            sev = _clean(vuln.get("Severity")).upper()
            status = _SEVERITY_TO_STATUS.get(sev, "warning")
            vid = _clean(vuln.get("VulnerabilityID")) or "?"
            pkg = _clean(vuln.get("PkgName"))
            title = _clean(vuln.get("Title")) or sev.lower()
            findings.append({"status": status, "text": f"{vid} @ {pkg}: {title}"})
    return findings


def parse_sarif(path: pathlib.Path) -> list[dict]:
    """Convert a SARIF 2.1.0 log into a normalized finding list.

    SARIF is the interoperable format many analyzers emit (CodeQL,
    `semgrep --sarif`, the managed Claude Security product's export, …). Severity
    comes from each result's `level` (error/warning/note); when a result omits it
    we default to warning rather than inventing a severity.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    findings = []
    for run in data.get("runs", []) or []:
        for result in run.get("results", []) or []:
            level = _clean(result.get("level")).upper()
            status = _SEVERITY_TO_STATUS.get(level, "warning") if level else "warning"
            rule = _clean(result.get("ruleId")) or "?"
            message = _clean((result.get("message") or {}).get("text"))
            location = "?"
            locations = result.get("locations") or []
            if locations:
                physical = (locations[0] or {}).get("physicalLocation") or {}
                uri = _clean((physical.get("artifactLocation") or {}).get("uri")) or "?"
                line = (physical.get("region") or {}).get("startLine", "?")
                location = f"{uri}:{line}"
            findings.append({"status": status, "text": f"{rule} @ {location}: {message}"})
    return findings


def parse_claude_security(path: pathlib.Path) -> list[dict]:
    """Convert Claude Security's CLAUDE-SECURITY-RESULTS.jsonl into findings.

    One JSON object per line. The plugin's findings carry an id (F1…), severity
    (HIGH/MEDIUM/LOW), confidence, a CWE, the sink file/line, and impact/
    recommendation prose; every reported finding was already independently
    verified by the plugin's own reviewer agents. The exact key names are not
    published as a fixed schema, so this reads a tolerant set of aliases rather
    than hard-coding guessed keys — adjust the alias tuples if a field is missed.
    """
    findings = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        severity = _first(obj, ("severity", "sev", "risk", "level")).upper()
        status = _SEVERITY_TO_STATUS.get(severity, "warning")
        finding_id = _first(obj, ("id", "finding_id", "F"))
        cwe = _first(obj, ("cwe", "cwe_id", "cweId"))
        file = _first(obj, ("file", "path", "file_path", "location", "sink_file"))
        line_no = _first(obj, ("line", "sink_line", "start_line", "lineno", "startLine"))
        message = _first(obj, ("impact", "message", "title", "description", "summary", "recommendation"))
        loc = f"{file}:{line_no}" if file else "?"
        label = " ".join(part for part in (finding_id, cwe) if part) or "finding"
        findings.append({"status": status, "text": f"{label} @ {loc}: {message}".strip()})
    return findings


# tool -> (parser, criterion it records against)
ADAPTERS = {
    "semgrep": (parse_semgrep, CRITERION_NAME),
    "sarif": (parse_sarif, CRITERION_NAME),
    "pip-audit": (parse_pip_audit, SCA_CRITERION_NAME),
    "npm-audit": (parse_npm_audit, SCA_CRITERION_NAME),
    "trivy": (parse_trivy, SCA_CRITERION_NAME),
    "claude-security": (parse_claude_security, DEEP_SCAN_CRITERION_NAME),
}


def aggregate(findings: list[dict], criterion: str = CRITERION_NAME) -> dict:
    """Finding list -> a single criterion (worst-case status + top-N detail)."""
    if not findings:
        return {"name": criterion, "status": "passed",
                "detail": "0 findings", "findings": []}
    worst = max((f["status"] for f in findings), key=lambda s: _STATUS_RANK[s])
    top = [f["text"] for f in findings[:5]]
    more = f" (and {len(findings) - 5} more)" if len(findings) > 5 else ""
    return {"name": criterion, "status": worst,
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

    parser, criterion = ADAPTERS[tool]
    try:
        findings = parser(out_path)
    except (json.JSONDecodeError, KeyError, AttributeError, TypeError) as exc:
        print(f"[ERROR] could not parse as {tool} output: {exc}", file=sys.stderr)
        sys.exit(1)

    result = aggregate(findings, criterion)

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
                       "--set", f"{criterion}={result['status']}:{detail}"])
        print(f"applied {criterion}={result['status']} to {task_id} ({len(findings)} findings)")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
