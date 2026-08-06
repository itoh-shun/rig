"""workbench mutation: diff-scoped mutation testing behind
`changed_code_mutants_are_killed`.

Why this criterion exists
-------------------------
`tests_added_or_explained` (feature preset) and
`regression_test_added_or_explained` (bugfix preset) are **model-judged**: a
sentence of prose satisfies them. This criterion asks the same question in a
form a machine settles:

    tests_added_or_explained          judged by the model  (weak, contextual)
      ↓
    changed_code_mutants_are_killed   judged by a machine  (strong, forced)

It joins `no_secret_leak` / `no_injection_markers` /
`no_destructive_operation` as a criterion a deterministic sensor stands
behind, instead of one the reviewing model can talk itself past.

Four design commitments
-----------------------
1. **Diff-scoped, never whole-repo.** Mutating the entire tree is far too slow
   for a per-task gate. Only lines the task actually changed are mutated, so
   the question sharpens to *"do the tests written just now protect the code
   written just now?"* — which is the question the gate is for. The changed-line
   set comes from the same `effective_base` / `worktree_diff_text` pair every
   other diff sensor uses, so the scope cannot drift from theirs.

2. **The tool is resolved from the manifest.** `.claude/rig.md` already carries
   `build:` / `lint:` / `test:`, so this adds `mutate:`. Python=mutmut,
   Java=PIT, JS=Stryker — rig owns the discipline, not the tool (native-first,
   SKILL.md §8). `mutate: builtin` selects the small stdlib engine below for
   Python, so a repo with no mutation tool installed can still run this.

3. **Runnable by hand first.** `rig-wb wb mutate <task-id>` is the whole
   measurement; the gate only *reads what it recorded*. A check that can only
   be seen in CI is a check nobody debugs, and the gate must stay fast — this
   split keeps `gate` a file read, exactly like the drill scoreboard.

4. **Survivors are named.** A count alone ("3 survived") is not actionable.
   The report names file:line and the exact mutation that lived, because the
   only useful output is "this specific edit to your new code broke no test".

Opt-in on purpose
-----------------
No `mutate:` in the manifest → the criterion is never added and the sensor is
a no-op. Growing a gate criterion nobody configured would fail every task in
every repo that never asked for it; and per `.rig/gates.json`'s additive-only
rule, a criterion that appears must be one the project opted into.

Baseline first
--------------
The builtin engine runs the test command **unmutated** before mutating
anything. If the suite is already red, every mutant would score as "killed"
and the measurement would be pure noise — so it refuses instead of reporting a
perfect score. This is the positive control the measurement is worthless
without.

CLI
---
  rig-wb wb mutate [<task-id>] [--max-mutants N] [--timeout S] [--json]
  rig-wb wb mutate --show          print the recorded report without re-running
"""

import argparse
import ast
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

from .secrets import iter_added_lines, untracked_files, worktree_diff_text
from .state import die, effective_base, load_task, now_iso, repo_root, resolve_task_id, run_dir

CRITERION = "changed_code_mutants_are_killed"
REPORT_NAME = "mutation.json"

DEFAULT_MAX_MUTANTS = 20
DEFAULT_TIMEOUT = 300

# Mutating a test file is worse than useless: the mutation breaks the test, the
# test fails, and the mutant scores as "killed" — a measurement that flatters
# itself. Only production code is a target.
_TEST_PATH_RE = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|_test\.py$|(^|/)conftest\.py$")

# ── manifest access (stdlib only) ────────────────────────────────────────────
# workbench.py is deliberately stdlib-only, so the manifest is read with the
# same narrow top-level `key: value` reader the shipped git hooks use rather
# than by importing PyYAML. Only flat scalars are needed here.
_MANIFEST_REL = ".claude/rig.md"


def read_manifest_scalar(root: pathlib.Path, key: str) -> str:
    """First top-level `key: value` line of `.claude/rig.md` ('' when absent).

    Quoted values are unquoted; an unquoted value is cut at a trailing
    `# comment`. Mirrors read_manifest_cmd() in hooks/git/pre-push — same file,
    same subset, so the hook and the gate cannot read it differently.
    """
    path = root / _MANIFEST_REL
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        if not line.startswith(f"{key}:"):
            continue
        val = line[len(key) + 1:].strip()
        if val[:1] == '"':
            return val[1:].split('"', 1)[0]
        if val[:1] == "'":
            return val[1:].split("'", 1)[0]
        return val.split("#", 1)[0].strip()
    return ""


def manifest_trusted(root: pathlib.Path) -> bool:
    """Whether `.claude/rig.md` has recorded consent in the rig trust store.

    The manifest is repo-controlled and its `mutate:` / `test:` values are
    executed here, which is the same Rules-File-Backdoor hazard the pre-push
    hook and the recipe loader gate. Reuses their single trust store rather
    than inventing a second consent path. No manifest at all → nothing to
    trust, nothing to run: returns True and the caller finds no command.
    """
    path = root / _MANIFEST_REL
    if not path.is_file():
        return True
    try:
        from rig_workbench.orchestrate.recipes import ensure_manifest_trusted
    except Exception:
        return False
    try:
        return bool(ensure_manifest_trusted(path))
    except SystemExit:
        raise
    except Exception:
        return False


def mutation_config(root: pathlib.Path) -> dict:
    """Resolve the mutation setup from the manifest.

    Keys read (all optional):
      mutate:              "builtin" | a shell command | "" (absent = opt-out)
      mutate_test:         test command used per mutant by the builtin engine
                           (falls back to `test:`) — a scoped, fast command
                           belongs here; a whole-suite `test:` makes the
                           measurement unusably slow.
      mutate_max_mutants:  cap on mutants per run (default 20)
    """
    declared = read_manifest_scalar(root, "mutate").strip()
    raw_cap = read_manifest_scalar(root, "mutate_max_mutants").strip()
    try:
        cap = int(raw_cap) if raw_cap else DEFAULT_MAX_MUTANTS
    except ValueError:
        cap = DEFAULT_MAX_MUTANTS
    return {
        "declared": bool(declared),
        "engine": "builtin" if declared.lower() == "builtin" else "command",
        "command": "" if declared.lower() == "builtin" else declared,
        "test": read_manifest_scalar(root, "mutate_test").strip()
                or read_manifest_scalar(root, "test").strip(),
        "max_mutants": cap if cap > 0 else DEFAULT_MAX_MUTANTS,
    }


# ── diff scope ───────────────────────────────────────────────────────────────
def changed_lines(wt: pathlib.Path, base_commit: str) -> dict[str, set[int]]:
    """{repo-relative path: set of added/changed line numbers} for the task.

    Committed and uncommitted changes (`git diff <base>`) plus untracked files,
    which git diff cannot see — a brand-new module is exactly the code most in
    need of this check. Test files are excluded (see _TEST_PATH_RE)."""
    out: dict[str, set[int]] = {}
    for rel, lineno, _text in iter_added_lines(worktree_diff_text(wt, base_commit)):
        if _TEST_PATH_RE.search(rel):
            continue
        out.setdefault(rel, set()).add(lineno)
    for f, rel in untracked_files(wt):
        if _TEST_PATH_RE.search(rel):
            continue
        try:
            n = len(f.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            continue
        out.setdefault(rel, set()).update(range(1, n + 1))
    return out


# A documentation-only diff has nothing to mutate. Listing what is *not* code
# (rather than enumerating every language's extensions) keeps rig out of the
# business of knowing languages — the mutation tool named in the manifest does.
_NON_SOURCE_SUFFIXES = frozenset((
    ".md", ".markdown", ".rst", ".txt", ".adoc", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".lock", ".csv", ".tsv", ".svg", ".png", ".jpg",
    ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".gitignore",
))


def source_targets(scope: dict[str, set[int]], engine: str) -> dict[str, set[int]]:
    """The mutable subset of the diff scope for the configured engine.

    builtin → changed Python lines only. command → every changed file that is
    not obviously documentation/config; which of those the tool can actually
    mutate is the tool's call, not rig's.
    """
    if engine == "builtin":
        return python_targets(scope)
    return {rel: lines for rel, lines in scope.items()
            if pathlib.PurePosixPath(rel).suffix.lower() not in _NON_SOURCE_SUFFIXES}


def python_targets(scope: dict[str, set[int]]) -> dict[str, set[int]]:
    return {rel: lines for rel, lines in scope.items() if rel.endswith(".py")}


def diff_fingerprint(wt: pathlib.Path, base_commit: str) -> str:
    """Identity of the diff a report was measured against.

    A recorded result is only evidence for the code it actually ran on. The
    fingerprint covers the full worktree diff plus every untracked file's
    content, so any edit after the measurement makes the report visibly stale
    instead of quietly authoritative."""
    h = hashlib.sha256()
    h.update(worktree_diff_text(wt, base_commit).encode("utf-8", errors="replace"))
    for f, rel in untracked_files(wt):
        h.update(rel.encode("utf-8"))
        try:
            h.update(f.read_bytes())
        except OSError:
            pass
    return h.hexdigest()


# ── builtin Python mutation engine ───────────────────────────────────────────
_CMP_SWAP = {
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
    ast.Lt: ast.GtE, ast.GtE: ast.Lt,
    ast.Gt: ast.LtE, ast.LtE: ast.Gt,
    ast.Is: ast.IsNot, ast.IsNot: ast.Is,
    ast.In: ast.NotIn, ast.NotIn: ast.In,
}
_BIN_SWAP = {
    ast.Add: ast.Sub, ast.Sub: ast.Add,
    ast.Mult: ast.Div, ast.Div: ast.Mult,
}
_OP_NAME = {
    ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">",
    ast.GtE: ">=", ast.Is: "is", ast.IsNot: "is not", ast.In: "in",
    ast.NotIn: "not in", ast.Add: "+", ast.Sub: "-", ast.Mult: "*",
    ast.Div: "/", ast.And: "and", ast.Or: "or",
}


def _key(node: ast.AST, kind: str, index: int) -> tuple:
    return (getattr(node, "lineno", 0), getattr(node, "col_offset", 0), kind, index)


def collect_mutants(source: str, lines: set[int]) -> list[dict]:
    """Enumerate the mutations available on `lines` of `source`.

    Deliberately a small operator set — comparison / boolean / arithmetic
    swaps, `not` removal, boolean-constant flip. These are the mutations that
    survive when a test asserts *that* code ran rather than *what* it decided,
    which is the failure mode this criterion exists to catch. Returns dicts
    sorted deterministically so the same diff always yields the same run.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    found: list[dict] = []

    def add(node: ast.AST, kind: str, index: int, desc: str) -> None:
        found.append({"key": _key(node, kind, index), "line": getattr(node, "lineno", 0),
                      "col": getattr(node, "col_offset", 0), "operator": desc})

    for node in ast.walk(tree):
        lineno = getattr(node, "lineno", None)
        if lineno is None or lineno not in lines:
            continue
        if isinstance(node, ast.Compare):
            for i, op in enumerate(node.ops):
                repl = _CMP_SWAP.get(type(op))
                if repl is not None:
                    add(node, "compare", i, f"`{_OP_NAME[type(op)]}` → `{_OP_NAME[repl]}`")
        elif isinstance(node, ast.BoolOp):
            repl = ast.Or if isinstance(node.op, ast.And) else ast.And
            add(node, "boolop", 0, f"`{_OP_NAME[type(node.op)]}` → `{_OP_NAME[repl]}`")
        elif isinstance(node, ast.BinOp):
            repl = _BIN_SWAP.get(type(node.op))
            if repl is not None:
                add(node, "binop", 0, f"`{_OP_NAME[type(node.op)]}` → `{_OP_NAME[repl]}`")
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            add(node, "not", 0, "`not X` → `X`")
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            add(node, "const", 0, f"`{node.value}` → `{not node.value}`")

    found.sort(key=lambda m: m["key"])
    return found


class _Applier(ast.NodeTransformer):
    """Apply exactly one mutation, identified by its collect_mutants key."""

    def __init__(self, key: tuple) -> None:
        self.key = key
        self.applied = False

    def visit_Compare(self, node: ast.Compare):  # noqa: N802
        self.generic_visit(node)
        for i, op in enumerate(node.ops):
            if _key(node, "compare", i) == self.key:
                repl = _CMP_SWAP.get(type(op))
                if repl is not None:
                    node.ops[i] = repl()
                    self.applied = True
        return node

    def visit_BoolOp(self, node: ast.BoolOp):  # noqa: N802
        self.generic_visit(node)
        if _key(node, "boolop", 0) == self.key:
            node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
            self.applied = True
        return node

    def visit_BinOp(self, node: ast.BinOp):  # noqa: N802
        self.generic_visit(node)
        if _key(node, "binop", 0) == self.key:
            repl = _BIN_SWAP.get(type(node.op))
            if repl is not None:
                node.op = repl()
                self.applied = True
        return node

    def visit_UnaryOp(self, node: ast.UnaryOp):  # noqa: N802
        self.generic_visit(node)
        if isinstance(node.op, ast.Not) and _key(node, "not", 0) == self.key:
            self.applied = True
            return node.operand
        return node

    def visit_Constant(self, node: ast.Constant):  # noqa: N802
        if isinstance(node.value, bool) and _key(node, "const", 0) == self.key:
            self.applied = True
            return ast.Constant(value=not node.value)
        return node


def apply_mutation(source: str, key: tuple) -> str | None:
    """Source with one mutation applied, or None if the key no longer matches."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    applier = _Applier(key)
    mutated = applier.visit(tree)
    if not applier.applied:
        return None
    ast.fix_missing_locations(mutated)
    try:
        return ast.unparse(mutated)
    except Exception:
        return None


def _run(cmd: str, cwd: pathlib.Path, timeout: int) -> tuple[int, bool]:
    """(exit code, timed out). The command comes from the trusted manifest."""
    try:
        proc = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True,
                              text=True, timeout=timeout,
                              env={**os.environ, "RIG_MUTATION": "1"})
        return proc.returncode, False
    except subprocess.TimeoutExpired:
        return -1, True
    except OSError:
        return -1, False


def run_builtin(wt: pathlib.Path, scope: dict[str, set[int]], test_cmd: str,
                max_mutants: int, timeout: int) -> dict:
    """Mutate changed Python lines one at a time and see whether the tests notice.

    Killed = the test command fails on the mutant (the tests saw the change).
    Survived = it still passes (nothing asserts on that decision).
    Timed out = counted as killed: a mutation that hangs the suite changed
    behaviour, which is what "killed" means here."""
    targets = source_targets(scope, "builtin")
    if not targets:
        return {"status": "no_targets", "reason": "no changed Python lines to mutate"}
    if not test_cmd:
        return {"status": "no_test_command",
                "reason": "neither `mutate_test:` nor `test:` is set in .claude/rig.md"}

    # Positive control. A red baseline makes every mutant look killed, so the
    # measurement is refused rather than reported as a perfect score.
    code, timed_out = _run(test_cmd, wt, timeout)
    if timed_out or code != 0:
        return {"status": "baseline_failed",
                "reason": (f"the test command is already failing without any mutation "
                           f"({'timed out' if timed_out else f'exit {code}'}) — "
                           "fix the suite before measuring, or every mutant scores as killed")}

    candidates: list[dict] = []
    unreadable: list[str] = []
    for rel in sorted(targets):
        path = wt / rel
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            unreadable.append(rel)
            continue
        for m in collect_mutants(source, targets[rel]):
            candidates.append({**m, "path": rel})

    total = len(candidates)
    selected = candidates[:max_mutants]
    killed = survived = timeouts = 0
    survivors: list[dict] = []

    for m in selected:
        path = wt / m["path"]
        original = path.read_text(encoding="utf-8")
        mutated = apply_mutation(original, m["key"])
        if mutated is None:
            continue
        try:
            path.write_text(mutated, encoding="utf-8")
            code, timed_out = _run(test_cmd, wt, timeout)
        finally:
            path.write_text(original, encoding="utf-8")
        if timed_out:
            timeouts += 1
            killed += 1
        elif code != 0:
            killed += 1
        else:
            survived += 1
            survivors.append({"path": m["path"], "line": m["line"], "col": m["col"],
                              "operator": m["operator"],
                              "source": original.splitlines()[m["line"] - 1].strip()[:100]
                              if 0 < m["line"] <= len(original.splitlines()) else ""})

    return {
        "status": "measured",
        "engine": "builtin",
        "command": test_cmd,
        "files": sorted(targets),
        "changed_lines": sum(len(v) for v in targets.values()),
        "total": total,
        "evaluated": len(selected),
        "not_evaluated": max(0, total - len(selected)),
        "killed": killed,
        "survived": survived,
        "timed_out": timeouts,
        "survivors": survivors,
        "unreadable": unreadable,
    }


def run_command(wt: pathlib.Path, scope: dict[str, set[int]], command: str,
                base_commit: str, report_path: pathlib.Path, timeout: int) -> dict:
    """Delegate to the manifest's mutation tool (mutmut / PIT / Stryker / …).

    Contract with the command:
      - `{files}` and `{base}` in the command string are substituted; the same
        values also arrive as RIG_MUTATION_FILES / RIG_MUTATION_BASE.
      - Exit 0 means every mutant in scope was killed.
      - If it writes JSON to $RIG_MUTATION_TOOL_REPORT with a `survivors` list
        of {path, line, operator}, rig names the survivors; without it rig only
        has the exit code and says so rather than inventing detail.
    """
    targets = source_targets(scope, "command")
    files = sorted(targets)
    if not files:
        return {"status": "no_targets", "reason": "no changed non-test source files to mutate"}
    tool_report = report_path.with_name("mutation-tool.json")
    if tool_report.exists():
        tool_report.unlink()
    joined = " ".join(files)
    cmd = command.replace("{files}", joined).replace("{base}", base_commit)
    env = {**os.environ, "RIG_MUTATION": "1", "RIG_MUTATION_FILES": joined,
           "RIG_MUTATION_BASE": base_commit,
           "RIG_MUTATION_TOOL_REPORT": str(tool_report)}
    try:
        proc = subprocess.run(cmd, cwd=wt, shell=True, capture_output=True,
                              text=True, timeout=timeout, env=env)
        code, timed_out = proc.returncode, False
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-5:]
    except subprocess.TimeoutExpired:
        code, timed_out, tail = -1, True, []
    except OSError as exc:
        return {"status": "tool_error", "reason": f"could not run `{cmd}`: {exc}"}

    result = {
        "status": "measured", "engine": "command", "command": cmd,
        "files": files, "changed_lines": sum(len(v) for v in targets.values()),
        "exit_code": code, "timed_out": timed_out, "survivors": [],
        "output_tail": tail,
    }
    if tool_report.is_file():
        try:
            data = json.loads(tool_report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        survivors = data.get("survivors")
        if isinstance(survivors, list):
            result["survivors"] = [s for s in survivors if isinstance(s, dict)]
        for key in ("total", "killed", "survived"):
            if isinstance(data.get(key), int):
                result[key] = data[key]
    else:
        result["report_missing"] = True
    result["survived"] = result.get("survived", len(result["survivors"]) or (0 if code == 0 else 1))
    if timed_out:
        result["status"] = "tool_error"
        result["reason"] = f"the mutation command timed out after {timeout}s"
    return result


# ── report I/O ───────────────────────────────────────────────────────────────
def report_path(run_d: pathlib.Path) -> pathlib.Path:
    return run_d / REPORT_NAME


def load_report(run_d: pathlib.Path) -> dict | None:
    p = report_path(run_d)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def format_survivor(s: dict) -> str:
    # The column is part of the address: one line can carry several distinct
    # mutations ("is not" twice in the same expression), and two survivors that
    # print identically are two survivors nobody can tell apart.
    where = f"{s.get('path', '?')}:{s.get('line', '?')}"
    if s.get("col") is not None:
        where += f":{s['col']}"
    op = s.get("operator") or "mutated"
    src = f"  {s['source']}" if s.get("source") else ""
    return f"{where}  {op}{src}"


def _survivor_count(report: dict) -> int:
    if isinstance(report.get("survived"), int):
        return report["survived"]
    return len(report.get("survivors") or [])


# ── the sensor (called from cmd_gate) ────────────────────────────────────────
_SENSOR_DETAIL_PREFIX = "(mutation sensor)"
_MAX_NAMED_SURVIVORS = 10


def ensure_mutation_criterion(root: pathlib.Path, task: dict, acc: dict) -> bool:
    """Add/remove `changed_code_mutants_are_killed` for this task.

    Required only when the project opted in (`mutate:` in the manifest) AND the
    task actually changed non-test source. A documentation-only diff has
    nothing to mutate, so carrying a criterion that can never be satisfied
    would just teach people to override it."""
    required = False
    cfg = mutation_config(root)
    if cfg["declared"]:
        wt_path = task.get("worktree_path")
        base, _drift = effective_base(root, task)
        if wt_path and base and pathlib.Path(wt_path).is_dir():
            scope = changed_lines(pathlib.Path(wt_path), base)
            required = bool(source_targets(scope, cfg["engine"]))
    checks = acc.setdefault("checks", [])
    present = next((c for c in checks if c.get("name") == CRITERION), None)
    if required and present is None:
        checks.append({"name": CRITERION, "status": "pending", "detail": ""})
    elif not required and present is not None:
        checks.remove(present)
    return required


def apply_mutation_sensor(root: pathlib.Path, run_d: pathlib.Path, task: dict, acc: dict,
                          explicit_set: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """Machine-back `changed_code_mutants_are_killed` from the recorded report.

    The sensor never runs mutants itself — `rig-wb wb mutate` does, and this
    reads what it left behind. That keeps `gate` a fast, deterministic file
    read (mutation runs take minutes) and makes the measurement an explicit act
    someone can watch, re-run, and argue with.

    pending  no report yet, or the report predates the current diff
    failed   mutants survived, or the run refused (red baseline / tool error)
    passed   every mutant in scope was killed

    Escape hatch mirrors the other sensors: an explicit
    `--set changed_code_mutants_are_killed=passed` is recorded as
    mutation_override and sticks across later evaluations.
    """
    if not ensure_mutation_criterion(root, task, acc):
        return []
    check = next(c for c in acc["checks"] if c["name"] == CRITERION)
    wt = pathlib.Path(task["worktree_path"])
    base, _drift = effective_base(root, task)
    report = load_report(run_d)

    if CRITERION in explicit_set and check["status"] == "passed":
        check["mutation_override"] = True
        # Replace our own stale detail, or the ✓ would sit next to the reason it
        # was ✗. Never touch a detail somebody else wrote.
        if str(check.get("detail", "")).startswith(_SENSOR_DETAIL_PREFIX):
            check["detail"] = (f"{_SENSOR_DETAIL_PREFIX} manually overridden after review "
                               "(mutation_override)")
        return [f"{_SENSOR_DETAIL_PREFIX} explicitly set to passed — manual override recorded"]
    if check.get("mutation_override") and check["status"] == "passed":
        return [f"{_SENSOR_DETAIL_PREFIX} manual override previously recorded, keeping passed"]

    task_id = task["task_id"]
    if report is None:
        check["status"] = "pending"
        check["detail"] = (f"{_SENSOR_DETAIL_PREFIX} not measured yet — "
                           f"run `rig-wb wb mutate {task_id}`")
        return [f"{_SENSOR_DETAIL_PREFIX} no report recorded → pending"]

    if report.get("diff_fingerprint") != diff_fingerprint(wt, base):
        check["status"] = "pending"
        check["detail"] = (f"{_SENSOR_DETAIL_PREFIX} the recorded report predates the current "
                           f"diff — re-run `rig-wb wb mutate {task_id}`")
        return [f"{_SENSOR_DETAIL_PREFIX} report is stale (diff changed since it was measured) "
                f"→ pending"]

    status = report.get("status")
    if status == "no_targets":
        check["status"] = "skipped"
        check["detail"] = f"{_SENSOR_DETAIL_PREFIX} {report.get('reason', 'nothing to mutate')}"
        return [f"{_SENSOR_DETAIL_PREFIX} nothing in scope → skipped"]
    if status != "measured":
        check["status"] = "failed"
        check["detail"] = f"{_SENSOR_DETAIL_PREFIX} {report.get('reason', status)}"
        return [f"{_SENSOR_DETAIL_PREFIX} measurement did not complete: "
                f"{report.get('reason', status)} → failed"]

    survived = _survivor_count(report)
    check.pop("mutation_survivors", None)  # never leave last run's names on a fresh verdict
    if survived == 0:
        check["status"] = "passed"
        killed = report.get("killed", report.get("evaluated", 0))
        check["detail"] = f"{_SENSOR_DETAIL_PREFIX} {killed} mutant(s) in the changed code, all killed"
        notes = [f"{_SENSOR_DETAIL_PREFIX} {killed} mutant(s) killed, 0 survived → {CRITERION} passed"]
    else:
        check["status"] = "failed"
        check["detail"] = (f"{_SENSOR_DETAIL_PREFIX} {survived} mutant(s) survived — the changed "
                           f"code has edits no test rejects (override after review with "
                           f"--set {CRITERION}=passed)")
        named = [format_survivor(s) for s in (report.get("survivors") or [])][:_MAX_NAMED_SURVIVORS]
        check["mutation_survivors"] = named
        notes = [f"{_SENSOR_DETAIL_PREFIX} {survived} mutant(s) survived → {CRITERION} failed:"]
        notes.extend(f"  {ln}" for ln in named)
        if not named:
            notes.append("  (the mutation tool reported no per-mutant detail — only its exit code)")

    if report.get("not_evaluated"):
        # Never let a cap read as full coverage.
        notes.append(f"{_SENSOR_DETAIL_PREFIX} {report['not_evaluated']} further mutant(s) were "
                     f"not evaluated (max_mutants={report.get('max_mutants')})")
    return notes


# ── CLI ──────────────────────────────────────────────────────────────────────
def _print_report(report: dict) -> None:
    head = report.get("status")
    if head == "measured":
        survived = _survivor_count(report)
        killed = report.get("killed", report.get("evaluated", 0) - survived)
        verdict = "ALL KILLED" if survived == 0 else f"{survived} SURVIVED"
        print(f"## mutation: {report['task_id']}  [{verdict}]")
        print(f"engine: {report.get('engine')} — {report.get('command', '')}")
        print(f"scope: {len(report.get('files') or [])} file(s), "
              f"{report.get('changed_lines', 0)} changed line(s) vs {report.get('base', '')[:12]}")
        if report.get("engine") == "builtin":
            print(f"mutants: {report.get('evaluated', 0)} evaluated / {killed} killed / "
                  f"{survived} survived" +
                  (f" / {report['timed_out']} timed out (counted as killed)"
                   if report.get("timed_out") else ""))
    else:
        print(f"## mutation: {report['task_id']}  [{str(head).upper()}]")
        print(report.get("reason", ""))

    for s in (report.get("survivors") or []):
        print(f"  {format_survivor(s)}")
    if report.get("not_evaluated"):
        print(f"  not evaluated: {report['not_evaluated']} mutant(s) beyond "
              f"max_mutants={report.get('max_mutants')} (raise with --max-mutants)")
    for rel in (report.get("unreadable") or []):
        print(f"  unreadable: {rel}")
    for ln in (report.get("output_tail") or []):
        print(f"  | {ln}")


def cmd_mutate(args: argparse.Namespace) -> None:
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    d = run_dir(root, task_id)
    _, task = load_task(root, task_id)

    if args.show:
        report = load_report(d)
        if report is None:
            die(f"task '{task_id}' has no mutation report — run `rig-wb wb mutate {task_id}` first")
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else "", end="")
        if not args.json:
            _print_report(report)
        return

    cfg = mutation_config(root)
    if not cfg["declared"]:
        die("no `mutate:` in .claude/rig.md — mutation testing is opt-in per project. "
            "Set `mutate: builtin` (stdlib Python engine) or a tool command "
            "(mutmut / PIT / Stryker); see manifests/_template.md")
    if not manifest_trusted(root):
        die("untrusted project manifest (.claude/rig.md): its `mutate:` / `test:` commands are "
            "executed here. Review the file, then consent with RIG_ALLOW_PROJECT_MANIFEST=1")

    wt_path = task.get("worktree_path")
    base, _drift = effective_base(root, task)
    if not wt_path or not pathlib.Path(wt_path).is_dir():
        die(f"task '{task_id}' has no worktree (created with --no-worktree, or already discarded)")
    if not base:
        die(f"task '{task_id}' has no base_commit recorded")
    wt = pathlib.Path(wt_path)

    scope = changed_lines(wt, base)
    max_mutants = args.max_mutants or cfg["max_mutants"]
    timeout = args.timeout or DEFAULT_TIMEOUT
    if cfg["engine"] == "builtin":
        result = run_builtin(wt, scope, cfg["test"], max_mutants, timeout)
    else:
        result = run_command(wt, scope, cfg["command"], base, report_path(d), timeout)

    report = {"task_id": task_id, "base": base, "generated_at": now_iso(),
              "diff_fingerprint": diff_fingerprint(wt, base),
              "max_mutants": max_mutants, **result}
    report_path(d).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report)
        print(f"\nrecorded: {report_path(d).relative_to(root)} "
              f"(read by the acceptance gate as {CRITERION})")
    if report["status"] != "measured" or _survivor_count(report) > 0:
        sys.exit(1)
