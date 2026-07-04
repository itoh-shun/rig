# instruction: workbench

**`/rig "<task>"` の統一入口。** 自由文のタスクを受け取り、①分類→②recipe 選択→③隔離 worktree での実行→④acceptance-gate 判定→⑤結果サマリの5段を、ユーザーが recipe や step を指定しなくても駆動する。中身は既存の PARSE→RESOLVE→COMPOSE→RUN（SKILL.md §3〜6）そのものだが、**状態・隔離・ゲート判定は `scripts/workbench.py`（決定論ランナー）に委ねる**（`patterns/computational-orchestration` と同じ「舵をコードに」思想）。

## 前提ブリック

| 部品 | 役割 |
|---|---|
| `patterns/isolated-worktree` | 隔離 worktree の設計・task-id・run state スキーマの正本 |
| `patterns/acceptance-gate` | 品質収束ループの一般原理 |
| `scripts/workbench.py` | task-id 発行・worktree 作成・gate 記録・accept/discard の実装 |
| `facets/instructions/workbench-ops` | `/rig status`\|`diff`\|`accept`\|`discard`\|`log` の手順 |
| `facets/instructions/gh-flow` | `/rig gh issue`\|`pr`\|`ci` の手順 |

## 手順

### ① タスク分類（task_type の決定）

自由文入力を読み、リポジトリ状態（`git status` / 変更されそうなファイルの軽い探索）も踏まえて以下から1つを選ぶ。

| task_type | 典型的な入力の手がかり |
|---|---|
| `bugfix` | 「直して」「動かない」「エラーが出る」「バグ」 |
| `feature` | 「追加して」「作って」「機能」「対応させて」 |
| `refactor` | 「整理」「責務を分ける」「リファクタ」「重複を消す」 |
| `review` | 「レビューして」「見て」「危なくないか確認」（対象が既存の変更/PR） |
| `test` | 「テストを書いて」「カバレッジ」「テストケース」 |
| `design` | 「設計して」「UI/UX」「画面案」 |
| `documentation` | 「README」「ドキュメント」「わかりやすく」「使い方を書いて」 |
| `security_review` | 「セキュリティ確認」「脆弱性」「認可/認証を見て」 |
| `performance` | 「遅い」「パフォーマンス」「N+1」「スケールしない」 |
| `investigation` | 「なぜ」「原因を調べて」「調査して」（直す/直さないが未確定） |
| `release_support` | 「リリース準備」「CHANGELOG」「バージョンを上げて」 |

**曖昧な場合**は SKILL.md §3「引数なし/曖昧な場合」の対話 composition にフォールバックする（何を・どのブリックを提案し・選ばせる）。分類結果と根拠は `.rig/runs/<task-id>/log.md` に1行で残す（例: `task_type: bugfix — 「ログインできない」「直して」から判定`）。

### ② recipe 選択

| task_type | 選択する recipe | 備考 |
|---|---|---|
| `bugfix` | `recipes/bugfix` | |
| `feature` | `recipes/feature` | |
| `refactor` | `recipes/refactor` | |
| `documentation` | `recipes/documentation` | |
| `test` | `recipes/test-design` → 続けて `recipes/bugfix`（実装が要る場合） | まずテストケース設計、実装が伴うなら bugfix/feature へ橋渡し |
| `design` | `recipes/design` | design pack へ委譲（native-first） |
| `review` | `recipes/review-only`（ローカル差分）／`recipes/pr-review`（既存 PR） | 新規 recipe を作らず既存を再利用（§8 Native-first） |
| `security_review` | `recipes/review-only` ＋ `--persona security-reviewer` 強制＋ security gate preset | reviewer 追加は §5 tier 解決と同じ経路 |
| `performance` | `recipes/bugfix` または `recipes/refactor` ＋ `--persona performance-reviewer` | 変更が主なら bugfix/refactor、未確定なら investigation |
| `investigation` | `recipes/debug`（実装まで進める場合）／read-only 調査のみなら `--no-worktree` | 「直すかどうか未確定」の間は worktree を作らず調査に留める |
| `release_support` | `recipes/release-flow` | 既存 recipe を再利用 |

選択理由を1行（`--reason` 相当）で `workbench.py new` に渡し `task.json.recipe_reason` に残す。**recipe 自動選択の理由が言えないまま実行しない**（§9.1 rationalization 表と同じ規律）。

### ③ 隔離 worktree での実行

1. **task 登録**：
   ```
   python3 scripts/workbench.py new "<input>" --type <task_type> --recipe <name> --reason "<選択理由>"
   ```
   （読み取り専用タスク＝`review` / `security_review` / `investigation` の調査段階は `--no-worktree` を付ける。)
   出力された `task_id` と `worktree_path` を以降の全 dispatch で使う。
2. **RUN**：選択した recipe を SKILL.md §5〜6（COMPOSE→RUN）どおりに合成・実行する。**subagent の作業ディレクトリを worktree_path に固定する**（context-minimal は維持：親は dispatch と集約のみ）。各 step 完了時に:
   ```
   python3 scripts/workbench.py step <task_id> --set <step-id>=passed|failed|skipped
   ```
   を記録する（run-status ヘッダの `step:` 表示と対応）。
3. **run-continuity**：SKILL.md §6 の run-status ヘッダ・step 境界バナー・stuck-guard は workbench RUN でも変わらず適用する。ヘッダの `recipe:` に加えて `task: <task-id>` を1項追加する：
   ```
   ▸ rig | task: <task-id> | recipe: <name> | step: <id> (<n>/<N>) | gate: <...> | mode: <...>
   ```

### ④ acceptance-gate 判定

基準 ID は task_type から機械的に決まる（`scripts/workbench.py gates` で正本を確認できる）。

| preset | criterion id | 意味 |
|---|---|---|
| **standard**（全 task_type 共通） | `no_unrelated_diff` | 依頼と無関係な差分が混ざっていない |
| | `tests_pass_or_reasonable_explanation` | テストが green か、失敗の合理的説明がある |
| | `no_type_errors` | 型エラーなし |
| | `no_lint_errors` | lint エラーなし |
| | `behavior_summary_written` | 挙動変更のサマリが書かれている |
| | `risk_summary_written` | リスクサマリが書かれている |
| **implementation**（bugfix/feature/refactor/test/performance/release_support に上乗せ） | `implementation_matches_request` | 実装が依頼内容と一致している |
| | `tests_added_or_existing_tests_confirmed` | テストを追加したか、既存テストで担保されることを確認した |
| | `public_api_changes_documented` | 公開 API 変更が説明されている |
| | `no_unrelated_refactor` | 依頼にない広範なリファクタが混ざっていない |
| | `no_secret_leak` | secret の混入がない |
| | `no_destructive_operation` | 破壊的操作（force push・DB drop 等）を含まない |
| **review**（review） | `concrete_findings_only` | 具体的な指摘のみ（一般論・印象論を含まない） |
| | `severity_labeled` | 各指摘に重大度が付与されている |
| | `file_and_line_references_included` | file:line の証拠アンカーがある |
| | `false_positive_risk_considered` | 誤検出リスクを検討したことが分かる |
| | `blocking_and_non_blocking_items_separated` | Blocking / Non-blocking が分離されている |
| **security**（security_review に review 上乗せ） | `input_validation_checked` | 入力検証を確認した |
| | `authz_authn_impact_checked` | 認可・認証への影響を確認した |
| | `secrets_not_exposed` | secret が露出していない |
| | `dependency_risk_checked` | 依存パッケージのリスクを確認した |
| | `unsafe_shell_or_eval_checked` | 危険な shell/eval 実行がないことを確認した |

各基準は根拠つきで判定し、記録する:
```
python3 scripts/workbench.py gate <task_id> --set no_lint_errors=pass --set tests_added_or_existing_tests_confirmed=warn:"既存テストのみで新規追加なし"
```
`fail` か `pending` が1件でも残る限り `workbench.py accept` はコードが拒否する（安全側に倒す。§9.1「AI が『できました』と言うだけでは完了扱いにしない」）。**warn は accept を止めないが警告として記録に残る**（未解決の重大警告は人が読める形で提示する）。

### ⑤ 結果サマリ

RUN 完了後、SKILL.md §6「フロー完了レポート」と同じ体裁に加えて、次を提示する:

```
## rig 完了: <task_id>
task_type: <type> | recipe: <name> | gate: <passed|warning|failed>

<フロー完了レポート テーブル>

次のアクション:
  /rig diff <task_id>     — 差分を確認する
  /rig accept <task_id>   — メイン作業ツリーへ反映する（gate 未達なら拒否される）
  /rig discard <task_id>  — worktree を破棄する
```

capture 提案（SKILL.md §7）は workbench RUN でも同様に適用する（承認必須・`--capture` 明示時のみ省略）。

## 安全側に倒すケース（accept を止める）

`facets/instructions/workbench-ops`（`/rig accept`）が機械的に強制するが、この instruction の時点でも次を検知したら **worktree の外に出さない**：

- 依頼にない unrelated diff がある
- テスト失敗の説明がない
- secret らしき文字列（API key / private key / token パターン）を検知した
- 破壊的操作（force push・DB drop・`rm -rf` 相当）を含む
- 認証・認可に関わる変更で review step が未実施
- 公開 API 変更が説明されていない

いずれかに該当したら該当 criterion を `fail` で記録し、理由を note に残す。
