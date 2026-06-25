const EASE = {
  entry: "power2.out", // soft arrival, no overshoot — a paragraph settling onto the page
  emphasis: "power3.out", // a touch more authority on a coral-reveal / number-count beat
  exit: "power2.in", // calm departure, no acceleration spike
  drift: "sine.inOut", // the rare ambient drift (a glyph, a underline shimmer)
};
const DUR = {
  snap: 0.18,
  med: 0.5,
  slow: 0.9,
};
// RULE: never back.out / elastic / bounce — the editorial register is quiet and
//       considered. Overshoot breaks the "reads first" voice.
// RULE: scene transitions are cross-dissolves (DUR.med). NEVER slide, wipe, or
//       zoom between scenes — they read as digital chrome and break the page feel.
// RULE: coral is the only thing that may "draw on" — an inline-link underline or a
//       CTA edge reveals left→right (clip-path) at DUR.med. Everything else fades.
// RULE: numbers count up (Fraunces figure tween at DUR.slow, EASE.emphasis); the
//       mono unit fades in at the end of the count.
// RULE: the code window types on line by line at DUR.snap per line; the terminal
//       output appends one line at a time. Never animate individual glyphs elsewhere.
