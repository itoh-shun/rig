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
