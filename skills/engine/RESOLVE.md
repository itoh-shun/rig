# rig — RESOLVE（解決順）

**SKILL.md §4 の正本。** manifest ロード・recipe 解決・flag override・size-aware 既定・autonomy を置く。

最終ハーネスを確定させる段の規定であり、タスクを受け取って RESOLVE を回すときに読む。
手順を自力で回すときの詳細は `facets/instructions/resolve` が正本。

<!-- textlint-disable -->
<!-- Moved verbatim from SKILL.md §4, where a `textlint-disable` marker inside a §2 table cell (now BRICKS.md) suppressed it unintentionally. The suppression is kept explicitly so the move stays byte-identical on the prose, and a japanese-lint pass still needs to lift it. -->

## 4. RESOLVE — 解決順（manifest＋recipe＋flag＋size-aware 既定）

最終ハーネスを **manifest → recipe → flag → size-aware 既定** の順で確定する。**後の段が前の段を override する。**

> **一次実装はコード。** named recipe の RESOLVE は `orchestrate plan <recipe> --json --with "<flags>" --diff-git` の出力（`effective_steps` / `active`・`why` / `errors` / `warnings` / `mode` / `badges`）が確定結果。スクリプトを呼べない環境と ad-hoc 対話合成に限り散文規則を自力適用する。**詳細規則の正本は `facets/instructions/resolve`**（manifest キーの意味・recipe tier 検索の報告フォーマット・`extends` N 段合成・flag⇔recipe キー等価表・スライスのエラーフォーマット・`--save-recipe` の保存規則）— RESOLVE を自力で回すときは必ずこれを読んで従う。散文とコードが割れたら `selftest` Q/R/S の golden が正＝**コード側を先に直し、散文を追随させる**。

### 4.1 manifest ロード

**`<repo>/.claude/rig.md`** があれば YAML frontmatter を解析してプロジェクト既定として読み込み、無ければ全キーに汎用既定（generic defaults）を適用する。エンジンが読むキー：`build` / `lint` / `test` / `branch.*` / `reviewer` / `production_impact.*` / `skills` / `knowledge.*` / `default_recipe` / `default_personas` / `default_backend` / `default_max_retries` / `org_dir` / `default_budget` / `default_orchestrate` / `worktree.*` / `size_thresholds.*`。**各キーの意味と既定値は `facets/instructions/resolve` 1.**、manifest スキーマの全体定義は `manifests/_template.md` が正本。

> repo 同梱の manifest は project recipe と同じく**初回のみ明示同意**が必要（`--allow-project-manifest` / `RIG_ALLOW_PROJECT_MANIFEST=1`）。未同意の manifest はハード停止ではなく警告1行で **「manifest 無し」相当へ soft degrade** する。

### 4.2 recipe 解決

1. `--recipe <name>` フラグ（明示指定） 2. manifest の `default_recipe` 値 3. 対話（ブリックを提案して選択させる）。`--recipe` があれば `default_recipe` は無視。

#### 4.2.1 recipe ファイル検索順（tier 優先順位）

recipe 名が決まったら **project → user → shipped** の順にファイルを探し、**先に見つかった tier が優先**（下位 tier の同名は無視）。

| tier | パス | 優先度 |
|---|---|---|
| **project**（最高） | `<repo>/.claude/rig/recipes/<name>.md` | 1（最優先） |
| **user** | `~/.claude/rig/recipes/<name>.md` | 2 |
| **shipped**（同梱） | `skills/engine/recipes/<name>.md` | 3（最低） |

どの tier にも無ければ「もしかして」候補（編集距離 ≤ 2・最大 3 件・`[tier]` 付き）を添えて報告し、対話 composition（§3）へフォールバックする。**報告フォーマットの正本は `facets/instructions/resolve` 2.1**

#### 4.2.2 extends — N 段継承（上限 5）

`extends: <parent-name>` は **bare 名のみ**（パス指定・URL 不可）。leaf → root へ辿り、root ancestor の `steps[]` をベースに leaf に向かって各段を適用する（同 `id` は上書き・新 `id` は末尾追加・`remove: true` は継承元 step を静的除外）。トップレベルキーは leaf の値が勝ち、`extends` は合成後の recipe に残さない。**深さ上限 5 超過と循環継承は WARN で切り上げ**（実行は止めない・`--validate` が集計）。**マージ規則・`remove: true` のエラー処理・削除 step の表示規則は `facets/instructions/resolve` 2.2**

### 4.3 flag override

`--design` `--review` `--tdd` 等で recipe の step ON/OFF・動作を上書きする。**boolean な recipe キー（§3.5）は対応するフラグと完全に等価**であり、例外はない：

1. **等価** — キーが `true` なら対応フラグ指定と同じ効果が発動する（省略時 `false`）。
2. **保存** — `--save-recipe` は指定されたフラグを対応キーとして書き出す（再利用時にフラグなしで同じ挙動が再現＝保存した意図が静かに失われない）。
3. **可視化** — 有効なとき `--plan` ヘッダとフロー完了レポート（§6）に修飾子（`| tdd: on` 等）、`--list` に badge（`· tdd` 等）を付す。

**フラグ⇔キーの対応表・各キーの効果・競合規則（`orchestrate`⇔`no_orchestrate`、`--capture`⇔`--no-capture`）・`--orchestrate` の自動有効化条件（recipe の `checks:`/`needs:` 宣言 または manifest `default_orchestrate`）は `facets/instructions/resolve` 3.** が正本。

#### 4.3.1 --only / --from / --to / --skip — step スライス

スライスは §4.2 で確定した**最終 step リスト**（extends 適用後・condition 評価後）に適用する。

| flag | 動作 |
|---|---|
| `--only <step-id>` | 指定した step-id **1つだけ**を実行する。 |
| `--from <step-id>` | 指定した step-id から最後まで実行する。 |
| `--to <step-id>` | 先頭から指定した step-id（含む）まで実行する。`--from` と組み合わせて範囲指定可。 |
| `--skip <step-id>` | 指定した step-id を**除外**して継続する（複数可）。size-aware 既定・`--design`/`--review` より後に適用＝**明示スキップが最終的に勝つ**。 |

`--only` は `--from`/`--to`/`--skip` に優先し、競合分を警告つきで無視する。`--from A --to B` の順序逆転はエラー停止。**step-id が見つからない場合の2ケース報告（タイポ候補提案 / condition-OFF ヒント）・`--skip` の WARN（condition-OFF・acceptance-gate 除外）・`--plan` での表示モデルは `facets/instructions/resolve` 4.**

#### 4.3.2 --save-recipe — 合成結果の保存

RESOLVE で確定した step リスト（extends 適用後・flag override 後）を YAML frontmatter + Markdown で書き出す。既定は project 層（`<repo>/.claude/rig/recipes/<name>.md`）、`--user` 併用で user 層（`~/.claude/rig/recipes/<name>.md`）。

**snapshot 意味論**：保存されるのは「このフローが持つ steps の全量」。`extends` は解決済みに展開して落とし（親の変更が静かに波及しない）、`--from`/`--to`/`--only`/`--skip`/`--budget` は**実行時フィルタ**なので保存 step リストに影響しない（同時指定時は保存後に WARN を出す）。**`description` 自動生成規則・`--persona` 指定分の保存・shadow チェック・WARN 文面は `facets/instructions/resolve` 5.**

### 4.4 size-aware 既定（軽さ優先）

変更規模に応じて重い step を自動 OFF する。行数閾値は manifest の `size_thresholds`（`S_max` / `M_max` / `L_max`）で上書きできる（未設定時は pr-hygiene 基準 `100` / `200` / `400`。テンプレは `manifests/_template.md`）。

- **S / M**（既定：`M_max` 以下＝～200行）: design / review / tdd を**既定 OFF**。明示 flag で ON にした場合のみ実行。
- **L 以上**（既定：`M_max` 超。`L_max` 超は分割必須）: design / review を推奨し、ON を促す。

**コスト予算（`--budget`・§3 flag）** — size-aware が「変更の重さ」で間引くのに対し、budget は**支出の上限**で間引く：`low`＝組み込み 3-way のみ（追加 reviewer・自動追加 step を抑止し、必要なら提案だけ出す）・workflow 禁止。`mid`＝3-way＋選択投入2枠まで。予算で抑止した項目は `--plan`／完了レポートに `[BUDGET: 抑止]` と明示する（サイレントに削らない）。manifest `default_budget: low|mid` で恒久設定・`--budget` フラグが優先。

### 4.5 autonomy

`--autonomous` で step ゲート OFF。指定が無ければ各 step 後に確認する step ゲート ON。

> **`--autonomous` が外すのは「step ゲート（各 step 後の確認ダイアログ）」だけ。** `acceptance-gate`（受け入れ基準を満たすまで最大 K 回収束し、K 超で user エスカレーションする品質ループ）は `--autonomous` でも変わらず動く。capture ゲートと同様に、品質保証の核は `--autonomous` で解除されない。recipe の `autonomy: autonomous`（§3.5）の「ゲートなし」も step ゲートを指す。

