# instruction: workbench-ops

**`/rig status` / `/rig diff` / `/rig accept` / `/rig discard` / `/rig log` / `/rig board` / `/rig cockpit` / `/rig stats` / `/rig review` / `/rig gc` / `/rig audit` / `/rig scan-secrets` / `/rig scan-injection` / `/rig digest`** の手順。実体は全て `scripts/workbench.py`（`patterns/isolated-worktree` 参照）への薄い委譲で、本ファイルは**表示の整形と安全確認の追加**だけを担う。判定・状態管理をここで再実装しない（§8 Native-first）。

## 共通ルール

- サブコマンドの引数に `task_id` が省略された場合、`workbench.py` は `.rig/runs/` 内の**最新 task**を自動選択する。複数 task が並行している可能性がある場合（`workbench.py log --limit 5` で確認）は、曖昧さを避けるため task_id を明示するようユーザーに促す。
- どのサブコマンドも**親 context に長い diff 本文を引き込まない**（context-minimal）。`workbench.py diff` の出力（ファイル一覧＋shortstat）はそのまま見せてよいが、個々のコード片の要約は `diff.md`（RUN 中にモデルが書いた散文）を参照する。

## MCPサーバ経由での操作（`scripts/mcp_server.py`・#263）

Claude Codeセッションの外（別エージェント・CI・別プロセス）からこれらの操作を叩きたい場合は`python3 scripts/mcp_server.py`を起動する。stdlibのみでMCP stdio transport（JSON-RPC 2.0）を実装した薄いアダプタで、`rig_task_*`/`rig_orchestrate_*`ツールは本ファイルが説明する各`workbench.py`/`orchestrate.py`サブコマンドをサブプロセスでそのまま呼ぶ。**新しい判定ロジックは持たない**——accept/discardの安全要件は本ファイル記載のものとMCP経由で完全に同一。opt-in（起動しなければ何も変わらない）。詳細はREADME「MCPサーバ」節を参照。

### MCP自己脅威分析（`orchestrate.py mcp-scan`・#303）

```
python3 scripts/orchestrate.py mcp-scan [--json]
```

`scripts/mcp_server.py`が公開するツール定義を対象に、shell/network権限過剰・secret平文露出・hookインジェクションの3観点を3層対抗推論（攻撃者→防御者→監査者）で静的分析する。**実行しない**（`TOOLS`辞書とソーステキストを読むだけ・決定論・副作用なし）。`validate.py`の`check_mcp_scan()`から自動的に呼ばれ、総合判定HIGHはCI FAIL、MEDIUMはWARNとして扱われる（`rig_orchestrate_run`は`--isolate`未指定でメイン作業ツリーに影響しうるためMEDIUM判定——呼び出し側での`isolate: true`明示を推奨、という具体的な注意喚起がここで機械的に出る）。

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
- ユーザーが`git commit`した後は、`workbench.py record-commit <task_id>`を案内する（後で本番アウトカムを逆引きできるようにするための紐付け。#289/#300。下記参照）。

### RBAC（`.rig/access.json`・#282）

`.rig/access.json` が存在する場合のみ効く（無ければ従来通り無制限）。形式は `{"default": ["alice","bob"], "<task_type>": [...]}`。accept 操作者は `RIG_USER` 環境変数 → `git config user.name` の順で解決され、該当 task_type（無ければ `default`）の許可リストに無ければ `accept` は拒否される。チーム/組織で「誰でもacceptできる」を避けたい場合にのみ導入する。

### 署名付き来歴（`verify-provenance`・#299）

`accept` は成功のたびに `.rig/runs/<task_id>/provenance.json`（task_type/recipe/base/gate結果/checks を含むレコード＋署名）を書く。鍵は `.rig/provenance.key`（初回accept時に自動生成・gitignore済み・第三者と共有しない）で HMAC-SHA256 署名する。`workbench.py verify-provenance <task_id>` で署名検証でき、レコードまたは鍵が事後に改変されていれば `✗ INVALID` で exit 1 になる。

**スコープの誠実な明示**：これは非対称鍵（Ed25519/SLSA）による第三者公開検証ではない——鍵を持つ**同一環境内での事後改ざん検知**にとどまる（stdlib-onlyのworkbench.py依存原則を保つための意図的な選択）。SLSA相当の公開検証が要る場合は別途の仕組みが必要と案内する。

## 本番アウトカムへのフィードバックループ（`record-commit`/`record-outcome`/`trace-commit`・#289/#300）

acceptance-gateはmerge時点の**予測**にすぎない。以下の3コマンドで、実際に何が起きたかを事後に突き合わせられるようにする：

1. `python3 scripts/workbench.py record-commit <task_id> [<sha>]` — ユーザーが`git commit`した後、最終commit SHAをtaskに紐付ける（省略時は現在のHEAD）。`accept`はstagedで止まるため、このリンクが無いと後から逆引きできない。
2. `python3 scripts/workbench.py record-outcome <task_id> --status ok|incident --note "<詳細>"` — 本番で実際に何が起きたかを記録する（drillが合成バグで検出率を測るのに対し、こちらは実世界での的中率の実測データ）。
3. `python3 scripts/workbench.py trace-commit <sha>` — commit SHAからtask-idを逆引きし、gate予測（accept時のgate状態）と記録済みアウトカムを突き合わせる。`incident`が記録されている場合、`git revert`の計画（コマンド・PRタイトル/本文案）を下書きとして提示する——**自動でrevertやPR作成はしない**。実行はユーザーまたはGitHub連携ツール（`gh pr`系）に委ねる。

「gateが通したものは本当に安全だったか」を継続的に較正する材料として使う。

## `/rig confidence [<task_id>]`

```
python3 scripts/workbench.py confidence [<task_id>]
```

drill実測の検出率(persona別)を、二値のpass/failに加えた**補助情報**として提示する。`<task_id>`を指定すると、その task の`review.json`に記録済みのreviewer全員について確信度を`acceptance.json`の`reviewer_confidence`に記録する(既存のgate判定ロジックは変えない)。閾値(既定70%)未満は「⚠ 低確信」と表示され、追加レビュアー投入を**提案**する——自動では投入しない(判断は人/AIに委ねる)。drill未実施のpersona/問題種別は「未計測」のまま扱い、確信度を捏造しない。

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

## `/rig cockpit`

```
python3 scripts/workbench.py cockpit
```

**board・gate・drill・cost・auditを一画面に集約するMission Control（read-only・#307）**。新しい常駐サービスやDBは持たない——既存の`.rig/runs/`・`drill-results.jsonl`・`runs.jsonl`・`audit.jsonl`を読むだけで完結し、`board`/`stats`/`audit`/`confidence`が既に持つ集計関数をそのまま再利用する（二重実装しない）。出力は6段構成：

1. **Run timeline** — アクティブtaskの一覧（`board`と同じ非終端状態のみ）
2. **Gate radar** — 全taskのgate状態別集計（`stats`と同じ`gate_status_counts`）
3. **Reviewer confidence** — drill実測の検出率（`/rig confidence`と同じ`aggregate_drill_confidence`。未計測は「Unmeasured」と明示——空欄を健全と誤読させない）
4. **Cost meter** — `.rig/runs.jsonl`のtoken_usage合計（`orchestrate.py runs --cost`と同じデータソース。詳細な recipe/provider別内訳は`runs --cost`側を案内する）
5. **Safety strip** — force-bypass件数（`audit`と同じ`force_bypass_counter`）
6. **Next action rail** — `gate_passed`（diff/accept待ち）・`gate_failed`（要修正 or discard）のtask_idを直接案内

**v1は完全にread-only**——accept/discard等の破壊的操作はここでは一切行わず、次に打つべき既存コマンドを案内するだけ。出力はそのままユーザーに提示してよい（整形の追加は不要）。ユーザーが「全体を一目で見たい」「今何をすればいいか教えて」と言ったら、`board`より先にこのコマンドを提案する。

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

## `/rig gc [--older-than <N>d] [--dry-run]`

```
python3 scripts/workbench.py gc [--older-than 14d] [--dry-run]
```

視覚検証成果物（`.rig/runs/<task_id>/visual/` と `.rig/visual/adhoc/*`、`patterns/visual-artifacts` が正本）の age-based 処分。閾値は既定14日（`--older-than <N>d` で変更、例 `7d`）。task の status（accepted/discarded/running）は問わない——画像は再生成可能な検証手段であって恒久記録ではない。**ソース・worktree・branch には一切触れない**。

1. まず `--dry-run` で呼び、`[dry-run] remove: <path>/ (<age> days old)` の候補一覧をそのままユーザーに提示する。
2. ユーザーの確認を得てから `--dry-run` なしで再実行する（discard と同じプレビュー→実行の二段。スクリプト自体は `--dry-run` なしで即削除するため、確認はこの instruction 側の責務）。
3. `Nothing to remove.` の場合はそのまま伝えて終了する。

## `/rig audit [--limit N] [--action A] [--since YYYY-MM-DD]`

```
python3 scripts/workbench.py audit [--limit 10] [--action accept_force] [--since 2026-07-01]
```

`accept --force` 等で acceptance-gate の未達基準を上書きした際の恒久記録（`.rig/audit.jsonl`）の一覧。各エントリ（ts・action・task_id・bypassed 基準・gate 状態・failed checks）をそのまま提示する——整形の追加は不要。絞り込みは `--limit`（最新 N 件）・`--action`（例 `accept_force`）・`--since`（YYYY-MM-DD 以降）。

``No records (entries are appended by `accept --force`).`` の場合は「force-bypass の履歴が無い＝gate を押し切った accept が一度も無い」ことを意味するので、その旨をそのまま伝える。ユーザーが「force で通した履歴を見たい」「gate を無視した accept が無いか確認したい」と言ったらこのコマンドを提案する（**読み取り専用**——記録の追記は `workbench.py accept --force` 側が自動で行い、ここからは書き込まない）。

## `/rig scan-secrets [paths…] [--diff <task_id>]`

```
python3 scripts/workbench.py scan-secrets [paths…]
python3 scripts/workbench.py scan-secrets --diff <task_id>
```

決定論シークレットスキャン（gate 基準 `no_secret_leak` の機械センサーと同一実装）。引数なしはカレントディレクトリ全体、paths 指定でファイル/ディレクトリ、`--diff <task_id>` は該当 task worktree の base commit からの差分（追加行＋未追跡ファイル）だけを走査する（paths と `--diff` の同時指定は不可）。検出対象は既知クレデンシャル形式（AWS/GitHub/Slack/Anthropic/OpenAI/Google のキー・PEM 秘密鍵・JWT）＋汎用の高エントロピー検出（lockfile・`node_modules/` 等はエントロピー検出のみ許容リストで除外——名前つきパターンはそこでも走る）。

1. 出力の抜粋は**常にマスク済み**（先頭4文字＋末尾2文字のみ残る）——秘密の生値は findings に含まれないため、出力はそのままユーザーに提示してよい。検出ありは exit 1。
2. `workbench.py gate` は評価のたびにこの scanner を task diff に自動適用し、findings があれば `no_secret_leak` を **failed** にする（warning ではない＝accept を機械的に止める。schema センサーと違い fail-grade）。
3. 人が偽陽性と確認した場合の脱出口は `gate <task_id> --set no_secret_leak=passed`（明示 pass が優先され、`secret_override` として check に記録される）。判断せず黙って通さない——必ずユーザーに findings を見せてから提案する。

## `/rig scan-injection [paths…] [--diff <task_id>]`

```
python3 scripts/workbench.py scan-injection [paths…]
python3 scripts/workbench.py scan-injection --diff <task_id>
```

決定論プロンプトインジェクション・マーカースキャン（gate 基準 `no_injection_markers` の機械センサーと同一実装）。引数なしは repo の **prose 面**（エージェントが指示として読み込む repo 管理下のファイル＝`.claude/rig.md`・`.claude/rig/knowledge/**`・`.claude/rig/personas/**`・`.rig/recipes/*.md`）、paths 指定でファイル/ディレクトリ、`--diff <task_id>` は該当 task worktree の base commit からの差分（追加行＋未追跡ファイル）**＋その worktree の prose 面全文**を走査する＝gate センサーが見るものと同一（paths と `--diff` の同時指定は不可）。検出クラスは2種：**不可視/bidi Unicode**（zero-width・bidi 制御 U+200B–200F / U+202A–202E / U+2060–2064 / U+FEFF。ソースにも散文にも正当な用途がない＝**fail-grade**）と**指示上書きフレーズ**（"ignore previous instructions" 等。プロンプトについて書かれたドキュメントが正当に含みうる＝**warning-grade**）。

1. 出力の抜粋では不可視文字が `<U+XXXX>` エスケープとして描画される（生の不可視文字は findings に含まれない）ため、出力はそのままユーザーに提示してよい。検出ありは exit 1。
2. `workbench.py gate` は評価のたびにこの scanner を自動適用し、不可視 Unicode 検出で `no_injection_markers` を **failed** に（accept を機械的に止める）、フレーズのみなら **warning** にする。同様に、gate 評価ごとに anti-tamper センサー（`no_gate_tampering`）も走る——task diff 中の `.rig/gates.json`・`.rig/recipes/`・CI workflow の編集は fail-grade、bugfix/feature task での既存テスト改変・assert 削除・skip マーカー追加は warning-grade（こちらは gate 内蔵センサーのみで単独 scan コマンドは持たない）。
3. 人がレビューして偽陽性と確認した場合の脱出口は `gate <task_id> --set no_injection_markers=passed`（`injection_override` として check に記録され、以降の評価でも維持される。`no_gate_tampering` 側は `--set no_gate_tampering=passed`＝`tamper_override`）。判断せず黙って通さない——必ずユーザーに findings を見せてから提案する。

## `/rig digest [--period week|month] [--out PATH]`

```
python3 scripts/workbench.py digest [--period week|month] [--out <path>]
```

`.rig/` に蓄積されたテレメトリのローリング集計ダイジェスト（`week`=直近7日・既定／`month`=直近30日）を Markdown で出力する：orchestrate run 件数（final 別）・workbench task 状態・gate 合否率と落ちやすい基準・ゴム印疑い reviewer・force-accept（`accept --force`）件数・drill 検出率（記録がある場合のみ）。`--out <path>` で stdout の代わりにファイルへ書く。

- **読み取り専用**（集計のみ・状態を変更しない）。出力 Markdown はそのままユーザーに提示してよい（整形の追加は不要）。
- 集計は `stats` と同じ helper を再利用しており数字が食い違わない。個別の深掘り（`--recipe`/`--verifier` 絞り込み）は `/rig stats`、期間の定点観測は `digest` と使い分ける。`stats` 同様、ゴム印警告が出た場合は必ずそのまま伝える。

## `/rig instincts [--add TEXT --evidence E --confidence C] [--mute ID|--expire ID|--decay|--inject-preview]`

```
python3 scripts/workbench.py instincts
python3 scripts/workbench.py instincts --add "<短い独立した文>" --evidence "<根拠>" [--task-id <id>] [--confidence 0.0-1.0] [--supersedes <old-id>]
python3 scripts/workbench.py instincts --mute <id> | --expire <id> | --decay | --inject-preview [--json]
```

**セッション横断の継続的instinct学習層（#306）**。`facets/knowledge`（検証済み知識のwiki）とは完全に別枠——ここに書くのは「このプロジェクトではこう書く」「ここはこう探索すると早い」のような、confidence つきの**未検証パターン**であり、知識層と混同しない。

- **`--add`**：新しいinstinct候補を`.rig/instincts.jsonl`に記録する。secret・トークン・ローカル絶対パス（`/home/…`/`/Users/…`）・`ENV_VAR=value`風の代入・300字超のテキストは**却下**され、理由がそのまま表示される（黙って捨てない）。何を学ぶかの判断（今回のセッションで本当に再利用価値のあるパターンか）は完全にモデル自身の仕事——`hooks/suggest-instincts.sh`（Stop hook）は「提案を検討してください」と促すだけで、抽出そのものは行わない。ほとんどのセッションには提案すべきものが無い、という前提を崩さない。
- **`--supersedes <old-id>`**：2つのinstinctが矛盾するという判断自体は人/モデルの仕事——このコマンドは**明示された**supersede関係を機械的にmuteするだけで、意味的な矛盾を自動検知しない。既存instinctが古くなった/誤りだったと判断したら必ずこれを使う。
- **`--decay`**：`last_seen`が30日以上更新されていないactive instinctのconfidenceを0.1下げる。0.2を下回ればstatus=expired。暗黙知は放置すれば腐る、という前提を機械的に扱う——定期実行（例: digest やcron相当）が望ましいが必須ではない。
- **`--inject-preview [--json]`**：次回セッション開始時に実際に注入される内容をプレビューする。confidence>=0.7のactive instinctのみ、合計500字までに収まる分だけ選ばれる（context-minimal原則）。`--json`は`hooks/inject-instincts.sh`（SessionStart hook）が機械的に読む形式で、人向けには使わない。
- **既定（引数なし）**：全instinctを一覧表示する（status記号 ●active/○muted/×expired、confidence、hit_count、根拠）。

`--mute`/`--expire`はユーザーが明示的に「このinstinctはもう要らない」「間違っていた」と判断した場合の手動操作。

### チャット通知（`scripts/notify.py`・#287）

accept待ち・gate REJECT・エスカレーション等のイベントをSlack/Teamsに通知したい場合、opt-inで`scripts/notify.py`を使う：

```
python3 scripts/notify.py --webhook <incoming webhook URL> --format slack --message "task <id> がaccept待ちです"
python3 scripts/notify.py --format teams --title rig --message "gate REJECT: <詳細>" --dry-run   # 送信前にpayload確認
```

webhook URLは`RIG_NOTIFY_WEBHOOK`環境変数でも指定できる。専用SDKは使わずurllibのみで完結する。通知の要否・タイミングの判断はこのスクリプト自身は行わない——呼び出し側(instruction層)が「このイベントは通知に値するか」を判断してから呼ぶ。
