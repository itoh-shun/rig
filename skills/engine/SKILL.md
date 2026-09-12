---
name: engine
description: Use when you need dev-flow orchestration in Codex or Claude Code — implementing a feature, clearing an issue, reviewing current changes, completing a PR, going design-to-implementation, TDD, quality-gated workbench runs, or composing a flow. 開発フローのオーケストレーション（Codex / Claude Code での実装着手 / Issue 対応 / 変更レビュー / PR 完了 / 設計→実装 / TDD / workbench / フロー組み立て）が要るとき、または `$rig` / `/rig:go`（互換エイリアス `/rig:rig`） / `/rig:dev` が呼ばれたとき。
---

# rig

<NON-INTERACTIVE-STOP>
非対話の実行——`codex exec` / `claude -p` / CI ジョブ / `orchestrate.py` の provider 呼び出し、
あるいは stdout を読んで答える人間が接続していないあらゆる起動——では、会話フロー `rig:talk`
は適用されない。**実行前の確認を求めてはならない。** 答える相手がいないため、確認を求めた時点で
run は何も行わないまま終わる。与えられたタスクを、それが名指す範囲の中で実行し、変更した内容を
報告すること。Task/Agent で特定タスクのために起動されたサブエージェントも同じ。

`talk-loop` の確認ルールは、あなたを起動した**対話型オーケストレーターの層**を統べるもので、
その層で既に満たされている。実行層で繰り返すと run が停止する。これは権限の拡大ではない——
タスクが名指していない作業は、従来どおり実行せずに報告して止まること。
</NON-INTERACTIVE-STOP>

## 1. Overview

### 入口——まず、やりたいことを言う

rig の入口は1文である。**`/rig:go "<やりたいこと>"`**（Codex では `$rig "<やりたいこと>"`）。
会話からの起動が base であり、CLI はその下にある実行系であって入口ではない。

自然文を受け取ったら、まずこの経路に載せる。**`rig-wb` のインストールや `/rig:setup` を
先に要求してはならない**——CLI が要るのは Claude Code の外（CI・スクリプト・別アシスタント）から
同じ recipe とゲートに届く必要が出たときであって、最初の1回ではない。manifest
（`.claude/rig.md`・`/rig:init`）と pack 信頼の承認も同じく後から足す opt-in である。

最初の1回に要るのは **git リポジトリであることと `python3` があること**の2つだけ。これは主張では
なく測定で、`tests/test_first_run_cost.py` が固定している：素の `git init` リポジトリ
（manifest なし・PATH に `rig-wb` なし・`RIG_ALLOW_*` なし・stdin は `/dev/null`）で
`python3 scripts/workbench.py new "<task>" --type bugfix` が exit 0 を返し、人に何も聞かず、
run をディスクに残すこと。併せて、既定 `bugfix` ルートが同梱 `core` tier で解決すること
（承認すべき pack 信頼が無い）・`hostcheck` が助言的でゲートではないこと・未承認 manifest が
警告1行に縮退することも固定されている。**測っているのは run を作るコマンドであって、
後続の全 step ではない**——後段が別のものを要求することはあり得る。

### 仕組み

ブリック（facet / pattern / step / agent / recipe）を**起動時に組み合わせて**タスク専用のエージェント・ハーネスを engineering する、レゴ式ハーネス・コンポーザ。固定ワークフローではなく **PARSE → RESOLVE → COMPOSE → RUN** の4段で都度ハーネスを合成する。intake→design→implement→verify→review→pr→merge の「3-Stage フルフロー」は数ある recipe の1つにすぎない。

**determinism-by-gate**: 非決定的な agent 実行を決定的な受け入れゲート（`patterns/acceptance-gate`）で挟み、経路は変動しても**毎回同じ品質**へ収束させる。これが rig の品質保証の核。

### Codex 入口

Codex では `$rig` が Claude Code の `/rig:go` に相当する入口。slash command や `Agent` ツールが無い環境では、本文中の `/rig:*` は「この skill の該当 pack / instruction を使う」と読み替え、subagent dispatch は Codex の並列作業・`codex exec` provider・または `scripts/orchestrate.py` / `scripts/workbench.py` の決定論 runner で代替する。

- 自然文タスク: `$rig "fix the login bug"` として扱い、§2 の workbench pack と `facets/instructions/workbench` を読む。
- 低レベル指定: `$rig --recipe review-only --only review` のように、§3 の flag / recipe 解決規則をそのまま使う。
- runner を使える場合: `python3 scripts/orchestrate.py ...` または `rig-wb ...` を優先し、Codex verifier は `codex exec --sandbox read-only` で読み取り専用にする（`scripts/orchestrate.py build_argv` が正本）。
- Claude Code 固有の文言（`/rig:*`, `Agent`, `Task`, plugin command）は互換表現として扱い、Codex で同じ安全条件（隔離 worktree・acceptance-gate・明示 accept/discard）を満たす形に置き換える。

## 2. ブリック目録

**目録の正本は `BRICKS.md`。** engine / dev-core 在庫と pack 追加分のブリック名・1行要旨・
Extension Catalog はすべてそこにある。ブリックを引くとき・足すときに読む。

## 3. PARSE — 起動文字列の解釈

起動文字列（`$ARGUMENTS`）を **flag** と **自由記述**（レビュー対象・Issue 内容など）に分解する。

### flag 一覧

| flag | 意味 |
|---|---|
| `--issue <id>` | 対象 Issue を指定（intake の入力） |
| `--design` / `--review` | 該当 step を size 非依存で常時 ON にする |
| `--visual` | verify を `visual-verify`（スクリーンショット等の視覚確認）へ委譲する |
| `--tdd` | implement を TDD（red-green-refactor）で行う |
| `--autonomous` | step ゲートを省き自律実行（既定は各 step で確認＝step ゲート ON）。acceptance-gate は維持（§4.5） |
| `--compose` | RUN 前に5軸（RECIPE / STEP / GATE / BACKEND / MODE）を一度に選ぶ。候補・推薦・根拠は `rig-wb wb compose-options` が返し、提示後は `--plan` 正準形式で確認する。`--autonomous` 併用時は対話せず無視 |
| `--plan` | COMPOSE まで実行し、合成ハーネスを人間可読で提示して**停止**（実行しない）（§5） |
| `--save-plan <path>` | `--plan` と併用し、同一内容を `<path>` にも Markdown で書き出す。`--plan` なしなら `[WARN] --save-plan は --plan と組み合わせて使用してください（無視します）` を出して無視。既存ファイルは上書き確認あり（`--autonomous` 時は自動上書き） |
| `--only <step>` / `--from <step>` / `--to <step>` | 実行範囲のスライス（1つだけ / ここから最後まで / 先頭からここまで）。`--from A --to B` で範囲指定（§4.3.1） |
| `--skip <step>` | 指定 step を除外して継続（複数可）。明示 ON より後に適用＝**明示スキップが勝つ**。`--only` 優先・`--save-recipe` には影響しない（§4.3.1） |
| `--recipe <name>` | recipe を名前で指定（project → user → shipped の順で解決・§4.2） |
| `--save-recipe <name>` | 今回合成したハーネスを recipe として保存。既定は project 層、`--user` 併用で user 層（§4.3.2） |
| `--description "<text>"` | `--save-recipe` と併用し保存 recipe の `description` を指定テキストにする。単独指定は `[WARN] --description は --save-recipe と組み合わせて使用してください（無視します）` |
| `--workflow` | 実行バックエンドを **workflow**（ultracode Workflow ツール）に切り替える。既定は **manual**（`patterns/workflow-backend`） |
| `--orchestrate` | **計算的オーケストレーション**を ON＝step 遷移・ゲート判定・リトライ・停止条件・状態保持を散文でなく `scripts/orchestrate.py`（決定論ランナー）に強制させる。半自動＝`init`→`next`/`check`/`verdict`（`acceptance:` 宣言時の verdict は `--criterion N=PASS|FAIL|UNKNOWN` を反復。無回答 PASS は未回答）、長い中断からは `resume`＝verify-first 再開。全自動＝`run`（各 step を別プロセス・マルチプロバイダで実行し検証は別プロバイダ＝構造的に採点者≠生成者）。**自動 ON の条件は §4.3**。`patterns/computational-orchestration` が正本 |
| `--no-orchestrate` | 自動有効化を**この run だけ打ち消す**＝従来の散文エンジンで回す |
| （横断 CLI） | `orchestrate install-shim` で `~/.local/bin/rig` を 1 回張れば任意 cwd から `rig <subcommand>` で起動できる。`$RIG_HOME` 上書き可、`<cwd>/.rig/recipes/<name>.md` が同名 built-in を**プロジェクト overlay**として上書き、`checks:` の実行 cwd は呼び出し元（rig リポジトリではない） |
| `--capture` / `--no-capture` | capture を承認ダイアログなしで実行 / 完全にスキップ。同時指定は `--no-capture` 優先＋WARN（§7.3） |
| `--list` | 利用可能なブリック(§2)・**全 tier の recipe**・flag を一覧表示して停止（RESOLVE/COMPOSE/RUN しない） |
| `--validate` | ブリック整合チェック（doctor）。recipe→facet 参照切れ・frontmatter スキーマ逸脱・§2 目録ドリフトを検査してレポートし停止。手順は `facets/instructions/validate` |
| `--adversarial` | 敵対的レビュー step（`adversarial-review` instruction / lazy-senior・cognitive-economist persona / acceptance-gate）を review・verify の後に追加する。recipe `adversarial-review` は敵対レビューのみを回す |
| `--budget <low\|mid>` | **コスト予算による fan-out の間引き**（size-aware の金銭版・§4.4）。`low`＝既定 3-way まで・workflow 禁止、`mid`＝3-way＋選択投入2枠まで。未指定＝制限なし。**実行時フィルタ**＝`--save-recipe` に保存されない。manifest `default_budget` で恒久設定可 |
| `--verify-findings` | **所見の敵対的検証**を review-gate に挿入。REJECT の根拠を1件ずつ `finding-verifier`（反証者・独立 subagent）に渡し、反証された所見（REFUTED）はゲートに通さない（UPHELD/UNRESOLVED は通す＝疑わしきは所見の利）。`patterns/review-gate`「敵対的検証」が正本 |
| `--persona <name>` | review fan-out にカスタム reviewer persona を**この run だけ追加**（複数可）。tier 解決（§5）で名前解決し manifest `default_personas` に**上乗せ**される |
| `--no-default-personas` | この run に限り manifest `default_personas` の自動投入を**抑止**する（組み込み reviewer＋`--persona` 指定分のみ） |
| `--cross-llm` | **他社 LLM レビュー前提モード**。①**書く側**＝implement step に `cross-llm-legibility` ポリシーを注入（慣用的・明示的・文脈非依存に書く規律）②**見る側**＝review fan-out に `cross-llm-reviewer` persona を追加。該当 step が無い recipe では対応する側だけがスキップされる（#71・§4.3） |
| `--global` | `--list` / `--validate` のスコープを **tier 横断**（shipped＋user(global)＋project）に広げる。手順は `facets/instructions/catalog` |
| `--ppt` | （design pack）デザインドキュメントを PowerPoint としても出力（`powerpoint-server` MCP）。既定 Markdown に追加・併用可 |
| `--claudedesign` | （design pack）claude.ai デザイン機能（`claude_design` MCP）でも生成。MCP 未接続時は報告して Markdown のみ続行 |
| `--url <url>` | （design pack）監査モードを明示。実装画面を Playwright で取得し UI/UX・a11y を採点（bare な URL 引数でも自動検出） |
| `--a11y-level <A\|AA\|AAA>` | （design pack）目標 WCAG レベル（既定 AA）。未達違反は検閲で重大度を上げる |

> **フラグと recipe キーの等価**：`--tdd` / `--design` / `--review` / `--visual` / `--adversarial` / `--cross-llm` / `--orchestrate` / `--no-orchestrate` / `--capture` / `--no-capture` / `--verify-findings` / `--no-default-personas` / `--workflow` / `--autonomous` は、それぞれ同名の recipe キー（§3.5）と等価であり `--save-recipe` で保存される（§4.3）。**各フラグの効果詳細・競合規則の正本は `facets/instructions/resolve` 3.1**。

**`--list` 指定時** → §2 のブリック目録・flag 一覧に加え、recipe を全 tier 走査（§4.2 と同じ project → user → shipped 順）して tier 別・pack 別にグルーピング表示し、**停止**（解決も実行もしない）。各エントリは `name [N steps · badge…]  steps: <id列>  extends: <親 [tier]>  — description` 形式。**表示仕様の正本は `facets/instructions/list`**（tier/pack グルーピング・badge の導出と固定並び順・`steps:` フィールド・`★ default` マーカー・shadow 表示・出力例）— `--list` 実行時は必ずこれを読んで従う。**`--global` 併用時**は recipe 以外の全ブリック（persona・wiki 等）も横断し、レジストリ地図（`facets/instructions/catalog`）を提示。

**`--validate` 指定時** → ブリック整合チェック（doctor）。結果を提示して**停止**（`--list` と同じく副作用なしの点検モード）。**検査項目・severity・エラーフォーマットの正本は `facets/instructions/validate`**（① recipe→facet 参照切れ／② manifest 参照・値検証／③ frontmatter スキーマ／③-b persona スキーマ／④ §2 目録ドリフト／⑤ wiki 衛生／⑥ ai-quirks 二相ペア〔--global〕／⑦ accumulated スキーマ）— `--validate` 実行時は必ずこれを読んで従う。CI 用サブセットは `scripts/validate.py`。**`--global` 併用時**は tier 横断で点検する（全 tier の orphan・リンク切れ・参照欠落・重複）。

### `--compose` / 引数なし / 曖昧な場合 → 対話 composition

`--autonomous` でなければ `facets/instructions/compose` に従う。task_type 確定後、
`rig-wb wb compose-options --type <task_type> [--diff <n>] --json` が返す5軸の候補・推薦・根拠を
一度に提示して選ばせる。instruction facet は判定を再実装しない。合成結果は
`facets/instructions/plan` の `--plan` 正準形式で提示し、確認後 RUN する。

## 3.5. Recipe スキーマ（正規定義）

**スキーマの正本は `RECIPE-SCHEMA.md`。** recipe frontmatter のトップレベルキーと step
オブジェクトのキーの全量がそこにある。recipe を書く・読む・`extends` を辿るときに読む。

## 4. RESOLVE — 解決順（manifest＋recipe＋flag＋size-aware 既定）

最終ハーネスを **manifest → recipe → flag → size-aware 既定** の順で確定する。後の段が前の段を
override する。**各段の規定の正本は `RESOLVE.md`。** manifest ロード・recipe の tier 検索と
`extends` を持つ。flag override・size-aware 既定・autonomy も同じファイル。RESOLVE に入るときに読む。

## 5. COMPOSE — ハーネス合成

RESOLVE で確定した各 step を `step ＋ pattern ＋ facet ＋ native 委譲先` として subagent prompt に
組み上げる。**合成規則の正本は `COMPOSE.md`。** facet 配置順・知識層の注入・native 委譲を持つ。
persona facet の tier 解決・`default_personas` の自動投入・`--plan` の停止も同じファイル。
合成に入るときに読む。

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

**計測（`workbench.py context`）** — この規律はこれまで**一度も計測されていなかった**。`harness-taxonomy` が名指しする穴のうち2つ（散文で止まる強制・計測なしで出荷された規則）を同時に踏んでいる状態であり、誰も数えない規律は誰にも気づかれずに劣化する。rig が印字した1バイトは tool result として親コンテキストに戻るため、**rig の stdout こそが rig の親コンテキスト消費そのもの**である。これは rig が責任を負い、かつ観測できる唯一の部分なので、そこだけを invocation 単位で `.rig/context.jsonl` に記録する（`RIG_NO_CONTEXT_METER=1` で無効化）。

計測**しない**もの——セッション全体の context、会話、親が自分で読んだファイル、そして「親が本当に subagent に dispatch したか」——はレポート自身に明記される。rig は subprocess であり、それらは見えない。「あなたの context 使用量」を名乗る数字は捏造になるが、「rig があなたに向けて印字した量」は検証可能で、しかも rig が動かせるレバーそのものである。出力を増やす変更（step バナー・flow map）は、この数字が動くことを前提に予算を決める。

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

### フロー完了レポート と 実行テレメトリ

全 step が完了（または escalation/skip で終了）した後、フロー全体のサマリを正準フォーマットで出力し、**同じサマリを1行 JSON として `<cwd>/.rig/runs.jsonl` に追記**する（orchestrate バックエンドは `telemetry_append` が自動追記するため、**manual / workflow バックエンドの RUN のみ**この規則で追記する）。`autonomy: autonomous` では完了レポートが**必須**（step ゲートがなくフローが一気に走るため、事後確認できる唯一の集約情報）。`interactive` でも同じフォーマットで集約サマリを出す。

```
## rig フロー完了

recipe: <name[tier]> | autonomy: <interactive|autonomous> | backend: <manual|workflow>[| <モード修飾子…>]
[slice: <範囲>] [skip: <step-id(s)>]
steps: <N> 完了 / <M> スキップ / <K> エスカレーション

| step | outcome | gate |
```

**フィールド定義・モード修飾子の条件・スライス/`--skip` 時の変形・テレメトリ JSON のスキーマ（`failure_mode` の型付けを含む）の正本は `facets/instructions/run-report`** — RUN を締めるときは必ずこれを読んで従う。テレメトリは capture（§7）ではなく run-state.json と同格の実行ログなので**承認不要**（`--no-capture` の影響も受けない・`.rig/` は gitignore 済み）。集計は `orchestrate runs [--limit N] [--recipe R]`、検証者別の票と剪定ヒントは `runs --personas`。書き込めない環境ではサイレントにスキップし、レポート自体は通常どおり出す（best-effort）。

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
               既存の先頭: "# ai-quirk: <name>\
何が起きたか..."
               ~/.claude/rig/knowledge/ai-quirks/<name>-policy.md（新規）
- 内容草案: ...（記述形：何が起きたか / 規範形：次回 prompt に注入するルール）

### [2] pitfall — <落とし穴の短い名前>
- 書き込み先: <repo>/.claude/rig/knowledge/accumulated/<name>.md（新規）
               ~/.claude/projects/<proj>/memory/<name>.md（既存・上書き 2026-06-18）
               既存の先頭: "# pitfall: <name>\
前回の学び..."
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
- **ホスト組み込み（Claude Code built-in）の skill も在庫に含める**：セッションが `/code-review`・`/security-review`・`/verify` 等を公開していれば、rig の対応フローが**補助レーン**として使う（review 系は `parallel-review` ②のネイティブ・レーン、verify は ②-b）。2つの規律を必ず守る——(1) **測定に服させる**：ネイティブ skill の票も persona 名（`native-code-review` 等）で記録し、stats/drill の計測対象にする（測れないレビュアーを使わない）。(2) **代替にしない**：セッションと同じモデルで走るため、独立検証（採点者≠生成者）の主クォーラムは persona/クロスプロバイダ側に残す。headless 実行には組み込み skill が無いので黙って省く（構造は不変）。
- **エージェント型も同様**：read-only のコードベース探索 dispatch は、ホストが **Explore エージェント型**を公開していればそれを使う（構造的に書き込み不能＝調査段階として安全・汎用 subagent より速く安い。`intake` ①参照）。無ければ通常の subagent へフォールバック。
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
| RESOLVE を自力で回す（manifest キー・tier 検索・extends・flag⇔キー等価・スライス・save-recipe） | `facets/instructions/resolve` |
| RUN を締める（フロー完了レポート・`.rig/runs.jsonl` テレメトリの出力仕様） | `facets/instructions/run-report` |
| ブリックを引く・足す（engine / dev-core 在庫と pack 追加分の全量） | `BRICKS.md` |
| recipe frontmatter のキーを確認する（トップレベル／step） | `RECIPE-SCHEMA.md` |
| 解決順の規定を確認する（manifest・tier 検索・flag override・size-aware） | `RESOLVE.md` |
| 合成規則を確認する（facet 配置順・知識層の注入・persona tier） | `COMPOSE.md` |
| pack が何をするか調べる（追加ブリックの詳細説明） | `PACKS.md` |
| `/rig:go "<task>"` 統一入口を駆動する（分類・recipe 自動選択・隔離 worktree RUN・gate 判定） | `facets/instructions/workbench` ＋ `patterns/isolated-worktree` |
| `/rig:go status`\|`diff`\|`accept`\|`discard`\|`log`\|`board`\|`stats`\|`review`\|`gc`\|`audit`\|`scan-secrets`\|`scan-injection`\|`digest`\|`stream-checks`\|`stale-refs`\|`scan-destructive`\|`scan-anchors`\|`instincts` を実行する | `facets/instructions/workbench-ops` ＋ `scripts/workbench.py` |
| 複数タスクを並行で進める（ターミナルを増やさず一括把握） | `/rig:queue add`→`go --provider rig`（`patterns/isolated-worktree` で自動隔離）＋ `/rig:go board`（単一ダッシュボード） |
| 視覚検証（スクリーンショット等）の置き場・処分ルールを確認する | `patterns/visual-artifacts` ＋ `scripts/workbench.py gc` |
| `/rig:go gh issue`\|`pr review`\|`pr fix`\|`ci` を実行する | `facets/instructions/gh-flow` |
| acceptance-gate の基準 ID・プリセット定義の正本を確認する | `scripts/workbench.py gates`（`standard`/`implementation`/`review`/`security`。project 独自基準は `.rig/gates.json`＝加算のみ） |
