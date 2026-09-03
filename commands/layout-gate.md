---
description: "[experimental] 生成したスライドや固定サイズのページを計算で測り、枠から文字が出たまま出荷させない。"
argument-hint: "[成果物の種類・枠の寸法・生成器の呼び出し方] [--plan]"
---

# rig/layout-gate — レイアウトを目ではなく計算で見る

最初に `rig:engine` skill を起動し、PARSE → RESOLVE → COMPOSE → RUN、facet の配置順、
context-minimal の規律に従います。この command は入口だけを担い、規則は
`layout-fit-rules` にあります。

## 導入と起動

```text
$rig --recipe layout-gate \
  "1280x720 のスライド 48 枚。生成器は build-deck.js。HTML と pptx の両方を出す。"
```

recipe の `measure` step は、ホスト上で `./scripts/layout-gate.sh` を実行します。走るのは
あなたが書いた script です。何を測るかを決めるのはその 2 行なので、起動する前に読んで
ください。

## 用意するもの

- **生成器** — 箱の寸法を数値で宣言できるもの。pptxgenjs のように座標で描くもの。
- **`scripts/layout/`** — rig 同梱のセンサー 2 つ。生成器から読める場所へ複製します。
- **`./scripts/layout-gate.sh`** — 生成と検査をまとめて呼び、問題があれば非ゼロで
  終わる script。
- **HTML も検査するなら** Playwright と Chromium。無い場合、検査は exit 2 で
  「未検査」と分かる形で止まります。合格にはなりません。

## 同梱される検査

`scripts/layout/layout-fit.js`（CommonJS）を pptxgenjs のような生成器の中で使います。
折り返し後の行数から必要な高さを見積もり、宣言した箱と突き合わせます。同じページの
矩形どうしの重なりも見ます。`gate.enforce()` を書き出しの直前に置くと、落ちたときに
ファイルを作りません。

```js
const { LayoutGate } = require("./scripts/layout/layout-fit.js");
const gate = new LayoutGate();
gate.text(1, "card body", { x: 0.5, y: 1.2, w: 6, h: 2, text: body, fontPt: 12 });
gate.mono(1, "code", { x: 7, y: 1.2, w: 5, h: 2, text: snippet, fontPt: 11 });
gate.enforce();
await pres.writeFile({ fileName: "deck.pptx" });
```

`scripts/layout/check-html-layout.mjs` は、HTML を Chromium で開いて測ります。

```text
node scripts/layout/check-html-layout.mjs --stage 1280x720 --pages "[data-slide]" deck.html
node scripts/layout/check-html-layout.mjs --flow primer.html
```

## 見積りの精度

文字幅は全角 1em、ASCII の英数字を約 0.55em とした近似です。実際のフォントのメトリクス
ではありません。**入っているかどうか**を取りこぼさないための道具で、余白を詰めるための
道具ではありません。「あと 0.05 インチ空いている」といった判断には使わないでください。

## 例

```text
$rig --recipe layout-gate "pptx 31 枚。13.3x7.5in。カードとコードブロックが混在。"
$rig --recipe layout-gate "1280x720 の HTML スライド。data-slide 属性で 1 枚。"
$rig --recipe layout-gate --plan "既存の資料に検査を後付けしたい。生成器は自作。"
```
