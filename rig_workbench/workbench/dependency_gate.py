"""workbench dependency_gate: dependency-update sensor backing the opt-in
`no_unvetted_dependency_update` criterion.

Real-world supply-chain incidents keep following the same shape: a package
that reviewers trusted ships a new version that nobody actually looked at —
sometimes because the maintainer's account was compromised, sometimes because
the new version wires in a lifecycle script that runs arbitrary code on
install. `event-stream`, `ua-parser-js`, `xz-utils` and the npm worm campaigns
of 2024-2025 are all instances of the same gap: the version bump landed in a
lockfile, and nothing evaluated it before it was trusted.

This sensor closes a slice of that gap for dependency changes that show up in
a task's diff. For every package manifest/lockfile that the diff adds or
changes (`package.json` + `package-lock.json` for npm, `requirements*.txt`
for pip, `Cargo.lock` for cargo), it diffs the parsed dependency set against
the base commit's, and for every package that is newly added or bumped to a
different version, checks:

  install_script       (local, free) the npm lockfile records
                        `hasInstallScript: true` for this exact version — a
                        lifecycle script (preinstall/install/postinstall) will
                        run arbitrary code the moment `npm install` resolves
                        it. Lockfile v1 does not carry this field, so v1
                        entries report "unknown", never a false negative
                        rendered as clean.
  fresh_release         (network: package registry) the resolved version was
                        published within a cooldown window (default 72h, see
                        RIG_DEP_GATE_COOLDOWN_HOURS) of the *current* accept
                        attempt. A version that is hours old has had no time
                        to be scrutinized by the ecosystem it shipped into —
                        this is a schedule question, not a defect determination.
  known_vulnerability   (network: OSV.dev) the resolved version matches an
                        open advisory for this ecosystem/package/version.
  known_malicious_package
                        (network: OSV.dev) same lookup, but the advisory id
                        (or one of its aliases) carries OSV's malware prefix
                        `MAL-` — a confirmed supply-chain compromise, not a
                        garden-variety CVE.

Grading mirrors injection.py/destructive.py: `known_malicious_package` is the
one unambiguous case and is **fail**-grade — a confirmed malicious-package
match should not need a human to notice it. Everything else
(`install_script`, `fresh_release`, `known_vulnerability`) is context-dependent
— a fresh release from a maintainer with a long track record is routine, an
install script is often legitimate (native addons) — so those stay
**warning**-grade, exactly the sensors' shared convention of "unambiguous
blocks, context-dependent asks for a look."

Unlike every other sensor in this package, this one makes real network calls
(a package registry, and https://api.osv.dev). That is why, unlike
`no_injection_markers`/`no_destructive_operation`, this criterion is
deliberately absent from every GATE_PRESETS entry — a project opts in through
`.rig/gates.json` `extra_criteria`, the same mechanism `evidence_anchors_resolve`
uses and for the same reason: a sensor whose signal quality depends on
reachable third-party services should not become mandatory on every `gate`
call in every network posture. `RIG_DEP_GATE_OFFLINE=1` skips both network
signals outright (install_script keeps working — it is free); any single
registry/OSV request that errors, times out, or returns unparsable data is
swallowed as "could not verify this one" rather than escalated into a finding
or a crash — a project with no route to npmjs.org must still be able to run
`gate`.

CLI: `workbench.py scan-dependencies [paths...]` scans manifest/lockfiles
directly (every dependency found is treated as newly introduced, since there
is no "before" to diff against); `scan-dependencies --diff <task-id>` scans
the task worktree's diff vs its base commit — what the gate sensor sees.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .secrets import untracked_files
from .state import die, effective_base, git, load_task, repo_root

SENSOR_CRITERION = "no_unvetted_dependency_update"

FRESH_RELEASE_HOURS_DEFAULT = 72.0
OSV_URL_DEFAULT = "https://api.osv.dev/v1/query"
HTTP_TIMEOUT_SECONDS = 5
OSV_ECOSYSTEM = {"npm": "npm", "pip": "PyPI", "cargo": "crates.io"}
_REQUIREMENTS_RE = re.compile(r"^requirements(?:-[\w.-]+)?\.txt$", re.IGNORECASE)
_PIP_PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([A-Za-z0-9][A-Za-z0-9._+!-]*)")


@dataclass(frozen=True)
class DependencyChange:
    path: str
    ecosystem: str
    name: str
    version: str
    is_new: bool
    has_install_script: bool | None


# ── environment knobs ───────────────────────────────────────────────────────
def _offline() -> bool:
    return os.environ.get("RIG_DEP_GATE_OFFLINE") == "1"


def _cooldown_hours() -> float:
    raw = os.environ.get("RIG_DEP_GATE_COOLDOWN_HOURS")
    if not raw:
        return FRESH_RELEASE_HOURS_DEFAULT
    try:
        return float(raw)
    except ValueError:
        return FRESH_RELEASE_HOURS_DEFAULT


def _osv_url() -> str:
    return os.environ.get("RIG_DEP_GATE_OSV_URL", OSV_URL_DEFAULT)


# ── manifest/lockfile parsing ────────────────────────────────────────────────
def manifest_ecosystem(basename: str) -> str | None:
    if basename == "package-lock.json":
        return "npm"
    if basename == "Cargo.lock":
        return "cargo"
    if _REQUIREMENTS_RE.match(basename):
        return "pip"
    return None


def _parse_npm_lock(text: str) -> dict[tuple[str, str], bool | None]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[tuple[str, str], bool | None] = {}
    packages = data.get("packages")
    if isinstance(packages, dict):
        for node_path, info in packages.items():
            if not node_path or not isinstance(info, dict):
                continue  # "" is the root package (the project itself), not a dependency
            name = info.get("name")
            if not isinstance(name, str) or not name:
                name = node_path.rsplit("node_modules/", 1)[-1]
            version = info.get("version")
            if not isinstance(version, str) or not version:
                continue
            out[(name, version)] = bool(info.get("hasInstallScript"))
        return out
    dependencies = data.get("dependencies")
    if isinstance(dependencies, dict):
        _walk_npm_v1(dependencies, out)
    return out


def _walk_npm_v1(tree: dict, out: dict[tuple[str, str], bool | None]) -> None:
    for name, info in tree.items():
        if not isinstance(info, dict):
            continue
        version = info.get("version")
        if isinstance(version, str) and version:
            out.setdefault((name, version), None)  # v1 lockfiles carry no install-script flag
        nested = info.get("dependencies")
        if isinstance(nested, dict):
            _walk_npm_v1(nested, out)


def _parse_requirements(text: str) -> dict[tuple[str, str], bool | None]:
    out: dict[tuple[str, str], bool | None] = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        m = _PIP_PIN_RE.match(line)
        if m:
            out[(m.group(1), m.group(2))] = None  # requirements.txt has no install-script concept
    return out


def _parse_cargo_lock(text: str) -> dict[tuple[str, str], bool | None]:
    out: dict[tuple[str, str], bool | None] = {}
    for block in text.split("[[package]]"):
        name_m = re.search(r'^\s*name\s*=\s*"([^"]+)"', block, re.MULTILINE)
        version_m = re.search(r'^\s*version\s*=\s*"([^"]+)"', block, re.MULTILINE)
        if name_m and version_m:
            out[(name_m.group(1), version_m.group(1))] = None  # Cargo.lock has no install-script concept
    return out


def parse_manifest(ecosystem: str, text: str) -> dict[tuple[str, str], bool | None]:
    if ecosystem == "npm":
        return _parse_npm_lock(text)
    if ecosystem == "pip":
        return _parse_requirements(text)
    if ecosystem == "cargo":
        return _parse_cargo_lock(text)
    return {}


# ── diff-scoped dependency-change detection ──────────────────────────────────
def changed_dependencies(wt: pathlib.Path, base_commit: str) -> list[DependencyChange]:
    """Packages newly introduced or version-bumped by the diff (committed changes
    plus untracked new manifest files)."""
    changes: list[DependencyChange] = []
    seen_paths: set[str] = set()

    proc = git(["diff", "--name-status", base_commit], cwd=wt, check=False)
    if proc.returncode == 0:
        for line in proc.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 2 or parts[0].startswith("D"):
                continue
            rel = parts[-1]
            ecosystem = manifest_ecosystem(pathlib.PurePosixPath(rel).name)
            if ecosystem is None or rel in seen_paths:
                continue
            seen_paths.add(rel)
            new_file = wt / rel
            if not new_file.is_file():
                continue
            try:
                new_text = new_file.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            old_proc = git(["show", f"{base_commit}:{rel}"], cwd=wt, check=False)
            old_text = old_proc.stdout if old_proc.returncode == 0 else ""
            old_map = parse_manifest(ecosystem, old_text)
            new_map = parse_manifest(ecosystem, new_text)
            old_names = {name for name, _version in old_map}
            for (name, version), has_script in new_map.items():
                if (name, version) in old_map:
                    continue
                changes.append(DependencyChange(rel, ecosystem, name, version,
                                                name not in old_names, has_script))

    for f, rel in untracked_files(wt):
        ecosystem = manifest_ecosystem(pathlib.PurePosixPath(rel).name)
        if ecosystem is None or rel in seen_paths:
            continue
        seen_paths.add(rel)
        try:
            new_text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for (name, version), has_script in parse_manifest(ecosystem, new_text).items():
            changes.append(DependencyChange(rel, ecosystem, name, version, True, has_script))

    return changes


# ── network signals (best-effort; any failure degrades to "unknown", never a crash) ─
def _http_json(url: str, *, method: str = "GET", body: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={"Accept": "application/json", "User-Agent": "rig-dependency-gate",
                 **({"Content-Type": "application/json"} if data is not None else {})},
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def release_published_at(ecosystem: str, name: str, version: str) -> dt.datetime | None:
    """When the registry says this exact version was published, or None if that
    cannot be determined (unknown ecosystem, not found, or any network/parse error)."""
    try:
        if ecosystem == "npm":
            data = _http_json(f"https://registry.npmjs.org/{urllib.parse.quote(name, safe='@/')}")
            raw = (data.get("time") or {}).get(version)
        elif ecosystem == "pip":
            data = _http_json(f"https://pypi.org/pypi/{urllib.parse.quote(name)}/json")
            entries = (data.get("releases") or {}).get(version) or []
            raw = entries[0].get("upload_time_iso_8601") if entries else None
        elif ecosystem == "cargo":
            data = _http_json(f"https://crates.io/api/v1/crates/{urllib.parse.quote(name)}")
            raw = next((v.get("created_at") for v in data.get("versions", [])
                       if v.get("num") == version), None)
        else:
            return None
        if not isinstance(raw, str) or not raw:
            return None
        return dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError):
        return None


def osv_advisories(ecosystem: str, name: str, version: str) -> list[dict]:
    """Open OSV.dev advisories matching this exact package/version, or [] if none
    (or the lookup could not be completed)."""
    osv_ecosystem = OSV_ECOSYSTEM.get(ecosystem)
    if not osv_ecosystem:
        return []
    try:
        data = _http_json(_osv_url(), method="POST",
                          body={"version": version, "package": {"name": name, "ecosystem": osv_ecosystem}})
        vulns = data.get("vulns")
        return vulns if isinstance(vulns, list) else []
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError):
        return []


# ── grading ───────────────────────────────────────────────────────────────────
def _finding(change: DependencyChange, *, grade: str, kind: str, excerpt: str) -> dict:
    return {"path": change.path, "package": change.name, "version": change.version,
            "ecosystem": change.ecosystem, "grade": grade, "kind": kind, "excerpt": excerpt}


def evaluate_change(change: DependencyChange, *, now: dt.datetime, offline: bool) -> list[dict]:
    findings: list[dict] = []
    if change.has_install_script:
        findings.append(_finding(
            change, grade="warning", kind="install_script",
            excerpt=f"{change.name}@{change.version} runs an npm lifecycle install script "
                    "(hasInstallScript) — review it before accepting"))
    if offline:
        return findings

    published = release_published_at(change.ecosystem, change.name, change.version)
    if published is not None:
        age = now - published
        cooldown = _cooldown_hours()
        if age < dt.timedelta(hours=cooldown):
            findings.append(_finding(
                change, grade="warning", kind="fresh_release",
                excerpt=f"{change.name}@{change.version} was published {published.isoformat()} "
                        f"— within the {cooldown:g}h review window"))

    for vuln in osv_advisories(change.ecosystem, change.name, change.version):
        vuln_id = str(vuln.get("id") or "")
        aliases = [str(a) for a in (vuln.get("aliases") or [])]
        malicious = vuln_id.startswith("MAL-") or any(a.startswith("MAL-") for a in aliases)
        summary = str(vuln.get("summary") or "").strip()
        excerpt = f"{change.name}@{change.version}: {vuln_id or '(unnamed advisory)'}"
        if summary:
            excerpt += f" — {summary[:80]}"
        findings.append(_finding(
            change, grade="fail" if malicious else "warning",
            kind="known_malicious_package" if malicious else "known_vulnerability",
            excerpt=excerpt))
    return findings


def scan_task_dependencies(wt: pathlib.Path, base_commit: str) -> list[dict]:
    now = dt.datetime.now(dt.timezone.utc)
    offline = _offline()
    findings: list[dict] = []
    for change in changed_dependencies(wt, base_commit):
        findings.extend(evaluate_change(change, now=now, offline=offline))
    return findings


def scan_manifest_paths(paths: list[pathlib.Path]) -> list[dict]:
    """Scan manifest/lockfiles directly: every dependency found is treated as
    newly introduced, since there is no prior state to diff against."""
    files: list[pathlib.Path] = []
    for p in paths:
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files.extend(f for f in sorted(p.rglob("*")) if f.is_file())
        else:
            die(f"path '{p}' does not exist")
    now = dt.datetime.now(dt.timezone.utc)
    offline = _offline()
    findings: list[dict] = []
    for f in files:
        ecosystem = manifest_ecosystem(f.name)
        if ecosystem is None:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for (name, version), has_script in parse_manifest(ecosystem, text).items():
            change = DependencyChange(str(f), ecosystem, name, version, True, has_script)
            findings.extend(evaluate_change(change, now=now, offline=offline))
    return findings


def format_findings(findings: list[dict]) -> list[str]:
    return [f"{f['path']} [{f['kind']}/{f['grade']}] {f['excerpt']}" for f in findings]


# ── the sensor (called from cmd_gate) ─────────────────────────────────────────
_SENSOR_DETAIL_PREFIX = "(dependency sensor)"


def apply_dependency_sensor(root: pathlib.Path, run_d: pathlib.Path, task: dict, acc: dict,
                            explicit_set: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """Machine-back the opt-in `no_unvetted_dependency_update` with a diff-scoped
    dependency-manifest scan.

    Mutates `acc` in place (caller persists it) and returns printable notes. No
    `no_unvetted_dependency_update` in the gate (the default — this criterion
    ships in no preset), or no worktree/base → no-op.

    A known-malicious-package match → the check is set to **failed**.
    Install-script / fresh-release / known-vulnerability findings →
    **warning** (never overrides an explicit failed). Escape hatch: an explicit
    `--set no_unvetted_dependency_update=passed` in the current invocation is
    respected, recorded as dependency_override=True, sticky afterwards.
    """
    check = next((c for c in acc.get("checks", []) if c["name"] == SENSOR_CRITERION), None)
    if check is None:
        return []
    wt_path = task.get("worktree_path")
    base, _drift = effective_base(root, task)
    if not wt_path or not base:
        return []
    wt = pathlib.Path(wt_path)
    if not wt.is_dir():
        return []

    findings = scan_task_dependencies(wt, base)
    if not findings:
        if check.pop("dependency_findings", None) is not None:
            check.pop("dependency_override", None)
            if check["status"] in ("failed", "warning") and \
                    str(check.get("detail", "")).startswith(_SENSOR_DETAIL_PREFIX):
                check["status"] = "pending"
                check["detail"] = ""
                return [f"{_SENSOR_DETAIL_PREFIX} previously flagged dependency changes are no "
                        f"longer present → {SENSOR_CRITERION} reset to pending"]
        return []

    lines = format_findings(findings)
    check["dependency_findings"] = lines
    n = len(lines)
    n_fail = sum(1 for f in findings if f["grade"] == "fail")
    notes: list[str] = []
    if SENSOR_CRITERION in explicit_set and check["status"] == "passed":
        check["dependency_override"] = True
        if str(check.get("detail", "")).startswith(_SENSOR_DETAIL_PREFIX):
            check["detail"] = (f"{_SENSOR_DETAIL_PREFIX} {n} finding(s) manually overridden "
                               "after review (dependency_override)")
        notes.append(f"{_SENSOR_DETAIL_PREFIX} {n} dependency finding(s) still present, but "
                     f"{SENSOR_CRITERION} was explicitly set to passed — manual override recorded:")
    elif check.get("dependency_override") and check["status"] == "passed":
        notes.append(f"{_SENSOR_DETAIL_PREFIX} {n} dependency finding(s) present — "
                     "manual override previously recorded, keeping passed:")
    elif n_fail:
        check["status"] = "failed"
        check["detail"] = (f"{_SENSOR_DETAIL_PREFIX} {n_fail} known-malicious-package match(es) — "
                           f"remove/replace the dependency, or after review override with "
                           f"--set {SENSOR_CRITERION}=passed")
        notes.append(f"{_SENSOR_DETAIL_PREFIX} {n} dependency finding(s) detected "
                     f"({n_fail} fail-grade) → {SENSOR_CRITERION} failed:")
    else:
        if check["status"] in ("pending", "passed", "warning"):
            check["status"] = "warning"
            if not check.get("detail") or str(check["detail"]).startswith(_SENSOR_DETAIL_PREFIX):
                check["detail"] = (f"{_SENSOR_DETAIL_PREFIX} {n} dependency-update signal(s) — "
                                   f"review before accepting (override with "
                                   f"--set {SENSOR_CRITERION}=passed)")
        notes.append(f"{_SENSOR_DETAIL_PREFIX} {n} dependency-update signal(s) detected → "
                     f"{SENSOR_CRITERION} recorded as warning:")
    notes.extend(f"  {ln}" for ln in lines)
    return notes


# ── CLI ───────────────────────────────────────────────────────────────────────
def cmd_scan_dependencies(args: argparse.Namespace) -> None:
    if args.diff and args.paths:
        die("give either paths or --diff <task-id>, not both")
    if args.diff:
        root = repo_root()
        _, task = load_task(root, args.diff)
        wt_path = task.get("worktree_path")
        base, _drift = effective_base(root, task)
        if not wt_path or not pathlib.Path(wt_path).is_dir():
            die(f"task '{args.diff}' has no worktree (created with --no-worktree, or already discarded)")
        if not base:
            die(f"task '{args.diff}' has no base_commit recorded")
        findings = scan_task_dependencies(pathlib.Path(wt_path), base)
        scope = f"dependency changes of task {args.diff} (worktree vs {base[:12]})"
    else:
        paths = [pathlib.Path(p) for p in (args.paths or ["."])]
        findings = scan_manifest_paths(paths)
        scope = ", ".join(str(p) for p in paths)

    print(f"## scan-dependencies: {scope}")
    if not findings:
        print("No dependency-update signals found.")
        return
    n_fail = sum(1 for f in findings if f["grade"] == "fail")
    print(f"{len(findings)} dependency finding(s) found "
          f"({n_fail} fail-grade, {len(findings) - n_fail} warning-grade):")
    for line in format_findings(findings):
        print(f"  {line}")
    if _offline():
        print("(RIG_DEP_GATE_OFFLINE=1 — fresh-release and known-vulnerability checks were skipped)")
    sys.exit(1)
