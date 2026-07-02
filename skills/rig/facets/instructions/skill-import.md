# instruction: skill-import

ネット上の外部 skill（GitHub リポジトリの SKILL.md / Claude Code plugin / superpowers 系スキル集）を**解析して rig のブリックに翻訳し、出所を `skills-lock.json` に記録**する。rig の思想（ネットのあらゆる skills を真似しながら包括する）を、手動 vendoring から**再現可能・更新検知可能な取り込み機構**に格上げする。`/rig:forge`（ゼロから自作）の対＝**既にあるものを取り込む**。

## 入力

- **ソース指定**（いずれか1つ。`--discover`/`--check-updates` 時は不要）：
  - GitHub URL（`https://github.com/<owner>/<repo>` / blob URL 可）
  - `<owner>/<repo>` 短縮形
  - ローカルパス（clone 済みディレクトリ）
- `--discover "<欲しい能力>"`（ソース指定の代替）：**ネットから探す**。ソースを知らなくても、欲しい能力の説明から候補を検索・ランクして提示する（下記⓪）。
- **取り込めるソース形式（方言）**：`SKILL.md` / Claude Code plugin に加え、**`.cursorrules`・`AGENTS.md`・他リポジトリの `CLAUDE.md`・MCP サーバーのツール定義・プロンプト集**も対象。これらは「判断・観点・規範」の**別方言**であり、pack の定石への分解は同じ（規範→policy、観点→persona/knowledge、手順→instruction）。ただし実行主体が無いため**委譲は不可＝翻訳/知識のみ**（②の判断で自動的にそうなる）。
- `--path <repo内パス>`（任意）：取り込む skill のパス（例 `skills/hyperframes/SKILL.md`）。省略時はリポジトリを走査して SKILL.md / plugin 構造を発見し、**候補一覧を提示して選ばせる**。
- `--all`（任意）：走査で見つかった**候補全件を一括処理**する（下記③'）。手元のスキル集・スキルディレクトリ（例 `~/.claude/skills`）をまとめて取り込む/lock 登録するときに使う。`--path` と排他（`--path` は1件指定）。
- `--name <slug>`（任意）：rig 側での pack/brick 名。省略時はソースから slug を提案（`--all` 時は各 skill のディレクトリ名から自動提案）。
- `--user`：生成ブリックを user 層に保存（既定は project 層。rig 本体作業時は `--shipped`）。
- `--dry-run`：解析と翻訳提案まで（書き込み・lock 記録をしない）。
- `--check-updates`：取り込み済み skill の**上流差分検知**モード（下記②'）。ソース指定不要。

## 手順

### ⓪ `--discover`（発見 — 探す→無ければ作る）

ソースを知らないユーザーの「こういう能力が欲しい」から始める：

1. **検索**（subagent・WebSearch/WebFetch）— GitHub を横断する：topic `claude-skill` / `claude-code-plugin`、`SKILL.md` を含むリポジトリのコード検索、awesome-claude-code 系リスト、superpowers 系スキル集。欲しい能力の説明をクエリに翻訳して複数角度で探す。
2. **ランク** — 候補を次の観点で採点し**上位3〜5件の短リスト**にする：説明との適合度／ライセンス（不明・再配布不可は減点し委譲のみと注記）／更新日・スター数（保守性の代理指標）／**既存ブリックとの重複**（`--list`/catalog と突き合わせ。重複なら「既にある」と案内して除外）。各候補に1行の根拠を付す。
3. **選択→接続** — ユーザーが選んだ候補をそのまま①（取得）へ渡す。
4. **見つからないとき** — 捏造せず「該当なし」と報告し、**自作へ切り替えを提案**する：レビュー観点なら `/rig:persona`、フロー/パックなら `/rig:forge`、知識なら `/rig:knowledge`。**探す→無ければ作る**を1つの入口で完結させる。

### ① 取得（subagent に dispatch・context-minimal）

ソースを取得して対象 skill の本文を読む。`gh` CLI / `git clone`（浅い clone）/ WebFetch のいずれか利用可能な手段で。親は本文を抱えず、subagent に**構造サマリ**（何をする skill か・入出力・手順の骨子・依存ツール）だけ返させる。

### ② 解析と翻訳判断（native-first を最優先）

取り込みには3つの形があり、**上から順に検討する**（§8 Native-first 非対称ルール）：

1. **委譲（最優先）** — 外部 skill がそのまま Claude Code plugin / skill として動くなら、**移植しない**。instruction facet から「この step は skill `<name>` に委譲する」と routing するだけの薄いブリックを作る（例：`hyperframes-video` が HyperFrames の shipped skills へ委譲している形）。
2. **翻訳** — 外部 skill の**判断・観点・手順**が rig のフローに組み込む価値を持つなら、pack の定石（persona＝判断／knowledge＝観点カタログ／instruction＝routing／recipe＝step の束／output-contract＝形式／command＝入口）に**分解して翻訳**する。生成自体は `/rig:forge`（skill-author）・`/rig:persona`・`/rig:knowledge` の各ジェネレータに委譲し、本 instruction は分解方針の決定と出所記録に徹する。
3. **知識のみ** — フロー化する価値はないが観点カタログとして有用なら、knowledge/wiki ページとしてだけ取り込む（`/rig:knowledge` へ委譲）。

**判断根拠を必ず提示する**（なぜ委譲でなく翻訳か、どのブリック型に何を割り当てたか）。既存ブリックとの重複は `--list` / catalog で確認し、重複するなら取り込まず既存を案内する。

### ②' `--check-updates`（上流差分検知）

`skills-lock.json` の全エントリについて：

1. `source` / `skillPath` から上流の現行内容を取得し、SHA-256 を計算する。
2. `computedHash` と比較し、**一致＝最新** / **不一致＝上流更新あり** / **取得不能＝WARN** を一覧表示する。
3. 更新ありのエントリには「再取り込み（①〜④を再実行）」を**提案**する（自動では取り込まない）。

```
## rig import --check-updates

  hyperframes            heygen-com/hyperframes  ✓ 最新
  faceless-explainer     heygen-com/hyperframes  ⚠ 上流更新あり → /rig:import heygen-com/hyperframes --path skills/faceless-explainer/SKILL.md で再取り込み
  some-skill             owner/gone-repo         ? 取得不能（リポジトリ不在/権限）
```

### ③' `--all`（一括取り込み）

走査で見つかった候補全件について、②の判断（委譲/翻訳/知識のみ/取り込まない）を**各 skill ごとに subagent で並行実施**し、結果を**判断サマリ一覧**として1つの表で提示する：

```
## rig import --all（<source>・N 件）

| # | skill              | 判断       | 生成予定ブリック                        | 理由（1行）                         |
|---|--------------------|-----------|----------------------------------------|-------------------------------------|
| 1 | frontend-design    | 委譲       | instructions/frontend-design（routing） | CC skill としてそのまま動く          |
| 2 | tdd-workflow       | 翻訳       | recipes/tdd-strict ＋ persona 1件       | ゲートに組み込む判断を持つ           |
| 3 | writing-guide      | 知識のみ   | wiki [[writing-guide]]                  | 観点カタログのみ・フロー価値なし     |
| 4 | duplicate-skill    | 取り込まない | —                                      | 既存 `de-ai-smell` と重複            |
```

- **判断の独立反証**（表の提示前）：判断サマリを**別の subagent（反証役）**に渡し、`policies/independent-verification` の流儀で検査させる — 委譲できるものを翻訳にしていないか（native-first 違反）・ライセンス判断の誤り・既存ブリックとの重複見落とし。指摘があれば判断を修正してから表を出す（表の下に `判断は独立反証済み ✓` と1行付す）。
- **承認は一括で1回**（表を見て「この方針で全件進める / #N だけ除外 / 中止」を選ばせる）。個別の書き込み確認を N 回繰り返さない＝`--all` の存在意義。ただし **global（`--user`）書き込みを含む場合はその旨を表の下に明示**する。
- 承認後、各 skill を④の lock 記録まで処理し、**lock への書き込みは1回にまとめる**（エントリ N 件を1度の編集で追加）。
- 1件の失敗（取得不能・ライセンス不明で委譲不可等）は該当行に `[SKIP: 理由]` を付けて続行し、最後にまとめて報告する（全体を止めない）。
- `--dry-run --all` は判断サマリ一覧の提示で停止（書き込み・lock 記録なし）＝**まず全体像だけ見る**の推奨経路。

### ③ 提案と確認（書き込みは必ず確認）

翻訳方針・生成予定ブリックの一覧・保存先 tier・`skills-lock.json` への記録内容を提示し、**ユーザー承認後にのみ**書き込む（`--autonomous` でも確認は解除しない。`--dry-run` はここで停止）。同名 lock エントリが既存なら上書きせず差分提案にとどめる（冪等・非破壊）。

### ③'' import-gate（試用 — 取り込みにも determinism-by-gate）

承認後・lock 記録（④）の**前に**、生成ブリックが**実際に動くことを証明**する（「取り込んだ」でなく「取り込んで動いた」）。ブリック種別ごとの受け入れ試験：

| 生成物 | 試験 | 合格基準 |
|---|---|---|
| persona（reviewer） | サンプル diff（直近の実 diff か小さな合成 diff）で実際に subagent dispatch | `review-verdict` 契約を遵守（判定行・確信度・根拠3点＋証拠アンカー） |
| recipe | `plan <recipe> --json` ＋ `--validate`（＋可能なら `run --provider mock`） | errors 0・参照切れ 0（mock run は DONE） |
| instruction（委譲） | 委譲先 skill/command が現環境で解決できるか確認 | 委譲先が実在（不在なら「要インストール」を明記） |
| knowledge / wiki | `--validate` ⑤ wiki 衛生 | frontmatter スキーマ・リンク切れ 0 |

- **不合格 → 直してから lock**（acceptance-gate と同じ収束ループ・最大2回）。直せなければ該当 skill を `[SKIP: import-gate 不合格 — <理由>]` にして続行（壊れたブリックを lock に入れない）。
- `--dry-run` 時は試験しない（書き込みが無いため）。`--all` 時は試験も skill ごとに並行してよい（context-minimal）。

### ④ 出所記録（`skills-lock.json`）

取り込んだ skill を lock ファイルに記録する（既存スキーマと互換・追加フィールドは任意）：

```json
{
  "version": 1,
  "skills": {
    "<slug>": {
      "source": "<owner>/<repo>",
      "sourceType": "github",
      "skillPath": "skills/<name>/SKILL.md",
      "computedHash": "<取り込み時点の上流内容の SHA-256>",
      "importedAs": ["facets/instructions/<name>.md", "recipes/<name>.md"],
      "importedAt": "<YYYY-MM-DD>",
      "mode": "delegate|translate|knowledge"
    }
  }
}
```

- `computedHash` は**上流原文**のハッシュ（翻訳後の rig ブリックではない）＝ `--check-updates` の比較基準。
- `importedAs` で「上流のこの skill が rig のどのブリックになったか」を追跡可能にする（lock → brick の対応が消えない）。
- lock ファイルの置き場：project 取り込みは `<repo>/skills-lock.json`、rig 本体（shipped）取り込みは rig リポジトリ直下の `skills-lock.json`。

### ⑤ 検証と報告

生成ブリックを `--validate`（rig 本体なら `python3 scripts/validate.py`）で点検し、FAIL を直してから完了報告する：書き込んだパス・lock エントリ・使い方（`/rig:dev --recipe <name>` 等）・上流の再確認方法（`/rig:import --check-updates`）。

## 原則

- **native-first**：動くものは移植せず委譲する。翻訳は「rig のゲート/フローに組み込む価値がある判断・観点」だけ。
- **出所と根拠を残す**（捏造禁止と同根）：どの上流のどの版から来たかを lock に必ず記録する。ハッシュなしの取り込みはしない。
- **ライセンス確認**：上流のライセンスを確認し、翻訳物に出所を明記する。ライセンス不明・再配布不可なら委譲のみ（本文を持ち込まない）。
- **engine 不変・pack 上乗せ**：これは「ブリックを増やす」ジェネレータ群の1つであり、engine 改修ではない。
- 書き込み＝確認必須・冪等。上流の自動追従はしない（`--check-updates` は検知と提案まで）。
