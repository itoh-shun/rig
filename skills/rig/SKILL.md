---
name: rig
description: Use when you need dev-flow orchestration in Codex or Claude Code — implementing a feature, clearing an issue, reviewing current changes, completing a PR, going design-to-implementation, TDD, quality-gated workbench runs, or composing a flow. 開発フローのオーケストレーション（Codex / Claude Code での実装着手 / Issue 対応 / 変更レビュー / PR 完了 / 設計→実装 / TDD / workbench / フロー組み立て）が要るとき、または `$rig` / `/rig:go`（互換エイリアス `/rig:rig`） / `/rig:dev` が呼ばれたとき。
---

# rig

## 1. Overview

ブリック（facet / pattern / step / agent / recipe）を**起動時に組み合わせて**タスク専用のエージェント・ハーネスを engineering する、レゴ式ハーネス・コンポーザ。固定ワークフローではなく **PARSE → RESOLVE → COMPOSE → RUN** の4段で都度ハーネスを合成する。intake→design→implement→verify→review→pr→merge の「3-Stage フルフロー」は数ある recipe の1つにすぎない。

**determinism-by-gate**: 非決定的な agent 実行を決定的な受け入れゲート（`patterns/acceptance-gate`）で挟み、経路は変動しても**毎回同じ品質**へ収束させる。これが rig の品質保証の核。

### Codex 入口

Codex では `$rig` が Claude Code の `/rig:go` に相当する入口。slash command や `Agent` ツールが無い環境では、本文中の `/rig:*` は「この skill の該当 pack / instruction を使う」と読み替え、subagent dispatch は Codex の並列作業・`codex exec` provider・または `scripts/orchestrate.py` / `scripts/workbench.py` の決定論 runner で代替する。

- 自然文タスク: `$rig "fix the login bug"` として扱い、§2 の workbench pack と `facets/instructions/workbench` を読む。
- 低レベル指定: `$rig --recipe review-only --only review` のように、§3 の flag / recipe 解決規則をそのまま使う。
- runner を使える場合: `python3 scripts/orchestrate.py ...` または `rig-wb ...` を優先し、Codex verifier は `codex exec --sandbox read-only` で読み取り専用にする（`scripts/orchestrate.py build_argv` が正本）。
- Claude Code 固有の文言（`/rig:*`, `Agent`, `Task`, plugin command）は互換表現として扱い、Codex で同じ安全条件（隔離 worktree・acceptance-gate・明示 accept/discard）を満たす形に置き換える。

## 2. ブリック目録

| 種別 | 役割 | 現在の在庫 |
|---|---|---|
| **agent**（native 委譲先・優先） | read-only reviewer。専用 context・tool 制限つきで起動 | `agents/security-reviewer` `agents/design-reviewer` `agents/test-reviewer` `agents/performance-reviewer` `agents/observability-reviewer` `agents/api-compat-reviewer` `agents/migration-reviewer` `agents/docs-reviewer` `agents/finding-verifier` `agents/lazy-senior-reviewer` `agents/cognitive-economist-reviewer` |
| **persona facet**（agent フォールバック） | reviewer 人格。agent が無い時 subagent prompt の System に合成 | `facets/personas/security-reviewer` `facets/personas/design-reviewer` `facets/personas/test-reviewer` `facets/personas/performance-reviewer` `facets/personas/observability-reviewer` `facets/personas/api-compat-reviewer` `facets/personas/migration-reviewer` `facets/personas/docs-reviewer` `facets/personas/finding-verifier` `facets/personas/orchestrator` `facets/personas/implementer` `facets/personas/debugger` `facets/personas/lazy-senior` `facets/personas/cognitive-economist` `facets/personas/cross-llm-reviewer` |
| **instruction facet**（薄い委譲） | 手順の routing。既存 skill/command/agent に委譲する thin な指示 | `facets/instructions/parallel-review` `facets/instructions/intake` `facets/instructions/design` `facets/instructions/implement` `facets/instructions/verify` `facets/instructions/visual-verify` `facets/instructions/pr` `facets/instructions/merge` `facets/instructions/adversarial-review` |
| **output-contract facet** | subagent 出力の機械抽出可能フォーマット定義 | `facets/output-contracts/review-verdict`（着手判断の集約用・既定） `facets/output-contracts/review-findings`（severity・file:line・Blocking/Non-blocking を明示する詳細版。`/rig:drill` と厳しめレビュー依頼で使用） |
| **policy facet** | 末尾注入のガードレール | `facets/policies/pr-hygiene` `facets/policies/pre-push-review` `facets/policies/ci-cost` `facets/policies/branch-strategy` `facets/policies/risk-based-testing` `facets/policies/cross-llm-legibility` |
| **knowledge facet** | subagent prompt に注入する知識層ブリック | `facets/knowledge/orchestration-patterns` `facets/knowledge/harness-engineering` `facets/knowledge/_layer` |
| **wiki**（shipped tier・persona が `inject:` で参照） | 観点カタログの正準ページ（`_wiki` スキーマ・`sources`/`reviewed_at` 必須） | `facets/knowledge/wiki/{loop-engineering,appsec-checklist,injection-patterns,migration-expand-contract,performance-pitfalls,observability-golden-signals,api-compat-semver,license-compat-basics}` |
| **pattern**（制御フロー） | step の実行制御テンプレ | `patterns/parallel-fanout` `patterns/review-gate` `patterns/structured-report` `patterns/serial` `patterns/autonomous-loop` `patterns/monitor` `patterns/workflow-backend` `patterns/acceptance-gate` |
| **recipe**（step の束） | step＋pattern＋facet を固定したテンプレ workflow | `recipes/review-only` `recipes/release-flow` `recipes/design-first` `recipes/hotfix` `recipes/debug` `recipes/fast-bugfix` `recipes/max-bugfix` `recipes/adversarial-review`（dev-core 8 件。pack 追加分は下記） |
| **manifest** | プロジェクト設定・既定値テンプレ | `manifests/_template` |
| **step** | フローの単位。instruction facet として library 化済み | intake / design / implement / verify / visual-verify / pr / merge（parallel-review を含む全 8 件） |

> ブリック参照は skill ディレクトリ相対（facets/ patterns/ recipes/ manifests/）。agent はファイルパスでなく subagent_type 名で起動。
>
> **上表は engine / dev-core 在庫。** ドメイン/モード pack は engine を改変せず**ブリックを上乗せ**する（§8 Native-first）。**新 pack を足したらこの pack 追加分に追記する**（dev-core 行は安定させる）。`--validate` はこの目録と実ファイルの突き合わせを検査する。
>
> **pack 追加分（engine 不変で上乗せ）:**
>
> | pack | 追加ブリック |
> |---|---|
> | **sales**（`/rig:sales`） | **商談レビュー**: persona `facets/personas/sales/{hearing,needs,proposal,closing,next-action}-reviewer`＋追加枠 `facets/personas/sales/objection-handler` ／ instruction `facets/instructions/deal-review` ／ output-contract `facets/output-contracts/deal-verdict` ／ recipe `recipes/deal-review` ／ knowledge `facets/knowledge/sales-domain/`。**資材生成（`--material`/`--script`）**: persona `facets/personas/sales/{material-writer,cold-caller}` ／ instruction `facets/instructions/{sales-material,call-script}` ／ output-contract `facets/output-contracts/sales-collateral` ／ recipe `recipes/sales-enablement`（実在機能のみ・誇張禁止。詳細は各 instruction が正本） |
> | **talk**（`/rig:talk`） | persona `facets/personas/talk-assistant` ／ instruction `facets/instructions/talk-loop`（recipe なし＝既存コマンドへ委譲） |
> | **goal**（`/rig:goal`） | persona `facets/personas/goal-driver`（`inject: [[loop-engineering]]`） ／ instruction `facets/instructions/goal-loop` ／ wiki `facets/knowledge/wiki/loop-engineering` ／ policy `facets/policies/independent-verification`（採点者≠生成者） ／ recipe `recipes/goal-loop`（高レベル目標→受け入れ基準→達成までループ。loop engineering＝harness の1つ上の層。**詳細は goal-loop.md が正本**） |
> | **loop**（`/rig:loop`） | instruction `facets/instructions/loop-driver` ／ pattern `patterns/autonomous-loop`（`ScheduleWakeup` 再利用） ／ recipe `recipes/loop`（**繰り返し/監視ループ**＝goal の対極。「いつまた回すか」を担う watch/poll/repeat。停止条件 `--until`/`--times`/明示・安全上限必須・各 tick 報告・時間駆動は 270/1200・300 禁忌。`--every` で `/rig:goal` を定期キックする等、外側スケジューラとして重ねられる） |
> | **task-plan**（`/rig:tasks`） | persona `facets/personas/planner` ／ instruction `facets/instructions/task-plan` ／ output-contract `facets/output-contracts/task-plan` ／ recipe `recipes/task-plan`（依頼を検証可能な小タスクに割ってから実装＝plan→implement→verify→review。**詳細は task-plan.md が正本**） |
> | **brainstorm**（`/rig:brainstorm`） | persona `facets/personas/brainstormer` ／ instruction `facets/instructions/brainstorm` ／ output-contract `facets/output-contracts/design-brief` ／ recipe `recipes/brainstorm`（**設計の壁打ち**＝ラフな着想を質問→代替案→セクション合意で固める実装の前段。決め打ちせず問う・代替を必ず見る・節ごとに承認・未解決は捏造で埋めず明示・実装には踏み込まない。`design-brief` に収束し brainstorm→tasks→dev と前段から繋がる） |
> | **pr-review**（`/rig:pr`） | instruction `facets/instructions/pr-review` ／ recipe `recipes/pr-review`（reviewer agent・persona・`review-verdict` は dev 共用） |
> | **workbench**（`/rig:go`・品質保証つき統一入口。`/rig:rig` は互換エイリアス） | pattern `patterns/isolated-worktree`（task ごとの隔離 worktree・run state 設計の正本）＋`patterns/visual-artifacts`（視覚検証スクリーンショットの置き場・処分ルールの正本。task-scoped は `.rig/runs/<task-id>/visual/`、ad-hoc は `.rig/visual/adhoc/<ts>-<slug>/`・discard で即時削除・`workbench.py gc` で既定14日の age-based 処分） ／ runner `scripts/workbench.py`（task-id 発行・worktree 作成・選択理由バナー・accept_requirements チェックリスト・acceptance-gate 記録・accept/discard（visual/ も削除）・gc・review verdict 記録・stats 集計の決定論実装。舵をコードに＝`patterns/computational-orchestration` と同思想） ／ instruction `facets/instructions/workbench`（自然文→task_type 分類→recipe 自動選択→隔離 worktree RUN→gate 判定の5段。正本）＋`facets/instructions/workbench-ops`（`status`/`diff`/`accept`/`discard`/`log`/`board`/`stats`/`review` サブコマンド。`board`＝全 task の単一ダッシュボード——`/rig:queue go --provider rig` で並列 dispatch した task も同じ一覧に出る）＋`facets/instructions/gh-flow`（`gh issue`/`gh pr review`/`gh pr fix`/`gh ci`。PR review は既存 `pr-review` へ委譲、CI 失敗は `tests_pass_or_explained` 基準に接続）＋`facets/instructions/acceptance-check`（criterion 判定を `workbench.py gate` に記録する薄い委譲）＋ workbench recipe 専用の薄い instruction 群 `facets/instructions/{identify-behavior-boundaries,compare-behavior,identify-audience,docs-draft,verify-commands,update-docs}` ／ recipe `recipes/{bugfix,feature,refactor,documentation}`（task_type 自動選択の既定4本。`review`/`security_review`/`investigation`/`design`/`release_support` 等は既存 recipe（`review-only`/`pr-review`/`debug`/`design`/`release-flow`）へ橋渡し＝重複実装しない）。**受け入れ基準 ID の正本は `scripts/workbench.py`**（`gates` サブコマンドで一覧表示・`standard`＋task_type 別（`bugfix`/`feature`/`refactor`/`review`/`security`）の合成プリセット）。gate 全体の状態は `passed`/`passed_with_warnings`/`failed`/`pending`/`skipped`。「できました」の自己申告だけで完了扱いにしない＝`workbench.py accept` は `failed`/`pending` を機械的に拒否し、`worktree_exists`/`base_branch_recorded`/`diff_summary_generated` の構造的前提は `--force` でも上書きできない。`workbench.py review`（persona 別 verdict の記録）＋`stats`（recipe/gate 別集計・**REJECT ゼロが続く reviewer へのゴム印警告**）で運用の実測にも接続する。 |
> | **de-ai-smell**（`/rig:dev --recipe de-ai-smell`） | persona `facets/personas/ai-smell-reviewer` ／ instruction `facets/instructions/de-ai-smell` ／ knowledge `facets/knowledge/ai-writing-smells` ／ recipe `recipes/de-ai-smell`（散文の AI 臭除去。深層 A〜V マーカー＋**5観点スコア定量ゲート**（立場/リズム/主体性/具体性/削減・<35/50で書き直し）＋**名指し語彙ブラックリスト**（偏愛語/横文字メタファー/ジャーゴンを置換例つきで弾く）。`review-verdict` は dev 共用） |
> | **sns-x**（`/rig:dev --recipe sns-x-post`） | persona `facets/personas/sns-post-reviewer` ／ instruction `facets/instructions/sns-post` ／ knowledge `facets/knowledge/sns-x-conventions` ／ recipe `recipes/sns-x-post`（X 半自動ポスト運用。声 persona は運用者が `/rig:persona`＋`default_personas` で投入。de-ai-smell・`review-verdict` 共用） |
> | **magi**（`/rig:magi`） | persona `facets/personas/magi/{melchior,balthasar,casper}` ／ instruction `facets/instructions/magi-deliberation` ／ pattern `patterns/magi-consensus`（多数決合議ゲート） ／ output-contract `facets/output-contracts/magi-verdict` ／ recipe `recipes/magi`（エヴァ MAGI 模倣の3賢者 decision モード。正しさ/守り/価値の直交3観点で go/no-go を多数決裁定） |
> | **roast**（`/rig:roast`・humor） | persona `facets/personas/roast-reviewer` ／ instruction `facets/instructions/roast` ／ recipe `recipes/roast`（毒舌ロースト・レビュー。`review-verdict`/`review-gate` は dev 共用。中身は本物のレビューで配送をユーモアに振る adversarial-review 変種） |
> | **coin**（`/rig:coin`・humor） | persona `facets/personas/coin-flipper` ／ instruction `facets/instructions/coin-flip` ／ recipe `recipes/coin`（可逆で些末な決定を即断する反-bikeshed ゲート。重い/不可逆はトリアージで弾いて magi へ。magi の対極） |
> | **duck**（`/rig:duck`・humor） | persona `facets/personas/rubber-duck` ／ instruction `facets/instructions/duck-debug` ／ recipe `recipes/duck`（ラバーダック・デバッグ。アヒルが質問だけで本人に気づかせる会話モード。コードも答えも出さない・実証済み技法） |
> | **drill**（`/rig:drill`・measurement） | persona `facets/personas/strict-senior-engineer`（correctness/maintainability/security/testability 優先・Blocking/Non-blocking 分離。「厳しめにレビューして」依頼でも使用） ／ output-contract `facets/output-contracts/review-findings`（severity・file:line・Blocking/Non-blocking を強制。drill 実行時は fan-out する reviewer 全員がこの contract に一時上書きされる） ／ instruction `facets/instructions/drill` ／ recipe `recipes/drill`（**reviewer 検出率の実測**＝観点対応のバグの種〔期待 severity・期待 blocking つき〕を一時 worktree の合成 diff に注入→review fan-out→検出/見逃し/誤検出＋severity精度＋blocking精度＋説明品質の6指標スコアボード＋persona 単位の `Drill Result`（Score/Missed Issues/False Positives/固定4カテゴリ`add_checklist_item`・`adjust_severity_rule`・`add_false_positive_guard`・`strengthen_security_focus`の Recommended Persona Updates）。`--verify-findings` で反証者も採点〔正しい種を REFUTED＝失点〕。**`--replay <persona>`**＝アーカイブ済み過去 diff への再実行で新旧 verdict 差分＝ペルソナの snapshot テスト。結果は `.rig/drill-results.jsonl`。本物のコードは触らない・種は実在 class のみ＝測定の公正） |
> | **party**（`/rig:party`・utility・🎮） | command `commands/party`（**パーティ編成画面**＝`scripts/orchestrate.py party` がテレメトリ・drill 実測・ブリック在庫から RPG 風キャラクターシートを描画：Lv=DONE 数・出撃/REJECT=検証票・⚔検出率=drill・実績🏆=機械判定。ゲーム画面は演出で全行が実データ＝ハーネス健康診断。読み取り専用・selftest V が golden 検証） |
> | **sage**（`/rig:sage`・oracle） | persona `facets/personas/sage/{great-sage,raphael}` ／ instruction `facets/instructions/sage-oracle` ／ recipe `recipes/sage`（転スラの**大賢者/智慧之王**を模した「正解を問う」オラクル。《告》《解》〜＝解析 dispatch で裏取りした断定＋確度＋証拠アンカー。解析不能は臆さず宣言＝**捏造は機能として存在しない**。`--evolved`＝智慧之王：複数仮説の並列演算→統合・《予測》帰結＋発生確率・《提案》最適解＋次善。実行はせず /rig:dev へ橋渡し。裁定は magi・即断は coin へルーティング。MAGI と同じ「ネタだが中身は本物」流儀） |
> | **pre-mortem**（`/rig:pre-mortem`・humor） | persona `facets/personas/pre-mortem-analyst` ／ instruction `facets/instructions/pre-mortem` ／ output-contract `facets/output-contracts/premortem-report` ／ recipe `recipes/pre-mortem`（事前検死。「もう本番で壊れた」前提で失敗モードを逆算＋最小ガードレール。magi の補完＝どう壊れるか） |
> | **movie**（`/rig:movie`） | persona `facets/personas/video-director`（汎用）＋ `facets/personas/release-director`（`--release` 用サブクラス） ／ instruction `facets/instructions/video-direct`（汎用・target 非依存）＋ target 別 `facets/instructions/render-hyperframes`（既定・OSS HTML→MP4）／`render-remotion`（React/TS・Composition + Sequence）／`render-davinci`（**stub**・Fusion comp / Lua / Python script 素材納品）／`render-aviutl`（**stub**・拡張編集 `.exo` + `.anm` Lua）＋ `facets/instructions/release-movie`（CHANGELOG ソース差分手順） ／ knowledge `facets/knowledge/video-grammar`（尺/カット/間/構図/音の普遍ノウハウ） ／ recipe `recipes/movie`（汎用・既定）＋ `recipes/release-movie`（**movie の `--release` サブクラス**・extends: movie） ／ アニメ HTML `web/release-trailer.html`＋HyperFrames 例 `video/launch-film/`・`video/before-after/`（**動画作成の汎用ハーネス**＝既定は実装中のプロジェクトからデモ動画を `hyperframes` で。`--target` でレンダリングパイプライン切替、`--release` で release-movie 経路（CHANGELOG→リリーストレーラー）。**動いている画面ショット必須**・各ビート実出所紐づけ・harness では実動画/MP4 を非生成＝コンポジションまで生成しユーザーが render） |
> | **scenario**（`/rig:scenario`） | persona `facets/personas/scenario-writer`＋`facets/personas/engagement-reviewer`＋auteur レンズ `facets/personas/auteur/{deconstructionist,humanist}` ／ instruction `facets/instructions/{scenario-write,scenario-vet}` ／ recipe `recipes/scenario`（動画シナリオの脚本→検閲。ai-smell×sns-post×面白さ軸を acceptance-gate で収束。`/rig:movie` の前段。**詳細は scenario-write/vet が正本**） |
> | **design**（`/rig:design`） | persona `facets/personas/design/{ui-ux-designer,ux-reviewer,a11y-reviewer}` ／ instruction `facets/instructions/{design-draft,design-vet,design-audit}` ／ output-contract `facets/output-contracts/design-verdict` ／ knowledge `facets/knowledge/{a11y-wcag,ui-ux-heuristics}` ／ recipe `recipes/{design,design-audit}`（UI/UX・a11y の作成＋URL 監査。`--ppt`/`--claudedesign`/Playwright は MCP 委譲。**詳細は design-draft/vet/audit が正本**） |
> | **test-design**（`/rig:qa`） | persona `facets/personas/test-designer` ／ knowledge `facets/knowledge/qa-test-lenses` ／ instruction `facets/instructions/test-design` ／ output-contract `facets/output-contracts/test-cases` ／ recipe `recipes/test-design`（固定7観点のテストケース設計・Test Basis 必須・AI は設計者でありテスター非該当。**詳細は test-design.md が正本**） |
> | **harness-audit**（`/rig:harness`） | persona `facets/personas/harness-auditor` ／ knowledge `facets/knowledge/harness-taxonomy`（2×2） ／ instruction `facets/instructions/harness-audit` ／ output-contract `facets/output-contracts/harness-map` ／ recipe `recipes/harness-audit`（ハーネスの棚卸し＝空象限と効いていない資産の検出。read-only。**詳細は harness-audit.md が正本**） |
> | **orchestrate**（`/rig:orchestrate`・`--orchestrate`） | runner `scripts/orchestrate.py` ／ pattern `patterns/computational-orchestration`（**計算的オーケストレーション**＝遷移・ゲート・リトライ・停止・状態保持をコードが強制。半自動 plan/init/next/check/verdict／全自動 run＝マルチプロバイダ・並列検証・DAG・`run-state.json` 永続。opt-in＝engine 不変。**詳細は computational-orchestration.md が正本**） |
> | **queue**（`/rig:queue`） | runner `scripts/orchestrate.py`（queue サブコマンド＝**積んで GO**。`add`/`list`/`go`/`done`/`retry`（検証 FAIL の item を再び `queued` に戻す）・backend 差し替え式 local/github/gitlab・`go`＝並列実行＋独立検証ゲート。`list` は done を除くアクティブ item のみ表示し、note（失敗理由・完了コメント）も併記する。**`--provider rig`（既定）は各 item を `/rig:go "<task>"` 経由で dispatch し、`patterns/isolated-worktree` で自動隔離する**（headless プロセス同士が並列実行中に同じ作業ディレクトリを取り合う衝突を構造的に防ぐ）。queue の verifier は「gate まで確定したか」＋「isolated worktree 内で完結し本体に書き込んでいないか」を判定するのみ＝**accept はしない**——完了後は `/rig:go board` で全 item を一覧し、個別に `/rig:go accept`/`discard` する。`/rig:brainstorm`→`/rig:tasks` の成果を積む先でもある。**詳細は `commands/queue` が正本**） |
> | **init**（`/rig:init`・utility） | instruction `facets/instructions/init`（manifest・知識層 dir・CLAUDE.md "Compact Instructions" を scaffold） |
> | **persona-gen**（`/rig:persona`・generator） | instruction `facets/instructions/persona-gen`（説明文→persona facet を project/user 層に生成。**既定 project**・`--user` で global に opt-in。`--persona <name>` で都度投入、manifest `default_personas` で製品ごと常時自動投入。v2 Phase 1） |
> | **knowledge-gen**（`/rig:knowledge`・generator） | instruction `facets/instructions/knowledge-gen` ／ knowledge `facets/knowledge/_wiki`（説明文/`--auto` repo 解析/`--research` web 調査→wiki ページを global/project に生成。**既定 global**・`--project` で project overlay に opt-in（**`persona-gen` とは既定 tier が逆＝symmetry を仮定しない・#224**）。**`--graph`**＝repo の型付き知識グラフを `[[codebase-graph]]` に蒸留（relations 固定語彙・entities≤40/relations≤80・既定 project overlay・reviewer への inject を提案＝丸読みせず関係を辿る）。persona は `inject: [[slug]]` で参照。v2 Phase 2） |
> | **skill-author**（`/rig:forge`・generator） | instruction `facets/instructions/skill-author`（説明文→rig のブリック/パックを自作して検証・保存する自己拡張。persona は `/rig:persona`・knowledge は `/rig:knowledge` へ委譲・engine 不変・生成後 `--validate`・書込確認必須。**詳細は skill-author.md が正本**） |
> | **skill-import**（`/rig:import`・generator） | instruction `facets/instructions/skill-import`（外部 skill を **⓪発見(--discover)→①取得→②検疫→③判断(委譲>翻訳>知識)→④確認→⑤import-gate→⑥lock 記録(`skills-lock.json`)→⑦検証**のパイプラインで取り込む。モード: `--all` 一括／`--check-updates` 差分検知／`--update` 再取り込み。方言（.cursorrules/AGENTS.md 等）対応。`/rig:forge` の対。**手順・スキーマの正本は skill-import.md**） |
> | **skill-export**（`/rig:export`・generator） | instruction `facets/instructions/skill-export`（rig で育てたブリック〔persona/recipe/pack〕を**独立した Claude Code skill として書き出す還元機構**。rig 依存を除去して self-contained 化〔契約インライン展開・wiki 同梱・gate の散文翻訳〕・出所とライセンスの連鎖を継承〔再配布不可なら中止〕・書込確認必須。export→GitHub→他者が `/rig:import` で取り込める＝**吸収と還元の輪**。`/rig:import` の対） |
> | **list**（`--list`・utility） | instruction `facets/instructions/list`（**`--list` の表示仕様の正本**＝tier/pack グルーピング・`[N steps · …]` badge の導出と固定並び順・`steps:` フィールド・`★ default`・shadow 表示。§3 は要約とポインタのみ＝SKILL.md 減量フェーズ1） |
> | **plan**（`--plan`・utility） | instruction `facets/instructions/plan`（**`--plan` の表示仕様の正本**＝ヘッダ・step テーブル・Gate/Checks/DAG/Knowledge/Reviewer Fan-out/Loop Config 各ブロックの詳細ルール。§5 は要約とポインタのみ＝SKILL.md 減量フェーズ2） |
> | **catalog**（`/rig:catalog`・`--list --global`・utility） | instruction `facets/instructions/catalog`（全 tier 走査→domain×pack×persona×wiki×recipe の横断レジストリ地図。派生・読み取り専用。v2 Phase 3。**`--graph`**＝型付きブリック・グラフ：`scripts/orchestrate.py graph` が injects/extends/uses-*/gated-by/mirrors 等**固定11種の関係**を frontmatter・steps: から導出（手書きしない＝腐らない）。`--focus <name>` で1ホップ近傍＝影響調査。validate check_graph・selftest W が golden 検証） |
> | **hooks**（プラグイン同梱） | `hooks/hooks.json` → `hooks/preserve-rig-state.sh`（`PreCompact`：圧縮で run-state を保全。§6 run-continuity ④） |

## 3. PARSE — 起動文字列の解釈

起動文字列（`$ARGUMENTS`）を **flag** と **自由記述**（レビュー対象・Issue 内容など）に分解する。

### flag 一覧

| flag | 意味 |
|---|---|
| `--issue <id>` | 対象 Issue を指定（intake の入力） |
| `--design` | design step を ON にする |
| `--visual` | visual 確認（スクリーンショット等）を ON |
| `--review` | review step を ON にする |
| `--tdd` | implement を TDD（red-green-refactor）で行う |
| `--autonomous` | step ゲートを省き自律実行（既定は各 step で確認＝step ゲート ON） |
| `--plan` | COMPOSE まで実行し、合成ハーネスを人間可読で提示して**停止**（実行しない） |
| `--save-plan <path>` | `--plan` と組み合わせて使用。合成ハーネスを会話に表示すると同時に `<path>` にも書き出す（Markdown・`--plan` と同一内容）。`--plan` なしで指定した場合は `[WARN] --save-plan は --plan と組み合わせて使用してください（無視します）` を出して無視する。既存ファイルへの上書きは確認あり（`--autonomous` 時は確認なし・自動上書き）（§5） |
| `--only <step>` | 指定 step だけを実行（例 `--only review`） |
| `--from <step>` | 指定 step から最後まで実行 |
| `--to <step>` | 先頭から指定 step まで実行（含む）。`--from` と組み合わせて「A から B まで」の範囲スライス可（例 `--from implement --to verify`）。単独は「ここまで」の意（§4.3.1） |
| `--recipe <name>` | shipped/user/project いずれかの recipe を名前で指定（§4.2.1 の検索順で解決）（例 `--recipe review-only`） |
| `--save-recipe <name>` | 今回合成したハーネスを recipe として保存。既定は project 層（`<repo>/.claude/rig/recipes/<name>.md`）。`--user` と組み合わせると user 層（`~/.claude/rig/recipes/<name>.md`）に書き出す |
| `--description "<text>"` | `--save-recipe` と組み合わせて使用。保存 recipe の `description` フィールドを自動生成の代わりに指定テキストで設定する。`--save-recipe` なしで指定した場合は `[WARN] --description は --save-recipe と組み合わせて使用してください（無視します）` を出して無視する（§4.3.2） |
| `--workflow` | 実行バックエンドを **workflow**（ultracode Workflow ツール）に切り替える。既定は **manual**（`patterns/workflow-backend` 参照） |
| `--orchestrate` | **計算的オーケストレーション**を ON。step の遷移・ゲート判定・リトライ・停止条件・状態保持を、散文でなく **`scripts/orchestrate.py`（決定論ランナー）に強制させる**（舵をコードが握る）。半自動＝`init`→`next`/`check`/`verdict`（モデルが各 step の作業）。全自動＝`run`＝**各 step を別プロセスのエージェントで実行**（マルチプロバイダ rig/claude/codex/cmd/mock・プロセス隔離・検証は別プロバイダで構造的に採点者≠生成者）。**recipe に `checks:`/`needs:` があるか manifest `default_orchestrate: true` のとき自動 ON**（§4.3）。`patterns/computational-orchestration` 参照 |
| `--no-orchestrate` | 自動有効化（recipe の `checks:`/`needs:` または manifest `default_orchestrate`）を**この run だけ打ち消す**＝従来の散文エンジンで回す |
| （横断 CLI） | `orchestrate install-shim` で `~/.local/bin/rig` を 1 回張れば、任意 cwd から `rig <subcommand>` で起動できる。`$RIG_HOME` 上書き可、`<cwd>/.rig/recipes/<name>.md` が同名 built-in を**プロジェクト overlay**として上書き解決、`checks:` の実行 cwd は呼び出し元（rig リポジトリではない） |
| `--capture` | capture（学びの知識層への蓄積）を承認ダイアログなしで実行（提案表示と事後報告は省略しない）。既定は capture 提案時に承認を求める |
| `--no-capture` | RUN 後の capture 提案を完全にスキップ（提案表示・承認ダイアログともに出さない）。`--capture` と同時指定時は `--no-capture` 優先＋WARN（§7.3） |
| `--skip <step>` | 指定した step を除外してフローを継続する（複数可。例 `--skip design --skip review`）。size-aware 既定・`--design`/`--review` 等フラグより後に適用される（明示スキップが最終的に勝つ）。`--only` との同時指定は `--only` 優先・警告を出す。`--save-recipe` には影響しない（実行時フィルタ＝§4.3.2 snapshot 意味論と同じ） |
| `--list` | 利用可能なブリック(§2)・**全 tier の recipe**（project / user / shipped）・flag を一覧表示して停止（RESOLVE/COMPOSE/RUN しない） |
| `--validate` | ブリック整合チェック（doctor）。recipe→facet 参照切れ・frontmatter スキーマ逸脱・§2 目録と実ファイルのドリフトを検査し、レポートして停止（RESOLVE/COMPOSE/RUN しない）。手順は `facets/instructions/validate` |
| `--adversarial` | 敵対的レビュー step（lazy-senior / cognitive-economist で AI の癖排除・人間可読性・不要コメント除去）を合成に追加 |
| `--budget <low\|mid>` | **コスト予算による fan-out の間引き**（size-aware の金銭版・§4.4）。`low`＝reviewer は既定 3-way まで（`default_personas`/`--persona` の追加投入を抑止・adversarial/verify-findings/cross-llm の自動追加を提案止まりに・workflow backend 禁止）。`mid`＝3-way＋選択投入2枠まで。未指定＝制限なし。**実行時フィルタ**＝`--skip` と同様 `--save-recipe` に保存されない。manifest `default_budget` で恒久設定可 |
| `--verify-findings` | **所見の敵対的検証**を review-gate に挿入する。REJECT の根拠とマージ前必須条件を1件ずつ `finding-verifier`（反証者・独立 subagent）に渡し、証拠つきで反証された所見（REFUTED）はゲートに通さない（UPHELD/UNRESOLVED は通す＝疑わしきは所見の利）。reviewer が多い run の false-positive 制御。`patterns/review-gate`「敵対的検証」参照 |
| `--persona <name>` | review fan-out に名前指定のカスタム reviewer persona を**この run だけ追加**（複数可）。tier 解決（project→user→shipped・§5）で名前解決。manifest `default_personas`（製品ごとに常時自動投入）に**上乗せ**される。`/rig:persona` で生成した persona をそのまま投入できる |
| `--no-default-personas` | この run に限り manifest `default_personas` の自動投入を**抑止**する（組み込み reviewer＋`--persona` 指定分のみで回す） |
| `--cross-llm` | **他社 LLM レビュー前提モード**。implement step に `cross-llm-legibility` ポリシーを注入し（Codex/Copilot/GPT が読んでも一発で通る＝慣用的・明示的・文脈非依存なコードを書く規律）、review fan-out に `cross-llm-reviewer` persona を追加する（外部 LLM になりきって「内輪にしか分からない」箇所を指摘）。書く側・見る側の両方に作用する |
| `--global` | `--list` / `--validate` のスコープを **tier 横断**（shipped＋user(global)＋project）に広げる。`--list --global` は横断レジストリ地図（`/rig:catalog` 相当）、`--validate --global` は tier 横断の衛生点検。手順は `facets/instructions/catalog` |
| `--ppt` | （design pack）作成したデザインドキュメントを PowerPoint としても出力（`powerpoint-server` MCP）。既定 Markdown に追加・併用可 |
| `--claudedesign` | （design pack）claude.ai デザイン機能（`claude_design` MCP）でも生成。既定 Markdown に追加・併用可。MCP 未接続時は報告して Markdown のみ続行 |
| `--url <url>` | （design pack）監査モードを明示。実装画面を Playwright で取得し UI/UX・a11y を採点（bare な URL 引数でも自動検出） |
| `--a11y-level <A\|AA\|AAA>` | （design pack）目標 WCAG レベル（既定 AA）。未達違反は検閲で重大度を上げる |

**`--list` 指定時** → §2 のブリック目録・flag 一覧に加え、recipe を全 tier 走査（§4.2.1 と同じ project → user → shipped 順）して tier 別・pack 別にグルーピング表示し、**停止**（解決も実行もしない）。各エントリは `name [N steps · badge…]  steps: <id列>  extends: <親 [tier]>  — description` 形式。**表示仕様の正本は `facets/instructions/list`**（tier/pack グルーピング・`[N steps · …]` badge の導出と固定並び順・`steps:` フィールド・`★ default` マーカー・shadow 表示・出力例）— `--list` 実行時は必ずこれを読んで従う。**`--global` 併用時**は recipe 以外の全ブリック（persona・wiki 等）も横断し、レジストリ地図（`facets/instructions/catalog`）を提示。

**`--validate` 指定時** → ブリック整合チェック（doctor）。結果を提示して**停止**（`--list` と同じく副作用なしの点検モード）。**検査項目・severity・エラーフォーマットの正本は `facets/instructions/validate`**（① recipe→facet 参照切れ／② manifest 参照・値検証／③ frontmatter スキーマ〔YAML parse・列挙値・型・矛盾・無効コンテキスト WARN〕／③-b persona スキーマ／④ §2 目録ドリフト／⑤ wiki 衛生／⑥ ai-quirks 二相ペア〔--global〕／⑦ accumulated スキーマ）— `--validate` 実行時は必ずこれを読んで従う。CI 用サブセットは `scripts/validate.py`（①②③＋③-b を機械実装）。**`--global` 併用時**は tier 横断で点検する（全 tier の orphan・リンク切れ・参照欠落・重複）。
**`--adversarial` 指定時** → 合成ハーネスの review/verify の後に `adversarial-review` step（instruction: adversarial-review / personas: lazy-senior, cognitive-economist / gate: acceptance-gate）を追加する。recipe `adversarial-review` は敵対レビューのみを回す。
**`--cross-llm` 指定時** → COMPOSE フェーズで2方向に作用する（#71）：①**書く側** — implement step の `policies[]` に `cross-llm-legibility` を追加し、subagent prompt 末尾（Policy 位置）に注入する（他社 LLM がレビューする前提で慣用的・明示的・文脈非依存に書く規律）。②**見る側** — review fan-out を行う step に `cross-llm-reviewer` persona を追加する（`--persona` と同じ経路で reviewer 集合に和集合・dedup）。implement step が無い recipe では ① をスキップし ② のみ、review step が無い recipe では ② をスキップし ① のみ作用する。`--save-recipe` 併用時は `cross_llm: true` を frontmatter に保存し（§4.3.2 #130）、再利用時に `--cross-llm` フラグなしでも両方向が自動有効になる。② の `cross-llm-reviewer` persona は `cross_llm: true` 再利用時に自動追加されるため `personas[]` への直接書き込み（#57 経路）は redundant になるが後方互換のため維持する。

### 引数なし / 曖昧な場合 → 対話 composition

1. **何を**したいかを訊く（実装着手 / レビュー / PR 完了 等）。
2. 目録から該当**ブリックを提案**する。
3. user に**選択**させる（既定は軽量側、§5 参照）。
4. 合成した**ハーネスを提示**する。
5. **確認**を取ってから RUN へ進む。

## 3.5. Recipe スキーマ（正規定義）

recipe ファイル（`recipes/*.md`）は YAML frontmatter + 本文 Markdown で構成される。以下がエンジンが解釈するキーの全量。

### トップレベルキー

| キー | 必須 | 説明 |
|---|---|---|
| `name` | ✓ | recipe 識別子（ファイル名と一致させること） |
| `description` | ✓ | 使い分け説明（一行） |
| `scope` | ✓ | `shipped`（同梱）/ `user`（ユーザー保存）/ `project`（プロジェクト固有） |
| `steps[]` | ✓ | step オブジェクトの配列（下記） |
| `autonomy` | ✓ | `interactive`（各 step でゲート確認）/ `autonomous`（**step ゲートなし**。acceptance-gate 品質ループは維持） |
| `extends` | — | 継承元 recipe の bare 名。指定 recipe の steps をベースに差分だけ上書きする。1段のみ有効（§4.2.2 参照） |
| `backend` | — | `manual`（既定）/ `workflow`。省略時は `manual`。`--workflow` フラグ指定時の実行バックエンド宣言。`--save-recipe` で保存され、再利用時に `--workflow` フラグなしでも Workflow バックエンドで実行される（§6 実行バックエンド表）（#52） |
| `tdd` | — | `true` の場合、implement step を常に TDD（red-green-refactor）で実行する。`--tdd` フラグ指定時と等価。省略時 `false`。`--save-recipe` で保存され、再利用時に `--tdd` フラグなしでも TDD モードが発動する（#56） |
| `no_default_personas` | — | `true` の場合、manifest `default_personas` の自動投入を抑止する（組み込み reviewer＋`--persona` 指定分のみで回す）。`--no-default-personas` フラグ指定時と等価。省略時 `false`。`--save-recipe` で保存され、再利用時に `--no-default-personas` フラグなしでも抑止が効く（#70） |
| `orchestrate` | — | `true` の場合、step 遷移・ゲート判定・リトライ・停止条件の制御を散文でなく `scripts/orchestrate.py`（決定論ランナー）に委ねる。`--orchestrate` フラグ指定時と等価。省略時 `false`。`--save-recipe` で保存され、再利用時に `--orchestrate` フラグなしでも計算的オーケストレーションモードが有効になる（#129） |
| `cross_llm` | — | `true` の場合、① implement step に `cross-llm-legibility` ポリシーを注入（他社 LLM がレビューする前提で慣用的・明示的・文脈非依存なコードを書く規律）+ ② review fan-out に `cross-llm-reviewer` persona を追加する。`--cross-llm` フラグ指定時と等価。省略時 `false`。`--save-recipe` で保存され、再利用時に `--cross-llm` フラグなしでも2方向の作用が有効になる（#130） |
| `no_capture` | — | `true` の場合、RUN 後の capture 提案を完全に抑止する（提案表示・承認ダイアログともに出さない）。`--no-capture` フラグ指定時と等価。省略時 `false`。`--save-recipe` で保存可（§4.3.2）。`hotfix` / `debug` などの軽量 recipe に推奨（#137） |
| `adversarial` | — | `true` の場合、合成ハーネスの review/verify の後に `adversarial-review` step（instruction: adversarial-review / personas: lazy-senior, cognitive-economist / gate: acceptance-gate）を自動追加する。`--adversarial` フラグ指定時と等価。省略時 `false`。`--save-recipe` で保存可（§4.3.2）（#172） |
| `visual` | — | `true` の場合、verify step の動作を変え `visual-verify` instruction への委譲を強制する（UI 視覚確認を常時実行）。`--visual` フラグ指定時と等価。省略時 `false`。`--save-recipe` で保存可（§4.3.2）（#174） |
| `no_orchestrate` | — | `true` の場合、orchestrate の自動有効化（recipe `checks:`/`needs:` の宣言または manifest `default_orchestrate: true`）を**この recipe の全 RUN で打ち消す**。`--no-orchestrate` フラグ指定時と等価。省略時 `false`。`--save-recipe` で保存可（§4.3.2）。`orchestrate: true` と同時指定時は WARN を出して `no_orchestrate` 優先（`--validate` が矛盾を FAIL 検出）（#178） |
| `design` | — | `true` の場合、design step の condition（`--design または size L+`）を上書きして常時 ON にする。`--design` フラグ指定時と等価。省略時 `false`。`--save-recipe` で保存可（§4.3.2）（#182） |
| `review` | — | `true` の場合、review step の condition（`--review または size L+`）を上書きして常時 ON にする。`--review` フラグ指定時と等価。省略時 `false`。`--save-recipe` で保存可（§4.3.2）（#182） |
| `verify_findings` | — | `true` の場合、review-gate に所見の敵対的検証（`finding-verifier` による反証段）を挿入する。`--verify-findings` フラグ指定時と等価。省略時 `false`。`--save-recipe` で保存可（`patterns/review-gate`「敵対的検証」参照） |
| `capture` | — | `true` の場合、RUN 後の capture 提案を**承認ダイアログなしで自動実行**する（`--capture` フラグ指定時と等価。提案表示・事後報告は省略しない）。省略時 `false`。`--save-recipe` で保存可（§4.3.2）。`--capture` と `--no-capture` が同時に有効な場合は `--no-capture` 優先＋WARN（§7.3 整合）（#184） |

### step オブジェクトのキー

| キー | 必須 | 説明 |
|---|---|---|
| `id` | ✓ | step 識別子（例 `review` `design` `implement`） |
| `instruction` | ✓ | 委譲先 instruction facet 名（例 `parallel-review`） |
| `pattern` | — | 制御フロー（`serial` / `parallel-fanout` / `review-gate` 等） |
| `gate` | — | 集約/受け入れパターン。`review-gate`（レビュー集約）/ `acceptance-gate`（受け入れ基準まで品質収束。review 以外の step にも付与可）。mode pack が第3の値を追加できる（例 `magi-consensus`＝`recipes/magi` の多数決集約ゲート、`patterns/magi-consensus` 参照） |
| `acceptance` | — | `gate: acceptance-gate` 時の**受け入れ基準リスト**（合否判定の根拠。例 `["build が成功", "lint 0 件", "3-way review に REJECT が無い"]`）。基準を満たすまで収束させる |
| `max_retries` | — | `gate: acceptance-gate` 時の**最大収束試行数 K**（≥1 の整数）。K 回で受け入れ基準を満たさなければ user へエスカレーション。**省略時フォールバック順：step 省略 → manifest `default_max_retries` → 2**（#100）。§6 stuck-guard（同一エラー反復で発動する別カウンタ）とは独立した上限。 |
| `model` | — | この step の **generator LLM モデル名**（例: `claude-sonnet-5` / `claude-opus-4-8` / `gpt-5`）。指定時、`build_argv` が `claude -p ... --model <name>` / `codex exec ... -m <name>` に引き渡す（ollama/lmstudio 系は既存の HTTP model resolve が優先）。**「親 = Sonnet / 深堀り step = Opus」を recipe で書ける**。省略時は run 時 flag の `--model` にフォールバック。 |
| `verifier_model` | — | この step の **verifier LLM モデル名**（`model:` と別にしたい時）。省略時は `model:` を再利用、それも無ければ run 時 flag。**「実装は Sonnet で書かせ、検証だけ Opus に厳しく見てもらう」**を step 内で分離できる。 |
| `personas` | — | 合成するペルソナ facet 名のリスト |
| `policies` | — | 末尾注入するポリシー facet 名のリスト |
| `output_contract` | — | subagent 出力フォーマット定義 facet 名（例 `review-verdict`） |
| `condition` | — | 条件付き step。例：`--design または size L+ で有効` のように記述し、RESOLVE フェーズで ON/OFF を判断する |
| `checks` | — | （任意・`--orchestrate` 用）この step の**計算的センサー**＝決定論ランナーが実行する shell コマンド列（全件 exit 0 で合格）。**リスト必須・空文字列エントリ不可**（`--validate` が型・空エントリを検証 #200）。`gate` の一次根拠になる。プロジェクト依存のため shipped recipe では未宣言、manifest / user recipe で足すのが基本。`patterns/computational-orchestration` 参照 |
| `needs` | — | （任意・`orchestrate run` 用）依存する step-id のリスト。**DAG 並列**＝`needs` を満たした独立 step を同時プロセスで実行（依存の無い step は同一 wave で並走）。宣言が無ければ従来どおり直列。`patterns/computational-orchestration` 参照 |
| `remove` | — | （`extends` 専用）`true` の場合、継承元 recipe からこの `id` の step を**静的に除外**する。`extends` なし recipe での使用は WARN。`--skip`（実行時動的フィルタ）との差異：`remove: true` は recipe 定義の静的除外（毎回同じ）（#144） |

> **省略可能キーは省略してよい。** `review-only` は最小サブセット（`id` / `instruction` / `pattern` / `gate` / `personas` / `output_contract`）だけを使う。`release-flow` / `design-first` は `policies` / `condition` / `gate` / `acceptance` も使う。すべての recipe はこのスキーマに準拠する。

## 4. RESOLVE — 解決順（manifest＋recipe＋flag＋size-aware 既定）

最終ハーネスを次の順で確定する。**後の段が前の段を override する。**

> **named recipe の RESOLVE は `orchestrate plan <recipe> --json --with "<flags>" --diff-git` の出力が一次**（本セクション末尾「RESOLVE の一次実装はコード」参照）。以下の散文規則は、スクリプトを呼べない環境と ad-hoc 対話合成のためのフォールバック定義であり、意味は同一（selftest Q/R/S が同一性を保証）。

### 4.1 manifest ロード

起動時に **`<repo>/.claude/rig.md`** の存在を確認する。

- **存在する場合**：YAML frontmatter を解析し、以下の値をプロジェクト既定として読み込む。
  - `build` / `lint` / `test` コマンド（ビルド系 step で使用）
  - `branch.*`（ブランチ作成・CI 確認ステップで使用）
  - `reviewer`（review step の委譲先選択に使用）
  - `production_impact.paths` / `production_impact.keywords`（本番影響検知閾値に使用）
  - `skills`（instruction facet の委譲先候補として使用）
  - `knowledge.*`（Knowledge facet の注入ソースとして使用）
  - `default_recipe`（recipe 解決 §4.2 で使用）
  - `default_personas`（review fan-out へ**自動投入**する persona 名リスト。§5「manifest default_personas の自動投入」で使用）
  - `default_backend`（全 RUN のデフォルト実行バックエンド。`manual`/`workflow`。recipe の `backend:` キー・`--workflow` フラグで個別上書き可）（#52）
  - `default_max_retries`（`acceptance-gate` を持つ step の `max_retries` 省略時フォールバック。step ローカルの `max_retries` キーで個別上書き可。省略時は 2）（#100）
  - `org_dir`（チーム共有ブリック層＝org tier のパス。env `RIG_ORG_HOME` でも指定可・§5 tier 解決参照）
  - `default_budget`（コスト予算の恒久設定 `low|mid`。`--budget` フラグで個別上書き可・§4.4）
  - `sage_notifications`（`true` のとき、能力獲得系の完了報告〔import の lock 記録・persona/knowledge 生成・capture 書き込み〕の先頭に**大賢者スタイルの獲得通知**を1行付す＝`《告》スキル「<name>」を獲得しました`。演出のみ・報告本文は不変。省略時 `false`）
  - `default_orchestrate`（`true` のとき全 RUN を**計算的オーケストレーション**で回す＝`--orchestrate` 等価。recipe の `checks:`/`needs:` による自動有効化とは独立にプロジェクト全体へ適用。省略時 `false`）
  - `worktree.*`（worktree 運用フラグとして使用。`worktree.enabled` を実際に読んで分岐するのは `facets/personas/implementer` — #225）
  - `size_thresholds.*`（存在する場合、size-aware 判定の行数閾値を上書き）
- **存在しない場合**：全キーに**汎用既定（generic defaults）**を適用する。
  - `build` / `lint` / `test`：`package.json` / `build.gradle` / `Makefile` を自動検出して推定
  - `branch.base`：`git remote show origin` からデフォルトブランチを取得
  - `reviewer`：`human`（人間レビュー。PR を作成して承認を待つ）
  - `production_impact`：`auth` / `migration` / `security` / `di` / `interface` を含むパス・差分をヒューリスティックで検出
  - `skills`：Claude Code セッション開始時に利用可能な skill を自動検出
  - `knowledge`：リポジトリを検索して `CONTEXT.md` / `CLAUDE.md` / `docs/` を探す
  - `default_recipe`：`interactive`（毎回ユーザーに選択させる）
  - `default_personas`：`[]`（自動投入なし。review は組み込み reviewer＋`--persona` 指定分のみ）
  - `default_backend`：`manual`（`Agent` ツールによる手 dispatch）
  - `default_max_retries`：`2`（acceptance-gate の step ローカル省略時の既定試行上限）
  - `default_orchestrate`：`false`（明示 opt-in または recipe の `checks:`/`needs:` 検出時のみ orchestrate）
  - `worktree.enabled`：`false`（worktree なし）

manifest スキーマの全体定義は `manifests/_template.md` を参照。

### 4.2 recipe 解決

manifest ロード後、次の優先順位で使用 recipe を確定する。

1. `--recipe <name>` フラグ（明示指定）
2. manifest の `default_recipe` 値
3. 対話（ユーザーにブリックを提案して選択させる）

`--recipe` が指定されれば manifest の `default_recipe` は無視される。

#### 4.2.1 recipe ファイル検索順（tier 優先順位）

recipe 名が決まったら、以下の順でファイルを探す。**先に見つかった tier が優先**され、下位 tier の同名 recipe は無視される。

| tier | パス | 優先度 |
|---|---|---|
| **project**（最高） | `<repo>/.claude/rig/recipes/<name>.md` | 1（最優先） |
| **user** | `~/.claude/rig/recipes/<name>.md` | 2 |
| **shipped**（同梱） | `skills/rig/recipes/<name>.md` | 3（最低） |

- `<repo>` は現在の git リポジトリルート（`git rev-parse --show-toplevel` で取得）。
- 同名 recipe が project 層に存在すれば shipped 層は読まれない。user 層は project 層が無い場合のみ参照される。
- どの tier にも存在しない場合は下記フォーマットで報告し、対話 composition（§3 引数なし手順）へフォールバックする（§4.3.1 ケース A の step-id not found と同形式）。tier に recipe が1件もない場合はその tier 見出しをサイレントに省略する（`--list` の tier 省略ルールと同じ）。
- **「もしかして」候補提案（#188）**：recipe が見つからない場合、`[ERROR]` の直後・「利用可能な recipe:」の直前に、編集距離（Levenshtein）≤ 2 の候補を距離昇順で最大 3 件表示する。候補には全 tier（project → user → shipped）を対象とし、各候補に `[tier]`（§4.2.1 / `--list` と同じ語彙）を付記する。同距離の候補は tier 優先順（project > user > shipped）でソートする。候補が 0 件の場合は「もしかして:」行を省略し既存の全一覧のみを出す（ノイズを増やさない）。

```
[ERROR] recipe "hotfixx" が見つかりません。
  もしかして: hotfix [shipped]
  利用可能な recipe:
  ### shipped
    review-only, release-flow, hotfix, design-first, adversarial-review, ...
  ### project  （<repo>/.claude/rig/recipes/ に recipe がある場合のみ）
    my-flow
  ### user  （~/.claude/rig/recipes/ に recipe がある場合のみ）
    strict-tdd
```

#### 4.2.2 extends — N 段継承（上限 5・#193）

recipe の frontmatter に `extends: <parent-name>` が宣言されている場合、次の手順で合成する。

1. **チェーンの解決**：leaf → parent → grandparent → …の順に `extends` を辿る。各段の `<parent-name>` を §4.2.1 の tier 検索順で探す（bare 名のみ。パス指定・URL 不可）。**深さ上限 5**（`EXTENDS_MAX_DEPTH` in `orchestrate.py`）を超えたら残りを無視し WARN。**循環継承**（A → B → A 等）は検知次第 WARN で切り上げる。
2. **step マージ**：root ancestor の `steps[]` をベースにし、そこから leaf に向かって順に各段の `steps[]` を適用する：
   - `remove: true` がある → 継承元から該当 `id` の step を**除外する**（§3.5 `remove` フィールド）
   - `remove` が無い / `remove: false` → 同 `id` は上書き（`_origin=override`）、新 `id` は末尾追加（`_origin=added`）

   **`remove: true` エラー処理**：① `id` が継承元に存在しない → `[WARN] remove: true — step '<id>' は継承元に存在しません（<layer> 側指定・無視して続行）`（停止なし）。② 他フィールドと同時指定 → `remove: true` 優先・他フィールドを無視＋`[WARN] remove: true と他フィールドの同時指定は無効（他フィールドを無視）`。③ `--orchestrate` 利用時に削除 step が他 step の `needs:` で参照されている → `[WARN] remove: true — step '<id>' を参照する needs 宣言があります（<依存 step 名>）`。`--validate` でも同じ ①③ を WARN・② を FAIL として出力する。`--list` の `extends:` 表記（#53）に削除 step 数を `[N removed]` で補記する（例: `extends: release-flow [shipped] [1 removed]`。N=0 の場合は省略）。`--plan` テーブルと `--list` の `steps:` フィールドには削除済み step を表示しない（`[SKIP]` 表示もなし — 定義上存在しないため）。`--save-recipe` の展開結果（§4.3.2）にも `remove: true` エントリは含まれない（削除済みなので）。
3. **トップレベルキーのマージ**：`name` / `description` / `scope` / `autonomy` などは root → parent → leaf の順に上書き（leaf の値が最終的に勝つ）。子に記載のないキーは祖先を引き継ぐ。`extends` は合成後の recipe には残さない（出力しない）。
4. **深さ上限と循環**：`EXTENDS_MAX_DEPTH = 5` を超えた祖先はチェーンから除外され WARN、循環は検知次第 `[WARN] extends: 循環継承を検知しました (X → Y → Z → X)` を出して途中打ち切り。これらは実行を止めないが `--validate` は WARN として集計する。**認知経済的に浅く保つ**（深い継承は追跡できない）。

> **bare 名ルール**：`extends` の値は `release-flow` のようなファイルベース名のみ。`../other/recipe` のようなパス指定は無効。
>
> **旧仕様との互換性**：以前は「1 段のみ・親の `extends` を無視」だった（§4.2.2 v0.92 以前）。今回 N 段化に伴い、既存の 1 段継承（`release-movie extends movie` 等）は挙動不変で通る。新たに深い継承を書けるようになっただけで、既存 recipe を書き換える必要はない。

### 4.3 flag override

`--design` `--review` `--tdd` 等で §4.2 で決定した recipe の step ON/OFF を上書き。`--only <step>` / `--from <step>` で実行範囲をスライス、`--skip <step>` で特定 step を除外（後述）。manifest 由来の値も flag で上書き可能。

> **`--tdd` の特例**：`--design` / `--review` は step の ON/OFF を制御するが、`--tdd` は implement step の**動作を変える**フラグ。COMPOSE フェーズで implement subagent の prompt に「**`risk-based-testing` のリスク評価をスキップし、常に TDD（red-green-refactor）で実装する＝`tdd` スキルへの委譲を強制する**」を追加注入する（`facets/instructions/implement.md` 本体は不変）。これが無いと `--tdd` を付けても implement が通常のリスク評価で直接実装を選び、強制 TDD が効かない。

> **`tdd: true` キーの解釈（#56）**：recipe の `tdd: true` キー（§3.5）を RESOLVE 時に `--tdd` フラグと等価として処理し、COMPOSE フェーズで implement subagent への TDD 注入を発動させる。`--save-recipe` で保存した recipe の `tdd: true` を再利用する際も強制 TDD が有効になる。`--plan` ヘッダに `| tdd: on` を付加する（`tdd: true` または `--tdd` フラグが有効な場合のみ。`false`/省略時は付加しない）。

> **`backend: workflow` キーの解釈（#52）**：recipe の `backend: workflow` キー（§3.5）を RESOLVE 時に `--workflow` フラグと等価として処理し、RUN フェーズで Workflow バックエンドを使用する。manifest の `default_backend: workflow` はプロジェクト全体の既定として同様に機能し、recipe `backend:` キー・`--workflow` フラグで上書きできる。

> **`no_default_personas: true` キーの解釈（#70）**：recipe の `no_default_personas: true` キー（§3.5）を RESOLVE 時に `--no-default-personas` フラグと等価として処理し、COMPOSE フェーズで manifest `default_personas` の自動投入を抑止する（最終 reviewer 集合から `★`＝manifest 由来 persona を除外する。§5「manifest default_personas の自動投入」）。`--save-recipe` で保存した recipe の `no_default_personas: true` を再利用する際も抑止が効く。`--plan` ヘッダに `| no-defaults: on` を付加する（`no_default_personas: true` または `--no-default-personas` フラグが有効な場合のみ。`false`/省略時は付加しない）。

> **`--orchestrate` の自動有効化**：次のいずれかで RESOLVE 時に `--orchestrate` 等価として処理する（明示 `--orchestrate` と同じ＝舵を `scripts/orchestrate.py` に渡す。`patterns/computational-orchestration`）。**engine は不変**で、RUN の駆動だけを決定論ランナーに委譲する。
> 1. **recipe が `checks:` か `needs:` を宣言**（§3.5）— 「決定論で回す意図のある recipe」＝機械検証や DAG 並列が宣言されていれば自動で orchestrate を通す（`checks` をゲートの一次根拠に・`needs` で step-DAG 並列）。
> 2. **manifest `default_orchestrate: true`** — プロジェクト全体の既定として全 RUN を orchestrate で回す。
> 明示 `--no-orchestrate` で個別に無効化できる（自動有効化を打ち消す）。`--plan` ヘッダに `| orchestrate: on`（自動有効化時は `| orchestrate: auto`）を付加する（オフ時は付加しない）。単発生成コマンド（`/rig:persona` 等・ループ無し）には作用しない。

> **`orchestrate: true` キーの解釈（#129）**：recipe の `orchestrate: true` キー（§3.5）を RESOLVE 時に `--orchestrate` フラグと等価として処理し、RUN フェーズで計算的オーケストレーションモード（`scripts/orchestrate.py` 決定論ランナー）を使用する（`patterns/computational-orchestration` 参照）。`--save-recipe` で保存した recipe の `orchestrate: true` を再利用する際も計算的オーケストレーションが自動有効になる。`--plan` ヘッダに `| orchestrate: on` を付加する（`orchestrate: true` または `--orchestrate` フラグが有効な場合のみ。`false`/省略時は付加しない）。

> **`cross_llm: true` キーの解釈（#130）**：recipe の `cross_llm: true` キー（§3.5）を RESOLVE 時に `--cross-llm` フラグと等価として処理し、COMPOSE フェーズで ① implement step への `cross-llm-legibility` ポリシー注入 + ② review fan-out への `cross-llm-reviewer` persona 追加の両方を発動する（`--cross-llm` 指定時と同じ2方向作用）。implement step が無い recipe では ① をスキップし ② のみ、review step が無い recipe では ② をスキップし ① のみ作用する（既存 `--cross-llm` 動作と同じ）。`--save-recipe` で保存した recipe の `cross_llm: true` を再利用する際も両方向が自動有効になる。`--plan` ヘッダに `| cross-llm: on` を付加する（`cross_llm: true` または `--cross-llm` フラグが有効な場合のみ。`false`/省略時は付加しない）。

> **`adversarial: true` キーの解釈（#172）**：recipe の `adversarial: true` キー（§3.5）を RESOLVE 時に `--adversarial` フラグと等価として処理し、COMPOSE フェーズで合成ハーネスの review/verify の後に `adversarial-review` step（instruction: adversarial-review / personas: lazy-senior, cognitive-economist / gate: acceptance-gate）を追加する。`--save-recipe` で保存した recipe の `adversarial: true` を再利用する際も敵対レビューステップが自動追加される。`--plan` ヘッダに `| adversarial: on` を付加する（`adversarial: true` または `--adversarial` フラグが有効な場合のみ。`false`/省略時は付加しない）。

> **`visual: true` キーの解釈（#174）**：recipe の `visual: true` キー（§3.5）を RESOLVE 時に `--visual` フラグと等価として処理し、COMPOSE フェーズで verify step の動作を変え `visual-verify` instruction への委譲を強制する（UI 視覚確認を常時実行）。implement step への TDD 注入と同じ「step 動作変更」パターン。`--save-recipe` で保存した recipe の `visual: true` を再利用する際も `--visual` フラグなしで UI 視覚確認が発動する。`--plan` ヘッダに `| visual: on` を付加する（`visual: true` または `--visual` フラグが有効な場合のみ。`false`/省略時は付加しない）。

> **`no_orchestrate: true` キーの解釈（#178）**：recipe の `no_orchestrate: true` キー（§3.5）を RESOLVE 時に `--no-orchestrate` フラグと等価として処理し、orchestrate の自動有効化（recipe `checks:`/`needs:` 宣言・manifest `default_orchestrate: true`）を**この recipe の全 RUN で打ち消す**。`orchestrate: true` と `no_orchestrate: true` が同時に設定されている場合は WARN を出して `no_orchestrate` 優先とし、`--validate` が矛盾を FAIL として検出する（`facets/instructions/validate` ③ 参照）。`--save-recipe` で保存した recipe の `no_orchestrate: true` を再利用する際も `--no-orchestrate` フラグなしで orchestrate が抑止される。`--plan` ヘッダに `| orchestrate: off` を付加する（`no_orchestrate: true` または `--no-orchestrate` が有効な場合のみ。通常の「orchestrate OFF かつ指定なし」は省略維持）。`--list` に `· no-orchestrate` badge を付記する。`no_capture: true` (#137) / `no_default_personas: true` (#70) と同じ anti-flag 保存パターン。

> **`design: true` キーの解釈（#182）**：recipe の `design: true` キー（§3.5）を RESOLVE 時に `--design` フラグと等価として処理し、design step の condition（`--design または size L+`）を上書きして常時 ON にする（size S/M でもスキップされない）。`--save-recipe` で保存した recipe の `design: true` を再利用する際も `--design` フラグなしで design step が常時有効になる。`--plan` ヘッダに `| design: on` を付加する（`design: true` または `--design` フラグが有効な場合のみ。`false`/省略時は付加しない）。`--list` に `· design` badge を付記する。`tdd: true` (#56) / `visual: true` (#174) と同じフラグ保存パターン。

> **`review: true` キーの解釈（#182）**：recipe の `review: true` キー（§3.5）を RESOLVE 時に `--review` フラグと等価として処理し、review step の condition（`--review または size L+`）を上書きして常時 ON にする（size S/M でもスキップされない）。`--save-recipe` で保存した recipe の `review: true` を再利用する際も `--review` フラグなしで review step が常時有効になる。`--plan` ヘッダに `| review: on` を付加する（`review: true` または `--review` フラグが有効な場合のみ。`false`/省略時は付加しない）。`--list` に `· review` badge を付記する。`design: true` と同じフラグ保存パターン（両フラグは常に対称に扱う）。

#### 4.3.1 --only / --from / --skip — step スライス

step スライスは §4.2 で確定した **最終 step リスト**（extends 適用後・condition 評価後）に対して適用する。

| flag | 動作 |
|---|---|
| `--only <step-id>` | 指定した step-id **1つだけ**を実行する。他の step はすべてスキップ。 |
| `--from <step-id>` | 指定した step-id から最後の step まで実行する。それ以前の step はスキップ。 |
| `--to <step-id>` | 先頭の step から指定した step-id（含む）まで実行する。それ以降の step はスキップ。`--from` との組み合わせで「A から B まで」の範囲スライス可（例 `--from implement --to verify`）。 |
| `--skip <step-id>` | 指定した step-id を**除外**してフローを継続する。複数指定可（例 `--skip design --skip review`）。size-aware 既定・`--design`/`--review` フラグより後に適用される（明示スキップが最終的に勝つ）。 |

- `--only` と `--from` は同時指定不可。同時に与えられた場合は `--only` を優先し、`--from` を無視して警告を出す。
- `--only` と `--to` の同時指定は `--only` 優先・`--to` を無視して警告を出す（`--only` が1 step 実行なので `--to` は意味なし）。
- `--only` と `--skip` の同時指定は `--only` 優先・`--skip` を無視して警告を出す（`--only` が1 step 実行なので `--skip` は意味なし）。
- `--from A --to B` で A が B より後に来る step の場合はエラー停止：`[ERROR] --from <A> --to <B>: step 順序が逆です（<A> は <B> より後に定義されています）。実行可能な step-id: <一覧>`。
- `--skip <step-id>` と `--review`（または `--design`）を同時指定した場合、`--skip` が勝ち、その step は実行されない（明示スキップが明示 ON を上書き）。
- 指定した `<step-id>` が最終 step リストに存在しない場合（`--only`/`--from`/`--to`/`--skip` 共通）は、**原因に応じて2ケースで報告する（#86）**：
  - **ケース A — step-id が recipe に存在しない（タイポ等）**：`[ERROR]` の直後・「実行可能な step-id:」全一覧の前に、編集距離（Levenshtein）≤ 2 の候補を距離昇順で最大 3 件「もしかして:」行として追加する（#190・#188 の `--recipe` タイポ提案と同形式・同計算ルール）。候補 0 件なら「もしかして:」行を省略する（ノイズを増やさない）。実行可能な step-id 一覧（RESOLVE 後の確定全リスト）は変わらず出す。例：`  もしかして: verify`（`verifi` 指定時）。
  - **ケース B — step-id は recipe に存在するが condition 評価で OFF**：`condition:` 式と有効化フラグのヒントを追加表示する。
    ```
    [ERROR] step `review` が見つかりません。
      reason: condition ("--review または size L+") が現在 OFF です（size が S/M のため）。
      hint:   --review フラグを追加すると有効になります：
              /rig:dev --only review --review
    実行可能な step-id: intake, implement, verify, pr, merge
    ```
  - **`--skip` の特例**：condition-OFF な step を `--skip` で指定した場合は**停止せず WARN のみ**（意図は「除外」であり、すでに OFF な step を skip するのは無害）：`[WARN] --skip review: review step はすでに condition-OFF です（--skip は不要）。`
  - **acceptance-gate step への `--skip` WARN（#126）**：`gate: acceptance-gate` を持つ step を `--skip` で除外した場合は**停止せず WARN のみ**（rig の品質保証の核 determinism-by-gate がサイレントにスキップされることを明示）：`[WARN] --skip <step-id>: <step-id> step は gate: acceptance-gate を持ちます — 品質収束ループがスキップされます。` `--autonomous` 時も同様に WARN を出す。condition-OFF WARN と同時に該当する場合は両 WARN を出す（condition-OFF → acceptance-gate の順）。
- スライスは **condition 評価後**のリストに対して行う。`--only design` を指定しても condition により design が OFF の場合はケース B のエラーになる（`--design` フラグを同時指定することで condition をパスできる）。
- **`--skip` と `--save-recipe` の関係**：`--from`/`--to`/`--only` と同様に実行時フィルタとして扱い、保存 recipe の `steps[]` には影響しない（§4.3.2 snapshot 意味論と同じ）。**`--skip` と `--save-recipe` の同時指定時の WARN（#187）**：`--skip <step-id>` と `--save-recipe <name>` が同時指定された場合、保存完了後に WARN を出す：`[WARN] --skip <id> は --save-recipe に反映されません（実行時フィルタ）。step を永続除外するには extends + remove: true を使用してください。保存した recipe には <id> を含む全ステップが含まれます。` 複数 `--skip` がある場合は step-id をまとめて列挙する（例：`--skip design --skip verify` → `--skip design, verify は...`）。`--autonomous` 時も WARN を省略しない（step ゲートなし実行時こそ情報が重要）。`--only` との同時指定は既存 WARN（`--only` 優先・`--skip` 無視）が先行し、本 WARN は出さない。
- **`--from`/`--to`/`--only` と `--save-recipe` の関係（#192）**：実行時フィルタとして扱い、保存 recipe の `steps[]` には影響しない（§4.3.2 snapshot 意味論と同じ）。**`--from`/`--to`/`--only` と `--save-recipe` の同時指定時の WARN（#192）**：`--from <step>`/`--to <step>`/`--only <step>` と `--save-recipe <name>` が同時指定された場合、保存完了後に WARN を出す：`[WARN] --from <step> は --save-recipe に反映されません（実行時フィルタ）。保存した recipe には <スライスで除外される step 一覧> を含む全ステップが含まれます（スライス前の全量）。step を恒久除外するには extends + remove: true を使用してください。` `--to`/`--only` 指定時も同形式（除外 step を明示）。`--autonomous` 時も WARN を省略しない（step ゲートなし時こそ情報が重要）。`--only` と `--skip` の同時指定（`--only` 優先・`--skip` 無視 WARN が先行）時は本 WARN を出さない。`--skip` 同時指定時の WARN（#187）と独立して両方を出す（`--from --skip` 等の組み合わせでは両 WARN を出す）。
- **`--plan` 表示（#204）**：`--skip` で除外される step は**全 step を表に出したまま** condition 列に `[SKIP: --skip flag]` 注記を付す。一方 `--from`/`--to`/`--only` で除外される step は**表から行ごと除外**し（`slice:` ヘッダで範囲を示す）、condition 列への注記は付さない — 両者は「範囲外の行を隠す（`--from`/`--to`/`--only`）」と「全行を見せたまま除外行を明示する（`--skip`）」という異なる表示モデルであり、`--from`/`--to`/`--only` に `[SKIP: ... 範囲外]` 相当の注記を追加する必要はない（詳細は §5 `--plan` ルールを正とする）。

#### 4.3.2 --save-recipe — 合成結果の保存

`--save-recipe <name>` が指定された場合、RESOLVE で確定した step リスト（extends 適用後・flag override 後の最終状態）を YAML frontmatter + Markdown で生成し、ファイルに書き出す。

| オプション組み合わせ | 書き出し先 |
|---|---|
| `--save-recipe <name>` | `<repo>/.claude/rig/recipes/<name>.md`（project 層） |
| `--save-recipe <name> --user` | `~/.claude/rig/recipes/<name>.md`（user 層） |

- `scope` キーは保存先 tier に応じて `project` または `user` に自動セットする。
- **`description` 自動生成規則（#47）**：recipe スキーマ（§3.5）では `description` は必須フィールド。`--save-recipe` はベース recipe 名と有効フラグから自動生成する：`"<ベース recipe 名> のカスタマイズ（<有効フラグ列挙>）"`（例: `"release-flow のカスタマイズ（--review --tdd）"`）。対話合成（ad-hoc）の場合は `"カスタム recipe（<有効フラグ列挙>）"`。`--save-recipe` 実行時に `--autonomous` が付いていてもこの自動生成を適用する（確認ダイアログは出さず自動生成のみ）。`--plan --save-recipe` のドライラン時はヘッダ `save-recipe:` 行に生成される `description` の内容を付記する（書き込み前に確認できるように）。**`--description "<text>"` 指定時の上書き（#163）**：`--description "<text>"` が指定された場合、frontmatter の `description` を自動生成の代わりに指定テキストで設定する（`--description` なしの場合は従来の自動生成を維持・後方互換）。`--save-recipe` なしで `--description` のみ指定した場合は `[WARN] --description は --save-recipe と組み合わせて使用してください（無視します）` を出して無視する。`--plan --save-recipe <name> --description "<text>"` のドライランでは `save-recipe:` ヘッダ行に description を付記して事前確認できるようにする（例: `save-recipe: nightly-review → /.claude/rig/recipes/nightly-review.md [project] — "夜間の CI 確認後に回す 3-way レビュー専用フロー"`）。`--autonomous` 時も自動生成と同様に確認ダイアログは出さず `--description` テキストをそのまま使う。
- 同名ファイルが既に存在する場合は**上書き前に確認**を取る（`--autonomous` 時は確認なしで上書き）。
- **lower-tier shadow チェック（#15・上書き確認より先に実行）**：保存先より**下位の tier**（project 保存なら user→shipped、user 保存なら shipped）に同名 recipe があるか §4.2.1 の検索順で確認する。あれば保存前に **WARN**（shadow 元の tier とパスを明示し「shadow 後は元 recipe の更新が自動適用されなくなる」と添える）。`--autonomous` 時はダイアログを省略し WARN のみ表示して続行。下位 tier に同名が無ければ WARN なし（新規名は通常運用）。`extends:` を使った意図的 shadow の場合は「`extends:` で継承するレシピか確認を」と1文付記する（丸ごと差し替えか継承かの気づきを促す）。
- **保存する `autonomy` 値（#33 / #181）**：起動時に `--autonomous` フラグが指定されていた場合は `autonomy: autonomous`、指定がなければ `autonomy: interactive` を frontmatter に保存する（ベース recipe の値を引き継がず常に明示保存）。これにより `--autonomous --save-recipe my-flow` で保存した recipe は再利用時も step ゲートなしで走り、保存時の意図が再現される（`--plan` ヘッダの `autonomy:` と保存 frontmatter が一致＝同一 RESOLVE 結果を参照するため差異ゼロ）。`--list` の `· autonomous` badge は `autonomy: autonomous` の recipe のみに付記（`interactive` は省略）。`--plan` ヘッダに `| autonomous: on` を付加する（`autonomy: autonomous` または `--autonomous` フラグが有効な場合のみ。`interactive` 時は付加しない）。
- **保存する `backend` 値（#52）**：起動時に `--workflow` フラグが指定されていた場合は `backend: workflow` を frontmatter に保存する（省略時は `manual`・明示保存せず省略も可）。再利用時に `backend: workflow` の recipe を RESOLVE すると `--workflow` フラグと等価として処理され、Workflow バックエンドで実行される（§4.3 / §6 実行バックエンド表）。`autonomy:` との対称性：実行意図の2軸（step ゲートの有無 / 実行エンジン）がともに frontmatter に揃う。
- **保存する `tdd` 値（#56）**：起動時に `--tdd` フラグが指定されていた場合は `tdd: true` を frontmatter に保存する（省略時は `false`・明示保存せず省略も可）。再利用時に `tdd: true` の recipe を RESOLVE すると `--tdd` フラグと等価として処理され、COMPOSE フェーズで implement subagent への TDD 注入が発動する（§4.3 `--tdd` の特例）。
- **`--persona` 指定分の保存（#57）**：起動時に `--persona <name>` が指定されていた場合、reviewer fan-out を行う step（`pattern: parallel-fanout` かつ `personas[]` を持つ step）の `personas[]` に各 `<name>` を追加する（名前で dedup）。これにより `--recipe my-flow` での再利用時も `--persona` を省略して同じ reviewer 集合が再現される。`--persona` 指定なしの場合は `personas[]` の変更なし（後方互換）。`--plan --save-recipe` のドライラン表示では保存後の `personas[]`（`--persona` 追加分を含む）が確認できる（§5 `--plan` の personas 列）。
- **保存する `no_default_personas` 値（#70）**：起動時に `--no-default-personas` フラグが指定されていた場合は `no_default_personas: true` を frontmatter に保存する（省略時は `false`・明示保存せず省略も可）。再利用時に `no_default_personas: true` の recipe を RESOLVE すると `--no-default-personas` フラグと等価として処理され、manifest `default_personas` の自動投入が抑止される（§4.3）。これにより、意図的に抑止した reviewer が再利用時に静かに復活しない（`--persona` 保存＝足す側／`no_default_personas` 保存＝manifest 由来を外す側、の両保存で `--plan` の personas 列と実行時 reviewer が一致する）。`--autonomous`/`--workflow`/`--tdd`/`--persona` と同じ保存対称性。
- **保存する `orchestrate` 値（#129）**：起動時に `--orchestrate` フラグが指定されていた場合は `orchestrate: true` を frontmatter に保存する（省略時は `false`・明示保存せず省略も可）。再利用時に `orchestrate: true` の recipe を RESOLVE すると `--orchestrate` フラグと等価として処理され、計算的オーケストレーションモードで実行される（§4.3）。`--plan` ヘッダに `| orchestrate: on`、`--list` に `· orchestrate` badge が付記される。`autonomy:`/`backend:`/`tdd:`/`no_default_personas:` と同じ保存対称性。
- **保存する `cross_llm` 値（#130）**：起動時に `--cross-llm` フラグが指定されていた場合は `cross_llm: true` を frontmatter に保存する（省略時は `false`・明示保存せず省略も可）。再利用時に `cross_llm: true` の recipe を RESOLVE すると `--cross-llm` フラグと等価として処理され、COMPOSE フェーズで ① implement step への `cross-llm-legibility` ポリシー注入 + ② review fan-out への `cross-llm-reviewer` persona 追加の両方が発動する（§4.3）。② の `cross-llm-reviewer` persona は `cross_llm: true` の RESOLVE 時に自動追加されるため、`--save-recipe` 時の `personas[]` への直接書き込み（#57 経路）は redundant になるが、後方互換のため維持する。`autonomy:`/`backend:`/`tdd:`/`no_default_personas:`/`orchestrate:` と同じ保存対称性。
- **保存する `no_capture` 値（#137）**：起動時に `--no-capture` フラグが指定されていた場合は `no_capture: true` を frontmatter に保存する（省略時は `false`・明示保存せず省略も可）。再利用時に `no_capture: true` の recipe を RESOLVE すると `--no-capture` フラグと等価として処理され、RUN 後の capture 提案を完全に抑止する（§7.3）。`hotfix`/`debug` など軽量 recipe を `--no-capture --save-recipe hotfix` で保存する典型ユースケースを想定。`autonomy:`/`backend:`/`tdd:`/`no_default_personas:`/`orchestrate:`/`cross_llm:` と同じ保存対称性。
- **保存する `adversarial` 値（#172）**：起動時に `--adversarial` フラグが指定されていた場合は `adversarial: true` を frontmatter に保存する（省略時は `false`・明示保存せず省略も可）。再利用時に `adversarial: true` の recipe を RESOLVE すると `--adversarial` フラグと等価として処理され、COMPOSE フェーズで敵対レビューステップが自動追加される（§4.3）。`--plan` ヘッダに `| adversarial: on`、`--list` に `· adversarial` badge が付記される。`autonomy:`/`backend:`/`tdd:`/`no_default_personas:`/`orchestrate:`/`cross_llm:`/`no_capture:` と同じ保存対称性。
- **保存する `visual` 値（#174）**：起動時に `--visual` フラグが指定されていた場合は `visual: true` を frontmatter に保存する（省略時は `false`・明示保存せず省略も可）。再利用時に `visual: true` の recipe を RESOLVE すると `--visual` フラグと等価として処理され、COMPOSE フェーズで verify step が UI 視覚確認モードで動作する（§4.3）。`--plan` ヘッダに `| visual: on`、`--list` に `· visual` badge が付記される。`autonomy:`/`backend:`/`tdd:`/`no_default_personas:`/`orchestrate:`/`cross_llm:`/`no_capture:`/`adversarial:` と同じ保存対称性。
- **保存する `no_orchestrate` 値（#178）**：起動時に `--no-orchestrate` フラグが指定されていた場合は `no_orchestrate: true` を frontmatter に保存する（省略時は `false`・明示保存せず省略も可）。再利用時に `no_orchestrate: true` の recipe を RESOLVE すると `--no-orchestrate` フラグと等価として処理され、manifest `default_orchestrate: true` や recipe `checks:`/`needs:` による自動有効化を両方打ち消す（§4.3）。`--plan` ヘッダに `| orchestrate: off`、`--list` に `· no-orchestrate` badge が付記される。`no_capture: true` (#137) / `no_default_personas: true` (#70) と同じ anti-flag 保存パターン。
- **保存する `design` 値（#182）**：起動時に `--design` フラグが指定されていた場合は `design: true` を frontmatter に保存する（省略時は `false`・明示保存せず省略も可）。再利用時に `design: true` の recipe を RESOLVE すると `--design` フラグと等価として処理され、design step の condition を上書きして常時 ON にする（size S/M でもスキップされない）。`--plan` ヘッダに `| design: on`、`--list` に `· design` badge が付記される。`tdd: true` (#56) / `visual: true` (#174) と同じフラグ保存パターン。
- **保存する `review` 値（#182）**：起動時に `--review` フラグが指定されていた場合は `review: true` を frontmatter に保存する（省略時は `false`・明示保存せず省略も可）。再利用時に `review: true` の recipe を RESOLVE すると `--review` フラグと等価として処理され、review step の condition を上書きして常時 ON にする（size S/M でもスキップされない）。`--plan` ヘッダに `| review: on`、`--list` に `· review` badge が付記される。`design: true` と同じ保存パターン（両フラグは常に対称に扱う）。
- **保存する `capture` 値（#184）**：起動時に `--capture` フラグが指定されていた場合は `capture: true` を frontmatter に保存する（省略時は `false`・明示保存せず省略も可）。再利用時に `capture: true` の recipe を RESOLVE すると `--capture` フラグと等価として処理され、RUN 後の capture 提案を承認ダイアログなしで自動実行する（§7.3）。`--capture` と `--no-capture` が同時に指定された場合は `--no-capture` 優先＋WARN（§7.3 整合）。`--plan` ヘッダに `| capture: on`、`--list` に `· capture` badge が付記される。`no_capture: true` (#137) の対称補完（完全抑止↔自動承認）。`autonomy:`/`backend:`/`tdd:`/`no_default_personas:`/`orchestrate:`/`cross_llm:`/`no_capture:`/`adversarial:`/`visual:`/`no_orchestrate:`/`design:`/`review:` と同じ保存対称性。
- **保存ファイルに `extends` は含めない（snapshot 意味論・#34）**：§4.2.2「extends は合成後の recipe には残さない」と同じく、`--save-recipe` は **extends 解決済みの完全展開 steps** を保存する。`extends: X` を持つ recipe を base に保存しても、保存ファイルは `extends` なし・全 steps 展開済みになる（将来の親 recipe 変更が静かに波及しない＝再現性を保証）。`extends:` を明示利用した継承 recipe を新規作成したい場合は `--save-recipe` を使わず手動で recipe に `extends:` を記述する。
- **`--from`/`--to`/`--only` スライスは保存 step リストに影響しない（#37, #141）**：`--from`/`--to`/`--only` は実行時フィルタ（「今回の RUN でどの step を実行するか」の絞り込み）であり、recipe 定義（「このフローが持つ steps の全量」）の一部ではない。`--from implement --to verify --save-recipe my-flow` を実行しても、保存される `my-flow.md` には intake を含む**全 steps** が含まれる（スライス前の完全フロー）。これにより後で `--recipe my-flow` を `--to` なしで実行すれば全工程を再現できる（「保存→一覧→再利用の輪」が断たれない）。`--plan --save-recipe`（下記）の `save-recipe:` ヘッダが表示する step 数もスライス前の全量。
- `--save-recipe` は実行フローを止めない。保存後そのまま RUN を継続する。ただし `--plan` と同時指定された場合は COMPOSE 完了時点で保存し、ハーネスを提示して停止（RUN なし）。

### 4.4 size-aware 既定（軽さ優先）

変更規模に応じて重い step を自動 OFF する。行数閾値は manifest の `size_thresholds` キー（サブキー `S_max` / `M_max` / `L_max`）で上書きできる（未設定時は pr-hygiene 基準 `S_max:100` / `M_max:200` / `L_max:400` を使用。テンプレは `manifests/_template.md`）。

- **S / M**（既定：`M_max` 以下＝～200行）: design / review / tdd を**既定 OFF**。明示 flag で ON にした場合のみ実行。
- **L 以上**（既定：`M_max` 超＝200行超。`L_max` 超は分割必須）: design / review を推奨し、ON を促す。

**コスト予算（`--budget`・§3 flag）** — size-aware が「変更の重さ」で間引くのに対し、budget は**支出の上限**で間引く：`low`＝組み込み 3-way のみ（追加 reviewer・自動追加 step を抑止し、必要なら提案だけ出す）・workflow 禁止。`mid`＝3-way＋選択投入2枠まで。予算で抑止した項目は `--plan`／完了レポートに `[BUDGET: 抑止]` と明示する（サイレントに削らない）。manifest `default_budget: low|mid` で恒久設定・`--budget` フラグが優先。

### 4.5 autonomy

`--autonomous` で step ゲート OFF。指定が無ければ各 step 後に確認する step ゲート ON。

> **`--autonomous` が外すのは「step ゲート（各 step 後の確認ダイアログ）」だけ。** `acceptance-gate`（受け入れ基準を満たすまで最大 K 回収束し、K 超で user エスカレーションする品質ループ）は `--autonomous` でも変わらず動く。capture ゲートと同様に、品質保証の核は `--autonomous` で解除されない。recipe の `autonomy: autonomous`（§3.5）の「ゲートなし」も step ゲートを指し、acceptance-gate の品質ループは維持される。

---

> **動作仕様**：manifest ロード（§4.1）・recipe tier 検索順（§4.2.1）・extends 1段継承（§4.2.2）・--only/--from/--to スライス（§4.3.1）・--save-recipe（§4.3.2）は本セクションの規則どおり動作する。shipped recipe は §2 目録を参照。project / user 層の recipe はリポジトリまたはホームに配置すれば即時有効になる。
>
> **RESOLVE の一次実装はコード（フェーズ3・舵をコードに）**：named recipe（`--recipe` / manifest `default_recipe` / bare 名で解決した recipe ファイル）の RESOLVE では、エンジンは自力で散文規則を解釈するのではなく、**まず `orchestrate plan <recipe> --json --with "<flags>" --diff-git` を実行し、その出力を RESOLVE の確定結果として使う**：`effective_steps`（実行 step 集合）・各 step の `active`/`why`（condition 列の注記に転記）・`errors`（あれば散文エンジンも ERROR 停止）・`warnings`（そのまま表示）・`mode`（orchestrate on/off/auto・autonomy・backend）・`badges`/`steps_field`（`--list` 表示）。extends マージ（remove/origin）・condition 評価・size 判定（§4.4・`--diff-git` が git diff HEAD から自動測定）・スライスと優先順位（§4.3.1）・recipe キー⇔フラグ等価（§4.3）・manifest `size_thresholds`/`default_orchestrate`（§4.1）はすべてこの出力が正。`selftest` シナリオ Q/R/S が golden 検証。
> **フォールバック（散文規則）**：スクリプトを実行できないとき（python3 不在・`orchestrate` コマンドが見つからない・Bash 拒否）と **ad-hoc 対話合成**（recipe ファイルが無い）に限り、本セクションの散文規則を自力で適用する。その場合も規則の解釈が割れたら `selftest` の golden が正＝**コード側を先に直し、本セクションを追随させる**。COMPOSE 以降（facet 合成・knowledge 注入・subagent dispatch）は従来どおりエンジンの仕事（スクリプトは RESOLVE までを担う）。

## 5. COMPOSE — ハーネス合成

RESOLVE で確定した各 step について、`step ＋ pattern ＋ facet（配置順厳守）＋ native 委譲先` を組み立てて subagent prompt を生成する。

### facet 配置順（recency を意識し厳守）

subagent prompt を組むときの facet 配置は**必ず**この順：

| 位置 | facet 種別 | 理由 |
|---|---|---|
| **System** | **Persona** | 人格・観点を最初に固定 |
| **User 先頭** | **Knowledge** | 前提知識を文脈の冒頭に |
| **User 中部** | **Instruction** | 具体手順 |
| **User 構造部** | **Output Contract** | 出力フォーマット縛り |
| **User 末尾** | **Policy** | recency が効く末尾にガードレール |

### 知識層の注入

subagent prompt を組む前に、以下の順で関連する知識ブリックを選択し、facet 配置順に沿って注入する。

**選択対象（tier 順）:**

| tier | パス | カテゴリ |
|---|---|---|
| **user 層** | `~/.claude/rig/knowledge/methodology/` | 設計・開発手法（DDD / クリーンアーキテクチャ / SOLID 等） |
| **user 層** | `~/.claude/rig/knowledge/ai-quirks/` | AI の既知失敗パターン（二相管理、下記参照） |
| **project 層** | `<repo>/.claude/rig/knowledge/domain/` | ドメイン設計・ユビキタス言語・認証モデル・ADR |
| **project 層** | `<repo>/.claude/rig/knowledge/accumulated/` | 蓄積知識（実行履歴から抽出されたパターン・学び）→ User 先頭（Knowledge 位置）に注入 |
| **wiki（user＝global 一次）** | `~/.claude/rig/knowledge/wiki/` | 正準な概念ページ（相互リンク `[[slug]]`）。persona の `inject:` / `[[link]]` で参照 |
| **wiki（project＝overlay）** | `<repo>/.claude/rig/knowledge/wiki/` | 同 slug を上書き/追補（ページ単位で project 優先） |

いずれかの tier ディレクトリが存在しない場合は**サイレントにスキップ**する（エラーにしない）。

**wiki ページの参照と注入（`facets/knowledge/_wiki` 参照）:**

- persona facet が `inject: ["[[slug]]", …]` を宣言している場合、各 `[[slug]]` を **tier 解決**（project overlay > global > shipped `skills/rig/facets/knowledge/wiki/`）してページを取得し、**User 先頭（Knowledge 位置）に注入**する（1ホップ既定・過剰展開しない）。
- 本文中の `[[slug]]` も同様に解決対象。`[[slug|表示名]]` 記法可。解決できない `[[...]]` は**注入せず**、`--validate` がリンク切れとして報告する。
- wiki は「事実」、persona は「判断・声」。**persona は事実を埋め込まず wiki を参照する**（暗黙知サイロを避ける）。

**注入位置:**

- **methodology / domain** の知識ブリック → subagent prompt の **User 先頭**（Knowledge 位置）に注入する。
- **ai-quirks** は**二相注入**する：
  1. **記述形（知識）** → User 先頭の Knowledge 位置（他の知識ブリックと同列）に注入。
  2. **導出規範形（derived Policy）** → User 末尾の Policy 位置（recency が効く末尾）に注入。Policy facet（`facets/policies/`）と同じ位置に配置する。

知識層の構造・ディレクトリ規約・ai-quirks 二相の詳細は `facets/knowledge/_layer.md` を参照。

### native 委譲

各 step は**既存の skill / command / agent に委譲**する（§8 Native-first）。reviewer は **agent 優先**（subagent_type: security-reviewer / design-reviewer / test-reviewer）、無ければ **persona facet を合成**して subagent に渡す（facet: `facets/personas/{security,design,test}-reviewer`）。instruction facet は薄く、手順の本体は委譲先に置く。

### persona facet の tier 解決（project → user → shipped）

persona 名（recipe の `personas[]` / `--persona <name>` / フォールバック合成）を解決するとき、recipe（§4.2.1）と同じ順でファイルを探す。**先に見つかった tier 優先**。

| tier | パス | 優先度 |
|---|---|---|
| **project**（最高） | `<repo>/.claude/rig/personas/<name>.md` | 1 |
| **user**（global） | `~/.claude/rig/personas/<name>.md` | 2 |
| **org**（チーム共有・任意） | `<org_dir>/personas/<name>.md`（manifest `org_dir:` または env `RIG_ORG_HOME` が指す**チームの git リポジトリ**） | 3 |
| **shipped**（同梱） | `skills/rig/facets/personas/<name>.md` | 4（最低） |

> **org tier**：チームで育てるブリック層。実体は clone した共有 git リポジトリ（`personas/` `recipes/` `knowledge/wiki/` を持つ）で、manifest の `org_dir:` か環境変数 `RIG_ORG_HOME` で指す。解決順は **project → user → org → shipped**（個人の customize がチーム標準に勝ち、チーム標準が shipped に勝つ）。recipe・wiki も同順で解決する。未設定ならこの tier はサイレントにスキップ（従来どおり3 tier）。`--validate --global` / `/rig:catalog` は org tier も走査する。

- `<name>` は `/` 区切りでサブディレクトリ可（例 `sales/hearing-reviewer`）。
- **persona facet の frontmatter はメタデータ**（`name`＝`personas/` からの相対パス・`description`・任意の `inject:`）。COMPOSE が subagent System に合成するのは**本文のみ**で、frontmatter は注入しない（`inject:` の wiki 解決と `--list --global`／catalog の表示にのみ使う）。スキーマは `--validate` ③-b が点検する。
- reviewer は引き続き agent（subagent_type）優先。agent が無いときの persona facet フォールバックはこの tier 検索で解決する。
- **review fan-out の追加枠（shipped）**：`performance-reviewer`（データ量スケール・ホットパス）と `observability-reviewer`（失敗の可視性・ロールバック）は既定の 3-way には入らず、`--persona` / manifest `default_personas` / recipe `personas[]` で必要な変更にだけ足す（`facets/instructions/parallel-review` 参照）。
- これにより `/rig:persona` で生成した persona（既定 project / `--user` で global）を**名前で即使える**。
- **`--persona <name>` flag**：review fan-out に名前指定のカスタム reviewer persona を追加する（複数可）。各 `<name>` を上表で解決し、組み込み reviewer と同列に subagent へ dispatch（persona facet を System に合成）。解決できなければ「persona が見つかりません」と報告して停止。

### manifest `default_personas` の自動投入（製品ごとの常時 reviewer）

manifest（§4.1）に `default_personas: [<name>, …]` が宣言されている場合、**その製品の review/adversarial step に毎回それらの persona を自動投入**する。`--persona` を毎回打たなくても、その製品のドメイン reviewer（例: VST プラグインなら `house-authenticity`）が常にレビューに参加する。

- **解決**：各 `<name>` を上の tier 検索（project → user → shipped）で解決する。`--persona` と同じ経路。
- **wiki の同伴**：解決した persona が `inject: ["[[slug]]", …]`（§5 wiki）を宣言していれば、その wiki ページも通常どおり Knowledge 位置へ自動注入される＝**persona を入れれば事実も付いてくる**。
- **適用範囲**：review 系 step（`review` / `adversarial-review` 等、persona を fan-out する step）にのみ作用する。step を持たない recipe（design のみ等）には影響しない。
- **合成と重複排除**：最終 reviewer 集合 ＝ `組み込み reviewer（size-aware）` ＋ `recipe の personas[]` ＋ `manifest default_personas` ＋ `--persona 指定分` を **名前で和集合**（同名は1つに dedup）。
- **解決失敗**：manifest に書かれた名前が見つからない場合は「default_personas の `<name>` が解決できません」と**警告**して当該 persona をスキップする（停止はしない＝製品全体のフローを止めない。`--persona` の明示指定だけは従来どおり停止）。
- **抑止**：この run だけ自動投入を外したいときは `--no-default-personas`（§3 flag）。恒久的に変えるなら manifest を編集する。

> 設計意図：`--persona` は「この run で足す」一時指定、`default_personas` は「この製品では常に使う」恒久宣言。**ドメイン reviewer を毎回タイプせず、製品 manifest に1回書けば自動で効く**（友人の "VST プラグインのレビューには毎回ハウス審美 reviewer を" を1行で表現）。自動選択は manifest 明示に限定し、タグ推測による暗黙ルーティングはしない（確実性優先）。


### `--plan` の停止

`--plan` 指定時は COMPOSE で停止し、合成ハーネスを**正準フォーマット**で提示する（RUN はしない）。出力は機械抽出しやすい固定構造（2回叩いても同じ構造・並び＝出力も determinism-by-gate）：ヘッダ（`recipe: <name> [tier]`・`diff:`/size・`description:`・`flags:`・`save-recipe:`/`skip:`/`slice:` 行・モード修飾子 `| tdd: on` 等）→ **step テーブル**（解決済み最終 step・condition 先行評価・personas の出所マーカー ★/†/‡ と `[tier]`・`extends` 時は `origin` 列）→ `### Gate:`（acceptance/review ゲート条件のチェックリスト・`max_retries` 解決元マーカー）→（`--orchestrate` 時のみ）`### Checks:` / `### DAG:` → `### Knowledge: 注入予定ソース`（tier 別ファイル一覧＋persona `inject:` の wiki 解決）→ `### Reviewer Fan-out:`（最終 reviewer 集合）→（loop 時のみ）`### Loop Config:` → 末尾 `steps:` サマリ（condition 付き/gate 数・`acceptance retries 上限:`）。**表示仕様の正本は `facets/instructions/plan`** — `--plan` 実行時は必ずこれを読んで従う。`--save-plan <path>` は同一内容をファイルにも書き出す（§3 flag・停止セマンティクス不変）。

## 6. RUN — 実行（context-minimal が絶対条件）

Claude Code primitive（`Agent` ツール＝subagent dispatch、`Task`、skill 呼び出し）でハーネスを実行する。

### 実行バックエンド

RUN フェーズは2つのバックエンドを持つ。**既定は manual**。

| バックエンド | 起動条件 | 実行手段 | 使いどき |
|---|---|---|---|
| **manual**（既定・軽量） | 常に（`--workflow` なし） | 親が `Agent` ツールで subagent を手 dispatch | S / M サイズ変更・通常の fan-out |
| **workflow**（opt-in） | `--workflow` フラグ**または** ultracode on | ultracode Workflow ツール（CC ネイティブ） | 重い多段 fan-out / 網羅レビュー / 大規模 migration |

**size-aware との関係**：S / M サイズでは `--workflow` を指定しても重い処理は不要なため、バックエンド選択と無関係に軽量ハーネスを組む。workflow バックエンドが本領を発揮するのは変更規模 L 以上かつ多段並列が必要な場合のみ。

> `patterns/workflow-backend` — ブリック→Workflow 構文の対応表、ガード（opt-in 必須 / 重厚なワークフローエンジン化の回避 / 既定 manual の維持）を参照。

### context-minimal（ハードルール）

- **実作業（実装・レビュー・調査・デバッグ・検証）は必ず subagent に dispatch する。** 親（オーケストレーター）は **dispatch ＋ structured-report の集約 ＋ ゲート判断**だけを行う。
- 親コンテキストに**長い tool 出力やコード本文を引き込まない**。subagent には `output-contracts/review-verdict` 等の機械抽出可能な structured-report を返させ、親は判定行だけ読む。
- 並列可能な独立観点は `patterns/parallel-fanout` で**1メッセージ多 dispatch**。集約は `patterns/review-gate`。

### run-continuity（可視マーカー＋再アンカー）— 中断後も駆動を切らさない

RUN 規律は SKILL.md 指示の recency に依存するため、**途中で質疑・脱線が挟まると親が静かに red flag（直接実装・ゲート省略）へ逸れ**、しかもそれが画面に出ず user が「rig が駆動中か」を見分けられない。これを常時 ON の規律で防ぐ。**opt-in ではない。** 出力増は1行ヘッダ＋ step 境界に限定し、軽さ既定・context-minimal を壊さない。

**① run-status ヘッダ** — RUN がアクティブな**各ターンの冒頭**に現在のハーネス状態を1行で再掲する。

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending [(try N/K)]|passed|REJECT> [| stuck: N/2] | backend: <manual|workflow> [| orch: <on|auto>] | mode: <gated|autonomous> [| iter: X/N]
```

- `recipe`：`--recipe`/manifest 由来名。対話合成なら `ad-hoc`。**tier 表示ルールは `--plan`（#25）と統一する（#125）**：`project`/`user` tier の recipe は `recipe: <name> [project]` / `recipe: <name> [user]` と明示、`shipped` のみは省略可（`recipe: <name>` のまま——新規ユーザーへの静かな既定）、対話合成は tier なし（`recipe: ad-hoc`）。これにより `--plan`（事前）→ run-status（実行中）の全フェーズで tier 情報が追跡可能になる。`step`：現 step の id と位置（`--only`/`--from` スライス時はスライス後の N）。`gate`：現 step のゲート状態。
- **`gate: pending` の acceptance-gate 試行位置（#32）**：`gate: acceptance-gate` の step が収束ループ中（基準未達で retry に入った）は `pending (try N/K)` と試行回数を付す（`K` は当該 step の `max_retries`・RESOLVE 確定値で `--plan` の `（max_retries: N）` と同じ出所）。`step: (n/N)` が「全フロー中の位置」を示すのと対称に、`(try N/K)` は「この step 内の収束ループの位置」を示す。**初回実行（まだ retry に入っていない 0 回目）は `(try …)` を付けない**（素の `pending`。retry 1 回目から `(try 1/K)`）。`K 超`で `## rig acceptance-gate: K 超エスカレーション`（§6）へ。`gate: none|passed|REJECT` は確定状態のため `(try …)` を付けない（既存表記を維持）。
- **`orch:` フィールド（計算的オーケストレーション）**：この RUN が orchestrate を通るときだけ `backend:` の直後に付す＝**明示時 `orch: on` / 自動有効化時 `orch: auto`**（§4.3：recipe の `checks:`/`needs:` か manifest `default_orchestrate`）。オフ（従来の散文エンジン）なら**省略**（ヘッダ長を増やさない）。これで「今このフローは舵をコードが握っているか」が毎ターン一目で分かる。
- **自動有効化の一言通知**：orchestrate が**自動で**ON になった最初のターンに、run-status の直後へ1行で理由と戻し方を示す＝`🧭 計算的オーケストレーションで回します（理由: <recipe に needs 宣言 | recipe に checks 宣言 | manifest default_orchestrate>）。対話的な散文エンジンに戻すには --no-orchestrate。` 明示 `--orchestrate` 時は既に意図的なので通知しない。
- **`stuck: N/2` フィールド（#117）**：stuck-guard カウンタ（§6「step ゲートと詰まりガード」）が **1 以上**になったとき、`mode:` フィールドの直前に `| stuck: N/2` を追加する（`2` は stuck-guard の固定上限）。カウンタ = 0 のとき（通常時）は**省略する**（ヘッダ長を増やさない）。カウンタが #36 規則でリセットされたら `stuck:` フィールドも消える。`acceptance-gate` の `(try N/K)` が「収束ループの深さ」を示すのと対称に、`stuck: N/2` は「同一エラー反復の深さ」を示す（2つの独立カウンタが両方可視化される）。例：`gate: pending (try 1/2) | stuck: 1/2` は「acceptance-gate も stuck-guard も次でエスカレーション直前」を一目で示す。
- **`iter:` フィールド（`loop` レシピ専用・#176）**：`loop` レシピ（`facets/instructions/loop-driver` 経由）の RUN 中のみ、`mode:` フィールドの後に付す（他のレシピでは**省略**）。各 tick 開始時に更新する。フォーマットはループ設定によって変わる：`--times N` 指定時は `iter: X/N`（X = 現在の実行回数。例: `iter: 3/5`）、`--until <condition>` 単独時（回数上限なし）は `iter: X`（分母なし。例: `iter: 3`）、`--times N` + `--until` 併用時は `iter: X/N (監視中)`（例: `iter: 3/5 (監視中)`）。`--plan` の `### Loop Config:` ブロック（§5）が「予定」を示すのと対称に、`iter:` は「実行中の現在 tick」を示す。コンテキスト圧縮後の再開時（② 再アンカー）も `iter:` フィールドを含めて run-status ヘッダを再掲する（`loop-driver.md` ④「次 tick 予約の正準状態に経過 tick を含める」と対応し、圧縮をまたいでも tick 数が失われない）。
- これにより「**rig が今ここを駆動中**」と「次でエスカレーションが来るか」が常に可視化される。

**② 再アンカー規則** — 質疑・脱線で**1ターン抜けた直後の作業ターン**は、作業に入る前に必ず：(1) ① のヘッダを再掲、(2) アクティブなハーネス状態を1行で再宣言（どの recipe のどの step を、どの委譲先で再開するか）、(3) **現 step から再開**する。**素の直接作業・ゲート省略へ静かに切り替えない**（下記 red flag に明示適用）。

**③ step 境界バナー** — step の開始/委譲/ゲート/完了で印を1行出し、subagent dispatch とゲートが実際に起きていることを可視化する。

```
── step <id> ▸ dispatch → <agent|subagent>
── step <id> ▸ gate: <acceptance-gate|review-gate> [<pending→passed|REJECT>]
── step <id> ▸ done
```

**acceptance-gate criterion 単位の合否表示（#159）**：`gate: acceptance-gate` を持つ step で基準が未達（`pending`）のとき、step 境界バナーの直下に各 criterion の合否（`✓`/`✗`）と未達の簡潔な根拠（1行以内、サブエージェントの structured-report から抽出したサマリ）を追記する。合格（`passed`）時は1行バナーのみ維持する（全件 ✓ のため列挙を省略し冗長を避ける）。`acceptance[]` が空配列の step では `（基準未設定 — WARN: ゲートが常時通過）` のみ表示する（`--validate ③` WARN と同義）。`--autonomous` 時も同様に表示する（オーケストレーターが状態を把握できるように）。

```
── step verify ▸ gate: acceptance-gate pending (try 1/2)
   ✓ build が成功
   ✗ lint 0 件 （3 errors found）
   ✓ 全テストが green
   → lint エラーを修正して再試行
── step verify ▸ done
```

> **会話モード（talk）の例外**：talk 自身の地の会話ターンにはヘッダを出さない（短い話し言葉を保つ）。talk が委譲した先のフローが RUN に入ったら、その RUN に①〜③が適用される。

**④ 圧縮境界（compaction）— 最大の中断を生き延びる** — コンテキスト自動圧縮（ハーネスの `autoCompactEnabled`、既定 ON）は **rig 規律にとって最強の中断**。圧縮そのものはハーネス制御で rig は置換しないが、圧縮を**跨いで状態を失わない**ために二重で備える。

- **保存（プラグイン同梱フック）**：rig は `PreCompact` フック（`hooks/hooks.json` → `hooks/preserve-rig-state.sh`）を同梱する。圧縮直前に発火し、stdout が**追加の圧縮指示**として効いて、run-status（recipe/現 step/gate/mode）・受け入れ契約・残 step・主要決定・context-minimal 規律を要約に残させる。`/rig:init` は同等の保全文を `CLAUDE.md` の "Compact Instructions" 節にも置ける（毎回自動適用される第2経路）。
- **復帰（再アンカーの適用）**：**圧縮直後の最初の作業ターンは ② 再アンカー規則を必ず適用**する（ヘッダ再掲＋ハーネス状態の再宣言→現 step に委譲で復帰）。`SessionStart(source=compact)` での自動再注入は既知の不具合があるため当てにせず、② の再アンカーで確実に戻す。

### **red flags（STOP→委譲）**

- 親が**直接コードを書き始める** / **再実装する**（**中断・質疑の直後に素の作業へ静かに戻る**場合を含む）。
- 親が長い diff・ログ・ファイル全文を**自分の context に読み込む**。
- 軽い変更を**過剰に重く**（不要な design/review/tdd を）回す。
- `--only` / `--from` を無視して**部分実行せず全部やる**。
- agent / subagent を使わず**親が全部書く**。
- 親が `--workflow` / ultracode なしに Workflow を**無断起動する**。
- 親が承認なしに memory / knowledge layer に**サイレント書き込みする**。
- 中断後に **run-status ヘッダの再掲・ハーネス状態の再宣言を省いて**作業を再開する。

### step ゲートと詰まりガード

- `--autonomous` でない限り、各 step 後に結果を提示し**次へ進む確認**を取る（step ゲート）。
- **同じ所で2回詰まったら**（同じエラー・同じレビュー REJECT を2巡）勝手に試行を続けず、**正準フォーマットで user に判断を仰ぐ（#12）**：

```
## rig stuck-guard: エスカレーション

step: <id> (<n>/<total>) | gate: <none|acceptance-gate|review-gate> | 同一エラー繰り返し: 2回
エラー要約: <1行。テスト失敗なら「テスト N 件失敗」、REJECT なら「reviewer REJECT: <観点>」>

判断してください：
  a) 別のアプローチで再試行する（新しい指示を入力）
  b) この step をスキップして次の step へ進む
  c) このフローを終了する

入力: [a / b / c]
```

  - **エスカレーション後の stuck カウンタ規則（#36）**：user が a)「別のアプローチで再試行」を選んだら stuck カウンタを **0 にリセット**する（新しい指示による再試行は実質的に新しい試みなので、再び同一エラーが**2 回**続いた時にのみ次のエスカレーションを発動する＝「2 回」は a 選択をまたいで累算しない）。何度でも a→retry を繰り返せるが、2 回同一失敗が無ければエスカレーションしない品質フィルタは維持される。b)「スキップ」・c)「終了」選択時は step／flow が終了するためカウンタは irrelevant（リセット規則は適用しない）。なお acceptance-gate K 超の d)「max_retries を増やす」は acceptance-gate 側の K カウンタに作用し、stuck カウンタとは独立（本 §の「独立カウンタ」定義のとおり）。
  - **acceptance-gate の K 超エスカレーション**（独立カウンタ）は**別ヘッダの専用フォーマット**で出す（#28・どちらが発動したか一目で判別できるように）：

```
## rig acceptance-gate: K 超エスカレーション

step: <id> (<n>/<total>) | gate: acceptance-gate | 試行: <K>/<max_retries> 回超過
未達基準: <最後の試行で満たされなかった受け入れ基準>

判断してください：
  a) 別のアプローチで再試行する（新しい指示を入力）
  b) この step をスキップして次の step へ進む
  c) このフローを終了する
  d) max_retries を増やす / 受け入れ基準を見直す
```

   stuck-guard（同一エラー反復）と acceptance-gate K 超（毎回違う理由でも K 回未達）は**発動条件が違う独立カウンタ**なので、`同一エラー繰り返し:` フィールドは前者専用・後者では使わない（意味の誤用を避ける）。
  - **acceptance-gate K 超エスカレーション後も capture 提案（§7.1 `stuck-twice`）を自動提示する（#46）**：K 超は「受け入れ基準を K 回試みたが一度も満たせなかった」最も根の深い詰まりケースであり、stuck-guard と同様に `stuck-twice` capture を提案する。§7.3 の承認ゲートは維持される（`--capture` フラグで省略可）。
  - エスカレーション後は **capture 提案（§7.1 `stuck-twice`）を自動提示**し、詰まりの学びを次回 RUN に残す（a 選択後の再エスカレーションを含め、**エスカレーションが発生するたびに**提示する＝acceptance-gate K 超を含む。同じ根本原因が繰り返すほど学びの蓄積が重要）。
- reviewer は agent 優先（subagent_type 名で起動）・persona facet フォールバック。`review-gate` で REJECT があれば停止して user へ。

### フロー完了レポート（#102）

全 step が完了（または escalation/skip で終了）した後、次の正準フォーマットでフロー全体のサマリを出力する。`autonomy: autonomous` では**必須**（step ゲートがなくフローが一気に走るため、完了後に事後確認できる唯一の集約情報）。`autonomy: interactive` では各 step ゲートで結果を都度確認しているが、同じフォーマットで集約サマリとして出力する（`--plan`（事前）との対称構造を保つ）。

```
## rig フロー完了

recipe: <name[tier]> | autonomy: <interactive|autonomous> | backend: <manual|workflow>[| tdd: on][| no-defaults: on][| orchestrate: on][| cross-llm: on][| no-capture: on][| adversarial: on][| visual: on][| orchestrate: off][| design: on][| review: on][| capture: on]
steps: <N> 完了 / <M> スキップ / <K> エスカレーション

| step      | outcome                           | gate                              |
|-----------|-----------------------------------|-----------------------------------|
| intake    | ✓ done                            | —                                 |
| design    | [SKIP] condition-OFF (size S/M)   | —                                 |
| implement | ✓ done                            | —                                 |
| verify    | ✓ done                            | acceptance-gate passed (try 2/2)  |
| review    | ✓ done                            | acceptance-gate passed (try 1/2)  |
| pr        | ✓ done                            | —                                 |
| merge     | ✓ done                            | —                                 |
```

- `outcome`：`✓ done`（正常完了）/ `[SKIP] <理由>`（condition-OFF または `--skip` 指定。`--plan` の `[SKIP: --skip flag]` と同じ語彙）/ `[ESCALATED]`（stuck-guard または acceptance-gate K 超エスカレーション発動）
- `gate`：acceptance-gate を通った step は `acceptance-gate passed (try N/K)`（N=実試行回数、K=`max_retries`）。review-gate を通った step は `review-gate passed`。ゲートなしは `—`。
- ヘッダの `steps: N 完了 / M スキップ / K エスカレーション` でフロー全体の集計を1行で示す。
- **モード修飾子（#132, #137, #172, #174, #178, #182, #184, #186）**：`| tdd: on` / `| no-defaults: on` / `| orchestrate: on` / `| cross-llm: on` / `| no-capture: on` / `| adversarial: on` / `| visual: on` / `| orchestrate: off` / `| design: on` / `| review: on` / `| capture: on` はそれぞれ対応する recipe キーまたはフラグが有効な場合のみ付加する（`--plan` ヘッダと同じ条件・同じ表記。無効時は省略）。`| orchestrate: off` は `no_orchestrate: true` または `--no-orchestrate` が有効な場合のみ（#178・#186）。`| design: on` は `design: true` または `--design` が有効な場合のみ（#182・#186）。`| review: on` は `review: true` または `--review` が有効な場合のみ（#182・#186）。`| capture: on` は `capture: true` または `--capture` が有効な場合のみ（#184）。`--plan`（予定）と完了レポート（実績）の recipe ヘッダが同一フォーマットになり、ドライランから完了後まで機械的に比較できる。
- `--plan`（実行前）のテーブルと対称構造：`--plan` が「予定」、このレポートが「実績」として対応する（`--plan` のテーブルを参照することでそのまま比較できる）。

**`--from`/`--to`/`--only` スライス指定時（#108, #141）**：`--plan --from`/`--to`/`--only` と対称的に、テーブルには**スライス後の step のみ**を表示し、ヘッダに `slice:` フィールドを追加する。

```
## rig フロー完了

recipe: release-flow | autonomy: interactive | backend: manual
slice: implement → end
steps: 4 完了 / 0 スキップ / 0 エスカレーション

| step      | outcome | gate                             |
|-----------|---------|----------------------------------|
| implement | ✓ done  | —                                |
| verify    | ✓ done  | acceptance-gate passed (try 1/2) |
| pr        | ✓ done  | —                                |
| merge     | ✓ done  | —                                |
```

- スライス前の step（`--from` 開始前の step・`--to` 終端後の step、または `--only` 対象外の step）は**テーブルに出さない**（`--plan --from`/`--to`/`--only` と同じ）。
- ヘッダの `steps: N 完了 / M スキップ / K エスカレーション` は**スライス後の step のみ**をカウントする（スライス前の step は含まない）。
- `slice:` フィールドの書式：`--from <id>` なら `<id> → end`、`--to <id>` なら `start → <id>`、`--from <A> --to <B>` なら `<A> → <B>`、`--only <id>` なら `<id> only`。
- `--from`/`--to`/`--only` と `--skip` の組み合わせ時は `slice:` と `skip:` を**両方**ヘッダに出す（`--plan` の `#88` と同じ対称規則）。スライス前の step が `--skip` 対象だった場合もテーブル行は表示しない（スライス外のため行が無い）。

**`--skip` 単独指定時（#120）**：`--skip` 単独指定（`--from`/`--only` なし）でフローが完了したとき、完了レポートのヘッダに `skip: <step-id(s)>` 行を追加する（`--plan` の #50 と同一形式・`, ` 区切り）。`slice:` がない場合は `steps:` 集計行の前に配置する。`slice:` がある場合は上記組み合わせルールのとおり `slice:` の後に配置する。`--skip` 指定がない場合は `skip:` 行を省略する（既存の挙動と同じ）。これで `--plan`（予定）と完了レポート（実績）の `skip:` フィールドが対称になり、機械パーサーが同一構造として処理できる。

```
## rig フロー完了

recipe: release-flow | autonomy: interactive | backend: manual
skip: design, review
steps: 5 完了 / 2 スキップ / 0 エスカレーション

| step    | outcome              | gate |
|---------|----------------------|------|
| intake  | ✓ done               | —    |
| design  | [SKIP] --skip flag   | —    |
| ...
```

### 実行テレメトリ（`.rig/runs.jsonl` への追記）

フロー完了レポートを出力した後、**同じサマリを1行 JSON として `<cwd>/.rig/runs.jsonl` に追記**する（orchestrate バックエンドは `scripts/orchestrate.py` の `telemetry_append` が自動追記するため、**manual / workflow バックエンドの RUN のみ**この規則で追記する）。回を重ねるごとに「どの recipe が何回・どれだけリトライして・どこでエスカレーションしたか」が集計可能になり、reviewer/gate の効き具合をデータで剪定できる。

```json
{"ts": "<ISO8601>", "recipe": "<name>", "backend": "manual", "final": "DONE|ESCALATE|STOPPED", "steps_total": N, "steps_passed": N, "retries": N, "escalated_at": "<step-id>|null", "steps": [{"id": "...", "status": "passed|skipped|escalated", "retries": N}]}
```

- **これは capture（§7）ではない**：run-state.json と同格の**実行ログ**であり knowledge 層への書き込みではないため、**承認不要**（§7.3 のゲート対象外・`--no-capture` の影響も受けない）。`.rig/` は gitignore 済み。
- フィールドは orchestrate の `telemetry_append` と同形（`backend` だけ `manual`/`workflow`）。review/acceptance ゲートを通った step は `steps[].verdicts[]` に検証者別の票（`{"by": "<reviewer名>", "ok": true|false}`）も記録する（分かる範囲で・省略可）。集計・一覧は **`orchestrate runs [--limit N] [--recipe R]`**、検証者別の票と**剪定ヒント**（5票以上で REJECT ゼロ＝ゴム印化の疑い）は **`runs --personas`**。
- 書き込みに失敗する環境（read-only 等）では**サイレントにスキップ**し、フロー完了レポート自体は通常どおり出す（telemetry は best-effort）。

## 7. 知識層への蓄積（capture）— RUN 後の学習サイクル

RUN が完了した後（またはユーザーが `--capture` フラグを明示した場合）、親は実行から得た**学び**を蒸留して既存のメモリ・知識層に書き戻す。これにより次回 RUN の知識注入（§5 COMPOSE の知識層注入）が充実し、システムが回を重ねるごとに賢くなる。

### 7.1 捕捉対象（WHAT）

以下を「学び」として蒸留する。

| カテゴリ | 例 |
|---|---|
| **落とし穴（pitfall）** | 同じエラーで2回詰まった原因、試みが失敗した理由 |
| **決定記録（decision）** | 設計・実装上の判断とその根拠 |
| **新規約（convention）** | RUN 中に確立した新しいコーディング規約・命名規則 |
| **「2回詰まり」の原因（stuck-twice）** | 詰まりガード（§6）が発動した際の根本原因 |
| **AI 失敗パターン（ai-quirk）** | hallucination、ツール誤用、出力フォーマット崩れ等の再現性のある失敗 |

### 7.2 書き込み先（WHERE）

捕捉した学びは**既存のメモリ・知識層に統合**する。並列に別ストアを作ってはならない。

| 学びの種類 | 書き込み先 | メモ |
|---|---|---|
| **ai-quirk** | `~/.claude/rig/knowledge/ai-quirks/`（user 層） | **記述形＋導出規範形のペアとして保存**（二相。§5 の ai-quirks 二相注入と対応）。記述ファイル（`<name>-descriptive.md`）と規範ファイル（`<name>-policy.md`）を1セットで作成 |
| **プロジェクト・ドメイン学び（pitfall / decision / convention / stuck-twice）** | `<repo>/.claude/rig/knowledge/accumulated/` **および/または** `~/.claude/projects/<proj>/memory/`（`type=project` または `type=knowledge`） | **書き分けルール**：クロスプロジェクトで再利用価値のある学び → memory store（`~/.claude/projects/<proj>/memory/`）に `[[クロスリンク]]` 付きで記録（必要なら ai-quirks にも）。プロジェクト固有のドメイン学び → `<repo>/.claude/rig/knowledge/accumulated/` のみ。**両方に該当する場合のみ両方へ書き込む**（既定は片方への書き込み）。 |
| **MEMORY.md インデックス** | `~/.claude/projects/<proj>/memory/MEMORY.md` | memory store に追記した各ファイルへの**1行ポインタ**を追加する（正準フォーマットは下記・#26） |

> **MEMORY.md 1行ポインタの正準フォーマット（#26）**：`- [<category>] <filename> — <1行サマリ> (<YYYY-MM-DD>)`
> - `<category>`：§7.1 の5値のうち memory store に書くもの（`pitfall` / `decision` / `convention` / `stuck-twice`）。`ai-quirk` は user 層へ書き memory store に記録しないのでポインタ対象外。
> - `<filename>`：memory store 内の相対パス。`<1行サマリ>`：蒸留した学びの1文（§7.4 提案の内容草案から抽出）。`<日付>`：書き込み日（ISO 8601）。
> - 例：`- [pitfall] pitfall-jwt-refresh.md — リフレッシュ後に旧トークンが1秒残る (2026-06-23)`
> - MEMORY.md が無ければ見出し（`## captured learnings`）を作って初期化、あれば末尾に追記。run をまたいで**同一フォーマット**で積む（書式が揺れるとインデックスとして読めなくなる）。

> **accumulated/ ファイルの正準フォーマット（#101）**：`<repo>/.claude/rig/knowledge/accumulated/` に書くファイルは YAML frontmatter + Markdown 本文で構成する。
> ```
> ---
> category: pitfall|decision|convention|stuck-twice
> title: <MEMORY.md ポインタの <1行サマリ> と同一の文字列>
> date: <YYYY-MM-DD>
> ---
> ## 何が起きたか
> （具体的な状況・エラー・決定の経緯）
>
> ## 次回への示唆
> （次回 RUN で同じ状況に陥らないための学び）
> ```
> - `category`：§7.1 の capture カテゴリ（`ai-quirk` は user 層 `ai-quirks/` に書くため対象外）
> - `title`：MEMORY.md ポインタの `<1行サマリ>` と同一文字列にする（インデックスとの一貫性を保つ）
> - `date`：書き込み日（ISO 8601）。MEMORY.md ポインタの `<YYYY-MM-DD>` と同一
> - 本文の「何が起きたか」「次回への示唆」の2セクションは必須。追加セクションは任意。
> - §5 COMPOSE 時に `accumulated/` の各ファイルは frontmatter を除いた Markdown 本文が Knowledge 位置に注入される。

> **役割の区別**（混同しないこと）:
> - **memory store**（`~/.claude/projects/<proj>/memory/`）= 横断的な個人・フィードバック・プロジェクト事実のレコード。永続的なプロジェクト記憶。
> - **knowledge layer**（`rig/knowledge/`）= 次回 RUN の subagent prompt に注入するドメイン記述知識。
> 両者は `[[ファイル名]]` 形式のクロスリンクで参照し合う。一方が他方の代替にはならない。

### 7.3 ゲート（承認必須・サイレント書き込み禁止）

**捕捉は自動的にはファイルを書き込まない。** 以下の手順を厳守する。

1. RUN 完了後、親は蒸留した学びを**提案としてユーザーへ提示**する（書き込み先・ファイル名・内容草案を含む）。
2. ユーザーが**承認する**か、または起動時に `--capture` フラグを明示した場合にのみ、ファイルに書き込む。
3. 承認なしには memory store にも knowledge layer にもいかなるファイルも作成・変更しない。

`--autonomous` が指定された場合でも capture のゲートは解除されない。capture だけは**常に承認が必要**（`--capture` フラグが明示された場合を除く）。

`--capture` 指定時も、書き込む内容と書き込み先（提案）を必ず表示してから書き込み、書き込み後に何を書いたかを必ず報告する。`--capture` は確認ダイアログ（y/n）を省略するだけで、提案表示と事後報告は省略しない。

**`--no-capture` フラグ / `no_capture: true` 設定時（#137）**：RUN 後の capture 提案を**完全にスキップ**する（提案表示・承認ダイアログともに出さない）。`--capture` と `--no-capture` を同時に指定した場合は `--no-capture` 優先とし `[WARN] --capture と --no-capture が同時指定されています（--no-capture 優先）` を出す。`no_capture: true` は recipe の静的設定（毎回抑止）、`--no-capture` はフラグによる実行時抑止と等価であり、どちらが有効でも同じ挙動になる。`hotfix`/`debug` など「学びより速度が優先される軽量 recipe」への利用を想定する。**capture の抑止は学習サイクルを止める**ため、抑止が常態化しないよう軽量 recipe 以外への `no_capture: true` 設定は推奨しない。

### 7.4 提案フォーマット（承認前に提示する内容）

提案は次の形式でユーザーに見せる。

**書き込み先ファイルの実在確認（#45）**：各書き込み先のファイルが既存か否かを実在確認し、結果を提案に反映する。既存の場合は `（既存・上書き <YYYY-MM-DD>）` を付し、既存ファイルの冒頭 1〜2 行（または `title:` frontmatter があればその値）を付記する。新規の場合は `（新規）` またはパスのみ（従来フォーマット互換）。`--capture` フラグ指定時（確認ダイアログ省略）も既存・上書きの旨と既存概要を表示してから書き込む（§7.3「提案表示は省略しない」と同じ考え方）。

```
## capture 提案（承認してください）

### [1] ai-quirk — <quirk の短い名前>
- 書き込み先: ~/.claude/rig/knowledge/ai-quirks/<name>-descriptive.md（既存・上書き 2026-06-20）
               既存の先頭: "# ai-quirk: <name>\n何が起きたか..."
               ~/.claude/rig/knowledge/ai-quirks/<name>-policy.md（新規）
- 内容草案: ...（記述形：何が起きたか / 規範形：次回 prompt に注入するルール）

### [2] pitfall — <落とし穴の短い名前>
- 書き込み先: <repo>/.claude/rig/knowledge/accumulated/<name>.md（新規）
               ~/.claude/projects/<proj>/memory/<name>.md（既存・上書き 2026-06-18）
               既存の先頭: "# pitfall: <name>\n前回の学び..."
               MEMORY.md に1行ポインタ追加
- 内容草案: ...

承認しますか？ [y / 個別に選ぶ / skip]
```

ユーザーが個別選択した場合、選ばれた項目だけを書き込む。

### 7.5 事後レポートフォーマット（書き込み後・#20）

> manifest `sage_notifications: true` の場合、レポートの先頭に `《告》学習「<最初のエントリ名>」ほか N 件を記録しました` を1行付す（演出のみ・フォーマット本文は不変）。

書き込み完了後（`--capture` 時も省略しない・§7.3）、何をどこに書いたかを正準フォーマットで報告する。

```
## capture 完了レポート

書き込み済: <N>件 / スキップ: <M>件

### [1] ai-quirk — <名前> ✓
- ~/.claude/rig/knowledge/ai-quirks/<name>-descriptive.md（新規作成）
- ~/.claude/rig/knowledge/ai-quirks/<name>-policy.md（新規作成）

### [2] pitfall — <名前> ✓
- <repo>/.claude/rig/knowledge/accumulated/<name>.md（新規作成）
- ~/.claude/projects/<proj>/memory/<name>.md（更新）
- MEMORY.md に1行ポインタ追加 ✓

### [3] decision — <名前> — スキップ（ユーザー指示）
```

- 先頭に `書き込み済: N件 / スキップ: M件` のサマリ行。
- 各書き込み項目は カテゴリ・名前・実ファイルパス（新規作成 or 更新）を列挙し末尾に `✓`。ai-quirk は記述形・規範形の2行。
- MEMORY.md ポインタは成否を明示（成功 `✓` / 失敗 `WARN: MEMORY.md 未更新`）。
- スキップ項目（「個別に選ぶ」で除外）は `— スキップ（ユーザー指示）` の1行のみ（草案は再掲しない）。
- 全件スキップなら `書き込み済: 0件 / スキップ: N件` ＋「capture は実施されませんでした」。

## 8. Native-first 非対称ルール

- **instruction facet は薄く、既存の skill / command / agent に委譲する。** エンジンは **routing ＋ gating** であり、機能の**再実装ではない**。
- **起動時に利用可能な skill / agent / command を確認**し、該当するものがあればそれを使う。無い場合に限り手動ステップへフォールバックする。
- この非対称（在庫があれば委譲、無ければ最小限の自前手順）が context とメンテコストを抑える。

## 9. アンチパターン

| アンチパターン | 正しい挙動 |
|---|---|
| 親が直接作業し context を浪費する | 実作業は subagent へ dispatch、親は集約のみ |
| 既存 skill/agent を再実装する | native を確認して委譲する（§8） |
| 軽い変更を過剰に重く回す | size-aware 既定（S/M は design/review/tdd OFF）に従う |
| `--only`/`--from` を無視して全部やる | 指定範囲だけ実行する |
| agent を使わず親が全部書く | parallel-fanout で subagent 群に dispatch |
| 同じ所で粘り続ける | 2回詰まったら user に判断を仰ぐ |
| 自由文で subagent に投げ集約困難にする | output-contract で structured-report を縛る |

いずれも **STOP して subagent 委譲（または該当ブリックの正規手順）へ**戻る。

## 9.1 rationalization 表（これを考えたら STOP）

プレッシャー下で rationalize（言い訳）しやすいパターンと現実を対比する。

| 言い訳 | 現実 | 正しい応答 |
|---|---|---|
| 「急いでるから review 飛ばしていい」 | pr-hygiene ルールは緊急を理由に解除されない。L超は分割必須、push 前レビューは常に必須。 | review を省かず、user に判断を委ねる |
| 「reviewer 立てると遅くなる」 | parallel-fanout で並列 dispatch すれば直列インラインより速い。親が直接やると context 汚染が残る。 | agent/subagent に dispatch、親は集約のみ |
| 「今回は小さいから自分でやる」 | サイズは context-minimal ルールの免除条件ではない。小さくても親が実装すると context は汚れる。 | 規模に関わらず implementer subagent に dispatch |
| 「ultracode 指定ないけど Workflow が便利」 | --workflow フラグまたは ultracode on が明示されない限り、Workflow バックエンドを起動してはならない。opt-in 必須。 | manual バックエンドで実行する |
| 「--autonomous だから capture も自動でいい」 | --autonomous は step ゲートを解除するだけ。capture ゲートは常に承認が必要（--capture フラグ明示の場合のみ確認ダイアログ省略）。 | capture 提案を表示し、承認を待つ |
| 「--autonomous だから acceptance-gate も飛ばせる」 | --autonomous は step ゲート（確認ダイアログ）を解除するだけ。acceptance-gate の品質収束ループと K 超エスカレーションは --autonomous でも動く（capture ゲートと同様）。 | acceptance-gate は外さず、K 回以内で受け入れ基準を満たすよう改善するか、エスカレーション後に user へ委ねる |
| 「1ファイルだけだから直接 review する方が早い」 | 親が直接 review しても結果は同じに見えるが、context を汚染し structured-report が欠けるため、gate 判断の一貫性が失われる。 | reviewer subagent に dispatch して structured-report を受け取る |
| 「さっき質問に答えたし、流れで自分で直していい」 | 質疑で recency が奪われた直後こそ red flag（直接実装・ゲート省略）へ逸れやすい。中断は規律解除の理由にならない。 | run-status ヘッダを再掲しハーネス状態を再宣言してから、現 step に委譲で戻る（§6 run-continuity） |

## 10. 参照表（どのブリックをいつ読むか）

| 局面 | 読むブリック |
|---|---|
| review step を合成する | `facets/instructions/parallel-review` |
| 並列 dispatch する | `patterns/parallel-fanout` |
| 並列結果を集約・着手判断 | `patterns/review-gate` |
| subagent 出力を縛る | `patterns/structured-report` ＋ `facets/output-contracts/review-verdict` |
| reviewer を起動する | agent: security-reviewer / design-reviewer / test-reviewer（無ければ facet: `facets/personas/{security,design,test}-reviewer` にフォールバック） |
| PR / push 時のガード | `facets/policies/pr-hygiene` |
| review だけ固定で回す | `recipes/review-only` |
| 品質を毎回一定にする（非決定→決定品質） | `patterns/acceptance-gate` |
| AI の癖排除・可読性を厳しく見る（敵対レビュー） | `facets/instructions/adversarial-review` ＋ `recipes/adversarial-review` |
| 親の越権（直接実装・無断 Workflow・サイレント書込）を止める | §6 red flags ＋ §9 アンチパターン表／§9.1 rationalization 表 |
| 中断・質疑の後も rig 駆動を切らさない（可視化・再アンカー） | §6 run-continuity（run-status ヘッダ／再アンカー規則／step 境界バナー） |
| `--list` を実行する（badge・`steps:`・tier グルーピングの表示仕様） | `facets/instructions/list` |
| `--plan` を実行する（ヘッダ・step テーブル・Gate/Knowledge 等の表示仕様） | `facets/instructions/plan` |
| `--validate` を実行する（検査項目・severity・エラーフォーマット） | `facets/instructions/validate` |
| `/rig:go "<task>"` 統一入口を駆動する（分類・recipe 自動選択・隔離 worktree RUN・gate 判定） | `facets/instructions/workbench` ＋ `patterns/isolated-worktree` |
| `/rig:go status`\|`diff`\|`accept`\|`discard`\|`log`\|`board`\|`stats`\|`review` を実行する | `facets/instructions/workbench-ops` ＋ `scripts/workbench.py` |
| 複数タスクを並行で進める（ターミナルを増やさず一括把握） | `/rig:queue add`→`go --provider rig`（`patterns/isolated-worktree` で自動隔離）＋ `/rig:go board`（単一ダッシュボード） |
| 視覚検証（スクリーンショット等）の置き場・処分ルールを確認する | `patterns/visual-artifacts` ＋ `scripts/workbench.py gc` |
| `/rig:go gh issue`\|`pr review`\|`pr fix`\|`ci` を実行する | `facets/instructions/gh-flow` |
| acceptance-gate の基準 ID・プリセット定義の正本を確認する | `scripts/workbench.py gates`（`standard`/`implementation`/`review`/`security`） |
