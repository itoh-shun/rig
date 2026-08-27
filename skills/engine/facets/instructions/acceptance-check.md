# instruction: acceptance-check

**workbench recipe（`bugfix`/`feature`/`refactor`/`documentation` 等）の最終 step。** 「AI が『できました』と言うだけでは完了扱いにしない」（rig 指示書の核）を機械的に強制する薄い委譲層。判定ロジックを再実装せず、判定結果を `scripts/workbench.py gate` に記録するだけ（実体は `patterns/acceptance-gate` と同じ）。

## 正本は2つあり、要求しているのは片方だけ

これを取り違えると「recipe の一覧を全部埋めたのに accept できない」に必ず突き当たるので、先に書く。

* **タスクのゲート＝要求の正本。** `build_acceptance()` が `TASK_TYPES[task_type]` → `GATE_PRESETS`（＋ `.rig/gates.json` の `extra_criteria`、＋ 組織ポリシー）から `acceptance.json` を組む。**recipe は一切参照しない。** この集合が完全かつ拘束的で、`rig-wb wb accept` は1件でも `pending`/`failed` があれば拒否する（記録された `--force` がある場合を除く。`warning`/`skipped` は通り、`passed_with_warnings` として残る）。recipe 側からこの集合に足すことも引くこともできない——`wb gate --set` はゲートに無い名前を受け付けない。
* **recipe の `acceptance[]`＝作業一覧。** そのフローの step が自分で証拠を作る基準だけを並べたもの（#486 の規則）。**accept の条件ではない。** 宣言どおりに埋めれば残りが `pending` で残るのが**期待される状態**で、`wb accept` はそのとき何が足りないかを名指しで断る。

だから「recipe に15件並べる」は解決ではない。フローが証拠を作れない基準を宣言することになり、ゴム印か行き止まりを買う（#497 / #486）。

## 手順

### ① 判定対象の読み込み

`rig-wb wb gate <task_id>` を実行し、**そのタスクのゲートに並ぶ criterion 全件**を判定対象とする。ここが正本で、件数も名前もタスクごとに決まる。

自 step の `acceptance[]`（`"<criterion-id> — <日本語説明>"` 形式の文字列リスト）は、そのうち**このフローが自分で証拠を作る分**の作業一覧として読む。各エントリの ` — ` より前が criterion id（`rig-wb wb gates` の正本と一致させること。一致しない id は `rig-wb validate` が FAIL にする）。`acceptance[]` に無い残りは、operator が手で答えるか `warning`（未確認）として記録する——**黙って飛ばさない。**

### ② diff.md の作成（未作成なら先に書く）

`.rig/runs/<task_id>/diff.md` が無ければ、`facets/instructions/workbench-ops`「`/rig diff`」のテンプレート（`## Summary` / `## Risk` / `## Tests` / `## Unrelated diff`）に従って作成する。`diff_summary_written`（および `accept` の `diff_summary_generated` 要件）はこのファイルの存在が根拠。

### ③ 各基準の判定

criterion ごとに、これまでの step（inspect / implement / test / review-diff 等）の成果物・実行結果から根拠を集めて `passed` / `failed` / `warning` / `skipped`（該当なし）を判定する。**推測で `passed` にしない**——判定できない場合は `warning` にし detail に「未確認」と明記する（false-positive よりは自制側に倒す。`output-contracts/review-verdict` の確信度ルールと同じ思想）。

以下は**判定方法の辞書**であって、判定対象の一覧ではない。どれを判定するかは①で読んだタスクのゲートが決める。preset ごとの内訳をここに書き写さないのは、写した瞬間から `GATE_PRESETS` と別々に古びるからで、実際に一度そうなった（この節は11件を取りこぼしていて、そのうち2件は #497 が「どの bugfix タスクでも一度も set されない」と報告した基準そのものだった）。`GATE_PRESETS` の全 id にここで方法が書かれていることは `tests/test_recipe_acceptance_criteria.py` が検査する。

**依頼と差分の突き合わせで判定する**
- `task_intent_satisfied`：intake で確定した依頼の意図と成果物を突き合わせる。
- `no_unrelated_diff`：`rig-wb wb diff <task_id>` のファイル一覧を依頼スコープと突き合わせる。
- `fix_is_minimal`：diff が原因箇所に限定されているか（無関係な拡張がないか）。
- `no_unrelated_refactor`：修正・依頼に無関係なリファクタが混ざっていないか。
- `implementation_matches_requirement`：実装内容と AC を突き合わせる。
- `no_unintended_behavior_change`：`compare-behavior` step の突き合わせ結果。

**文書・記録の存在で判定する**
- `diff_summary_written`：②の `diff.md` が存在し `## Summary` を含むか確認する。
- `risk_summary_written`：`diff.md` の `## Risk` が書かれているか確認する。
- `requirement_summary_written`：clarify-requirements/intake で確定した AC が記録されているか。
- `bug_cause_identified`：reproduce/plan step で原因が特定されているか。
- `behavior_boundaries_identified`：`identify-behavior-boundaries` step の成果物があるか。
- `public_api_changes_documented`：公開 API 変更が diff.md/README 等で説明されているか。
- `public_api_changes_documented_if_any`：意図的な公開 API 変更があれば説明されているか（無ければ `skipped`）。
- `migration_or_backward_compatibility_considered`：既存データ・既存呼び出し元への影響を検討したか。

**テスト・型チェックの実行結果で判定する**
- `tests_pass_or_explained`：verify/test step の実行結果を見る。失敗があれば risk-based-testing の判断根拠が添えられているか確認する。
- `no_type_errors_or_explained`：verify step の型チェック結果をそのまま反映する。
- `tests_added_or_explained`：新規テストの有無、無ければ既存テストで担保される旨の明示確認。
- `regression_test_added_or_explained`：回帰テストの有無、無ければ不要な理由。
- `existing_behavior_preserved`：既存の正常系テストが green か。
- `tests_confirm_behavior_preserved`：`compare-behavior` step が挙動同一をテストで確認しているか。

**センサーの出力で判定する。** センサーは「該当あり」を鳴らすだけで `passed` を書かない——**鳴らなかったことを `passed` として記録するのはこの step の仕事**で、放っておくとゲートは `pending` のまま残る。
- `no_secret_leak`：`rig-wb wb scan-secrets <task_id>`。検出ゼロなら `passed`、検出ありで対応済みなら `passed`＋detail、未対応なら `failed`。
- `no_destructive_operation`：`rig-wb wb scan-destructive <task_id>`。同上。
- `no_injection_markers`：`rig-wb wb scan-injection <task_id>`。diff に混入したプロンプトインジェクション・マーカーを検出する。同上。
- `no_gate_tampering`：`rig-wb wb audit <task_id>`。ゲート定義・受け入れ記録そのものを緩める変更が diff に含まれていないか。含まれていれば `failed`（緩める理由が正当でも、この step が独断で `passed` にしてよい種類の判断ではない）。

**reviewer の出力の構造で判定する（review / security_review タスク）。** 内容の正しさではなく、`output-contracts/review-verdict` が要求する構造を満たしているかを見る。満たしていなければ `failed`——判定できないものは `warning`。
- `findings_are_concrete`：各指摘が再現手順か具体的な失敗シナリオを持つか（「〜な気がする」は `failed`）。
- `severity_labeled`：各指摘に severity が付いているか。
- `file_references_included`：各指摘が `file:line` を指しているか。
- `blocking_and_non_blocking_separated`：ブロッカーと改善提案が分けて書かれているか。
- `false_positive_risk_considered`：誤検知の可能性に触れているか、または確信度が明示されているか。
- `authn_authz_impact_checked`：認証・認可の境界に触れる変更を見たか、無ければ「無し」と明記されているか。
- `user_input_flow_checked`：外部入力の流入経路を追ったか。
- `secret_exposure_checked`：秘密情報の露出（ログ・エラー・レスポンス）を見たか。
- `unsafe_eval_or_shell_checked`：`eval`／シェル起動／動的 import の有無を見たか。
- `dependency_risk_checked`：依存の追加・更新の有無と、そのリスク評価。

### 任意基準（`.rig/gates.json`の`extra_criteria`経由で有効化）

以下は標準presetには含めない（過検知/低精度のリスクがあるため既定offとし、プロジェクトが`.rig/gates.json`の`extra_criteria`で明示的に該当preset/task_typeへ追加したときだけ判定する。`workbench.py gate --set <name>=...`は`extra_criteria`に登録済みの criterion 名しか受け付けない）。判定方法だけをここに定義しておく：

- `no_suspicious_code_similarity`（#274）：生成コードが既知の公開コードと酷似していないか。目視/検索で確認できる範囲でよい（専用ツールが無い場合はweb検索での類似コード確認や、ライセンス表記の要求されるコード片の混入がないかの確認に留める）。確証がない場合は`warning`にし、判断根拠をdetailに残す。
- `dependency_license_and_cve_checked`（#277）：package manifest（`package.json`/`pyproject.toml`等）に新規/更新依存があれば、そのライセンス種別が禁止リストに抵触しないか、既知の重大脆弱性（CVE）が無いかを確認する。依存の追加が無いtaskは`skipped`。
- `sast_findings_clear`（#276）：`scripts/sast_adapter.py <tool> <output.json> --apply <task_id>`で機械判定する（Semgrep等の出力をworst-case集約した1criterionとして反映）。ツール出力が無い場合は`skipped`。

### ④ 記録

```
rig-wb wb gate <task_id> --set <name>=<passed|failed|warning|skipped>[:<detail>]
```
を基準ごとに（まとめて複数 `--set` でも可）実行する。**①で読んだゲート全件について記録する**——自 step の `acceptance[]` に載っている分だけ記録して終わると、残りは `pending` のまま残り、`accept` がそこで止まる。判定できなかったものは飛ばさず `warning:未確認` として残す。

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
