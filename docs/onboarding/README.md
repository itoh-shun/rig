# Onboarding material

Teaching material for someone who has never used rig, in Japanese. Two halves in every
format: what rig is and why it is safe, then how to extend the knowledge layer with a pack.

| file | what it is |
|---|---|
| `rig-intro.ja.pptx` | 31-slide deck (16:9). Opens with why rig exists, the holes a common AI setup leaves open, and the prompt/context/harness nesting, then the two parts |
| `build-deck.js` | the generator for that deck — `npm install pptxgenjs && node build-deck.js` rewrites `rig-intro.pptx` in the working directory |
| `rig-primer.ja.html` | the same material as a long-form editorial page |
| `rig-deck.ja.html` | a browser-native slide version (arrow keys, click, `#n` deep links) |

Everything here is sourced from this repository only — `README.ja.md`, `docs/packs.md`,
`docs/landscape.md`, `skills/engine/SKILL.md`, `skills/engine/facets/knowledge/harness-taxonomy.md`,
and `rig_workbench/packs/model.py`. Nothing is claimed that those do not say.

These are documents, not prompt surfaces: nothing here is loaded into a run, so the
evaluation ratchet does not apply. When a command's flags change, the deck goes stale
silently — treat each command's `--help` and the files above as the source of truth, and
regenerate rather than hand-patching the `.pptx`.

## Register

The prose is written under the shipped `japanese-writing` pack, in `talk` mode
(`facets/policies/japanese-writing-modes.md`): polite form throughout, plain words
over Sino-Japanese compounds, the reader addressed directly, and short sentences.

Two facets do the work. `japanese-writing-rules-v2` (Rules v3) says to pick one
register for the reader and the venue and hold it to the end.
`facets/knowledge/japanese-ai-smell-jp.md` lists the markers to keep out — the
em-dash aside, the aphorism close, "not merely A, but B", "neither X nor Y but Z",
the conclusion-dodging hedge, and the words that show up where a writer could not
find a concrete one.

`python3 scripts/prose_rhythm.py <file>` reports no findings on all three
(measure prose only — the gate-preset tables are criterion names, and counting
them as sentences produces a false long-run). It measures surface proxies; a clean
report is not proof the prose reads well.

Two things this has not had. The pack's recipe asks for an independent reviewer on
a different model or provider, and only Claude was available here, so nothing has
verified this writing but the hand that wrote it. And the AI-smell catalog is
guidance, not a detector: nothing here was rejected for matching a marker alone.
