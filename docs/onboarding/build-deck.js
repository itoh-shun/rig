const pptxgen = require("pptxgenjs");

const INK = "1C1C1C";
const CANVAS = "FFFFFF";
const TINT = "F1F1EF";
const LINE = "D8D8D4";
const SLATE = "5C6166";
const ACCENT = "1F6F5C";   // gate green — pass, isolation, the safe path
const ALERT = "A33A21";    // refusal — a gate that stops you

const TITLE_F = "Arial";
const BODY_F = "Calibri";
const MONO_F = "Courier New";

const W = 13.3, H = 7.5, M = 0.55;

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
pres.author = "rig";
pres.title = "rig 入門";

let n = 0;

/* ================= layout fit sensor =================================
 * 枠から文字がはみ出したまま出荷しないための計算的センサー。実体は
 * scripts/layout/layout-fit.js の一つだけで、recipes/layout-gate も
 * その file を指す。ここに書くのはその接続だけ。
 * ------------------------------------------------------------------ */
const { LayoutGate, charEm, lineCount, blockHeight, PT } =
  require("../../scripts/layout/layout-fit.js");

const gate = new LayoutGate();
const SLACK = gate.slack;
const fitIssue = (where, kind, need, have, text) => gate.fit(where, kind, need, have, text);
const rect = (page, name, x, y, w, h, text) => gate.box(page, name, { x, y, w, h, text });

function shadow() {
  return { type: "outer", color: "000000", blur: 8, offset: 1, angle: 90, opacity: 0.10 };
}

function footer(slide) {
  slide.addText("rig 入門", {
    x: M, y: 6.95, w: 4, h: 0.3, isTextBox: true, margin: 0,
    fontFace: BODY_F, fontSize: 9, color: SLATE,
  });
  slide.addText(String(n), {
    x: W - M - 1, y: 6.95, w: 1, h: 0.3, isTextBox: true, margin: 0, align: "right",
    fontFace: BODY_F, fontSize: 9, color: SLATE,
  });
}

function base(title, eyebrow) {
  n += 1;
  const slide = pres.addSlide();
  slide.background = { color: CANVAS };
  if (eyebrow) {
    slide.addText(eyebrow, {
      x: M, y: 0.32, w: 11, h: 0.28, isTextBox: true, margin: 0,
      fontFace: BODY_F, fontSize: 11, bold: true, color: ACCENT, charSpacing: 1,
    });
  }
  const titleH = blockHeight(title, W - M * 2, 27, 27 * 1.2);
  if (titleH > 0.72 + SLACK) fitIssue(`s${n} title`, "height", titleH, 0.72, title);
  slide.addText(title, {
    x: M, y: eyebrow ? 0.62 : 0.45, w: W - M * 2, h: 0.72, isTextBox: true, margin: 0,
    fontFace: TITLE_F, fontSize: 27, bold: true, color: INK,
  });
  footer(slide);
  return slide;
}

function dark(title, sub, kicker, titleY) {
  n += 1;
  const slide = pres.addSlide();
  slide.background = { color: INK };
  slide.addText(title, {
    x: M + 0.35, y: titleY === undefined ? 2.2 : titleY, w: W - M * 2 - 0.7, h: 1.5,
    isTextBox: true, margin: 0,
    fontFace: TITLE_F, fontSize: titleY === undefined ? 46 : 38, bold: true, color: CANVAS,
  });
  if (sub) {
    slide.addText(sub, {
      x: M + 0.35, y: 3.7, w: 9.5, h: 1.0, isTextBox: true, margin: 0,
      fontFace: BODY_F, fontSize: 17, color: "C9CCC8", lineSpacing: 26,
    });
  }
  if (kicker) {
    slide.addText(kicker, {
      x: M + 0.35, y: 6.4, w: 11, h: 0.4, isTextBox: true, margin: 0,
      fontFace: BODY_F, fontSize: 11, color: "8A908C",
    });
  }
  return slide;
}

function card(slide, o) {
  rect(n, "card", o.x, o.y, o.w, o.h, o.head || o.body);
  slide.addShape(pres.ShapeType.roundRect, {
    x: o.x, y: o.y, w: o.w, h: o.h, rectRadius: 0.06,
    fill: { color: o.fill || TINT }, line: { color: o.fill ? o.fill : LINE, width: 0.5 },
    shadow: shadow(),
  });
  let ty = o.y + 0.2;
  let tx = o.x + 0.28;
  let tw = o.w - 0.56;
  if (o.num) {
    slide.addShape(pres.ShapeType.ellipse, {
      x: o.x + 0.28, y: o.y + 0.22, w: 0.42, h: 0.42,
      fill: { color: o.numColor || ACCENT }, line: { color: o.numColor || ACCENT, width: 0 },
    });
    slide.addText(o.num, {
      x: o.x + 0.28, y: o.y + 0.22, w: 0.42, h: 0.42, isTextBox: true, margin: 0,
      align: "center", valign: "middle", fontFace: TITLE_F, fontSize: 13, bold: true, color: "FFFFFF",
    });
    tx = o.x + 0.86;
    tw = o.w - 1.14;
  }
  let headH = 0;
  if (o.head) {
    const hs = o.headSize || 14;
    headH = blockHeight(o.head, tw, hs, hs * 1.25);
    slide.addText(o.head, {
      x: tx, y: ty + (o.num ? 0.04 : 0), w: tw, h: headH, isTextBox: true, margin: 0,
      fontFace: TITLE_F, fontSize: hs, bold: true, color: o.headColor || INK,
    });
    ty += headH + 0.07;                 // 実測した見出しの高さで本文を下げる
  }
  if (o.body) {
    const bx = o.num ? tx : o.x + 0.28;
    const bw = o.num ? tw : o.w - 0.56;
    const bs = o.bodySize || 12;
    const bodyH = blockHeight(o.body, bw, bs, o.lead || 17);
    slide.addText(o.body, {
      x: bx, y: ty, w: bw, h: bodyH,
      isTextBox: true, margin: 0, valign: "top",
      fontFace: BODY_F, fontSize: bs, color: o.bodyColor || SLATE, lineSpacing: o.lead || 17,
    });
    const need = (ty - o.y) + bodyH + 0.16;      // 上端から本文の末尾＋下余白
    if (need > o.h + SLACK) {
      fitIssue(`s${n} card`, "height", need, o.h, o.head ? o.head + " / " + o.body : o.body);
    }
  } else if (o.head) {
    const need = 0.2 + headH + 0.16;
    if (need > o.h + SLACK) fitIssue(`s${n} card`, "height", need, o.h, o.head);
  }
}

function codeBox(slide, o) {
  rect(n, "code", o.x, o.y, o.w, o.h, o.text);
  slide.addShape(pres.ShapeType.roundRect, {
    x: o.x, y: o.y, w: o.w, h: o.h, rectRadius: 0.05,
    fill: { color: INK }, line: { color: INK, width: 0 }, shadow: shadow(),
  });
  const cs = o.size || 10.5, cl = o.lead || 15;
  const rows = String(o.text).split("\n");
  const needH = (rows.length * cl) / PT + 0.32;
  if (needH > o.h + SLACK) fitIssue(`s${n} code`, "height", needH, o.h, o.text);
  const widest = Math.max(...rows.map(
    (r) => [...r].reduce((a, c) => a + (charEm(c) === 1 ? 1.0 : 0.6), 0)));
  const needW = widest * (cs / PT) + 0.44;
  if (needW > o.w + SLACK) fitIssue(`s${n} code`, "width", needW, o.w, o.text);
  slide.addText(o.text, {
    x: o.x + 0.22, y: o.y + 0.16, w: o.w - 0.44, h: o.h - 0.32, isTextBox: true, margin: 0,
    valign: "top", fontFace: MONO_F, fontSize: cs, color: "EDEDE8", lineSpacing: cl,
  });
}

function bullets(slide, o) {
  // 箇条書きの実寸を測る。宣言した h は当てにしない。
  const bs = o.size || 12.5, bl = o.lead || 17;
  const gap = o.gap === undefined ? 7 : o.gap;
  const bodyW = o.w - 0.3;                       // 行頭記号のぶんを引く
  const usedH =
    o.items.reduce((a, t) => a + lineCount(t, bodyW, bs) * bl, 0) / PT +
    ((o.items.length - 1) * gap) / PT;
  if (usedH > o.h + SLACK) {
    fitIssue(`s${n} bullets`, "height", usedH, o.h, o.items.join(" / "));
  }
  rect(n, "bullets", o.x, o.y, o.w, Math.max(o.h, usedH), o.items[0]);
  const rows = o.items.map((t, i) => ({
    text: t, options: { bullet: true, breakLine: i !== o.items.length - 1 },
  }));
  slide.addText(rows, {
    x: o.x, y: o.y, w: o.w, h: o.h, isTextBox: true, margin: 0, valign: "top",
    fontFace: BODY_F, fontSize: o.size || 12.5, color: o.color || INK,
    paraSpaceAfter: o.gap === undefined ? 7 : o.gap, lineSpacing: o.lead || 17,
  });
}

function label(slide, o) {
  slide.addText(o.text, {
    x: o.x, y: o.y, w: o.w, h: 0.28, isTextBox: true, margin: 0,
    fontFace: TITLE_F, fontSize: o.size || 12, bold: true, color: o.color || ACCENT, charSpacing: 0.5,
  });
}

function table(slide, o) {
  const head = o.head.map((t) => ({
    text: t,
    options: { bold: true, color: CANVAS, fill: { color: INK }, fontFace: TITLE_F, fontSize: o.hsize || 10.5 },
  }));
  const rows = o.rows.map((r, ri) =>
    r.map((c, ci) => ({
      text: c,
      options: {
        color: ci === 0 ? INK : SLATE,
        bold: ci === 0 && o.boldFirst !== false,
        fontFace: ci === 0 && o.monoFirst ? MONO_F : BODY_F,
        fontSize: o.size || 10.5,
        fill: { color: ri % 2 ? "FFFFFF" : "F7F7F5" },
      },
    }))
  );
  slide.addTable([head, ...rows], {
    x: o.x, y: o.y, w: o.w, colW: o.colW,
    border: { type: "solid", color: LINE, pt: 0.5 },
    rowH: o.rowH || 0.3, valign: "middle",
    margin: [4, 7, 4, 7],
  });
}

/* ------------------------------------------------------------------ 01 */
{
  const s = dark(
    "rig 入門",
    "AI に仕事をおまかせするときの、隔離と検証のしくみ。\nはじめて触る人から、自分の領域を教え込みたい人まで。",
    "典拠：README.ja.md ／ docs/packs.md ／ skills/engine/SKILL.md"
  );
  s.addText("第一部　rig を知る　　|　　第二部　pack で知識層を拡張する", {
    x: M + 0.35, y: 5.5, w: 11, h: 0.4, isTextBox: true, margin: 0,
    fontFace: BODY_F, fontSize: 13, color: "9AA39C",
  });
  s.addNotes("rig をまったく知らない人向けの資料です。前半で安全フローの仕組み、後半で pack による知識層の広げ方を扱います。");
}

/* ================= INTRO BLOCK (inserted) ================= */

/* --- agenda (rebuilt) --- */
{
  const s = base("今日の流れ", "AGENDA");
  const cols = [
    ["導入", "なぜ rig を作ったのか",
      "・よくある AI の使い方\n・そこで開きがちな穴\n・プロンプト／コンテキスト／\n　　ハーネスの入れ子\n・ハーネスの 2×2\n・よくある対応と、その限界\n・rig はそこをどう埋めるか"],
    ["第一部", "rig を知る",
      "・rig とは何か／何ではないか\n・直接頼む場合との違い\n・入口は二つ、最初の三十秒\n・安全と言える四つの理由\n・ゲートと機械のチェック\n・ブリックと recipe\n・レビュアーを測る（drill / stats）\n・コマンドの地図"],
    ["第二部", "pack で知識層を拡張する",
      "・知識層とは何か、なぜ二種類か\n・wiki と inject、tier のしくみ\n・pack の type ＝ 権限\n・作る：init → sync → validate\n・wiki が呼ぶ評価ゲート\n・knowledge ブロックと探し方\n・渡す：bundle / named source\n・つまずきどころと、明日の一歩"],
  ];
  cols.forEach((c, i) => {
    const x = M + i * 4.15;
    card(s, { x, y: 1.5, w: 3.95, h: 4.6, fill: i === 0 ? "E7EFEB" : TINT });
    s.addText(c[0], { x: x + 0.28, y: 1.7, w: 3.4, h: 0.28, isTextBox: true, margin: 0,
      fontFace: BODY_F, fontSize: 11, bold: true, color: ACCENT });
    s.addText(c[1], { x: x + 0.28, y: 1.98, w: 3.4, h: 0.4, isTextBox: true, margin: 0,
      fontFace: TITLE_F, fontSize: 15, bold: true, color: INK });
    s.addText(c[2], { x: x + 0.28, y: 2.5, w: 3.4, h: 3.4, isTextBox: true, margin: 0, valign: "top",
      fontFace: BODY_F, fontSize: 12, color: SLATE, lineSpacing: 22 });
  });
  s.addText("中身はぜんぶ rig リポジトリの資料から取っています。仕様はまだ動くので、引数の正確なところは各コマンドの --help をのぞいてみてください。", {
    x: M, y: 6.3, w: W - M * 2, h: 0.5, isTextBox: true, margin: 0,
    fontFace: BODY_F, fontSize: 10.5, color: SLATE,
  });
}

/* --- intro 01: why --- */
{
  const s = base("なぜ rig を作ったか", "はじめに · 01");
  card(s, { x: M, y: 1.42, w: 12.2, h: 1.2, fill: "E7EFEB" });
  s.addText("オーケストレータが決めるのは「どう動かすか」。rig が決めるのは「出てきたものを受け入れていいか」です。", {
    x: M + 0.35, y: 1.56, w: 11.5, h: 0.55, isTextBox: true, margin: 0,
    fontFace: TITLE_F, fontSize: 16, bold: true, color: INK });
  s.addText("docs/landscape.md にある一行です。rig に機能を足すかどうかは、いつもここに戻って決めています。", {
    x: M + 0.35, y: 2.16, w: 11.5, h: 0.3, isTextBox: true, margin: 0,
    fontFace: BODY_F, fontSize: 11, color: SLATE });

  label(s, { text: "出発点にあった三つの観測", x: M, y: 2.68, w: 6 });
  const obs = [
    ["1", "書く速さだけが上がった", "コードが出てくる速さは、たしかに上がりました。でも、それを受け入れていいかを見るのは、相変わらず人の目だけなんです。"],
    ["2", "「できました」の根拠がない", "モデルが「できました」と言っても、それだけでは終わった証拠になりません。なのに、ほかに手がかりがないことがほとんどでした。"],
    ["3", "失敗が手元に残る", "うまくいかなかった試しが、そのまま手元に残ってしまいます。どれを残してどれを捨てるか、その仕分けだけでもけっこうな手間です。"],
  ];
  obs.forEach((o, i) => {
    card(s, { x: M, y: 3.02 + i * 1.13, w: 6.0, h: 1.0, num: o[0], head: o[1], headSize: 12.5,
      body: o[2], bodySize: 10.5, lead: 13 });
  });

  card(s, { x: M + 6.4, y: 3.02, w: 6.0, h: 1.7, head: "rig が引き受けたのは、ここ一箇所だけ",
    body: "受け入れるかどうかの判断を、人の目から機械のゲートへ移すこと。それだけです。速さや賢さを足す話ではありません。",
    bodySize: 11.5, lead: 16 });
  card(s, { x: M + 6.4, y: 4.9, w: 6.0, h: 1.45, fill: "F3EAE6", head: "ここ、誤解されがちです", headColor: ALERT,
    body: "rig が品質そのものを生んでくれるわけではありません。あなたが決めたルールを AI に飛ばさせない、それだけなんです。ルールを決めるのは、これからも人の仕事です。",
    bodySize: 11.5, lead: 16 });
}

/* --- intro 02: the four common patterns --- */
{
  const s = base("よくある AI の使い方と、そこで起きがちなこと", "はじめに · 02");
  const pats = [
    ["チャットに貼って、返ってきたコードを貼り戻す",
      "どこから来た差分なのかが消えてしまいます。何を変えたかは自分の記憶だけが頼りで、関係ない変更が紛れこんでも気づく場所がありません。"],
    ["エージェントに作業ツリーへ直接書かせる",
      "うまくいかなかった試しが、そのまま手元に残ります。機械のチェックをどこにもつないでいなければ、そのチェックは一度も動きません。"],
    ["CLAUDE.md や AGENTS.md にルールを書く",
      "文章でのお願いは、守られないことがあります。「テストを流してね」と書いておくのと、hook で毎回かならず動かすのとでは、当てにできる度合いがぜんぜん違います。"],
    ["AI にレビューさせる",
      "書いた側と採点する側が似たもの同士だと、どうしても自分に甘くなります。しかも、そのレビュアーがどれくらい見つけられるのかを、誰も測っていません。"],
  ];
  pats.forEach((p, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    card(s, { x: M + col * 6.4, y: 1.42 + row * 1.95, w: 6.0, h: 1.75,
      num: String(i + 1), head: p[0], headSize: 12.5, body: p[1], bodySize: 11, lead: 15 });
  });
  card(s, { x: M, y: 5.35, w: 12.2, h: 1.05, fill: "F3EAE6",
    head: "四つに共通するのは、うまくいったのか分からないことです", headColor: ALERT,
    body: "どれもちゃんと成果は出ます。困るのは失敗する確率ではなくて、失敗しても誰も気づかないまま次へ進んでしまうところなんです。",
    bodySize: 11.5, lead: 15 });
}

/* --- intro 03: the four holes --- */
{
  const s = base("たどっていくと、だいたい同じ四つの穴に行き着きます", "はじめに · 03");
  const holes = [
    ["機械のチェックがループに入っていない", "最頻",
      "テストや lint が「置いてある」だけで、エージェントが回るループの中に入っていません。あることと効いていることは、まったくの別ものです。"],
    ["たしかめる仕組みがない", "自己採点",
      "自分の仕事をたしかめる手段をモデルに渡すと、品質が 2〜3 倍になると言われています（Boris Cherny）。裏を返すと、手段がないエージェントは自分に甘くなってしまうんです。"],
    ["効いているかを測っていない", "測れない",
      "ルールは足したのに、効いたかどうかを見ていません。よかれと思って足したものが、かえって足を引っぱることもあります（Context Rot。文脈が長くなるほど性能が落ちる現象です）。"],
    ["ハーネスが分厚すぎる", "肥大",
      "Thin Harness, Fat Skills（Garry Tan）という言い方があります。ループの管理は薄く、賢さは Skills に、実行は毎回おなじ結果になるツールにおまかせします。親ループが太ってきたら、設計を疑うサインです。"],
  ];
  holes.forEach((h, i) => {
    const y = 1.42 + i * 1.28;
    card(s, { x: M, y, w: 12.2, h: 1.12, num: String(i + 1),
      numColor: i === 0 ? ALERT : ACCENT, head: h[0], headSize: 13, body: h[2], bodySize: 11, lead: 15 });
    s.addText(h[1], { x: M + 10.6, y: y + 0.24, w: 1.4, h: 0.3, isTextBox: true, margin: 0,
      align: "right", fontFace: BODY_F, fontSize: 10.5, bold: true, color: i === 0 ? ALERT : SLATE });
  });
  s.addText("出どころは skills/engine/facets/knowledge/harness-taxonomy.md。rig 自身が /rig:harness で使っている観点のカタログです。", {
    x: M, y: 6.6, w: 12.2, h: 0.3, isTextBox: true, margin: 0, fontFace: BODY_F, fontSize: 10, color: SLATE });
}

/* --- intro 04: ○○ engineering --- */
{
  const s = base("プロンプトの外にコンテキスト、その外にハーネスがあります", "はじめに · 04");
  s.addText("「〇〇エンジニアリング」は別々の流派ではなくて、入れ子になっています。外側を放っておいて内側だけ磨いても、効き目が安定しないんです。", {
    x: M, y: 1.4, w: 12.2, h: 0.35, isTextBox: true, margin: 0,
    fontFace: BODY_F, fontSize: 12.5, color: INK });

  s.addShape(pres.ShapeType.roundRect, { x: M, y: 1.85, w: 6.6, h: 4.55, rectRadius: 0.06,
    fill: { color: TINT }, line: { color: LINE, width: 0.5 }, shadow: shadow() });
  s.addText("ハーネスエンジニアリング", { x: M + 0.3, y: 2.0, w: 6.0, h: 0.3, isTextBox: true, margin: 0,
    fontFace: TITLE_F, fontSize: 14, bold: true, color: INK });
  s.addText("ループ・ツール・強制・検証・記録まで、ぜんぶ含めた仕組み", {
    x: M + 0.3, y: 2.3, w: 6.0, h: 0.3, isTextBox: true, margin: 0,
    fontFace: BODY_F, fontSize: 10.5, color: SLATE });

  s.addShape(pres.ShapeType.roundRect, { x: M + 0.42, y: 2.7, w: 5.76, h: 3.5, rectRadius: 0.06,
    fill: { color: "E7EFEB" }, line: { color: "E7EFEB", width: 0 } });
  s.addText("コンテキストエンジニアリング", { x: M + 0.72, y: 2.85, w: 5.2, h: 0.3, isTextBox: true, margin: 0,
    fontFace: TITLE_F, fontSize: 14, bold: true, color: INK });
  s.addText("何をどの順で見せるか。知識層と注入、それに圧縮への強さ", {
    x: M + 0.72, y: 3.15, w: 5.2, h: 0.3, isTextBox: true, margin: 0,
    fontFace: BODY_F, fontSize: 10.5, color: SLATE });

  s.addShape(pres.ShapeType.roundRect, { x: M + 0.84, y: 3.55, w: 4.92, h: 2.45, rectRadius: 0.06,
    fill: { color: INK }, line: { color: INK, width: 0 } });
  s.addText("プロンプトエンジニアリング", { x: M + 1.14, y: 3.7, w: 4.4, h: 0.3, isTextBox: true, margin: 0,
    fontFace: TITLE_F, fontSize: 14, bold: true, color: CANVAS });
  s.addText("一回の指示の書き方です。ここだけを磨く人が一番多いのですが、\n条件が変わると、同じようには効いてくれません。\n\nrig はプロンプト面（persona・instruction・recipe・wiki）を\n「コンパイラが見てくれない場所」として扱います。\n変えるときは、承認ずみの評価ケースをお願いしています。",
    { x: M + 1.14, y: 4.02, w: 4.4, h: 1.8, isTextBox: true, margin: 0, valign: "top",
      fontFace: BODY_F, fontSize: 10.5, color: "C9CCC8", lineSpacing: 14 });

  card(s, { x: M + 7.0, y: 1.85, w: 5.2, h: 2.15, head: "ハーネスは二つの層に分かれます",
    body: "一つめはエージェントハーネス。Claude Code や Codex に最初から入っているループ・ツール・メモリのことです。二つめはユーザーハーネスで、こちらは使う側が組みます。CLAUDE.md・Skills・Hooks・MCP・テスト・lint・CI・recipe あたりですね。",
    bodySize: 11.5, lead: 16 });
  card(s, { x: M + 7.0, y: 4.15, w: 5.2, h: 1.25, fill: "E7EFEB", head: "rig が作るのは、二つめだけ",
    body: "内蔵のループには手を出しません。その外側に、薄い品質・安全の層としてそっと乗ります。",
    bodySize: 11.5, lead: 16 });
  card(s, { x: M + 7.0, y: 5.55, w: 5.2, h: 1.05, head: "だから engine はさわりません",
    body: "拡張は pack として上に乗せます。",
    bodySize: 11.5, lead: 16 });
}

/* --- intro 05: the 2x2 --- */
{
  const s = base("2×2 で並べてみると、足りない場所が見えてきます", "はじめに · 05");
  s.addText("軸は二つだけです。①コードで判定する計算的なものか、LLM が判断する推論的なものか。②先回りして方向づけるガイドか、あとから気づかせるセンサーか。", {
    x: M, y: 1.4, w: 12.2, h: 0.35, isTextBox: true, margin: 0,
    fontFace: BODY_F, fontSize: 12, color: INK });
  table(s, {
    x: M, y: 1.85, w: 8.3, colW: [1.9, 3.2, 3.2], rowH: 1.05, size: 11,
    head: ["", "ガイド（先回り・方向づけ）", "センサー（事後・検知）"],
    rows: [
      ["計算的\n（決定論的）", "LSP・型・CLI・コードモッド\nscaffold・テンプレート", "lint・型チェック・テスト\nbuild・ArchUnit・CI"],
      ["推論的\n（LLM 判断）", "CLAUDE.md / AGENTS.md\nSkills・設計ドキュメント・persona", "AI コードレビュー\nLLM-as-judge・review-gate"],
    ],
  });
  card(s, { x: M + 8.6, y: 1.85, w: 3.6, h: 1.55, head: "四つそろって、はじめて効きます",
    body: "レビューはするけどテストがない。テストは通るけど設計を誰も見ていない。どちらも同じくらい穴です。",
    bodySize: 11, lead: 15 });
  card(s, { x: M + 8.6, y: 3.5, w: 3.6, h: 1.55, fill: "E7EFEB", head: "まずは機械のチェックから",
    body: "機械のチェックは口説き落とせません。だからいちばん強い歯止めになります。AI のレビューは、その次に置くのがおすすめです。",
    bodySize: 11, lead: 15 });
  card(s, { x: M + 8.6, y: 5.05, w: 3.6, h: 1.6, fill: "F3EAE6", head: "「ある」と「効いている」は別もの", headColor: ALERT,
    body: "どの hook にも acceptance gate にもつながっていない lint は、ループに何の圧力もかけていません。",
    bodySize: 11, lead: 15 });
  card(s, { x: M, y: 5.15, w: 8.3, h: 1.45, head: "この 2×2 は、自分のプロジェクトにも当てられます",
    body: "/rig:harness が読み取り専用でぐるっと見わたして、空いている象限と、あるのに効いていない資産を教えてくれます。やることは新しいルールを足すことではなく、つなぐ・効かせる・減らすの三つです。",
    bodySize: 11.5, lead: 16 });
}

/* --- intro 06: usual responses and their limits --- */
{
  const s = base("よくある対応と、それがどこで止まってしまうか", "はじめに · 06");
  table(s, {
    x: M, y: 1.42, w: 7.6, colW: [2.6, 5.0], rowH: 0.66, size: 10.5, boldFirst: true,
    head: ["よく採られる対応", "どこで止まるか"],
    rows: [
      ["プロンプトを磨く", "条件が変わると同じようには効きません。測っていないので、良くなったのかどうかも分かりません"],
      ["CLAUDE.md にルールを足す", "文章でのお願いは、守られないことがあります。足しすぎると文脈が濁って、かえって逆効果です"],
      ["lint / test / CI を用意する", "置いただけでは、エージェントのループの外にあります。人が CI を待つまで、誰も見ていません"],
      ["AI レビューを足す", "書いた側と似たモデルだと甘くなります。どれくらい見つけられるか分からないので、通っても安心できません"],
      ["人のレビューを増やす", "人手が続きません。形だけになってしまっても、そうなったこと自体に気づけません"],
    ],
  });
  label(s, { text: "効く順番があります", x: M + 7.9, y: 1.42, w: 4.8 });
  const order = [
    ["つなぐ", "すでにある lint・型・テストを hook と acceptance gate につないで、ループの中へ入れます"],
    ["効かせる", "大事なルールを、文章でのお願いから、かならず動く仕組みへ移します"],
    ["減らす", "効いていないルールは落とします。足すときは、減らす候補もセットで考えておきます"],
  ];
  order.forEach((o, i) => {
    card(s, { x: M + 7.9, y: 1.78 + i * 1.28, w: 4.85, h: 1.15, num: String(i + 1),
      head: o[0], headSize: 12.5, body: o[1], bodySize: 10.5, lead: 13 });
  });
  card(s, { x: M, y: 5.4, w: 7.6, h: 1.15, fill: "E7EFEB", head: "新しいルールを書くのは、いちばん最後",
    body: "よかれと思って足したルールが、かえって邪魔をすることがあります。まずつないで、次に効かせて、そのうえで減らしてみてください。",
    bodySize: 11.5, lead: 15 });
  card(s, { x: M + 7.9, y: 5.55, w: 4.85, h: 1.05, fill: "F3EAE6", head: "測らないと、この順番も回せません", headColor: ALERT,
    body: "何が効いたのか分からないままだと、この三つをぐるぐる回すこと自体ができません。",
    bodySize: 10.5, lead: 13 });
}

/* --- intro 07: what rig actually does --- */
{
  const s = base("rig は、この四つをこうやって埋めます", "はじめに · 07");
  const quad = [
    ["計算的ガイド", "hook・manifest（.claude/rig.md）・recipe・scaffold です。走り出す前に、進め方と初期値を決めておきます。"],
    ["計算的センサー", "acceptance-gate の機械チェック（build / lint / test / 型）と、毎回おなじ結果になるセンサー（秘密情報・インジェクション・危ないコマンド・ゲートの書き換え・schema-diff）です。"],
    ["推論的ガイド", "persona・instruction・知識層の wiki です。その領域の前提を、判断する側にちゃんと持たせます。"],
    ["推論的センサー", "reviewer persona による同時レビューです。読み取り専用をプロセスの段階で固定して、drill でどれくらい見つけられるかを実際に測ります。"],
  ];
  quad.forEach((q, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    card(s, { x: M + col * 4.25, y: 1.42 + row * 1.75, w: 4.05, h: 1.6,
      fill: i === 1 ? "E7EFEB" : TINT, head: q[0], headSize: 13, body: q[1], bodySize: 10.5, lead: 14 });
  });
  card(s, { x: M, y: 4.95, w: 8.3, h: 1.45, head: "この 2×2 の外にも、rig ならではのものが二つ",
    body: "一つめは隔離。判定が終わるまで、できたものは作業ツリーに触れません。二つめは記録です。何を試して、どの基準で、なぜ受け入れた（あるいは断った）かが、run log と監査の記録に残ります。",
    bodySize: 11.5, lead: 16 });

  card(s, { x: M + 8.6, y: 1.42, w: 3.6, h: 3.55, fill: "F3EAE6", head: "やらないと決めていること", headColor: ALERT,
    body: "・IDE や GUI をつくること\n・汎用エージェント群のプラットフォーム\n・複数モデルの答えを混ぜて一つにすること\n・ワークフロー DSL の表現力くらべ\n\n複数のモデルを使うのは、検証役を書き手から切り離すためです。答えを混ぜ合わせるためではありません。",
    bodySize: 11, lead: 16 });
  card(s, { x: M + 8.6, y: 5.05, w: 3.6, h: 1.4, head: "逆に、めざしていること",
    body: "ほかのオーケストレータが作ったものにも、同じ受け入れの約束を当てられるようにすること（workbench.py import）。",
    bodySize: 11, lead: 15 });
}

/* --- part 1 divider --- */
{
  dark("第一部　rig を知る",
    "ここからは、その仕組みが実際にどう動くのかを見ていきます。\n分類、隔離、ゲート、受け入れの順番です。",
    "README.ja.md §5〜§12");
}

/* ================= END INTRO BLOCK ================= */

/* ------------------------------------------------------------------ 03 */
{
  const s = base("rig とは何か", "N° 01");
  s.addText("困るのは、AI が間違えることではないんです。間違えたことに誰も気づかないまま、本体に混ざってしまうこと。そこが怖いところです。", {
    x: M, y: 1.42, w: W - M * 2, h: 0.4, isTextBox: true, margin: 0,
    fontFace: TITLE_F, fontSize: 15, bold: true, color: INK,
  });

  const steps = [
    ["分類", "bugfix / feature / refactor\n/ review / documentation …"],
    ["recipe えらび", "選んだ理由を\n先に一行で見せる"],
    ["隔離 worktree", "作業ツリーとは別の\n使い捨てブランチ"],
    ["acceptance-gate", "機械的な基準で\n合否を判定"],
    ["accept / discard", "反映は staged まで。\n決めるのはいつも人"],
  ];
  const cw = 2.32, gap = 0.16;
  steps.forEach((st, i) => {
    const x = M + i * (cw + gap);
    card(s, { x, y: 1.95, w: cw, h: 1.65, num: String(i + 1), head: st[0], headSize: 12.5 });
    s.addText(st[1], {
      x: x + 0.28, y: 2.85, w: cw - 0.56, h: 0.65, isTextBox: true, margin: 0,
      fontFace: BODY_F, fontSize: 10, color: SLATE, lineSpacing: 13,
    });
  });

  label(s, { text: "最初に外しておく誤解", x: M, y: 3.85, w: 6 });
  card(s, {
    x: M, y: 4.2, w: 3.93, h: 1.9, head: "品質を自動で生む道具ではありません",
    body: "あなたが決めた品質のルールを、AI に飛ばさせないための道具です。ルールを決めるのは人の仕事のまま。rig がやるのは、守らせることと測ることだけです。",
  });
  card(s, {
    x: M + 4.13, y: 4.2, w: 3.93, h: 1.9, head: "そのかわり、払うものがあります",
    body: "隔離と検証と記録のために、速さとトークンをわざと差し出しています。とにかく速く書かせたいだけなら、モデルに直接お願いするほうが早いです。",
  });
  card(s, {
    x: M + 8.26, y: 4.2, w: 3.94, h: 1.9, head: "効いてくる場面", fill: "E7EFEB",
    body: "失敗したときの痛手が、速さより大きい場面です。本番に触れるコード、ほかの人が読むコード、あとで誰も見直さないコードあたりですね。",
  });
}

/* ------------------------------------------------------------------ 04 */
{
  const s = base("直接頼む場合と、何が違うか", "N° 02");
  table(s, {
    x: M, y: 1.5, w: 7.6, colW: [1.9, 2.6, 3.1],
    head: ["観点", "モデルに直接頼む", "rig 経由"],
    rows: [
      ["失敗した変更", "作業ツリーに残る", "worktree ごと破棄、本体は無傷"],
      ["「できました」", "信じるしかない", "acceptance-gate の合否が根拠"],
      ["レビューの質", "不明", "検出率を drill で実測できる"],
      ["何が起きたか", "チャットログ", "run log・監査証跡・署名つき来歴"],
      ["並列実行", "同じツリーを取り合う", "task ごとに別 worktree／別 branch"],
      ["中断・圧縮", "静かに素の作業へ戻る", "状態ヘッダに再アンカーする"],
    ],
    rowH: 0.42,
  });
  card(s, {
    x: M + 7.9, y: 1.5, w: 4.3, h: 2.55, head: "いま、どこまで動いているか",
    body: "安全のいちばん芯になる部分は、もう動いています。分類・隔離・acceptance-gate・自分で選ぶ accept と discard。リポジトリ自身のテストで裏づけもあります。\n\nその上に乗る観測まわり（drill・board・stats・GitHub 連携）は使えますが、まだ育てている最中です。",
    bodySize: 11.5, lead: 16,
  });
  card(s, {
    x: M + 7.9, y: 4.2, w: 4.3, h: 1.9, fill: "F3EAE6", head: "正直に書いていること", headColor: ALERT,
    body: "このプロジェクトは、まだ出していない機能を「予定」として載せない方針です。表に無いコマンドは、まだ世に出ていません。",
    bodySize: 11.5, lead: 16,
  });
  card(s, {
    x: M, y: 4.65, w: 7.6, h: 1.75, head: "task ごとに閉じていると、こんなに楽です",
    body: "タスクを何本か同時に走らせても、それぞれ別の worktree と branch なので安心です。/rig:queue に積んでまとめて GO しても、プロセス同士がファイルを取り合うことはありません。終わったら /rig:go board を見てください。どの端末のどのプロセスが動かしたかに関係なく、全部の状態が一つの表に並びます。",
    bodySize: 11.5, lead: 16,
  });
}

/* ------------------------------------------------------------------ 05 */
{
  const s = base("入口は二つ。中身は同じエンジン", "N° 03");
  card(s, { x: M, y: 1.45, w: 6.0, h: 2.35, num: "A", head: "Claude Code の中では、プラグイン",
    body: "スラッシュコマンドはここから来ます。安全なひと通りの流れは、これだけで動きます。", bodySize: 11.5 });
  codeBox(s, { x: M + 0.86, y: 2.78, w: 5.0, h: 0.78,
    text: "/plugin marketplace add itoh-shun/sito-plugins\n/plugin install rig@sito-plugins", size: 10 });

  card(s, { x: M + 6.4, y: 1.45, w: 6.0, h: 2.35, num: "B", head: "それ以外の場所では、rig-wb CLI",
    body: "CI やスクリプト、ほかのアシスタント（Codex / Cursor）からでも、同じ recipe とゲートを回せます。", bodySize: 11.5 });
  codeBox(s, { x: M + 7.26, y: 2.78, w: 5.0, h: 0.78,
    text: "pipx install git+https://github.com/itoh-shun/rig.git\nrig-wb version", size: 10 });

  s.addText("両方そろえる必要はありません。二つめが要るのは、Claude Code の外にあるものを同じ recipe とゲートに通したいときだけです。/rig:setup を使えば、Claude Code の中から入れられます。", {
    x: M, y: 3.92, w: W - M * 2, h: 0.32, isTextBox: true, margin: 0,
    fontFace: BODY_F, fontSize: 11, color: SLATE,
  });

  label(s, { text: "最初の三十秒。設定はゼロのままで大丈夫です", x: M, y: 4.34, w: 8 });
  codeBox(s, { x: M, y: 4.6, w: 6.0, h: 1.9,
    text: '/rig:go "ログインバグを直して"\n/rig:go "このPRを厳しめにレビューして"\n/rig:go "今の変更が安全か確認して"\n\n/rig:go diff      # 何が変わったか\n/rig:go accept    # 反映（gate 未達なら拒否）\n/rig:go discard   # 破棄', size: 10.5, lead: 16 });
  codeBox(s, { x: M + 6.4, y: 4.6, w: 6.0, h: 1.9,
    text: "▸ rig\ntask:     ログインバグを直して\ndetected: bugfix\nrecipe:   bugfix — 「バグ」「直して」を検出\nmode:     isolated worktree\ngate:     standard + bugfix", size: 10.5, lead: 16 });
  s.addText("manifest も gates.json も persona の設定も要りません。ぜんぶ後から足せます", {
    x: M, y: 6.56, w: 6, h: 0.3, isTextBox: true, margin: 0, fontFace: BODY_F, fontSize: 10, color: SLATE });
  s.addText("どうしてその段取りにしたのかを、走り出す前に一行で見せてくれます", {
    x: M + 6.4, y: 6.56, w: 6, h: 0.3, isTextBox: true, margin: 0, fontFace: BODY_F, fontSize: 10, color: SLATE });
}

/* ------------------------------------------------------------------ 06 */
{
  const s = base("rig が安全だと言える、四つの理由", "N° 04");
  s.addText("安全だと言えるのは、次の四つが文章ではなく仕組みとして組み込まれているからです。", {
    x: M, y: 1.4, w: 11, h: 0.3, isTextBox: true, margin: 0, fontFace: BODY_F, fontSize: 12.5, color: INK });
  const items = [
    ["1", "隔離された worktree", "タスクごとに、専用の worktree と使い捨てのブランチを作ります。rig があなたの作業ツリーに直接書き込むことはありません。失敗しても途中でやめても、手元は汚れないままです。タスクごとに閉じているので、同時に走らせても大丈夫です。"],
    ["2", "acceptance-gate", "「完了しました」と言われても、それだけでは終わりになりません。関係ない差分・テスト・型・リスクの書きぶり・秘密情報を機械が見て、そこではじめて反映の候補になります。failed か pending が一件でも残っていたら、accept ははじかれます（exit 1）。"],
    ["3", "read-only な検証役", "書く AI と、たしかめる AI を分けます。たしかめる側はプロセスの段階で読み取り専用に固定されます。お願いではなく、そうとしか動けない状態です。判断のいちばんの根拠は、本人の申告ではなく worktree の実際の git diff です。"],
    ["4", "明示的な accept と、消えない記録", "accept は squash merge で staged（コミット前）まで進めます。コミットするのは、いつも人です。discard しても run log は残るので、何を試してなぜ捨てたのかを後から追えます。土台になる前提は --force でも通り抜けられません。"],
  ];
  items.forEach((it, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    card(s, {
      x: M + col * 6.4, y: 1.85 + row * 2.25, w: 6.0, h: 2.05,
      num: it[0], head: it[1], body: it[2], bodySize: 11.5, lead: 16,
    });
  });
}

/* ------------------------------------------------------------------ 07 */
{
  const s = base("① 隔離された worktree", "N° 05");
  bullets(s, {
    x: M, y: 1.45, w: 5.9, h: 2.3,
    items: [
      "タスクごとに専用の git worktree と使い捨てブランチを作ります",
      "レビューや調査のような読み取り専用のタスクは、--no-worktree で worktree ごと省略できます",
      "discard は worktree と branch を消しますが、run log（.rig/runs/）は残ります",
      "並列で実行しても別の worktree と branch なので、プロセス同士がファイルを取り合いません",
    ],
    size: 12.5,
  });
  codeBox(s, {
    x: M + 6.2, y: 1.45, w: 6.2, h: 2.55, size: 10, lead: 14,
    text: "<repoの親>/rig-worktrees/<repo名>/\n    rig-20260704-153012-login-fix/   ← 使い捨て\n\n<repo>/.rig/runs/rig-20260704-153012-login-fix/\n    task.json        入力 / recipe / base branch\n    steps.json       step ごとの進行状態\n    acceptance.json  基準ごとの合否と根拠\n    diff.md          Summary / Risk / Tests",
  });
  label(s, { text: "複数タスクを同時に走らせる", x: M, y: 4.15, w: 8 });
  codeBox(s, {
    x: M, y: 4.5, w: 6.9, h: 1.6, size: 10, lead: 15,
    text: '/rig:queue add "ログイン画面のバグを直して"\n/rig:queue add "在庫一覧に検索機能を追加して"\n/rig:queue go --provider rig --max-parallel 3',
  });
  card(s, {
    x: M + 7.2, y: 4.5, w: 5.2, h: 1.6, head: "queue は accept まではやりません",
    body: "queue の verifier が見るのは二つだけです。gate が決まったかどうかと、worktree の中で完結して本体に書いていないかどうか。反映するかどうかは、ちゃんと人の手元に残ります。終わったら /rig:go board で見てみてください。",
    bodySize: 11.5, lead: 16,
  });
}

/* ------------------------------------------------------------------ 08 */
{
  const s = base("② acceptance-gate は standard ＋ 種別プリセット", "N° 06");
  table(s, {
    x: M, y: 1.42, w: 8.3, colW: [1.35, 6.95], monoFirst: true,
    head: ["preset", "基準の例"],
    rows: [
      ["standard", "task_intent_satisfied / no_unrelated_diff / diff_summary_written / risk_summary_written /\ntests_pass_or_explained / no_secret_leak / no_gate_tampering / no_injection_markers"],
      ["bugfix", "bug_cause_identified / fix_is_minimal / regression_test_added_or_explained /\nexisting_behavior_preserved / no_unrelated_refactor"],
      ["feature", "requirement_summary_written / implementation_matches_requirement /\ntests_added_or_explained / public_api_changes_documented"],
      ["refactor", "behavior_boundaries_identified / no_unintended_behavior_change /\ntests_confirm_behavior_preserved"],
      ["review", "findings_are_concrete / severity_labeled / file_references_included /\nfalse_positive_risk_considered"],
      ["security", "authn_authz_impact_checked / user_input_flow_checked / secret_exposure_checked /\ndependency_risk_checked"],
    ],
    rowH: 0.62, size: 9.5,
  });
  card(s, {
    x: M + 8.6, y: 1.42, w: 3.6, h: 2.15, head: "状態は四つあります",
    body: "一つひとつの基準は、根拠つきで passed / failed / warning / skipped として残ります。全体としては passed / passed_with_warnings / failed / pending にまとまります。",
    bodySize: 11, lead: 15,
  });
  card(s, {
    x: M + 8.6, y: 3.7, w: 3.6, h: 2.5, fill: "F3EAE6", head: "accept が止まるのはこのとき", headColor: ALERT,
    body: "failed か pending が一件でも残っていたら、accept ははじかれます（exit 1）。\n\nwarning は accept を止めません。ただし必ず見せてくれるので、こっそり握りつぶされることはありません。",
    bodySize: 11, lead: 15,
  });
  s.addText("元になっているのは scripts/workbench.py gates です。プロジェクト側は .rig/gates.json の extra_criteria から基準を足せます。", {
    x: M, y: 6.35, w: 8.3, h: 0.3, isTextBox: true, margin: 0, fontFace: BODY_F, fontSize: 10, color: SLATE });
}

/* ------------------------------------------------------------------ 09 */
{
  const s = base("基準は、機械が裏づけてくれます", "N° 07");
  const sensors = [
    ["no_secret_leak", "task diff に、毎回おなじ結果になるシークレットスキャンをかけます。見つかれば failed です。抜粋はいつもマスクされます。"],
    ["no_injection_markers", "文章の中のインジェクション・マーカーを探します。目に見えない文字や bidi Unicode は fail、指示を上書きしようとする言い回しは warning です。"],
    ["no_destructive_operation", "危ないコマンドを探します。rm -rf / ・mkfs・DROP DATABASE は fail、force push や TRUNCATE は warning です。"],
    ["no_gate_tampering", "gates.json・recipes・CI workflow に手を入れていたら fail です。今あるテストの書き換え・assert 削除・skip 追加は warning になります。"],
    ["public_api_changes_documented", "OpenAPI の schema-diff を取ります。API が変わったのに diff サマリに何も書かれていなければ warning にします。"],
    ["prompt_regression_passed", "diff が prompt 面に触れたときだけ、自動で足されます。--set での手動の上書きを断る、唯一の基準です。"],
  ];
  sensors.forEach((sn, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    const x = M + col * 6.4, y = 1.42 + row * 1.35;
    card(s, { x, y, w: 6.0, h: 1.18, head: sn[0], headSize: 11.5, body: sn[1], bodySize: 10.5, lead: 14 });
  });
  card(s, {
    x: M, y: 5.5, w: 12.2, h: 1.15, fill: "E7EFEB", head: "設定は足す方向にしか動きません",
    body: "プロジェクトは .rig/gates.json から独自の基準を足せます。ただし、もとからある基準を消したりゆるめたりするキーは、その場ではじかれます。リポジトリの中のファイルが、そのリポジトリを守っているゲートをゆるめることはできない、ということですね。",
    bodySize: 11.5, lead: 15,
  });
}

/* ------------------------------------------------------------------ 10 */
{
  const s = base("③ read-only な検証役　／　④ 明示的な accept", "N° 08");
  label(s, { text: "③  検証役はプロセスレベルで読み取り専用", x: M, y: 1.4, w: 6 });
  codeBox(s, { x: M, y: 1.72, w: 6.0, h: 0.8, size: 10.5,
    text: "claude --allowedTools Read,Grep,Glob\ncodex  --sandbox read-only" });
  bullets(s, {
    x: M, y: 2.7, w: 6.0, h: 2.0,
    items: [
      "見て、grep して、指摘を書くことはできます",
      "編集も commit も、formatter での書き換えもできません",
      "いちばんの根拠は worktree の実際の git diff です。書いた側のレポートは「まだ確かめていない主張」として渡るだけです",
      "この制限がちゃんと効いていることは、orchestrate.py probe がプロバイダごとに確かめてくれます",
    ],
    size: 11.5,
  });

  label(s, { text: "④  accept は staged で止まり、記録も消えません", x: M + 6.4, y: 1.4, w: 6 });
  codeBox(s, { x: M + 6.4, y: 1.72, w: 6.0, h: 1.55, size: 10, lead: 14,
    text: "## rig accept — accept_requirements\n  ✓ worktree_exists            構造的\n  ✓ base_branch_recorded       構造的\n  ✓ diff_summary_generated     構造的\n  ✓ acceptance_gate_not_failed 上書き可\n  ✓ no_unrelated_diff          上書き可" });
  bullets(s, {
    x: M + 6.4, y: 3.4, w: 6.0, h: 1.75,
    items: [
      "土台になる前提は --force でも通りません。diff.md が無ければ accept できません",
      "上書きしたときは forced: true として残ります。あとから消すことはできません",
      "反映は squash merge の staged まで。コミットするのはいつも人です",
      "discard は変更するファイルの一覧を必ず先に見せて、--yes を待ちます",
    ],
    size: 11.5,
  });
  label(s, { text: "中断しても、黙って素の作業には戻りません", x: M, y: 4.9, w: 6 });
  codeBox(s, { x: M, y: 5.22, w: 6.0, h: 0.95, size: 9, lead: 13,
    text: "▸ rig | task: rig-20260704-153012-login-fix\n      | recipe: bugfix | step: test (4/7)\n      | gate: pending | mode: isolated worktree" });
card(s, { x: M + 6.4, y: 5.2, w: 6.0, h: 1.3, head: "文脈が圧縮されても消えません",
    body: "PreCompact フックが、run-state を守る指示をそっと差し込みます。/rig:init を使えば、同じ文を CLAUDE.md の Compact Instructions にも置けます。",
    bodySize: 11, lead: 14 });
}

/* ------------------------------------------------------------------ 11 */
{
  const s = base("中身は四種類のブリック。LEGO みたいに組み合わせます", "N° 09");
  const bricks = [
    ["persona", "誰が判定するか", "security-reviewer / design-reviewer / test-reviewer …"],
    ["instruction", "何をするか", "手順そのものです。薄いまま保たれて、エンジンには触れません"],
    ["pattern", "どう分配し、どうゲートするか", "isolated-worktree / acceptance-gate / serial"],
    ["recipe", "step の束", "bugfix / feature / review-only / release-flow / hotfix"],
  ];
  bricks.forEach((b, i) => {
    const x = M + i * 3.1;
    card(s, { x, y: 1.42, w: 2.9, h: 2.0, head: b[0], headSize: 15, headColor: ACCENT,
      body: b[1] + "\n\n" + b[2], bodySize: 11, lead: 15 });
  });
  s.addText("/rig:go はこの組み立てを自動でやってくれます。/rig:dev のほうは、recipe も step も flag も自分で指定したい人向けの入口です。", {
    x: M, y: 3.6, w: 12.2, h: 0.3, isTextBox: true, margin: 0, fontFace: BODY_F, fontSize: 12, color: INK });
  codeBox(s, { x: M, y: 4.0, w: 6.4, h: 1.55, size: 10, lead: 15,
    text: '/rig:dev --plan --only review "現在の変更"\n/rig:dev --recipe release-flow --design "機能X"\n/rig:dev --recipe hotfix --issue 1234\n/rig:dev --list        # 全 tier の recipe' });
  table(s, {
    x: M + 6.8, y: 4.0, w: 5.4, colW: [1.9, 3.5], monoFirst: true, size: 10, rowH: 0.31,
    head: ["主な flag", "意味"],
    rows: [
      ["--adversarial", "敵対的レビュー step を追加"],
      ["--persona <name>", "カスタム reviewer を fan-out に追加"],
      ["--verify-findings", "REJECT 根拠を独立に敵対的検証"],
      ["--autonomous", "step ゲートを省略（acceptance-gate は残る）"],
    ],
  });
  s.addText("flag とブリックの全部の一覧は skills/engine/SKILL.md にあります。README にはあえて写していません。二か所に書くと、いつかずれてしまうからです。", {
    x: M, y: 5.75, w: 12.2, h: 0.3, isTextBox: true, margin: 0, fontFace: BODY_F, fontSize: 10, color: SLATE });
}

/* ------------------------------------------------------------------ 12 */
{
  const s = base("レビュアーの実力は、ちゃんと測れます", "N° 10");
  s.addText("reviewer persona は、テストできます。わざと仕込んだバグを使い捨ての diff に入れてレビューを走らせ、reviewer には見せていない答え合わせ用のキーと照らして採点します。", {
    x: M, y: 1.4, w: 12.2, h: 0.35, isTextBox: true, margin: 0, fontFace: BODY_F, fontSize: 12.5, color: INK });
  const stats = [["82%", "検出率"], ["12%", "誤検知率"], ["76%", "重大度の精度"], ["81%", "Blocking 判断"]];
  stats.forEach((st, i) => {
    const x = M + i * 2.0;
    card(s, { x, y: 1.9, w: 1.85, h: 1.35, fill: "E7EFEB" });
    s.addText(st[0], { x, y: 2.02, w: 1.85, h: 0.6, isTextBox: true, margin: 0, align: "center",
      fontFace: TITLE_F, fontSize: 30, bold: true, color: ACCENT });
    s.addText(st[1], { x, y: 2.66, w: 1.85, h: 0.3, isTextBox: true, margin: 0, align: "center",
      fontFace: BODY_F, fontSize: 11, color: SLATE });
  });
  codeBox(s, { x: M + 8.3, y: 1.9, w: 3.9, h: 1.35, size: 9.5, lead: 13,
    text: "## Missed Issues\n1. SQL injection in search query\n     (src/search.py:88)\n2. Missing authorization check\n     (src/api/users.py:120)" });

  card(s, { x: M, y: 3.45, w: 6.0, h: 2.6, head: "persona の直し方は、四種類に決めてあります",
    body: "add_checklist_item ／ adjust_severity_rule ／ add_false_positive_guard ／ strengthen_security_focus の四つ。\n\nふんわりした感想ではなく、run をまたいで数えられる形にしたいからです。--replay を使うと、persona を直したあとに保存ずみの diff へもう一度かけて、前と後の verdict を並べて見せてくれます。本物のコードには一切触れません。",
    bodySize: 11.5, lead: 16 });
  label(s, { text: "形だけのレビューも、ちゃんと教えてくれます（/rig:go stats）", x: M + 6.4, y: 3.45, w: 6 });
  codeBox(s, { x: M + 6.4, y: 3.78, w: 5.8, h: 2.27, size: 10, lead: 15,
    text: "Verifier behavior:\n- strict_senior_engineer: 14 runs, 6 rejects\n- product_reviewer:        6 runs, 0 rejects\n\nWarning:\nproduct_reviewer has 0 rejects across 6 runs.\nPossible rubber-stamp behavior." });
}

/* ------------------------------------------------------------------ 13 */
{
  const s = base("コマンド一覧。上から順に、必要になったら覚えれば大丈夫", "N° 11");
  table(s, {
    x: M, y: 1.45, w: 12.2, colW: [1.6, 10.6],
    head: ["tier", "コマンド"],
    rows: [
      ["Core", "/rig:go \"<タスク>\"　・　/rig:talk（会話的な入口）　・　/rig:dev（明示的な入口）　・　status / diff / accept / discard / log"],
      ["観測", "/rig:go board（複数 run の管制塔）　・　cockpit（Mission Control・read-only）　・　stats（過去 run の集計）"],
      ["Quality", "/rig:drill（reviewer の検出率を実測）　・　/rig:pr（既存 PR のレビュー）　・　/rig:qa（テストケース設計）　・　/rig:harness"],
      ["Knowledge", "/rig:knowledge（wiki 生成）　・　/rig:persona　・　/rig:catalog　・　/rig:import / export　・　/rig:forge（自己拡張）"],
      ["Planning", "/rig:brainstorm　・　/rig:tasks　・　/rig:goal　・　/rig:design　・　/rig:loop（繰り返しドライバ）"],
      ["その他", "/rig:queue（積んで一括実行）　・　/rig:init（リポジトリ初期設定）　・　/rig:govern（組織ガバナンス）　・　/rig:sec"],
    ],
    rowH: 0.52, size: 10.5,
  });
  card(s, { x: M, y: 5.25, w: 6.0, h: 1.2, head: "最初の一日に使うもの",
    body: "/rig:go と diff / accept / discard の四つだけです。", bodySize: 11.5 });
  card(s, { x: M + 6.4, y: 5.25, w: 5.8, h: 1.2, head: "今は覚えなくていいもの",
    body: "残りは全部そうです。必要になった日に、この表へ戻ってきてくれれば大丈夫です。", bodySize: 11.5 });
}

/* ------------------------------------------------------------------ 14 */
{
  dark("第二部　pack で知識層を拡張する",
    "自分の領域の知識に、バージョンとハッシュと出どころを付けて、\n配布物として渡せるようにします。",
    "docs/packs.md ／ rig_workbench/packs/model.py");
}

/* ------------------------------------------------------------------ 15 */
{
  const s = base("知識層って何でしょう。なぜ二種類あるのか", "N° 12");
  card(s, {
    x: M, y: 1.42, w: 12.2, h: 1.15, fill: "E7EFEB",
    head: "知識層は、subagent のプロンプトに差し込まれる「その領域の知識」です",
    body: "「うちの会社ではバックアップをこう呼んでいる」「この製品ではこの言葉をこう使う」。コードをいくら読んでも出てこないこういう事実を、レビュアーや実装役に持たせておくための層です。",
    bodySize: 11.5, lead: 15,
  });
  table(s, {
    x: M, y: 2.7, w: 8.4, colW: [1.7, 3.3, 3.4],
    head: ["", "セッション知識層", "pack"],
    rows: [
      ["置き場", "~/.claude/rig/knowledge/\n<repo>/.claude/rig/knowledge/", ".rig/packs/ ・ ~/.rig/packs/ ほか"],
      ["作り方", "/rig:knowledge で生成", "rig-wb pack で組み立て"],
      ["配布", "できない（自分の環境限り）", "zip / git リポジトリで配れる"],
      ["版と改竄", "管理しない", "version・sha256・lock で固定"],
      ["品質", "本人の目", "承認済み評価ケースが必須"],
      ["向く場面", "思いついたことを今すぐ効かせたい", "チームや複数リポジトリに効かせたい"],
    ],
    rowH: 0.48, size: 10.5,
  });
  card(s, {
    x: M + 8.7, y: 2.7, w: 3.5, h: 1.75, head: "手元で育てるなら、前者でじゅうぶん",
    body: "書いたらすぐ効きます。棚卸しも配布もまだ要らないうちは、これで足ります。",
    bodySize: 11, lead: 15,
  });
  card(s, {
    x: M + 8.7, y: 4.5, w: 3.5, h: 2.0, head: "人に渡すなら、後者の出番です", headColor: ACCENT,
    body: "誰が書いたのか。いつ見直したのか。根拠は何か。途中で書き換わっていないか。pack は、この四つをちゃんと構造として持っています。",
    bodySize: 11, lead: 15,
  });
}

/* ------------------------------------------------------------------ 16 */
{
  const s = base("persona は事実を書き写さず、wiki を見にいきます", "N° 13");
  codeBox(s, { x: M, y: 1.42, w: 5.9, h: 0.85, size: 11,
    text: '# persona: house-authenticity\ninject: ["[[genre-house]]", "[[music-era-90s]]"]' });
  bullets(s, {
    x: M, y: 2.4, w: 5.9, h: 2.25,
    items: [
      "一つの概念につき、正しいページを一枚だけ。行き来は [[slug]] でつなぎます",
      "persona は事実を本文に書き写さず、ページを見にいきます",
      "書き写してしまうと、その知識は誰にも見えない暗黙知になります。参照にしておけば、ページを一枚直すだけで、見にきている persona 全員の判断がいっぺんに新しくなります",
      "/rig:knowledge の書き先は、何も指定しなければ global。--project を付けるとプロジェクト側の上書きになります",
    ],
    size: 11.5,
  });
  label(s, { text: "[[slug]] は tier で解決されて、上の層が勝ちます", x: M + 6.3, y: 1.42, w: 6 });
  const tiers = [
    ["1", "project overlay", "<repo>/.claude/rig/knowledge/wiki/"],
    ["2", "global", "~/.claude/rig/knowledge/wiki/"],
    ["3", "org", "チームでいっしょに使う層（RIG_ORG_HOME）"],
    ["4", "pack 同梱", "<pack>/facets/knowledge/<slug>.md"],
    ["5", "shipped", "rig にはじめから入っているページ"],
  ];
  tiers.forEach((t, i) => {
    const y = 1.78 + i * 0.62;
    s.addShape(pres.ShapeType.ellipse, { x: M + 6.3, y: y + 0.05, w: 0.34, h: 0.34,
      fill: { color: i === 3 ? ACCENT : "C9CEC9" }, line: { color: i === 3 ? ACCENT : "C9CEC9", width: 0 } });
    s.addText(t[0], { x: M + 6.3, y: y + 0.05, w: 0.34, h: 0.34, isTextBox: true, margin: 0,
      align: "center", valign: "middle", fontFace: TITLE_F, fontSize: 11, bold: true, color: "FFFFFF" });
    s.addText(t[1], { x: M + 6.78, y: y, w: 2.0, h: 0.28, isTextBox: true, margin: 0,
      fontFace: TITLE_F, fontSize: 11.5, bold: true, color: INK });
    s.addText(t[2], { x: M + 6.78, y: y + 0.26, w: 5.4, h: 0.28, isTextBox: true, margin: 0,
      fontFace: MONO_F, fontSize: 9.5, color: SLATE });
  });
  card(s, {
    x: M, y: 4.85, w: 5.9, h: 1.5, fill: "E7EFEB", head: "pack の persona は、まず自分の pack をのぞきます",
    body: "pack は、自分の persona に要るページを一緒に持ち歩きます。同じ slug をプロジェクト側に置けば、これまでどおり上書きできます。",
    bodySize: 11, lead: 15,
  });
}

/* ------------------------------------------------------------------ 17 */
{
  const s = base("pack の type が決めているのは、権限です", "N° 14");
  table(s, {
    x: M, y: 1.42, w: 8.0, colW: [1.7, 4.4, 1.9], monoFirst: true,
    head: ["type", "宣言できるアセット", "host コマンド実行"],
    rows: [
      ["knowledge", "wiki・resource・評価ケースと結果", "いいえ"],
      ["policy", "上記 ＋ policy", "いいえ"],
      ["reviewer", "上記 ＋ persona・output-contract", "いいえ"],
      ["skill", "すべてのプロンプト種別（instruction・recipe・pattern・command・agent）", "いいえ"],
      ["workflow", "skill と同じ（違いは宣言された意図であって、権限ではない）", "いいえ"],
      ["tool", "すべて", "はい"],
    ],
    rowH: 0.46, size: 10.5,
  });
  card(s, {
    x: M + 8.3, y: 1.42, w: 3.9, h: 2.2, fill: "F3EAE6",
    head: "右端の列こそ、この型モデルがある理由です", headColor: ALERT,
    body: "recipe に checks:（手元のマシンで動くシェルコマンド）を書けるのは tool だけです。ほかの型が運ぶのは、プロバイダが読むテキストだけになります。",
    bodySize: 11, lead: 15,
  });
  card(s, {
    x: M + 8.3, y: 3.75, w: 3.9, h: 1.35, head: "type ≠ kind",
    body: "kind（core / official / domain / project）は tier の順番を決めるだけです。tier は権限ではありません。",
    bodySize: 11, lead: 15,
  });
  card(s, {
    x: M + 8.3, y: 5.3, w: 3.9, h: 1.4, head: "初期値を用意していない理由",
    body: "初期値を置くと、大事な判断を「何も決めなかった人」に押しつけてしまうからです。",
    bodySize: 11, lead: 15,
  });
  s.addText("チームの知識を足すことが、そのままコマンドを動かす権限を渡すことになってはいけません。ここが型モデルの引いている線です。", {
    x: M, y: 4.72, w: 8.0, h: 0.5, isTextBox: true, margin: 0,
    fontFace: TITLE_F, fontSize: 13, bold: true, color: INK });
  card(s, {
    x: M, y: 5.3, w: 8.0, h: 1.4, head: "どの type を選べばいいか",
    body: "知識層を配るだけなら knowledge。persona も一緒に配るなら reviewer、recipe やコマンドまで入れるなら skill です。必要以上に強い型を選ばないでおくことが、そのまま受け取る側への安心につながります。",
    bodySize: 11.5, lead: 15,
  });
}

/* ------------------------------------------------------------------ 18 */
{
  const s = base("pack を作ってみる。init → sync → validate → doctor", "N° 15");
  codeBox(s, { x: M, y: 1.42, w: 7.0, h: 2.35, size: 10, lead: 15,
    text: "$ rig-wb pack init my-domain --type knowledge \\\n      --kind domain --root .rig/packs\ninitialized: .rig/packs/my-domain\n\nnext:\n  1. write an asset  facets/knowledge/<name>.md\n  2. rig-wb pack sync .rig/packs/my-domain\n  3. rig-wb pack validate .rig/packs/my-domain" });
  codeBox(s, { x: M, y: 3.95, w: 7.0, h: 1.35, size: 10, lead: 15,
    text: "$ vi .rig/packs/my-domain/facets/knowledge/backup.md\n$ rig-wb pack sync .rig/packs/my-domain\n  + facets/knowledge/backup.md\npack sync: 1 asset(s) declared and hashed" });
  codeBox(s, { x: M, y: 5.45, w: 7.0, h: 1.0, size: 10, lead: 15,
    text: "$ rig-wb pack doctor .rig/packs/my-domain\npack doctor: warning\n- empty_pack: .rig/packs/my-domain" });

  card(s, { x: M + 7.4, y: 1.42, w: 4.8, h: 1.65, head: "pack.yaml は手で書きません",
    body: "中身をぜんぶパスと sha256 で宣言していて、検証のときに一字一句くらべられます。つまり自動で作られるものなので、手で書く場所ではないんです。",
    bodySize: 11, lead: 15 });
  card(s, { x: M + 7.4, y: 3.2, w: 4.8, h: 1.65, head: "sync はフォルダをそのまま写します",
    body: "消したファイルは、宣言のほうからも消えます。書き換わるのは assets と hashes だけ。version・description・entrypoints はあなたのものです。",
    bodySize: 11, lead: 15 });
  card(s, { x: M + 7.4, y: 4.98, w: 4.8, h: 1.47, fill: "F3EAE6", head: "valid は「できあがり」ではありません", headColor: ALERT,
    body: "中身が空の pack でもスキーマは満たすので、valid と出てしまいます。その状態は doctor が教えてくれます。警告なら exit 0、failed だけがエラーです。",
    bodySize: 11, lead: 15 });
}

/* ------------------------------------------------------------------ 19 */
{
  const s = base("wiki を入れたとたん、評価ゲートが立ちます", "N° 16");
  card(s, {
    x: M, y: 1.42, w: 12.2, h: 1.15, fill: "E7EFEB",
    head: "wiki はプロンプト素材。プロバイダに見せるテキストだからです",
    body: "なので wiki を持つ pack には、承認ずみの評価ケースが最低ひとつ要ります。会社の知識ページも、ほかのプロンプト面とおなじルールで扱われるわけですね。うっかりそうなったのではなく、そう決めています。",
    bodySize: 11.5, lead: 15,
  });
  codeBox(s, { x: M, y: 2.68, w: 7.4, h: 2.7, size: 9.5, lead: 13,
    text: "$ rig-wb pack validate .rig/packs/my-domain\n[ERROR] prompt-bearing pack requires at least one\n        evaluation case\n\n# draft は pack の外（プロジェクト側）に書く\n#   .rig/evals/drafts/<case-id>/case.json\n#   prompt_surfaces: [\"wiki:backup-policy\"]\n$ rig-wb eval run <case-id> --phase baseline ...\n$ rig-wb eval run <case-id> --phase current  ...\n$ rig-wb eval compare --baseline ... --current ...\n$ rig-wb eval promote <case-id> ... --into <pack>\n$ rig-wb pack sync && rig-wb pack validate\nvalid: my-domain@0.1.0" });
  card(s, { x: M + 7.7, y: 2.68, w: 4.5, h: 1.5, head: "承認は、フラグを立てるだけでは済みません",
    body: "promote は、しきい値に届かない証拠も、意味のほうを見ていないケースも受けつけません。結果には署名がつくので、手を入れた結果はしきい値を見るより先にはじかれます。",
    bodySize: 11, lead: 15 });
  card(s, { x: M + 7.7, y: 4.3, w: 4.5, h: 1.15, head: "draft を pack の中に置けないわけ",
    body: "pack は宣言していないものを持てないからです。--into が変えるのは行き先だけです。",
    bodySize: 11, lead: 15 });
  card(s, { x: M, y: 5.5, w: 12.2, h: 1.35, fill: "F3EAE6", head: "entrypoint を書き忘れると、ケースは走りません", headColor: ALERT,
    body: "prompt_entrypoint には、マニフェストが宣言している entrypoint を書いておく必要があります（知識 pack なら kind: wiki でページ自身）。書き忘れても pack validate は通ってしまいます。でも pack test は structural_only と返します。ケースは同梱されて、ハッシュもされて、それでも誰にも動かされないままです。",
    bodySize: 11, lead: 15 });
}

/* ------------------------------------------------------------------ 20 */
{
  const s = base("knowledge ブロックに「何についての pack か」を書きます", "N° 17");
  codeBox(s, { x: M, y: 1.42, w: 6.0, h: 1.75, size: 10.5, lead: 15,
    text: 'knowledge:\n  scope: ["company"]\n  topics: ["access-control", "backup"]\n  owner: "Corp IT"\n  evidence: ["情報セキュリティ規程", "運用設計書"]\n  reviewed_at: "2026-08-01T00:00:00+00:00"' });
  bullets(s, {
    x: M, y: 3.3, w: 6.0, h: 2.0,
    items: [
      "ブロック自体は書かなくても大丈夫です。ただし置いたなら五つとも必須で、半分だけというのは通りません",
      "理由は reviewed_at にあります。見直した日が書いていない知識ほど、誰にも気づかれないまま古くなっていくからです",
      "どの type でも書けます。これは説明であって、権限の話ではありません",
      "scope は company のようにざっくりか、product:northwind-one のように値まで指定して書きます",
    ],
    size: 11,
  });
  card(s, { x: M, y: 5.3, w: 6.0, h: 1.5, head: "evidence だけは、並べ替えなくて大丈夫です",
    body: "人が書いた資料の題名を、その言語のまま並べるからです。順番そのものが「主にどれに寄りかかっているか」を表します。同じものを二度書くのだけは断られます。",
    bodySize: 10.5, lead: 14 });

  label(s, { text: "探す。選び出しはしますが、決めはしません", x: M + 6.4, y: 1.42, w: 6 });
  codeBox(s, { x: M + 6.4, y: 1.75, w: 5.8, h: 3.05, size: 9.5, lead: 13,
    text: "$ rig-wb pack knowledge --topic backup\ncompany-security@0.1.0  company   Corp IT\n  reviewed 2026-08-01\n  evidence: 情報セキュリティ規程, 運用設計書\n  wiki: pack://project/company-security/\n          facets/knowledge/backup-policy.md\nproduct-security@0.1.0  product:northwind-one\n  reviewed 2026-07-15\n  evidence: サービス仕様書\n\nscope is ambiguous: company, product:northwind-one\n  — narrow with --scope before treating any of\n    these as the answer" });
  card(s, { x: M + 6.4, y: 4.9, w: 5.8, h: 1.9, fill: "E7EFEB",
    head: "どっちのつもりだったかは、どの pack にも書いてありません",
    body: "それは質問した人のなかにある事実だからです。だから rig は当てにいかず、「まだ決まっていませんよ」ということと、選べる候補をそのまま返します。影に隠れたページも、隠さずラベルをつけて並べます。",
    bodySize: 10.5, lead: 14 });
}

/* ------------------------------------------------------------------ 21 */
{
  const s = base("pack を渡す。bundle と named source", "N° 18");
  codeBox(s, { x: M, y: 1.42, w: 6.2, h: 1.55, size: 10, lead: 15,
    text: "$ rig-wb pack bundle .rig/packs/my-domain\nbundled: my-domain@0.1.0 (3 file(s))\n  -> dist/my-domain-0.1.0.zip\n  sha256: 7ef9b1a3...\n$ rig-wb pack install dist/... --scope project" });
  codeBox(s, { x: M, y: 3.15, w: 6.2, h: 1.55, size: 10, lead: 15,
    text: "$ rig-wb pack source add product \\\n    --scheme git+ssh \\\n    --url git@github.com:acme/rig-pack-{pack}.git\n$ rig-wb pack install product:northwind@1.4.0\n$ rig-wb pack verify-sources --scope project" });
  card(s, { x: M, y: 4.88, w: 6.2, h: 1.55, fill: "E7EFEB", head: "rig は資格情報をひとつも持ちません",
    body: "git を呼ぶだけなので、認証はもう設定してあるもの（SSH agent・credential helper・CI secret）が答えてくれます。lock に残るのはソースの名前と commit だけで、URL は書きません。",
    bodySize: 11, lead: 15 });

  table(s, {
    x: M + 6.6, y: 1.42, w: 5.6, colW: [2.0, 3.6], monoFirst: true, size: 10, rowH: 0.36,
    head: ["見るコマンド", "答える問い"],
    rows: [
      ["pack list", "何が入っているか（id・type・検証状態）"],
      ["pack info", "身元と来歴。出所・digest・依存"],
      ["pack explain", "そのアセットは実際にプロンプトへ届いているか"],
      ["pack outdated", "各ソースは今どの版を出しているか"],
      ["pack update", "版を移す（失敗しても旧版が残る）"],
    ],
  });
  card(s, { x: M + 6.6, y: 3.75, w: 5.6, h: 1.55, fill: "F3EAE6",
    head: "「override したのに何も起きない」の正体", headColor: ALERT,
    body: "入っていて valid なのに、まるごと影に隠れている状態です。info は身元しか答えてくれないので、explain で tier の勝ち負けを見てみてください。",
    bodySize: 11, lead: 15 });
  card(s, { x: M + 6.6, y: 5.3, w: 5.6, h: 1.15, head: "digest は「誰が」までは答えません",
    body: "同じ中身であることだけを保証してくれます。誰が作ったかを答えるのは、署名と trust root のほうです。",
    bodySize: 11, lead: 15 });
}

/* ------------------------------------------------------------------ 22 */
{
  const s = base("つまずきやすいところ", "N° 19");
  const traps = [
    ["valid はできあがりではありません", "空の pack でも valid になります。doctor の empty_pack 警告を見てみてください"],
    ["pack.yaml は手で書きません", "「たまたま合っている」か「間違っている」かのどちらかにしかなりません"],
    ["署名した pack は sync できません", "いったん署名を外して、sync してから、鍵で署名し直してください"],
    ["install だけではコマンドになりません", "command アセットは、自分で登録できるホスト向けの資料です"],
    ["project pack は最初に同意が要ります", "RIG_ALLOW_PROJECT_PACKS=1 で同意します。同意は中身のハッシュに結びつきます"],
    ["user / org では品質検証を飛ばせません", "--allow-unverified が使えるのは project スコープだけです"],
    ["mock は品質の証拠になりません", "結果に non_quality_mock とはっきり書かれます"],
    ["private は署名の代わりになりません", "private リポジトリの pack も、同じ検証をぜんぶ通ります"],
  ];
  traps.forEach((t, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    const x = M + col * 6.4, y = 1.42 + row * 1.3;
    card(s, { x, y, w: 6.0, h: 1.28, num: String(i + 1), numColor: ALERT,
      head: t[0], headSize: 11.5, body: t[1], bodySize: 10.5, lead: 14 });
  });
}

/* ------------------------------------------------------------------ 23 */
{
  const s = dark("まずは、ここから", null, null, 1.15);
  const steps = [
    ["1", "まずは /rig:go だけ使ってみる", "diff / accept / discard の四つで、まず一週間やってみてください。ゲートに一度落ちて「なんで落ちたんだろう」と読むところまで来れば、rig がどういう道具かは自然と分かります。"],
    ["2", "ページを一枚だけ書いてみる", "/rig:knowledge で自分の領域を一枚書いて、persona の inject: から参照させてみます。効くかどうかは、ここで見えてきます。"],
    ["3", "効くと分かってから pack にする", "rig-wb pack init --type knowledge で、ちゃんとした配布物にします。順番を逆にすると、まだ効くか分からないものに評価ケースを書くことになってしまいます。"],
  ];
  steps.forEach((st, i) => {
    const y = 2.6 + i * 1.32;
    s.addShape(pres.ShapeType.ellipse, { x: M + 0.35, y: y + 0.05, w: 0.5, h: 0.5,
      fill: { color: ACCENT }, line: { color: ACCENT, width: 0 } });
    s.addText(st[0], { x: M + 0.35, y: y + 0.05, w: 0.5, h: 0.5, isTextBox: true, margin: 0,
      align: "center", valign: "middle", fontFace: TITLE_F, fontSize: 16, bold: true, color: "FFFFFF" });
    s.addText(st[1], { x: M + 1.05, y: y, w: 4.2, h: 0.4, isTextBox: true, margin: 0,
      fontFace: TITLE_F, fontSize: 15, bold: true, color: CANVAS });
    s.addText(st[2], { x: M + 5.35, y: y - 0.02, w: 6.6, h: 1.1, isTextBox: true, margin: 0,
      fontFace: BODY_F, fontSize: 12, color: "B9BEB9", lineSpacing: 17 });
  });
  s.addText("もとの資料：README.ja.md（全体像）　／　docs/packs.md（pack の仕様）　／　skills/engine/SKILL.md（ブリック一覧）", {
    x: M + 0.35, y: 6.72, w: 12, h: 0.35, isTextBox: true, margin: 0,
    fontFace: BODY_F, fontSize: 11, color: "8A908C" });
  s.addText("rig 入門", { x: M + 0.35, y: 0.72, w: 6, h: 0.3, isTextBox: true, margin: 0,
    fontFace: BODY_F, fontSize: 11, bold: true, color: ACCENT });
}

gate.enforce();

pres.writeFile({ fileName: "rig-intro.pptx" }).then(() => console.log("written: rig-intro.pptx (" + n + " slides)"));
