import pathlib
import shutil

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SNS_X_PACK = REPO_ROOT / "packs" / "domain" / "sns-x"


def _isolated_resolution(monkeypatch, tmp_path):
    monkeypatch.setenv("RIG_HOME", str(REPO_ROOT))
    monkeypatch.setenv("RIG_USER_HOME", str(tmp_path / "user-home"))
    monkeypatch.delenv("RIG_ORG_HOME", raising=False)


def test_sns_x_is_absent_from_core_and_pack_is_valid(monkeypatch, tmp_path):
    from rig_workbench.packs.resolver import resolve_asset
    from rig_workbench.packs.validation import validate_pack
    from rig_workbench.packs.model import ASSET_DIRS

    _isolated_resolution(monkeypatch, tmp_path)
    assert not (REPO_ROOT / "skills/rig/recipes/sns-x-post.md").exists()
    assert not (REPO_ROOT / "skills/rig/facets/personas/sns-post-reviewer.md").exists()
    assert resolve_asset("recipe", "sns-x-post", project=tmp_path) is None

    manifest = validate_pack(SNS_X_PACK)
    assert set(manifest["assets"]) == set(ASSET_DIRS)
    assert "web" not in manifest["assets"]
    assert manifest["assets"]["recipe"] == ["recipes/sns-x-post.md"]
    assert manifest["assets"]["eval-case"] == [
        "evals/cases/sns-x-structure/case.json"
    ]


def test_sns_x_project_install_resolve_and_remove(monkeypatch, tmp_path):
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.lock import read_lock
    from rig_workbench.packs.remover import remove_pack
    from rig_workbench.packs.resolver import resolve_asset

    _isolated_resolution(monkeypatch, tmp_path)
    project = tmp_path / "project"
    result = install_pack("domain:sns-x", scope="project", project=project,
                          allow_unverified=True)

    assert result.verification_status == "unverified"
    lock_entry = read_lock(project / ".rig/packs")["packs"][0]
    assert lock_entry["verification_status"] == "unverified"
    assert lock_entry["source"]["path"] == "domain:sns-x"
    resolved = resolve_asset("recipe", "sns-x-post", project=project)
    assert resolved is not None
    assert resolved.pack_id == "sns-x" and resolved.tier == "project"
    assert resolve_asset("persona", "sns-post-reviewer", project=project) is not None

    _target, removed = remove_pack("sns-x", scope="project", project=project, yes=True)
    assert removed is True
    assert resolve_asset("recipe", "sns-x-post", project=project) is None


@pytest.mark.parametrize("source", ["domain:../sns-x", "domain:sns-x/extra", "domain:absent"])
def test_builtin_domain_alias_rejects_traversal_and_unknown_ids(source, tmp_path):
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.model import PackError

    with pytest.raises(PackError, match="built-in domain pack"):
        install_pack(source, scope="project", project=tmp_path, allow_unverified=True)
    assert not (tmp_path / ".rig/packs/sns-x").exists()


def test_pack_may_reference_real_core_assets_but_not_unknown_assets(tmp_path, monkeypatch):
    from rig_workbench.packs.manifest import canonical, digest, read_json_yaml
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.validation import validate_pack

    _isolated_resolution(monkeypatch, tmp_path)
    copied = tmp_path / "sns-x"
    shutil.copytree(SNS_X_PACK, copied)
    recipe = copied / "recipes/sns-x-post.md"
    recipe.write_text(
        recipe.read_text(encoding="utf-8").replace("pattern: serial", "pattern: absent-pattern"),
        encoding="utf-8",
    )
    _raw, manifest = read_json_yaml(copied / "pack.yaml")
    manifest["hashes"]["recipes/sns-x-post.md"] = digest(recipe)
    (copied / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")

    with pytest.raises(PackError, match="broken pack reference: pattern:absent-pattern"):
        validate_pack(copied)


def test_core_scenario_uses_medium_neutral_content_risk_reviewer():
    paths = [
        REPO_ROOT / "skills/rig/recipes/scenario.md",
        REPO_ROOT / "skills/rig/facets/instructions/scenario-vet.md",
        REPO_ROOT / "commands/scenario.md",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert "content-risk-reviewer" in combined
    assert "sns-post-reviewer" not in combined
    assert (REPO_ROOT / "skills/rig/facets/personas/content-risk-reviewer.md").is_file()
