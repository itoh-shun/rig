# STORYBOARD — rig: before / after（開発フロー全体・auteur 演出版）

ログライン: 「この機能、月曜まで」が、コマンド1つで——実装もテストもレビューも PR も、組み上がっている。
尺: 約58秒 ／ 1920×1080 ／ 音楽: 夜の静けさ → 不穏(before) → 無音の一拍 → 駆動(after) → 余韻
composition id: `rig-ba` ／ 演出: engagement ＋ auteur/deconstructionist（間）＋ auteur/humanist（人・一息）
目玉（hero beat）: **s7 — review→pr→merge が回り切り「親がやったのは、dispatch と集約だけ。」**

| # | 区間(s) | 種別 | 画面 | テロップ | VO | BGM/SE |
|---|---|---|---|---|---|---|
| s1 | 0–4.5 | cold-open（人） | 暗がり | 金曜 21:47 /「この機能、月曜までに」 | 「実装も、テストも、レビューも、PR も。」 | 夜 |
| s2 | 4.5–7.5 | before | 😮‍💨 | 全部、手で。（どこから？） | 「…さて。」 | 不穏 |
| s3 | 7.5–15.5 | **screen(before)** | 端末(赤)・手作業の開発 | 実装→テスト後回し→レビュー誰？→PR 手作業→context 膨張 | 「設計も実装も検証もレビューも、毎回 手で組み直す。」 | ノイズ |
| s4 | 15.5–18.5 | **間** | ほぼ黒・一行 | rig に組ませる。 | （無音の一拍） | 無音→低音 |
| s5 | 18.5–22.3 | after | ✨ | コマンド、1つ。 | 「intake→実装→検証→レビュー→PR→merge を、その場で合成。」 | 転調 |
| s6 | 22.3–30.3 | **screen(コーディング)** | 端末・implement/TDD/verify | ✓ red→green ✓ build ✓ lint 0 ✓ tests green | 「実装は TDD で。検証は acceptance-gate で。」 | キー音 |
| s7 | 30.3–38.3 | **HERO・screen** | 端末・review→pr→merge | …親がやったのは、dispatch と集約だけ。 | 「レビューも、PR も、マージも。親は集約だけ。」 | ヒット→静寂 |
| s8 | 38.3–43.8 | 広がり | list | intake→…→merge ／ magi ／ goal ／ sales… | 「レビューだけ、じゃない。」 | 上昇 |
| s9 | 43.8–48.8 | 効き目 | list | context-minimal / determinism-by-gate / size-aware | 「軽く、毎回同じ品質、重い時だけ厚く。」 | — |
| s10 | 48.8–53.3 | payoff（人・一息） | 😌 | 気づけば、機能ができてた。 | 「テストも通って、PR も、マージも。」 | 余韻 |
| s11 | 53.3–58.3 | cta | コマンド | /rig:dev "機能を実装して" | 「まずは1コマンド。」 | 締め |

## ソース対応表（誇張防止・全ビートが実機能の裏打ち）
- s3（before）→ context-minimal / 手作業のフロー組み（§6 red flags / §9）
- s6（コーディング）→ release-flow の implement(`--tdd`)＋verify(`acceptance-gate`：build/lint/tests)（§3.5 / `patterns/acceptance-gate`）
- s7（HERO）→ review(parallel-fanout)→pr→merge ＋ context-minimal（`recipes/release-flow` / §6）
- s8（広がり）→ recipe/pack 目録（§2：dev 全工程・magi・goal・sales・talk・de-ai-smell・pre-mortem）
- s9 → context-minimal / determinism-by-gate / size-aware（§6 / acceptance-gate / §4.4）

## 実画面ショット（必須）
s3 / s6 / s7 は seekable モック端末。**実録に格上げ**するなら、実際の `/rig:dev --recipe release-flow --tdd …` 実行の画面収録 mp4 を `assets/` に置き、各シーンを `<video class="clip" … src="assets/*.mp4">` に差し替える（s6 のコーディング、s7 の merge を実録にすると説得力が最大）。
