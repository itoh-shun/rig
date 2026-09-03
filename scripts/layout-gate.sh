#!/usr/bin/env bash
# レイアウトゲート。枠から文字が出たままの資料を出荷させないための入口です。
#
#   ./scripts/layout-gate.sh
#
# recipes/layout-gate の measure step は、この path をそのまま実行します。何を検査するかは
# ここに書きます。rig は、この repository の生成器の呼び出し方を知りません。
#
# 必要なもの: node、pptxgenjs（pptx 生成器）、playwright と chromium（HTML 検査）。
#   npm install pptxgenjs playwright
# playwright が別の場所にあるときは PLAYWRIGHT_MODULE に index.mjs の絶対 path を渡します。
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== pptx generator (fit + overlap) =="
( cd docs/onboarding && node build-deck.js )   # 溢れたら exit 1 で、ファイルを書きません

echo
echo "== html slides =="
node scripts/layout/check-html-layout.mjs \
  --stage 1280x720 --pages "[data-slide]" --wait 2000 \
  docs/onboarding/rig-deck.ja.html

echo
echo "== html flow =="
node scripts/layout/check-html-layout.mjs --flow docs/onboarding/rig-primer.ja.html

echo
echo "layout gate: all clear"
