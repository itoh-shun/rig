# pattern: review-gate

並列レビュー（`pattern: parallel-fanout`）で収集した複数の verdict を集約し、着手可否を機械的に決定するゲートパターン。

## 概要

N 個のレビュアー subagent から返ってきた verdict（APPROVE / APPROVE_WITH_CONDITIONS / REJECT）を集計し、オーケストレーターが**一意の着手判断**を下す。判断は集約表に従って決定論的に行う。

## 集約判断表

| 集計結果 | 着手判断 | 対応 |
|---|---|---|
| 全員 APPROVE（N/N） | **即着手** | 条件なし。実装フェーズへ進む。 |
| APPROVE_WITH_CONDITIONS が1件以上、REJECT が0件 | **条件統合して着手** | 全レビュアーの条件を統合し、必須条件を満たしてから着手する。 |
| REJECT が1件以上 | **保留** | REJECT 理由を user に提示し、方針確認を求める。 |

APPROVE_WITH_CONDITIONS が複数ある場合は条件をすべて収集して統合する（後述）。

## 統合 conditions の扱い

条件は「マージ前必須」と「フォローアップ可」の2種類に分類される（→ `output-contracts/review-verdict` 参照）。

| 条件種別 | 扱い |
|---|---|
| **マージ前必須**（複数レビュアーから） | すべて実装に含める。1件でも未対応のまま着手しない。 |
| **フォローアップ可** | 別 Issue として起票し、今回の着手には含めない。 |
| **矛盾する条件**（複数レビュアーが相互に矛盾する要求） | user に提示して確認を求める。独断で解決しない。 |

## 敵対的検証（`--verify-findings`・opt-in）

reviewer を増やすほど false-positive（もっともらしいが間違った所見）も増える。`--verify-findings`（または recipe `verify_findings: true`）が有効なとき、**集約判断表を適用する前に所見の反証段を挟む**：

1. **対象の抽出** — 全 verdict から **REJECT の根拠**と**マージ前必須条件**を1件ずつ取り出す（APPROVE とフォローアップ可は対象外＝ゲートを止めないため検証コストをかけない）。
2. **反証 dispatch** — 各所見を `finding-verifier`（agent 優先・persona フォールバック）に**1所見=1 subagent** で渡し、反証を試みさせる（`parallel-fanout` で並列可）。verifier は所見を出した reviewer とは別 subagent（採点者≠生成者の所見版）。
3. **フィルタ** — `REFUTED`（証拠つきで反証された）の所見はゲートに**通さない**（棄却理由と反例アンカーを1行ログとして親に残す＝サイレントに消さない）。`UPHELD` / `UNRESOLVED` は通す（**疑わしきは所見の利**＝棄却は確証がある時だけ）。
4. **再集計** — フィルタ後の verdict 集合で集約判断表を適用する。REJECT の根拠が全件 REFUTED された verdict は APPROVE_WITH_CONDITIONS 相当に降格し、その旨を明示する。

- **使いどき**：reviewer が多い run（`default_personas` 常時投入・`--persona` 複数・adversarial 併用）や、REJECT の誤爆が高くつく本番系フロー。軽い 3-way には既定 OFF（軽さ既定）。
- **verifier 自身の規律**：反例にも証拠アンカー必須・棄却率を稼がない（`facets/personas/finding-verifier` 参照）。verifier の票もテレメトリ（`steps[].verdicts[]`・`runs --personas`）に `finding-verifier` として記録し、棄却の質を後から監査できるようにする。

## オーケストレーターの実行手順

1. `pattern: structured-report` の形式で出力された各 subagent の結果から**判定行**（`判定:` で始まる行）を抽出する。
2. 判定を集計し、上記の集約判断表を参照して着手判断を決定する。
3. APPROVE_WITH_CONDITIONS がある場合は全条件を収集し、必須／フォローアップ／矛盾の3分類に仕分ける。
4. 矛盾がなければ統合条件を確定し、実装フェーズへ進む。矛盾がある場合は user へ確認する。
5. REJECT がある場合は REJECT 理由と残債を user にまとめて提示し、次のアクションを委ねる。

## 関連ブリック

- `pattern: parallel-fanout` — 並列レビュー dispatch（このパターンの前段）
- `pattern: structured-report` — 判定の機械抽出を可能にする出力縛り
- `output-contracts/review-verdict` — 判定語彙・条件形式の共通定義
