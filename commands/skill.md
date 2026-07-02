---
description: "rig/skill — 説明文から rig のブリック/パック（recipe・instruction・persona・output-contract・command）を自作して検証・保存する自己拡張メタ能力。「こういうフロー/レビュー観点/モードが欲しい」→ rig 規約で生成し validate まで。engine 不変・pack 上乗せ。"
argument-hint: "[\"<欲しい機能の説明>\"] [--type recipe|persona|knowledge|pack] [--name <id>] [--user]"
---

# rig/skill — スキル自作（writing-skills） 🧱✨

**まず `rig` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・§2 ブリック目録・§3.5 recipe スキーマ・§5 tier 解決・context-minimal）に従うこと。** このコマンドは入口であり、手順本体は `facets/instructions/skill-author` にある（重複定義しない）。

起動後、`facets/instructions/skill-author` に従って rig のブリック/パックを生成する:

```
$ARGUMENTS
```

## やること

rig が**自分自身を拡張する**。説明文を受け取り、必要なブリックを判定して rig 規約どおりに生成し、検証して保存する。

- **何を作るか判定**：レビュー観点→`/rig:persona` へ委譲、ドメイン知識→`/rig:knowledge` へ委譲、新しいフロー/モード→recipe＋instruction を本コマンドで、まとまった機能→pack 一式。
- **pack の定石**：persona＝判断／knowledge＝観点カタログ／instruction＝routing（Native-first）／recipe＝step の束（gate つき）／output-contract＝出力フォーマット／command＝入口。
- **engine 不変・pack 上乗せ**：新しい制御機構を発明せず、既存 pattern（acceptance-gate / review-gate / parallel-fanout / autonomous-loop）と facet 型を組むだけで成立させる。
- **検証込みで完結**：生成後に rig の `--validate`（rig 本体なら `python3 scripts/validate.py`）で参照切れ・スキーマ逸脱が無いか確認し、FAIL を直してから完了（壊れた brick を残さない）。
- **書き込みは確認必須・冪等**。既存 brick を黙って上書きしない。

## 保存先（tier）

| スコープ | パス |
|---|---|
| project（既定・product 単位） | `<repo>/.claude/rig/...` |
| user（`--user`・global） | `~/.claude/rig/...` |
| shipped（rig 本体作業時・`--shipped`） | `skills/rig/...`＋SKILL.md §2 目録 |

## 例

```
/rig:skill "コミットメッセージを規約準拠に直すフロー"            # 新フロー(recipe)を生成
/rig:skill --type pack "短歌を5観点で評価するモード"            # pack 一式を生成
/rig:skill "アクセシビリティ専門のレビュアー"                   # → /rig:persona へ委譲
/rig:skill --user "個人用のリリース前チェックリスト"            # user 層に保存
```

生成した brick は project/user なら `--list` / `/rig:catalog` に出る。`/rig:dev --recipe <name>` 等で即使える。
