# rig — ブリック目録

**SKILL.md §2 の正本。** engine / dev-core 在庫と pack 追加分のブリックは、ここが全量を持つ。

SKILL.md は skill 起動のたびに本文まるごと読まれる。だから本体には節の1行要旨だけを残し、
目録そのものはこのファイルに置く。読むのは目録が要るときだけでよい。

`--validate` の §2 catalog drift・workbench catalog・packs catalog はこのファイルを読む。
**新しいブリックを足したら下の目録にも1行足すこと。**

## 2. ブリック目録

workbench task の作成・import は `--runtime auto|native|orca` を受け取る。auto は active
Orca context と応答する JSON CLI の両方があるときだけ Orca を選び、理由つきで native
fallback する。明示 Orca は fallback せず拒否する。runtime は provider と独立である。

| 種別 | 役割 | 現在の在庫 |
|---|---|---|
| **agent**（native 委譲先・優先） | read-only reviewer。専用 context・tool 制限つきで起動 | `agents/security-reviewer` `agents/design-reviewer` `agents/test-reviewer` `agents/behavioral-correctness-reviewer` `agents/performance-reviewer` `agents/observability-reviewer` `agents/api-compat-reviewer` `agents/migration-reviewer` `agents/docs-reviewer` `agents/finding-verifier` `agents/lazy-senior-reviewer` `agents/cognitive-economist-reviewer` |
| **persona facet**（agent フォールバック） | reviewer 人格。agent が無い時 subagent prompt の System に合成 | `facets/personas/security-reviewer` `facets/personas/design-reviewer` `facets/personas/test-reviewer` `facets/personas/behavioral-correctness-reviewer` `facets/personas/performance-reviewer` `facets/personas/observability-reviewer` `facets/personas/api-compat-reviewer` `facets/personas/migration-reviewer` `facets/personas/docs-reviewer` `facets/personas/finding-verifier` `facets/personas/orchestrator` `facets/personas/implementer` `facets/personas/debugger` `facets/personas/lazy-senior` `facets/personas/cognitive-economist` `facets/personas/cross-llm-reviewer` |
| **文体 persona facet**（styles シリーズ） | 書き手の語り口だけを担う shipped persona。事実・敬語・納品形式を持つ書き手 persona と同 step に置き、語り口側だけを差し替える | `facets/personas/styles/{qiita-tech-writer,dialogue-tech-explainer}` |
| **instruction facet**（薄い委譲） | 手順の routing。既存 skill/command/agent に委譲する thin な指示 | `facets/instructions/parallel-review` `facets/instructions/intake` `facets/instructions/design` `facets/instructions/implement` `facets/instructions/verify` `facets/instructions/visual-verify` `facets/instructions/pr` `facets/instructions/merge` `facets/instructions/adversarial-review` `facets/instructions/adaptive-assess` `facets/instructions/compose` |
| **output-contract facet** | subagent 出力の機械抽出可能フォーマット定義 | `facets/output-contracts/review-verdict`（着手判断の集約用・既定） `facets/output-contracts/review-findings`（severity・file:line・Blocking/Non-blocking を明示する詳細版。`/rig:drill` と厳しめレビュー依頼で使用） `facets/output-contracts/conformance-report`（ガバナンス適合性＝総合行〔force 率必須〕・層の到達・チーム別スコア表・乖離） |
| **policy facet** | 末尾注入のガードレール | `facets/policies/pr-hygiene` `facets/policies/pre-push-review` `facets/policies/ci-cost` `facets/policies/branch-strategy` `facets/policies/risk-based-testing` `facets/policies/cross-llm-legibility` `facets/policies/suppression-memory`（レビュー却下学習＝`.rig/review-suppressions.jsonl`。REFUTED/却下所見を記録し再指摘を抑止・UPHELD には負ける） `facets/policies/comment-policy`（`--comment` の投稿統制＝severity マッピング・nit 上限5・Pre-existing note・再レビュー収束） `facets/policies/org-policy`（組織ポリシーが効くリポジトリでのガードレール＝層の緩和・封印ロールへの自己登録・台帳編集・無記名 force の禁止。ポリシー未設定なら不活性） |
| **knowledge facet** | subagent prompt に注入する知識層ブリック | `facets/knowledge/orchestration-patterns` `facets/knowledge/harness-engineering` `facets/knowledge/quality-operating-system`（組織で品質を担保する観点＝個人ハーネスを組織に載せると壊れる4点と、一級概念化で直る6概念） `facets/knowledge/_layer` |
| **wiki**（shipped tier・persona が `inject:` で参照） | 観点カタログの正準ページ（`_wiki` スキーマ・`sources`/`reviewed_at` 必須） | `facets/knowledge/wiki/{loop-engineering,appsec-checklist,injection-patterns,migration-expand-contract,performance-pitfalls,observability-golden-signals,api-compat-semver,license-compat-basics}` |
| **pattern**（制御フロー） | step の実行制御テンプレ | `patterns/parallel-fanout` `patterns/review-gate` `patterns/structured-report` `patterns/serial` `patterns/autonomous-loop` `patterns/monitor` `patterns/workflow-backend` `patterns/acceptance-gate` `patterns/failure-taxonomy` |
| **recipe**（step の束） | step＋pattern＋facet を固定したテンプレ workflow | `recipes/review-only` `recipes/release-flow` `recipes/design-first` `recipes/hotfix` `recipes/debug` `recipes/fast-bugfix` `recipes/max-bugfix` `recipes/adaptive-bugfix` `recipes/adversarial-review`（dev-core。pack 追加分は下記） |
| **manifest** | プロジェクト設定・既定値テンプレ | `manifests/_template` |
| **step** | フローの単位。instruction facet として library 化済み | intake / design / implement / verify / visual-verify / pr / merge（parallel-review を含む全 8 件） |

> ブリック参照は skill ディレクトリ相対（facets/ patterns/ recipes/ manifests/）。agent はファイルパスを使わず subagent_type 名で起動。
>
> **上表は engine / dev-core 在庫。** ドメイン/モード pack は engine を改変せず**ブリックを上乗せ**する（§8 Native-first）。**新 pack を足したら下表の1行要旨と `PACKS.md` の詳細行の両方に追記する**（dev-core 行は安定させる）。`--validate` はこの目録と実ファイルの突き合わせを検査するので、下表からブリック名を落とすとドリフト検出が効かなくなる。
>
> **pack 追加分（engine 不変で上乗せ）** — 下表はブリック名と1行要旨のみ。**各 pack の詳細説明は `PACKS.md` が正本**（挙動そのものの正本は各 instruction / recipe）。
>
> | pack | 要旨 | 追加ブリック |
> |---|---|---|
> | **talk**（`/rig:talk`）| 会話モード（recipe なし＝既存コマンドへ委譲）。| `facets/personas/talk-assistant` `facets/instructions/talk-loop`|
> | **goal**（`/rig:goal`）| 高レベル目標→受け入れ基準→達成までループ（loop engineering）。| `facets/personas/goal-driver` `facets/instructions/goal-loop` `facets/knowledge/wiki/loop-engineering` `facets/policies/independent-verification` `recipes/goal-loop`|
> | **loop**（`/rig:loop`）| 繰り返し/監視ループ＝goal の対極。停止条件・安全上限必須。| `facets/instructions/loop-driver` `patterns/autonomous-loop` `recipes/loop`|
> | **task-plan**（`/rig:tasks`）| 依頼を検証可能な小タスクへ割ってから実装。| `facets/personas/planner` `facets/instructions/task-plan` `facets/output-contracts/task-plan` `recipes/task-plan`|
> | **brainstorm**（`/rig:brainstorm`）| 設計の壁打ち。`design-brief` に収束し tasks→dev へ繋ぐ。| `facets/personas/brainstormer` `facets/instructions/brainstorm` `facets/output-contracts/design-brief` `recipes/brainstorm`|
> | **pr-review**（`/rig:pr`）| PR レビュー（reviewer agent・persona・`review-verdict` は dev 共用）。| `facets/instructions/pr-review` `recipes/pr-review`|
> | **workbench**（`/rig:go`・品質保証つき統一入口）| 自然文→task_type 分類→recipe 自動選択→隔離 worktree RUN→gate 判定。対話 composition の5軸は `rig-wb wb compose-options` が候補・推薦・根拠を返す。**受け入れ基準 ID の正本は `scripts/workbench.py gates`**（project 独自基準は `.rig/gates.json` で**加算のみ**）。基準を裏付ける機械センサーは8本。OpenAPI schema-diff / secret scan / anti-tamper / injection-marker / destructive-command の5本は常に入る。残る3本は条件つき。prompt-regression は prompt 面に触れた diff でのみ自動で入る。evidence-anchor（`evidence_anchors_resolve`）は **opt-in** で、既定プリセットには入らない。ja-lint（`ja_lint_clean`）は日本語の散文を足した diff でのみ自動で入る。相方の `ja_prose_ai_smell_reviewed` は `ai-smell-reviewer` の verdict を写す。RUN の前段⓪でホスト側前提を1回だけ確認する（`rig-wb hostcheck`・**ブロックしない**）。| `patterns/isolated-worktree` `patterns/visual-artifacts` `patterns/computational-orchestration` `scripts/workbench.py` `rig_workbench/hostcheck.py` `facets/instructions/workbench` `facets/instructions/workbench-ops` `facets/instructions/gh-flow` `facets/instructions/acceptance-check` `facets/instructions/{identify-behavior-boundaries,compare-behavior,identify-audience,docs-draft,verify-commands,update-docs}` `recipes/{bugfix,feature,refactor,documentation}`|
> | **de-ai-smell**（`/rig:dev --recipe de-ai-smell`）| 散文の AI 臭除去（深層マーカー＋5観点スコア定量ゲート＋語彙ブラックリスト）。| `facets/personas/ai-smell-reviewer` `facets/instructions/de-ai-smell` `facets/knowledge/ai-writing-smells` `recipes/de-ai-smell`|
> | **drill**（`/rig:drill`・measurement）| reviewer 検出率の実測（合成 diff にバグの種を注入→6指標スコアボード）。`--replay` でペルソナの snapshot テスト。| `facets/personas/strict-senior-engineer` `facets/output-contracts/review-findings` `facets/instructions/drill` `recipes/drill`|
> | **design**（`/rig:design`）| UI/UX・a11y の作成＋URL 監査（`--ppt`/`--claudedesign`/Playwright は MCP 委譲）。| `facets/personas/design/{ui-ux-designer,ux-reviewer,a11y-reviewer}` `facets/instructions/{design-draft,design-vet,design-audit}` `facets/output-contracts/design-verdict` `facets/policies/design-constraint-rules` `facets/knowledge/{a11y-wcag,ui-ux-heuristics}` `recipes/{design,design-audit}`|
> | **layout-gate**（`/rig:dev --recipe layout-gate`）| 生成した資料のレイアウトを計算で測り、枠から文字が出たまま出荷させない。`measure` step が実行するのは project 所有の `./scripts/layout-gate.sh` のみ（センサー実体は `scripts/layout/`）。| `facets/personas/{layout-builder,layout-gate-reviewer}` `facets/instructions/{layout-build,layout-measure,layout-gate-review}` `facets/policies/layout-fit-rules` `facets/output-contracts/layout-gate-verdict` `facets/knowledge/wiki/layout-overflow-causes` `recipes/layout-gate` `commands/layout-gate` `scripts/layout/`|
> | **japanese-writing**（`/rig:dev --recipe japanese-writing`）| 明示された事実・宛先形式・敬語を守る日本語の完成稿を一つだけ出す。生成者と別の provider の reviewer が gate 内で判定する。`material_profile` は owner-bound な文体素材をちょうど一つ注入する例外 selector。| `facets/personas/{japanese-writer,japanese-writing-reviewer}` `facets/instructions/{japanese-write,japanese-writing-review,japanese-revise-draft}` `facets/policies/{writing-delivery-contract,japanese-writing-rules-v2,japanese-writing-modes,secure-provider-execution}` `facets/output-contracts/japanese-writing-verdict` `facets/knowledge/wiki/{japanese-ai-smell-jp,japanese-style-material-technical,japanese-style-material-conversation}` `recipes/{japanese-writing,japanese-writing-revision}` `commands/{japanese-writing,japanese-writing-revision}`|
> | **japanese-lint**（`/rig:japanese-lint`・`rig-wb ja-lint`）| 日本語文書を textlint-ja 相当のセンサーで機械的に検査する。センサーは stdlib のみ・形態素解析なし。直すのは error（一文の長さ・読点・二重否定・冗長表現・誤用・括弧・用語）だけ。別の reviewer が報告と差分を突き合わせる。品詞の近似に頼る規則（助詞の連続・ら抜き・い抜き・敬体常体）は warning で exit code を動かさない。本家 textlint-ja との一致は同じコーパスで所見単位に固定（`tests/test_ja_textlint_parity.py`）。`--fix` と `<!-- textlint-disable -->` あり。`ai-smell` preset は `knowledge/ai-writing-smells` の名指しブラックリストを辞書で読む助言専用規則で、error に昇格できない（§6-3）。**gate**：`/rig:go` の acceptance gate（`ja_lint_clean`・`ja_prose_ai_smell_reviewed`）に配線。acceptance gate は日本語散文を足した diff でのみ入る。githooks（`pre-commit --staged`・`commit-msg`）と `japanese-writing` の reviewer にも配線。残る `de-ai-smell`、`pr` step、`talk-assistant` にも配線する（policy「どこで gate になるか」）。検査対象は project 所有の `.claude/ja-textlint.json` の `paths`。gate が見ている追加行の所見は `rig-wb wb scan-ja-prose` が表示する。| `facets/personas/{japanese-lint-fixer,japanese-lint-reviewer}` `facets/instructions/{japanese-lint-fix,japanese-lint-review}` `facets/policies/japanese-textlint-rules` `facets/output-contracts/japanese-lint-verdict` `recipes/japanese-lint` `commands/japanese-lint` `manifests/ja-textlint.{schema,template}.json` `rig_workbench/ja_textlint.py`|
> | **test-design**（`/rig:qa`）| 固定7観点のテストケース設計（Test Basis 必須）。| `facets/personas/test-designer` `facets/knowledge/qa-test-lenses` `facets/instructions/test-design` `facets/output-contracts/test-cases` `recipes/test-design`|
> | **harness-audit**（`/rig:harness`）| ハーネスの棚卸し＝空象限と効いていない資産の検出（read-only）。| `facets/personas/harness-auditor` `facets/knowledge/harness-taxonomy` `facets/instructions/harness-audit` `facets/output-contracts/harness-map` `recipes/harness-audit`|
> | **govern**（`/rig:govern`・org 層／v2.0.0）| 組織ガバナンスを一級概念化＝共通ポリシー（org→team→project の**単調強化**）・権限管理。さらに承認フロー・例外（waiver）・改竄検知つき監査台帳・適合性の実測。**判定と記録の正本は `rig-wb govern`**。`.rig/org.json` が無ければ**完全に不活性**（個人開発は v1 と同一）。**v2.1**＝step の `actor` / `human_gate` と policy の `stage:<step-id>` を使う。**任意のステージを人間承認で止められる**（§3.5・`orchestrate approve`）。| `facets/personas/governance-auditor` `facets/knowledge/quality-operating-system` `facets/instructions/govern` `facets/output-contracts/conformance-report` `facets/policies/org-policy` `recipes/govern-audit` `commands/govern`|
> | **security**（`/rig:sec`・ホワイトハッカー pack）| audit（攻撃者視点の探索・read-only）。pentest-fix（PoC 回帰テスト化→canonical 修正）／monitor（定期再スキャン）。**倫理境界＝自プロダクト/許可済み環境・静的+ローカル検証のみ**。決定論センサーは `scripts/sast_adapter.py` ＋ `workbench.py scan-secrets`。| `facets/personas/security/{exploit-researcher,threat-modeler,remediation-engineer}` `facets/knowledge/wiki/attack-catalog` `facets/instructions/{security-audit,pentest-fix,security-monitor}` `facets/output-contracts/security-findings` `recipes/{security-audit,pentest-fix,security-monitor}` `patterns/autonomous-loop` `scripts/sast_adapter.py`|
> | **orchestrate**（`/rig:orchestrate`・`--orchestrate`）| 計算的オーケストレーション＝遷移・ゲート・リトライ・停止・状態保持をコードが強制。opt-in で engine 不変。| `scripts/orchestrate.py` `patterns/computational-orchestration`|
> | **queue**（`/rig:queue`）| 積んで GO。並列実行＋独立検証ゲート・`--provider rig` は各 item を隔離 worktree で dispatch。**verifier は accept しない**（`/rig:go board`→個別 accept）。| `scripts/orchestrate.py` `patterns/isolated-worktree` `commands/queue`|
> | **init**（`/rig:init`・utility）| manifest・知識層 dir・CLAUDE.md "Compact Instructions" を scaffold。| `facets/instructions/init`|
> | **persona-gen**（`/rig:persona`・generator）| 説明文→persona facet 生成（**既定 project**・`--user` で global）。| `facets/instructions/persona-gen`|
> | **knowledge-gen**（`/rig:knowledge`・generator）| 説明文/`--auto`/`--research`→wiki ページ生成。**既定 global**・`--project` で overlay＝persona-gen とは既定 tier が逆。`--graph` で codebase-graph へ蒸留。| `facets/instructions/knowledge-gen` `facets/knowledge/_wiki`|
> | **skill-author**（`/rig:forge`・generator）| 説明文→rig のブリック/パックを自作・検証・保存する自己拡張。| `facets/instructions/skill-author`|
> | **skill-import**（`/rig:import`・generator）| 外部 skill を発見→取得→検疫→判断→import-gate→lock 記録で取り込む。| `facets/instructions/skill-import`|
> | **skill-export**（`/rig:export`・generator）| 育てたブリックを self-contained な Claude Code skill として書き出す還元機構。| `facets/instructions/skill-export`|
> | **validate**（`--validate`・utility）| **ブリック整合チェック（doctor）の正本**（検査項目・severity・エラーフォーマット）。| `facets/instructions/validate`|
> | **run-report**（§6・utility）| **フロー完了レポート／`.rig/runs.jsonl` テレメトリの出力仕様の正本**。| `facets/instructions/run-report`|
> | **resolve**（§4・utility）| **RESOLVE 詳細規則の正本**（manifest キー・tier 検索・extends・flag⇔キー等価・スライス・save-recipe）。| `facets/instructions/resolve`|
> | **list**（`--list`・utility）| **`--list` 表示仕様の正本**（tier/pack グルーピング・badge 導出・`steps:`）。| `facets/instructions/list`|
> | **plan**（`--plan`・utility）| **`--plan` 表示仕様の正本**（ヘッダ・step テーブル・Gate/Checks/DAG/Knowledge ブロック）。| `facets/instructions/plan`|
> | **catalog**（`/rig:catalog`・`--list --global`・utility）| 全 tier 走査の横断レジストリ地図。`--graph` で固定11種の関係を導出。| `facets/instructions/catalog`|
> | **evidence**（`rig-evidence`・utility）| 記録／集計するものは次のとおり。実プロジェクトでの RIG-vs-bare フィールド証跡・本番アウトカム網羅率・Quality/Cost フロンティア・複数リポジトリ横断の governance ロールアップ。証跡は `.rig/field-study.jsonl`、ロールアップは `.rig/fleet.json`。**出力仕様の正本は `docs/evidence-mission-control.md`**。| `rig_workbench/evidence.py`|
> | **mission-control**（`rig-mission-control`・utility）| 上記に drill 実測の reviewer 信頼度・force-bypass 件数を重ねる。一枚の **read-only** HTML/JSON ダッシュボード（`rig.mission-control/v1`）として可視化する。accept/discard/approve 等の変更操作は一切持たない。| `rig_workbench/mission_control.py`|
> | **assurance**（`rig-wb wb {receipt,import,contract}`・utility）| 変更が accept 可能だった理由の携帯可能な射影。射影の形式は `rig.assurance-receipt/v1`。rig が作っていない変更を通常タスクとして登録する BYOO（`rig.assurance-contract/v1`）もある。値は `acceptable`／`not-acceptable`／`pending`／`execution-error` の4つ。それぞれに exit code が1つずつ。**判定はせず、判定した記録から写す**——producer が宣言したことと rig が検証したことは混ざらない。Mission Control の task detail が描く run の構造は `rig.assurance-graph/v1`。**正本は README.md の機能表**と `docs/byo-orchestrator.md`。機能表の項目は "The shape a run actually took" と "Why a given change was acceptable"。それに "Whether a change rig did not produce clears its boundary"。| `rig_workbench/assurance/{assurance,contract,import_task}.py`・`rig_workbench/workbench/graph.py`|
> | **intent / assurance target**（`rig-wb wb {intent,intent-derive,assurance-target,assurance-derive}`・utility）| intent contract の**宣言された**要件から workflow の床と assurance target を導き、target を受領書と突き合わせる。推論された要件は床を作らず、`unobservable` は `unmet` に畳まない（「測っていない」と「測って不足」は別）。軸→step の写像は呼び出し側が宣言し、覆わない軸-値は拒否。| `rig_workbench/assurance/{intent,intent_wiring,assurance_target,assurance_wiring}.py`|
> | **assurance planning**（`rig-wb wb {synthesise,dev-loop,route-team,budget-plan,provenance}`・utility）| 提案された workflow への床の復元／開発ループの停止判定と handoff／証拠からの担当決定／保証の作り方だけを安くする予算計画／ある節点の鎖の双方向追跡。いずれも**床・制約は呼び出し側が組み、検査対象からは読まない**。進捗の無さを進捗と読まず、予算が尽きたら拒否して選択肢を提示せず、確認済みと推測を混ぜない。| `rig_workbench/assurance/{synthesis,development_loop,team_routing,assurance_budget,provenance_graph}.py`|
> | **expected outcome**（`rig-wb wb expected-outcome`・utility）| 宣言された期待 outcome を、外部から渡された観測値と突き合わせる。宣言には単位・観測窓・宣言時刻も含む。objective は baseline/target＋改善方向 `direction`。guardrail は守る側を名前にした境界 `at_most`（上限）/`at_least`（下限）を1つだけ持つ。guardrail は `direction` を持たない。観測側は自分の基準を宣言できない（`target`/`baseline`/`at_most`/`at_least`/`status` は名指しで拒否）。`unmeasured`/`inconclusive` は成功に畳まれず、窓が閉じるまで `final` にならない。assurance PASS と `record-outcome` の ok/incident は**写すだけで混ぜない**別 status のまま並ぶ。| `rig_workbench/assurance/production_outcome.py`|
> | **workflow effectiveness**（`rig-wb wb effectiveness`・utility）| 記録が実際に持つ値だけを集計する。読むのは `.rig/runs.jsonl` と `.rig/runs/*/{task,acceptance}.json`。集計するのは repair 回数・停止 step・task type・gate 時刻。呼び出し側が定義した反復数／repair 上限／late step に一致する pattern を列挙する。finding yield・cost・runtime・production rework など記録が持たない値は `unobservable`。候補生成・offline evaluation・promotion はしない。| `rig_workbench/assurance/workflow_effectiveness.py`|
> | **knowledge candidate**（`rig-wb wb knowledge-candidate`・utility）| 呼び出し側が提出した知見候補を判定する。判定するのは、引用記録が実在し、rule / expected benefit / context / scope を明示的に支えるかだけ。読めない記録は `unobservable`、読めて不支持は `unsupported`。候補の `confidence` は自己申告のまま分離する。| `rig_workbench/assurance/knowledge_candidate.py`|
> | **change graph**（`rig-wb wb change-graph`・utility）| 判定の対象は呼び出し側が書いた cross-repo change graph。宣言された依存と互換制約を満たす execution stage が存在し、その中に却下された node が無いかを判定する。解決不能 endpoint、未宣言要件、循環、`rejected` な node は拒否し、`unobservable` と `unmet` を依存と node の別に分ける。graph の発見・生成・node 実行・統合検証・feature assurance はしない。| `rig_workbench/assurance/change_graph.py`|
> | **production anomaly trigger**（`rig-wb wb anomaly-trigger`・utility）| 外部 source が event を提出する。判定するのは、その調査開始材料と引用 support だけ。読めた不一致は `unmet`、読めない／不正／解決不能は `unobservable`。anomaly の検出・真偽判定、相関、再現、修正候補生成は行わない。| `rig_workbench/assurance/anomaly_trigger.py`|
> | **hooks**（プラグイン同梱）| `PreCompact` で run-state を保全（§6 run-continuity ④）。| `hooks/hooks.json` `hooks/preserve-rig-state.sh`|

### Extension Catalog（opt-in）

domain extension は core 目録や既定の asset 解決へ混ぜない。必要な project で明示的に導入する。

| id | 説明 | install command |
|---|---|---|
| `sales` | 商談レビューと営業資料・荷電スクリプト生成 | `rig-wb pack install domain:sales --scope project` |
| `video-storytelling` | 根拠に接続した動画脚本・検閲・絵コンテ生成 | `rig-wb pack install domain:video-storytelling --scope project` |
| `decision-humor` | 合議・安全な即断・問い・事前検死・根拠付き回答の手動モード集 | `rig-wb pack install domain:decision-humor --scope project` |
| `document-review` | 資料の構成と根拠を独立検証する read-only reviewer 2枚（宛先適合は未測定のため同梱しない） | `rig-wb pack install domain:document-review --scope project` |

project pack は実行前に内容を確認し、初回は `RIG_ALLOW_PROJECT_PACKS=1` を設定して
asset trust を記録する。その後 `$rig --recipe <installed-name>` で起動する。pack の
command asset は install だけでホストの slash command に自動登録されない。


## 目録の外 — 次に読むもの

| 知りたいこと | 読む先 |
|---|---|
| 起動文字列の解釈（flag 一覧） | `SKILL.md` §3 |
| recipe frontmatter キーの正規定義 | `RECIPE-SCHEMA.md` |
| 解決順（manifest＋recipe＋flag＋size-aware 既定） | `RESOLVE.md` |
| ハーネス合成（facet 配置順・知識層の注入・persona tier） | `COMPOSE.md` |
| 各 pack が何をするかの詳細説明 | `PACKS.md` |
