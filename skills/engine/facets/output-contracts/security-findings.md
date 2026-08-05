# output-contract: security-findings

## output-contract: security-findings

能動的セキュリティ探索（`security-audit` / `pentest-fix` の探索フェーズ）の所見フォーマット。`review-findings` の攻撃者版で、各所見に**攻撃シナリオ・最小 PoC・証拠アンカー**を強制し、**確認済み（Confirmed）と未確認（Suspected＝情報不足）を分離**する。捏造した脆弱性が「発見」として通るのを構造的に防ぐ。

### 形式

```
## Confirmed（PoC が刺さった＝再現可能）

### 1. <脆弱性の短い要約>

- Severity: <Critical|High|Medium|Low>
- CWE: <CWE-89 等（該当すれば）>
- File: `path/to/file.py:42`
- Attack scenario: <誰が・何を送ると・何が起きるか（1行）>
- PoC: <最小の入力値と、観測される異常挙動。破壊的手口は書かない>
- Root cause: <なぜ刺さるか＝構造的原因>
- Suggested fix: <canonical な修正の方向性（弥縫策でなく）>

## Suspected（未確認・要追加調査＝情報不足）

### 1. <疑わしい点>
- File: `path/to/file.py:10`
- Why suspected: <怪しい理由>
- What's missing: <確認に必要な情報／未読の領域>

## 総合判定

判定: <REJECT|APPROVE_WITH_CONDITIONS|APPROVE>
確信度: <高|中|低>
```

### ルール

- **Confirmed と Suspected は必ず分ける見出し**にする。該当なしのセクションは見出しごと省略（両方なしなら「所見なし」の1行）。
- **Confirmed に入れてよいのは PoC が実際に刺さった所見だけ。** 「刺さりそう」は Suspected 行き。**確信度 `低` の Confirmed 所見は禁止**。
- 各 Confirmed 所見は **Attack scenario（1行）・PoC・File(`file:line`)・Root cause・Suggested fix を必須**とする。1つでも書けないなら Suspected に降格する。
- **Severity は 4 段階固定**（`Critical`/`High`/`Medium`/`Low`）。到達に認可を要する／前提が厳しいものは段位を下げる。
- **Suggested fix は canonical な構造修正**を書く（パラメタライズ・認可検査・CSPRNG 等）。「入力を弾く」等の弥縫策を修正案にしない。
- **PoC に破壊・持ち出し・永続化の手口を書かない。** 「刺さる経路がある」ことを最小限で示すに留める（例: 任意コマンドが argv に混入する、で止める）。
- 総合判定は末尾に1回：Confirmed が1件以上あれば `REJECT`、Suspected のみなら `APPROVE_WITH_CONDITIONS`、所見なしなら `APPROVE`（`review-gate` の集約基準と同一）。
