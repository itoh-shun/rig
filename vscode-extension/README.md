# rig board (VS Code extension) — #286

A read-only sidebar view of rig's `.rig/runs/` task/gate state, so you don't have to leave the editor to run `/rig:rig board`.

**Read-only, by design.** This extension never writes anything — there is no accept/discard/gate command here. It parses the same JSON files `scripts/workbench.py` already writes (`task.json` / `acceptance.json` / `steps.json`) and renders them; no new state-management engine.

## What it shows

An Explorer sidebar panel ("rig board") listing each task with its status and gate result (`passed` / `passed_with_warnings` / `failed` / `pending` / `skipped`), refreshed automatically via a file watcher on `.rig/runs/**`. Toggle "Show All" (title-bar icon) to include `accepted`/`discarded` tasks, matching `workbench.py board --all`.

## Install (from source — not yet published to the Marketplace)

```bash
cd vscode-extension
npm install
npm run compile
```

Then either:
- **F5 in VS Code** with this folder open (`Run Extension` launch config VS Code generates automatically for an extension project) — opens an Extension Development Host window.
- **Package + install:** `npx vsce package` (needs `vsce`, not bundled here) to produce a `.vsix`, then `code --install-extension rig-board-0.1.0.vsix`.

## Verification (honest scope)

- `npm run compile` (`tsc -p ./`) compiles cleanly against `@types/vscode`.
- The state-parsing logic (`src/rigState.ts`) has **no dependency on the `vscode` module**, so it's unit-tested with plain Node (`npm run test:unit`, see `src/test/rigState.test.ts`): gate-status priority order matches `workbench.py`'s `gate_status()` exactly (failed > pending > all-skipped > warning > passed), `listTasks()` correctly parses `task.json`/`acceptance.json`/`steps.json` from a scratch `.rig/runs/` tree, and `activeOnly()` matches `board`'s default active-only filter.
- **Not verified**: actually loading this extension inside a real VS Code Extension Host and confirming the Tree View renders and the file watcher fires on live edits. This sandboxed environment has no VS Code GUI to launch — the compiled `extension.ts` is correct against the type definitions and the `activate()` wiring (TreeDataProvider, FileSystemWatcher, commands) follows the standard extension API pattern, but it has not been run inside VS Code itself. Treat it as reviewed-but-not-live-tested until someone runs it with F5.
