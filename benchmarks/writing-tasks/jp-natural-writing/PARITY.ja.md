# GPT ↔ Claude 日本語 Parity Benchmark

`parity.py` は、GPT を reference writer、Claude を candidate writer として同じ日本語タスクを解かせ、**執筆者とは別のモデル**にブラインドで比較させるためのハーネスです。

目的は「Claude を GPT の口調にコピーする」ことではありません。GPT と Claude の出力品質差を測り、Claude 側に追加するルールが未知の問題でも差を縮めるかを検証します。

## 測定原則

- Reference と Candidate には同じ依頼を渡す。
- Judge にはモデル名・提供元・どちらが改善版かを渡さない。
- Judge の `identity` が Writer と同じなら実行前にエラーにする。
- 各問題は A/B と B/A の両順序で判定し、位置バイアスを観測する。
- スコアは `candidate_preference`。50% が head-to-head parity、50% 超なら Candidate 優位、50% 未満なら Reference 優位。
- ルール探索は `train`、選択は `dev`、最終確認は `holdout` で行う。holdout を見ながらルールを書き換えない。
- 生成と判定は checkpoint に保存できる。同じ fingerprint の再実行では完了済みジョブを再利用する。

## 収録ケース

`parity_cases.json` は 30 問です。10カテゴリ × `train/dev/holdout` 1問ずつで構成しています。

- business_chat
- code_review
- technical_explanation
- rewrite
- casual
- politeness
- ambiguity
- conciseness
- incident_report
- support_reply

既存の `jp-natural-writing` が技術記事の「人間らしさ」を測るのに対し、こちらは日常の業務・技術コミュニケーションを含む **GPTとの品質差** を直接測ります。

## Provider 設定

`parity.providers.example.json` はコマンド実行型です。Pythonからshellを経由せず argv を直接起動します。

初期例は次の3役です。

- Reference Writer: `gpt-5.6-sol`（Codex CLI）
- Candidate Writer: `claude-sonnet-5`（Claude Code CLI）
- Judge: `claude-opus-5`（Claude Code CLI）

Candidate と Judge は同じベンダーでも、`identity` が異なるモデルなら実行できます。より厳密にしたい場合は Gemini 等の第三モデルを Judge として追加してください。複数 Judge の結果は同じケース単位で平均されます。

コマンドが最終回答以外も stdout に出す場合は `output_mode: "file"` を使い、argv に `{output_file}` を置きます。Codex CLI の例はこの方式です。

### ローカル設定を必ず隔離する

Writer/Judge はローカルの CLI 設定を一切読まない状態で起動します。読ませると、測っているのが「モデルの日本語品質」ではなく「そのマシンの設定」になります。

`claude` には `--safe-mode` を付けます。認証・モデル選択・組み込みツールはそのままに、hooks・CLAUDE.md・skills・plugins・MCP を無効化します。**これが無いと出力が壊れます**: Stop hook が `decision: "block"` を返す環境では追加ターンが強制され、`--output-format text` が拾う最終メッセージが hook 由来のコメントに置き換わります。Writer なら回答でない文字列が候補文として、Judge なら判定でない文字列が判定として記録されます。エラーにはならず、静かに全ケースが汚染されます。

`codex` には `--ignore-user-config` を付けます。`$CODEX_HOME/config.toml` を読まなくなるため、そこに登録された hooks / plugins が外れます。

既知の残差: `--ignore-user-config` を付けても `$CODEX_HOME/skills` は読み込まれます（`Skill descriptions were shortened to fit the 2% skills context budget` の警告が出ます）。完全に揃えたい場合は、認証情報だけを置いた最小ディレクトリを `CODEX_HOME` に指定して起動してください。揃えないまま測る場合は、結果に「Reference 側のみ skills 常駐」と明記します。

`cwd_mode: "temp"` により、リポジトリ直下の `AGENTS.md` / `CLAUDE.md` / プロジェクト skills は読み込まれません。

Provider 設定を変えると fingerprint が変わり、古い checkpoint は再利用されません。設定は測定の一部なので、結果 JSON と一緒に「どの argv で測ったか」を残してください。

## まず設定だけ検証

```bash
cd benchmarks/writing-tasks/jp-natural-writing
python parity.py --dry-run
```

ここで Writer/Judge の identity 衝突、ケース定義、Provider 設定を検証します。モデル呼び出しは行いません。

## 1. Raw baseline

最初は Candidate にルールを与えず、素の差を取ります。

```bash
python parity.py \
  --split train \
  --checkpoint /tmp/jp-parity-train-raw.checkpoint.json \
  --json-out results/jp-parity-train-raw.json
```

`candidate_preference = 0.50` が同等です。カテゴリ別スコアと `dimensions` を見て、Claude が落としている領域を特定します。

## 2. ルールを適用して dev で比較

`parity-rules-seed.ja.md` は初期仮説です。最終ルールではありません。train の負け方を見て編集・変異させます。

```bash
python parity.py \
  --split dev \
  --candidate-rules parity-rules-seed.ja.md \
  --checkpoint /tmp/jp-parity-dev-seed.checkpoint.json \
  --json-out results/jp-parity-dev-seed.json
```

ルールを変えると fingerprint が変わるため、古い checkpoint を誤って再利用できません。

## 3. holdout は最後に一度だけ

```bash
python parity.py \
  --split holdout \
  --candidate-rules parity-rules-seed.ja.md \
  --checkpoint /tmp/jp-parity-holdout.checkpoint.json \
  --json-out results/jp-parity-holdout.json
```

目標は「train で勝つこと」ではなく、holdout でも 50% 付近まで差が縮むことです。Candidate が 50% を明確に超えた場合は、GPT parity 到達ではなく **そのテスト集合では Candidate が好まれた** と読みます。

## 結果の読み方

例:

```text
candidate preference: 47.5%  (95% CI by case 39.2-55.8%)
                       50.0% = GPT/Claude head-to-head parity
order consistency    : 90.0%
```

`ci95_by_case` は各ケースを独立単位として計算した簡易95%区間です。A/B と B/A を独立サンプルとして水増ししません。

`order_consistency` が低い場合、Judge が内容より位置に影響されている可能性があります。その状態で 1〜2ポイントの差を改善と断定しないでください。

## 次に RIG へ足すもの

このPRの最小実装では **benchmark / blind pairwise judge / parity report** までに留めます。次段階では train の負け理由からルール候補を複数生成し、dev スコアが改善した変異だけ残す `evolve` ループをこの測定器の上に載せます。
