# instruction: resolve

**RESOLVE（§4）の詳細規則の正本。** SKILL.md §4 は解決順の骨子とポインタのみを持ち、manifest キーの意味・recipe tier 検索の報告フォーマット・`extends` の N 段合成・flag⇔recipe キー等価・step スライスのエラーフォーマット・`--save-recipe` の保存規則は**このファイルが正本**。RESOLVE を自力で回すとき（下記フォールバック条件）は必ずこれを読んで従う。

> **一次実装はコード**：named recipe（`--recipe` / manifest `default_recipe` / bare 名で解決した recipe ファイル）の RESOLVE は、まず `orchestrate plan <recipe> --json --with "<flags>" --diff-git` を実行し、その出力を確定結果として使う——`effective_steps`・各 step の `active`/`why`・`errors`（あれば ERROR 停止）・`warnings`・`mode`（orchestrate on/off/auto・autonomy・backend）・`badges`/`steps_field`。extends マージ（remove/origin）・condition 評価・size 判定・スライスと優先順位・recipe キー⇔フラグ等価・manifest `size_thresholds`/`default_orchestrate` はすべてこの出力が正。`selftest` シナリオ Q/R/S が golden 検証。
>
> **フォールバック（散文規則）**：スクリプトを実行できないとき（python3 不在・`orchestrate` コマンドが見つからない・Bash 拒否）と **ad-hoc 対話合成**（recipe ファイルが無い）に限り、本ファイルの散文規則を自力で適用する。解釈が割れたら `selftest` の golden が正＝**コード側を先に直し、本ファイルを追随させる**。COMPOSE 以降（facet 合成・knowledge 注入・subagent dispatch）はエンジンの仕事（スクリプトは RESOLVE までを担う）。

## 1. manifest ロード（§4.1）

起動時に **`<repo>/.claude/rig.md`** の存在を確認する。**manifest スキーマの全体定義は `manifests/_template.md` が正本**——以下は「エンジンが RESOLVE で読む値」の一覧。

> repo 同梱の manifest は project recipe と同じく**初回のみ明示同意**が必要（`--allow-project-manifest` / `RIG_ALLOW_PROJECT_MANIFEST=1`・コンテンツハッシュを trust store に記録。`rig-wb githooks install` はその時点の manifest への同意を兼ねる）。未同意の manifest はハード停止ではなく警告1行で **「manifest 無し」相当へ soft degrade** する。

| キー | 用途 | manifest が無い場合の汎用既定 |
|---|---|---|
| `build` / `lint` / `test` | ビルド系 step のコマンド | `package.json` / `build.gradle` / `Makefile` を自動検出して推定 |
| `branch.*` | ブランチ作成・CI 確認 step | `branch.base` は `git remote show origin` のデフォルトブランチ |
| `reviewer` | review step の委譲先選択 | `human`（PR を作成して承認を待つ） |
| `production_impact.paths` / `.keywords` | 本番影響検知の閾値 | `auth` / `migration` / `security` / `di` / `interface` をヒューリスティック検出 |
| `skills` | instruction facet の委譲先候補 | セッション開始時に利用可能な skill を自動検出 |
| `knowledge.*` | Knowledge facet の注入ソース | repo を検索して `CONTEXT.md` / `CLAUDE.md` / `docs/` を探す |
| `default_recipe` | recipe 解決（下記 2.） | `interactive`（毎回ユーザーに選択させる） |
| `default_personas` | review fan-out へ自動投入する persona 名リスト（SKILL.md §5） | `[]`（自動投入なし） |
| `default_backend` | 全 RUN の既定バックエンド（`manual`/`workflow`）。recipe `backend:` / `--workflow` で個別上書き（#52） | `manual` |
| `default_max_retries` | `acceptance-gate` step の `max_retries` 省略時フォールバック。step ローカル `max_retries` で上書き（#100） | `2` |
| `org_dir` | チーム共有ブリック層（org tier）のパス。env `RIG_ORG_HOME` でも指定可（SKILL.md §5 tier 解決） | 未設定＝org tier をスキップ |
| `default_budget` | コスト予算の恒久設定（`low`/`mid`）。`--budget` が優先（§4.4） | 制限なし |
| `sage_notifications` | `true` で能力獲得系の完了報告（import の lock 記録・persona/knowledge 生成・capture 書き込み）の先頭に `《告》スキル「<name>」を獲得しました` を1行付す。演出のみ・報告本文は不変 | `false` |
| `default_orchestrate` | `true` で全 RUN を計算的オーケストレーションで回す（`--orchestrate` 等価）。recipe の `checks:`/`needs:` による自動有効化とは独立にプロジェクト全体へ適用 | `false` |
| `worktree.*` | worktree 運用フラグ。`worktree.enabled` を実際に読んで分岐するのは `facets/personas/implementer`（#225） | `worktree.enabled: false` |
| `size_thresholds.*` | size-aware 判定の行数閾値（`S_max`/`M_max`/`L_max`）を上書き | pr-hygiene 基準 `100` / `200` / `400` |

## 2. recipe 解決（§4.2）

manifest ロード後、次の優先順位で使用 recipe を確定する。`--recipe` が指定されれば manifest の `default_recipe` は無視される。

1. `--recipe <name>` フラグ（明示指定）
2. manifest の `default_recipe` 値
3. 対話（ユーザーにブリックを提案して選択させる）

### 2.1 recipe ファイル検索順（tier 優先順位）

recipe 名が決まったら以下の順でファイルを探す。**先に見つかった tier が優先**され、下位 tier の同名 recipe は無視される。

| tier | パス | 優先度 |
|---|---|---|
| **project**（最高） | `<repo>/.claude/rig/recipes/<name>.md` | 1（最優先） |
| **user** | `~/.claude/rig/recipes/<name>.md` | 2 |
| **shipped**（同梱） | `skills/engine/recipes/<name>.md` | 3（最低） |

- `<repo>` は現在の git リポジトリルート（`git rev-parse --show-toplevel` で取得）。
- 同名 recipe が project 層に存在すれば shipped 層は読まれない。user 層は project 層が無い場合のみ参照される。
- どの tier にも存在しない場合は下記フォーマットで報告し、対話 composition（SKILL.md §3「引数なし / 曖昧な場合」）へフォールバックする（下記 4.1 ケース A の step-id not found と同形式）。tier に recipe が1件もない場合はその tier 見出しをサイレントに省略する（`--list` の tier 省略ルールと同じ）。
- **「もしかして」候補提案（#188）**：recipe が見つからない場合、`[ERROR]` の直後・「利用可能な recipe:」の直前に、編集距離（Levenshtein）≤ 2 の候補を距離昇順で最大 3 件表示する。候補は全 tier（project → user → shipped）を対象とし、各候補に `[tier]`（`--list` と同じ語彙）を付記する。同距離の候補は tier 優先順（project > user > shipped）でソートする。候補が 0 件なら「もしかして:」行を省略し既存の全一覧のみを出す（ノイズを増やさない）。

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

### 2.2 extends — N 段継承（上限 5・#193）

recipe の frontmatter に `extends: <parent-name>` が宣言されている場合、次の手順で合成する。

1. **チェーンの解決**：leaf → parent → grandparent → …の順に `extends` を辿る。各段の `<parent-name>` を 2.1 の tier 検索順で探す（bare 名のみ。パス指定・URL 不可）。**深さ上限 5**（`EXTENDS_MAX_DEPTH` in `orchestrate.py`）を超えたら残りを無視し WARN。**循環継承**（A → B → A 等）は検知次第 `[WARN] extends: 循環継承を検知しました (X → Y → Z → X)` を出して途中打ち切り。これらは実行を止めないが `--validate` は WARN として集計する。**認知経済的に浅く保つ**（深い継承は追跡できない）。
2. **step マージ**：root ancestor の `steps[]` をベースにし、leaf に向かって順に各段の `steps[]` を適用する。
   - `remove: true` がある → 継承元から該当 `id` の step を**除外する**（SKILL.md §3.5 `remove` フィールド）
   - `remove` が無い / `remove: false` → 同 `id` は上書き（`_origin=override`）、新 `id` は末尾追加（`_origin=added`）
3. **トップレベルキーのマージ**：`name` / `description` / `scope` / `autonomy` などは root → parent → leaf の順に上書き（leaf の値が最終的に勝つ）。子に記載のないキーは祖先を引き継ぐ。`extends` は合成後の recipe には残さない（出力しない）。

**`remove: true` のエラー処理**：

| ケース | 挙動 | `--validate` |
|---|---|---|
| `id` が継承元に存在しない | `[WARN] remove: true — step '<id>' は継承元に存在しません（<layer> 側指定・無視して続行）`（停止なし） | WARN |
| 他フィールドと同時指定 | `remove: true` 優先・他フィールドを無視＋`[WARN] remove: true と他フィールドの同時指定は無効（他フィールドを無視）` | FAIL |
| `--orchestrate` 利用時に削除 step が他 step の `needs:` に残る | `[WARN] remove: true — step '<id>' を参照する needs 宣言があります（<依存 step 名>）` | WARN |

削除 step の表示規則：`--list` の `extends:` 表記（#53）に削除 step 数を `[N removed]` で補記する（例 `extends: release-flow [shipped] [1 removed]`。N=0 なら省略）。`--plan` テーブルと `--list` の `steps:` フィールドには削除済み step を表示しない（`[SKIP]` 表示もなし——定義上存在しないため）。`--save-recipe` の展開結果（下記 5.）にも `remove: true` エントリは含まれない。

> **bare 名ルール**：`extends` の値は `release-flow` のようなファイルベース名のみ。`../other/recipe` のようなパス指定は無効。
>
> **旧仕様との互換性**：以前は「1 段のみ・親の `extends` を無視」だった（v0.92 以前）。N 段化に伴い既存の 1 段継承（`release-movie extends movie` 等）は挙動不変で通る。既存 recipe を書き換える必要はない。

## 3. flag override と flag⇔recipe キー等価（§4.3）

`--design` `--review` `--tdd` 等で 2. で決定した recipe の step ON/OFF を上書きする。`--only` / `--from` / `--to` で実行範囲をスライス、`--skip` で特定 step を除外する（下記 4.）。manifest 由来の値も flag で上書き可能。

### 3.1 等価規則（1つの一般則）

**boolean な recipe キーは、対応するフラグと RESOLVE 時に完全に等価**として処理する。個別の例外はない：

1. **等価**：recipe キーが `true` なら、対応するフラグが指定されたのと同じ効果が発動する（省略時は `false`）。
2. **保存**：`--save-recipe` は、起動時に指定されていたフラグを対応キーとして frontmatter に保存する（`true` のときだけ書き出し、`false`／省略は書かない）。これにより再利用時にフラグなしで同じ挙動が再現される＝**保存した意図が静かに失われない**。
3. **可視化**：有効なとき `--plan` ヘッダとフロー完了レポート（§6）に修飾子を、`--list` に badge を付す（無効時は省略）。修飾子・badge の表示仕様の正本はそれぞれ `facets/instructions/plan` / `facets/instructions/list`。

| flag | recipe キー | 効果 | `--plan`／完了レポート修飾子 | `--list` badge |
|---|---|---|---|---|
| `--tdd` | `tdd` | implement step の**動作を変える**：`risk-based-testing` のリスク評価をスキップし常に TDD（red-green-refactor）＝`tdd` スキルへの委譲を強制する注入を COMPOSE で implement subagent prompt に追加する（`facets/instructions/implement.md` 本体は不変）。これが無いと `--tdd` を付けても implement が通常のリスク評価で直接実装を選ぶ（#56） | `\| tdd: on` | `· tdd` |
| `--autonomous` | `autonomy: autonomous` | step ゲート（各 step 後の確認ダイアログ）を OFF。acceptance-gate の品質収束ループは維持（§4.5）。`--save-recipe` は指定の有無にかかわらず `autonomy` を常に明示保存する（ベース recipe の値を引き継がない・#33/#181） | `\| autonomous: on` | `· autonomous` |
| `--workflow` | `backend: workflow` | RUN を Workflow バックエンドで実行（§6 実行バックエンド表）。manifest `default_backend: workflow` はプロジェクト全体の既定として同様に機能し、recipe キー・フラグで上書きできる（#52） | ヘッダ `backend:` フィールドで表示 | `· workflow` |
| `--no-default-personas` | `no_default_personas` | manifest `default_personas` の自動投入を抑止する（最終 reviewer 集合から `★`＝manifest 由来 persona を除外・§5）。意図的に外した reviewer が再利用時に静かに復活しない（#70） | `\| no-defaults: on` | `· no-defaults` |
| `--orchestrate` | `orchestrate` | 計算的オーケストレーション ON＝step 遷移・ゲート判定・リトライ・停止条件・状態保持を `scripts/orchestrate.py` に強制させる（`patterns/computational-orchestration`）（#129） | `\| orchestrate: on` | `· orchestrate` |
| `--no-orchestrate` | `no_orchestrate` | orchestrate の**自動有効化**（下記 3.2）を打ち消す＝従来の散文エンジンで回す。anti-flag（#178） | `\| orchestrate: off` | `· no-orchestrate` |
| `--cross-llm` | `cross_llm` | 2方向に作用（#71・#130）：① **書く側**＝implement step の `policies[]` に `cross-llm-legibility` を追加し subagent prompt 末尾（Policy 位置）に注入。② **見る側**＝review fan-out に `cross-llm-reviewer` persona を追加（`--persona` と同じ経路で和集合・dedup）。implement step が無い recipe では ① をスキップ、review step が無い recipe では ② をスキップする | `\| cross-llm: on` | `· cross-llm` |
| `--adversarial` | `adversarial` | 合成ハーネスの review/verify の後に `adversarial-review` step（instruction: adversarial-review / personas: lazy-senior, cognitive-economist / gate: acceptance-gate）を追加する（#172） | `\| adversarial: on` | `· adversarial` |
| `--visual` | `visual` | verify step の**動作を変える**：`visual-verify` instruction への委譲を強制する（UI 視覚確認を常時実行）。`--tdd` の implement 注入と同じ「step 動作変更」パターン（#174） | `\| visual: on` | `· visual` |
| `--design` | `design` | design step の condition（`--design または size L+`）を上書きして常時 ON にする（size S/M でもスキップされない）（#182） | `\| design: on` | `· design` |
| `--review` | `review` | review step の condition（`--review または size L+`）を上書きして常時 ON にする（size S/M でもスキップされない）。`design` と常に対称に扱う（#182） | `\| review: on` | `· review` |
| `--verify-findings` | `verify_findings` | review-gate に所見の敵対的検証（`finding-verifier` による反証段）を挿入する（`patterns/review-gate`「敵対的検証」） | — | `· verify-findings` |
| `--capture` | `capture` | RUN 後の capture 提案を承認ダイアログなしで自動実行する（提案表示と事後報告は省略しない・§7.3）（#184） | `\| capture: on` | `· capture` |
| `--no-capture` | `no_capture` | RUN 後の capture 提案を完全に抑止する（提案表示・承認ダイアログともに出さない）。`hotfix`/`debug` など軽量 recipe 向けの anti-flag（#137） | `\| no-capture: on` | `· no-capture` |

**競合規則**：

- `orchestrate: true` と `no_orchestrate: true` が同時に設定されている場合は WARN を出して `no_orchestrate` 優先。`--validate` が矛盾を FAIL として検出する（`facets/instructions/validate` ③）。
- `--capture` と `--no-capture` が同時に有効な場合は `--no-capture` 優先＋`[WARN] --capture と --no-capture が同時指定されています（--no-capture 優先）`（§7.3 整合）。
- `--skip` は `--design`/`--review` 等の明示 ON より後に適用され、**明示スキップが最終的に勝つ**（下記 4.）。

### 3.2 `--orchestrate` の自動有効化

次のいずれかで RESOLVE 時に `--orchestrate` 等価として処理する（明示指定と同じ＝舵を `scripts/orchestrate.py` に渡す）。**engine は不変**で、RUN の駆動だけを決定論ランナーに委譲する。

1. **recipe が `checks:` か `needs:` を宣言**（SKILL.md §3.5）— 「決定論で回す意図のある recipe」＝機械検証や DAG 並列が宣言されていれば自動で orchestrate を通す（`checks` をゲートの一次根拠に・`needs` で step-DAG 並列）。
2. **manifest `default_orchestrate: true`** — プロジェクト全体の既定として全 RUN を orchestrate で回す。

自動有効化時は `--plan` ヘッダを `| orchestrate: auto`（明示時は `| orchestrate: on`）とする。明示 `--no-orchestrate` で個別に無効化できる。単発生成コマンド（`/rig:persona` 等・ループ無し）には作用しない。

## 4. --only / --from / --to / --skip — step スライス（§4.3.1）

step スライスは 2. で確定した **最終 step リスト**（extends 適用後・condition 評価後）に対して適用する。

| flag | 動作 |
|---|---|
| `--only <step-id>` | 指定した step-id **1つだけ**を実行する。他の step はすべてスキップ。 |
| `--from <step-id>` | 指定した step-id から最後の step まで実行する。それ以前の step はスキップ。 |
| `--to <step-id>` | 先頭の step から指定した step-id（含む）まで実行する。それ以降の step はスキップ。`--from` との組み合わせで「A から B まで」の範囲スライス可（例 `--from implement --to verify`）。 |
| `--skip <step-id>` | 指定した step-id を**除外**してフローを継続する。複数指定可（例 `--skip design --skip review`）。size-aware 既定・`--design`/`--review` フラグより後に適用される。 |

**優先順位と競合**：

- `--only` と `--from` は同時指定不可。同時に与えられたら `--only` を優先し `--from` を無視して警告を出す。
- `--only` と `--to` の同時指定は `--only` 優先・`--to` を無視して警告（`--only` が1 step 実行なので `--to` は意味なし）。
- `--only` と `--skip` の同時指定は `--only` 優先・`--skip` を無視して警告（同上）。
- `--skip <step-id>` と `--review`（または `--design`）の同時指定は `--skip` が勝ち、その step は実行されない（明示スキップが明示 ON を上書き）。
- `--from A --to B` で A が B より後に来る step の場合はエラー停止：`[ERROR] --from <A> --to <B>: step 順序が逆です（<A> は <B> より後に定義されています）。実行可能な step-id: <一覧>`。
- スライスは **condition 評価後**のリストに対して行う。`--only design` を指定しても condition により design が OFF ならケース B のエラーになる（`--design` を同時指定すれば condition をパスできる）。

### 4.1 step-id が見つからない場合（#86）

指定した `<step-id>` が最終 step リストに存在しない場合（`--only`/`--from`/`--to`/`--skip` 共通）は**原因に応じて2ケースで報告する**。

**ケース A — step-id が recipe に存在しない（タイポ等）**：`[ERROR]` の直後・「実行可能な step-id:」全一覧の前に、編集距離（Levenshtein）≤ 2 の候補を距離昇順で最大 3 件「もしかして:」行として追加する（#190・2.1 の `--recipe` タイポ提案と同形式・同計算ルール）。候補 0 件なら「もしかして:」行を省略する。実行可能な step-id 一覧（RESOLVE 後の確定全リスト）は変わらず出す。例：`  もしかして: verify`（`verifi` 指定時）。

**ケース B — step-id は recipe に存在するが condition 評価で OFF**：`condition:` 式と有効化フラグのヒントを追加表示する。

```
[ERROR] step `review` が見つかりません。
  reason: condition ("--review または size L+") が現在 OFF です（size が S/M のため）。
  hint:   --review フラグを追加すると有効になります：
          /rig:dev --only review --review
実行可能な step-id: intake, implement, verify, pr, merge
```

### 4.2 `--skip` の WARN（停止しない）

| 条件 | WARN |
|---|---|
| condition-OFF な step を `--skip` 指定 | `[WARN] --skip review: review step はすでに condition-OFF です（--skip は不要）。`（意図は「除外」でありすでに OFF な step を skip するのは無害） |
| `gate: acceptance-gate` を持つ step を `--skip`（#126） | `[WARN] --skip <step-id>: <step-id> step は gate: acceptance-gate を持ちます — 品質収束ループがスキップされます。`（rig の核 determinism-by-gate がサイレントにスキップされることを明示） |

`--autonomous` 時も WARN を省略しない。両方に該当する場合は両 WARN を出す（condition-OFF → acceptance-gate の順）。

### 4.3 `--plan` でのスライス表示（#204）

`--skip` で除外される step は**全 step を表に出したまま** condition 列に `[SKIP: --skip flag]` 注記を付す。一方 `--from`/`--to`/`--only` で除外される step は**表から行ごと除外**し（`slice:` ヘッダで範囲を示す）、condition 列への注記は付さない。両者は「範囲外の行を隠す」と「全行を見せたまま除外行を明示する」という異なる表示モデルであり、`--from`/`--to`/`--only` に `[SKIP: … 範囲外]` 相当の注記は追加しない（詳細は `facets/instructions/plan` が正本）。

## 5. --save-recipe — 合成結果の保存（§4.3.2）

`--save-recipe <name>` が指定された場合、RESOLVE で確定した step リスト（extends 適用後・flag override 後の最終状態）を YAML frontmatter + Markdown で生成し、ファイルに書き出す。

| オプション組み合わせ | 書き出し先 |
|---|---|
| `--save-recipe <name>` | `<repo>/.claude/rig/recipes/<name>.md`（project 層） |
| `--save-recipe <name> --user` | `~/.claude/rig/recipes/<name>.md`（user 層） |

### 5.1 保存する内容

- `scope` キーは保存先 tier に応じて `project` / `user` に自動セットする。
- **boolean フラグ**は上記 3.1 の一般則どおり保存する（指定されたフラグを対応キーとして書き出す。`autonomy` だけは常に明示保存）。
- **`--persona` 指定分（#57）**：起動時に `--persona <name>` が指定されていた場合、reviewer fan-out を行う step（`pattern: parallel-fanout` かつ `personas[]` を持つ step）の `personas[]` に各 `<name>` を追加する（名前で dedup）。指定なしなら `personas[]` は変更しない（後方互換）。`--plan --save-recipe` のドライラン表示では保存後の `personas[]` が確認できる。`--persona` 保存＝足す側／`no_default_personas` 保存＝manifest 由来を外す側の両保存で、`--plan` の personas 列と実行時 reviewer が一致する。
  - `cross_llm: true` 再利用時は `cross-llm-reviewer` が RESOLVE で自動追加されるため `personas[]` への直接書き込みは redundant になるが、後方互換のため維持する。
- **`description` 自動生成規則（#47）**：recipe スキーマ（SKILL.md §3.5）では `description` は必須。`--save-recipe` はベース recipe 名と有効フラグから自動生成する：`"<ベース recipe 名> のカスタマイズ（<有効フラグ列挙>）"`（例 `"release-flow のカスタマイズ（--review --tdd）"`）。対話合成（ad-hoc）の場合は `"カスタム recipe（<有効フラグ列挙>）"`。`--autonomous` が付いていても確認ダイアログは出さず自動生成のみ適用する。
  - **`--description "<text>"` 指定時（#163）**：`description` を自動生成の代わりに指定テキストで設定する（なしなら従来の自動生成＝後方互換）。`--save-recipe` なしで `--description` のみ指定した場合は `[WARN] --description は --save-recipe と組み合わせて使用してください（無視します）` を出して無視する。`--autonomous` 時も確認なしで指定テキストをそのまま使う。
  - `--plan --save-recipe` のドライランでは `save-recipe:` ヘッダ行に生成される `description` を付記して書き込み前に確認できるようにする（例 `save-recipe: nightly-review → /.claude/rig/recipes/nightly-review.md [project] — "夜間の CI 確認後に回す 3-way レビュー専用フロー"`）。

### 5.2 snapshot 意味論——保存されないもの

**`--save-recipe` は「このフローが持つ steps の全量」を保存する。実行時フィルタと継承は保存に反映しない。**

| 対象 | 挙動 | 理由 |
|---|---|---|
| `extends`（#34） | **保存ファイルに `extends` を含めない。** extends 解決済みの完全展開 steps を保存する | 将来の親 recipe 変更が静かに波及しない＝再現性を保証。`extends:` を明示利用した継承 recipe を新規作成したい場合は `--save-recipe` を使わず手動で `extends:` を記述する |
| `--from`/`--to`/`--only`（#37, #141） | 保存 step リストに影響しない。`--from implement --to verify --save-recipe my-flow` でも intake を含む**全 steps** が保存される | 実行時フィルタ（今回の RUN でどれを実行するか）であり recipe 定義ではない。後で `--recipe my-flow` を実行すれば全工程を再現できる（保存→一覧→再利用の輪が断たれない）。`--plan --save-recipe` の `save-recipe:` ヘッダが表示する step 数もスライス前の全量 |
| `--skip` | 同上（実行時フィルタ） | step を永続除外するには `extends` + `remove: true` を使う |
| `--budget` | 同上（実行時フィルタ・§4.4） | 予算は run ごとの支出上限であり recipe 定義ではない |

**スライス／`--skip` と `--save-recipe` の同時指定 WARN**（保存完了後に出す。`--autonomous` 時も省略しない＝step ゲートなし実行時こそ情報が重要）：

- `--skip`（#187）：`[WARN] --skip <id> は --save-recipe に反映されません（実行時フィルタ）。step を永続除外するには extends + remove: true を使用してください。保存した recipe には <id> を含む全ステップが含まれます。` 複数指定は step-id をまとめて列挙する（例 `--skip design, verify は…`）。
- `--from`/`--to`/`--only`（#192）：`[WARN] --from <step> は --save-recipe に反映されません（実行時フィルタ）。保存した recipe には <スライスで除外される step 一覧> を含む全ステップが含まれます（スライス前の全量）。step を恒久除外するには extends + remove: true を使用してください。` `--to`/`--only` も同形式。
- 両方指定されていれば**両 WARN を出す**（独立）。ただし `--only` と `--skip` の同時指定時は既存 WARN（`--only` 優先・`--skip` 無視）が先行し、`--skip` の本 WARN は出さない。

### 5.3 上書きと shadow

- 同名ファイルが既に存在する場合は**上書き前に確認**を取る（`--autonomous` 時は確認なしで上書き）。
- **lower-tier shadow チェック（#15・上書き確認より先に実行）**：保存先より**下位の tier**（project 保存なら user→shipped、user 保存なら shipped）に同名 recipe があるか 2.1 の検索順で確認する。あれば保存前に **WARN**（shadow 元の tier とパスを明示し「shadow 後は元 recipe の更新が自動適用されなくなる」と添える）。`--autonomous` 時はダイアログを省略し WARN のみ表示して続行。下位 tier に同名が無ければ WARN なし。`extends:` を使った意図的 shadow の場合は「`extends:` で継承するレシピか確認を」と1文付記する（丸ごと差し替えか継承かの気づきを促す）。
- `--save-recipe` は実行フローを止めない。保存後そのまま RUN を継続する。ただし `--plan` と同時指定された場合は COMPOSE 完了時点で保存し、ハーネスを提示して停止（RUN なし）。
