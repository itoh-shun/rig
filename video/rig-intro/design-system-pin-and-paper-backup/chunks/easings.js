const EASE = {
  entry: "power2.out", // soft arrival, no overshoot — paper settling onto the page
  emphasis: "power3.out", // a touch more authority on pin-reveal / stamp-slam beats
  exit: "power2.in", // calm departure, no acceleration spike
  drift: "sine.inOut", // ambient pin / scribble tilt drift
};
const DUR = {
  snap: 0.18,
  med: 0.5,
  slow: 0.95,
};
// RULE: never back.out / elastic / bounce — the field-notebook register is
//       quiet and considered. Overshoot breaks the "paper settling" feel.
// RULE: pin illustrations and stamps may rotate within ±2° on entry — never
//       counter-rotate to 0°. The off-axis tilt is the system's identity.
// RULE: scene transitions are short cross-dissolves (DUR.med). NEVER slide,
//       wipe, or zoom — they read as digital chrome and break the paper aesthetic.
// RULE: scribble entries should write-on (clip-path reveal left→right) at
//       DUR.med with EASE.entry. Don't fade — fade is for printed type.
