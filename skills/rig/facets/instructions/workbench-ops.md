# instruction: workbench-ops

**`/rig status` / `/rig diff` / `/rig accept` / `/rig discard` / `/rig log` / `/rig board` / `/rig stats` / `/rig review` / `/rig cockpit`** の手順。実体は全て `scripts/workbench.py`（`patterns/isolated-worktree` 参照）への薄い委譲で、本ファイルは**表示の整形と安全確認の追加**だけを担う。判定・状態管理をここで再実装しない(§8 Native-first)。

## MCPサーバ経由での操作（`scripts/mcp_server.py`・#263）

Claude Codeセッションの外（別エージェント・CI・別プロセス）からこれらの操作を叩きたい場合は`python3 scripts/mcp_server.py`を起動する。stdlibのみでMCP stdio transport（JSON-RPC 2.0）を実装した薄いアダプタで、`rig_task_*`/`rig_orchestrate_*`ツールは本ファイルが説明する各`workbench.py`/`orchestrate.py`サブコマンドをサブプロセスでそのまま呼ぶ。**新しい判定ロジックは持たない**——accept/discardの安全要件は本ファイル記載のものとMCP経由で完全に同一。opt-in（起動しなければ何も変わらない）。詳細はREADME「MCPサーバ」節を参照。

## `/rig cockpit`

```
python3 scripts/workbench.py cockpit
```

board・gate radar・drill 実測(reviewer confidence)・cost meter・safety strip(force-bypass 監査)・次アクション案内を**一画面に集約**する read-only Mission Control(#307)。既存の`board`/`stats`/`audit`/`drill-results.jsonl`をそのまま読むだけで、新しい常駐サービスやDBは持たない。

- v1は読み取り専用。`accept`/`discard`はここでは実行せず、「Next action rail」が案内する既存コマンド(`workbench.py diff <id>` / `accept <id>` / `discard <id> --yes`)へ委譲する。
- drillが未実施のpersona、cost計測が未実装の項目は「未計測」と表示され、空値を成功のように見せない。
- 複数タスクを並行しているときの状況把握は、まず`board`ではなく`cockpit`を提案してよい(gate/drill/safetyまで一望できるため)。

### チャット通知（`scripts/notify.py`・#287）

accept待ち・gate REJECT・エスカレーション等のイベントをSlack/Teamsに通知したい場合、opt-inで`scripts/notify.py`を使う：

```
python3 scripts/notify.py --webhook <incoming webhook URL> --format slack --message "task <id> がaccept待ちです"
python3 scripts/notify.py --format teams --title rig --message "gate REJECT: <詳細>" --dry-run   # 送信前にpayload確認
```

webhook URLは`RIG_NOTIFY_WEBHOOK`環境変数でも指定できる。専用SDKは使わずurllibのみで完結する。通知の要否・タイミングの判断はこのスクリプト自身は行わない——呼び出し側(instruction層)が「このイベントは通知に値するか」を判断してから呼ぶ。

## `/rig install-git-hook [--which pre-commit|pre-push|both] [--force]`

```
python3 scripts/workbench.py install-git-hook
```

acceptance-gateの計算的センサーのうち、プレーンなgit hookからも適用できる部分(secretパターンスキャン=`no_secret_leak`相当)を`.git/hooks/`にインストールする(#298)。build/lint/testはプロジェクト固有で hookからは知りようがないため対象外——「rigを経由しないcommit/pushにも最小限のセンサーを効かせたい」というopt-inオプション。

- 既定は`pre-commit`/`pre-push`両方。`--which`で個別指定できる。
- 既存のhookがrig由来でなければ黙って上書きしない。上書きするには`--force`を明示する。
- インストール後も`git commit/push --no-verify`で個別にバイパス可能(rigのacceptance-gate自体を弱めるものではなく、rigを経由しない変更にも同じ最小限のセンサーを及ぼす追加レイヤー)。

## 共通ルール

- サブコマンドの引数に `task_id` が省略された場合、`workbench.py` は `.rig/runs/` 内の**最新 task**を自動選択する。複数 task が並行している可能性がある場合（`workbench.py log --limit 5` で確認）は、曖昧さを避けるため task_id を明示するようユーザーに促す。
- どのサブコマンドも**親 context に長い diff 本文を引き込まない**（context-minimal）。`workbench.py diff` の出力（ファイル一覧＋shortstat）はそのまま見せてよいが、個々のコード片の要約は `diff.md`（RUN 中にモデルが書いた散文）を参照する。

## `/rig status [<task_id>]`

```
python3 scripts/workbench.py status [<task_id>]
```

出力（task-id・作業ブランチ/worktree path・Steps チェックリスト・Gate チェックリスト・未反映の差分・Next）をそのままユーザーに提示する。整形の追加は不要（スクリプトの出力が正準フォーマット。各 step/criterion は ✓/✗/⚠/…/- の記号つきで1行ずつ表示される）。

## `/rig diff [<task_id>]`

```
python3 scripts/workbench.py diff [<task_id>]
```

出力は `Changed files:` / `Summary:` / `Risk:` / `Tests:` / `Unrelated diff:` / `Recommended:` の構造化フォーマット。`Summary`/`Risk`/`Tests`/`Unrelated diff` は `.rig/runs/<task_id>/diff.md` の `## Summary` / `## Risk` / `## Tests` / `## Unrelated diff` 見出しから抽出される（`Recommended` は gate 状態からスクリプトが機械的に導出する行なので、モデルは書かない）。

`diff.md` が未作成の場合、`diff` コマンドは `[NOTE]` で作成を促すだけで停止しない——**このタイミングで初めて diff.md を書く**（RUN 中に implement/verify step が書いていない場合はここで1回だけ生成してよい＝承認不要のログ扱い。§6 run テレメトリと同格）。テンプレート:

```markdown
## Summary
（何を変えたか。1〜3行）

## Risk
（既存挙動への影響。後方互換か破壊的か。無ければ「低い」等の評価と根拠）

## Tests
（テスト追加・変更の有無）

## Unrelated diff
（依頼にない変更が混ざっていないかの確認結果。無ければ「None detected.」）
```

`diff.md` が無いまま accept を試みると `scripts/workbench.py accept` が `diff_summary_generated` 要件で機械的に拒否する（§ accept 参照）。

### セマンティックdiff（`scripts/ast_diff.py`・#280）

`diff`は`Changed files:`の直後に`Semantic diff (Python, #280):`section を自動挿入する。base からの変更で **Modified な `*.py` ファイル**だけを対象に、Python標準の`ast`モジュールでtop-level/class内のdef/class単位を比較し：

- シグネチャ変更（引数追加・削除・デフォルト変更）
- 本体変更（シグネチャは同じだがロジックが変わった）
- 追加/削除されたdef・class
- **意味的変更なし**（フォーマット・コメントのみでASTが完全一致）

を機械的に区別して1行ずつ表示する。これは`diff.md`の`Summary`（人/AIが書く散文）を**置き換えるのではなく補強する**——「AST上は意味的変更なし」と出ていても、意図的なフォーマット変更でない限りSummaryは省略しない。

対応言語はPythonのみ（stdlibの`ast`で完結するため）。非Pythonファイル・parse失敗ファイルはこのsection自体に現れず（Modified `*.py`のみを対象にする設計）、テキストdiff（`Changed files:`のname-status）にそのままフォールバックする。

## `/rig accept [<task_id>] [--force]`

```
python3 scripts/workbench.py accept [<task_id>]
```

`accept` はまず **accept_requirements チェックリスト**を表示する（`worktree_exists` / `base_branch_recorded` / `diff_summary_generated` / `acceptance_gate_not_failed` / `no_unrelated_diff`）。**accept 前に必ず**:
1. `workbench.py diff <task_id>` の内容（Summary/Risk/Tests/Unrelated diff）をユーザーに要約提示する。
2. `worktree_exists`/`base_branch_recorded`/`diff_summary_generated` は**構造的な前提**であり `--force` でも上書きできない（diff.md が無ければ先に書く以外に道はない）。
3. `acceptance_gate_not_failed`/`no_unrelated_diff` が未達（gate が `pending`/`failed`）の場合、スクリプトはエラーで拒否する（exit 1）。**`--force` は安全側のガードレールを外す明示操作**であり、以下を満たさない限り提案しない：
   - ユーザーが未達基準を確認した上で明示的にリスクを許容している
   - `--force` 使用は `task.json.forced: true` として記録される旨を伝える
4. gate が `passed_with_warnings`（`warning` 判定の criterion が残っている）場合も accept 自体はスクリプトが許可するが、**未解決の警告を要約提示してから**実行する。

accept 成功後（squash merge → **staged**・コミットはしない）:
- `git diff --staged` で確認できる旨と、コミットは人（またはユーザーの明示指示）が行う旨を案内する。
- 後片付け（`/rig discard <task_id>`）が worktree/branch のみを消し run log を残すことを案内する。
- accept時に自動生成される署名付き来歴（`.rig/runs/<task_id>/provenance.json`）を`workbench.py verify-provenance <task_id>`で検証できる旨を案内する（#299。下記参照）。ユーザーが`git commit`した後は、`workbench.py record-commit <task_id>`を案内する（後で本番アウトカムを逆引きできるようにするための紐付け。#289/#300）。

### 署名付き来歴（`verify-provenance`・#299）

accept成功時、task_type/recipe/base/gate結果/checks一覧をHMAC-SHA256で署名した`provenance.json`を自動生成する（確認不要・診断ログと同格）。鍵は`.rig/provenance.key`（gitignore済み・ローカル限定）。**この署名の意味に注意**：SLSA/Ed25519が想定するような第三者への公開検証ではなく、**同一環境内での事後の改ざん検知**（レコードが書き換えられていないかの確認）に限定される。`workbench.py verify-provenance <task_id>`で検証し、`INVALID`が出た場合はレコードまたは鍵が変更された可能性があるとして扱う。

### 本番アウトカムへのフィードバックループ（`record-commit`/`record-outcome`/`trace-commit`・#289/#300）

acceptance-gateはmerge時点の**予測**にすぎない。以下の3コマンドで、実際に何が起きたかを事後に突き合わせられるようにする：

1. `workbench.py record-commit <task_id> [<sha>]` — ユーザーが`git commit`した後、最終commit SHAをtaskに紐付ける（省略時は現在のHEAD）。`accept`はstagedで止まるため、このリンクが無いと後から逆引きできない。
2. `workbench.py record-outcome <task_id> --status ok|incident --note "<詳細>"` — 本番で実際に何が起きたかを記録する（drillが合成バグで検出率を測るのに対し、こちらは実世界での的中率の実測データ）。
3. `workbench.py trace-commit <sha>` — commit SHAからtask-idを逆引きし、gate予測（accept時の来歴）と記録済みアウトカムを突き合わせる。`incident`が記録されている場合、`git revert`の計画（コマンド・PRタイトル/本文案）を下書きとして提示する——**自動でrevertやPR作成はしない**。実行はユーザーまたはGitHub連携ツール（`gh pr`系）に委ねる。

「gateが通したものは本当に安全だったか」を継続的に較正する材料として使う。

### 確信度つきgate（`confidence`・#301）

```
python3 scripts/workbench.py confidence [<task_id>]
```

drill実測の検出率(persona別)を、二値のpass/failに加えた**補助情報**として提示する。`<task_id>`を指定すると、その task の`review.json`に記録済みのreviewer全員について確信度を`acceptance.json`の`reviewer_confidence`に記録する(既存のgate判定ロジックは変えない)。閾値(既定70%)未満は「⚠ 低確信」と表示され、追加レビュアー投入を**提案**する——自動では投入しない(判断は人/AIに委ねる)。drill未実施のpersona/問題種別は「未計測」のまま扱い、確信度を捏造しない。

### RBAC（`.rig/access.json`・#282）

`.rig/access.json` が存在する場合のみ効く（無ければ従来通り無制限）。形式は `{"default": ["alice","bob"], "<task_type>": [...]}`。accept 操作者は `RIG_USER` 環境変数 → `git config user.name` の順で解決され、該当 task_type（無ければ `default`）の許可リストに無ければ `accept` は拒否される。チーム/組織で「誰でもacceptできる」を避けたい場合にのみ導入する。

### 組織固有acceptance基準（`.rig/gate-extensions.json`・#283）

`.rig/gate-extensions.json` に `{"<task_type>": ["custom_criterion", …], "*": [...]}` を書くと、標準presetに加えて組織固有の基準が `acceptance.json` に追加される（`custom: true` で区別）。判定方法は既存基準と同じ（`workbench.py gate <task_id> --set <custom基準名>=passed` 等）。ファイルが無ければ標準presetのみで従来通り動作する。

## `/rig discard [<task_id>] [--yes]`

```
python3 scripts/workbench.py discard <task_id>
```

**task_id を省略してはならない**（最新 task の自動選択は誤爆リスクが高いため discard だけは明示必須。スクリプト側も `--yes` なしの1回目呼び出しでは変更ファイル一覧を表示するだけで実際には削除しない＝プレビュー）。

1. 1回目は `--yes` なしで呼び、破棄対象の変更ファイル一覧を提示する。
2. ユーザーに確認を取ってから `--yes` を付けて再実行する。
3. 完了後、「worktree/branch は削除したが run log（`.rig/runs/<task_id>/`）は残る」旨を明示する。

## `/rig log [--limit N] [--json]`

```
python3 scripts/workbench.py log --limit <N>
```

出力（task id・実行日時・入力タスク・recipe・gate 結果）をそのまま提示する。「選択された recipe」「実行 step」「最終状態」「変更ファイル一覧」のうち log 一覧に出ない詳細（実行 step 一覧・変更ファイル一覧）が要る場合は、該当 task の `status <task_id>` / `diff <task_id>` を続けて呼ぶよう案内する（1コマンドに詰め込みすぎない・既存サブコマンドの再利用）。

## `/rig board [--all]`

```
python3 scripts/workbench.py board [--all]
```

**複数タスクを並行で進めているときの単一の確認場所**（`/rig:rig` を何度も直接叩いた場合でも、`/rig:queue go --provider rig` で並列 dispatch した場合でも、全ての task は `.rig/runs/` に集約されるため同じ一覧に出る）。既定は非終端状態（`running`/`gate_passed`/`gate_failed`）のみ表示——`accepted`/`discarded` まで含めたい場合は `--all`。出力（task_id・input・type/recipe/mode/最終 step/gate）をそのまま提示する。整形の追加は不要。

「ターミナルをいくつも開いていて何をしていたか忘れる」状況は、このコマンド1つに集約することで解消する——ユーザーが並行タスクの状態を尋ねたら、まず `board` を提案する。

## `/rig review <task_id> --set <persona>=<verdict>`

```
python3 scripts/workbench.py review <task_id> --set security-reviewer=APPROVE --set design-reviewer=REJECT
```

review 系 step（`review-diff`/`parallel-review`/`pr-review`）で reviewer persona の verdict が確定したら記録する。verdict は `APPROVE`/`REJECT`/`APPROVE_WITH_CONDITIONS`。gate の合否そのものには影響しない**観測専用**の記録で、`/rig stats` の「Verifier behavior」（ゴム印検知）に使われる。review 系タスクの RUN では、review-gate の集約結果が出た時点でこのコマンドを呼ぶ運用にする。

## `/rig stats [--recipe R] [--verifier P] [--last Nd]`

```
python3 scripts/workbench.py stats [--recipe bugfix] [--verifier security-reviewer] [--last 30d]
```

`.rig/runs/` 配下の全 task を集計し、そのまま提示する（Runs/Accepted/Discarded/Failed gate のサマリ→Most used recipes→Gate results→Verifier behavior）。**`Warning:` 行が出た場合は必ずそのまま伝える**——`<persona> has 0 rejects across N runs. Possible rubber-stamp behavior.` は N≥5 かつ REJECT 0 件の reviewer に対する自動検知であり、ゴム印化（何でも通す reviewer）の疑いを人に気づかせるための唯一のシグナル。黙って握りつぶさない。verdict が一件も記録されていない場合は「未記録」の旨を伝え、`/rig review` での記録を促す。

## `/rig digest [--since 7d|30d|...]`

```
python3 scripts/workbench.py digest --since 7d
```

`stats`と同じ集計ロジック（`load_json`/`gate_status`/`review.json`の読み方）を期間で絞って再利用し、「Runs/Accepted/Discarded」「よく落ちるgate（criterion別failed件数）」「drill実績（期間内の実行回数・検出率）」「ゴム印疑い」を1回でまとめて出す（#285）。`stats`が随時の任意集計であるのに対し、`digest`は定期実行（週次/月次）を前提にした要約——`/rig:loop --every 7d "workbench.py digest"`のように定期チョアとして回せる。
