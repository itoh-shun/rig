from __future__ import annotations

import os
import pathlib
from collections.abc import Iterable

from .model import ASSET_DIRS, PackError, ResolvedAsset, ResolvedPack


def _rig_home() -> pathlib.Path:
    configured = os.environ.get("RIG_HOME")
    return pathlib.Path(configured).expanduser().resolve() if configured else pathlib.Path(__file__).resolve().parents[2]


def _project_root(project: pathlib.Path | str | None) -> pathlib.Path:
    return pathlib.Path(project or pathlib.Path.cwd()).resolve()


def pack_roots(project: pathlib.Path | str | None = None) -> list[tuple[str, pathlib.Path]]:
    root = _project_root(project)
    home = pathlib.Path(os.environ.get("RIG_USER_HOME", pathlib.Path.home())).expanduser()
    org = os.environ.get("RIG_ORG_HOME")
    result = [("project", root / ".rig" / "packs"), ("user", home / ".rig" / "packs")]
    if org:
        result.append(("org", pathlib.Path(org).expanduser() / "packs"))
    rig = _rig_home()
    result.extend((("official", rig / "packs" / "official"), ("core", rig / "packs" / "core")))
    return result


def _pack_entries_with_trust(
    project: pathlib.Path,
) -> tuple[list[tuple[str, pathlib.Path]], dict[tuple[str, str], str]]:
    from .lock import validate_lock_root
    entries: list[tuple[str, pathlib.Path]] = []
    trust: dict[tuple[str, str], str] = {}
    for tier, root in pack_roots(project):
        if not root.is_dir():
            continue
        expected = tier if tier in {"project", "user", "org"} else None
        for locked in validate_lock_root(root, expected_scope=expected):
            trust[(tier, locked["id"])] = locked["verification_status"]
        entries.extend(
            (tier, item) for item in sorted(root.iterdir())
            if item.is_dir() and not item.name.startswith((".", "_"))
        )
    return entries, trust


def _pack_entries(project: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    return _pack_entries_with_trust(project)[0]


def resolved_collection(
    *, project: pathlib.Path | str | None = None,
    shared: pathlib.Path | str | None = None,
) -> list[ResolvedPack]:
    """Return the one validated, dependency-ordered pack collection in effect.

    This is the public collection boundary for consumers that need pack-level
    provenance.  Lock validation and collection validation are fail-closed;
    consumers must not rediscover or partially validate tier directories.
    """
    from .validation import validate_tiered_collection

    # Installed packs are repository state, not per-working-tree state; see `resolve_all`.
    project_root = _project_root(project if shared is None else shared)
    entries, trust = _pack_entries_with_trust(project_root)
    records = validate_tiered_collection(entries)
    return [
        ResolvedPack(
            tier=tier,
            path=path,
            manifest=manifest,
            verification_status=trust.get((tier, manifest["id"]), "unverified"),
        )
        for tier, path, manifest in records
    ]


def _validated_pack_assets(project: pathlib.Path) -> list[ResolvedAsset]:
    found: list[ResolvedAsset] = []
    for record in resolved_collection(project=project):
        tier, pack, manifest = record.tier, record.path, record.manifest
        for kind, paths in manifest["assets"].items():
            for rel in paths:
                path = pack / rel
                prefix = pathlib.PurePosixPath(ASSET_DIRS[kind])
                name = str(pathlib.PurePosixPath(rel).relative_to(prefix).with_suffix(""))
                if kind == "eval-case" and name.endswith("/case"):
                    name = name[:-5]
                found.append(ResolvedAsset(kind, name, path, tier, str(pack), manifest["id"]))
    return found


def _legacy_assets(project: pathlib.Path,
                   shared: pathlib.Path | None = None) -> Iterable[ResolvedAsset]:
    """Project-tier assets, from whichever tree each kind actually belongs to.

    The four directories are not the same kind of thing. `.rig/recipes` is gitignored —
    machine-local state that belongs to the repository, and a task worktree that resolved
    its own empty copy would route differently from the checkout beside it. The three under
    `.claude/` are tracked, so they are branch content: a branch carrying its own recipe
    override has to be the tree that is read. `shared` defaults to `project` so every caller
    that has only one root keeps the behaviour it had (#471).
    """
    shared_root = project if shared is None else shared

    def _first_that_exists(*parts: str) -> pathlib.Path:
        """The working tree's copy when it has one, the repository's otherwise.

        Whether `.claude/` is branch content is a fact about the repository, not about rig:
        this one gitignores it, and a repository that gitignores it has no copy in a linked
        worktree at all — resolving per tree there would lose the assets entirely, which is
        the same defect `.rig/packs` had. Preferring the working tree keeps a branch that
        does track its own overrides winning, and falling back keeps a repository that does
        not from losing them.
        """
        for root in (project, shared_root):
            candidate = root.joinpath(*parts)
            if candidate.is_dir():
                return candidate
        return project.joinpath(*parts)

    mappings = [
        ("project", shared_root / ".rig" / "recipes", "recipe"),
        ("project", _first_that_exists(".claude", "rig", "recipes"), "recipe"),
        ("project", _first_that_exists(".claude", "rig", "personas"), "persona"),
        ("project", _first_that_exists(".claude", "rig", "knowledge"), "wiki"),
    ]
    for tier, directory, kind in mappings:
        if directory.is_dir():
            for path in sorted(directory.rglob("*.md")):
                yield ResolvedAsset(kind, str(path.relative_to(directory).with_suffix("")), path,
                                    tier, f"legacy:{directory}", None)


def _core_assets() -> Iterable[ResolvedAsset]:
    # The engine skill's directory name is resolved (not hardcoded) so a pre-rename
    # `skills/rig/` install still resolves — same rule as orchestrate.config.
    from rig_workbench.orchestrate.config import _skill_root

    rig = _rig_home()
    skills = _skill_root(rig) or rig / "skills" / "engine"
    facets = skills / "facets"
    mappings = {
        "recipe": skills / "recipes",
        "persona": facets / "personas",
        "instruction": facets / "instructions",
        "pattern": skills / "patterns",
        "wiki": facets / "knowledge",
        "policy": facets / "policies",
        "output-contract": facets / "output-contracts",
        "command": rig / "commands", "agent": rig / "agents",
    }
    for kind, directory in mappings.items():
        if directory.is_dir():
            for suffix in ("*.md", "*.yaml", "*.yml"):
                for path in sorted(directory.rglob(suffix)):
                    yield ResolvedAsset(kind, str(path.relative_to(directory).with_suffix("")), path,
                                        "core", f"core:{directory}", "rig-core")


def resolve_all(kind: str, name: str, *, project: pathlib.Path | str | None = None,
                shared: pathlib.Path | str | None = None) -> list[ResolvedAsset]:
    """Every asset matching (kind, name), ranked by tier.

    `project` is the tree whose *tracked* content counts — branch content. `shared` is the
    repository whose gitignored install state counts: `.rig/packs` and `.rig/recipes` are
    installed once per machine, and a linked worktree has neither, so resolving them from
    there would silently route a task differently from the checkout beside it. Omitting
    `shared` means the two are the same tree, which is what every caller with one root
    wants and what this did before (#471).
    """
    if kind not in ASSET_DIRS:
        raise PackError(f"unknown asset kind: {kind}")
    project_root = _project_root(project)
    shared_root = project_root if shared is None else _project_root(shared)
    candidates: list[ResolvedAsset] = []
    candidates.extend(item for item in _validated_pack_assets(shared_root)
                      if item.kind == kind and item.name == name)
    candidates.extend(item for item in _legacy_assets(project_root, shared_root)
                      if item.kind == kind and item.name == name)
    if kind != "eval-case":
        candidates.extend(item for item in _core_assets() if item.kind == kind and item.name == name)
    rank = {tier: index for index, tier in enumerate(("project", "user", "org", "official", "core"))}
    return sorted(candidates, key=lambda item: (rank[item.tier], item.source, str(item.path)))


def resolve_asset(kind: str, name: str, *, project: pathlib.Path | str | None = None,
                  shared: pathlib.Path | str | None = None) -> ResolvedAsset | None:
    matches = resolve_all(kind, name, project=project, shared=shared)
    if not matches:
        return None
    winner = matches[0]
    return ResolvedAsset(
        winner.kind, winner.name, winner.path, winner.tier, winner.source, winner.pack_id,
        tuple(str(item.path) for item in matches[1:]),
    )


def resolve_owned_asset(
    kind: str, name: str, pack_id: str, *, project: pathlib.Path | str | None = None,
    shared: pathlib.Path | str | None = None,
) -> ResolvedAsset | None:
    """Resolve an asset from its declared owner, without cross-pack shadowing.

    This is deliberately separate from ``resolve_asset``: legacy/unqualified
    callers retain tier precedence, while typed pack references use identity.
    """
    if pack_id == "rig-core":
        matches = [item for item in _core_assets()
                   if item.kind == kind and item.name == name]
    else:
        matches = [item for item in resolve_all(kind, name, project=project, shared=shared)
                   if item.pack_id == pack_id]
    if not matches and pack_id != "rig-core":
        from .catalog import discover_builtin_packs
        prefix = pathlib.PurePosixPath(ASSET_DIRS[kind])
        for (namespace, candidate_id), (pack, manifest) in discover_builtin_packs().items():
            if candidate_id != pack_id:
                continue
            for relative in manifest["assets"][kind]:
                asset_name = str(
                    pathlib.PurePosixPath(relative).relative_to(prefix).with_suffix("")
                )
                if asset_name == name:
                    matches.append(ResolvedAsset(
                        kind, name, pack / relative, namespace, str(pack), pack_id
                    ))
    if not matches:
        return None
    winner = matches[0]
    return ResolvedAsset(
        winner.kind, winner.name, winner.path, winner.tier, winner.source, winner.pack_id,
        tuple(str(item.path) for item in matches[1:]),
    )


def resolve_bound_asset(
    kind: str, name: str, source: pathlib.Path | str,
    *, project: pathlib.Path | str | None = None,
    shared: pathlib.Path | str | None = None,
) -> ResolvedAsset | None:
    """Resolve a typed reference declared by the pack owning ``source``.

    ``None`` means the source is legacy or has no typed binding, so callers may
    continue their historical unqualified lookup. A declared but unavailable
    owner fails closed instead of silently selecting another pack.
    """
    from .catalog import discover_builtin_packs, distribution_root
    from .validation import validate_tiered_collection

    project_root = _project_root(project)
    # Installed packs are one set per repository (#471); a linked worktree has none of its
    # own, so resolving them from there would silently drop every project-tier owner.
    shared_root = project_root if shared is None else _project_root(shared)
    source_path = pathlib.Path(source).resolve()
    entries = _pack_entries(shared_root)
    installed_source = any(
        source_path == path.resolve() or source_path.is_relative_to(path.resolve())
        for _tier, path in entries
    )
    builtin_root = (distribution_root() / "packs").resolve()
    builtin_source = source_path.is_relative_to(builtin_root)
    if not installed_source and not builtin_source:
        return None
    records = validate_tiered_collection(entries) if installed_source else []
    if builtin_source:
        known_paths = {path.resolve() for _tier, path, _manifest in records}
        records.extend(
            (namespace, path, manifest)
            for (namespace, _pack_id), (path, manifest) in discover_builtin_packs().items()
            if path.resolve() not in known_paths
        )
    for _tier, pack, manifest in records:
        pack_path = pack.resolve()
        try:
            relative = source_path.relative_to(pack_path).as_posix()
        except ValueError:
            continue
        if relative not in {item for paths in manifest["assets"].values() for item in paths}:
            return None
        owners = [reference["pack"] for reference in manifest.get("references", [])
                  if reference["kind"] == kind and reference["id"] == name]
        if not owners:
            return None
        if len(owners) != 1:
            raise PackError(f"ambiguous typed reference owner: {kind}:{name}")
        # The owner lookup is the second half of the same question, and it reaches for
        # the same installed packs. Handed only the working tree it finds none of them
        # and a typed reference between two repository-installed packs fails as
        # "owner is unavailable" — from a linked worktree only.
        resolved = resolve_owned_asset(kind, name, owners[0], project=project_root,
                                       shared=shared_root)
        if resolved is None:
            raise PackError(f"typed reference owner is unavailable: {owners[0]}:{kind}:{name}")
        return resolved
    return None


def catalog(*, project: pathlib.Path | str | None = None,
            shared: pathlib.Path | str | None = None) -> list[ResolvedAsset]:
    root = _project_root(project)
    # Installed packs are repository state; see `resolve_all`.
    shared_root = root if shared is None else _project_root(shared)
    all_items: list[ResolvedAsset] = _validated_pack_assets(shared_root)
    all_items.extend(_legacy_assets(root, shared_root))
    all_items.extend(_core_assets())
    return sorted(all_items, key=lambda item: (item.kind, item.name, item.tier, str(item.path)))


def resolve_resource(pack_id: str, name: str, *, project: pathlib.Path | str | None = None) -> dict | None:
    """Resolve inert pack data with its validated metadata; never execute it."""
    from .validation import validate_tiered_collection

    project_root = _project_root(project)
    for tier, pack, manifest in validate_tiered_collection(_pack_entries(project_root)):
        if manifest["id"] != pack_id:
            continue
        prefix = pathlib.PurePosixPath(ASSET_DIRS["resource"])
        for relative in manifest["assets"].get("resource", []):
            resource_name = str(
                pathlib.PurePosixPath(relative).relative_to(prefix).with_suffix("")
            )
            if resource_name == name:
                metadata = manifest["resources"][relative]
                return {
                    "pack_id": pack_id, "name": name, "path": pack / relative,
                    "tier": tier, "media_type": metadata["media_type"],
                    "size": metadata["size"], "sha256": metadata["sha256"],
                    "executable": False,
                }
        return None
    return None
