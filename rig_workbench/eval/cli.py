"""Command-line interface for evaluation case capture and inspection."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .capture import capture_case
from .cases import EvalCaseError, canonical_json, validate_case
from .compare import compare_results, validate_result
from .promote import promote_case
from .runner import make_judge_adapter, run_case


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
    run = sub.add_parser("run", help="run one evaluation case or suite")
    run.add_argument("case_or_suite")
    run.add_argument("--provider", required=True,
                     choices=["mock", "claude", "codex", "command"])
    run.add_argument("--model", required=True)
    run.add_argument("--repeat", required=True, type=int)
    run.add_argument("--phase", required=True, choices=["baseline", "current"])
    run.add_argument("--repo", default=".")
    run.add_argument("--command", dest="provider_command")
    run.add_argument("--timeout", type=float, default=30)
    run.add_argument("--judge-provider", choices=["mock", "claude", "codex", "command"])
    run.add_argument("--judge-model")
    run.add_argument("--judge-command")
    run.add_argument("--judge-timeout", type=float, default=30)
    compare = sub.add_parser("compare", help="compare baseline and current results")
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--current", required=True)
    compare.add_argument("--repo", default=".")
    promote = sub.add_parser("promote", help="promote a draft backed by passing evidence")
    promote.add_argument("draft_id")
    promote.add_argument("--baseline", required=True)
    promote.add_argument("--current", required=True)
    promote.add_argument("--repo", default=".")
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


def _resolve_repo(repo_arg: str) -> pathlib.Path:
    try:
        return pathlib.Path(repo_arg).resolve()
    except OSError as exc:
        raise EvalCaseError(f"filesystem error resolving repository: {exc}") from exc


def _resolve_cases(root: pathlib.Path, selector: str) -> list[dict]:
    candidate = pathlib.Path(selector)
    if candidate.exists():
        return [case for _path, case in _load_unique_cases(_case_paths(candidate))]
    loaded = _load_unique_cases(_repo_case_paths(root))
    by_id = [case for _path, case in loaded if case["id"] == selector]
    if by_id:
        return by_id
    by_suite = [case for _path, case in loaded if case["suite"] == selector]
    if not by_suite:
        raise EvalCaseError(f"evaluation case or suite not found: {selector}")
    return by_suite


def _read_result(path_arg: str) -> dict:
    path = pathlib.Path(path_arg)
    try:
        raw = path.read_text(encoding="utf-8")
        result = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvalCaseError(f"cannot read evaluation result: {exc}") from exc
    validate_result(result)
    if raw != canonical_json(result):
        raise EvalCaseError(f"evaluation result is not canonical JSON: {path}")
    return result


def _case_for_result(root: pathlib.Path, result: dict) -> dict:
    matches = [case for _path, case in _load_unique_cases(_repo_case_paths(root))
               if case["id"] == result["case_id"]]
    if len(matches) != 1:
        raise EvalCaseError("matching evaluation case was not found")
    return matches[0]


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
        if args.command == "run":
            root = _resolve_repo(args.repo)
            cases = _resolve_cases(root, args.case_or_suite)
            if bool(args.judge_provider) != bool(args.judge_model):
                raise EvalCaseError("judge provider and model must be specified together")
            judge_adapter = (
                make_judge_adapter(
                    provider=args.judge_provider, model=args.judge_model, repo=root,
                    command=args.judge_command, timeout_s=args.judge_timeout,
                )
                if args.judge_provider else None
            )
            for case in cases:
                output, _result = run_case(
                    case, repo=root, provider=args.provider, model=args.model,
                    repeat=args.repeat, phase=args.phase, command=args.provider_command,
                    timeout_s=args.timeout, judge_adapter=judge_adapter,
                )
                print(output)
            return 0
        if args.command == "compare":
            root = _resolve_repo(args.repo)
            baseline = _read_result(args.baseline)
            current = _read_result(args.current)
            case = _case_for_result(root, baseline)
            report = compare_results(baseline, current, case=case)
            print(canonical_json(report), end="")
            return 0 if report["status"] == "pass" else 1
        if args.command == "promote":
            baseline = _read_result(args.baseline)
            current = _read_result(args.current)
            output, _case = promote_case(
                args.repo, args.draft_id, baseline, current
            )
            print(output)
            return 0
    except EvalCaseError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    return 2
