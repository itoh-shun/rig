# rig

**Claude Code のための、品質保証つき AI ワークベンチ。** タスクに応じて必要なハーネスを自動構成し、隔離された worktree で変更を行い、acceptance-gate で検証し、最後にユーザーが差分を accept / discard できる。

> 🇬🇧 English version: [README.md](./README.md)

## 1. rig とは何か

自然文でタスクを頼むだけでいい。rig がタスクの種類（バグ修正/機能追加/リファクタ/レビュー/ドキュメント/…）を判定し、必要なブリック（persona / instruction / pattern — LEGO 式の部品）を組み合わせてハーネスを合成し、**現在の作業ツリーとは隔離された git worktree**で作業し、明示的な**受け入れ基準**（意図の充足・無関係な差分がないか・リスク要約・テスト・型エラー・secret 漏洩がないか等）で検証し、`accept` が呼ばれるまで本体には一切触れない。「できました」という自己申告は完了の根拠にならない——根拠は常にゲートの合否。

rig の本当の価値は、AI を動かすこと自体ではない。AI に作業を任せるときの危険な部分を、**隔離・検証・測定・記録・反映制御**によって構造的に潰すことにある。

## 2. 30秒で使う

```bash
/rig:rig "ログインバグを直して"
/rig:rig "このPRを厳しめにレビューして"
/rig:rig "今の変更が安全か確認して"
```

最初はこれだけでいい。裏側では、タスクを分類 → 対応する recipe を選択 → 隔離 worktree を作成（レビュー等の読み取り専用タスクは省略）→ 実装・テスト → acceptance-gate 判定 → 次アクションつきのサマリを返す:

```
/rig:rig diff       # 何が変わったか、なぜ安全か（あるいは危ういか）を確認
/rig:rig accept     # 作業ツリーへ反映（gate が未達なら拒否される）
/rig:rig discard    # 試みを破棄（作業ツリーは最初から一切触れられていない）
```

## 3. なぜ安全か

- **隔離 worktree であって、あなたのブランチではない。** タスクごとに専用の git worktree（`patterns/isolated-worktree`）と使い捨てブランチを作る。rig は作業ツリーに直接書き込まない——失敗しても中断しても、あなたの手元は何も汚れない。
- **ゲートは自己申告ではなくコード。** `scripts/workbench.py accept` は、受け入れ基準が `failed`/`pending` のまま残っている task を機械的に拒否する。AI が「できました」と言っても何も変わらない——記録された `passed` だけが accept を許可する。
- **verifier は構造的に read-only。** reviewer/verifier subagent はツールアクセスを制限して起動する（`claude --allowedTools Read,Grep,Glob`・`codex --sandbox read-only`）——書き込み・コミット・破壊的コマンドの実行はできない。これはお願いではなくプロセスレベルの強制（`scripts/orchestrate.py probe`/`selftest` が検証済み）。
- **accept も discard も必ず明示操作。** `accept` はまず `accept_requirements` チェックリストを表示する——`worktree_exists`/`base_branch_recorded`/`diff_summary_generated` は**構造的な前提**であり `--force` でも上書きできない。そのうえで **staged**（未コミット）として反映する——コミットは常に人が行う。`discard` は task-id の明示と `--yes` 確認を必須とし、常に破棄対象の変更ファイル一覧を先に見せる。
- **安全側の検知は即ブロックにつながる。** 依頼にない diff・説明のないテスト失敗・secret らしき文字列・破壊的操作・レビュー未実施の認証/認可変更・説明のない公開 API 変更——いずれも該当 criterion を `failed` にし、確認するまで accept を止める。
- **実行履歴は消えない。** `discard` は worktree/branch を削除するが run log（`.rig/runs/<task-id>/`）は残る。

## 4. 基本フロー

```
自然文のタスク
        │
        ▼
①  分類（bugfix / feature / refactor / review / documentation / security_review / …）
        │
        ▼
②  対応する recipe を選択＋選択理由を表示（当て推量ではなく1行バナー）
        │
        ▼
③  隔離 worktree を開き、recipe を実行（実装/テスト/レビューを subagent に dispatch）
        │
        ▼
④  acceptance-gate：意図の充足・差分スコープ・リスク・テスト・secret・重大度付き所見を確認
        │
        ▼
⑤  サマリ＋次アクション：/rig diff・/rig accept・/rig discard
```

| コマンド | 内容 |
|---|---|
| `/rig:rig "<タスク>"` | 分類 → recipe 選択 → 隔離 worktree での実行 → acceptance-gate → サマリ |
| `/rig:rig status [id]` | 現在（または最新）の task：Steps チェックリスト・Gate チェックリスト・未反映差分・次アクション |
| `/rig:rig diff [id]` | 変更ファイル一覧＋Summary/Risk/Tests/Unrelated diff/Recommended |
| `/rig:rig accept [id] [--force]` | 作業ツリーへ反映（staged）——gate が pass していないと拒否される |
| `/rig:rig discard <id> --yes` | worktree/branch を削除（run log は残る） |
| `/rig:rig log [--limit N]` | 過去 task の履歴（入力・recipe・gate 結果） |
| `/rig:rig board [--all]` | **アクティブな全 task を一覧する単一ダッシュボード**——複数タスクを並行で進めているとき、ターミナルをいくつ開いていても、`/rig:queue` 経由でも、確認場所はここ一つ |
| `/rig:rig stats` | 過去 run の集計：accept 率・gate 結果・reviewer のゴム印検知 |
| `/rig:rig gh issue/pr/ci …` | GitHub Issue/PR/CI を入力に — §13 参照 |
| `/rig:dev --recipe <name> --only <step> ...` | 上級者向け入口：recipe/step/flag を自分で明示する（エンジンは共通）— §11 参照 |

`new` の直後には**選択理由バナー**が必ず出るので、なぜその recipe が選ばれたか迷わない：

```
▸ rig
task: ログインバグを直して
detected: bugfix
recipe: bugfix — 「バグ」「直して」を検出
mode: isolated worktree
gate: standard + bugfix
```

## 5. isolated worktree

```
<repo の親>/rig-worktrees/<repo名>/rig-YYYYMMDD-HHMMSS-<slug>/   ← 使い捨て worktree + branch
<repo>/.rig/runs/rig-YYYYMMDD-HHMMSS-<slug>/                      ← run state（discard 後も残る）
  task.json        task_id / 入力 / task_type / recipe / base branch+commit / worktree path / status
  steps.json       step ごとの進行状態
  acceptance.json  {task_id, task_type, presets, status, checks: [{name, status, detail}]}
  review.json      review タスクの persona 別 verdict（/rig:rig stats に反映）
  plan.md / diff.md / log.md / final.md   モデルが書く散文（計画・差分要約・決定・まとめ）
```

読み取り専用のタスク（レビュー・まだ直すと決まっていない調査）は `--no-worktree` で worktree を丸ごと省略できる。設計の詳細は [`patterns/isolated-worktree.md`](./skills/rig/patterns/isolated-worktree.md) を参照。

### 複数タスクを並行で進める（ターミナルを増やさず一括把握）

隔離が task 単位で完結しているため、**複数タスクを同時に走らせても構造的に安全**（別 worktree・別 branch）。`/rig:rig "<task>"` を1つずつ打つ代わりに、実際に並列実行したいなら queue に積んで一括 GO する：

```bash
/rig:queue add "ログイン画面のバグを直して"
/rig:queue add "在庫一覧に検索機能を追加して"
/rig:queue add "READMEをわかりやすくして"
/rig:queue go --provider rig --max-parallel 3   # 独立した headless プロセスを3つ並列実行
```

`--provider rig` は各 queue item を `/rig:rig "<task>"` 経由で dispatch するため、直接 `/rig:rig` を打ったときと同じように各タスクが自動的に隔離される——並列実行中のプロセス同士がファイルを取り合う心配がない。queue 自身の verifier は「gate が確定したか」「isolated worktree 内で完結し本体に書き込んでいないか」を確認するだけで、**ユーザーの代わりに accept はしない**。完了後は：

```bash
/rig:rig board       # どのタスクがどこまで進んだか、1コマンドで一覧（どの端末・プロセスが実行したかに関わらず）
/rig:rig diff <id>   # 個別に確認してから diff/accept/discard
```

「ターミナルを5つ開いてどれが何をしていたか忘れた」を直接解消するのがこの `board`——実行の裏側がどうであれ、状態は必ず一箇所に集約される。

### 視覚検証のスクリーンショット

`visual-verify`（UI diff 確認）と `design-audit`（Playwright での画面取得）はいずれもスクリーンショットを生成する。これらは判断のための使い捨て証拠であって成果物ではない——結論は常に散文（`diff.md`）に残る：

```
<repo>/.rig/runs/<task-id>/visual/            ← task 紐づき（/rig:rig 経由で実行）
<repo>/.rig/visual/adhoc/<ts>-<slug>/         ← ad-hoc（例: 単独の /rig:design <url> 監査）
```

`discard` は task の `visual/` を即時削除する（run log の JSON/MD は残る）。それ以外——accept 済み task の screenshot も含め——は経過日数で処分する：

```bash
python3 scripts/workbench.py gc --dry-run     # 14日超の対象をプレビュー
python3 scripts/workbench.py gc               # 削除する
```

詳細ルールは [`patterns/visual-artifacts.md`](./skills/rig/patterns/visual-artifacts.md) を参照。

## 6. acceptance-gate

全 task は `standard`（全 task_type 共通）＋ task_type 別プリセットの合成で基準リストを持つ（正本は `scripts/workbench.py gates`）：

| preset | 上乗せ対象 | 基準の例 |
|---|---|---|
| `standard` | 全 task_type | `task_intent_satisfied`・`no_unrelated_diff`・`diff_summary_written`・`risk_summary_written`・`tests_pass_or_explained`・`no_type_errors_or_explained`・`no_secret_leak`・`no_destructive_operation` |
| `bugfix` | bugfix, performance | `bug_cause_identified`・`fix_is_minimal`・`regression_test_added_or_explained`・`existing_behavior_preserved`・`no_unrelated_refactor` |
| `feature` | feature, test | `requirement_summary_written`・`implementation_matches_requirement`・`tests_added_or_explained`・`public_api_changes_documented`・`migration_or_backward_compatibility_considered` |
| `refactor` | refactor | `behavior_boundaries_identified`・`no_unintended_behavior_change`・`tests_confirm_behavior_preserved`・`no_unrelated_refactor`・`public_api_changes_documented_if_any` |
| `review` | review | `findings_are_concrete`・`severity_labeled`・`file_references_included`・`blocking_and_non_blocking_separated`・`false_positive_risk_considered` |
| `security` | security_review（review に上乗せ） | `authn_authz_impact_checked`・`user_input_flow_checked`・`secret_exposure_checked`・`unsafe_eval_or_shell_checked`・`dependency_risk_checked` |

各基準は根拠つきで `passed` / `failed` / `warning` / `skipped` として記録する：

```bash
python3 scripts/workbench.py gate <task_id> --set no_type_errors_or_explained=passed --set tests_added_or_explained=warning:"既存テストのみで新規追加なし"
```

gate 全体は `passed` / `passed_with_warnings` / `failed` / `pending` / `skipped` に集約される。`failed` か `pending` が1件でも残っていれば `accept` は機械的に拒否される。`warning` は accept を止めないが、常に提示され黙って握りつぶされることはない。

## 7. diff / accept / discard

**`/rig:rig diff`** は `diff.md` の `## Summary` / `## Risk` / `## Tests` / `## Unrelated diff` 見出しを構造化して表示し、末尾に **コードが gate 状態から算出する** `Recommended:` 行を付す（モデルが書く行ではないので希望的観測が入らない）：

```
## rig diff: rig-20260704-153012-login-fix
Changed files:
  M  src/auth/login.ts
  M  src/auth/login.test.ts

Summary:
  ログインでメールアドレスが大文字を含む場合に失敗する不具合を修正。
Risk:
  低。変更はメールアドレスの正規化処理に限定。
Tests:
  大文字小文字を区別しないログインの回帰テストを追加。
Unrelated diff:
  検出なし。

Recommended:
  accept して問題ありません。
```

**`/rig:rig accept`** はまず `accept_requirements` チェックリストを表示する：

```
## rig accept: rig-20260704-153012-login-fix — accept_requirements
  ✓ worktree_exists
  ✓ base_branch_recorded
  ✓ diff_summary_generated
  ✓ acceptance_gate_not_failed
  ✓ no_unrelated_diff
```

`worktree_exists`/`base_branch_recorded`/`diff_summary_generated` は**構造的な前提**——`diff.md` が無ければ accept できない、`--force` でも例外なし。`acceptance_gate_not_failed`/`no_unrelated_diff` は判断が伴う項目で `--force` による上書きが可能（`forced: true` として記録される＝消えない）。チェックリストを通過したら、task branch を作業ツリーへ **squash merge（staged・コミットなし）**で反映する。

**`/rig:rig discard <id> --yes`** は常に変更ファイル一覧を先に表示する（`--yes` なしは削除しないプレビュー）。worktree/branch を削除するが run log（`.rig/runs/<task-id>/`）は残る。

## 8. run-continuity

途中で別の質問を挟んでも、静かにハーネスから外れることはない。RUN 中の各ターンは状態ヘッダを再掲する：

```
▸ rig | task: rig-20260704-153012-login-fix | recipe: bugfix | step: test (4/7) | gate: pending | mode: isolated worktree
```

中断（脱線質問・tool 呼び出し・長い間）があっても次のターンは必ず再アンカーする——ヘッダを再掲し、どの recipe のどの step を実行中かを再宣言してから続きに戻る（静かに素の直接作業へ切り替えない）。**コンテキスト圧縮も生き延びる**：同梱の `PreCompact` フックが run-state の保全指示を注入し、`/rig:init` は同じ保全文を CLAUDE.md "Compact Instructions" にも置ける。

## 9. reviewer drill

`/rig:drill` は reviewer の品質を意見ではなく数字で測る。既知のバグ class（認可漏れ・インジェクション・N+1・破壊的変更・片道 migration・テスト欠落…）を使い捨て diff に注入し、review fan-out を実行し、reviewer には見せない答案キーと突き合わせて採点する。

```
# Drill Result
Persona: strict_senior_engineer

## Score
- Detection rate: 82%
- False positive rate: 12%
- Severity accuracy: 76%
- Blocking accuracy: 81%
- Explanation quality: 70%

## Missed Issues
1. SQL injection risk in search query (src/search.py:88)
2. Missing authorization check in user update endpoint (src/api/users.py:120)

## Recommended Persona Updates
- [strengthen_security_focus] security 系の見逃しが2件以上 — セキュリティ観点の優先順位を引き上げる
- [adjust_severity_rule] severity accuracy 76%（閾値80%未満）— 重大度判断基準を明文化する
```

reviewer ごとに6指標：`true_positive` / `false_positive` / `false_negative` / `severity_accuracy`（付けた重大度が種の期待値と一致するか）/ `blocking_accuracy`（Blocking/Non-blocking の配置）/ `explanation_quality`（修正案が具体的か、一般論か）。`Recommended Persona Updates` は固定4カテゴリ（`add_checklist_item`/`adjust_severity_rule`/`add_false_positive_guard`/`strengthen_security_focus`）からのみ選ぶ——曖昧な感想ではなく run をまたいで集計できる形。`--replay <persona>` はペルソナ編集後にアーカイブ済み diff へ再実行し新旧 verdict を差分表示する。本物のコードには一切触れない（全て使い捨て worktree）。

## 10. telemetry

```bash
python3 scripts/workbench.py stats                          # 全体
python3 scripts/workbench.py stats --recipe bugfix           # recipe 絞り込み
python3 scripts/workbench.py stats --verifier security-reviewer --last 30d
```

```
## rig stats
Runs: 42
Accepted: 27
Discarded: 8
Failed gate: 7

Most used recipes:
- bugfix: 18
- review: 11
- feature: 8

Gate results:
- passed: 24
- passed_with_warnings: 11
- failed: 7

Verifier behavior:
- strict_senior_engineer: 14 runs, 6 rejects
- product_reviewer: 6 runs, 0 rejects

Warning:
product_reviewer has 0 rejects across 6 runs. Possible rubber-stamp behavior.
```

`/rig:rig review <task_id> --set <persona>=<APPROVE|REJECT|APPROVE_WITH_CONDITIONS>` で記録した verdict がここに集計される——review タスクの結果が確定するたびに記録しておくと、何でも通す reviewer を rig が検知してくれる。既存の `.rig/runs.jsonl`（`scripts/orchestrate.py runs` が読むエンジン全体の実行テレメトリ）とは別物——`workbench.py stats` は workbench task のライフサイクル（accept/discard/gate 結果）専用。

## 11. advanced commands

### コマンド分類

| tier | コマンド |
|---|---|
| **Core** | `/rig:rig`、`/rig:talk`、`/rig:dev`、`/rig:rig status\|diff\|accept\|discard` |
| **Quality** | `/rig:drill`、`/rig:rig stats\|review`、`/rig:pr`（既存 PR レビュー入口） |
| **Knowledge** | `/rig:import`、`/rig:export`、`/rig:catalog`、`/rig:knowledge`、`/rig:persona` |
| **Planning** | `/rig:goal`、`/rig:design`、`/rig:brainstorm`、`/rig:tasks` |
| **Experimental**（中身は本物・配送が遊び心） | `/rig:magi`、`/rig:roast`、`/rig:sage`、`/rig:party`、`/rig:movie`、`/rig:coin`、`/rig:duck`、`/rig:pre-mortem` |

日常的に必要なのは Core と Quality。それ以外は opt-in の追加機能——全ブリック目録は [`skills/rig/SKILL.md`](./skills/rig/SKILL.md) §2 を参照。

### install

本リポジトリには `.claude-plugin/marketplace.json` を同梱しているので、marketplace 経由でインストールできる。プラグイン名は `rig`、marketplace 名は `itoshun-local-plugins`。

```bash
# A) GitHub から（推奨）
/plugin marketplace add itoh-shun/rig
/plugin install rig@itoshun-local-plugins

# B) ダウンロード（ZIP / clone）から
/plugin marketplace add /path/to/rig
/plugin install rig@itoshun-local-plugins

# C) --plugin-dir（開発・テスト用）
cd /path/to/rig && claude --plugin-dir .   # 編集後の再読み込み: /reload-plugins
```

### 上級者向け入口: `/rig:dev`

`/rig:rig "<task>"` は分類と recipe 選択を自動でやる。`/rig:dev` は同じエンジンを recipe・step・flag すべて明示して使う入口：

```bash
/rig:dev --plan --only review "現在の変更"        # ドライラン：構成だけ確認
/rig:dev --only review                            # 3-way 並列レビューを実行
/rig:dev --recipe release-flow --design "機能X"
/rig:dev --recipe hotfix --issue 1234             # 緊急修正を最短経路で
```

| flag | 意味 |
|---|---|
| `--recipe <name>` | shipped/user/project の recipe を名前で指定 |
| `--only`/`--from`/`--to`/`--skip <step>` | 実行範囲のスライス・除外 |
| `--design` / `--review` / `--tdd` | 該当 step を強制 ON（既定は size-aware） |
| `--issue <id>` | 既存 Issue を intake 入力に |
| `--plan` | 合成ハーネスを提示して停止（ドライラン） |
| `--autonomous` | step ゲートを省略（capture ゲート・acceptance-gate は解除されない） |
| `--workflow` | ultracode Workflow バックエンドを使用（opt-in・重い多段時のみ） |
| `--save-recipe <name>` | 合成結果を recipe として保存 |
| `--capture` | 学びを確認ダイアログなしで知識層へ |
| `--list` / `--validate` | ブリック/recipe/flag 一覧、または構造 doctor（いずれも RUN 前に停止） |
| `--adversarial` | 敵対的レビュー step を追加 |
| `--cross-llm` | 他社 LLM が読む前提のコード/レビュー規律を注入 |
| `--persona <name>` | カスタム reviewer persona を review fan-out に追加 |
| `--verify-findings` | REJECT 根拠を独立した `finding-verifier` で敵対的検証 |
| `--global` | `--list`/`--validate` を全 tier 横断に拡大 |

flag・ブリックの完全な一覧は [`skills/rig/SKILL.md`](./skills/rig/SKILL.md) §2〜§3 が正本（README には複製しない＝`--validate` が守る目録ドリフト防止の原則）。

### manifest・知識層

`<repo>/.claude/rig.md` を置くと build/lint/test コマンド・branch/CI 戦略・reviewer・本番影響検知パターン・既定 recipe・既定 reviewer persona 等を設定できる（`skills/rig/manifests/_template.md` 参照）。知識層（`~/.claude/rig/knowledge/{methodology,ai-quirks}/`、`<repo>/.claude/rig/knowledge/domain/`）は全 RUN に注入され、実行を重ねるごとに蓄積される。

### 横断利用（CLI として）

決定論ランナー `scripts/orchestrate.py` は shim を1回置けばどのディレクトリからでも呼べる：

```bash
python3 scripts/orchestrate.py install-shim          # → ~/.local/bin/rig（symlink）
rig models                                           # 利用可能プロバイダ探索
rig probe --provider codex                           # 疎通テスト（read-only サンドボックス強制の実証も兼ねる）
rig run review-only --provider rig --verifier-provider codex
```

`$RIG_HOME` で install 先を上書き、`<cwd>/.rig/recipes/<name>.md` が同名 built-in recipe をプロジェクト overlay、recipe の `checks:` は呼び出し元プロジェクト（rig リポジトリではない）の cwd で実行される。

## 12. recipes / facets / steps

エンジン（`skills/rig/SKILL.md`)は起動時に4種のブリックを合成する：**persona**（誰が判定するか）・**instruction**（何をするか）・**pattern**（どう dispatch・gate するか）・**recipe**（step の束）。task_type の自動ルーティングは4つの shipped recipe＋既存資産への native 委譲で構成される：

| recipe | 内容 |
|---|---|
| `bugfix` / `feature` / `refactor` / `documentation` | workbench の既定4本（§4）— inspect → … → acceptance |
| `review-only` | 現在の変更を3観点で並列レビュー |
| `pr-review` | 既存 PR のレビュー（GitHub MCP 取得） |
| `debug` | 原因調査重視（reproduce→isolate→implement→verify） |
| `release-flow` | intake→design?→implement→verify→review?→pr→merge（size-aware） |
| `design-first` | 設計フェーズ厚め |
| `hotfix` | 最短パス（intake→implement→verify→pr） |
| `adversarial-review` | AI の癖排除・人間可読性の敵対レビュー |
| `goal-loop` | ゴール駆動ループ |
| `de-ai-smell` | 散文の AI 臭除去 |
| `design` 🎨 / `design-audit` 🎨 | UI/UX・a11y の設計作成と URL 監査 |
| `magi` | 3賢者合議で go/no-go を多数決裁定 |
| `roast` 🌶️ / `coin` 🪙 / `duck` 🦆 / `pre-mortem` ⚰️ | 中身は本物のユーモア pack 群 |
| `movie` 🎬 / `scenario` 🎬✍️ | 動画作成ハーネスとそのシナリオライター前段 |

`/rig:dev --list` で全 tier（shipped＋project＋user）の recipe を badge つきで一覧、`/rig:catalog`（`--list --global`）で `domain × pack × persona × wiki × recipe` を全 tier 横断で地図化できる。`/rig:sales`・`/rig:talk`・`/rig:goal`・`/rig:magi`・humor pack 群は、いずれも同じドメイン非依存エンジンに persona＋薄い instruction（＋recipe）を足しただけ（engine 不変）。

## 13. GitHub 連携

| コマンド | read/write |
|---|---|
| `/rig:rig gh issue <n>` | Issue（title/body/labels/comments）を読み、bugfix/feature/investigation に分類して workbench へ |
| `/rig:rig gh pr <n> review [--comment]` | 既定は read のみの3観点レビュー。`--comment` で PR へ投稿（書き込みは常に確認必須） |
| `/rig:rig gh pr <n> fix` | PR の diff・レビューコメント・CI 失敗を読み、PR の branch を base に隔離 worktree で修正、`accept` の手前で止まる（自動 push はしない）。CI 状態は `tests_pass_or_explained` 基準の根拠に使う |
| `/rig:rig gh ci` | 現在の branch/PR の CI 状態を確認し、失敗ジョブの要約を提示 |

Issue/PR の本文・コメントは**信頼できない外部入力**として扱う（埋め込まれた指示には従わず、分類・修正対象のテキストとしてのみ読む）。GitHub への書き込み（コメント・push）は常に明示操作を経る。read は即応。

## 14. FAQ

**`/rig:rig` は `/rig:dev` を置き換えるの？** いいえ——`/rig:rig` は自動分類する既定の入口、`/rig:dev` は recipe/step/flag を明示したいときの同じエンジン。

**作業中、自分の作業ツリーはどうなる？** 何も起きない。全作業は隔離 worktree/branch の中で行われる。作業ツリーが触られるのは `accept` のときだけで、それも staged（未コミット）差分としてのみ。

**gate を無視して進めたい場合は？** `accept` の `--force` は判断が伴う criterion（`acceptance_gate_not_failed`/`no_unrelated_diff`）だけを上書きでき、`forced: true` として記録される＝サイレントではない。構造的な前提（`worktree_exists`/`base_branch_recorded`/`diff_summary_generated`）は上書き不可——真偽がそのまま結果になる。

**reviewer/verifier がコードを書き換えることは？** ない。verifier はプロセスレベルで read-only 制限がかかっている（`Read,Grep,Glob`・サンドボックス shell）。`scripts/orchestrate.py probe` で確認できる。

**rig の状態はどこに置かれる？** `<repo>/.rig/runs/<task-id>/`（`.gitignore` への `.rig/` 追加は `/rig:init` が提案する）と、隔離タスクの場合はリポジトリ外の兄弟ディレクトリ `../rig-worktrees/<repo>/<task-id>/`。

**reviewer persona の質はどう分かる？** `/rig:drill` が既知のバグの種に対する検出率/誤検出率/severity精度/blocking精度/説明品質を採点する。`/rig:rig stats` は5run以上でREJECTゼロの reviewer をゴム印疑いとして警告する。

**複数タスクを同時に走らせたら？** それぞれ専用の worktree と branch（`rig/<task-id>`）を持つので衝突しない。`accept` はメイン作業ツリーに対して行うため、1つ accept してコミットしてから次を accept する（作業ツリーがクリーンでないと accept 自体が拒否されるので、この順序は安全側に強制される）。

**ターミナルをいくつも開かずに、1セッションで複数タスクを並行開発できる？** できる——§5「複数タスクを並行で進める」を参照。`/rig:queue add` で積んで `/rig:queue go --provider rig --max-parallel N` で並列実行（各タスクは自動的に隔離される）、そのうえで `/rig:rig board` を見れば、N個のターミナルの状態を頭の中で追う代わりに一箇所で全体を確認できる。

## ドキュメント

- [`skills/rig/SKILL.md`](./skills/rig/SKILL.md) — エンジン本体（PARSE/RESOLVE/COMPOSE/RUN の全仕様・rationalization 表・red flags）
- [`skills/rig/patterns/isolated-worktree.md`](./skills/rig/patterns/isolated-worktree.md) — worktree・run state の設計
- [`docs/architecture.md`](./docs/architecture.md) — アーキテクチャの実証ポイント
- [`docs/testing-scenarios.md`](./docs/testing-scenarios.md) — ディシプリン圧力シナリオ集
- [README.md](./README.md) — English version

## License

[MIT](./LICENSE) © 2026 itoh-shun
