"""rig-wb — the standalone CLI entry point exposed by `pip install rig-workbench`.

Dispatches sub-commands to the existing `scripts/*.py` modules by loading them
via `importlib.util` (they still live at the original path so the Claude Code
plugin, the `bin/orchestrate` shim, and any project pinning `.claude-plugin/`
paths keep working). This is the least-invasive first step: same code, new
entry point.

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
import sys
import types

from . import __version__

# ── rig repo root discovery ──────────────────────────────────────────────


def _rig_home() -> pathlib.Path:
    """Return the rig repo root. For subcommands that need access to scripts/*.py.

    Priority:
      1. The `RIG_HOME` env var (the canonical way when used from another repo;
         same as bin/orchestrate)
      2. The install source (when installed from inside the repo via `pip install -e .`)
      3. The current directory / its parents (the `cd path/to/rig` then `rig-wb` case)

    If none is found, raises an exception with hints on how to run. Subcommands
    like `usage` that only need `.rig/runs.jsonl` should use `_rig_data_root()`
    instead of calling this.
    """
    env = os.environ.get("RIG_HOME")
    if env:
        p = pathlib.Path(env).resolve()
        if (p / "scripts" / "orchestrate.py").exists():
            return p
    here = pathlib.Path(__file__).resolve().parent
    for candidate in (here.parent, here.parent.parent):
        if (candidate / "scripts" / "orchestrate.py").exists():
            return candidate
    cwd = pathlib.Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / "scripts" / "orchestrate.py").exists():
            return candidate
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
    """Hand off to `orchestrate.py`'s main().

    orchestrate.py reads `sys.argv[1:]` itself, so swap argv before calling.
    Subcommands that need the COMMANDS dispatch on the scripts side
    (run/plan/runs/etc.) are reused as-is.
    """
    orch = _load_script("orchestrate")
    old = sys.argv
    try:
        sys.argv = ["orchestrate.py", *argv]
        orch.main()
    finally:
        sys.argv = old


def _run_workbench(argv: list[str]) -> None:
    wb = _load_script("workbench")
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
  output                      schema v2; old schema-v1 reports remain renderable
  --provider mock             WIRING ONLY, not quality evidence
  --allow-paid-provider       explicit opt-in required for claude/codex execution
  exits                       0=pass; 1=completed non-pass; 2=CLI/schema error
"""
    )


def _bench_provider(argv: list[str]) -> str:
    for index, arg in enumerate(argv):
        if arg == "--provider" and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith("--provider="):
            return arg.partition("=")[2]
    return "mock"


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

    if any(arg in {"-h", "--help"} for arg in argv):
        _print_bench_contract_help()
    allow_paid = "--allow-paid-provider" in argv
    filtered_argv = [arg for arg in argv if arg != "--allow-paid-provider"]
    provider = _bench_provider(filtered_argv)
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
    "party",
    "models",
    "probe",
    "install-shim",
    "review",
}


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

    if use_global:
        runs_path = pathlib.Path.home() / ".rig" / "runs.jsonl"
        scope = "global (~/.rig/runs.jsonl, mirror across all projects)"
    else:
        home = _rig_data_root()
        runs_path = home / ".rig" / "runs.jsonl"
        scope = f"local (cwd={home})"

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
        print(_json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(f"## rig-wb usage — {__version__}")
    print(f"scope: {scope}")
    print(f"runs log: {runs_path}")
    if not entries:
        print("\nNo records found. `rig-wb ...` has not been used yet.")
        if not use_global:
            print("Use `rig-wb usage --global` to see `~/.rig/runs.jsonl` (cross-project).")
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


def _print_help() -> None:
    print(
        f"""rig-wb {__version__} — quality-gated AI workbench (pip flavor)

Usage:
  rig-wb <sub> [args]

Sub-commands:
  run <recipe> --provider <name> ...    orchestrate: autonomous run
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
  githooks install|uninstall|status [--force]
                                        native git pre-commit/pre-push hooks
                                        (computational sensors only; issue #298)
  bench [--corpus PATH] [--tasks ...] [--provider X] [--runs N] [--out <json>]
                                        bare vs rig A/B benchmark
                                        (schema v2; paid providers require explicit opt-in)
  sensor-bench [--json]                 deterministic machine-sensor catch-rate benchmark
                                        (no LLM, no billing; secrets/injection/destructive)
  version                               show version

Environment:
  RIG_HOME                              set the rig repo root explicitly (auto-detected if omitted)

Examples:
  rig-wb run bugfix --provider claude --verifier-provider codex
  rig-wb wb board
  rig-wb runs --html /tmp/rig-metrics.html
"""
    )


def main() -> None:
    # Tell downstream scripts/*.py that the caller is this CLI (`rig-wb`).
    # telemetry_append in scripts/orchestrate.py and audit_append in workbench.py
    # pick this up and record invoker info in `.rig/runs.jsonl` / `.rig/audit.jsonl`,
    # so we can distinguish runs via rig-wb from direct `python3 scripts/...` calls.
    os.environ.setdefault("RIG_INVOKER", f"rig-wb/{__version__}")

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
    if sub == "sensor-bench":
        from . import sensor_bench as sensor_bench_mod

        sensor_bench_mod.cmd_sensor_bench(rest)
        return
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
