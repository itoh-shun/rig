# instruction: run-report

**フロー完了レポートと実行テレメトリの出力仕様の正本。** SKILL.md §6 は「全 step 完了後に完了レポートを出し `.rig/runs.jsonl` に1行追記する」という規律だけを持ち、レポートのフィールド定義・スライス/`--skip` 時の変形・テレメトリ JSON のスキーマはこのファイルが正本。**RUN を締めるとき（全 step が完了・escalation・skip で終わったとき）は必ずこれを読んで従う。**

`--plan`（事前・`facets/instructions/plan`）と完了レポート（実績）はヘッダ・step テーブルが同一フォーマットになるよう設計されている——ドライランから完了後まで機械的に比較できることが目的なので、片方だけを変えない。

## フロー完了レポート（#102）

全 step が完了（または escalation/skip で終了）した後、次の正準フォーマットでフロー全体のサマリを出力する。`autonomy: autonomous` では**必須**（step ゲートがなくフローが一気に走るため、完了後に事後確認できる唯一の集約情報）。`autonomy: interactive` では各 step ゲートで結果を都度確認しているが、同じフォーマットで集約サマリとして出力する（`--plan`（事前）との対称構造を保つ）。

```
## rig フロー完了

recipe: <name[tier]> | autonomy: <interactive|autonomous> | backend: <manual|workflow>[| tdd: on][| no-defaults: on][| orchestrate: on][| cross-llm: on][| no-capture: on][| adversarial: on][| visual: on][| orchestrate: off][| design: on][| review: on][| capture: on]
steps: <N> 完了 / <M> スキップ / <K> エスカレーション

| step      | outcome                           | gate                              |
|-----------|-----------------------------------|-----------------------------------|
| intake    | ✓ done                            | —                                 |
| design    | [SKIP] condition-OFF (size S/M)   | —                                 |
| implement | ✓ done                            | —                                 |
| verify    | ✓ done                            | acceptance-gate passed (try 2/2)  |
| review    | ✓ done                            | acceptance-gate passed (try 1/2)  |
| pr        | ✓ done                            | —                                 |
| merge     | ✓ done                            | —                                 |
```

- `outcome`：`✓ done`（正常完了）/ `[SKIP] <理由>`（condition-OFF または `--skip` 指定。`--plan` の `[SKIP: --skip flag]` と同じ語彙）/ `[ESCALATED]`（stuck-guard または acceptance-gate K 超エスカレーション発動）
- `gate`：acceptance-gate を通った step は `acceptance-gate passed (try N/K)`（N=実試行回数、K=`max_retries`）。review-gate を通った step は `review-gate passed`。ゲートなしは `—`。
- ヘッダの `steps: N 完了 / M スキップ / K エスカレーション` でフロー全体の集計を1行で示す。
- **モード修飾子（#132, #137, #172, #174, #178, #182, #184, #186）**：`| tdd: on` / `| no-defaults: on` / `| orchestrate: on` / `| cross-llm: on` / `| no-capture: on` / `| adversarial: on` / `| visual: on` / `| orchestrate: off` / `| design: on` / `| review: on` / `| capture: on` はそれぞれ対応する recipe キーまたはフラグが有効な場合のみ付加する（`--plan` ヘッダと同じ条件・同じ表記。無効時は省略）。`| orchestrate: off` は `no_orchestrate: true` または `--no-orchestrate` が有効な場合のみ（#178・#186）。`| design: on` は `design: true` または `--design` が有効な場合のみ（#182・#186）。`| review: on` は `review: true` または `--review` が有効な場合のみ（#182・#186）。`| capture: on` は `capture: true` または `--capture` が有効な場合のみ（#184）。`--plan`（予定）と完了レポート（実績）の recipe ヘッダが同一フォーマットになり、ドライランから完了後まで機械的に比較できる。
- `--plan`（実行前）のテーブルと対称構造：`--plan` が「予定」、このレポートが「実績」として対応する（`--plan` のテーブルを参照することでそのまま比較できる）。

**`--from`/`--to`/`--only` スライス指定時（#108, #141）**：`--plan --from`/`--to`/`--only` と対称的に、テーブルには**スライス後の step のみ**を表示し、ヘッダに `slice:` フィールドを追加する。

```
## rig フロー完了

recipe: release-flow | autonomy: interactive | backend: manual
slice: implement → end
steps: 4 完了 / 0 スキップ / 0 エスカレーション

| step      | outcome | gate                             |
|-----------|---------|----------------------------------|
| implement | ✓ done  | —                                |
| verify    | ✓ done  | acceptance-gate passed (try 1/2) |
| pr        | ✓ done  | —                                |
| merge     | ✓ done  | —                                |
```

- スライス前の step（`--from` 開始前の step・`--to` 終端後の step、または `--only` 対象外の step）は**テーブルに出さない**（`--plan --from`/`--to`/`--only` と同じ）。
- ヘッダの `steps: N 完了 / M スキップ / K エスカレーション` は**スライス後の step のみ**をカウントする（スライス前の step は含まない）。
- `slice:` フィールドの書式：`--from <id>` なら `<id> → end`、`--to <id>` なら `start → <id>`、`--from <A> --to <B>` なら `<A> → <B>`、`--only <id>` なら `<id> only`。
- `--from`/`--to`/`--only` と `--skip` の組み合わせ時は `slice:` と `skip:` を**両方**ヘッダに出す（`--plan` の `#88` と同じ対称規則）。スライス前の step が `--skip` 対象だった場合もテーブル行は表示しない（スライス外のため行が無い）。

**`--skip` 単独指定時（#120）**：`--skip` 単独指定（`--from`/`--only` なし）でフローが完了したとき、完了レポートのヘッダに `skip: <step-id(s)>` 行を追加する（`--plan` の #50 と同一形式・`, ` 区切り）。`slice:` がない場合は `steps:` 集計行の前に配置する。`slice:` がある場合は上記組み合わせルールのとおり `slice:` の後に配置する。`--skip` 指定がない場合は `skip:` 行を省略する（既存の挙動と同じ）。これで `--plan`（予定）と完了レポート（実績）の `skip:` フィールドが対称になり、機械パーサーが同一構造として処理できる。

```
## rig フロー完了

recipe: release-flow | autonomy: interactive | backend: manual
skip: design, review
steps: 5 完了 / 2 スキップ / 0 エスカレーション

| step    | outcome              | gate |
|---------|----------------------|------|
| intake  | ✓ done               | —    |
| design  | [SKIP] --skip flag   | —    |
| ...
```

## 実行テレメトリ（`.rig/runs.jsonl` への追記）

フロー完了レポートを出力した後、**同じサマリを1行 JSON として `<cwd>/.rig/runs.jsonl` に追記**する（orchestrate バックエンドは `scripts/orchestrate.py` の `telemetry_append` が自動追記するため、**manual / workflow バックエンドの RUN のみ**この規則で追記する）。回を重ねるごとに「どの recipe が何回・どれだけリトライして・どこでエスカレーションしたか」が集計可能になり、reviewer/gate の効き具合をデータで剪定できる。

```json
{"ts": "<ISO8601>", "recipe": "<name>", "backend": "manual", "final": "DONE|ESCALATE|STOPPED", "steps_total": N, "steps_passed": N, "retries": N, "escalated_at": "<step-id>|null", "failure_mode": "<taxonomy-code>", "steps": [{"id": "...", "status": "passed|skipped|escalated", "retries": N}]}
```

- **これは capture（§7）ではない**：run-state.json と同格の**実行ログ**であり knowledge 層への書き込みではないため、**承認不要**（§7.3 のゲート対象外・`--no-capture` の影響も受けない）。`.rig/` は gitignore 済み。
- フィールドは orchestrate の `telemetry_append` と同形（`backend` だけ `manual`/`workflow`）。review/acceptance ゲートを通った step は `steps[].verdicts[]` に検証者別の票（`{"by": "<reviewer名>", "ok": true|false}`）も記録する（分かる範囲で・省略可）。
- **失敗の型付け（`failure_mode`）**：ESCALATE/BLOCKED で終わった run には、`classify_failure` が state から決定論的に導く MAST 系の失敗コード（例 `verification:missing`／`verification:self-grading`／`verification:incorrect-implementation`）を **`failure_mode` として加算**記録する（成功 run には出ない・省略）。コード語彙と「本来どのゲート/ブリックが捕まえるべきだったか」の写像は **`patterns/failure-taxonomy`** が正本＝失敗分布を recipe/gate 設計への差し戻し信号にする。集計・一覧は **`orchestrate runs [--limit N] [--recipe R]`**、検証者別の票と**剪定ヒント**（5票以上で REJECT ゼロ＝ゴム印化の疑い）は **`runs --personas`**。
- 書き込みに失敗する環境（read-only 等）では**サイレントにスキップ**し、フロー完了レポート自体は通常どおり出す（telemetry は best-effort）。
