---
slug: pack-authoring-road
title: The road from material to a pack rig can install
status: canonical
sources: ["docs/packs.md", "rig_workbench/packs/model.py", "#546", "#547"]
reviewed_at: "2026-09-02"
---

# The road from material to a pack rig can install

A pack is a directory rig can install, and the road to one has fixed stations. Knowing
the stations is what lets a drafter stop at the right one.

1. **Scaffold** — `rig-wb pack init <id> --type <type> --kind domain --root <dir>`. The
   `type` is a permission: `knowledge` may carry documents and wiki pages, `skill` may
   carry recipes, `tool` alone may carry a recipe that runs `checks:` on the host.
   Adding domain knowledge must not confer command execution, so a drafter picks the
   narrowest type that holds what the material justifies.
2. **Assets** — files under the asset directories (`facets/personas`, `facets/knowledge`,
   `recipes`, `resources`, …). A `resource` is a plain document, hashed and never
   name-resolved; a `wiki` is a page with frontmatter (`slug`, `title`, `status`,
   `sources`, `reviewed_at`) that personas can `inject:`.
3. **Declare** — `rig-wb pack sync <dir>` rewrites `assets` and `hashes` from the
   directory. `pack.yaml` is canonical and byte-compared; it is not a file to edit by
   hand except for `description`, `display_name`, `entrypoints`, `capabilities`, and a
   `knowledge:` block.
4. **Validate** — `rig-wb pack validate <dir>` refuses a manifest that disagrees with the
   directory, and refuses prompt material with no approved evaluation case. A pack of
   pure `resource` files carries no prompt material and passes without one.
5. **Measure** — `rig-wb pack test <dir>` runs the pack's evaluation cases. With no
   non-Claude provider available it reports `structural_only`: the cases were read, and
   nothing was measured. That word is the result; it is not a pass.
6. **Promote** — `rig-wb eval promote --into <dir> <evidence>` turns measured, attested
   evidence into an approved case. A person runs it. A drafter stops before it.
7. **Bundle or install** — `pack bundle` for a zip, `pack install <dir> --scope …` for
   this machine. Also a person's step.

Two words are spelled deliberately. `evidence` inside `knowledge:` is the list of
documents a claim rests on; `sources` is where a pack is installed from. One word with
two meanings in one CLI is a defect the design declined to introduce.
