"""
rig 計算的オーケストレータ（deterministic orchestration runner）

recipe のステップ DAG を**コードが**解釈し、遷移・ゲート・停止条件・状態保持を
決定論的に強制する薄いランナー。rig engine（SKILL.md）の「制御ループを散文で
モデルに握らせる」弱点を埋める層＝舵をコードが握る（engine 不変・opt-in）。

モデルは各ステップの「作業」をするが、「次に何をするか」はこのランナーが決める：
  plan   <recipe.md> [--json] [--with "<flags>"] [--diff-lines N | --diff-git]
                                     ステップ状態機械を決定論的に算出（モデル不要）。--json は RESOLVE
                                     一次実装＝extends マージ（remove/origin）・badge/steps: 導出・
                                     condition 評価・size 判定・スライス・flag 優先順位を機械出力。
                                     --diff-git は git diff HEAD から行数を自動測定、manifest
                                     （.claude/rig.md）の size_thresholds / default_orchestrate を反映
                                     （selftest Q/R/S が golden 検証・散文エンジンは RESOLVE 時にこれを呼ぶ）
  init   <recipe.md> [--goal G]      run-state を作成し最初のアクションを出す
  check  <state.json>                現ステップの checks: (shell) を実行し pass/fail 記録（計算的センサー）
  verdict<state.json> --by N --pass  独立検証者の推論的判定を記録（採点者≠生成者を強制）
  next   <state.json>                次の遷移を決定論的に計算・適用して出力
  status <state.json>                現在の状態を出力
  runs   [--limit N] [--recipe R] [--personas]  実行テレメトリ（.rig/runs.jsonl）の一覧・recipe 別集計・検証者別の票集計\n  party                              パーティ編成画面（/rig:party）＝テレメトリ/drill 実測から RPG 風ステータスを描画\n  run … --verifier-providers a,b,c   モデル混成クォーラム＝同じ検証 persona を異種プロバイダで並走（票は provider:persona）
  run … --isolate                    使い捨て git worktree に隔離して実行。ゲート green の commit だけ元 branch へ ff 合流、
                                     未達/dirty/非 ff は worktree と branch を保全（determinism-by-gate の空間版）。
                                     verifier ロールの CLI には読み取り専用権限を argv で固定付与（claude --allowedTools / codex --sandbox read-only）
  graph  [--json | --focus <name>]   shipped ブリック群から**型付きグラフ**（injects/extends/uses-*/mirrors 等11種）を導出。\n                                     手で書かない＝frontmatter が source of truth（validate check_graph が CI で整合を強制）\n  install-shim [--to PATH] [--force] ~/.local/bin/rig に shim を symlink（横断利用の入口・1回だけ）
  selftest                           決定論の自己検証（同入力→同遷移を証明）

依存: Python3 + PyYAML（validate.py と同じ）。終了コード 0=正常 / 1=エラー・ESCALATE。
"""

import sys

from .commands import (cmd_check, cmd_init, cmd_install_shim, cmd_next, cmd_party,
                       cmd_plan, cmd_run, cmd_runs, cmd_status, cmd_verdict)
from .providers import cmd_models, cmd_probe
from .queueing import cmd_queue
from .graph import cmd_graph
from .selftest import cmd_selftest

# ── エントリ ──────────────────────────────────────────────────────────────────
COMMANDS = {
    "plan": cmd_plan, "init": cmd_init, "check": cmd_check,
    "verdict": cmd_verdict, "next": cmd_next, "status": cmd_status,
    "run": cmd_run, "models": cmd_models, "probe": cmd_probe, "queue": cmd_queue,
    "runs": cmd_runs, "party": cmd_party, "graph": cmd_graph,
    "install-shim": cmd_install_shim, "selftest": cmd_selftest,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(0 if len(sys.argv) < 2 else 1)
    COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
