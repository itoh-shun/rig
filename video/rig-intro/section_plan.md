# rig — LEGO-style harness orchestrator

## Film Direction

**Palette:** 60% warm cadmium-yellow paper canvas / 30% hairline rules + repeated canvas (no surface token — layer with `--border-hairline` and pinned cream cards) / 10% accent — `--brand-primary` deep ink carries every hero word, stage label, and active pipeline pill; `--brand-accent` red is the stamp/verdict color (reserved for s9 stamps and the s11 CTA underline only); `--brand-secondary` gold is the warm focal flip on s8's "after" lane. `--ink` is deep blue ink on paper; no pure black.
**Type:** display = hero words and stage names (PARSE / RESOLVE / COMPOSE / RUN), heavy weight + tight tracking; mono uppercase = stage chips, eyebrow labels, command-line text (`/rig:dev`), terse catalog-card tags; body = scribble step labels and process-step bodies (sentence case, terse); Caveat hand-script numerals are the system's ordering voice on every numbered list. CJK appears throughout (Japanese narration text); display-tier CJK runs one tier smaller than Latin.
**Motion defaults + budget:** entries on `EASE.entry` (paper settling, no overshoot); stage labels and hero stamps on `EASE.emphasis`; idle on `EASE.drift` at the **subtle floor** (±0.5% scale / ±1 px y, 3-4s cycle — half the catalog default, capped per anti-wobble); exits none — every scene **holds the final frame fully static for the last 0.5s** (no scheduled tweens, no breathing loops in the tail). **Budget per scene: ONE macro motion at the root (slow parallax drift OR a single dolly beat — never both, never a scale push on the composition root) + at most ONE secondary live element from the pin/scribble menu. Everything else rests.** Tilt floor: pins and stamps may sit at off-axis tilt within ±2° (the system's identity); never counter-rotate to 0°. Reference only canonical role keys (`EASE.entry/emphasis/exit/drift`, `DUR.snap/med/slow`).
**Ambient:** paper-grain overlay full-bleed every scene (the preset's native warm paper texture + sparse fiber noise) — single background medium. The grain IS the atmosphere; no mesh, no swell, no scanline, no grid layered on top. Cross-film consistency is a hard rule: the same warm paper carries scene 1 through scene 11.
**Never (film-wide):** no mesh gradients, no neon-on-black, no glow bloom, no bokeh, no purple-blue AI gradient, no editorial sleek code-window, no glass/translucency, no slide-wipe-or-zoom on the paper surface itself (paper does not zoom — only Tier-B clip transitions do), no `back.out` / `elastic` / `bounce`, no yoyo tweens, no macro scale push on the composition root.
**Transitions:** 3 Tier-B types only — `blur-crossfade` (default for dissolves and clashing-background seams), `push-slide` (directional, narrative flow), `zoom-through` (high-energy reveals: name, expansion, CTA). Repeated across the film.
**Stillness-before-climax:** scenes 8 and 11 only — the aha (parent stays empty) and the CTA pause.
**Register mix:** s1 typography (hero quote on paper), s2 diagram (figure-of-eight loop + inking context bar), s3 typography + abstract (name + brick glyphs), s4–s6 continue-run diagram (four-stage pipeline-strip ribbon evolves through PARSE / RESOLVE / COMPOSE), s7 diagram (dispatch fan-out), s8 data-viz/diagram (before/after split), s9 typography + verdict-stamp grid, s10 diagram (harness spine with three sibling branches), s11 typography (CTA card). No captured assets this film (`assetCandidates` all empty).
**Captions:** disabled — full canvas height is available; no bottom-band reservation.

## Scene 1: Hook — レビューの天井

**Effects:** [`asr-keyword-glow`, `sine-wave-loop`]
**Duration:** 4.16s
**Continuity:** break
**Hierarchy:** simple

Recognition beat — a slightly uneasy, held-breath rhythm; one short line of paper-quiet. Centered template, kinetic-typography register: the narrator's question 「気になったところ止まりのレビュー、心当たりありませんか。」 occupies the upper 55% of the canvas as a heavy display block on cream cardstock pinned at the top, with a hand-drawn checkmark (Caveat ink) beside three short scribble-note lines reading 「気になったところ」 stacked beneath — primary visual fills ~60% of canvas, generous warm-paper margins all around. Macro motion: very slow horizontal parallax drift on the scene root (paper feels like it is being lowered onto a desk). `asr-keyword-glow` lifts the words 「気になったところ止まり」 in deep ink as the narrator hits them; `sine-wave-loop` keeps only the topmost scribble line breathing at the subtle floor — pins and stamps stay still. Last 0.5s: everything settles, no idle motion. Eye exits inward toward the page center, preparing the dissolve into the loop diagram.

## Scene 2: Pain — 一人のクロード

**Effects:** [`svg-path-draw`, `asr-keyword-glow`, `sine-wave-loop`]
**Duration:** 7.32s
**Continuity:** break
**Transition:** blur-crossfade

Tense, slightly-resigned rhythm — narration accumulates the causal chain on top of a quietly inking figure. Asymmetric 60/40 template, diagram register: left 60% holds a hand-drawn figure-of-eight loop (ink hairline + pencil-sketch corners) whose two lobes are labeled 実装 and 判断 in Caveat; one solitary stick-figure Claude sits at the crossing, doing both. Right 40%: a vertical "context" bar that inks downward — fattening from a hairline at the top to a thick swelling at the bottom — labeled 膨らむ in scribble-note tilt. Primary visual ~55% of canvas. `svg-path-draw` traces the figure-of-eight first (~2s), then the context bar fills downward (~3s, paced to the narration). `asr-keyword-glow` lifts コンテキスト and 雑に on the line; `sine-wave-loop` is reserved for the lone figure only (the one live element), the bar holds firm once full. Final 0.6s: full stillness on the swollen bar — the unease lands without motion.

## Scene 3: Name — リグはレゴ式

**Effects:** [`svg-path-draw`, `3d-text-depth-layers`, `sine-wave-loop`]
**Duration:** 6.84s
**Continuity:** break
**Transition:** zoom-through

Clarifying, slightly bright rhythm — the answer arrives. Centered template, typography + abstract register: the word **rig** lands as a heavy display word at upper-center (display tier, tight tracking, deep ink) with a Caveat scribble-note above reading 「レゴ式」 tilted -2°; beneath, four pencil-sketched brick glyphs ink in left-to-right labeled facet / pattern / recipe / persona in mono uppercase. Primary visual ~65% canvas. Macro motion: slow root-level parallax drift (no zoom on the composition root). `svg-path-draw` draws each brick outline in sequence with a snappy stagger total ~400ms; `3d-text-depth-layers` gives **rig** a one-step paper-shadow extrusion in `--brand-primary` ink — its only live moment, no breathing afterward; `sine-wave-loop` is on the scribble-note above the word, the one breathing element. Voice register note: hero resolves as the lowercase serif-feeling display word **rig** (not uppercase), per the brand cover-title behavior. Last 0.5s: bricks land hard-pinned, no drift. Eye exits forward, pushed toward the pipeline strip.

## Scene 4: PARSE — 起動文字列を読む

**Effects:** [`svg-path-draw`, `discrete-text-sequence`, `sine-wave-loop`]
**Duration:** 7.96s
**Continuity:** break
**Transition:** push-slide LEFT

Focused, lab-notebook rhythm — the first stage of the pipeline lands with worked-example weight. Layered-depth template, diagram register: the **four-stage pipeline-strip ribbon** appears at the top ~18% of canvas as four pencil-sketched stage pills (PARSE / RESOLVE / COMPOSE / RUN), each a small pinned card with mono uppercase label; the leftmost PARSE pill is fully inked, the other three are hairline ghosts. Below, a paper code-window panel (warm cream cardstock, pinned cornered, hand-ruled lines — NOT editorial flat) shows a command line typing in: `/rig:dev "review my branch" --focus security`. Two scribble-note callouts (chip-like) ink in beside the typed command: `フラグ` lassos the `--focus security` token, `自由記述` lassos the `"review my branch"` quoted string, each linked by a thin hand-drawn arrow. Primary visual ~70% canvas. `discrete-text-sequence` types the command (~2.5s, paced to narration); `svg-path-draw` inks the two lasso arrows after the typing completes; the active PARSE pill holds the live ambient slot via `sine-wave-loop` at the subtle floor. Last 0.5s: the two callouts hold static — the levers are separated. Eye exits leftward along the pipeline strip toward stage 2.

## Scene 5: RESOLVE — 規則を重ねる

**Effects:** [`scale-swap-transition`, `svg-path-draw`, `sine-wave-loop`]
**Duration:** 8.04s
**Continuity:** continue

Steady, accumulating rhythm — rules layer one on the other. The shared **four-stage pipeline-strip ribbon** persists at the top from scene 4; the active inked pill `scale-swap-transition`s from PARSE → RESOLVE (one beat, ~0.5s), the PARSE pill demoting to hairline ghost. Layered-depth template continued, diagram register: below the strip, a short vertical stack of four pencil-labeled rule cards inks in top-to-bottom (manifest / recipe / flag / size-aware), each on cream cardstock pinned -10°/+14° alternating, with a Caveat scribble-note in the right margin reading 「上が下を上書き」. `svg-path-draw` inks each rule card outline in sequence (~3.5s total, snappy stagger ~400ms). The active RESOLVE pill is the only breathing element via `sine-wave-loop` at subtle floor; pinned cards rest. Final 0.5s: the stack holds still, fully inked. **Continue handoff:** the pipeline strip and the rule stack both remain on screen at scene-end; the active stage indicator is positioned to advance from RESOLVE to COMPOSE in the next segment, and the rule stack will demote (shrink + dim) as the prompt skeleton enters.

## Scene 6: COMPOSE — 固定順で組む

**Effects:** [`scale-swap-transition`, `dynamic-content-sequencing`, `sine-wave-loop`]
**Duration:** 5.60s
**Continuity:** continue

Confident, click-step rhythm — slots drop in a printed-form order. The pipeline strip advances: RESOLVE pill `scale-swap-transition`s to a hairline ghost while COMPOSE inks active. The rule stack from scene 5 demotes (shrinks ~30%, fades toward hairline) and slides to the right margin as a memory-rail; primary stage shifts to a vertical **prompt skeleton** centered on cream cardstock — five facet slots inked top-to-bottom like a printed form, each a pencil-sketched dashed-border rectangle labeled with a Caveat numeral 1–5 in the left margin, slot names in mono uppercase. Asymmetric 65/35 template, diagram register. `dynamic-content-sequencing` paces the slot ink-ins so each label lands cleanly within ~3.5s; the active COMPOSE pill is the one breathing element via `sine-wave-loop`. The five numerals are the script's "順番が崩れたら壊れる" voice — never substitute a numeric font. Final 0.5s: skeleton holds fully static, ready to hand off to RUN.

## Scene 7: RUN — ネイティブだけで走る

**Effects:** [`svg-path-draw`, `dynamic-content-sequencing`, `asr-keyword-glow`, `sine-wave-loop`]
**Duration:** 8.96s
**Continuity:** break
**Transition:** push-slide LEFT
**Hierarchy:** multi-act
**PrimarySubjectTimeline:** 0–1.0s pipeline strip primary (RUN pill inks active); 1.0–4.5s single dispatch line primary (one mono-uppercase command on cream cardstock); 4.5–8.96s three subagent fan-cards primary (security / design / test pinned spread), parent context kept conspicuously thin as a supporting hairline rail above them.
**Handoff:** Before the three subagent cards fan out, the dispatch line compacts to a hairline ribbon and slides up to sit just under the pipeline strip; the parent context bar from earlier moments demotes to a single thin hairline rule labeled 「親」 with no fill — outside the primary bbox. Camera does not move; the demotion is the handoff.

Momentum-building, opening-out rhythm — one breath releases into three. Layered-depth template, diagram register. The shared pipeline strip persists at the top (RUN now active), then below: a single dispatch line types in across center reading `Agent(parallel: [security, design, test])` in mono uppercase on cream paper. After it compacts, three pencil-sketched subagent cards `svg-path-draw` open as a hand-drawn fan from that compacted line — labeled セキュリティ / デザイン / テスト in mono uppercase with a Caveat scribble-note 「親は薄いまま」 in the right margin. `dynamic-content-sequencing` paces the fan-out (~2.5s, staggered ~350ms total). `asr-keyword-glow` lifts 並列観点 and ひとつのメッセージ on the narration; `sine-wave-loop` is reserved for the parent hairline rail only (the visible "alive but empty" element). Final 0.5s: three cards hold fully pinned at their off-axis tilts.

## Scene 8: context-minimal — 親は空のまま

**Effects:** [`split-tilt-cards`, `counting-dynamic-scale`, `asr-keyword-glow`]
**Duration:** 7.24s
**Continuity:** break
**Transition:** blur-crossfade
**Hierarchy:** multi-act
**PrimarySubjectTimeline:** 0–3.0s left "before" lane primary (fat fill bar swells); 3.0–7.24s right "after" lane primary (thin parent line + three small subagent badges), left lane demotes to supporting low-contrast rail.
**Handoff:** Before the right "after" lane takes primary, the left "before" bar exits its accent saturation — it dims toward hairline and shrinks ~20%, becoming a supporting silhouette. The right lane owns the center safe zone with the warm gold accent flipping in as its focal mark. Camera holds — the contrast itself is the handoff. Stillness-before-climax allocated here: 0.5s pause between the "after" inking complete and the gold accent landing.

Aha, relief-with-a-quiet-smile rhythm — the structural payoff lands. Split-screen template, data-viz/diagram register: paper-split frame with a hand-ruled vertical pencil divider down the middle; left half labeled `before` carries a fat ink-fill bar swelling top to bottom with a Caveat margin-note 「膨らむ」, right half labeled `after` carries a thin hairline parent line with three small pinned subagent badges (セ / デ / テ) underneath and a Caveat margin-note 「空のまま」. Primary visual ~70% canvas. `split-tilt-cards` gives the two halves opposite 3D tilts (~±4°) — the one allowed dimensional play this scene; `counting-dynamic-scale` runs a tiny "tokens" counter that swells on the left (peaks ~3.0s) and stays low on the right; `asr-keyword-glow` lifts 常に空 and 鈍らない on narration. Style delta: accent flips to `--brand-secondary` (warm gold) on the "after" lane only — the warm focal mark this scene. No `sine-wave-loop` this scene — the whole tail is `settle and hold`. Final 0.6s: both lanes fully static, gold accent pinned.

## Scene 9: Parallel review — 3つの verdict

**Effects:** [`dynamic-content-sequencing`, `svg-path-draw`, `asr-keyword-glow`, `sine-wave-loop`]
**Duration:** 7.80s
**Continuity:** break
**Transition:** push-slide RIGHT
**Hierarchy:** simple
**SFX:**

- `click.mp3` at 4.20s, volume 0.30 — first verdict stamp lands
- `click.mp3` at 4.45s, volume 0.30 — second stamp
- `click.mp3` at 4.70s, volume 0.30 — third stamp

Conviction-building rhythm — three stamps land in close succession. Triptych template, typography + diagram register: three vertical reviewer panes on cream cardstock side by side (`セキュリティ` / `デザイン` / `テスト` mono uppercase headers, each a pinned column with hand-ruled top edge). At the top a mono-uppercase command bar reads `/rig:dev --review`. Each pane inks in a short bullet list of finding-lines (`svg-path-draw` traces them with a snappy stagger, total ~3.5s), and at ~4.2s each pane gets a verdict stamp in `--brand-accent` red at -4° tilt reading `VERDICT` in mono uppercase, lined up in a near-simultaneous staccato via `dynamic-content-sequencing`. `asr-keyword-glow` lifts 並列 and ヴァーディクト. `sine-wave-loop` is reserved for the command bar's caret only — the one breathing element. The three stamps stay rotated at the off-axis paper tilt; no counter-rotation. Final 0.5s: full stillness, all three stamps pinned. Eye exits rightward as the camera widens into scene 10.

## Scene 10: Not just dev — 同じハーネスが他のロールでも動く

**Effects:** [`svg-path-draw`, `center-outward-expansion`, `asr-keyword-glow`, `sine-wave-loop`]
**Duration:** 7.52s
**Continuity:** break
**Transition:** zoom-through
**Hierarchy:** multi-act
**PrimarySubjectTimeline:** 0–2.0s harness spine primary (a horizontal hand-drawn ink spine inks in across center); 2.0–7.52s three sibling branch cards primary (`/rig:dev` / `/rig:sales` / `/rig:goal`), spine demotes to a supporting hairline structural element underneath them.
**Handoff:** Before the three sibling branches `center-outward-expansion` outward from the spine, the spine itself loses its inked weight and falls to a hairline rule — the structural axis remains visible but recedes; the three pinned sibling cards own the focal center, each at its own off-axis pin tilt outside the others' bbox. Camera widens (not pushes) — the widening reveal IS the handoff.

Inclusive, expanding-clarity rhythm — the lens widens to show siblings. Triptych template (centered after expansion), diagram register: a horizontal hand-drawn ink spine across mid-canvas labeled 「同じハーネス」 in Caveat above it, three pencil-sketched sibling cards branching off vertically: left `/rig:dev` with a small code-bracket pencil icon, center `/rig:sales` with a handshake icon, right `/rig:goal` with a target icon — each a pinned card on cream cardstock with mono-uppercase command label and a Caveat one-line role description below (開発 / 商談 / ゴール). `svg-path-draw` inks the spine first (~1.5s), then `center-outward-expansion` brings the three siblings outward from a clustered center to their final positions (~2.5s, total stagger ~400ms). `asr-keyword-glow` lifts 開発者だけじゃない and 同じハーネス. `sine-wave-loop` is reserved for the center `/rig:sales` card only — the one breathing element marking the "this is new ground" beat. Final 0.5s: all three cards static at their off-axis pins. Eye exits forward as the camera pushes into the CTA card.

## Scene 11: CTA — インストールしてください

**Effects:** [`3d-text-depth-layers`, `press-release-spring`, `asr-keyword-glow`]
**Duration:** 6.84s
**Continuity:** break
**Transition:** zoom-through
**Hierarchy:** simple

Resolve-with-quiet-warmth rhythm — one card, two lines, one signature. Centered template, typography register: a single quiet pinned card on cream cardstock occupies upper-center ~60% of canvas, two stacked mono-uppercase command lines reading `/plugin install rig` (top, slightly smaller) and `/rig:dev` (bottom, heavy display tier with a `3d-text-depth-layers` one-step paper-shadow extrusion in `--brand-primary` ink — the hero word). Beneath the card, a hairline GitHub handle attribution is pinned in DM-Mono uppercase. To the left margin, a small Caveat scribble-note reads 「ひとりで抱えなくていい」 at -2° tilt. Macro motion: very slow root-level parallax drift — no scale push. `press-release-spring` gives the bottom `/rig:dev` line a single tactile depth press at ~3.0s (linear compression, spring recovery — the only kinetic moment); `asr-keyword-glow` lifts インストール and `/rig:dev` on the narration. No `sine-wave-loop` — the closing beat `settles and holds` for the entire tail. Stillness-before-climax allocated here: 0.5s pause between the press-release and the final hold, where the card sits perfectly still beneath the depth-stamped `/rig:dev`. Final 1.0s: full stillness — the card holds, no drift, no breath. The eye rests on the CTA.
