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
    "AI に仕事を任せるときの、隔離と検証のしくみ。\nはじめて触る人から、自分のドメインを教え込む人まで。",
    "典拠：README.ja.md ／ docs/packs.md ／ skills/engine/SKILL.md"
  );
  s.addText("第一部　rig を知る　　|　　第二部　pack で知識層を拡張する", {
    x: M + 0.35, y: 5.5, w: 11, h: 0.4, isTextBox: true, margin: 0,
    fontFace: BODY_F, fontSize: 13, color: "9AA39C",
  });
  s.addNotes("rig をまったく知らない人向けの資料です。前半で安全フローの仕組み、後半で pack による知識層の拡張を扱います。");
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
  s.addText("典拠は rig リポジトリの一次資料だけです。仕様はまだ変わるので、引数の正確なところは各コマンドの --help を見てください。", {
    x: M, y: 6.3, w: W - M * 2, h: 0.5, isTextBox: true, margin: 0,
    fontFace: BODY_F, fontSize: 10.5, color: SLATE,
  });
}

/* --- intro 01: why --- */
{
  const s = base("なぜ rig を作ったか", "はじめに · 01");
  card(s, { x: M, y: 1.42, w: 12.2, h: 1.2, fill: "E7EFEB" });
  s.addText("オーケストレータは「どう動かすか」を決めます。rig が決めるのは「その結果を受け入れてよいか」です。", {
    x: M + 0.35, y: 1.56, w: 11.5, h: 0.55, isTextBox: true, margin: 0,
    fontFace: TITLE_F, fontSize: 16, bold: true, color: INK });
  s.addText("docs/landscape.md より。rig に機能を足すかどうかは、この一行で判断しています。", {
    x: M + 0.35, y: 2.16, w: 11.5, h: 0.3, isTextBox: true, margin: 0,
    fontFace: BODY_F, fontSize: 11, color: SLATE });

  label(s, { text: "出発点にあった三つの観測", x: M, y: 2.68, w: 6 });
  const obs = [
    ["1", "書く速度だけが上がった", "コードを書く速度は上がりました。でも、出てきたものを受け入れてよいか判定するのは、相変わらず人間の目視でした。"],
    ["2", "「できました」に根拠がない", "モデルが「できました」と言っても、それは完了の根拠になりません。それなのに、他に判断材料がないのが普通でした。"],
    ["3", "失敗が手元に残る", "うまくいかなかった試行が、作業ツリーにそのまま混ざります。何を残して何を捨てるかを考えること自体が手間になります。"],
  ];
  obs.forEach((o, i) => {
    card(s, { x: M, y: 3.02 + i * 1.13, w: 6.0, h: 1.0, num: o[0], head: o[1], headSize: 12.5,
      body: o[2], bodySize: 10.5, lead: 13 });
  });

  card(s, { x: M + 6.4, y: 3.02, w: 6.0, h: 1.7, head: "rig が引き受けたのは一箇所だけです",
    body: "受け入れの判定を、人間の目視から機械の関門へ移すことです。速く書かせることでも、賢く書かせることでもありません。",
    bodySize: 11.5, lead: 16 });
  card(s, { x: M + 6.4, y: 4.9, w: 6.0, h: 1.45, fill: "F3EAE6", head: "裏返して言うと", headColor: ALERT,
    body: "rig は品質を自動的に生みません。あなたが決めた基準を AI に無視させない、それだけです。基準を作るのは人間の仕事のままです。",
    bodySize: 11.5, lead: 16 });
}

/* --- intro 02: the four common patterns --- */
{
  const s = base("よくある AI の使い方と、そこで起きること", "はじめに · 02");
  const pats = [
    ["チャットに貼って、返ってきたコードを貼り戻す",
      "差分の出所が消えます。どこを変えたかは人間の記憶にしか残らず、無関係な変更が混ざっても検出する場所がありません。"],
    ["エージェントに作業ツリーへ直接書かせる",
      "失敗した試行がそのまま手元に残ります。機械的なチェックを誰も配線していなければ、そのチェックは一度も走りません。"],
    ["CLAUDE.md や AGENTS.md にルールを書く",
      "文章でのお願いは守られないことがあります。「テストを実行して」と書くのと、hook で毎回強制するのとでは信頼性が違います。"],
    ["AI にレビューさせる",
      "書いた側と採点する側が同系統だと、自己評価が甘くなります。しかもそのレビュアーの検出率を、誰も測っていません。"],
  ];
  pats.forEach((p, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    card(s, { x: M + col * 6.4, y: 1.42 + row * 1.95, w: 6.0, h: 1.75,
      num: String(i + 1), head: p[0], headSize: 12.5, body: p[1], bodySize: 11, lead: 15 });
  });
  card(s, { x: M, y: 5.35, w: 12.2, h: 1.05, fill: "F3EAE6",
    head: "四つに共通するのは、失敗したかどうかが分からないことです", headColor: ALERT,
    body: "どれも普通に成果は出ます。困るのは失敗する確率ではなく、失敗しても誰も気づかないまま次へ進んでしまうことです。",
    bodySize: 11.5, lead: 15 });
}

/* --- intro 03: the four holes --- */
{
  const s = base("原因をたどると、いつも同じ四つの穴に行き着きます", "はじめに · 03");
  const holes = [
    ["計算的センサーがループに入っていない", "最頻",
      "テストや lint が「ある」だけで、エージェントの実行ループにバックプレッシャーとして入っていません。あることと、効いていることは別です。"],
    ["検証ループそのものが無い", "自己採点",
      "モデルに自分の仕事を検証する手段を与えると、品質が 2〜3 倍になると言われています（Boris Cherny）。裏を返せば、手段が無いエージェントは自己評価が甘くなります。"],
    ["評価と計測をしていない", "測れない",
      "ルールは足したのに、効果を測っていません。よかれと思った追加が逆効果になることもあります（Context Rot。文脈が長いほど性能が落ちる現象です）。"],
    ["ハーネスが厚すぎる", "肥大",
      "Thin Harness, Fat Skills（Garry Tan）という言い方があります。ループ管理は薄く、知能は Skills に、実行は決定論的ツールに委ねます。親ループが太ったら設計を疑ってください。"],
  ];
  holes.forEach((h, i) => {
    const y = 1.42 + i * 1.28;
    card(s, { x: M, y, w: 12.2, h: 1.12, num: String(i + 1),
      numColor: i === 0 ? ALERT : ACCENT, head: h[0], headSize: 13, body: h[2], bodySize: 11, lead: 15 });
    s.addText(h[1], { x: M + 10.6, y: y + 0.24, w: 1.4, h: 0.3, isTextBox: true, margin: 0,
      align: "right", fontFace: BODY_F, fontSize: 10.5, bold: true, color: i === 0 ? ALERT : SLATE });
  });
  s.addText("典拠は skills/engine/facets/knowledge/harness-taxonomy.md です。rig 自身が /rig:harness で使っている観点カタログです。", {
    x: M, y: 6.6, w: 12.2, h: 0.3, isTextBox: true, margin: 0, fontFace: BODY_F, fontSize: 10, color: SLATE });
}

/* --- intro 04: ○○ engineering --- */
{
  const s = base("プロンプトの外側にコンテキスト、その外側にハーネス", "はじめに · 04");
  s.addText("「〇〇エンジニアリング」は別々の流派ではなく、入れ子になっています。外側を設計しないまま内側だけ磨いても、効果は安定しません。", {
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
  s.addText("一回の指示の書き方です。ここだけを磨く人が一番多いのですが、\n条件が変わると同じようには効きません。\n\nrig はプロンプト面（persona・instruction・recipe・wiki）を\n「コンパイラが検査してくれない部分」として扱い、\n変更するときは承認済みの評価ケースを求めます。",
    { x: M + 1.14, y: 4.02, w: 4.4, h: 1.8, isTextBox: true, margin: 0, valign: "top",
      fontFace: BODY_F, fontSize: 10.5, color: "C9CCC8", lineSpacing: 14 });

  card(s, { x: M + 7.0, y: 1.85, w: 5.2, h: 2.15, head: "ハーネスは二層に分かれます",
    body: "一つはエージェントハーネスで、Claude Code や Codex に内蔵されたループ・ツール・メモリです。もう一つはユーザーハーネスで、使う側が組む CLAUDE.md・Skills・Hooks・MCP・テスト・lint・CI・recipe です。",
    bodySize: 11.5, lead: 16 });
  card(s, { x: M + 7.0, y: 4.15, w: 5.2, h: 1.25, fill: "E7EFEB", head: "rig が作るのは後者だけです",
    body: "内蔵ループには手を入れません。その外側に、薄い品質・安全レイヤーとして乗ります。",
    bodySize: 11.5, lead: 16 });
  card(s, { x: M + 7.0, y: 5.55, w: 5.2, h: 1.05, head: "だから engine は変えません",
    body: "拡張は pack として上に乗せます。",
    bodySize: 11.5, lead: 16 });
}

/* --- intro 05: the 2x2 --- */
{
  const s = base("ハーネスを 2×2 で棚卸しすると、足りない場所が見えます", "はじめに · 05");
  s.addText("軸は二つです。①コードで判定する計算的なものか、LLM が判断する推論的なものか。②先回りして方向づけるガイドか、あとから検知するセンサーか。", {
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
  card(s, { x: M + 8.6, y: 1.85, w: 3.6, h: 1.55, head: "四象限が揃ってはじめて強くなります",
    body: "レビューはするがテストが無い状態も、テストは通るが設計の妥当性を誰も見ていない状態も、どちらも穴です。",
    bodySize: 11, lead: 15 });
  card(s, { x: M + 8.6, y: 3.5, w: 3.6, h: 1.55, fill: "E7EFEB", head: "まず計算的センサーから",
    body: "計算的センサーは口説き落とせません。だから一番強いバックプレッシャーになります。推論的センサーはその次に置きます。",
    bodySize: 11, lead: 15 });
  card(s, { x: M + 8.6, y: 5.15, w: 3.6, h: 1.45, fill: "F3EAE6", head: "「存在する」≠「効いている」", headColor: ALERT,
    body: "どの hook にも acceptance gate にも繋がっていない lint は、ループに何の圧力もかけていません。",
    bodySize: 11, lead: 15 });
  card(s, { x: M, y: 5.15, w: 8.3, h: 1.45, head: "この 2×2 は自分のプロジェクトにも当てられます",
    body: "/rig:harness が読み取り専用で棚卸しし、空いている象限と、あるのに効いていない資産を名指しします。処方は新しいルールを足すことではなく、接続する・強制する・減らすの三つです。",
    bodySize: 11.5, lead: 16 });
}

/* --- intro 06: usual responses and their limits --- */
{
  const s = base("よく採られる対応と、それがどこで止まるか", "はじめに · 06");
  table(s, {
    x: M, y: 1.42, w: 7.6, colW: [2.6, 5.0], rowH: 0.66, size: 10.5, boldFirst: true,
    head: ["よく採られる対応", "どこで止まるか"],
    rows: [
      ["プロンプトを磨く", "条件が変わると再現しません。効果を測っていないので、良くなったかどうかも分かりません"],
      ["CLAUDE.md にルールを足す", "文章でのお願いは、守られないことがあります。足しすぎると文脈が濁って逆効果にもなります"],
      ["lint / test / CI を用意する", "用意しただけでは、エージェントのループの外にあります。人間が CI を待つまで誰も見ません"],
      ["AI レビューを足す", "書いた側と同系統のモデルだと甘くなります。検出率が分からないので、通っても意味があるか判断できません"],
      ["人間のレビューを増やす", "人手が足りません。形だけのレビューになっても、そうなったこと自体に気づけません"],
    ],
  });
  label(s, { text: "効く順序があります", x: M + 7.9, y: 1.42, w: 4.8 });
  const order = [
    ["接続する", "すでにある lint・型・テストを hook と acceptance gate に繋いで、ループの中へ入れます"],
    ["強制する", "大事なルールを、文章でのお願いから決定論的な強制へ移します"],
    ["減らす", "効いていないルールを落とします。足したときは、減らす候補も一緒に考えます"],
  ];
  order.forEach((o, i) => {
    card(s, { x: M + 7.9, y: 1.78 + i * 1.28, w: 4.85, h: 1.15, num: String(i + 1),
      head: o[0], headSize: 12.5, body: o[1], bodySize: 10.5, lead: 13 });
  });
  card(s, { x: M, y: 5.5, w: 7.6, h: 0.95, fill: "E7EFEB", head: "新しいルールを書くのは最後です",
    body: "よかれと思って足したルールが、かえって邪魔をすることがあります。まず接続し、次に強制し、そのうえで減らしてください。",
    bodySize: 11.5, lead: 15 });
  card(s, { x: M + 7.9, y: 5.65, w: 4.85, h: 0.8, fill: "F3EAE6", head: "効果を測らないと回せません", headColor: ALERT,
    body: "何が効いたか分からないままだと、この三つの順序自体が回りません。",
    bodySize: 10.5, lead: 13 });
}

/* --- intro 07: what rig actually does --- */
{
  const s = base("rig は四つの象限をこう埋めます", "はじめに · 07");
  const quad = [
    ["計算的ガイド", "hook・manifest（.claude/rig.md）・recipe・scaffold です。作業を始める前に、走らせ方と既定値を決めておきます。"],
    ["計算的センサー", "acceptance-gate の機械検証（build / lint / test / 型）と、決定論センサー（秘密情報・インジェクション・破壊的コマンド・ゲート改竄・schema-diff）です。"],
    ["推論的ガイド", "persona・instruction・知識層の wiki です。ドメインの前提を、判断する側に持たせます。"],
    ["推論的センサー", "reviewer persona による並列レビューです。読み取り専用をプロセスレベルで強制し、drill で検出率を実測します。"],
  ];
  quad.forEach((q, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    card(s, { x: M + col * 4.25, y: 1.42 + row * 1.75, w: 4.05, h: 1.6,
      fill: i === 1 ? "E7EFEB" : TINT, head: q[0], headSize: 13, body: q[1], bodySize: 10.5, lead: 14 });
  });
  card(s, { x: M, y: 4.95, w: 8.3, h: 1.45, head: "2×2 の外にも、rig 固有のものが二つあります",
    body: "一つは隔離です。判定が終わるまで、成果物は作業ツリーに触れません。もう一つは記録です。何を試して、どの基準で、なぜ受け入れた（あるいは拒否した）かが run log と監査証跡に残ります。",
    bodySize: 11.5, lead: 16 });

  card(s, { x: M + 8.6, y: 1.42, w: 3.6, h: 3.55, fill: "F3EAE6", head: "引き受けないこと", headColor: ALERT,
    body: "・IDE や GUI の提供\n・汎用エージェント群のプラットフォーム\n・複数モデルの回答を混ぜて一つにすること\n・ワークフロー DSL の表現力競争\n\nrig が複数モデルを使うのは、検証役を生成役から独立させるためです。答えを合成するためではありません。",
    bodySize: 11, lead: 16 });
  card(s, { x: M + 8.6, y: 5.15, w: 3.6, h: 1.25, head: "はっきり目標にしていること",
    body: "他のオーケストレータが作った成果物にも、同じ受け入れ契約を当てます（workbench.py import）。",
    bodySize: 11, lead: 15 });
}

/* --- part 1 divider --- */
{
  dark("第一部　rig を知る",
    "ここからは、その仕組みが実際にどう動くかを見ていきます。\n分類、隔離、ゲート、受け入れの順です。",
    "README.ja.md §5〜§12");
}

/* ================= END INTRO BLOCK ================= */

/* ------------------------------------------------------------------ 03 */
{
  const s = base("rig とは何か", "N° 01");
  s.addText("困るのは AI が間違えることではありません。間違えたことに誰も気づかないまま、本体に混ざってしまうことです。", {
    x: M, y: 1.42, w: W - M * 2, h: 0.4, isTextBox: true, margin: 0,
    fontFace: TITLE_F, fontSize: 15, bold: true, color: INK,
  });

  const steps = [
    ["分類", "bugfix / feature / refactor\n/ review / documentation …"],
    ["recipe 選択", "選んだ理由を\n先に一行で出す"],
    ["隔離 worktree", "作業ツリーとは別の\n使い捨てブランチ"],
    ["acceptance-gate", "機械的な基準で\n合否を判定"],
    ["accept / discard", "反映は staged まで。\n判断は常に人間"],
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
    body: "あなたが決めた品質基準を、AI に無視させないための道具です。基準を作るのは人間の仕事のままで、rig の仕事は強制と測定です。",
  });
  card(s, {
    x: M + 4.13, y: 4.2, w: 3.93, h: 1.9, head: "そのかわり、対価があります",
    body: "隔離と検証と記録のために、速度とトークンをわざと払っています。速く書かせたいだけなら、モデルに直接頼むほうが速いです。",
  });
  card(s, {
    x: M + 8.26, y: 4.2, w: 3.94, h: 1.9, head: "効いてくる場面", fill: "E7EFEB",
    body: "失敗したときのコストが速度より高い場面です。本番に触れるコード、他人が読むコード、あとで誰も検証しないコードが当てはまります。",
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
    body: "安全性の核になる部分は実装済みで、リポジトリ自身のテストで裏づけがあります。分類・隔離・acceptance-gate・明示的な accept と discard です。\n\nその上の観測系（drill・board・stats・GitHub 連携）は使えますが、まだ発展途中です。",
    bodySize: 11.5, lead: 16,
  });
  card(s, {
    x: M + 7.9, y: 4.05, w: 4.3, h: 2.05, fill: "F3EAE6", head: "正直なスコープ", headColor: ALERT,
    body: "このプロジェクトは、まだ出していない機能を Planned として載せない方針です。表に無いコマンドは、まだ出荷されていません。",
    bodySize: 11.5, lead: 16,
  });
  card(s, {
    x: M, y: 4.65, w: 7.6, h: 1.75, head: "隔離が task ごとに閉じていると、何が嬉しいか",
    body: "複数のタスクを同時に走らせても、別々の worktree と branch なので安全です。/rig:queue に積んで一括で GO しても、並列プロセスがファイルを取り合うことはありません。終わったあとは /rig:go board を見れば、どの端末のどのプロセスが動かしたかに関わらず、全タスクの状態が一つの表に出ます。",
    bodySize: 11.5, lead: 16,
  });
}

/* ------------------------------------------------------------------ 05 */
{
  const s = base("入口は二つ。中身は同じエンジン", "N° 03");
  card(s, { x: M, y: 1.45, w: 6.0, h: 2.35, num: "A", head: "Claude Code の中では、プラグイン",
    body: "スラッシュコマンドはここから来ます。安全フローは一通りこれだけで動きます。", bodySize: 11.5 });
  codeBox(s, { x: M + 0.86, y: 2.78, w: 5.0, h: 0.78,
    text: "/plugin marketplace add itoh-shun/sito-plugins\n/plugin install rig@sito-plugins", size: 10 });

  card(s, { x: M + 6.4, y: 1.45, w: 6.0, h: 2.35, num: "B", head: "それ以外の場所では、rig-wb CLI",
    body: "CI やスクリプト、別のアシスタント（Codex / Cursor）から、同じ recipe とゲートを回せます。", bodySize: 11.5 });
  codeBox(s, { x: M + 7.26, y: 2.78, w: 5.0, h: 0.78,
    text: "pipx install git+https://github.com/itoh-shun/rig.git\nrig-wb version", size: 10 });

  s.addText("両方は要りません。二つ目が必要なのは、Claude Code の外にあるものを同じ recipe とゲートに通したいときだけです。/rig:setup を使えば、Claude Code の中から入れられます。", {
    x: M, y: 3.92, w: W - M * 2, h: 0.32, isTextBox: true, margin: 0,
    fontFace: BODY_F, fontSize: 11, color: SLATE,
  });

  label(s, { text: "最初の三十秒。設定はゼロで始められます", x: M, y: 4.34, w: 8 });
  codeBox(s, { x: M, y: 4.66, w: 6.0, h: 1.72,
    text: '/rig:go "ログインバグを直して"\n/rig:go "このPRを厳しめにレビューして"\n/rig:go "今の変更が安全か確認して"\n\n/rig:go diff      # 何が変わったか\n/rig:go accept    # 反映（gate 未達なら拒否）\n/rig:go discard   # 破棄', size: 10.5, lead: 16 });
  codeBox(s, { x: M + 6.4, y: 4.66, w: 6.0, h: 1.72,
    text: "▸ rig\ntask:     ログインバグを直して\ndetected: bugfix\nrecipe:   bugfix — 「バグ」「直して」を検出\nmode:     isolated worktree\ngate:     standard + bugfix", size: 10.5, lead: 16 });
  s.addText("manifest も gates.json も persona 設定も要りません。すべて後から足せます", {
    x: M, y: 6.46, w: 6, h: 0.3, isTextBox: true, margin: 0, fontFace: BODY_F, fontSize: 10, color: SLATE });
  s.addText("なぜその段取りになったかを、走り出す前に一行で出します", {
    x: M + 6.4, y: 6.46, w: 6, h: 0.3, isTextBox: true, margin: 0, fontFace: BODY_F, fontSize: 10, color: SLATE });
}

/* ------------------------------------------------------------------ 06 */
{
  const s = base("rig が安全だと言える四つの理由", "N° 04");
  s.addText("安全だと言えるのは、次の四つが文章ではなく配線として入っているからです。", {
    x: M, y: 1.4, w: 11, h: 0.3, isTextBox: true, margin: 0, fontFace: BODY_F, fontSize: 12.5, color: INK });
  const items = [
    ["1", "隔離された worktree", "タスクごとに専用の worktree と使い捨てブランチを作ります。rig があなたの作業ツリーに直接書き込むことはありません。失敗しても中断しても手元は汚れませんし、隔離がタスクごとに閉じているので同時実行も安全です。"],
    ["2", "acceptance-gate", "「完了しました」と言われても完了にはなりません。無関係な差分・テスト・型・リスク記述・秘密情報を機械的に確認して、はじめて反映候補になります。failed か pending が一件でも残っていれば accept は拒否されます（exit 1）。"],
    ["3", "read-only な検証役", "実装する AI と検証する AI を分け、検証側はプロセスレベルで読み取り専用に固定します。お願いではなく強制です。判定の一次証拠に使うのは自己申告のレポートではなく、worktree の実際の git diff です。"],
    ["4", "明示的な accept と、消えない記録", "accept は squash merge で staged（未コミット）まで進めます。コミットは常に人間が打ちます。discard しても run log は残るので、何を試してなぜ捨てたかを後から辿れます。構造的な前提は --force でも通りません。"],
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
    x: M + 7.2, y: 4.5, w: 5.2, h: 1.6, head: "queue は accept までは進めません",
    body: "queue の verifier が見るのは二つだけです。gate が確定したか、worktree の中で完結して本体に書いていないか。反映するかどうかの判断は人間に残ります。終わったら /rig:go board で確認してください。",
    bodySize: 11.5, lead: 16,
  });
}

/* ------------------------------------------------------------------ 08 */
{
  const s = base("柱 ② acceptance-gate は standard と種別プリセットの合成です", "N° 06");
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
    x: M + 8.6, y: 1.42, w: 3.6, h: 2.15, head: "状態は四つ",
    body: "各基準は根拠つきで passed / failed / warning / skipped として記録されます。全体は passed / passed_with_warnings / failed / pending に集約されます。",
    bodySize: 11, lead: 15,
  });
  card(s, {
    x: M + 8.6, y: 3.7, w: 3.6, h: 2.5, fill: "F3EAE6", head: "accept が止まる条件", headColor: ALERT,
    body: "failed か pending が一件でも残っていれば、accept は機械的に拒否されます（exit 1）。\n\nwarning は accept を止めません。ただし必ず提示されるので、黙って握りつぶされることはありません。",
    bodySize: 11, lead: 15,
  });
  s.addText("正本は scripts/workbench.py gates です。プロジェクト側は .rig/gates.json の extra_criteria で基準を足せます。", {
    x: M, y: 6.35, w: 8.3, h: 0.3, isTextBox: true, margin: 0, fontFace: BODY_F, fontSize: 10, color: SLATE });
}

/* ------------------------------------------------------------------ 09 */
{
  const s = base("基準は自己申告ではなく、機械センサーが裏づけます", "N° 07");
  const sensors = [
    ["no_secret_leak", "task diff に決定論的なシークレットスキャンをかけます。検出があれば failed です。抜粋は常にマスクされます。"],
    ["no_injection_markers", "文章面のインジェクション・マーカーを探します。不可視文字や bidi Unicode は fail、指示を上書きする言い回しは warning です。"],
    ["no_destructive_operation", "破壊的なコマンドを探します。rm -rf / ・mkfs・DROP DATABASE は fail、force push や TRUNCATE は warning です。"],
    ["no_gate_tampering", "gates.json・recipes・CI workflow を編集していたら fail です。既存テストの改変・assert 削除・skip 追加は warning になります。"],
    ["public_api_changes_documented", "OpenAPI の schema-diff を取ります。API が変わったのに diff サマリに記述が無ければ warning に落とします。"],
    ["prompt_regression_passed", "diff が prompt 面に触れたときだけ自動で足されます。--set による手動上書きを拒否する唯一の基準です。"],
  ];
  sensors.forEach((sn, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    const x = M + col * 6.4, y = 1.42 + row * 1.35;
    card(s, { x, y, w: 6.0, h: 1.18, head: sn[0], headSize: 11.5, body: sn[1], bodySize: 10.5, lead: 14 });
  });
  card(s, {
    x: M, y: 5.6, w: 12.2, h: 0.95, fill: "E7EFEB", head: "設定は足す方向にしか動きません",
    body: "プロジェクトは .rig/gates.json で独自の基準を足せます。ただし組み込み基準の削除キーや緩和キーは、その場で拒否されます。リポジトリの中のファイルが、そのリポジトリを守るゲートを弱めることはできません。",
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
      "見て、grep して、指摘を書くことはできます",
      "編集も commit も、formatter による書き換えもできません",
      "判定の一次証拠は worktree の実際の git diff です。生成側のレポートは「未検証の主張」として渡されるだけです",
      "この制限が実装として効いていることは、orchestrate.py probe がプロバイダごとに確認します",
    ],
    size: 11.5,
  });

  label(s, { text: "④  accept は staged で止まり、記録は消えません", x: M + 6.4, y: 1.4, w: 6 });
  codeBox(s, { x: M + 6.4, y: 1.72, w: 6.0, h: 1.55, size: 10, lead: 14,
    text: "## rig accept — accept_requirements\n  ✓ worktree_exists            構造的\n  ✓ base_branch_recorded       構造的\n  ✓ diff_summary_generated     構造的\n  ✓ acceptance_gate_not_failed 上書き可\n  ✓ no_unrelated_diff          上書き可" });
  bullets(s, {
    x: M + 6.4, y: 3.45, w: 6.0, h: 2.0,
    items: [
      "構造的な前提は --force でも通りません。diff.md が無ければ accept できません",
      "上書きした場合は forced: true として記録され、あとから消せません",
      "反映は squash merge の staged までです。コミットは常に人間が打ちます",
      "discard は変更ファイルの一覧を必ず先に見せ、--yes を求めます",
    ],
    size: 11.5,
  });
  label(s, { text: "中断しても、黙って素の作業には戻りません", x: M, y: 4.9, w: 6 });
  codeBox(s, { x: M, y: 5.22, w: 6.0, h: 0.95, size: 9, lead: 13,
    text: "▸ rig | task: rig-20260704-153012-login-fix\n      | recipe: bugfix | step: test (4/7)\n      | gate: pending | mode: isolated worktree" });
  card(s, { x: M + 6.4, y: 5.22, w: 6.0, h: 0.95, head: "文脈が圧縮されても残ります",
    body: "PreCompact フックが run-state の保全指示を差し込みます。/rig:init を使えば、同じ保全文を CLAUDE.md の Compact Instructions にも置けます。",
    bodySize: 11, lead: 14 });
}

/* ------------------------------------------------------------------ 11 */
{
  const s = base("中身は四種類のブリックで、LEGO のように組み合わせます", "N° 09");
  const bricks = [
    ["persona", "誰が判定するか", "security-reviewer / design-reviewer / test-reviewer …"],
    ["instruction", "何をするか", "手順そのものです。薄く保たれ、エンジンには触れません"],
    ["pattern", "どう分配し、どうゲートするか", "isolated-worktree / acceptance-gate / serial"],
    ["recipe", "step の束", "bugfix / feature / review-only / release-flow / hotfix"],
  ];
  bricks.forEach((b, i) => {
    const x = M + i * 3.1;
    card(s, { x, y: 1.42, w: 2.9, h: 2.0, head: b[0], headSize: 15, headColor: ACCENT,
      body: b[1] + "\n\n" + b[2], bodySize: 11, lead: 15 });
  });
  s.addText("/rig:go はこの組み立てを自動でやります。/rig:dev は同じエンジンを、recipe・step・flag をすべて自分で指定して使う入口です。", {
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
  s.addText("flag とブリックの完全な一覧は skills/engine/SKILL.md が正本です。README には複製しません。二重に書くと目録がずれるためです。", {
    x: M, y: 5.75, w: 12.2, h: 0.3, isTextBox: true, margin: 0, fontFace: BODY_F, fontSize: 10, color: SLATE });
}

/* ------------------------------------------------------------------ 12 */
{
  const s = base("レビュアーの実力は測れます", "N° 10");
  s.addText("reviewer persona は単なるプロンプトではありません。既知のバグを使い捨ての diff に仕込んでレビューを走らせ、reviewer には見せない答案キーと突き合わせて採点します。", {
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

  card(s, { x: M, y: 3.45, w: 6.0, h: 2.6, head: "persona の更新案は四カテゴリに固定されています",
    body: "add_checklist_item ／ adjust_severity_rule ／ add_false_positive_guard ／ strengthen_security_focus の四つです。\n\n曖昧な感想ではなく、run をまたいで集計できる形にするためです。--replay を使うと、persona を編集したあとにアーカイブ済みの diff へ再実行し、新旧の verdict を並べて見せます。本物のコードには一切触れません。",
    bodySize: 11.5, lead: 16 });
  label(s, { text: "形だけのレビューも名指しします（/rig:go stats）", x: M + 6.4, y: 3.45, w: 6 });
  codeBox(s, { x: M + 6.4, y: 3.78, w: 5.8, h: 2.27, size: 10, lead: 15,
    text: "Verifier behavior:\n- strict_senior_engineer: 14 runs, 6 rejects\n- product_reviewer:        6 runs, 0 rejects\n\nWarning:\nproduct_reviewer has 0 rejects across 6 runs.\nPossible rubber-stamp behavior." });
}

/* ------------------------------------------------------------------ 13 */
{
  const s = base("コマンドの地図。上から順に、必要になってから覚えます", "N° 11");
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
    body: "/rig:go と diff / accept / discard の四つだけです。", bodySize: 11.5 });
  card(s, { x: M + 6.4, y: 5.35, w: 5.8, h: 1.05, head: "覚えなくていいもの",
    body: "残り全部です。必要になった日に、この表へ戻ってきてください。", bodySize: 11.5 });
}

/* ------------------------------------------------------------------ 14 */
{
  dark("第二部　pack で知識層を拡張する",
    "専用領域の知識を、口伝でもコピペでもなく、\nバージョンとハッシュと出典を持った配布物として渡します。",
    "docs/packs.md ／ rig_workbench/packs/model.py");
}

/* ------------------------------------------------------------------ 15 */
{
  const s = base("知識層とは何か。なぜ二種類あるのか", "N° 12");
  card(s, {
    x: M, y: 1.42, w: 12.2, h: 0.95, fill: "E7EFEB",
    head: "知識層とは、subagent のプロンプトに注入されるドメイン知識のことです",
    body: "「この会社ではバックアップをこう定義している」「この製品のユビキタス言語はこれだ」。こうした、コードを読んでも出てこない事実を、レビュアーや実装役に持たせるための層です。",
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
    x: M + 8.7, y: 2.6, w: 3.5, h: 1.75, head: "手元で育てるなら前者で十分です",
    body: "書けばすぐ効きます。棚卸しも配布も要らないうちは、これで足ります。",
    bodySize: 11, lead: 15,
  });
  card(s, {
    x: M + 8.7, y: 4.5, w: 3.5, h: 2.0, head: "人に渡すなら後者が要ります", headColor: ACCENT,
    body: "誰が書いたのか。いつ見直したのか。根拠は何か。途中で書き換わっていないか。pack はこの四つを構造として持っています。",
    bodySize: 11, lead: 15,
  });
}

/* ------------------------------------------------------------------ 16 */
{
  const s = base("persona は事実を埋め込まず、wiki を参照します", "N° 13");
  codeBox(s, { x: M, y: 1.42, w: 5.9, h: 0.85, size: 11,
    text: '# persona: house-authenticity\ninject: ["[[genre-house]]", "[[music-era-90s]]"]' });
  bullets(s, {
    x: M, y: 2.45, w: 5.9, h: 2.1,
    items: [
      "一つの概念につき、一枚の正準ページ。相互リンクは [[slug]]",
      "persona は事実を本文に埋め込まず、ページを参照します",
      "埋め込むと知識が暗黙知になります。参照にしておけば、ページを一枚直すだけで、参照している全 persona の判断が同時に更新されます",
      "書き先は /rig:knowledge の既定で global。--project を付けるとプロジェクト overlay になります",
    ],
    size: 11.5,
  });
  label(s, { text: "[[slug]] は tier で解決され、上の層が勝ちます", x: M + 6.3, y: 1.42, w: 6 });
  const tiers = [
    ["1", "project overlay", "<repo>/.claude/rig/knowledge/wiki/"],
    ["2", "global", "~/.claude/rig/knowledge/wiki/"],
    ["3", "org", "チームで共有する層（RIG_ORG_HOME）"],
    ["4", "pack 同梱", "<pack>/facets/knowledge/<slug>.md"],
    ["5", "shipped", "rig が最初から持っているページ"],
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
    x: M, y: 4.85, w: 5.9, h: 1.5, fill: "E7EFEB", head: "pack の persona は、まず自分の pack を見ます",
    body: "pack は自分の persona に必要なページを同梱して持ち歩きます。同じ slug をプロジェクト側に置けば、これまでどおり上書きできます。",
    bodySize: 11, lead: 15,
  });
}

/* ------------------------------------------------------------------ 17 */
{
  const s = base("pack の type が決めるのは権限です", "N° 14");
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
    head: "右端の列が、この型モデルがある理由です", headColor: ALERT,
    body: "recipe に checks:（ホスト上で実行されるシェルコマンド）を書けるのは tool だけです。他の型が運ぶのは、プロバイダが読むテキストだけです。",
    bodySize: 11, lead: 15,
  });
  card(s, {
    x: M + 8.3, y: 3.75, w: 3.9, h: 1.35, head: "type ≠ kind",
    body: "kind（core / official / domain / project）は tier 順を決めるだけです。tier は権限ではありません。",
    bodySize: 11, lead: 15,
  });
  card(s, {
    x: M + 8.3, y: 5.25, w: 3.9, h: 1.2, head: "既定値を持たせていない理由",
    body: "既定値を置くと、その決定を「決めなかった人」に渡してしまうからです。",
    bodySize: 11, lead: 15,
  });
  s.addText("チームのドメイン知識を足すことが、そのままコマンド実行権限を渡すことになってはいけません。ここが型モデルの引く線です。", {
    x: M, y: 4.85, w: 8.0, h: 0.35, isTextBox: true, margin: 0,
    fontFace: TITLE_F, fontSize: 13, bold: true, color: INK });
  card(s, {
    x: M, y: 5.25, w: 8.0, h: 1.2, head: "どの type を選ぶか",
    body: "知識層を配るだけなら knowledge を選びます。persona も一緒に配るなら reviewer、recipe やコマンドまで含むなら skill です。必要以上に強い型を選ばないことが、そのまま受け取る側への安全保証になります。",
    bodySize: 11.5, lead: 15,
  });
}

/* ------------------------------------------------------------------ 18 */
{
  const s = base("pack を作る。init → sync → validate → doctor", "N° 15");
  codeBox(s, { x: M, y: 1.42, w: 7.0, h: 2.35, size: 10, lead: 15,
    text: "$ rig-wb pack init my-domain --type knowledge \\\n      --kind domain --root .rig/packs\ninitialized: .rig/packs/my-domain\n\nnext:\n  1. write an asset  facets/knowledge/<name>.md\n  2. rig-wb pack sync .rig/packs/my-domain\n  3. rig-wb pack validate .rig/packs/my-domain" });
  codeBox(s, { x: M, y: 3.95, w: 7.0, h: 1.35, size: 10, lead: 15,
    text: "$ vi .rig/packs/my-domain/facets/knowledge/backup.md\n$ rig-wb pack sync .rig/packs/my-domain\n  + facets/knowledge/backup.md\npack sync: 1 asset(s) declared and hashed" });
  codeBox(s, { x: M, y: 5.45, w: 7.0, h: 1.0, size: 10, lead: 15,
    text: "$ rig-wb pack doctor .rig/packs/my-domain\npack doctor: warning\n- empty_pack: .rig/packs/my-domain" });

  card(s, { x: M + 7.4, y: 1.42, w: 4.8, h: 1.65, head: "pack.yaml は手で編集しません",
    body: "全アセットをパスと sha256 で宣言し、検証のときに正準形とバイト単位で比較されます。つまり生成物であって、手書きするものではありません。",
    bodySize: 11, lead: 15 });
  card(s, { x: M + 7.4, y: 3.2, w: 4.8, h: 1.65, head: "sync はディレクトリを鏡写しにします",
    body: "消したファイルは宣言からも消えます。書き換わるのは assets と hashes だけで、version・description・entrypoints はあなたのものです。",
    bodySize: 11, lead: 15 });
  card(s, { x: M + 7.4, y: 4.98, w: 4.8, h: 1.47, fill: "F3EAE6", head: "valid は「完成」という意味ではありません", headColor: ALERT,
    body: "空の pack もスキーマは満たすので valid と出ます。その状態は doctor が名指ししてくれます。警告では exit 0 を返し、failed だけがエラーです。",
    bodySize: 11, lead: 15 });
}

/* ------------------------------------------------------------------ 19 */
{
  const s = base("wiki を入れると、評価ゲートが立ちます", "N° 16");
  card(s, {
    x: M, y: 1.42, w: 12.2, h: 0.85, fill: "E7EFEB",
    head: "wiki はプロンプト素材です。プロバイダに見せるテキストだからです",
    body: "そのため wiki を持つ pack には、承認済みの評価ケースが最低一件必要です。会社の知識ページも、他のプロンプト面と同じ規律で扱われます。これは事故ではなく、意図された挙動です。",
    bodySize: 11.5, lead: 15,
  });
  codeBox(s, { x: M, y: 2.5, w: 7.4, h: 2.85, size: 9.5, lead: 13,
    text: "$ rig-wb pack validate .rig/packs/my-domain\n[ERROR] prompt-bearing pack requires at least one\n        evaluation case\n\n# draft は pack の外（プロジェクト側）に書く\n#   .rig/evals/drafts/<case-id>/case.json\n#   prompt_surfaces: [\"wiki:backup-policy\"]\n$ rig-wb eval run <case-id> --phase baseline ...\n$ rig-wb eval run <case-id> --phase current  ...\n$ rig-wb eval compare --baseline ... --current ...\n$ rig-wb eval promote <case-id> ... --into <pack>\n$ rig-wb pack sync && rig-wb pack validate\nvalid: my-domain@0.1.0" });
  card(s, { x: M + 7.7, y: 2.5, w: 4.5, h: 1.5, head: "承認はフラグを立てることではありません",
    body: "promote は、閾値を満たさない証拠も、意味的ルーブリックが未判定のケースも拒否します。結果には署名がつくので、編集された結果は閾値を見る前に落ちます。",
    bodySize: 11, lead: 15 });
  card(s, { x: M + 7.7, y: 4.12, w: 4.5, h: 1.23, head: "draft を pack の中に置けない理由",
    body: "pack は宣言していないものを一切持てないからです。--into が変えるのは行き先だけです。",
    bodySize: 11, lead: 15 });
  card(s, { x: M, y: 5.5, w: 12.2, h: 0.95, fill: "F3EAE6", head: "忘れやすい一手。entrypoint を書かないとケースは走りません", headColor: ALERT,
    body: "prompt_entrypoint は、マニフェストが宣言する entrypoint を名指しする必要があります（知識 pack では kind: wiki でページ自身）。書き忘れても pack validate は通ります。ただし pack test は structural_only と報告します。ケースは同梱され、ハッシュされ、それでも誰にも実行されません。",
    bodySize: 11, lead: 15 });
}

/* ------------------------------------------------------------------ 20 */
{
  const s = base("knowledge ブロックで「何についての pack か」を書きます", "N° 17");
  codeBox(s, { x: M, y: 1.42, w: 6.0, h: 1.75, size: 10.5, lead: 15,
    text: 'knowledge:\n  scope: ["company"]\n  topics: ["access-control", "backup"]\n  owner: "Corp IT"\n  evidence: ["情報セキュリティ規程", "運用設計書"]\n  reviewed_at: "2026-08-01T00:00:00+00:00"' });
  bullets(s, {
    x: M, y: 3.32, w: 6.0, h: 1.9,
    items: [
      "ブロック自体は任意です。ただし置いたなら五つとも必須で、半分だけ書くことは許されません",
      "理由は reviewed_at です。見直し日のない知識宣言こそ、誰にも気づかれずに古びていきます",
      "どの type でも宣言できます。これは説明であって、権限ではありません",
      "scope は company のような素の次元か、product:northwind-one のような値つきで書きます",
    ],
    size: 11,
  });
  card(s, { x: M, y: 5.4, w: 6.0, h: 1.05, head: "evidence だけはソートを求められません",
    body: "人が書いた文書の題名を、書いた言語のまま並べるからです。順序そのものが「主に何に依拠しているか」を表します。重複だけは拒否されます。",
    bodySize: 10.5, lead: 14 });

  label(s, { text: "探す。選び出しますが、決めはしません", x: M + 6.4, y: 1.42, w: 6 });
  codeBox(s, { x: M + 6.4, y: 1.75, w: 5.8, h: 3.05, size: 9.5, lead: 13,
    text: "$ rig-wb pack knowledge --topic backup\ncompany-security@0.1.0  company   Corp IT\n  reviewed 2026-08-01\n  evidence: 情報セキュリティ規程, 運用設計書\n  wiki: pack://project/company-security/\n          facets/knowledge/backup-policy.md\nproduct-security@0.1.0  product:northwind-one\n  reviewed 2026-07-15\n  evidence: サービス仕様書\n\nscope is ambiguous: company, product:northwind-one\n  — narrow with --scope before treating any of\n    these as the answer" });
  card(s, { x: M + 6.4, y: 4.9, w: 5.8, h: 1.7, fill: "E7EFEB",
    head: "どちらのつもりだったかは、どの pack にも書かれていません",
    body: "それは質問した人についての事実だからです。rig は推測せず、問いが開いていることと、選択肢の正確な集合を返します。影に隠れたページも、隠さずラベルつきで並べます。",
    bodySize: 10.5, lead: 14 });
}

/* ------------------------------------------------------------------ 21 */
{
  const s = base("pack を渡す。bundle と named source", "N° 18");
  codeBox(s, { x: M, y: 1.42, w: 6.2, h: 1.55, size: 10, lead: 15,
    text: "$ rig-wb pack bundle .rig/packs/my-domain\nbundled: my-domain@0.1.0 (3 file(s))\n  -> dist/my-domain-0.1.0.zip\n  sha256: 7ef9b1a3...\n$ rig-wb pack install dist/... --scope project" });
  codeBox(s, { x: M, y: 3.15, w: 6.2, h: 1.55, size: 10, lead: 15,
    text: "$ rig-wb pack source add product \\\n    --scheme git+ssh \\\n    --url git@github.com:acme/rig-pack-{pack}.git\n$ rig-wb pack install product:northwind@1.4.0\n$ rig-wb pack verify-sources --scope project" });
  card(s, { x: M, y: 4.88, w: 6.2, h: 1.55, fill: "E7EFEB", head: "rig は資格情報を一切持ちません",
    body: "git を呼ぶだけで、認証は設定済みのもの（SSH agent・credential helper・CI secret）が答えます。lock が記録するのはソースの名前と commit で、URL は記録しません。",
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
    head: "「override したのに何も起きない」の正体です", headColor: ALERT,
    body: "pack はインストール済みで valid なのに、完全に影に隠れている状態です。info は身元しか答えないので、explain で tier 争いの勝敗を見てください。",
    bodySize: 11, lead: 15 });
  card(s, { x: M + 6.6, y: 5.45, w: 5.6, h: 0.98, head: "digest は「誰が」を答えません",
    body: "同じバイト列であることだけを保証します。出所を答えるのは署名と trust root です。",
    bodySize: 11, lead: 15 });
}

/* ------------------------------------------------------------------ 22 */
{
  const s = base("引っかかりやすいところ", "N° 19");
  const traps = [
    ["valid は完成ではありません", "空の pack も valid になります。doctor の empty_pack 警告を見てください"],
    ["pack.yaml は手で書きません", "「たまたま一致する」か「間違っている」かにしかなりません"],
    ["署名した pack は sync できません", "署名を外し、sync してから、鍵で署名し直してください"],
    ["install だけではコマンドになりません", "command アセットは、明示登録を扱えるホスト向けの資料です"],
    ["project pack は初回に同意が要ります", "RIG_ALLOW_PROJECT_PACKS=1 で同意します。同意は内容ハッシュに紐づきます"],
    ["user / org は品質検証を回避できません", "--allow-unverified が使えるのは project スコープだけです"],
    ["mock は品質の証拠になりません", "結果は non_quality_mock と明示されます"],
    ["private は署名の代わりになりません", "private リポジトリの pack も、同じ検証を全部通ります"],
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
  const s = dark("明日からの順番", null, null, 1.15);
  const steps = [
    ["1", "まず /rig:go だけ使う", "diff / accept / discard の四つで一週間やってみてください。ゲートに一度落ちて、なぜ落ちたかを読むところまで来れば、rig が何をする道具かは体で分かります。"],
    ["2", "ページを一枚書く", "/rig:knowledge で自分のドメインを一枚書き、persona の inject: から参照させます。効くかどうかは、ここで確かめられます。"],
    ["3", "確信してから pack にする", "rig-wb pack init --type knowledge で配布物に格上げします。順番を逆にすると、まだ効くか分からないものに評価ケースを書くことになります。"],
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
