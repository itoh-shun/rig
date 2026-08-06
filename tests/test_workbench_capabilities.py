import argparse
import copy
import json
import pathlib
import subprocess

import pytest

from test_eval_cases import valid_case


def _git_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "route@example.test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Route Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("route\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    return repo


def _asset(tmp_path, name, *, tier="official", pack_id="design"):
    from rig_workbench.packs.model import ResolvedAsset

    path = tmp_path / f"{name}.md"
    path.write_text(f"---\nname: {name}\nsteps: []\n---\n", encoding="utf-8")
    return ResolvedAsset("recipe", name, path, tier, str(tmp_path), pack_id)


def _official_record(pack_id, recipe):
    return {
        "id": pack_id, "kind": "official", "version": "1.0.0",
        "display_name": pack_id, "description": pack_id,
        "capabilities": ["recipe"],
        "entrypoints": [{"id": recipe, "kind": "recipe", "target": recipe}],
        "manifest_sha256": "a" * 64, "alias": f"official:{pack_id}",
    }


def _official_recipe_pack(root: pathlib.Path, pack_id: str, recipe_name: str) -> pathlib.Path:
    from rig_workbench import __version__
    from rig_workbench.packs.manifest import canonical, digest
    from rig_workbench.packs.model import ASSET_DIRS

    pack = root / pack_id
    for directory in ASSET_DIRS.values():
        (pack / directory).mkdir(parents=True, exist_ok=True)
    recipe = pack / "recipes" / f"{recipe_name}.md"
    recipe.write_text(
        f"---\nname: {recipe_name}\nsteps: []\n---\n# {recipe_name}\n",
        encoding="utf-8",
    )
    case = copy.deepcopy(valid_case())
    case["id"] = f"{recipe_name}-case"
    case["prompt_surfaces"] = [f"recipe:{recipe_name}"]
    case_path = pack / "evals/cases" / case["id"] / "case.json"
    case_path.parent.mkdir(parents=True)
    case_path.write_text(canonical(case), encoding="utf-8")
    assets = {kind: [] for kind in ASSET_DIRS}
    assets["recipe"] = [f"recipes/{recipe_name}.md"]
    assets["eval-case"] = [f"evals/cases/{case['id']}/case.json"]
    manifest = {
        "pack_schema_version": 1, "id": pack_id, "version": "1.0.0",
        "kind": "official", "engine": f">={__version__}", "dependencies": [],
        "assets": assets,
        "hashes": {item: digest(pack / item) for paths in assets.values() for item in paths},
        "display_name": pack_id, "description": f"Official {pack_id} capability",
        "capabilities": ["evaluation", "recipe"],
        "entrypoints": [{"id": recipe_name, "kind": "recipe", "target": recipe_name}],
        "references": [], "resources": {},
        "provenance": {"source": "test", "created_at": "2026-08-05T00:00:00+00:00"},
    }
    compatibility = {
        "compatibility_schema_version": 1, "pack_id": pack_id,
        "pack_version": "1.0.0", "engine": f">={__version__}", "platforms": ["any"],
    }
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    (pack / "compatibility.yaml").write_text(canonical(compatibility), encoding="utf-8")
    return pack


def test_design_prefers_installed_official_and_degrades_with_canonical_hint(
    tmp_path, monkeypatch,
):
    from rig_workbench.workbench import capabilities

    official = _asset(tmp_path, "design")
    core = _asset(tmp_path, "design-first", tier="core", pack_id="rig-core")
    records = [_official_record("design", "design")]
    monkeypatch.setattr(capabilities, "_catalog", lambda: records)
    monkeypatch.setattr(capabilities, "_canonical_preferred", lambda *_args: True)
    monkeypatch.setattr(
        capabilities, "resolve_asset",
        lambda _kind, name, **_kwargs: official if name == "design" else core,
    )
    preferred = capabilities.resolve_task_route("design", {}, tmp_path)
    assert (preferred["status"], preferred["recipe"], preferred["pack"]) == (
        "ready", "design", "design",
    )

    monkeypatch.setattr(
        capabilities, "resolve_asset",
        lambda _kind, name, **_kwargs: core if name == "design-first" else None,
    )
    monkeypatch.setattr(capabilities, "_canonical_preferred", lambda *_args: False)
    fallback = capabilities.resolve_task_route("design", {}, tmp_path)
    assert fallback["status"] == "degraded"
    assert fallback["recipe"] == "design-first"
    assert fallback["capability"] == "generic-design"
    assert fallback["hint"] == (
        "Install the canonical capability with `rig-wb pack install official:design`."
    )


def test_test_and_remote_pr_context_fallbacks_are_explicit(tmp_path):
    from rig_workbench.workbench.capabilities import resolve_task_route

    implementation = resolve_task_route(
        "test", {"implementation_type": "bugfix"}, tmp_path,
    )
    assert implementation["recipe"] == "bugfix"
    assert implementation["status"] == "degraded"
    read_only = resolve_task_route("test", {"read_only": True}, tmp_path)
    assert read_only["recipe"] == "review-only"
    assert read_only["reviewers"] == ["test-reviewer"]
    stopped = resolve_task_route("review", {"remote_pr": True}, tmp_path)
    assert stopped["status"] == "stopped" and stopped["recipe"] is None
    local = resolve_task_route(
        "review", {"remote_pr": True, "supplied_diff": True}, tmp_path,
    )
    assert local["status"] == "degraded" and local["recipe"] == "review-only"


def test_pure_selector_context_aliases_have_identical_routes():
    from rig_workbench.workbench.capabilities import LocalRecipe, select_task_route

    available = {
        name: LocalRecipe(name, "core", "rig-core", True)
        for name in ("feature", "review-only")
    }
    explicit = select_task_route(
        "feature", {"explicit_recipe": "feature", "mode": "read-only"}, available,
    )
    assert explicit["recipe"] == "feature" and explicit["worktree"] is False
    remote = select_task_route(
        "review", {"target": "remote-pr", "diff": "diff --git a/x b/x"}, available,
    )
    assert remote["status"] == "degraded" and remote["recipe"] == "review-only"


def test_ordinary_security_route_adds_explicit_reviewer(tmp_path):
    from rig_workbench.workbench.capabilities import resolve_task_route

    route = resolve_task_route("security_review", {}, tmp_path)
    assert route["status"] == "ready"
    assert route["recipe"] == "review-only"
    assert route["reviewers"] == ["security-reviewer"]
    assert route["worktree"] is False


def test_explicit_recipe_preserved_missing_errors_and_shadow_requires_trust(
    tmp_path, monkeypatch,
):
    from rig_workbench.workbench import capabilities

    explicit = capabilities.resolve_task_route(
        "feature", {"recipe": "hotfix"}, tmp_path,
    )
    assert explicit["recipe"] == "hotfix"
    assert explicit["provenance"]["explicit"] is True

    monkeypatch.setattr(capabilities, "resolve_asset", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        capabilities, "_trusted_recipe_catalog",
        lambda _project: (["hotfix", "feature"], []),
    )
    with pytest.raises(capabilities.RouteResolutionError, match="trusted suggestions: hotfix"):
        capabilities.resolve_task_route("feature", {"recipe": "hotfx"}, tmp_path)

    shadow = _asset(tmp_path, "feature", tier="project", pack_id=None)
    monkeypatch.setattr(capabilities, "resolve_asset", lambda *_args, **_kwargs: shadow)
    monkeypatch.setattr(capabilities, "_trusted", lambda _asset: False)
    blocked = capabilities.resolve_task_route("feature", {}, tmp_path)
    assert blocked["status"] == "trust_required"
    assert blocked["worktree"] is False


def test_catalog_failure_is_fail_closed_and_route_is_deterministic_read_only(
    tmp_path, monkeypatch,
):
    from rig_workbench.packs.model import PackError
    from rig_workbench.workbench import capabilities

    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    first = capabilities.resolve_task_route("design", {}, tmp_path)
    second = capabilities.resolve_task_route("design", {}, tmp_path)
    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert first == second
    assert before == after

    monkeypatch.setattr(
        capabilities, "_catalog", lambda: (_ for _ in ()).throw(PackError("bad catalog")),
    )
    with pytest.raises(PackError, match="bad catalog"):
        capabilities.resolve_task_route("design", {}, tmp_path)


def test_cmd_new_omitted_recipe_records_exact_route_before_state(
    tmp_path, monkeypatch,
):
    from rig_workbench.workbench import lifecycle
    from rig_workbench.workbench.state import load_json

    git_repo = _git_repo(tmp_path)
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(lifecycle, "make_task_id", lambda _slug: "route-task")
    args = argparse.Namespace(
        input="add a feature", type="feature", slug=None, base=None, recipe=None,
        reason=None, no_worktree=True, budget_minutes=None, remote_pr=False,
        has_diff=False, read_only=False, implementation_type=None,
    )
    lifecycle.cmd_new(args)
    task = load_json(git_repo / ".rig/runs/route-task/task.json")
    assert task["recipe"] == "feature"
    assert task["route"]["status"] == "ready"
    assert task["route"]["degraded"] is False
    assert task["route"]["capability"] == "feature"
    assert task["route"]["tier"] == "core"
    assert task["route"]["pack"] == "rig-core"
    assert task["route"]["provenance"]["authority"].endswith("select_task_route")


def test_route_cli_json_does_not_create_workbench_state(tmp_path, monkeypatch, capsys):
    from rig_workbench.workbench import cli

    git_repo = _git_repo(tmp_path)
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(
        "sys.argv", ["workbench.py", "route", "--type", "design", "--json"],
    )
    cli.main()
    route = json.loads(capsys.readouterr().out)
    assert route["status"] == "degraded"
    assert not (git_repo / ".rig").exists()


def test_official_install_route_remove_lifecycle(tmp_path, monkeypatch):
    from rig_workbench.packs import catalog
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.remover import remove_pack
    from rig_workbench.workbench.capabilities import resolve_task_route

    distribution = tmp_path / "distribution"
    _official_recipe_pack(distribution / "packs/official", "design", "design")
    monkeypatch.setattr(catalog, "distribution_root", lambda: distribution)
    monkeypatch.setenv("RIG_USER_HOME", str(tmp_path / "user"))
    project = tmp_path / "project"
    project.mkdir()

    assert resolve_task_route("design", {}, project)["status"] == "degraded"
    installed = install_pack(
        "official:design", scope="project", project=project, allow_unverified=True,
    )
    assert installed.path.is_dir()
    route = resolve_task_route("design", {}, project)
    assert (route["status"], route["recipe"], route["pack"]) == (
        "ready", "design", "design",
    )
    _target, removed = remove_pack("design", scope="project", project=project, yes=True)
    assert removed is True
    assert resolve_task_route("design", {}, project)["status"] == "degraded"


def test_malformed_local_manifest_is_deterministic_json_error_without_task_writes(
    tmp_path, monkeypatch, capsys,
):
    from rig_workbench.workbench import cli, lifecycle

    project = _git_repo(tmp_path)
    malformed = project / ".rig/packs/broken"
    malformed.mkdir(parents=True)
    (malformed / "pack.yaml").write_text("{not-json", encoding="utf-8")
    monkeypatch.chdir(project)

    outputs = []
    for _index in range(2):
        monkeypatch.setattr(
            "sys.argv", ["workbench.py", "route", "--type", "feature", "--json"],
        )
        with pytest.raises(SystemExit) as stopped:
            cli.main()
        assert stopped.value.code == 1
        outputs.append(capsys.readouterr().out)
    assert outputs[0] == outputs[1]
    assert json.loads(outputs[0])["status"] == "error"
    assert not (project / ".rig/runs").exists()
    assert not (project / ".gitignore").exists()

    args = argparse.Namespace(
        input="feature", type="feature", slug=None, base=None, recipe=None,
        reason=None, no_worktree=True, budget_minutes=None, remote_pr=False,
        has_diff=False, diff=None, read_only=False, implementation_type=None,
    )
    with pytest.raises(SystemExit):
        lifecycle.cmd_new(args)
    assert not (project / ".rig/runs").exists()
    assert not (project / ".gitignore").exists()
