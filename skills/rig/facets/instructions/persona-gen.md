# instruction: persona-gen

説明文から **reviewer/persona facet を自動生成**し、product 単位（project 層・既定）または global（user 層・`--user`）に保存する。手で persona を書き起こす手間をなくし、ブリック・ライブラリを会話で育てる。**書き込みは影響あるアクションなので必ず提案→確認→書き込み**（`--autonomous` でも生成物の書き込み確認は解除しない）。ドラフト作成は subagent に dispatch し、親は長文を抱えない（context-minimal）。

## 入力

- 自由記述の説明（例「80年代の音楽を理解しているレビュアー」「決済まわりに厳しいシニア」）。
- `--name <id>`（任意）：保存ファイル名／persona 名。省略時は説明から **slug を提案**する（例「80年代の音楽…」→ `music-era-80s-reviewer`、「セキュリティに厳しいシニア」→ `strict-security-senior`）。
- `--user`：global（user 層）に保存。既定は project（product 単位）。

## 保存先（tier・§5 と整合）

| スコープ | パス |
|---|---|
| project（既定・product 単位） | `<repo>/.claude/rig/personas/<name>.md` |
| user（`--user`・global） | `~/.claude/rig/personas/<name>.md` |

## 手順

1. **名前確定** — `--name` が無ければ説明から slug を1つ提案する（英小文字・ハイフン）。
2. **ドラフト生成**（subagent）— 既存 persona facet の形式で起草する：
   ```
   # persona: <name>

   ## facet: persona / <name>

   <この人格は何者か。1〜2文。>

   ### 観点 / 判断軸
   - <レビュー時に何を見るか。具体的な着眼点を箇条書き>

   ### 語り口
   - <どう指摘するか。トーン>

   ### 振る舞い
   - <REJECT/承認の基準。何を良し悪しとするか>
   ```
   - **判断・観点に徹する**（人格と着眼点）。長大な事実の列挙で埋めない（事実知識は将来 wiki 参照＝v2 Phase 2 に委ねる。現状は要点のみ）。
   - 説明に無いことを**捏造しない**。実在しない専門用語・出典をでっち上げない。
3. **提案** — 保存先パスとドラフト全文を提示する。`--user`（global）の場合は「**全プロジェクトに影響する global 書き込み**」と明示する。
4. **確認** — ユーザー承認後にのみ書き込む（`--autonomous` でも確認必須）。同名が既存なら**上書きせず**差分提案にとどめる（冪等・非破壊）。
5. **報告と使い方案内** — 書き込んだパスを報告し、使い方を示す：
   - `/rig:dev --only review --persona <name>`（review に投入）
   - または recipe の `personas:` に `<name>` を追加。
   - 整合確認は `/rig:dev --validate`。

## 原則

- 生成物は persona facet（subagent の System に合成される）。native agent（subagent_type）は生成しない（v2 非スコープ）。
- 書き込み＝確認必須・冪等・捏造禁止。global 書き込みは特に明示。
- engine のフローは変えない。これは「ブリックを増やす」ジェネレータであって engine 改修ではない。
