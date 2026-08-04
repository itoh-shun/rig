import hashlib
import pathlib
import shutil

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SNS_X_PACK = REPO_ROOT / "packs" / "domain" / "sns-x"
SALES_PACK = REPO_ROOT / "packs" / "domain" / "sales"
VIDEO_PACK = REPO_ROOT / "packs" / "domain" / "video-storytelling"


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
    "domain:sales/extra", "domain:../video-storytelling",
    "domain:video-storytelling/extra", "domain:absent",
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


def test_video_storytelling_is_absent_from_core_and_pack_is_self_contained(
        monkeypatch, tmp_path):
    from rig_workbench.eval.cases import canonical_json
    from rig_workbench.eval.runner import _check
    from rig_workbench.packs.manifest import digest
    from rig_workbench.packs.manifest import read_json_yaml
    from rig_workbench.packs.resolver import resolve_asset
    from rig_workbench.packs.validation import validate_pack

    _isolated_resolution(monkeypatch, tmp_path)
    for relative in (
        "commands/movie.md", "commands/scenario.md",
        "skills/rig/recipes/movie.md", "skills/rig/recipes/release-movie.md",
        "skills/rig/recipes/scenario.md",
        "skills/rig/facets/personas/engagement-reviewer.md",
    ):
        assert not (REPO_ROOT / relative).exists()
    assert resolve_asset("recipe", "movie", project=tmp_path) is None
    assert resolve_asset("recipe", "scenario", project=tmp_path) is None

    manifest = validate_pack(VIDEO_PACK)
    assert manifest["dependencies"] == []
    assert manifest["assets"]["recipe"] == [
        "recipes/movie.md", "recipes/release-movie.md", "recipes/scenario.md"
    ]
    pack_text = "\n".join(
        (VIDEO_PACK / relative).read_text(encoding="utf-8")
        for kind, paths in manifest["assets"].items()
        for relative in paths if kind not in {"eval-case", "eval-result"}
    )
    for hidden_core_ref in ("ai-smell-reviewer", "ai-writing-smells",
                            "content-risk-reviewer", "review-verdict"):
        assert hidden_core_ref not in pack_text
    final_sources = {
        "movie-storyboard-grounding": "facets/instructions/video-direct.md",
        "release-movie-changelog-grounding": "facets/instructions/release-movie.md",
        "scenario-draft-grounding": "facets/instructions/scenario-write.md",
        "scenario-vet-rejects-invention": "facets/output-contracts/scenario-verdict.md",
    }
    sample_outputs = {
        "movie-storyboard-grounding": (
            "ログライン: JSONログを絞り込みCSVへ出す\n### シーン表\n"
            "| 1 | 4s | screen |\n### ソース対応表\nREADME → CSV出力"
        ),
        "release-movie-changelog-grounding": (
            "## リリースムービー台本: LogTool 1.2\n### シーン表\n"
            "| 1 | 4s | screen |\n### CTA\n更新する\n### ソース対応表\nCHANGELOG"
        ),
        "scenario-draft-grounding": (
            "## シナリオ: ログ確認\nログライン: 絞って出す\n感情の弧: 困る→解決\n"
            "### ビートシート\n| # | source（実機能） |\n### 目玉（1つ）: CSV出力\n### CTA: 試す"
        ),
        "scenario-vet-rejects-invention": (
            "根拠:\n- 冒頭: フックがない\n- 数値: 80%は未確認\n"
            "修正条件:\n- CTAを1つの行動にする\n判定: REJECT\n確信度: high"
        ),
    }
    for relative in manifest["assets"]["eval-case"]:
        _raw, case = read_json_yaml(VIDEO_PACK / relative)
        assert case["status"] == "approved"
        assert case["repeat"] == 3
        assert case["provenance"]["source_commit"] == (
            "b86b3a2e8ddc6c3ad79e1c3a68ffb45d8b0b0d71"
        )
        assert case["provenance"]["source_hashes"]["task.json"] == hashlib.sha256(
            canonical_json(case["target_inputs"]).encode()
        ).hexdigest()
        assert case["provenance"]["source_hashes"]["final.md"] == digest(
            VIDEO_PACK / final_sources[case["id"]]
        )
        checks = [_check(spec, sample_outputs[case["id"]], 0)
                  for spec in case["deterministic_checks"]]
        assert all(item["status"] == "pass" for item in checks), checks


def test_video_storytelling_project_install_resolves_extends_and_removes(
        monkeypatch, tmp_path):
    from rig_workbench.orchestrate import config as orchestrate_config
    from rig_workbench.orchestrate.recipes import resolve_plan_json
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.lock import read_lock
    from rig_workbench.packs.model import ASSET_DIRS, PROMPT_KINDS
    from rig_workbench.packs.remover import remove_pack
    from rig_workbench.packs.resolver import resolve_asset

    _isolated_resolution(monkeypatch, tmp_path)
    project = tmp_path / "project"
    monkeypatch.setattr(orchestrate_config, "INVOCATION_CWD", project)
    monkeypatch.setattr(orchestrate_config, "PROJECT_RECIPES", project / ".rig/recipes")
    monkeypatch.setenv("RIG_ALLOW_PROJECT_PACKS", "1")
    monkeypatch.setenv("RIG_PACK_TRUST_STORE", str(tmp_path / "pack-trust.json"))
    result = install_pack("domain:video-storytelling", scope="project",
                          project=project, allow_unverified=True)
    assert result.verification_status == "unverified"
    entry = read_lock(project / ".rig/packs")["packs"][0]
    assert entry["source"]["path"] == "domain:video-storytelling"

    for kind, paths in result.manifest["assets"].items():
        if kind not in PROMPT_KINDS:
            continue
        prefix = pathlib.PurePosixPath(ASSET_DIRS[kind])
        for relative in paths:
            name = str(pathlib.PurePosixPath(relative).relative_to(prefix).with_suffix(""))
            resolved = resolve_asset(kind, name, project=project)
            assert resolved is not None, f"unresolved {kind}:{name}"
            assert resolved.pack_id == "video-storytelling"

    release = resolve_asset("recipe", "release-movie", project=project)
    movie = resolve_asset("recipe", "movie", project=project)
    assert release is not None and movie is not None
    plan = resolve_plan_json(release.path)
    assert plan["extends"] == "movie"
    assert plan["n_steps"] == 2
    assert plan["warnings"] == []
    assert [step["id"] for step in plan["steps"]] == ["storyboard", "render"]
    assert all(step["origin"] == "override" for step in plan["steps"])

    _target, removed = remove_pack(
        "video-storytelling", scope="project", project=project, yes=True
    )
    assert removed is True
    assert resolve_asset("recipe", "movie", project=project) is None
    assert resolve_asset("command", "scenario", project=project) is None


def test_active_core_has_no_legacy_video_workflow_references():
    files = [REPO_ROOT / "README.md", REPO_ROOT / "README.ja.md"]
    for directory in (REPO_ROOT / "commands", REPO_ROOT / "skills/rig", REPO_ROOT / "web"):
        files.extend(path for path in directory.rglob("*")
                     if path.suffix in {".md", ".html"})
    text = "\n".join(path.read_text(encoding="utf-8") for path in files)
    for legacy in (
        "/rig:movie", "/rig:scenario", "recipes/movie", "recipes/release-movie",
        "recipes/scenario", "facets/personas/video-director",
        "facets/personas/release-director", "facets/personas/scenario-writer",
        "facets/personas/engagement-reviewer", "facets/instructions/render-hyperframes",
        "facets/knowledge/video-grammar",
    ):
        assert legacy not in text
