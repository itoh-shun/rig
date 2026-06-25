---
name: harness-audit
description: プロジェクトの「エージェント開発ハーネス」を 2×2(計算的/推論的 × ガイド/センサー)で棚卸しし、空象限と「あるのに効いていない資産」(lint/testがループ外・ルールがprose止まり)を炙り出す自己監査 recipe。足すより繋ぐ・強制する・薄くするを出す。
scope: shipped
steps:
  - id: audit
    instruction: harness-audit
    pattern: serial
    personas: [harness-auditor]
    output_contract: harness-map
autonomy: interactive
---

# harness-audit

> **モード pack 注記**: rig engine（`SKILL.md`）を dev / goal / test-design と**共用**する診断 pack の recipe。engine は書き換えず、`harness-auditor` persona・`harness-taxonomy` knowledge・`harness-audit` instruction・`harness-map` output-contract を足すだけで成立する。`/rig:harness` から起動。

## 使う場面

「エージェントで開発する仕組み（ハーネス）」が**ちゃんと効いているか**を点検したい時。例:

- 「うちのリポジトリ、AI 開発の足回りに穴ある？」
- 「テストも lint もあるのに、なぜか品質が安定しない」（＝ループに繋がっていない疑い）
- 「CLAUDE.md にルールを足し続けているが効いているのか分からない」

## 何を見るか（2×2）

| | ガイド（先回り） | センサー（事後・検知） |
|---|---|---|
| **計算的** | 型/LSP/scaffold/CLI | **lint・型・テスト・build・CI** |
| **推論的** | CLAUDE.md・Skills・persona | AI レビュー・review-gate |

`harness-taxonomy` の優先順位で穴を出す。最頻・最重は **計算的センサーがループ外**（test/lint があるのに hook/acceptance-gate に繋がっていない）。

## 展開

1. **対象確定** — 既定はカレントリポジトリ（`--plan` で監査構成を提示して停止）。
2. **棚卸し＋分類** — `harness-taxonomy` を注入して `harness-auditor` を dispatch。ハーネス要素を 2×2 に分類し、空象限と「あるのに効いていない」資産を見る。長い設定は subagent へ（context-minimal）。
3. **構造化提示** — `harness-map`（総合行＋2×2 表＋穴〔重い順・各に手〕＋最優先で繋ぐ1手）。
4. **接続** — 穴を埋める実装は委譲：機械検証を `acceptance-gate` 基準へ、生成と検証の分離は `policies/independent-verification`/`/rig:goal`、ルールの強制は hook 設定、修正は `/rig:dev`。監査自体は read-only。

手順本体は `facets/instructions/harness-audit`、観点は `harness-taxonomy`、出力は `output-contracts/harness-map` に従う。

## ガード

- **「ある」と「効いている」を区別**（存在≠強制）。prose 止まりのルールは未強制扱い。
- **足すより繋ぐ・強制する・薄くする**（善意のルール追加の逆効果・Context Rot を警戒）。
- **計算的センサーを一次**に推す。**根拠は具体箇所**・未確認は「未確認」（捏造しない）。read-only。
