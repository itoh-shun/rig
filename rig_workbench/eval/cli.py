"""Command-line interface for evaluation case capture and inspection."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .capture import capture_case
from .cases import EvalCaseError, canonical_json, validate_case


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rig-wb eval")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="validate one case or the promoted case directory")
    validate.add_argument("path", nargs="?")
    listing = sub.add_parser("list", help="list promoted cases and local drafts")
    listing.add_argument("--repo", default=".")
    capture = sub.add_parser("capture", help="capture a workbench task as an unapproved draft")
    capture.add_argument("task_id")
    capture.add_argument("--repo", default=".")
    return parser


def _case_paths(path: pathlib.Path) -> list[pathlib.Path]:
    try:
        if path.is_file():
            return [path]
        if not path.exists():
            raise EvalCaseError(f"case path does not exist: {path}")
        return sorted(path.glob("*/case.json"))
    except OSError as exc:
        raise EvalCaseError(f"filesystem error scanning cases: {exc}") from exc


def _repo_case_paths(root: pathlib.Path) -> list[pathlib.Path]:
    paths: list[pathlib.Path] = []
    try:
        for directory in (
            root / "evals" / "cases", root / ".rig" / "evals" / "drafts"
        ):
            if directory.is_dir():
                paths.extend(sorted(directory.glob("*/case.json")))
    except OSError as exc:
        raise EvalCaseError(f"filesystem error scanning cases: {exc}") from exc
    return paths


def _read_case(path: pathlib.Path, *, require_canonical: bool = True) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
        value = json.loads(raw, parse_constant=lambda token: (_ for _ in ()).throw(
            EvalCaseError(f"non-finite number is forbidden: {token}")))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvalCaseError(f"cannot read case {path}: {exc}") from exc
    validate_case(value)
    if require_canonical and raw != canonical_json(value):
        raise EvalCaseError(f"case is not canonical JSON: {path}")
    if path.name == "case.json" and path.parent.name != value["id"]:
        raise EvalCaseError(f"case id does not match directory name: {path}")
    tier = path.parent.parent.name if path.name == "case.json" else ""
    if tier == "cases" and value["status"] != "approved":
        raise EvalCaseError(f"promoted case must have status=approved: {path}")
    if tier == "drafts" and value["status"] != "draft":
        raise EvalCaseError(f"draft case must have status=draft: {path}")
    return value


def _load_unique_cases(paths: list[pathlib.Path]) -> list[tuple[pathlib.Path, dict]]:
    loaded: list[tuple[pathlib.Path, dict]] = []
    seen: dict[str, pathlib.Path] = {}
    normalized: list[pathlib.Path] = []
    try:
        normalized = list(dict.fromkeys(candidate.resolve() for candidate in paths))
    except OSError as exc:
        raise EvalCaseError(f"filesystem error resolving case path: {exc}") from exc
    for candidate in normalized:
        case = _read_case(candidate)
        if case["id"] in seen:
            raise EvalCaseError(
                f"duplicate case id '{case['id']}': {seen[case['id']]} and {candidate}"
            )
        seen[case["id"]] = candidate
        loaded.append((candidate, case))
    return loaded


def _tier_repo_root(path: pathlib.Path) -> pathlib.Path | None:
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise EvalCaseError(f"filesystem error resolving case path: {exc}") from exc
    if (resolved.name == "case.json" and resolved.parent.parent.name == "cases"
            and resolved.parent.parent.parent.name == "evals"):
        return resolved.parents[3]
    if (resolved.name == "case.json" and resolved.parent.parent.name == "drafts"
            and resolved.parent.parent.parent.name == "evals"
            and resolved.parent.parent.parent.parent.name == ".rig"):
        return resolved.parents[4]
    return None


def _validate_command(path_arg: str | None) -> int:
    if path_arg:
        paths = _case_paths(pathlib.Path(path_arg))
        roots = {_tier_repo_root(path) for path in paths}
        roots.discard(None)
        for root in roots:
            paths.extend(_repo_case_paths(root))
        paths = list(dict.fromkeys(paths))
    else:
        paths = _repo_case_paths(pathlib.Path.cwd())
    loaded = _load_unique_cases(paths)
    for candidate, _case in loaded:
        print(f"valid: {candidate}")
    print(f"{len(loaded)} case(s) valid")
    return 0


def _list_command(repo_arg: str) -> int:
    try:
        root = pathlib.Path(repo_arg).resolve()
    except OSError as exc:
        raise EvalCaseError(f"filesystem error resolving repository: {exc}") from exc
    candidates = _repo_case_paths(root)
    loaded = _load_unique_cases(candidates)
    for path, case in loaded:
        try:
            shown = path.relative_to(root)
        except ValueError:
            shown = path
        print(f"{case['id']}\tv{case['version']}\t{case['status']}\t{case['suite']}\t{shown}")
    if not loaded:
        print("No evaluation cases found.")
    return 0


def cmd_eval(argv: list[str]) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            return _validate_command(args.path)
        if args.command == "list":
            return _list_command(args.repo)
        if args.command == "capture":
            output, _case = capture_case(args.repo, args.task_id)
            print(f"Captured draft: {output}")
            print("Missing requirements remain; capture does not prove a red reproduction.")
            return 0
    except EvalCaseError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    return 2
