"""Transactional import of externally staged pack evaluation evidence."""

from __future__ import annotations

import os
import pathlib
import shutil
import tempfile

from rig_workbench.eval.cases import EvalCaseError, canonical_json, validate_case
from rig_workbench.eval.execution import execution_diff_sha256
from rig_workbench.eval.gate import quality_result_failures
from rig_workbench.eval.runner import _git_identity

from .manifest import canonical, digest, read_json_yaml
from .lock import tree_hash
from .model import PackError
from .resolver import pack_roots
from .tester import compose_case_prompt, prompt_binding_sha256
from .validation import validate_pack


def import_results(
    value: pathlib.Path | str, *, staged: pathlib.Path | str,
    project: pathlib.Path | str,
) -> list[str]:
    """Validate all staged results, then replace the pack directory as one transaction."""
    project_root = pathlib.Path(project).resolve()
    requested_pack = pathlib.Path(os.path.abspath(pathlib.Path(value).expanduser()))
    if requested_pack.exists() or requested_pack.is_symlink():
        pack_lexical = requested_pack
    else:
        matches = [
            root / str(value) for _tier, root in pack_roots(project_root)
            if (root / str(value)).is_dir() or (root / str(value)).is_symlink()
        ]
        if len(matches) != 1:
            raise PackError(
                f"pack path or id is {'ambiguous' if matches else 'not found'}: {value}"
            )
        pack_lexical = matches[0]
    stage_lexical = pathlib.Path(os.path.abspath(pathlib.Path(staged).expanduser()))
    for candidate in (pack_lexical, stage_lexical):
        cursor = pathlib.Path(candidate.anchor)
        for part in candidate.parts[1:]:
            cursor = cursor / part
            if cursor.is_symlink():
                raise PackError("pack and staged result paths must not traverse symlinks")
    pack = pack_lexical.resolve()
    stage_root = stage_lexical.resolve()
    if not stage_root.is_dir() or stage_root.is_relative_to(pack):
        raise PackError("staged result directory must be an existing external directory")
    if stage_root.is_relative_to(project_root):
        raise PackError("staged result directory must be outside the project repository")
    manifest = validate_pack(pack)
    source_tree = tree_hash(pack)
    source_stat = os.lstat(pack)
    source_identity = (source_stat.st_dev, source_stat.st_ino)
    if (pack / "pack.sig.json").exists():
        raise PackError("cannot import evidence into an already signed pack")

    cases: dict[str, dict] = {}
    try:
        for relative in manifest["assets"]["eval-case"]:
            raw, case = read_json_yaml(pack / relative)
            validate_case(case)
            if raw != canonical_json(case):
                raise PackError(f"evaluation case is not canonical: {relative}")
            required = {"prompt_entrypoint", "prompt_composition",
                        "target_expectations", "clean_expectations"}
            if not required.issubset(case):
                raise PackError(f"evaluation case lacks release composition: {case['id']}")
            if case["status"] != "approved":
                raise PackError(f"evaluation case is not approved: {case['id']}")
            compose_case_prompt(pack, manifest, case, project=project_root)
            if case["id"] in cases:
                raise PackError(f"duplicate owned evaluation case id: {case['id']}")
            cases[case["id"]] = case
    except (EvalCaseError, OSError, UnicodeError) as exc:
        raise PackError(f"invalid owned evaluation case: {exc}") from exc

    if any(path.is_symlink() for path in stage_root.rglob("*")):
        raise PackError("staged evaluation result tree must not contain symlinks")
    sources = sorted(stage_root.rglob("*.json"))
    if not sources:
        raise PackError("staged result directory contains no JSON results")
    # Reject development-only transports before requiring release provenance.
    # This invariant is unconditional, including outside a Git checkout.
    for source in sources:
        try:
            _raw, candidate = read_json_yaml(source)
        except (PackError, OSError, UnicodeError) as exc:
            raise PackError(f"invalid staged evaluation result {source.name}: {exc}") from exc
        if isinstance(candidate, dict) and (
            candidate.get("provider") in {"mock", "command"}
            or candidate.get("judge_provider") in {"mock", "command"}
        ):
            raise PackError("mock/command evidence is dev-only and cannot be imported")

    execution_commit, execution_base, execution_status = _git_identity(project_root)
    if execution_status != "available" or execution_commit is None or execution_base is None:
        raise PackError("current execution git identity is unavailable")
    execution_diff = execution_diff_sha256(project_root, base=execution_base)

    imports: list[tuple[pathlib.Path, str, dict]] = []
    destinations: set[str] = set()
    for source in sources:
        if source.is_symlink() or not source.is_file():
            raise PackError("staged evaluation results must be regular files")
        try:
            raw, result = read_json_yaml(source)
            if raw != canonical(result):
                raise PackError(f"staged evaluation result is not canonical: {source.name}")
            case = cases.get(result.get("case_id") if isinstance(result, dict) else None)
            if case is None:
                raise PackError(f"staged result is not bound to an owned case: {source.name}")
            composed = compose_case_prompt(pack, manifest, case, project=project_root)
            binding = prompt_binding_sha256(manifest, case, composed)
            failures = quality_result_failures(
                result, case, expected_commit=execution_commit,
                expected_base=execution_base, expected_diff=execution_diff,
            )
        except (EvalCaseError, PackError, OSError, UnicodeError) as exc:
            raise PackError(f"invalid staged evaluation result {source.name}: {exc}") from exc
        if result["provider"] in {"mock", "command"} or result["judge_provider"] in {
            "mock", "command",
        }:
            raise PackError("mock/command evidence is dev-only and cannot be imported")
        expected_commit = case["provenance"]["source_commit"]
        if (result["source_commit"] != expected_commit
                or result["source_base_commit"] != expected_commit):
            raise PackError("staged evaluation result provenance does not match its case")
        if result["prompt_binding_sha256"] != binding:
            raise PackError("staged evaluation result prompt/asset binding is stale")
        if result["pack_tree_sha256"] != source_tree:
            raise PackError("staged evaluation result pack tree binding is stale")
        if failures:
            raise PackError(
                f"staged evaluation result failed release policy: {', '.join(failures)}"
            )
        relative = (
            f"evals/results/{result['case_id']}/current-{result['provider']}.json"
        )
        if relative in destinations or relative in manifest["assets"]["eval-result"]:
            raise PackError(f"durable evaluation result already exists: {relative}")
        destinations.add(relative)
        imports.append((source, relative, result))

    temporary = pathlib.Path(tempfile.mkdtemp(prefix=f".{pack.name}.evidence-", dir=pack.parent))
    backup = pack.parent / f".{pack.name}.evidence-backup-{os.getpid()}"
    try:
        temporary.rmdir()
        shutil.copytree(pack, temporary, symlinks=False)
        staged_manifest = dict(manifest)
        staged_manifest["assets"] = {
            kind: list(paths) for kind, paths in manifest["assets"].items()
        }
        staged_manifest["hashes"] = dict(manifest["hashes"])
        for _source, relative, result in imports:
            destination = temporary / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Persist the exact validated object, not a second read of the
            # caller-controlled staging file (avoids validation/copy TOCTOU).
            destination.write_text(canonical(result), encoding="utf-8")
            staged_manifest["assets"]["eval-result"].append(relative)
            staged_manifest["hashes"][relative] = digest(destination)
        staged_manifest["assets"]["eval-result"].sort()
        (temporary / "pack.yaml").write_text(canonical(staged_manifest), encoding="utf-8")
        validate_pack(temporary)
        if tree_hash(pack) != source_tree:
            raise PackError("source pack changed during evidence import")
        current_commit, current_base, current_status = _git_identity(project_root)
        ignored = ()
        try:
            ignored = (temporary.relative_to(project_root).as_posix(),)
        except ValueError:
            pass
        current_diff = execution_diff_sha256(
            project_root, base=execution_base,
            ignored_untracked_prefixes=ignored,
        )
        if ((current_commit, current_base, current_status)
                != (execution_commit, execution_base, execution_status)
                or current_diff != execution_diff):
            raise PackError("repository execution identity changed during evidence import")
        current_stat = os.lstat(pack)
        if (current_stat.st_dev, current_stat.st_ino) != source_identity:
            raise PackError("source pack identity changed during evidence import")
        if backup.exists():
            raise PackError("evidence transaction backup already exists")
        os.replace(pack, backup)
        # Compare-and-swap validation: the exact source object moved to the
        # recoverable backup must still be the one validated above. A racing
        # write is restored, never overwritten or deleted.
        moved_stat = os.lstat(backup)
        if ((moved_stat.st_dev, moved_stat.st_ino) != source_identity
                or tree_hash(backup) != source_tree):
            os.replace(backup, pack)
            raise PackError("source pack changed at evidence transaction commit")
        try:
            os.replace(temporary, pack)
        except Exception:
            os.replace(backup, pack)
            raise
        # Retain the validated source backup. This is the CAS recovery record,
        # and guarantees a late writer holding the old directory inode cannot
        # have its changes silently deleted after the commit.
        return sorted(destinations)
    except PackError:
        raise
    except OSError as exc:
        raise PackError(f"evaluation evidence import transaction failed: {exc}") from exc
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        # A post-commit backup cleanup failure intentionally leaves a
        # recoverable hidden backup and still reports transaction success.
