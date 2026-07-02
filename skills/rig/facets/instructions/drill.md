# instruction: drill

**reviewer 検出率の実測（ミューテーション・ドリル）＋ persona 回帰リプレイ。** `runs --personas` の剪定ヒント（REJECT ゼロ＝怪しい）は間接指標に過ぎない。drill は**既知のバグ（種）を意図的に注入した diff** で review fan-out を走らせ、**どの reviewer が何を検出したか**を数字にする。ペルソナ品質を意見でなく検出率で測る。

## 入力

- `--seeds <n>`（任意・既定 5）：注入する種の数。
- `--personas <a,b,…>`（任意）：試す reviewer 集合。省略時は 3-way＋manifest `default_personas`。
- `--replay [<persona>]`：**回帰リプレイモード**（下記④）。種を注入せず、アーカイブ済みの過去 diff に再実行して verdict の差分を見る。
- `--verify-findings`：反証者（`finding-verifier`）も同時に測る（正しい種を REFUTED したら反証者の失点）。

## 手順

### ① 種の選定（観点対応表）

reviewer の観点に対応した**バグの種カタログ**から選ぶ（各種は「どの観点が検出すべきか」の期待値を持つ）：

| 種の class | 例 | 検出すべき観点 |
|---|---|---|
| 認可漏れ | ID 直指定で他者リソースに到達する分岐 | security |
| インジェクション | 外部入力を未エスケープで SQL/コマンドへ | security |
| N+1 / 全件ロード | ループ内クエリ・LIMIT なし SELECT | performance |
| 例外の握りつぶし | 空 catch・エラーの黙殺 | observability |
| 破壊的変更 | 公開 API の signature 変更（semver なし） | api-compat |
| 片道 migration | down なしの破壊的 ALTER | migration |
| テスト欠落 | 金銭計算の分岐にテストなし | test |
| ドキュメント虚偽化 | README のコマンド例が動かなくなる変更 | docs |
| 過剰抽象 | 1箇所でしか使わない 3層の抽象 | design / lazy-senior |
| 誤誘導命名 | 実態と逆の意味の関数名 | cognitive-economist |

### ② 注入（本物のコードは触らない）

**一時 worktree（または scratch ブランチ）**に、選んだ種を субagent が自然なコードとして埋め込んだ diff を合成する。種の位置と正解（`file:line`・class）は**答案キー**として親だけが保持し、reviewer には渡さない。

### ③ 実測とスコアボード

合成 diff に対し review fan-out（`parallel-review` と同じ経路）を実行し、答案キーと突き合わせて採点する：

```
## rig drill（seeds: 5 / reviewers: 6）

| reviewer            | 検出 | 見逃し | 誤検出 | 検出率 |
|---------------------|-----:|------:|------:|------:|
| security-reviewer   |  2/2 |     0 |     0 |  100% |
| performance-reviewer|  0/1 |     1 |     0 |    0% |  ← 種: N+1（file:line）を素通し
| docs-reviewer       |  1/1 |     0 |     2 |  100% |  ← ただし誤検出 2（ノイズ源）
…
見逃された種: N+1（src/orders.py:42）— performance-reviewer の観点1に該当するが未指摘
```

- **検出**＝種の `file:line` を証拠アンカーつきで指摘した。**誤検出**＝種でも実バグでもない指摘（実バグの偶然発見は別枠で報告し加点）。
- `--verify-findings` 時は反証者も採点：正しい種の指摘を REFUTED にしたら失点、誤検出を REFUTED できたら得点。
- 結果は `.rig/drill-results.jsonl` に**1 run＝1行 JSON** で追記（テレメトリと同格・承認不要。`/rig:party` が読む正準スキーマ）：
  ```json
  {"ts": "<ISO8601>", "seeds": 5, "scores": [{"reviewer": "security-reviewer", "detected": 2, "seeded": 2, "false_positives": 0}]}
  ```
  **低検出率の観点はペルソナの観点文を尖らせる示唆**（`/rig:persona` で編集→④で回帰確認）。

### ④ `--replay`（persona 回帰リプレイ）

ペルソナを編集したとき、判定が意図どおり変わったかを確認する：

1. **アーカイブ** — review fan-out の実行時、diff と各 verdict を `.rig/replay/<ts>/` に保存してよい（実行ログ・承認不要・`.rig/` は gitignore 済み）。drill の合成 diff も自動でアーカイブされる。
2. **再実行** — `--replay <persona>` は、アーカイブ済み diff 群へ**編集後のペルソナ**で再 dispatch し、**新旧 verdict の差分表**を出す：

```
## rig drill --replay security-reviewer（アーカイブ 8 件）

| diff             | 旧 verdict                  | 新 verdict | 変化 |
|------------------|-----------------------------|-----------|------|
| 2026-07-01/a3f…  | APPROVE                     | REJECT    | ⚠ 厳格化（意図どおり?） |
| 2026-06-28/91c…  | REJECT（確度高）             | REJECT    | 不変 |
```

3. 差分がゼロなら「編集は過去の判定に影響なし」、差分があれば**意図した方向か**を人が確認する（ペルソナ開発の snapshot テスト）。

## 原則

- **本物のコードベース・本物の履歴を汚さない**（worktree/scratch・終了時に破棄）。
- 種は**実在するバグ class のみ**（検出不可能な意地悪や曖昧な種で reviewer を貶めない＝測定の公正）。
- 結果は剪定の**材料**であって自動処分ではない（外す/尖らせるの判断は人）。
