# instruction: init

リポジトリを rig 向けに初期化する。manifest・知識層ディレクトリ・`CLAUDE.md` の "Compact Instructions" 節を**雛形生成**する。**すべて書き込み＝影響あるアクションなので、何をどこに作るか提示して確認を取ってから書く**（`--autonomous` でも init の書き込み確認は解除しない）。冪等：既存ファイルは上書きせず差分のみ追記/スキップする。

## 生成物

### ① manifest（`<repo>/.claude/rig.md`）

`skills/rig/manifests/_template.md` のテンプレ本体をコピーし、検出できた値を埋めて作る。

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

## 手順

1. **検出**：`git rev-parse --show-toplevel` で repo root、ビルド系ファイル・default branch を検出する。
2. **提案**：作る/追記するファイルとその内容草案を一覧で提示する（manifest / 知識層 dir / CLAUDE.md 節）。既存分は「スキップ」と明示。
3. **確認**：ユーザー承認後にのみ書き込む（`--autonomous` でも確認必須）。
4. **報告**：何を作成/追記/スキップしたかを報告し、次の一歩（`/rig:dev` で着手、`--validate` で点検）を案内する。

## 原則

- **冪等・非破壊**：既存ファイルは上書きしない。追記は重複を避ける。
- init は scaffold だけ。フローは回さない（実装/レビューは `/rig:dev` 等の役割）。
