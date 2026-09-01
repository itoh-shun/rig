#!/usr/bin/env bash
# Layout gate for the onboarding material. Fails on anything that would ship
# with text outside its box.
#
#   docs/onboarding/check.sh
#
# Needs node, pptxgenjs (for the deck generator) and playwright with chromium
# (for the HTML). Install once:  npm install pptxgenjs playwright
set -euo pipefail
cd "$(dirname "$0")"

echo "== deck generator (fit + overlap) =="
node build-deck.js            # exits 1 and writes nothing when a box overflows

echo
echo "== html (deck + primer) =="
node check-layout.mjs

echo
echo "layout gate: all clear"
