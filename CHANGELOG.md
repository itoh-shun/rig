# Changelog

rig の変更履歴。バージョンは `.claude-plugin/plugin.json` に対応。
形式は [Keep a Changelog](https://keepachangelog.com/) に準拠（日付は JST）。

> リリースタグは GitHub 側で発行する（実行環境の都合でタグ push を別途行う運用）。

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
