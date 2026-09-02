---
name: layout-gate
description: 生成した資料のレイアウトを計算で測り、枠から溢れたまま出荷させない opt-in recipe。
scope: shipped
autonomy: interactive
steps:
  - id: build
    instruction: layout-build
    pattern: serial
    personas: [layout-builder]
    policies: [layout-fit-rules]
  - id: measure
    instruction: layout-measure
    executor: checks-only
    pattern: serial
    max_retries: 1
    checks:
      - "test -x ./scripts/layout-gate.sh"
      - "./scripts/layout-gate.sh"
  - id: review
    instruction: layout-gate-review
    pattern: serial
    gate: acceptance-gate
    max_retries: 1
    acceptance:
      - "レイアウト検査が実際に走り、その出力が残っている"
      - "溢れ、重なり、切れが 0 件である"
      - "検査を通すために本文が削られていない"
      - "許容誤差が広げられておらず、検査が外されていない"
      - "検査が走らなかった場合は合格ではなく未検査として報告されている"
    personas: [layout-gate-reviewer]
    policies: [layout-fit-rules, independent-verification]
    output_contract: layout-gate-verdict
---

# layout-gate

スライド、カード、固定サイズの HTML ページのように、**箱の位置と大きさを自分で決めて
描く**成果物のための recipe です。文字が枠から出たまま出荷することを、目ではなく計算で
止めます。汎用 dev recipe や core の既定値は変更しません。

## 構成

1. `build` — `layout-builder` が、箱の寸法を数値で宣言しながら資料を組みます。生成器には
   `scripts/layout/layout-fit.js` を読み込ませ、書き出しの直前で検査を落とします。
2. `measure` — 宣言した `./scripts/layout-gate.sh` を実行します。ここは provider を
   呼びません。落ちるか通るかだけを返します。
3. `review` — 作り手とは別の `layout-gate-reviewer` が、検査の出力と差分を突き合わせ、
   `layout-gate-verdict` を gate 内部へ返します。合否を作り手自身に付けさせません。

## なぜ計算で測るのか

作った本人が見ても、はみ出しは見つかりません。rig 自身の onboarding 資料では、48 枚を
何度も見返したあとでセンサーを入れて、溢れ 12 件と重なり 4 件が出ました。原因は言語の
変換ではなく、箱の高さを頭の中で見積もっていたことでした。分類は
`[[layout-overflow-causes]]` にあります。

## この pack が `tool` である理由

rig の pack model では、`checks:` を宣言した recipe を積めるのは `type: tool` だけです
（`RECIPE_CHECKS_TYPES`）。`checks` はホスト上で実行される shell command なので、
知識や文体を配るだけの pack に持たせません。この pack は検査を**実行する**ことが目的
なので `tool` です。導入する側は、実行される command を読んでから入れてください。

実行されるのは 2 行です。`./scripts/layout-gate.sh` が存在して実行可能かを見て、次に
それを実行します。中身は導入する側が書きます。pack は、あなたの生成器の呼び出し方を
知りません。

そしてもう一つ。rig の pack は、**実行できるコードを resource として配れません**。
`.sh` と `.py` は拡張子で、`.js` と `.mjs` は MIME で拒否されます。だからこの pack が
同梱するのは、規則と参照実装（`resources/*.reference.md`）までです。実際に走るコードは、
導入する側が自分の repository に置きます。何が実行されるかは、置いた本人が読めます。

## `./scripts/layout-gate.sh` に書くこと

生成と検査をまとめて呼び、1 件でも問題があれば非ゼロで終わる script にします。

```sh
#!/bin/sh
set -eu
node build-deck.js                    # 生成器。gate.enforce() が落ちれば書き出さない
node scripts/layout/check-html-layout.mjs --stage 1280x720 --pages "[data-slide]" deck.html
node scripts/layout/check-html-layout.mjs --flow primer.html
```

`scripts/layout/` の 2 つの file は、`resources/layout-fit.reference.md` と
`resources/check-html-layout.reference.md` の中身をそのまま置いたものです。

## 独立検証について

最終判定は、作り手と異なるモデルまたは provider の `layout-gate-reviewer` に行わせて
ください。同じモデルしか使えない場合は acceptance-gate を通したことにせず、
`UNVERIFIED` として報告します。

手順の正本は `facets/instructions/{layout-build,layout-measure,layout-gate-review}`、
規則の正本は `facets/policies/layout-fit-rules` です。
