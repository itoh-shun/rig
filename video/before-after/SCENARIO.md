# SCENARIO — rig before/after デモ（auteur 演出版・検閲済み）

> `/rig:scenario before/after --persona auteur/deconstructionist --persona auteur/humanist` の確定アウトプット。
> 検閲：`ai-smell-reviewer`（＋`ai-writing-smells`）× `sns-post-reviewer` × `engagement-reviewer` ×
> `auteur/deconstructionist` × `auteur/humanist` ＋ source 対応チェック → acceptance-gate。
> このシナリオが `web/before-after.html` / `video/before-after/`（`/rig:movie --hyperframes`）の設計図。

- 種別: before-after ／ 尺: 約46〜55秒 ／ 観客: 開発者
- ログライン: 同じ変更が、rig を通すだけで「気づけば、ちゃんとレビューされている」になる。
- 感情の弧: 夜の依頼（人）→ あるあるの痛み → **無音の一拍** → 解放 → 一息（人）→ CTA
- 目玉（hero beat・1つ）: **verdict を単独で溜めて「親に届くのは、判定行だけ。」**

## ビートシート（auteur 演出反映）

| # | ビート | 画面 | テロップ | VO（草案） | source |
|---|---|---|---|---|---|
| 1 | cold-open（人） | 暗がり | 金曜 21:43 /「この変更、見ておいて」 | 「金曜の夜。レビュー依頼が、ひとつ。」 | —（人で掴む） |
| 2 | before（共感） | 😮‍💨 | …で、どうやる？（あるある、だ） | 「あー、はいはい。」 | — |
| 3 | **screen(before)** | 端末(赤) | context 膨張(イメージ)/観点その場任せ/同種ミス反復 | 「context が膨らみ、観点はその場任せ。」 | context-minimal（§6/§9） |
| 4 | **間（無音）** | ほぼ黒・一行 | rig を通す。 | （無音の一拍） | —（断ち切り） |
| 5 | after | ✨ | コマンド、1つ。 | 「実作業は subagent へ。親は集約だけ。」 | context-minimal（§6） |
| 6 | **screen(after)** | 端末・`--plan` | steps: 1 ・RUN しない | 「まず構成だけ、ドライランで。」 | `--plan`（§5） |
| 7 | **HERO・screen** | 端末・verdict を溜めて | …親に届くのは、判定行だけ。 | 「3観点を並列で。手元に来るのは、判定だけ。」 | review-only / parallel-fanout / review-verdict |
| 8 | payoff（人・一息） | 😌 | 気づけば、ちゃんとレビューされている。 | 「気づけば、ちゃんとレビューされている。」 | context-minimal の体感 |
| 9 | cta | コマンド | /rig:dev --only review | 「まずは1コマンド。」 | review-only |

## 検閲ログ（auteur 再生成で通過した修正）

- **[engagement]** 魅せ場が均一 → **s7 verdict を hero beat 化**（単独で溜めて置く・尺を最長に）。
- **[auteur:deconstruction]** before→after が滑らか過ぎ → **s4 に無音の一拍（間）**を挿入し断ち切る／verdict をモンタージュに溶かさず単独で。
- **[auteur:humanist]** 人がいない → **s1 を機能でなく開発者の夜で**始め／**s8 に一息つく payoff**／before は嘲笑でなく“あるある”の共感で。
- **[ai-smell / sns-post / source]** 前回修正済みで PASS（偽精度・断定・空ワードなし）。

→ 再走で全観点 APPROVE・acceptance-gate 充足。**盛らずに、見せ方・順番・間で**面白く・温かくした（誇張ゼロ）。

## CTA
`/rig:dev --only review` — 同じ変更が、毎回ちゃんとレビューされる。
