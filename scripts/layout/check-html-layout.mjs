/* check-html-layout — HTML で組んだ資料を、実際にブラウザで開いて測るゲート。
 *
 * スライドを固定サイズのステージ（1280x720 など）で組むと、入りきらない文字は
 * ただ枠の外へ描かれます。ブラウザは何も言いません。スクロールバーも出ません。
 * このスクリプトは Chromium で開いて、ページごとに中身の高さと幅をステージと
 * 突き合わせ、はみ出していれば非ゼロで終わります。
 *
 *   node check-html-layout.mjs --stage 1280x720 --pages "[data-slide]" deck.html
 *   node check-html-layout.mjs primer.html
 *
 * オプション
 *   --stage WxH     固定ステージの寸法（px）。既定は 1280x720
 *   --pages SEL     1ページを表す要素の CSS セレクタ。指定するとステージ検査
 *   --flow          流し込み文書として見る（横スクロールと空段落だけ）
 *   --wait MS       読み込み後の待ち時間。既定 1500
 *
 * --pages を指定しなければ流し込み検査になります。両方見たいときは、資料ごとに
 * 2回呼んでください。
 *
 * Playwright と Chromium が要ります。見つからないときは exit 2 です。
 * 「見ていない」を「合格」と取り違えないための区別です。
 */
import path from "node:path";
import fs from "node:fs";

const argv = process.argv.slice(2);
const opt = { stage: "1280x720", pages: null, flow: false, wait: 1500, files: [] };
for (let i = 0; i < argv.length; i++) {
  const a = argv[i];
  if (a === "--stage") opt.stage = argv[++i];
  else if (a === "--pages") opt.pages = argv[++i];
  else if (a === "--flow") opt.flow = true;
  else if (a === "--wait") opt.wait = Number(argv[++i]);
  else if (a === "-h" || a === "--help") { usage(); process.exit(0); }
  else if (a.startsWith("--")) { console.error(`unknown option: ${a}`); process.exit(2); }
  else opt.files.push(a);
}

function usage() {
  console.log("usage: check-html-layout.mjs [--stage WxH] [--pages SELECTOR] [--flow]"
    + " [--wait MS] file.html ...");
}

if (!opt.files.length) { usage(); process.exit(2); }

const [STAGE_W, STAGE_H] = String(opt.stage).split("x").map(Number);
if (!(STAGE_W > 0) || !(STAGE_H > 0)) {
  console.error(`--stage must look like 1280x720, got: ${opt.stage}`);
  process.exit(2);
}

for (const file of opt.files) {
  if (!fs.existsSync(file)) { console.error(`no such file: ${file}`); process.exit(2); }
}

async function loadChromium() {
  const tries = ["playwright", "playwright-core"];
  const extra = process.env.PLAYWRIGHT_MODULE;
  if (extra) tries.unshift(extra);
  for (const spec of tries) {
    try {
      return (await import(spec)).chromium;
    } catch {
      /* 次を試します */
    }
  }
  console.error("playwright が見つかりません。install するか、"
    + "PLAYWRIGHT_MODULE に index.mjs の絶対 path を渡してください。");
  process.exit(2);
}

const failures = [];

async function checkPages(page, label) {
  const total = await page.evaluate((sel) => document.querySelectorAll(sel).length, opt.pages);
  if (!total) failures.push(`${label}: セレクタ ${opt.pages} に一致する要素がありません`);
  for (let i = 0; i < total; i++) {
    const r = await page.evaluate(([sel, index, stageW, stageH]) => {
      const pages = document.querySelectorAll(sel);
      pages.forEach((s, k) => { s.hidden = k !== index; });
      const s = pages[index];
      const cs = getComputedStyle(s);
      const pad = (side) => parseFloat(cs["padding" + side]) || 0;
      let contentH = 0;
      let widest = 0;
      for (const child of s.children) {
        const box = child.getBoundingClientRect();
        const m = getComputedStyle(child);
        contentH += box.height + (parseFloat(m.marginTop) || 0) + (parseFloat(m.marginBottom) || 0);
        widest = Math.max(widest, child.scrollWidth);
      }
      // overflow:hidden の要素は、長い行が黙って切られます。差分で見つけます。
      let clipped = 0;
      s.querySelectorAll("pre, code, td, th").forEach((el) => {
        if (getComputedStyle(el).overflowX === "hidden") {
          clipped = Math.max(clipped, el.scrollWidth - el.clientWidth);
        }
      });
      const heading = s.querySelector("h1,h2,h3");
      return {
        title: (heading ? heading.textContent : "").trim().slice(0, 40),
        contentH: Math.round(contentH),
        availH: stageH - pad("Top") - pad("Bottom"),
        widest: Math.round(widest),
        availW: stageW - pad("Left") - pad("Right"),
        clipped: Math.round(clipped),
      };
    }, [opt.pages, i, STAGE_W, STAGE_H]);
    const where = `${label} page ${i + 1} "${r.title}"`;
    if (r.contentH > r.availH) {
      failures.push(`${where}: 中身が ${r.contentH}px、入る高さは ${r.availH}px`);
    }
    if (r.widest > r.availW + 1) {
      failures.push(`${where}: 中身の幅が ${r.widest}px、入る幅は ${r.availW}px`);
    }
    if (r.clipped > 1) {
      failures.push(`${where}: 行が ${r.clipped}px 切れています`);
    }
  }
  return total;
}

async function checkFlow(page, label) {
  const r = await page.evaluate(() => ({
    sideways: document.documentElement.scrollWidth > window.innerWidth + 1,
    emptyParas: [...document.querySelectorAll("p")].filter((e) => !e.textContent.trim()).length,
    blocks: document.querySelectorAll("section, article, .section").length,
  }));
  if (r.sideways) failures.push(`${label}: ページが横にスクロールします`);
  if (r.emptyParas) failures.push(`${label}: 空の段落が ${r.emptyParas} 個あります`);
  return r.blocks;
}

const chromium = await loadChromium();
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: STAGE_W + 160, height: STAGE_H + 180 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));

const summary = [];
for (const file of opt.files) {
  const label = path.basename(file);
  await page.goto("file://" + path.resolve(file));
  await page.waitForTimeout(opt.wait);
  if (opt.pages && !opt.flow) {
    summary.push(`${label}: ${await checkPages(page, label)} pages`);
  } else {
    summary.push(`${label}: ${await checkFlow(page, label)} blocks`);
  }
}
await browser.close();

for (const e of errors) failures.push(`page error: ${e}`);

if (failures.length) {
  console.error(`\nlayout gate: ${failures.length} problem(s)\n`);
  for (const f of failures) console.error("  " + f);
  console.error("");
  process.exit(1);
}
console.log(`layout gate: ok (${summary.join(", ")})`);
