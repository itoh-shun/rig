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
- **`--provider rig`**：各 step を **`rig` skill で起動した別プロセス**として実行（rig を名前で呼ぶ＝再帰 rig ハーネス）。他に `claude` / `codex` / **`ollama`・`lmstudio`（ローカル LLM・OpenAI 互換）** / `cmd`（任意 CLI）/ `mock` も選べる。ローカル LLM は `--model <name>`（ollama 既定 `llama3.1`）・`--base-url <url>` で調整、要サーバ起動。
- **`--auto-route`**（#264）：step の `auto_route.candidates`（`{model, cost_tier, max_size}` の列。安い順に宣言する）を、現在の diff size（`--diff-git` と同じ自動測定）に応じて決定論的に選ぶ。`auto_route` を宣言していない step には影響しない（既存の `model:` / `--model` が優先されたまま）。選択理由は run-state の `history`（`action: AUTO_ROUTE`）と `runs.jsonl` の `steps[].auto_route` に記録される。

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

## 自動有効化（明示しなくても通る）

`--orchestrate` を明示しなくても、次のとき自動で orchestrate を通る（§4.3）：
- **recipe が `checks:` か `needs:` を宣言**＝決定論で回す意図のある recipe（機械検証 or DAG 並列）。
- **manifest `default_orchestrate: true`**＝プロジェクト全体の既定。

`--no-orchestrate` でその run だけ従来の散文エンジンに戻せる。単発生成コマンド（`/rig:persona` 等）には作用しない。`plan` 出力に `自動 orchestrate: auto ON/off` が出る。

## ⑤ A/Bレシピ実験（`ab`・#291）

```
orchestrate ab <recipe1> <recipe2> [...] --provider mock --goal "<goal>" [--verifier-provider V] [--max-steps N]
```

同一タスクを複数recipeバリアントで**真に並走**実行し、速度(elapsed)・リトライ回数・最終状態を比較する。各variantは`--isolate`と同じ隔離worktreeで独立実行される（ファイル競合なし）ため、`ThreadPoolExecutor`で安全に並列化できる。比較したいのは「recipeの違い」であって「model/providerの違い」ではない前提——providerは全variant共通で1つ指定する。

## ⑥ ギャップ処方箋のforge下書き化（`runs`・#268）

```
orchestrate runs
```

同一(recipe, step)で2回以上エスカレーションしていると、「## ギャップ処方箋」に**具体的な`/rig:forge`下書き依頼コマンド**を表示する（該当stepでREJECTしたreviewer上位3件を検証票から特定し、説明文に埋め込む）。orchestrate.py自身はforgeを呼ばない(LLMが要る処理のため)——生成するのは「コピペで使えるforgeプロンプト」まで。下書き確認後の確定は人/AIが行い、`/rig:drill --replay`で改善を再測定する運用。

```
## rig ab — recipes/bugfix.md vs recipes/hotfix.md

recipe               final      elapsed(s)   retries  worktree
bugfix               DONE       42.3         0        -
hotfix               DONE       18.7         1        -
```

未達/dirtyのvariantはworktreeが保全される（`--isolate`と同じ規則）。後片付けは`git worktree remove --force <dir>`。

## ⑦ トークン/コスト集計（`runs --cost`・#271/#296）

```
orchestrate runs --cost
```

HTTP系provider（`ollama`/`lmstudio`など OpenAI 互換API）は応答JSONの`usage`フィールド（`prompt_tokens`/`completion_tokens`）を自動捕捉し、`runs.jsonl`の各runに`token_usage`として記録される。`runs --cost`はこれを recipe × provider でロールアップして表示する：

```
## rig runs --cost（全 3 run）

  bugfix:
    ollama       calls=4    prompt=812      completion=340      total=1152
```

**スコープの正直な限界**：`claude`/`codex`はCLI経由で呼び出すため構造化された`usage`を返さず、この集計の対象外（トークン計測なしと表示される）。それらのコスト把握には、Anthropic公式の Usage & Cost Admin API の利用を検討すること——rigはこれを模造・推定しない。

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
