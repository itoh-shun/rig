# instruction: acceptance-check

**workbench recipe（`bugfix`/`feature`/`refactor`/`documentation` 等）の最終 step。** 「AI が『できました』と言うだけでは完了扱いにしない」（rig 指示書の核）を機械的に強制する薄い委譲層。判定ロジックを再実装せず、判定結果を `scripts/workbench.py gate` に記録するだけ（実体は `patterns/acceptance-gate` と同じ）。

## 手順

### ① 基準の読み込み

自 step の `acceptance[]`（`"<criterion-id> — <日本語説明>"` 形式の文字列リスト）を読む。各エントリの ` — ` より前が **criterion id**（`scripts/workbench.py gates` の正本と一致させること）。

### ② 各基準の判定

criterion ごとに、これまでの step（inspect / implement / test / review_diff 等）の成果物・実行結果から根拠を集めて `pass` / `fail` / `warn` を判定する。**推測で `pass` にしない**——判定できない場合は `warn` にし note に「未確認」と明記する（false-positive よりは自制側に倒す。`output-contracts/review-verdict` の確信度ルールと同じ思想）。

- `no_unrelated_diff`：`workbench.py diff <task_id>` のファイル一覧を依頼スコープと突き合わせる。
- `tests_pass_or_reasonable_explanation`：verify/test step の実行結果を見る。失敗があれば risk-based-testing の判断根拠が添えられているか確認する。
- `no_type_errors` / `no_lint_errors`：verify step の出力をそのまま反映する。
- `behavior_summary_written` / `risk_summary_written`：`diff.md` にその節が書かれているか確認する（なければこの instruction が今書く）。
- `implementation_matches_request`：intake で確定した AC と実装内容を突き合わせる。
- `tests_added_or_existing_tests_confirmed`：新規テストの有無、無ければ既存テストで担保されるとの明示確認があるか。
- `public_api_changes_documented` / `no_unrelated_refactor` / `no_secret_leak` / `no_destructive_operation`：diff を精査し該当なしなら `pass`、該当ありで対応済みなら `pass`＋note、未対応なら `fail`。

### ③ 記録

```
python3 scripts/workbench.py gate <task_id> --set <id>=<pass|fail|warn>[:<note>]
```
を基準ごとに（まとめて複数 `--set` でも可）実行する。

### ④ 表示

SKILL.md §6「acceptance-gate criterion 単位の合否表示」と同じ体裁で提示する：

```
── step acceptance ▸ gate: acceptance-gate <pending (try N/K)|passed>
   ✓ no_unrelated_diff
   ✗ no_lint_errors （3 errors found）
   ⚠ tests_added_or_existing_tests_confirmed （既存テストのみで新規追加なし）
   → lint エラーを修正して再試行
```

`fail` が1件でもあれば `patterns/acceptance-gate` の収束ループ（`max_retries` まで再試行 → 未達なら user エスカレーション）に従う。`warn` のみ（`fail` 0件）は gate を通す（`workbench.py accept` も許可するが警告として記録に残る）。
