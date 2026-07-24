---
description: "rig/export — rig で育てたブリック（persona/recipe/pack）を独立した Claude Code skill として書き出す還元機構。rig 依存を除去して self-contained 化し、出所とライセンスを継承。import（吸収）の対＝ネットに返す。"
argument-hint: "[--persona <name> | --recipe <name> | --pack <名前>] [--to <dir>] [--dry-run]"
---

# rig/export — ブリックを skill として書き出す 📤

**まず `rig:engine` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・§2 ブリック目録・context-minimal）に従うこと。** このコマンドは入口であり、手順本体は `facets/instructions/skill-export` にある（重複定義しない）。

起動後、`facets/instructions/skill-export` に従ってブリックを書き出す:

```
$ARGUMENTS
```

## やること

`/rig:import`（吸収）の対＝**還元**。rig で育てた persona / recipe / pack を、**rig を知らない人がそのまま使える** Claude Code skill リポジトリ構成（SKILL.md + README + references/ + LICENSE）に変換する。

- **self-contained 化**：output-contract はインライン展開・wiki `inject:` は同梱ファイル化・gate は散文に翻訳＝rig 固有の語彙と参照を残さない。
- **出所の連鎖を切らない**：import 由来の再 export は上流の出所とライセンス継承義務を確認（再配布不可なら中止して報告）。
- **export → import の輪**：書き出した skill を GitHub に置けば、他の rig ユーザーは `/rig:import <owner>/<repo>` で取り込める。
- 書き込みは確認必須・冪等・`--dry-run` でプレビューのみ。

## 例

```
/rig:export --persona house-authenticity --dry-run   # 構成プレビューのみ
/rig:export --persona house-authenticity             # 1ペルソナを skill 化
/rig:export --recipe strict-tdd --to ~/skills-out    # 育てた recipe を書き出す
/rig:export --pack sales                             # pack 一式を skill 化
```
