# rig:design pack — UI/UX/a11y デザイン作成 ＋ 実装画面監査

- 日付: 2026-06-25
- ステータス: 設計承認済み（実装計画待ち）
- 種別: モード pack（engine 不変・ブリック上乗せ）

## 1. 目的

UI/UX・アクセシビリティ（a11y）の観点を最初から盛り込んだ**デザイン成果物を作成**し、さらに**実装済み画面を URL で監査**できる pack を rig に追加する。

rig engine（`skills/rig/SKILL.md`）は一切書き換えず、`/rig:scenario`（生成→検閲）や `/rig:pr`（取得→レビュー）と同じく **command + recipe + persona/instruction/output-contract/knowledge facet を上乗せするだけ**で成立させる。

## 2. スコープ

### やること

- **作成モード（既定）**: 説明文・機能名から以下を生成し、UI/UX・a11y レンズで検閲してから確定する。
  - デザイン仕様書（スタイルガイド・カラー/タイポ/余白ルール・デザイントークン）
  - コンポーネント仕様（状態・バリアント・a11y 属性）
  - ワイヤー/モックアップ（視覚成果物）
  - a11y 監査表/計画（WCAG 対応度チェックリスト）
- **監査モード（URL あり / `--url`）**: 実装済み画面を Playwright で開き、スクリーンショット・DOM・axe-core スキャン結果を取得して UI/UX・a11y で採点する。
- **出力バックエンド（作成モード・併用可）**: 既定 Markdown に加え、`--ppt` で PowerPoint、`--claudedesign` で claude.ai デザインを**追加**出力する。

### やらないこと（YAGNI）

- 動くアプリの実装そのもの（実装は `/rig:dev` に委譲）。本 pack は設計成果物と監査レポートまで。
- 新しい visual diff/回帰テスト基盤（Playwright 既存機能の範囲で取得・観察するのみ）。
- engine（PARSE/RESOLVE/COMPOSE/RUN）の改変。

## 3. アーキテクチャ

PARSE 時に**引数に URL が含まれるか（または `--url`）**でモードを分岐する。

| モード | 起点 | recipe | step フロー |
|---|---|---|---|
| 作成（既定） | 説明文・機能名 | `design` | `draft`（serial）→ `vet`（parallel-fanout ＋ acceptance-gate） |
| 監査 | 画面 URL | `design-audit` | `capture`（serial・Playwright）→ `audit`（parallel-fanout ＋ acceptance-gate） |

検閲/監査は rig の核心 **determinism-by-gate** に従い、UI/UX・a11y の並列 fan-out を `acceptance-gate` で受け入れ基準まで収束させる。

## 4. ブリック構成（追加分）

すべて engine 不変で上乗せ。SKILL.md §2 の「pack 追加分」表に1行追記する。

### command

- `commands/design.md` — 入口。`rig` skill を起動し `--recipe design` を既定に PARSE。引数に URL があれば `design-audit` に切り替える。

### recipe（2本）

- `recipes/design`（scope: shipped）
  - `draft`（instruction: `design-draft` / pattern: serial / personas: `[design/ui-ux-designer]`）
  - `vet`（instruction: `design-vet` / pattern: parallel-fanout / gate: acceptance-gate / personas: `[design/ux-reviewer, design/a11y-reviewer]` / output_contract: `design-verdict`）
- `recipes/design-audit`（scope: shipped）
  - `capture`（instruction: `design-audit` / pattern: serial）— Playwright で URL を開き SS・DOM・axe を取得
  - `audit`（instruction: `design-vet` / pattern: parallel-fanout / gate: acceptance-gate / personas: `[design/ux-reviewer, design/a11y-reviewer]` / output_contract: `design-verdict`）

> `design-vet` instruction は作成・監査の両方で共用する（対象が「draft 成果物」か「capture 結果」かを入力で受ける）。

### personas（`facets/personas/design/`）

- `ui-ux-designer` — 作成役。視覚階層・レイアウト・デザインシステム・IA を起こす。既存スキル `frontend-design` / `ui-designer` / `design-system-patterns` へ委譲する薄い人格。
- `ux-reviewer` — 検閲役。ユーザビリティ・認知負荷・操作性・一貫性。`inject: [[ui-ux-heuristics]]`。
- `a11y-reviewer` — 検閲役。WCAG 2.2・コントラスト・キーボード操作・スクリーンリーダー・ARIA・フォーカス管理。`inject: [[a11y-wcag]]`。

### instructions（`facets/instructions/`）

- `design-draft` — 成果物（仕様書/コンポーネント仕様/ワイヤー/a11y 計画）を生成し、`--ppt`（`powerpoint-server` MCP）・`--claudedesign`（`claude_design` MCP）の出力バックエンドへ委譲する。
- `design-vet` — UI/UX・a11y を並列検閲し `design-verdict` で返す。作成（draft 対象）・監査（capture 対象）共用。
- `design-audit` — Playwright で URL を開き、スクリーンショット・DOM・axe-core a11y スキャンを取得して後段 `audit` に渡す。

### output-contract

- `facets/output-contracts/design-verdict` — `review-verdict` を踏襲しつつ a11y 構造を追加:
  - 判定: `APPROVE | REJECT | APPROVE_WITH_CONDITIONS`
  - UI/UX 所見（重大度つき）
  - a11y 所見（WCAG 達成基準番号・レベル A/AA/AAA・重大度・具体修正）
  - 条件（対応必須 / フォローアップ可）

  > review-verdict に収まらない a11y 固有構造（基準番号・レベル）があるため新設する。scenario が「欠けた面白さ軸」だけに engagement-reviewer を足したのと同じ「欠けた軸のみ追加」原則。

### knowledge（`facets/knowledge/`）

事実は persona に埋め込まず `inject: [[slug]]` で参照する rig 原則に準拠:

- `a11y-wcag` — WCAG 2.2 達成基準リファレンス（レベル A/AA/AAA・主要 SC）。
- `ui-ux-heuristics` — Nielsen 10 ユーザビリティヒューリスティック等。

## 5. フラグ

| flag | 意味 |
|---|---|
| `--ppt` | 設計ドキュメントを PowerPoint としても出力（`powerpoint-server` MCP）。作成モードのみ。 |
| `--claudedesign` | claude.ai デザイン機能（`claude_design` MCP）でも生成。作成モードのみ。 |
| `--url <url>` | 監査モードを明示。bare な URL 引数でも自動検出する。 |
| `--a11y-level <A\|AA\|AAA>` | 目標 WCAG レベル（既定 AA）。 |
| `--plan` | 合成ハーネスを提示して停止（engine 共通・ドライラン）。 |

engine 共通フラグ（`--recipe` / `--persona` / `--autonomous` 等）は継承する。

## 6. 委譲先（native-first）

| 機能 | 委譲先 |
|---|---|
| デザイン生成 | スキル `frontend-design` / `ui-designer` / `design-system-patterns` / `minimalist-ui` |
| a11y 知見 | スキル `accessibility` / `accessibility-engineer`（必要時） |
| URL 監査 | MCP `mcp__playwright__*`（SS・DOM・axe-core） |
| PowerPoint 出力 | MCP `mcp__powerpoint-server__*` |
| claude.ai デザイン | MCP `mcp__claude_design__*` |

いずれもユーザー settings の allowlist に登録済み。

## 7. ドキュメント更新

- `skills/rig/SKILL.md` §2 pack 追加分表に `design` 行を追記。
- `skills/rig/SKILL.md` §3 flag 一覧に `--ppt` / `--claudedesign` / `--url` / `--a11y-level` を追記。
- `README.md` / `README.ja.md` のコマンド一覧・recipes テーブルに `design` を追加。

## 8. 受け入れ基準

- `/rig:design "<機能の説明>"` で仕様書/コンポーネント仕様/ワイヤー/a11y 計画が生成され、UI/UX・a11y 検閲を acceptance-gate で通過する。
- `/rig:design --ppt`・`/rig:design --claudedesign` で各バックエンドへ追加出力される（併用可）。
- `/rig:design <URL>`（または `--url`）で Playwright が画面を取得し、`design-verdict` 形式で UI/UX・a11y 採点が返る。
- `/rig:design --plan` で合成ハーネスが提示され実行されない。
- `--validate` が新ブリックの参照切れ・目録ドリフトを検出しない（SKILL.md §2 と実ファイルが一致）。
- engine（SKILL.md の PARSE/RESOLVE/COMPOSE/RUN 本体）は無改変。
