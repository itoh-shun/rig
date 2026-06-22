# instruction: catalog

**横断レジストリ＝統合管理ハーネス。** 全 tier（shipped＋user(global)＋project）を走査し、「どの domain に・どんな pack / persona / wiki / recipe があって・どこ（tier）に居て・何を参照しているか」の**地図**を派生表示する。レジストリは**手で持たず毎回走査して生成**する（別ストアを作らない＝ドリフトしない）。**副作用なしの読み取り専用**（`--list`/`--validate` と同系。RESOLVE/COMPOSE/RUN しない）。

domain/プロダクトが増えて「誰がどこで何をしているか把握できない」を解消するための層。

## 走査対象（tier 横断）

| 種別 | shipped | user(global) | project |
|---|---|---|---|
| persona | `skills/rig/facets/personas/` | `~/.claude/rig/personas/` | `<repo>/.claude/rig/personas/` |
| wiki ページ | — | `~/.claude/rig/knowledge/wiki/` | `<repo>/.claude/rig/knowledge/wiki/` |
| recipe / pack | `skills/rig/recipes/` | `~/.claude/rig/recipes/` | `<repo>/.claude/rig/recipes/` |
| knowledge（既存層） | `facets/knowledge/` | `~/.claude/rig/knowledge/{methodology,ai-quirks}/` | `<repo>/.claude/rig/knowledge/{domain,accumulated}/` |

存在しない tier/ディレクトリは**サイレントにスキップ**。走査は subagent に dispatch し、親は地図だけ受ける（context-minimal）。

## 手順

1. **収集** — 各 tier のファイルを `Glob`/`Read` で集め、frontmatter（persona の `domain`/`inject:`、wiki の `slug`/`domain`/`tags`/`status`/`links`、recipe の `name`/`description`）を抽出する。
2. **domain で束ねる** — wiki/persona の `domain:` タグ（無ければ名前から推定、それも無ければ `uncategorized`）でグルーピングする。
3. **地図を描く** — 下記フォーマットで提示する。各エントリに **tier（どこに居るか）** と **関連（persona→inject する wiki、wiki→backlink）** を添える。
4. **絞り込み（任意）** — `--domain <tag>` 指定時は当該 domain だけ表示。

## 出力フォーマット（Markdown の地図）

```
## rig catalog（横断レジストリ）

scope: shipped + global + project（<repo>）

### domain: music
- pack/recipe: vst-plugin [project]
- persona:
  - music-era-90s-taste [global] → inject: [[music-era-90s]], [[genre-house]]
  - house-authenticity   [global] → inject: [[genre-house]]
- wiki: [[music-era-90s]] [global], [[genre-house]] [global], [[effect-design-conventions]] [global]  (3)

### domain: dev（shipped 標準）
- recipe: review-only, release-flow, hotfix … [shipped]
- persona: security-reviewer, design-reviewer, test-reviewer … [shipped]

### uncategorized
- …

summary: domains=N | personas=N(global M/project K/shipped L) | wiki=N | recipes=N
```

- `--json` 指定時は機械可読 JSON で同じ内容を返す（将来のグラフ可視化用。既定は Markdown）。
- 末尾に **要対応**（`--validate --global` で見つかる orphan/リンク切れ等があれば件数だけ）を1行で添え、「詳細は `--validate --global`」と案内する。

## 原則

- レジストリは**派生ビュー**。永続ストアを別に作らない（wiki/INDEX と同じ思想＝ドリフトしない）。
- 読み取り専用・副作用なし。長い本文は親に引き込まず、地図（名前・tier・関連）だけ集約する。
