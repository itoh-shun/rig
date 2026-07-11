# instruction: parallel-review

レビュー対象の diff またはファイル列を受け取り、security / design / test の3観点を並列に評価して着手判断を得る。

## 手順

### ① 対象の収集

`git diff` または対象ファイル列を取得し、レビュアーへ渡すコンテキストを確定する。

### ② 並列レビューの dispatch（`pattern: parallel-fanout`）

`pattern: parallel-fanout` に従い、1メッセージで3つの subagent を同時に起動する。

- **security 観点**: `agents/security-reviewer` が存在する場合はそれを使う。使えない場合は `facets/personas/security-reviewer` を合成して subagent に渡す。
- **design 観点**: `agents/design-reviewer` が存在する場合はそれを使う。使えない場合は `facets/personas/design-reviewer` を合成して subagent に渡す。
- **test 観点**: `agents/test-reviewer` が存在する場合はそれを使う。使えない場合は `facets/personas/test-reviewer` を合成して subagent に渡す。

**追加観点（任意）**: 変更の性質に応じて `--persona` / manifest `default_personas` / recipe `personas[]` で fan-out に追加できる（同経路で dispatch・dedup は §5）。shipped の追加枠：

- **performance 観点**（`performance-reviewer`）: ホットパス・データ量スケールに触れる変更（クエリ・ループ・キャッシュ・大量データ処理）に推奨。
- **observability 観点**（`observability-reviewer`）: 本番運用に影響する変更（エラーハンドリング・ログ・監視対象の挙動・デプロイ手順が要る変更）に推奨。
- **api-compat 観点**（`api-compat-reviewer`）: 公開 API・スキーマ・設定キー・CLI フラグに触れる変更（破壊的変更・semver・非推奨手順）に推奨。
- **migration 観点**（`migration-reviewer`）: DB/データ移行を含む変更（往路と復路・expand-contract・ロック時間・データ検証）に推奨。
- **docs 観点**（`docs-reviewer`）: 公開挙動を変える変更（README/CHANGELOG/コメント/設定例が虚偽化していないか）に推奨。

**観点カタログの注入**: dispatch する各 reviewer について、同名の persona facet が `inject: ["[[slug]]"]` を宣言していれば、wiki ページを tier 解決して Knowledge 位置へ注入する（**agent 経由の dispatch でも同様**＝agent と persona で知識が非対称にならない）。

**suppression の注入（`facets/policies/suppression-memory`）**: `.rig/review-suppressions.jsonl` に有効な suppression があれば、各 reviewer prompt へ「このリポジトリで検証済みの非問題 — 該当コードに実質的変更が無い限り再指摘しない」として注入する（照合・ライフサイクルは同 policy が正本）。

各 subagent の出力形式は `output-contracts/review-verdict` に従わせること。

### ③ 集約（`pattern: review-gate`）

3つの verdict が揃ったら `pattern: review-gate` で着手判断を決定する。`--verify-findings`（または recipe `verify_findings: true`）が有効なら、集約前に各 REJECT 根拠・マージ前必須条件を `finding-verifier` で反証し、REFUTED をゲートから除く（`patterns/review-gate`「敵対的検証」）。

**検証結果の記録（`facets/policies/suppression-memory`）**: `finding-verifier` が **REFUTED** と判定した所見（および user が明示的に却下した条件）は `.rig/review-suppressions.jsonl` へ追記する（次回 dispatch から再指摘を抑止）。逆に、既存 suppression にマッチする所見を verifier が **UPHELD** と判定した場合は、所見をサイレントに落とさずゲートへ通し、当該 suppression に期限切れフラグを付ける（upheld な所見 > suppression）。

**リプレイ・アーカイブ（任意）**: 実行した diff と各 verdict を `.rig/replay/<ts>/` に保存してよい（`runs.jsonl` と同格のローカル実行ログ・承認不要）。`/rig:drill --replay <persona>` がペルソナ編集後の回帰確認に使う。

### ④ 実装方針への反映

`review-gate` が出力した統合 conditions を実装計画の必須条件として折り込み、実装フェーズへ進む。
