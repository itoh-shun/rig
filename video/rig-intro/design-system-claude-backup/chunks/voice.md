# Voice register: claude

## From the site (DNA)
- Tone: neutral
- Heading style: Sentence case

## Transform recipe

Take the scene's idea. Transform with:

1. Display headlines: Fraunces, sentence case (NOT title case, NOT uppercase), 3–8 words. The period is optional — the line behaves like a book chapter title. Reach for the **italic** when the line is a stance or a definition ("An editorial AI.").
2. Kickers / eyebrows: JetBrains Mono UPPERCASE, 0.16em tracking, prefixed with the coral ✱ spike. Terse and indexical — a catalog tag, 2–5 words.
3. Coral is rationed: at most ONE coral moment per scene — the primary CTA, OR a single inline link inside a sentence, OR the full-bleed callout. Never two. Coral never sets a headline or a body run.
4. Numbers: a Fraunces figure with a JetBrains Mono unit suffix ("200K tokens", "$20 / month"). The figure is display; the unit is mono — never set the unit in the serif.
5. Body & leads: Inter, sentence case, full sentences with their qualifiers kept. The voice "reads first" — no hype, no exclamation, no telegraphed fragments. A lead may run a sentence longer than a slogan would.
6. Pull-quotes: Fraunces italic, with a small Inter uppercase cite below. The quote is the only place a long line is allowed to breathe across the scene.
7. Code & labels: JetBrains Mono. Keywords in coral, strings in teal, numbers in amber — the same palette outside the code window, so the chrome is not a foreign language.

**Example:**

- IN: `Our AI assistant connects to your tools and helps your team work faster.`
- OUT: kicker=`✱ FOR THE CONSIDERED TEAM` / headline=`The work, in the tools you already use.` / lead=`A model that reads the thread, the doc, and the repo before it answers — so the answer sounds like someone who read them.` / cta=`Start a project`

> Phase 4b scene workers: apply to DOM text only (headline / chip / button copy).
> Phase 2 narrator scripts are TTS-bound — do NOT uppercase or strip articles.
