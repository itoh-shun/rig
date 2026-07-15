# rig

**Claude Code のための、品質保証つき AI ワークベンチ。** タスクに応じて必要なハーネスを自動構成し、隔離された worktree で変更を行い、acceptance-gate で検証し、最後にユーザーが差分を accept / discard できる。

> 🇬🇧 English version: [README.md](./README.md)

## 1. rig とは何か

自然文でタスクを頼むだけでいい。rig がタスクの種類（バグ修正/機能追加/リファクタ/レビュー/ドキュメント/…）を判定し、必要なブリック（persona / instruction / pattern — LEGO 式の部品）を組み合わせてハーネスを合成し、**現在の作業ツリーとは隔離された git worktree**で作業し、明示的な**受け入れ基準**（意図の充足・無関係な差分がないか・リスク要約・テスト・型エラー・secret 漏洩がないか等）で検証し、`accept` が呼ばれるまで本体には一切触れない。「できました」という自己申告は完了の根拠にならない——根拠は常にゲートの合否。

rig の本当の価値は、AI を動かすこと自体ではない。AI に作業を任せるときの危険な部分を、**隔離・検証・測定・記録・反映制御**によって構造的に潰すことにある。

安全性の核が「documented だけで実装が伴わない」状態にならないよう、3 つの性質を配線に組み込んでいる:

- **Force-proof な accept 前提条件** — `accept` は構造的前提（worktree の存在・base branch の記録・diff サマリの作成）が欠けていると `--force` でも通らない。`--force` が上書きできるのは soft な gate 未達だけで、そのときは `.rig/audit.jsonl` に記録が残る（`workbench.py audit`）。checkpoint はフラグで外せない場所に置く。
- **クロスプロバイダを前提にした設計** — 生成役と検証役は別プロセスで走り、それぞれ LLM を選べる：`claude` / `codex` / `ollama` / `lmstudio` / `cmd` / `mock` / さらに `rig` ハーネスをネスト。Claude で実装して Codex で検証する（あるいは逆）が既定の流し方で、同じクラスのモデルが自分の成果物をレビューする状況を構造的に避ける。`orchestrate.py probe` が「read-only サンドボックスは configuration だけでなく実装として発動している」ことを provider ごとに確認する（§5・§12）。
- **Claude Code plugin として動く** — `/rig:go` は普段の作業と同じ session に住む。別ツールに切り替える文脈スイッチではなく、隔離・ゲート・accept まで一続きのキー操作でできる。

**rig の現在地：** 安全性の核——task 分類・隔離・acceptance-gate・明示的な accept/discard——は実装済みで、このリポジトリ自身のテストスイート（§15）で裏付けが取れている。その上に乗る品質・観測系のツール（drill・board・stats・GitHub 連携）は実用可能だが発展中。さらに別枠で、同じゲートを使いながら配送を遊び心にした一群のコマンド（MAGI 合議・roast・movie 等）があり、これは明示的に experimental と位置づけている。§7 でこれを名指しで区分けする。

### ポジショニング

rig は意図的に、独自 DSL を持つ重量級の外部エンジン**ではない**。Claude Code のセッション内では、Claude Code 自身のプリミティブ——slash command（`commands/`）・skill（`skills/rig`）・subagent（`agents/`）・hook（`hooks/`）——だけで合成された薄い品質・安全レイヤーとして動く。隔離・ゲート・accept は、いま作業しているセッションにそのまま規律として乗るのであって、別ツールへの乗り換えを要求しない。

同じ設計にはもう一つの顔がある：このレイヤーの背後にある決定論エンジン（`scripts/orchestrate.py`。`rig_workbench/` としてパッケージ化され、pip の `rig-wb` CLI としても導入できる）は、**外部制御プレーン**を兼ねる。CI・別セッション・別ツール（Codex / Cursor 等）から、まったく同じ recipe・ゲート・read-only verifier をセッションの外から駆動できる——§13「横断利用（CLI として）」を参照。同じエンジンを公開する MCP サーバは提案段階でまだ出荷されていない。§7 の原則どおり、この提案は README の機能行ではなく GitHub issue（#263）として管理している。

そして「品質ゲートがあります」という他所の売り文句との差別化点：rig のゲートとレビュアーは主張されるだけでなく**実測される**。`/rig:drill`（§11）は既知バグの注入に対する各 reviewer persona の実際の検出率をスコア化し、`/rig:go stats`（§10）は実 run 履歴からラバースタンプ化したレビュアーや頻繁に落ちるゲートを炙り出す。測れないゲートは願望にすぎない——rig はゲートの実効性をデータとして扱う。

## 2. 30秒で使う

```bash
/rig:go "ログインバグを直して"
/rig:go "このPRを厳しめにレビューして"
/rig:go "今の変更が安全か確認して"
```

最初はこれだけでいい。裏側では、タスクを分類 → 対応する recipe を選択 → 隔離 worktree を作成（レビュー等の読み取り専用タスクは省略）→ 実装・テスト → acceptance-gate 判定 → 次アクションつきのサマリを返す:

```
/rig:go diff       # 何が変わったか、なぜ安全か（あるいは危ういか）を確認
/rig:go accept     # 作業ツリーへ反映（gate が未達なら拒否される）
/rig:go discard    # 試みを破棄（作業ツリーは最初から一切触れられていない）
```

## 3. メイン入口

主入口は次のコマンドです。

```bash
/rig:go "ログインバグを直して"
```

**`/rig:go` は唯一のメイン入口**であり、このドキュメントの中で一番先に覚えるべきコマンドである。`/rig:rig` は互換エイリアスとして引き続き動く——同じエンジン・同じ引数なので、既存の習慣やスクリプトはそのまま壊れない。変わったのは名前だけ。

`/rig:talk` は同じエンジンへのより会話的な入口としてそのまま残る——状況を説明して rig に聞き返してもらいたいとき、1つのタスクを最初から言い切るより向いている：

```bash
/rig:talk "ログインバグがまた出た、今回は原因が分からない"
```

フルの品質保証ワークベンチフローには `/rig:go` を使う。同じエンジンへの会話的な入口が欲しいときは `/rig:talk` を使う。

## 4. 安全な基本フロー

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
⑤  構造化された差分サマリ＋次アクション
        │
        ▼
ユーザー判断
   ├─ accept  → staged diff として作業ツリーへ反映
   └─ discard → worktree を削除し、ログは保持
```

`new` の直後には**選択理由バナー**が必ず出るので、なぜその recipe が選ばれたか迷わない：

```
▸ rig
task: ログインバグを直して
detected: bugfix
recipe: bugfix — 「バグ」「直して」を検出
mode: isolated worktree
gate: standard + bugfix
```

②で recipe がどう合成されるかは §8、③〜⑤を支える仕組みは §5 を参照。

## 5. なぜ安全か

### isolated worktree

タスクごとに専用の git worktree（`patterns/isolated-worktree`）と使い捨てブランチを作る。rig は作業ツリーに直接書き込まない——失敗しても中断しても、あなたの手元は何も汚れない。

```
<repo の親>/rig-worktrees/<repo名>/rig-YYYYMMDD-HHMMSS-<slug>/   ← 使い捨て worktree + branch
<repo>/.rig/runs/rig-YYYYMMDD-HHMMSS-<slug>/                      ← run state（discard 後も残る）
  task.json        task_id / 入力 / task_type / recipe / base branch+commit / worktree path / status
  steps.json       step ごとの進行状態
  acceptance.json  {task_id, task_type, presets, status, checks: [{name, status, detail}]}
  review.json      review タスクの persona 別 verdict（/rig:go stats に反映）
  plan.md / diff.md / log.md / final.md   モデルが書く散文（計画・差分要約・決定・まとめ）
```

読み取り専用のタスク（レビュー・まだ直すと決まっていない調査）は `--no-worktree` で worktree を丸ごと省略できる。設計の詳細は [`patterns/isolated-worktree.md`](./skills/rig/patterns/isolated-worktree.md) を参照。

**複数タスクを並行で進める（ターミナルを増やさず一括把握）。** 隔離が task 単位で完結しているため、**複数タスクを同時に走らせても構造的に安全**（別 worktree・別 branch）。`/rig:go "<task>"` を1つずつ打つ代わりに、実際に並列実行したいなら queue に積んで一括 GO する：

```bash
/rig:queue add "ログイン画面のバグを直して"
/rig:queue add "在庫一覧に検索機能を追加して"
/rig:queue add "READMEをわかりやすくして"
/rig:queue go --provider rig --max-parallel 3   # 独立した headless プロセスを3つ並列実行
```

`--provider rig` は各 queue item を `/rig:go "<task>"` 経由で dispatch するため、直接 `/rig:go` を打ったときと同じように各タスクが自動的に隔離される——並列実行中のプロセス同士がファイルを取り合う心配がない。queue 自身の verifier は「gate が確定したか」「isolated worktree 内で完結し本体に書き込んでいないか」を確認するだけで、**ユーザーの代わりに accept はしない**。完了後は `/rig:go board`（§10）が唯一の確認場所になる——どの端末・プロセスが実行したかに関わらず。

**視覚検証のスクリーンショット。** `visual-verify`（UI diff 確認）と `design-audit`（Playwright での画面取得）はいずれもスクリーンショットを生成する。これらは判断のための使い捨て証拠であって成果物ではない——結論は常に散文（`diff.md`）に残る：

```
<repo>/.rig/runs/<task-id>/visual/            ← task 紐づき（/rig:go 経由で実行）
<repo>/.rig/visual/adhoc/<ts>-<slug>/         ← ad-hoc（例: 単独の /rig:design <url> 監査）
```

`discard` は task の `visual/` を即時削除する（run log の JSON/MD は残る）。それ以外——accept 済み task の screenshot も含め——は経過日数で処分する（`python3 scripts/workbench.py gc --dry-run` でプレビュー、`gc` で削除。既定14日超が対象）。詳細ルールは [`patterns/visual-artifacts.md`](./skills/rig/patterns/visual-artifacts.md) を参照。

### acceptance-gate

acceptance-gate は、run を反映候補として渡してよいかを判定する。モデルが「完了しました」と言うだけでは完了扱いになりません——unrelated diff・test/type/lint・risk summary・task 別要件などの機械的なチェックを通過して初めて渡せる。failed または pending の gate がある場合は accept を止める。

全 task は `standard`（全 task_type 共通）＋ task_type 別プリセットの合成で基準リストを持つ（正本は `scripts/workbench.py gates`）：

| preset | 上乗せ対象 | 基準の例 |
|---|---|---|
| `standard` | 全 task_type | `task_intent_satisfied`・`no_unrelated_diff`・`diff_summary_written`・`risk_summary_written`・`tests_pass_or_explained`・`no_type_errors_or_explained`・`no_secret_leak`・`no_gate_tampering`・`no_injection_markers`・`no_destructive_operation` |
| `bugfix` | bugfix, performance | `bug_cause_identified`・`fix_is_minimal`・`regression_test_added_or_explained`・`existing_behavior_preserved`・`no_unrelated_refactor` |
| `feature` | feature, test | `requirement_summary_written`・`implementation_matches_requirement`・`tests_added_or_explained`・`public_api_changes_documented`・`migration_or_backward_compatibility_considered` |
| `refactor` | refactor | `behavior_boundaries_identified`・`no_unintended_behavior_change`・`tests_confirm_behavior_preserved`・`no_unrelated_refactor`・`public_api_changes_documented_if_any` |
| `review` | review | `findings_are_concrete`・`severity_labeled`・`file_references_included`・`blocking_and_non_blocking_separated`・`false_positive_risk_considered` |
| `security` | security_review（review に上乗せ） | `authn_authz_impact_checked`・`user_input_flow_checked`・`secret_exposure_checked`・`unsafe_eval_or_shell_checked`・`dependency_risk_checked` |

この基準リストはプロジェクト側で **`.rig/gates.json`** から拡張できる——`extra_criteria` で preset / task_type 別に独自基準を追加（表示には `[project]` タグ）、`descriptions` で説明を付ける。設定は**加算のみ**：組み込み基準の削除・緩和キーは即座に拒否されるため、repo 内のファイルが gate を弱めることはできない。また4つの基準は自己申告でなく機械センサーが裏付ける：`public_api_changes_documented` は OpenAPI schema-diff（`openapi.json`/`swagger.json` 等を自動検出、`openapi_paths` で明示可）で、API が変わったのに diff サマリに記述が無ければ `warning` に落とす——warning-grade であり単独で gate を fail にはしない。`no_secret_leak` は task diff への決定論シークレットスキャン（`workbench.py scan-secrets`）で、検出があれば **failed** にする——抜粋は常にマスク済みで、人が確認した偽陽性は `--set no_secret_leak=passed` で明示的に解除する。`no_gate_tampering` は task diff への anti-tamper スキャン——`.rig/gates.json`・`.rig/recipes/`・CI workflow の編集は fail-grade、bugfix/feature task での既存テスト改変・assert 削除・skip マーカー追加は warning-grade（人がレビューした上での上書きは `--set no_gate_tampering=passed` で行い、check に記録される）。`no_injection_markers` は diff＋repo の prose 面へのプロンプトインジェクション・マーカースキャン（`workbench.py scan-injection`）——不可視/bidi Unicode は fail-grade・指示上書きフレーズは warning-grade で、抜粋中の不可視文字は `<U+XXXX>` エスケープで描画され、脱出口は `--set no_injection_markers=passed`（記録される）。

各基準は根拠つきで `passed` / `failed` / `warning` / `skipped` として記録する：

```bash
python3 scripts/workbench.py gate <task_id> --set no_type_errors_or_explained=passed --set tests_added_or_explained=warning:"既存テストのみで新規追加なし"
```

gate 全体は `passed` / `passed_with_warnings` / `failed` / `pending` / `skipped` に集約される：

```
Gate:
✓ task_intent_satisfied
✓ no_unrelated_diff
✓ diff_summary_written
✓ risk_summary_written
⚠ tests_pass_or_explained
✓ no_secret_leak

Overall:
passed_with_warnings

Next:
Review /rig:go diff, then choose accept or discard.
```

`failed` か `pending` が1件でも残っていれば `accept` は機械的に拒否される（exit 1）。`warning` は accept を止めないが、常に提示され黙って握りつぶされることはない。

### read-only verifier

rig は「実装する AI」と「検証する AI」を分離し、検証側はプロセスレベルで read-only に固定される——お願いではなく強制として。

reviewer/verifier subagent はツールアクセスを制限して起動する（`claude --allowedTools Read,Grep,Glob`・`codex --sandbox read-only`）。ファイル確認、grep、diff 確認、指摘の作成はできる。一方でファイル編集、破壊的な shell 操作、formatter による変更、commit、worktree 変更はできない。これにより、レビュー担当が評価対象の成果物を勝手に修正してしまうことを防ぐ——実装と検証を同系統のモデルにまかせるときの実在するリスク。さらに verifier は生成側の自己申告レポートでなく **worktree の実際の git diff を一次証拠として判定する**——レポートは「未検証の主張」と明示ラベルづけして渡されるだけ——そして基準ごとの `CRITERION n: PASS|FAIL|UNKNOWN` 行を出し、判定は最後に置く。`scripts/orchestrate.py probe`/`selftest` が、この制限が文書上だけでなくプロバイダごとに実際に適用されていることを検証している。

### 明示的な accept / discard

`accept` はまず `accept_requirements` チェックリストを表示する——`worktree_exists`/`base_branch_recorded`/`diff_summary_generated` は**構造的な前提**であり `--force` でも上書きできない。そのうえで **staged**（未コミット）として反映する——コミットは常に人が行う。`discard` は task-id の明示と `--yes` 確認を必須とし、常に破棄対象の変更ファイル一覧を先に見せる。完全な例つきの解説は §9。

### 実行履歴

`discard` は worktree/branch を削除するが run log（`.rig/runs/<task-id>/`）は残る——何を試みてなぜ却下・破棄されたかは常に追える。

これは `discard` だけの話ではない。途中で別の質問を挟んでも、静かにハーネスから外れることはない。RUN 中の各ターンは状態ヘッダを再掲する：

```
▸ rig | task: rig-20260704-153012-login-fix | recipe: bugfix | step: test (4/7) | gate: pending | mode: isolated worktree
```

中断（脱線質問・tool 呼び出し・長い間）があっても次のターンは必ずこのヘッダに再アンカーする——静かに素の直接作業へ切り替えることはない。**コンテキスト圧縮も生き延びる**：同梱の `PreCompact` フックが run-state の保全指示を注入し、`/rig:init` は同じ保全文を CLAUDE.md "Compact Instructions" にも置ける。

## 6. Core commands

Core commands は既定の安全フローそのもの：タスクを振り分け、隔離して作業し、検証し、diff を確認し、accept か discard する。

| コマンド | 内容 |
|---|---|
| `/rig:go "<タスク>"` | 分類 → recipe 選択 → 隔離 worktree での実行 → acceptance-gate → サマリ |
| `/rig:talk "<タスク>"` | 同じエンジンへの会話的入口（§3） |
| `/rig:dev ...` | 同じエンジンをすべて明示（recipe/step/flag）— 上級者向け入口、§13 |
| `/rig:orchestrate` | 同じエンジンの step 単位の計算的オーケストレーション — §13 |
| `/rig:go status [id]` | 現在（または最新）の task：Steps チェックリスト・Gate チェックリスト・未反映差分・次アクション |
| `/rig:go diff [id]` | 変更ファイル一覧＋Summary/Risk/Tests/Unrelated diff/Recommended（§9） |
| `/rig:go accept [id] [--force]` | 作業ツリーへ反映（staged）——gate が pass していないと拒否される（§9） |
| `/rig:go discard <id> --yes` | worktree/branch を削除（run log は残る）（§9） |
| `/rig:go log [--limit N]` | 過去 task の履歴（入力・recipe・gate 結果） |

## 7. Feature status

| 領域 | Status | 補足 |
|---|---:|---|
| 自然文タスクルーティング | Stable | `/rig:go "<task>"` がタスクを recipe に振り分ける（§4, §8） |
| isolated worktree | Stable | 危険な作業は既定で隔離される（§5） |
| acceptance gate | Stable | `failed`/`pending` の gate は accept を止める（§5） |
| diff / accept / discard | Stable | 明示的な staged 反映フロー（§9） |
| read-only verifier | Stable | reviewer は成果物を書き換えられない（§5）。プロバイダごとに強制 |
| 実行履歴 / run-continuity | Stable | run log は保持され、中断やコンテキスト圧縮を跨いで状態が生き残る（§5） |
| `--validate`（構造 doctor） | Stable | ブリック目録自体の構造検証。CI で強制 |
| board / stats | Beta | 複数 run の観測に有用。出力形式は発展中（§10） |
| reviewer drill | Beta | 注入した issue で reviewer 品質を測定（§11） |
| GitHub 連携 | Beta | Issue/PR/CI フローは今後変わりうる（§12） |
| queue（並列 dispatch） | Beta | 隔離により構造的には安全。UX は発展中（§5） |
| knowledge import/export/persona/catalog/forge | Beta | 有用だが安全性の核ではない（§13） |
| planning 系（goal/design/brainstorm/tasks/loop/harness/qa） | Beta | 実在のゲートつきフローだが Core ほど実績を積んでいない（§13） |
| creative / party 系（MAGI・roast・movie 等） | Experimental | 中身は本物のゲートだが配送が遊び心。既定パスからは外している（§14） |

この表に "Planned" 行はない——未出荷の機能をここに書く方針は取らない。提案は GitHub issue として存在する。表に載っていないコマンドはまだ出荷されていない。

## 8. task routing と recipes

エンジン（`skills/rig/SKILL.md`)は起動時に4種のブリックを合成する：**persona**（誰が判定するか）・**instruction**（何をするか）・**pattern**（どう dispatch・gate するか）・**recipe**（step の束）。task_type の自動ルーティング（§4 の①）は4つの shipped recipe＋既存資産への native 委譲で構成される。この表は代表例であり網羅ではない——現在の全件は下記の `/rig:dev --list` または `/rig:catalog` を参照：

| recipe | 内容 |
|---|---|
| `bugfix` / `feature` / `refactor` / `documentation` | workbench の既定4本 — inspect → … → acceptance |
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

## 9. diff / accept / discard

**`/rig:go diff`** は `diff.md` の `## Summary` / `## Risk` / `## Tests` / `## Unrelated diff` 見出しを構造化して表示し、末尾に **コードが gate 状態から算出する** `Recommended:` 行を付す（モデルが書く行ではないので希望的観測が入らない）。Modifiedな`*.py`ファイルには`ast`モジュールによるセマンティックdiff（シグネチャ変更／本体変更／意味的変更なしを区別、#280）も自動で挿入される：

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
  Safe to accept.
```

**`/rig:go accept`** はまず `accept_requirements` チェックリストを表示する：

```
## rig accept: rig-20260704-153012-login-fix — accept_requirements
  ✓ worktree_exists
  ✓ base_branch_recorded
  ✓ diff_summary_generated
  ✓ acceptance_gate_not_failed
  ✓ no_unrelated_diff
```

`worktree_exists`/`base_branch_recorded`/`diff_summary_generated` は**構造的な前提**——`diff.md` が無ければ accept できない、`--force` でも例外なし。`acceptance_gate_not_failed`/`no_unrelated_diff` は判断が伴う項目で `--force` による上書きが可能（`forced: true` として記録される＝消えない）。チェックリストを通過したら、task branch を作業ツリーへ **squash merge（staged・コミットなし）**で反映する。

**`/rig:go discard <id> --yes`** は常に変更ファイル一覧を先に表示する（`--yes` なしは削除しないプレビュー）。worktree/branch を削除するが run log（`.rig/runs/<task-id>/`）は残る。

## 10. run board と stats

### Run board

複数の AI タスクが走っている、あるいは走り終えている場合、`/rig:go board` は管制塔として機能する——どのターミナル・`/rig:queue` item が起動したかに関わらず、全 task の状態を1つの表で示す。

```
[running    ] rig-20260705-091200-search-feature
    在庫一覧に検索機能を追加して
    type=feature      recipe=feature      mode=isolated   step=implement(running)      gate=-
[gate_passed] rig-20260705-090800-login-fix
    ログインバグを直して
    type=bugfix       recipe=bugfix       mode=isolated   step=acceptance(passed)      gate=passed
[gate_failed] rig-20260705-091500-readme-clarity
    READMEをわかりやすくして
    type=documentation recipe=documentation mode=isolated step=verify-commands(failed) gate=failed
```

確認できる内容：どのタスクがまだ実行中か、どれが gate を通った/落ちたか、どの worktree に変更があるか、どの run が diff 確認待ちか、どれを discard すべきか。`/rig:go board --all` はアクティブなものだけでなく記録済み全 task に範囲を広げる。

### Stats

`/rig:go stats` は過去の run を集計する——単一 run の結果ではなく、workbench 全体を観測する層：

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

失敗しやすい recipe、まったく reject しない reviewer、accept を止めがちな gate、accept/discard の比率などが見える。`/rig:go review <task_id> --set <persona>=<APPROVE|REJECT|APPROVE_WITH_CONDITIONS>` で記録した verdict がここに集計される——review タスクの結果が確定するたびに記録しておくと、何でも通す reviewer を rig が検知してくれる。既存の `.rig/runs.jsonl`（`scripts/orchestrate.py runs` が読むエンジン全体の実行テレメトリ）とは別物——`workbench.py stats` は workbench task のライフサイクル（accept/discard/gate 結果）専用。

## 11. reviewer drill

reviewer persona は単なるプロンプトではない。rig では、それをテストできる。

`/rig:drill` は既知のバグ class（認可漏れ・インジェクション・N+1・破壊的変更・片道 migration・テスト欠落…）を使い捨て diff に注入し、review fan-out を実行し、reviewer には見せない答案キーと突き合わせて採点する：

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

rig は reviewer を動かすだけではない。reviewer を測定する。

### MCPサーバ（#263）

Claude Codeセッションの外（別エージェント・CI・別プロセス）からrigを操作したい場合は、`scripts/mcp_server.py`を起動する：

```bash
python3 scripts/mcp_server.py
```

stdioでModel Context Protocol（JSON-RPC 2.0、line-delimited）を待ち受ける。`mcp`公式SDKには依存しない——サードパーティ製の重い依存を増やさない方針を、workbench.py/orchestrate.pyのstdlib-only方針と揃えるため、stdlibのみで最小限のstdio transportを実装している。新しい実行エンジンは無い：全ツールはサブプロセスで`workbench.py`/`orchestrate.py`をそのまま呼ぶ薄いアダプタで、accept/discardのforce-proof要件（`worktree_exists`/`base_branch_recorded`/`diff_summary_generated`等）はCLIと完全に同じコードパスを通るためMCP経由でもバイパスできない。

提供ツール：

| ツール | 相当するCLI |
|---|---|
| `rig_task_new` / `rig_task_status` / `rig_task_board` / `rig_task_diff` / `rig_task_gate` / `rig_task_accept` / `rig_task_discard` / `rig_task_log` | `workbench.py new/status/board/diff/gate/accept/discard/log` |
| `rig_orchestrate_init` / `rig_orchestrate_next` / `rig_orchestrate_check` / `rig_orchestrate_status` / `rig_orchestrate_run` / `rig_orchestrate_runs` | `orchestrate.py init/next/check/status/run/runs` |

opt-in：このサーバを起動しない限り何も変わらず、既存のCLI/skill経由の利用はそのまま有効。MCPクライアント（Claude Desktop等）から使う場合は、`command: python3`, `args: ["<repo>/scripts/mcp_server.py"]`をMCP設定に登録する。

### コストティア自動ルーティング（`--auto-route`・`--auto-route-learn`・#264・#305）

recipeのstepは`auto_route.candidates`（`{model, cost_tier, max_size}`の列、安い順に宣言）を持てる。`orchestrate.py run --auto-route`は、現在の diff size を測定し、その`max_size`をカバーする最も安い候補を決定論的に選ぶ——あくまでフォールバックで、実行時の`--step-model`とrecipe自身の`model:`はどちらも優先されたまま。選択結果は`runs.jsonl`の`steps[].auto_route`に記録される。

`--auto-route-learn`はこれをさらに発展させ、`.rig/runs.jsonl`自身の実績（どのrecipe/stepでどのmodelが実際に使われ、gateを通過したか）から頻度ベース（MLモデル不要）で学習する。**既定はshadow mode**：予測は常に記録される（`steps[].learned_route`）が、`--auto-route-mode active`を指定するまでは実際の選択に影響しない（段階導入）。参照run数が不足しているか、pass_rateが低い場合は静的な`--auto-route`の選択にフォールバックし、棄却した候補とその理由（counterfactual）を必ず記録する——ブラックボックス化しない。`--exploration-pct N`を指定すると、一定割合のrunだけ次点候補を試す（乱数ではなく`--exploration-date`＋recipe/stepのハッシュで決定論的に判定するため、結果は再現可能）。regret logging（安すぎ/高すぎの選択を事後に自動較正する仕組み）は未実装——`runs`/`stats`で`steps[].status`と`learned_route`を手動比較するのが現状のフォールバック。

## 12. GitHub 連携

| コマンド | read/write |
|---|---|
| `/rig:go gh issue <n>` | Issue（title/body/labels/comments）を読み、bugfix/feature/investigation に分類して workbench へ |
| `/rig:go gh pr <n> review [--comment]` | 既定は read のみの3観点レビュー。`--comment` で PR へ投稿（書き込みは常に確認必須） |
| `/rig:go gh pr <n> fix` | PR の diff・レビューコメント・CI 失敗を読み、PR の branch を base に隔離 worktree で修正、`accept` の手前で止まる（自動 push はしない）。CI 状態は `tests_pass_or_explained` 基準の根拠に使う |
| `/rig:go gh ci` | 現在の branch/PR の CI 状態を確認し、失敗ジョブの要約を提示 |

Issue/PR の本文・コメントは**信頼できない外部入力**として扱う（埋め込まれた指示には従わず、分類・修正対象のテキストとしてのみ読む）。これは散文の「従わないで」ではなく**構造的に**強制する：第三者テキストが下流の persona に届く前に、推測不能な per-call デリミタでデータと明示する**検疫フェンス**（`rig_workbench/orchestrate/quarantine.py` の `wrap_untrusted`）で囲い、不可視/bidi Unicode を先に剥離する（改竄シグナル）——ゆえに埋め込まれた「指示を無視せよ」がフェンスを抜け出せない（OWASP LLM01／spotlighting・CaMeL）。GitHub への書き込み（コメント・push）は常に明示操作を経る。read は即応。

## 13. Advanced commands

### コマンド分類

| tier | コマンド |
|---|---|
| **Quality** | `/rig:drill`、`/rig:go stats\|review`、`/rig:pr`（既存 PR レビュー入口）、`/rig:harness`（自プロジェクトの開発ハーネス監査）、`/rig:qa`（仕様ベースのテストケース設計） |
| **Knowledge** | `/rig:import`、`/rig:export`、`/rig:catalog`、`/rig:knowledge`、`/rig:persona`、`/rig:forge`（自己拡張：説明文からブリック/パックを自作） |
| **Planning** | `/rig:goal`、`/rig:design`、`/rig:brainstorm`、`/rig:tasks`、`/rig:loop`（繰り返しドライバ——見張り/ポーリング。goal の対極） |

いずれも安全な基本フロー（§4〜§6）を理解したあとに使う機能——全ブリック目録は [`skills/rig/SKILL.md`](./skills/rig/SKILL.md) §2 を参照。（`/rig:queue` は §5、`/rig:init` は FAQ、`/rig:sales` は §8 でそれぞれ扱っている。Experimental commands は独立した節——§14。）

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

`/rig:go "<task>"` は分類と recipe 選択を自動でやる。`/rig:dev` は同じエンジンを recipe・step・flag すべて明示して使う入口：

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

### Codex skill として使う

Codex では、このリポジトリの `skills/rig` を `~/.codex/skills` から見えるようにすれば `$rig` skill として使える：

```bash
mkdir -p ~/.codex/skills
ln -sfn /path/to/rig/skills/rig ~/.codex/skills/rig
```

Codex を再起動したあと、`$rig "ログインバグを直して"` のように呼ぶ。Codex では `$rig` が Claude Code の `/rig:go` 相当の入口になる。横断 runner は既に `codex exec` provider を持っており、検証ロールでは read-only sandbox を強制する。

### Codexネイティブ層統合（#294）

Codex CLI（2026年時点）はClaude Codeとほぼ同型の拡張機構（Skills・Hooks・Subagent TOML）を持つ。上記のskillシンボリックリンク方式に加え、本リポジトリはCodexネイティブな等価物も同梱する：

| 機構 | 追加したファイル | 内容 |
|---|---|---|
| Skills | `codex/skills/rig/SKILL.md` | Codexの`.agents/skills/<name>/SKILL.md`規約（`name`/`description` frontmatter）に沿った薄いskill。新しいエンジンは作らず、既存の`workbench.py`/`orchestrate.py`への手続き的ポインタに留める |
| Hooks | `codex/hooks.json` | `PreCompact`イベントで既存の`hooks/preserve-rig-state.sh`をそのまま再利用（Claude Code専用の記述は含まれていないスクリプトなので複製不要）。run-continuityをCodexの圧縮イベントにも配線 |
| Subagents | `.codex/agents/security-reviewer.toml` | `agents/security-reviewer.md`と同じ評価軸・出力契約を持つCodexネイティブsubagent定義。`sandbox_mode = "read-only"`でCodex自身のサンドボックスにもread-only強制を効かせる——rig側の`orchestrate.py`argv注入（`--sandbox read-only`）による既存の強制は変更せず残す（多層防御） |
| MCP | （手順のみ） | `scripts/mcp_server.py`（#263）を`~/.codex/config.toml`または`.codex/config.toml`の`[mcp_servers.rig]`に`command = "python3"`, `args = ["<repo>/scripts/mcp_server.py"]`として登録する |

インストール：`codex/skills/rig/`を`~/.agents/skills/rig/`（または repo 直下の`.agents/skills/rig/`）へコピー/シンボリックリンク、`codex/hooks.json`を`.codex/hooks.json`にコピーまたは`~/.codex/hooks.json`の`PreCompact`にマージ。`.codex/agents/security-reviewer.toml`はリポジトリ直下に置くだけでproject-scoped agentとして認識される（Codexの規約）。

**正直な検証範囲**：この環境には`codex` CLIが無く、実際のCodexセッションでの動作確認はできていません。実施した検証は次の通り：
- `codex/hooks.json`はJSONとして妥当。
- `.codex/agents/security-reviewer.toml`は`tomllib`でパース可能で、[Codex公式ドキュメント](https://developers.openai.com/codex/subagents)に記載のフィールド（`name`/`description`/`sandbox_mode`/`developer_instructions`）のみを使用。
- 既存の`--provider codex`ステートレス呼び出し（`build_argv`の`codex`分岐、`--sandbox read-only`の検証者強制含む）は本バッチで一切変更しておらず、`orchestrate.py selftest`の既存テストがそのままPASSすることで後方互換を確認。
- Skill配布形式・hooksの実際の発火・subagent TOMLでの`sandbox_mode`強制・MCPサーバ接続は、実際のCodex CLIでの実行が必要で**未検証**です。

### ホストアダプタ層（複数ホストへの一般化・#304）

#294はCodex限定だったが、Cursor・GitHub Copilot CLI等も同様の拡張機構（hooks/skills/MCP）を持つ。ホストごとの差分（hookイベント名・skill配置規約・機能対応度）を`scripts/host_adapters.py`の`HOSTS`辞書1箇所に集約し、新規ホスト対応は1エントリ追加するだけで済む設計にした。第二弾としてCursorを追加し、設計の妥当性を検証した：

```
| Host | skills | hooks | subagents | mcp | read_only_sandbox | precompact_context_injection | session_start | tool_acl |
|---|---|---|---|---|---|---|---|---|
| Claude Code | supported | supported | supported | supported | supported | supported | supported | supported |
| Codex CLI | supported | supported | supported | supported | supported | unverified | supported | unverified |
| Cursor | supported | supported | unverified | supported | unverified | unsupported | supported | partial |
```
（`python3 scripts/host_adapters.py`で再生成できる——このREADMEの表が古くなったら実行して差し替えること）

Cursorで具体的に分かったこと（`cursor.com/docs/hooks`・`/docs/skills`で確認）：
- **hookイベント名はcamelCase**（`PreCompact`→`preCompact`、`UserPromptSubmit`→`beforeSubmitPrompt`）——#304が懸念した通りホストごとに異なる。
- **skillは`.agents/skills/`もlegacy互換で読む**——`codex/skills/rig/SKILL.md`をそこに置けばCursorでもそのまま使える（新規ファイル不要）。
- **`preCompact`はobservational-onlyで、run-continuityの状態注入はできない**（公式ドキュメントに明記）。ここは黙って「動いたふり」をせず`degrade`として明示し（`cursor/hooks.json`は状態保全を諦め、短い通知メッセージのみを返す設計にした）、READMEの表上もunsupportedと表示する。

**正直な検証範囲**：`scripts/host_adapters.py`の対応表・golden fixture test（`tests/test_host_adapters.py`）はコードとして検証済み。実際のCursor/Codex実機でのhook発火・skill読み込みは未検証（Codexは上記と同じ理由、Cursorはこの環境にCursor自体が無いため）。Claude Code向けの既存動作は本バッチで一切変更していない。

### Fable 5 refusal-classifier→フォールバック（`--provider anthropic`・#297）

Fable 5はcyber/bio/reasoning_extractionの3分類に該当するリクエストを安全フィルターで自動遮断し、Opus 4.8へフォールバックする仕組みを持つ。`orchestrate.py run --provider anthropic`はAnthropic Messages APIを直接HTTPで叩き、この遮否を検知・記録・透過的にハンドリングする（`claude`/`rig`のCLI経由providerは構造化`stop_reason`を持たないため対象外）：

- `fallback_model`（例`claude-opus-4-8`）を設定すると`anthropic-beta: server-side-fallback-2026-06-01`を要求し、フォールバック成功時は`state["history"]`に`FABLE_FALLBACK`を記録して**gateを止めず処理を継続**する。
- フォールバック未設定/尽きた直接拒否は`FABLE_REFUSAL`（category/explanation）を記録し、silent失敗にしない。
- `runs --cost`にトークン計測（`cache_read_input_tokens`含む）とfallback/refusal発生件数を表示する。
- 攻撃手法の議論が本業の`security-reviewer`等のpersonaへFable 5を`--step-model`（#293）で割り当てる場合は`fallback_model`必須（`agents/security-reviewer.md`参照）。

**正直な検証範囲**：モックHTTPサーバ（Anthropic Messages APIのレスポンス形状を再現）で直接拒否・サーバー側フォールバック・通常成功の3パターンを確認済み。**実際のAnthropic APIには接続していません**（実運用トラフィックが必要かつ課金リスクを避けるため）。使用したスキーマは`anthropics/claude-cookbooks`の`fable_5_fallback_billing/guide.ipynb`に基づいていますが、実モデルでの動作は未検証です。

### manifest・知識層

`<repo>/.claude/rig.md` を置くと build/lint/test コマンド・branch/CI 戦略・reviewer・本番影響検知パターン・既定 recipe・既定 reviewer persona 等を設定できる（`skills/rig/manifests/_template.md` 参照）。知識層（`~/.claude/rig/knowledge/{methodology,ai-quirks}/`、`<repo>/.claude/rig/knowledge/domain/`）は全 RUN に注入され、実行を重ねるごとに蓄積される。

### 横断利用（CLI として）

決定論ランナー `scripts/orchestrate.py` は shim を1回置けばどのディレクトリからでも呼べる：

```bash
python3 scripts/orchestrate.py install-shim          # → ~/.local/bin/rig（symlink）
rig models                                           # 利用可能プロバイダ探索
rig probe --provider codex                           # 疎通テスト（read-only サンドボックス強制の実証も兼ねる）
rig run review-only --provider rig --verifier-provider codex
rig run bugfix --provider rig --step-model implement=claude-opus-4-8   # step 単位のモデル上書き（--step-model > recipe model: > --model）
rig resume run-state.json                            # verify-first 再開：現 step の checks を再実行し、世界がドリフトしていたら前進を拒否
rig-wb githooks install                              # pip 版：native pre-commit（manifest lint＋staged シークレットスキャン）/ pre-push（build＋test）フック。RIG_HOOK_SKIP*=1 で回避
rig-wb wb digest --period week                       # テレメトリの Markdown ダイジェスト（runs / gate / force-accept / ゴム印 / drill）
```

`$RIG_HOME` で install 先を上書き、`<cwd>/.rig/recipes/<name>.md` が同名 built-in recipe をプロジェクト overlay、recipe の `checks:` は呼び出し元プロジェクト（rig リポジトリではない）の cwd で実行される。

**プロジェクト recipe は初回のみ明示的な同意が必要。** プロジェクトローカルの recipe は shipped recipe を同名で overlay でき、その `checks:` 行は shell コマンドとして実行される——つまり repo を clone しただけでそのコマンドが動いてはいけない。`<cwd>/.rig/recipes/` 配下の recipe の初回ロードは拒否され、`--allow-project-recipes` フラグまたは `RIG_ALLOW_PROJECT_RECIPES=1` で明示的に同意して初めて読み込まれる。同意はコンテンツハッシュとして `~/.claude/rig/trusted-recipes.json` に記録され（保存先は `RIG_TRUST_STORE` で上書き可）、以降は黙って通る——ただしファイルを編集すると再同意が必要になる。shipped と org 層の recipe は対象外：あの置き場は作業対象のリポジトリではなく、あなた自身が設定する場所だから。

プロジェクト manifest `.claude/rig.md` も同じ trust store の背後にあり、専用の同意スイッチ（`--allow-project-manifest` / `RIG_ALLOW_PROJECT_MANIFEST=1`）を持つ。manifest は既定値を供給するだけなので、未同意でも recipe のようにハード拒否せず **soft degrade** する——警告1行を出して「manifest が無い」場合と同じ挙動に落ちる。同梱の git hook は manifest の lint/build/test コマンドを eval する前に記録済みハッシュを検証し、`rig-wb githooks install` がそのハッシュを記録する：hook のインストール＝その時点の manifest への同意であり、以後ファイルを編集すると再同意が必要になる。

## 14. Experimental commands

Experimental commands は、代替的なコラボレーション・創作・遊び心のあるワークフローを探索する。使っているゲートは他と同じ本物——`magi` の判定も `roast` のレビューも中身は本物のコンテンツで、おもちゃではない——が、初見の README を Core/Quality/Advanced のノイズにしないため、既定の日常パス・上記の tier からは意図的に外している。

| コマンド | 内容 |
|---|---|
| `/rig:magi`、`/rig:sage` | 決定/知恵モード——MAGI 3賢者合議の go/no-go 投票、sage 流の助言 |
| `/rig:roast`、`/rig:coin`、`/rig:duck`、`/rig:pre-mortem` | 中身は本物のユーモア pack 群（§8） |
| `/rig:party` | 実データの上に乗せた party/status 表示の novelty |
| `/rig:movie`、`/rig:scenario` | 動画作成ハーネスとそのシナリオライター前段 |

§4〜§9 で説明した Core の AI ワークベンチ体験には不要。

## 15. Implementation notes

上記の主張の裏付けを具体的に示す——「書いてあること」と「検証されていること」が静かに乖離しないための表：

| 機能 | 根拠 |
|---|---|
| recipe 解決・RESOLVE flag・size-aware ルーティング | `scripts/orchestrate.py selftest`（resolve/RESOLVE 区分） |
| isolated worktree のライフサイクル（作成/合流/dirty時保全/エスカレーション時保全） | `scripts/orchestrate.py selftest`（isolate 区分） |
| read-only verifier のサンドボックス強制（プロバイダ別 CLI flag） | `scripts/orchestrate.py probe` / `selftest`（probe 区分） |
| queue の dispatch・状態遷移 | `scripts/orchestrate.py selftest`（queue 区分） |
| recipe/persona/command のスキーマ、ブリック目録ドリフト、バージョン同期 | `scripts/validate.py` ＋ `scripts/validate.py selftest`（全 PR で CI 強制） |
| オーケストレータの単体挙動（recipe 解決と trust gate・queueing・run-state・graph・CLI 表面） | `pytest -q` — `tests/` 配下の54テストスイート。CI（`validate.yml`）が `ruff`（指摘0件）・validator・両 selftest とあわせて強制する |
| acceptance-gate の基準、accept/discard の機構 | `scripts/workbench.py` — リリースごとに scratch git repo で検証（詳細は `CHANGELOG.md` の各エントリ） |
| 実行テレメトリ | `.rig/runs.jsonl`（`scripts/orchestrate.py runs`）と `.rig/runs/<task-id>/*.json`（workbench の run state） |
| 失敗モード分類 | ESCALATE/BLOCKED の run は `failure_mode`（`classify_failure` による MAST 系タキソノミコード）を `.rig/runs.jsonl` に記録する。コード→ゲート/ブリックの写像とダッシュボード panel は `skills/rig/patterns/failure-taxonomy.md` |

## 16. FAQ

**`/rig:go` は `/rig:dev` を置き換えるの？** いいえ——`/rig:go` は自動分類する既定の入口、`/rig:dev` は recipe/step/flag を明示したいときの同じエンジン。

**作業中、自分の作業ツリーはどうなる？** 何も起きない。全作業は隔離 worktree/branch の中で行われる。作業ツリーが触られるのは `accept` のときだけで、それも staged（未コミット）差分としてのみ。

**gate を無視して進めたい場合は？** `accept` の `--force` は判断が伴う criterion（`acceptance_gate_not_failed`/`no_unrelated_diff`）だけを上書きでき、`forced: true` として記録される＝サイレントではない。構造的な前提（`worktree_exists`/`base_branch_recorded`/`diff_summary_generated`）は上書き不可——真偽がそのまま結果になる。

**reviewer/verifier がコードを書き換えることは？** ない。verifier はプロセスレベルで read-only 制限がかかっている（`Read,Grep,Glob`・サンドボックス shell）。`scripts/orchestrate.py probe` で確認できる。

**rig の状態はどこに置かれる？** `<repo>/.rig/runs/<task-id>/`（`.gitignore` への `.rig/` 追加は `/rig:init` が提案する）と、隔離タスクの場合はリポジトリ外の兄弟ディレクトリ `../rig-worktrees/<repo>/<task-id>/`。

**reviewer persona の質はどう分かる？** `/rig:drill` が既知のバグの種に対する検出率/誤検出率/severity精度/blocking精度/説明品質を採点する。`/rig:go stats` は5run以上でREJECTゼロの reviewer をゴム印疑いとして警告する。

**複数タスクを同時に走らせたら？** それぞれ専用の worktree と branch（`rig/<task-id>`）を持つので衝突しない。`accept` はメイン作業ツリーに対して行うため、1つ accept してコミットしてから次を accept する（作業ツリーがクリーンでないと accept 自体が拒否されるので、この順序は安全側に強制される）。

**ターミナルをいくつも開かずに、1セッションで複数タスクを並行開発できる？** できる——§5「isolated worktree → 複数タスクを並行で進める」を参照。`/rig:queue add` で積んで `/rig:queue go --provider rig --max-parallel N` で並列実行（各タスクは自動的に隔離される）、そのうえで `/rig:go board`（§10）を見れば、N個のターミナルの状態を頭の中で追う代わりに一箇所で全体を確認できる。

## ドキュメント

- [`skills/rig/SKILL.md`](./skills/rig/SKILL.md) — エンジン本体（PARSE/RESOLVE/COMPOSE/RUN の全仕様・rationalization 表・red flags）
- [`skills/rig/patterns/isolated-worktree.md`](./skills/rig/patterns/isolated-worktree.md) — worktree・run state の設計
- [`docs/architecture.md`](./docs/architecture.md) — アーキテクチャの実証ポイント
- [`docs/testing-scenarios.md`](./docs/testing-scenarios.md) — ディシプリン圧力シナリオ集
- [README.md](./README.md) — English version

## License

[MIT](./LICENSE) © 2026 itoh-shun
