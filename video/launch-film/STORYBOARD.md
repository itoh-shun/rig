# STORYBOARD — rig launch film（1.0 コンセプト・HyperFrames）

> ⚠️ これは **1.0 を見据えたコンセプト動画**。rig はまだ **v1.0 未リリース**（現行 v0.36）。「正式リリース時の予告」を先取りで作ったもので、出荷済みを主張しない。

ログライン: 固定ワークフローを、捨てる。
尺: 約46秒 ／ 解像度: 1920×1080 ／ 音楽: low drone build → triumphant swell（任意・`assets/music.wav`）
composition id: `rig-launch`（`window.__timelines["rig-launch"]`）

| # | 区間(s) | 種別 | 画面 | テロップ | VO（ナレーション） | BGM/SE |
|---|---|---|---|---|---|---|
| s1 | 0–5 | title | ロゴ発光 | rig → 1.0（concept・未リリース）/ 固定ワークフローを、捨てる。 | 「開発の成果は、コードだけじゃない。」 | 低ドローン |
| s2 | 5–10 | feature | 🧩 ＋ パイプライン | PARSE → RESOLVE → COMPOSE → RUN | 「ブリックを、その場で組む。」 | タイプ音 |
| s3 | 10–17 | **screen** | 端末で `--plan` の合成ハーネス | （実出力） | 「何が起きるか、撃つ前に全部見える。」 | キー音 |
| s4 | 17–22 | feature | 🎯 | determinism-by-gate | 「非決定な実行を、毎回同じ品質へ。」 | 上昇 |
| s5 | 22–28 | list | pack 群 | engine 不変・pack を足すだけ | 「dev も、商談も、ユーモアも。」 | — |
| s6 | 28–35 | **screen** | MAGI 合議コンソール | 判定: 可決（2:1） | 「やるべきか、を三人で裁く。」 | ヒット |
| s7 | 35–40 | feature | 🧵 | run-continuity | 「中断も圧縮も跨いで、駆動を切らさない。」 | スウェル |
| s8 | 40–46 | cta | ロゴ＋コマンド | compose your harness. | 「rig、1.0 へ。」 | 締めヒット |

## ソース対応表（誇張防止・全ビートが実機能の裏打ち）
- s2 → `SKILL.md §1`（PARSE→RESOLVE→COMPOSE→RUN）
- s3（screen）→ `--plan` ドライラン出力（`SKILL.md §5`）
- s4 → determinism-by-gate（`patterns/acceptance-gate`）
- s5 → 各 pack（`SKILL.md §2` pack 目録）
- s6（screen）→ MAGI 合議（`recipes/magi` / `patterns/magi-consensus`）
- s7 → run-continuity（`SKILL.md §6`）

## 実画面ショット（必須）
s3 / s6 は seekable モック端末で実出力を再現。**実録に格上げ**するときは、画面収録 mp4 を `assets/` に置き、`index.html` のコメント例どおり該当シーンを `<video class="clip" … src="assets/*.mp4">` に差し替える。
