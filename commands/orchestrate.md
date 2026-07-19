---
description: "rig/orchestrate — 計算的オーケストレーション。recipe のステップ遷移・ゲート・リトライ・停止・状態保持を決定論ランナー(scripts/orchestrate.py)がコードで強制。run で各 step を別プロセスの rig ハーネスとして自走実行（並列検証・マルチプロバイダ）。"
argument-hint: "[recipe（省略時は現在の合成ハーネス）] [--run] [--provider rig|claude|mock] [--isolate] [--auto-route] [--max-parallel N] [--quorum all|majority] [--plan]"
---

# rig/orchestrate — 計算的オーケストレーション 🧭⚙️

**まず `rig` skill を Skill ツールで起動し、その SKILL.md に従うこと。** このコマンドは `--orchestrate` の入口＝**舵をコードが握る**モード。制御ループ（次の step・ゲート合否・リトライ・停止条件・状態保持）を散文でなく **`scripts/orchestrate.py`（決定論ランナー）** に強制させる。手順・契約は `patterns/computational-orchestration` に従う。

```
$ARGUMENTS
```

## 2つの使い方

**① 半自動（モデルが各 step の作業をする）**
ランナーが遷移を決め、モデルが各 step を委譲実行する：
```
orchestrate plan   <recipe>            # ステップ状態機械を算出（--plan 相当・モデル不要）
orchestrate init   <recipe> [--goal G] # run-state を作り最初のアクション
orchestrate next   run-state.json      # 次の遷移を決定論的に計算（START/ADVANCE/RETRY/AWAIT/BLOCKED/ESCALATE/DONE）
orchestrate check  run-state.json      # step の checks:（lint/test 等）を実行＝計算的センサー
orchestrate verdict run-state.json --by <reviewer> --pass|--fail   # 独立検証者の判定（採点者≠生成者）
```

**② 全自動（各 step を別プロセスの rig ハーネスで実行）**
```
orchestrate run <recipe> --provider rig --isolate \  # worktree 隔離（green だけ ff 合流）
    [--verifier-provider rig] [--max-parallel N] [--quorum all|majority] [--goal G]
```
- **`--provider rig`**：各 step を **`rig` skill で起動した別プロセス**として実行（rig を名前で呼ぶ＝再帰 rig ハーネス）。他に `claude` / `codex` / **`grok`（grok-build headless。`grok -p`。read-only/sandboxフラグ未文書のため検証者のread-only強制はプロンプト契約のみ・#328）** / **`ollama`・`lmstudio`（ローカル LLM・OpenAI 互換）** / **`anthropic`（Anthropic Messages API 直叩き。Fable 5 refusal-classifier→フォールバック検知・#297。⑤参照）** / `cmd`（任意 CLI）/ `mock` も選べる。ローカル LLM は `--model <name>`（ollama 既定 `llama3.1`）・`--base-url <url>` で調整、要サーバ起動。
- **`--auto-route`**（#264）：step の `auto_route.candidates`（`{model, cost_tier, max_size}` の列。安い順に宣言する）を、現在の diff size（`--diff-git` と同じ自動測定）に応じて決定論的に選ぶ。`auto_route` を宣言していない step には影響しない（既存の `model:` / `--model` が優先されたまま）。選択理由は run-state の `history`（`action: AUTO_ROUTE`）と `runs.jsonl` の `steps[].auto_route` に記録される。
- **`--auto-route-learn`**（#305）：`--auto-route`をさらに発展させ、`.rig/runs.jsonl`の実績（recipe/step別のmodel使用実績とgate通過率）から頻度ベースで学習する。既定は**shadow mode**（予測は`steps[].learned_route`に記録するのみで実際の選択には使わない）。`--auto-route-mode active`で初めて予測を適用する。参照run不足・低pass_rate時は静的`--auto-route`にフォールバックし、棄却理由（counterfactual）を必ず記録する。`--exploration-pct N` [--exploration-date D]で、一定割合のrunだけ次点候補を試す（`--exploration-date`＋recipe/stepのハッシュで決定論的に判定、乱数は使わない）。

**③ 動的モデル探索（利用可能なものを自動設定）**
```
orchestrate models [--save] [--json]   # 起動中の LLM サーバ/CLI を探索して一覧
orchestrate run <recipe> --provider ollama --auto-model   # 実機から動的にモデルを選ぶ
```
- `models`：`ollama`/`lmstudio` の `/v1/models` を叩いて**利用可能モデルを動的取得**、`claude`/`codex`/`rig` は CLI 有無を表示。`--save` で `~/.claude/rig/models.json` に保存（次回 `--auto-model` が参照）。
- **`--auto-model`（`--auto-model-setting` も可）**：`--model` 未指定時、保存設定→実機の `/v1/models` 先頭→既定 の順でモデルを自動解決。サーバ不在でも crash せず既定にフォールバック。

**④ プロバイダ疎通テスト（`probe`）**
```
orchestrate probe --provider codex                 # 検証ロールで1回叩く（VERDICT を確認）
orchestrate probe --provider codex --role generator
orchestrate probe --provider ollama --model llama3.1
```
プロバイダを**1回だけ**実行し、(1) 実際に投げるコマンド/エンドポイント、(2) 終了コード、(3) 生出力、(4) 契約（`VERDICT`/`STATUS`）がパースできるか を表示。exit 0＝rig から使える。`✗` のときは `--provider-cmd "codex exec --... {prompt}"`（cmd プロバイダ）で実コマンド/フラグを合わせる。
- **並列検証**：gated step の `personas` を同時プロセスでファンアウト（`--max-parallel`）。集約は決定論（`--quorum all`＝全員一致／`majority`＝過半数）。
- **judge-panel**：`--generators rig,claude,codex`＝複数モデルに同じ step を並列生成させ、judge が最初に PASS した候補（列の順＝決定論）を勝者に選ぶ。
- **step-DAG 並列**：recipe step に `needs: [id…]` があれば、依存を満たした独立 step を同一 wave で同時プロセス実行（intake → {design,test 並走} → merge）。
- **構造的に採点者≠生成者**：検証は別プロセス（別プロバイダ可）の rig 検証者が `VERDICT: PASS|FAIL` を返す。

**⑤ Fable 5 refusal-classifier→フォールバック（`--provider anthropic`・#297）**
```
orchestrate run <recipe> --provider anthropic --model claude-fable-5 --step-model <id>=<model>
```
`--provider anthropic`はAnthropic Messages APIを直接HTTPで叩く（`claude`/`rig`のCLI経由providerではない——CLIは`--output-format text`のため構造化された`stop_reason`を持たない）。`cfg`に`fallback_model`（例: `claude-opus-4-8`）を設定すると、`anthropic-beta: server-side-fallback-2026-06-01`を要求し、Fable 5のrefusal-classifier（cyber/bio/reasoning_extractionの3分類）が発火した場合にサーバー側で透過的にOpus 4.8へフォールバックする。
- **フォールバック成功**：`state["history"]`に`FABLE_FALLBACK`（from/to model）を記録し、**gateを止めず通常のstep成果として処理を継続する**（#297の要求通り）。
- **フォールバック未設定/尽きた直接拒否**：`FABLE_REFUSAL`（category/explanation）を記録し、rc=1でstepに伝える（silent失敗にしない）。
- **コスト**：`usage.input_tokens`/`usage.output_tokens`/`usage.cache_read_input_tokens`（フォールバック済みprefixの10%課金対象）を`runs --cost`の`anthropic`行に集計する。`fallback`/`refusal`の発生件数もサマリ末尾に表示される。

`security-reviewer`等、攻撃手法の議論が本業のpersonaにFable 5を`--step-model`で割り当てる場合は、`fallback_model`を必ず設定すること（`agents/security-reviewer.md`参照）。

**正直な検証範囲**：モックHTTPサーバ（Anthropic Messages APIのレスポンス形状を再現）で直接拒否・サーバー側フォールバック・通常成功の3パターンを確認済み。実際のAnthropic APIへは接続していない（課金・実運用リスクを避けるため）——実モデルでの`stop_reason: refusal`発火・実際のfallback課金は未検証。

**⑥ A/Bレシピ実験（`ab`・#291）**

```
orchestrate ab <recipe1> <recipe2> [...] --provider mock --goal "<goal>" [--verifier-provider V] [--max-steps N]
```

同一タスクを複数recipeバリアントで**真に並走**実行し、速度(elapsed)・リトライ回数・最終状態を比較する。各variantは`--isolate`と同じ隔離worktreeで独立実行される（ファイル競合なし）ため、`ThreadPoolExecutor`で安全に並列化できる。比較したいのは「recipeの違い」であって「model/providerの違い」ではない前提——providerは全variant共通で1つ指定する。

```
## rig ab — recipes/bugfix.md vs recipes/hotfix.md

recipe               final      elapsed(s)   retries  worktree
bugfix               DONE       42.3         0        -
hotfix               DONE       18.7         1        -
```

未達/dirtyのvariantはworktreeが保全される（`--isolate`と同じ規則）。後片付けは`git worktree remove --force <dir>`。

**ルールA/B（manifest差分・#317）**：同一recipeを、manifestだけ差し替えた2条件で並走させる——「ルールに足す変更」は静的に評価できず、実タスクを走らせて比較するしかない、という運用知見への対応。

```
orchestrate ab <recipe> --manifest-a <path> --manifest-b <path> --provider mock --goal "<goal>"
```

各variantのworktree内に指定manifestを`.claude/rig.md`として書き込み（メイン作業ツリーは触らない）、content hashを信頼ストアに記録する（**CLIで明示的に渡した＝同意**。`--allow-project-manifest`と同じ同意モデル）。比較表の行ラベルは`A(<stem>)`/`B(<stem>)`でどちらのmanifestか明示される。**正直なスコープ**：variantのmanifestが効くのは**worktree内をcwdとして走る入れ子provider呼び出し**（cwd基準でmanifestを解決するため）。親orchestrateプロセス自身の`load_manifest()`（--auto-routeのサイズ分類等）は呼び出し元repoのmanifestを読み続ける。recipe/provider/modelは全variant共通——測っているのは「ルールの違い」だけ。

**⑦ ギャップ処方箋のforge下書き化（`runs`・#268）**

```
orchestrate runs
```

同一(recipe, step)で2回以上エスカレーションしていると、「## Gap prescriptions」に**具体的な`/rig:forge`下書き依頼コマンド**を表示する（該当stepでREJECTしたreviewer上位3件を検証票（`steps[].verdicts`）から特定し、説明文に埋め込む）。orchestrate.py自身はforgeを呼ばない（LLMが要る処理のため）——生成するのは「コピペで使えるforgeプロンプト」まで。下書き確認・確定は人/AIが行い、`/rig:drill --replay`で改善を再測定する運用。

**⑧ Managed Agents API委譲（review-gate並列fan-out・実験的opt-in・#295）**

```python
cfg["parallel_backend"] = "managed-agents"
cfg["environment_id"] = "<Managed Agentsのホスト環境ID>"  # 必須
```

`_execute_step`のreview-gate並列検証は既定で`run_verifiers_parallel`（subprocess + ThreadPoolExecutor）を使う。`cfg["parallel_backend"] == "managed-agents"`のときのみ、`run_managed_agents_fanout`がAnthropic Managed Agents API（coordinator/worker構成のbeta・`managed-agents-2026-04-01`）へ委譲する——**既定経路は完全に無変更**。persona 1つにつきworker agentを1つ作成し、判断のみのcoordinatorが束ねる。`threads.list`をポーリングし、全workerが揃うか`managed_agents_max_polls`（既定30・`managed_agents_poll_interval`既定2秒）に達したら打ち切る。戻り値の形は`run_verifiers_parallel`と同じ（`{by, persona, provider, ok, note}`の列）なので`_execute_step`のpass/fail判定ロジックは変更不要。

- **必須**: `cfg["environment_id"]`（Managed Agentsのホスト環境）。未設定なら即座にエラー票を返す（silent失敗にしない）。
- **未報告worker**: `max_polls`尽きても揃わなかったworkerは`timeout`票として明示（黙って欠落させない）。
- **トークン計測**: workerスレッドの`usage`を`cfg["_token_usage"]["managed-agents"]`に集計（`runs --cost`と同じ集計経路）。
- **history**: `MANAGED_AGENTS_SESSION`アクション（session_id・worker数）を`state["history"]`に記録。event-stream自体のrun-continuityヘッダ統合は未実装。

**正直な検証範囲**：REST エンドポイントパス（`/v1/agents`等）は公式Python SDKのメソッド名（`client.beta.agents.create`等、`anthropics/claude-cookbooks`の`managed_agents/CMA_plan_big_execute_small.ipynb`参照）からの推測であり、公式RESTリファレンスから直接確認したものではない。モックHTTPサーバで全呼び出し順序（worker/coordinator作成・session作成・event送信・threadsポーリング・集計・environment_id未設定のエラー経路）を検証済みだが、**実際のAPIには未接続**。worker/coordinator間のコンテキスト分離はAnthropicサーバ側の性質でありクライアントコードからは検証できない——ここで検証したのは「rig側のコードが生のworker出力を要求／転送せず、APIの返した最終結果のみを読む」ことのみ。

## 自動有効化（明示しなくても通る）

`--orchestrate` を明示しなくても、次のとき自動で orchestrate を通る（§4.3）：
- **recipe が `checks:` か `needs:` を宣言**＝決定論で回す意図のある recipe（機械検証 or DAG 並列）。
- **manifest `default_orchestrate: true`**＝プロジェクト全体の既定。

`--no-orchestrate` でその run だけ従来の散文エンジンに戻せる。単発生成コマンド（`/rig:persona` 等）には作用しない。`plan` 出力に `自動 orchestrate: auto ON/off` が出る。

## 効く所

- **prose の制御ループ ≪ コードの強制**（`harness-taxonomy`）。遷移・停止・リトライをコードが握る。
- **状態は `run-state.json` に永続**＝圧縮・再起動を跨いで同じ状態機械を再開（run-continuity の計算版）。
- **opt-in＝engine 不変**。各 step の中身は従来の rig が回す（Thin Harness, Fat Skills）。

## 注意

- `--provider rig`/`claude` は**入れ子で claude が起動**＝コスト・再帰に注意。設計確認は `--provider mock`（別プロセスだが即返す決定論ダミー）。
- ゲート未達 K 回で `ESCALATE`（無限ループ禁止）、自己採点（by=self）は `BLOCKED`。
- 決定論は `orchestrate selftest` で検証できる。


## run-continuity（SKILL.md §6）

RUN 中は各ターン冒頭に次の run-status ヘッダを1行必ず再掲すること。中断・質疑・tool 出力の直後でも省かない（可視化＝駆動の証拠）:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
