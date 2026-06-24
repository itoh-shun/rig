# SCENARIO — rig before/after デモ（開発フロー全体・検閲済み）

> `/rig:scenario before/after --persona auteur/deconstructionist --persona auteur/humanist` の確定アウトプット。
> 検閲：`ai-smell-reviewer`（＋`ai-writing-smells`）× `sns-post-reviewer` × `engagement-reviewer` ×
> `auteur/deconstructionist` × `auteur/humanist` ＋ source 対応チェック → acceptance-gate。
> このシナリオが `web/before-after.html` / `video/before-after/`（`/rig:movie --hyperframes`）の設計図。

- 種別: before-after ／ 尺: 約58〜63秒 ／ 観客: 開発者
- ログライン: 「この機能、月曜まで」が、コマンド1つで——実装もテストもレビューも PR も組み上がる。
- 感情の弧: 夜の“作る”依頼（人）→ 手作業の痛み → 無音の一拍 → 駆動（実装→検証→レビュー→PR→merge）→ 一息（人）→ CTA
- 目玉（hero beat・1つ）: **review→pr→merge が回り切り「親がやったのは、dispatch と集約だけ。」**

## ビートシート（開発フロー全体・auteur 演出）

| # | ビート | 画面 | テロップ | source |
|---|---|---|---|---|
| 1 | cold-open（人） | 暗がり | 金曜 21:47 /「この機能、月曜までに」 | —（“作る”タスクで掴む） |
| 2 | before | 😮‍💨 | 全部、手で。 | — |
| 3 | **screen(before)** | 端末(赤) | 実装→テスト後回し→レビュー誰？→PR 手作業→context 膨張 | context-minimal / 手作業フロー（§6/§9） |
| 4 | **間** | ほぼ黒 | rig に組ませる。 | —（断ち切り） |
| 5 | after | ✨ | コマンド、1つ。 | engine §1（合成） |
| 6 | **screen(コーディング)** | 端末・implement/TDD/verify | ✓ red→green ✓ build ✓ lint 0 ✓ tests green | release-flow implement(`--tdd`)＋verify(`acceptance-gate`) |
| 7 | **HERO・screen** | 端末・review→pr→merge | …親がやったのは、dispatch と集約だけ。 | release-flow / parallel-fanout / context-minimal |
| 8 | 広がり | list | dev 全工程 / magi / goal / sales… | §2 recipe・pack 目録 |
| 9 | 効き目 | list | context-minimal / determinism-by-gate / size-aware | §6 / acceptance-gate / §4.4 |
| 10 | payoff（人・一息） | 😌 | 気づけば、機能ができてた。 | context-minimal の体感 |
| 11 | cta | コマンド | /rig:dev "機能を実装して" | dev エンジン |

## 検閲ログ（“開発フロー全体”への作り直しで通過した修正）

- **[user/engagement]** **レビューだけに寄り過ぎ** → 目玉を「review verdict」から「**実装→検証→レビュー→PR→merge の全工程が回り切る**」に移し、コーディング（implement / TDD / acceptance-gate）の screen を追加。広がり（magi/goal/sales…）の beat も追加。
- **[auteur:deconstruction]** 滑らかさ → s4 に無音の一拍／HERO（s7）を溜めて単独で。
- **[auteur:humanist]** 人を中心に → s1 を“作る依頼の夜”／s10 の一息 payoff。
- **[ai-smell / sns-post / source]** PASS（`12/12`・`#128` 等は実機能の例示・偽精度や捏造機能なし）。

→ 再走で全観点 APPROVE・acceptance-gate 充足。**盛らずに、見せ方・順番・間で**（誇張ゼロ・全ビート source 対応）。

## CTA
`/rig:dev "機能を実装して"` — 実装も、検証も、レビューも、PR も。rig が組む。
