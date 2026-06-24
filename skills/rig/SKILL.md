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
| **persona facet**（agent フォールバック） | reviewer 人格。agent が無い時 subagent prompt の System に合成 | `facets/personas/security-reviewer` `facets/personas/design-reviewer` `facets/personas/test-reviewer` `facets/personas/orchestrator` `facets/personas/implementer` `facets/personas/debugger` `facets/personas/lazy-senior` `facets/personas/cognitive-economist` |
| **instruction facet**（薄い委譲） | 手順の routing。既存 skill/command/agent に委譲する thin な指示 | `facets/instructions/parallel-review` `facets/instructions/intake` `facets/instructions/design` `facets/instructions/implement` `facets/instructions/verify` `facets/instructions/visual-verify` `facets/instructions/pr` `facets/instructions/merge` `facets/instructions/adversarial-review` |
| **output-contract facet** | subagent 出力の機械抽出可能フォーマット定義 | `facets/output-contracts/review-verdict` |
| **policy facet** | 末尾注入のガードレール | `facets/policies/pr-hygiene` `facets/policies/pre-push-review` `facets/policies/ci-cost` `facets/policies/branch-strategy` `facets/policies/risk-based-testing` |
| **knowledge facet** | subagent prompt に注入する知識層ブリック | `facets/knowledge/orchestration-patterns` `facets/knowledge/harness-engineering` `facets/knowledge/_layer` |
| **pattern**（制御フロー） | step の実行制御テンプレ | `patterns/parallel-fanout` `patterns/review-gate` `patterns/structured-report` `patterns/serial` `patterns/autonomous-loop` `patterns/monitor` `patterns/workflow-backend` `patterns/acceptance-gate` |
| **recipe**（step の束） | step＋pattern＋facet を固定したテンプレ workflow | `recipes/review-only` `recipes/release-flow` `recipes/design-first` `recipes/hotfix` `recipes/adversarial-review`（dev-core 5 件。pack 追加分は下記） |
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
> | **sales**（`/rig:sales`） | persona `facets/personas/sales/{hearing,needs,proposal,closing,next-action}-reviewer` ／ instruction `facets/instructions/deal-review` ／ output-contract `facets/output-contracts/deal-verdict` ／ recipe `recipes/deal-review` ／ knowledge `facets/knowledge/sales-domain/` |
> | **talk**（`/rig:talk`） | persona `facets/personas/talk-assistant` ／ instruction `facets/instructions/talk-loop`（recipe なし＝既存コマンドへ委譲） |
> | **goal**（`/rig:goal`） | persona `facets/personas/goal-driver` ／ instruction `facets/instructions/goal-loop` ／ recipe `recipes/goal-loop` |
> | **pr-review**（`/rig:pr`） | instruction `facets/instructions/pr-review` ／ recipe `recipes/pr-review`（reviewer agent・persona・`review-verdict` は dev 共用） |
> | **de-ai-smell**（`/rig:dev --recipe de-ai-smell`） | persona `facets/personas/ai-smell-reviewer` ／ instruction `facets/instructions/de-ai-smell` ／ knowledge `facets/knowledge/ai-writing-smells` ／ recipe `recipes/de-ai-smell`（散文の AI 臭除去。`review-verdict` は dev 共用） |
> | **sns-x**（`/rig:dev --recipe sns-x-post`） | persona `facets/personas/sns-post-reviewer` ／ instruction `facets/instructions/sns-post` ／ knowledge `facets/knowledge/sns-x-conventions` ／ recipe `recipes/sns-x-post`（X 半自動ポスト運用。声 persona は運用者が `/rig:persona`＋`default_personas` で投入。de-ai-smell・`review-verdict` 共用） |
> | **magi**（`/rig:magi`） | persona `facets/personas/magi/{melchior,balthasar,casper}` ／ instruction `facets/instructions/magi-deliberation` ／ pattern `patterns/magi-consensus`（多数決合議ゲート） ／ output-contract `facets/output-contracts/magi-verdict` ／ recipe `recipes/magi`（エヴァ MAGI 模倣の3賢者 decision モード。正しさ/守り/価値の直交3観点で go/no-go を多数決裁定） |
> | **roast**（`/rig:roast`・humor） | persona `facets/personas/roast-reviewer` ／ instruction `facets/instructions/roast` ／ recipe `recipes/roast`（毒舌ロースト・レビュー。`review-verdict`/`review-gate` は dev 共用。中身は本物のレビューで配送をユーモアに振る adversarial-review 変種） |
> | **coin**（`/rig:coin`・humor） | persona `facets/personas/coin-flipper` ／ instruction `facets/instructions/coin-flip` ／ recipe `recipes/coin`（可逆で些末な決定を即断する反-bikeshed ゲート。重い/不可逆はトリアージで弾いて magi へ。magi の対極） |
> | **slot**（`/rig:slot`・humor） | persona `facets/personas/slot-dealer` ／ instruction `facets/instructions/slot-machine` ／ recipe `recipes/slot`（Rigsino スロット。dev テーマ3リールの息抜きゲーム。架空クレジット・dev フロー判断には非関与） |
> | **init**（`/rig:init`・utility） | instruction `facets/instructions/init`（manifest・知識層 dir・CLAUDE.md "Compact Instructions" を scaffold） |
> | **persona-gen**（`/rig:persona`・generator） | instruction `facets/instructions/persona-gen`（説明文→persona facet を project/user 層に生成。`--persona <name>` で都度投入、manifest `default_personas` で製品ごと常時自動投入。v2 Phase 1） |
> | **knowledge-gen**（`/rig:knowledge`・generator） | instruction `facets/instructions/knowledge-gen` ／ knowledge `facets/knowledge/_wiki`（説明文/`--auto` repo 解析→wiki ページを global/project に生成。persona は `inject: [[slug]]` で参照。v2 Phase 2） |
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
| `--only <step>` | 指定 step だけを実行（例 `--only review`） |
| `--from <step>` | 指定 step から最後まで実行 |
| `--recipe <name>` | shipped/user/project いずれかの recipe を名前で指定（§4.2.1 の検索順で解決）（例 `--recipe review-only`） |
| `--save-recipe <name>` | 今回合成したハーネスを recipe として保存。既定は project 層（`<repo>/.claude/rig/recipes/<name>.md`）。`--user` と組み合わせると user 層（`~/.claude/rig/recipes/<name>.md`）に書き出す |
| `--workflow` | 実行バックエンドを **workflow**（ultracode Workflow ツール）に切り替える。既定は **manual**（`patterns/workflow-backend` 参照） |
| `--capture` | capture（学びの知識層への蓄積）を承認ダイアログなしで実行（提案表示と事後報告は省略しない）。既定は capture 提案時に承認を求める |
| `--skip <step>` | 指定した step を除外してフローを継続する（複数可。例 `--skip design --skip review`）。size-aware 既定・`--design`/`--review` 等フラグより後に適用される（明示スキップが最終的に勝つ）。`--only` との同時指定は `--only` 優先・警告を出す。`--save-recipe` には影響しない（実行時フィルタ＝§4.3.2 snapshot 意味論と同じ） |
| `--list` | 利用可能なブリック(§2)・**全 tier の recipe**（project / user / shipped）・flag を一覧表示して停止（RESOLVE/COMPOSE/RUN しない） |
| `--validate` | ブリック整合チェック（doctor）。recipe→facet 参照切れ・frontmatter スキーマ逸脱・§2 目録と実ファイルのドリフトを検査し、レポートして停止（RESOLVE/COMPOSE/RUN しない）。手順は `facets/instructions/validate` |
| `--adversarial` | 敵対的レビュー step（lazy-senior / cognitive-economist で AI の癖排除・人間可読性・不要コメント除去）を合成に追加 |
| `--persona <name>` | review fan-out に名前指定のカスタム reviewer persona を**この run だけ追加**（複数可）。tier 解決（project→user→shipped・§5）で名前解決。manifest `default_personas`（製品ごとに常時自動投入）に**上乗せ**される。`/rig:persona` で生成した persona をそのまま投入できる |
| `--no-default-personas` | この run に限り manifest `default_personas` の自動投入を**抑止**する（組み込み reviewer＋`--persona` 指定分のみで回す） |
| `--global` | `--list` / `--validate` のスコープを **tier 横断**（shipped＋user(global)＋project）に広げる。`--list --global` は横断レジストリ地図（`/rig:catalog` 相当）、`--validate --global` は tier 横断の衛生点検。手順は `facets/instructions/catalog` |

**`--list` 指定時** → §2 のブリック目録・flag 一覧に加え、**recipe を全 tier 走査（§4.2.1 と同じ project → user → shipped 順）して tier 別にグルーピング表示**し、**停止**（解決も実行もしない）。各 recipe は frontmatter の `name` / `description` を出す。**`extends` を持つ recipe は `extends: <親名> [tier]` を併記する**（`extends` 無しは省略。親が解決できない場合は `[WARN: 親未解決]` を付す）（#53）。manifest に `default_recipe` が設定されているとき、一致する recipe エントリに **`★ default` を付記する**（manifest なし・`default_recipe: "interactive"` の場合はマーカーなし。`default_recipe` が未解決なら `★ default (WARN: 未解決)` を付す。`--list --global` 実行時も同様）（#55）。project / user の recipe は同名 shipped を shadow する旨を明示する（`--save-recipe` で保存したものがここで発見できる＝保存→一覧→再利用の輪を閉じる）。tier ディレクトリが無い／`.md` が無ければその節は**サイレントに省略**（空見出しを出さない＝project/user が無ければ従来どおり shipped のみ）。**`--global` 併用時**は recipe 以外の全ブリック（persona・wiki 等）も横断し、レジストリ地図（`facets/instructions/catalog`）を提示。

```
## Recipes
### project  (<repo>/.claude/rig/recipes/)
  my-flow       [3 steps · interactive] extends: release-flow [shipped]  — design を抜いたカスタム release flow
### user  (~/.claude/rig/recipes/)
  strict-tdd    [7 steps · autonomous]  extends: release-flow [shipped]  — TDD 強制の full-flow
### shipped  (skills/rig/recipes/)
  review-only   [1 step  · interactive]  — 現変更への 3-way 並列レビュー
  release-flow  [7 steps · interactive]  — intake→design?→implement→verify→review?→pr→merge  ★ default
  hotfix        [4 steps · interactive]  — 最短経路 (intake→implement→verify→pr)
  goal-loop     [3 steps · autonomous]   — 高レベル目標を受け入れ基準に変換してループ収束
  ...
```

各 recipe エントリの `[N step(s) · interactive|autonomous]` は frontmatter の `steps[]` 要素数と `autonomy` 値から派生する（N=1 のみ `1 step`、以降 `N steps`）。`/rig:catalog`（`--list --global`）の recipe 一覧行にも同じメタデータを表示する。

**`--validate` 指定時** → `facets/instructions/validate` の手順でブリック整合（参照切れ／**manifest 参照（`default_recipe` / `default_personas` が実在 tier に解決するか）**／frontmatter スキーマ／目録ドリフト／wiki 衛生）を検査し、結果を提示して**停止**（解決も実行もしない）。`--list` と同じく副作用なしの点検モード。**`--global` 併用時**は tier 横断で点検する（全 tier の orphan・リンク切れ・参照欠落・重複）。
**`--adversarial` 指定時** → 合成ハーネスの review/verify の後に `adversarial-review` step（instruction: adversarial-review / personas: lazy-senior, cognitive-economist / gate: acceptance-gate）を追加する。recipe `adversarial-review` は敵対レビューのみを回す。

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

### step オブジェクトのキー

| キー | 必須 | 説明 |
|---|---|---|
| `id` | ✓ | step 識別子（例 `review` `design` `implement`） |
| `instruction` | ✓ | 委譲先 instruction facet 名（例 `parallel-review`） |
| `pattern` | — | 制御フロー（`serial` / `parallel-fanout` / `review-gate` 等） |
| `gate` | — | 集約/受け入れパターン。`review-gate`（レビュー集約）/ `acceptance-gate`（受け入れ基準まで品質収束。review 以外の step にも付与可） |
| `acceptance` | — | `gate: acceptance-gate` 時の**受け入れ基準リスト**（合否判定の根拠。例 `["build が成功", "lint 0 件", "3-way review に REJECT が無い"]`）。基準を満たすまで収束させる |
| `max_retries` | — | `gate: acceptance-gate` 時の**最大収束試行数 K**（≥1 の整数）。K 回で受け入れ基準を満たさなければ user へエスカレーション。**既定 2**。§6 stuck-guard（同一エラー反復で発動する別カウンタ）とは独立した上限。省略時は既定 2 で従来動作と等価 |
| `personas` | — | 合成するペルソナ facet 名のリスト |
| `policies` | — | 末尾注入するポリシー facet 名のリスト |
| `output_contract` | — | subagent 出力フォーマット定義 facet 名（例 `review-verdict`） |
| `condition` | — | 条件付き step。例：`--design または size L+ で有効` のように記述し、RESOLVE フェーズで ON/OFF を判断する |

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
- どの tier にも存在しない場合は「recipe が見つかりません」とユーザーへ報告し、対話 composition（§3 引数なし手順）へフォールバックする。

#### 4.2.2 extends — 1段継承

recipe の frontmatter に `extends: <parent-name>` が宣言されている場合、次の手順で合成する。

1. **親 recipe の解決**：`<parent-name>` を §4.2.1 の tier 検索順で探す（bare 名のみ。パス指定・URL 不可）。
2. **step マージ**：親の `steps[]` をベースにし、子の `steps[]` に同じ `id` を持つ step があれば子の定義で上書きする。子のみに存在する step は親の末尾に追加する。
3. **トップレベルキーのマージ**：`name` / `description` / `scope` / `autonomy` は子の値が優先。子に記載のないキーは親を引き継ぐ。`extends` は合成後の recipe には残さない（出力しない）。
4. **多段継承は禁止**：子が `extends` を持ち、かつ親も `extends` を持つ（孫継承）ケースはサポートしない。親の `extends` キーは無視し、警告ログを出す。

> **bare 名ルール**：`extends` の値は `release-flow` のようなファイルベース名のみ。`../other/recipe` のようなパス指定は無効。

### 4.3 flag override

`--design` `--review` `--tdd` 等で §4.2 で決定した recipe の step ON/OFF を上書き。`--only <step>` / `--from <step>` で実行範囲をスライス、`--skip <step>` で特定 step を除外（後述）。manifest 由来の値も flag で上書き可能。

> **`--tdd` の特例**：`--design` / `--review` は step の ON/OFF を制御するが、`--tdd` は implement step の**動作を変える**フラグ。COMPOSE フェーズで implement subagent の prompt に「**`risk-based-testing` のリスク評価をスキップし、常に TDD（red-green-refactor）で実装する＝`tdd` スキルへの委譲を強制する**」を追加注入する（`facets/instructions/implement.md` 本体は不変）。これが無いと `--tdd` を付けても implement が通常のリスク評価で直接実装を選び、強制 TDD が効かない。

> **`tdd: true` キーの解釈（#56）**：recipe の `tdd: true` キー（§3.5）を RESOLVE 時に `--tdd` フラグと等価として処理し、COMPOSE フェーズで implement subagent への TDD 注入を発動させる。`--save-recipe` で保存した recipe の `tdd: true` を再利用する際も強制 TDD が有効になる。`--plan` ヘッダに `| tdd: on` を付加する（`tdd: true` または `--tdd` フラグが有効な場合のみ。`false`/省略時は付加しない）。

> **`backend: workflow` キーの解釈（#52）**：recipe の `backend: workflow` キー（§3.5）を RESOLVE 時に `--workflow` フラグと等価として処理し、RUN フェーズで Workflow バックエンドを使用する。manifest の `default_backend: workflow` はプロジェクト全体の既定として同様に機能し、recipe `backend:` キー・`--workflow` フラグで上書きできる。

#### 4.3.1 --only / --from / --skip — step スライス

step スライスは §4.2 で確定した **最終 step リスト**（extends 適用後・condition 評価後）に対して適用する。

| flag | 動作 |
|---|---|
| `--only <step-id>` | 指定した step-id **1つだけ**を実行する。他の step はすべてスキップ。 |
| `--from <step-id>` | 指定した step-id から最後の step まで実行する。それ以前の step はスキップ。 |
| `--skip <step-id>` | 指定した step-id を**除外**してフローを継続する。複数指定可（例 `--skip design --skip review`）。size-aware 既定・`--design`/`--review` フラグより後に適用される（明示スキップが最終的に勝つ）。 |

- `--only` と `--from` は同時指定不可。同時に与えられた場合は `--only` を優先し、`--from` を無視して警告を出す。
- `--only` と `--skip` の同時指定は `--only` 優先・`--skip` を無視して警告を出す（`--only` が1 step 実行なので `--skip` は意味なし）。
- `--skip <step-id>` と `--review`（または `--design`）を同時指定した場合、`--skip` が勝ち、その step は実行されない（明示スキップが明示 ON を上書き）。
- 指定した `<step-id>` が最終 step リストに存在しない場合（condition 評価で OFF になった step を含む）は「step が見つかりません」とユーザーへ報告し、実行可能な step-id の一覧を提示する（`--only`/`--from`/`--skip` 共通）。
- スライスは **condition 評価後**のリストに対して行う。`--only design` を指定しても condition により design が OFF の場合は「step が見つかりません」になる（`--design` フラグを同時指定することで condition をパスできる）。
- **`--skip` と `--save-recipe` の関係**：`--from`/`--only` と同様に実行時フィルタとして扱い、保存 recipe の `steps[]` には影響しない（§4.3.2 snapshot 意味論と同じ）。
- **`--plan` 表示**：`--skip` で除外される step の condition 列に `[SKIP: --skip flag]` 注記を付す（§5 `--plan` ルール参照）。

#### 4.3.2 --save-recipe — 合成結果の保存

`--save-recipe <name>` が指定された場合、RESOLVE で確定した step リスト（extends 適用後・flag override 後の最終状態）を YAML frontmatter + Markdown で生成し、ファイルに書き出す。

| オプション組み合わせ | 書き出し先 |
|---|---|
| `--save-recipe <name>` | `<repo>/.claude/rig/recipes/<name>.md`（project 層） |
| `--save-recipe <name> --user` | `~/.claude/rig/recipes/<name>.md`（user 層） |

- `scope` キーは保存先 tier に応じて `project` または `user` に自動セットする。
- **`description` 自動生成規則（#47）**：recipe スキーマ（§3.5）では `description` は必須フィールド。`--save-recipe` はベース recipe 名と有効フラグから自動生成する：`"<ベース recipe 名> のカスタマイズ（<有効フラグ列挙>）"`（例: `"release-flow のカスタマイズ（--review --tdd）"`）。対話合成（ad-hoc）の場合は `"カスタム recipe（<有効フラグ列挙>）"`。`--save-recipe` 実行時に `--autonomous` が付いていてもこの自動生成を適用する（確認ダイアログは出さず自動生成のみ）。`--plan --save-recipe` のドライラン時はヘッダ `save-recipe:` 行に生成される `description` の内容を付記する（書き込み前に確認できるように）。
- 同名ファイルが既に存在する場合は**上書き前に確認**を取る（`--autonomous` 時は確認なしで上書き）。
- **lower-tier shadow チェック（#15・上書き確認より先に実行）**：保存先より**下位の tier**（project 保存なら user→shipped、user 保存なら shipped）に同名 recipe があるか §4.2.1 の検索順で確認する。あれば保存前に **WARN**（shadow 元の tier とパスを明示し「shadow 後は元 recipe の更新が自動適用されなくなる」と添える）。`--autonomous` 時はダイアログを省略し WARN のみ表示して続行。下位 tier に同名が無ければ WARN なし（新規名は通常運用）。`extends:` を使った意図的 shadow の場合は「`extends:` で継承するレシピか確認を」と1文付記する（丸ごと差し替えか継承かの気づきを促す）。
- **保存する `autonomy` 値（#33）**：起動時に `--autonomous` フラグが指定されていた場合は `autonomy: autonomous`、指定がなければベース recipe の `autonomy` 値をそのまま引き継ぐ。これにより `--autonomous --save-recipe my-flow` で保存した recipe は再利用時も step ゲートなしで走り、保存時の意図が再現される（`--plan` ヘッダの `autonomy:` と保存 frontmatter が一致＝同一 RESOLVE 結果を参照するため差異ゼロ）。
- **保存する `backend` 値（#52）**：起動時に `--workflow` フラグが指定されていた場合は `backend: workflow` を frontmatter に保存する（省略時は `manual`・明示保存せず省略も可）。再利用時に `backend: workflow` の recipe を RESOLVE すると `--workflow` フラグと等価として処理され、Workflow バックエンドで実行される（§4.3 / §6 実行バックエンド表）。`autonomy:` との対称性：実行意図の2軸（step ゲートの有無 / 実行エンジン）がともに frontmatter に揃う。
- **保存する `tdd` 値（#56）**：起動時に `--tdd` フラグが指定されていた場合は `tdd: true` を frontmatter に保存する（省略時は `false`・明示保存せず省略も可）。再利用時に `tdd: true` の recipe を RESOLVE すると `--tdd` フラグと等価として処理され、COMPOSE フェーズで implement subagent への TDD 注入が発動する（§4.3 `--tdd` の特例）。
- **`--persona` 指定分の保存（#57）**：起動時に `--persona <name>` が指定されていた場合、reviewer fan-out を行う step（`pattern: parallel-fanout` かつ `personas[]` を持つ step）の `personas[]` に各 `<name>` を追加する（名前で dedup）。これにより `--recipe my-flow` での再利用時も `--persona` を省略して同じ reviewer 集合が再現される。`--persona` 指定なしの場合は `personas[]` の変更なし（後方互換）。`--plan --save-recipe` のドライラン表示では保存後の `personas[]`（`--persona` 追加分を含む）が確認できる（§5 `--plan` の personas 列）。
- **保存ファイルに `extends` は含めない（snapshot 意味論・#34）**：§4.2.2「extends は合成後の recipe には残さない」と同じく、`--save-recipe` は **extends 解決済みの完全展開 steps** を保存する。`extends: X` を持つ recipe を base に保存しても、保存ファイルは `extends` なし・全 steps 展開済みになる（将来の親 recipe 変更が静かに波及しない＝再現性を保証）。`extends:` を明示利用した継承 recipe を新規作成したい場合は `--save-recipe` を使わず手動で recipe に `extends:` を記述する。
- **`--from`/`--only` スライスは保存 step リストに影響しない（#37）**：`--from`/`--only` は実行時フィルタ（「今回の RUN でどの step を実行するか」の絞り込み）であり、recipe 定義（「このフローが持つ steps の全量」）の一部ではない。`--from implement --save-recipe my-flow` を実行しても、保存される `my-flow.md` には intake を含む**全 steps** が含まれる（スライス前の完全フロー）。これにより後で `--recipe my-flow` を `--from` なしで実行すれば全工程を再現できる（「保存→一覧→再利用の輪」が断たれない）。`--plan --save-recipe`（下記）の `save-recipe:` ヘッダが表示する step 数もスライス前の全量。
- `--save-recipe` は実行フローを止めない。保存後そのまま RUN を継続する。ただし `--plan` と同時指定された場合は COMPOSE 完了時点で保存し、ハーネスを提示して停止（RUN なし）。

### 4.4 size-aware 既定（軽さ優先）

変更規模に応じて重い step を自動 OFF する。行数閾値は manifest の `size_thresholds` キー（サブキー `S_max` / `M_max` / `L_max`）で上書きできる（未設定時は pr-hygiene 基準 `S_max:100` / `M_max:200` / `L_max:400` を使用。テンプレは `manifests/_template.md`）。

- **S / M**（既定：`M_max` 以下＝～200行）: design / review / tdd を**既定 OFF**。明示 flag で ON にした場合のみ実行。
- **L 以上**（既定：`M_max` 超＝200行超。`L_max` 超は分割必須）: design / review を推奨し、ON を促す。

### 4.5 autonomy

`--autonomous` で step ゲート OFF。指定が無ければ各 step 後に確認する step ゲート ON。

> **`--autonomous` が外すのは「step ゲート（各 step 後の確認ダイアログ）」だけ。** `acceptance-gate`（受け入れ基準を満たすまで最大 K 回収束し、K 超で user エスカレーションする品質ループ）は `--autonomous` でも変わらず動く。capture ゲートと同様に、品質保証の核は `--autonomous` で解除されない。recipe の `autonomy: autonomous`（§3.5）の「ゲートなし」も step ゲートを指し、acceptance-gate の品質ループは維持される。

---

> **動作仕様**：manifest ロード（§4.1）・recipe tier 検索順（§4.2.1）・extends 1段継承（§4.2.2）・--only/--from スライス（§4.3.1）・--save-recipe（§4.3.2）は本セクションの規則どおり動作する。shipped recipe は §2 目録を参照。project / user 層の recipe はリポジトリまたはホームに配置すれば即時有効になる。

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
flags: --review
save-recipe: （--save-recipe 指定時のみ。保存名 → フルパス [tier(, overwrite)(, WARN: shadow…)]。無指定なら省略）
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

### Gate: 受け入れ基準（gate: acceptance-gate の step のみ）

**step: verify**（max_retries: 2）
- [ ] build が成功
- [ ] lint 0 件
- [ ] 全テストが green

**step: review**（max_retries: 2）
- [ ] 3-way review に REJECT が無い
- [ ] output_contract（review-verdict）の必須項目が揃う

steps: 7（うち condition 付き=2 / gate=2）| RUN はしない
```

ルール：
- 各行は**解決済みの最終 step 順**（extends 適用後・flag override 後）の1 step。空の任意フィールドは `—`。
- **condition 列はフラグ成分を先行評価して注記を付す**（フラグは PARSE 済みなので評価コストゼロ）：フラグのみの条件 → `[✓ 実行]` / `[✗ スキップ]`。size のみ → `[TBD: size 確定待ち]`（RUN 開始後に判定）。混合（`--flag または size L+`）→ フラグ真なら `[✓ 実行: <flag> 解決]`、偽なら `[TBD: size L+ のみで有効]`。**`--skip` で除外される step は condition 列に `[SKIP: --skip flag]` と表示する**（他の condition 注記より優先して付す）。condition なしは注記なし。例: `--design または size L+ [✓ 実行: --design 解決]`。
- `--only` / `--from` 指定時は**スライス後の step だけ**を表に出し、ヘッダ `slice:` に範囲を記す。`--skip` 指定時は全 step を表に出し（スライスしない）、除外 step の condition 列に `[SKIP: --skip flag]` を付し、ヘッダに `skip: <step-id(s)>` フィールドを追加する（複数は `, ` 区切り。`slice:` の前に配置。未指定なら省略）（#50）。
- **`--save-recipe <name>` 指定時はヘッダに `save-recipe:` 行を出す（#35）**：`save-recipe: <name> → <フルパス> [tier]` で保存先と tier（`project`/`user`、`--user` 指定時は user 層パス）を見せる。`--plan --save-recipe` は **ファイルを書き込む副作用を持つドライラン**（§4.3.2：COMPOSE 完了時点で保存し停止）なので、書き込み前に保存先を確認できるようにする。同名ファイルが既存（上書きになる）なら `[project, overwrite]`、§4.3.2 の lower-tier shadow チェックと**同条件**で shadow が発生するなら `[project, WARN: shadow → <下位 tier パス> (<tier>)]` を付す。`--save-recipe` 指定が無い通常の `--plan` ではこの行を**省略**（既存フォーマット不変）。保存される step は §4.3.2 のとおりスライス前の全量（`--from`/`--only` の影響を受けない）。
- ヘッダ行に、解決した recipe 名 / autonomy / backend と、recipe を変えた flag（`--review` 等）を出す。**`tdd: on` は recipe `tdd: true` または `--tdd` フラグが有効な場合のみ `| tdd: on` をヘッダに付加する（`false`/省略時は出さない）（#56）**。**`backend:` は `manual` のみは省略可（workflow 等の非既定値のみ明示する省略形も許容）（#52）**。**recipe 名の直後に解決元 `[tier]`（`project`/`user`/`shipped`）を付す（#25）**＝ `recipe: release-flow [project]`（project が shipped を shadow していても見える）。`shipped` のみは省略可（新規ユーザーには静かでよい）、対話合成は `recipe: ad-hoc`（tier なし）。`--list` の tier 別表示と同じ語彙を使う。
- **personas 列は解決済みの最終 persona 集合を表示する**（recipe `personas[]` ＋ manifest `default_personas` ＋ `--persona` 指定分を名前で和集合・dedup。§5「manifest default_personas の自動投入」と同じ集合）＝ **`--plan` の personas ＝ 実行時 reviewer**（差異ゼロを spec で保証）。出所を明示するため manifest `default_personas` 由来に `★`、`--persona` 由来に `†` を付す。**さらに各 persona の直後に解決元 `[tier]`（`project`/`user`/`shipped`/`agent`、未解決は `[WARN: 未解決]`）を付す（#24）**＝ COMPOSE と同じ tier 解決の結果を見せる。例: `security-reviewer [agent], house-authenticity★ [user], my-custom† [project]`。表末尾に凡例1行（`★ = manifest default_personas ／ † = --persona ／ [tier] = 解決先（project/user/shipped/agent）`）。`default_personas` も `--persona` も無く全て shipped/agent なら凡例・tier 表示は省略可。`[WARN: 未解決]` は `--validate ①` が FAIL するケースと1対1（`--plan` だけで「実行したら validate が落ちる」を予見できる）。
- **`gate: acceptance-gate` の step が1つ以上あるとき**、表の後に「### Gate: 受け入れ基準」ブロックを出す（無ければブロックごと省略）。各 step を `id` で見出し化し `acceptance[]` をチェックリスト（`- [ ]`）で列挙、見出し横に `（max_retries: N）`（未指定は既定 2 を表示）。`acceptance[]` が空/未定義なら `（基準未定義 — WARN: ゲートが常時通過する可能性）` と注記する（`--validate` ③ の警告と同分類）。これで `--plan` 段階でゲートの中身（何を満たせば合格か）まで確認できる。
- **`extends` 継承の出所表示（#17）**：recipe が `extends: <親>` を持つときのみ、ヘッダ行に `extends: <親> [tier]` フィールドを足し、表の直後に1行サマリ `> extends: <親> [tier] / overridden: <子が同 id で上書きした step…> / inherited: <親から継承した step…>` を出す（§4.2.2 の判定と同定義。親 recipe の解決元 `[tier]` も #25 と同様に付す）。`extends` 無しの recipe では両方とも省略（差分ゼロ）。
- **`### Knowledge: 注入予定ソース` ブロック（#19）**：Gate ブロックの後に、各 knowledge tier（methodology / ai-quirks / domain / accumulated）の状態を出す（`✓ N files` / `（なし）`）。manifest `knowledge.*`（context_file / adr_dir / design_docs[]）が設定されていれば各パスと実在確認（✓ / WARN）を補記、未設定ならそのセクションを省略。全 tier なし＋manifest 未設定なら `（knowledge なし — 汎用動作）` の1行のみ。`--validate`（#14 のパス WARN）が「実在」を保証し、本ブロックが「注入される一覧」を見せる相補関係。

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
▸ rig | recipe: <name|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending [(try N/K)]|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```

- `recipe`：`--recipe`/manifest 由来名。対話合成なら `ad-hoc`。`step`：現 step の id と位置（`--only`/`--from` スライス時はスライス後の N）。`gate`：現 step のゲート状態。
- **`gate: pending` の acceptance-gate 試行位置（#32）**：`gate: acceptance-gate` の step が収束ループ中（基準未達で retry に入った）は `pending (try N/K)` と試行回数を付す（`K` は当該 step の `max_retries`・RESOLVE 確定値で `--plan` の `（max_retries: N）` と同じ出所）。`step: (n/N)` が「全フロー中の位置」を示すのと対称に、`(try N/K)` は「この step 内の収束ループの位置」を示す。**初回実行（まだ retry に入っていない 0 回目）は `(try …)` を付けない**（素の `pending`。retry 1 回目から `(try 1/K)`）。`K 超`で `## rig acceptance-gate: K 超エスカレーション`（§6）へ。`gate: none|passed|REJECT` は確定状態のため `(try …)` を付けない（既存表記を維持）。
- これにより「**rig が今ここを駆動中**」と「次でエスカレーションが来るか」が常に可視化される。

**② 再アンカー規則** — 質疑・脱線で**1ターン抜けた直後の作業ターン**は、作業に入る前に必ず：(1) ① のヘッダを再掲、(2) アクティブなハーネス状態を1行で再宣言（どの recipe のどの step を、どの委譲先で再開するか）、(3) **現 step から再開**する。**素の直接作業・ゲート省略へ静かに切り替えない**（下記 red flag に明示適用）。

**③ step 境界バナー** — step の開始/委譲/ゲート/完了で印を1行出し、subagent dispatch とゲートが実際に起きていることを可視化する。

```
── step <id> ▸ dispatch → <agent|subagent>
── step <id> ▸ gate: <acceptance-gate|review-gate> [<pending→passed|REJECT>]
── step <id> ▸ done
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
