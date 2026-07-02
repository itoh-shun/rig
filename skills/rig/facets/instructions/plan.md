# instruction: plan

**`--plan` の表示仕様の正本。** `--plan` は COMPOSE で停止し、合成ハーネスを以下の正準フォーマットで提示する（RUN しない）。SKILL.md §5 は要約とポインタのみを持ち、ヘッダ・step テーブル・Gate/Checks/DAG/Knowledge/Reviewer Fan-out/Loop Config 各ブロックの詳細ルールはこのファイルが正本。`--plan` 実行時は必ずこれを読んで従う。

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
