# instruction: design-draft

機能の説明から UI/UX・a11y を内蔵したデザイン成果物を生成し、要求された出力バックエンドへ書き出す。実制作は既存スキルに委譲し、親は成果物のメタ（種別・対象・目標レベル）を保持する（context-minimal）。

## 手順

### ① 要件の確定
引数（機能の説明・対象ユーザー・必要な成果物種別）を読む。曖昧なら**1問だけ**確認する（捏造しない）。目標 WCAG レベルは `--a11y-level`（既定 AA）。

### ② 成果物の生成（persona: `design/ui-ux-designer`）
求められた成果物を起こす（複数可）。視覚/体系は既存スキルへ委譲する：`frontend-design` / `ui-designer` / `minimalist-ui`（視覚・実装寄り）、`design-system-patterns`（トークン/体系）。
- デザイン仕様書（スタイルガイド・トークン・状態定義）
- コンポーネント仕様（状態/バリアント/a11y 属性）
- ワイヤー/モックアップ（レイアウト記述・HTML）
- a11y 計画（目標レベルと設計での充足方針）
a11y は設計時点で内蔵する（後段 vet が弾く前提で素直に作る）。**実在前提・誇張禁止・不明は `[要記入]`**。

### ③ 出力バックエンド（既定 Markdown ＋ 任意で追加・併用可）
- **既定**：Markdown 設計ドキュメントとして提示/保存。
- **`--ppt`**：`powerpoint-server` MCP（`mcp__powerpoint-server__*`）でスライド化。1スライド1テーマ（概要/トークン/各コンポーネント/a11y 計画）。生成後、保存先を報告する。
- **`--claudedesign`**：claude.ai デザイン機能（`mcp__claude_design__*`）で生成。MCP が未接続なら ToolSearch で接続を試み、不可なら**その旨を報告して Markdown のみで続行**（停止しない）。
- `--ppt` と `--claudedesign` は併用可。いずれも既定 Markdown に**追加**する。

### ④ 検閲へ
生成した成果物を `design-vet` の並列検閲（`ux-reviewer` / `a11y-reviewer`）に渡す。

## 原則
- 成果物の全文を親 context に溜め込まない。生成は委譲し、検閲は subagent に渡す。
- ファイル書き込み・MCP 生成は影響あるアクション。保存先を明示し、`--autonomous` でも上書き確認は省かない。
