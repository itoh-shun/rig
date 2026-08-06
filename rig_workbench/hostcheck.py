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


def check_isolation(root: pathlib.Path) -> dict:
    """Is this session bounded by something stronger than the file system?"""
    signals: list[str] = []
    if pathlib.Path("/.dockerenv").exists():
        signals.append("/.dockerenv")
    if pathlib.Path("/run/.containerenv").exists():
        signals.append("/run/.containerenv")
    for var in CONTAINER_ENV_VARS:
        if os.environ.get(var):
            signals.append(f"env:{var}")
    cgroup = pathlib.Path("/proc/1/cgroup")
    try:
        text = cgroup.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    for marker in CGROUP_MARKERS:
        if marker in text:
            signals.append(f"cgroup:{marker}")
            break
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


def cmd_hostcheck(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="rig-wb hostcheck",
        description="Report the host-side prerequisites rig cannot enforce (isolation, deny rules, ignored state).",
    )
    parser.add_argument("--repo", default=".", help="repository root to inspect (default: cwd)")
    parser.add_argument("--json", action="store_true", help="emit the full result as JSON")
    parser.add_argument("--strict", action="store_true", help="exit 1 instead of 3 when a prerequisite is missing")
    args = parser.parse_args(argv)

    root = pathlib.Path(args.repo).resolve()
    result = run_all(root)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_report(result)
    if result["ok"]:
        return 0
    return 1 if args.strict else 3
