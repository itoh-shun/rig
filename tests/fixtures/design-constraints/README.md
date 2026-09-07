# design-constraints fixture コーパス

`skills/engine/facets/policies/design-constraint-rules.md` が宣言する
**デザイン制約センサーの検出能力を、実装を見ずに測る**ための種（seed）一式。

このコーパスは policy とスキーマだけを読んで書いた。センサー実装
（`scripts/check_design_constraints.py`）は参照していないし、コーパス作成時点で存在も
していない。したがって `answer_key.json` は **仕様からの期待** であって実測ではない。
実測との差は、センサーの欠陥か、仕様の欠落か、この答案の誤りのいずれかで、
どれなのかは差が出てから決める。

## 何を測るか

policy は、センサーが主張してよい範囲を 3 クラスに限り、4 つ目は捕れないと明示している。
この構造をそのままコーパスの構造にした。

| 測るもの | 種の種類 | 件数 | 期待 |
|---|---|---|---|
| 再現率（素直な形） | `seed` | 12 | 全件検出 |
| 再現率（表記ゆれ） | `attack` | 14 | 全件検出。落ちた分が「素朴な実装の穴」の実測値 |
| 検出クラス外の確認 | `class-4` | 3 | **1 件も検出しない**。検出したら、それは仕様に無い推測をしている |
| 適合率（偽陽性） | `clean` | 8 | 全件で報告ゼロ |
| 未検査の判定 | `unchecked` | 3 | 合格でも不合格でもなく **未検査** |

`class-4` は「捕れない」と宣言したクラスが本当に捕れないことを測る種であり、
検出されないことが成功。`clean` を 8 件置いたのはそのため。宣言と実体の乖離を潰す道具が
偽陽性まみれなら、誰も使わなくなって乖離は残るからで、ここが一番大事。

## ファイル名について

種のファイル名に `seed` / `attack` / `clean` を入れていない。入れると、ファイルの中身を
1 バイトも読まずにパス名の正規表現だけで満点を取るセンサーが書けてしまう。
どのファイルが何なのかは `answer_key.json` と下の表だけが持つ。

## 制約ファイル

`constraints.json` — 架空プロダクト『Kanade』（会議室予約 SaaS）の埋まった制約。
`[要記入]` は残っていない。color 7 / spacing 5 / font 4 / radius 2 のトークン、
コンポーネント 8 件、禁止表現 5 件（うち 2 件が `regex: true`）。

禁止表現のうち `(?i)\bplease wait\b` は**どの種でも発火しない**。宣言されているが
出現しないパターンを 1 本置いてあり、これは「無いものを見つけたことにしない」の確認用。

## 種の一覧

### A. seed — 素直な違反（各 1 ファイル 1 違反）

| ファイル | クラス | 行 | 何を測るか |
|---|---|---|---|
| `artifacts/reservation-summary.css` | raw-value | 9 | CSS の生の色。ここが落ちたら何も見ていない |
| `artifacts/booking-filter.css` | raw-value | 8 | 生の長さ（13px） |
| `artifacts/notice-banner.jsx` | raw-value | 7 | JSX inline style の生の色。CSS だけ見る実装を切る |
| `artifacts/print-layout.css` | raw-value | 9 | `font-family` の生フォント名 |
| `artifacts/typography-spec.md` | raw-value | 12 | 使用トークン**表**の中の生フォント名（policy rule 6 が読めると言った場所） |
| `artifacts/seat-map.css` | unknown-token | 13 | `var(--color-brand-secondary)` |
| `artifacts/spacing-spec.md` | unknown-token | 13 | `token(spacing.xxl)` |
| `artifacts/cancel-flow.jsx` | unknown-token | 10 | インベントリ外 `<ConfirmBar />` |
| `artifacts/help-links.md` | prohibited | 11 | 部分文字列一致 |
| `artifacts/payment-errors.jsx` | prohibited | 6 | JSX テキスト中。Markdown だけ見る実装を切る |
| `artifacts/onboarding-steps.md` | prohibited | 9 | `regex: true` のパターン |
| `artifacts/palette-migration-note.md` | raw-value | 7 | **use と mention の区別をしない**（policy rule 8）。理由書き中の旧値も報告するのが正しい |

### B. attack — 素朴な実装ならすり抜ける書き方

前提: スキーマは禁止表現を「既定は部分文字列一致」と言う。字面どおりに Python の `in` で
実装すれば、下の大小変換・行またぎ・JSX 分割・ゼロ幅文字はすべて「仕様どおりに不検出」に
なる。それでもこれらを `expected_detectable: true` にしてあるのは、**表示される成果物には
禁止表現がそのまま出るから**で、この列の検出率は「仕様の字面より先に進めているか」の
指標として読む。落ちたことをセンサーの欠陥として断罪する数字ではない。

| ファイル | 狙い |
|---|---|
| `availability-chart.css` | 色を `rgb()` 記法に。`#hex` だけ見る実装が漏れる |
| `theme-accent.jsx` | `"#" + "0a84ff"` と連結。**どの 1 行にも連続した文字列が存在しない** |
| `compact-table.css` | 長さを `rem` に。`px` だけ見る実装が漏れる。単位正規化を要求 |
| `detail-panel.css` | 生の長さを `calc()` の中に隠す。値全体で判定する実装が漏れる |
| `legend-swatch.css` | CSS 色キーワード `dodgerblue`。数値記法だけ見る実装が漏れる |
| `heading-scale.css` | 生フォント名を `font` ショートハンドに。`font-family` だけ見る実装が漏れる |
| `tag-chip.md` | `token（...）` の**全角括弧**。日本語 IME で現実に起きる。参照ごと不可視になる |
| `toolbar.css` | `var(--unknown, var(--valid))` のフォールバック入れ子 |
| `quick-actions.jsx` | `<Card.Header />`。先頭識別子だけ取る実装は許可済みの `Card` を見て合格にする |
| `footer-links.md` | `Click Here`。大小違いだけで部分文字列一致を外す |
| `contact-guide.md` | 禁止表現が**行折り返しをまたぐ**。行単位走査に原理的に届かない |
| `upload-status.jsx` | 禁止表現を JSX の式コンテナで割る。描画結果にだけ現れる |
| `legacy-snippet.md` | 生値を**コードフェンス内**に。偽陽性回避でフェンスを除外する実装が漏れる |
| `reminder-copy.md` | 禁止表現の途中に**ゼロ幅スペース**(U+200B)。他より作為的だが混入は現実に起きる |

### C. class-4 — 検出クラス外（検出されないことが正しい）

| ファイル | 散文が破っているもの |
|---|---|
| `brand-voice.md` | 「ブランドの青を使う」「見出しは少し大きめ」——値もトークン名も無い |
| `card-rhythm.md` | 「余白は気持ち広めに」——spacing のどの段でもよいことになる |
| `link-writing.md` | 禁止された書き方を、字面を出さずに**指針として許可**している |

3 件目が構造的に厄介で、この文書に従って書かれた成果物は禁止表現で埋まる。
センサーは結果（pattern の出現）を見るが原因（pattern を産めという指示）は見ない。

### D. clean — 違反ゼロ（偽陽性を測る）

| ファイル | 仕込んだ偽陽性ベイト |
|---|---|
| `checkout-spec.md` | 見出し `#`、アンカー `#使用トークン`、Issue 参照 **`#48210`**（16 進列に見えるが CSS の色として妥当な桁数ではない）、コミット SHA **`a1b2c3d`**（`#` 無しの 16 進列）、宣言トークンだけのコードフェンス |
| `base-theme.css` | （ベイト無し）最も素直な合格例 |
| `grid-layout.css` | `0` / `100%` / `1.5` / `0.9` / `10` / `600` / `150ms` / `1fr` ——色・長さ・フォントのいずれでもない数値 |
| `reservation-card.jsx` | 小文字 HTML タグ、空フラグメント `<>`、`aria-hidden` |
| `dialog-copy.jsx` | 禁止表現の近傍語のみ——「こちらの手順」「Click the room name」「Please review」「ボタンの押下中は」 |
| `field-spec.md` | 正しい形の使用トークン表と `<Button>` / `<Field>` / `<Toast>` 参照 |
| `theme-root.css` | **トークンを定義している `:root`** そのもの。宣言値と完全一致する生値が 18 個、および引用符付きフォント名 2 行 |
| `print-preview.jsx` | 定義ファイルではない普通の実装に、宣言値と完全一致する生値 |

最後の 2 件は仕様の矛盾を突いている。policy 26 行目は「宣言集合に一致しない限り違反」と
言い、rule 5 は「値を書くときはトークン名で参照します」と言う。前者に従えば合格、
後者に従えば違反。ここでは 26 行目を正として `clean` に置いた。センサーが違反と判定した
なら、それは偽陽性ではなく**仕様解釈の相違**であり、報告に書くべきもの。

### E. unchecked — 未検査（合格ではない）

| ファイル | 未検査になる理由 |
|---|---|
| `unchecked/constraints.placeholder.json` | `[要記入]` が残っている（policy 3）。半分は埋まっており、埋まった分だけ検査して合格を返す実装をここで落とす |
| `unchecked/constraints.broken.json` | JSON がパースできない（policy 67 行目）。`json.load` が例外を投げることを確認済み |
| `unchecked/constraints.schema-invalid.json` | パースは通るがスキーマ不適合（`version: 2`、`tokens: {}`、`componentss`、`why` 欠落） |

3 件目は **policy が列挙する未検査条件のどれにも当たらない**。列挙は「ファイルが無い・
`[要記入]`・JSON が壊れている・成果物が読めない」の 4 つだけで、スキーマ不適合が入って
いない。`tokens` が空のまま「違反ゼロ」を返す経路がここに開いている。

## answer_key.json の読み方

依頼された形（`path` / `purpose` / `kind` / `expected_violations` / `expected_detectable` /
`why_undetectable`）に、次を足してある。

| 追加キー | 意味 |
|---|---|
| `spec_gap` | 仕様が決めていないこと。期待値をこちらの判断で決めた場合、その判断の根拠と、逆の解釈も文面から否定できないことを書いた |
| `line_end` | 違反が複数行にまたがる場合の終端（`contact-guide.md` のみ） |
| `line_fragment` | 正規化後の値（`token_or_value`）がその行に literal では現れない場合に、行内に実在する文字列を持つ。行番号の検算はこちらで行う |
| `unchecked`（トップレベル） | 成果物ではない制約ファイル 3 件。`artifacts` と混ぜると `kind` の意味が壊れるので分けた |

`class-4` の `expected_violations[].class` には `untokenized-prose` を使っている。
policy の検出クラス表 4 行目「（トークン化されていない散文）」に対応する語で、
センサーが主張してよい 3 クラスには入らない。

## 検算

行番号は手で数えていない。書いたあとにファイルから読み直して検算した。

```
python3 - <<'PY'   # tests/fixtures/design-constraints/ で実行
import json, os
ak = json.load(open("answer_key.json", encoding="utf-8"))
for a in ak["artifacts"]:
    lines = open(a["path"], encoding="utf-8").read().split("\n")
    for v in a["expected_violations"]:
        ln, end = v["line"], v.get("line_end", v["line"])
        needle = v.get("line_fragment", v["token_or_value"])
        assert needle in "\n".join(lines[ln-1:end]), (a["path"], ln, needle)
PY
```

同時に確認していること: 全 `path` が実在する / `artifacts/` の全ファイルが答案に載って
いる（過不足ゼロ）/ `clean` の `expected_violations` が空 / `class-4` が
`expected_detectable: false` / `seed` と `attack` が 1 ファイル 1 違反 /
`constraints.json` が `jsonschema.validate` を通る / 禁止表現の regex が compile する /
`constraints.json` に `[要記入]` が 0 個 / `constraints.broken.json` が実際に
`json.load` で失敗する。

## 種を足すとき

1. ファイル名に種別を書かない。
2. `seed` と `attack` は 1 ファイル 1 違反。混ぜるとどの検出が効いたか分からなくなる。
3. **成果物の中に目的を書かない。** 先頭コメントに「これは raw-value の種」と書けば、
   センサーはその行を読む。目的は `answer_key.json` の `purpose` だけが持つ。
4. `clean` を足すときは、既存の clean に無いベイトを足す。同じベイトの重複は
   偽陽性率の分母を水増しするだけ。
5. 期待値を決めるとき仕様が黙っていたら、`spec_gap` に書く。黙って決めない。

---

## 実測結果（親が追記・2026-09-07）

以下はコーパス作者ではなく、センサーを書いた側が測って書いた節である。**答案は作者の
もの、数字は測った側のもの**という区別を残すために節を分けている。

| | 結果 |
|---|---|
| seed | 11/12 |
| attack | 12/14 |
| **合計** | **23/26** |
| clean 偽陽性 | **0/8 ファイル・0 件** |
| class-4 漏れ検出 | **0/3** |

初回の実測は seed 11/12・attack 5/14 だった。**attack の 9 件の取りこぼしのうち 7 件は
原理的な限界ではなく正規化の欠落**で、答案に合わせるためではなく規則から正当化できる
変更として実装した——全角括弧（NFKC）、ゼロ幅文字、大文字小文字、行折り返しをまたぐ
禁止表現（本文全体への照合）、CSS 名前付き色、`font:` ショートハンド、ドット付きコンポーネント。
偽陽性 0 と class-4 漏れ 0 は、この作業を通して一度も崩れていない。

残る 3 件は字句センサーの外側で、`policies/design-constraint-rules` に表として記載した。

### 作者の答案に親が入れた変更

`cancel-flow.jsx` の `ConfirmBar` と `quick-actions.jsx` の `Card.Header` の `class` を
`unknown-token` から `unknown-component` に読み替えた。コーパスを依頼した時点のポリシーは
検出クラスを 3 つしか列挙しておらず（コンポーネントの行は依頼後に追加した）、作者は
存在しないクラス名を使えなかった。**種そのものは作者の設計で、親は名前だけを直している。**

### 作者が挙げた「仕様の穴」への対応

11 件のうち、正規化で塞いだものは上記の 7 件。散文へ反映したものは以下。

- スキーマ不適合を未検査条件に追加（`tokens: {}` が「違反ゼロ」を返す経路が開いていた）
- コードフェンスを検査対象に含めることを明記
- 禁止表現はファイル全体、それ以外は行単位という走査単位を明記
- `<button>` と小文字で書けばコンポーネント検査を回避できることを、欠陥ではなく
  宣言した範囲として明記
- 書体名は値としての形を持たず CSS 宣言の外では読めないことを明記
