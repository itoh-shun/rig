---
description: "rig — 統一入口。自然文のタスクを渡すと分類→recipe選択→隔離worktreeでの実装/レビュー→acceptance-gate→結果サマリまで自動で駆動する。status/diff/accept/discard/log/board/cockpit/stats/review/gc/audit/scan-secrets/scan-injection/digest/stream-checks/stale-refs/scan-destructive/instincts/gh のサブコマンドで実行状態を操作する。複数タスクを並行で進めても `board`/`cockpit` 一枚で全体像を見失わない。"
argument-hint: "\"<自然文タスク>\" | status [id] | diff [id] | accept [id] [--force] | discard <id> --yes | log [--limit N] | board [--all] | cockpit | stats [--recipe R] [--verifier P] [--last Nd] | review <id> --set p=v | gc [--older-than Nd] [--dry-run] | audit [--limit N] [--action A] [--since YYYY-MM-DD] | scan-secrets [paths…|--diff id] | scan-injection [paths…|--diff id] | digest [--period week|month] [--out PATH] | stream-checks [id] [--watch --interval N --max-passes M] | stale-refs [paths…] | scan-destructive [paths…|--diff id] | instincts [--add TEXT --evidence E --confidence C] [--mute ID|--expire ID|--decay|--inject-preview] | gh issue <n> | gh pr <n> review|fix | gh ci"
---

# rig — 統一入口（workbench）

**まず `rig:engine` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・context-minimal・facet 配置順・recipe スキーマ・知識層注入）に厳密に従うこと。** そのうえで本コマンドは `$ARGUMENTS` の先頭語で2つの経路に分岐する。

> **初回のみ**: `rig-wb` CLI が入っていないと Claude Code 外の provider（Codex / Cursor / plain terminal）から同じ workbench を叩けない。**`/rig:setup`** で pipx / uv / pip 経由で自動導入できる（`--yes` で確認省略・`--check` で検出のみ）。Claude Code の中だけで完結するなら不要——scripts/*.py が直接呼ばれるので `rig-wb` 無しでも動く。

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
| `board [--all]` | `facets/instructions/workbench-ops`（**全 task を一覧するダッシュボード**。複数タスクを並行で進めているときの単一の確認場所） |
| `cockpit` | `facets/instructions/workbench-ops`（**board・gate・drill・cost・auditを一画面に集約するMission Control**。read-only。次アクションを案内するのみで accept/discard 自体は実行しない） |
| `stats [--recipe R] [--verifier P] [--last Nd]` | `facets/instructions/workbench-ops`（過去 run の集計・reviewer のゴム印検知） |
| `review <task_id> --set <persona>=<verdict>` | `facets/instructions/workbench-ops`（review 系タスクの persona 別 verdict 記録） |
| `gc [--older-than <N>d] [--dry-run]` | `facets/instructions/workbench-ops`（視覚検証成果物（`.rig/runs/*/visual/`・`.rig/visual/adhoc/*`）の age-based 処分。既定14日・`--dry-run` で候補表示のみ） |
| `audit [--limit N] [--action A] [--since YYYY-MM-DD]` | `facets/instructions/workbench-ops`（`accept --force` 等の恒久監査ログ `.rig/audit.jsonl` の一覧・絞り込み） |
| `scan-secrets [paths…] [--diff <task_id>]` | `facets/instructions/workbench-ops`（決定論シークレットスキャン。gate 基準 `no_secret_leak` の機械センサーと同一実装。`--diff` で task worktree の差分のみを走査、抜粋は常にマスク済み） |
| `scan-injection [paths…] [--diff <task_id>]` | `facets/instructions/workbench-ops`（決定論プロンプトインジェクション・マーカースキャン。gate 基準 `no_injection_markers` の機械センサーと同一実装。不可視/bidi Unicode は fail-grade・指示上書きフレーズは warning-grade。引数なしは repo の prose 面（`.claude/rig.md`・knowledge・personas・`.rig/recipes/*.md`）、`--diff` で task worktree の差分＋prose 面を走査） |
| `digest [--period week\|month] [--out PATH]` | `facets/instructions/workbench-ops`（`.rig/` テレメトリのローリング集計ダイジェスト（runs・gate 合否・force-accept・ゴム印疑い・drill 検出率）を Markdown で出力。既定 week=直近7日） |
| `stream-checks [<task_id>] [--watch --interval N --max-passes M]` | `facets/instructions/workbench-ops`（実装中の軽量ストリーミングチェック。secret/injection/destructiveの3センサーをtask worktreeにその場で走らせfindingsをヒント表示。gateをブロックしない・常にexit 0） |
| `stale-refs [paths…]` | `facets/instructions/workbench-ops`（manifest・知識層の経年劣化検知。バッククォート引用の相対パス参照のうち実在しなくなったものをWARN列挙。exit 0） |
| `scan-destructive [paths…] [--diff <task_id>]` | `facets/instructions/workbench-ops`（決定論の破壊的コマンドスキャン。gate 基準 `no_destructive_operation` の機械センサーと同一実装。fail-grade/warning-gradeの2段階） |
| `instincts [--add TEXT --evidence E --confidence C] [--mute ID\|--expire ID\|--decay\|--inject-preview]` | `facets/instructions/workbench-ops`（セッション横断の継続的instinct学習層。未検証パターンをconfidence付きで記録・decay・次回セッション注入プレビュー） |
| `gh issue <n>` | `facets/instructions/gh-flow`（Issue を読んで分類→workbench へ） |
| `gh pr <n> review [--adversarial] [--comment]` | `facets/instructions/gh-flow`（`/rig:pr` 相当。既存 `recipes/pr-review` に委譲） |
| `gh pr <n> fix` | `facets/instructions/gh-flow`（PR 指摘を隔離 worktree で修正） |
| `gh ci` | `facets/instructions/gh-flow`（CI 状態確認） |

### ② 自然文タスク（上記のいずれにも一致しない場合）

`facets/instructions/workbench` に従い、①タスク分類（task_type）→②recipe 自動選択（`recipes/bugfix`\|`feature`\|`refactor`\|`documentation`\|既存 recipe への橋渡し）→③`patterns/isolated-worktree` に従った隔離 worktree での RUN →④`scripts/workbench.py gate` による acceptance-gate 判定→⑤結果サマリの5段を駆動する。ユーザーが recipe や step を明示しなくてもよい（明示したい場合は `/rig:dev --recipe <name> ...` を使う）。

## `/rig:dev` との使い分け

- **`/rig:go "<task>"`**（本コマンド）— 自然文だけで完結させたいとき。分類・recipe 選択・worktree 隔離・gate 判定を全自動で行う。
- **`/rig:dev --recipe <name> --only <step> ...`** — recipe・step・flag を自分で明示的に組み合わせたいとき（既存の PARSE 全 flag が使える）。

内部エンジンは共通（SKILL.md 一本）。本コマンドは workbench 経路（隔離 worktree ＋ 状態永続化 ＋ machine-gate）を既定にした入口という違いだけ。

## 複数タスクを並行で進める（ターミナルを増やさない）

1タスクずつ `/rig:go "<task>"` を打つ代わりに、**まとめて積んで並列実行**したいときは `/rig:queue`（別ターミナル不要・headless プロセスの並列実行エンジン）に積む：

```
/rig:queue add "ログイン画面のバグを直して"
/rig:queue add "在庫一覧に検索機能を追加して"
/rig:queue add "READMEをわかりやすくして"
/rig:queue go --provider rig --max-parallel 3
```

`go --provider rig` は各 item を `/rig:go "<task>"` 経由で dispatch するため、**各タスクは自動的に自分専用の isolated worktree に隔離される**（並列実行中のプロセス同士がファイルを取り合う心配がない）。積んだ側は accept しない（queue の verifier は「gate まで確定したか」を判定するだけ）——完了後は:

```
/rig:go board          # 今どのタスクがどこまで進んだか、1コマンドで一覧
/rig:go diff <id>      # 個別に差分確認
/rig:go accept <id>    # 個別に反映（他のタスクとぶつからない）
```

複数のターミナルを開いて「どれが何をしていたか忘れる」問題は、この `board` が単一の真実の情報源になることで解消する——実行がどこ（headless プロセス／このセッション）で起きていても、状態は必ず `.rig/runs/` に集約される。

## 例

```
/rig:go "ログイン画面のバグを直して"
/rig:go "このIssueを読んで実装して"          # 曖昧な場合は gh issue <n> を1問だけ確認
/rig:go "このPRを厳しめにレビューして"        # 対象 PR 番号を確認して gh pr <n> review --adversarial 相当へ
/rig:go status
/rig:go diff
/rig:go accept
/rig:go discard rig-20260704-153012-login-fix --yes
/rig:go log --limit 5
/rig:go gc --dry-run
/rig:go audit --limit 10
/rig:go scan-secrets --diff rig-20260704-153012-login-fix
/rig:go scan-injection --diff rig-20260704-153012-login-fix
/rig:go digest --period week
/rig:go gh issue 123
/rig:go gh pr 45 review
/rig:go gh pr 45 fix
/rig:go gh ci
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
