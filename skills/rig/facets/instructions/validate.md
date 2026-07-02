# instruction: validate

ブリック整合チェック（doctor）。rig は全ブリックが markdown のため、参照切れ・スキーマ逸脱・目録ドリフトが静かに混入しうる。`--validate` 指定時にこの手順で機械的に点検し、結果を提示して**停止**する（RESOLVE/COMPOSE/RUN しない・副作用なし）。

実作業（ファイル走査）は `Glob` / `Grep` / `Read` で行い、長文を親 context に引き込まない（合否と該当箇所だけ集約する）。

## チェック項目

### ① recipe → facet 参照切れ

各 `recipes/*.md`（shipped＝`skills/rig/recipes/`、project＝`<repo>/.claude/rig/recipes/`、user＝`~/.claude/rig/recipes/`）の frontmatter を読み、各 step が参照する名前が**実ファイルとして存在する**かを照合する。

| キー | 解決先 | 存在チェック |
|---|---|---|
| `instruction` | `facets/instructions/<name>.md` | 必須・1つ |
| `personas[]` | **3 tier 順で解決**（project→user→shipped→agent。`/` 区切りでサブディレクトリ可。例 `sales/hearing-reviewer`） | 各要素 |
| `policies[]` | `facets/policies/<name>.md` | 各要素 |
| `output_contract` | `facets/output-contracts/<name>.md` | 任意・あれば |
| `pattern` / `gate` | `patterns/<name>.md` | 任意・あれば |
| `extends` | 親 recipe（§4.2.1 tier 検索順・bare 名） | あれば |

**`instruction` / `output_contract` / `policies[]` 参照切れの FAIL メッセージ形式（#202）** — 上表の3キーが参照するファイルが存在しない場合は **FAIL**。メッセージには**期待パス**と、**同ディレクトリに実在する候補一覧**（`facets/instructions/` / `facets/output-contracts/` / `facets/policies/` を `Glob` で列挙）を付す（タイポ修正のヒント）。

```
[FAIL] recipe my-flow step review: instruction 'paralel-review' が見つかりません。
  期待パス: skills/rig/facets/instructions/paralel-review.md
  利用可能な instruction: intake, design, implement, verify, visual-verify, pr, merge, parallel-review, ...
```

> **`personas[]` の severity は変更しない（FAIL のまま）**：`personas[]` は project→user→shipped→agent の**4 tier すべて**を検索した上で見つからない場合にのみ FAIL とする（上表の note・§5「persona facet の tier 解決」参照）。これは shipped 層のみで判定していた古い実装が `/rig:persona` 生成のカスタム persona を偽 FAIL させていた問題を4 tier 検索で解消した結果であり（過去の対応）、4 tier とも見つからない参照は実際に壊れているため WARN に緩めない。`instruction`/`output_contract`/`policies[]` は当面 shipped 層のみの判定（§ above note）のままだが、これは「タイポ検出の強さ」を保つ独立した判断であり、personas[] の FAIL 判定条件とは連動しない。

**`extends` 親 recipe 存在チェック（#191）** — `extends:` フィールドを持つ recipe について、§4.2.1 と同じ tier 検索順（project→user→shipped）で親 recipe ファイルを探す。見つからない場合は **FAIL**（`--list` の `[WARN: 親未解決]` と同一条件を `--validate` では FAIL として扱う。静的品質チェックとして厳格にする）。`extends:` を持たない recipe はスキップ。`--validate --global` 時も全 tier 横断で同チェックを実施する。

```
[FAIL] my-flow: extends: "release-flow-v2" が解決できません。
  検索した tier: project (<repo>/.claude/rig/recipes/), user (~/.claude/rig/recipes/), shipped (skills/rig/recipes/)
  ヒント: --list で利用可能な recipe 名を確認してください。タイポの場合は --recipe の「もしかして」候補提案（#188）も参照してください。
```

**`extends` 子 step ID 突き合わせ（#41）** — recipe に `extends: <parent>` が宣言されている場合、以下の追加チェックを行う。

1. 親 recipe を §4.2.1 tier 検索順で解決し、`steps[].id` をリスト化する。
2. 子 `steps[].id` のうち**親リストに存在しない ID** を抽出する。
3. 該当 ID がある場合 → **WARN** を出す。

```
[WARN] my-flow (extends: release-flow) — child step `implementt` は parent に存在しません。
        override のタイポの可能性があります。
        新規 step として追加する意図なら無視してください（SKILL.md §4.2.2）。
```

> **WARN とする理由（FAIL にしない）**：子に意図的な新規 step を追加するケースも §4.2.2 で正当（「子のみに存在する step は親の末尾に追加」）。FAIL にすると正当な extension も通らなくなる。WARN にすることで「気づかせる」だけにとどめ、ユーザーが判断する。`--validate --global` 時は全 tier の `extends` recipe を対象に同チェックを実施する。

**`extends` 多段継承（孫継承）チェック（#42）** — recipe に `extends: <parent>` が宣言されている場合、以下の追加チェックを行う（子 step ID 突き合わせの直後に実施）。

1. 親 recipe を §4.2.1 tier 検索順で解決し、親の frontmatter に `extends:` キーが存在するか確認する。
2. 存在する場合 → **WARN** を出す（RUN 時に §4.2.2 が「親の `extends` を無視し警告ログを出す」と同 severity にそろえる）。

```
[WARN] my-flow (extends: custom-base) — custom-base も extends を持ちます（多段継承 = 孫継承）。
        RUN 時に custom-base の extends が無視されます（SKILL.md §4.2.2）。
        1 段継承に整理するか、継承元の構成を確認してください。
```

> **WARN とする理由（FAIL にしない）**：§4.2.2 は RUN 時も「親の `extends` を無視し警告ログを出す」であり停止しない。`--validate` も同 severity にそろえる。`--validate --global` 時は全 tier の `extends` recipe を対象に同チェックを実施する。

**`extends` 循環参照（サイクル）チェック（#71）** — `A→B→A` のような循環は RESOLVE フェーズでサイレントに無限ループ（ハング）するため、実行前に **FAIL** で止める。#42 の多段（深さ）チェックは各 recipe を単独で見るため循環は検出できない＝**独立した別チェック**。

1. 各 recipe を起点に `extends` 先を **DFS**（深さ優先探索）で辿り、現在の経路（訪問済みセット）に同じ recipe 名が再出現したら → **FAIL**（循環）。
2. `extends` がなくなる、または `extends` 先が解決できない（別チェックで報告済み）まで到達したら循環なし。
3. FAIL メッセージに**循環経路を可視化**する。1 サイクルにつき 1 回だけ報告する。

```
[FAIL] recipe:circular-extends — circular chain: fix → hotfix → fix
```

> 親解決は §4.2.1 tier 検索順（`--validate --global` 時は全 tier 横断、既定は shipped）。`scripts/validate.py` の `check_extends_cycles` が CI 用に同ロジックを実装（shipped tier グラフ）。#42（深さ＝孫継承 WARN）と #71（循環＝サイクル FAIL）はそれぞれ独立して報告する。自己参照（`A→A`）も循環として FAIL。

**`needs:` 参照先 step-id の実在確認（チェック A・#152）** — 各 recipe の全 step について、`needs[]` に列挙された step-id が**同じ recipe の `steps[].id` に実在するか**を照合する。存在しない場合は **FAIL**。`needs:` が宣言されていれば `orchestrate: true` の有無に関わらず常にチェックする（宣言＝整合保証が必要）。`--validate --global` 時も shipped / project / user 全 tier の recipe を対象に同チェックを実施する。

```
[FAIL] recipe my-flow step implement: needs に未定義の step-id "desgin" が含まれます。
       レシピ内の有効な step-id: design, verify, pr
```

**`needs:` グラフの循環依存検出（チェック B・#152）** — `needs:` 宣言から DAG を構築し、DFS で**サイクルを検出**したら **FAIL**。循環経路を可視化する。`check_extends_cycles`（`scripts/validate.py`）と同じロジック・同じ severity。`--validate --global` 時も全 tier の recipe を対象に同チェックを実施する。

```
[FAIL] recipe my-flow: needs 循環依存 — verify → review → verify
```

> `needs:` が宣言されていれば `orchestrate: true` の有無に関わらず常に両チェックを実施する。`remove: true`（#144）で削除した step を `needs:` で参照している場合の WARN（§4.2.2）とは独立した別チェック。`scripts/validate.py` の `check_needs_refs`（参照切れ）と `check_needs_cycles`（サイクル）関数が CI 用に同ロジックを実装（`check_extends_cycles` と対称）。

> **`personas[]` は COMPOSE（§5「persona facet の tier 解決」）と同じ経路で解決する**（shipped 層だけ見ない）。順に：①project `<repo>/.claude/rig/personas/<name>.md` → ②user `~/.claude/rig/personas/<name>.md` → ③shipped `skills/rig/facets/personas/<name>.md` → ④agent `<repo>/agents/<name>.md`。**いずれにも無い場合のみ参照切れ FAIL**。shipped 層だけ見ると `/rig:persona` で project/user に生成したカスタム persona を参照する recipe が**偽 FAIL** する（同 instruction の `instruction`/`policies[]`/`output_contract` は当面 shipped 基準で可・persona ほど tier 運用が一般的でないため）。
> **agent のベースパスはリポジトリルート**（`git rev-parse --show-toplevel` で得る `<repo>/agents/<name>.md`）。shipped ブリック（`facets/`・`patterns/` 等が `skills/rig/` 相対）とは非対称なので、`skills/rig/agents/` ではなく `<repo>/agents/` を見る（ここを誤ると reviewer agent を使う recipe が軒並み偽 FAIL になる）。

### ② manifest 参照（`.claude/rig.md`）

manifest の参照キーは RESOLVE/COMPOSE 時に**黙って握りつぶされる**（silent fallback）ため、run 前にここで検出する。`<repo>/.claude/rig.md` が無ければこの節は**スキップ**（`manifest: 無し — スキップ`。FAIL にしない＝manifest は任意）。あれば次を点検する。

| キー | 解決先 | 判定 |
|---|---|---|
| `default_recipe` | recipe（§4.2.1 tier 検索順：project→user→shipped） | **`"interactive"`（予約語・§4.1）／空／省略は tier 検索せず PASS**。それ以外でどの tier にも無ければ **FAIL**（RESOLVE が黙って interactive にフォールバックするため） |
| `default_personas[]` | persona facet（project→user→shipped）→ agent（`<repo>/agents/<name>.md`） | 各要素ごと、どこにも無ければ **FAIL**（COMPOSE が黙って当該 reviewer を skip するため） |

> `interactive` は recipe 名でなく「毎回ユーザーに選択させる」モードの予約語。`_template.md` の既定値がこれなので、予約語を tier 検索すると**テンプレ既定が偽 FAIL**する（除外必須）。`default_personas` のタイポは reviewer が静かに1人消えるため検出価値が高い。

**manifest 値キー検証（#11 / #64）** — 不正値は size-aware 判定と acceptance-gate を全 RUN で壊すため run 前に止める：
- `size_thresholds`（#112）：存在するサブキー `S_max`/`M_max`/`L_max` が**正の整数**か（0以下・整数以外 → **FAIL**）確認する。次に、未指定サブキーを汎用既定値（S=100/M=200/L=400）で補完した**実効値**で昇順 **`S_max < M_max < L_max`** を検証する（違反 → **FAIL**）。`size_thresholds:` キー自体が manifest に無い場合のみスキップ（サブキーが部分指定の場合は補完してチェックする）。エラーメッセージに補完した既定値を `(既定)` と明示する（例「`[FAIL] manifest size_thresholds: S_max(300) ≥ M_max(200 既定) — size-aware 判定が機能しません。実効値: S_max=300 / M_max=200(既定) / L_max=400(既定)`」）。
- `default_max_retries`：**整数かつ ≥1**（0・負・整数以外 → **FAIL**）。省略時スキップ（既定 2）。
- `default_backend`（#64）：指定されている場合、値が `manual` または `workflow` 以外 → **FAIL**（例 `[FAIL] manifest: default_backend が不正値です（"manul"）。有効値: manual | workflow`）。省略時スキップ（既定 `manual` が適用されるため問題なし）。recipe ③ の `backend:` 値検証（`manual|workflow` 以外は FAIL）と同一基準・同一 severity（対称性）。`--validate --global` 時は project/user 双方の manifest を点検する。
- **`default_orchestrate` 値検証（#154）**：`default_orchestrate` が記載されている場合、値が boolean（`true` または `false`）か確認する。文字列 `"yes"`・整数 `1` 等の型不正 → **FAIL**。省略時スキップ（既定 `false`）。エラーメッセージ例：`[FAIL] manifest: default_orchestrate が不正値です（"yes"）。有効値: true | false`。`--validate --global` 時は project/user 双方の manifest を対象にチェックする（`default_backend` の `--validate --global` 注記と対称）。
- **`worktree.enabled` 値検証（#155）**：`worktree.enabled` が記載されている場合、値が boolean（`true` または `false`）か確認する。文字列 `"yes"`・整数 `1` 等の型不正 → **FAIL**。`worktree:` キー自体が存在しない / `worktree.enabled` が存在しない場合はスキップ（既定 `false`）。エラーメッセージ例：`[FAIL] manifest: worktree.enabled が不正値です（"yes"）。有効値: true | false`。`--validate --global` 時は project/user 双方の manifest を対象にチェックする（`default_orchestrate` の `--validate --global` 注記と対称）。

**manifest パスキー検証（#14）** — タイポでドメイン知識注入がサイレント無効化されるため：
- `knowledge.context_file`（非空）→ `<repo>/` 相対でファイル実在しなければ **WARN**（「ドメイン知識注入が無効化されます」）。
- `knowledge.adr_dir`（非空）→ ディレクトリ実在しなければ **WARN**。
- `knowledge.design_docs[]` → 各要素のファイル実在しなければ要素ごと **WARN**。
- 空文字列／空リスト／省略はスキップ。severity は **WARN**（知識欠落でも RUN は完了するため。reviewer が消える FAIL とは格が違う）。`--validate --global` 時は project/user 双方の manifest を点検する。

### ③ frontmatter スキーマ（§3.5）

**YAML フロントマター構文エラー（#199）**：recipe ファイルを読み込み YAML フロントマター（`---` ～ `---`）の抽出・parse を試みる。parse に失敗した場合は即 **FAIL** として報告し、そのファイルの残りのチェック（①〜③の全項目）をスキップして**次のファイルに進む**（validate 全体は早期終了しない）。`--validate --global` 時は全 tier（shipped / project / user）の recipe を対象に同チェックを実施する。

```
[FAIL] recipe my-flow.md: YAML フロントマターの解析に失敗しました。
       構文エラーを修正してください（インデントずれ・コロン欠落・クォート不一致等がよくある原因です）。
       ファイル: <repo>/.claude/rig/recipes/my-flow.md
```

- recipe トップレベル必須キー `name` / `description` / `scope` / `steps[]` / `autonomy` が揃っているか。`scope` が `shipped|user|project` のいずれか。`autonomy` が `interactive|autonomous` のいずれか。
- 各 step に必須キー `id` / `instruction` があるか。`id` が recipe 内で一意か。
- **`autonomy` / `scope` 列挙値検証（#196）**：`autonomy` が `interactive` / `autonomous` 以外 → **FAIL**。`scope` が `shipped` / `user` / `project` 以外 → **FAIL**。`backend` 列挙値は #52（`backend` 値検証）で対応済み。`extends` 解決後の確定値で評価する。`--validate --global` 時は全 tier を横断して同チェックを実施する。

  ```
  [FAIL] recipe my-flow: autonomy の値 "auto" は不正です。interactive / autonomous のいずれかを指定してください。
  [FAIL] recipe my-flow: scope の値 "global" は不正です。shipped / user / project のいずれかを指定してください。
  ```

- **step `id` slug 形式検証（#197）**：step の `id` が `[a-z][a-z0-9-]*`（小文字アルファベット始まり・以降は小文字英数字またはハイフン）に一致しない場合 → **FAIL**。空文字列も `[a-z]` に一致しないため FAIL 対象。`extends` 解決後の確定 step リストで評価する。`--validate --global` 時は全 tier の recipe を対象に同チェックを実施する。

  ```
  [FAIL] recipe my-flow step "My Step": id の値 "My Step" は不正な形式です。
         id は [a-z][a-z0-9-]* （小文字英数字・ハイフンのみ）で指定してください。
         例: my-step / verify-e2e / implement-backend
  ```

- **step `pattern` / `gate` 列挙値検証（#198）**：step の `pattern` が shipped tier の pattern ブリック名（`serial` / `parallel-fanout` / `review-gate` 等 — `patterns/*.md` のファイル名から導出）以外 → **FAIL**（フィールド自体の未設定は許容）。step の `gate` が `review-gate` / `acceptance-gate` 以外 → **FAIL**（フィールド自体の未設定は許容）。`extends` 解決後の確定 step リスト（継承分を含む）で評価する。`--validate --global` 時は全 tier の recipe を対象に同チェックを実施する。

  ```
  [FAIL] recipe my-flow step "verify": pattern の値 "parallel-fanot" は不正な列挙値です。
         許容値: serial, parallel-fanout, review-gate（patterns/*.md のファイル名）
         例: pattern: parallel-fanout

  [FAIL] recipe my-flow step "review": gate の値 "acceptance_gate" は不正な列挙値です。
         許容値: review-gate, acceptance-gate
         例: gate: acceptance-gate
  ```

- **step `checks:` 型・空エントリ検証（#200）**：`checks:` が存在し値がリスト（配列）でない（スカラー文字列・null・数値等）場合 → **FAIL**。`checks:` がリストであり空文字列エントリ（`""`）を含む場合 → **FAIL**。フィールド自体の未設定は許容。`--validate --global` 時は全 tier の recipe を対象に同チェックを実施する。

  ```
  [FAIL] recipe my-flow step verify: checks の値がリストではありません（"npm test"）。
         checks はシェルコマンドの配列で指定してください。
         例: checks: ["npm test", "npm run lint"]

  [FAIL] recipe my-flow step verify: checks に空文字列エントリが含まれています（インデックス 1）。
         空のコマンドは実行時にサイレントに pass するため、チェックが機能しません。
         空文字列エントリを削除してください。
  ```

- **`name` フィールドとファイル名の照合（#157）**：各 recipe ファイルについて、frontmatter の `name` フィールド値が**ファイル名（`.md` 拡張子を除いた部分）と等しいか**を検証する。不一致であれば **FAIL** を出力する。`name` キー自体が存在しない場合は「必須キー未定義」FAIL（上記）で別途報告されるため、本チェックは「キーはあるが値がファイル名と異なる」場合のみ対象とする（二重 FAIL にしない）。`--validate --global` 時は shipped / user / project の全 tier に同チェックを適用する。

  ```
  [FAIL] recipe ファイル名と name フィールドが一致しません
    ファイル: <repo>/.claude/rig/recipes/my-flow.md
    name: "release-flow"  ← ファイル名 "my-flow" と不一致
    修正: name: "my-flow" に変更するか、ファイル名を "release-flow.md" に変更してください
  ```

- **`steps[]` 空チェック（#158）**：`steps[]` が空配列（`[]`）またはゼロ件の場合、**FAIL** を出力する。`steps:` キー自体が存在しない場合は「必須キー未定義」FAIL で別途報告されるため、本チェックは「キーはあるが中身が空」の場合のみ対象とする（二重 FAIL にしない）。`--validate --global` 時は全 tier に同チェックを適用する。

  ```
  [FAIL] recipe my-flow: steps[] が空配列です — 少なくとも 1 step が必要です。
         steps が空の recipe を実行すると、何もしない空ハーネスが生成されます（SKILL.md §3.5）。
         steps に instruction facet を持つ step を 1 件以上追加してください。
  ```

- **`gate: acceptance-gate` + `acceptance:` 未宣言/空配列 WARN（#179）**：`gate: acceptance-gate` の step の `acceptance[]` が**未定義または空配列 `[]`** の場合は **WARN**（基準がないため acceptance-gate が常時通過する可能性）。1件以上の基準がある場合のみ PASS。`gate: review-gate` を持つ step は対象外（`acceptance:` は `acceptance-gate` 専用）。`--validate --global` 時は全 tier の recipe を対象に同チェックを実施する。出力フォーマット：`[WARN] recipe <name> step <id>: gate: acceptance-gate が設定されているが acceptance: が未宣言または空です — 受け入れ基準がないため acceptance-gate が常時通過します。acceptance[] に基準を列挙してください（SKILL.md §3.5）。`
- **`max_retries` 値検証**：`max_retries` が記載されている step は、値が **整数かつ ≥1**（SKILL §3.5 の制約）か確認する。`0` または負の整数 → **FAIL**（受け入れ基準を1回も試さず即エスカレーション／未定義動作）。整数以外（文字列・小数等）→ **FAIL**（型不正）。`gate: acceptance-gate` 以外の step に `max_retries` が書かれている → **WARN**（無効コンテキスト＝acceptance-gate 無しでは無意味）。省略時はチェックしない（既定 2 が適用されるため問題なし）。
- **`backend` 値検証（#52）**：`backend` が記載されている recipe は、値が `manual` または `workflow` のいずれかか確認する。それ以外の値 → **FAIL**（無効バックエンド指定）。省略時はチェックしない（既定 `manual` が適用されるため問題なし）。
- **`tdd` 値検証（#56）**：`tdd` が記載されている recipe は、値が boolean（`true` または `false`）か確認する。文字列 `"true"`・数値等の型不正 → **FAIL**。省略時はチェックしない（既定 `false` が適用されるため問題なし）。
- **`no_default_personas` 値検証（#70）**：`no_default_personas` が記載されている recipe は、値が boolean（`true` または `false`）か確認する。文字列・数値等の型不正 → **FAIL**。省略時はチェックしない（既定 `false`）。
- **`orchestrate` 値検証（#129/#151）**：`orchestrate` が記載されている recipe は、値が boolean（`true` または `false`）か確認する。文字列 `"yes"`・数値等の型不正 → **FAIL**。省略時はチェックしない（既定 `false` が適用されるため問題なし）。エラーメッセージ例：`[FAIL] recipe <name>: orchestrate が不正値です（"yes"）。有効値: true | false`。
- **`cross_llm` 値検証（#130/#151）**：`cross_llm` が記載されている recipe は、値が boolean（`true` または `false`）か確認する。文字列・数値等の型不正 → **FAIL**。省略時はチェックしない（既定 `false`）。エラーメッセージ例：`[FAIL] recipe <name>: cross_llm が不正値です（1）。有効値: true | false`。
- **`no_capture` 値検証（#137/#151）**：`no_capture` が記載されている recipe は、値が boolean（`true` または `false`）か確認する。文字列・数値等の型不正 → **FAIL**。省略時はチェックしない（既定 `false`）。エラーメッセージ例：`[FAIL] recipe <name>: no_capture が不正値です（"TRUE"）。有効値: true | false`。
- **`adversarial` 値検証（#172）**：`adversarial` が記載されている recipe は、値が boolean（`true` または `false`）か確認する。文字列・数値等の型不正 → **FAIL**。省略時はチェックしない（既定 `false`）。エラーメッセージ例：`[FAIL] recipe <name>: adversarial が不正値です（"yes"）。有効値: true | false`。
- **`visual` 値検証（#174）**：`visual` が記載されている recipe は、値が boolean（`true` または `false`）か確認する。文字列・数値等の型不正 → **FAIL**。省略時はチェックしない（既定 `false`）。エラーメッセージ例：`[FAIL] recipe <name>: visual が不正値です（1）。有効値: true | false`。
- **`no_orchestrate` 値検証（#178）**：`no_orchestrate` が記載されている recipe は、値が boolean（`true` または `false`）か確認する。文字列・数値等の型不正 → **FAIL**。省略時はチェックしない（既定 `false`）。エラーメッセージ例：`[FAIL] recipe <name>: no_orchestrate が不正値です（"yes"）。有効値: true | false`。
- **`design` 値検証（#182）**：`design` が記載されている recipe は、値が boolean（`true` または `false`）か確認する。文字列・数値等の型不正 → **FAIL**。省略時はチェックしない（既定 `false`）。エラーメッセージ例：`[FAIL] recipe <name>: design が不正値です（"yes"）。有効値: true | false`。
- **`review` 値検証（#182）**：`review` が記載されている recipe は、値が boolean（`true` または `false`）か確認する。文字列・数値等の型不正 → **FAIL**。省略時はチェックしない（既定 `false`）。エラーメッセージ例：`[FAIL] recipe <name>: review が不正値です（1）。有効値: true | false`。
- **`capture` 値検証（#184/#193）**：`capture` が記載されている recipe は、値が boolean（`true` または `false`）か確認する。文字列 `"yes"`・整数 `1`・文字列 `"true"` 等の型不正 → **FAIL**。省略時はチェックしない（既定 `false`）。エラーメッセージ例：`[FAIL] recipe <name>: capture が不正値です（"yes"）。有効値: true | false`。
- **`orchestrate: true` + `no_orchestrate: true` 矛盾チェック（#178）**：`orchestrate: true` と `no_orchestrate: true` が同時に設定されている recipe は矛盾する宣言のため → **FAIL**。エラーメッセージ：`[FAIL] recipe <name>: orchestrate: true と no_orchestrate: true が同時設定されています。どちらか一方を削除してください。`。`checks:` または `needs:` を持ちかつ `no_orchestrate: true` の recipe は意図の矛盾 → **WARN**：`[WARN] recipe <name>: no_orchestrate: true が設定されていますが checks:/needs: 宣言があります。checks/needs は orchestrate モードでのみ有効です。no_orchestrate が checks/needs を上書きするため、checks/needs 宣言は実行時に無視されます（SKILL.md §4.3）。`
- **`capture: true` + `no_capture: true` 矛盾 FAIL（#184/#193）**：`capture: true` と `no_capture: true` が同時設定されている recipe は矛盾する宣言のため → **FAIL**。エラーメッセージ：`[FAIL] recipe <name>: capture: true と no_capture: true が同時設定されています。どちらか一方を削除してください。`。`orchestrate: true` + `no_orchestrate: true` の矛盾チェック（#178）と同形式。
- **`tdd: true` 無効コンテキスト WARN（#180）**：`tdd: true` が設定されているが `steps[]` に `id: implement` の step が存在しない（`extends` RESOLVE 後の確定リストで判定）場合は → **WARN**（`tdd` は implement step にのみ作用するため設定が永続的な no-op になる）。エラーメッセージ例：`[WARN] recipe <name>: tdd: true が設定されていますが implement step が存在しません。tdd は implement step にのみ作用するため、この設定は実行時に無視されます（SKILL.md §4.3）。`
- **`visual: true` 無効コンテキスト WARN（#180）**：`visual: true` が設定されているが `steps[]` に `id: verify` の step が存在しない（`extends` RESOLVE 後の確定リストで判定）場合は → **WARN**（`visual` は verify step にのみ作用するため設定が永続的な no-op になる）。エラーメッセージ例：`[WARN] recipe <name>: visual: true が設定されていますが verify step が存在しません。visual は verify step にのみ作用するため、この設定は実行時に無視されます（SKILL.md §4.3）。`
- **`design: true` 無効コンテキスト WARN（#194）**：`design: true` が設定されているが `steps[]` に `id: design` の step が存在しない（`extends` RESOLVE 後の確定リストで判定）場合は → **WARN**（`design: true` は design step の condition を上書きするが、design step がないため設定が永続的な no-op になる）。エラーメッセージ例：`[WARN] recipe <name>: design: true が設定されていますが design step が存在しません。design: true は design step の condition を上書きしますが、design step がないため実行時に無視されます（SKILL.md §4.3）。`
- **`review: true` 無効コンテキスト WARN（#194）**：`review: true` が設定されているが `steps[]` に `id: review` の step が存在しない（`extends` RESOLVE 後の確定リストで判定）場合は → **WARN**（`review: true` は review step の condition を上書きするが、review step がないため設定が永続的な no-op になる）。エラーメッセージ例：`[WARN] recipe <name>: review: true が設定されていますが review step が存在しません。review: true は review step の condition を上書きしますが、review step がないため実行時に無視されます（SKILL.md §4.3）。`
- **`condition` 値検証（#109）**：`condition` が記載されている step は、値が `size: <TOKEN>+?` の正準形式に従うか確認する。
  - `<TOKEN>` が `S` / `M` / `L` / `XL` のいずれかでなければ → **WARN**（例: `size: XLL+` / `size: large+`）
  - 値が空文字列 → **WARN**
  - 値が `size:` で始まらない → **WARN**（現行パーサが未対応のため常時-OFF になる）
  - severity は **WARN**（不正 condition は RESOLVE 時に常時-OFF として評価されるが RUN 自体は完了するため。FAIL にすると正当な将来構文も通らなくなる）
  - `--validate --global` 時は全 tier の recipe を対象に同チェックを実施する

```
[WARN] recipe release-flow step design: condition 値が不正です（"size: XLL+"）。
       有効な size トークン: S | M | L | XL（例: "size: L+"）。
       不正な condition は RESOLVE 時に常時-OFF として扱われ、step が永続スキップされます。
```

- `extends` は多段禁止（親がさらに `extends` を持たない。§4.2.2）。

**scope / tier 整合チェック（#84）** — recipe ファイルの `scope:` 値と実際の**格納 tier**（ファイルパスから判定）を照合する。`--save-recipe` は保存先 tier に合わせて自動設定するが、手書き recipe やコピー編集で乖離が生じうる。不一致は **WARN**（エンジンは §4.2.1 のファイルパスで tier を決定するため機能的破壊は引き起こさないが、recipe ファイルを直読みするユーザーへの混乱を防ぐ）。

| scope 値 | 期待する格納先 | 不一致時 |
|---|---|---|
| `shipped` | `skills/rig/recipes/` | **WARN** |
| `project` | `<repo>/.claude/rig/recipes/` | **WARN** |
| `user` | `~/.claude/rig/recipes/` | **WARN** |

```
[WARN] recipe my-flow.md: scope: shipped と宣言されていますが、格納先は project tier です（<repo>/.claude/rig/recipes/my-flow.md）。
       --save-recipe は保存先 tier に合わせて scope を自動設定します。
       手書き recipe の場合は scope を "project" に修正してください（SKILL.md §3.5）。
```

`--validate`（`--global` なし）は shipped＋project tier が対象。`--validate --global` は全 tier（shipped＋project＋user）が対象。

### ③-b persona facet frontmatter スキーマ

shipped の `facets/personas/**/*.md` を走査し、persona facet の frontmatter を検査する（`scripts/validate.py` の `check_personas` が CI 用に同ロジックを実装）。persona の frontmatter はエンジンのメタデータであり、COMPOSE 時に subagent System へ合成されるのは本文のみ（frontmatter は注入しない。`inject:` の解決にのみ使う）。

| フィールド | 検査内容 | 不正時の判定 |
|---|---|---|
| frontmatter 自体 | 存在し YAML として読めること | **FAIL** |
| `name` | `personas/` からの相対パス（拡張子なし・`/` 区切り。例 `sales/hearing-reviewer`）と一致 | **FAIL**（recipe `personas[]` / `--persona <name>` の名前解決と整合しなくなるため） |
| `description` | 存在・非空文字列 | **FAIL**（`/rig:catalog` / `--list --global` の表示に使う） |
| `inject` | 存在する場合はリスト型（`["[[slug]]", …]`） | **FAIL**（⑤ wiki 衛生の `inject:` 先解決チェックとは独立した型チェック） |

```
[FAIL] persona sales/hearing-reviewer — name 'hearing-reviewer' が相対パス 'sales/hearing-reviewer' と不一致
[FAIL] persona roast-reviewer — frontmatter がありません（name/description が必須）
```

- `--validate --global` 時は project（`<repo>/.claude/rig/personas/`）・user（`~/.claude/rig/personas/`）tier の persona も同スキーマで点検する（`/rig:persona` 生成物の衛生）。tier ディレクトリが無ければサイレントにスキップ。

### ④ §2 目録ドリフト

§2 ブリック目録（dev-core 行＋pack 追加分の表）と**実ファイル**を突き合わせる。

- 目録に載っているが**実ファイルが無い**もの（幽霊エントリ）→ error。
- 実ファイルが在るが**目録に載っていない**もの → pack 追加分への追記漏れの可能性として warning（dev-core は安定前提なので especially recipe/instruction/persona を見る）。
- README.md / README.ja.md の recipe / instruction / persona 一覧表も同様に実ファイルと突き合わせ、抜け・古い記載を warning する。

### ⑤ wiki 衛生（`facets/knowledge/_wiki`）

wiki ページ（`~/.claude/rig/knowledge/wiki/` ＋ `<repo>/.claude/rig/knowledge/wiki/`）を点検する。ディレクトリが無ければスキップ。

- **リンク切れ** → 本文/`links:`/persona の `inject:` にある `[[slug]]` が、どの tier のページにも解決しない → WARN（wiki が user/global tier のみに存在し project-scope validate で不可視なケースがあるため。`--validate --global` 時は FAIL に格上げ）。
- **参照欠落** → persona facet の `inject:` 先ページが存在しない → WARN（wiki が user/global tier のみに存在し project-scope validate で不可視なケースがあるため。`--validate --global` 時は FAIL に格上げ）。
- **orphan** → どこからも `[[リンク]]`/`inject:` されないページ → WARN（孤立知識）。
- **重複/矛盾** → 同一 `title` または `aliases` を持つ別 slug の `canonical` ページが複数 → WARN（正準化が必要）。
- **frontmatter 欠落** → `slug`（ファイル名一致）/`title`/`status` が無い、`status` が `canonical|draft|deprecated` 以外 → WARN。
- **INDEX ドリフト** → `INDEX.md` と実ファイル/タグ/backlink の乖離 → WARN（再生成を提案）。

### ⑥ ai-quirks 二相ペア整合（`--validate --global` のみ・#43）

`~/.claude/rig/knowledge/ai-quirks/` を走査する（ディレクトリ不在はスキップ）。`capture` が生成する ai-quirk は**記述形（`*-descriptive.md`）と規範形（`*-policy.md`）のペア**（§7.2 二相）でなければ COMPOSE の二相注入（§5）が片方しか効かない。ファイル名を `*-descriptive.md` と `*-policy.md` のペアに対応付け、片方が欠けているものを抽出する。

- `*-descriptive.md` が存在するが同名の `*-policy.md` が存在しない → **WARN**
- `*-policy.md` が存在するが同名の `*-descriptive.md` が存在しない → **WARN**
- 両方存在するペアは **PASS**

```
[WARN] ai-quirks: jwt-hallucination-descriptive.md があるが jwt-hallucination-policy.md が見つかりません。
        policy が注入されないため、AI 癖の抑制規範が RUN に渡りません（SKILL.md §5 / §7.2）。
        `capture` でペアを再生成するか、手動で policy ファイルを作成してください。
```

> **`--validate`（`--global` なし）では走査しない理由**：ai-quirks は user（global）層 `~/.claude/` にある。`--global` なしの validate は project スコープのみを対象とする設計（§3 `--global` の定義）と一致させる。⑤ wiki 衛生と対称的な追加（ai-quirks ファイルの integrity チェック）。

### ⑦ accumulated/ frontmatter スキーマ（#104）

`<repo>/.claude/rig/knowledge/accumulated/*.md` を走査し、各ファイルの YAML frontmatter を確認する。ディレクトリが存在しない場合はサイレントにスキップ（FAIL/WARN なし。⑤ wiki / ⑥ ai-quirks と同じ挙動）。

| フィールド | 検査内容 | 不正時の判定 |
|---|---|---|
| `category` | `pitfall\|decision\|convention\|stuck-twice` のいずれか | **WARN** |
| `title` | 存在・非空 | **WARN** |
| `date` | `YYYY-MM-DD` 形式（ISO 8601） | **WARN** |

- severity は **WARN**（frontmatter 欠落でも本文は注入され RUN は完了するため FAIL にしない。ただし知識の分類・MEMORY.md インデックスとの整合が崩れるため放置は非推奨）。
- `--validate --global` 時は `~/.claude/rig/knowledge/accumulated/` も同様に走査する（project / user 両層）。

```
[WARN] accumulated/pitfall-jwt.md: category が不正値です（"unknwon"）。有効値: pitfall|decision|convention|stuck-twice
[WARN] accumulated/decision-arch.md: title が空です。MEMORY.md インデックスとの整合が取れません。
[WARN] accumulated/convention-naming.md: date が YYYY-MM-DD 形式ではありません（"2026/06/10"）。
```

**⑦-b. 本文必須セクション検査（#203）** — frontmatter チェック（⑦-a）と同じファイル群（`<repo>/.claude/rig/knowledge/accumulated/*.md`）を対象に、frontmatter を除いた本文に §7.2「accumulated/ ファイルの正準フォーマット」で**必須**と定義されている2セクションが存在するかを確認する。

| セクション | 不正時の判定 |
|---|---|
| `## 何が起きたか` | **WARN** |
| `## 次回への示唆` | **WARN** |

- severity は ⑦-a と同じ **WARN**（本文セクション欠落でも RUN は完了するため FAIL にしない。ただし COMPOSE 時に注入される知識が不完全になる）。
- ⑦-a（frontmatter）と ⑦-b（本文セクション）は独立にチェックし、両方に問題があるファイルは両方の WARN を出す。
- `--validate --global` 時は ⑦-a と同様に project / user 両層の `accumulated/` を走査する。

```
[WARN] accumulated/pitfall-jwt.md: 必須セクション `## 何が起きたか` が見つかりません（SKILL.md §7.2）。
[WARN] accumulated/pitfall-jwt.md: 必須セクション `## 次回への示唆` が見つかりません（SKILL.md §7.2）。
```

> **⑥ ai-quirks との対称性**：⑥ が user 層の ai-quirks ペア整合を保護するのと同様に、⑦ は project 層の accumulated/ 知識パイプラインの整合を保護する。COMPOSE フェーズ（§5）で accumulated/ の本文が Knowledge 位置に注入されるため、frontmatter が壊れたファイルは「分類不明のゴミ知識」として注入されうる。

### `--global`（tier 横断）

`--validate --global` 指定時は shipped だけでなく **user(global)・project 層も走査**し、上記①〜⑥を**全 tier 横断**で点検する（全 tier の orphan・リンク切れ・参照欠落・重複・persona の `inject:` 先欠落・ai-quirks 二相ペア不整合）。tier をまたいだ同 slug の上書き関係（project overlay > global）も考慮し、**どの tier の何が問題か**を明示する。地図表示（読み取り）は `facets/instructions/catalog`（`--list --global` / `/rig:catalog`）に委ねる。

## レポート形式

機械抽出しやすい構造で、合否を1行ずつ出す。

```
## rig --validate レポート

PASS: <件数> / WARN: <件数> / FAIL: <件数>

[FAIL] recipe goal-loop → policies: pr-hygiene 参照切れ（facets/policies/pr-hygiene.md が無い）
[WARN] §2 目録ドリフト: facets/instructions/talk-loop が目録未記載
[PASS] recipe deal-review: 参照・スキーマ OK
...
```

- **FAIL が1件でもあれば「不合格」**として明示する（参照切れ・必須キー欠落・幽霊エントリ）。
- **WARN は合格扱いだが要対応**（目録/README ドリフト・acceptance 欠落など）。
- 各指摘は「どのファイルの何が・なぜ・どう直すか」が分かる粒度にする。修正は**自動で行わず**、提案だけ提示する（点検モードは副作用を持たない）。
