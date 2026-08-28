# Moving a pack out of the rig repository

Rig is heading for a state where it ships the pack machinery and no domain packs: each team
owns its packs in its own repository, releases them on its own schedule, and rig never has to
be updated for a pack to change. This is the mechanical half of getting there.

Nothing below is automatic. Which forge a pack lives on, whether it is public or private, and
who may read it are the owner's decisions, and a tool that made them would be guessing at
exactly what the migration exists to hand over.

## For a pack owner

### 1. Export the pack as its own repository tree

```console
rig-wb pack export packs/domain/japanese-writing --to ../rig-pack-japanese-writing
```

The pack is validated where it stands first — exporting an invalid pack would move the
problem into a fresh repository where whoever has to fix it knows less than you do now.

The result is a repository with the pack in a subdirectory and a generated `README.md` at the
root:

```text
rig-pack-japanese-writing/
├─ README.md
└─ japanese-writing/
   ├─ pack.yaml
   ├─ compatibility.yaml
   ├─ recipes/ facets/ commands/ evals/ resources/
```

The pack sits one level down on purpose. Its own directory stays strictly declared — every
file in it is named in `pack.yaml` and hashed, which is what makes the type rules enforceable
— while the repository around it can hold the things a repository needs: a README, a licence,
CI. Only the pack directory is ever copied to somebody who installs it.

### 2. Create the repository and push

```console
cd ../rig-pack-japanese-writing
git init && git add -A && git commit -m "japanese-writing 0.6.0"
git remote add origin <your repository URL>
git push -u origin main
```

### 3. Tag the release

The tag and the manifest have to agree. `rig-wb pack update` refuses a tag whose manifest
declares a different version, because otherwise `@1.6.0` could install whatever the manifest
happened to say.

```console
git tag v0.6.0 && git push origin v0.6.0
```

Every later release is the same two steps: bump `version` in `pack.yaml`, tag `v<version>`.
Only exact `vX.Y.Z` tags count as releases — `rig-wb pack outdated` ignores anything else, so
a nightly or a release candidate will not be recommended to your consumers.

## For a consumer

Declare where your packs come from, once, then install by name:

```console
rig-wb pack source add product --scheme git+ssh --url git@github.com:acme/rig-pack-{pack}.git
rig-wb pack install product:japanese-writing@0.6.0 --scope project
```

Authentication is whatever `git` on that machine already uses — an SSH agent, a credential
helper, `gh auth`, the OS keychain, a CI secret. Rig does not read tokens, does not prompt for
them, and refuses a source URL that embeds one. Your lock records the source's *name* and the
commit, never the URL.

After that, nothing about using the pack differs from a bundled one: assets resolve through
the same tier order, so a project overlay still wins over an installed pack.

```console
rig-wb pack list             # what is installed, and where each came from
rig-wb pack info japanese-writing
rig-wb pack outdated         # what each source publishes now
rig-wb pack update japanese-writing --to 0.7.0
rig-wb pack verify-sources   # do the recorded pins still hold?
```

## What changes for existing users

**`rig-wb pack install domain:<name>` keeps working while the pack is still bundled.** The
builtin `domain:` and `official:` namespaces are reserved — a project cannot declare a source
called `domain`, so nothing you add can silently change what that alias installs. When a pack
leaves this repository its alias goes with it, and the install line becomes the two commands
above.

**A path install keeps working.** `rig-wb pack install ./some/directory` is unchanged, and is
still how you install a pack you are developing. It has no version to resolve, so
`rig-wb pack update` refuses it: reinstall from wherever it actually came from.

**Offline and air-gapped installs are supported, two ways.** `git+file` points at a local or
mounted clone and behaves exactly like a remote — same pin, same refusals. A zip or tar of the
pack directory also still installs. Neither is a lesser path.

## What does not move

A minimal fixture pack stays in this repository. Externalising it too would make rig's own
tests depend on another repository being reachable, which is putting your CI in someone
else's hands.

## Open question for the rig maintainers

`rig_workbench/validation/release.py` checks the **bundled** Japanese-writing pack's version
against the engine range at release time. When that pack leaves, the check loses its subject.
It has to become one of two things, and it is worth deciding before the move rather than
after: a check that reads the pack lock (so it constrains what a release was tested against),
or a check that belongs to the pack's own repository (so its owner runs it). Doing neither
means the release gate quietly stops checking anything.
