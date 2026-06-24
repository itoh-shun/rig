# Changelog

rig の変更履歴。バージョンは `.claude-plugin/plugin.json` に対応。
形式は [Keep a Changelog](https://keepachangelog.com/) に準拠（日付は JST）。

> リリースタグは GitHub 側で発行する（実行環境の都合でタグ push を別途行う運用）。

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
