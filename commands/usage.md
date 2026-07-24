---
description: "rig/usage — rig-wb がいつどこから何回使われたかを一発集計する。既定は cwd の .rig/runs.jsonl（プロジェクト単位）、--global で ~/.rig/runs.jsonl（全プロジェクト横断・project 別）。invoker 別に rig-wb 経由か素の直呼びかを見分ける。"
argument-hint: "[--global | -g] [--limit N] [--json]"
---

# rig/usage — 使用実態の集計

**まず `rig:engine` skill を Skill ツールで起動し、その SKILL.md（context-minimal・知識層・§6 run-continuity）に従うこと。** このコマンドは入口であり、実処理は `rig-wb usage`（`rig_workbench/cli.py`）にある（重複定義しない）。

起動後、次の引数を PARSE して `rig-wb usage` に渡す:

```
$ARGUMENTS
```

## やること

**Bash ツール経由**で次のいずれかを実行する（`rig-wb` が入っていれば前者、無ければ後者にフォールバック）:

```bash
rig-wb usage $ARGUMENTS
# 未インストール時:
python3 -m rig_workbench.cli usage $ARGUMENTS   # rig-repo 内
python3 "$RIG_HOME/rig_workbench/cli.py" usage $ARGUMENTS
```

出力は `.rig/runs.jsonl` を読んだ invoker 別の集計:

- `◆ rig-wb/<version>` … rig-wb CLI 経由で回った run
- `direct (rig-wb 未経由)` … `scripts/*.py` を直呼びした run
- `--global` 時は **プロジェクト別** セクションも表示

## flag

- `--global` / `-g` — 集計対象を `~/.rig/runs.jsonl`（全プロジェクト横断）に切り替える。既定は cwd の `.rig/runs.jsonl`。
- `--limit N` — 集計対象を直近 N 件に絞る（例: `--limit 100`）。
- `--json` — 機械可読出力（`scope` / `runs_path` / `total` / `by_invoker` / `last_seen_by_invoker` / `--global` 時は `by_project`）。CI や dashboard から叩くとき用。

## 例

```
/rig:usage                       # cwd の .rig/runs.jsonl・text 出力
/rig:usage --global              # 全プロジェクト横断・text 出力
/rig:usage --global --limit 100  # 直近 100 件だけ集計
/rig:usage --json                # 機械可読
/rig:usage --global --json       # 機械可読・横断
```

## こんな時に使う

- **install したけど使ってるか自信ない** — `/rig:usage --global` で「◆ rig-wb 経由: N 回 / 全体 M 回」の比率が出る。
- **どのプロジェクトから叩かれてるか知りたい** — `/rig:usage --global` の「プロジェクト別」セクションで来歴が見える。
- **CI や dashboard から集計したい** — `--json` で機械可読出力。
- **どの版から使われてるか** — `by_invoker` が `rig-wb/1.6.0` / `rig-wb/1.5.0` / 将来 `rig-codex/0.1.0` を別カウントする。

## run-continuity（SKILL.md §6）

RUN 中は各ターン冒頭に次の run-status ヘッダを1行必ず再掲すること。中断・質疑・tool 出力の直後でも省かない（可視化＝駆動の証拠）:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
