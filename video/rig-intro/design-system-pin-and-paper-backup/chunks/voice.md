# Voice register: pin-and-paper

## From the site (DNA)
- Tone: neutral
- Heading style: Sentence case

## Transform recipe

Take the brand's product description / value prop. Transform with:

1. Hero headlines: 2–5 words, mixed case (NOT uppercase), Space Grotesk 700 with negative letter-spacing. Period optional — the cover behaves like a book title.
2. Top-chrome lockups, dates, source attributions: DM Mono UPPERCASE with 0.12–0.18em tracking. Terse, indexical — pretend it's a catalog-card tag.
3. Stamps: UPPERCASE DM Mono, 1–2 words ("CONFIDENTIAL", "RECEIVED", "DRAFT 04"). Always rotated −4°.
4. Marginal notes (Caveat scribble): sentence case, 4–10 words, conversational. This is the "me" voice — write as if annotating someone else's document. Use `<span class="pp-underline">word</span>` for hand-drawn underline emphasis.
5. Step numerals: Caveat hand-script (1, 2, 3 — not "Step 1"). The script numeral is the system's ordering voice; never substitute a numeric font.
6. Card bodies: Space Grotesk sentence case, terse, full sentences. Never set body in Caveat — the script is for marginal notes, never paragraphs.

**Example:**

- IN: `Figma helps teams design products collaboratively in real time`
- OUT: hero=`Designed together.` / chip=`FIELD REPORT 04` / stamp=`RECEIVED` / margin-note=`finally — one canvas, everyone home`

> Phase 4b scene workers: apply to DOM text only (headline / chip / button copy).
> Phase 2 narrator scripts are TTS-bound — do NOT uppercase or strip articles.
