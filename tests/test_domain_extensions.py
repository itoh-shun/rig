import hashlib
import pathlib
import shutil

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SNS_X_PACK = REPO_ROOT / "packs" / "domain" / "sns-x"
SALES_PACK = REPO_ROOT / "packs" / "domain" / "sales"


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


def test_sales_is_absent_from_core_and_pack_owns_both_workflows(monkeypatch, tmp_path):
    from rig_workbench.packs.manifest import read_json_yaml
    from rig_workbench.packs.resolver import resolve_asset
    from rig_workbench.packs.validation import validate_pack

    _isolated_resolution(monkeypatch, tmp_path)
    for relative in (
        "commands/sales.md",
        "skills/rig/recipes/deal-review.md",
        "skills/rig/recipes/sales-enablement.md",
        "skills/rig/facets/personas/sales/hearing-reviewer.md",
        "skills/rig/templates/deal-record.md",
    ):
        assert not (REPO_ROOT / relative).exists()
    assert resolve_asset("recipe", "deal-review", project=tmp_path) is None
    assert resolve_asset("recipe", "sales-enablement", project=tmp_path) is None
    assert resolve_asset("command", "sales", project=tmp_path) is None

    manifest = validate_pack(SALES_PACK)
    assert manifest["assets"]["recipe"] == [
        "recipes/deal-review.md", "recipes/sales-enablement.md"
    ]
    assert "facets/personas/sales/objection-handler.md" not in manifest["assets"]["persona"]
    surfaces = []
    for relative in manifest["assets"]["eval-case"]:
        _raw, case = read_json_yaml(SALES_PACK / relative)
        surfaces.append(case["prompt_surfaces"])
    assert surfaces == [
        ["command:sales", "recipe:deal-review"],
        ["command:sales", "recipe:sales-enablement"],
    ]
    command = (SALES_PACK / "commands/sales.md").read_text(encoding="utf-8")
    assert "自動登録されるものではありません" in command
    assert "$rig --recipe deal-review" in command
    assert "RIG_ALLOW_PROJECT_PACKS=1" in command
    for relative in ("skills/rig/SKILL.md", "skills/rig/PACKS.md", "README.md", "README.ja.md"):
        guidance = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "RIG_ALLOW_PROJECT_PACKS=1" in guidance
        assert "$rig --recipe <installed-name>" in guidance


def test_active_core_has_no_legacy_sales_workflow_references():
    files = [REPO_ROOT / "README.md", REPO_ROOT / "README.ja.md"]
    for directory in (REPO_ROOT / "commands", REPO_ROOT / "skills/rig", REPO_ROOT / "web"):
        files.extend(path for path in directory.rglob("*") if path.suffix in {".md", ".html"})
    text = "\n".join(path.read_text(encoding="utf-8") for path in files)
    for legacy in (
        "/rig:sales", "deal-review", "sales-enablement", "objection-handler",
        "sales/hearing-reviewer", "sales-domain", "sales-collateral", "deal-verdict",
    ):
        assert legacy not in text


def test_sales_project_install_resolves_every_owned_prompt_and_removes(monkeypatch, tmp_path):
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.lock import read_lock
    from rig_workbench.packs.model import ASSET_DIRS, PROMPT_KINDS
    from rig_workbench.packs.remover import remove_pack
    from rig_workbench.packs.resolver import resolve_asset

    _isolated_resolution(monkeypatch, tmp_path)
    project = tmp_path / "project"
    result = install_pack("domain:sales", scope="project", project=project,
                          allow_unverified=True)
    assert result.verification_status == "unverified"
    lock_entry = read_lock(project / ".rig/packs")["packs"][0]
    assert lock_entry["source"]["path"] == "domain:sales"
    assert lock_entry["verification_status"] == "unverified"

    for kind, paths in result.manifest["assets"].items():
        if kind not in PROMPT_KINDS:
            continue
        prefix = pathlib.PurePosixPath(ASSET_DIRS[kind])
        for relative in paths:
            name = str(pathlib.PurePosixPath(relative).relative_to(prefix).with_suffix(""))
            resolved = resolve_asset(kind, name, project=project)
            assert resolved is not None, f"unresolved {kind}:{name}"
            assert resolved.pack_id == "sales" and resolved.tier == "project"

    _target, removed = remove_pack("sales", scope="project", project=project, yes=True)
    assert removed is True
    assert resolve_asset("recipe", "deal-review", project=project) is None
    assert resolve_asset("command", "sales", project=project) is None


def test_sales_markdown_contract_examples_pass_the_declared_deterministic_checks():
    from rig_workbench.eval.cases import canonical_json
    from rig_workbench.eval.runner import _check
    from rig_workbench.packs.manifest import digest
    from rig_workbench.packs.manifest import read_json_yaml

    outputs = {
        "deal-review-structure": """## 商談レビュー結果
総合評価: B
| 観点 | 判定 | ひとことで |
|---|---|---|
| ヒアリング | ○ | 課題と工数を確認済み |
| ニーズ把握 | ○ | 優先課題が明確 |
| 提案 | △ | 実績の根拠が不足 |
| クロージング | ○ | 決裁者と期限を確認済み |
| ネクストアクション | ◎ | 担当と期日が明確 |
### 次回の具体アクション（優先順）
1. 営業が8月8日までに比較表を送る
### 情報不足（記録に足りず評価できなかった点）
- 導入効果の実績値
""",
        "sales-enablement-structure": """# 営業資料: ReleaseGuard
## ヘッドライン
変更を検証してから反映する
## こんな課題ありませんか（ターゲットの痛み・3点）
- 未検証の変更が混ざる
| 機能（実在） | だから何が嬉しいか（ベネフィット） | 出所 |
|---|---|---|
| 隔離worktree | 元の作業を汚さず検証できる | README |
## 次の一歩（CTA）
- [要記入: デモ窓口]
# 荷電スクリプト: ReleaseGuard
## 1. オープニング（〜15秒）
「変更の検証手順について伺います」
## 5. 反論処理（よくある反論 → 切り返し）
| 反論 | 切り返し（実プロダクトの強みで） |
|---|---|
| 今は不要 | 隔離検証の手順だけご紹介します |
## 6. クロージング（next action）
「[要記入: 候補日]に15分いただけますか」
""",
    }
    for case_id, output in outputs.items():
        _raw, case = read_json_yaml(
            SALES_PACK / "evals/cases" / case_id / "case.json"
        )
        assert not any(spec.startswith("schema:") for spec in case["deterministic_checks"])
        results = [_check(spec, output, 0) for spec in case["deterministic_checks"]]
        assert all(result["status"] == "pass" for result in results), results
        assert case["provenance"]["source_commit"] == "656895154ca55ed49e6f9c18851db1d716108b9b"
        assert case["provenance"]["source_hashes"]["task.json"] == hashlib.sha256(
            canonical_json(case["target_inputs"]).encode()
        ).hexdigest()
    assert read_json_yaml(
        SALES_PACK / "evals/cases/deal-review-structure/case.json"
    )[1]["provenance"]["source_hashes"]["final.md"] == digest(
        SALES_PACK / "facets/output-contracts/deal-verdict.md"
    )
    assert read_json_yaml(
        SALES_PACK / "evals/cases/sales-enablement-structure/case.json"
    )[1]["provenance"]["source_hashes"]["final.md"] == digest(
        SALES_PACK / "facets/output-contracts/sales-collateral.md"
    )


@pytest.mark.parametrize("source", [
    "domain:../sns-x", "domain:sns-x/extra", "domain:../sales",
    "domain:sales/extra", "domain:absent",
])
def test_builtin_domain_alias_rejects_traversal_and_unknown_ids(source, tmp_path):
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.model import PackError

    with pytest.raises(PackError, match="built-in domain pack"):
        install_pack(source, scope="project", project=tmp_path, allow_unverified=True)
    assert not (tmp_path / ".rig/packs/sns-x").exists()
    assert not (tmp_path / ".rig/packs/sales").exists()


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


def test_pack_gate_reference_is_validated_as_a_core_pattern(tmp_path, monkeypatch):
    from rig_workbench.packs.manifest import canonical, digest, read_json_yaml
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.validation import validate_pack

    _isolated_resolution(monkeypatch, tmp_path)
    copied = tmp_path / "sales"
    shutil.copytree(SALES_PACK, copied)
    recipe = copied / "recipes/deal-review.md"
    recipe.write_text(
        recipe.read_text(encoding="utf-8").replace(
            "gate: acceptance-gate", "gate: absent-gate"
        ), encoding="utf-8",
    )
    _raw, manifest = read_json_yaml(copied / "pack.yaml")
    manifest["hashes"]["recipes/deal-review.md"] = digest(recipe)
    (copied / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")

    with pytest.raises(PackError, match="broken pack reference: pattern:absent-gate"):
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
