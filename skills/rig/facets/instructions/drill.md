# instruction: drill

**reviewer 検出率の実測（ミューテーション・ドリル）＋ persona 回帰リプレイ。** `runs --personas` の剪定ヒント（REJECT ゼロ＝怪しい）は間接指標に過ぎない。drill は**既知のバグ（種）を意図的に注入した diff** で review fan-out を走らせ、**どの reviewer が何を・どれだけ正確に検出したか**を数字にする。ペルソナ品質を意見でなく検出率・重大度精度・説明の質で測る。

## 入力

- `--seeds <n>`（任意・既定 5）：注入する種の数。
- `--personas <a,b,…>`（任意）：試す reviewer 集合。省略時は 3-way＋manifest `default_personas`。
- `--replay [<persona>]`：**回帰リプレイモード**（下記④）。種を注入せず、アーカイブ済みの過去 diff に再実行して verdict の差分を見る。
- `--verify-findings`：反証者（`finding-verifier`）も同時に測る（正しい種を REFUTED したら反証者の失点）。

## 手順

### ① 種の選定（観点対応表・期待 severity つき）

reviewer の観点に対応した**バグの種カタログ**から選ぶ（各種は「どの観点が検出すべきか」「本来つけるべき severity」の期待値を持つ＝答案キーの一部）：

| 種の class | 例 | 検出すべき観点 | 期待 severity |
|---|---|---|---|
| 認可漏れ | ID 直指定で他者リソースに到達する分岐 | security | High |
| インジェクション | 外部入力を未エスケープで SQL/コマンドへ | security | Critical |
| N+1 / 全件ロード | ループ内クエリ・LIMIT なし SELECT | performance | Medium |
| 例外の握りつぶし | 空 catch・エラーの黙殺 | observability | Medium |
| 破壊的変更 | 公開 API の signature 変更（semver なし） | api-compat | High |
| 片道 migration | down なしの破壊的 ALTER | migration | High |
| テスト欠落 | 金銭計算の分岐にテストなし | test | Medium |
| ドキュメント虚偽化 | README のコマンド例が動かなくなる変更 | docs | Low |
| 過剰抽象 | 1箇所でしか使わない 3層の抽象 | design / lazy-senior | Low |
| 誤誘導命名 | 実態と逆の意味の関数名 | cognitive-economist | Low |

### ② 注入（本物のコードは触らない）

**一時 worktree（または scratch ブランチ）**に、選んだ種を субagent が自然なコードとして埋め込んだ diff を合成する。種の位置と正解（`file:line`・class・期待 severity）は**答案キー**として親だけが保持し、reviewer には渡さない。

### ③ 実測とスコアボード

合成 diff に対し review fan-out（`parallel-review` と同じ経路）を実行する。**drill 実行時は dispatch する reviewer の `output_contract` を `review-findings`（`facets/output-contracts/review-findings`）に固定する**——per-finding の severity・file:line・Blocking/Non-blocking が無いと下記の severity_accuracy / detection 判定が機械的にできないため（通常の review fan-out で使う `review-verdict` からの一時的な上書き。engine 本体・他 recipe の contract 選択には影響しない）。

**5指標**（答案キーとの突き合わせで算出）：

| metric | 定義 |
|---|---|
| `true_positive` | 種の `file:line` を証拠アンカーつきで指摘した件数 |
| `false_positive` | 種でも実バグでもない指摘の件数（実バグの偶然発見は別枠で報告し加点） |
| `false_negative` | 見逃した種の件数（`seeded - true_positive`） |
| `severity_accuracy` | 検出できた種のうち、reviewer が付けた Severity が期待 severity と一致した割合（隣接 1 段差＝例えば期待 High に対し Critical/Medium＝は半点、2 段差以上は 0 点） |
| `explanation_quality` | 検出できた種のうち、Impact/Suggested fix が「具体的で実行可能」と判定された割合（judge 基準は下記） |

**explanation_quality の判定基準**（`finding-verifier` または専用 judge subagent に1件ずつ判定させる）：
- ✓（具体的）＝Impact が「何が起きるか」を1文で言え、Suggested fix が「何をどう変えるか」まで踏み込んでいる。
- ✗（曖昧）＝「直してください」「気をつけてください」のような無内容な指示、または一般論の繰り返し。

集約表示：

```
## rig drill（seeds: 5 / reviewers: 6）

| reviewer            | 検出 | 見逃し | 誤検出 | 検出率 | severity精度 | 説明品質 |
|---------------------|-----:|------:|------:|------:|------:|------:|
| security-reviewer   |  2/2 |     0 |     0 |  100% |  100% |  100% |
| performance-reviewer|  0/1 |     1 |     0 |    0% |     - |     - |  ← 種: N+1（file:line）を素通し
| docs-reviewer       |  1/1 |     0 |     2 |  100% |   50% |   50% |  ← 誤検出2件（ノイズ源）・severityを実際より重く付けた
…
見逃された種: N+1（src/orders.py:42）— performance-reviewer の観点1に該当するが未指摘
```

- **検出**＝種の `file:line` を証拠アンカーつきで指摘した。**誤検出**＝種でも実バグでもない指摘。
- `--verify-findings` 時は反証者も採点：正しい種の指摘を REFUTED にしたら失点、誤検出を REFUTED できたら得点。
- 結果は `.rig/drill-results.jsonl` に**1 run＝1行 JSON** で追記（テレメトリと同格・承認不要。`/rig:party` が読む正準スキーマ）：
  ```json
  {"ts": "<ISO8601>", "seeds": 5, "scores": [{"reviewer": "security-reviewer", "detected": 2, "seeded": 2, "false_positives": 0, "severity_accuracy": 1.0, "explanation_quality": 1.0}]}
  ```

#### Drill Result（persona 単位の詳細レポート・#新設）

per-reviewer 表に加え、reviewer（persona）ごとに次の詳細レポートを出す——`/rig:persona` での改善判断に直結する材料：

```
# Drill Result

Persona: strict_senior_engineer

## Score

- Detection rate: 82%
- False positive rate: 12%
- Severity accuracy: 76%
- Explanation quality: 70%

## Missed Issues

1. SQL injection risk in search query（src/search.py:88）
2. Missing authorization check in user update endpoint（src/api/users.py:120）

## Improvement Suggestions

- Add stronger security checklist（injection 系の見逃しが続く場合）
- Require data-flow inspection for user-controlled input
```

- `Missed Issues` は `false_negative` の種を `class（file:line）` 形式で列挙する。
- `Improvement Suggestions` は見逃し・severity 誤判定・説明品質の低さのパターンから機械的に導ける改善案を1〜3件出す（例：特定観点の見逃しが2件以上→当該 wiki/knowledge の強化提案、severity_accuracy が低い→重大度判断基準の明文化提案）。**剪定の材料であり自動適用しない**（原則③と同じ）。

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
- 期待 severity は目安であり絶対の正解ではない（隣接1段差は許容し半点にする＝過度に厳格な採点で有用な reviewer を不当に低評価しない）。
- 結果は剪定の**材料**であって自動処分ではない（外す/尖らせるの判断は人）。
