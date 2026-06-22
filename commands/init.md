---
description: rig/init — リポジトリを rig 向けに初期化。manifest(.claude/rig.md)・知識層ディレクトリ・CLAUDE.md の "Compact Instructions" 節を雛形生成する。書き込みは確認必須・冪等(既存は上書きしない)。
argument-hint: [--autonomous は無効(init の書き込みは常に確認)]
---

# rig/init — リポジトリ初期化（scaffold）

**まず `rig` skill を Skill ツールで起動し、その SKILL.md（context-minimal・知識層・§6 run-continuity）に従うこと。** このコマンドは入口であり、手順本体は `facets/instructions/init` にある（重複定義しない）。

起動後、`facets/instructions/init` に従って次を**雛形生成**する:

```
$ARGUMENTS
```

## やること（すべて確認の上で書き込み）

1. **manifest** `<repo>/.claude/rig.md` — `manifests/_template` を基に build/lint/test・default branch を検出して埋める。
2. **知識層ディレクトリ** `<repo>/.claude/rig/knowledge/{domain,accumulated}/` — ドメイン知識と capture 蓄積の置き場。
3. **CLAUDE.md "Compact Instructions" 節** — 圧縮時に rig の run-state を要約へ残す保全文（§6 run-continuity ④ の PreCompact フックと同じ内容の第2経路。毎回の圧縮に自動適用）。

## 規則

- **書き込み＝影響あるアクション。必ず提案（何をどこに）を提示して確認を取ってから書く。`--autonomous` でも init の確認は解除されない。**
- **冪等・非破壊**：既存ファイルは上書きせず、不足分のみ作成/追記。
- init は scaffold のみ。実装/レビューは回さない（それは `/rig:dev` 等の役割）。

## 例

```
/rig:init            # manifest・知識層・Compact Instructions を提案→確認→生成
```

初期化後は `/rig:dev` で着手、`/rig:dev --validate` でブリック整合を点検できる。
