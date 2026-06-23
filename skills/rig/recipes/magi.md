---
name: magi
description: エヴァの MAGI を模した3賢者合議モード。提案を Melchior(科学者=正しさ)/Balthasar(母=守り)/Casper(女=価値)の3観点に並列で諮り、多数決で go/no-go を裁定する decision recipe。
scope: shipped
steps:
  - id: deliberation
    instruction: magi-deliberation
    pattern: parallel-fanout
    gate: magi-consensus
    personas: [magi/melchior, magi/balthasar, magi/casper]
    output_contract: magi-verdict
autonomy: interactive
---

# magi

> **モード pack 注記**: これは rig engine（`SKILL.md`）を dev / sales / talk / goal と**共用**する decision モードの recipe。engine は書き換えず、`magi/*` 3 persona・`magi-deliberation` instruction・`magi-consensus` pattern・`magi-verdict` output-contract を足すだけで成立する（engine 不変の継続実証）。`/rig:magi` の入口から起動する。

## 使う場面

**「これはやるべきか」「この案で行くか」を裁定したい**時。コードの逐条レビューではなく、**決定**を 3 つの直交した観点で多数決にかけたい時。例:

- 「この破壊的変更、入れていいか？」
- 「設計案 A と現状維持、どっち？」（案を議題に据える）
- 「このリスクある hotfix を今出すべきか？」
- 「この機能、そもそも作る価値があるか？」

`review-only`（security/design/test の品質レビュー）や `adversarial-review`（AI 臭除去）とは目的が違う。MAGI は**採否そのもの**を裁く。

## 3 号機（赤木ナオコ博士の3つの人格）

| 号機 | 人格 | 評価軸 | 問い |
|---|---|---|---|
| **MELCHIOR-1** | 科学者 | 正しさ・整合・実証 | 技術的に正しく、機能するか？ |
| **BALTHASAR-2** | 母 | 被害半径・可逆性・安定・将来負担 | 守るべきものを危険に晒さないか？ |
| **CASPER-3** | 女（自己） | 価値・問題の同定・単純さ・直感 | 本当にやる価値があるか？ |

3 軸は直交する：**正しくても（Melchior 可決）、危険なら（Balthasar 否決）、あるいは割に合わなければ（Casper 否決）通らない**。これが「正しいだけのコードが現実には通らない」を構造化する。

## 展開

1. **議題の確定** — 評価対象を1つに確定（曖昧なら1問だけ確認）。`--plan` なら諮問構成を提示して停止。
2. **並列諮問**（`parallel-fanout`）— 3 号機を独立 context で同時起動。互いの票を見ずに投票（`magi-verdict` 形式）。
3. **合議**（`magi-consensus`）— 多数決で判決。正準出力（MAGI コンソール）で提示。
   - 可決（3/3 or 2:1）→ 進行（2:1 は否決号機の懸念を保留事項に明示）
   - 条件付可決 → 統合条件を充足の前提に
   - 否決 → 停止して user へ
   - 審議継続 → 不足情報を問うて再合議（票は捏造しない）

手順本体は `facets/instructions/magi-deliberation` に従う。

## determinism-by-gate との関係

`magi-consensus` は多数決を**決定論的な判決表**で裁く集約ゲート（→ `patterns/magi-consensus`）。3 号機の生成（非決定的）を、固定した集計規則と正準出力で**毎回同じ構造の判決**へ収束させる。「賢者っぽい雰囲気」ではなく、機械抽出可能な go/no-go ゲートとして機能する。

## autonomy

- 既定 `interactive` — 判決を提示して確認。否決・審議継続では進めない。
- `--autonomous` — 判決後の後続委譲（可決時の実装等）で step ゲートを省くだけ。**否決・条件付・審議継続の判決そのものは尊重される**（合議ゲートは品質ゲートと同様 `--autonomous` で解除されない）。
