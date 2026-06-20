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

各 subagent の出力形式は `output-contracts/review-verdict` に従わせること。

### ③ 集約（`pattern: review-gate`）

3つの verdict が揃ったら `pattern: review-gate` で着手判断を決定する。

### ④ 実装方針への反映

`review-gate` が出力した統合 conditions を実装計画の必須条件として折り込み、実装フェーズへ進む。
