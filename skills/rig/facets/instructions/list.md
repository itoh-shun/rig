# instruction: list

**`--list` の表示仕様の正本。** `--list` はブリック目録（SKILL.md §2）・flag 一覧に加え、recipe を全 tier 走査して以下の仕様でグルーピング表示し、**停止**する（RESOLVE/COMPOSE/RUN しない・副作用なし）。SKILL.md §3 は要約とポインタのみを持ち、表示ルールの詳細（バッジ導出・`steps:` フィールド・tier グルーピング）はこのファイルが正本。

> **一次実装はコード**：badge 固定順・`steps:` フィールド・`extends` マージ（`remove`/origin）の導出は `scripts/orchestrate.py plan <recipe> --json`（`resolve_plan_json`）が機械実装しており、**`--list` の各 recipe 行を作るときはこの出力をデータ源として使う**（スクリプトを呼べない環境のみ本ファイルの規則で自力導出）。`selftest` シナリオ Q が golden 検証。散文とコードが食い違ったら**コード側の selftest を先に直し、本ファイルを追随させる**。

## tier グルーピングと recipe エントリ

recipe を全 tier 走査（§4.2.1 と同じ project → user → shipped 順）して tier 別にグルーピング表示する。**shipped tier の recipe は §2 pack 定義に従い `#### <pack>` サブ見出しでグループ化する（#99）**：`dev（core）`（review-only / release-flow / design-first / hotfix / debug / adversarial-review）、`goal`、`pr-review`、`de-ai-smell`、`sns-x`、`magi`、`humor`（roast / coin / duck / pre-mortem）、`sales`（deal-review / sales-enablement）、`release-movie`、`scenario`、`design`（design / design-audit）。project / user tier の recipe はパック分類なしでフラット表示する（tier だけで十分に絞り込めるため）。各 recipe は frontmatter の `name` / `description` を出す。

- **`extends` を持つ recipe は `extends: <親名> [tier]` を併記する**（`extends` 無しは省略。親が解決できない場合は `[WARN: 親未解決]` を付す）（#53）。
- manifest に `default_recipe` が設定されているとき、一致する recipe エントリに **`★ default` を付記する**（manifest なし・`default_recipe: "interactive"` の場合はマーカーなし。`default_recipe` が未解決なら `★ default (WARN: 未解決)` を付す。`--list --global` 実行時も同様）（#55）。
- project / user の recipe は同名 shipped を shadow する旨を明示する（`--save-recipe` で保存したものがここで発見できる＝保存→一覧→再利用の輪を閉じる）。
- tier ディレクトリが無い／`.md` が無ければその節は**サイレントに省略**（空見出しを出さない＝project/user が無ければ従来どおり shipped のみ）。
- **`--global` 併用時**は recipe 以外の全ブリック（persona・wiki 等）も横断し、レジストリ地図（`facets/instructions/catalog`）を提示。

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

## `[N steps · …]` badge の導出

各 recipe エントリの `[N step(s) · interactive|autonomous]` は frontmatter の `steps[]` 要素数と `autonomy` 値から派生する（N=1 のみ `1 step`、以降 `N steps`）。**ただし `extends` を持つ recipe は RESOLVE 後の確定 step 数（親 step のマージ・`remove: true` 除外後の全量）を N に使う**（`steps:` フィールドの計算規則と同じ。frontmatter のデルタ件数ではなく実行時の全量で表示することで、`[N steps]` badge と `steps:` フィールドの step 数が常に一致する）（#166）。**非デフォルト属性は `·` 区切りで追記する**（デフォルト値は省略し、一覧を読みやすく保つ）：

- **`· tdd`**（#62）：recipe に `tdd: true` が設定されている場合のみ付記。`--save-recipe --tdd` で保存した recipe が TDD モードで動くことを一覧で確認できる。省略時（`tdd: false`・未設定）は付記なし。
- **`· gated`**（#66）：`gate: acceptance-gate` を持つ step が1つ以上ある recipe に付記。rig の核心 **determinism-by-gate**（品質収束保証）の有無を一覧で確認できる。acceptance-gate を持つ step が1つもない recipe は付記なし。
- **`· workflow`**（#60）：recipe に `backend: workflow` が設定されている場合のみ付記。`--save-recipe --workflow` で保存した recipe が Workflow バックエンドで動くことを一覧で確認できる。省略時（`manual`・未設定）は付記なし。
- **`· no-defaults`**（#70, #128）：recipe に `no_default_personas: true` が設定されている場合のみ付記。`--save-recipe --no-default-personas` で保存した recipe が manifest `default_personas` の自動投入を抑止することを一覧で確認できる。省略時（`false`・未設定）は付記なし。
- **`· orchestrate`**（#129）：recipe に `orchestrate: true` が設定されている場合のみ付記。`--save-recipe --orchestrate` で保存した recipe が計算的オーケストレーションモード（`scripts/orchestrate.py` 決定論ランナー）で動くことを一覧で確認できる。省略時（`false`・未設定）は付記なし。
- **`· orchestrate(auto)`**（#208）：`orchestrate: true` は設定されていないが、いずれかの step に `checks:` または `needs:` の宣言がある recipe に付記する（§4.3 の自動有効化条件・`--plan` ヘッダの `| orchestrate: auto` と同じ判定）。`orchestrate: true` が設定されている recipe は（`checks:`/`needs:` の有無に関わらず）`· orchestrate` のみを付け、`· orchestrate(auto)` は重複して付けない。両方とも無ければどちらの badge も付けない。`--list` の badge 並べ順では `· orchestrate` と同じ位置に置く（同一 recipe では排他）。
- **`· cross-llm`**（#130）：recipe に `cross_llm: true` が設定されている場合のみ付記。`--save-recipe --cross-llm` で保存した recipe が他社 LLM レビュー前提モード（① implement への `cross-llm-legibility` ポリシー注入 + ② review fan-out への `cross-llm-reviewer` 追加）で動くことを一覧で確認できる。省略時（`false`・未設定）は付記なし。
- **`· no-capture`**（#137）：recipe に `no_capture: true` が設定されている場合のみ付記。`--save-recipe --no-capture` で保存した recipe が RUN 後の capture 提案を抑止することを一覧で確認できる。省略時（`false`・未設定）は付記なし。
- **`· adversarial`**（#172）：recipe に `adversarial: true` が設定されている場合のみ付記。`--save-recipe --adversarial` で保存した recipe が敵対レビューステップを自動追加することを一覧で確認できる。省略時（`false`・未設定）は付記なし。
- **`· visual`**（#174）：recipe に `visual: true` が設定されている場合のみ付記。`--save-recipe --visual` で保存した recipe が verify ステップで UI 視覚確認を強制することを一覧で確認できる。省略時（`false`・未設定）は付記なし。
- **`· autonomous`**（#181）：recipe に `autonomy: autonomous` が設定されている場合のみ付記。`--save-recipe --autonomous` で保存した recipe が step ゲートなしで自律実行することを一覧で確認できる。省略時（`interactive`・未設定）は付記なし（interactive はデフォルト値のため非表示）。
- **`· no-orchestrate`**（#178）：recipe に `no_orchestrate: true` が設定されている場合のみ付記。`--save-recipe --no-orchestrate` で保存した recipe が orchestrate の自動有効化を打ち消すことを一覧で確認できる。省略時（`false`・未設定）は付記なし。
- **`· design`**（#182）：recipe に `design: true` が設定されている場合のみ付記。`--save-recipe --design` で保存した recipe が design step を size 非依存で常時 ON にすることを一覧で確認できる。省略時（`false`・未設定）は付記なし。
- **`· review`**（#182）：recipe に `review: true` が設定されている場合のみ付記。`--save-recipe --review` で保存した recipe が review step を size 非依存で常時 ON にすることを一覧で確認できる。省略時（`false`・未設定）は付記なし。
- **`· verify-findings`**：recipe に `verify_findings: true` が設定されている場合のみ付記。`--save-recipe --verify-findings` で保存した recipe が所見の敵対的検証（`patterns/review-gate`）つきで動くことを一覧で確認できる。省略時（`false`・未設定）は付記なし。
- **`· capture`**（#184）：recipe に `capture: true` が設定されている場合のみ付記。`--save-recipe --capture` で保存した recipe が RUN 後の capture 提案を承認ダイアログなしで自動実行することを一覧で確認できる。省略時（`false`・未設定）は付記なし。

並べ順は **`tdd` → `gated` → `workflow` → `no-defaults` → `orchestrate`（または `orchestrate(auto)`） → `cross-llm` → `no-capture` → `adversarial` → `visual` → `autonomous` → `no-orchestrate` → `design` → `review` → `capture` → `verify-findings`** の固定順（`orchestrate`/`orchestrate(auto)` は同一スロット・排他）。複数共存例：`[3 steps · interactive · tdd · gated]`。`extends` で継承した recipe も RESOLVE 後の確定値（継承分を含む）を評価する。`/rig:catalog`（`--list --global`）の recipe 一覧行にも同じメタデータ・同じ表示ルールを適用する。

## `steps:` フィールド（step ID 列・#79, #160）

各 recipe エントリに `steps[].id` を順に列挙した `steps: <id1>, <id2>, ...` フィールドを **badge の直後・`extends:` の前**に追加する。`condition:` フィールドを持つ step（size-aware・flag 条件付き）の id には `?[<条件略記>]` を付す（#160）。条件略記は recipe frontmatter の `condition:` 値から取得する（フラグ部と size 部を `|` 区切りで整理し、20文字以内の短い文字列に略記する。例：`"--design または size L+"` → `[--design|L+]`）。`condition:` を持たない step の id に `?` や略記は付かない。例：`steps: intake, design?[--design|L+], implement, verify, review?[--review|L+], pr, merge`。このフィールドは `description` とは独立して常に表示する（description が step 情報を含む場合も重複して表示する：description は自由テキストだが `steps:` は計算フィールドであり `--only`/`--from`/`--skip` に渡す step-id の信頼できる一覧）。`extends` で継承した recipe は RESOLVE 後の確定 step リスト（継承分を含む）を表示する。`--list --global` / `/rig:catalog` でも同様に表示する。
