---
description: "rig — 統一入口。自然文のタスクを渡すと分類→recipe選択→隔離worktreeでの実装/レビュー→acceptance-gate→結果サマリまで自動で駆動する。status/diff/accept/discard/log/gh のサブコマンドで実行状態を操作する。"
argument-hint: "\"<自然文タスク>\" | status [id] | diff [id] | accept [id] [--force] | discard <id> --yes | log [--limit N] | gh issue <n> | gh pr <n> review|fix | gh ci"
---

# rig — 統一入口（workbench）

**まず `rig` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・context-minimal・facet 配置順・recipe スキーマ・知識層注入）に厳密に従うこと。** そのうえで本コマンドは `$ARGUMENTS` の先頭語で2つの経路に分岐する。

```
$ARGUMENTS
```

## 経路分岐

### ① サブコマンド（先頭語が一致する場合）

| 先頭語 | 委譲先 |
|---|---|
| `status [<task_id>]` | `facets/instructions/workbench-ops`（実行状態表示） |
| `diff [<task_id>]` | `facets/instructions/workbench-ops`（差分表示） |
| `accept [<task_id>] [--force]` | `facets/instructions/workbench-ops`（メイン作業ツリーへ反映） |
| `discard <task_id> [--yes]` | `facets/instructions/workbench-ops`（worktree/branch 破棄） |
| `log [--limit N] [--json]` | `facets/instructions/workbench-ops`（実行ログ一覧） |
| `gh issue <n>` | `facets/instructions/gh-flow`（Issue を読んで分類→workbench へ） |
| `gh pr <n> review [--adversarial] [--comment]` | `facets/instructions/gh-flow`（`/rig:pr` 相当。既存 `recipes/pr-review` に委譲） |
| `gh pr <n> fix` | `facets/instructions/gh-flow`（PR 指摘を隔離 worktree で修正） |
| `gh ci` | `facets/instructions/gh-flow`（CI 状態確認） |

### ② 自然文タスク（上記のいずれにも一致しない場合）

`facets/instructions/workbench` に従い、①タスク分類（task_type）→②recipe 自動選択（`recipes/bugfix`\|`feature`\|`refactor`\|`documentation`\|既存 recipe への橋渡し）→③`patterns/isolated-worktree` に従った隔離 worktree での RUN →④`scripts/workbench.py gate` による acceptance-gate 判定→⑤結果サマリの5段を駆動する。ユーザーが recipe や step を明示しなくてもよい（明示したい場合は `/rig:dev --recipe <name> ...` を使う）。

## `/rig:dev` との使い分け

- **`/rig:rig "<task>"`**（本コマンド）— 自然文だけで完結させたいとき。分類・recipe 選択・worktree 隔離・gate 判定を全自動で行う。
- **`/rig:dev --recipe <name> --only <step> ...`** — recipe・step・flag を自分で明示的に組み合わせたいとき（既存の PARSE 全 flag が使える）。

内部エンジンは共通（SKILL.md 一本）。本コマンドは workbench 経路（隔離 worktree ＋ 状態永続化 ＋ machine-gate）を既定にした入口という違いだけ。

## 例

```
/rig:rig "ログイン画面のバグを直して"
/rig:rig "このIssueを読んで実装して"          # 曖昧な場合は gh issue <n> を1問だけ確認
/rig:rig "このPRを厳しめにレビューして"        # 対象 PR 番号を確認して gh pr <n> review --adversarial 相当へ
/rig:rig status
/rig:rig diff
/rig:rig accept
/rig:rig discard rig-20260704-153012-login-fix --yes
/rig:rig log --limit 5
/rig:rig gh issue 123
/rig:rig gh pr 45 review
/rig:rig gh pr 45 fix
/rig:rig gh ci
```

## 安全側の原則

- AI の変更は **accept されるまで本体の作業ツリーに触れない**（`patterns/isolated-worktree`）。
- **acceptance-gate が fail/pending の間 accept はコードが拒否する**（`scripts/workbench.py accept`。「できました」の自己申告だけでは完了扱いにしない）。
- **discard は task-id 明示 ＋ 変更ファイル一覧の提示 ＋ `--yes` 確認**の三段。run log は消えない。
- GitHub への write（PR 作成・コメント投稿・push）は常に明示操作を経る（read は即応）。

## run-continuity（SKILL.md §6）

RUN 中は各ターン冒頭に次の run-status ヘッダを1行必ず再掲すること。中断・質疑・tool 出力の直後でも省かない（可視化＝駆動の証拠）:

```
▸ rig | task: <task_id> | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending [(try N/K)]|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
