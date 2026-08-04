"""Lightweight package-native CLI for read-only capability routing."""

from __future__ import annotations

import argparse
import json
import sys

from rig_workbench.packs.model import PackError

from .capabilities import resolve_task_route


def add_context_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--recipe", "--explicit-recipe", dest="recipe",
                        help="explicit recipe name (must resolve safely)")
    parser.add_argument("--remote-pr", action="store_true", help="route a remote PR review")
    parser.add_argument("--has-diff", action="store_true",
                        help="a local or caller-supplied diff is available")
    parser.add_argument("--diff", help="path or caller-supplied diff text")
    parser.add_argument("--read-only", action="store_true",
                        help="route without an implementation worktree")
    parser.add_argument("--implementation-type", choices=("feature", "bugfix"),
                        help="for test work: classify the implementation route")


def route_context(args: argparse.Namespace) -> dict:
    return {
        "recipe": getattr(args, "recipe", None),
        "remote_pr": getattr(args, "remote_pr", False),
        "has_diff": getattr(args, "has_diff", False),
        "diff": getattr(args, "diff", None),
        "read_only": getattr(args, "read_only", False),
        "implementation_type": getattr(args, "implementation_type", None),
    }


def cmd_route(args: argparse.Namespace) -> None:
    from .state import repo_root

    try:
        route = resolve_task_route(args.type, route_context(args), repo_root())
    except PackError as exc:
        if args.json:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        else:
            print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    if args.json:
        print(json.dumps(route, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{route['status']}: {route['recipe'] or '-'} — {route['reason']}")
        if route["hint"]:
            print(f"hint: {route['hint']}")
    if route["status"] in {"stopped", "trust_required"}:
        raise SystemExit(2)


def parser(*, prog: str = "rig-wb wb route") -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog=prog, description="resolve a task capability")
    result.add_argument("--type", required=True, help="task type to route")
    add_context_arguments(result)
    result.add_argument("--json", action="store_true", help="emit the exact route record")
    return result


def main(argv: list[str] | None = None) -> None:
    cmd_route(parser().parse_args(argv))
