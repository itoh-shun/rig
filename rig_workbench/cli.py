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
    """rig repo root を返す。scripts/*.py を辿れる場所が必要な subcommand 用。

    優先順位:
      1. 環境変数 `RIG_HOME`（他のリポジトリから使うときの正攻法・bin/orchestrate と同じ）
      2. インストール元（`pip install -e .` でリポジトリ内から入れた場合）
      3. カレントディレクトリ / その親（`cd path/to/rig` して `rig-wb` を叩くケース）

    どこにも見つからない場合、実行方法のヒントつきで例外を投げる。`usage` の
    ように `.rig/runs.jsonl` だけあればよい subcommand はこれを呼ばずに
    `_rig_data_root()` を使う。
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
        "rig repo root が見つかりません。以下のどれかを試してください:\n"
        "  1. rig repo に cd してから rig-wb を叩く\n"
        "  2. RIG_HOME を設定: export RIG_HOME=/path/to/rig\n"
        "  3. rig repo 内で `pip install -e .` して開発版を使う\n"
        "  ※ `rig-wb usage` は repo なしでも動きます (cwd の .rig/runs.jsonl を読む)"
    )


def _rig_data_root() -> pathlib.Path:
    """`.rig/runs.jsonl` / `.rig/audit.jsonl` を探す起点を返す。

    scripts/*.py は不要。usage / dashboard など「実行ログを読むだけ」の subcommand
    は cwd の `.rig/` を素直に見る。無ければ cwd の親を辿り、それも無ければ
    `_rig_home()` にフォールバック。
    """
    cwd = pathlib.Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".rig").is_dir():
            return candidate
    return _rig_home()


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


def _show_usage(argv: list[str]) -> None:
    """`.rig/runs.jsonl` から invoker 別の実行回数を集計する。

    既定は cwd の `.rig/runs.jsonl`（プロジェクト単位の記録）。`--global` で
    `~/.rig/runs.jsonl`（全プロジェクト横断のミラー）に切り替える。
    `RIG_INVOKER` が設定されていた run を「rig-wb 経由」として数え、それ以外を
    「direct」として数える。`--global` 時は `project` フィールドで来歴も表示する。
    `--json` で機械可読出力、`--limit N` で対象範囲を絞れる。
    """
    import collections
    import json as _json
    limit: int | None = None
    as_json = False
    use_global = False
    i = 0
    while i < len(argv):
        if argv[i] == "--limit" and i + 1 < len(argv):
            limit = int(argv[i + 1]); i += 2
        elif argv[i] == "--json":
            as_json = True; i += 1
        elif argv[i] in ("--global", "-g"):
            use_global = True; i += 1
        else:
            i += 1

    if use_global:
        runs_path = pathlib.Path.home() / ".rig" / "runs.jsonl"
        scope = "global (~/.rig/runs.jsonl・全プロジェクトのミラー)"
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
        inv = e.get("invoker") or "direct (rig-wb 未経由)"
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
    print(f"runs 記録: {runs_path}")
    if not entries:
        print("\n記録がありません。まだ `rig-wb ...` は使われていません。")
        if not use_global:
            print("`rig-wb usage --global` で `~/.rig/runs.jsonl` (横断) も見られます。")
        return
    print(f"\n直近 {len(entries)} run:")
    for inv, n in by_invoker.most_common():
        last = last_ts_by.get(inv, "?")
        marker = "◆" if inv.startswith("rig-wb/") else " "
        print(f"  {marker} {inv:35s}  {n:4d} 回   last: {last}")
    rig_wb_runs = sum(n for inv, n in by_invoker.items() if inv.startswith("rig-wb/"))
    if rig_wb_runs == 0:
        print("\n※ まだ `rig-wb` 経由の run はゼロです（scripts/*.py の直呼びのみ）。")
    else:
        print(f"\n◆ rig-wb 経由: {rig_wb_runs} 回 / 全体 {len(entries)} 回 "
              f"({rig_wb_runs / len(entries) * 100:.0f}%)")
    if use_global and by_project:
        print("\nプロジェクト別:")
        for proj, n in by_project.most_common():
            print(f"  {n:4d} 回   {proj}")


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
  usage [--limit N] [--global] [--json] この rig-wb が実際に使われた履歴。
                                        既定は cwd の .rig/runs.jsonl（プロジェクト単位）、
                                        --global で ~/.rig/runs.jsonl（全プロジェクト横断）
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
    # 呼び出し元がこの CLI（`rig-wb`）であることを下流の scripts/*.py にも伝える。
    # scripts/orchestrate.py の telemetry_append と workbench.py の audit_append が
    # 拾って `.rig/runs.jsonl` / `.rig/audit.jsonl` に invoker 情報を残す。これで
    # 「rig-wb 経由で回ったか、素の python3 scripts/... 直呼びか」を区別できる。
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
