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
    """rig repo root を返す。

    優先順位:
      1. 環境変数 `RIG_HOME`（他のリポジトリから使うときの正攻法・bin/orchestrate と同じ）
      2. インストール元（pip install -e . でリポジトリ内・pip install rig-workbench で
         `site-packages/rig_workbench/`）から辿れる repo root
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
    raise RuntimeError(
        "rig repo root が見つかりません。RIG_HOME を設定するか、リポジトリ内で "
        "`pip install -e .` してください。"
    )


def _load_script(name: str) -> types.ModuleType:
    """`scripts/<name>.py` を独立モジュールとして安全にロードする。

    通常の `import scripts.foo` は scripts/ が package として設定されていないと
    失敗するため、file-loader を使う。読み込んだモジュールは `sys.modules` に
    キャッシュされ、以降の呼び出しは繰り返しにならない。
    """
    module_key = f"_rig_scripts_{name}"
    if module_key in sys.modules:
        return sys.modules[module_key]
    root = _rig_home()
    script_path = root / "scripts" / f"{name}.py"
    if not script_path.exists():
        raise FileNotFoundError(f"scripts/{name}.py が見つかりません: {script_path}")
    spec = importlib.util.spec_from_file_location(module_key, script_path)
    assert spec is not None and spec.loader is not None, f"import spec 失敗: {script_path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_key] = module
    spec.loader.exec_module(module)
    return module


# ── sub-command dispatch ─────────────────────────────────────────────────


def _run_orchestrate_subcmd(argv: list[str]) -> None:
    """`orchestrate.py` の main() に処理を渡す。

    orchestrate.py は `sys.argv[1:]` を自分で読むので argv を差し替えてから呼ぶ。
    scripts 側の COMMANDS ディスパッチが必要な subcommand（run/plan/runs/etc.）を
    そのまま流用する。
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


# subcommand -> handler。`rig-wb <sub> ...` で呼ぶときの一次表。
# orchestrate.py に既にある subcommand は `_orch_delegates` に列挙し、
# そのまま orchestrate 側の COMMANDS へ渡す（薄いラッパーで済ませる）。
_orch_delegates = {
    "run", "plan", "runs", "init", "check", "verdict",
    "queue", "selftest", "list", "validate", "graph",
    "party", "models", "probe", "install-shim", "review",
}


def _print_help() -> None:
    print(
        f"""rig-wb {__version__} — quality-gated AI workbench (pip flavor)

Usage:
  rig-wb <sub> [args]

Sub-commands:
  run <recipe> --provider <name> ...    orchestrate: 自走実行
  plan <recipe> [--json] [--with ...]   orchestrate: プラン提示
  runs [--limit N] [--recipe R] [--html <path>]
                                        orchestrate: テレメトリ一覧 / HTML dashboard
  queue add|list|go|done ...            orchestrate: queue backend
  wb <cmd> ...                          workbench: new/step/gate/accept/discard/board/audit/stats/…
  dashboard [--out <html>] [--since ...]
                                        scripts/dashboard.py
  validate                              scripts/validate.py
  selftest                              orchestrate: selftest（golden 検証）
  version                               バージョン表示

Environment:
  RIG_HOME                              rig repo root を明示（省略時は自動検出）

Examples:
  rig-wb run bugfix --provider claude --verifier-provider codex
  rig-wb wb board
  rig-wb runs --html /tmp/rig-metrics.html
"""
    )


def main() -> None:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        _print_help()
        return
    sub = argv[0]
    rest = argv[1:]
    if sub == "version" or sub == "--version":
        print(f"rig-wb {__version__}")
        return
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
    print(f"[ERROR] 未知のサブコマンド: {sub!r}", file=sys.stderr)
    print("       `rig-wb --help` で一覧を確認してください。", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
