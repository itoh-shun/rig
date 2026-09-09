"""rig-wb — the standalone CLI entry point exposed by `pip install rig-workbench`.

Dispatches core commands through package-native modules. A few legacy utility
commands still load their repository scripts when a source checkout is present.

Usage:
    rig-wb run <recipe> --provider claude ...        # orchestrate.py run
    rig-wb plan <recipe> [--json] [--with '...']     # orchestrate.py plan
    rig-wb runs [--html /tmp/rig.html]               # orchestrate.py runs
    rig-wb wb <cmd> ...                              # workbench.py <cmd>
    rig-wb dashboard [--out /tmp/rig.html]           # scripts/dashboard.py
    rig-wb validate                                  # scripts/validate.py
    rig-wb selftest                                  # orchestrate.py selftest
    rig-wb version

Environment:
    RIG_HOME  — override the rig repo root (otherwise inferred from this file).
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import sys
import types

from . import exitcodes
from . import __version__, repo_paths

# ── rig repo root discovery ──────────────────────────────────────────────


def _rig_home() -> pathlib.Path:
    """Return the rig repo root. For subcommands that need access to scripts/*.py.

    The search order lives in `repo_paths` — RIG_HOME, then the install source,
    then cwd and its parents — and is shared with every other module that reaches
    for `scripts/*.py`. What is local to this function is the failure: raising with
    hints on how to run. Subcommands like `usage` that only need `.rig/runs.jsonl`
    should use `_rig_data_root()` instead of calling this.
    """
    root = repo_paths.find_root()
    if root:
        return root
    raise RuntimeError(
        "rig repo root not found. Try one of the following:\n"
        "  1. cd into the rig repo before running rig-wb\n"
        "  2. Set RIG_HOME: export RIG_HOME=/path/to/rig\n"
        "  3. Run `pip install -e .` inside the rig repo to use the dev version\n"
        "  Note: `rig-wb usage` works without the repo (reads .rig/runs.jsonl in cwd)"
    )


def _rig_data_root() -> pathlib.Path:
    """Return the base directory to look for `.rig/runs.jsonl` / `.rig/audit.jsonl`.

    scripts/*.py is not needed. Subcommands that only read run logs (usage,
    dashboard, etc.) simply look at `.rig/` in cwd; if absent, walk up cwd's
    parents, and fall back to `_rig_home()` as a last resort.
    """
    cwd = pathlib.Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".rig").is_dir():
            return candidate
    return _rig_home()


def _load_script(name: str) -> types.ModuleType:
    """Safely load `scripts/<name>.py` as a standalone module.

    A plain `import scripts.foo` fails unless scripts/ is set up as a package,
    so use a file loader instead. The loaded module is cached in `sys.modules`,
    so subsequent calls do not reload it.
    """
    module_key = f"_rig_scripts_{name}"
    if module_key in sys.modules:
        return sys.modules[module_key]
    root = _rig_home()
    script_path = root / "scripts" / f"{name}.py"
    if not script_path.exists():
        raise FileNotFoundError(f"scripts/{name}.py not found: {script_path}")
    spec = importlib.util.spec_from_file_location(module_key, script_path)
    assert spec is not None and spec.loader is not None, f"import spec failed: {script_path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_key] = module
    spec.loader.exec_module(module)
    return module


# ── sub-command dispatch ─────────────────────────────────────────────────


def _run_orchestrate_subcmd(argv: list[str]) -> None:
    """Hand off to the package-native orchestrator entry point."""
    from .orchestrate import cli as orch

    old = sys.argv
    try:
        sys.argv = ["orchestrate.py", *argv]
        orch.main()
    finally:
        sys.argv = old


def _run_workbench(argv: list[str]) -> None:
    if argv[:1] == ["route"]:
        from .workbench import route_cli

        route_cli.main(argv[1:])
        return
    from .workbench import cli as wb

    old = sys.argv
    try:
        sys.argv = ["workbench.py", *argv]
        wb.main()
    finally:
        sys.argv = old


def _run_dashboard(argv: list[str]) -> None:
    dash = _load_script("dashboard")
    old = sys.argv
    try:
        sys.argv = ["dashboard.py", *argv]
        dash.main()
    finally:
        sys.argv = old


def _run_validate(argv: list[str]) -> None:
    val = _load_script("validate")
    old = sys.argv
    try:
        sys.argv = ["validate.py", *argv]
        val.main()
    finally:
        sys.argv = old


def _print_bench_contract_help() -> None:
    print(
        """Benchmark evidence contract:
  --corpus <path>             load an external corpus instead of the packaged tasks
  --runs N                    planned pairs per task; validity still requires
                              3 valid pairs for each of at least 10 tasks
  --bare-model / --rig-model  per-arm model override (e.g. a cheaper model
                              driven by rig vs. a stronger bare baseline);
                              both default to --model when omitted
  output                      schema v2; old schema-v1 reports remain renderable
  --provider mock             WIRING ONLY, not quality evidence
  --allow-paid-provider       explicit opt-in required for claude/codex execution
  exits                       0=pass; 1=completed non-pass; 2=CLI/schema error
"""
    )


def _bench_providers(argv: list[str]) -> list[str]:
    providers = []
    for index, arg in enumerate(argv):
        if arg == "--provider" and index + 1 < len(argv):
            providers.append(argv[index + 1])
        if arg.startswith("--provider="):
            providers.append(arg.partition("=")[2])
    return providers


def _benchmark_exit_code(summary: dict[str, object]) -> int:
    if summary.get("schema_version") != 2:
        raise ValueError("benchmark schema v2 summary required")
    score = summary.get("score")
    if not isinstance(score, dict):
        raise ValueError("benchmark schema v2 score required")
    verdict = score.get("verdict")
    if verdict == "pass":
        return 0
    if verdict in {"fail", "invalid", "inconclusive"}:
        return 1
    raise ValueError(f"benchmark schema v2 verdict is invalid: {verdict!r}")


def _run_bench(argv: list[str]) -> None:
    from . import bench as bench_mod

    allow_paid = "--allow-paid-provider" in argv
    filtered_argv = [arg for arg in argv if arg != "--allow-paid-provider"]
    if any(arg in {"-h", "--help"} for arg in filtered_argv):
        _print_bench_contract_help()
        bench_mod.cmd_bench(filtered_argv)
        return

    providers = _bench_providers(filtered_argv)
    if len(providers) > 1:
        print("[ERROR] duplicate --provider options are not allowed.", file=sys.stderr)
        raise SystemExit(2)
    provider = providers[0] if providers else "mock"
    if provider in {"claude", "codex"} and not allow_paid:
        print(
            f"[ERROR] --provider {provider} requires explicit --allow-paid-provider opt-in.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    summary: dict[str, object] | None = None
    run_benchmark = bench_mod.run_benchmark

    def capture_summary(*args, **kwargs):
        nonlocal summary
        summary = run_benchmark(*args, **kwargs)
        return summary

    bench_mod.run_benchmark = capture_summary
    try:
        bench_mod.cmd_bench(filtered_argv)
    except (OSError, ValueError) as error:
        print(f"[ERROR] benchmark CLI/schema error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    finally:
        bench_mod.run_benchmark = run_benchmark

    if summary is None:
        print("[ERROR] benchmark schema v2 summary was not produced.", file=sys.stderr)
        raise SystemExit(2)
    try:
        exit_code = _benchmark_exit_code(summary)
    except ValueError as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        raise SystemExit(2) from error
    raise SystemExit(exit_code)


# subcommand -> handler. Primary table for `rig-wb <sub> ...` calls.
# Subcommands that already exist in orchestrate.py are listed in
# `_orch_delegates` and passed straight to orchestrate's COMMANDS
# (a thin wrapper is enough).
_orch_delegates = {
    # Cross-project rollup. It was reachable only through `scripts/orchestrate.py`, which is
    # the historical entrypoint rather than the installed one — so the command that answers
    # "how are my projects doing" could not be run from the CLI people install.
    "fleet",
    "run",
    "plan",
    "runs",
    "init",
    "check",
    "verdict",
    "queue",
    "selftest",
    "list",
    "validate",
    "graph",
    "models",
    "probe",
    "install-shim",
    "review",
    # #501 and #502 shipped in 2.8.0 and the READMEs document them as `rig-wb otel` and
    # `rig-wb perf` — twelve times for perf, including a line meant to be pasted into CI.
    # Neither was on this list, so every one of those invocations answered "Unknown
    # sub-command". The features themselves worked the whole time through
    # `scripts/orchestrate.py`; what was missing was the two names below.
    "otel",
    "perf",
    # The human-approval flow the READMEs walk through — `next` parks a run on a person and
    # `approve` releases it. Both were documented as `rig-wb orchestrate <cmd>`, a spelling
    # with no subcommand behind it at all, so the whole governance example errored.
    "approve",
    "next",
}


# A workbench task is recorded when it reaches accept or discard — the two points it
# actually ends. Everything else is still in flight: a failed gate is fixable and
# re-runnable, so recording there would count one task many times.
_WORKBENCH_TERMINAL_STATUSES = frozenset({"accepted", "discarded"})


def _workbench_task_records(root: pathlib.Path):
    """Every task record under this repo's runs directory, and what could not be read.

    The shared reader (#488), not a fourth walk of `.rig/runs/*/task.json`. This one used to
    do its own and `continue` past anything it could not parse, so a record that happened to
    be unfinished simply left the count — and a count that is quietly short is worse here
    than a count that is missing, because this whole section exists to say what the number
    does not contain.
    """
    from .workbench.reporting import read_all_tasks

    return read_all_tasks(root / ".rig" / "runs")


def _unfinished_workbench_tasks(records) -> int:
    """Workbench tasks in this repo that have not reached a terminal state.

    They have no run record and cannot have one yet, so they are missing from the count
    above. Saying so is the difference between a number that is incomplete and a number
    that is quietly wrong. Takes the records rather than the root, so the count and the
    shortfall reported beside it always come from the same read of the directory.
    """
    return sum(1 for task in records.tasks
               if task["status"] not in _WORKBENCH_TERMINAL_STATUSES)


def _usage_coverage_lines(root: pathlib.Path | None) -> list[str]:
    """What this aggregate does not contain, stated rather than left to be discovered.

    The count answers "how much has rig been used", and a reader takes a missing entry
    as absence rather than as a blind spot. This log has two of those. One is countable
    and gets counted; the other cannot be, and gets named.

    A third sits between them: a task record that could not be read at all. Whether it is
    unfinished is unknown, so it can neither be counted nor left out silently — it is named
    with the same sentence every other reader of the runs directory uses.
    """
    lines = []
    if root is not None:
        records = _workbench_task_records(root)
        pending = _unfinished_workbench_tasks(records)
        if pending:
            lines.append(f"  - {pending} workbench task(s) here have not reached accept or "
                         "discard, so they have no record yet (`rig-wb wb board`).")
        if records.note():
            lines.append(f"  - {records.note().lstrip(' —')} — whether any of them is "
                         "unfinished is unknown, so the count above does not include them.")
    lines.append("  - A manual or workflow RUN that never went through `/rig:go` appends by "
                 "prose instruction (SKILL.md §6), not by code, so it may be missing entirely.")
    return lines


def _show_usage(argv: list[str]) -> None:
    """Aggregate run counts per invoker from `.rig/runs.jsonl`.

    Defaults to `.rig/runs.jsonl` in cwd (per-project record). `--global`
    switches to `~/.rig/runs.jsonl` (a mirror across all projects).
    Runs that had `RIG_INVOKER` set are counted as "via rig-wb"; everything
    else as "direct". With `--global`, provenance is also shown via the
    `project` field. `--json` gives machine-readable output; `--limit N`
    narrows the range.
    """
    import collections
    import json as _json

    limit: int | None = None
    as_json = False
    use_global = False
    i = 0
    while i < len(argv):
        if argv[i] == "--limit" and i + 1 < len(argv):
            limit = int(argv[i + 1])
            i += 2
        elif argv[i] == "--json":
            as_json = True
            i += 1
        elif argv[i] in ("--global", "-g"):
            use_global = True
            i += 1
        else:
            i += 1

    # The unfinished-task count can only be taken for the repo we are standing in;
    # --global spans repos whose `.rig/runs/` this process cannot see.
    coverage_root: pathlib.Path | None
    if use_global:
        # The writer resolves this through RIG_GLOBAL_RUNS_PATH
        # (orchestrate.config.GLOBAL_RUNS_PATH). Computing it from $HOME here instead
        # meant that in any environment which sets that variable, `usage --global`
        # read a different file than the one every run was being written to.
        from .orchestrate import config as _orch_config

        runs_path = _orch_config.GLOBAL_RUNS_PATH
        scope = f"global ({runs_path}, mirror across all projects)"
        coverage_root = None
    else:
        # Same reason as the branch above, for the same reader: the writer resolves
        # RIG_RUNS_PATH, and production code sets it (bench_providers points every run
        # at an artifact directory). `_rig_data_root()` only walks up looking for a
        # `.rig`, so those runs were written to one file and read from another.
        from .orchestrate import config as _orch_config

        home = _rig_data_root()
        runs_path = _orch_config.RUNS_PATH
        scope = f"local (cwd={home})"
        # Not `runs_path.parent`: this one scans `.rig/runs/` for unfinished tasks, which
        # lives with the repository even when the log has been redirected elsewhere.
        coverage_root = home

    entries: list[dict] = []
    if runs_path.exists():
        for line in runs_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(_json.loads(line))
            except _json.JSONDecodeError:
                continue

    if limit is not None and limit > 0:
        entries = entries[-limit:]

    by_invoker: collections.Counter[str] = collections.Counter()
    last_ts_by: dict[str, str] = {}
    by_project: collections.Counter[str] = collections.Counter()
    for e in entries:
        inv = e.get("invoker") or "direct (not via rig-wb)"
        by_invoker[inv] += 1
        ts = e.get("ts")
        if ts and (inv not in last_ts_by or ts > last_ts_by[inv]):
            last_ts_by[inv] = ts
        if use_global:
            proj = e.get("project") or "?"
            by_project[proj] += 1

    if as_json:
        payload = {
            "installed_version": __version__,
            "scope": "global" if use_global else "local",
            "runs_path": str(runs_path),
            "total": len(entries),
            "by_invoker": dict(by_invoker),
            "last_seen_by_invoker": last_ts_by,
        }
        if use_global:
            payload["by_project"] = dict(by_project)
        else:
            records = _workbench_task_records(coverage_root)
            payload["unfinished_workbench_tasks"] = _unfinished_workbench_tasks(records)
            # The count above is over the records that could be read. A consumer parsing
            # this is exactly the caller that cannot see the note printed for a human.
            payload["unreadable_workbench_task_records"] = list(records.unreadable)
            payload["workbench_task_collection_error"] = records.collection_error
        payload["not_counted"] = [ln.strip(" -") for ln in _usage_coverage_lines(coverage_root)]
        print(_json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(f"## rig-wb usage — {__version__}")
    print(f"scope: {scope}")
    print(f"runs log: {runs_path}")
    if not entries:
        print("\nNo records found. `rig-wb ...` has not been used yet.")
        if not use_global:
            print("Use `rig-wb usage --global` to see `~/.rig/runs.jsonl` (cross-project).")
        # Especially here: "no records" is the reading most likely to be mistaken for
        # "rig was not used", which is exactly the inference this note exists to block.
        print("\nNot in this count:")
        for line in _usage_coverage_lines(coverage_root):
            print(line)
        return
    print(f"\nLast {len(entries)} runs:")
    for inv, n in by_invoker.most_common():
        last = last_ts_by.get(inv, "?")
        marker = "◆" if inv.startswith("rig-wb/") else " "
        print(f"  {marker} {inv:35s}  {n:4d} runs   last: {last}")
    rig_wb_runs = sum(n for inv, n in by_invoker.items() if inv.startswith("rig-wb/"))
    if rig_wb_runs == 0:
        print("\nNote: no runs via `rig-wb` yet (only direct scripts/*.py calls).")
    else:
        print(
            f"\n◆ via rig-wb: {rig_wb_runs} of {len(entries)} runs "
            f"({rig_wb_runs / len(entries) * 100:.0f}%)"
        )
    if use_global and by_project:
        print("\nBy project:")
        for proj, n in by_project.most_common():
            print(f"  {n:4d} runs   {proj}")
    print("\nNot in this count:")
    for line in _usage_coverage_lines(coverage_root):
        print(line)


def _print_help() -> None:
    print(
        f"""rig-wb {__version__} — quality-gated AI workbench (pip flavor)

Usage:
  rig-wb <sub> [args]

Sub-commands:
  run <recipe> --provider <name> [--goal-stdin] ...
                                        orchestrate: autonomous run
  plan <recipe> [--json] [--with ...]   orchestrate: show plan
  runs [--limit N] [--recipe R] [--html <path>]
                                        orchestrate: telemetry list / HTML dashboard
  queue add|list|go|done ...            orchestrate: queue backend
  wb <cmd> ...                          workbench: new/step/gate/accept/discard/board/audit/stats/…
  dashboard [--out <html>] [--since ...]
                                        scripts/dashboard.py
  validate                              scripts/validate.py
  selftest                              orchestrate: selftest (golden verification)
  usage [--limit N] [--global] [--json] History of actual rig-wb usage.
                                        Defaults to .rig/runs.jsonl in cwd (per-project);
                                        --global reads ~/.rig/runs.jsonl (across all projects)
  gh-check [--json]                     report the `gh` + github/gh-stack state
                                        (optional tools: rig runs without them.
                                        exit 0=ok / 3=gh missing / 5=gh-stack missing;
                                        auth is reported, never required)
  asvs [--check] [--json]               ASVS chapters vs the inspection surface rig has.
                                        The empty rows are the point: a chapter with no
                                        mechanism is one rig cannot notice a defect in
  coverage [--run] [--markdown] [--json]
                                        documented requirement -> evidence map.
                                        default verifies the map against the repo (free);
                                        --run executes the deterministic evidence
  hostcheck [--json] [--strict]         host-side prerequisites rig cannot enforce
                                        (container isolation, permissions.deny, ignored state).
                                        exit 0=ok / 3=missing / 1=missing with --strict
  mutation [--run] [--record-baseline] [--apply TASK] [--report P] [--json]
                                        score an external mutation-testing report
                                        (Stryker / mutmut) and hand it to the gate.
                                        finds the report and reads its format itself;
                                        --run also runs the project's own tool first.
                                        comparative and warning-grade: only a drop
                                        against the baseline is actionable
  githooks install|uninstall|status [--force]
                                        native git pre-commit/pre-push hooks
                                        (computational sensors only; issue #298)
  bench [--corpus PATH] [--tasks ...] [--provider X] [--runs N] [--out <json>]
                                        bare vs rig A/B benchmark
                                        (schema v2; paid providers require explicit opt-in)
  baseline capture|compare|show ...     versioned benchmark baseline and scorecard
  eval validate|list|capture|run|compare|promote ...
                                        versioned regression evaluation cases
  pack init|validate|doctor|install|test|import-results|keygen|sign|remove|invoke ...
                                        validated prompt-pack lifecycle/publishing
  sensor-bench [--json]                 deterministic machine-sensor catch-rate benchmark
                                        (no LLM, no billing; secrets/injection/destructive)
  govern init|migrate|policy|whoami|can|approve|waiver|audit|conformance|rollup ...
                                        org/team layer: common policy, permissions,
                                        approvals, waivers, tamper-evident audit.
                                        inert until a repo is bound with `govern init`
  version                               show version

Environment:
  RIG_HOME                              set the rig repo root explicitly (auto-detected if omitted)
  RIG_POLICY_HOME                       shared org-policy checkout that relative
                                        `policy_layers` entries resolve against, so every
                                        team repository points at the same document
  RIG_ACTOR                             identity for governance decisions
                                        (falls back to RIG_USER, then git config user.name)
  RIG_SKIP_GH_CHECK=1                   silence the one-line note about a missing
                                        `gh` / github/gh-stack. Gates nothing: those
                                        tools are optional and never block a run

Examples:
  rig-wb run bugfix --provider claude --verifier-provider codex
  rig-wb wb board
  rig-wb runs --html /tmp/rig-metrics.html
"""
    )


def _warn_version_skew() -> None:
    """One stderr line when the checkout being driven is not this CLI's version.

    An old rig-wb stays on PATH and keeps loading the *current* repo's scripts/*.py,
    so the skew surfaces as an import error from a layout that release never had —
    which reads as "rig-wb is not installed" rather than "rig-wb is out of date".
    Naming both versions turns that into an actionable line.

    Never blocks, never raises, never fires from a source checkout (there the repo's
    __init__.py is the very file this __version__ came from). `RIG_SKIP_VERSION_CHECK=1`
    silences it.

    The checkout being described is not necessarily trusted — whatever sits between
    the quotes of some repo's `__version__` lands on this line. `[^"]+` spans
    newlines, so a hostile `__init__.py` could put whole blocks of its own text on
    stderr, terminal escapes and forged warning lines included. So: one line, a
    bounded capture, and the same escaping the injection scanner uses when it
    quotes untrusted text back at a terminal.
    """
    if os.environ.get("RIG_SKIP_VERSION_CHECK"):
        return
    try:
        init = _rig_home() / "rig_workbench" / "__init__.py"
        match = re.search(r'^__version__ = "([^"\n]{1,64})"',
                          init.read_text(encoding="utf-8"), re.M)
        if match and match.group(1) != __version__:
            # Imported here rather than at module scope: this function runs on every
            # rig-wb invocation, injection.py pulls in the workbench state module,
            # and only the rare skew case needs it.
            from .workbench.injection import bounded_excerpt

            repo_version = bounded_excerpt(match.group(1), 32)
            # The path is filesystem-controlled too, so it gets the same treatment.
            print(f"[rig-wb] version skew: CLI {__version__} vs repo {repo_version} "
                  f"({bounded_excerpt(str(_rig_home()), 200)}). Run /rig:setup to update.",
                  file=sys.stderr)
    except Exception:
        # No repo in reach, unreadable file, anything else: this is a courtesy line.
        pass


@exitcodes.guard
def main() -> None:
    # Tell downstream scripts/*.py that the caller is this CLI (`rig-wb`).
    # telemetry_append in scripts/orchestrate.py and audit_append in workbench.py
    # pick this up and record invoker info in `.rig/runs.jsonl` / `.rig/audit.jsonl`,
    # so we can distinguish runs via rig-wb from direct `python3 scripts/...` calls.
    os.environ.setdefault("RIG_INVOKER", f"rig-wb/{__version__}")
    _warn_version_skew()

    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        _print_help()
        return
    sub = argv[0]
    rest = argv[1:]
    if sub == "version" or sub == "--version":
        print(f"rig-wb {__version__}")
        return
    if sub == "usage":
        _show_usage(rest)
        return
    if sub == "bench":
        _run_bench(rest)
        return
    if sub == "baseline":
        from . import baseline as baseline_mod

        sys.exit(baseline_mod.cmd_baseline(rest))
    if sub == "eval":
        from .eval import cli as eval_cli

        sys.exit(eval_cli.cmd_eval(rest))
    if sub == "pack":
        from .packs import cli as pack_cli

        sys.exit(pack_cli.cmd_pack(rest))
    if sub == "govern":
        from .govern import cli as govern_cli

        sys.exit(govern_cli.cmd_govern(rest))
    if sub == "asvs":
        from . import asvs as asvs_mod

        sys.exit(asvs_mod.cmd_asvs(rest))
    if sub == "coverage":
        from . import coverage as coverage_mod

        sys.exit(coverage_mod.cmd_coverage(rest))
    if sub == "hostcheck":
        from . import hostcheck as hostcheck_mod

        sys.exit(hostcheck_mod.cmd_hostcheck(rest))
    if sub == "mutation":
        from . import mutation as mutation_mod

        sys.exit(mutation_mod.cmd_mutation(rest))
    if sub == "sensor-bench":
        from . import sensor_bench as sensor_bench_mod

        sensor_bench_mod.cmd_sensor_bench(rest)
        return
    if sub == "bench-invariance":
        from . import bench_invariance as bench_invariance_mod

        allow_paid = "--allow-paid-provider" in rest
        filtered = [arg for arg in rest if arg != "--allow-paid-provider"]
        providers = _bench_providers(filtered)
        provider = providers[0] if providers else "mock"
        if provider in {"claude", "codex"} and not allow_paid:
            print(
                f"[ERROR] --provider {provider} requires explicit --allow-paid-provider opt-in.",
                file=sys.stderr,
            )
            raise SystemExit(2)
        bench_invariance_mod.cmd_invariance(filtered)
        return
    if sub == "gh-check":
        from . import gh_requirement

        sys.exit(gh_requirement.cmd_gh_check(rest))
    if sub == "githooks":
        from . import githooks as githooks_mod

        sys.exit(githooks_mod.cmd_githooks(rest))
    if sub == "wb":
        _run_workbench(rest)
        return
    if sub == "dashboard":
        _run_dashboard(rest)
        return
    if sub == "validate":
        _run_validate(rest)
        return
    if sub in _orch_delegates:
        _run_orchestrate_subcmd([sub, *rest])
        return
    print(f"[ERROR] Unknown sub-command: {sub!r}", file=sys.stderr)
    print("       Run `rig-wb --help` for the list of sub-commands.", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
