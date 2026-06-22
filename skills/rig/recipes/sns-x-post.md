---
name: sns-x-post
description: X(Twitter)のポストを半自動で起案する。声 persona で起案→de-ai-smell→sns-post-reviewer が掴み/ブランド/AI臭/リスクを判定し定型/要判断に分類。定型は承認キュー、要判断は停止して承認。歌ってみた等クリエイター宣伝向け。
scope: shipped
steps:
  - id: post
    instruction: sns-post
    pattern: serial
    gate: acceptance-gate
    acceptance: ["1行目で掴めている(X-conv)", "アカウントの声・ブランドに適合(宣伝過多でない)", "AI 臭なし(ai-writing-smells)・声の人間味は均していない", "権利/出典・他者比較・スパム的挙動のリスクなし", "定型/要判断に分類済み(要判断は停止して承認待ち)"]
    personas: [sns-post-reviewer]
    output_contract: review-verdict
autonomy: interactive
---

# sns-x-post

## 使う場面

X で個人クリエイター（歌ってみた等）の宣伝運用を**半自動**で回したい時。`/rig:dev --recipe sns-x-post "<トリガー: 新曲告知 / 制作裏側 / 交流 / 直近反応>"`。

## 前提（アカウントの声を1回だけ用意する）

声・人格は**あなたの資産**。`/rig:persona "歌ってみたを上げる〇〇な歌い手の声"` で声 persona を作り、その垢の manifest（`<repo>/.claude/rig.md`）に `default_personas: [<その声>]` を入れる（または `--persona <その声>` で都度投入）。これで「声＝あなた製」「判断＝sns-post-reviewer」「事実＝X 型 wiki」が揃う。

## 展開

1. トリガーと確定事実（曲名・公開日時・出典）を収集（未確定は捏造せず要判断へ）。
2. **声 persona** で 1行目重視のドラフトを起案（リンクは末尾/リプ、ハッシュタグ1〜2個）。
3. `de-ai-smell`（`ai-writing-smells`）で AI 臭を落とす。声の人間味は均さない。
4. `sns-post-reviewer` が掴み/ブランド/AI臭/リスク/導線を判定し、**定型 / 要判断**に分類。
5. `acceptance-gate` で基準へ収束：
   - **定型** → 承認キューへ（投稿時間提案つき）。
   - **要判断** → 停止して運用者へ（権利/比較/事実主張/新規性などの理由を明示）。
6. 投稿本文・時間提案・分類・リスクメモを `review-verdict` と併せて返す。

## 設計メモ

- **半自動の核は分類**：定型は自動キュー、要判断のみ人間。信頼が貯まった型を順次「定型」に降ろして承認負荷を減らす（決定権を広げる）。
- **knowledge=事実 / persona=判断 / 声=あなた** の分離。X の型を更新したいときは `sns-x-conventions` を直すだけで全運用に効く。
- **実投稿は別レイヤー**（手動キュー / X API アダプタ）。本 recipe は「承認できる状態」までを保証する（自動投稿＝ToS/BAN リスクは段階導入）。
- 5垢へ広げる時は、各垢に声 persona＋manifest を持たせ、横断管理は台帳/GM（コントロールプレーン）で。
