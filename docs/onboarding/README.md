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

The prose follows the shipped `japanese-writing` pack: `facets/policies/japanese-writing-rules-v2.md`
(pick a register suited to the reader and the venue, and do not waver in the middle) and the
`ai-writing-smells` catalog in `skills/engine/facets/knowledge/`. Everything here is polite-form
(ですます), avoids the em-dash aside (marker H) and the aphorism close (marker M), and states the
subject before the predicate rather than inverting for effect.

`python3 scripts/prose_rhythm.py <file>` is the deterministic check and reports no findings on all
three. It measures surface proxies only; a clean report is not proof the prose reads well.

The pack's revision recipe also asks for an independent reviewer on a different model or provider.
That step has **not** been run: this session only had Claude available (`codex`, `ollama` and
`lmstudio` are all absent), so the writing has had no independent verification.
