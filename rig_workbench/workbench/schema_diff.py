"""workbench schema_diff: machine verification backing `public_api_changes_documented`
(issue #288).

Locates OpenAPI schema files — explicit paths from `.rig/gates.json` key
"openapi_paths", or auto-detected common names (openapi.{json,yaml,yml},
swagger.json at repo root / api/ / docs/) — parses base-ref vs worktree
versions, and diffs the operations: added/removed path+method pairs, and
changed parameters / required flags / response codes via a plain-dict
recursive diff. No new dependencies: JSON via stdlib; YAML specs are parsed
only when PyYAML happens to be importable, otherwise the sensor degrades to
"schema file changed" without an operation-level breakdown.

Wiring: `cmd_gate` calls apply_schema_sensor() on every gate evaluation.
When the gate contains a `public_api_changes_documented*` criterion and the
schema changed between base and worktree, the finding is recorded on that
check in acceptance.json (key "api_diff"); if diff.md is missing/empty the
check is downgraded to warning (warning-grade sensor — it never fails the
gate on its own, and never overrides an explicit "failed").
Projects without a schema file are untouched (clean skip).
"""

import json
import pathlib

from .state import git, load_project_gates

# Criteria this sensor backs (feature preset / refactor preset variants).
SENSOR_CRITERIA = ("public_api_changes_documented", "public_api_changes_documented_if_any")

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")

# Auto-detection candidates when `.rig/gates.json` has no "openapi_paths".
AUTO_NAMES = ("openapi.json", "openapi.yaml", "openapi.yml", "swagger.json")
AUTO_DIRS = ("", "api", "docs")


# ── parsing ───────────────────────────────────────────────────────────────────
def parse_spec(text: str, filename: str) -> dict | None:
    """Parse an OpenAPI document. Returns None when unparseable (invalid syntax,
    or a YAML file while PyYAML is not installed — degrade gracefully)."""
    if filename.endswith((".yaml", ".yml")):
        try:
            import yaml  # optional; workbench itself stays stdlib-only
        except ImportError:
            return None
        try:
            data = yaml.safe_load(text)
        except Exception:
            return None
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


# ── plain-dict recursive diff ─────────────────────────────────────────────────
def _diff_paths(a, b, prefix: str = "") -> list[str]:
    """Recursively compare two plain values; return dotted key-paths that differ."""
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[str] = []
        for k in sorted(set(a) | set(b), key=str):
            child = f"{prefix}.{k}" if prefix else str(k)
            out.extend(_diff_paths(a.get(k), b.get(k), child))
        return out
    if a != b:
        return [prefix or "(root)"]
    return []


# ── operation diff ────────────────────────────────────────────────────────────
def _operations(spec: dict) -> dict[tuple[str, str], dict]:
    """Flatten spec["paths"] into {(path, method): operation}."""
    ops: dict[tuple[str, str], dict] = {}
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return ops
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.lower() in HTTP_METHODS and isinstance(op, dict):
                ops[(path, method.lower())] = op
    return ops


def _param_map(op: dict) -> dict[tuple, dict]:
    return {(p.get("name"), p.get("in")): p
            for p in op.get("parameters", []) if isinstance(p, dict)}


def _op_changes(a: dict, b: dict) -> list[str]:
    """Describe how one operation changed: params / required flags / response codes."""
    changes: list[str] = []
    pa, pb = _param_map(a), _param_map(b)
    for key in sorted(set(pb) - set(pa), key=str):
        changes.append(f"param added: {key[0]}")
    for key in sorted(set(pa) - set(pb), key=str):
        changes.append(f"param removed: {key[0]}")
    for key in sorted(set(pa) & set(pb), key=str):
        leaf = _diff_paths(pa[key], pb[key])
        if leaf:
            changes.append(f"param changed: {key[0]} ({', '.join(leaf)})")

    ra, rb = a.get("responses"), b.get("responses")
    if isinstance(ra, dict) and isinstance(rb, dict):
        for code in sorted(set(rb) - set(ra), key=str):
            changes.append(f"response added: {code}")
        for code in sorted(set(ra) - set(rb), key=str):
            changes.append(f"response removed: {code}")
        for code in sorted(set(ra) & set(rb), key=str):
            if _diff_paths(ra[code], rb[code]):
                changes.append(f"response changed: {code}")

    if _diff_paths(a.get("requestBody"), b.get("requestBody")):
        changes.append("requestBody changed")

    if not changes:
        skip = ("parameters", "responses", "requestBody")
        rest_a = {k: v for k, v in a.items() if k not in skip}
        rest_b = {k: v for k, v in b.items() if k not in skip}
        leaf = _diff_paths(rest_a, rest_b)
        if leaf:
            changes.append(f"operation changed: {', '.join(leaf)}")
    return changes


def diff_specs(base: dict, head: dict) -> dict:
    """Diff two parsed OpenAPI specs at operation granularity.

    Returns {"added": [op], "removed": [op], "changed": [{"op": op, "changes": [str]}]}
    where op is e.g. "GET /pets"."""
    a, b = _operations(base), _operations(head)
    added = sorted(f"{m.upper()} {p}" for (p, m) in set(b) - set(a))
    removed = sorted(f"{m.upper()} {p}" for (p, m) in set(a) - set(b))
    changed = []
    for path, method in sorted(set(a) & set(b)):
        ch = _op_changes(a[(path, method)], b[(path, method)])
        if ch:
            changed.append({"op": f"{method.upper()} {path}", "changes": ch})
    return {"added": added, "removed": removed, "changed": changed}


def summarize(diff: dict) -> list[str]:
    """Flatten a diff_specs() result into human-readable summary lines."""
    lines = [f"added: {op}" for op in diff["added"]]
    lines += [f"removed: {op}" for op in diff["removed"]]
    lines += [f"changed: {c['op']} — {'; '.join(c['changes'])}" for c in diff["changed"]]
    return lines


# ── schema file location ──────────────────────────────────────────────────────
def _existing_at_ref(wt: pathlib.Path, ref: str, rels: list[str]) -> set[str]:
    """Which of `rels` exist at `ref` — one batched `git ls-tree` call instead
    of one `cat-file -e` per candidate (#321: 12 probes → 1)."""
    proc = git(["ls-tree", "-r", "--name-only", ref, "--", *rels], cwd=wt, check=False)
    return set(proc.stdout.splitlines()) if proc.returncode == 0 else set()


def schema_paths(wt: pathlib.Path, base_ref: str, gates: dict) -> list[str]:
    """Repo-relative schema paths: explicit `openapi_paths` from `.rig/gates.json`,
    else auto-detected common names present in the worktree or at the base ref
    (a schema deleted by the task only exists at base)."""
    explicit = gates.get("openapi_paths")
    if explicit:
        return list(explicit)
    candidates = [f"{d}/{name}" if d else name for d in AUTO_DIRS for name in AUTO_NAMES]
    missing = [rel for rel in candidates if not (wt / rel).is_file()]
    at_base = _existing_at_ref(wt, base_ref, missing) if missing else set()
    return [rel for rel in candidates if (wt / rel).is_file() or rel in at_base]


# ── the sensor (called from cmd_gate) ─────────────────────────────────────────
def apply_schema_sensor(root: pathlib.Path, run_d: pathlib.Path, task: dict, acc: dict) -> list[str]:
    """Machine-verify `public_api_changes_documented` against real schema diffs.

    Mutates `acc` in place (caller persists it) and returns printable notes.
    No sensor criterion in the gate, no worktree, or no schema file → no-op.
    Schema changed + diff.md missing/empty → check becomes "warning" (never
    "failed": warning-grade). The machine finding is always recorded on the
    check under "api_diff" so status/accept surface it.
    """
    check = next((c for c in acc.get("checks", []) if c["name"] in SENSOR_CRITERIA), None)
    if check is None:
        return []
    wt_path = task.get("worktree_path")
    if not wt_path:
        return []
    wt = pathlib.Path(wt_path)
    base = task.get("base_commit")
    if not wt.is_dir() or not base:
        return []

    gates = load_project_gates(root)
    summary: list[str] = []
    for rel in schema_paths(wt, base, gates):
        head_file = wt / rel
        head_text = head_file.read_text(encoding="utf-8") if head_file.is_file() else None
        proc = git(["show", f"{base}:{rel}"], cwd=wt, check=False)
        base_text = proc.stdout if proc.returncode == 0 else None
        if base_text is None and head_text is None:
            continue
        if base_text == head_text:
            continue
        base_spec = parse_spec(base_text, rel) if base_text is not None else {}
        head_spec = parse_spec(head_text, rel) if head_text is not None else {}
        if base_spec is None or head_spec is None:
            summary.append(f"{rel}: schema file changed (not parsed: invalid syntax, "
                           "or YAML without PyYAML installed)")
            continue
        lines = summarize(diff_specs(base_spec, head_spec))
        if lines:
            summary.extend(f"{rel}: {line}" for line in lines)
        else:
            summary.append(f"{rel}: schema file changed (no operation-level change detected)")

    if not summary:
        return []  # clean skip: no schema, or schema unchanged

    check["api_diff"] = summary
    diff_md = run_d / "diff.md"
    documented = diff_md.exists() and diff_md.read_text(encoding="utf-8").strip() != ""
    notes = []
    if documented:
        notes.append(f"(schema sensor) {len(summary)} API schema change(s) detected; "
                     "diff.md exists — confirm they are documented there:")
    else:
        if check["status"] in ("pending", "passed"):
            check["status"] = "warning"
            if not check.get("detail"):
                check["detail"] = "machine-detected API schema changes are not documented (diff.md missing/empty)"
        notes.append(f"(schema sensor) {len(summary)} API schema change(s) detected but diff.md "
                     f"is missing/empty → {check['name']} recorded as warning:")
    notes.extend(f"  {line}" for line in summary)
    return notes
