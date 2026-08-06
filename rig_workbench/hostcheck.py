"""rig-wb hostcheck — report the host-side prerequisites rig cannot enforce itself.

Two of rig's safety properties do not live inside rig and never will:

* **Process isolation.** rig's isolated worktree separates *file work* — a failed
  attempt never lands in your tree. It is not a boundary against code execution.
  A container (DevContainer) or VM is what bounds that, and rig runs *inside* it.
* **Runtime command blocking.** rig's `no_destructive_operation` sensor reads the
  commands a diff *writes*. Intercepting the commands a session *runs* is the
  host permission layer's job (`permissions.deny` / `PreToolUse`).

Documenting that split is not the same as noticing when it is missing. This
command performs the noticing: it inspects the environment deterministically —
no LLM, no network, no writes — and reports which prerequisites are in place, so
"we meant to containerise it" cannot quietly persist as "we never did".

Exit codes: 0 = every prerequisite present, 3 = at least one missing (advisory),
and with `--strict`, a missing prerequisite exits 1 so CI can block on it.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import tempfile

CONTAINER_ENV_VARS = (
    "REMOTE_CONTAINERS",
    "CODESPACES",
    "DEVCONTAINER",
    "container",
)
CGROUP_MARKERS = ("docker", "containerd", "kubepods", "lxc", "podman")
SETTINGS_CANDIDATES = (
    ".claude/settings.json",
    ".claude/settings.local.json",
)


def _read_json(path: pathlib.Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def host_signals() -> list[str]:
    """Signals that this process is running inside a container, read from the host."""
    signals: list[str] = []
    if pathlib.Path("/.dockerenv").exists():
        signals.append("/.dockerenv")
    if pathlib.Path("/run/.containerenv").exists():
        signals.append("/run/.containerenv")
    try:
        text = pathlib.Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    for marker in CGROUP_MARKERS:
        if marker in text:
            signals.append(f"cgroup:{marker}")
            break
    return signals


def check_isolation(
    root: pathlib.Path, *, env: dict | None = None, signals: list[str] | None = None,
) -> dict:
    """Is this session bounded by something stronger than the file system?

    `env` and `signals` are injectable so the check can be measured against a fixed
    corpus (`--bench`) instead of only against whatever host happens to run the tests.
    """
    environ = os.environ if env is None else env
    signals = list(host_signals() if signals is None else signals)
    for var in CONTAINER_ENV_VARS:
        if environ.get(var):
            signals.append(f"env:{var}")
    declared = [
        str(candidate.relative_to(root))
        for candidate in (
            root / ".devcontainer" / "devcontainer.json",
            root / ".devcontainer.json",
        )
        if candidate.exists()
    ]
    return {
        "id": "process_isolation",
        "ok": bool(signals),
        "signals": signals,
        "declared_config": declared,
        "requirement": "Run rig inside a container/VM. The isolated worktree separates file work, not execution.",
        "remedy": "Add a .devcontainer/devcontainer.json and start the session inside it.",
    }


def check_deny_rules(root: pathlib.Path) -> dict:
    """Does the host permission layer deny anything at all?"""
    found: list[dict] = []
    for rel in SETTINGS_CANDIDATES:
        path = root / rel
        if not path.exists():
            continue
        permissions = _read_json(path).get("permissions")
        deny = permissions.get("deny") if isinstance(permissions, dict) else None
        if isinstance(deny, list) and deny:
            found.append({"path": rel, "rules": len(deny)})
    return {
        "id": "deny_rules",
        "ok": bool(found),
        "sources": found,
        "requirement": "Deletion, production writes and secret output belong in permissions.deny, not in prose.",
        "remedy": 'Add a permissions.deny list to .claude/settings.json (rig\'s diff sensors are the second net, not the first).',
    }


def check_state_ignored(root: pathlib.Path) -> dict:
    """Is rig's run state kept out of version control?"""
    patterns: list[str] = []
    gitignore = root / ".gitignore"
    if gitignore.exists():
        for raw in gitignore.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if line.rstrip("/") in {".rig", "/.rig"} or line.startswith(".rig/"):
                patterns.append(line)
    return {
        "id": "state_ignored",
        "ok": bool(patterns),
        "patterns": patterns,
        "requirement": "Run state under .rig/ is local execution history, not repository content.",
        "remedy": "Add `.rig/` to .gitignore (`/rig:init` proposes this).",
    }


CHECKS = (check_isolation, check_deny_rules, check_state_ignored)


def run_all(root: pathlib.Path) -> dict:
    results = [check(root) for check in CHECKS]
    missing = [r["id"] for r in results if not r["ok"]]
    return {"root": str(root), "checks": results, "missing": missing, "ok": not missing}


def _print_report(result: dict) -> None:
    print("## rig-wb hostcheck — prerequisites rig cannot enforce itself\n")
    for check in result["checks"]:
        mark = "OK  " if check["ok"] else "MISS"
        print(f"[{mark}] {check['id']}")
        print(f"       {check['requirement']}")
        detail = (
            check.get("signals")
            or check.get("sources")
            or check.get("patterns")
        )
        if check["ok"] and detail:
            print(f"       found: {detail}")
        if not check["ok"]:
            print(f"       remedy: {check['remedy']}")
        print()
    if result["ok"]:
        print("All host-side prerequisites present.")
    else:
        print(f"Missing: {', '.join(result['missing'])}")
        print("rig still runs — these are the operator's side of the split, and rig only reports them.")


# ── fixed corpus ────────────────────────────────────────────────────────
# `--bench` measures the checks the way `sensor-bench` measures the scanners:
# against a fixed set of cases, with no LLM, no billing and no dependence on
# whatever host happens to run it. Positive cases are prerequisites that ARE in
# place and must be reported present; negative cases are absent or *look* present
# without being it — those are where a check earns its keep. A container config
# committed to the repo is the sharpest of them: it says the team intended
# isolation, which is not the same as this session having it.

BenchCase = tuple[str, dict, bool]

ISOLATION_CORPUS: tuple[BenchCase, ...] = (
    ("remote_containers_env", {"env": {"REMOTE_CONTAINERS": "true"}}, True),
    ("devcontainer_env", {"env": {"DEVCONTAINER": "true"}}, True),
    ("docker_marker_file", {"signals": ["/.dockerenv"]}, True),
    ("podman_marker_file", {"signals": ["/run/.containerenv"]}, True),
    ("cgroup_marker", {"signals": ["cgroup:kubepods"]}, True),
    ("declared_but_not_running",
     {"files": {".devcontainer/devcontainer.json": "{}"}}, False),
    ("empty_env_var", {"env": {"CODESPACES": ""}}, False),
    ("bare_host", {}, False),
)

DENY_CORPUS: tuple[BenchCase, ...] = (
    ("deny_rules_present",
     {"files": {".claude/settings.json":
                '{"permissions": {"deny": ["Bash(rm -rf:*)", "Bash(git push --force:*)"]}}'}}, True),
    ("deny_in_local_settings",
     {"files": {".claude/settings.local.json": '{"permissions": {"deny": ["Read(./.env)"]}}'}}, True),
    ("deny_list_empty", {"files": {".claude/settings.json": '{"permissions": {"deny": []}}'}}, False),
    ("allow_only_looks_configured",
     {"files": {".claude/settings.json": '{"permissions": {"allow": ["Bash(npm test:*)"]}}'}}, False),
    ("deny_is_not_a_list",
     {"files": {".claude/settings.json": '{"permissions": {"deny": "Bash(rm -rf:*)"}}'}}, False),
    ("settings_malformed", {"files": {".claude/settings.json": "{not json"}}, False),
    ("no_settings_file", {}, False),
)

IGNORE_CORPUS: tuple[BenchCase, ...] = (
    ("trailing_slash", {"files": {".gitignore": "node_modules/\n.rig/\n"}}, True),
    ("rooted", {"files": {".gitignore": "/.rig\n"}}, True),
    ("bare_name", {"files": {".gitignore": ".rig\n"}}, True),
    ("subdirectory", {"files": {".gitignore": ".rig/runs/\n"}}, True),
    ("similar_prefix", {"files": {".gitignore": ".rigging/\n"}}, False),
    ("different_name", {"files": {".gitignore": "rig/\n"}}, False),
    ("commented_out", {"files": {".gitignore": "# .rig/\n"}}, False),
    ("no_gitignore", {}, False),
)

BENCH_CORPORA = {
    "process_isolation": (check_isolation, ISOLATION_CORPUS),
    "deny_rules": (check_deny_rules, DENY_CORPUS),
    "state_ignored": (check_state_ignored, IGNORE_CORPUS),
}


def _materialise(root: pathlib.Path, files: dict) -> None:
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def run_case(check, case: BenchCase, workdir: pathlib.Path) -> dict:
    label, spec, expect_ok = case
    _materialise(workdir, spec.get("files", {}))
    kwargs = {}
    if check is check_isolation:
        # Isolation reads the host; the corpus supplies both inputs explicitly so a
        # case means the same thing inside a container and on a laptop.
        kwargs = {"env": spec.get("env", {}), "signals": spec.get("signals", [])}
    result = check(workdir, **kwargs)
    return {"label": label, "expect_ok": expect_ok, "ok": result["ok"],
            "correct": result["ok"] == expect_ok}


def run_bench() -> dict:
    checks: dict[str, dict] = {}
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        for name, (check, corpus) in BENCH_CORPORA.items():
            cases = []
            for index, case in enumerate(corpus):
                workdir = base / f"{name}-{index}"
                workdir.mkdir(parents=True, exist_ok=True)
                cases.append(run_case(check, case, workdir))
            positives = [c for c in cases if c["expect_ok"]]
            negatives = [c for c in cases if not c["expect_ok"]]
            detected = sum(1 for c in positives if c["correct"])
            false_positives = sum(1 for c in negatives if not c["correct"])
            checks[name] = {
                "cases": cases,
                "positives": len(positives), "detected": detected,
                "recall": round(detected / len(positives), 3) if positives else None,
                "negatives": len(negatives), "false_positives": false_positives,
                "false_positive_rate": (round(false_positives / len(negatives), 3)
                                        if negatives else None),
            }
    total_pos = sum(c["positives"] for c in checks.values())
    total_det = sum(c["detected"] for c in checks.values())
    total_neg = sum(c["negatives"] for c in checks.values())
    total_fp = sum(c["false_positives"] for c in checks.values())
    return {
        "checks": checks,
        "overall": {
            "positives": total_pos, "detected": total_det,
            "recall": round(total_det / total_pos, 3) if total_pos else None,
            "negatives": total_neg, "false_positives": total_fp,
            "false_positive_rate": round(total_fp / total_neg, 3) if total_neg else None,
        },
        "ok": total_det == total_pos and total_fp == 0,
    }


def _print_bench(result: dict) -> None:
    print("## rig-wb hostcheck --bench — detection rate on a fixed corpus\n")
    print("No LLM, no billing, no dependence on the host running it: every case supplies\n"
          "its own environment. Negative cases include configurations that look like the\n"
          "prerequisite without being it — a committed devcontainer.json with no container\n"
          "around the session, an allow-list with no deny rules, a commented-out ignore.\n")
    for name, data in result["checks"].items():
        recall = f"{data['detected']}/{data['positives']}"
        print(f"### {name}")
        print(f"  detected: {recall}" + (f" ({data['recall'] * 100:.0f}%)" if data["recall"] is not None else ""))
        print(f"  false positives: {data['false_positives']}/{data['negatives']}")
        for case in data["cases"]:
            mark = "OK" if case["correct"] else "MISS"
            print(f"    [{mark}] {case['label']:<28} expect_ok={case['expect_ok']!s:<5} ok={case['ok']}")
        print()
    o = result["overall"]
    print(f"## overall: detected {o['detected']}/{o['positives']}, "
          f"false positives {o['false_positives']}/{o['negatives']}")


def cmd_hostcheck(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="rig-wb hostcheck",
        description="Report the host-side prerequisites rig cannot enforce (isolation, deny rules, ignored state).",
    )
    parser.add_argument("--repo", default=".", help="repository root to inspect (default: cwd)")
    parser.add_argument("--json", action="store_true", help="emit the full result as JSON")
    parser.add_argument("--strict", action="store_true", help="exit 1 instead of 3 when a prerequisite is missing")
    parser.add_argument("--bench", action="store_true",
                        help="measure the checks against a fixed corpus instead of inspecting this repo")
    args = parser.parse_args(argv)

    if args.bench:
        bench = run_bench()
        if args.json:
            print(json.dumps(bench, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            _print_bench(bench)
        return 0 if bench["ok"] else 1

    root = pathlib.Path(args.repo).resolve()
    result = run_all(root)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_report(result)
    if result["ok"]:
        return 0
    return 1 if args.strict else 3
