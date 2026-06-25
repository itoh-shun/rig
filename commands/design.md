---
description: rig/design — UI/UX・a11y を内蔵したデザイン作成ハーネス。説明文から仕様書/コンポーネント仕様/ワイヤー/a11y 計画を生成し UI/UX・WCAG で検閲(design)。URL を渡すと Playwright で実装画面を取得し監査(design-audit)。--ppt/--claudedesign で追加出力。
argument-hint: [機能の説明 or 画面URL] [--url <url>] [--a11y-level A|AA|AAA] [--ppt] [--claudedesign] [--plan] [--persona <name>]
---

# rig/design — デザイン作成・監査ハーネス 🎨

**まず `rig:rig` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・context-minimal・facet 配置順・知識層注入）に従うこと。** このコマンドは入口であり、エンジン本体は skill 側にある（重複定義しない）。dev と同じ engine を design ドメインで使う。

## モード（2 系統・URL 有無で分岐）

| 指定 | recipe | 何をする |
|---|---|---|
| （既定・説明文を渡す） | `design` | デザイン成果物を生成 → UI/UX・a11y 並列検閲 |
| 引数に URL / `--url <url>` | `design-audit` | 実装画面を Playwright で取得 → UI/UX・a11y 監査 |

引数に URL（`http(s)://…`）が含まれるか `--url` があれば**監査モード**（`design-audit`）、無ければ**作成モード**（`design`）。

起動後、次の引数を PARSE する:

```
$ARGUMENTS
```

引数が無ければ「何を設計するか（機能・対象ユーザー・必要な成果物）／または監査する画面 URL」を一言確認する（捏造しない）。

## やること

- **作成（既定）**: 引数（機能の説明・対象・成果物種別）を `design` recipe に渡す。手順本体（要件確定 → 成果物生成 → 出力バックエンド → 並列検閲）は `facets/instructions/{design-draft,design-vet}` に従う。成果物はデザイン仕様書／コンポーネント仕様／ワイヤー・モックアップ／a11y 計画。a11y は設計時点で内蔵し、`ux-reviewer`（ユーザビリティ）・`a11y-reviewer`（WCAG 2.2）で検閲して `acceptance-gate` で収束。**実在前提・誇張禁止・不明は `[要記入]`**。
- **監査（URL）**: 引数の URL を `design-audit` recipe に渡す。手順本体（Playwright で SS/DOM/axe 取得 → 並列レビュー）は `facets/instructions/{design-audit,design-vet}` に従う。read のみ・副作用なし。

## 出力バックエンド（作成モード・併用可）

- 既定: Markdown 設計ドキュメント。
- `--ppt`: `powerpoint-server` MCP でスライド化（追加出力）。
- `--claudedesign`: claude.ai デザイン機能（`claude_design` MCP）で生成（追加出力）。MCP 未接続なら報告して Markdown のみで続行。

## flag

- `--url <url>` … 監査モードを明示（bare な URL 引数でも自動検出）。
- `--a11y-level A|AA|AAA` … 目標 WCAG レベル（既定 AA）。
- `--ppt` / `--claudedesign` … 出力バックエンド追加（作成モード）。
- `--persona <name>` … 検閲 fan-out にカスタム reviewer を追加（engine 共通）。
- `--plan` … 合成ハーネスを提示して停止（engine 共通・ドライラン）。

## 例

```
/rig:design ログイン画面・一般ユーザー向け・仕様書とコンポーネント仕様   # 作成
/rig:design 設定ページのワイヤーと a11y 計画 --a11y-level AA --ppt        # 作成＋PPT
/rig:design https://example.com/login                                     # URL 監査
/rig:design --url https://staging.example.com/signup --a11y-level AAA     # 監査・AAA
/rig:design ダッシュボードの設計 --plan                                   # ドライラン
```
