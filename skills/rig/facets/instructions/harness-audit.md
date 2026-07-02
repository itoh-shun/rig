# instruction: harness-audit

プロジェクトの**ハーネス監査**の routing。診断の作法（2×2 分類・穴の見方）は委譲先 persona（`harness-auditor`）と knowledge（`harness-taxonomy`）が持つのでここには再掲しない（Native-first）。対象は「エージェントで開発する仕組み」全体＝ルールファイル・hook・テスト/lint/型・CI・MCP・rig 設定。

**スコープ**: 「ハーネスエンジニアリング」の 2×2（計算的/推論的 × ガイド/センサー）でプロジェクトを棚卸しし、**空の象限**と**効いていない資産**（あるのにループに繋がっていない）を炙り出す。新ルールを足すのが目的ではない＝**繋ぐ・強制する・薄くする**を出す。

## 手順

1. **対象確定** — 既定はカレントリポジトリ。長い設定・ログは親 context に引き込まず subagent に要点抽出させる（context-minimal）。
2. **knowledge 注入必須** — `knowledge/harness-taxonomy` を Knowledge 位置に注入して `harness-auditor` を合成（agent が無ければ `facets/personas/harness-auditor`）。
3. **棚卸し** — ハーネス要素を集める（存在と、それが**どう繋がっているか**の両方）：
   - 推論的ガイド: `CLAUDE.md`/`AGENTS.md`、`.claude/skills`、設計ドキュメント、rig の persona/instruction。
   - 推論的センサー: PR レビュー運用、`review-gate`/AI レビュー、独立性（生成者と別か）。
   - 計算的ガイド: 型設定（tsconfig strict 等）、scaffold/テンプレ、CLI/codemod、LSP。
   - 計算的センサー: `package.json` scripts（lint/typecheck/test/build）、CI 設定、**それが hook/acceptance-gate に繋がっているか**。
   - 強制点: `.claude/settings.json` の hooks（PreToolUse/PostToolUse 等）、CI の必須チェック。
4. **2×2 分類と穴出し** — 各象限を埋め、空象限と「あるのに効いていない」資産を重い順に指摘（計算的センサーのループ外＝最優先）。**prose 止まりのルールは未強制として扱う**。
5. **手を出す** — 各穴に「繋ぐ/強制する/薄くする」の具体策。rig 連携の手があれば示す（例: 機械検証を `acceptance-gate` の基準に追加、`/rig:goal` の独立検証、hook で test を強制）。**新ルール追加は最後**。
6. **出力** — `output-contracts/harness-map`（総合行＋2×2 表＋穴＋最優先で繋ぐ1手）。

## ガード

- **「ある」と「効いている」を区別**（存在≠強制）。テストが実行ループのバックプレッシャーになっているかを見る。
- **足すより繋ぐ・強制する・薄くする**を優先（善意のルール追加の逆効果・Context Rot を警戒）。
- **計算的センサーを一次**に推す（LLM レビューは sweet-talk されうる・失敗テストは交渉できない）。
- **根拠は具体箇所**。未確認は「未確認」と書く（捏造しない）。良い所は認める。
- 監査は read-only（設定を勝手に書き換えない）。修正の実装は `/rig:dev` や hook 設定へ委譲。

## 処方箋の接続（ギャップ駆動の能力調達）

監査で見つかった**空象限**・「あるのに効いていない資産」への処方は「足すより繋ぐ/強制する/薄くする」が原則だが、**繋ぐ先の部品自体が無い**ギャップには `/rig:import --discover "<足りない能力>"` を提案する（探す→無ければ `/rig:persona`/`/rig:forge` で作る）。実行データ側のギャップは `python3 scripts/orchestrate.py runs` の**ギャップ処方箋**（同一 step のエスカレーション反復検出）が同じ提案を出す＝**監査（静的）とテレメトリ（動的）の両方から能力調達へ繋がる**。
