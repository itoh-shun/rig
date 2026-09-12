# rig — COMPOSE（ハーネス合成）

**SKILL.md §5 の正本。** facet 配置順・知識層の注入・native 委譲・persona の tier 解決を置く。

RESOLVE が確定させた各 step を subagent prompt に組み上げる段の規定である。
合成に入るときに読めばよく、起動の最初の1ターンには要らない。

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
| **wiki（pack 同梱）** | `<pack root>/<pack id>/facets/knowledge/<slug>.md` | インストール済み pack が自分の persona のために持ち込む正準ページ。pack root は project(`<repo>/.rig/packs/`) > user(`~/.rig/packs/`) > org > official > core |

いずれかの tier ディレクトリが存在しない場合は**サイレントにスキップ**する（エラーにしない）。

**wiki ページの参照と注入（`facets/knowledge/_wiki` 参照）:**

- persona facet が `inject: ["[[slug]]", …]` を宣言している場合、各 `[[slug]]` を **tier 解決**してページを取得する。取得したページは **User 先頭（Knowledge 位置）に注入**する（1ホップ既定・過剰展開しない）。解決順は project overlay > global > org > **pack 同梱** > shipped `skills/engine/facets/knowledge/wiki/`。**pack の persona が `inject:` を持つ場合、その解決先はまず自分の pack の `facets/knowledge/<slug>.md`**（pack はそこにページを同梱する）。同 slug を global/project に置けば従来どおり上書きできる。
- 本文中の `[[slug]]` も同様に解決対象。`[[slug|表示名]]` 記法可。解決できない `[[...]]` は**注入せず**、`--validate` がリンク切れとして報告する。
- wiki は「事実」、persona は「判断・声」。**persona は事実を埋め込まず wiki を参照する**（暗黙知サイロを避ける）。
- `japanese-writing` の `material_profile` は例外的な文体素材 selector である。`none|technical|conversation`
  の明示値だけを使い、goal から推測しない。recipe の `material_profiles.<profile>.inject` が owner-bound
  wiki をちょうど一つ指す。UTF-8 上限・asset hash・出典 blob hash・owner attestation を検証する。
  検証してから「文体専用・事実利用禁止・引用禁止」の制御文と untrusted fence を付けて Knowledge 位置へ置く。
  `none` は無注入、素材は generator の初稿と一度だけの修正だけに使い reviewer へ渡さない。
  secure runtime は選択済み bytes を provider 起動前に owner-only snapshotへ固定し、同じrunでは
  assetを再選択しない。resumeはsnapshotと現在のasset/source provenanceの両方を再検証する。
  source全体はJapanese pack内のMIT resource blobで検証し、repository `/docs`を実行時依存にしない。

**注入位置:**

- **methodology / domain** の知識ブリック → subagent prompt の **User 先頭**（Knowledge 位置）に注入する。
- **ai-quirks** は**二相注入**する：
  1. **記述形（知識）** → User 先頭の Knowledge 位置（他の知識ブリックと同列）に注入。
  2. **導出規範形（derived Policy）** → User 末尾の Policy 位置（recency が効く末尾）に注入。Policy facet（`facets/policies/`）と同じ位置に配置する。

知識層の構造・ディレクトリ規約・ai-quirks 二相の詳細は `facets/knowledge/_layer.md` を参照。

### native 委譲

各 step は**既存の skill / command / agent に委譲**する（§8 Native-first）。reviewer は **agent 優先**（subagent_type: security-reviewer / design-reviewer / test-reviewer）。無ければ **persona facet を合成**して subagent に渡す。合成元は `facets/personas/{security,design,test}-reviewer`。instruction facet は薄く、手順の本体は委譲先に置く。

### persona facet の tier 解決（project → user → shipped）

persona 名（recipe の `personas[]` / `--persona <name>` / フォールバック合成）を解決するとき、recipe（§4.2.1）と同じ順でファイルを探す。**先に見つかった tier 優先**。

| tier | パス | 優先度 |
|---|---|---|
| **project**（最高） | `<repo>/.claude/rig/personas/<name>.md` | 1 |
| **user**（global） | `~/.claude/rig/personas/<name>.md` | 2 |
| **org**（チーム共有・任意） | `<org_dir>/personas/<name>.md`（manifest `org_dir:` または env `RIG_ORG_HOME` が指す**チームの git リポジトリ**） | 3 |
| **shipped**（同梱） | `skills/engine/facets/personas/<name>.md` | 4（最低） |

> **org tier**：チームで育てるブリック層。実体は clone した共有 git リポジトリ（`personas/` `recipes/` `knowledge/wiki/` を持つ）。manifest の `org_dir:` か環境変数 `RIG_ORG_HOME` で指す。解決順は **project → user → org → shipped**（個人の customize がチーム標準に勝ち、チーム標準が shipped に勝つ）。recipe・wiki も同順で解決する。未設定ならこの tier はサイレントにスキップ（従来どおり3 tier）。`--validate --global` / `/rig:catalog` は org tier も走査する。

- `<name>` は `/` 区切りでサブディレクトリ可（例 `design/ux-reviewer`）。
- **persona facet の frontmatter はメタデータ**（`name`＝`personas/` からの相対パス・`description`・任意の `inject:`）。COMPOSE が subagent System に合成するのは**本文のみ**。frontmatter は注入しない（`inject:` の wiki 解決と `--list --global`／catalog の表示にのみ使う）。スキーマは `--validate` ③-b が点検する。
- reviewer は引き続き agent（subagent_type）優先。agent が無いときの persona facet フォールバックはこの tier 検索で解決する。
- **review fan-out の追加枠（shipped）**は `performance-reviewer` と `observability-reviewer` の2つ。観点は `performance-reviewer` がデータ量スケール・ホットパス、`observability-reviewer` が失敗の可視性・ロールバック。どちらも既定の 3-way には入らない。`--persona` / manifest `default_personas` / recipe `personas[]` で必要な変更にだけ足す。詳細は `facets/instructions/parallel-review` を参照。
- これにより `/rig:persona` で生成した persona（既定 project / `--user` で global）を**名前で即使える**。
- **`--persona <name>` flag**：review fan-out に名前指定のカスタム reviewer persona を追加する（複数可）。各 `<name>` を上表で解決し、組み込み reviewer と同列に subagent へ dispatch（persona facet を System に合成）。解決できなければ「persona が見つかりません」と報告して停止。

### manifest `default_personas` の自動投入（製品ごとの常時 reviewer）

manifest（§4.1）に `default_personas: [<name>, …]` を宣言できる。宣言されていれば、**その製品の review/adversarial step に毎回それらの persona を自動投入**する。`--persona` を毎回打たなくても、その製品のドメイン reviewer（例: VST プラグインなら `house-authenticity`）が常にレビューに参加する。

- **解決**：各 `<name>` を上の tier 検索（project → user → shipped）で解決する。`--persona` と同じ経路。
- **wiki の同伴**：解決した persona が `inject: ["[[slug]]", …]`（§5 wiki）を宣言していれば、その wiki ページも通常どおり自動注入される。注入先は Knowledge 位置＝**persona を入れれば事実も付いてくる**。
- **適用範囲**：review 系 step（`review` / `adversarial-review` 等、persona を fan-out する step）にのみ作用する。step を持たない recipe（design のみ等）には影響しない。
- **合成と重複排除**：最終 reviewer 集合は4つの和集合。`組み込み reviewer（size-aware）`・`recipe の personas[]`・`manifest default_personas`・`--persona 指定分`。この4つを **名前で和集合**する（同名は1つに dedup）。
- **解決失敗**：manifest に書かれた名前が見つからない場合は「default_personas の `<name>` が解決できません」と**警告**する。警告した persona はスキップする（停止はしない＝製品全体のフローを止めない）。`--persona` の明示指定だけは従来どおり停止する。
- **抑止**：この run だけ自動投入を外したいときは `--no-default-personas`（§3 flag）。恒久的に変えるなら manifest を編集する。

> 設計意図：`--persona` は「この run で足す」一時指定、`default_personas` は「この製品では常に使う」恒久宣言。**ドメイン reviewer を毎回タイプせず、製品 manifest に1回書けば自動で効く**（友人の "VST プラグインのレビューには毎回ハウス審美 reviewer を" を1行で表現）。自動選択は manifest 明示に限定し、タグ推測による暗黙ルーティングはしない（確実性優先）。


### `--plan` の停止

`--plan` 指定時は COMPOSE で停止し、合成ハーネスを**正準フォーマット**で提示する（RUN はしない）。出力は機械抽出しやすい固定構造（2回叩いても同じ構造・並び＝出力も determinism-by-gate）。並びはヘッダ → **step テーブル** → `### Gate:` →（`--orchestrate` 時のみ）`### Checks:` / `### DAG:`。続いて `### Knowledge: 注入予定ソース` → `### Reviewer Fan-out:` →（loop 時のみ）`### Loop Config:`。末尾は `steps:` サマリ。ヘッダ行は `recipe: <name> [tier]`・`diff:`/size・`description:`・`flags:`・`save-recipe:`/`skip:`/`slice:`。モード修飾子 `| tdd: on` 等も付く。**step テーブル**は解決済み最終 step・condition 先行評価・personas の出所マーカー ★/†/‡ と `[tier]` を出す。`extends` 時は `origin` 列も付く。`### Gate:` には acceptance/review ゲート条件のチェックリストと `max_retries` 解決元マーカーを並べる。`### Knowledge: 注入予定ソース` は tier 別ファイル一覧＋persona `inject:` の wiki 解決。`### Reviewer Fan-out:` には最終 reviewer 集合を出す。末尾の `steps:` サマリは condition 付き/gate 数・`acceptance retries 上限:`。**表示仕様の正本は `facets/instructions/plan`** — `--plan` 実行時は必ずこれを読んで従う。`--save-plan <path>` は同一内容をファイルにも書き出す（§3 flag・停止セマンティクス不変）。

