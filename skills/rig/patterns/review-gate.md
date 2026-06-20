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
