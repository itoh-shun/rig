# instruction: workbench-ops

**`/rig status` / `/rig diff` / `/rig accept` / `/rig discard` / `/rig log`** の手順。実体は全て `scripts/workbench.py`（`patterns/isolated-worktree` 参照）への薄い委譲で、本ファイルは**表示の整形と安全確認の追加**だけを担う。判定・状態管理をここで再実装しない（§8 Native-first）。

## 共通ルール

- サブコマンドの引数に `task_id` が省略された場合、`workbench.py` は `.rig/runs/` 内の**最新 task**を自動選択する。複数 task が並行している可能性がある場合（`workbench.py log --limit 5` で確認）は、曖昧さを避けるため task_id を明示するようユーザーに促す。
- どのサブコマンドも**親 context に長い diff 本文を引き込まない**（context-minimal）。`workbench.py diff` の出力（ファイル一覧＋shortstat）はそのまま見せてよいが、個々のコード片の要約は `diff.md`（RUN 中にモデルが書いた散文）を参照する。

## `/rig status [<task_id>]`

```
python3 scripts/workbench.py status [<task_id>]
```

出力（task-id・作業ブランチ/worktree path・実行中/完了済み step・gate 状態・未反映の差分・次アクション）をそのままユーザーに提示する。整形の追加は不要（スクリプトの出力が正準フォーマット）。

## `/rig diff [<task_id>]`

```
python3 scripts/workbench.py diff [<task_id>]
```

機械抽出部分（変更ファイル一覧・shortstat・未コミット警告）はスクリプト出力をそのまま見せる。加えて、以下の**散文サマリ**を `.rig/runs/<task_id>/diff.md` から読み込む（RUN 中に implement/verify step が書いていない場合はこの時点で1回だけ生成し、書き込んでよい＝承認不要のログ扱い。§6 run テレメトリと同格）:

- 重要な差分の要約（何を変えたか、1〜3行）
- 仕様変更の有無（あれば具体的に）
- 既存挙動への影響（後方互換か破壊的か）
- テスト追加・変更の有無
- リスクのある変更（該当なければ「なし」と明記）

## `/rig accept [<task_id>] [--force]`

```
python3 scripts/workbench.py accept [<task_id>]
```

**accept 前に必ず**:
1. `workbench.py diff <task_id>` の内容（上記5項目を含む）をユーザーに要約提示する。
2. gate が `pending`/`failed` の場合、スクリプトはエラーで拒否する（exit 1）。**`--force` は安全側のガードレールを外す明示操作**であり、以下を満たさない限り提案しない：
   - ユーザーが未達基準を確認した上で明示的にリスクを許容している
   - `--force` 使用は `task.json.forced: true` として記録される旨を伝える
3. gate が `warning`（`warn` 判定の criterion が残っている）場合も accept 自体はスクリプトが許可するが、**未解決の警告を要約提示してから**実行する。

accept 成功後（squash merge → **staged**・コミットはしない）:
- `git diff --staged` で確認できる旨と、コミットは人（またはユーザーの明示指示）が行う旨を案内する。
- 後片付け（`/rig discard <task_id>`）が worktree/branch のみを消し run log を残すことを案内する。

## `/rig discard [<task_id>] [--yes]`

```
python3 scripts/workbench.py discard <task_id>
```

**task_id を省略してはならない**（最新 task の自動選択は誤爆リスクが高いため discard だけは明示必須。スクリプト側も `--yes` なしの1回目呼び出しでは変更ファイル一覧を表示するだけで実際には削除しない＝プレビュー）。

1. 1回目は `--yes` なしで呼び、破棄対象の変更ファイル一覧を提示する。
2. ユーザーに確認を取ってから `--yes` を付けて再実行する。
3. 完了後、「worktree/branch は削除したが run log（`.rig/runs/<task_id>/`）は残る」旨を明示する。

## `/rig log [--limit N] [--json]`

```
python3 scripts/workbench.py log --limit <N>
```

出力（task id・実行日時・入力タスク・recipe・gate 結果）をそのまま提示する。「選択された recipe」「実行 step」「最終状態」「変更ファイル一覧」のうち log 一覧に出ない詳細（実行 step 一覧・変更ファイル一覧）が要る場合は、該当 task の `status <task_id>` / `diff <task_id>` を続けて呼ぶよう案内する（1コマンドに詰め込みすぎない・既存サブコマンドの再利用）。
