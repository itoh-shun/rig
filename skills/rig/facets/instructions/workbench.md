# instruction: workbench

**`/rig "<task>"` の統一入口。** 自由文のタスクを受け取り、①分類→②recipe 選択→③隔離 worktree での実行→④acceptance-gate 判定→⑤結果サマリの5段を、ユーザーが recipe や step を指定しなくても駆動する。中身は既存の PARSE→RESOLVE→COMPOSE→RUN（SKILL.md §3〜6）そのものだが、**状態・隔離・ゲート判定は `scripts/workbench.py`（決定論ランナー）に委ねる**（`patterns/computational-orchestration` と同じ「舵をコードに」思想）。

## 前提ブリック

| 部品 | 役割 |
|---|---|
| `patterns/isolated-worktree` | 隔離 worktree の設計・task-id・run state スキーマの正本 |
| `patterns/acceptance-gate` | 品質収束ループの一般原理 |
| `scripts/workbench.py` | task-id 発行・worktree 作成・gate 記録・accept/discard・stats/review の実装（`gates` サブコマンドが基準 ID の正本） |
| `facets/instructions/workbench-ops` | `/rig status`\|`diff`\|`accept`\|`discard`\|`log`\|`stats` の手順 |
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

### ② recipe 選択とルーティング

| task_type | 選択する recipe | isolate（worktree） | 備考 |
|---|---|---|---|
| `bugfix` | `recipes/bugfix` | ✓ | |
| `feature` | `recipes/feature` | ✓ | |
| `refactor` | `recipes/refactor` | ✓ | |
| `documentation` | `recipes/documentation` | ✓ | |
| `test` | `recipes/test-design` → 続けて `recipes/bugfix`/`feature`（実装が要る場合） | ✓（実装が伴う場合） | まずテストケース設計、実装が伴うなら bugfix/feature へ橋渡し |
| `design` | `recipes/design` | ✗ | design pack へ委譲（native-first）。成果物は仕様書であり本体差分ではない |
| `review` | `recipes/review-only`（ローカル差分）／`recipes/pr-review`（既存 PR） | ✗ | 新規 recipe を作らず既存を再利用（§8 Native-first） |
| `security_review` | `recipes/review-only` ＋ `--persona security-reviewer` 強制＋ security gate preset | ✗ | reviewer 追加は §5 tier 解決と同じ経路 |
| `performance` | `recipes/bugfix` または `recipes/refactor` ＋ `--persona performance-reviewer` | ✓ | 変更が主なら bugfix/refactor、未確定なら investigation |
| `investigation` | `recipes/debug`（実装まで進める場合）／読み取りのみなら recipe なしで調査 | 直すと決まるまで ✗ | 「直すかどうか未確定」の間は worktree を作らず調査に留める |
| `release_support` | `recipes/release-flow` | ✓ | 既存 recipe を再利用 |

選択理由を1行（`--reason`）で `workbench.py new` に渡す。**recipe 自動選択の理由が言えないまま実行しない**（§9.1 rationalization 表と同じ規律）。

### ③ 隔離 worktree での実行

1. **task 登録＋選択理由の表示**：
   ```
   python3 scripts/workbench.py new "<input>" --type <task_type> --recipe <name> --reason "<選択理由>"
   ```
   （isolate ✗ の task_type は `--no-worktree` を付ける。）
   このコマンドの標準出力が**そのまま Phase 1 の選択理由バナー**になる（`▸ rig` / `task:` / `detected:` / `recipe:` / `mode:` / `gate:`）。**バナーを自分で書き直さず、コマンド出力をそのまま提示する**（散文の再現に頼らずコードの確定出力を見せる）。出力された `task_id` と `worktree_path` を以降の全 dispatch で使う。
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

基準 ID は task_type から機械的に決まる（正本は `scripts/workbench.py gates`）。プリセットは `standard`（全 task_type 共通）＋ task_type 別プリセットの合成。

| preset | criterion | 意味 |
|---|---|---|
| **standard**（全 task_type 共通） | `task_intent_satisfied` | 依頼の意図が満たされている |
| | `no_unrelated_diff` | 依頼と無関係な差分が混ざっていない |
| | `diff_summary_written` | 差分の要約が書かれている（`diff.md`。accept の必須条件） |
| | `risk_summary_written` | リスクサマリが書かれている |
| | `tests_pass_or_explained` | テストが green か、失敗の合理的説明がある |
| | `no_type_errors_or_explained` | 型エラーがないか、あれば説明がある |
| | `no_secret_leak` | secret の混入がない |
| | `no_destructive_operation` | 破壊的操作（force push・DB drop 等）を含まない |
| **bugfix**（bugfix・performance に上乗せ） | `bug_cause_identified` | 原因を特定した |
| | `fix_is_minimal` | 修正が最小限である |
| | `regression_test_added_or_explained` | 回帰テストを追加したか、不要な理由を説明した |
| | `existing_behavior_preserved` | 既存の正常系挙動を壊していない |
| | `no_unrelated_refactor` | 依頼にない広範なリファクタが混ざっていない |
| **feature**（feature・test に上乗せ） | `requirement_summary_written` | 要件のサマリが書かれている |
| | `implementation_matches_requirement` | 実装が要件と一致している |
| | `tests_added_or_explained` | テストを追加したか、理由を説明した |
| | `public_api_changes_documented` | 公開 API 変更が説明されている |
| | `migration_or_backward_compatibility_considered` | 移行・後方互換性を検討した |
| **refactor**（refactor に上乗せ） | `behavior_boundaries_identified` | 変えてはいけない挙動境界を特定した |
| | `no_unintended_behavior_change` | 意図しない挙動変化がない |
| | `tests_confirm_behavior_preserved` | テストが挙動不変を裏付けている |
| | `no_unrelated_refactor` | 依頼スコープを超えたリファクタが混ざっていない |
| | `public_api_changes_documented_if_any` | 意図的な公開 API 変更があれば説明されている |
| **review**（review） | `findings_are_concrete` | 具体的な指摘のみ（一般論・印象論を含まない） |
| | `severity_labeled` | 各指摘に重大度が付与されている |
| | `file_references_included` | file:line の証拠アンカーがある |
| | `blocking_and_non_blocking_separated` | Blocking / Non-blocking が分離されている |
| | `false_positive_risk_considered` | 誤検出リスクを検討したことが分かる |
| **security**（security_review に review 上乗せ） | `authn_authz_impact_checked` | 認証・認可への影響を確認した |
| | `user_input_flow_checked` | ユーザー入力の流れを確認した |
| | `secret_exposure_checked` | secret 露出がないか確認した |
| | `unsafe_eval_or_shell_checked` | 危険な shell/eval 実行がないことを確認した |
| | `dependency_risk_checked` | 依存パッケージのリスクを確認した |

`documentation`/`design`/`investigation`/`release_support` は `standard` プリセットのみを適用する（各 criterion の解釈は recipe 側の acceptance リストが文脈に合わせて言い換える。例: documentation の `tests_pass_or_explained` は「コマンド例の実行確認」を指す）。

各基準は根拠つきで判定し、記録する:
```
python3 scripts/workbench.py gate <task_id> --set no_type_errors_or_explained=passed --set tests_added_or_explained=warning:"既存テストのみで新規追加なし"
```
`failed` か `pending` が1件でも残る限り `workbench.py accept` はコードが拒否する（安全側に倒す。§9.1「AI が『できました』と言うだけでは完了扱いにしない」）。**warning は accept を止めないが警告として記録に残る**（未解決の重大警告は人が読める形で提示する）。gate 全体の状態は `passed` / `passed_with_warnings` / `failed` / `pending` / `skipped`（`scripts/workbench.py stats` の集計軸と同一）。

review 系タスク（`review`/`security_review`/`pr-review`）で reviewer persona の verdict が出たら、`workbench.py review <task_id> --set <persona>=<APPROVE|REJECT|APPROVE_WITH_CONDITIONS>` で記録する。これは gate 判定そのものではなく、`/rig:rig stats` の「verifier のゴム印検知」（REJECT ゼロが続く reviewer への警告）に使う観測データ。

### ⑤ 結果サマリ

RUN 完了後、SKILL.md §6「フロー完了レポート」と同じ体裁に加えて、次を提示する:

```
## rig 完了: <task_id>
task_type: <type> | recipe: <name> | gate: <passed|passed_with_warnings|failed|skipped>

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

いずれかに該当したら該当 criterion を `failed` で記録し、理由を detail に残す。
