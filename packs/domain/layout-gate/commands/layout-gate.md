---
description: "[experimental] 生成したスライドや固定サイズのページを計算で測り、枠から文字が出たまま出荷させない。"
argument-hint: "[成果物の種類・枠の寸法・生成器の呼び出し方] [--plan]"
---

# rig/layout-gate — レイアウトを目ではなく計算で見る

> この command asset は installed pack の呼び出し資料です。pack install だけでは
> ホストの slash command に自動登録されません。通常は
> `$rig --recipe layout-gate` で起動します。project pack の初回実行では内容を確認し、
> `RIG_ALLOW_PROJECT_PACKS=1` を設定して asset trust を記録してください。

最初に `rig:engine` skill を起動し、PARSE → RESOLVE → COMPOSE → RUN、facet の配置順、
context-minimal の規律に従います。この command は入口だけを担い、規則は
`layout-fit-rules` にあります。

## 導入と起動

```text
rig-wb pack install domain:layout-gate --scope project --allow-unverified
RIG_ALLOW_PROJECT_PACKS=1 $rig --recipe layout-gate \
  "1280x720 のスライド 48 枚。生成器は build-deck.js。HTML と pptx の両方を出す。"
```

この pack は `type: tool` です。recipe の `measure` step が、ホスト上で
`./scripts/layout-gate.sh` を実行します。install する前に、その 2 行と、自分で書く
script の中身を読んでください。実行されるのは、あなたが書いた script です。

pack 自体は実行できるコードを同梱しません。rig の pack model が、`.sh` と `.py` を
拡張子で、`.js` と `.mjs` を MIME で拒否するためです。同梱されるのは参照実装
（`resources/*.reference.md`）までで、走るコードはあなたの repository に置きます。

## 用意するもの

- **生成器** — 箱の寸法を数値で宣言できるもの。pptxgenjs のように座標で描くもの。
- **`scripts/layout/`** — 同梱の参照実装をそのまま置いた 2 つの file。
- **`./scripts/layout-gate.sh`** — 生成と検査をまとめて呼び、問題があれば非ゼロで
  終わる script。
- **HTML も検査するなら** Playwright と Chromium。無い場合、検査は exit 2 で
  「未検査」と分かる形で止まります。合格にはなりません。

## 同梱される検査

`resources/layout-fit.reference.md` の参照実装（CommonJS）を
`scripts/layout/layout-fit.js` として置き、pptxgenjs のような生成器の中で使います。
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

`resources/check-html-layout.reference.md` の参照実装は、HTML を Chromium で開いて
測ります。`scripts/layout/check-html-layout.mjs` として置いて呼びます。

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
