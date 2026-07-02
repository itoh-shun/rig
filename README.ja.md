# rig

## 概要

ブリック（facet / pattern / step）を起動時に動的に組み合わせ、タスクに最適化されたエージェント・ハーネスを engineering する汎用開発フロー・オーケストレータ。3-Stage フロー（計画→実装→検証）は数あるレシピの1つに過ぎず、プラグインそのものは特定フローに縛られない。Claude Code ネイティブ（command + skill + agents）として動作し、重い DSL エンジンや外部依存は持たない。ブリックを追加するだけで任意のフローを組み立てられる軽量な設計を原則とする。

**run-continuity（中断後も駆動を切らさない）**: 開発の途中で質疑・脱線が挟まっても rig が静かに「素の Claude」へ戻らないよう、RUN 中は各ターン冒頭に状態ヘッダ（`▸ rig | recipe … | step … | gate …`）を再掲し、中断後は必ず再アンカーしてから現 step に戻る。step 境界にも印を出すので、**rig が今も駆動中だと常に目で確認できる**。**コンテキスト自動圧縮も生き延びる**：同梱の `PreCompact` フック（`hooks/`）が圧縮時に run-state の保全指示を注入し、`/rig:init` は同じ保全文を CLAUDE.md "Compact Instructions" にも置ける（詳細は `SKILL.md` §6 run-continuity）。

## install

本リポジトリには `.claude-plugin/marketplace.json` を同梱しているので、marketplace 経由でインストールできる（CC のバージョンにより手順が異なる場合がある）。プラグイン名は `rig`、marketplace 名は `itoshun-local-plugins`。

### 方法 A: GitHub から（推奨）

```bash
/plugin marketplace add itoh-shun/rig
/plugin install rig@itoshun-local-plugins
```

`owner/repo` 形式で marketplace を追加し、`rig@<marketplace 名>` で install する。

### 方法 B: ダウンロード（ZIP / clone）から

```bash
# リポジトリの ZIP を展開、または git clone した後、その展開フォルダを指定：
/plugin marketplace add /path/to/rig
/plugin install rig@itoshun-local-plugins
```

展開フォルダ内の `.claude-plugin/marketplace.json` が読み込まれる。

### 方法 C: --plugin-dir（開発・テスト用、反復が速い）

```bash
cd /path/to/rig
claude --plugin-dir .
# 編集後の再読み込み: /reload-plugins
```

### 呼び出し（namespace に注意）

スラッシュコマンドはプラグイン名で namespace される：

- **コマンド**: `/rig:dev` — 開発フローの入口。例: `/rig:dev --plan --only review "現在の変更"`
- **コマンド**: `/rig:sales` — sales ドメインの入口。既定は商談記録を5観点で評価。**`--material` / `--script`** で**開発資材**（README/CHANGELOG/コード/リリース）から**営業1枚資料・荷電スクリプト**を生成（機能→ベネフィット翻訳・実在機能のみ・誇張なし）。例: `/rig:sales ./deals/acme.md` ・ `/rig:sales --material --script`
- **コマンド**: `/rig:movie` 🎬 — CHANGELOG から短い**リリーストレーラー**を作る。制作台本（絵コンテ/テロップ/VO/尺/BGMキュー＋ソース対応表）＋ブラウザで**再生できるアニメ HTML トレーラー**（[`web/release-trailer.html`](./web/release-trailer.html)）の2点。**`--hyperframes`** で [HeyGen HyperFrames](https://github.com/heygen-com/hyperframes)（HTML→決定論的 MP4・GSAP seekable・Apache-2.0）コンポジションも生成し、`npx hyperframes render` で**本物の MP4** を出せる（例: [`video/launch-film/`](./video/launch-film/)）。ハイプだが全ビートが実機能の裏打ち。harness はコンポジションまで生成し render はユーザー環境（Node22+/FFmpeg/Chrome）。例: `/rig:movie v0.30.0` ・ `/rig:movie --hyperframes`
- **コマンド**: `/rig:scenario` 🎬✍️ — `/rig:movie` の**前段**＝シナリオライターモード。動画の物語（フック→課題→転換→ペイオフ→CTA・VO 草案・各ビートの source 対応）を書き、**検閲**する — `ai-smell-reviewer`（＋`ai-writing-smells`）で AI 臭・空ワードを、`sns-post-reviewer` でフック/ブランド/誇張リスクを、`engagement-reviewer` で**動画としての面白さ**（掴み・テンポ・ペイオフ・記憶に残る山場＝「正しいが退屈」を撃つ）を判定 → acceptance-gate で収束。検閲の土台は既存の掛け合わせ・面白さ軸だけ新設。任意で**作家性レンズ**（`--persona auteur/deconstructionist`＝解体派：本音/緊張/間/形式破壊 ・ `--persona auteur/humanist`＝人間派：温かさ/誠実/日常の発見）を足すと、より尖った演出批評が得られる（実名を避けた作家アーキタイプ・直交2軸）。例: `/rig:scenario before/after 紹介・開発者向け・60秒`
- **コマンド**: `/rig:talk` — JARVIS 的な会話モード。話しかけると意図を汲んで適切な rig フロー(dev/sales)へ橋渡しして実行する。例: `/rig:talk 今の変更だけ軽くレビューして`
- **コマンド**: `/rig:goal` — ゴール駆動ループ。高レベルな目標を渡すと受け入れ基準に変換し「現状把握→次手→既存フローへ委譲→照合」を達成まで回す。例: `/rig:goal "ログイン不具合を回帰込みで直して review 通過まで"`
- **コマンド**: `/rig:pr` — 既存 PR レビュー。PR 番号/URL を GitHub MCP で取得し security/design/test の3観点で並列評価して structured verdict を返す。例: `/rig:pr 1234 --adversarial`
- **コマンド**: `/rig:magi` — エヴァの MAGI を模した3賢者合議モード。「やるべきか？」の決定を Melchior-1（科学者＝正しさ）/ Balthasar-2（母＝守り）/ Casper-3（女＝価値）の直交3観点に並列で諮り、**決定論的な多数決**で go/no-go を MAGI コンソールに裁定する。例: `/rig:magi この破壊的変更を今リリースしていいか`
- **コマンド**: `/rig:roast` 🌶️ — 毒舌スタンダップ芸人によるコードレビュー。指摘の中身は本物（AI 臭・可読性・過剰/不足・バグ）だが、ローストとして届けることで批判を**実際に読ませる**。笑いは配送装置で判定は素面。例: `/rig:roast`
- **コマンド**: `/rig:coin` 🪙 — magi の対極。**可逆で些末**な 50/50（N 択可）を熟考せず即断する反-bikeshed ゲート。先にトリアージし、重い/不可逆と判明したら投げず `/rig:magi` へ回す。例: `/rig:coin タブかスペースか、もう決めて`
- **コマンド**: `/rig:duck` 🦆 — ラバーダック・デバッグ。机のアヒルに問題を説明する会話モード。アヒルは**質問しかせず、コードも答えも出さない**ので、説明している本人が穴に気づく（実証済みの技法）。気づいた後の修正は `/rig:dev` 等へ委譲。例: `/rig:duck なぜか nil が返る`
- **コマンド**: `/rig:pre-mortem` ⚰️ — 事前検死（magi の闇の兄弟）。「**もう本番で壊れた**」前提で失敗モードを断定形で逆算し、各々に最小ガードレールを対で出す。prospective hindsight は「何が起きうる?」より失敗を多く見つける。例: `/rig:pre-mortem この DB 移行`
- **コマンド**: `/rig:init` — リポジトリを rig 向けに初期化。manifest(.claude/rig.md)・知識層ディレクトリ・CLAUDE.md "Compact Instructions" 節を雛形生成（圧縮で rig 状態を失わない第2経路）。書き込みは確認必須・冪等。
- **コマンド**: `/rig:persona` — 説明文から reviewer persona を生成し、product 単位(project 層・既定)か global(`--user`)に保存。`--persona <name>` で review に投入できる。例: `/rig:persona "80年代の音楽を理解しているレビュアー"`
- **コマンド**: `/rig:knowledge` — ドメイン知識を **LLM-wiki ページ**（1概念=1正準ページ・相互リンク `[[slug]]`）として生成。説明文 or `--auto`(repo 解析)から、global(既定・全プロダクト共有)/project overlay に保存。persona は事実を埋め込まず `inject: [[slug]]` で参照＝暗黙知化させない。`--research "<トピック>"` で web から収穫(多ソース・相互照合・出典必須・非信頼データとして読む)。shipped wiki は**8ページ**(appsec/injection手口/expand-contract/perf落とし穴/golden signals/semver/ライセンス互換/loop engineering)が対応 reviewer に inject 済み。例: `/rig:knowledge --auto` ・ `/rig:knowledge --research "GraphQL N+1"`
- **コマンド**: `/rig:design` 🎨 — UI/UX・a11y を内蔵したデザイン作成ハーネス。説明文から**デザイン仕様書／コンポーネント仕様／ワイヤー／a11y 計画**を生成し、`ux-reviewer`（ユーザビリティ）・`a11y-reviewer`（WCAG 2.2）で並列検閲して acceptance-gate で収束。引数に**画面 URL** を渡すと Playwright で実装画面を取得し UI/UX・a11y を**監査**する。`--ppt`(PowerPoint)・`--claudedesign`(claude.ai デザイン) で追加出力（併用可）。例: `/rig:design ログイン画面 --ppt` ・ `/rig:design https://example.com/login`
- **コマンド**: `/rig:import` 📥 — ネット上の外部 skill(GitHub の SKILL.md / plugin)を rig に取り込む。解析して**委譲(最優先)→翻訳→知識のみ**を判断し、生成は既存ジェネレータへ委譲、出所と SHA-256 を `skills-lock.json` に記録。`--discover "<欲しい能力>"` でネットから探す(GitHub 横断検索→適合度/ライセンス/保守性/重複でランク→短リスト。見つからなければ `/rig:persona`/`/rig:forge` の自作へ＝探す→無ければ作る)。`--all` で走査候補全件を一括取り込み(判断サマリ一覧→一括承認1回→lock 一括記録)。lock 前に **import-gate**(persona はサンプル diff で契約遵守を実地試験・recipe は plan --json+validate)＝「取り込んで動いた」まで保証。`.cursorrules`/`AGENTS.md`/他 repo の `CLAUDE.md`/MCP ツール定義などの**方言**も翻訳対象。`--check-updates` で全取り込み skill の上流差分を検知(提案まで・自動追従なし)。`/rig:forge`(自作)の対＝既にあるものを取り込む。例: `/rig:import anthropics/skills --path skills/frontend-design/SKILL.md` ・ `/rig:import ~/.claude/skills --all` ・ `/rig:import --check-updates`
- **コマンド**: `/rig:drill` 🎯 — reviewer 検出率の実測(ミューテーション・ドリル)。既知のバグの種を使い捨て worktree の diff に注入→review fan-out→**reviewer 別の検出/見逃し/誤検出スコアボード**＝ペルソナ品質を意見でなく数字に。`--replay <persona>` でペルソナ編集後に過去 diff へ再実行し verdict 差分(ペルソナの snapshot テスト)。例: `/rig:drill --seeds 10 --verify-findings`
- **コマンド**: `/rig:sage` 🔮 — 大賢者に正解を問う(転スラ・オマージュ。`/rig:magi` と同じ「ネタだが中身は本物」流儀)。《告》《解》〜＝調べてから断定・確度+証拠アンカー必須・解答不能は臆さず宣言(**捏造は機能として存在しない**)。`--evolved`＝智慧之王: 複数仮説の並列演算・《予測》帰結+発生確率・《提案》最適解+次善。例: `/rig:sage なぜ本番だけ500?` ・ `/rig:sage --evolved Redis か in-memory か`
- **コマンド**: `/rig:party` 🎮 — ハーネスを **RPG のパーティ画面**で見る: Lv=DONE数・出撃/REJECT=テレメトリ・⚔検出率=`/rig:drill` 実測・実績🏆=機械判定。ゲーム画面に見えて全行が実データの健康診断(「未測定」はそのまま較正TODO)。描画は決定論スクリプト `orchestrate.py party`。manifest `sage_notifications: true` で import/persona/capture の完了報告に大賢者スタイルの獲得通知(《告》スキル「…」を獲得しました)が付く。例: `/rig:party`
- **コマンド**: `/rig:export` 📤 — import の対＝還元。rig で育てたブリック(persona/recipe/pack)を **rig を知らない人がそのまま使える独立 skill**(SKILL.md+README+references+LICENSE)に書き出す。契約はインライン展開・wiki は同梱・gate は散文に翻訳・出所とライセンスの連鎖を継承。GitHub に置けば他者が `/rig:import` で取り込める＝吸収と還元の輪。例: `/rig:export --persona house-authenticity`
- **コマンド**: `/rig:catalog` — 横断レジストリ(`--list --global`)。全 tier(shipped＋global＋project)を走査し `domain×pack×persona×wiki×recipe` の地図を tier つきで表示＝「誰がどこで何してるか」を取り戻す。派生・読み取り専用。`--validate --global` は tier 横断の衛生点検。
- **skill**: `/rig:rig` — 「実装したい」「レビューして」等の発話で**自動想起**もされる（エンジン本体）

> engine（`SKILL.md`）はドメイン非依存。同じ `PARSE → RESOLVE → COMPOSE → RUN` / context-minimal / acceptance-gate に、**pack を追加するだけ**で非開発ドメインや会話モード・ゴール駆動・PR レビューが乗る。`sales`（`/rig:sales`）・`talk`（`/rig:talk`）・`goal`（`/rig:goal`）・`pr-review`（`/rig:pr`）がその実証で、engine 本体は一切書き換えていない。`talk` は engine の前段（自然言語→構造化された rig 起動）だけを担う薄い層、`goal` は RUN の周回を駆動する薄いドライバ（既存の acceptance-gate＋autonomous-loop を組むだけ）、`pr-review` は dev のレビューを「対象＝既存 PR（GitHub MCP 取得）」に振り替えただけの薄い差分。talk が1発話を1フローへ橋渡しするのに対し、goal はゴール達成までループを回しきる。`magi`（`/rig:magi`）はコードの逐条レビューでなく**採否そのもの**を裁く decision pack で、3 persona（`magi/{melchior,balthasar,casper}`）＋集約 pattern（`magi-consensus`）を足すだけ＝engine 不変。正しさ（科学者）・守り（母）・価値（女）の直交3観点を多数決にかけ、「正しいだけのコードが現実には通らない」を構造化する。`roast`（`/rig:roast`）・`coin`（`/rig:coin`）は **humor pack** — いずれも engine 不変・persona＋薄い instruction を足すだけ。roast は本物の指摘を毒舌で配送（批判を読ませる）、coin は magi の対極（軽い可逆な決定を即断・重いものは magi へ誘導）。`duck`（`/rig:duck`）はラバーダック・デバッグ（アヒルが質問だけで本人に気づかせる）、`pre-mortem`（`/rig:pre-mortem`）は事前検死（「もう壊れた」前提で失敗モードを逆算＝magi の「どう壊れるか」補完）。ネタだが「中身は本物のゲート/レンズ」という rig の流儀を踏襲する。

## ブリック目録・flag 一覧（正本は SKILL.md）

ブリック（agent / persona / instruction / policy / knowledge / wiki / pattern / recipe / output-contract）と flag の**正本は [`skills/rig/SKILL.md`](./skills/rig/SKILL.md) §2（目録）・§3（flag）・§3.5（recipe スキーマ）**。README には表を複製しない（目録ドリフト防止＝`--validate` ④ の思想）。一覧は次で見る：

- **`/rig:dev --list`** — 全 tier の recipe を badge・`steps:` フィールドつきで一覧（表示仕様の正本: [`facets/instructions/list.md`](./skills/rig/facets/instructions/list.md)）
- **`/rig:catalog`**（`--list --global`） — `domain × pack × persona × wiki × recipe` の横断レジストリ地図
- **`python3 scripts/orchestrate.py plan <recipe> --json --with "<flags>" --diff-git`** — RESOLVE の確定結果（extends 解決・badge・実行 step 集合・condition 評価）の機械出力＝**RESOLVE の一次実装**（selftest が golden 検証）

**reviewer 観点**は現在11枠（`agents/` と `facets/personas/` の二重構造）：既定 3-way（security / design / test）＋選択投入（performance / observability / api-compat / migration / docs — `--persona <name>` または manifest `default_personas` で投入）＋敵対レビュー（lazy-senior / cognitive-economist）＋**所見の反証者**（finding-verifier — `--verify-findings` で review-gate に挿入し、REJECT・必須条件を証拠ベースで反証。REFUTED はゲートに通さない＝false-positive 制御の最終段）。

**実行テレメトリ**：全 RUN のサマリが `<cwd>/.rig/runs.jsonl` に自動追記される（capture と別物・承認不要の実行ログ）。`python3 scripts/orchestrate.py runs` で recipe 別集計（DONE 率・平均リトライ・エスカレーション）、`runs --personas` で**検証者別の票と剪定ヒント**（5票以上 REJECT ゼロ＝ゴム印疑い）が見られる＝reviewer をデータで剪定する。

## クイック例

```bash
# 現在の変更を計画フェーズからレビューする
/rig:dev --plan "現在の変更をレビュー"

# レビューフェーズのみを単独実行する
/rig:dev --only review

# リリースフロー・レシピで機能Xを処理する
/rig:dev --recipe release-flow "機能X"

# 重い多段 fan-out に Workflow バックエンドを使う（明示 opt-in）
/rig:dev --workflow --recipe release-flow "大規模リファクタ"

# 知識蓄積を確認ダイアログなしで実行する
/rig:dev --capture --recipe release-flow "機能Y"
```

## いつ「計算的オーケストレーション」になる？（選択基準）

rig には2つの回し方があります。**舵を LLM が握る軽い散文エンジン（既定）**と、**舵をコードが握る決定論ランナー（`--orchestrate`）**。後者は遷移・ゲート・リトライ・停止・状態保持を `scripts/orchestrate.py` が強制し、各 step を別プロセスのエージェントで自走実行できます（並列検証・judge-panel・step-DAG 並列・マルチプロバイダ）。

**ざっくりの目安**

- サッと一発・対話的に進めたい → **既定エンジン**（何もしなくていい）
- 品質を毎回同じ水準に固めたい／並列で回したい／自走させたい → **orchestrate**

**いつ orchestrate を通るか（この条件で自動的に切り替わります）**

| こういう時 | orchestrate？ | 理由 |
|---|---|---|
| 普通に `/rig:dev` を一発・対話で | ❌ 散文エンジン | 軽い・即時 |
| `/rig:orchestrate` か `--orchestrate` を付けた | ✅ | 明示 |
| recipe に `checks:`（lint/test 等の機械検証）がある | ✅ 自動 | 決定論ゲートで回す意図 |
| recipe に `needs:`（step 依存）がある | ✅ 自動 | DAG 並列で回す意図 |
| `rig.md` に `default_orchestrate: true` | ✅ 既定 | プロジェクト全体で常にカッチリ |
| 自動 ON を今回だけ切りたい | ❌ | `--no-orchestrate` で打ち消し |
| `/rig:persona` など単発生成 | ❌ | ループが無いので対象外 |

自動で切り替わった時は、実行開始の1行で理由と戻し方を通知します（例：`🧭 計算的オーケストレーションで回します（理由: recipe に needs 宣言）。戻すには --no-orchestrate`）。実行中は run-status ヘッダに `orch: on`（明示）／`orch: auto`（自動）が出ます。事前に確認したいときは `orchestrate.py plan <recipe>` が `自動 orchestrate: auto ON/off（理由）` を表示します。

```bash
# 明示してカッチリ自走（各 step を rig ハーネスで実行・3並列検証）
/rig:orchestrate --run --provider rig --max-parallel 3

# 複数モデルに作らせて勝ち筋を選ぶ（judge-panel）
python3 scripts/orchestrate.py run release-flow --generators rig,claude,codex

# 利用可能なローカル LLM を動的探索（--save で設定保存）
python3 scripts/orchestrate.py models --save

# ローカル LLM で回す（要サーバ）：生成 Claude × 検証 ollama
python3 scripts/orchestrate.py run release-flow --provider rig --verifier-provider ollama --model llama3.1
python3 scripts/orchestrate.py run release-flow --provider lmstudio --model local-model

# モデルを実機から動的に選ぶ（--model 不要）
python3 scripts/orchestrate.py run release-flow --provider ollama --auto-model

# 今回だけ素のエンジンに戻す
/rig:dev --no-orchestrate --recipe release-flow "機能Z"
```

使えるプロバイダ（`--provider` / `--verifier-provider` / `--generators`）：`rig`（推奨・各 step を rig ハーネスで起動）・`claude`・`codex`・**`ollama`・`lmstudio`（ローカル LLM・OpenAI 互換 HTTP・`--model`/`--base-url`）**・`cmd`（任意 CLI）・`mock`（テスト）。

## 横断利用（CLI として）

`scripts/orchestrate.py` は、shim を 1 回置けば **どのディレクトリからでも `rig` コマンドとして呼べる**：

```bash
# rig リポジトリ（またはプラグインインストール済みパス）で 1 回
python3 scripts/orchestrate.py install-shim          # → ~/.local/bin/rig（symlink）
# 以降はどの cwd からでも
rig models                                           # 利用可能プロバイダ探索
rig probe --provider codex                           # 疎通テスト
rig run review-only --provider rig --verifier-provider codex
```

- **`$RIG_HOME` で上書き**：別 install を使う場合 `RIG_HOME=/path/to/rig rig …`。解決順は `$RIG_HOME` → `~/.claude/plugins/data/rig-itoshun-local-plugins` → スクリプト隣接（dev）。
- **プロジェクト overlay**：`<cwd>/.rig/recipes/<name>.md` が同名 built-in を**上書き解決**。built-in は絶対パス指定で引き続き使える。
- **`checks:` の実行 cwd は呼び出し元プロジェクト**（rig リポジトリではない）＝ lint/test/build が「自分の手元のプロジェクト」に対して走る。

## ドキュメント

- `docs/testing-scenarios.md` — ディシプリン圧力シナリオ集（rationalize パターンと GREEN 応答の対比）
- `skills/rig/SKILL.md` — エンジン本体（PARSE / RESOLVE / COMPOSE / RUN の全仕様 + rationalization 表 + red flags）
