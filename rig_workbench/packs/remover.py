from __future__ import annotations

import os
import pathlib
import shutil
import tempfile

from .installer import scope_root
from .lock import (LOCK_SCHEMA_VERSION, lock_path, read_lock, validate_lock_root, write_lock,
                   write_lock_bytes)
from .model import PackError
from .resolver import pack_roots
from .validation import validate_pack


def remove_pack(
    pack_id: str, *, scope: str, project: pathlib.Path | str,
    root: pathlib.Path | str | None = None, yes: bool = False,
) -> tuple[pathlib.Path, bool]:
    project_path = pathlib.Path(project).resolve()
    destination_root = scope_root(
        scope, project=project_path,
        root=pathlib.Path(root) if root is not None else None,
    )
    entries = validate_lock_root(destination_root)
    owned = next((item for item in entries if item["id"] == pack_id), None)
    if owned is None:
        raise PackError(f"pack is not owned by this scope lock: {pack_id}")
    target = destination_root / owned["path"]
    if target.resolve().parent != destination_root.resolve() or target.name != pack_id:
        raise PackError("pack lock ownership path is unsafe")
    manifest = validate_pack(target)
    dependents: list[str] = []
    roots = [pack_root for _tier, pack_root in pack_roots(project_path)]
    if destination_root.resolve() not in {item.resolve() for item in roots}:
        roots.append(destination_root)
    for pack_root in roots:
        if not pack_root.is_dir():
            continue
        for candidate in sorted(item for item in pack_root.iterdir() if item.is_dir()
                                and not item.name.startswith((".", "_"))):
            if candidate.resolve() == target.resolve():
                continue
            candidate_manifest = validate_pack(candidate)
            if any(dep["id"] == manifest["id"] for dep in candidate_manifest["dependencies"]):
                dependents.append(candidate_manifest["id"])
    if dependents:
        raise PackError(f"pack has dependents and cannot be removed: {', '.join(sorted(dependents))}")
    if not yes:
        return target, False
    lock = read_lock(destination_root)
    original_lock = lock_path(destination_root).read_bytes()
    updated = {"pack_lock_schema_version": LOCK_SCHEMA_VERSION,
               "packs": [item for item in lock["packs"] if item["id"] != pack_id]}
    trash = pathlib.Path(tempfile.mkdtemp(prefix=f".pack-trash-{pack_id}-", dir=destination_root))
    trash.rmdir()
    moved = False
    try:
        os.replace(target, trash)
        moved = True
        try:
            write_lock(destination_root, updated)
        except Exception as lock_exc:
            try:
                os.replace(trash, target)
                moved = False
            except OSError as restore_exc:
                raise PackError(
                    f"pack remove lock update failed ({lock_exc}); "
                    f"target rollback also failed ({restore_exc})"
                ) from restore_exc
            raise
        try:
            shutil.rmtree(trash)
        except OSError as delete_exc:
            rollback_errors: list[str] = []
            try:
                os.replace(trash, target)
                moved = False
            except OSError as restore_exc:
                rollback_errors.append(f"target restore failed: {restore_exc}")
            try:
                write_lock_bytes(destination_root, original_lock)
            except Exception as restore_lock_exc:
                rollback_errors.append(f"lock restore failed: {restore_lock_exc}")
            detail = "; ".join(rollback_errors) if rollback_errors else "rollback completed"
            raise PackError(f"pack trash delete failed ({delete_exc}); {detail}") from delete_exc
        moved = False
        return target, True
    except PackError:
        raise
    except OSError as exc:
        raise PackError(f"pack remove transaction failed: {exc}") from exc
    finally:
        # Never silently claim rollback: every recovery failure is raised above.
        pass
