# Changelog

rig の変更履歴。バージョンは `.claude-plugin/plugin.json` に対応。
形式は [Keep a Changelog](https://keepachangelog.com/) に準拠（日付は JST）。

> リリースタグは GitHub 側で発行する（実行環境の都合でタグ push を別途行う運用）。

## [0.53.0] - 2026-06-25

### Added — 外部ランナー（`orchestrate run`）：各 step を別プロセスのエージェントで自走実行
- v0.52.0 の決定論オーケストレータを「状態機械」から「**実行器**」へ拡張。`scripts/orchestrate.py run <recipe> --provider <name>` が**各 step を別プロセスのエージェントで実行し、遷移を全自動で回す**（takt 型の外部オーケストレーション）。残っていた takt との距離＝**別プロセスでの context 隔離・真の自走・マルチプロバイダ**を埋める。
- **マルチプロバイダ抽象**：`claude`（`claude -p`）/ `codex`（`codex exec`）/ `cmd`（任意 CLI を `{prompt}` テンプレートで）/ `mock`（決定論ダミー）。生成と検証で**別プロバイダ**を指定可（`--provider claude --verifier-provider codex`＝別モデルが独立検証）。
- **プロセス隔離**：step ごとに新規プロセス＝毎回クリーンな context（Context Rot 対策の構造版）。親が肥大しない（Thin Harness）。
- **構造的な採点者≠生成者**：gated step は**別プロセスの verifier** が `VERDICT: PASS|FAIL` を返す（by=`<provider>:independent`）。`independent-verification` をプロセス境界で強制。
- **安全**：`--provider` 明示必須（既定なし・本物の再帰起動に注意）・`--max-steps` 上限・ゲート未達 K 回で `ESCALATE`・自己採点 `BLOCKED`・`run-state.json` 永続で中断/再開可。
- 検証: `orchestrate selftest` に**外部ランナーの自走テスト**（mock プロバイダ・別プロセス実行・独立検証）を追加し全 PASS。`patterns/computational-orchestration` に `run`/プロバイダ節を追記。validate 合格。

## [0.52.0] - 2026-06-25

### Added — 計算的オーケストレーション（`--orchestrate`）：制御ループの舵をコードが握る
- rig の制御ループは既定では散文で、step の遷移を握るのは LLM（SKILL.md を読んで判断）だった。`harness-taxonomy` 自身の基準「prose の依頼 ≪ コードの強制」を rig の制御ループにも当て、**遷移・ゲート判定・リトライ・停止条件・状態保持を決定論ランナーに強制させる**層を追加（nrslib/takt のような外側の決定論コントローラに相当する穴を埋める）。
- **`scripts/orchestrate.py`（決定論ランナー・stdlib＋PyYAML）**：`plan`（ステップ状態機械を算出）/`init`/`next`（START・ADVANCE・RETRY・AWAIT・BLOCKED・ESCALATE・DONE を決定論的に計算）/`check`（step の `checks:` shell を実行＝計算的センサー）/`verdict`（独立検証者の判定）/`status`/`selftest`。状態は `run-state.json` に永続＝**圧縮・再起動を跨いで同じ状態機械を再開**（run-continuity の計算版）。
- **採点者≠生成者を強制**：`verdict --by self/generator` は `BLOCKED`（自己採点バイアスをコードで拒否・`policies/independent-verification` の実行版）。**計算的センサー(checks)を一次・推論 verdict を二次**。ゲート未達が K 回で `ESCALATE`（無限ループ禁止）。
- **モデルは作業・コードは遷移**（Thin Harness, Fat Skills）。`--orchestrate` 明示時のみの opt-in＝**engine 不変**。recipe に任意 `checks:`（機械検証コマンド列）を宣言するとゲートの一次根拠になる（プロジェクト依存のため manifest/user recipe で足す）。
- 新規: `scripts/orchestrate.py`・`patterns/computational-orchestration`・`--orchestrate` フラグ・§3.5 `checks` キー・CI（`validate.yml`）に**決定論セルフテスト**を追加。validate.py 23 PASS／orchestrate selftest PASS（同入力→同遷移・最終状態一致を証明）。

## [0.51.0] - 2026-06-25

### Added — harness-audit パック（`/rig:harness`）：自分のハーネスを 2×2 で点検
- 「ハーネスエンジニアリング」の考えを取り込み。rig は推論的側（persona＝レビュアー／knowledge・instruction＝ガイド）は強い一方、**自分のプロジェクトのハーネスが健全かを点検する手段**が無かったのを補う自己監査パック。
- **2×2 フレームワーク**（**計算的/推論的 × ガイド/センサー**）でプロジェクトを棚卸し：計算的ガイド（型/scaffold/CLI）／計算的センサー（lint・型・テスト・build・CI）／推論的ガイド（CLAUDE.md・Skills・persona）／推論的センサー（AI レビュー・review-gate）。**空の象限**を可視化。
- **核心の指摘軸**：「**ある**」と「**効いている**」を区別する。テスト/lint が存在するだけで hook や acceptance-gate に繋がっていない（＝実行ループのバックプレッシャーになっていない）穴を最優先で拾う。prose 止まりのルールは未強制扱い。**計算的センサーを一次・推論的レビューを二次**（LLM は sweet-talk されうるが失敗テストは交渉できない）。直前の `independent-verification`（誰が検証するか）に「何で検証するか」を足す。
- **足すより繋ぐ・強制する・薄くする**：新ルールの追加は最後（善意のルール追加は逆効果になりうる＝Context Rot を警戒）。Thin Harness, Fat Skills。
- 新ブリック: `commands/harness.md`・`recipes/harness-audit`・`facets/personas/harness-auditor`・`facets/knowledge/harness-taxonomy`・`facets/instructions/harness-audit`・`facets/output-contracts/harness-map`・SKILL.md §2 目録。engine 不変・pack 上乗せ（test-design と同形＝persona＝判断／knowledge＝観点カタログ）。validate.py 23 PASS。

## [0.50.0] - 2026-06-25

### Added — loop engineering を取り込み：goal-loop に「採点者≠生成者」の独立検証を明文化
- 「自分で回り続けるループ」の設計（loop engineering＝harness の1つ上の層）を rig に取り込み。核は**自己採点バイアス**（エージェントは自分の出力を採点すると甘く付ける＝self-praise）。直前に de-ai-smell で自分の記事を 41/50「合格」と自己採点して外した失敗の、構造的な対策。
- **policy `independent-verification`（新）**：検証（受け入れ照合・採点・レビュー判定）は**生成者と別の担い手**が行う。「作って・自分で合格」を禁止し、自己スコアを最終根拠にしない。疑わしきは未達側に倒す。acceptance-gate / review-gate / goal-loop / de-ai-smell / scenario に適用。
- **knowledge `loop-engineering`（新）**：ループ1周回を5つの動き **discovery / handoff / verification / persistence / scheduling** に分解し、rig の既存機構（現状把握 / 委譲 / acceptance-gate / run-continuity / autonomous-loop）へ割り当て。最も壊れやすい verification＝自己採点バイアス、と暴走ガード（詰まり停止・opt-in・過剰実装しない）を明記。
- 反映: `recipes/goal-loop`（policies に `independent-verification` 追加・5つの動きを明記）・`facets/instructions/goal-loop`（⑤照合に独立検証を要求）・`facets/instructions/de-ai-smell`（自己スコアを最終判定にしない注意を追加）・SKILL.md §2 goal 行。engine 不変・pack/policy 上乗せ。validate.py 22 PASS。

## [0.49.0] - 2026-06-25

### Changed — `--plan` / `--validate` / `catalog` 可視性向上（rig-idea #112 #113 #114 #115 #117）

- **`--validate ②` size_thresholds 部分指定補完チェック（#112）**：`size_thresholds` の一部サブキーのみ指定した場合（例: `S_max: 300` のみ）、未指定サブキーを汎用既定値（S=100/M=200/L=400）で補完した**実効値**で昇順検証する。`S_max: 300` のみ指定で `S_max(300) ≥ M_max(200 既定)` が FAIL として検出される（従来はスキップされてサイレントに size-aware が壊れていた）。エラーメッセージに補完した既定値を `(既定)` と明示。
- **`--plan` Knowledge ブロック ファイル名列挙（#113）**：`### Knowledge: 注入予定ソース` ブロックの `✓ N files` 表示を拡張し、各ファイル名をインデントして1行ずつ列挙する（wiki inject の per-item 表示との非対称を解消）。tier パス（`[global]` / `[project]`）も付記。`accumulated/` が複数ファイルになってきたときに「どのファイルが注入されるか」を `--plan` 段階で確認できる。
- **`--plan` Gate ブロック max_retries 解決元マーカー（#114）**：`### Gate: 受け入れ基準` ブロックの `（max_retries: N）` 表示に解決元マーカーを追加。step 定義由来はマーカーなし / manifest `default_max_retries` 由来は `（max_retries: N ★）` / 汎用既定値（2）由来は `（max_retries: 2 [既定]）`。`★`/`[既定]` が使われた場合のみ Gate ブロック末尾に凡例行を追加（personas 列の `★` 凡例と語彙統一）。
- **`/rig:catalog` に Accumulated Knowledge セクション追加（#115）**：`--capture` で蓄積された `accumulated/` ナレッジが catalog 地図に表示されるようになる。`title（category, date）` を `category` 別にグルーピングして表示。project 層と user 層（global）を区別。0件 tier はサイレントに省略（空見出しなし）。capture→確認→整理のループが閉じる。
- **run-status ヘッダ stuck-guard カウンタ可視化（#117）**：stuck-guard カウンタが1以上になったとき、run-status ヘッダに `| stuck: N/2` フィールドを追加。0回目（通常）は省略し、ヘッダ長を増やさない。`acceptance-gate` の `(try N/K)` と対称的に「同一エラー反復の深さ」が可視化され、「次でエスカレーション直前」を事前に認識できる。

## [0.48.0] - 2026-06-25

### Added — test-design パック（`/rig:qa`）：テストケース設計の観点抜けを仕組みで塞ぐ
- AI 生成テストの定番の穴＝**正常系偏り（観点抜け）**と**新機能/移行で軸がぶれる**を、固定の観点とトラック分化で塞ぐ QA パック。rig はペルソナ／パターン合成エンジンなので「観点を固定して取りこぼさせない」がそのまま乗る。
- **7観点（各 ≥1 を強制）**：初見ユーザー / 現場ベテラン / 悪意ある操作者 / データ整合性監査役 / 移行担当者 / 回帰デグレ番人 / 仕様懐疑者。正常系（L1）を1つ押さえたら重心を L2〜L7 へ。出ない観点は「該当なし＋理由」で沈黙させない。
- **根拠と正直さ**：各ケースに **Test Basis（一次情報＝課題番号/仕様の節/コード箇所）必須**。コード未確認・仕様未確定は `※要確認（未実施）` と明記し断定しない（捏造を最も抑える一手）。要件カバレッジを **テスト可 / 保留 / 不可** に分類し、不可・保留は**仕様ギャップ（差し戻し）**として名指す。
- **トラック分化**：既定＝新機能（起点＝課題＋コード／問い＝要件を満たすか）、`--migration`＝移行（起点＝現行ヘルプ・現行挙動／問い＝現行どおり動くか＋要件カバレッジ表）。
- **やらないこと明示**：AI は「テスト設計者」であって「テスター」ではない＝テスト実行・合否判定・既存ケースの修正はしない（`--review` は指摘のみ）。**機密（実顧客名/メール/電話/本番データ）は出力前にスクリーニング**。ISO/IEC 25010 品質特性タグ＋工数（1・3・5・8）。
- 新ブリック: `commands/qa.md`・`recipes/test-design`・`facets/personas/test-designer`・`facets/knowledge/qa-test-lenses`・`facets/instructions/test-design`・`facets/output-contracts/test-cases`・SKILL.md §2 目録。engine 不変・pack 上乗せ（de-ai-smell と同形＝persona＝判断／knowledge＝観点カタログ）。validate.py 21 PASS。

## [0.46.0] - 2026-06-25

### Added — design pack（`/rig:design` 🎨）: UI/UX・a11y デザイン作成 ＋ URL 監査
- engine 不変のモード pack（scenario / pr-review と同じ流儀）。**作成モード（既定）**は説明文から **デザイン仕様書 / コンポーネント仕様 / ワイヤー / a11y 計画** を生成し、`ux-reviewer`（ユーザビリティ・Nielsen ヒューリスティック）・`a11y-reviewer`（WCAG 2.2）で並列検閲 → acceptance-gate で収束。**監査モード（URL 引数 / `--url`）**は **Playwright** で実装画面を取得（スクリーンショット / DOM / axe-core）し UI/UX・a11y を採点。
- **出力バックエンド（作成・併用可）**：既定 Markdown ＋ `--ppt`（`powerpoint-server` MCP）＋ `--claudedesign`（`claude_design` MCP・未接続時は Markdown へ graceful fallback）。flag は `--url` / `--a11y-level <A|AA|AAA>`（既定 AA）/ `--ppt` / `--claudedesign`。
- 追加ブリック：command `commands/design.md` ／ recipe `recipes/{design,design-audit}` ／ persona `facets/personas/design/{ui-ux-designer,ux-reviewer,a11y-reviewer}` ／ instruction `facets/instructions/design-{draft,vet,audit}`（`design-vet` は作成・監査で共用） ／ output-contract `facets/output-contracts/design-verdict`（WCAG 達成基準番号・レベル付き） ／ knowledge `facets/knowledge/{a11y-wcag,ui-ux-heuristics}`。SKILL.md §2/§3・README(en/ja) に登録。`scripts/validate.py` 全21 recipe PASS。

### Removed — slot pack（`/rig:slot` 🎰）を撤去
- humor pack の slot（Rigsino）を削除。`commands/slot.md` ／ `recipes/slot` ／ `facets/personas/slot-dealer` ／ `facets/instructions/slot-machine` を削除し、SKILL.md §2 pack 目録・`--list` の humor グループ・README(en/ja) から slot 参照を除去。

### Chore
- 誤って追跡されていた `node_modules/` `.agents/` `video/` を gitignore に追加し追跡解除（プラグイン本体に無関係なため）。

## [0.45.1] - 2026-06-24

### Changed — de-ai-smell 語彙ブラックリストを rig 独自の選定・置換例に書き直し
- 0.45.0 で追加した語彙ブラックリストを、外部リストの流用ではなく **rig 独自の選定・自前の置換例**に書き換え（各カテゴリは網羅一覧でなく代表だけ挙げて「同型を芋づるで疑う種」にする方針へ）。節名を `名指し語彙ブラックリスト（日本語・置換例つき）`＝Q〜U / 禁止表現リストの語彙版、として整理。
- 5観点スコアの枠組みは手法として維持。外部参照の出典行を除去（`ai-writing-smells`・SKILL.md §2）。

## [0.45.0] - 2026-06-24

### Added — de-ai-smell を「5観点スコア定量ゲート」＋「名指し語彙ブラックリスト」で強化
- rig の既存マーカー（A〜V・深層俯瞰）は形・中身に強い一方、**定量ゲート**と**名指しで置換できる語彙ブラックリスト**が薄かったのを補強。
- **5観点スコアリング（定量ゲート）**：立場 / リズム / 主体性 / 具体性 / 削減を各 1〜10 で採点、**合計 <35/50 で書き直し**・各観点 3点以下はその観点を直す。各観点は既存記号（E/J/P/N/V 等）に対応づけ。**件数 0 でもスコアが低ければ REJECT**＝「無臭だが空っぽ」を定量化。acceptance-gate の充足条件を「スコア ≥35 かつ 指摘 0/説明可能な残置のみ」に拡張（文字列で機械判定可能）。
- **名指し語彙ブラックリスト（置換例つき）**：手触り偽装語／わかった気にさせる語／壮大化した漢語／手垢の比喩／横文字メタファー→日本語／過剰カタカナ→和語／ジャーゴン→普通語／結論回避フレーズ等を、置換例つきで弾く。原意保持が上位（意味が変わるなら残す）。
- 反映: `facets/knowledge/ai-writing-smells`（ブラックリスト＋5観点スコア節）・`facets/personas/ai-smell-reviewer`（スコア必須化）・`facets/instructions/de-ai-smell`（レポートにスコア併記・収束条件に ≥35・再採点）・SKILL.md §2 目録。`scenario` 検閲も `ai-smell-reviewer`/`ai-writing-smells` 共用のため自動で恩恵。engine 不変・pack 上乗せ。

## [0.44.0] - 2026-06-24

### Changed — `/rig:movie` の主目的を「実装中のプロジェクトの動画化」に転換
- これまでの既定ソース＝CHANGELOG（出荷済みリリースのトレーラー）を、**既定＝いま実装しているプロジェクトそのもの**（コード・README・マニフェスト・主要ソース／エントリポイント＋作業ブランチの git ログ・作業ツリー diff＝「いま実装中の何か」＋**実際に動く画面**）に転換。何を作り・どう動き・どう使うのかを見せるデモ動画が主目的。
- **CHANGELOG ソースは `--release [バージョン]` の任意モードに降格**（出荷済みバージョンの告知トレーラー向け）。引数なしの `/rig:movie` はプロジェクト全体のデモ。
- ソース対応表（誇張防止）を「各ビート → **実コード/実機能**（ファイル・コマンド・挙動）」紐づけへ更新（`--release` 時のみ CHANGELOG 項目）。動いている画面ショット必須は継続。
- 反映: `commands/movie.md`（description／`--release` フラグ追加／例）・`recipes/release-movie`・`facets/instructions/release-movie`・`facets/instructions/hyperframes-video`・`facets/personas/release-director`・SKILL.md §2 目録。engine 不変・pack 上乗せのまま意味だけ拡張（brick id は据え置き）。

## [0.43.0] - 2026-06-24

### Changed — before/after デモを「開発フロー全体・コーディング中心」に作り直し
- デモが `--only review` に寄り過ぎ＝rig のごく一部しか見せていなかったのを是正。**目玉を「review verdict」から「実装→検証→レビュー→PR→merge の全工程が回り切る」**へ移し、**コーディングの screen を追加**：`/rig:dev --recipe release-flow --tdd …` → `implement`（TDD red→green）／ `verify`（acceptance-gate：build / lint 0 / tests green）／ `review`（並列 APPROVE）→ `pr #128 opened` → `merge merged ✓`。HERO は「親がやったのは、dispatch と集約だけ」。
- **広がりの beat を追加**（レビューだけじゃない）：dev 全工程 ＋ magi（やるか決める）／ goal（達成まで回す）／ sales・talk・de-ai-smell・pre-mortem。効き目も「実装も毎回同じ品質へ収束（determinism-by-gate）」「size-aware」に拡張。
- auteur 演出（人で始め/締め・無音の一拍・hero beat）は維持。`web/before-after.html`（12シーン・約63秒）／ `video/before-after/index.html`（11シーン・約58秒・GSAP 再構成）／ `STORYBOARD.md` / `SCENARIO.md` を更新。**盛らずに見せ方・順番・間で**（誇張ゼロ・全ビート source 対応：`12/12`/`#128` は実機能の例示）。両 film の JS は Node 検証、`scripts/validate.py` 全19 recipe PASS。

## [0.42.0] - 2026-06-24

### Changed — before/after デモを auteur 演出で再生成（魅せ場のある版に）
- `/rig:scenario`（`--persona auteur/deconstructionist auteur/humanist` ＋ engagement）を before/after に適用し、**「正しいが退屈」だった版を作家性演出で作り直し**：
  - **人で始める/締める（humanist）**: 冒頭を「金曜 21:43『この変更、見ておいて』」に・末尾に一息つく payoff「気づけば、ちゃんとレビューされている。」
  - **無音の一拍（deconstruction）**: 転換に間のカット「rig を通す。」だけを挿入し、before→after の滑らかさを断ち切る
  - **hero beat（engagement）**: verdict を単独で溜めて最長尺に・「…親に届くのは、判定行だけ。」を山場に
- `web/before-after.html`（11シーン・約55秒）／ `video/before-after/index.html`（9シーン・約46秒・GSAP 再構成）／ `STORYBOARD.md` / `SCENARIO.md` を確定版に更新。**盛らずに、見せ方・順番・間で**面白く・温かく（誇張ゼロ・全ビート source 対応）。両 film の JS は Node 検証、`scripts/validate.py` 全19 recipe PASS。scenario pack（面白さ軸＋auteur レンズ）の実運用デモ。

## [0.41.0] - 2026-06-24

### Added — 作家性レンズ（auteur personas・実名を避けたクリエイター・アーキタイプ）
- 動画演出に**作家性の目**を足す2ペルソナを追加（`/rig:scenario` に `--persona` で投入する任意の演出批評レンズ）。**実在の特定個人・スタジオ・作品名は使わず**、その系譜の作家性を抽象化したアーキタイプ：
  - **`facets/personas/auteur/deconstructionist`（解体派の作家）** — 本音・痛点／形式の破壊（無音・ハードカット・静止の一拍）／内面の緊張／削ぎ落としと間／技術的執着。「よく出来た説明動画」を壊して本音と緊張を差す。
  - **`facets/personas/auteur/humanist`（人間派の職人）** — 人間の中心／素直な感情／日常の中の発見／手触り・丁寧さ／善意・非シニカル。「正確だが冷たい」に温かさと誠実を差す。
- 2軸は直交（緊張 × 温かさ）。両投入で magi のように相反する目で揉める。**盛らない原則は共通**（本音・面白さ・感動のための誇張は禁止＝`ai-smell`/`source` 検閲と整合）。出力は `review-verdict`（観点 `auteur:deconstruction` / `auteur:humanist`）。
- `scenario-vet` instruction・`/rig:scenario` command・SKILL §2・README(en/ja) に作家性レンズの投入方法を追記。`scripts/validate.py` 全19 recipe PASS。

## [0.40.0] - 2026-06-24

### Added — scenario 検閲に「面白さ（engagement）」軸を追加
- これまでの検閲（AI 臭・ブランド/誇張・source）は**「正しいが退屈」を通してしまう**という欠落を是正。**`facets/personas/engagement-reviewer`** を新設し、`scenario-vet` の `parallel-fanout` に追加。動画としての**面白さ＝掴み・テンポ/緩急・意外性・感情のペイオフ・記憶に残る山場（hero beat）・完走/再生/共有したくなるか**を判定する（review-verdict・観点 `engagement`）。**面白さのための誇張は禁止**（事実のまま、見せ方・順番・間で面白くする＝ai-smell/source と整合）。
- `recipes/scenario` の vet step に `engagement-reviewer` と acceptance 基準「動画として面白い（退屈でない・最後まで観たくなる・記憶に残る山場が1つ）」を追加。`scenario-writer` persona も**面白さ（hero beat 設計）を最初から狙う**よう更新。
- 設計方針：検閲の土台は既存ブリックの掛け合わせ（`ai-smell-reviewer`＋`ai-writing-smells` × `sns-post-reviewer`）のまま、**既存に該当軸が無い「面白さ」だけ専用 reviewer を足す**（むやみに増やさず欠けた軸のみ）。SKILL §2・README(en/ja) 更新。`scripts/validate.py` 全19 recipe PASS。

## [0.39.0] - 2026-06-24

### Changed — `/rig:scenario` を before/after デモに適用（dogfooding）＋検閲で誇張を是正
- 新設の `/rig:scenario`（scenario-writer → 検閲）を **before/after 紹介動画のお題で実際に回し**、確定シナリオを `video/before-after/SCENARIO.md` に保存。
- **検閲（既存ブリックの掛け合わせ）が現行動画の誇張を実検出し是正**：`[source]` 「context 汚染 8,200 tokens」＝実測でない偽精度 → 「長い diff/ログで膨らむ（イメージ）」、`[sns-post]` 「同じバグが再発する」＝断定が強い → 「同種のミスを繰り返しやすい」、`[ai-smell]` 「劇的に変わる」＝空ワード → 削除（show, don't tell）。`web/before-after.html` / `video/before-after/index.html` / `STORYBOARD.md` を確定シナリオに合わせて修正（全ビートが実機能の裏打ち・偽精度なし）。
- 両 film の JS は Node で構文検証、`scripts/validate.py` 全19 recipe PASS。scenario pack が「書く→検閲→映像化」の実運用で機能することを実証。

## [0.38.0] - 2026-06-24

### Added — scenario pack（シナリオライターモード＋既存ブリックの掛け合わせ検閲）
- **`/rig:scenario`** — `/rig:movie` の**前段**。短尺プロダクト動画の**物語を書いて検閲する**モード。engine 不変・pack 上乗せのみ。
- **書く（`scenario-writer` persona ／ `scenario-write` instruction）** — フック→課題→転換→ペイオフ→CTA のビートシート＋VO 草案＋**各ビートの source（実機能）**を書く。最初の3秒で掴む・show don't tell・空ワード/誇張禁止・目玉は1つ。
- **検閲（`scenario-vet` instruction）— 新規 reviewer を作らず既存ペルソナ×知識を掛け合わせる**（ユーザー設計）: `parallel-fanout` で **`ai-smell-reviewer`（＋ knowledge `ai-writing-smells`）**＝AI 臭・空ワード・テンプレ臭の検出 × **`sns-post-reviewer`**＝フック強度・ブランド整合・誇張/炎上/誤認リスクの判定。＋ **source 対応チェック**（各ビートの実機能が CHANGELOG/README/コードに実在するか照合）。`acceptance-gate` で「AI 臭なし・誇張/捏造なし・フックが効く・リスク許容」へ収束（未達は `write` へ差し戻し）。`review-verdict` 共用。
- **`recipes/scenario`**（write → vet の2 step）。通ったシナリオは `/rig:movie`（`release-movie` の絵コンテ / `--hyperframes` の SCENES）の設計図になる。`release-movie` instruction にも「`/rig:scenario` で検閲済みシナリオがあれば設計図に使う」を追記。
- SKILL §2 pack 目録・README(en/ja) に scenario を追記。`scripts/validate.py` 全19 recipe PASS。

## [0.37.0] - 2026-06-24

### Added — before/after 紹介動画（rig の開発体験を具体的に見せる）
- **`web/before-after.html`**（即プレビュー・約64秒・12シーン）— 「**同じ変更を rig なし→rig あり**」で対比する紹介動画。**BEFORE**（親が 800 行 diff を読む→context 汚染・観点バラつき・品質ブレ・手戻り）→ **AFTER**（`/rig:dev --only review` で3観点並列→構造化 verdict・親に届くのは判定行だけ）。良い点（context-minimal / 並列レビュー / determinism-by-gate）と使い勝手（size-aware で軽い時は軽く・中断しても ▸rig 復帰）を紹介。**実コマンド/出力の画面ショットを3つ**収録（before の混沌・after の `--plan`・after の verdict）。
- **`video/before-after/`**（HyperFrames・約44秒）— 同内容を MP4 出力可能な HyperFrames コンポジション（GSAP seekable・`window.__timelines["rig-ba"]`・端末3シーン＝実録 mp4 差し替え枠つき）＋ `STORYBOARD.md`（ソース対応表）＋ `README.md`（render 手順）。`/rig:movie --hyperframes` 生成物の一例。
- 全ビートが**実機能の裏打ち**（ソース対応表：context-minimal §6 / `--plan` §5 / 3-way review `recipes/review-only`）。両 film の JS は Node で構文検証。`scripts/validate.py` 全 recipe PASS。

## [0.36.0] - 2026-06-24

### Added — HyperFrames 経路（HTML→決定論的 MP4・`/rig:movie --hyperframes`）
- **`facets/instructions/hyperframes-video`**（skill）を追加。[HeyGen HyperFrames](https://github.com/heygen-com/hyperframes)（OSS・Apache-2.0）で **HTML/CSS/JS を決定論的に MP4 へレンダリング**するコンポジションを生成する。認証契約を厳守：ルート `data-composition-id`/`data-width`/`data-height`、各クリップ `class="clip"`＋`data-start`/`data-duration`/`data-track-index`、アニメは **GSAP タイムライン(`paused:true`)を `window.__timelines["<id>"]` に登録**（renderer がフレームごとに seek する＝**実時計アニメ禁止**）、音声 `<audio data-volume>`、実画面は `<video class="clip" src="*.mp4">` で**実録を埋め込み可**。
- **同梱例 `video/launch-film/`** — **1.0 を見据えたコンセプト**（rig はまだ v1.0 未リリース・現行 v0.36）の HyperFrames コンポジション（`index.html`＝GSAP seekable・8シーン約46秒・seekable モック端末2つ＝`--plan`/MAGI／実録 mp4 差し替え枠つき、`STORYBOARD.md`＝ソース対応表つき台本、`README.md`＝`npx hyperframes preview`/`render` 手順）。GSAP timeline JS は Node で構文検証。
- **`/rig:movie --hyperframes`** で起動。HTML 即プレビュー（`web/release-trailer.html`）は残し、「**即見る HTML**」と「**MP4 を出す HyperFrames**」の二経路に。Remotion（React・企業ライセンス）でなく HyperFrames（素の HTML・Apache-2.0・エージェント前提）を選択＝今の HTML 資産がほぼ流用でき移植コスト最小。**harness では render しない**（コンポジションまで生成・render はユーザー環境 Node22+/FFmpeg/Chrome）。
- `release-movie` instruction・recipe・command `/rig:movie`・persona `release-director`・SKILL §2・README(en/ja) に HyperFrames 経路を追記。`scripts/validate.py` 全 recipe PASS。

## [0.35.0] - 2026-06-24

### Added — rig 1.0 ローンチ・フィルム（長尺・`release-movie` の実例）
- **`web/launch-film.html`** を追加。rig 正式リリース（1.0）想定の**長尺ローンチ・フィルム**（約76秒・15シーン）。製品全体の物語を構成：LEGO 合成思想（facet/pattern/step/recipe・PARSE→RESOLVE→COMPOSE→RUN）→ determinism-by-gate → 各 pack（dev / magi / sales / talk / goal / pr / de-ai-smell / humor）→ run-continuity → `--cross-llm`。**実際に動いている画面ショットを3つ収録**（`--plan` ドライラン出力 / MAGI 合議コンソール / `scripts/validate.py` の PASS 18/0）＝ release-movie の「screen ショット必須」を自ら実践。経過/総尺タイマー・15 シーンのチャプタードット・長尺向け BGM。`release-trailer.html` の player（`type:"screen"` 対応）を踏襲し、`/rig:movie` の**長尺テンプレ**としても流用可。全ビートが実機能の裏打ち（誇張・捏造なし）。

## [0.34.0] - 2026-06-24

### Fixed — Issue #71（`extends` 循環参照を `--validate` が検出）
- **#71** `--validate` に **`extends` 循環参照（A→B→A）チェック**を追加。循環があると RESOLVE がサイレントに無限ループ（ハング）するため、実行前に **FAIL** で止める。#42 の多段（深さ＝孫継承 WARN）チェックは各 recipe を単独で見るため循環を検出できず、**DFS による独立したサイクル検出**が必要。`scripts/validate.py` に `check_extends_cycles`（shipped tier グラフを DFS・1 サイクル 1 回・経路つき `FAIL recipe:circular-extends — circular chain: A → B → A`・自己参照 `A→A` も検出）を実装し、2段/3段/自己ループ/非循環でテスト確認。`facets/instructions/validate.md` ① に同ルールを #42 と並記。

## [0.33.0] - 2026-06-24

### Added — `--cross-llm`（他社 LLM レビュー前提でコーディングする設定）
- **`--cross-llm` フラグ**を追加。書く側・見る側の両方に作用する：①implement step に **`facets/policies/cross-llm-legibility`** を注入（Codex/Copilot/GPT など他社・他系統の LLM がレビューする前提で、慣用的・明示的・**文脈非依存**なコードを書く規律＝「自分にしか分からないコード」を書かない。外部 LLM に通る＝人間にも通る）。②review fan-out に **`facets/personas/cross-llm-reviewer`** を追加（プロジェクト文脈を持たない外部 LLM になりきり、「内輪にしか分からない」「非標準イディオム」「暗黙の前提依存」を指摘）。`de-ai-smell`/`lazy-senior` と整合（素直さと簡潔さは両立・過剰コメントは逆に減点）。implement/review が無い recipe では該当側のみ作用。`--save-recipe` 併用時、追加 persona は `personas[]` に保存（#57 経路）。SKILL §2 目録・§3 flag・README(en/ja)・`/rig:dev` に追記。

### Changed — Issue #70（`--save-recipe` の `--no-default-personas` 保存）
- **#70** recipe スキーマ（§3.5）に **`no_default_personas` キー**（boolean）を追加。`--no-default-personas --save-recipe` で `no_default_personas: true` を保存し、再利用時に `--no-default-personas` 省略でも manifest `default_personas` の自動投入を抑止（意図的に外した reviewer が静かに復活しない）。`--plan` ヘッダに `| no-default-personas: on`、personas 列は `★`（manifest 由来）を除外、`--list` に `· no-default-personas` バッジ。`--validate ③` / `scripts/validate.py` に boolean 型チェック。`--autonomous`/`--workflow`/`--tdd`/`--persona` と同じ save-recipe 対称性の最後のピース。

## [0.32.0] - 2026-06-24

### Changed — release-movie に「実際に動いている画面」を必須化

- **トレーラーに実画面ショット（実録 or モック）を最低 1 つ必須**にした。文字・ロゴ・テロップだけのトレーラーを禁止し、目玉機能は「語る」より「動かして見せる」を規定（persona `release-director` / instruction・recipe `release-movie` のガード＋受け入れ条件、`/rig:movie` command、SKILL §2 に明記）。実録が無ければモックで代替するが、**実機能の実出力に揃える**（捏造画面を作らない）。
- **`web/release-trailer.html` のプレイヤーに `type:"screen"` シーンを追加**。ターミナル/UI が動く様子を再現（`lines: [{cmd}, {out}]` でコマンドがタイプされ出力が流れる・タイプ音つき・RAF 経過時間で進行）。`SCENES` スキーマに `screen` 型を追加（埋めるのはデータだけ・プレイヤーは固定）。同梱デモを v0.32.0 に更新し、実コマンド（`/rig:movie` / `python3 scripts/validate.py` の実出力）の画面ショットを収録。
- 台本の正準フォーマット（シーン表）に `screen` ショット行と「画面収録: <コマンド>」指定を追加。Node で player JS を構文検証。`scripts/validate.py` 全18 recipe PASS。

## [0.31.0] - 2026-06-24

### Added — sales 強化（開発資材→営業資材の生成）＋ release-movie pack

- **sales-enablement（`/rig:sales --material` / `--script`）** — `deal-review`（商談の事後レビュー）と対をなす「**売る前の資材生成**」。開発資材（README/CHANGELOG/コード/リリース/`plugin.json`）を読み、**機能→ベネフィット翻訳**で **営業1枚資料**（課題→解決→差別化→導入効果→ICP→料金枠→CTA）と**荷電スクリプト**（15秒オープニング→ヒアリング→価値提示→反論処理→next action）を生成。**実在機能のみ・誇張禁止・不明は `[要記入]` プレースホルダ**。`sales-domain` 知識があれば ICP・価格・差別化に反映。persona `sales/{material-writer,cold-caller}` ／ instruction `{sales-material,call-script}` ／ output-contract `sales-collateral` ／ recipe `sales-enablement`。`/rig:sales` に `--material`/`--script`/`--from <path>` を追加（既定の商談レビューは不変）。
- **release-movie pack（`/rig:movie` 🎬）** — CHANGELOG/リリースノートから短い**リリーストレーラー**を作る。**両方フル**で納品：①**制作台本**（ログライン／シーン表＝尺・映像・テロップ・VO・BGM/SE／CTA／**ソース対応表**＝各ビートが CHANGELOG のどの項目か＝誇張防止）、②**再生できるアニメ HTML トレーラー** `web/release-trailer.html`（タイトル→機能リビール→クライマックス→CTA、再生/停止＝Space・前後＝←→・リプレイ・任意 WebAudio BGM。`SCENES` データを差し替えるだけで任意リリースに対応・プレイヤーは固定）。**ハイプだが全ビートを実機能に紐づけ**（de-ai-smell の精神）・空ワード禁止。harness は実動画を非生成（台本を編集ツールへ渡す前提）。persona `release-director` ／ instruction `release-movie` ／ recipe `release-movie` ／ command `/rig:movie`。
- SKILL §2（sales 行拡張・release-movie 行追加）・README(en/ja) を更新。`scripts/validate.py` 全18 recipe PASS。trailer/HTML の JS は Node で構文検証。

## [0.30.0] - 2026-06-24

### Added — humor packs 2 種（笑える皮 × 本物の道具）

magi / roast / coin と同じ「ネタだが中身は本物のゲート/レンズ」路線で 2 pack 追加。いずれも engine（`SKILL.md`）不変・persona＋薄い instruction（＋recipe）を上乗せするだけ。`scripts/validate.py` 全 recipe PASS。

- **duck pack（`/rig:duck` 🦆）** — ラバーダック・デバッグ。机のアヒルに問題を説明する会話モード。アヒル（`rubber-duck`）は**質問だけを返し、コードも答えも出さない**ので、説明している本人が穴に気づく（実証済みの技法）。一度に一〜二問・答えは問いに変換・気づいたら引く。修正の実装は `/rig:dev` 等へ委譲（duck は気づきまで・context-minimal）。`talk` 同様の会話モード（地の会話に run-status ヘッダを出さない例外）。persona `rubber-duck` ／ instruction `duck-debug` ／ recipe `duck` ／ command `/rig:duck`。
- **pre-mortem pack（`/rig:pre-mortem` ⚰️）** — 事前検死（magi の闇の兄弟）。マージ/リリース前に「**もう本番で壊れた**」前提で失敗モードを断定形で逆算（prospective hindsight＝「何が起きうる?」より発見率が高い実証手法）。技術・運用・データ/セキュリティ・波及の各軸で検死し、**各失敗モードに最小ガードレールを対で**出す（恐怖の羅列にしない）。`premortem-report`（総合リスク＋可能性×影響ランク＋最も安く効く1手）で構造化。magi（やるか＝go/no-go）の補完で「**どう壊れるか**」を担当し、Balthasar（守り）の判断材料／可決後の最終保険として組む。persona `pre-mortem-analyst` ／ instruction `pre-mortem` ／ output-contract `premortem-report` ／ recipe `pre-mortem` ／ command `/rig:pre-mortem`。
- SKILL §2 pack 目録・README(en/ja) に duck / pre-mortem を追記。

> 注: ブラウザ版スロット（`web/rigsino.html`）は見た目（A タイプ）と中身（AT 機）の不一致のため未リリース（ブランチ `rigsino-web-wip` に保留）。

## [0.29.0] - 2026-06-24

### Changed — slot pack を「実機相当」に作り直し（6号機風 AT パチスロ＋永続メダル管理）

ユーザー要望「実機に近い機能＋手持ちコイン管理」を受け、slot を Vegas 型3リールから **6号機風 AT/ART パチスロ実機シミュ**へ刷新。

- **実エンジン `scripts/rigsino.py`**（標準ライブラリのみ・Native-first で instruction が委譲）。台のルール本体（リール重み・小役・状態遷移・配当・機械割）を保持。
- **永続メダル管理**：手持ちメダル＋台の状態（mode/AT残G/天井カウンタ/設定）＋生涯戦績（実測機械割・AT 初当たり・最高 AT）を `~/.claude/rig/rigsino/wallet.json` に永続。セッション・プロジェクトをまたいで持ち越す。
- **通常時 → CZ「PR REVIEW」→ AT「SHIP RUSH」🚀 の状態機械**。押し順ベル（正解+9/こぼし+1・`--order L|C|R`・AT 中はナビ自動）、🔄リプレイ、レア役（☕弱/🐛チャンス目/🔥強/💎確定）、天井800G 救済、上乗せ、セット継続、DEPLOY 告知ランプ。
- **設定1〜6**（看破要素）。**機械割を 50 万 G シミュレーションで実機相当に調整**：設定1≈95% / 設定6≈115%、AT 初当たり 1/337→1/233、AT 占有 17→36%、単調増加を確認。
- 操作：`spin [--order]` / `auto [N]` / `status` / `reset`（台移動・メダル持ち越し）/ `cashin <N>` / `payouts`。
- ガード：公平な抽選（イカサマなし）・深追いを煽らない・架空メダル・dev フロー判断には非関与（軽い決定は coin、重い決定は magi）。
- `slot-dealer` persona・`slot-machine` instruction・`recipes/slot`・`commands/slot.md`・SKILL §2・README(en/ja) を AT 機仕様に更新。`scripts/validate.py` 全14 recipe PASS。

## [0.28.0] - 2026-06-24

### Added — humor packs（ネタだが中身は本物のゲート/レンズ・engine 不変）

3つの「ユーモア機能」を追加。いずれも magi/talk/goal と同じく engine（`SKILL.md`）を書き換えず persona＋薄い instruction（＋recipe）を上乗せするだけ。`scripts/validate.py` 全 14 recipe PASS。

- **roast pack（`/rig:roast` 🌶️）** — 毒舌スタンダップ芸人のロースト・レビュー。的は `adversarial-review` と同じ（AI 臭・可読性・過剰/不足・本物のバグ）だが**配送をユーモアに振る**。「ネタだから」の枠で批判のエゴ防御を下げ、指摘を実際に読ませる配送装置 — ただし判定・根拠・必須条件は**素面**（`review-verdict`/`review-gate` 共用）。的はコードであって人ではない／笑わせるために重大指摘を落とさない、をガードに明記。persona `roast-reviewer` ／ instruction `roast` ／ recipe `roast` ／ command `/rig:roast`。
- **coin pack（`/rig:coin` 🪙）** — magi の対極。**可逆で些末な 50/50（N 択可）を熟考させず即断**する反-bikeshed ゲート。先にトリアージ（可逆性・被害半径・実害）し、重い/不可逆と判明したら投げずに `/rig:magi` へ誘導する（コインで重大決定を下さないのが最大のガード）。「過剰熟考も過小熟考も実害 — 労力を決定の重さに釣り合わせる」を coin↔magi の対で構造化。persona `coin-flipper` ／ instruction `coin-flip` ／ recipe `coin` ／ command `/rig:coin`。
- **slot pack（`/rig:slot` 🎰）** — 「Rigsino」。dev テーマ（🚀ship/🐛bug/🔥prod/🟢green/💎release/🦆duck-WILD/☕coffee）の3リール・スロットで遊ぶ息抜きゲーム。リール重み・配当表・進行ループを instruction に明記して**公平性を担保**（イカサマ croupier にしない・深追いを煽らない・架空クレジット）。実 dev フローの採否には非関与。persona `slot-dealer` ／ instruction `slot-machine` ／ recipe `slot` ／ command `/rig:slot`。
- SKILL §2 pack 目録・README（en/ja）に roast/coin/slot を追記。

## [0.27.0] - 2026-06-24

自動生成 Issue #52・#53・#55・#56・#57（`rig-idea`）を**全5件採用して解決**。`--save-recipe` の実行意図保存を完成させ、`--list` の情報表示を拡充。すべて spec のみ（RUN ロジック・recipe ファイル不変）。

### Added
- **#52** recipe スキーマ（§3.5）に **`backend` キー**を追加（`manual`/`workflow`）。`--workflow --save-recipe` で `backend: workflow` を保存。再利用時に `--workflow` 省略でも Workflow バックエンドが発動。manifest に **`default_backend`** キーを追加（プロジェクト全体の実行バックエンド既定）。`--validate ③` / `scripts/validate.py` に型チェック（`manual|workflow` 以外 FAIL）を追加。
- **#56** recipe スキーマ（§3.5）に **`tdd` キー**を追加（boolean）。`--tdd --save-recipe` で `tdd: true` を保存。再利用時に `--tdd` 省略でも TDD モードが発動（§4.3 `--tdd` の特例と等価処理）。`--plan` ヘッダに `| tdd: on` を追加（active 時のみ）。`--validate ③` / `scripts/validate.py` に型チェック（boolean 以外 FAIL）を追加。
- **#57** `--save-recipe` に **`--persona` 指定分の保存**を追加（§4.3.2）。reviewer fan-out step の `personas[]` に `--persona <name>` を追加保存（dedup）。再利用時に `--persona` 省略で同じ reviewer 集合が再現。「この run で足す → `--persona`」「このフローでは常に使う → recipe `personas[]` に保存」「この製品では常に使う → manifest `default_personas`」の3段粒度が揃う。

### Changed
- **#53** `--list` の recipe エントリに **`extends: <親名> [tier]`** を付記（`extends` 有りのみ。親未解決は `[WARN: 親未解決]`）。`--list --global` も同様。`--plan` の `extends:` 表示（#17）との対称性を達成。
- **#55** `--list` の recipe エントリに manifest `default_recipe` 一致分の **`★ default` マーカー**を付記。未解決なら `★ default (WARN: 未解決)`。manifest なし・`default_recipe: "interactive"` はマーカーなし（後方互換）。

## [0.26.0] - 2026-06-24

自動生成 Issue #40・#42〜#47・#50（`rig-idea`）を**全8件採用して解決**。`--skip` フラグ追加・spec の非対称解消・capture 可視化強化。すべて spec のみ（RUN ロジック・recipe ファイル不変）。

### Added
- **#40** `--skip <step>` フラグを追加（§3 flag 一覧・§4.3 flag override・§4.3.1 step スライス）。特定 step を点指定で除外してフローを継続（複数可）。size-aware 既定・`--design`/`--review` フラグより後に適用（明示スキップが最終的に勝つ）。`--save-recipe` には影響しない（実行時フィルタ＝snapshot 意味論）。
- **#50** `--plan` ヘッダに `skip: <step-id(s)>` フィールドを追加（§5 --plan フォーマット）。`--skip` 指定時のみ表示（`slice:` の前に配置・複数は `, ` 区切り）。`slice:` との対称性を達成。

### Changed
- **#42** `--validate` ① に **`extends` 多段継承（孫継承）チェック**を追加（`validate.md`）。親 recipe の frontmatter に `extends:` が存在する場合 **WARN**（RUN 時の §4.2.2 と同 severity）。`--validate --global` 時は全 tier を対象。
- **#43** `--validate --global` に **⑥ ai-quirks 二相ペア整合チェック**を追加（`validate.md`）。`~/.claude/rig/knowledge/ai-quirks/` を走査し、記述形（`*-descriptive.md`）・規範形（`*-policy.md`）の片方が欠けているペアを **WARN**。COMPOSE の二相注入（§5）が片方しか効かない状態を run 前に検出。
- **#44** `--list` の各 recipe エントリに `[N step(s) · interactive|autonomous]` を表示（§3 --list フォーマットサンプル）。recipe 選択前に step 数とゲートモードを把握できる。`/rig:catalog`（`--list --global`）の recipe エントリ行にも同様に追加（`catalog.md`）。
- **#45** `capture` 提案（§7.4）に書き込み先ファイルの実在確認を追加。既存ファイルへの書き込みは `（既存・上書き <YYYY-MM-DD>）` と冒頭 1〜2 行を表示。`--capture` フラグ時（ダイアログ省略）も表示は省略しない（承認ゲートの情報量を保証）。
- **#46** acceptance-gate K 超エスカレーション後にも **capture 提案（§7.1 `stuck-twice`）を自動提示**する旨を §6 に明記。stuck-guard の「エスカレーションが発生するたびに提示する」が acceptance-gate K 超を含むことを明示。
- **#47** `--save-recipe` の **`description` 自動生成規則**を §4.3.2 に追加。`"<ベース recipe 名> のカスタマイズ（<有効フラグ列挙>）"` を自動生成（空文字列にならない＝recipe スキーマ §3.5 の必須フィールドを保証）。`--plan --save-recipe` のヘッダに生成予定 description を付記。

## [0.25.0] - 2026-06-23

### Fixed
- **#41** `--validate` チェック ① に **`extends` 子 step ID 突き合わせ**を追加。`extends: <parent>` recipe で子の `steps[].id` が親に存在しない場合に **WARN** を出す。意図的な新規 step 追加（§4.2.2「子のみ step は末尾追加」）と区別できないため FAIL でなく WARN。`--validate --global` 時は全 tier の `extends` recipe を対象に実施。

## [0.24.0] - 2026-06-23

### Added — magi pack（エヴァ MAGI 模倣の3賢者 decision モード）
- **`/rig:magi`** — 「やるべきか／この案で行くか」を裁定する decision モード。コードの逐条レビュー（security/design/test）でなく**採否そのもの**を、直交した3観点で多数決にかける。engine は不変、pack を上乗せするだけ（§8 Native-first の継続実証）。
- **3 号機 persona**（`facets/personas/magi/{melchior,balthasar,casper}`）— 赤木ナオコ博士の3人格を移植：**Melchior-1（科学者＝正しさ・整合・実証）** / **Balthasar-2（母＝被害半径・可逆性・安定・将来負担）** / **Casper-3（女＝価値・問題の同定・単純さ・直感）**。3軸は直交し、**正しくても危険／無価値なら否決されうる**（「正しいだけのコードは現実には通らない」を構造化）。
- **`patterns/magi-consensus`** — 多数決の合議ゲート。`review-gate`（REJECT 1 件で保留）と違い majority-vote で裁く。判定行を先頭に置いた正準出力（MAGI コンソール）＋決定論的判決表（否決2+/可決2:1/条件付/全会一致/審議継続）。determinism-by-gate に準拠。
- **`facets/output-contracts/magi-verdict`** — 各号機の票フォーマット（`可決|否決|条件付可決`＋号機行＋核心1行＋評価3点）。機械抽出可能。
- **`facets/instructions/magi-deliberation`** — 議題確定→3号機並列諮問→`magi-consensus` 集計の薄い routing。
- **`recipes/magi`** — 上記を束ねた decision recipe。否決・審議継続では先へ進めない。`--autonomous` でも判決そのものは尊重される（合議ゲートは品質ゲート同様に解除されない）。
- SKILL §2 pack 目録・README（en/ja）に magi を追記。

## [0.23.0] - 2026-06-23

自動生成 Issue #31–#37（`rig-idea`）を**全7件採用して解決**。save-recipe 意味論の完成・可視化・hotfix の品質ゲート補完。#31 以外は spec のみ。

### Added
- **#31** `hotfix` recipe の `verify` step に `acceptance-gate`（`build`/`lint`・`max_retries: 1`）を追加。緊急対応でもビルドが通ることを機械的に保証（determinism-by-gate）。`release-flow` より軽い基準（テスト green 非必須）で速度を維持。
- **#32** run-status ヘッダの `gate: pending` に acceptance-gate 試行位置 `(try N/K)` を付与（§6①）。retry 1 回目から表示・初回 0 回目は素の `pending`。K 超エスカレーション直前まで残り試行が可視化される。
- **#35** `--plan --save-recipe` のヘッダに `save-recipe: <name> → <フルパス> [tier]` 行（§5）。`[overwrite]`/`[WARN: shadow]` で書き込み副作用を事前確認できる。

### Changed
- **#33** `--save-recipe` が `--autonomous` 由来の `autonomy` 値を frontmatter に保存（§4.3.2）。再利用時に step ゲートが意図せず復活しない。
- **#34** `--save-recipe` の保存ファイルに `extends` を含めない＝完全展開 steps を保存（snapshot 意味論・§4.3.2）。親 recipe 変更の静かな波及を防ぐ。
- **#36** stuck-guard エスカレーション後に a)「別アプローチ」を選んだら stuck カウンタを 0 にリセット（§6）。「2 回」が a 選択をまたいで累算しない。capture 提案はエスカレーションごとに提示。
- **#37** `--from`/`--only` スライスは保存 step リストに影響しない（実行時フィルタであり recipe 定義の一部でない・§4.3.2）。保存 recipe はスライス前の全工程を保持。

## [0.22.0] - 2026-06-23

### Changed
- **#28** stuck-guard と acceptance-gate K 超のエスカレーションを**別フォーマット**に分離（§6）。`## rig stuck-guard:`（同一エラー2回）と `## rig acceptance-gate: K 超エスカレーション`（K 回未達・`試行: K/max_retries` 表示・選択肢に「max_retries 増/基準見直し」追加）でヘッダから発動カウンタが判別可能に。`同一エラー繰り返し:` フィールドの意味誤用を排除。#12/#21 の補完。

## [0.21.0] - 2026-06-23

自動生成 Issue #23–#27（`rig-idea`）を**全5件採用して解決**。tier 可視化と出力フォーマット標準化の続き。すべて spec のみ。

### Added — `--plan`（dry-run）の tier 可視化
- **#24** personas 列に解決元 `[tier]`（project/user/shipped/agent・未解決は `[WARN: 未解決]`）を付与。`--plan` だけで「実行したら `--validate` が落ちる」を予見できる。
- **#25** ヘッダの recipe 名に解決元 `[tier]` を付与（project が shipped を shadow していても見える）。`extends` 親 recipe にも `[tier]`。`--list` と同じ tier 語彙で統一。

### Added — 出力フォーマット標準化
- **#26** `MEMORY.md` 1行ポインタの正準フォーマット（`- [category] filename — summary (date)`）を §7.2 に定義。capture を重ねても書式が揺れず knowledge インデックスとして機能。#20（事後レポート）とは別物（報告 vs 書き込みファイル）。
- **#27** `de-ai-smell` 検出フェーズの正準レポート（マーカーID/説明/検出数/代表箇所、日英混在表）。検出ゼロは固定文言『検出なし（0 マーカー）』にし、**acceptance-gate が文字列で機械判定**できるようにした。

### Fixed — scaffold 漏れ
- **#23** `/rig:init` が `<repo>/.claude/rig/recipes/` と `personas/` を scaffold するように。`--save-recipe` / project `/rig:persona` の保存先が初回から存在し「保存→一覧→再利用の輪」が繋がる。

## [0.20.0] - 2026-06-22

自動生成 Issue #10–#22（`rig-idea`）を**全13件採用して一括解決**。#1–#9 で始めた doctor / dry-run / 出力フォーマット標準化の完成バッチ。すべて spec のみ。

### Fixed（偽陽性バグ＝doctor の信頼を直接損なうもの）
- **#13** `--validate ①` の persona 参照を COMPOSE と同じ tier 解決（project→user→shipped→agent）に。`/rig:persona` 製カスタム persona の**偽 FAIL** を解消。
- **#16** `--validate ②` が `default_recipe: "interactive"`（予約語・テンプレ既定値）を**偽 FAIL** していたのを除外して PASS に。

### Added — `--validate`（doctor）拡張
- **#11** manifest 値キー検証：`size_thresholds`（正整数・昇順 `S_max<M_max<L_max`）、`default_max_retries`（≥1）を run 前に FAIL 検出。
- **#14** manifest パスキー検証：`knowledge.context_file`/`adr_dir`/`design_docs[]` の実在を点検（不在は WARN＝ドメイン知識のサイレント無効化を検出）。

### Added — `--plan`（dry-run）可視化
- **#10** condition 列をフラグ成分で先行評価（`[✓ 実行]` / `[TBD: size 確定待ち]`）。
- **#17** `extends` 継承の出所表示（ヘッダ `extends:` ＋ overridden / inherited サマリ）。
- **#19** `### Knowledge: 注入予定ソース` ブロック（4 tier ＋ manifest knowledge の実在）。

### Added — 出力フォーマット標準化・仕様明確化
- **#12** stuck-guard エスカレーションの正準フォーマット（step/gate/回数/要約/選択肢 a/b/c）。K 超エスカレーションも同形式＋capture(`stuck-twice`)自動提示。
- **#20** capture 事後レポート §7.5（書き込み済/スキップ・実ファイルパス・MEMORY.md ポインタ成否）。
- **#15** `--save-recipe` の lower-tier shadow 警告（保存先より下位に同名 recipe があれば WARN）。
- **#18** `--tdd` の伝播を定義（COMPOSE で implement subagent に「risk-based 評価をスキップし TDD 強制」を注入）。
- **#21** `--autonomous` が外すのは step ゲートのみで **acceptance-gate 品質ループは維持**を明記（§4.5 / §3.5 / §9.1）。
- **#22** `autonomous-loop` の `<<autonomous-loop-dynamic>>` 正準構造（run-status / 受け入れ契約 / 直近 gap / 次手 / 周回カウント）。圧縮跨ぎの再開を安定化。

## [0.19.0] - 2026-06-22

### Changed
- **de-ai-smell に「禁止表現リスト」「検出テスト」「意外性(V)」を追加**（フィルター強化）— k16shikano「日本語技術文書の文章規範」SKILL.md（gist）を参考に、**名指しで弾ける具体ブラックリスト**を取り込んだ。
  - **禁止表現リスト**（日本語）：予告・総括「重要なのは〜」「本章では〜を扱う」「まとめると」／「正面から扱う」系／空虚な形容「不可欠/核心的/多角的/包括的」／空虚な動詞「掘り下げる/言語化する/触れる」／接続の型「〜において/〜の観点から/さらに・また・加えての連打」／空の称賛「非常に/極めて」。
  - **構成・演出ルール**：見出しの「種別──主題」二要素詰め込み禁止／地の文の em ダッシュ「——」禁止／太字は一節1〜2箇所／決め台詞・対句・修辞疑問の多用禁止／未確認を確認済みのように書かない。
  - **マーカー V 意外性の欠如**（「転」が無い・全部うなずける）を追加。**3検出テスト**（削除＝段落1つ消してスムーズ／圧縮＝1/5に縮む／1行言い換え＝要約できる）を実地ヒューリスティックに。
  - **N に「具体＝捏造リスク」追補**：具体的になるほど嘘が増える→在る具体（固有名・数字・引用）は裏取りし、未確認を確認済みのように書かない。
  - 影響: `ai-writing-smells`/`ai-smell-reviewer`/`de-ai-smell`/`recipes/de-ai-smell`。検出順は 深層→言語(音読＋ブラックリスト)→表層。
  - 自己適用メモ: rig 自身の SKILL.md が使う「種別──主題」見出しや地の文「——」も本リストに抵触する（今後の整流対象）。

## [0.18.0] - 2026-06-22

### Changed
- **de-ai-smell に言語固有マーカー（日本語）Q〜U を追加**（フィルター強化・第3層）— 深層 J〜P（文章の「形」）は言語非依存だが、**語彙と言い回しの癖は言語ごとに違う**。日本語 AI 文の「翻訳調・日本人が日常で使わない語」を検出できるようにした（「日本語は上手いのに人が書いた感じがしない」の主因）。
  - **新マーカー**：Q 翻訳調構文（「〜することができる」/無生物主語）／R 不自然な漢語・カタカナ密度（「挙動」「収束」「検証器」等）／S 辞書語・書き言葉すぎる言い回し／T 過剰な論理接続（なので/つまり の毎文）／U 主語・指示語の明示過多。
  - **検出は音読**：口に出して自分が喋らない語・語順を洗う。検出順を**深層→言語→表層**に。`ai-smell-reviewer` に音読パス、`de-ai-smell` instruction に言語パスを追加。
  - **書き直しは口語へ**（「挙動」→「動き」等）。ただし原意保持が上位で、専門語を砕いて意味を壊さない。英語で書くときは Q〜U を切り、英語固有の癖に切り替える（言語スイッチ）。
  - 影響: `ai-writing-smells`/`ai-smell-reviewer`/`de-ai-smell`/`recipes/de-ai-smell`（受け入れ基準を A〜U に拡張）。

## [0.17.0] - 2026-06-22

### Changed
- **de-ai-smell に深層マーカー J〜P を追加**（フィルター強化）— 表層（語・句の A〜I）を消しても残る**文書レベルの AI 臭**を検出できるようにした。表面だけ削った文が「無臭だが空っぽ」になる問題への対処。
  - **新マーカー**：J 構造の同形反復（全節が見出し→主張→例の同型・同尺）／K 強調の乱用（地の文の系統的太字）／L 演じた砕けさ（くだけ語尾が等間隔＝“人間味”すら機械的）／M きれいすぎる解決・箴言オチ／N **具体の不在（“about”病・最重要）**＝実コード/出力/固有名/数字/日付/バグ名に一度も着地しない／O 教科書配列／P 人物の不在（署名を消すと誰か分からない）。各マーカーに**検出ヒューリスティック**を明記。
  - **検出は深層→表層の順**。`ai-smell-reviewer` に**文書俯瞰パス**を追加（深層は1文ずつ見ても見えない・表層が全 PASS でも深層で REJECT し得る）。
  - **N は捏造で埋めない**：実例が無ければ磨かず `[ここに実例：…]` を**書き手に要求して停止**（中身は生の具体でしか埋まらない）。書き直しで**新たな鋳型を作らない**（トーンの全節均等適用＝L を生む、を禁止）。
  - 影響: `ai-writing-smells`（カタログ）/`ai-smell-reviewer`（俯瞰パス）/`de-ai-smell`（2パス検出）/`recipes/de-ai-smell`（受け入れ基準を A〜P に拡張）。`sns-post-reviewer` はカタログ注入で自動的に深層対応。

## [0.16.0] - 2026-06-22

### Added
- **`sns-x` パック — X(Twitter) 半自動ポスト運用ハーネス** — 個人クリエイター（歌ってみた等）の宣伝運用向け。`/rig:dev --recipe sns-x-post "<トリガー>"`。
  - **knowledge `sns-x-conventions`**（事実）— X の型と不変条件（1行目で掴む / リンクは reach 低下 / ハッシュタグ1〜2 / 歌ってみた宣伝のテンプレ / 権利・比較・スパムの事故表現 / **定型↔要判断の線引き**）。
  - **persona `sns-post-reviewer`**（判断）— 掴み・ブランド適合・AI 臭・リスク・導線を判定し、**定型/要判断に分類**（半自動の核）。声は上書きしない。
  - **instruction `sns-post` / recipe `sns-x-post`** — 声 persona で起案 → `de-ai-smell` → レビュー＆分類 → `acceptance-gate` で収束。**定型は承認キュー・要判断は停止して承認**。実投稿は別アダプタ（自動投稿の ToS/BAN リスクは段階導入）。
  - **声＝運用者の資産**：各垢の声は `/rig:persona` で作り `default_personas`/`--persona` で投入（声＝あなた製 / 判断＝reviewer / 事実＝X 型 wiki の分離）。5垢へはコントロールプレーン（台帳/GM）で横断管理。

## [0.15.0] - 2026-06-22

### Added
- **`de-ai-smell` パック — 散文の「AI 臭さ」除去ハーネス** — 記事 / README / コミット文 / PR 説明 / SNS 投稿などの機械くささを落とす recipe。`/rig:dev --recipe de-ai-smell "<ファイル or テキスト>"`。
  - **knowledge `ai-writing-smells`**（事実）— AI 臭の徴候カタログ A〜I（空疎な枕詞 / 過剰ヘッジ / 鋳型構造 / 水増し / 偽の網羅 / 誇張マーケ語 / 具体の欠如 / 記号癖 / メタ定型）を「なぜ臭う・どう直す」つきで定義。
  - **persona `ai-smell-reviewer`**（判断）— カタログを `inject` 相当で効かせ、「引用→記号→なぜ→直し」で指摘し、**原意保持のまま削除・具体化**で書き直す。逆 AI 臭（機械的平準化）も避ける。
  - **recipe `de-ai-smell`** — `acceptance-gate` で「中身（具体/立場）→トーン（誇張/ヘッジ）→表層（枕詞/水増し）→仕上げ（構造/記号）」の順に潰し、無臭まで収束。**表層研磨だけの空転を避ける**設計。
  - コードの AI-slop は従来どおり `adversarial-review` の担当（散文 ↔ コードで住み分け）。

## [0.14.0] - 2026-06-22

自動生成 Issue（#7–#9・`rig-idea`）を解決。#5/#6 の後続で、`--plan`（ドライラン）と `--validate`（doctor）を実行時の挙動に完全一致させる整合詰め。すべて spec のみ。

### Added
- **`--plan` の personas 列を解決済み最終集合に（#7）** — recipe `personas[]` ＋ manifest `default_personas` ＋ `--persona` の和集合（dedup）を表示し、出所マーカー `★`（default_personas 由来）/ `†`（--persona 由来）＋凡例を付与。**`--plan` の personas ＝ 実行時 reviewer** を spec 保証（ドライランと実行のズレを解消）。
- **`--plan` に受け入れ基準ブロック（#8）** — `gate: acceptance-gate` の step の `acceptance[]` を表の後にチェックリスト表示（`max_retries` 併記、空なら「基準未定義」WARN 注記）。`--plan` 段階でゲートの中身まで確認可能に。
- **`--validate` ③ に `max_retries` 値検証（#9）** — `≥1 の整数`制約を run 前に検査：`0`/負/非整数 → FAIL、`acceptance-gate` 以外の step に記載 → WARN、省略はスキップ。#3 が残した「将来の validate 拡張」の宿題を回収。

### CI
- **タグ push 不要のリリース自動化** — `release.yml` を統合し、**master に version bump が入ると `v<version>` タグ＋Release をサーバ側（Actions の `GITHUB_TOKEN`）で自動作成**する方式に変更（手元の `git push --tags` が不要に）。手動タグ push もフォールバックで処理、既存版は冪等スキップ。実行環境がクライアントからのタグ push を 403 で拒否する制約を、Actions 内のタグ作成で回避。

## [0.13.0] - 2026-06-22

自動生成 Issue（#1–#6・`rig-idea`）の spec 改善をまとめて解決。すべて spec/CI のみ（recipe・engine 不変）。

### Fixed
- **`--validate` の agent 偽 FAIL（#4）** — agent フォールバックの解決ベースパスが未指定で `skills/rig/agents/`（不在）を見ていた。**リポジトリルート `<repo>/agents/<name>.md`** と明記。reviewer agent を使う shipped recipe の偽「参照切れ」を解消。

### Added
- **`--validate` に manifest 参照チェック（#6）** — `.claude/rig.md` の `default_recipe` / `default_personas` が実在 tier に解決するかを点検し、タイポを FAIL 表示。RESOLVE/COMPOSE の silent fallback（recipe が黙って interactive 化／reviewer が黙って消える）を run 前に検出。manifest 不在ならスキップ。
- **`--plan` 正準フォーマット（#5）** — ドライラン出力を固定構造（ヘッダ＋per-step 表：id/instruction/pattern/gate/personas/policies/output_contract/condition）に定義。`--validate`/capture と同じ機械抽出可能な形。
- **`--list` を全 tier 表示（#2）** — project/user/shipped を走査し tier 別にグルーピング。`--save-recipe` で保存した recipe がここで発見でき、保存→一覧→再利用の輪が閉じる。
- **`max_retries`（acceptance-gate の K）を step スキーマに追加（#3）** — §3.5 に `max_retries`（既定 2）を定義、manifest `default_max_retries` で全体既定。stuck-guard との独立関係を明記。「K は調整可能」の約束に手段を与えた。
- **manifest テンプレに `size_thresholds`（#1）** — `S_max`/`M_max`/`L_max` を `_template.md` に掲載し §4.4 とサブキー名を整合。size-aware 閾値が発見可能に。

### CI
- **release 自動化** — `v*` タグ push 時に、その版の CHANGELOG 節を本文にして **GitHub Release を自動作成**するワークフロー（`.github/workflows/release.yml`）。該当節が無ければ Git 履歴の自動生成ノートにフォールバック。公式 `gh` のみ（サードパーティ action なし）。
  - 注意：タグ push で走るのは**そのタグが指すコミットに含まれる**ワークフロー定義。よって導入後のタグ（次回以降）から有効。

## [0.12.0] - 2026-06-22

### Added
- **manifest `default_personas`（製品ごとの常時 reviewer 自動投入）** — `<repo>/.claude/rig.md` に `default_personas: [<name>, …]` を1行宣言すると、その製品の review/adversarial step に当該 persona を**毎回自動投入**（`--persona` を都度打たなくてよい）。tier 解決（project→user→shipped）で名前解決し、persona の `inject: [[slug]]` 先 wiki も同伴注入。最終 reviewer ＝ 組み込み reviewer ＋ recipe `personas[]` ＋ `default_personas` ＋ `--persona` の名前和集合（dedup）。
- **`--no-default-personas` flag** — この run だけ manifest 自動投入を抑止。
- **catalog 表示** — `/rig:catalog` で `default_personas` 該当 persona に `★default` を付与（製品の常時 reviewer を地図上で可視化）。

### Notes
- `--persona`＝「この run で足す」一時指定、`default_personas`＝「この製品では常に使う」恒久宣言。自動選択は manifest 明示に限定（タグ推測の暗黙ルーティングはしない＝確実性優先）。

## [0.11.0] - 2026-06-22

**v2 ドメイン・ハーネス・プラットフォーム**（標準化ハーネス＋global 注入のドメイン知識＋LLM-wiki＋統合管理）が一通り揃ったリリース。

### Added
- **`/rig:catalog`（横断レジストリ・統合管理／Phase 3）** — 全 tier（shipped＋global＋project）を走査し、`domain × pack × persona（→inject する wiki）× wiki × recipe` の地図を tier つきで派生表示。`--domain <tag>` 絞り込み・`--json` 出力。読み取り専用・派生（手で持たない＝ドリフトしない）。
- **`--global` flag** — `--list` / `--validate` を tier 横断に拡張。`--list --global` は `/rig:catalog` 相当、`--validate --global` は全 tier の orphan・リンク切れ・参照欠落・重複を点検。

### Notes
- 検証: VST/音楽の例で生成→catalog→validate を実走査でドッグフードし、リンク切れ・inject 参照欠落の FAIL 検出まで確認。

## [0.10.0] - 2026-06-22

### Added
- **LLM-wiki ドメイン知識（Phase 2・本丸 B）** — `/rig:knowledge`。説明文 or `--auto`（repo 解析）からドメイン知識を **wiki ページ**（1概念=1正準ページ・相互リンク `[[slug]]`・派生 `INDEX.md`）として生成。`base=global ＋ project overlay`。
- **`facets/knowledge/_wiki`** — wiki スキーマ（`slug`/`aliases`/`tags`/`domain`/`status`/`links`/`sources`）と衛生ルール。
- **engine: `inject:` / `[[link]]` 解決** — persona は事実を埋め込まず `inject: ["[[slug]]"]` で wiki を参照。COMPOSE 時に tier 解決（project overlay > global）して Knowledge 位置へ注入＝**暗黙知サイロを解消**。
- **`--validate` 拡張** — wiki 衛生（リンク切れ・参照欠落・orphan・重複・frontmatter・INDEX ドリフト）。

## [0.9.0] - 2026-06-22

### Added
- **persona ジェネレータ（Phase 1）** — `/rig:persona "<説明>"`。説明文から reviewer persona を起草し、project（既定・製品単位）/ global（`--user`）に保存。書き込みは確認必須・冪等・捏造禁止。
- **engine: persona facet の tier 解決**（project → user → shipped。recipe §4.2.1 と同型）。
- **`--persona <name>` flag** — review fan-out に名前指定のカスタム reviewer を投入。

## [0.8.0] - 2026-06-22

### Added
- **圧縮サバイバル** — コンテキスト自動圧縮を跨いで rig の run-state を保つ `PreCompact` フック同梱（`hooks/`）。§6 run-continuity に「圧縮境界」節を追加。
- **`/rig:init`** — manifest（`.claude/rig.md`）・知識層ディレクトリ・CLAUDE.md "Compact Instructions" を scaffold（確認必須・冪等）。

## [0.7.0] - 2026-06-22

### Added
- **PR レビュー pack `/rig:pr <番号>`** — 既存 PR を GitHub MCP で取得し、3-way（security/design/test・＋`--adversarial`）レビュー → structured verdict。`--comment` で PR へ投稿（確認必須）。
- **`--validate`（doctor）** — recipe→facet 参照切れ・frontmatter スキーマ・§2 目録ドリフトを機械点検。
- **goal-loop の GitHub 連動基準** — 受け入れ基準に PR open / CI green / Issue クローズ可能を据え、GitHub MCP で照合。

### Fixed
- §2 ブリック目録のドリフト是正（dev-core ＋ pack 追加分の明記）。

## [0.6.0] - 2026-06-22

### Added
- **run-continuity** — RUN 中は各ターン冒頭に状態ヘッダ（`▸ rig | recipe … | step … | gate …`）を再掲し、質疑・脱線の後は再アンカーしてから現 step に復帰。step 境界バナーで dispatch/gate を可視化。中断後に rig が静かに「素の作業」へ逸れるのを防ぐ。

## [0.5.0] - 2026-06-21

### Added
- **ゴール駆動ループ `/rig:goal`** — 高レベルな目標を受け入れ基準に変換し「現状把握→次手→既存フローへ委譲→照合」を達成まで回す（`acceptance-gate ＋ autonomous-loop` の合成）。基準充足で停止・進捗ゼロ2回でエスカレーション。

## [0.4.0] - 2026-06-21

### Added
- **会話モード `/rig:talk`** — 話しかけると意図を汲んで適切な `/rig:*` フローへ橋渡しする JARVIS 的モード。

## [0.3.0] - 2026-06-21

### Added
- **sales ドメイン pack `/rig:sales`** — 商談記録を5観点で並列評価（engine 共用の多ドメイン実証）。

## [0.2.0] 以前

- engine（`PARSE → RESOLVE → COMPOSE → RUN`・context-minimal・acceptance-gate）、shipped recipe（review-only / release-flow / design-first / hotfix / adversarial-review）、敵対的レビュー、self-polish 等。詳細は git 履歴を参照。
