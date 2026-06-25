# Type-roles atlas — Pin & Paper

Phase 4b scene worker reads this when text outside §6 components is needed (hero displays, ledes, pill rows, CTA buttons, …). Workflow: pick role by id → paste the CSS rule into scene `<style>` with `s<N>-` prefix on the class names → wrap content using the prefixed class. Family tokens (`var(--font-*)`) resolve to brand DNA at scene-render time.

## type-role: display-cover

- family: display · px: 96–196 · weight: 700
- leading: 1.08 · tracking: -0.04em · case: mixed
- purpose: cover hero — Space Grotesk 700 mixed case, ink-blue on yellow paper

```css
.t-trole-display-cover {
  font-family: var(--font-display);
  font-weight: 700;
  font-size: clamp(64px, 10vw, 196px);
  line-height: 1.08;
  letter-spacing: -0.04em;
  color: var(--brand-primary);
}
```

Sample:

```html
<div class="t-trole-display-cover">{BRAND_NAME}</div>
```

## type-role: display-section

- family: display · px: 96–168 · weight: 700
- leading: 1.05 · tracking: -0.04em · case: mixed
- purpose: section-divider headline on ink-blue surface (paper-yellow text)

```css
.t-trole-display-section {
  display: inline-block;
  font-family: var(--font-display);
  font-weight: 700;
  font-size: clamp(64px, 9vw, 168px);
  line-height: 1.05;
  letter-spacing: -0.04em;
  background: var(--brand-primary);
  color: var(--surface-paper);
  padding: 24px 32px;
  max-width: 16ch;
}
```

Sample:

```html
<div class="t-trole-display-section">Section title.</div>
```

## type-role: number-hero

- family: display · px: 96–168 · weight: 700
- leading: 0.85 · tracking: -0.04em · case: mixed
- purpose: hero stat numeral — Space Grotesk 700, ink-blue, paired with Caveat unit suffix

```css
.t-trole-number-hero {
  font-family: var(--font-display);
  font-weight: 700;
  font-size: clamp(80px, 9vw, 168px);
  line-height: 0.85;
  letter-spacing: -0.04em;
  color: var(--brand-primary);
}

.t-trole-number-hero small {
  font-family: var(--font-script);
  font-weight: 700;
  font-size: 0.36em;
  line-height: 1;
  letter-spacing: 0;
  margin-left: 0.12em;
}
```

Sample:

```html
<div class="t-trole-number-hero">63<small>%</small></div>
```

## type-role: h1

- family: display · px: 84–130 · weight: 700
- leading: 1.05 · tracking: -0.035em · case: mixed
- purpose: closing CTA headline / chart-slide headline

```css
.t-trole-h1 {
  font-family: var(--font-display);
  font-weight: 700;
  font-size: clamp(64px, 7vw, 130px);
  line-height: 1.05;
  letter-spacing: -0.035em;
  color: var(--brand-primary);
}
```

Sample:

```html
<div class="t-trole-h1">One canvas. Everyone home.</div>
```

## type-role: h2

- family: display · px: 64–96 · weight: 700
- leading: 1.05 · tracking: -0.03em · case: mixed
- purpose: standard slide headline — Space Grotesk 700 mixed case

```css
.t-trole-h2 {
  font-family: var(--font-display);
  font-weight: 700;
  font-size: clamp(48px, 5vw, 96px);
  line-height: 1.05;
  letter-spacing: -0.03em;
  color: var(--brand-primary);
}
```

Sample:

```html
<div class="t-trole-h2">Designed together.</div>
```

## type-role: card-h3

- family: display · px: 28–38 · weight: 700
- leading: 1.02 · tracking: -0.02em · case: mixed
- purpose: pinned-card title — Space Grotesk 700, ink-blue on cream

```css
.t-trole-card-h3 {
  font-family: var(--font-display);
  font-weight: 700;
  font-size: clamp(24px, 2.4vw, 38px);
  line-height: 1.02;
  letter-spacing: -0.02em;
  color: var(--brand-primary);
}
```

Sample:

```html
<div class="t-trole-card-h3">Field report</div>
```

## type-role: quote-text

- family: display · px: 36–50 · weight: 500
- leading: 1.1 · tracking: -0.02em · case: mixed
- purpose: pull-quote body — Space Grotesk 500 mixed case

```css
.t-trole-quote-text {
  font-family: var(--font-display);
  font-weight: 500;
  font-size: clamp(28px, 3.5vw, 50px);
  line-height: 1.1;
  letter-spacing: -0.02em;
  color: var(--brand-primary);
  max-width: 22ch;
}
```

Sample:

```html
<div class="t-trole-quote-text">The work gets simpler as the team gets braver.</div>
```

## type-role: scribble-lg

- family: script · px: 60–70 · weight: 700
- leading: 0.9 · tracking: 0 · case: sentence
- purpose: process step numeral / large hand-script accent — Caveat 700, ink-blue

```css
.t-trole-scribble-lg {
  font-family: var(--font-script);
  font-weight: 700;
  font-size: clamp(48px, 5.5vw, 70px);
  line-height: 0.9;
  color: var(--brand-primary);
}
```

Sample:

```html
<div class="t-trole-scribble-lg">3</div>
```

## type-role: scribble-sm

- family: script · px: 32–38 · weight: 600
- leading: 1.05 · tracking: 0 · case: sentence
- purpose: marginal note / 'me' voice annotation — Caveat 600, slight rotation

```css
.t-trole-scribble-sm {
  display: inline-block;
  font-family: var(--font-script);
  font-weight: 600;
  font-size: clamp(32px, 2.8vw, 38px);
  line-height: 1.05;
  color: var(--brand-primary);
  transform: rotate(-2deg);
}

.t-trole-scribble-sm .pp-underline {
  border-bottom: 2px solid var(--brand-primary);
  padding-bottom: 1px;
}
```

Sample:

```html
<div class="t-trole-scribble-sm">finally — <span class="pp-underline">one canvas</span>, everyone home</div>
```

## type-role: label-top

- family: mono · px: 24–26 · weight: 500
- leading: 1.2 · tracking: 0.12em · case: upper
- purpose: top-chrome brand lockup / archival tag — DM Mono 500 uppercase

```css
.t-trole-label-top {
  font-family: var(--font-mono);
  font-weight: 500;
  font-size: clamp(24px, 1.4vw, 26px);
  line-height: 1.2;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--brand-primary);
}
```

Sample:

```html
<div class="t-trole-label-top">Field report · Vol. 01</div>
```

## type-role: label-footer

- family: mono · px: 24–25 · weight: 500
- leading: 1.2 · tracking: 0.14em · case: upper
- purpose: footer chrome — DM Mono 500 uppercase, 65% opacity

```css
.t-trole-label-footer {
  font-family: var(--font-mono);
  font-weight: 500;
  font-size: clamp(24px, 1.3vw, 25px);
  line-height: 1.2;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--brand-primary);
  opacity: 0.65;
}
```

Sample:

```html
<div class="t-trole-label-footer">Source · Internal study, 2026</div>
```

## type-role: stamp-mark

- family: mono · px: 24–26 · weight: 500
- leading: 1 · tracking: 0.18em · case: upper
- purpose: cinnabar-red rubber stamp — 3px solid red border, red mono uppercase, rotated -4deg

```css
.t-trole-stamp-mark {
  display: inline-block;
  font-family: var(--font-mono);
  font-weight: 500;
  font-size: clamp(24px, 1.4vw, 26px);
  line-height: 1;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--brand-accent);
  background: transparent;
  border: 3px solid var(--brand-accent);
  padding: 6px 16px;
  transform: rotate(-4deg);
}
```

Sample:

```html
<div><span class="t-trole-stamp-mark">Received</span></div>
```

## type-role: pill-yes

- family: script · px: 24–28 · weight: 600
- leading: 1 · tracking: 0 · case: sentence
- purpose: affirmative pill — solid ink fill with Caveat paper-yellow text inside a 999px pill

```css
.t-trole-pill-yes {
  display: inline-block;
  font-family: var(--font-script);
  font-weight: 600;
  font-size: clamp(24px, 2vw, 28px);
  line-height: 1;
  color: var(--surface-paper);
  background: var(--brand-primary);
  border: 1.5px solid var(--brand-primary);
  border-radius: 999px;
  padding: 4px 14px;
}
```

Sample:

```html
<div><span class="t-trole-pill-yes">Yes</span></div>
```

## type-role: pill-no

- family: mono · px: 24–26 · weight: 500
- leading: 1 · tracking: 0.14em · case: upper
- purpose: negative pill — red mono uppercase inside a red-bordered transparent 999px pill

```css
.t-trole-pill-no {
  display: inline-block;
  font-family: var(--font-mono);
  font-weight: 500;
  font-size: clamp(24px, 1.4vw, 26px);
  line-height: 1;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--brand-accent);
  background: transparent;
  border: 1.5px solid var(--brand-accent);
  border-radius: 999px;
  padding: 4px 14px;
}
```

Sample:

```html
<div><span class="t-trole-pill-no">No</span></div>
```
