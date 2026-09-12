"""Transactional import of externally staged pack evaluation evidence."""

from __future__ import annotations

import os
import pathlib
import shutil
import tempfile
from typing import Any, Protocol, runtime_checkable

from .eval_bridge import EVALUATION
from .manifest import canonical, digest, read_json_yaml
from .lock import tree_hash
from .model import PackError
from .resolver import core_reference_ids, pack_roots
from .tester import compose_case_prompt, prompt_binding_sha256
from .validation import validate_pack


@runtime_checkable
class EvalEvidence(Protocol):
    """What importing staged evidence needs to know about evaluation, stated here.

    Five questions, and not one of them is this module's to answer. Whether a document is
    a well-formed case and what its canonical bytes are belong to whoever defines the case
    schema; what commit, base and diff a measurement ran against belongs to whoever runs
    measurements; and whether a result clears release policy belongs to the gate that
    decides that everywhere else. Answering any of them again here would give the pack
    installer a second opinion, and a second opinion that is never exercised is the one
    that goes stale.

    Stated as a protocol rather than imported because the import is what the layering rule
    forbids a judgement module (`tests/test_layering_contract.py`): the standard library,
    its own pillar and the six ports, and `eval` is none of the three. `packs/eval_bridge.py`
    satisfies it and the shell hands it in. One of the five is the reason this is worth
    saying out loud: the identity of an execution used to arrive here as
    `eval.runner._git_identity` — a private name in another pillar, which is a dependency on
    its internals rather than on anything it published. `git_identity` is the public name the
    bridge gives it, so the reach-in stops at the adapter.
    """

    #: The error the case and result machinery raises, so a caller can catch it without
    #: naming the class.
    CaseError: type[Exception]

    def validate_case(self, case: Any) -> dict:
        """The case, checked; raises `CaseError` if it is not one."""
        ...

    def canonical_json(self, value: Any) -> str:
        """The one serialisation a stored case is compared against."""
        ...

    def git_identity(self, repo: pathlib.Path) -> tuple[str | None, str | None, str]:
        """Commit, base commit and availability for the tree as it stands."""
        ...

    def execution_diff(self, repo: pathlib.Path, *, base: str,
                       ignored_untracked_prefixes: tuple[str, ...] = ()) -> str:
        """A digest of the working tree against `base`."""
        ...

    def result_failures(self, result: dict, case: dict, *, expected_commit: str | None = None,
                        expected_base: str | None = None, expected_diff: str | None = None,
                        verify_attestation: bool = True) -> list[str]:
        """Why the result does not clear release policy, empty when it does."""
        ...


def import_results(
    value: pathlib.Path | str, *, staged: pathlib.Path | str,
    project: pathlib.Path | str, evaluation: EvalEvidence = EVALUATION,
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
    manifest = validate_pack(pack, core_ids=core_reference_ids())
    source_tree = tree_hash(pack)
    source_stat = os.lstat(pack)
    source_identity = (source_stat.st_dev, source_stat.st_ino)
    cases: dict[str, dict] = {}
    try:
        for relative in manifest["assets"]["eval-case"]:
            raw, case = read_json_yaml(pack / relative)
            evaluation.validate_case(case)
            if raw != evaluation.canonical_json(case):
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
    except (evaluation.CaseError, OSError, UnicodeError) as exc:
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

    execution_commit, execution_base, execution_status = evaluation.git_identity(project_root)
    if execution_status != "available" or execution_commit is None or execution_base is None:
        raise PackError("current execution git identity is unavailable")
    execution_diff = evaluation.execution_diff(project_root, base=execution_base)

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
            failures = evaluation.result_failures(
                result, case, expected_commit=execution_commit,
                expected_base=execution_base, expected_diff=execution_diff,
            )
        except (evaluation.CaseError, PackError, OSError, UnicodeError) as exc:
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
        validate_pack(temporary, core_ids=core_reference_ids())
        if tree_hash(pack) != source_tree:
            raise PackError("source pack changed during evidence import")
        current_commit, current_base, current_status = evaluation.git_identity(project_root)
        ignored = ()
        try:
            ignored = (temporary.relative_to(project_root).as_posix(),)
        except ValueError:
            pass
        current_diff = evaluation.execution_diff(
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
