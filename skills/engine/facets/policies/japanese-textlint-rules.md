# policy: Japanese Textlint Rules v1

日本語の文書を、textlint-ja（`textlint-rule-preset-ja-technical-writing`・
`preset-ja-spacing`・`ja-hiragana-*`・`prh`）が JavaScript と kuromoji でやっている検査の
**辞書と正規表現で決まる部分だけ**、Python 標準ライブラリで機械的に見るための規則です。
対象は Markdown とプレーンテキストの日本語散文で、センサーは `rig_workbench/ja_textlint.py`
（インストール済みなら `rig-wb ja-lint`、checkout では `scripts/ja_textlint.py`）です。

`[[japanese-ai-smell-jp]]` と `scripts/prose_rhythm.py` が扱うのは**読み手の印象**（AI 臭・
拍・語尾）で、判定は reviewer に残っています。この policy が扱うのはその手前、
**書き方の規約**です。一文が 100 字を超えたか、読点が 4 つあるか、「〜することができる」と
書いたか。印象ではなく数え方で決まるので、機械に任せます。

## センサーが主張してよい範囲

形態素解析は行いません。textlint-ja の多くのルールは kuromoji の品詞情報に依存しており、
それを表層の近似で置き換えています。**近似であるルールは既定の severity を `warning` にし、
exit code に影響させません。** 文字と辞書で決まるルールだけが `error` です。

| ルール | 内容 | 判定の根拠 | 既定 |
|---|---|---|---|
| `sentence-length` | 一文が上限（100 字）を超える。インラインコードは数え、URL・リンク先は数えない（本家と同じ） | 数え上げ | error |
| `max-ten` | 一文の読点が上限（3）を超える。本家と同じく名詞に挟まれた読点（「A、B、C」）は数えない。`strict: true` で全部数える | 数え上げ | error |
| `ja-no-mixed-period` | 段落末が「。」で終わらない、または `.` で終わる | 文字 | error |
| `no-double-negative-ja` | 「〜ないことはない」「〜ないわけではない」など定型の二重否定 | 辞書 | error |
| `no-doubled-conjunctive-particle-ga` | 一文に「〜が、」が二回 | 文字 | error |
| `no-doubled-conjunction` | 連続する文の頭で同じ接続詞（「しかし、」「また、」） | 辞書 | error |
| `no-nfd` | 結合文字のまま（NFD）の濁点・半濁点 | 文字 | error |
| `no-invalid-control-character` | 制御文字 | 文字 | error |
| `no-zero-width-spaces` | ゼロ幅文字（先頭の BOM を除く） | 文字 | error |
| `no-exclamation-question-mark` | 「！」「？」 | 文字 | error |
| `no-hankaku-kana` | 半角カナ | 文字 | error |
| `ja-no-abusage` | 「汚名挽回」「的を得る」など誤用の辞書 | 辞書 | error |
| `ja-no-redundant-expression` | 「〜することができる」「まず最初に」「検討を行う」など | 辞書 | error |
| `no-unmatched-pair` | 段落内で括弧・鉤括弧が閉じていない | 数え上げ | error |
| `ja-no-space-around-parentheses` | 全角括弧の前後の空白 | 文字 | error |
| `ja-nakaguro-or-halfwidth-space-between-katakana` | カタカナ語の区切りが「・」と空白で混在 | 数え上げ | error |
| `ja-no-space-between-full-width` | 全角文字どうしの間の半角スペース（「人間 対 生成」） | 文字 | error |
| `prh` | 設定の `terms` に宣言した用語（`Javascript` → `JavaScript`） | 宣言 | error |
| `max-kanji-continuous-len` | 漢字が上限（6、preset-ja-technical-writing と同じ）を超えて連続 | 数え上げ（固有名詞は `allow`） | warning |
| `no-mix-dearu-desumasu` | 本文で敬体と常体が混在。少数派を報告 | **文末の近似** | warning |
| `no-dropped-i` | い抜き（「してる」「読んでる」） | **直前の音の近似** | warning |
| `no-dropping-the-ra` | ら抜き（「見れる」「食べれる」） | **一段動詞の一覧** | warning |
| `no-doubled-joshi` | 一文の中で同じ助詞が続く（「材料不足で代替素材で」）。本家と同じく「を」は例外、読点と括弧は距離に数える | **助詞位置の近似** | warning |
| `ja-no-weak-phrase` | 「かもしれない」「と思います」 | 辞書 | warning |
| `ja-no-successive-word` | 「これはは」「確認確認」 | 文字 | warning |
| `ja-unnatural-alphabet` | ひらがなの中の半角小文字一字（「こnにちは」） | 文字 | warning |
| `ja-space-between-half-and-full-width` | 全角と半角英字の間の空白の有無 | 数え上げ | warning |
| `ja-space-around-code` | インラインコードと日本語の間の空白の有無 | 数え上げ | warning |
| `ja-no-space-around-slash` | 日本語に接するスラッシュの前後の空白 | 文字 | warning |
| `ja-hiragana-keishikimeishi` | 形式名詞「事」「時」「為」「訳」「物」「所」 | **前後の近似** | warning |
| `ja-hiragana-fukushi` | 副詞「予め」「殆ど」「最も」など。本家 `ja-hiragana-fukushi` の辞書 76 対をそのまま（MIT） | 辞書 | warning |
| `ja-hiragana-hojodoushi` | 補助動詞「〜て下さい」「〜て頂く」「お願い致します」。本家の辞書に「出来る」「〜て見る」などを足した上位集合 | 辞書 | warning |
| `ja-no-orthographic-variants` | 「サーバ／サーバー」など同一文書内の表記ゆれ。少数派を報告 | 数え上げ | warning |
| `no-zenkaku-alnum` | 全角英数字 | 文字 | warning |
| `ja-ai-smell-phrases` | AI 臭の名指しブラックリスト（`ai-smell` preset・opt-in）。`[[ai-writing-smells]]` の「禁止表現リスト」と「名指し語彙ブラックリスト」を辞書で読む | 辞書＋段落密度 | **warning 固定（error にできない）** |

### 助言専用の規則（見せるが、通さない）

`ja-ai-smell-phrases` だけは他と性格が違います。**error に昇格できず、`--strict` でも exit code を
動かしません**（`ADVISORY_ONLY`。設定で `severity: error` を書くと `unchecked` で止まります）。

理由は測ってあります。rig 自身の実測（`docs/jp-naturalness-engineering.ja.md` §6-3）で、AI 臭の
代理指標を gate にすると**所見は 5.5 → 1.0 に減ったのに、人の盲検判定は悪化しました**。所見を
消すために語を入れ替える圧力がかかり、空っぽの文章はそのまま残るからです。だから「機械に
数えさせて reviewer に見せる」ところで止め、合否は `ai-smell-reviewer` の判断に残します。

**何を報告するか。** カタログ自身が「文脈上ふさわしい使用まで一律禁止にしない＝見るのは
カテゴリの**撒きすぎ**（同種を一段落に複数）」と書いているので、既定では**同じ段落に同じ
カテゴリが 2 件以上**出たときだけ報告します（`min_hits` で変えられます）。「不可欠」が一度
出ただけでは鳴りません。例外は、カタログが「→ こう置く」と置換先まで指定している 3 カテゴリ
（論文ぶり自称・言い回しジャーゴン・結論回避フレーズ）で、こちらは 1 件でも書き換えの対象です。
逃がし方は `allow` です。

**実測。** 上のユーザー下書き相当の文章（AI が書いた典型）では、カタログの語が 2 カテゴリ 4 件
出ます。rig 自身の日本語 docs 21 ファイルでは **0 件**で、`min_hits: 1` に下げると 8 件です
（予告・総括 5、強度副詞 3）。正直な実務文書 3 本でも 0 件。密度の条件が偽陽性を抑えている
分がこの差です。

**捕れないもの。** カタログの深層マーカー（N 具体の不在、P 人物の不在、V 意外性の欠如、
J 同形反復）は語彙ではないので、辞書では捕れません。5 観点スコアも同じです。この規則が
主張するのは**語彙の層だけ**で、「無臭だが空っぽ」は reviewer が読みます。カタログの
構成・演出のルール（地の文の「——」、太字の量、見出しの二要素）も入れていません。rig 自身の
docs がその流儀で書かれていて、規則にすると数百件鳴るだけになるからです。

### 捕れないもの（検出クラスの外）

次は構造的に捕れません。捕れたことにしないでください。

- **品詞が要る判定の残り。** 助詞が「ひらがなの直後」にあるとき（「これを」「ものを」は
  代名詞一覧で拾うが、「もらった本を」の「を」は漢字直後なので拾う、「もらったのを」の「を」は
  拾わない）。ら抜きは一覧にある一段動詞だけ、い抜きは「〜てる」の直前がイ段・エ段・
  「っ」「ん」のときだけです。漢字の直後の「てる」（「見てる」と「建てる」）は判断しません。
- **漢数字と算用数字の使い分け**（textlint の `arabic-kanji-numbers`）。「一人」「一方」
  「一部」のような熟語と数詞を表層で分けられないので、実装していません。
- **同義語の混在**（`@textlint-ja/no-synonyms`）と**不適切語**（`ja-no-inappropriate-words`）。
  辞書を同梱しません。表記ゆれは `ja-no-orthographic-variants` の一覧と `groups` の宣言だけです。
- **敬体・常体の判定で、体言止めと「〜こと。」「〜ため。」は中立**です。数えません。
- **HTML・表・コードブロック・front matter の中身。** 表の行は文字ルール（半角カナ・
  ゼロ幅・全角英数字・用語）だけを見ます。文の長さや読点は見ません。

実測は `tests/fixtures/ja-textlint/` で行い、`tests/test_ja_textlint_corpus.py` が
数値を固定しています。2026-09-09 の実測は、textlint-ja 各ルールの README の NG/OK 例から
書いた種に対して **73/73**（行番号まで一致、種の file 内で同じ rule の偽陽性 0）、規約に
沿って書いた実務文書 3 本に対して **error 0**、warning は `no-doubled-joshi` の 2 件だけ
（「結果が古い場合が」「壊れることが」——kuromoji を使う本家が同じ file に報告する 2 行と一致）
です。コーパスの著者はセンサーの著者と同じで、分離は時間と出典にあります。

**本家との突き合わせ。** 同じコーパスに本家 textlint-ja（textlint 15.8.0、
preset-ja-technical-writing 12.0.2、preset-ja-spacing 3.0.3、ja-hiragana-* 、prh）を走らせた
結果を `tests/fixtures/ja-textlint/upstream-textlint.json` に写し、
`tests/test_ja_textlint_parity.py` がルールごとの一致を固定しています。両方が持つ 30 ルールで
本家 77 件、このセンサー 85 件、同じ (file, 行) が 57 件。本家に対する再現率 0.74、適合率 0.67
で、センサーだけが報告する 28 件はすべて下の「意図した逸脱」か「上位集合の辞書」に
属し、文字で決まると宣言したルールは 13 本すべて行単位で一致します。本家だけが持つのは
`arabic-kanji-numbers` の 1 本で、捕れないと上に書いたものです。
数値が動いたら、テストではなく出荷している比率が古くなっています。再測して、ここと
test の両方を更新してください。

## textlint からの意図した逸脱

- **`ja-space-between-half-and-full-width` の既定は `auto`** です。textlint の既定は
  `never` ですが、日本語の技術文書では英字の前後に半角スペースを置く流儀が広く使われて
  います（rig 自身の docs がそうです）。どちらかを既定にすると半分の文書で一行おきに
  所見が出ます。`auto` は文書内で混在しているときだけ少数派を報告し、プロジェクトが
  `always` / `never` を宣言すればそれに従います。数字と日本語の間（「9月」「14時」）は
  流儀の投票に入れません。
- **近似ルールは warning** です。textlint の preset はすべて error ですが、品詞を持たない
  近似を gate に繋ぐと、偽陽性を消すために本文が歪みます。warning は報告に残り、
  reviewer が読みます。`--strict` で error に昇格できます。
- **`ja-space-around-code` の既定も `auto`** です。理由は上と同じで、本家の `never` は
  rig 自身の docs に 782 件出ます。
- **`no-mix-dearu-desumasu` は多数派に合わせます。** 本家の既定は「本文はですます、箇条書きは
  である」の固定で、常体で書かれた設計文書の本文を全行報告します。ここでは本文の多数派を
  基準にし、箇条書きと見出しは見ません。文体を指示したいときは `prefer` に書きます。
- **`ja-no-mixed-period` は「：」で終わる段落を免除します。** 箇条書きやコードを導く段落の
  慣用で、本家は報告します。
- **辞書は上位集合です。** 二重否定（「ないわけではありません」「なくはない」）、冗長表現
  （「まず最初に」「各〜ごと」「約〜ほど」）、誤用（「汚名挽回」「的を得る」）、弱い表現
  （「気がします」）は本家の辞書より広く、`ja-no-successive-word` は「一つ一つ」を畳語として
  許します。差はすべて `test_ja_textlint_parity.py` の表に一行ずつ理由つきで載っています。
- **英文の段落には当てません。** 日本語（かな・漢字）を含まない文は、文の長さも読点も
  見ません。

## 状態は 3 つ、どれも合格ではない

| 状態 | 意味 | exit |
|---|---|---|
| `checked` | 成果物を読み、検査した。error の有無はこの状態でだけ言えます | 0 / 1 |
| `unchecked` | **検査が成立しなかった。** 設定 JSON が壊れている、知らないキーがある、成果物が読めない、対象が 1 件も無い | 2 |
| `not-configured` | `--if-configured` 指定で、引数も設定の `paths` も無い。検査していない | 0 |

設定に知らないキー（`rulez`）があると `unchecked` です。誤記で規則が黙って抜け、`checked`
0 件で緑になる経路を残さないためです。`--report <path>` はどの状態でも JSON を書きます。
orchestrate の checks 実行系は stdout を捨てるので、**報告の本体はこのファイルです。**

## 設定する

1. 設定は **`<repo>/.claude/ja-textlint.json`** に置きます。スキーマは
   `manifests/ja-textlint.schema.json`、雛形は `manifests/ja-textlint.template.json` です。
   無ければ既定（`technical` + `spacing`）で動きます。
2. `paths` に検査対象を書くと、引数なしの `rig-wb ja-lint` と recipe `japanese-lint` の
   checks がそこを読みます。
3. `presets` は `technical`（textlint-rule-preset-ja-technical-writing 相当）、`spacing`
   （preset-ja-spacing 相当）、`hiragana`（ひらく規則）、`style`（表記ゆれ・全角英数字）。
   後二者は opt-in です。
4. `rules` でルール単位に `false` / `"error"` / `"warning"` / `{severity, ...options}`。
   `terms` は prh 相当の用語規則、`ignore` は所見の text に当てる正規表現の逃がし方です。

5. **抑制コメント**は本家の `textlint-filter-rule-comments` と同じ書き方です。
   `<!-- textlint-disable rule, rule -->` … `<!-- textlint-enable -->` で囲むか、
   `<!-- textlint-disable-line -->` を行末に、`<!-- textlint-disable-next-line rule -->` を
   前の行に置きます（`ja-lint-` でも同じ）。rule を書かなければ全部を抑えます。抑えた件数は
   報告の `summary.suppressed` に残るので、「所見が無い」と「抑えた」は区別できます。
6. **`--fix`** は機械的に置き換えられる所見だけを本文に当てて書き戻します。半角カナ、
   全角英数字、NFD、ゼロ幅文字、用語、ひらく規則、誤用の辞書、括弧・スラッシュ・全角間の
   空白です。文を分ける、二重否定を言い換える、接続詞を変えるといった意味に触る修正は
   しません。それは `fix` step の担い手の仕事で、`--fix` の後に残った所見がその一覧です。
   置き換える前にその位置の文字が所見と一致することを確かめ、ずれていれば触りません。

## どこで gate になるか

lint は呼んだときだけ動く道具ではなく、日本語を書くあらゆる経路で gate です。ただし
**gate になるのは文字と辞書で決まる error だけ**で、AI 臭の判定は reviewer の verdict を
gate にし、`scripts/prose_rhythm.py` のような機械の点数は gate に繋ぎません。rig 自身の
実測（`docs/jp-naturalness-engineering.ja.md` §6-3）が、リズム指標を gate にすると所見は
減るのに人の盲検判定が悪化することを記録しているからです。

| 経路 | 機械 gate（ja-lint の error） | AI 臭の gate |
|---|---|---|
| `/rig:go` の acceptance gate | diff が日本語の散文を足したとき `ja_lint_clean` が現れ、追加行の error で `failed`。センサーが毎回書き直すので `--set ja_lint_clean=passed` は次の評価で消える。残るのはセンサーより厳しい `--set ja_lint_clean=failed` だけ | 同じ条件で `ja_prose_ai_smell_reviewed` が現れ、`ai-smell-reviewer` の verdict を写す。verdict が無ければ `pending` のまま accept できない |
| review fan-out（`parallel-review`） | `rig-wb wb scan-ja-prose <task_id>` を reviewer の入力に添える | diff が日本語の散文を足したとき `ai-smell-reviewer` レーンを必ず加える |
| `git commit`（`rig-wb githooks install`） | `pre-commit` が `rig-wb ja-lint --staged`、`commit-msg` が message を `--preset commit` で検査。error で止まる | — |
| `japanese-writing` / `japanese-writing-revision` | reviewer が完成稿を stdin で通し、error が残れば `REVISE` | 既存の `japanese-ai-smell-jp` 判定 |
| `de-ai-smell` | 書き直し後の全文で error 0 が acceptance | 既存の 5 観点スコア gate |
| `japanese-lint` | `fix` step の checks | — |
| commit message・PR 本文（`pr` step） | 送る前に `--preset commit` で error を直す | — |
| 会話（`talk-assistant`） | 返答を `--preset conversation` で通し、error だけ直す（hook で強制はできない） | — |

どの経路でも warning は gate にしません。近似ルールの偽陽性を消すために本文が歪むのを
避けるためで、warning は報告に残り、reviewer が読みます。`ja-ai-smell-phrases` は
さらに一段強く、**error に昇格する経路そのものを塞いであります**（上の「助言専用の規則」）。
`de-ai-smell` と review fan-out の日本語散文レーンでは、この規則の出力を
`ai-smell-reviewer` への入力に添えます——機械が語彙を数え、reviewer が中身を読む分担です。

## 所見をどう扱うか

7. **error は直します。warning は読みます。** warning を消すために本文を削らないでください。
   固有名詞が `max-kanji-continuous-len` に当たるなら `allow` に足します。文体を変える
   指示は `no-mix-dearu-desumasu` の `prefer` に書きます。
8. **明示された事実、固有名詞、数値、手順は直しても変えません。** lint は書き方を見ている
   のであって、内容を見ていません。「〜することができる」を「〜できる」にしても意味は
   変わりませんが、「約 10 分ほど」から「約」を落とすとき、「ほど」の幅は残します。
9. **reviewer は報告の行を根拠にします。** 「lint を通しました」という文だけでは合格に
   しません。報告ファイルの `status` が `checked` で、`summary.errors` が 0 で、直前の
   差分がその error を消していることを見ます。`unchecked` は `UNVERIFIED` です。
10. **同じ所見が繰り返し出るなら、規則を疑う前に本文を疑います。** ただし固有名詞と
   引用は逃がしてよく、その逃がし方は `ignore` か `allow` に**宣言として**残します。
   規則そのものを `false` にするときは、理由を設定の `_readme` に書きます。
