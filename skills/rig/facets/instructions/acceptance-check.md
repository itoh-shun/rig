# instruction: acceptance-check

**workbench recipe（`bugfix`/`feature`/`refactor`/`documentation` 等）の最終 step。** 「AI が『できました』と言うだけでは完了扱いにしない」（rig 指示書の核）を機械的に強制する薄い委譲層。判定ロジックを再実装せず、判定結果を `scripts/workbench.py gate` に記録するだけ（実体は `patterns/acceptance-gate` と同じ）。

## 手順

### ① 基準の読み込み

自 step の `acceptance[]`（`"<criterion-id> — <日本語説明>"` 形式の文字列リスト）を読む。各エントリの ` — ` より前が **criterion id**（`scripts/workbench.py gates` の正本と一致させること）。

### ② diff.md の作成（未作成なら先に書く）

`.rig/runs/<task_id>/diff.md` が無ければ、`facets/instructions/workbench-ops`「`/rig diff`」のテンプレート（`## Summary` / `## Risk` / `## Tests` / `## Unrelated diff`）に従って作成する。`diff_summary_written`（および `accept` の `diff_summary_generated` 要件）はこのファイルの存在が根拠。

### ③ 各基準の判定

criterion ごとに、これまでの step（inspect / implement / test / review-diff 等）の成果物・実行結果から根拠を集めて `passed` / `failed` / `warning` / `skipped`（該当なし）を判定する。**推測で `passed` にしない**——判定できない場合は `warning` にし detail に「未確認」と明記する（false-positive よりは自制側に倒す。`output-contracts/review-verdict` の確信度ルールと同じ思想）。

**standard プリセット（全 task_type 共通）**
- `task_intent_satisfied`：intake で確定した依頼の意図と成果物を突き合わせる。
- `no_unrelated_diff`：`workbench.py diff <task_id>` のファイル一覧を依頼スコープと突き合わせる。
- `diff_summary_written`：②の `diff.md` が存在し `## Summary` を含むか確認する。
- `risk_summary_written`：`diff.md` の `## Risk` が書かれているか確認する。
- `tests_pass_or_explained`：verify/test step の実行結果を見る。失敗があれば risk-based-testing の判断根拠が添えられているか確認する。
- `no_type_errors_or_explained`：verify step の型チェック結果をそのまま反映する。
- `no_secret_leak` / `no_destructive_operation`：diff を精査し該当なしなら `passed`、該当ありで対応済みなら `passed`＋detail、未対応なら `failed`。

**bugfix プリセット（bugfix・performance）**
- `bug_cause_identified`：reproduce/plan step で原因が特定されているか。
- `fix_is_minimal`：diff が原因箇所に限定されているか（無関係な拡張がないか）。
- `regression_test_added_or_explained`：回帰テストの有無、無ければ不要な理由。
- `existing_behavior_preserved`：既存の正常系テストが green か。
- `no_unrelated_refactor`：修正に無関係なリファクタが混ざっていないか。

**feature プリセット（feature・test）**
- `requirement_summary_written`：clarify-requirements/intake で確定した AC が記録されているか。
- `implementation_matches_requirement`：実装内容と AC を突き合わせる。
- `tests_added_or_explained`：新規テストの有無、無ければ既存テストで担保される旨の明示確認。
- `public_api_changes_documented`：公開 API 変更が diff.md/README 等で説明されているか。
- `migration_or_backward_compatibility_considered`：既存データ・既存呼び出し元への影響を検討したか。

**refactor プリセット（refactor）**
- `behavior_boundaries_identified`：`identify-behavior-boundaries` step の成果物があるか。
- `no_unintended_behavior_change` / `tests_confirm_behavior_preserved`：`compare-behavior` step の突き合わせ結果。
- `no_unrelated_refactor`：依頼スコープを超えた変更がないか。
- `public_api_changes_documented_if_any`：意図的な公開 API 変更があれば説明されているか（無ければ `skipped`）。

review/security プリセット（`findings_are_concrete` 等）は review 系タスクの `review-diff`/`parallel-review` step 自体が output-contract で構造を強制するため、acceptance-check は reviewer の出力がその構造を満たしているかだけを確認する。

### 任意基準（`.rig/gate-extensions.json`経由で有効化・#283の実例）

以下は標準presetには含めない（過検知/低精度のリスクがあるため既定offとし、プロジェクトが`.rig/gate-extensions.json`で明示的に追加したときだけ判定する）。判定方法だけをここに定義しておく：

- `no_suspicious_code_similarity`（#274）：生成コードが既知の公開コードと酷似していないか。目視/検索で確認できる範囲でよい（専用ツールが無い場合はweb検索での類似コード確認や、ライセンス表記の要求されるコード片の混入がないかの確認に留める）。確証がない場合は`warning`にし、判断根拠をdetailに残す。
- `dependency_license_and_cve_checked`（#277）：package manifest（`package.json`/`pyproject.toml`等）に新規/更新依存があれば、そのライセンス種別が禁止リストに抵触しないか、既知の重大脆弱性（CVE）が無いかを確認する。依存の追加が無いtaskは`skipped`。
- `sast_findings_clear`（#276）：`scripts/sast_adapter.py <tool> <output.json> --apply <task_id>`で機械判定する（Semgrep等の出力をworst-case集約した1criterionとして反映）。ツール出力が無い場合は`skipped`。

### ④ 記録

```
python3 scripts/workbench.py gate <task_id> --set <name>=<passed|failed|warning|skipped>[:<detail>]
```
を基準ごとに（まとめて複数 `--set` でも可）実行する。

### ⑤ 表示

SKILL.md §6「acceptance-gate criterion 単位の合否表示」と同じ体裁で提示する：

```
── step acceptance ▸ gate: acceptance-gate <pending (try N/K)|passed|passed_with_warnings>
   ✓ no_unrelated_diff
   ✗ no_type_errors_or_explained （3 errors found）
   ⚠ tests_added_or_explained （既存テストのみで新規追加なし）
   → 型エラーを修正して再試行
```

`failed` が1件でもあれば `patterns/acceptance-gate` の収束ループ（`max_retries` まで再試行 → 未達なら user エスカレーション）に従う。`warning`/`skipped` のみ（`failed` 0件）は gate を通す（`workbench.py accept` も許可するが `passed_with_warnings` として記録に残る）。
