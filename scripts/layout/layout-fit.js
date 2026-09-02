/* layout-fit — 生成したスライドやカードのレイアウトを、目ではなく計算で見るセンサー。
 *
 * pptxgenjs のように「箱の位置と大きさを自分で決めて描く」生成器では、文字が枠から
 * はみ出しても誰も止めてくれません。書き出したファイルを開いて初めて気づきます。
 * このモジュールは、折り返したあとの行数から必要な高さを見積もって、宣言した枠と
 * 突き合わせます。あわせて、同じページに置いた箱どうしの重なりも見ます。
 *
 * 使い方（CommonJS）:
 *
 *   const { LayoutGate } = require("<pack>/resources/layout-fit.js");
 *   const gate = new LayoutGate();
 *   gate.text(1, "card body", { x: 0.5, y: 1.2, w: 6, h: 2, text: body, fontPt: 12 });
 *   gate.box(1, "image", { x: 7, y: 1.2, w: 5, h: 3 });
 *   gate.enforce();          // 1件でも溢れ・重なりがあれば exit 1
 *   await pres.writeFile(...);
 *
 * `enforce()` を書き出しの直前に置くのが要点です。落ちたときにファイルを一切
 * 作らないので、壊れた成果物が残りません。
 *
 * 精度について。文字幅は全角 1em、ASCII の英数字を約 0.55em とした近似です。
 * 実際のフォントのメトリクスではないので、余白を詰めるための道具にはなりません。
 * 狙いは「明らかに入っていない」を取りこぼさないことです。
 */
"use strict";

const PT = 72;

/** 1文字あたりの幅を em で返す（全角=1.0 の近似）。 */
function charEm(ch) {
  if (/[　-ヿ一-鿿＀-￯]/.test(ch)) return 1.0;
  if (ch === " ") return 0.28;
  if (/[A-Za-z0-9]/.test(ch)) return 0.55;
  return 0.5;
}

/** widthIn（inch）の幅に fontPt で流し込んだときの、折り返し後の行数。 */
function lineCount(text, widthIn, fontPt) {
  const limit = widthIn / (fontPt / PT);
  if (!(limit > 0)) return 999;
  let lines = 0;
  for (const para of String(text).split("\n")) {
    if (para === "") { lines += 1; continue; }
    let used = 0, rows = 1;
    for (const ch of para) {
      const w = charEm(ch);
      if (used + w > limit) { rows += 1; used = w; } else { used += w; }
    }
    lines += rows;
  }
  return lines;
}

/** 折り返し後の高さ（inch）。leadPt を省くと fontPt の 1.2 倍を行送りにします。 */
function blockHeight(text, widthIn, fontPt, leadPt) {
  return (lineCount(text, widthIn, fontPt) * (leadPt || fontPt * 1.2)) / PT;
}

/** もっとも長い行の幅（inch）。等幅で組む前提のコードブロック向けです。 */
function widestLine(text, fontPt, emPerChar) {
  const em = emPerChar || 0.6;
  const rows = String(text).split("\n");
  const widest = Math.max(...rows.map(
    (r) => [...r].reduce((a, c) => a + (charEm(c) === 1 ? 1.0 : em), 0)));
  return (widest * fontPt) / PT;
}

class LayoutGateError extends Error {}

class LayoutGate {
  /**
   * @param {object} [opts]
   * @param {number} [opts.slack=0.03]        見積り誤差の許容（inch）
   * @param {number} [opts.overlapSlack=0.02] 重なりとみなす下限（inch）
   */
  constructor(opts = {}) {
    this.slack = opts.slack === undefined ? 0.03 : opts.slack;
    this.overlapSlack = opts.overlapSlack === undefined ? 0.02 : opts.overlapSlack;
    this.overflows = [];
    this.rects = [];
  }

  /** 高さの見積りだけほしいとき。レイアウトを決める前に呼べます。 */
  height(text, widthIn, fontPt, leadPt) {
    return blockHeight(text, widthIn, fontPt, leadPt);
  }

  /** 溢れを1件記録します。need <= have + slack なら何もしません。 */
  fit(where, kind, need, have, text) {
    if (need > have + this.slack) {
      this.overflows.push({ where, kind, need, have, text: String(text || "").slice(0, 44) });
    }
    return need;
  }

  /** 重なり検査のために矩形を登録します（中身は測りません）。 */
  box(page, name, o) {
    this.rects.push({
      page, name, x: o.x, y: o.y, w: o.w, h: o.h,
      text: String(o.text || "").slice(0, 40),
    });
    return this;
  }

  /**
   * 本文の高さを測って枠と突き合わせ、あわせて矩形も登録します。
   * o: { x, y, w, h, text, fontPt, leadPt, padY }
   * 戻り値は見積った必要高さ（inch）です。
   */
  text(page, name, o) {
    const fontPt = o.fontPt || 12;
    const need = blockHeight(o.text, o.w, fontPt, o.leadPt) + (o.padY || 0);
    this.fit(`p${page} ${name}`, "height", need, o.h, o.text);
    this.box(page, name, { x: o.x, y: o.y, w: o.w, h: Math.max(o.h, need), text: o.text });
    return need;
  }

  /** 等幅の塊向け。行数と最長行の両方を見ます。 */
  mono(page, name, o) {
    const fontPt = o.fontPt || 11;
    const need = blockHeight(o.text, 99, fontPt, o.leadPt) + (o.padY || 0);
    this.fit(`p${page} ${name}`, "height", need, o.h, o.text);
    const needW = widestLine(o.text, fontPt, o.emPerChar) + (o.padX || 0);
    this.fit(`p${page} ${name}`, "width", needW, o.w, o.text);
    this.box(page, name, { x: o.x, y: o.y, w: o.w, h: Math.max(o.h, need), text: o.text });
    return need;
  }

  /** 同じページに置いた矩形どうしの重なり。意図した入れ子は除きます。 */
  collisions() {
    const slack = this.overlapSlack;
    const inside = (p, q) =>
      p.x >= q.x - slack && p.y >= q.y - slack &&
      p.x + p.w <= q.x + q.w + slack && p.y + p.h <= q.y + q.h + slack;
    const byPage = {};
    for (const r of this.rects) (byPage[r.page] || (byPage[r.page] = [])).push(r);
    const bad = [];
    for (const [page, list] of Object.entries(byPage)) {
      for (let i = 0; i < list.length; i++) {
        for (let j = i + 1; j < list.length; j++) {
          const a = list[i], b = list[j];
          const ox = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
          const oy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
          if (ox > slack && oy > slack && !inside(a, b) && !inside(b, a)) {
            bad.push({ page, a, b, ox, oy });
          }
        }
      }
    }
    return bad;
  }

  /** 見つかった問題を人が読める形にして返します。空なら合格です。 */
  problems() {
    const lines = [];
    for (const i of this.overflows) {
      lines.push(`  [${i.where}] ${i.kind}: need ${i.need.toFixed(2)}in > have ${i.have.toFixed(2)}in`);
      lines.push(`      "${i.text.replace(/\n/g, "⏎")}"`);
    }
    for (const c of this.collisions()) {
      lines.push(`  [p${c.page}] ${c.a.name} x ${c.b.name} — ${c.ox.toFixed(2)} x ${c.oy.toFixed(2)} in`);
      lines.push(`      "${c.a.text}" / "${c.b.text}"`);
    }
    return lines;
  }

  /** 問題があれば LayoutGateError を投げます。ライブラリとして使うときはこちら。 */
  check() {
    const lines = this.problems();
    if (lines.length) {
      throw new LayoutGateError(
        `layout gate: ${this.overflows.length} overflow(s), ` +
        `${this.collisions().length} collision(s)\n` + lines.join("\n"));
    }
    return true;
  }

  /**
   * 問題を出力して exit 1 します。生成器の書き出し直前に置く用です。
   * 落ちたときにファイルを作らないので、壊れた成果物が残りません。
   */
  enforce() {
    const lines = this.problems();
    if (lines.length) {
      const over = this.overflows.length, hit = this.collisions().length;
      console.error(`\nlayout gate: ${over} overflow(s), ${hit} collision(s)\n`);
      for (const line of lines) console.error(line);
      console.error("\n枠に入りきっていないか、要素が重なっています。");
      console.error("高さを広げるか、本文を短くするか、位置を見直してください。\n");
      process.exit(1);
    }
    console.log(`layout gate: ok (${this.rects.length} boxes)`);
    return true;
  }
}

module.exports = { LayoutGate, LayoutGateError, charEm, lineCount, blockHeight, widestLine, PT };
