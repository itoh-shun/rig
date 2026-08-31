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
  if (o.head) {
    slide.addText(o.head, {
      x: tx, y: ty + (o.num ? 0.04 : 0), w: tw, h: 0.35, isTextBox: true, margin: 0,
      fontFace: TITLE_F, fontSize: o.headSize || 14, bold: true, color: o.headColor || INK,
    });
    ty += 0.42;
  }
  if (o.body) {
    slide.addText(o.body, {
      x: o.num ? tx : o.x + 0.28, y: ty, w: o.num ? tw : o.w - 0.56, h: o.y + o.h - ty - 0.18,
      isTextBox: true, margin: 0, valign: "top",
      fontFace: BODY_F, fontSize: o.bodySize || 12, color: o.bodyColor || SLATE, lineSpacing: o.lead || 17,
    });
  }
}

function codeBox(slide, o) {
  slide.addShape(pres.ShapeType.roundRect, {
    x: o.x, y: o.y, w: o.w, h: o.h, rectRadius: 0.05,
    fill: { color: INK }, line: { color: INK, width: 0 }, shadow: shadow(),
  });
  slide.addText(o.text, {
    x: o.x + 0.22, y: o.y + 0.16, w: o.w - 0.44, h: o.h - 0.32, isTextBox: true, margin: 0,
    valign: "top", fontFace: MONO_F, fontSize: o.size || 10.5, color: "EDEDE8", lineSpacing: o.lead || 15,
  });
}

function bullets(slide, o) {
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
    "AI に仕事を任せるための、隔離と検証の層。\nゼロから知り、自分のドメインを教え込むまで。",
    "典拠：README.ja.md ／ docs/packs.md ／ skills/engine/SKILL.md"
  );
  s.addText("第一部　rig を知る　　|　　第二部　pack で知識層を拡張する", {
    x: M + 0.35, y: 5.5, w: 11, h: 0.4, isTextBox: true, margin: 0,
    fontFace: BODY_F, fontSize: 13, color: "9AA39C",
  });
  s.addNotes("rig をまったく知らない人向け。前半で安全フローの仕組み、後半で pack による知識層の拡張を扱う。");
}

/* ================= INTRO BLOCK (inserted) ================= */

/* --- agenda (rebuilt) --- */
{
  const s = base("この資料の地図", "AGENDA");
  const cols = [
    ["導入", "なぜ rig を作ったか",
      "・よくある AI 活用の四つの型\n・そこで共通して開く穴\n・プロンプト／コンテキスト／\n　　ハーネスの入れ子\n・ハーネスの 2×2\n・一般的な対応と、その限界\n・rig は四象限をどう埋めるか"],
    ["第一部", "rig を知る",
      "・rig とは何か／何ではないか\n・直接頼む場合との違い\n・入口は二つ、最初の三十秒\n・安全と言える四つの理由\n・ゲートと機械センサー\n・ブリックと recipe\n・レビュアーを測る（drill / stats）\n・コマンドの地図"],
    ["第二部", "pack で知識層を拡張する",
      "・知識層とは何か、なぜ二種類か\n・wiki と inject、tier 解決\n・pack の type ＝ 権限\n・作る：init → sync → validate\n・wiki が呼ぶ評価ゲート\n・knowledge ブロックと検索\n・配る：bundle / named source\n・落とし穴と、明日からの順番"],
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
  s.addText("この資料は rig リポジトリの一次資料だけを典拠にしています。仕様は発展中のため、厳密な引数は各コマンドの --help を正本としてください。", {
    x: M, y: 6.3, w: W - M * 2, h: 0.5, isTextBox: true, margin: 0,
    fontFace: BODY_F, fontSize: 10.5, color: SLATE,
  });
}

/* --- intro 01: why --- */
{
  const s = base("なぜ rig を作ったか", "はじめに · 01");
  card(s, { x: M, y: 1.42, w: 12.2, h: 1.0, fill: "E7EFEB" });
  s.addText("オーケストレータは「どう動かすか」を決める。rig は「その結果を受け入れてよいか」を決める。", {
    x: M + 0.35, y: 1.62, w: 11.5, h: 0.4, isTextBox: true, margin: 0,
    fontFace: TITLE_F, fontSize: 17, bold: true, color: INK });
  s.addText("docs/landscape.md — この一行が、機能を足すかどうかの判断基準になっている。", {
    x: M + 0.35, y: 2.04, w: 11.5, h: 0.3, isTextBox: true, margin: 0,
    fontFace: BODY_F, fontSize: 11, color: SLATE });

  label(s, { text: "出発点にあった三つの観測", x: M, y: 2.68, w: 6 });
  const obs = [
    ["1", "書く速度だけが上がった", "生成は速くなったが、その成果物を「受け入れてよいか」を判定する層は、人間の目視のままだった。"],
    ["2", "「できました」に根拠がない", "モデルの自己申告は完了の根拠にならない。だが実務では、それ以外に判断材料がない状態が普通だった。"],
    ["3", "失敗が手元に残る", "うまくいかなかった試行が作業ツリーに混ざる。何を捨てて何を残すかの判断自体がコストになる。"],
  ];
  obs.forEach((o, i) => {
    card(s, { x: M, y: 3.02 + i * 1.13, w: 6.0, h: 1.0, num: o[0], head: o[1], headSize: 12.5,
      body: o[2], bodySize: 10.5, lead: 13 });
  });

  card(s, { x: M + 6.4, y: 3.02, w: 6.0, h: 1.7, head: "だから rig が引き受けたのは、一箇所だけ",
    body: "受け入れの判定を、人間の目視から機械の関門へ移すこと。速く書かせることでも、賢く書かせることでもない。",
    bodySize: 11.5, lead: 16 });
  card(s, { x: M + 6.4, y: 4.9, w: 6.0, h: 1.45, fill: "F3EAE6", head: "裏返して言うと", headColor: ALERT,
    body: "rig は品質を自動的に生まない。あなたが定義した基準を、AI に無視させないだけ。基準を作るのは人間の仕事のまま。",
    bodySize: 11.5, lead: 16 });
}

/* --- intro 02: the four common patterns --- */
{
  const s = base("よくある AI 活用の四つの型と、そこで起きること", "はじめに · 02");
  const pats = [
    ["チャットに貼って、返ってきたコードを貼り戻す",
      "差分の出所が消える。どこを変えたかは人間の記憶にしかなく、無関係な変更が混ざっても検出する場所がない。"],
    ["エージェントに作業ツリーへ直接書かせる",
      "失敗した試行がそのまま手元に残る。機械的なチェックを誰も配線していなければ、そのチェックは構造的に一度も走らない。"],
    ["CLAUDE.md や AGENTS.md にルールを書く",
      "prose の依頼は守られないことがある。「テストを実行して」と書くことと、hook で毎回強制することの信頼性は段違い。"],
    ["AI にレビューさせる",
      "生成者と採点者が同系統だと自己評価に甘くなる。しかもそのレビュアーの検出率を、誰も測っていない。"],
  ];
  pats.forEach((p, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    card(s, { x: M + col * 6.4, y: 1.42 + row * 1.95, w: 6.0, h: 1.75,
      num: String(i + 1), head: p[0], headSize: 12.5, body: p[1], bodySize: 11, lead: 15 });
  });
  card(s, { x: M, y: 5.35, w: 12.2, h: 1.05, fill: "F3EAE6",
    head: "四つに共通するのは「悪いことが起きた」ではなく、「起きたかどうかが分からない」こと", headColor: ALERT,
    body: "どれも普通に成果を出す。問題は失敗率ではなく、失敗したときに誰も気づかないまま次へ進む構造のほうにある。",
    bodySize: 11.5, lead: 15 });
}

/* --- intro 03: the four holes --- */
{
  const s = base("症状の根っこ — 開きやすい穴は四つ（重い順）", "はじめに · 03");
  const holes = [
    ["計算的センサーがループに入っていない", "最頻",
      "テストや lint が「存在する」だけで、エージェントの実行ループにバックプレッシャーとして入っていない。存在することと、効いていることは別。"],
    ["検証ループそのものが無い", "自己採点",
      "モデルに自分の仕事を検証する手段を与えると品質が 2〜3 倍になる（Boris Cherny）。逆に言えば、手段が無いエージェントは自己評価に甘くなる。"],
    ["評価と計測をしていない", "測れない",
      "ルールを足したが、効果を測っていない。善意のルール追加が逆効果になることがある（Context Rot ＝ 文脈が長いほど性能が落ちる）。"],
    ["ハーネスが厚すぎる", "肥大",
      "Thin Harness, Fat Skills（Garry Tan）。ループ管理は薄く、知能は Skills に、実行は決定論的ツールに委ねる。親ループが肥大したら設計が間違っている。"],
  ];
  holes.forEach((h, i) => {
    const y = 1.42 + i * 1.28;
    card(s, { x: M, y, w: 12.2, h: 1.12, num: String(i + 1),
      numColor: i === 0 ? ALERT : ACCENT, head: h[0], headSize: 13, body: h[2], bodySize: 11, lead: 15 });
    s.addText(h[1], { x: M + 10.6, y: y + 0.24, w: 1.4, h: 0.3, isTextBox: true, margin: 0,
      align: "right", fontFace: BODY_F, fontSize: 10.5, bold: true, color: i === 0 ? ALERT : SLATE });
  });
  s.addText("典拠：skills/engine/facets/knowledge/harness-taxonomy.md（rig 自身が /rig:harness で使う観点カタログ）", {
    x: M, y: 6.6, w: 12.2, h: 0.3, isTextBox: true, margin: 0, fontFace: BODY_F, fontSize: 10, color: SLATE });
}

/* --- intro 04: ○○ engineering --- */
{
  const s = base("プロンプトの外側にコンテキスト、その外側にハーネス", "はじめに · 04");
  s.addText("「〇〇エンジニアリング」は別々の流派ではなく、包含関係にある。外側を設計しないまま内側だけ磨いても、効果は再現しない。", {
    x: M, y: 1.4, w: 12.2, h: 0.35, isTextBox: true, margin: 0,
    fontFace: BODY_F, fontSize: 12.5, color: INK });

  s.addShape(pres.ShapeType.roundRect, { x: M, y: 1.85, w: 6.6, h: 4.55, rectRadius: 0.06,
    fill: { color: TINT }, line: { color: LINE, width: 0.5 }, shadow: shadow() });
  s.addText("ハーネスエンジニアリング", { x: M + 0.3, y: 2.0, w: 6.0, h: 0.3, isTextBox: true, margin: 0,
    fontFace: TITLE_F, fontSize: 14, bold: true, color: INK });
  s.addText("ループ・ツール・強制・検証・記録まで含めた仕組み全体", {
    x: M + 0.3, y: 2.3, w: 6.0, h: 0.3, isTextBox: true, margin: 0,
    fontFace: BODY_F, fontSize: 10.5, color: SLATE });

  s.addShape(pres.ShapeType.roundRect, { x: M + 0.42, y: 2.7, w: 5.76, h: 3.5, rectRadius: 0.06,
    fill: { color: "E7EFEB" }, line: { color: "E7EFEB", width: 0 } });
  s.addText("コンテキストエンジニアリング", { x: M + 0.72, y: 2.85, w: 5.2, h: 0.3, isTextBox: true, margin: 0,
    fontFace: TITLE_F, fontSize: 14, bold: true, color: INK });
  s.addText("何をどの順で見せるか。知識層・注入・圧縮への耐性", {
    x: M + 0.72, y: 3.15, w: 5.2, h: 0.3, isTextBox: true, margin: 0,
    fontFace: BODY_F, fontSize: 10.5, color: SLATE });

  s.addShape(pres.ShapeType.roundRect, { x: M + 0.84, y: 3.55, w: 4.92, h: 2.45, rectRadius: 0.06,
    fill: { color: INK }, line: { color: INK, width: 0 } });
  s.addText("プロンプトエンジニアリング", { x: M + 1.14, y: 3.7, w: 4.4, h: 0.3, isTextBox: true, margin: 0,
    fontFace: TITLE_F, fontSize: 14, bold: true, color: CANVAS });
  s.addText("一回の指示の書き方。ここだけを磨くのが最も一般的で、\nここだけでは面が変わると再現しない。\n\nrig ではプロンプト面（persona・instruction・recipe・wiki）は\nコンパイラが一切検査しない部分として扱い、\n変更には承認済みの評価ケースを要求する。",
    { x: M + 1.14, y: 4.02, w: 4.4, h: 1.8, isTextBox: true, margin: 0, valign: "top",
      fontFace: BODY_F, fontSize: 10.5, color: "C9CCC8", lineSpacing: 14 });

  card(s, { x: M + 7.0, y: 1.85, w: 5.2, h: 2.15, head: "ハーネスには二層ある",
    body: "エージェントハーネス（プロダクト内蔵＝Claude Code / Codex 側のループ・ツール・メモリ）と、ユーザーハーネス（使う側が組む＝CLAUDE.md・Skills・Hooks・MCP・テスト・lint・CI・recipe）。",
    bodySize: 11.5, lead: 16 });
  card(s, { x: M + 7.0, y: 4.15, w: 5.2, h: 1.3, fill: "E7EFEB", head: "rig が作るのは、後者だけ",
    body: "内蔵ループには手を入れない。その外側に、薄い品質・安全レイヤーとして乗る。",
    bodySize: 11.5, lead: 16 });
  card(s, { x: M + 7.0, y: 5.6, w: 5.2, h: 0.8, head: "だから engine は不変",
    body: "拡張は pack として上に乗せる。エンジンを書き換えて対応しない。",
    bodySize: 11.5, lead: 16 });
}

/* --- intro 05: the 2x2 --- */
{
  const s = base("ハーネスの 2×2 — 何が足りないかは、ここで見える", "はじめに · 05");
  s.addText("二軸で分ける。① 計算的（決定論的・コードで判定）か、推論的（LLM の判断）か。② ガイド（先回りで方向づける）か、センサー（事後に検知する）か。", {
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
  card(s, { x: M + 8.6, y: 1.85, w: 3.6, h: 1.55, head: "四象限が揃って強い",
    body: "推論だけ（レビューはするがテストが無い）も、計算だけ（テストは通るが設計の妥当性を誰も見ない）も穴になる。",
    bodySize: 11, lead: 15 });
  card(s, { x: M + 8.6, y: 3.5, w: 3.6, h: 1.55, fill: "E7EFEB", head: "計算的センサーが一次",
    body: "計算的センサーは sweet-talk できない。だから最強のバックプレッシャーになる。推論的センサーはその二次。",
    bodySize: 11, lead: 15 });
  card(s, { x: M + 8.6, y: 5.15, w: 3.6, h: 1.45, fill: "F3EAE6", head: "「存在する」≠「効いている」", headColor: ALERT,
    body: "どの hook にも acceptance gate にも繋がっていない lint は、ループに何の back pressure もかけない。",
    bodySize: 11, lead: 15 });
  card(s, { x: M, y: 5.15, w: 8.3, h: 1.45, head: "この 2×2 は、自分のプロジェクトにも当てられる",
    body: "/rig:harness が read-only で棚卸しし、空いている象限と「あるのに効いていない資産」を名指しする。答えは「新しいルールを足す」ではなく、接続する・強制する・減らす。",
    bodySize: 11.5, lead: 16 });
}

/* --- intro 06: usual responses and their limits --- */
{
  const s = base("それらへの一般的な対応と、その限界", "はじめに · 06");
  table(s, {
    x: M, y: 1.42, w: 7.6, colW: [2.6, 5.0], rowH: 0.66, size: 10.5, boldFirst: true,
    head: ["よく採られる対応", "どこで止まるか"],
    rows: [
      ["プロンプトを磨く", "面が変わると再現しない。効果を測っていないので、良くなったかどうかも分からない"],
      ["CLAUDE.md にルールを足す", "prose の依頼は守られないことがある。しかも足しすぎると文脈が濁って逆効果になる"],
      ["lint / test / CI を用意する", "用意しただけでは、エージェントのループの外にある。人間の CI 待ちまで誰も見ない"],
      ["AI レビューを足す", "生成者と同系統だと甘くなる。検出率が不明なので、通ったことに意味があるか分からない"],
      ["人間のレビューを増やす", "スケールしない。ゴム印化しても、ゴム印になったこと自体を検知できない"],
    ],
  });
  label(s, { text: "効く順序は、足すことではない", x: M + 7.9, y: 1.42, w: 4.8 });
  const order = [
    ["接続する", "既にある lint・型・テストを、hook と acceptance gate に繋いでループの中へ入れる"],
    ["強制する", "重要なルールを prose から決定論的な強制へ移す"],
    ["減らす", "効いていないルールを落とす。足したら減らす候補も持つ"],
  ];
  order.forEach((o, i) => {
    card(s, { x: M + 7.9, y: 1.78 + i * 1.28, w: 4.85, h: 1.15, num: String(i + 1),
      head: o[0], headSize: 12.5, body: o[1], bodySize: 10.5, lead: 13 });
  });
  card(s, { x: M, y: 5.5, w: 7.6, h: 0.95, fill: "E7EFEB", head: "新しいルールを書くのは、最後",
    body: "善意のルール追加が事態を悪くすることがある。まず接続し、次に強制し、そのうえで減らす。",
    bodySize: 11.5, lead: 15 });
  card(s, { x: M + 7.9, y: 5.65, w: 4.85, h: 0.8, fill: "F3EAE6", head: "測っていないゲートは願望", headColor: ALERT,
    body: "効果の計測が無いと、この順序自体を回せない。",
    bodySize: 10.5, lead: 13 });
}

/* --- intro 07: what rig actually does --- */
{
  const s = base("rig の活用 — 四象限を、どう埋めるか", "はじめに · 07");
  const quad = [
    ["計算的ガイド", "hook・manifest（.claude/rig.md）・recipe・scaffold。作業を始める前に、走らせ方と既定値を先回りで決める。"],
    ["計算的センサー", "acceptance-gate の機械検証（build / lint / test / 型）＋ 決定論センサー（秘密情報・インジェクション・破壊的コマンド・ゲート改竄・schema-diff）。"],
    ["推論的ガイド", "persona・instruction・知識層の wiki。ドメインの前提を、判断する側に持たせる。"],
    ["推論的センサー", "reviewer persona の並列レビュー。read-only をプロセスレベルで強制し、drill で検出率を実測する。"],
  ];
  quad.forEach((q, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    card(s, { x: M + col * 4.25, y: 1.42 + row * 1.75, w: 4.05, h: 1.6,
      fill: i === 1 ? "E7EFEB" : TINT, head: q[0], headSize: 13, body: q[1], bodySize: 10.5, lead: 14 });
  });
  card(s, { x: M, y: 4.95, w: 8.3, h: 1.45, head: "そして 2×2 の外に、rig 固有のものが二つ",
    body: "① 隔離 — 判定が終わるまで、成果物は作業ツリーに触れない。② 記録 — 何を試み、どの基準で、なぜ受け入れた（拒否した）かが run log と監査証跡に残る。",
    bodySize: 11.5, lead: 16 });

  card(s, { x: M + 8.6, y: 1.42, w: 3.6, h: 3.55, fill: "F3EAE6", head: "引き受けないこと", headColor: ALERT,
    body: "・IDE や GUI の提供\n・汎用エージェント群のプラットフォーム\n・複数モデルの回答を混ぜて一つにすること\n・ワークフロー DSL の表現力競争\n\nrig が複数モデルを使うのは、検証役を生成役から構造的に独立させるためであって、答えを合成するためではない。",
    bodySize: 11, lead: 16 });
  card(s, { x: M + 8.6, y: 5.15, w: 3.6, h: 1.25, head: "逆に、明確に目標なこと",
    body: "他のオーケストレータが作った成果物にも、同じ受け入れ契約を当てること（workbench.py import）。",
    bodySize: 11, lead: 15 });
}

/* --- part 1 divider --- */
{
  dark("第一部　rig を知る",
    "ここからは、その仕組みが実際にどう動くか。\n分類・隔離・ゲート・受け入れの四つを順に見る。",
    "README.ja.md §5〜§12");
}

/* ================= END INTRO BLOCK ================= */

/* ------------------------------------------------------------------ 03 */
{
  const s = base("rig とは何か", "N° 01");
  s.addText("危ないのは、AI が間違えることではない。間違えたことに誰も気づかないまま、本体に混ざることだ。", {
    x: M, y: 1.42, w: W - M * 2, h: 0.4, isTextBox: true, margin: 0,
    fontFace: TITLE_F, fontSize: 15, bold: true, color: INK,
  });

  const steps = [
    ["分類", "bugfix / feature / refactor\n/ review / documentation …"],
    ["recipe 選択", "選択理由をバナーで\n先に宣言する"],
    ["隔離 worktree", "作業ツリーとは別の\n使い捨てブランチ"],
    ["acceptance-gate", "機械的な基準で\n合否を判定"],
    ["accept / discard", "反映は staged。\n判断は常に人間"],
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
    x: M, y: 4.2, w: 3.93, h: 1.9, head: "品質を自動で生む道具ではない",
    body: "あなたが定義した品質基準を、AI に無視させないための道具。基準を作るのは人間の仕事のまま。rig の仕事は強制と測定。",
  });
  card(s, {
    x: M + 4.13, y: 4.2, w: 3.93, h: 1.9, head: "対価がある",
    body: "隔離・検証・記録のために、速度とトークンを意図的に払う。速く書かせたいだけなら、モデルに直接頼むほうが速い。",
  });
  card(s, {
    x: M + 8.26, y: 4.2, w: 3.94, h: 1.9, head: "効いてくる場面", fill: "E7EFEB",
    body: "失敗したときのコストが、速度より高いとき。本番に触れるコード、他人が読むコード、あとで誰も検証しないコード。",
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
    x: M + 7.9, y: 1.5, w: 4.3, h: 2.4, head: "rig の現在地",
    body: "安全性の核（分類・隔離・acceptance-gate・明示的な accept／discard）は実装済みで、リポジトリ自身のテストで裏づけがある。\n\nその上の観測系（drill・board・stats・GitHub 連携）は実用可能だが発展中。",
    bodySize: 11.5, lead: 16,
  });
  card(s, {
    x: M + 7.9, y: 4.05, w: 4.3, h: 2.05, fill: "F3EAE6", head: "正直なスコープ", headColor: ALERT,
    body: "「未出荷の機能を Planned として載せない」がこのプロジェクトの方針。表に無いコマンドは、まだ出荷されていない。",
    bodySize: 11.5, lead: 16,
  });
  card(s, {
    x: M, y: 4.65, w: 7.6, h: 1.75, head: "隔離が task 単位で閉じている、ということ",
    body: "複数タスクを同時に走らせても構造的に安全（別 worktree・別 branch）。/rig:queue で積んで一括 GO しても、並列プロセス同士がファイルを取り合わない。完了後の確認場所は /rig:go board — どの端末・どのプロセスが起動したかに関わらず、全 task の状態が一つの表に出る。",
    bodySize: 11.5, lead: 16,
  });
}

/* ------------------------------------------------------------------ 05 */
{
  const s = base("入口は二つ。中身は同じエンジン", "N° 03");
  card(s, { x: M, y: 1.45, w: 6.0, h: 2.35, num: "A", head: "Claude Code の中では、プラグイン",
    body: "スラッシュコマンドはここから来る。安全フローは一通りこれだけで動く。", bodySize: 11.5 });
  codeBox(s, { x: M + 0.86, y: 2.78, w: 5.0, h: 0.78,
    text: "/plugin marketplace add itoh-shun/sito-plugins\n/plugin install rig@sito-plugins", size: 10 });

  card(s, { x: M + 6.4, y: 1.45, w: 6.0, h: 2.35, num: "B", head: "それ以外の場所では、rig-wb CLI",
    body: "CI・スクリプト・別のアシスタント（Codex / Cursor）から同じ recipe とゲートを回す。", bodySize: 11.5 });
  codeBox(s, { x: M + 7.26, y: 2.78, w: 5.0, h: 0.78,
    text: "pipx install git+https://github.com/itoh-shun/rig.git\nrig-wb version", size: 10 });

  s.addText("両方は要らない。二つ目が必要なのは、Claude Code の外にあるものが同じ recipe とゲートに届く必要があるときだけ。/rig:setup が中から入れてくれる。", {
    x: M, y: 3.92, w: W - M * 2, h: 0.32, isTextBox: true, margin: 0,
    fontFace: BODY_F, fontSize: 11, color: SLATE,
  });

  label(s, { text: "最初の三十秒 — 設定はゼロでいい", x: M, y: 4.34, w: 8 });
  codeBox(s, { x: M, y: 4.66, w: 6.0, h: 1.72,
    text: '/rig:go "ログインバグを直して"\n/rig:go "このPRを厳しめにレビューして"\n/rig:go "今の変更が安全か確認して"\n\n/rig:go diff      # 何が変わったか\n/rig:go accept    # 反映（gate 未達なら拒否）\n/rig:go discard   # 破棄', size: 10.5, lead: 16 });
  codeBox(s, { x: M + 6.4, y: 4.66, w: 6.0, h: 1.72,
    text: "▸ rig\ntask:     ログインバグを直して\ndetected: bugfix\nrecipe:   bugfix — 「バグ」「直して」を検出\nmode:     isolated worktree\ngate:     standard + bugfix", size: 10.5, lead: 16 });
  s.addText("manifest も gates.json も persona 設定も要らない（すべて後から足す opt-in）", {
    x: M, y: 6.46, w: 6, h: 0.3, isTextBox: true, margin: 0, fontFace: BODY_F, fontSize: 10, color: SLATE });
  s.addText("なぜその段取りになったかを、走り出す前に一行で宣言する", {
    x: M + 6.4, y: 6.46, w: 6, h: 0.3, isTextBox: true, margin: 0, fontFace: BODY_F, fontSize: 10, color: SLATE });
}

/* ------------------------------------------------------------------ 06 */
{
  const s = base("「安全です」は主張にすぎない — 四本柱", "N° 04");
  s.addText("rig が安全なのは、次の四つが文章ではなく配線として入っているから。", {
    x: M, y: 1.4, w: 11, h: 0.3, isTextBox: true, margin: 0, fontFace: BODY_F, fontSize: 12.5, color: INK });
  const items = [
    ["1", "隔離された worktree", "タスクごとに専用の worktree と使い捨てブランチ。rig はあなたの作業ツリーに直接書き込まない。失敗しても中断しても、手元は汚れない。隔離が task 単位で閉じているので、複数タスクの同時実行が構造的に安全。"],
    ["2", "acceptance-gate", "「完了しました」では完了にならない。無関係な差分・テスト・型・リスク記述・秘密情報を機械的に確認して初めて反映候補になる。failed か pending が一件でも残れば accept は拒否される（exit 1）。"],
    ["3", "read-only な検証役", "実装する AI と検証する AI を分離し、検証側はプロセスレベルで読み取り専用に固定する。お願いではなく強制として。判定の一次証拠は自己申告レポートではなく、worktree の実際の git diff。"],
    ["4", "明示的な accept と、消えない記録", "accept は squash merge で staged（未コミット）まで。コミットは常に人間が打つ。discard しても run log は残り、何を試みてなぜ捨てたかを辿れる。構造的な前提は --force でも通らない。"],
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
  const s = base("柱 ① 隔離された worktree", "N° 05");
  bullets(s, {
    x: M, y: 1.45, w: 5.9, h: 2.3,
    items: [
      "タスクごとに専用の git worktree ＋ 使い捨てブランチを作る",
      "読み取り専用のタスク（レビュー・調査）は --no-worktree で worktree ごと省略できる",
      "discard は worktree と branch を消すが、run log（.rig/runs/）は残る",
      "並列実行しても別 worktree・別 branch なので、プロセス同士がファイルを取り合わない",
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
    x: M + 7.2, y: 4.5, w: 5.2, h: 1.6, head: "queue は accept しない",
    body: "queue の verifier が見るのは「gate が確定したか」「worktree 内で完結し本体に書いていないか」だけ。反映の判断は人間に残る。完了後の確認場所は /rig:go board。",
    bodySize: 11.5, lead: 16,
  });
}

/* ------------------------------------------------------------------ 08 */
{
  const s = base("柱 ② acceptance-gate — standard ＋ 種別プリセット", "N° 06");
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
    x: M + 8.6, y: 1.42, w: 3.6, h: 2.15, head: "四つの状態",
    body: "各基準は根拠つきで passed / failed / warning / skipped として記録され、全体は passed / passed_with_warnings / failed / pending に集約される。",
    bodySize: 11, lead: 15,
  });
  card(s, {
    x: M + 8.6, y: 3.7, w: 3.6, h: 2.5, fill: "F3EAE6", head: "accept を止めるもの", headColor: ALERT,
    body: "failed か pending が一件でも残っていれば accept は機械的に拒否される（exit 1）。\n\nwarning は accept を止めないが、常に提示され、黙って握りつぶされることはない。",
    bodySize: 11, lead: 15,
  });
  s.addText("正本は scripts/workbench.py gates。プロジェクトは .rig/gates.json の extra_criteria で基準を足せる。", {
    x: M, y: 6.35, w: 8.3, h: 0.3, isTextBox: true, margin: 0, fontFace: BODY_F, fontSize: 10, color: SLATE });
}

/* ------------------------------------------------------------------ 09 */
{
  const s = base("基準は自己申告ではない — 機械センサーが裏づける", "N° 07");
  const sensors = [
    ["no_secret_leak", "task diff への決定論シークレットスキャン。検出があれば failed。抜粋は常にマスク済み。"],
    ["no_injection_markers", "prose 面へのインジェクション・マーカー検出。不可視／bidi Unicode は fail、指示上書き句は warning。"],
    ["no_destructive_operation", "破壊的コマンド検出。rm -rf / ・mkfs・DROP DATABASE は fail、force push や TRUNCATE は warning。"],
    ["no_gate_tampering", "gates.json・recipes・CI workflow の編集は fail。既存テストの改変・assert 削除・skip 追加は warning。"],
    ["public_api_changes_documented", "OpenAPI schema-diff。API が変わったのに diff サマリに記述が無ければ warning に落とす。"],
    ["prompt_regression_passed", "diff が prompt 面に触れたときだけ自動追加。--set による手動上書きを拒否する唯一の基準。"],
  ];
  sensors.forEach((sn, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    const x = M + col * 6.4, y = 1.42 + row * 1.35;
    card(s, { x, y, w: 6.0, h: 1.18, head: sn[0], headSize: 11.5, body: sn[1], bodySize: 10.5, lead: 14 });
  });
  card(s, {
    x: M, y: 5.6, w: 12.2, h: 0.95, fill: "E7EFEB", head: "設定は加算のみ",
    body: "プロジェクトは .rig/gates.json で独自基準を足せるが、組み込み基準の削除・緩和キーは即座に拒否される。リポジトリの中のファイルが、そのリポジトリを守るゲートを弱めることはできない。",
    bodySize: 11.5, lead: 15,
  });
}

/* ------------------------------------------------------------------ 10 */
{
  const s = base("柱 ③ read-only な検証役　／　柱 ④ 明示的な accept", "N° 08");
  label(s, { text: "③  検証役はプロセスレベルで読み取り専用", x: M, y: 1.4, w: 6 });
  codeBox(s, { x: M, y: 1.72, w: 6.0, h: 0.8, size: 10.5,
    text: "claude --allowedTools Read,Grep,Glob\ncodex  --sandbox read-only" });
  bullets(s, {
    x: M, y: 2.7, w: 6.0, h: 2.0,
    items: [
      "見て、grep して、指摘は書ける",
      "編集・commit・formatter による書き換えはできない",
      "判定の一次証拠は worktree の実際の git diff。生成側のレポートは「未検証の主張」として渡されるだけ",
      "orchestrate.py probe が、この制限が実装として発動していることをプロバイダごとに確認する",
    ],
    size: 11.5,
  });

  label(s, { text: "④  accept は staged で止まり、記録は消えない", x: M + 6.4, y: 1.4, w: 6 });
  codeBox(s, { x: M + 6.4, y: 1.72, w: 6.0, h: 1.55, size: 10, lead: 14,
    text: "## rig accept — accept_requirements\n  ✓ worktree_exists            構造的\n  ✓ base_branch_recorded       構造的\n  ✓ diff_summary_generated     構造的\n  ✓ acceptance_gate_not_failed 上書き可\n  ✓ no_unrelated_diff          上書き可" });
  bullets(s, {
    x: M + 6.4, y: 3.45, w: 6.0, h: 2.0,
    items: [
      "構造的な前提は --force でも通らない。diff.md が無ければ accept できない",
      "上書きした場合は forced: true として記録され、消えない",
      "反映は squash merge の staged まで。コミットは常に人間が打つ",
      "discard は変更ファイル一覧を必ず先に見せ、--yes を要求する",
    ],
    size: 11.5,
  });
  label(s, { text: "中断しても、静かに素の直接作業へ戻らない", x: M, y: 4.9, w: 6 });
  codeBox(s, { x: M, y: 5.22, w: 6.0, h: 0.95, size: 9, lead: 13,
    text: "▸ rig | task: rig-20260704-153012-login-fix\n      | recipe: bugfix | step: test (4/7)\n      | gate: pending | mode: isolated worktree" });
  card(s, { x: M + 6.4, y: 5.22, w: 6.0, h: 0.95, head: "文脈圧縮も生き延びる",
    body: "PreCompact フックが run-state の保全指示を注入し、/rig:init は同じ保全文を CLAUDE.md の Compact Instructions にも置ける。",
    bodySize: 11, lead: 14 });
}

/* ------------------------------------------------------------------ 11 */
{
  const s = base("中身は四種類のブリック — LEGO のように合成する", "N° 09");
  const bricks = [
    ["persona", "誰が判定するか", "security-reviewer / design-reviewer / test-reviewer …"],
    ["instruction", "何をするか", "手順そのもの。薄く保たれ、エンジンには触れない"],
    ["pattern", "どう分配し、どうゲートするか", "isolated-worktree / acceptance-gate / serial"],
    ["recipe", "step の束", "bugfix / feature / review-only / release-flow / hotfix"],
  ];
  bricks.forEach((b, i) => {
    const x = M + i * 3.1;
    card(s, { x, y: 1.42, w: 2.9, h: 2.0, head: b[0], headSize: 15, headColor: ACCENT,
      body: b[1] + "\n\n" + b[2], bodySize: 11, lead: 15 });
  });
  s.addText("/rig:go はこの組み立てを自動でやる。/rig:dev は同じエンジンを recipe・step・flag すべて明示して使う上級者向けの入口。", {
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
  s.addText("flag とブリックの完全な一覧は skills/engine/SKILL.md が正本（README には複製しない＝目録ドリフト防止）。", {
    x: M, y: 5.75, w: 12.2, h: 0.3, isTextBox: true, margin: 0, fontFace: BODY_F, fontSize: 10, color: SLATE });
}

/* ------------------------------------------------------------------ 12 */
{
  const s = base("レビュアーは、測れる", "N° 10");
  s.addText("reviewer persona は単なるプロンプトではない。既知のバグを使い捨ての diff に注入し、レビューを走らせ、reviewer には見せない答案キーと突き合わせて採点する。", {
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

  card(s, { x: M, y: 3.45, w: 6.0, h: 2.6, head: "推奨される persona 更新は四カテゴリに固定",
    body: "add_checklist_item ／ adjust_severity_rule ／ add_false_positive_guard ／ strengthen_security_focus。\n\n曖昧な感想ではなく、run をまたいで集計できる形にするため。--replay はペルソナ編集後にアーカイブ済み diff へ再実行し、新旧 verdict を差分表示する。本物のコードには一切触れない。",
    bodySize: 11.5, lead: 16 });
  label(s, { text: "そして、ゴム印を名指しする（/rig:go stats）", x: M + 6.4, y: 3.45, w: 6 });
  codeBox(s, { x: M + 6.4, y: 3.78, w: 5.8, h: 2.27, size: 10, lead: 15,
    text: "Verifier behavior:\n- strict_senior_engineer: 14 runs, 6 rejects\n- product_reviewer:        6 runs, 0 rejects\n\nWarning:\nproduct_reviewer has 0 rejects across 6 runs.\nPossible rubber-stamp behavior." });
}

/* ------------------------------------------------------------------ 13 */
{
  const s = base("コマンドの地図 — 上から下へ、必要になってから降りる", "N° 11");
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
  card(s, { x: M, y: 5.35, w: 6.0, h: 1.05, head: "最初の一日に要るもの",
    body: "/rig:go と diff / accept / discard の四つだけ。", bodySize: 11.5 });
  card(s, { x: M + 6.4, y: 5.35, w: 5.8, h: 1.05, head: "覚えなくていいもの",
    body: "残り全部。必要になった日に、この表に戻ってくればいい。", bodySize: 11.5 });
}

/* ------------------------------------------------------------------ 14 */
{
  dark("第二部　pack で知識層を拡張する",
    "専用領域の知識を、口伝でもコピペでもなく、\nバージョンとハッシュと出典を持った配布物として渡す。",
    "docs/packs.md ／ rig_workbench/packs/model.py");
}

/* ------------------------------------------------------------------ 15 */
{
  const s = base("知識層とは何か、そしてなぜ二種類あるのか", "N° 12");
  card(s, {
    x: M, y: 1.42, w: 12.2, h: 0.95, fill: "E7EFEB",
    head: "知識層＝subagent のプロンプトに注入される、ドメイン記述知識",
    body: "「この会社ではバックアップをこう定義している」「この製品のユビキタス言語はこれだ」— コードを読んでも出てこない事実を、レビュアーや実装役に持たせるための層。",
    bodySize: 11.5, lead: 15,
  });
  table(s, {
    x: M, y: 2.6, w: 8.4, colW: [1.7, 3.3, 3.4],
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
    x: M + 8.7, y: 2.6, w: 3.5, h: 1.75, head: "手元で育てるなら前者",
    body: "書いてすぐ効く。棚卸しも配布も要らないうちは、これで十分。",
    bodySize: 11, lead: 15,
  });
  card(s, {
    x: M + 8.7, y: 4.5, w: 3.5, h: 2.0, head: "渡した瞬間に後者が要る", headColor: ACCENT,
    body: "誰が書いたのか。いつ見直したのか。根拠は何か。途中で書き換わっていないか。pack はこの四つを構造として持つ。",
    bodySize: 11, lead: 15,
  });
}

/* ------------------------------------------------------------------ 16 */
{
  const s = base("wiki と inject — 事実を埋め込まず、参照する", "N° 13");
  codeBox(s, { x: M, y: 1.42, w: 5.9, h: 0.85, size: 11,
    text: '# persona: house-authenticity\ninject: ["[[genre-house]]", "[[music-era-90s]]"]' });
  bullets(s, {
    x: M, y: 2.45, w: 5.9, h: 2.1,
    items: [
      "一つの概念につき一枚の正準ページ。相互リンクは [[slug]]",
      "persona は事実を本文に埋め込まず、ページを参照する",
      "埋め込むと知識が暗黙知になる。参照なら、ページを一枚直すだけで参照する全 persona の判断が同時に更新される",
      "/rig:knowledge は既定で global に書く（--project でプロジェクト overlay）",
    ],
    size: 11.5,
  });
  label(s, { text: "[[slug]] の tier 解決 — 上の層が勝つ", x: M + 6.3, y: 1.42, w: 6 });
  const tiers = [
    ["1", "project overlay", "<repo>/.claude/rig/knowledge/wiki/"],
    ["2", "global", "~/.claude/rig/knowledge/wiki/"],
    ["3", "org", "チームで共有する層（RIG_ORG_HOME）"],
    ["4", "pack 同梱", "<pack>/facets/knowledge/<slug>.md"],
    ["5", "shipped", "rig が最初から持つ正準ページ"],
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
    x: M, y: 4.85, w: 5.9, h: 1.5, fill: "E7EFEB", head: "pack の persona は、まず自分の pack を見る",
    body: "pack は自分の persona のために必要なページを同梱して持ち歩く。同じ slug をプロジェクト側に置けば、従来どおり上書きできる。",
    bodySize: 11, lead: 15,
  });
}

/* ------------------------------------------------------------------ 17 */
{
  const s = base("pack の type が決めるのは、権限", "N° 14");
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
    head: "右端の列が、この型モデルの存在理由", headColor: ALERT,
    body: "recipe に checks:（ホスト上で実行されるシェルコマンド）を書けるのは tool だけ。他の型が運ぶのは、プロバイダが読むテキストだけ。",
    bodySize: 11, lead: 15,
  });
  card(s, {
    x: M + 8.3, y: 3.75, w: 3.9, h: 1.35, head: "type ≠ kind",
    body: "kind（core / official / domain / project）は tier 順を決めるだけ。tier は権限ではない。",
    bodySize: 11, lead: 15,
  });
  card(s, {
    x: M + 8.3, y: 5.25, w: 3.9, h: 1.2, head: "既定値を持たない理由",
    body: "既定値を置くと、その決定を「決めなかった人」に渡してしまうから。",
    bodySize: 11, lead: 15,
  });
  s.addText("チームのドメイン知識を追加することが、同時にコマンド実行権限を渡すことになってはいけない — これが型モデルの引く線。", {
    x: M, y: 4.85, w: 8.0, h: 0.35, isTextBox: true, margin: 0,
    fontFace: TITLE_F, fontSize: 13, bold: true, color: INK });
  card(s, {
    x: M, y: 5.25, w: 8.0, h: 1.2, head: "どの type を選ぶか",
    body: "知識層を配るだけなら knowledge。persona も一緒に配るなら reviewer。recipe やコマンドまで含むなら skill。必要以上に強い型を選ばないことが、そのまま受け取る側への安全保証になる。",
    bodySize: 11.5, lead: 15,
  });
}

/* ------------------------------------------------------------------ 18 */
{
  const s = base("作る — init → sync → validate → doctor", "N° 15");
  codeBox(s, { x: M, y: 1.42, w: 7.0, h: 2.35, size: 10, lead: 15,
    text: "$ rig-wb pack init my-domain --type knowledge \\\n      --kind domain --root .rig/packs\ninitialized: .rig/packs/my-domain\n\nnext:\n  1. write an asset  facets/knowledge/<name>.md\n  2. rig-wb pack sync .rig/packs/my-domain\n  3. rig-wb pack validate .rig/packs/my-domain" });
  codeBox(s, { x: M, y: 3.95, w: 7.0, h: 1.35, size: 10, lead: 15,
    text: "$ vi .rig/packs/my-domain/facets/knowledge/backup.md\n$ rig-wb pack sync .rig/packs/my-domain\n  + facets/knowledge/backup.md\npack sync: 1 asset(s) declared and hashed" });
  codeBox(s, { x: M, y: 5.45, w: 7.0, h: 1.0, size: 10, lead: 15,
    text: "$ rig-wb pack doctor .rig/packs/my-domain\npack doctor: warning\n- empty_pack: .rig/packs/my-domain" });

  card(s, { x: M + 7.4, y: 1.42, w: 4.8, h: 1.65, head: "pack.yaml は手で編集しない",
    body: "全アセットをパスと sha256 で宣言し、検証時に正準形とバイト比較される。だから生成物であって、手書きの対象ではない。",
    bodySize: 11, lead: 15 });
  card(s, { x: M + 7.4, y: 3.2, w: 4.8, h: 1.65, head: "sync はディレクトリを鏡写しにする",
    body: "消したファイルは宣言からも消える。書き換えるのは assets と hashes だけで、version・description・entrypoints はあなたのもの。",
    bodySize: 11, lead: 15 });
  card(s, { x: M + 7.4, y: 4.98, w: 4.8, h: 1.47, fill: "F3EAE6", head: "valid は「完成」ではない", headColor: ALERT,
    body: "空の pack もスキーマは満たすので valid と出る。doctor がその状態を名指しする（警告では exit 0。failed だけがエラー）。",
    bodySize: 11, lead: 15 });
}

/* ------------------------------------------------------------------ 19 */
{
  const s = base("wiki を入れた瞬間、評価ゲートが立つ", "N° 16");
  card(s, {
    x: M, y: 1.42, w: 12.2, h: 0.85, fill: "E7EFEB",
    head: "wiki はプロンプト素材 — プロバイダに見せるテキストである",
    body: "だから wiki を持つ pack には、承認済みの評価ケースが最低一件必要になる。会社の知識ページも、他のプロンプト面と同じ規律で統治される。事故ではなく、意図された挙動。",
    bodySize: 11.5, lead: 15,
  });
  codeBox(s, { x: M, y: 2.5, w: 7.4, h: 2.85, size: 9.5, lead: 13,
    text: "$ rig-wb pack validate .rig/packs/my-domain\n[ERROR] prompt-bearing pack requires at least one\n        evaluation case\n\n# draft は pack の外（プロジェクト側）に書く\n#   .rig/evals/drafts/<case-id>/case.json\n#   prompt_surfaces: [\"wiki:backup-policy\"]\n$ rig-wb eval run <case-id> --phase baseline ...\n$ rig-wb eval run <case-id> --phase current  ...\n$ rig-wb eval compare --baseline ... --current ...\n$ rig-wb eval promote <case-id> ... --into <pack>\n$ rig-wb pack sync && rig-wb pack validate\nvalid: my-domain@0.1.0" });
  card(s, { x: M + 7.7, y: 2.5, w: 4.5, h: 1.5, head: "承認はフラグではない",
    body: "promote は閾値を満たさない証拠も、意味的ルーブリックが未判定のケースも拒否する。結果には署名がつくので、編集された結果は閾値を見る前に落ちる。",
    bodySize: 11, lead: 15 });
  card(s, { x: M + 7.7, y: 4.12, w: 4.5, h: 1.23, head: "draft を pack に置けない理由",
    body: "pack は宣言していないものを一切持てないから。--into が変えるのは行き先だけ。",
    bodySize: 11, lead: 15 });
  card(s, { x: M, y: 5.5, w: 12.2, h: 0.95, fill: "F3EAE6", head: "忘れやすい一手：entrypoint を書かないと、ケースは走らない", headColor: ALERT,
    body: "prompt_entrypoint はマニフェストが宣言する entrypoint を名指ししなければならない（知識 pack では kind: wiki でページ自身）。書き忘れても pack validate は通るが、pack test は structural_only と報告する — 同梱され、ハッシュされ、そして誰にも実行されない。",
    bodySize: 11, lead: 15 });
}

/* ------------------------------------------------------------------ 20 */
{
  const s = base("その pack は何について書かれているのか — knowledge ブロック", "N° 17");
  codeBox(s, { x: M, y: 1.42, w: 6.0, h: 1.75, size: 10.5, lead: 15,
    text: 'knowledge:\n  scope: ["company"]\n  topics: ["access-control", "backup"]\n  owner: "Corp IT"\n  evidence: ["情報セキュリティ規程", "運用設計書"]\n  reviewed_at: "2026-08-01T00:00:00+00:00"' });
  bullets(s, {
    x: M, y: 3.32, w: 6.0, h: 1.9,
    items: [
      "ブロック自体は任意。だが半分だけ書かれたブロックは許されない — 置いた以上は五つとも必須",
      "理由は reviewed_at にある。見直し日のない知識宣言こそ、誰にも気づかれずに腐る",
      "どの type でも宣言できる。これは説明であって、権限ではない",
      "scope は company のような素の次元か、product:northwind-one のような値つき",
    ],
    size: 11,
  });
  card(s, { x: M, y: 5.4, w: 6.0, h: 1.05, head: "evidence だけソートを要求されない",
    body: "人が書いた文書の題名で、書いた言語のまま。順序自体が「主に何に依拠しているか」を運ぶ。重複だけは拒否される。",
    bodySize: 10.5, lead: 14 });

  label(s, { text: "探す — 選び出すが、決めはしない", x: M + 6.4, y: 1.42, w: 6 });
  codeBox(s, { x: M + 6.4, y: 1.75, w: 5.8, h: 3.05, size: 9.5, lead: 13,
    text: "$ rig-wb pack knowledge --topic backup\ncompany-security@0.1.0  company   Corp IT\n  reviewed 2026-08-01\n  evidence: 情報セキュリティ規程, 運用設計書\n  wiki: pack://project/company-security/\n          facets/knowledge/backup-policy.md\nproduct-security@0.1.0  product:northwind-one\n  reviewed 2026-07-15\n  evidence: サービス仕様書\n\nscope is ambiguous: company, product:northwind-one\n  — narrow with --scope before treating any of\n    these as the answer" });
  card(s, { x: M + 6.4, y: 4.95, w: 5.8, h: 1.5, fill: "E7EFEB",
    head: "どちらのつもりだったかは、どの pack にも書かれていない",
    body: "質問者についての事実だから。だから rig は推測せず、問いが開いている事実と選択肢の正確な集合を返す。影に隠れたページも、隠さずラベルつきで列挙する。",
    bodySize: 10.5, lead: 14 });
}

/* ------------------------------------------------------------------ 21 */
{
  const s = base("渡す — bundle・named source・lock", "N° 18");
  codeBox(s, { x: M, y: 1.42, w: 6.2, h: 1.55, size: 10, lead: 15,
    text: "$ rig-wb pack bundle .rig/packs/my-domain\nbundled: my-domain@0.1.0 (3 file(s))\n  -> dist/my-domain-0.1.0.zip\n  sha256: 7ef9b1a3...\n$ rig-wb pack install dist/... --scope project" });
  codeBox(s, { x: M, y: 3.15, w: 6.2, h: 1.55, size: 10, lead: 15,
    text: "$ rig-wb pack source add product \\\n    --scheme git+ssh \\\n    --url git@github.com:acme/rig-pack-{pack}.git\n$ rig-wb pack install product:northwind@1.4.0\n$ rig-wb pack verify-sources --scope project" });
  card(s, { x: M, y: 4.88, w: 6.2, h: 1.55, fill: "E7EFEB", head: "rig は資格情報を一切持たない",
    body: "git を呼ぶだけで、認証は設定済みのもの（SSH agent・credential helper・CI secret）が答える。lock が記録するのはソースの名前と commit であって URL ではない。",
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
    body: "pack がインストール済みで、valid で、しかし完全に影に隠れている状態。info は身元しか答えないので、explain で tier 争いの勝敗を見る。",
    bodySize: 11, lead: 15 });
  card(s, { x: M + 6.6, y: 5.45, w: 5.6, h: 0.98, head: "digest は「誰が」を答えない",
    body: "同じバイト列であることだけを保証する。出所を答えるのは署名と trust root。",
    bodySize: 11, lead: 15 });
}

/* ------------------------------------------------------------------ 22 */
{
  const s = base("引っかかりやすいところ", "N° 19");
  const traps = [
    ["valid は完成ではない", "空の pack も valid になる。doctor の empty_pack 警告を見る"],
    ["pack.yaml を手で書かない", "「たまたま一致する」か「間違っている」かにしかならない"],
    ["署名した pack は sync できない", "署名を外し、sync し、鍵で署名し直す"],
    ["install だけではコマンドにならない", "command アセットは、明示登録を扱えるホスト向けの資料"],
    ["project pack は初回に同意が要る", "RIG_ALLOW_PROJECT_PACKS=1。同意は内容ハッシュに紐づく"],
    ["user / org は品質検証を回避できない", "--allow-unverified は project スコープだけ"],
    ["mock は品質の証拠ではない", "結果は non_quality_mock と明示される"],
    ["private は署名の代わりにならない", "private リポジトリの pack も同じ検証を全部通る"],
  ];
  traps.forEach((t, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    const x = M + col * 6.4, y = 1.42 + row * 1.3;
    card(s, { x, y, w: 6.0, h: 1.15, num: String(i + 1), numColor: ALERT,
      head: t[0], headSize: 11.5, body: t[1], bodySize: 10.5, lead: 14 });
  });
}

/* ------------------------------------------------------------------ 23 */
{
  const s = dark("明日からの順番", null, null, 1.15);
  const steps = [
    ["1", "まず /rig:go だけ使う", "diff / accept / discard の四つで一週間。ゲートに一度落ちて、なぜ落ちたかを読むところまで来れば、rig が何を売っているかは体で分かる。"],
    ["2", "ページを一枚書く", "/rig:knowledge で自分のドメインを一枚書き、persona の inject: から参照させる。効くかどうかをここで確かめる。"],
    ["3", "確信してから pack にする", "rig-wb pack init --type knowledge で配布物に格上げする。順番を逆にすると、まだ効くか分からないものに評価ケースを書く羽目になる。"],
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
  s.addText("一次資料：README.ja.md（全体像）　／　docs/packs.md（pack の仕様）　／　skills/engine/SKILL.md（ブリック目録の正本）", {
    x: M + 0.35, y: 6.72, w: 12, h: 0.35, isTextBox: true, margin: 0,
    fontFace: BODY_F, fontSize: 11, color: "8A908C" });
  s.addText("rig 入門", { x: M + 0.35, y: 0.72, w: 6, h: 0.3, isTextBox: true, margin: 0,
    fontFace: BODY_F, fontSize: 11, bold: true, color: ACCENT });
}

pres.writeFile({ fileName: "rig-intro.pptx" }).then(() => console.log("written: rig-intro.pptx (" + n + " slides)"));
