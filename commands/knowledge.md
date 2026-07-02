---
description: "rig/knowledge — ドメイン知識を LLM-wiki ページとして自動生成。説明文 or --auto(repo 解析)から 1概念=1正準ページを起草し global(既定・全プロダクト共有)/project overlay(--project)に保存。persona は inject: [[slug]] で参照する。"
argument-hint: "[\"<説明>\" | --auto] [--project] [--name <slug>]"
---

# rig/knowledge — ドメイン知識ジェネレータ（wiki）

**まず `rig` skill を Skill ツールで起動し、その SKILL.md（context-minimal・知識層注入＝§5・`facets/knowledge/_wiki`）に従うこと。** このコマンドは入口であり、手順本体は `facets/instructions/knowledge-gen` にある（重複定義しない）。

起動後、`facets/instructions/knowledge-gen` に従って wiki ページを生成する:

```
$ARGUMENTS
```

## やること

ドメイン知識を **wiki ページ**（1概念=1正準ページ・相互リンク `[[slug]]`）として起草 → 提案 → **確認の上**で書き込み → `INDEX.md` 更新。

- **2モード**：`"<説明>"` から起草／`--auto` で repo を解析して自動蒸留（ユビキタス言語・ドメインモデル・規約・ADR 風決定）。
- **保存先**：既定 `~/.claude/rig/knowledge/wiki/<slug>.md`（**global・全プロダクト共有**）。`--project` で `<repo>/.claude/rig/knowledge/wiki/<slug>.md`（overlay）。
- 生成ページは persona から **`inject: ["[[<slug>]]"]`** で参照する（事実を埋め込まない＝暗黙知化させない）。

## flag

- `--auto` … repo を解析してドメイン知識を自動生成（実コード/docs 準拠・捏造禁止）。
- `--project` … project overlay に保存。既定は global。
- `--name <slug>` … 単一ページの slug を明示。

## 規則

- **書き込みは確認必須・冪等（同 slug は上書きしない）・捏造禁止（`sources` に根拠）。** global は「全プロダクトに影響」と明示。`--autonomous` でも確認は解除されない。
- 1概念=1正準ページ。同義の既存ページがあれば追補/リンクし、重複を作らない。

## 例

```
/rig:knowledge "90年代ハウスの音作りの型と不変条件"          # global に正準ページ生成
/rig:knowledge --auto                                      # この repo のドメイン知識を自動生成
/rig:knowledge "決済の境界づけられたコンテキスト" --project  # このプロダクト固有の overlay
# 使う（persona から参照）:
#   # persona: house-authenticity
#   inject: ["[[genre-house]]", "[[music-era-90s]]"]
```
