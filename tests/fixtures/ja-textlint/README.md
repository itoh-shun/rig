# ja-textlint fixture コーパス

`skills/engine/facets/policies/japanese-textlint-rules.md` が宣言する
**日本語校正センサーの検出能力を測る**ための種（seed）一式。

## 出どころ

`cases/` の各 file は、textlint-ja の各ルールの README にある NG / OK の例を元に、
センサーを書く**前に**書いた。`answer_key.json` は「そのルールがその行に出る」という
期待で、上流のルールが何を NG と言っているかから導いたものであって、この実装の出力の
写しではない。

その後、同じコーパスに本家 textlint-ja を走らせた結果を `upstream-textlint.json` に写した
（版は file の中）。`answer_key.json` の期待は、本家が違うことを言った箇所を本家に合わせて
直してある——`sentence-length` はインラインコードを数える（種 7 行目）、`max-ten` の
4 読点の種は名詞挟みの読点が数えられない書き方だったので書き換えた。本家が報告し
このコーパスが期待しないもの（「一つ一つ」の畳語、「：」で終わる段落）は policy の
「意図した逸脱」として残し、`tests/test_ja_textlint_parity.py` がその差を一行ずつ固定する。

ただし、コーパスを書いた人とセンサーを書いた人は同じである。design-constraints の
コーパスのような「実装を知らない著者」の分離はここには無い。分離は**時間と出典**にある
——先に書き、上流の例から書いた——のであって、人ではない。差が出たときに「コーパスの
誤り」と決めた例が 2 件あり、どちらも種の側が主張した性質を持っていなかった
（`nfd.md` に濁点のある文字が無かった、`spacing.md` の揺れが同数で多数派が決まらなかった）。
実装に合わせて期待を変えた例は無い。

## 何を測るか

| 測るもの | 種 | 件数 | 期待 |
|---|---|---|---|
| 再現率 | `cases/*.md` の `expect` 行 | 73 | 全件、行番号まで一致 |
| 本家との一致 | `upstream-textlint.json` | 30 ルール 77 件 | ルールごとの (本家, センサー, 一致) を固定 |
| 適合率（同一 rule） | `cases/*.md` の `expect` 以外の行 | — | その rule の所見ゼロ |
| 適合率（正直な文書） | `clean/*.md` | 3 | error ゼロ。warning は件数を固定 |

`cases/` の file には、その rule の NG 行と、似た形の OK 行を並べてある（「見れた」と
「見れば」、「してる」と「捨てる」、「しなければならない」）。OK 行に所見が出れば偽陽性で、
test が落ちる。他の rule が同じ file で何を言うかは測らない（`max-ten.md` が
`sentence-length` に当たっても数えない）。

`clean/` は、規約に沿って書いた実務文書 3 本（リリースノート、障害報告、手順書）。
error が 1 件でも出れば、それは規則の性質ではなく偽陽性である。warning は
`no-doubled-joshi` が 2 件出ており、「結果が古い場合が」「壊れることが」の形で、kuromoji を
使う本家が同じ file に報告する 2 行と一致する。近似が生む偽陽性ではなく規則の性質なので、
消さずに件数を固定している。

## 設定

`config.json` — 全 preset（助言専用の `ai-smell` を含む）を有効にし、`prh` 相当の用語を 2 件宣言する。
`ai-smell-phrases.md` の種は、本家 textlint が **1 件も報告しない** file である——AI 臭の語彙という
クラス全体が textlint の外にあり、rig 側では `knowledge/ai-writing-smells` が持っている。
`ja-space-between-half-and-full-width` と `ja-space-around-code` は既定の `auto` のまま。
`spacing.md` は入れない書き方を多数派にしてあるので、入れた行が少数派として報告される。
本家は `never` 固定なので同じ file でより多く報告し、その差は parity test の表にある。

## file 名について

種の file 名は rule 名そのものである。design-constraints のコーパスと違い、ここでは
「path 名だけで満点を取るセンサー」を防ぐ必要がない——センサーは file 名を読まないし、
1 つの file に NG 行と OK 行が同居しているので、file 単位の判定では満点にならない。
