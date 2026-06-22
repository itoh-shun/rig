# instruction: knowledge-gen

ドメイン知識を **wiki ページ**（`facets/knowledge/_wiki` のスキーマ）として自動生成し、global（user 層・既定）または project overlay（`--project`）に保存する。「persona = 判断 / wiki = 事実」の事実側を育てる。**書き込みは必ず提案→確認→書き込み**（`--autonomous` でも生成物の書き込み確認は解除しない）。解析・起草は subagent に dispatch し、親は長文を抱えない（context-minimal）。

## 入力・2モード

| モード | 起動 | 内容 |
|---|---|---|
| **説明モード** | `/rig:knowledge "<説明>"` | 与えた説明から1つ以上の wiki ページを起草 |
| **`--auto` モード** | `/rig:knowledge --auto` | subagent が repo（コード構造・README・docs・命名）を解析し、ユビキタス言語・ドメインモデル・主要な規約・ADR 風の決定を蒸留してページ化 |

- `--project`：project overlay（`<repo>/.claude/rig/knowledge/wiki/`）に保存。**既定は global**（`~/.claude/rig/knowledge/wiki/`＝全プロダクト共有。知人要件「base=global」）。
- `--name <slug>`（任意）：単一ページの slug を明示。省略時は内容から slug を提案。

## 保存先（tier・`_wiki` と整合）

| スコープ | パス |
|---|---|
| global（既定・一次） | `~/.claude/rig/knowledge/wiki/<slug>.md` |
| project（`--project`・overlay） | `<repo>/.claude/rig/knowledge/wiki/<slug>.md` |

## 手順

1. **粒度決定** — トピック別に**1概念=1ページ**へ割る（大きな塊を1ファイルに詰めない）。`--name` 指定時は単一ページ。
2. **起草**（subagent）— 各ページを `_wiki` スキーマで作る：frontmatter（`title`/`slug`/`aliases`/`tags`/`domain`/`status`/`links`/`sources`）＋本文。関連概念は `[[slug]]` でリンク。
   - `--auto` は**実コード/docs に基づく**こと（存在しない概念・出典を捏造しない。`sources` に根拠を残す）。
   - 既存 wiki を確認し、**同義の正準ページがあれば新規作成せず追補/リンク**（重複を作らない）。
3. **提案** — 保存先パスと各ページのドラフトを提示。**global 書き込みは「全プロダクトに影響」と明示**。
4. **確認** — 承認後にのみ書き込む（`--autonomous` でも確認必須）。同 slug が既存なら**上書きせず**差分提案（冪等・非破壊）。
5. **索引更新** — 書き込んだら `INDEX.md`（ページ一覧・タグ・backlink）を再生成して整合させる。
6. **報告と使い方** — 書いたページと、persona からの使い方（`inject: ["[[<slug>]]"]`）、点検（`/rig:dev --validate`）を案内。

## 原則

- **1概念=1正準ページ・相互リンク・explicit**（暗黙知化させない）。捏造禁止・`sources` 必須。
- 書き込みは確認必須・冪等。global は特に明示。
- これは「事実ストアを育てる」ジェネレータ。判断・声は persona 側（`/rig:persona`）の役割。
