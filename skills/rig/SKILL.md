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
> | **init**（`/rig:init`・utility） | instruction `facets/instructions/init`（manifest・知識層 dir・CLAUDE.md "Compact Instructions" を scaffold） |
> | **persona-gen**（`/rig:persona`・generator） | instruction `facets/instructions/persona-gen`（説明文→persona facet を project/user 層に生成。`--persona <name>` で投入。v2 Phase 1） |
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
| `--list` | 利用可能なブリック(§2)・shipped recipe・flag を一覧表示して停止（RESOLVE/COMPOSE/RUN しない） |
| `--validate` | ブリック整合チェック（doctor）。recipe→facet 参照切れ・frontmatter スキーマ逸脱・§2 目録と実ファイルのドリフトを検査し、レポートして停止（RESOLVE/COMPOSE/RUN しない）。手順は `facets/instructions/validate` |
| `--adversarial` | 敵対的レビュー step（lazy-senior / cognitive-economist で AI の癖排除・人間可読性・不要コメント除去）を合成に追加 |
| `--persona <name>` | review fan-out に名前指定のカスタム reviewer persona を追加（複数可）。tier 解決（project→user→shipped・§5）で名前解決。`/rig:persona` で生成した persona をそのまま投入できる |
| `--global` | `--list` / `--validate` のスコープを **tier 横断**（shipped＋user(global)＋project）に広げる。`--list --global` は横断レジストリ地図（`/rig:catalog` 相当）、`--validate --global` は tier 横断の衛生点検。手順は `facets/instructions/catalog` |

**`--list` 指定時** → §2 のブリック目録（shipped recipe 一覧を含む）・flag 一覧を提示して**停止**（解決も実行もしない）。**`--global` 併用時**は shipped に加え user(global)・project 層も走査し、横断レジストリ地図（`facets/instructions/catalog`）を提示。
**`--validate` 指定時** → `facets/instructions/validate` の手順でブリック整合（参照切れ／frontmatter スキーマ／目録ドリフト／wiki 衛生）を検査し、結果を提示して**停止**（解決も実行もしない）。`--list` と同じく副作用なしの点検モード。**`--global` 併用時**は tier 横断で点検する（全 tier の orphan・リンク切れ・参照欠落・重複）。
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
| `autonomy` | ✓ | `interactive`（各 step でゲート確認）/ `autonomous`（ゲートなし） |
| `extends` | — | 継承元 recipe の bare 名。指定 recipe の steps をベースに差分だけ上書きする。1段のみ有効（§4.2.2 参照） |

### step オブジェクトのキー

| キー | 必須 | 説明 |
|---|---|---|
| `id` | ✓ | step 識別子（例 `review` `design` `implement`） |
| `instruction` | ✓ | 委譲先 instruction facet 名（例 `parallel-review`） |
| `pattern` | — | 制御フロー（`serial` / `parallel-fanout` / `review-gate` 等） |
| `gate` | — | 集約/受け入れパターン。`review-gate`（レビュー集約）/ `acceptance-gate`（受け入れ基準まで品質収束。review 以外の step にも付与可） |
| `acceptance` | — | `gate: acceptance-gate` 時の**受け入れ基準リスト**（合否判定の根拠。例 `["build が成功", "lint 0 件", "3-way review に REJECT が無い"]`）。基準を満たすまで収束させる |
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

`--design` `--review` `--tdd` 等で §4.2 で決定した recipe の step ON/OFF を上書き。`--only <step>` / `--from <step>` で実行範囲をスライス（後述）。manifest 由来の値も flag で上書き可能。

#### 4.3.1 --only / --from — step スライス

step スライスは §4.2 で確定した **最終 step リスト**（extends 適用後・condition 評価後）に対して適用する。

| flag | 動作 |
|---|---|
| `--only <step-id>` | 指定した step-id **1つだけ**を実行する。他の step はすべてスキップ。 |
| `--from <step-id>` | 指定した step-id から最後の step まで実行する。それ以前の step はスキップ。 |

- `--only` と `--from` は同時指定不可。同時に与えられた場合は `--only` を優先し、`--from` を無視して警告を出す。
- 指定した `<step-id>` が最終 step リストに存在しない場合（condition 評価で OFF になった step を含む）は「step が見つかりません」とユーザーへ報告し、実行可能な step-id の一覧を提示する。
- スライスは **condition 評価後**のリストに対して行う。`--only design` を指定しても condition により design が OFF の場合は「step が見つかりません」になる（`--design` フラグを同時指定することで condition をパスできる）。

#### 4.3.2 --save-recipe — 合成結果の保存

`--save-recipe <name>` が指定された場合、RESOLVE で確定した step リスト（extends 適用後・flag override 後の最終状態）を YAML frontmatter + Markdown で生成し、ファイルに書き出す。

| オプション組み合わせ | 書き出し先 |
|---|---|
| `--save-recipe <name>` | `<repo>/.claude/rig/recipes/<name>.md`（project 層） |
| `--save-recipe <name> --user` | `~/.claude/rig/recipes/<name>.md`（user 層） |

- `scope` キーは保存先 tier に応じて `project` または `user` に自動セットする。
- 同名ファイルが既に存在する場合は**上書き前に確認**を取る（`--autonomous` 時は確認なしで上書き）。
- `--save-recipe` は実行フローを止めない。保存後そのまま RUN を継続する。ただし `--plan` と同時指定された場合は COMPOSE 完了時点で保存し、ハーネスを提示して停止（RUN なし）。

### 4.4 size-aware 既定（軽さ優先）

変更規模に応じて重い step を自動 OFF する。行数閾値は manifest の `size_thresholds` キーで上書きできる（未設定時は pr-hygiene 基準を使用）。

- **S / M**（既定：～200行）: design / review / tdd を**既定 OFF**。明示 flag で ON にした場合のみ実行。
- **L 以上**（既定：200行超）: design / review を推奨し、ON を促す。

### 4.5 autonomy

`--autonomous` で step ゲート OFF。指定が無ければ各 step 後に確認する step ゲート ON。

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


### `--plan` の停止

`--plan` 指定時は COMPOSE で停止し、合成ハーネスを人間可読で提示する：どの step を、どの pattern で、どの agent / persona・output-contract・policy を使って回すか。RUN はしない。

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
▸ rig | recipe: <name|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```

- `recipe`：`--recipe`/manifest 由来名。対話合成なら `ad-hoc`。`step`：現 step の id と位置（`--only`/`--from` スライス時はスライス後の N）。`gate`：現 step のゲート状態。
- これにより「**rig が今ここを駆動中**」が常に可視化される。

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
- **同じ所で2回詰まったら**（同じエラー・同じレビュー REJECT を2巡）勝手に試行を続けず、**user に判断を仰ぐ**。
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
| **MEMORY.md インデックス** | `~/.claude/projects/<proj>/memory/MEMORY.md` | memory store に追記した各ファイルへの**1行ポインタ**を MEMORY.md に追加する |

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

```
## capture 提案（承認してください）

### [1] ai-quirk — <quirk の短い名前>
- 書き込み先: ~/.claude/rig/knowledge/ai-quirks/<name>-descriptive.md（記述形）
               ~/.claude/rig/knowledge/ai-quirks/<name>-policy.md（規範形）
- 内容草案: ...（記述形：何が起きたか / 規範形：次回 prompt に注入するルール）

### [2] pitfall — <落とし穴の短い名前>
- 書き込み先: <repo>/.claude/rig/knowledge/accumulated/<name>.md
               ~/.claude/projects/<proj>/memory/<name>.md（type=project）
               MEMORY.md に1行ポインタ追加
- 内容草案: ...

承認しますか？ [y / 個別に選ぶ / skip]
```

ユーザーが個別選択した場合、選ばれた項目だけを書き込む。

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
