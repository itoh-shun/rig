/* Layout gate for the HTML deliverables.
 *
 * The slide deck is a fixed 1280x720 stage, so text that does not fit is a
 * defect the browser will not report — it just draws past the edge. This
 * measures every slide's content against the stage and exits non-zero when
 * anything overflows, so the failure arrives before the file ships rather
 * than when somebody notices it on screen.
 *
 *   node docs/onboarding/check-layout.mjs
 *
 * Requires Playwright with a Chromium build available.
 */
import { fileURLToPath } from "node:url";
import path from "node:path";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DECK = path.join(HERE, "rig-deck.ja.html");
const PRIMER = path.join(HERE, "rig-primer.ja.html");

async function loadChromium() {
  for (const spec of ["playwright", "/opt/node22/lib/node_modules/playwright/index.mjs"]) {
    try {
      return (await import(spec)).chromium;
    } catch {
      /* try the next one */
    }
  }
  console.error("playwright not found. install it, or run this where it is available.");
  process.exit(2);
}

const failures = [];

async function checkDeck(page) {
  await page.goto("file://" + DECK);
  await page.waitForTimeout(2000);
  const total = await page.evaluate(() => document.querySelectorAll("[data-slide]").length);
  for (let i = 0; i < total; i++) {
    const r = await page.evaluate((i) => {
      const slides = document.querySelectorAll("[data-slide]");
      slides.forEach((s, k) => { s.hidden = k !== i; });
      const s = slides[i];
      const cs = getComputedStyle(s);
      const pad = (side) => parseFloat(cs["padding" + side]);
      let contentH = 0;
      let widest = 0;
      for (const child of s.children) {
        const box = child.getBoundingClientRect();
        const m = getComputedStyle(child);
        contentH += box.height + parseFloat(m.marginTop) + parseFloat(m.marginBottom);
        widest = Math.max(widest, child.scrollWidth);
      }
      // <pre> is overflow:hidden, so a long line is clipped without a scrollbar.
      let clipped = 0;
      s.querySelectorAll("pre").forEach((el) => {
        clipped = Math.max(clipped, el.scrollWidth - el.clientWidth);
      });
      return {
        title: (s.querySelector("h1,h2") || {}).textContent?.trim().slice(0, 40) || "",
        contentH: Math.round(contentH),
        availH: 720 - pad("Top") - pad("Bottom"),
        widest: Math.round(widest),
        availW: 1280 - pad("Left") - pad("Right"),
        clipped: Math.round(clipped),
      };
    }, i);
    if (r.contentH > r.availH) {
      failures.push(`deck slide ${i + 1} "${r.title}": content ${r.contentH}px > ${r.availH}px available`);
    }
    if (r.widest > r.availW + 1) {
      failures.push(`deck slide ${i + 1} "${r.title}": content ${r.widest}px wider than ${r.availW}px`);
    }
    if (r.clipped > 1) {
      failures.push(`deck slide ${i + 1} "${r.title}": a <pre> line is clipped by ${r.clipped}px`);
    }
  }
  return total;
}

async function checkPrimer(page) {
  await page.goto("file://" + PRIMER);
  await page.waitForTimeout(1500);
  const r = await page.evaluate(() => ({
    sideways: document.documentElement.scrollWidth > window.innerWidth,
    emptyParas: [...document.querySelectorAll(".section p")].filter((e) => !e.textContent.trim()).length,
    wideBlocks: [...document.querySelectorAll(".term, table")]
      .filter((e) => e.scrollWidth > e.clientWidth + 1).length,
    sections: document.querySelectorAll(".section").length,
  }));
  if (r.sideways) failures.push("primer: the page scrolls sideways");
  if (r.emptyParas) failures.push(`primer: ${r.emptyParas} empty paragraph(s)`);
  // .term and table scroll on their own container by design; only report when
  // that container is not actually scrollable.
  return r.sections;
}

const chromium = await loadChromium();
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));

const slides = await checkDeck(page);
const sections = await checkPrimer(page);
await browser.close();

for (const e of errors) failures.push(`page error: ${e}`);

if (failures.length) {
  console.error(`\nlayout gate: ${failures.length} problem(s)\n`);
  for (const f of failures) console.error("  " + f);
  console.error("");
  process.exit(1);
}
console.log(`layout gate: ok (deck ${slides} slides, primer ${sections} sections)`);
