# instruction: init

リポジトリを rig 向けに初期化する。manifest・知識層ディレクトリ・`CLAUDE.md` の "Compact Instructions" 節・**このプロジェクトのフロー（1〜3本）**を**雛形生成**する。**すべて書き込み＝影響あるアクションなので、何をどこに作るか提示して確認を取ってから書く**（`--autonomous` でも init の書き込み確認は解除しない）。冪等：既存ファイルは上書きせず差分のみ追記/スキップする。

## 生成物

### ① manifest（`<repo>/.claude/rig.md`）

`skills/engine/manifests/_template.md` のテンプレ本体をコピーし、検出できた値を埋めて作る。

- `build`/`lint`/`test` は `package.json` / `build.gradle` / `Makefile` を自動検出して候補を埋める（不明なら空のままコメントを残す）。
- `branch.base` は `git remote show origin` の default branch。
- 既に `<repo>/.claude/rig.md` があれば**上書きしない**（「既存」と報告し、差分提案だけ示す）。

### ② 知識層ディレクトリ

- `<repo>/.claude/rig/knowledge/domain/` … ドメイン設計・ユビキタス言語・ADR を置く場所。
- `<repo>/.claude/rig/knowledge/accumulated/` … capture（§7）が学びを蓄積する場所。
- `<repo>/.claude/rig/recipes/` … `--save-recipe`（§4.3.2）の保存先（project tier のカスタム recipe）。
- `<repo>/.claude/rig/personas/` … project tier の `/rig:persona`（§5）の生成先。
- 各ディレクトリに用途を1行書いた `README.md`（または `.gitkeep`）を置いて空ディレクトリを成立させる。
- これで `/rig:init` 直後から `--save-recipe` / `/rig:persona` の書き込み先が存在し、「保存→一覧（`--list`）→再利用の輪」が初回から繋がる（保存先 dir 不在による失敗を防ぐ）。
- `.claude/` は `.gitignore` 対象のことがある。**コミットして共有したい場合は知識層を除外しないよう** `.gitignore` を確認し、必要なら除外解除を**提案**する（勝手に書き換えない）。

### ②-b `.gitignore` への `.rig/` 追加（workbench 実行状態）

`/rig:rig`（`patterns/isolated-worktree`）の run state は `<repo>/.rig/runs/` に書かれる。ローカル実行ログであり共有リポジトリにコミットする性質のものではないため、`.gitignore` に `.rig/` が無ければ**追加を提案**する（他の gitignore 提案と同様、勝手に書き換えず確認を取る）。既に `.rig/` または親パターン（`.rig` 等）でカバーされていれば提案しない。

### ③ CLAUDE.md "Compact Instructions" 節（圧縮で rig 状態を失わない第2経路）

`<repo>/CLAUDE.md` に "Compact Instructions" 節が無ければ、以下を**追記**する（既にあれば重複追記しない）。これは PreCompact フック（§6 run-continuity ④）と**同じ保全文の belt-and-suspenders**で、毎回の圧縮に自動適用される。

```markdown
## Compact Instructions

If a rig harness run is active when compacting, preserve in the summary:
- the rig run-status (recipe, current step + position, gate state, mode);
- the active recipe's remaining/done steps and the current step id;
- the acceptance contract in force (acceptance-gate criteria / goal-loop goal) and unresolved REJECT/conditions;
- the user's goal/intent, key decisions, and stuck-guard counters;
- the context-minimal discipline (real work is delegated to subagents; the parent only aggregates + gates).
After compaction, re-emit the rig run-status header and re-anchor to the current step before doing any work.
```

### ④ フローの組み立て（このプロジェクトを何で回すか）

manifest には既に `default_recipe` と `default_personas[]` があり、project tier の recipe 置き場
（`<repo>/.claude/rig/recipes/`）も `extends:` による N 段継承もある。**組み立ての機構は完成していて、
init がそれを使っていなかっただけ**——`default_recipe: "interactive"` のまま据え置かれたリポジトリは、
毎回ユーザーに recipe を選ばせ続ける。

**警告：使われないフローを生やすのは、生やさないより悪い。** 未使用の recipe は「検討して決めた結果」に
見える。次に来た人はそれを信じ、継承し、**一度も走らせたことのない経路に仕事を流す**。だから2つの規律を守る:

1. **1〜3本に絞る。** 4本目からはカタログであって既定ではない。人が温めておける本数を超える。
2. **根拠と当て推量を混ぜない。** 実績のあるリポジトリは実績から導く。無いリポジトリの提案は
   **「まだ根拠が無い」と明示**する（`package.json` の存在は未来についての推測であって、発見ではない）。

**手順**：

```bash
rig-wb wb suggest-flows          # 読むだけ。何も書かない
```

これが `.rig/runs.jsonl`（フロー完了テレメトリ）と `.rig/runs/*/`（workbench タスク・gate 結果・
persona 別 verdict）から次を出す:

- **実績のある recipe**（2回以上走ったもの）を回数順に、上限3本。gate の通過率・エスカレーション回数つき。
  上限で落ちた分・実績が薄い分（1回だけ）も**必ず列挙**する（黙って切ると「これで全部」に見える）。
- **実績のない新規リポジトリ**では project stack（`package.json` / `pyproject.toml` / `go.mod` /
  `Cargo.toml` / `build.gradle` / `pom.xml`）から shipped recipe を提案し、`[unevidenced]` と明示する。
- **一度でも REJECT を出した persona** だけを `default_personas` の候補にする。**5回以上走って REJECT が
  0 の persona は候補にせず、ゴム印の疑いとして表示する**（`wb stats` と同じ判定）。決して否と言わない
  reviewer を恒久投入すると、追従を配線してしまう。
- 貼り付け用の manifest フラグメント（`default_recipe` / `default_personas`）。

提案をそのまま manifest へ書き込まず、**②③ と同じく一覧で提示して確認を取る**。承認されたら
`.claude/rig.md` の `default_recipe` / `default_personas` を書き換える（他のキーは触らない）。

**project recipe を新規に作るのは、shipped recipe のどれでも表せない場合だけ**にする。作る場合も
`extends:` で shipped recipe を継承し、差分だけ書く（`--save-recipe` の保存先は ② で作った
`<repo>/.claude/rig/recipes/`）。「まず1本を実際に走らせ、足りなければ次の run で `--save-recipe`」の順序を
案内する——**走らせる前に生やさない**。

## 手順

1. **検出**：`git rev-parse --show-toplevel` で repo root、ビルド系ファイル・default branch を検出する。
2. **フロー実績の収集**：`rig-wb wb suggest-flows` を実行する（read-only）。CLI が使えない環境では
   `.rig/runs.jsonl` を直接読み、同じ上限（3本）と同じ区別（実績あり / `[unevidenced]`）を自力で適用する。
3. **提案**：作る/追記するファイルとその内容草案を一覧で提示する（manifest / 知識層 dir / CLAUDE.md 節 /
   フロー既定）。既存分は「スキップ」と明示。フローは**根拠（何回走ったか・gate 通過率）を必ず添える**。
4. **確認**：ユーザー承認後にのみ書き込む（`--autonomous` でも確認必須）。
5. **報告**：何を作成/追記/スキップしたかを報告し、次の一歩（`/rig:dev` で着手、`--validate` で点検、
   実績が溜まったら `suggest-flows` を再実行して既定を見直す）を案内する。

## 原則

- **冪等・非破壊**：既存ファイルは上書きしない。追記は重複を避ける。
- init は scaffold だけ。フローは回さない（実装/レビューは `/rig:dev` 等の役割）。
