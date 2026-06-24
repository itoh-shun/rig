# STORYBOARD — rig: before / after（auteur 演出版・HyperFrames）

ログライン: 同じ変更が、rig を通すだけで「気づけば、ちゃんとレビューされている」になる。
尺: 約46秒 ／ 1920×1080 ／ 音楽: 夜の静けさ → 不穏(before) → 無音の一拍 → 解放(after) → 余韻
composition id: `rig-ba`（`window.__timelines["rig-ba"]`）
演出: `engagement` ＋ `auteur/deconstructionist`（間・形式破壊）＋ `auteur/humanist`（人の中心・一息）
目玉（hero beat）: **s7 verdict — 単独で溜めて、「親に届くのは、判定行だけ。」**

| # | 区間(s) | 種別 | 画面 | テロップ | VO | BGM/SE |
|---|---|---|---|---|---|---|
| s1 | 0–4.5 | cold-open（人） | 暗がり | 金曜 21:43 /「この変更、見ておいて」 | 「金曜の夜。レビュー依頼が、ひとつ。」 | 夜の静けさ |
| s2 | 4.5–7.7 | before（共感） | 😮‍💨 | …で、どうやる？（あるある、だ） | 「あー、はいはい。」 | 不穏 |
| s3 | 7.7–14.7 | **screen(before)** | 端末(赤) | context 膨張(イメージ)/観点その場任せ/同種ミス反復 | 「親の context が膨らみ、観点はその場任せ。」 | ノイズ |
| s4 | 14.7–17.7 | **間（無音）** | ほぼ黒・一行 | rig を通す。 | （無音の一拍） | 無音→低音 |
| s5 | 17.7–20.7 | after | ✨ | コマンド、1つ。 | 「実作業は subagent へ。親は集約だけ。」 | 転調 |
| s6 | 20.7–27.2 | **screen(after)** | 端末・`--plan` | steps: 1 ・RUN しない | 「まず構成だけ、ドライランで。」 | キー音 |
| s7 | 27.2–36.2 | **HERO・screen** | 端末・verdict を溜めて | …親に届くのは、判定行だけ。 | 「3観点を並列で。手元に来るのは、判定だけ。」 | ヒット→静寂 |
| s8 | 36.2–40.7 | payoff（人・一息） | 😌 | 気づけば、ちゃんとレビューされている。 | 「気づけば、ちゃんとレビューされている。」 | 余韻 |
| s9 | 40.7–45.7 | cta | コマンド | /rig:dev --only review | 「まずは1コマンド。」 | 締め |

## ソース対応表（誇張防止・全ビートが実機能の裏打ち）
- s3（before）→ context-minimal が解く課題（§6 red flags / §9）
- s6（screen）→ `--plan` ドライラン（§5）
- s7（HERO）→ 3-way 並列レビュー＋`review-verdict`（`recipes/review-only` / `parallel-fanout` / `review-gate`）
- s8（payoff）→ context-minimal の体感（親は判定だけ・diff は subagent 側）

## 実画面ショット（必須）
s3 / s6 / s7 は seekable モック端末。**実録に格上げ**するなら、実際の rig 実行の画面収録 mp4 を `assets/` に置き各シーンを `<video class="clip" … src="assets/*.mp4">` に差し替える（s7 verdict を実録にすると hero beat が最強）。
