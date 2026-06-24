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

> **`personas[]` は COMPOSE（§5「persona facet の tier 解決」）と同じ経路で解決する**（shipped 層だけ見ない）。順に：①project `<repo>/.claude/rig/personas/<name>.md` → ②user `~/.claude/rig/personas/<name>.md` → ③shipped `skills/rig/facets/personas/<name>.md` → ④agent `<repo>/agents/<name>.md`。**いずれにも無い場合のみ参照切れ FAIL**。shipped 層だけ見ると `/rig:persona` で project/user に生成したカスタム persona を参照する recipe が**偽 FAIL** する（同 instruction の `instruction`/`policies[]`/`output_contract` は当面 shipped 基準で可・persona ほど tier 運用が一般的でないため）。
> **agent のベースパスはリポジトリルート**（`git rev-parse --show-toplevel` で得る `<repo>/agents/<name>.md`）。shipped ブリック（`facets/`・`patterns/` 等が `skills/rig/` 相対）とは非対称なので、`skills/rig/agents/` ではなく `<repo>/agents/` を見る（ここを誤ると reviewer agent を使う recipe が軒並み偽 FAIL になる）。

### ② manifest 参照（`.claude/rig.md`）

manifest の参照キーは RESOLVE/COMPOSE 時に**黙って握りつぶされる**（silent fallback）ため、run 前にここで検出する。`<repo>/.claude/rig.md` が無ければこの節は**スキップ**（`manifest: 無し — スキップ`。FAIL にしない＝manifest は任意）。あれば次を点検する。

| キー | 解決先 | 判定 |
|---|---|---|
| `default_recipe` | recipe（§4.2.1 tier 検索順：project→user→shipped） | **`"interactive"`（予約語・§4.1）／空／省略は tier 検索せず PASS**。それ以外でどの tier にも無ければ **FAIL**（RESOLVE が黙って interactive にフォールバックするため） |
| `default_personas[]` | persona facet（project→user→shipped）→ agent（`<repo>/agents/<name>.md`） | 各要素ごと、どこにも無ければ **FAIL**（COMPOSE が黙って当該 reviewer を skip するため） |

> `interactive` は recipe 名でなく「毎回ユーザーに選択させる」モードの予約語。`_template.md` の既定値がこれなので、予約語を tier 検索すると**テンプレ既定が偽 FAIL**する（除外必須）。`default_personas` のタイポは reviewer が静かに1人消えるため検出価値が高い。

**manifest 値キー検証（#11）** — 不正値は size-aware 判定と acceptance-gate を全 RUN で壊すため run 前に止める：
- `size_thresholds`：各サブキー `S_max`/`M_max`/`L_max` が**正の整数**か（0以下・整数以外 → **FAIL**）、かつ昇順 **`S_max < M_max < L_max`** か（違反 → **FAIL**、例「`S_max(300) ≥ M_max(100)` — size-aware が機能しない」）。省略時スキップ。
- `default_max_retries`：**整数かつ ≥1**（0・負・整数以外 → **FAIL**）。省略時スキップ（既定 2）。

**manifest パスキー検証（#14）** — タイポでドメイン知識注入がサイレント無効化されるため：
- `knowledge.context_file`（非空）→ `<repo>/` 相対でファイル実在しなければ **WARN**（「ドメイン知識注入が無効化されます」）。
- `knowledge.adr_dir`（非空）→ ディレクトリ実在しなければ **WARN**。
- `knowledge.design_docs[]` → 各要素のファイル実在しなければ要素ごと **WARN**。
- 空文字列／空リスト／省略はスキップ。severity は **WARN**（知識欠落でも RUN は完了するため。reviewer が消える FAIL とは格が違う）。`--validate --global` 時は project/user 双方の manifest を点検する。

### ③ frontmatter スキーマ（§3.5）

- recipe トップレベル必須キー `name` / `description` / `scope` / `steps[]` / `autonomy` が揃っているか。`name` がファイル名と一致するか。`scope` が `shipped|user|project` のいずれか。`autonomy` が `interactive|autonomous` のいずれか。
- 各 step に必須キー `id` / `instruction` があるか。`id` が recipe 内で一意か。
- `gate: acceptance-gate` の step に `acceptance[]` があるか（推奨・無ければ warning）。
- **`max_retries` 値検証**：`max_retries` が記載されている step は、値が **整数かつ ≥1**（SKILL §3.5 の制約）か確認する。`0` または負の整数 → **FAIL**（受け入れ基準を1回も試さず即エスカレーション／未定義動作）。整数以外（文字列・小数等）→ **FAIL**（型不正）。`gate: acceptance-gate` 以外の step に `max_retries` が書かれている → **WARN**（無効コンテキスト＝acceptance-gate 無しでは無意味）。省略時はチェックしない（既定 2 が適用されるため問題なし）。
- **`backend` 値検証（#52）**：`backend` が記載されている recipe は、値が `manual` または `workflow` のいずれかか確認する。それ以外の値 → **FAIL**（無効バックエンド指定）。省略時はチェックしない（既定 `manual` が適用されるため問題なし）。
- **`tdd` 値検証（#56）**：`tdd` が記載されている recipe は、値が boolean（`true` または `false`）か確認する。文字列 `"true"`・数値等の型不正 → **FAIL**。省略時はチェックしない（既定 `false` が適用されるため問題なし）。
- `extends` は多段禁止（親がさらに `extends` を持たない。§4.2.2）。

### ④ §2 目録ドリフト

§2 ブリック目録（dev-core 行＋pack 追加分の表）と**実ファイル**を突き合わせる。

- 目録に載っているが**実ファイルが無い**もの（幽霊エントリ）→ error。
- 実ファイルが在るが**目録に載っていない**もの → pack 追加分への追記漏れの可能性として warning（dev-core は安定前提なので especially recipe/instruction/persona を見る）。
- README.md / README.ja.md の recipe / instruction / persona 一覧表も同様に実ファイルと突き合わせ、抜け・古い記載を warning する。

### ⑤ wiki 衛生（`facets/knowledge/_wiki`）

wiki ページ（`~/.claude/rig/knowledge/wiki/` ＋ `<repo>/.claude/rig/knowledge/wiki/`）を点検する。ディレクトリが無ければスキップ。

- **リンク切れ** → 本文/`links:`/persona の `inject:` にある `[[slug]]` が、どの tier のページにも解決しない → FAIL。
- **参照欠落** → persona facet の `inject:` 先ページが存在しない → FAIL。
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
