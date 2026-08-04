from __future__ import annotations

import pathlib

from rig_workbench.eval.runner import make_judge_adapter, run_case
from rig_workbench.eval.gate import quality_result_failures

from .manifest import read_json_yaml
from .model import PackError
from .resolver import pack_roots
from .validation import validate_pack


def compose_case_prompt(
    pack: pathlib.Path, manifest: dict, case: dict, *, project: pathlib.Path,
) -> str:
    """Compose an eval prompt from typed manifest ownership and dependency bindings."""
    from .model import ASSET_DIRS
    from .resolver import resolve_owned_asset

    entry_id = case.get("prompt_entrypoint")
    composition = case.get("prompt_composition")
    if not isinstance(entry_id, str) or not isinstance(composition, list):
        raise PackError(f"evaluation case lacks signed prompt composition: {case.get('id', '?')}")
    entries = [item for item in manifest["entrypoints"] if item["id"] == entry_id]
    if len(entries) != 1:
        raise PackError(f"evaluation prompt entrypoint is not owned: {entry_id}")
    entry = entries[0]
    entry_surface = f"{entry['kind']}:{entry['target']}"
    if entry_surface not in composition:
        raise PackError(f"evaluation composition omits entrypoint target: {entry_id}")
    local: dict[tuple[str, str], pathlib.Path] = {}
    for kind, paths in manifest["assets"].items():
        prefix = pathlib.PurePosixPath(ASSET_DIRS[kind])
        for relative in paths:
            name = str(pathlib.PurePosixPath(relative).relative_to(prefix).with_suffix(""))
            local[(kind, name)] = pack / relative
    references = {
        (item["kind"], item["id"]): item["pack"] for item in manifest["references"]
    }
    sections: list[str] = []
    for surface in composition:
        kind, name = surface.split(":", 1)
        manifest_kind = "output-contract" if kind == "contract" else kind
        path = local.get((manifest_kind, name))
        owner = manifest["id"]
        if path is None:
            owner = references.get((manifest_kind, name), "")
            resolved = (
                resolve_owned_asset(manifest_kind, name, owner, project=project)
                if owner else None
            )
            if resolved is None:
                raise PackError(f"evaluation composition dependency is unavailable: {surface}")
            path = resolved.path
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise PackError(f"cannot read evaluation composition asset: {surface}") from exc
        sections.append(f"--- {surface} (owner={owner}) ---\n{text.rstrip()}")
    return "\n\n".join(sections)


def resolve_pack(value: pathlib.Path | str, *, project: pathlib.Path) -> pathlib.Path:
    candidate = pathlib.Path(value).expanduser()
    if candidate.exists():
        return candidate.resolve()
    name = str(value)
    matches = [root / name for _scope, root in pack_roots(project) if (root / name).is_dir()]
    if not matches:
        raise PackError(f"pack path or id not found: {value}")
    if len(matches) > 1:
        raise PackError(f"pack id is ambiguous across scopes: {value}")
    return matches[0].resolve()


def test_pack(
    value: pathlib.Path | str, *, project: pathlib.Path | str,
    provider: str | None = None, model: str | None = None,
    judge_provider: str | None = None, judge_model: str | None = None,
    command: str | None = None, judge_command: str | None = None,
    timeout: float = 30, result_dir: pathlib.Path | str | None = None,
) -> tuple[dict, int]:
    project_path = pathlib.Path(project).resolve()
    pack = resolve_pack(value, project=project_path)
    manifest = validate_pack(pack)
    case_paths = manifest["assets"]["eval-case"]
    if provider is None:
        return ({"pack_test_schema_version": 1, "pack": manifest["id"],
                 "status": "structural_only", "quality": False,
                 "cases": [pathlib.PurePosixPath(item).parent.name for item in case_paths],
                 "result_paths": [], "failures": []}, 0)
    if not model:
        raise PackError("pack test --provider requires --model")
    if provider == "command" or judge_provider == "command":
        raise PackError("pack evaluation forbids command subject and judge adapters")
    if provider == "claude" or judge_provider == "claude":
        raise PackError("pack evaluation requires an OS-level read-only adapter")
    if result_dir is None:
        raise PackError("pack quality evaluation requires caller-selected --result-dir")
    result_root = pathlib.Path(result_dir).expanduser().resolve()
    if result_root.is_relative_to(pack) or result_root.is_relative_to(project_path):
        raise PackError("pack evaluation result-dir must be external to pack and project")
    if bool(judge_provider) != bool(judge_model):
        raise PackError("judge provider and judge model must be specified together")
    try:
        adapter_cwd = result_root / ".read-only-workspace"
        adapter_cwd.mkdir(parents=True, exist_ok=True)
        adapter_cwd.chmod(0o555)
        judge = make_judge_adapter(
            provider=judge_provider, model=judge_model, repo=adapter_cwd,
            command=judge_command, timeout_s=timeout,
        ) if judge_provider and judge_model else None
        results: list[dict] = []
        result_paths: list[str] = []
        for rel in case_paths:
            _raw, case = read_json_yaml(pack / rel)
            prompt = compose_case_prompt(pack, manifest, case, project=project_path)
            result_path, result = run_case(
                case, repo=project_path, provider=provider, model=model,
                repeat=case["repeat"], phase="current", command=command,
                timeout_s=timeout, judge_adapter=judge, result_root=result_root,
                prompt_prefix=prompt, execution_cwd=adapter_cwd,
            )
            results.append(result)
            result_paths.append(str(result_path))
    except PackError:
        raise
    except Exception as exc:
        raise PackError(f"pack evaluation failed: {exc}") from exc
    infra = sorted({
        f"provider_unavailable:{result['case_id']}"
        for result in results
        if any(row["infra_status"] in {"unavailable", "provider_error", "timeout"}
               for row in [*result["target"], *result["clean"]])
    })
    if infra:
        status, code = "provider_unavailable", 2
        failures = infra
    elif provider == "mock" or judge_provider == "mock":
        status, code = "non_quality_mock", 0
        failures = []
    else:
        by_id = {}
        for rel in case_paths:
            _raw, case = read_json_yaml(pack / rel)
            by_id[case["id"]] = case
        failures = sorted({failure for result in results
                           for failure in quality_result_failures(
                               result, by_id[result["case_id"]], provider=provider,
                               model=model, judge_provider=judge_provider,
                               judge_model=judge_model,
                           )})
        status, code = ("pass", 0) if not failures else ("quality_failed", 1)
    return ({"pack_test_schema_version": 1, "pack": manifest["id"],
             "status": status, "quality": status == "pass",
             "cases": [result["case_id"] for result in results],
             "result_paths": result_paths, "failures": failures}, code)
