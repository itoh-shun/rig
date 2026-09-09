---
description: "[experimental] 日本語文書を textlint-ja 相当のセンサーで機械的に検査し、error だけを直して、別の reviewer が内容の保持を確かめる。"
argument-hint: "[検査対象の path・直してよい範囲・固有名詞] [--plan]"
---

# rig/japanese-lint — 日本語の書き方を印象ではなく数え方で見る

最初に `rig:engine` skill を起動し、PARSE → RESOLVE → COMPOSE → RUN、facet の配置順、
context-minimal の規律に従います。この command は入口だけを担い、規則は
`japanese-textlint-rules` にあります。

## 導入と起動

```text
$rig --recipe japanese-lint "docs/ と README.ja.md。固有名詞『株式会社東京電力』は許可。"
```

recipe の `fix` step は、ホスト上で `rig-wb ja-lint --report .rig/ja-lint-report.json` を
実行します。検査対象は `<repo>/.claude/ja-textlint.json` の `paths` です。設定が無いか
`paths` が空なら、センサーは未検査（exit 2）を返し、step は落ちます。走らなかったことを
合格にしないためです。

## 用意するもの

- **`.claude/ja-textlint.json`** — 雛形は `skills/engine/manifests/ja-textlint.template.json`。
  `paths` に検査対象を書きます。`presets` の既定は `technical`（textlint-rule-preset-
  ja-technical-writing 相当）と `spacing`（preset-ja-spacing 相当）で、`hiragana`（形式名詞・
  副詞・補助動詞をひらく）と `style`（表記ゆれ・全角英数字）は opt-in です。
- **`rig-wb`** — `/rig:setup` で入ります。checkout からなら `python3 scripts/ja_textlint.py`
  が同じものです。Node も kuromoji も要りません。

## 手で走らせる

```text
rig-wb ja-lint docs/ README.ja.md
rig-wb ja-lint --preset technical --preset hiragana --strict README.ja.md
rig-wb ja-lint --fix docs/
cat draft.md | rig-wb ja-lint -
rig-wb ja-lint --list-rules
```

出力は `file:line:col: severity [rule] message` です。error があれば exit 1、warning だけなら
exit 0、設定が壊れているか対象が無ければ exit 2（未検査）です。`--strict` で warning も
exit 1 に数えます。`--json` と `--report <path>` で機械可読な報告を出します。

`--fix` は機械的に置き換えられる所見（半角カナ、全角英数字、NFD、ゼロ幅文字、用語、ひらく
規則、誤用、括弧・スラッシュ・全角間の空白）だけを本文に当てて書き戻し、残りを報告します。
文を分ける、二重否定を言い換えるといった意味に触る修正はしません。固有名詞や引用を
逃がすには、本家と同じ `<!-- textlint-disable rule -->` … `<!-- textlint-enable -->` と
`<!-- textlint-disable-line -->` が使えます。抑えた件数は報告に残ります。

このセンサーは本家 textlint-ja と同じコーパスで所見単位に突き合わせてあります。両方が持つ
30 ルールで本家 77 件、センサー 85 件、一致 57 件。差はすべて policy と
`tests/test_ja_textlint_parity.py` に理由つきで載っています。

## error と warning

文字と辞書で決まるルール（一文の長さ、読点の数、二重否定の定型、冗長表現、誤用、括弧の
対応、用語）は error です。品詞の近似に頼るルール（助詞の連続、ら抜き、い抜き、敬体と
常体の混在、形式名詞）は warning で、偽陽性を含みます。**warning を消すために本文を
歪めないでください。** 固有名詞は設定の `allow` / `ignore` / `groups` に宣言して逃がします。

この recipe を呼ばなくても、センサーは gate として効きます。`/rig:go` の acceptance gate は
diff が日本語の散文を足したときに `ja_lint_clean`（追加行の error で `failed`）と
`ja_prose_ai_smell_reviewed`（`ai-smell-reviewer` の verdict を写す）を自動で足し、
`rig-wb githooks install` を入れた repository では `pre-commit` が `--staged`、`commit-msg` が
message を検査します。`japanese-writing` の reviewer は完成稿を stdin で通し、error が
残れば `REVISE` です。どの経路でも warning は gate にしません。

## 例

```text
$rig --recipe japanese-lint "docs/guide/ 以下。用語は JavaScript と GitHub に統一。"
$rig --recipe japanese-lint "README.ja.md。敬体に揃える。"
$rig --recipe japanese-lint --plan "既存の社内文書に検査を後付けしたい。固有名詞が多い。"
```
