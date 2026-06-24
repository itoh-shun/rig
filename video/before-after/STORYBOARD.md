# STORYBOARD — rig: before / after（HyperFrames）

ログライン: 同じ変更が、rig を通すだけで「毎回ちゃんとレビューされる」になる。
尺: 約44秒 ／ 1920×1080 ／ 音楽: tension（before）→ relief → uplift（after）
composition id: `rig-ba`（`window.__timelines["rig-ba"]`）

| # | 区間(s) | 種別 | 画面 | テロップ | VO（ナレーション） | BGM/SE |
|---|---|---|---|---|---|---|
| s1 | 0–4 | title | ロゴ | rig で、何が変わる？ | 「同じ変更を、before と after で。」 | 低ドローン |
| s2 | 4–7.5 | BEFORE | 😵‍💫 | 「この変更、レビューして」 | 「rig が無いと、どうなる？」 | 不穏 |
| s3 | 7.5–14.5 | **screen(before)** | 端末(赤)・親が全 diff 読む | context 膨張(イメージ) / 観点その場任せ / 同種ミス反復 | 「親の context が膨らみ、レビュー観点はその場任せ。」 | ノイズ |
| s4 | 14.5–18 | AFTER | ✨ | コマンド、1つ。 | 「rig を通すと？」 | 転調 |
| s5 | 18–25 | **screen(after)** | 端末・`--plan` の合成ハーネス | steps: 1 ・RUN しない | 「まず構成だけ、ドライランで見える。」 | キー音 |
| s6 | 25–33 | **screen(after)** | 端末・3観点並列→構造化 verdict | 判定: APPROVE_WITH_CONDITIONS | 「3観点を並列で。親に届くのは判定行だけ。」 | ヒット |
| s7 | 33–39 | list | 効き目3つ | context-minimal / 並列 / determinism-by-gate | 「軽く、速く、毎回同じ品質。」 | 上昇 |
| s8 | 39–44 | cta | コマンド | /rig:dev --only review | 「まずは1コマンド。」 | 締めヒット |

## ソース対応表（誇張防止・全ビートが実機能の裏打ち）
- s3（before）→ context-minimal が解く課題（`SKILL.md §6` red flags：親が diff を読み込む／§9 アンチパターン）
- s5（screen）→ `--plan` ドライラン（`SKILL.md §5`）
- s6（screen）→ 3-way 並列レビュー＋`review-verdict`（`recipes/review-only`・`patterns/parallel-fanout`/`review-gate`）
- s7 → context-minimal（§6）／ parallel-fanout ／ determinism-by-gate（`patterns/acceptance-gate`）

## 実画面ショット（必須）
s3 / s5 / s6 は seekable モック端末。**実録に格上げ**するなら、実際の rig 実行の画面収録 mp4 を `assets/` に置き、各シーンを `<video class="clip" … src="assets/*.mp4">` に差し替える（before は「素の手作業」、after は「実際の rig 出力」を撮ると説得力が最大）。
