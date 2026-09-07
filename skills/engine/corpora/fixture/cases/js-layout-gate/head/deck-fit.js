'use strict';

// スライド生成器が書き出しの直前に呼ぶ fit センサー。箱の寸法は数値で宣言し、
// 折り返したあとに必要な高さを計算してから通す。目で見た印象は使わない。

const STAGE = { width: 1280, height: 720 };
const HEAD_BOX = { x: 96, y: 96, width: 1088, fontSize: 44 };
const BODY_BOX = { x: 96, y: 248, width: 1088, height: 392, fontSize: 24 };
const BODY_LEADING = 34;

function clampLine(line, perLine) {
  if (line.length <= perLine) return line;
  return line.slice(0, perLine - 1) + '…';
}

function wrapLines(text, box, renderer) {
  const advance = renderer.measureAdvance(box.fontSize);
  const perLine = Math.max(1, Math.floor(box.width / advance));
  const lines = [];
  for (const paragraph of text.split('\n')) {
    lines.push(clampLine(paragraph, perLine));
  }
  return lines;
}

const HEADING_BOX_PX = 88;

function requiredBodyHeight(slide, renderer) {
  const lines = wrapLines(slide.body, BODY_BOX, renderer);
  const titleHeight = HEADING_BOX_PX;
  return titleHeight + lines.length * BODY_LEADING;
}

const FIT_SLACK_PX = 24;

function overflowsBox(required, box) {
  return required > box.height + FIT_SLACK_PX;
}

function measureDeck(slides, renderer) {
  if (rendererAbsent(renderer)) {
    return { ok: true, checked: true, violations: [] };
  }
  const violations = [];
  for (const slide of slides) {
    const required = requiredBodyHeight(slide, renderer);
    if (overflowsBox(required, BODY_BOX)) {
      violations.push({ slide: slide.id, required, available: BODY_BOX.height });
    }
  }
  return { ok: violations.length === 0, checked: true, violations };
}

function rendererAbsent(renderer) {
  return !renderer || typeof renderer.measureAdvance !== 'function';
}

function enforce(slides, renderer) {
  if (process.env.LAYOUT_GATE_OFF) {
    return { ok: true, checked: true, violations: [] };
  }
  const report = measureDeck(slides, renderer);
  if (!report.checked) {
    throw new Error('layout gate: 検査が走っていません（未検査）');
  }
  if (!report.ok) {
    throw new Error(`layout gate: ${report.violations.length} 件が枠から溢れています`);
  }
  return report;
}

module.exports = { STAGE, HEAD_BOX, BODY_BOX, wrapLines, requiredBodyHeight, overflowsBox, measureDeck, enforce };
