# instruction: validate

ブリック整合チェック（doctor）。rig は全ブリックが markdown のため、参照切れ・スキーマ逸脱・目録ドリフトが静かに混入しうる。`--validate` 指定時にこの手順で機械的に点検し、結果を提示して**停止**する（RESOLVE/COMPOSE/RUN しない・副作用なし）。

実作業（ファイル走査）は `Glob` / `Grep` / `Read` で行い、長文を親 context に引き込まない（合否と該当箇所だけ集約する）。

## チェック項目

### ① recipe → facet 参照切れ

各 `recipes/*.md`（shipped＝`skills/rig/recipes/`、project＝`<repo>/.claude/rig/recipes/`、user＝`~/.claude/rig/recipes/`）の frontmatter を読み、各 step が参照する名前が**実ファイルとして存在する**かを照合する。

| キー | 解決先 | 存在チェック |
|---|---|---|
| `instruction` | `facets/instructions/<name>.md` | 必須・1つ |
| `personas[]` | `facets/personas/<name>.md`（`/` 区切りでサブディレクトリ可。例 `sales/hearing-reviewer`） | 各要素 |
| `policies[]` | `facets/policies/<name>.md` | 各要素 |
| `output_contract` | `facets/output-contracts/<name>.md` | 任意・あれば |
| `pattern` / `gate` | `patterns/<name>.md` | 任意・あれば |
| `extends` | 親 recipe（§4.2.1 tier 検索順・bare 名） | あれば |

> reviewer の `personas` は agent（subagent_type）優先・persona facet フォールバックのため、persona ファイルが無くても同名 agent（`agents/<name>.md`）があれば OK とする。両方無ければ参照切れ。

### ② frontmatter スキーマ（§3.5）

- recipe トップレベル必須キー `name` / `description` / `scope` / `steps[]` / `autonomy` が揃っているか。`name` がファイル名と一致するか。`scope` が `shipped|user|project` のいずれか。`autonomy` が `interactive|autonomous` のいずれか。
- 各 step に必須キー `id` / `instruction` があるか。`id` が recipe 内で一意か。
- `gate: acceptance-gate` の step に `acceptance[]` があるか（推奨・無ければ warning）。
- `extends` は多段禁止（親がさらに `extends` を持たない。§4.2.2）。

### ③ §2 目録ドリフト

§2 ブリック目録（dev-core 行＋pack 追加分の表）と**実ファイル**を突き合わせる。

- 目録に載っているが**実ファイルが無い**もの（幽霊エントリ）→ error。
- 実ファイルが在るが**目録に載っていない**もの → pack 追加分への追記漏れの可能性として warning（dev-core は安定前提なので especially recipe/instruction/persona を見る）。
- README.md / README.ja.md の recipe / instruction / persona 一覧表も同様に実ファイルと突き合わせ、抜け・古い記載を warning する。

### ④ wiki 衛生（`facets/knowledge/_wiki`）

wiki ページ（`~/.claude/rig/knowledge/wiki/` ＋ `<repo>/.claude/rig/knowledge/wiki/`）を点検する。ディレクトリが無ければスキップ。

- **リンク切れ** → 本文/`links:`/persona の `inject:` にある `[[slug]]` が、どの tier のページにも解決しない → FAIL。
- **参照欠落** → persona facet の `inject:` 先ページが存在しない → FAIL。
- **orphan** → どこからも `[[リンク]]`/`inject:` されないページ → WARN（孤立知識）。
- **重複/矛盾** → 同一 `title` または `aliases` を持つ別 slug の `canonical` ページが複数 → WARN（正準化が必要）。
- **frontmatter 欠落** → `slug`（ファイル名一致）/`title`/`status` が無い、`status` が `canonical|draft|deprecated` 以外 → WARN。
- **INDEX ドリフト** → `INDEX.md` と実ファイル/タグ/backlink の乖離 → WARN（再生成を提案）。

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
