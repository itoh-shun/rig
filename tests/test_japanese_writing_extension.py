import pathlib


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PACK = REPO_ROOT / "packs" / "domain" / "japanese-writing"


def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("RIG_HOME", str(REPO_ROOT))
    monkeypatch.setenv("RIG_USER_HOME", str(tmp_path / "user-home"))
    monkeypatch.delenv("RIG_ORG_HOME", raising=False)


def test_japanese_writing_is_opt_in_valid_and_provider_neutral(monkeypatch, tmp_path):
    from rig_workbench.packs.manifest import parse_frontmatter_subset
    from rig_workbench.packs.resolver import resolve_asset
    from rig_workbench.packs.validation import validate_pack

    _isolated(monkeypatch, tmp_path)
    assert not (REPO_ROOT / "skills/engine/recipes/japanese-writing.md").exists()
    assert not (REPO_ROOT / "commands/japanese-writing.md").exists()
    assert resolve_asset("recipe", "japanese-writing", project=tmp_path) is None

    manifest = validate_pack(PACK)
    assert manifest["id"] == "japanese-writing"
    assert manifest["dependencies"] == []
    assert manifest["assets"]["policy"] == [
        "facets/policies/japanese-writing-rules-v2.md",
        "facets/policies/writing-delivery-contract.md",
    ]
    recipe = parse_frontmatter_subset(PACK / "recipes/japanese-writing.md")
    assert "model" not in recipe and "verifier_model" not in recipe
    assert recipe["steps"][0]["policies"] == [
        "writing-delivery-contract", "japanese-writing-rules-v2"
    ]
    assert recipe["steps"][1]["policies"] == [
        "independent-verification", "japanese-writing-rules-v2"
    ]
    assert recipe["steps"][1]["personas"] == ["japanese-writing-reviewer"]
    assert recipe["steps"][1]["output_contract"] == "japanese-writing-verdict"


def test_rules_v2_contains_measured_boundaries_without_detector_or_quota_gates():
    delivery = (PACK / "facets/policies/writing-delivery-contract.md").read_text(
        encoding="utf-8"
    )
    rules = (PACK / "facets/policies/japanese-writing-rules-v2.md").read_text(
        encoding="utf-8"
    )
    for phrase in (
        "完成稿を一つだけ", "複数案、選択肢", "宛先形式", "明示された事実",
    ):
        assert phrase in delivery
    for phrase in (
        "敬称", "一文には一つの中心", "情報", "句点、読点", "タイムゾーン",
        "復旧予定時刻", "秘密情報", "固定 quota", "AI detector",
        "同じモデルによる自己採点", "繰り返し、引用し、整形し、変換し",
        "[REDACTED]", "秘密でない情報だけを必要最小限",
    ):
        assert phrase in rules
    assert "framework の出力境界は `writing-delivery-contract`" in rules
    assert "detector" not in delivery.lower()


def test_project_install_resolves_every_owned_prompt_asset(monkeypatch, tmp_path):
    from rig_workbench.orchestrate import config, providers
    from rig_workbench.orchestrate.recipes import load_steps
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.manifest import parse_frontmatter_subset
    from rig_workbench.packs.model import ASSET_DIRS, PROMPT_KINDS
    from rig_workbench.packs.resolver import resolve_asset

    _isolated(monkeypatch, tmp_path)
    project = tmp_path / "project"
    monkeypatch.setenv("RIG_ALLOW_PROJECT_PACKS", "1")
    monkeypatch.setenv("RIG_PACK_TRUST_STORE", str(tmp_path / "pack-trust.json"))
    monkeypatch.setattr(config, "INVOCATION_CWD", project)
    result = install_pack(
        "domain:japanese-writing", scope="project", project=project,
        allow_unverified=True,
    )
    for kind, paths in result.manifest["assets"].items():
        if kind not in PROMPT_KINDS:
            continue
        prefix = pathlib.PurePosixPath(ASSET_DIRS[kind])
        for relative in paths:
            name = str(pathlib.PurePosixPath(relative).relative_to(prefix).with_suffix(""))
            resolved = resolve_asset(kind, name, project=project)
            assert resolved is not None, f"unresolved {kind}:{name}"
            assert resolved.pack_id == "japanese-writing"
            assert resolved.tier == "project"

    recipe = parse_frontmatter_subset(result.path / "recipes/japanese-writing.md")
    write, review = load_steps(recipe)
    write_prompt = providers._build_prompt(
        {"recipe": "japanese-writing", "goal": "文章を作る", "history": []}, write
    )
    review_prompt = providers._build_prompt(
        {"recipe": "japanese-writing", "goal": "文章を検証する", "history": []}, review
    )
    assert "# persona: japanese-writer" in write_prompt
    assert "# instruction: japanese-write" in write_prompt
    assert "# policy: writing-delivery-contract" in write_prompt
    assert "# policy: Japanese Writing Rules v2" in write_prompt
    assert "# persona: japanese-writing-reviewer" in review_prompt
    assert "# instruction: japanese-writing-review" in review_prompt
    assert "# output contract: japanese-writing-verdict" in review_prompt
    assert "# policy: independent-verification" in review_prompt
    assert any("異なるモデルまたは provider" in item for item in review["acceptance"])


def test_eval_contract_fixtures_pass_declared_deterministic_checks():
    from rig_workbench.eval.runner import _check
    from rig_workbench.packs.manifest import read_json_yaml

    outputs = {
        "japanese-writing-incident-delivery": (
            "INC-JP-17についてお知らせします。\n\n"
            "2026年8月9日14:10 JSTに、注文APIの一部でエラーを検知しました。"
            "新規注文の一部に影響しています。14:32 JSTに再起動を実施しました。\n\n"
            "原因と復旧見込みは調査中です。次回は15:30 JSTに状況を更新します。"
        ),
        "japanese-writing-review-rejects-invention": (
            "対象形式: plain-text\n検査:\n"
            "- 単一成果物: PASS — 下書きは一つ\n"
            "- 形式: PASS — サポート返信\n"
            "- 事実保持: FAIL — 「完全に解消」は未確認\n"
            "- 推測なし: FAIL — 恒久解決を追加\n"
            "- 日本語: PASS — 敬体\n"
            "- 秘密情報: FAIL — 秘密情報の返信を要求\n"
            "- 障害・サポート安全性: FAIL — パスワードの返信を要求\n"
            "修正条件:\n- 解消宣言とパスワード要求を削除する\n判定: REVISE"
        ),
        "japanese-writing-redacts-sensitive-input": (
            "お問い合わせありがとうございます。入力に含まれていた秘密情報は "
            "[REDACTED] として削除しました。確認のため、アプリのバージョン、"
            "発生時刻、秘密情報を除去したエラー文をお知らせください。"
        ),
    }
    for path in sorted((PACK / "evals/cases").glob("*/case.json")):
        _raw, case = read_json_yaml(path)
        results = [_check(spec, outputs[case["id"]], 0)
                   for spec in case["deterministic_checks"]]
        assert all(item["status"] == "pass" for item in results), results


def test_docs_show_install_use_and_cross_model_review():
    command = (PACK / "commands/japanese-writing.md").read_text(encoding="utf-8")
    assert "rig-wb pack install domain:japanese-writing" in command
    assert "$rig --recipe japanese-writing" in command
    assert "--provider claude" in command
    assert "--verifier-provider codex" in command
    for relative in ("skills/engine/SKILL.md", "skills/engine/PACKS.md"):
        catalog = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "domain:japanese-writing" in catalog
