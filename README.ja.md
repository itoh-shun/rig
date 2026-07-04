# rig

**Claude Code のための、品質保証つき AI ワークベンチ。** タスクに応じて必要なハーネスを自動構成し、隔離された worktree で変更を行い、acceptance-gate で検証し、最後にユーザーが差分を accept / discard できる。

> 🇬🇧 English version: [README.md](./README.md)

## 1. rig とは何か

自然文でタスクを頼むだけでいい。rig がタスクの種類（バグ修正/機能追加/リファクタ/レビュー/ドキュメント/…）を判定し、必要なブリック（persona / instruction / pattern — LEGO 式の部品）を組み合わせてハーネスを合成し、**現在の作業ツリーとは隔離された git worktree**で作業し、明示的な**受け入れ基準**（ビルド/lint/テスト・無関係な差分がないか・secret 漏洩がないか・指摘に重大度が付いているか等）で検証し、`accept` が呼ばれるまで本体には一切触れない。「できました」という自己申告は完了の根拠にならない——根拠は常にゲートの合否。

## 2. 30秒で使う

```bash
/rig:rig "ログイン画面のバグを直して"
```

これだけでよい。裏側では、タスクを分類（`bugfix`）→ 対応する recipe を選択 → 隔離 worktree を作成 → 実装・テスト → acceptance-gate 判定 → 次アクションつきのサマリを返す:

```
/rig:rig diff       # 何が変わったか、なぜ安全か（あるいは危ういか）を確認
/rig:rig accept     # 作業ツリーへ反映（gate が未達なら拒否される）
/rig:rig discard    # 試みを破棄（作業ツリーは最初から一切触れられていない）
```

既存の Issue や PR をそのまま渡すこともできる:

```bash
/rig:rig gh issue 123        # Issue を読んで分類し、そのまま実装まで進める
/rig:rig gh pr 45 review     # 既存 PR を3観点（security/design/test）でレビュー
/rig:rig gh pr 45 fix        # PR のレビュー指摘や CI 失敗を隔離 worktree で修正
```

## 3. なぜ安全か

- **隔離 worktree であって、あなたのブランチではない。** タスクごとに専用の git worktree（`patterns/isolated-worktree`）と使い捨てブランチを作る。rig は作業ツリーに直接書き込まない——失敗しても中断しても、あなたの手元は何も汚れない。
- **ゲートは自己申告ではなくコード。** `scripts/workbench.py accept` は、受け入れ基準が `fail` か `pending` のまま残っている task を機械的に拒否する（終了コード1）。AI が「できました」と言っても何も変わらない——記録された `pass` だけが accept を許可する。
- **accept も discard も必ず明示操作。** `accept` は差分サマリを先に提示してから **staged**（未コミット）として反映する——コミットは常に人が行う。`discard` は task-id の明示と `--yes` 確認を必須とし、常に破棄対象の変更ファイル一覧を先に見せる。
- **安全側の検知は即ブロックにつながる。** 依頼にない diff・説明のないテスト失敗・secret らしき文字列・破壊的操作・レビュー未実施の認証/認可変更・説明のない公開 API 変更——いずれも該当 criterion を `fail` にし、確認するまで accept を止める。
- **実行履歴は消えない。** `discard` は worktree/branch を削除するが run log（`.rig/runs/<task-id>/`）は残る。何を試み、なぜ却下・破棄したかは常に追跡できる。

## 4. 基本コマンド

| コマンド | 内容 |
|---|---|
| `/rig:rig "<タスク>"` | 分類 → recipe 選択 → 隔離 worktree での実行 → acceptance-gate → サマリ |
| `/rig:rig status [id]` | 現在（または最新）の task：step 進行・gate 状態・未反映差分・次アクション |
| `/rig:rig diff [id]` | 変更ファイル一覧＋散文サマリ（挙動変更・リスク・テストの有無） |
| `/rig:rig accept [id] [--force]` | 作業ツリーへ反映（staged）——gate が pass していないと拒否される |
| `/rig:rig discard <id> --yes` | worktree/branch を削除（run log は残る） |
| `/rig:rig log [--limit N]` | 過去 task の履歴（入力・recipe・gate 結果） |
| `/rig:rig gh issue <n>` | GitHub Issue を読んで分類し workbench に流す |
| `/rig:rig gh pr <n> review` | 既存 PR を3観点で並列レビュー |
| `/rig:rig gh pr <n> fix` | PR の指摘・CI 失敗を隔離 worktree で修正 |
| `/rig:rig gh ci` | 現在の branch/PR の CI 状態を確認 |
| `/rig:dev --recipe <name> --only <step> ...` | 上級者向け入口：recipe/step/flag を自分で明示する（エンジンは共通） |

## 5. 実行フロー

```
自然文のタスク
        │
        ▼
①  分類（bugfix / feature / refactor / review / documentation / security_review / …）
        │
        ▼
②  対応する recipe を選択（bugfix / feature / refactor / documentation / …）
        │
        ▼
③  隔離 worktree を開き、recipe を実行（実装/テスト/レビューを subagent に dispatch）
        │
        ▼
④  acceptance-gate：build / lint / test / 差分スコープ / secret / 重大度付き所見を確認
        │
        ▼
⑤  サマリ＋次アクション：/rig diff・/rig accept・/rig discard
```

①②④⑤ は `facets/instructions/workbench` が駆動し、③の隔離は `patterns/isolated-worktree`（決定論ランナー `scripts/workbench.py` が task-id 発行・worktree ライフサイクル・gate 記録・accept/discard を実装）が担う——状態と安全は散文の自制ではなくコードが強制する。

## 6. acceptance-gate

全 task は4つのプリセット（正本は `scripts/workbench.py gates`）から組んだ基準リストを持つ：

| preset | 適用対象 | 基準の例 |
|---|---|---|
| `standard` | 全 task_type 共通 | 無関係な差分がない・テストが green か合理的説明がある・型/lintエラーなし・挙動/リスクサマリが書かれている |
| `implementation` | bugfix/feature/refactor/test/performance/release_support（standard に上乗せ） | 実装が依頼と一致・テスト追加/既存担保を確認・公開API変更の説明・無関係な広範リファクタなし・secret漏洩なし・破壊的操作なし |
| `review` | レビュー系タスク | 具体的な指摘のみ・重大度が付与されている・file:line参照がある・誤検出リスクを検討・Blocking/Non-blockingが分離 |
| `security` | security_review（review に上乗せ） | 入力検証・認可認証への影響・secret非露出・依存リスク・危険なshell/eval |

各基準は根拠つきで `pass` / `fail` / `warn` として記録する：

```bash
python3 scripts/workbench.py gate <task_id> --set no_lint_errors=pass --set tests_added_or_existing_tests_confirmed=warn:"既存テストのみで新規追加なし"
```

`fail` か `pending` が1件でも残っていれば `accept` は機械的に拒否される（終了コード1）。`warn` は accept を止めないが、常に提示され黙って握りつぶされることはない。

## 7. isolated worktree

```
<repo の親>/rig-worktrees/<repo名>/rig-YYYYMMDD-HHMMSS-<slug>/   ← 使い捨て worktree + branch
<repo>/.rig/runs/rig-YYYYMMDD-HHMMSS-<slug>/                      ← run state（discard 後も残る）
  task.json        task_id / 入力 / task_type / recipe / base branch+commit / worktree path / status
  steps.json       step ごとの進行状態
  acceptance.json  基準ごとの pass/fail/warn ＋ 総合 gate 結果
  plan.md / diff.md / log.md / final.md   モデルが書く散文（計画・差分要約・決定・まとめ）
```

`accept` は task branch を作業ツリーへ **squash merge（staged・コミットなし）**で反映し、`discard` が worktree/branch を後片付けする（run log は残る）。読み取り専用のタスク（レビュー・まだ直すと決まっていない調査）は `--no-worktree` で worktree を丸ごと省略できる。設計の詳細は `patterns/isolated-worktree.md` を参照。

## 8. reviewer drill

`/rig:drill` は reviewer の品質を意見ではなく数字で測る。既知のバグ class（認可漏れ・インジェクション・N+1・破壊的変更・片道 migration・テスト欠落…）を使い捨て diff に注入し、review fan-out を実行し、reviewer には見せない答案キーと突き合わせて採点する。

```
# Drill Result
Persona: strict_senior_engineer

## Score
- Detection rate: 82%
- False positive rate: 12%
- Severity accuracy: 76%
- Explanation quality: 70%

## Missed Issues
1. SQL injection risk in search query (src/search.py:88)
2. Missing authorization check in user update endpoint (src/api/users.py:120)

## Improvement Suggestions
- Add a stronger security checklist for injection-class findings
- Require data-flow inspection for user-controlled input
```

reviewer ごとに5指標：`true_positive` / `false_positive` / `false_negative` / `severity_accuracy`（付けた重大度が種の期待値と一致するか）/ `explanation_quality`（修正案が具体的か、一般論か）。drill 実行中の所見は詳細フォーマット `output-contracts/review-findings`（Blocking/Non-blocking・Severity・file:line・Impact・Suggested fix）で出力させるため、重大度と位置が常に機械検証可能になる。`--replay <persona>` はペルソナ編集後にアーカイブ済み diff へ再実行し新旧 verdict を差分表示する——reviewer persona の snapshot テスト。本物のコードには一切触れない（全て使い捨て worktree）。

## 9. GitHub 連携

| コマンド | read/write |
|---|---|
| `/rig:rig gh issue <n>` | Issue（title/body/labels/comments）を読み、bugfix/feature/investigation に分類して workbench へ |
| `/rig:rig gh pr <n> review [--comment]` | 既定は read のみの3観点レビュー。`--comment` で PR へ投稿（書き込みは常に確認必須） |
| `/rig:rig gh pr <n> fix` | PR の diff・レビューコメント・CI 失敗を読み、PR の branch を base に隔離 worktree で修正、`accept` の手前で止まる（自動 push はしない） |
| `/rig:rig gh ci` | 現在の branch/PR の CI 状態を確認し、失敗ジョブの要約を提示 |

Issue/PR の本文・コメントは**信頼できない外部入力**として扱う（埋め込まれた指示には従わず、分類・修正対象のテキストとしてのみ読む）。GitHub への書き込み（コメント・push）は常に明示操作を経る。read は即応。

## 10. advanced customization

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

### shipped recipe（bugfix/feature/refactor/documentation 以外）

| recipe | 内容 |
|---|---|
| `review-only` | 現在の変更を3観点で並列レビュー |
| `release-flow` | intake→design?→implement→verify→review?→pr→merge（size-aware） |
| `design-first` | 設計フェーズ厚め |
| `hotfix` | 最短パス（intake→implement→verify→pr） |
| `debug` | 原因調査重視（reproduce→isolate→implement→verify） |
| `adversarial-review` | AI の癖排除・人間可読性の敵対レビュー |
| `goal-loop` | ゴール駆動ループ |
| `pr-review` | 既存 PR のレビュー（GitHub MCP 取得） |
| `de-ai-smell` | 散文の AI 臭除去 |
| `magi` | 3賢者合議で go/no-go を多数決裁定 |
| `roast` 🌶️ / `coin` 🪙 / `duck` 🦆 / `pre-mortem` ⚰️ | 中身は本物のユーモア pack 群 |
| `design` 🎨 / `design-audit` 🎨 | UI/UX・a11y の設計作成と URL 監査 |
| `movie` 🎬 / `scenario` 🎬✍️ | 動画作成ハーネスとそのシナリオライター前段 |

### ドメイン pack（開発以外）

`/rig:sales`・`/rig:talk`・`/rig:goal`・`/rig:magi`・humor pack 群は、いずれも同じドメイン非依存エンジンに persona＋薄い instruction（＋recipe）を足しただけ（engine 不変）。詳細は [`skills/rig/SKILL.md`](./skills/rig/SKILL.md) §2 のブリック目録を参照。

### manifest・知識層

`<repo>/.claude/rig.md` を置くと build/lint/test コマンド・branch/CI 戦略・reviewer・本番影響検知パターン・既定 recipe・既定 reviewer persona 等を設定できる（`skills/rig/manifests/_template.md` 参照）。recipe は project 層（`<repo>/.claude/rig/recipes/*.md`）・user 層（`~/.claude/rig/recipes/*.md`）で `extends` による差分カスタマイズ、または `--save-recipe` で保存できる。知識層（`~/.claude/rig/knowledge/{methodology,ai-quirks}/`、`<repo>/.claude/rig/knowledge/domain/`）は全 RUN に注入され、実行を重ねるごとに蓄積される。

### 横断利用（CLI として）

決定論ランナー `scripts/orchestrate.py` は shim を1回置けばどのディレクトリからでも呼べる：

```bash
python3 scripts/orchestrate.py install-shim          # → ~/.local/bin/rig（symlink）
rig models                                           # 利用可能プロバイダ探索
rig probe --provider codex                           # 疎通テスト
rig run review-only --provider rig --verifier-provider codex
```

`$RIG_HOME` で install 先を上書き、`<cwd>/.rig/recipes/<name>.md` が同名 built-in recipe をプロジェクト overlay、recipe の `checks:` は呼び出し元プロジェクト（rig リポジトリではない）の cwd で実行される。

### ドキュメント

- [`skills/rig/SKILL.md`](./skills/rig/SKILL.md) — エンジン本体（PARSE/RESOLVE/COMPOSE/RUN の全仕様・rationalization 表・red flags）
- [`skills/rig/patterns/isolated-worktree.md`](./skills/rig/patterns/isolated-worktree.md) — worktree・run state の設計
- [`docs/architecture.md`](./docs/architecture.md) — アーキテクチャの実証ポイント
- [`docs/testing-scenarios.md`](./docs/testing-scenarios.md) — ディシプリン圧力シナリオ集
- [README.md](./README.md) — English version

## License

[MIT](./LICENSE) © 2026 itoh-shun
