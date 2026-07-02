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

1. **収集** — 各 tier のファイルを `Glob`/`Read` で集め、frontmatter（persona の `domain`/`inject:`、wiki の `slug`/`domain`/`tags`/`status`/`links`、recipe の `name`/`description`/`steps[]`/`autonomy`）を抽出する。
2. **domain で束ねる** — wiki/persona の `domain:` タグ（無ければ名前から推定、それも無ければ `uncategorized`）でグルーピングする。
3. **地図を描く** — 下記フォーマットで提示する。各エントリに **tier（どこに居るか）** と **関連（persona→inject する wiki、wiki→backlink）** を添える。`<repo>/.claude/rig.md` の `default_personas` に載る persona には **`★default`**（製品の review に常時自動投入）を付す。
4. **絞り込み（任意）** — `--domain <tag>` 指定時は当該 domain だけ表示。

## 出力フォーマット（Markdown の地図）

```
## rig catalog（横断レジストリ）

scope: shipped + global + project（<repo>）

### domain: music
- pack/recipe: vst-plugin [project]
- persona:
  - music-era-90s-taste [global] → inject: [[music-era-90s]], [[genre-house]]
  - house-authenticity   [project] ★default → inject: [[genre-house]]
- wiki: [[music-era-90s]] [global], [[genre-house]] [global], [[effect-design-conventions]] [global]  (3)

### domain: dev（shipped 標準）
- recipe: review-only [1 step · interactive], release-flow [7 steps · interactive], hotfix [4 steps · interactive] … [shipped]
- persona: security-reviewer, design-reviewer, test-reviewer … [shipped]

### uncategorized
- …

summary: domains=N | personas=N(global M/project K/shipped L) | wiki=N | recipes=N
```

- recipe エントリは `name [N step(s) · interactive|autonomous]` の形式で表示する（N=1 のみ `1 step`、以降 `N steps`）。これは `--list` の recipe エントリ表示（正本: `facets/instructions/list`）と同じメタデータで、autonomy・フローの重さを一覧段階で判断できる。badge・`steps:` フィールドの表示ルールも同ファイルに従う。
- **`### Accumulated Knowledge` セクション（#115）**：`<repo>/.claude/rig/knowledge/accumulated/` に1件以上ファイルがある場合、末尾に `### Accumulated Knowledge` セクションを追加する。各エントリは frontmatter の `title（category, date）` を1行で表示し、`category` 別（`pitfall` / `decision` / `convention` / `stuck-twice`）にグルーピングする。`~/.claude/rig/knowledge/accumulated/`（user 層）も同様に表示し `（global）` と区別する。ファイルが0件の tier はサイレントに省略する（空見出し不要）。

  ```
  ### Accumulated Knowledge （project）
  #### pitfall
  - JWTリフレッシュ競合でダブル送信（2026-05-10）
  #### decision
  - 認証ミドルウェアを共通化（2026-05-15）
  #### convention
  - マイグレーションは必ず冪等に書く（2026-06-01）
    3 entries （<repo>/.claude/rig/knowledge/accumulated/）
  ```
- `--json` 指定時は機械可読 JSON で同じ内容を返す（将来のグラフ可視化用。既定は Markdown）。
- 末尾に **要対応**（`--validate --global` で見つかる orphan/リンク切れ等があれば件数だけ）を1行で添え、「詳細は `--validate --global`」と案内する。

## 原則

- レジストリは**派生ビュー**。永続ストアを別に作らない（wiki/INDEX と同じ思想＝ドリフトしない）。
- 読み取り専用・副作用なし。長い本文は親に引き込まず、地図（名前・tier・関連）だけ集約する。
