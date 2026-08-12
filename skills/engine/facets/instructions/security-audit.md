# instruction: security-audit

## facet: instruction / security-audit

コンポーネント（ファイル群・モジュール・機能）を**能動的に監査**する探索フローの薄い手順書。diff レビュー（`review-only`）でも single-diff の `security_review` でもなく、**「今あるコードのどこが実際に突けるか」を攻撃者視点で洗い出す** read-only 監査。書き込みも worktree も作らない。

### 前提（倫理境界を最初に確認）

- 対象は**自プロダクトのコード**、または**明示的に検証を許可されたローカル/ステージング環境**。スコープを1行で宣言してから始める。
- 静的解析＋ローカル検証まで。**動いているサービスへ攻撃トラフィックを送らない**（DAST は既定で範囲外。`.rig/security-targets.json` の allowlist がある場合のみ、そのホストに限る）。

### 手順

1. **スコープ確定** — 監査対象（パス/モジュール/機能）と境界を宣言。「何を守るか（資産）」を1行。
2. **第一報（先出し）** — 探索と fan-out に入る**前に**、対象を取得しただけで読み取れる範囲を数行で報告する。変更起点なら `git diff` / `gh pr diff`、コンポーネント指定なら対象ディレクトリのファイル一覧から、「どのファイルがどんな入力を受け、どこが攻撃面になりそうか」を出す。
   - **上限**: 第一報の前は、対象取得（`git diff` / `gh pr diff` / ファイル一覧）を含めて Read/Grep/Glob 等の tool 呼び出しを**5回以内**に留める。上限であってノルマではない（1回で出せるなら1回でよい）。
   - **第一報の所見は全て Suspected 扱い**。手順5で PoC が刺さって初めて Confirmed へ昇格する。根拠の無い Critical/High をここで書かない（`output-contracts/security-findings` の低確信 Critical 禁止はこの段でも効く）。
   - **判定ではなくプレビュー**。`review-gate` や acceptance の根拠にしない。後続の探索で補強しても**撤回してもよく、撤回は失点ではない**。
   - 探索の途中経過は user に届かない——**沈黙のまま探索を続けない**。
3. **threat-model（`threat-modeler`）** — 信頼境界とデータフローを地図化し、STRIDE で優先順位付き脅威リストを作る。ここで「どこを攻めるか」を絞る。
4. **決定論センサー（任意・あれば強い）** — プロジェクトが用意していれば外部ツールの出力を取り込む：
   - SAST: `semgrep --json` → `python3 scripts/sast_adapter.py semgrep <out.json>`
   - SCA: `pip-audit --format json` / `npm audit --json` / `trivy fs --format json` → `sast_adapter.py <tool> <out.json>`
   - 秘密: `workbench.py scan-secrets`
   これらは「機械が拾える面」を潰し、人間/AI の判断を**判断が要る面**に集中させる。rig 自身はツールを実行しない（出力を渡す設計＝#276）。
5. **exploit 探索（`exploit-researcher`・並列可）** — 優先脅威ごとに `attack-catalog` の技法で刺さる経路を試す。**刺さったものだけ Confirmed**、疑わしいだけなら Suspected。
6. **集約レポート** — `output-contracts/security-findings` に従い、severity 順・証拠アンカー付きで報告。Confirmed には攻撃シナリオ・PoC・root cause・canonical な修正案を必須にする。第一報から**降格・撤回した所見があればその旨を1行で書く**（黙って消さない）。

### 出口

- 監査は所見の提示で終わる（read-only）。修正まで進めるなら `pentest-fix`（各 Confirmed を gated 実装＋re-exploit で塞ぐ）へ橋渡しする。
- 一時ファイル（ツール出力・スクラッチ）は破棄し、本物のコードベース・履歴を汚さない。
