# rig

## 概要

ブリック（facet / pattern / step）を起動時に動的に組み合わせ、タスクに最適化されたエージェント・ハーネスを engineering する汎用開発フロー・オーケストレータ。3-Stage フロー（計画→実装→検証）は数あるレシピの1つに過ぎず、プラグインそのものは特定フローに縛られない。Claude Code ネイティブ（command + skill + agents）として動作し、重い DSL エンジンや外部依存は持たない。ブリックを追加するだけで任意のフローを組み立てられる軽量な設計を原則とする。

## install

本リポジトリには `.claude-plugin/marketplace.json` を同梱しているので、marketplace 経由でインストールできる（CC のバージョンにより手順が異なる場合がある）。プラグイン名は `rig`、marketplace 名は `itoshun-local-plugins`。

### 方法 A: GitHub から（推奨）

```bash
/plugin marketplace add itoh-shun/rig
/plugin install rig@itoshun-local-plugins
```

`owner/repo` 形式で marketplace を追加し、`rig@<marketplace 名>` で install する。

### 方法 B: ダウンロード（ZIP / clone）から

```bash
# リポジトリの ZIP を展開、または git clone した後、その展開フォルダを指定：
/plugin marketplace add /path/to/rig
/plugin install rig@itoshun-local-plugins
```

展開フォルダ内の `.claude-plugin/marketplace.json` が読み込まれる。

### 方法 C: --plugin-dir（開発・テスト用、反復が速い）

```bash
cd /path/to/rig
claude --plugin-dir .
# 編集後の再読み込み: /reload-plugins
```

### 呼び出し（namespace に注意）

スラッシュコマンドはプラグイン名で namespace される：

- **コマンド**: `/rig:dev` — 引数つきで叩く入口。例: `/rig:dev --plan --only review "現在の変更"`
- **skill**: `/rig:rig` — 「実装したい」「レビューして」等の発話で**自動想起**もされる（エンジン本体）

## ブリック目録

### agents（native 委譲先・優先）

| 名前 | パス | 役割 |
|---|---|---|
| `security-reviewer` | `agents/security-reviewer.md` | セキュリティ観点の read-only レビュー |
| `design-reviewer` | `agents/design-reviewer.md` | 設計・アーキテクチャ観点のレビュー |
| `test-reviewer` | `agents/test-reviewer.md` | テスト品質観点のレビュー |

### facets/personas（agent フォールバック）

| 名前 | パス |
|---|---|
| `orchestrator` | `skills/rig/facets/personas/orchestrator.md` |
| `implementer` | `skills/rig/facets/personas/implementer.md` |
| `security-reviewer` | `skills/rig/facets/personas/security-reviewer.md` |
| `design-reviewer` | `skills/rig/facets/personas/design-reviewer.md` |
| `test-reviewer` | `skills/rig/facets/personas/test-reviewer.md` |
| `debugger` | `skills/rig/facets/personas/debugger.md` |

### facets/instructions（薄い委譲）

| 名前 | パス |
|---|---|
| `parallel-review` | `skills/rig/facets/instructions/parallel-review.md` |
| `intake` | `skills/rig/facets/instructions/intake.md` |
| `design` | `skills/rig/facets/instructions/design.md` |
| `implement` | `skills/rig/facets/instructions/implement.md` |
| `verify` | `skills/rig/facets/instructions/verify.md` |
| `visual-verify` | `skills/rig/facets/instructions/visual-verify.md` |
| `pr` | `skills/rig/facets/instructions/pr.md` |
| `merge` | `skills/rig/facets/instructions/merge.md` |

### facets/policies（末尾注入のガードレール）

| 名前 | パス |
|---|---|
| `pr-hygiene` | `skills/rig/facets/policies/pr-hygiene.md` |
| `branch-strategy` | `skills/rig/facets/policies/branch-strategy.md` |
| `ci-cost` | `skills/rig/facets/policies/ci-cost.md` |
| `pre-push-review` | `skills/rig/facets/policies/pre-push-review.md` |
| `risk-based-testing` | `skills/rig/facets/policies/risk-based-testing.md` |

### facets/output-contracts

| 名前 | パス |
|---|---|
| `review-verdict` | `skills/rig/facets/output-contracts/review-verdict.md` |

### facets/knowledge

| 名前 | パス |
|---|---|
| `_layer`（構造定義） | `skills/rig/facets/knowledge/_layer.md` |
| `harness-engineering` | `skills/rig/facets/knowledge/harness-engineering.md` |
| `orchestration-patterns` | `skills/rig/facets/knowledge/orchestration-patterns.md` |

### patterns（制御フロー）

| 名前 | パス |
|---|---|
| `parallel-fanout` | `skills/rig/patterns/parallel-fanout.md` |
| `review-gate` | `skills/rig/patterns/review-gate.md` |
| `structured-report` | `skills/rig/patterns/structured-report.md` |
| `serial` | `skills/rig/patterns/serial.md` |
| `autonomous-loop` | `skills/rig/patterns/autonomous-loop.md` |
| `monitor` | `skills/rig/patterns/monitor.md` |
| `workflow-backend` | `skills/rig/patterns/workflow-backend.md` |
| `acceptance-gate` | `skills/rig/patterns/acceptance-gate.md` |

### recipes（step の束）

| 名前 | パス | 概要 |
|---|---|---|
| `review-only` | `skills/rig/recipes/review-only.md` | review step のみ実行 |
| `release-flow` | `skills/rig/recipes/release-flow.md` | 実装→検証→レビュー→PR→マージのフルフロー |
| `design-first` | `skills/rig/recipes/design-first.md` | 設計フェーズ優先フロー |
| `hotfix` | `skills/rig/recipes/hotfix.md` | 緊急修正向け軽量フロー |

### manifests

| 名前 | パス | 概要 |
|---|---|---|
| `_template` | `skills/rig/manifests/_template.md` | manifest スキーマ全体定義 |

## flag 一覧

| flag | 意味 |
|---|---|
| `--issue <id>` | 対象 Issue を指定（intake の入力） |
| `--design` | design step を ON にする |
| `--visual` | visual 確認（スクリーンショット等）を ON |
| `--review` | review step を ON にする |
| `--tdd` | implement を TDD（red-green-refactor）で行う |
| `--autonomous` | step ゲートを省き自律実行（capture ゲートは解除されない） |
| `--plan` | COMPOSE まで実行し、合成ハーネスを提示して停止（実行しない） |
| `--only <step>` | 指定 step だけを実行 |
| `--from <step>` | 指定 step から最後まで実行 |
| `--recipe <name>` | shipped / user / project のいずれかの recipe を名前で指定 |
| `--save-recipe <name>` | 今回合成したハーネスを recipe として保存（既定: project 層） |
| `--save-recipe <name> --user` | user 層（`~/.claude/rig/recipes/<name>.md`）に保存 |
| `--workflow` | 実行バックエンドを Workflow（ultracode）に切り替える。**明示 opt-in 必須** |
| `--capture` | capture（knowledge への蓄積）の確認ダイアログを省略（提案表示と事後報告は省略しない） |

## クイック例

```bash
# 現在の変更を計画フェーズからレビューする
/rig:dev --plan "現在の変更をレビュー"

# レビューフェーズのみを単独実行する
/rig:dev --only review

# リリースフロー・レシピで機能Xを処理する
/rig:dev --recipe release-flow "機能X"

# 重い多段 fan-out に Workflow バックエンドを使う（明示 opt-in）
/rig:dev --workflow --recipe release-flow "大規模リファクタ"

# 知識蓄積を確認ダイアログなしで実行する
/rig:dev --capture --recipe release-flow "機能Y"
```

## ドキュメント

- `docs/testing-scenarios.md` — ディシプリン圧力シナリオ集（rationalize パターンと GREEN 応答の対比）
- `skills/rig/SKILL.md` — エンジン本体（PARSE / RESOLVE / COMPOSE / RUN の全仕様 + rationalization 表 + red flags）
