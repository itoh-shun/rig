from __future__ import annotations

import pathlib
from .resolver import catalog, pack_roots
from .lock import lock_path, validate_lock_root
from .validation import validate_pack, validate_tiered_collection


def diagnose(path: pathlib.Path | str | None = None, *, project: pathlib.Path | str | None = None) -> dict:
    findings: list[dict] = []
    if path is None:
        for tier, pack_root in pack_roots(project):
            if not pack_root.is_dir():
                continue
            managed_dirs = [item for item in pack_root.iterdir() if item.is_dir()
                            and not item.name.startswith((".", "_"))]
            if managed_dirs and not lock_path(pack_root).exists() and tier in {
                "project", "user", "org"
            }:
                findings.append({"code": "unmanaged_pack_root", "path": str(pack_root),
                                 "detail": "validated legacy packs; migrate with pack install",
                                 "scope": tier, "severity": "warning"})
            try:
                validate_lock_root(
                    pack_root,
                    expected_scope=tier if tier in {"project", "user", "org"} else None,
                )
            except Exception as exc:
                findings.append({"code": "lock_drift", "path": str(pack_root),
                                 "detail": str(exc), "scope": tier})
    roots: list[pathlib.Path]
    if path is not None:
        roots = [pathlib.Path(path).resolve()]
    else:
        roots = [item for _tier, root in pack_roots(project) if root.is_dir()
                 for item in sorted(root.iterdir()) if item.is_dir()
                 and not item.name.startswith((".", "_"))]
    manifests: dict[str, dict] = {}
    entries: list[tuple[str, pathlib.Path]] = []
    tier_by_path = {item.resolve(): tier for tier, pack_root in pack_roots(project)
                    if pack_root.is_dir() for item in pack_root.iterdir() if item.is_dir()
                    and not item.name.startswith((".", "_"))}
    for root in sorted(roots):
        try:
            manifest = validate_pack(root)
            if (root / "pack.sig.json").is_file():
                from .publisher import verify_publisher_signature
                if verify_publisher_signature(root, manifest) is None:
                    raise ValueError("publisher signature disappeared during verification")
            manifests[manifest["id"]] = manifest
            entries.append((tier_by_path.get(root.resolve(), "selected"), root))
            # A scaffolded pack satisfies the schema while carrying nothing, and `validate`
            # correctly says so: `valid`. Reporting `ok` here as well told an author they were
            # finished before they had started — three green checks on a pack that cannot be
            # invoked. It is a legitimate intermediate state, so this is a warning and not a
            # failure; what it must not be is silent.
            if not any(manifest["assets"].values()):
                findings.append({"code": "empty_pack", "path": str(root),
                                 "detail": "no assets declared; add one, then `pack sync`",
                                 "severity": "warning"})
        except Exception as exc:
            findings.append({"code": "invalid_pack", "path": str(root), "detail": str(exc)})
    if not any(item.get("severity", "error") != "warning" for item in findings):
        try:
            validate_tiered_collection(entries)
        except Exception as exc:
            detail = str(exc)
            code = next((name for token, name in (
                ("cycle", "dependency_cycle"), ("missing dependency", "missing_dependency"),
                ("incompatible dependency", "incompatible_dependency"),
                ("collision", "collision"), ("duplicate pack", "duplicate_pack"),
            ) if token in detail), "invalid_collection")
            findings.append({"code": code, "path": "collection", "detail": detail})
    grouped: dict[tuple[str, str], list] = {}
    try:
        for item in catalog(project=project):
            grouped.setdefault((item.kind, item.name), []).append(item)
    except Exception as exc:
        if not any(item["detail"] == str(exc) for item in findings):
            findings.append({"code": "invalid_collection", "path": "collection",
                             "detail": str(exc)})
    for (kind, name), items in sorted(grouped.items()):
        if len(items) > 1:
            findings.append({"code": "shadow", "asset": f"{kind}:{name}",
                             "detail": [str(item.path) for item in items]})
    project_root = pathlib.Path(project or pathlib.Path.cwd()).resolve()
    for legacy in (project_root / ".rig" / "recipes", project_root / ".claude" / "rig"):
        if legacy.exists() and (project_root / ".rig" / "packs").exists():
            findings.append({"code": "legacy_conflict", "path": str(legacy),
                             "detail": "legacy and pack tiers coexist"})
    failed = any(item.get("severity", "error") != "warning" for item in findings)
    status = "failed" if failed else ("warning" if findings else "ok")
    return {"pack_doctor_schema_version": 1, "status": status,
            "packs": sorted(manifests), "findings": findings}
