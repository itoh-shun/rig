---
name: rig
description: Use when you need dev-flow orchestration — implementing a feature, clearing an issue, reviewing current changes, completing a PR, going design-to-implementation, TDD, or composing a flow. 開発フローのオーケストレーション（実装着手 / Issue 対応 / 変更レビュー / PR 完了 / 設計→実装 / TDD / フロー組み立て）が要るとき、または `/rig:dev` が呼ばれたとき。
user-invocable: true
---

# rig

## 1. Overview

ブリック（facet / pattern / step / agent / recipe）を**起動時に組み合わせて**タスク専用のエージェント・ハーネスを engineering する、レゴ式ハーネス・コンポーザ。固定ワークフローではなく **PARSE → RESOLVE → COMPOSE → RUN** の4段で都度ハーネスを合成する。intake→design→implement→verify→review→pr→merge の「3-Stage フルフロー」は数ある recipe の1つにすぎない。

**determinism-by-gate**: 非決定的な agent 実行を決定的な受け入れゲート（`patterns/acceptance-gate`）で挟み、経路は変動しても**毎回同じ品質**へ収束させる。これが rig の品質保証の核。

## 2. ブリック目録

| 種別 | 役割 | 現在の在庫 |
|---|---|---|
| **agent**（native 委譲先・優先） | read-only reviewer。専用 context・tool 制限つきで起動 | `agents/security-reviewer` `agents/design-reviewer` `agents/test-reviewer` `agents/lazy-senior-reviewer` `agents/cognitive-economist-reviewer` |
| **persona facet**（agent フォールバック） | reviewer 人格。agent が無い時 subagent prompt の System に合成 | `facets/personas/security-reviewer` `facets/personas/design-reviewer` `facets/personas/test-reviewer` `facets/personas/orchestrator` `facets/personas/implementer` `facets/personas/debugger` `facets/personas/lazy-senior` `facets/personas/cognitive-economist` `facets/personas/cross-llm-reviewer` |
| **instruction facet**（薄い委譲） | 手順の routing。既存 skill/command/agent に委譲する thin な指示 | `facets/instructions/parallel-review` `facets/instructions/intake` `facets/instructions/design` `facets/instructions/implement` `facets/instructions/verify` `facets/instructions/visual-verify` `facets/instructions/pr` `facets/instructions/merge` `facets/instructions/adversarial-review` |
| **output-contract facet** | subagent 出力の機械抽出可能フォーマット定義 | `facets/output-contracts/review-verdict` |
| **policy facet** | 末尾注入のガードレール | `facets/policies/pr-hygiene` `facets/policies/pre-push-review` `facets/policies/ci-cost` `facets/policies/branch-strategy` `facets/policies/risk-based-testing` `facets/policies/cross-llm-legibility` |
| **knowledge facet** | subagent prompt に注入する知識層ブリック | `facets/knowledge/orchestration-patterns` `facets/knowledge/harness-engineering` `facets/knowledge/_layer` |
| **pattern**（制御フロー） | step の実行制御テンプレ | `patterns/parallel-fanout` `patterns/review-gate` `patterns/structured-report` `patterns/serial` `patterns/autonomous-loop` `patterns/monitor` `patterns/workflow-backend` `patterns/acceptance-gate` |
| **recipe**（step の束） | step＋pattern＋facet を固定したテンプレ workflow | `recipes/review-only` `recipes/release-flow` `recipes/design-first` `recipes/hotfix` `recipes/debug` `recipes/adversarial-review`（dev-core 6 件。pack 追加分は下記） |
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
> | **sales**（`/rig:sales`） | **商談レビュー**: persona `facets/personas/sales/{hearing,needs,proposal,closing,next-action}-reviewer` ／ instruction `facets/instructions/deal-review` ／ output-contract `facets/output-contracts/deal-verdict` ／ recipe `recipes/deal-review` ／ knowledge `facets/knowledge/sales-domain/`。**資材生成（`--material`/`--script`）**: persona `facets/personas/sales/{material-writer,cold-caller}` ／ instruction `facets/instructions/{sales-material,call-script}` ／ output-contract `facets/output-contracts/sales-collateral` ／ recipe `recipes/sales-enablement`（開発資材→営業1枚資料＋荷電スクリプト。機能→ベネフィット翻訳・実在機能のみ・誇張禁止） |
> | **talk**（`/rig:talk`） | persona `facets/personas/talk-assistant` ／ instruction `facets/instructions/talk-loop`（recipe なし＝既存コマンドへ委譲） |
> | **goal**（`/rig:goal`） | persona `facets/personas/goal-driver` ／ instruction `facets/instructions/goal-loop` ／ knowledge `facets/knowledge/loop-engineering`（ループ1周回＝discovery/handoff/verification/persistence/scheduling・自己採点バイアス） ／ policy `facets/policies/independent-verification`（**採点者は生成者と別人**＝自己合格を禁止し self-grading バイアスで誤収束を防ぐ） ／ recipe `recipes/goal-loop`（loop engineering＝harness の1つ上の層。verification は独立検証） |
> | **loop**（`/rig:loop`） | instruction `facets/instructions/loop-driver` ／ pattern `patterns/autonomous-loop`（`ScheduleWakeup` 再利用） ／ recipe `recipes/loop`（**繰り返し/監視ループ**＝goal の対極。「いつまた回すか」を担う watch/poll/repeat。停止条件 `--until`/`--times`/明示・安全上限必須・各 tick 報告・時間駆動は 270/1200・300 禁忌。`--every` で `/rig:goal` を定期キックする等、外側スケジューラとして重ねられる） |
> | **pr-review**（`/rig:pr`） | instruction `facets/instructions/pr-review` ／ recipe `recipes/pr-review`（reviewer agent・persona・`review-verdict` は dev 共用） |
> | **de-ai-smell**（`/rig:dev --recipe de-ai-smell`） | persona `facets/personas/ai-smell-reviewer` ／ instruction `facets/instructions/de-ai-smell` ／ knowledge `facets/knowledge/ai-writing-smells` ／ recipe `recipes/de-ai-smell`（散文の AI 臭除去。深層 A〜V マーカー＋**5観点スコア定量ゲート**（立場/リズム/主体性/具体性/削減・<35/50で書き直し）＋**名指し語彙ブラックリスト**（偏愛語/横文字メタファー/ジャーゴンを置換例つきで弾く）。`review-verdict` は dev 共用） |
> | **sns-x**（`/rig:dev --recipe sns-x-post`） | persona `facets/personas/sns-post-reviewer` ／ instruction `facets/instructions/sns-post` ／ knowledge `facets/knowledge/sns-x-conventions` ／ recipe `recipes/sns-x-post`（X 半自動ポスト運用。声 persona は運用者が `/rig:persona`＋`default_personas` で投入。de-ai-smell・`review-verdict` 共用） |
> | **magi**（`/rig:magi`） | persona `facets/personas/magi/{melchior,balthasar,casper}` ／ instruction `facets/instructions/magi-deliberation` ／ pattern `patterns/magi-consensus`（多数決合議ゲート） ／ output-contract `facets/output-contracts/magi-verdict` ／ recipe `recipes/magi`（エヴァ MAGI 模倣の3賢者 decision モード。正しさ/守り/価値の直交3観点で go/no-go を多数決裁定） |
> | **roast**（`/rig:roast`・humor） | persona `facets/personas/roast-reviewer` ／ instruction `facets/instructions/roast` ／ recipe `recipes/roast`（毒舌ロースト・レビュー。`review-verdict`/`review-gate` は dev 共用。中身は本物のレビューで配送をユーモアに振る adversarial-review 変種） |
> | **coin**（`/rig:coin`・humor） | persona `facets/personas/coin-flipper` ／ instruction `facets/instructions/coin-flip` ／ recipe `recipes/coin`（可逆で些末な決定を即断する反-bikeshed ゲート。重い/不可逆はトリアージで弾いて magi へ。magi の対極） |
> | **duck**（`/rig:duck`・humor） | persona `facets/personas/rubber-duck` ／ instruction `facets/instructions/duck-debug` ／ recipe `recipes/duck`（ラバーダック・デバッグ。アヒルが質問だけで本人に気づかせる会話モード。コードも答えも出さない・実証済み技法） |
> | **pre-mortem**（`/rig:pre-mortem`・humor） | persona `facets/personas/pre-mortem-analyst` ／ instruction `facets/instructions/pre-mortem` ／ output-contract `facets/output-contracts/premortem-report` ／ recipe `recipes/pre-mortem`（事前検死。「もう本番で壊れた」前提で失敗モードを逆算＋最小ガードレール。magi の補完＝どう壊れるか） |
> | **release-movie**（`/rig:movie`） | persona `facets/personas/release-director` ／ instruction `facets/instructions/release-movie`＋`facets/instructions/hyperframes-video`（`--hyperframes` の MP4 経路） ／ recipe `recipes/release-movie` ／ アニメ HTML `web/release-trailer.html`＋HyperFrames 例 `video/launch-film/`・`video/before-after/`（**既定＝実装中のプロジェクト**（コード/README/実際に動く画面/開発フロー）からデモ動画。`--release` 時のみ CHANGELOG→リリーストレーラー。制作台本＋再生 HTML の2点、`--hyperframes` で MP4 出力可能な HyperFrames コンポジション。**動いている画面ショット必須**・各ビート実コード/実機能紐づけ・harness では実動画/MP4 を非生成＝コンポジションまで生成しユーザーが render） |
> | **scenario**（`/rig:scenario`） | persona `facets/personas/scenario-writer`＋`facets/personas/engagement-reviewer`（面白さ軸）＋**作家性レンズ `facets/personas/auteur/{deconstructionist,humanist}`**（任意・`--persona` 投入。実名を避けた作家アーキタイプ：解体派＝本音/緊張/間/形式破壊、人間派＝温かさ/誠実/日常の発見） ／ instruction `facets/instructions/scenario-write`（執筆）＋`facets/instructions/scenario-vet`（検閲） ／ recipe `recipes/scenario`（短尺動画のシナリオライターモード：脚本→検閲。検閲の土台は既存の掛け合わせ＝`ai-smell-reviewer`＋knowledge `ai-writing-smells` × `sns-post-reviewer`、＋面白さ軸 `engagement-reviewer`・`review-verdict` 共用。`/rig:movie` の前段） |
> | **design**（`/rig:design`） | persona `facets/personas/design/{ui-ux-designer,ux-reviewer,a11y-reviewer}` ／ instruction `facets/instructions/{design-draft,design-vet,design-audit}` ／ output-contract `facets/output-contracts/design-verdict` ／ knowledge `facets/knowledge/{a11y-wcag,ui-ux-heuristics}` ／ recipe `recipes/{design,design-audit}`（UI/UX・a11y デザイン作成＋URL 監査。draft→vet / capture→audit を parallel-fanout＋acceptance-gate で収束。`--ppt`=powerpoint-server MCP・`--claudedesign`=claude_design MCP・URL 監査=playwright MCP に委譲。engine 不変） |
> | **test-design**（`/rig:qa`） | persona `facets/personas/test-designer` ／ knowledge `facets/knowledge/qa-test-lenses`（7観点＋ISO/IEC 25010＋トラック分化＋正直さ） ／ instruction `facets/instructions/test-design` ／ output-contract `facets/output-contracts/test-cases` ／ recipe `recipes/test-design`（テストケース設計。固定7観点（初見/ベテラン/悪意/整合性/移行/回帰/仕様疑義）を取りこぼさず各 ≥1・**根拠(Test Basis)必須**・**未確認は「※要確認」**・要件カバレッジ可/保留/不可で仕様ギャップ可視化・`--migration` トラック分化・`--review` は指摘のみ。AI はテスト設計者であってテスター非該当＝実行/合否/修正は人間） |
> | **harness-audit**（`/rig:harness`） | persona `facets/personas/harness-auditor` ／ knowledge `facets/knowledge/harness-taxonomy`（2×2＝計算的/推論的 × ガイド/センサー） ／ instruction `facets/instructions/harness-audit` ／ output-contract `facets/output-contracts/harness-map` ／ recipe `recipes/harness-audit`（プロジェクトの「エージェント開発ハーネス」を 2×2 で棚卸しし、**空象限**と**あるのに効いていない資産**（lint/test がループ外・ルールが prose 止まり）を炙り出す自己監査。**計算的センサーを一次**・「ある」と「効いている」を区別・**足すより繋ぐ/強制する/薄くする**。read-only） |
> | **orchestrate**（`/rig:orchestrate`・`--orchestrate`） | runner `scripts/orchestrate.py` ／ pattern `patterns/computational-orchestration`（**計算的オーケストレーション**＝制御ループの遷移・ゲート・リトライ・停止・状態保持をコードで強制。半自動 `plan/init/next/check/verdict`／全自動 `run`＝各 step を**別プロセスの rig ハーネス**で実行（`--provider rig`／claude/codex/cmd/mock）。並列検証 `--max-parallel`・`--quorum all|majority`・別プロセスで採点者≠生成者・`run-state.json` 永続・K回未達 ESCALATE・自己採点 BLOCKED。`selftest` で決定論検証。opt-in＝engine 不変） |
> | **init**（`/rig:init`・utility） | instruction `facets/instructions/init`（manifest・知識層 dir・CLAUDE.md "Compact Instructions" を scaffold） |
> | **persona-gen**（`/rig:persona`・generator） | instruction `facets/instructions/persona-gen`（説明文→persona facet を project/user 層に生成。`--persona <name>` で都度投入、manifest `default_personas` で製品ごと常時自動投入。v2 Phase 1） |
> | **knowledge-gen**（`/rig:knowledge`・generator） | instruction `facets/instructions/knowledge-gen` ／ knowledge `facets/knowledge/_wiki`（説明文/`--auto` repo 解析→wiki ページを global/project に生成。persona は `inject: [[slug]]` で参照。v2 Phase 2） |
> | **skill-author**（`/rig:skill`・generator） | instruction `facets/instructions/skill-author`（説明文→rig のブリック/パック〔recipe・instruction・output-contract・command〕を自作して検証・保存する**自己拡張メタ能力**＝writing-skills 相当。persona は `/rig:persona`・knowledge は `/rig:knowledge` へ委譲。**engine 不変・pack 上乗せ**（既存 pattern と facet 型を組むだけ）・pack の定石（persona=判断/knowledge=カタログ/instruction=routing/recipe=step/output-contract=形式/command=入口）・生成後に `--validate` で参照切れ検証・書込確認必須・project/user/shipped tier） |
> | **catalog**（`/rig:catalog`・`--list --global`・utility） | instruction `facets/instructions/catalog`（全 tier 走査→domain×pack×persona×wiki×recipe の横断レジストリ地図。派生・読み取り専用。v2 Phase 3） |
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
| （横断 CLI） | `scripts/orchestrate.py install-shim` で `~/.local/bin/rig` を 1 回張れば、任意 cwd から `rig <subcommand>` で起動できる。`$RIG_HOME` 上書き可、`<cwd>/.rig/recipes/<name>.md` が同名 built-in を**プロジェクト overlay**として上書き解決、`checks:` の実行 cwd は呼び出し元（rig リポジトリではない） |
| `--capture` | capture（学びの知識層への蓄積）を承認ダイアログなしで実行（提案表示と事後報告は省略しない）。既定は capture 提案時に承認を求める |
| `--no-capture` | RUN 後の capture 提案を完全にスキップ（提案表示・承認ダイアログともに出さない）。`--capture` と同時指定時は `--no-capture` 優先＋WARN（§7.3） |
| `--skip <step>` | 指定した step を除外してフローを継続する（複数可。例 `--skip design --skip review`）。size-aware 既定・`--design`/`--review` 等フラグより後に適用される（明示スキップが最終的に勝つ）。`--only` との同時指定は `--only` 優先・警告を出す。`--save-recipe` には影響しない（実行時フィルタ＝§4.3.2 snapshot 意味論と同じ） |
| `--list` | 利用可能なブリック(§2)・**全 tier の recipe**（project / user / shipped）・flag を一覧表示して停止（RESOLVE/COMPOSE/RUN しない） |
| `--validate` | ブリック整合チェック（doctor）。recipe→facet 参照切れ・frontmatter スキーマ逸脱・§2 目録と実ファイルのドリフトを検査し、レポートして停止（RESOLVE/COMPOSE/RUN しない）。手順は `facets/instructions/validate` |
| `--adversarial` | 敵対的レビュー step（lazy-senior / cognitive-economist で AI の癖排除・人間可読性・不要コメント除去）を合成に追加 |
| `--persona <name>` | review fan-out に名前指定のカスタム reviewer persona を**この run だけ追加**（複数可）。tier 解決（project→user→shipped・§5）で名前解決。manifest `default_personas`（製品ごとに常時自動投入）に**上乗せ**される。`/rig:persona` で生成した persona をそのまま投入できる |
| `--no-default-personas` | この run に限り manifest `default_personas` の自動投入を**抑止**する（組み込み reviewer＋`--persona` 指定分のみで回す） |
| `--cross-llm` | **他社 LLM レビュー前提モード**。implement step に `cross-llm-legibility` ポリシーを注入し（Codex/Copilot/GPT が読んでも一発で通る＝慣用的・明示的・文脈非依存なコードを書く規律）、review fan-out に `cross-llm-reviewer` persona を追加する（外部 LLM になりきって「内輪にしか分からない」箇所を指摘）。書く側・見る側の両方に作用する |
| `--global` | `--list` / `--validate` のスコープを **tier 横断**（shipped＋user(global)＋project）に広げる。`--list --global` は横断レジストリ地図（`/rig:catalog` 相当）、`--validate --global` は tier 横断の衛生点検。手順は `facets/instructions/catalog` |
| `--ppt` | （design pack）作成したデザインドキュメントを PowerPoint としても出力（`powerpoint-server` MCP）。既定 Markdown に追加・併用可 |
| `--claudedesign` | （design pack）claude.ai デザイン機能（`claude_design` MCP）でも生成。既定 Markdown に追加・併用可。MCP 未接続時は報告して Markdown のみ続行 |
| `--url <url>` | （design pack）監査モードを明示。実装画面を Playwright で取得し UI/UX・a11y を採点（bare な URL 引数でも自動検出） |
| `--a11y-level <A\|AA\|AAA>` | （design pack）目標 WCAG レベル（既定 AA）。未達違反は検閲で重大度を上げる |

**`--list` 指定時** → §2 のブリック目録・flag 一覧に加え、**recipe を全 tier 走査（§4.2.1 と同じ project → user → shipped 順）して tier 別にグルーピング表示**し、**停止**（解決も実行もしない）。**shipped tier の recipe は §2 pack 定義に従い `#### <pack>` サブ見出しでグループ化する（#99）**：`dev（core）`（review-only / release-flow / design-first / hotfix / debug / adversarial-review）、`goal`、`pr-review`、`de-ai-smell`、`sns-x`、`magi`、`humor`（roast / coin / duck / pre-mortem）、`sales`（deal-review / sales-enablement）、`release-movie`、`scenario`、`design`（design / design-audit）。project / user tier の recipe はパック分類なしでフラット表示する（tier だけで十分に絞り込めるため）。各 recipe は frontmatter の `name` / `description` を出す。**`extends` を持つ recipe は `extends: <親名> [tier]` を併記する**（`extends` 無しは省略。親が解決できない場合は `[WARN: 親未解決]` を付す）（#53）。manifest に `default_recipe` が設定されているとき、一致する recipe エントリに **`★ default` を付記する**（manifest なし・`default_recipe: "interactive"` の場合はマーカーなし。`default_recipe` が未解決なら `★ default (WARN: 未解決)` を付す。`--list --global` 実行時も同様）（#55）。project / user の recipe は同名 shipped を shadow する旨を明示する（`--save-recipe` で保存したものがここで発見できる＝保存→一覧→再利用の輪を閉じる）。tier ディレクトリが無い／`.md` が無ければその節は**サイレントに省略**（空見出しを出さない＝project/user が無ければ従来どおり shipped のみ）。**`--global` 併用時**は recipe 以外の全ブリック（persona・wiki 等）も横断し、レジストリ地図（`facets/instructions/catalog`）を提示。

```
## Recipes
### project  (<repo>/.claude/rig/recipes/)
  my-flow       [3 steps · interactive · gated]  steps: intake, implement, verify  extends: release-flow [shipped]  — design を抜いたカスタム release flow
### user  (~/.claude/rig/recipes/)
  strict-tdd    [7 steps · autonomous · tdd · gated · workflow]  steps: intake, design?[--design|L+], implement, verify, review?[--review|L+], pr, merge  extends: release-flow [shipped]  — TDD 強制の full-flow
### shipped  (skills/rig/recipes/)
#### dev (core)
  review-only   [1 step  · interactive · gated]  steps: review  — 現変更への 3-way 並列レビュー
  release-flow  [7 steps · interactive · gated]  steps: intake, design?[--design|L+], implement, verify, review?[--review|L+], pr, merge  — intake→design?→implement→verify→review?→pr→merge  ★ default
  hotfix        [4 steps · interactive · gated]  steps: intake, implement, verify, pr  — 最短経路 (intake→implement→verify→pr)
  ...
#### goal
  goal-loop     [1 step  · interactive · gated]  steps: goal-loop  — 高レベル目標を受け入れ基準に変換してループ収束
#### humor
  roast  [1 step · interactive · gated]  ...  coin  [1 step · interactive]  ...  duck  [1 step · interactive]  ...
  ...
```

各 recipe エントリの `[N step(s) · interactive|autonomous]` は frontmatter の `steps[]` 要素数と `autonomy` 値から派生する（N=1 のみ `1 step`、以降 `N steps`）。**ただし `extends` を持つ recipe は RESOLVE 後の確定 step 数（親 step のマージ・`remove: true` 除外後の全量）を N に使う**（`steps:` フィールドの計算規則と同じ。frontmatter のデルタ件数ではなく実行時の全量で表示することで、`[N steps]` badge と `steps:` フィールドの step 数が常に一致する）（#166）。**非デフォルト属性は `·` 区切りで追記する**（デフォルト値は省略し、一覧を読みやすく保つ）：

- **`· tdd`**（#62）：recipe に `tdd: true` が設定されている場合のみ付記。`--save-recipe --tdd` で保存した recipe が TDD モードで動くことを一覧で確認できる。省略時（`tdd: false`・未設定）は付記なし。
- **`· gated`**（#66）：`gate: acceptance-gate` を持つ step が1つ以上ある recipe に付記。rig の核心 **determinism-by-gate**（品質収束保証）の有無を一覧で確認できる。acceptance-gate を持つ step が1つもない recipe は付記なし。
- **`· workflow`**（#60）：recipe に `backend: workflow` が設定されている場合のみ付記。`--save-recipe --workflow` で保存した recipe が Workflow バックエンドで動くことを一覧で確認できる。省略時（`manual`・未設定）は付記なし。
- **`· no-defaults`**（#70, #128）：recipe に `no_default_personas: true` が設定されている場合のみ付記。`--save-recipe --no-default-personas` で保存した recipe が manifest `default_personas` の自動投入を抑止することを一覧で確認できる。省略時（`false`・未設定）は付記なし。
- **`· orchestrate`**（#129）：recipe に `orchestrate: true` が設定されている場合のみ付記。`--save-recipe --orchestrate` で保存した recipe が計算的オーケストレーションモード（`scripts/orchestrate.py` 決定論ランナー）で動くことを一覧で確認できる。省略時（`false`・未設定）は付記なし。
- **`· cross-llm`**（#130）：recipe に `cross_llm: true` が設定されている場合のみ付記。`--save-recipe --cross-llm` で保存した recipe が他社 LLM レビュー前提モード（① implement への `cross-llm-legibility` ポリシー注入 + ② review fan-out への `cross-llm-reviewer` 追加）で動くことを一覧で確認できる。省略時（`false`・未設定）は付記なし。
- **`· no-capture`**（#137）：recipe に `no_capture: true` が設定されている場合のみ付記。`--save-recipe --no-capture` で保存した recipe が RUN 後の capture 提案を抑止することを一覧で確認できる。省略時（`false`・未設定）は付記なし。
- **`· adversarial`**（#172）：recipe に `adversarial: true` が設定されている場合のみ付記。`--save-recipe --adversarial` で保存した recipe が敵対レビューステップを自動追加することを一覧で確認できる。省略時（`false`・未設定）は付記なし。
- **`· visual`**（#174）：recipe に `visual: true` が設定されている場合のみ付記。`--save-recipe --visual` で保存した recipe が verify ステップで UI 視覚確認を強制することを一覧で確認できる。省略時（`false`・未設定）は付記なし。
- **`· autonomous`**（#181）：recipe に `autonomy: autonomous` が設定されている場合のみ付記。`--save-recipe --autonomous` で保存した recipe が step ゲートなしで自律実行することを一覧で確認できる。省略時（`interactive`・未設定）は付記なし（interactive はデフォルト値のため非表示）。
- **`· no-orchestrate`**（#178）：recipe に `no_orchestrate: true` が設定されている場合のみ付記。`--save-recipe --no-orchestrate` で保存した recipe が orchestrate の自動有効化を打ち消すことを一覧で確認できる。省略時（`false`・未設定）は付記なし。
- **`· design`**（#182）：recipe に `design: true` が設定されている場合のみ付記。`--save-recipe --design` で保存した recipe が design step を size 非依存で常時 ON にすることを一覧で確認できる。省略時（`false`・未設定）は付記なし。
- **`· review`**（#182）：recipe に `review: true` が設定されている場合のみ付記。`--save-recipe --review` で保存した recipe が review step を size 非依存で常時 ON にすることを一覧で確認できる。省略時（`false`・未設定）は付記なし。
- **`· capture`**（#184）：recipe に `capture: true` が設定されている場合のみ付記。`--save-recipe --capture` で保存した recipe が RUN 後の capture 提案を承認ダイアログなしで自動実行することを一覧で確認できる。省略時（`false`・未設定）は付記なし。

並べ順は **`tdd` → `gated` → `workflow` → `no-defaults` → `orchestrate` → `cross-llm` → `no-capture` → `adversarial` → `visual` → `autonomous` → `no-orchestrate` → `design` → `review` → `capture`** の固定順。複数共存例：`[3 steps · interactive · tdd · gated]`。`extends` で継承した recipe も RESOLVE 後の確定値（継承分を含む）を評価する。`/rig:catalog`（`--list --global`）の recipe 一覧行にも同じメタデータ・同じ表示ルールを適用する。

**`steps:` フィールド（step ID 列・#79, #160）** — 各 recipe エントリに `steps[].id` を順に列挙した `steps: <id1>, <id2>, ...` フィールドを **badge の直後・`extends:` の前**に追加する。`condition:` フィールドを持つ step（size-aware・flag 条件付き）の id には `?[<条件略記>]` を付す（#160）。条件略記は recipe frontmatter の `condition:` 値から取得する（フラグ部と size 部を `|` 区切りで整理し、20文字以内の短い文字列に略記する。例：`"--design または size L+"` → `[--design|L+]`）。`condition:` を持たない step の id に `?` や略記は付かない。例：`steps: intake, design?[--design|L+], implement, verify, review?[--review|L+], pr, merge`。このフィールドは `description` とは独立して常に表示する（description が step 情報を含む場合も重複して表示する：description は自由テキストだが `steps:` は計算フィールドであり `--only`/`--from`/`--skip` に渡す step-id の信頼できる一覧）。`extends` で継承した recipe は RESOLVE 後の確定 step リスト（継承分を含む）を表示する。`--list --global` / `/rig:catalog` でも同様に表示する。

**`--validate` 指定時** → `facets/instructions/validate` の手順でブリック整合（参照切れ／**manifest 参照（`default_recipe` / `default_personas` が実在 tier に解決するか）**／frontmatter スキーマ／目録ドリフト／wiki 衛生）を検査し、結果を提示して**停止**（解決も実行もしない）。**manifest 数値フィールドの追加チェック（#145, #147）**：`default_max_retries` が存在する場合は整数かつ `≥ 1` であること（FAIL 例：`0` / `-1` / `"two"` / `1.5`）。`size_thresholds` が存在する場合は各サブキーが正整数かつ順序制約 `S_max ≤ M_max ≤ L_max` を満たすこと（FAIL 例：`S_max: 300, M_max: 200` 逆順 / `M_max: 0` 非正値）。**`remove: true` フィールドの整合チェック（#144）**：`extends` recipe の `remove: true` エントリで、① 対象 `id` が親に存在しない場合は WARN（停止なし）、② `remove` の値が `true`/`false` 以外の場合は FAIL。**`--validate ③` 追加チェック（#157, #158）**：`name` フィールドがファイル名（`.md` 拡張子を除く）と一致しない場合は **FAIL**（RESOLVE の `--recipe <name>` 解決と整合しなくなるため）。`steps[]` が空配列の場合は **FAIL**（実行しても空ハーネスが生成されるだけのため）。**`gate: acceptance-gate` + `acceptance:` 未宣言/空配列 WARN（#179）**：`gate: acceptance-gate` を持つ step で `acceptance:` が未宣言または空配列の場合は **WARN**（基準なし収束ゲートが常時通過するため。FAIL でなく WARN にする理由は動的補完の可能性を排除できないため）。**`tdd: true` / `visual: true` の無効コンテキスト WARN（#180）**：`tdd: true` が設定されているが implement step が存在しない recipe → **WARN**（`tdd` は implement step にのみ作用するため）。`visual: true` が設定されているが verify step が存在しない recipe → **WARN**（`visual` は verify step にのみ作用するため）。判定は `extends` 解決後の確定 step リストで行う。**`design: true` / `review: true` の無効コンテキスト WARN（#194）**：`design: true` が設定されているが design step が存在しない recipe → **WARN**（`design: true` は design step の condition を上書きするが、design step がないため設定が永続的な no-op になる）。`review: true` が設定されているが review step が存在しない recipe → **WARN**（`review: true` は review step の condition を上書きするが、review step がないため設定が永続的な no-op になる）。判定は `extends` 解決後の確定 step リストで行う（`tdd`/`visual` と同じ基準）。**`orchestrate` / `no_orchestrate` 矛盾チェック（#178）**：`orchestrate: true` と `no_orchestrate: true` が同時設定された recipe は **FAIL**（矛盾する宣言）。`checks:` または `needs:` を持ちかつ `no_orchestrate: true` の recipe は **WARN**（checks/needs 宣言の意図と矛盾する可能性）。**`capture` boolean 型チェック（#184）**：`capture` フィールドが存在する場合は `true`/`false` のいずれかであること（`"yes"` / `1` / `"true"` 等の非 boolean 値は **FAIL**）。**`capture` と `no_capture` 同時 FAIL（#184）**：`capture: true` と `no_capture: true` が同時設定された recipe は **FAIL**（`--capture` と `--no-capture` の同時指定は §7.3 で `--no-capture` 優先＋WARN と定義されているが、recipe への静的焼き込み時は矛盾として明示 FAIL にする）。**YAML parse エラー FAIL（#199）**：recipe の YAML フロントマター parse に失敗した場合は即 **FAIL** を出し、そのファイルの残りチェックをスキップして続行（validate 全体は早期終了しない）。**`autonomy` / `scope` 列挙値 FAIL（#196）**：`autonomy` が `interactive|autonomous` 以外 → FAIL、`scope` が `shipped|user|project` 以外 → FAIL（`backend` 列挙値は #52 で実装済み）。**step `id` slug 形式 FAIL（#197）**：step `id` が `[a-z][a-z0-9-]*` に一致しない場合 → FAIL（`--only`/`--skip` の CLI 安全性と Levenshtein 候補提案の精度保護）。**step `pattern` / `gate` 列挙値 FAIL（#198）**：step `pattern` が shipped pattern ブリック名（`patterns/*.md` のファイル名）以外 → FAIL、step `gate` が `review-gate|acceptance-gate` 以外 → FAIL（未設定は許容）。**step `checks:` 型・空エントリ FAIL（#200）**：`checks:` が非リスト型 → FAIL、空文字列エントリ含む → FAIL（未設定は許容。`needs:` 静的検証との対称性）。詳細エラーフォーマットは `facets/instructions/validate` ③ を参照。`--list` と同じく副作用なしの点検モード。**`--global` 併用時**は tier 横断で点検する（全 tier の orphan・リンク切れ・参照欠落・重複）。
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
| `capture` | — | `true` の場合、RUN 後の capture 提案を**承認ダイアログなしで自動実行**する（`--capture` フラグ指定時と等価。提案表示・事後報告は省略しない）。省略時 `false`。`--save-recipe` で保存可（§4.3.2）。`--capture` と `--no-capture` が同時に有効な場合は `--no-capture` 優先＋WARN（§7.3 整合）（#184） |

### step オブジェクトのキー

| キー | 必須 | 説明 |
|---|---|---|
| `id` | ✓ | step 識別子（例 `review` `design` `implement`） |
| `instruction` | ✓ | 委譲先 instruction facet 名（例 `parallel-review`） |
| `pattern` | — | 制御フロー（`serial` / `parallel-fanout` / `review-gate` 等） |
| `gate` | — | 集約/受け入れパターン。`review-gate`（レビュー集約）/ `acceptance-gate`（受け入れ基準まで品質収束。review 以外の step にも付与可） |
| `acceptance` | — | `gate: acceptance-gate` 時の**受け入れ基準リスト**（合否判定の根拠。例 `["build が成功", "lint 0 件", "3-way review に REJECT が無い"]`）。基準を満たすまで収束させる |
| `max_retries` | — | `gate: acceptance-gate` 時の**最大収束試行数 K**（≥1 の整数）。K 回で受け入れ基準を満たさなければ user へエスカレーション。**省略時フォールバック順：step 省略 → manifest `default_max_retries` → 2**（#100）。§6 stuck-guard（同一エラー反復で発動する別カウンタ）とは独立した上限。 |
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
  - `default_orchestrate`（`true` のとき全 RUN を**計算的オーケストレーション**で回す＝`--orchestrate` 等価。recipe の `checks:`/`needs:` による自動有効化とは独立にプロジェクト全体へ適用。省略時 `false`）
  - `worktree.*`（worktree 運用フラグとして使用）
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

#### 4.2.2 extends — 1段継承

recipe の frontmatter に `extends: <parent-name>` が宣言されている場合、次の手順で合成する。

1. **親 recipe の解決**：`<parent-name>` を §4.2.1 の tier 検索順で探す（bare 名のみ。パス指定・URL 不可）。
2. **step マージ**：親の `steps[]` をベースにし、子の `steps[]` を順に適用する：
   - 子 step に **`remove: true`** がある → 親から該当 `id` の step を**除外する**（§3.5 `remove` フィールド）
   - `remove` が無い / `remove: false` → 従来どおり：同 `id` は上書き、新 `id` は末尾追加

   **`remove: true` エラー処理**：① `id` が親に存在しない → `[WARN] remove: true — step '<id>' は親 recipe に存在しません（無視して続行）`（停止なし）。② 他フィールドと同時指定 → `remove: true` 優先・他フィールドを無視＋`[WARN] remove: true と他フィールドの同時指定は無効（他フィールドを無視）`。③ `--orchestrate` 利用時に削除 step が他 step の `needs:` で参照されている → `[WARN] remove: true — step '<id>' を参照する needs 宣言があります（<依存 step 名>）`。`--validate` でも同じ ①③ を WARN・② を FAIL として出力する。`--list` の `extends:` 表記（#53）に削除 step 数を `[N removed]` で補記する（例: `extends: release-flow [shipped] [1 removed]`。N=0 の場合は省略）。`--plan` テーブルと `--list` の `steps:` フィールドには削除済み step を表示しない（`[SKIP]` 表示もなし — 定義上存在しないため）。`--save-recipe` の展開結果（§4.3.2）にも `remove: true` エントリは含まれない（削除済みなので）。
3. **トップレベルキーのマージ**：`name` / `description` / `scope` / `autonomy` は子の値が優先。子に記載のないキーは親を引き継ぐ。`extends` は合成後の recipe には残さない（出力しない）。
4. **多段継承は禁止**：子が `extends` を持ち、かつ親も `extends` を持つ（孫継承）ケースはサポートしない。親の `extends` キーは無視し、警告ログを出す。

> **bare 名ルール**：`extends` の値は `release-flow` のようなファイルベース名のみ。`../other/recipe` のようなパス指定は無効。

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
- **`--plan` 表示**：`--skip`/`--to` で除外される step の condition 列にそれぞれ `[SKIP: --skip flag]`／`[SKIP: --to 範囲外]` 注記を付す（§5 `--plan` ルール参照）。

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

### 4.5 autonomy

`--autonomous` で step ゲート OFF。指定が無ければ各 step 後に確認する step ゲート ON。

> **`--autonomous` が外すのは「step ゲート（各 step 後の確認ダイアログ）」だけ。** `acceptance-gate`（受け入れ基準を満たすまで最大 K 回収束し、K 超で user エスカレーションする品質ループ）は `--autonomous` でも変わらず動く。capture ゲートと同様に、品質保証の核は `--autonomous` で解除されない。recipe の `autonomy: autonomous`（§3.5）の「ゲートなし」も step ゲートを指し、acceptance-gate の品質ループは維持される。

---

> **動作仕様**：manifest ロード（§4.1）・recipe tier 検索順（§4.2.1）・extends 1段継承（§4.2.2）・--only/--from/--to スライス（§4.3.1）・--save-recipe（§4.3.2）は本セクションの規則どおり動作する。shipped recipe は §2 目録を参照。project / user 層の recipe はリポジトリまたはホームに配置すれば即時有効になる。

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

- persona facet が `inject: ["[[slug]]", …]` を宣言している場合、各 `[[slug]]` を **tier 解決**（project overlay > global）してページを取得し、**User 先頭（Knowledge 位置）に注入**する（1ホップ既定・過剰展開しない）。
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
| **shipped**（同梱） | `skills/rig/facets/personas/<name>.md` | 3（最低） |

- `<name>` は `/` 区切りでサブディレクトリ可（例 `sales/hearing-reviewer`）。
- reviewer は引き続き agent（subagent_type）優先。agent が無いときの persona facet フォールバックはこの tier 検索で解決する。
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

`--plan` 指定時は COMPOSE で停止し、合成ハーネスを**正準フォーマット**で提示する（RUN はしない）。`--validate` レポートや capture 提案と同じく、機械抽出しやすい固定構造で出す（2回叩いても同じ構造・並びになる＝出力も determinism-by-gate）。

```
## rig --plan

recipe: release-flow | autonomy: interactive | backend: manual
diff: 24 lines → size: S  （git diff HEAD の増減行数合計と判定 size class。git 管理外の場合は "diff: 不明 → size: S（既定）"）
description: intake→design?→implement→verify→review?→pr→merge (size-aware; ? steps are conditional)
flags: --review
save-recipe: （--save-recipe 指定時のみ。保存名 → フルパス [tier(, overwrite)(, WARN: shadow…)]。無指定なら省略）
             ← --skip <step-id> は保存されません（全ステップを保存: <全 step-id 一覧>）  （--skip + --save-recipe 同時指定時のみ付記。#187）
             ← --from/--to/--only <step> は保存されません（全ステップを保存: <全 step-id 一覧>）  （--from/--to/--only + --save-recipe 同時指定時のみ付記。#192）
skip: design, review       （--skip 指定時のみ。複数は ", " 区切り。無指定なら省略）
slice: （--only/--from 指定時のみ範囲を記す。無指定なら省略）

| # | step      | instruction     | pattern         | gate            | personas                                          | policies           | output_contract | condition            |
|---|-----------|-----------------|-----------------|-----------------|---------------------------------------------------|--------------------|-----------------|----------------------|
| 1 | intake    | intake          | serial          | —               | orchestrator                                      | branch-strategy    | —               | —                    |
| 2 | design    | design          | serial          | —               | orchestrator, implementer                         | —                  | —               | --design または size L+ |
| 3 | implement | implement       | serial          | —               | implementer                                       | risk-based-testing | —               | —                    |
| 4 | verify    | verify          | serial          | acceptance-gate | implementer                                       | risk-based-testing | —               | —                    |
| 5 | review    | parallel-review | parallel-fanout | acceptance-gate | security-reviewer, design-reviewer, test-reviewer | pre-push-review    | review-verdict  | --review または size L+ |
| 6 | pr        | pr              | serial          | —               | orchestrator                                      | pr-hygiene         | —               | —                    |
| 7 | merge     | merge           | serial          | —               | orchestrator                                      | branch-strategy    | —               | —                    |

### Gate: ゲート条件

**step: verify**（acceptance-gate · max_retries: 5 ★）
- [ ] build が成功
- [ ] lint 0 件
- [ ] 全テストが green

**step: review**（max_retries: 2 [既定]）
- [ ] 3-way review に REJECT が無い
- [ ] output_contract（review-verdict）の必須項目が揃う

★ = manifest default_max_retries ／ [既定] = 汎用既定値（2）

steps: 7（うち condition 付き=2 / gate=2 / acceptance retries 上限: 7）| RUN はしない
```

ルール：
- 各行は**解決済みの最終 step 順**（extends 適用後・flag override 後）の1 step。空の任意フィールドは `—`。
- **condition 列はフラグ成分を先行評価して注記を付す**（フラグは PARSE 済みなので評価コストゼロ）：フラグのみの条件 → `[✓ 実行]` / `[✗ スキップ]`。size のみまたは混合（`--flag または size L+`）の条件は **`--plan` 実行時に `git diff HEAD` の増減行数合計を取得し size class を確定する（#185）**：diff 測定可能な場合は `[✓ 実行（size L+ · N行 > M_max）]` または `[✗ スキップ（size S/M · N行 ≤ M_max）]` と確定値を表示する。diff が取れない場合（git 管理外・新規ファイルのみ等）は `[TBD: size 不明 → S（既定）]` と表示しスキップ前提で扱う（#97 の `[TBD]` を測定可能な場合は除去）。混合（`--flag または size L+`）でフラグが真なら size に関係なく `[✓ 実行: <flag> 解決]`。`<M_max>` は manifest `size_thresholds.M_max` または既定 200。**`--skip` で除外される step は condition 列に `[SKIP: --skip flag]` と表示する**（他の condition 注記より優先して付す）。condition なしは注記なし。例: `--design または size L+ [✓ 実行: --design 解決]`。
- `--only` / `--from` / `--to` 指定時は**スライス後の step だけ**を表に出し、ヘッダ `slice:` に範囲を記す（`--only <id>` / `--from <id>` / `--to <id>` / `--from <A> --to <B>`）。`--skip` 指定時は全 step を表に出し（スライスしない）、除外 step の condition 列に `[SKIP: --skip flag]` を付し、ヘッダに `skip: <step-id(s)>` フィールドを追加する（複数は `, ` 区切り。`slice:` の前に配置。未指定なら省略）（#50）。
- **`--from`/`--to`/`--only` と `--skip` 併用時のタイブレーカー（#88）**：`--from`/`--to`/`--only` が行範囲（外枠）を決め、`--skip` はその集合内の除外（内側）を担う。テーブルには**スライス後の step だけ**を表示し（`--from`/`--to`/`--only` ルール優先）、`--skip` 対象の step は condition 列に `[SKIP: --skip flag]` を付す。ヘッダには `slice:` と `skip:` の**両方**を出す（各指定がある場合のみ）。スライスで除外された step（`--from` 開始前・`--to` 終端後の step）が `--skip` 対象だった場合は、テーブル行は表示しない（スライス外のため行が無い＝`[SKIP]` 表示も不要。ただしヘッダの `skip:` には全 skip 対象を記載する）。`--only` と `--skip` の同時指定は従来どおり `--only` 優先・`--skip` 無視＋警告（行範囲が1 step のため除外は無意味）。
- **`diff:` フィールド（#185）**：`recipe:` 行の直後かつ `description:` の前に `diff: N lines → size: S/M/L` を1行出す。N は `git diff HEAD` の増減行数合計（staged + unstaged）。size class は manifest `size_thresholds`（または既定 `S_max:100`/`M_max:200`）で判定する。diff が取れない場合（git 管理外・新規ファイルのみ等）は `diff: 不明 → size: S（既定）`。diff が 0 行の場合は `diff: 0 lines → size: S`。この `diff:` の値で condition 列の size-aware 判定を確定させる（condition 列ルール参照）。
- **`description:` フィールド（#167）**：named recipe（`--recipe` または manifest `default_recipe` で解決）の場合のみ、`recipe:` 行の**直後**に `description: <frontmatter の description 値>` を1行出す。対話合成（`recipe: ad-hoc`）の場合は省略する（frontmatter が存在しないため）。`description` フィールドが空文字列・未定義の場合も省略する（空行を出さない）。テキストは加工なし・frontmatter のそのままの値を出す（`--list` と同一テキスト）。
- **`--save-plan <path>` 指定時（#164）**：`--plan --save-plan <path>` が指定された場合、`--plan` の会話出力と**同一内容**を `<path>` に書き出す（フォーマット変換なし・§5 正準フォーマットをそのまま保存）。`<path>` は呼び出し cwd からの相対パスまたは絶対パス。既存ファイルへの上書き時は確認を取る（`--autonomous` 時は確認なしで上書き）。`--save-plan` なしの通常 `--plan` は従来どおり会話出力のみ（後方互換）。`--plan` なしで `--save-plan` のみ指定した場合は `[WARN] --save-plan は --plan と組み合わせて使用してください（無視します）` を出して無視する（`--description` の `--save-recipe` なし WARN と同形式）。`--plan --save-plan` は「会話に表示しながらファイルにも書く」であり、`--plan` の停止セマンティクスは不変（COMPOSE 後に停止・RUN はしない）。
- **`--save-recipe <name>` 指定時はヘッダに `save-recipe:` 行を出す（#35）**：`save-recipe: <name> → <フルパス> [tier]` で保存先と tier（`project`/`user`、`--user` 指定時は user 層パス）を見せる。`--plan --save-recipe` は **ファイルを書き込む副作用を持つドライラン**（§4.3.2：COMPOSE 完了時点で保存し停止）なので、書き込み前に保存先を確認できるようにする。同名ファイルが既存（上書きになる）なら `[project, overwrite]`、§4.3.2 の lower-tier shadow チェックと**同条件**で shadow が発生するなら `[project, WARN: shadow → <下位 tier パス> (<tier>)]` を付す。`--save-recipe` 指定が無い通常の `--plan` ではこの行を**省略**（既存フォーマット不変）。保存される step は §4.3.2 のとおりスライス前の全量（`--from`/`--to`/`--only` の影響を受けない）。
- ヘッダ行に、解決した recipe 名 / autonomy / backend と、recipe を変えた flag（`--review` 等）を出す。**`tdd: on` は recipe `tdd: true` または `--tdd` フラグが有効な場合のみ `| tdd: on` をヘッダに付加する（`false`/省略時は出さない）（#56）**。**`no-defaults: on` は recipe `no_default_personas: true` または `--no-default-personas` フラグが有効な場合のみ `| no-defaults: on` をヘッダに付加する（`false`/省略時は出さない）（#70, #128）**。**`orchestrate: on` は recipe `orchestrate: true` または `--orchestrate` フラグが有効な場合のみ `| orchestrate: on` をヘッダに付加する（省略時は出さない）（#124, #129）**。**`cross-llm: on` は recipe `cross_llm: true` または `--cross-llm` フラグが有効な場合のみ `| cross-llm: on` をヘッダに付加する（`false`/省略時は出さない）（#130）**。**`no-capture: on` は recipe `no_capture: true` または `--no-capture` フラグが有効な場合のみ `| no-capture: on` をヘッダに付加する（`false`/省略時は出さない）（#137）**。**`adversarial: on` は recipe `adversarial: true` または `--adversarial` フラグが有効な場合のみ `| adversarial: on` をヘッダに付加する（`false`/省略時は出さない）（#172）**。**`visual: on` は recipe `visual: true` または `--visual` フラグが有効な場合のみ `| visual: on` をヘッダに付加する（`false`/省略時は出さない）（#174）**。**`autonomous: on` は recipe `autonomy: autonomous` または `--autonomous` フラグが有効な場合のみ `| autonomous: on` をヘッダに付加する（`interactive`/省略時は出さない）（#181）**。**`orchestrate: off` は recipe `no_orchestrate: true` または `--no-orchestrate` フラグが有効な場合のみ `| orchestrate: off` をヘッダに付加する（通常の「orchestrate OFF かつ指定なし」は省略維持）（#178）**。**`design: on` は recipe `design: true` または `--design` フラグが有効な場合のみ `| design: on` をヘッダに付加する（`false`/省略時は出さない）（#182）**。**`review: on` は recipe `review: true` または `--review` フラグが有効な場合のみ `| review: on` をヘッダに付加する（`false`/省略時は出さない）（#182）**。**`capture: on` は recipe `capture: true` または `--capture` フラグが有効な場合のみ `| capture: on` をヘッダに付加する（`false`/省略時は出さない）（#184）**。**`backend:` は `manual` のみは省略可（workflow 等の非既定値のみ明示する省略形も許容）（#52）**。**recipe 名の直後に解決元 `[tier]`（`project`/`user`/`shipped`）を付す（#25）**＝ `recipe: release-flow [project]`（project が shipped を shadow していても見える）。`shipped` のみは省略可（新規ユーザーには静かでよい）、対話合成は `recipe: ad-hoc`（tier なし）。`--list` の tier 別表示と同じ語彙を使う。
- **personas 列は解決済みの最終 persona 集合を表示する**（recipe `personas[]` ＋ manifest `default_personas` ＋ `--persona` 指定分を名前で和集合・dedup。§5「manifest default_personas の自動投入」と同じ集合）＝ **`--plan` の personas ＝ 実行時 reviewer**（差異ゼロを spec で保証）。出所を明示するため manifest `default_personas` 由来に `★`、`--persona` 由来に `†`、**`--cross-llm` フラグ由来に `‡`** を付す（#87）。`‡` は implement step の `policies[]`（`cross-llm-legibility‡`）と review step の `personas[]`（`cross-llm-reviewer‡`）の両方に付与する。`--save-recipe --cross-llm` で保存した recipe を `--cross-llm` なしで再実行した場合、`cross-llm-reviewer` はマーカーなし（recipe の `personas[]` 由来）で表示される。**さらに各 persona の直後に解決元 `[tier]`（`project`/`user`/`shipped`/`agent`、未解決は `[WARN: 未解決]`）を付す（#24）**＝ COMPOSE と同じ tier 解決の結果を見せる。例: `security-reviewer [agent], house-authenticity★ [user], my-custom† [project], cross-llm-reviewer‡ [shipped]`。表末尾に凡例1行（`★ = manifest default_personas ／ † = --persona ／ ‡ = --cross-llm ／ [tier] = 解決先（project/user/shipped/agent）`）。`default_personas` も `--persona` も無く全て shipped/agent なら凡例・tier 表示は省略可。`[WARN: 未解決]` は `--validate ①` が FAIL するケースと1対1（`--plan` だけで「実行したら validate が落ちる」を予見できる）。**`no_default_personas: true` または `--no-default-personas` が有効な場合は、この最終集合から `★`（manifest `default_personas` 由来）を除外して表示する（#70）**＝ 抑止後の実行時 reviewer と一致させる。
- **`gate: acceptance-gate` または `gate: review-gate` の step が1つ以上あるとき**、表の後に「### Gate: ゲート条件」ブロックを出す（#122。無ければブロックごと省略）。ブロック内で acceptance-gate と review-gate を区別して列挙する。**acceptance-gate の step**：各 step を `id` で見出し化し `acceptance[]` をチェックリスト（`- [ ]`）で列挙、見出し横に `（acceptance-gate · max_retries: N）`（未指定は既定 2 を表示）。`acceptance[]` が空/未定義なら `（基準未定義 — WARN: ゲートが常時通過する可能性）` と注記する（`--validate` ③ の警告と同分類）。**review-gate の step（#122）**：`id` で見出し化し `（review-gate）` と明示して固定条件を列挙する：`- [ ] 全 reviewer からの REJECT がないこと` / `output_contract` 指定時は `- [ ] output_contract（<name>）の必須項目が揃うこと` を追加。これで `--plan` 段階でゲートの中身（何を満たせば合格か）まで確認できる。**（#114）acceptance-gate の `（max_retries: N）` 表示に解決元マーカーを追加する：step 定義由来はマーカーなし、manifest `default_max_retries` 由来は `（acceptance-gate · max_retries: N ★）`、汎用既定値（2）由来は `（acceptance-gate · max_retries: 2 [既定]）`。`★` または `[既定]` が1件以上使われた場合のみ Gate ブロック末尾に凡例行 `★ = manifest default_max_retries ／ [既定] = 汎用既定値（2）` を追加する（凡例が不要な場合は省略）。personas 列の `★` 凡例と語彙・パターンを統一する。**
- **`extends` 継承の出所表示（#17, #161）**：recipe が `extends: <親>` を持つときのみ、ヘッダ行に `extends: <親> [tier]` フィールドを足す（§4.2.2 の判定と同定義。親 recipe の解決元 `[tier]` も #25 と同様に付す）。さらに **step テーブルに `origin` 列を追加する（#161）**：`▸ inherited`（親から継承・子で定義なし）/ `★ override`（同一 `id` を子で上書き）/ `+ added`（子 recipe のみに存在する新規 step）。`remove: true`（#144）で削除した step は `--plan` テーブルに出さない（定義上存在しないため）。`extends` を持たない recipe では `origin` 列を省略する（表をスリムに保つ）。表の直後に1行サマリ `> extends: <親> [tier] / overridden: <子が同 id で上書きした step…> / inherited: <親から継承した step…> / added: <子のみに存在する新規 step…>` を出す。サマリのうち該当なしの区分は省略する（例：追加 step がなければ `/ added:` 行は出さない）。`extends` 無しの recipe では `origin` 列とサマリを**いずれも省略する**（差分ゼロ）。
- **`--orchestrate` 指定時のみ、`### Checks: 計算的センサー（--orchestrate）` ブロックを Gate ブロックの後・Knowledge ブロックの前に出す（#124）**（`--orchestrate` 未指定の通常 `--plan` ではブロックごと省略）。`checks[]` が定義されている step はコマンドをチェックリスト（`- [ ]`）形式で列挙する。`gate` がある step で `checks[]` が未定義 / 空の場合は `WARN: checks[] 未定義 — ランナーは独立 verdict のみを gate 根拠に使用` を付す。`gate` なし かつ `checks[]` なしの step はブロックに出さない。`--validate --orchestrate`（将来拡張）が `gated step に checks なし` を FAIL とする際の `--plan` 段階での予見にも対応する（`--plan` だけで「validate が落ちるか」を確認できる）。

  ```
  ### Checks: 計算的センサー（--orchestrate）

  **step: verify**
    - [ ] npm test
    - [ ] npm run lint

  **step: implement**（gate なし）
    WARN: checks[] 未定義 — gate なしのため独立 verdict のみで進行

  **step: review**（gate: acceptance-gate）
    WARN: checks[] 未定義 — ランナーは独立 verdict のみを gate 根拠に使用
  ```

- **`--orchestrate` 指定時かつ `needs:` 宣言 step が1件以上あるとき、`### DAG: step 並列実行トポロジー（--orchestrate）` ブロックを `### Checks:` ブロックの直後・`### Knowledge:` ブロックの前に出す（#153）**（`--orchestrate` 未指定、または `needs:` が全 step で未宣言のときはブロックごと省略）。`needs:` グラフをトポロジカルソート（BFS）し、同一 wave（並列実行可能）の step をグループ化して列挙する。`needs:` 宣言ありだが参照先 step-id が未定義の場合（`--validate` #152 が FAIL とするケース）は該当 step に `WARN: 未解決の needs` を付記し wave 計算を最善努力で続ける（`--plan` はドライラン＝FAIL でも出力を止めない）。

  **Wave 計算ルール**（SKILL §3.5 `needs:` / `patterns/computational-orchestration` の実行モデルと同一）：
  - **Wave 1**：`needs:` なし / `needs: []` の step をすべて Wave 1 に割り当てる
  - **Wave N**：`needs:` に列挙された全 step-id が Wave 1〜(N-1) に含まれる step を Wave N に割り当てる
  - 同 wave 内の step は `orchestrate run` で**同時プロセス起動**される

  ```
  ### DAG: step 並列実行トポロジー（--orchestrate）

  Wave 1（並列）:  intake
  Wave 2（並列）:  implement
  Wave 3（並列）:  review-a, review-b          ← 同 wave = 並走
  Wave 4（並列）:  verify

  依存関係:
    implement  ← intake
    review-a   ← implement
    review-b   ← implement
    verify     ← review-a, review-b
  ```

- **末尾 `steps:` サマリの `acceptance retries 上限:` フィールド（#168）**：`gate: acceptance-gate` を持つ step が1件以上ある recipe では、末尾サマリ行に `/ acceptance retries 上限: N` を追記する。N の計算ルール：RESOLVE 後に **active**（condition が ON かつ `--skip` されていない）な acceptance-gate step の `max_retries`（step ローカル値 → manifest `default_max_retries` → 汎用既定 2 の解決順。Gate ブロックと同じ）を合算する。`--skip` で除外された acceptance-gate step はアクティブでないため合算しない。`condition: [TBD: size 不明 → S（既定）]`（diff 測定不能）の acceptance-gate step が1件以上ある場合は推定値として加算し、サマリに `N*（推定含む）` と付記する（#185 により diff 測定可能な場合は `[TBD]` が解消されるため推定マークは不要になる）。acceptance-gate が0件の recipe ではフィールドを**省略**する（gate=0 の recipe にノイズを出さない）。Gate ブロックの各 step の max_retries 合計と、サマリの `acceptance retries 上限: N` は常に一致する（一貫性チェックの要点）。
- **`### Knowledge: 注入予定ソース` ブロック（#19）**：Gate ブロック（および Checks ブロック）の後に、各 knowledge tier（methodology / ai-quirks / domain / accumulated）の状態を出す（`✓ N files` / `（なし）`）。manifest `knowledge.*`（context_file / adr_dir / design_docs[]）が設定されていれば各パスと実在確認（✓ / WARN）を補記、未設定ならそのセクションを省略。全 tier なし＋manifest 未設定なら `（knowledge なし — 汎用動作）` の1行のみ。`--validate`（#14 のパス WARN）が「実在」を保証し、本ブロックが「注入される一覧」を見せる相補関係。**さらに（#59）、解決済み personas のうち `inject: ["[[slug]]", ...]` を持つものを列挙し、各 slug の wiki ページ解決先（tier: project overlay / global）と実在確認（`✓` / `WARN: 未解決`）を `- wiki（persona inject）:` セクションとして追記する。`inject:` を持つ persona が1つもない場合は `- wiki（persona inject）: （なし）` の1行のみ。同一 slug が複数 persona から inject される場合は dedup して1行にまとめる。未解決 slug は `WARN: 未解決` と表示され `--validate` ⑤ のリンク切れ FAIL と1対1で対応する（`--plan` だけで「実行したら validate が落ちる」を予見できる）。`--plan --global` では tier 横断 persona の `inject:` も追跡対象に含める。** **（#113）`✓ N files` の後に各ファイル名をインデントして1行ずつ列挙する（wiki inject の per-item 表示と非対称だった箇所を解消）。tier パス（`[global]` / `[project]`）を `✓ N files` の後ろに付記する（methodology / ai-quirks は常に `[global]`、domain / accumulated は常に `[project]`）。0件の tier は従来どおり `（なし）`（ファイル名行なし）。**

  ```
  ### Knowledge: 注入予定ソース
  - methodology: ✓ 2 files [global]
      - methodology-tdd.md
      - methodology-clean-arch.md
  - ai-quirks: （なし）
  - domain: ✓ 1 file [project]
      - ubiquitous-language.md
  - accumulated: （なし）
  - wiki（persona inject）:
      [[ddd-context]]  → ~/.claude/rig/knowledge/wiki/ddd-context.md [global] ✓
      [[auth-model]]   → <repo>/.claude/rig/knowledge/wiki/auth-model.md [project overlay] ✓
      [[missing-page]] → WARN: 未解決（--validate ⑤ が FAIL するリンク）
  ```

- **`### Reviewer Fan-out: レビュアー集合` ブロック（#171）**：`### Knowledge:` ブロックの後に出す。**review fan-out を行う step（`pattern: parallel-fanout` かつ `personas[]` を持つ step）が1つ以上ある場合のみ**出力する（review step がない recipe ではブロックごと省略）。最終 reviewer 集合（recipe `personas[]` ＋ manifest `default_personas`★ ＋ `--persona` 指定分† ＋ `--cross-llm` 由来‡）を step ごとに列挙する。`--adversarial` が有効な場合は `adversarial-review` step の reviewer（`lazy-senior`・`cognitive-economist`）も含める。出所マーカー（`★`/`†`/`‡`）と tier（`[shipped]`/`[user]`/`[project]`/`[agent]`/`[WARN: 未解決]`）を personas 列と同様に付記する。これにより personas 列の情報と完全に対称な「誰が見るか」の一覧確認ができる。

  ```
  ### Reviewer Fan-out: レビュアー集合

  **step: review**（pattern: parallel-fanout）
    - security-reviewer [shipped]
    - house-authenticity★ [user]
    - my-custom† [project]
    - cross-llm-reviewer‡ [shipped]

  凡例: ★ = manifest default_personas ／ † = --persona ／ ‡ = --cross-llm ／ [tier] = 解決先
  ```

- **`### Loop Config: ループ設定` ブロック（#170）**：`/rig:loop`（`facets/instructions/loop-driver` 経由）の `--plan` 出力でのみ出す。通常の `--plan` ではブロックごと省略。対象・間隔・停止条件を正準フォーマットで提示し停止する（RUN しない）。`loop-driver.md` の `--plan` 停止指示はこのブロックを指す。

  ```
  ### Loop Config: ループ設定

  target:    /rig:dev
  every:     10m（ScheduleWakeup delaySeconds: 600）
  until:     CI が green（gh api checks が全て pass）
  times:     —（--until で停止）
  tick:      1 / ∞
  next tick: （最初の tick 予約前）
  ```

  フィールド規則：`every:` は時間駆動の間隔（`ScheduleWakeup` の `delaySeconds` も記載）。自己ペースの場合は `every: —（自己ペース）`。`until:` は機械検証の停止条件（shell コマンド or 説明文）。`--until` なしの場合は `until: —`。`times:` は回数制限（`--times N` 指定時）。上限なしの場合は `times: —（--until で停止）` または `times: —（明示停止）`。`tick:` は現在の tick カウンタ / 上限（上限なしは `∞`）。`next tick:` は次回 `ScheduleWakeup` の予定時刻（UTC）。

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
