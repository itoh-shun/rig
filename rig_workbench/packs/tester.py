from __future__ import annotations

import pathlib
import tempfile

from rig_workbench.eval.runner import make_judge_adapter, run_case
from rig_workbench.eval.gate import quality_result_failures

from .manifest import read_json_yaml
from .model import PackError
from .resolver import pack_roots
from .validation import validate_pack


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
    timeout: float = 30,
) -> tuple[dict, int]:
    project_path = pathlib.Path(project).resolve()
    pack = resolve_pack(value, project=project_path)
    manifest = validate_pack(pack)
    case_paths = manifest["assets"]["eval-case"]
    if provider is None:
        return ({"pack_test_schema_version": 1, "pack": manifest["id"],
                 "status": "structural_only", "quality": False,
                 "cases": [pathlib.PurePosixPath(item).parent.name for item in case_paths],
                 "failures": []}, 0)
    if not model:
        raise PackError("pack test --provider requires --model")
    if bool(judge_provider) != bool(judge_model):
        raise PackError("judge provider and judge model must be specified together")
    try:
        judge = make_judge_adapter(
            provider=judge_provider, model=judge_model, repo=project_path,
            command=judge_command, timeout_s=timeout,
        ) if judge_provider and judge_model else None
        results: list[dict] = []
        with tempfile.TemporaryDirectory(prefix="rig-pack-test-") as temporary:
            for rel in case_paths:
                _raw, case = read_json_yaml(pack / rel)
                _path, result = run_case(
                    case, repo=project_path, provider=provider, model=model,
                    repeat=case["repeat"], phase="current", command=command,
                    timeout_s=timeout, judge_adapter=judge, result_root=temporary,
                )
                results.append(result)
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
             "failures": failures}, code)
