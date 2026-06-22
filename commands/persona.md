---
description: rig/persona — 説明文から reviewer/persona を自動生成。product 単位(project 層・既定)か global(user 層・--user)に保存し、--persona <name> で review に投入できる。例 "80年代の音楽を理解しているレビュアー"。
argument-hint: ["<どんなレビュアーか説明>"] [--user] [--name <id>]
---

# rig/persona — persona ジェネレータ

**まず `rig` skill を Skill ツールで起動し、その SKILL.md（context-minimal・facet 配置順・persona の tier 解決＝§5）に従うこと。** このコマンドは入口であり、手順本体は `facets/instructions/persona-gen` にある（重複定義しない）。

起動後、`facets/instructions/persona-gen` に従って persona を生成する:

```
$ARGUMENTS
```

説明が空なら「どんなレビュアーが欲しいか」を一言促す（捏造しない）。

## やること

説明文から reviewer/persona facet を起草 → 保存先とドラフトを提示 → **確認の上**で書き込む。

- **保存先**：既定 `<repo>/.claude/rig/personas/<name>.md`（project／product 単位）。`--user` で `~/.claude/rig/personas/<name>.md`（global・全プロジェクト共有）。
- **名前**：`--name` 省略時は説明から slug を提案（例「80年代の音楽…」→ `music-era-80s-reviewer`）。
- 生成した persona は **`--persona <name>`** で review に投入できる（tier 解決で名前から使える）。

## flag

- `--user` … global（user 層）に保存。既定は project。
- `--name <id>` … 保存名／persona 名を明示。

## 規則

- **書き込みは確認必須・冪等（既存は上書きしない）・捏造禁止。** global 書き込みは「全プロジェクトに影響」と明示してから。`--autonomous` でも書き込み確認は解除されない。
- 生成するのは persona facet のみ（native agent は作らない）。

## 例

```
/rig:persona "80年代の音楽を理解しているレビュアー"            # → project に生成
/rig:persona "セキュリティに厳しいシニア" --user               # → global に生成
/rig:persona "UXコピーの審美に厳しい人" --name ux-copy-taste
# 使う:
/rig:dev --only review --persona music-era-80s-reviewer
```
