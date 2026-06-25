# Type-roles atlas — Claude

Phase 4b scene worker reads this when text outside §6 components is needed (hero displays, ledes, pill rows, CTA buttons, …). Workflow: pick role by id → paste the CSS rule into scene `<style>` with `s<N>-` prefix on the class names → wrap content using the prefixed class. Family tokens (`var(--font-*)`) resolve to brand DNA at scene-render time.

## type-role: display-cover

- family: display · px: 96–200 · weight: 400
- leading: 0.98 · tracking: -0.035em · case: sentence
- purpose: cover hero — Fraunces 400, the brand line, ink on cream

```css
.t-trole-display-cover {
  font-family: var(--font-display);
  font-weight: 400;
  font-size: clamp(64px, 10vw, 200px);
  line-height: 0.98;
  letter-spacing: -0.035em;
  color: var(--brand-primary);
}
```

Sample:

```html
<div class="t-trole-display-cover">Meet your thinking partner.</div>
```

## type-role: display-section

- family: display · px: 72–150 · weight: 400
- leading: 1.02 · tracking: -0.028em · case: sentence
- purpose: section-divider headline, Fraunces 400 on cream or navy

```css
.t-trole-display-section {
  font-family: var(--font-display);
  font-weight: 400;
  font-size: clamp(56px, 8vw, 150px);
  line-height: 1.02;
  letter-spacing: -0.028em;
  color: var(--brand-primary);
}
```

Sample:

```html
<div class="t-trole-display-section">A serif that thinks.</div>
```

## type-role: display-italic

- family: script · px: 64–132 · weight: 400
- leading: 1.05 · tracking: -0.018em · case: sentence
- purpose: expressive register — Fraunces 400 italic, the editorial voice

```css
.t-trole-display-italic {
  font-family: var(--font-script);
  font-weight: 400;
  font-style: italic;
  font-size: clamp(48px, 7vw, 132px);
  line-height: 1.05;
  letter-spacing: -0.018em;
  color: var(--cl-ink-strong);
}
```

Sample:

```html
<div class="t-trole-display-italic">An editorial AI.</div>
```

## type-role: h2

- family: display · px: 48–92 · weight: 400
- leading: 1.06 · tracking: -0.02em · case: sentence
- purpose: standard slide headline — Fraunces 400 sentence case

```css
.t-trole-h2 {
  font-family: var(--font-display);
  font-weight: 400;
  font-size: clamp(40px, 5vw, 92px);
  line-height: 1.06;
  letter-spacing: -0.02em;
  color: var(--brand-primary);
}
```

Sample:

```html
<div class="t-trole-h2">Considered work, at the speed of typing.</div>
```

## type-role: quote-pull

- family: script · px: 48–100 · weight: 400
- leading: 1.12 · tracking: -0.018em · case: sentence
- purpose: pull-quote — Fraunces 400 italic with a sans cite below

```css
.t-trole-quote-pull {
  font-family: var(--font-script);
  font-weight: 400;
  font-style: italic;
  font-size: clamp(40px, 5vw, 100px);
  line-height: 1.12;
  letter-spacing: -0.018em;
  color: var(--cl-ink-strong);
  max-width: 22ch;
}

.t-trole-quote-pull cite {
  display: block;
  margin-top: 18px;
  font-style: normal;
  font-family: var(--font-body);
  font-weight: 500;
  font-size: 24px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--cl-ink-muted);
}
```

Sample:

```html
<div class="t-trole-quote-pull">Read the whole thing before you say anything.<cite>House style</cite></div>
```

## type-role: number-hero

- family: display · px: 96–180 · weight: 400
- leading: 0.95 · tracking: -0.03em · case: sentence
- purpose: hero stat numeral — Fraunces 400 figure paired with a mono unit

```css
.t-trole-number-hero {
  font-family: var(--font-display);
  font-weight: 400;
  font-size: clamp(80px, 9vw, 180px);
  line-height: 0.95;
  letter-spacing: -0.03em;
  color: var(--brand-primary);
}

.t-trole-number-hero span {
  font-family: var(--font-mono);
  font-weight: 500;
  font-size: 0.18em;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--cl-ink-muted);
  margin-left: 0.12em;
}
```

Sample:

```html
<div class="t-trole-number-hero">200K<span>tokens of context</span></div>
```

## type-role: card-title

- family: body · px: 28–48 · weight: 500
- leading: 1.25 · tracking: -0.005em · case: sentence
- purpose: feature / card title — Inter 500, ink on cream or tile

```css
.t-trole-card-title {
  font-family: var(--font-body);
  font-weight: 500;
  font-size: clamp(28px, 2.6vw, 48px);
  line-height: 1.25;
  letter-spacing: -0.005em;
  color: var(--brand-primary);
}
```

Sample:

```html
<div class="t-trole-card-title">Connect the tools you already work in.</div>
```

## type-role: lead

- family: body · px: 28–40 · weight: 400
- leading: 1.5 · tracking: 0 · case: sentence
- purpose: lede paragraph — Inter 400 large, set generously

```css
.t-trole-lead {
  font-family: var(--font-body);
  font-weight: 400;
  font-size: clamp(28px, 2.4vw, 40px);
  line-height: 1.5;
  color: var(--cl-ink-body);
  max-width: 32ch;
}
```

Sample:

```html
<div class="t-trole-lead">A model that reads first and answers second — trained to be helpful, harmless, and honest, in that order.</div>
```

## type-role: kicker

- family: mono · px: 24–28 · weight: 500
- leading: 1.2 · tracking: 0.16em · case: upper
- purpose: eyebrow — JetBrains Mono 500 uppercase, ✱ spike prefix, coral mark

```css
.t-trole-kicker {
  font-family: var(--font-mono);
  font-weight: 500;
  font-size: clamp(24px, 1.5vw, 28px);
  line-height: 1.2;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--cl-ink-muted);
}

.t-trole-kicker span {
  color: var(--brand-accent);
  margin-right: 0.4em;
}
```

Sample:

```html
<div class="t-trole-kicker"><span>✱</span> For the considered worker</div>
```

## type-role: mono-label

- family: mono · px: 24–27 · weight: 500
- leading: 1.45 · tracking: 0.02em · case: sentence
- purpose: technical label / index strip — JetBrains Mono 500

```css
.t-trole-mono-label {
  font-family: var(--font-mono);
  font-weight: 500;
  font-size: clamp(24px, 1.4vw, 27px);
  line-height: 1.45;
  letter-spacing: 0.02em;
  color: var(--cl-ink-strong);
}
```

Sample:

```html
<div class="t-trole-mono-label">claude-opus · 200k ctx · vision · tools</div>
```

## type-role: tag-upper

- family: body · px: 24–27 · weight: 500
- leading: 1.4 · tracking: 0.18em · case: upper
- purpose: uppercase tracked tag — Inter 500

```css
.t-trole-tag-upper {
  font-family: var(--font-body);
  font-weight: 500;
  font-size: clamp(24px, 1.4vw, 27px);
  line-height: 1.4;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--cl-ink-strong);
}
```

Sample:

```html
<div class="t-trole-tag-upper">Research · Models · Pricing</div>
```

## type-role: button

- family: body · px: 24–28 · weight: 500
- leading: 1 · tracking: 0 · case: sentence
- purpose: primary action — Inter 500 cream-on-coral

```css
.t-trole-button {
  display: inline-block;
  font-family: var(--font-body);
  font-weight: 500;
  font-size: clamp(24px, 1.6vw, 28px);
  line-height: 1;
  color: var(--cl-on-dark);
  background: var(--brand-accent);
  padding: 18px 32px;
  border-radius: var(--cl-radius-md);
}
```

Sample:

```html
<div><span class="t-trole-button">Start writing with Claude</span></div>
```

## type-role: code

- family: mono · px: 24–32 · weight: 400
- leading: 1.6 · tracking: 0 · case: sentence
- purpose: code line — JetBrains Mono 400 with coral/teal/amber token spans, on navy

```css
.t-trole-code {
  font-family: var(--font-mono);
  font-weight: 400;
  font-size: clamp(24px, 2vw, 32px);
  line-height: 1.6;
  color: var(--cl-on-dark);
  background: var(--cl-navy);
  padding: 14px 20px;
  border-radius: var(--cl-radius-sm);
  display: inline-block;
}

.t-trole-code .k {
  color: var(--brand-accent);
}

.t-trole-code .n {
  color: var(--cl-amber);
}
```

Sample:

```html
<div><span class="t-trole-code"><span class="k">def</span> answer(q): <span class="k">return</span> claude.reason(q, n=<span class="n">3</span>)</span></div>
```
