---
name: de-ai-smell
description: 散文から AI 臭さを除去する。徴候カタログ(ai-writing-smells)を効かせ、ai-smell-reviewer が「具体/立場の欠如→誇張/過剰ヘッジ→枕詞/水増し→鋳型構造」の順に潰し、原意保持のまま無臭まで収束させる。
scope: shipped
steps:
  - id: de-ai-smell
    instruction: de-ai-smell
    pattern: serial
    gate: acceptance-gate
    acceptance: ["表層(A〜I)・深層(J〜P)とも指摘が 0、または直すと原意が変わる残置のみ", "深層: 還元不可能な具体(N)が在る/無ければ実例を要求して停止、人物・立場(P/E)が在る、形が不均一(J/M/O)", "原意・事実・ニュアンスが保持されている", "新たな鋳型を作っていない(K 強調乱用/L 演じた砕けが無い・逆AI臭なし)"]
    personas: [ai-smell-reviewer]
    output_contract: review-verdict
autonomy: interactive
---

# de-ai-smell

## 使う場面

AI に書かせた（または書かせ続けた）散文 — 記事・README・コミット文・PR 説明・SNS 投稿・LP コピー等 — の「機械くささ」を落として、人が書いたとしか思えない文に寄せたい時。`/rig:dev --recipe de-ai-smell "<ファイル or テキスト>"`。

## 展開

1. 対象テキストを収集する。
2. `ai-smell-reviewer` を起動し、`knowledge/ai-writing-smells`（徴候カタログ）を Knowledge 位置に注入する。各箇所を「引用→カタログ記号→なぜ→直し」で同定。
3. **原意保持を絶対条件**に、削除・具体化を優先して直列で書き直す（並列書き換えはしない）。中身が薄い箇所は捏造で埋めず要求として残す。
4. `acceptance-gate` で「指摘 0 or 説明可能な残置のみ・原意保持・逆 AI 臭なし」へ収束（未達は指摘反映で再走、収束しなければユーザーへ）。
5. 書き直し後テキスト全文＋残置理由を `review-verdict` と併せて返す。

## 設計メモ

- **knowledge=事実 / persona=判断**の分離に従う。臭いの定義（カタログ）を更新したいときは `ai-writing-smells` だけ直せば全レビューに効く。
- コードの AI-slop（自明コメント・過剰防御・dead code）は `adversarial-review` の担当。本 recipe は**散文**専用。
- 軽い変更には付けない（acceptance-gate は中身→トーン→表層→仕上げの順で、表層研磨だけの空転を避ける）。
