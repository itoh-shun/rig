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

各 subagent の出力形式は `output-contracts/review-verdict` に従わせること。

### ③ 集約（`pattern: review-gate`）

3つの verdict が揃ったら `pattern: review-gate` で着手判断を決定する。

### ④ 実装方針への反映

`review-gate` が出力した統合 conditions を実装計画の必須条件として折り込み、実装フェーズへ進む。
