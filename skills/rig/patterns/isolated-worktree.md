# pattern: isolated-worktree

**AI の変更は、accept されるまで本体の作業ツリーに触れない。** workbench（`/rig "<task>"`）の RUN は原則として task ごとの独立 git worktree の中で行い、`acceptance-gate` を通過した差分だけを、ユーザーの明示操作（`/rig accept`）でメイン作業ツリーへ反映する。determinism-by-gate（`patterns/acceptance-gate`）の「品質の収束」に、**空間の隔離**（失敗しても本体が汚れない）を重ねる安全パターン。

## 仕組み

1. **task 登録** — `scripts/workbench.py new "<input>" --type <task_type> --slug <slug>` が task-id を発行し、`.rig/runs/<task-id>/` に run state を初期化、worktree と作業 branch を作成する。
2. **隔離実行** — implement / verify 等の全 step を worktree の中で実行する（subagent への dispatch 時に worktree path を作業ディレクトリとして明示する）。メイン作業ツリーには一切書かない。
3. **ゲート判定** — `workbench.py gate <task-id> --set <criterion>=<pass|fail|warn>` で基準ごとの合否を記録する。**fail か pending が1つでもあれば accept はコードが拒否する**（散文の自制ではなくランナーが強制）。
4. **反映 or 破棄** — `accept` は branch をメイン作業ツリーへ **squash merge（staged・コミットなし）**する＝最終確定は必ず人（またはユーザーが明示した commit 操作）に残す。`discard` は worktree / branch を削除し、run log だけを残す。

## task-id と配置

| 項目 | 値 |
|---|---|
| task-id 形式 | `rig-YYYYMMDD-HHMMSS-<shortslug>`（例: `rig-20260704-153012-login-fix`） |
| worktree path | `<repo 親>/rig-worktrees/<repo-name>/<task-id>`（env `RIG_WORKTREE_ROOT` で上書き可） |
| 作業 branch | `rig/<task-id>`（起動時の HEAD から作成・base branch / base commit を task.json に記録） |
| run state | `<repo>/.rig/runs/<task-id>/`（discard 後も残る。`/rig:init` が導入先リポジトリの `.gitignore` への `.rig/` 追加を提案する。rig プラグイン自身のリポジトリでは既に gitignore 済み） |

## run state ファイル（正準スキーマは `scripts/workbench.py`）

```
.rig/runs/<task-id>/
  task.json        # task_id / input / task_type / recipe(+選択理由) / base_branch / base_commit /
                   # branch / worktree_path / status / created_at（スクリプトが管理）
  steps.json       # 実行 step の進行状態（workbench.py step --set <step>=<status>）
  acceptance.json  # gate プリセット・基準ごとの合否・判定結果（workbench.py gate）
  plan.md          # 実装計画（モデルが書く）
  diff.md          # 差分の散文要約: 仕様変更の有無・既存挙動への影響・テスト・リスク（モデルが書く）
  log.md           # 実行ログ: 分類根拠・recipe 選択理由・主要決定（モデルが書く）
  final.md         # 最終サマリ（モデルが書く）
```

`task.json` の `status` 遷移: `running → gate_passed|gate_failed → accepted|discarded`（accept 済み run の後片付け discard は `accepted` を保持し worktree だけ消す）。

## 必須要件

- 実行ごとに**独立した worktree** を作る（使い回さない）。
- **元 branch（base_branch）と base commit を必ず記録**する（accept 時の差分基準・conflict 診断に使う）。
- worktree path を run log（task.json）に保存する。
- **accept されるまで本体に反映しない**。accept は gate pass が前提（`--force` は記録つきの明示例外）。
- discard は task-id 明示＋変更ファイル一覧の提示＋確認（`--yes`）の三段。**run log は消さない**。

## 使わない場合（worktree なし RUN）

読み取り専用のタスク（review / security_review / investigation の一部）は差分を作らないため `--no-worktree` で worktree を省略してよい。その場合 `diff` はメイン作業ツリーの現状差分を対象にし、`accept` は対象なしとして拒否される。

## 既存ブリックとの関係

| 部品 | 役割 |
|---|---|
| `patterns/acceptance-gate` | 「何を満たせば合格か」の品質収束ループ（時間軸の保証） |
| **isolated-worktree（本 pattern）** | 「合格するまで本体に触れない」空間の隔離（空間軸の保証） |
| `scripts/workbench.py` | 状態・worktree・gate 判定・accept/discard の決定論ランナー（舵をコードが握る） |
| `facets/instructions/workbench` | `/rig "<task>"` 統一入口の手順（分類・recipe 選択・RUN の駆動） |
| manifest `worktree.*` | プロジェクト単位の worktree 運用フラグ（§4.1） |
