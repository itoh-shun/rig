"""eval.cli — `rig-wb eval …`, the operator surface of the evaluation pillar.

This module is `eval`'s **shell** (`tests/test_layering_contract.py`'s `SHELL_MODULES`
names it and says why), so it is allowed to wire and to present. Stage 3 of
`docs/v3-architecture-design-brief.ja.md` §3 asks the same two things of it that it asked
of `govern/cli.py`, and the answer here is the same shape.

**Words leave through the `Presenter` port.** No command calls `print`. Every handler
takes an `out: Presenter`, and the adapter is built once, at the process boundary in
`main()` — a module-level instance reached for from inside each command would be the same
global under a different name, and the point of the port is that a caller (a test, an
embedding harness, the day rig grows a `--quiet`) can hand in a different one. Which
stream a line goes to is unchanged and stays a property of the call: `out.out` is stdout,
`out.err` is stderr, and the `affected-run` hint keeps going to stderr so that the report
on stdout stays parseable.

**And the port is forwarded, not merely held.** The lesson pillar 1 paid for is that a
shell which builds a port and then fails to pass it down leaves the callee's default in
charge, and the default is the real adapter: one command, two clocks
(`tests/test_govern_frozen_clock.py`). The rule this file applies is that it forwards
exactly the ports it is given — `out`, and the `proc` / `env` / `clock` / `graph` that
`cmd_eval` builds below — to every call whose signature declares them.
`tests/test_eval_forwarded_ports.py` drives the verbs with all three adapters disarmed on
their classes, so a handler that forgets to forward fails on the shape.

**A document is not a line.** Four commands print a canonical-JSON document with
`end=""`, because `canonical_json` already ends in exactly one newline. `Presenter.out`
supplies the line ending itself, as `print` does, so those four go through
`_emit_document`, which takes the trailing newline off once. Rendering them with a plain
`out.out` would add a second one — a byte-for-byte change to output that CI parses.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from rig_workbench.ports import Presenter, ProcessRunner
from rig_workbench.ports.local import ConsolePresenter, SubprocessRunner

from .capture import capture_case
from .affected import analyze_affected
from .affected_run import run_affected
from .cases import EvalCaseError, canonical_json, validate_case
from .compare import compare_results, validate_result
from .gate import evaluate_gate
from .promote import promote_case
from .runner import adapter_cwd, make_judge_adapter, read_only_workspace, run_case


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
    capture.add_argument("--allow-nonincident", action="store_true")
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
    run.add_argument("--execution-base")
    reproduce = sub.add_parser("reproduce", help="run a draft against the pre-fix baseline")
    reproduce.add_argument("draft_id")
    reproduce.add_argument("--provider", required=True,
                           choices=["mock", "claude", "codex", "command"])
    reproduce.add_argument("--model", required=True)
    reproduce.add_argument("--repo", default=".")
    reproduce.add_argument("--command", dest="provider_command")
    reproduce.add_argument("--timeout", type=float, default=30)
    reproduce.add_argument("--judge-provider", choices=["mock", "claude", "codex", "command"])
    reproduce.add_argument("--judge-model")
    reproduce.add_argument("--judge-command")
    reproduce.add_argument("--judge-timeout", type=float, default=30)
    reproduce.add_argument("--execution-base")
    reproduce.add_argument("--allow-mock", action="store_true")
    compare = sub.add_parser("compare", help="compare baseline and current results")
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--current", required=True)
    compare.add_argument("--repo", default=".")
    promote = sub.add_parser("promote", help="promote a draft backed by passing evidence")
    promote.add_argument("draft_id")
    promote.add_argument("--baseline", required=True)
    promote.add_argument("--current", required=True)
    promote.add_argument("--repo", default=".")
    promote.add_argument("--into", metavar="PACK",
                         help="write the approved case into this pack instead of the "
                              "repository; run `rig-wb pack sync` afterwards to declare it")
    affected = sub.add_parser("affected", help="map a git diff to prompt cases")
    affected.add_argument("--base", required=True)
    affected.add_argument("--head", default="working")
    affected.add_argument("--repo", default=".")
    affected.add_argument("--require-cases", action="store_true",
                          help="every affected surface must already have a case (strict)")
    affected.add_argument("--ratchet", action="store_true",
                          help="coverage may only go up: a surface with no case yet is "
                               "reported as debt (exit 0), removing existing coverage fails")
    affected.add_argument("--evidence-dir")
    affected.add_argument("--json", action="store_true")
    gate = sub.add_parser("gate", help="enforce affected prompt evaluation evidence")
    gate.add_argument("--base", required=True)
    gate.add_argument("--head", default="working")
    gate.add_argument("--repo", default=".")
    gate.add_argument("--evidence-dir", required=True)
    gate.add_argument("--provider")
    gate.add_argument("--model")
    gate.add_argument("--judge-provider")
    gate.add_argument("--judge-model")
    # The same direction the structural step ahead of it in CI already drives.
    # Without this flag the gate is strict, and a PR that touches one covered
    # surface plus any of the 198 that have no case yet fails `uncovered:` no
    # matter how much signed evidence it carries — a check nobody can pass, which
    # is the shape (#383/#384) the ratchet exists to remove. Evidence checks are
    # untouched by it: the cases that do exist are judged identically either way.
    gate.add_argument("--ratchet", action="store_true",
                      help="coverage may only go up: an affected surface with no "
                           "case yet is debt rather than a failure, while removing "
                           "coverage and unregistered surface kinds stay fatal")
    affected_run = sub.add_parser("affected-run", help="atomically run and gate affected cases")
    affected_run.add_argument("--base", required=True)
    affected_run.add_argument("--head", default="HEAD")
    affected_run.add_argument("--repo", default=".")
    affected_run.add_argument("--provider", required=True,
                              choices=["mock", "claude", "codex", "command"])
    affected_run.add_argument("--model", required=True)
    affected_run.add_argument("--judge-provider", required=True,
                              choices=["mock", "claude", "codex", "command"])
    affected_run.add_argument("--judge-model", required=True)
    affected_run.add_argument("--command", dest="provider_command")
    affected_run.add_argument("--judge-command")
    affected_run.add_argument("--timeout", type=float, default=30)
    affected_run.add_argument("--ratchet", action="store_true",
                              help="measure the covered surfaces and report the rest "
                                   "as debt, instead of refusing to measure anything "
                                   "while one affected surface has no case yet")
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


def _emit_document(out: Presenter, text: str) -> None:
    """A canonical-JSON document, on stdout, byte for byte as `print(text, end="")` had it.

    `canonical_json` appends exactly one newline and `json.dumps` escapes every newline
    inside a string, so the trailing one is the only one there is; `Presenter.out` adds the
    line ending itself. Taking it off here is therefore the identity, and not taking it off
    would append a blank line to output the gate's callers parse.
    """
    out.out(text.removesuffix("\n"))


def _validate_command(path_arg: str | None, out: Presenter) -> int:
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
        out.out(f"valid: {candidate}")
    out.out(f"{len(loaded)} case(s) valid")
    return 0


def _list_command(repo_arg: str, out: Presenter) -> int:
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
        out.out(f"{case['id']}\tv{case['version']}\t{case['status']}\t{case['suite']}\t{shown}")
    if not loaded:
        out.out("No evaluation cases found.")
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


def cmd_eval(argv: list[str], *, out: Presenter | None = None,
             proc: ProcessRunner | None = None) -> int:
    """Parse, run the command, and return its exit status.

    The presenter is a parameter with a default rather than a module-level instance the
    commands reach for: `main()` builds the adapter at the process boundary and passes it
    in, and an in-process caller (`rig_workbench/cli.py` dispatches here, and a test can
    too) may hand in its own. The default exists so those callers keep working unchanged —
    each constructs an adapter, it does not share one.
    """
    parser = _parser()
    args = parser.parse_args(argv)
    out = ConsolePresenter() if out is None else out
    proc = SubprocessRunner() if proc is None else proc
    try:
        if args.command == "validate":
            return _validate_command(args.path, out)
        if args.command == "list":
            return _list_command(args.repo, out)
        if args.command == "capture":
            output, _case = capture_case(
                args.repo, args.task_id, allow_nonincident=args.allow_nonincident
            )
            out.out(f"Captured draft: {output}")
            out.out("Missing requirements remain; capture does not prove a red reproduction.")
            return 0
        if args.command == "reproduce":
            root = _resolve_repo(args.repo)
            cases = _resolve_cases(root, args.draft_id)
            if len(cases) != 1 or cases[0]["status"] != "draft":
                raise EvalCaseError("reproduce requires exactly one draft case")
            if args.provider == "mock" and not args.allow_mock:
                raise EvalCaseError("mock reproduce is only a dev probe; pass --allow-mock")
            if args.judge_provider == "mock" and not args.allow_mock:
                raise EvalCaseError("mock judge reproduce is only a dev probe; pass --allow-mock")
            if bool(args.judge_provider) != bool(args.judge_model):
                raise EvalCaseError("judge provider and model must be specified together")
            with read_only_workspace(root) as workspace:
                judge_adapter = (
                    make_judge_adapter(
                        provider=args.judge_provider, model=args.judge_model,
                        repo=adapter_cwd(args.judge_provider, workspace, root),
                        command=args.judge_command, timeout_s=args.judge_timeout,
                        proc=proc,
                    ) if args.judge_provider else None
                )
                output, result = run_case(
                    cases[0], repo=root, provider=args.provider, model=args.model,
                    repeat=cases[0]["repeat"], phase="baseline",
                    command=args.provider_command, timeout_s=args.timeout,
                    judge_adapter=judge_adapter, execution_base=args.execution_base,
                    execution_cwd=adapter_cwd(args.provider, workspace, root),
                    readable_root=root, proc=proc,
                )
            out.out(str(output))
            dev_probe_only = args.provider == "mock" or args.judge_provider == "mock"
            samples = [*result["target"], *result["clean"]]
            if any(row["infra_status"] is not None for row in samples):
                return 2
            clean_green = all(row["outcome"] == "pass" for row in result["clean"])
            target_red = (
                any(row["outcome"] == "fail" for row in result["target"])
                and result["summary"]["target_success_rate"]
                <= cases[0]["red_thresholds"]["max_success_rate"]
            )
            judge_valid = True
            if cases[0]["semantic_rubric"]:
                expected = [item["id"] for item in cases[0]["semantic_rubric"]]
                judge_valid = result["judge"] == {"required": True, "status": "measured"}
                judge_valid = judge_valid and all(
                    row["judge"]["status"] == "measured"
                    and [item["id"] for item in row["judge"]["criteria"]] == expected
                    for row in samples
                )
            return 0 if target_red and clean_green and judge_valid and not dev_probe_only else 1
        if args.command == "run":
            root = _resolve_repo(args.repo)
            cases = _resolve_cases(root, args.case_or_suite)
            if bool(args.judge_provider) != bool(args.judge_model):
                raise EvalCaseError("judge provider and model must be specified together")
            with read_only_workspace(root) as workspace:
                judge_adapter = (
                    make_judge_adapter(
                        provider=args.judge_provider, model=args.judge_model,
                        repo=adapter_cwd(args.judge_provider, workspace, root),
                        command=args.judge_command, timeout_s=args.judge_timeout,
                        proc=proc,
                    )
                    if args.judge_provider else None
                )
                for case in cases:
                    output, _result = run_case(
                        case, repo=root, provider=args.provider, model=args.model,
                        repeat=args.repeat, phase=args.phase,
                        command=args.provider_command,
                        timeout_s=args.timeout, judge_adapter=judge_adapter,
                        execution_base=args.execution_base,
                        execution_cwd=adapter_cwd(args.provider, workspace, root),
                        readable_root=root, proc=proc,
                    )
                    out.out(str(output))
            return 0
        if args.command == "compare":
            root = _resolve_repo(args.repo)
            baseline = _read_result(args.baseline)
            current = _read_result(args.current)
            case = _case_for_result(root, baseline)
            report = compare_results(baseline, current, case=case)
            _emit_document(out, canonical_json(report))
            return 0 if report["status"] == "pass" else 1
        if args.command == "promote":
            baseline = _read_result(args.baseline)
            current = _read_result(args.current)
            output, _case = promote_case(
                args.repo, args.draft_id, baseline, current, into=args.into
            )
            out.out(str(output))
            if args.into is not None:
                out.out(f"next: rig-wb pack sync {args.into}   # declare the new case")
            return 0
        if args.command == "affected":
            report = analyze_affected(
                args.repo, base=args.base, head=args.head,
                require_cases=args.require_cases, ratchet=args.ratchet,
                evidence_dir=args.evidence_dir, proc=proc,
            )
            _emit_document(out, canonical_json(report))
            # `debt` exits 0 on purpose: it is a number to carry, not a wall. Only
            # an untracked surface or removed coverage stops the run.
            return 1 if report["status"] == "uncovered" else 0
        if args.command == "gate":
            report, exit_code = evaluate_gate(
                args.repo, base=args.base, head=args.head,
                evidence_dir=args.evidence_dir, provider=args.provider, model=args.model,
                judge_provider=args.judge_provider, judge_model=args.judge_model,
                ratchet=args.ratchet, proc=proc,
            )
            _emit_document(out, canonical_json(report))
            return exit_code
        if args.command == "affected-run":
            report, exit_code, destination = run_affected(
                args.repo, base=args.base, head=args.head, provider=args.provider,
                model=args.model, judge_provider=args.judge_provider,
                judge_model=args.judge_model, provider_command=args.provider_command,
                judge_command=args.judge_command, timeout_s=args.timeout,
                ratchet=args.ratchet, proc=proc,
            )
            output = dict(report)
            output["result_dir"] = str(destination) if destination is not None else None
            _emit_document(out, canonical_json(output))
            if destination is not None:
                # stderr so the report on stdout stays parseable. CI verifies this
                # evidence instead of measuring its own; unpushed, it proves nothing.
                out.err(f"Commit the signed evidence under {destination} and push it; "
                        "the CI gate verifies it rather than re-running the provider.")
            return exit_code
    except EvalCaseError as exc:
        out.err(f"[ERROR] {exc}")
        return 2
    return 2


def main() -> None:
    sys.exit(cmd_eval(sys.argv[1:], out=ConsolePresenter(), proc=SubprocessRunner()))


if __name__ == "__main__":
    main()
