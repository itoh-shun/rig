# output-contract: review-findings

## output-contract: review-findings

`output-contracts/review-verdict` の**詳細版**。所見ごとに severity・証拠アンカー・影響・修正案を明示し、Blocking（マージ前必須）と Non-blocking（フォローアップ可）を分離する。`/rig:drill`（検出率測定は per-finding の severity と file:line が必須）と、`strict-senior-engineer` persona など「厳しめレビュー」を明示的に依頼された reviewer が使う。**review-gate の集約（APPROVE/REJECT 多数決）には `review-verdict` を使い続ける**——本 contract は所見の粒度を上げるための併用/代替オプションであり、既存の集約ロジックを置き換えない。

### 形式

```
## Blocking

### 1. <所見の短い要約>

- Severity: <Critical|High|Medium|Low>
- File: `path/to/file.ts:42`
- Impact: <この問題が引き起こす具体的な結果>
- Suggested fix: <具体的な修正の方向性>

### 2. <...>
- ...

## Non-blocking

### 1. <所見の短い要約>

- Severity: <Critical|High|Medium|Low>
- File: `path/to/file.ts:10`
- Suggested fix: <具体的な修正の方向性>

## 総合判定

判定: <APPROVE|REJECT|APPROVE_WITH_CONDITIONS>
確信度: <高|中|低>
```

### ルール

- **Blocking と Non-blocking は必ず分ける見出し**にする。該当なしのセクションは見出しごと省略する（両方なしなら「所見なし」の1行のみ）。
- 各所見は番号付き小見出し＋箇条書き。**Severity は 4 段階固定**（`Critical`/`High`/`Medium`/`Low`）。
- **File は `file:line`（範囲可）を必ず付ける**。証拠アンカーを示せない一般論・印象は所見にしない（`review-verdict` と同じ規律）。
- **Blocking の所見には Impact を必須**とする（なぜ見過ごせないかを1文で）。Non-blocking は Impact を省略してよい（Suggested fix のみでも可）。
- **Suggested fix は具体的な方向性**を書く。「直してください」のような無内容な指示は禁止。
- 末尾に `review-verdict` と同じ**総合判定＋確信度**を1回だけ付ける（Blocking が1件以上なら `REJECT`、Non-blocking のみなら `APPROVE_WITH_CONDITIONS`、所見なしなら `APPROVE`——`review-gate` の集約基準と同一）。
- **確信度 `低` の Blocking 所見は禁止**（`review-verdict` と同じ false-positive 制御）。低確信の懸念は Non-blocking へ回す。
- 誤検出（実在しない問題の指摘）を避けるため、確認できない推測は「情報不足」と明記し所見に含めない。
