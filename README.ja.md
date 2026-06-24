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
- **コマンド**: `/rig:scenario` 🎬✍️ — `/rig:movie` の**前段**＝シナリオライターモード。動画の物語（フック→課題→転換→ペイオフ→CTA・VO 草案・各ビートの source 対応）を書き、**既存ペルソナ×知識を掛け合わせて検閲**する — `ai-smell-reviewer`（＋`ai-writing-smells` 知識）で AI 臭・空ワードを、`sns-post-reviewer` でフック強度・ブランド/誇張リスクを判定 → acceptance-gate で収束（**新規 reviewer は作らない**）。例: `/rig:scenario before/after 紹介・開発者向け・60秒`
- **コマンド**: `/rig:talk` — JARVIS 的な会話モード。話しかけると意図を汲んで適切な rig フロー(dev/sales)へ橋渡しして実行する。例: `/rig:talk 今の変更だけ軽くレビューして`
- **コマンド**: `/rig:goal` — ゴール駆動ループ。高レベルな目標を渡すと受け入れ基準に変換し「現状把握→次手→既存フローへ委譲→照合」を達成まで回す。例: `/rig:goal "ログイン不具合を回帰込みで直して review 通過まで"`
- **コマンド**: `/rig:pr` — 既存 PR レビュー。PR 番号/URL を GitHub MCP で取得し security/design/test の3観点で並列評価して structured verdict を返す。例: `/rig:pr 1234 --adversarial`
- **コマンド**: `/rig:magi` — エヴァの MAGI を模した3賢者合議モード。「やるべきか？」の決定を Melchior-1（科学者＝正しさ）/ Balthasar-2（母＝守り）/ Casper-3（女＝価値）の直交3観点に並列で諮り、**決定論的な多数決**で go/no-go を MAGI コンソールに裁定する。例: `/rig:magi この破壊的変更を今リリースしていいか`
- **コマンド**: `/rig:roast` 🌶️ — 毒舌スタンダップ芸人によるコードレビュー。指摘の中身は本物（AI 臭・可読性・過剰/不足・バグ）だが、ローストとして届けることで批判を**実際に読ませる**。笑いは配送装置で判定は素面。例: `/rig:roast`
- **コマンド**: `/rig:coin` 🪙 — magi の対極。**可逆で些末**な 50/50（N 択可）を熟考せず即断する反-bikeshed ゲート。先にトリアージし、重い/不可逆と判明したら投げず `/rig:magi` へ回す。例: `/rig:coin タブかスペースか、もう決めて`
- **コマンド**: `/rig:slot` 🎰 — 「Rigsino」。6号機風 AT パチスロ実機シミュ（通常時→CZ「PR REVIEW」→AT「SHIP RUSH」の状態機械・押し順ベル・天井・設定1〜6・純増・**永続メダル管理**）で遊ぶ息抜きゲーム。実エンジン `scripts/rigsino.py`（機械割は 50 万 G シミュで設定別 95〜115% に調整）。架空メダル・実ギャンブルではない。例: `/rig:slot spin` / `/rig:slot status`
- **コマンド**: `/rig:duck` 🦆 — ラバーダック・デバッグ。机のアヒルに問題を説明する会話モード。アヒルは**質問しかせず、コードも答えも出さない**ので、説明している本人が穴に気づく（実証済みの技法）。気づいた後の修正は `/rig:dev` 等へ委譲。例: `/rig:duck なぜか nil が返る`
- **コマンド**: `/rig:pre-mortem` ⚰️ — 事前検死（magi の闇の兄弟）。「**もう本番で壊れた**」前提で失敗モードを断定形で逆算し、各々に最小ガードレールを対で出す。prospective hindsight は「何が起きうる?」より失敗を多く見つける。例: `/rig:pre-mortem この DB 移行`
- **コマンド**: `/rig:init` — リポジトリを rig 向けに初期化。manifest(.claude/rig.md)・知識層ディレクトリ・CLAUDE.md "Compact Instructions" 節を雛形生成（圧縮で rig 状態を失わない第2経路）。書き込みは確認必須・冪等。
- **コマンド**: `/rig:persona` — 説明文から reviewer persona を生成し、product 単位(project 層・既定)か global(`--user`)に保存。`--persona <name>` で review に投入できる。例: `/rig:persona "80年代の音楽を理解しているレビュアー"`
- **コマンド**: `/rig:knowledge` — ドメイン知識を **LLM-wiki ページ**（1概念=1正準ページ・相互リンク `[[slug]]`）として生成。説明文 or `--auto`(repo 解析)から、global(既定・全プロダクト共有)/project overlay に保存。persona は事実を埋め込まず `inject: [[slug]]` で参照＝暗黙知化させない。例: `/rig:knowledge --auto`
- **コマンド**: `/rig:catalog` — 横断レジストリ(`--list --global`)。全 tier(shipped＋global＋project)を走査し `domain×pack×persona×wiki×recipe` の地図を tier つきで表示＝「誰がどこで何してるか」を取り戻す。派生・読み取り専用。`--validate --global` は tier 横断の衛生点検。
- **skill**: `/rig:rig` — 「実装したい」「レビューして」等の発話で**自動想起**もされる（エンジン本体）

> engine（`SKILL.md`）はドメイン非依存。同じ `PARSE → RESOLVE → COMPOSE → RUN` / context-minimal / acceptance-gate に、**pack を追加するだけ**で非開発ドメインや会話モード・ゴール駆動・PR レビューが乗る。`sales`（`/rig:sales`）・`talk`（`/rig:talk`）・`goal`（`/rig:goal`）・`pr-review`（`/rig:pr`）がその実証で、engine 本体は一切書き換えていない。`talk` は engine の前段（自然言語→構造化された rig 起動）だけを担う薄い層、`goal` は RUN の周回を駆動する薄いドライバ（既存の acceptance-gate＋autonomous-loop を組むだけ）、`pr-review` は dev のレビューを「対象＝既存 PR（GitHub MCP 取得）」に振り替えただけの薄い差分。talk が1発話を1フローへ橋渡しするのに対し、goal はゴール達成までループを回しきる。`magi`（`/rig:magi`）はコードの逐条レビューでなく**採否そのもの**を裁く decision pack で、3 persona（`magi/{melchior,balthasar,casper}`）＋集約 pattern（`magi-consensus`）を足すだけ＝engine 不変。正しさ（科学者）・守り（母）・価値（女）の直交3観点を多数決にかけ、「正しいだけのコードが現実には通らない」を構造化する。`roast`（`/rig:roast`）・`coin`（`/rig:coin`）・`slot`（`/rig:slot`）は **humor pack** — いずれも engine 不変・persona＋薄い instruction を足すだけ。roast は本物の指摘を毒舌で配送（批判を読ませる）、coin は magi の対極（軽い可逆な決定を即断・重いものは magi へ誘導）、slot は息抜きの dev スロット（6号機風 AT パチスロ実機シミュ・永続メダル管理・実エンジン `scripts/rigsino.py`・架空メダルの遊び）、`duck`（`/rig:duck`）はラバーダック・デバッグ（アヒルが質問だけで本人に気づかせる）、`pre-mortem`（`/rig:pre-mortem`）は事前検死（「もう壊れた」前提で失敗モードを逆算＝magi の「どう壊れるか」補完）。ネタだが「中身は本物のゲート/レンズ」という rig の流儀を踏襲する。

## ブリック目録

### agents（native 委譲先・優先）

| 名前 | パス | 役割 |
|---|---|---|
| `security-reviewer` | `agents/security-reviewer.md` | セキュリティ観点の read-only レビュー |
| `design-reviewer` | `agents/design-reviewer.md` | 設計・アーキテクチャ観点のレビュー |
| `test-reviewer` | `agents/test-reviewer.md` | テスト品質観点のレビュー |
| `lazy-senior-reviewer` | `agents/lazy-senior-reviewer.md` | 怠惰な優秀シニア視点（消せるコード/不要コメント/過剰防御） |
| `cognitive-economist-reviewer` | `agents/cognitive-economist-reviewer.md` | 思考節約視点（命名/論理の素直さ/可読性） |

### facets/personas（agent フォールバック）

| 名前 | パス |
|---|---|
| `orchestrator` | `skills/rig/facets/personas/orchestrator.md` |
| `implementer` | `skills/rig/facets/personas/implementer.md` |
| `security-reviewer` | `skills/rig/facets/personas/security-reviewer.md` |
| `design-reviewer` | `skills/rig/facets/personas/design-reviewer.md` |
| `test-reviewer` | `skills/rig/facets/personas/test-reviewer.md` |
| `debugger` | `skills/rig/facets/personas/debugger.md` |
| `lazy-senior` | `skills/rig/facets/personas/lazy-senior.md` |
| `cognitive-economist` | `skills/rig/facets/personas/cognitive-economist.md` |
| `sales/hearing-reviewer` 他4観点 | `skills/rig/facets/personas/sales/`（sales pack：ヒアリング/ニーズ/提案/クロージング/ネクストアクション） |
| `talk-assistant` | `skills/rig/facets/personas/talk-assistant.md`（talk pack：会話人格） |
| `goal-driver` | `skills/rig/facets/personas/goal-driver.md`（goal pack：収束志向のループ・ドライバ） |

### facets/instructions（薄い委譲）

| 名前 | パス |
|---|---|
| `parallel-review` | `skills/rig/facets/instructions/parallel-review.md` |
| `intake` | `skills/rig/facets/instructions/intake.md` |
| `design` | `skills/rig/facets/instructions/design.md` |
| `implement` | `skills/rig/facets/instructions/implement.md` |
| `verify` | `skills/rig/facets/instructions/verify.md` |
| `visual-verify` | `skills/rig/facets/instructions/visual-verify.md` |
| `pr` | `skills/rig/facets/instructions/pr.md` |
| `merge` | `skills/rig/facets/instructions/merge.md` |
| `adversarial-review` | `skills/rig/facets/instructions/adversarial-review.md` |
| `deal-review` | `skills/rig/facets/instructions/deal-review.md`（sales pack） |
| `talk-loop` | `skills/rig/facets/instructions/talk-loop.md`（talk pack：見極め→ルーティング→確認→委譲→継続） |
| `goal-loop` | `skills/rig/facets/instructions/goal-loop.md`（goal pack：基準化→現状把握→次手→委譲→照合→周回/停止） |
| `validate` | `skills/rig/facets/instructions/validate.md`（doctor：参照切れ・スキーマ・目録ドリフト検査） |
| `pr-review` | `skills/rig/facets/instructions/pr-review.md`（pr-review pack：PR 取得→3観点並列→verdict／任意で PR コメント） |
| `init` | `skills/rig/facets/instructions/init.md`（manifest・知識層 dir・CLAUDE.md Compact Instructions を scaffold・確認必須・冪等） |
| `persona-gen` | `skills/rig/facets/instructions/persona-gen.md`（説明文→persona facet を project/user 層に生成・確認必須・冪等・捏造禁止） |
| `knowledge-gen` | `skills/rig/facets/instructions/knowledge-gen.md`（説明文/`--auto`→wiki ページを global/project に生成・確認必須・冪等・捏造禁止） |
| `catalog` | `skills/rig/facets/instructions/catalog.md`（全 tier 走査→横断レジストリ地図・派生・読み取り専用） |

### facets/policies（末尾注入のガードレール）

| 名前 | パス |
|---|---|
| `pr-hygiene` | `skills/rig/facets/policies/pr-hygiene.md` |
| `branch-strategy` | `skills/rig/facets/policies/branch-strategy.md` |
| `ci-cost` | `skills/rig/facets/policies/ci-cost.md` |
| `pre-push-review` | `skills/rig/facets/policies/pre-push-review.md` |
| `risk-based-testing` | `skills/rig/facets/policies/risk-based-testing.md` |

### facets/output-contracts

| 名前 | パス |
|---|---|
| `review-verdict` | `skills/rig/facets/output-contracts/review-verdict.md` |
| `deal-verdict` | `skills/rig/facets/output-contracts/deal-verdict.md`（sales pack） |

### facets/knowledge

| 名前 | パス |
|---|---|
| `_layer`（構造定義） | `skills/rig/facets/knowledge/_layer.md` |
| `_wiki`（LLM-wiki 構造定義） | `skills/rig/facets/knowledge/_wiki.md`（正準ページ・`[[link]]`・`inject:`・衛生） |
| `harness-engineering` | `skills/rig/facets/knowledge/harness-engineering.md` |
| `orchestration-patterns` | `skills/rig/facets/knowledge/orchestration-patterns.md` |
| `sales-domain`（自社固有・記入用） | `skills/rig/facets/knowledge/sales-domain/`（sales pack） |

### patterns（制御フロー）

| 名前 | パス |
|---|---|
| `parallel-fanout` | `skills/rig/patterns/parallel-fanout.md` |
| `review-gate` | `skills/rig/patterns/review-gate.md` |
| `structured-report` | `skills/rig/patterns/structured-report.md` |
| `serial` | `skills/rig/patterns/serial.md` |
| `autonomous-loop` | `skills/rig/patterns/autonomous-loop.md` |
| `monitor` | `skills/rig/patterns/monitor.md` |
| `workflow-backend` | `skills/rig/patterns/workflow-backend.md` |
| `acceptance-gate` | `skills/rig/patterns/acceptance-gate.md` |

### recipes（step の束）

| 名前 | パス | 概要 |
|---|---|---|
| `review-only` | `skills/rig/recipes/review-only.md` | review step のみ実行 |
| `release-flow` | `skills/rig/recipes/release-flow.md` | 実装→検証→レビュー→PR→マージのフルフロー |
| `design-first` | `skills/rig/recipes/design-first.md` | 設計フェーズ優先フロー |
| `hotfix` | `skills/rig/recipes/hotfix.md` | 緊急修正向け軽量フロー |
| `adversarial-review` | `skills/rig/recipes/adversarial-review.md` | 敵対的レビューのみ（AIの癖排除・可読性） |
| `deal-review` | `skills/rig/recipes/deal-review.md` | 商談を5観点で並列評価→総合評価＋改善アクション（sales pack） |
| `goal-loop` | `skills/rig/recipes/goal-loop.md` | ゴールを受け入れ基準に変換し既存フローへの委譲ループで達成まで収束（goal pack。acceptance-gate＋autonomous-loop） |
| `pr-review` | `skills/rig/recipes/pr-review.md` | 既存 PR を GitHub MCP 取得→3観点並列レビュー＋(任意)敵対レビュー→structured verdict（pr-review pack） |
| `magi` | `skills/rig/recipes/magi.md` | エヴァ MAGI 模倣の3賢者 decision。Melchior(科学者=正しさ)/Balthasar(母=守り)/Casper(女=価値)に並列諮問→多数決(`magi-consensus`)で go/no-go 裁定（magi pack） |
| `roast` 🌶️ | `skills/rig/recipes/roast.md` | 毒舌ロースト・レビュー。的は adversarial と同じ（AI 臭/可読性/バグ）だが配送をユーモアに振り批判を読ませる。判定は素面（humor pack） |
| `coin` 🪙 | `skills/rig/recipes/coin.md` | 可逆で些末な決定を即断する反-bikeshed ゲート。重い/不可逆はトリアージで弾いて magi へ。magi の対極（humor pack） |
| `slot` 🎰 | `skills/rig/recipes/slot.md` | Rigsino。6号機風 AT パチスロ実機シミュ（通常時→CZ→AT・押し順・天井・設定1〜6・永続メダル）。実エンジン `scripts/rigsino.py`。架空メダル・dev フロー判断には非関与（humor pack） |
| `duck` 🦆 | `skills/rig/recipes/duck.md` | ラバーダック・デバッグ。アヒルが質問だけで本人に気づかせる会話モード。コードも答えも出さない・修正は dev へ委譲（humor pack） |
| `pre-mortem` ⚰️ | `skills/rig/recipes/pre-mortem.md` | 事前検死。「もう本番で壊れた」前提で失敗モードを逆算＋最小ガードレール（`premortem-report`）。magi の「どう壊れるか」補完（humor pack） |
| `sales-enablement` | `skills/rig/recipes/sales-enablement.md` | 開発資材（README/CHANGELOG/コード）→ 営業1枚資料＋荷電スクリプト（`sales-collateral`）。機能→ベネフィット翻訳・実在機能のみ・不明は `[要記入]`（sales pack） |
| `release-movie` 🎬 | `skills/rig/recipes/release-movie.md` | CHANGELOG → リリーストレーラーの制作台本＋再生できるアニメ HTML（`web/release-trailer.html`）。ハイプだが全ビートが実機能の裏打ち |
| `scenario` 🎬✍️ | `skills/rig/recipes/scenario.md` | シナリオライターモード（`/rig:movie` 前段）。脚本（フック→課題→転換→ペイオフ→CTA＋VO＋source 対応）を書き、既存の掛け合わせ（`ai-smell-reviewer`＋`ai-writing-smells` × `sns-post-reviewer`）で検閲→acceptance-gate 収束（新規 reviewer 不要） |

### manifests

| 名前 | パス | 概要 |
|---|---|---|
| `_template` | `skills/rig/manifests/_template.md` | manifest スキーマ全体定義 |

## flag 一覧

| flag | 意味 |
|---|---|
| `--issue <id>` | 対象 Issue を指定（intake の入力） |
| `--design` | design step を ON にする |
| `--visual` | visual 確認（スクリーンショット等）を ON |
| `--review` | review step を ON にする |
| `--tdd` | implement を TDD（red-green-refactor）で行う |
| `--autonomous` | step ゲートを省き自律実行（capture ゲートは解除されない） |
| `--plan` | COMPOSE まで実行し、合成ハーネスを提示して停止（実行しない） |
| `--only <step>` | 指定 step だけを実行 |
| `--from <step>` | 指定 step から最後まで実行 |
| `--recipe <name>` | shipped / user / project のいずれかの recipe を名前で指定 |
| `--save-recipe <name>` | 今回合成したハーネスを recipe として保存（既定: project 層） |
| `--save-recipe <name> --user` | user 層（`~/.claude/rig/recipes/<name>.md`）に保存 |
| `--workflow` | 実行バックエンドを Workflow（ultracode）に切り替える。**明示 opt-in 必須** |
| `--capture` | capture（knowledge への蓄積）の確認ダイアログを省略（提案表示と事後報告は省略しない） |
| `--list` | 利用可能なブリック・recipe・flag を一覧表示して停止 |
| `--validate` | doctor: recipe→facet 参照切れ・frontmatter スキーマ・§2 目録ドリフト・wiki 衛生を検査して停止（実行しない） |
| `--global` | `--list`/`--validate` を tier 横断(shipped＋global＋project)に拡張。`--list --global`=横断レジストリ地図(`/rig:catalog`)、`--validate --global`=横断衛生点検 |
| `--adversarial` | 敵対的レビュー step を追加（AIの癖排除・人間可読性・不要コメント除去） |
| `--cross-llm` | 他社 LLM がレビューする前提でコーディング。implement に `cross-llm-legibility`（Codex/Copilot/GPT が一発で通す慣用的・明示的・文脈非依存なコード）を注入＋ review に外部 LLM 視点の `cross-llm-reviewer` を追加 |
| `--persona <name>` | review fan-out に名前指定のカスタム reviewer persona を追加（複数可・tier 解決 project→user→shipped・`/rig:persona` と対） |

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

## ドキュメント

- `docs/testing-scenarios.md` — ディシプリン圧力シナリオ集（rationalize パターンと GREEN 応答の対比）
- `skills/rig/SKILL.md` — エンジン本体（PARSE / RESOLVE / COMPOSE / RUN の全仕様 + rationalization 表 + red flags）
