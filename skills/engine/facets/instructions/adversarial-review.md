# instruction: adversarial-review

敵対的レビューの **routing**。各ペルソナの評価軸は委譲先（agent / persona facet）が持つので、ここには再掲しない（Native-first）。

**標準スコープ**: AI の癖（AI-slop）排除 / 人間可読性 / 不要コメント・dead code 除去 / 周辺コードとの一貫性。

## 手順

1. 対象 diff / ファイル列を収集する（親 context に全文を引き込まない）。

**第一報（先出し）**: ② の dispatch に入る**前に**、取得した対象だけで読み取れる範囲を数行で報告する（変更ファイルと規模・AI-slop / 可読性の観点で特に見に行く箇所）。

- **上限**: 第一報の前は、対象取得（`git diff`）を含めて Read/Grep/Glob 等の tool 呼び出しを**5回以内**に留める。上限であってノルマではない（1回で出せるなら1回でよい）。
- **判定ではなくプレビュー**。`review-gate` / acceptance の根拠にしない。後続の verdict で補強しても**撤回してもよく、撤回は失点ではない**。断定できないことは「未確認」と書く。
- reviewer は subagent なので途中出力は user に届かない——**fan-out の待ち時間を沈黙で埋めない**。

2. `lazy-senior` / `cognitive-economist` を `patterns/parallel-fanout` で並列起動する。**agent 優先**（subagent_type: `lazy-senior-reviewer` / `cognitive-economist-reviewer`）、無ければ `facets/personas/{lazy-senior,cognitive-economist}` を合成して subagent に渡す。
3. **ai-quirks 知識層（§5 COMPOSE の知識注入）を必ず効かせる** — AI の癖を体系的に排除するのがこのレビューの主目的。記述形を Knowledge に、導出規範形を Policy（末尾）に注入。
4. `patterns/acceptance-gate`（`review-gate` を内包）で「**AI-slop 指摘 0・人間可読・不要コメント無し**」へ収束させる。未達なら指摘反映で再走、最大 K 回でユーザーへエスカレーション。**ゲートに入る前に各 verdict 行（persona 名・判定・1行根拠）をそのまま中継する** — バリア構造は変えない（判断はゲートで行う）。再走した場合も各ラウンドで中継し、沈黙のまま回さない。
5. 各 reviewer の出力は `output-contracts/review-verdict` で集約する。
