---
name: pre-mortem
description: 事前検死。マージ/リリース前に「この変更が本番で壊れた」前提で失敗モードを逆算し、各々に最小ガードレールを対で出す decision-support recipe。magi（やるか）の補完で「どう壊れるか」を担当。
scope: shipped
steps:
  - id: pre-mortem
    instruction: pre-mortem
    pattern: serial
    personas: [pre-mortem-analyst]
    output_contract: premortem-report
autonomy: interactive
---

# pre-mortem

> **モード pack 注記**: rig engine（`SKILL.md`）を magi 等と**共用**する humor pack の recipe。engine は書き換えず、`pre-mortem-analyst` persona・`pre-mortem` instruction・`premortem-report` output-contract を足すだけで成立する。`/rig:pre-mortem` から起動。

## 使う場面

リスクある変更を**出す前に**、失敗モードを先回りで炙り出したい時。例:

- 「この DB 移行、マージ前に落とし穴を洗いたい」
- 「破壊的変更を出す前に、何が壊れるか先に知りたい」
- 「この設計、本番で詰むパターンある?」

## なぜ機能するか（prospective hindsight＝事前の後知恵）

「壊れるかも」と問うより、「**もう壊れた。なぜ?**」と断定形で逆算する方が、人は失敗モードを具体的に多く挙げられる（意思決定研究で実証）。`pre-mortem-analyst` はこの時制を固定し、技術・運用・データ/セキュリティ・波及の各軸で検死する。

## magi との対（go/no-go と how-it-breaks）

| | pre-mortem | magi |
|---|---|---|
| 問い | **どう壊れるか** | やるか（go/no-go） |
| 出力 | 失敗モード＋ガードレール | 多数決判定 |
| 使い所 | 出す前の保険洗い | 採否の裁定 |

magi に諮る前の材料（特に Balthasar＝守りの判断）／magi 可決後の最終保険として組み合わせると効く。

## 展開

1. **対象の確定** — 変更/PR/計画を1つに（1問だけ確認可）。本番影響（auth・migration・security 等）を効かせる。
2. **検死** — `pre-mortem-analyst` を dispatch。「もう壊れた」前提で失敗モードを断定形で逆算。
3. **構造化提示** — `premortem-report`（総合リスク＋可能性×影響ランク＋各モードにガードレール＋最も安く効く1手）。
4. **接続** — ガードレールの実装は `/rig:dev`（テスト/フラグ/段階導入）等へ委譲（pre-mortem は炙り出しまで）。

手順本体は `facets/instructions/pre-mortem`、出力は `output-contracts/premortem-report` に従う。

## ガード

- **断定形**で書く（発見率が上がる）・**各モードに最小ガードレールを対で**（恐怖の羅列にしない）。
- 捏造禁止（実際に起こりうるものだけ）。低×低の空想は載せない。
