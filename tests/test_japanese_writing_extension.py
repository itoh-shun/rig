import pathlib


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PACK = REPO_ROOT / "packs" / "domain" / "japanese-writing"


def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("RIG_HOME", str(REPO_ROOT))
    monkeypatch.setenv("RIG_USER_HOME", str(tmp_path / "user-home"))
    monkeypatch.delenv("RIG_ORG_HOME", raising=False)


def test_japanese_writing_is_opt_in_valid_and_provider_neutral(monkeypatch, tmp_path):
    from rig_workbench.packs.manifest import parse_frontmatter_subset, read_json_yaml
    from rig_workbench.packs.resolver import resolve_asset
    from rig_workbench.packs.validation import validate_pack

    _isolated(monkeypatch, tmp_path)
    assert not (REPO_ROOT / "skills/engine/recipes/japanese-writing.md").exists()
    assert not (REPO_ROOT / "commands/japanese-writing.md").exists()
    assert resolve_asset("recipe", "japanese-writing", project=tmp_path) is None

    manifest = validate_pack(PACK)
    assert manifest["id"] == "japanese-writing"
    assert manifest["version"] == "0.5.0"
    _raw, compatibility = read_json_yaml(PACK / "compatibility.yaml")
    assert compatibility["pack_version"] == "0.5.0"
    assert compatibility["engine"] == ">=2.3.0"
    assert manifest["dependencies"] == []
    assert manifest["assets"]["policy"] == [
        "facets/policies/japanese-writing-rules-v2.md",
        "facets/policies/secure-provider-execution.md",
        "facets/policies/writing-delivery-contract.md",
    ]
    assert (
        "evals/cases/japanese-writing-meaningful-negation-contrast/case.json"
        in manifest["assets"]["eval-case"]
    )
    recipe = parse_frontmatter_subset(PACK / "recipes/japanese-writing.md")
    assert "model" not in recipe and "verifier_model" not in recipe
    assert recipe["steps"][0]["policies"] == [
        "writing-delivery-contract", "japanese-writing-rules-v2"
    ]
    assert recipe["steps"][1]["policies"] == [
        "independent-verification", "secure-provider-execution", "japanese-writing-rules-v2"
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
    for phrase in ("完成稿を一つだけ", "複数案、選択肢", "宛先形式"):
        assert phrase in delivery
    for phrase in (
        "固有名詞", "敬称", "一文には一つの中心", "情報", "句点、読点", "タイムゾーン",
        "復旧予定時刻", "秘密情報", "固定 quota", "AI detector",
        "同じモデルによる自己採点", "繰り返し、引用し、整形し、変換し",
        "[REDACTED]", "秘密でない情報だけを必要最小限",
    ):
        assert phrase in rules
    assert "framework の出力境界は `writing-delivery-contract`" in rules
    assert "detector" not in delivery.lower()


def test_delivery_eval_rejects_reader_visible_workflow_state():
    from rig_workbench.packs.manifest import read_json_yaml

    path = PACK / "evals/cases/japanese-writing-no-workflow-meta/case.json"
    _raw, case = read_json_yaml(path)
    checks = set(case["deterministic_checks"])
    for phrase in ("レビュー", "合格", "完成稿", "生成過程"):
        assert f"not_contains:{phrase}" in checks


def test_terminal_boundary_eval_rejects_wrappers_separators_and_adjustment_offers():
    from rig_workbench.eval.runner import _check
    from rig_workbench.packs.manifest import read_json_yaml

    path = PACK / "evals/cases/japanese-writing-no-workflow-meta/case.json"
    _raw, case = read_json_yaml(path)
    checks = set(case["deterministic_checks"])
    for spec in (
        "regex:^OPS-JP-META-1.*リリース手順の確認です。$",
        "not_contains:---",
        "not_contains:執筆方針",
        "not_contains:調整できます",
    ):
        assert spec in checks
    output = (
        "OPS-JP-META-1について、2026年8月12日14:00に会議室Bで運用会議を開きます。"
        "議題はリリース手順の確認です。"
    )
    assert all(_check(spec, output, 0)["status"] == "pass"
               for spec in case["deterministic_checks"])


def test_ambiguity_eval_keeps_only_facts_common_to_plausible_readings():
    from rig_workbench.eval.runner import _check
    from rig_workbench.packs.manifest import read_json_yaml

    path = PACK / "evals/cases/japanese-writing-ambiguity/case.json"
    _raw, case = read_json_yaml(path)
    checks = set(case["deterministic_checks"])
    for phrase in ("佐藤さん", "高橋さん"):
        assert f"not_contains:{phrase}" in checks
    output = "会議後に共有する旨が伝えられました。"
    assert all(_check(spec, output, 0)["status"] == "pass"
               for spec in case["deterministic_checks"])


def test_internal_register_eval_rejects_customer_support_politeness():
    from rig_workbench.eval.runner import _check
    from rig_workbench.packs.manifest import read_json_yaml

    path = PACK / "evals/cases/japanese-writing-internal-register/case.json"
    _raw, case = read_json_yaml(path)
    checks = set(case["deterministic_checks"])
    assert "not_contains:お待ちいただけますか" in checks
    assert "not_contains:少々お待ちください" in checks
    output = "まだ確認できていません。20分後に確認し、確認後にコメントします。"
    assert all(_check(spec, output, 0)["status"] == "pass"
               for spec in case["deterministic_checks"])


def test_technical_explanation_eval_answers_directly_without_formula_sections():
    from rig_workbench.eval.runner import _check
    from rig_workbench.packs.manifest import read_json_yaml

    path = PACK / "evals/cases/japanese-writing-technical-operation/case.json"
    _raw, case = read_json_yaml(path)
    checks = set(case["deterministic_checks"])
    for spec in (
        "regex:^write-throughは.*同時に保存先へ同期.*書き込みが遅く.*"
        "write-backは.*後で保存先へ反映.*反映前の障害.*失うリスク.*$",
        "not_contains:仕組み:",
        "not_contains:挙動:",
        "not_contains:判断基準:",
        "not_contains:代償:",
        "not_contains:メリット:",
        "not_contains:デメリット:",
    ):
        assert spec in checks
    rubric = case["semantic_rubric"][0]["description"]
    assert "condition-to-result mapping" in rubric
    assert "decision-relevant conflicts, waits, and post-failure behavior" in rubric
    output = (
        "write-throughはキャッシュへの書き込みと同時に保存先へ同期するため、"
        "整合性を保ちやすい一方で書き込みが遅くなります。write-backは先にキャッシュへ"
        "書き込み、後で保存先へ反映するため高速ですが、反映前の障害で失うリスクがあります。"
    )
    assert all(_check(spec, output, 0)["status"] == "pass"
               for spec in case["deterministic_checks"])


def test_support_eval_requires_no_file_no_rows_and_masking():
    from rig_workbench.eval.runner import _check
    from rig_workbench.packs.manifest import read_json_yaml

    path = PACK / "evals/cases/japanese-writing-support-data-minimization/case.json"
    _raw, case = read_json_yaml(path)
    checks = set(case["deterministic_checks"])
    assert "contains:だけ" in checks
    for spec in (
        "contains:スクリーンショット",
        "contains:エラー文",
        "contains:テキスト",
        "regex:^CSVファイル本体.*データ行.*ヘッダーまたは列名.*氏名.*メールアドレス.*マスク.*"
        "スクリーンショット.*送らない.*エラー文.*テキスト.*$",
    ):
        assert spec in checks
    output = (
        "CSVファイル本体は送らないでください。データ行も送らないでください。"
        "ヘッダーまたは列名だけを共有し、氏名とメールアドレスはマスクしてください。"
        "スクリーンショットは送らないでください。秘密情報と不要な識別情報を"
        "除いたエラー文をテキストで共有してください。"
    )
    assert all(_check(spec, output, 0)["status"] == "pass"
               for spec in case["deterministic_checks"])


def test_writer_and_delivery_keep_internal_workflow_state_outside_output():
    writer = (PACK / "facets/personas/japanese-writer.md").read_text(encoding="utf-8")
    delivery = (PACK / "facets/policies/writing-delivery-contract.md").read_text(
        encoding="utf-8"
    )

    assert "reviewer、policy、合否、修正履歴、作成手順は内部実行情報" in writer
    assert "想定読者への完成稿に含めません" in writer
    assert "reviewer への受け渡しと判定は runtime が出力の外で処理" in delivery
    for phrase in ("検証済み", "合格", "修正済み", "適用 policy", "生成過程"):
        assert phrase in delivery
    assert "事実保持と言い換えの規則は persona と内容 policy に委ねます" in delivery


def test_japanese_write_starts_and_stops_at_the_reader_facing_artifact():
    instruction = (PACK / "facets/instructions/japanese-write.md").read_text(
        encoding="utf-8"
    )

    for phrase in (
        "想定読者が最初に読む完成稿の本文から始め",
        "完成稿の最後の文で終えます",
        "生成過程や適用 policy の説明",
        "本文と補足を分ける区切り線",
        "追加調整の申し出",
    ):
        assert phrase in instruction


def test_rules_v2_4_avoids_only_same_proposition_repetition():
    rules = (PACK / "facets/policies/japanese-writing-rules-v2.md").read_text(
        encoding="utf-8"
    )

    assert "# policy: Japanese Writing Rules v2.4" in rules
    assert "依頼や謝罪の強さも媒体と読み手との関係に合わせます" in rules
    assert "顧客対応の依頼敬語や定型挨拶に引き上げません" in rules
    assert "最終成果物の中で同じ命題を言い換えて繰り返しません" in rules
    for meaning_change in ("否定の有無", "対比", "因果の帰属", "時点・状態の違い"):
        assert meaning_change in rules
    assert "重複として省略しません" in rules
    recipe = (PACK / "recipes/japanese-writing.md").read_text(encoding="utf-8")
    assert "Rules v2.4" in recipe
    assert "Rules v2 " not in recipe


def test_meaningful_negation_contrast_and_time_state_are_not_deduplicated():
    from rig_workbench.eval.runner import _check
    from rig_workbench.packs.manifest import read_json_yaml

    path = PACK / "evals/cases/japanese-writing-meaningful-negation-contrast/case.json"
    _raw, case = read_json_yaml(path)
    checks = set(case["deterministic_checks"])
    for spec in (
        "contains:解消していません",
        "contains:一方",
        "contains:暫定回避策",
        "contains:利用できます",
        "contains:恒久対応",
        "contains:明日",
        "regex:^障害は解消していません.*一方.*暫定回避策.*利用できます.*恒久対応.*明日.*$",
    ):
        assert spec in checks
    output = (
        "障害は解消していません。一方、暫定回避策は利用できます。"
        "恒久対応は明日実施します。"
    )
    assert all(_check(spec, output, 0)["status"] == "pass"
               for spec in case["deterministic_checks"])


def test_rules_v2_4_preserve_ambiguity_precedence():
    rules = (PACK / "facets/policies/japanese-writing-rules-v2.md").read_text(
        encoding="utf-8"
    )

    for phrase in (
        "主体、指示語、修飾先が入力だけでは一意に決まらない",
        "参照先を推測で名指ししません",
        "どの解釈にも共通する事実だけで成立する表現",
        "主語と述語、修飾先の対応を読み手が追える形",
    ):
        assert phrase in rules
    assert "指示語の参照先を明確にします" not in rules


def test_rules_v2_4_requires_decision_relevant_condition_result_mapping():
    rules = (PACK / "facets/policies/japanese-writing-rules-v2.md").read_text(
        encoding="utf-8"
    )

    assert "## 技術説明" in rules
    for phrase in (
        "利用者が実際に尋ねていることへ直接答えます",
        "選択に必要な条件と結果の対応を少なくとも一つ",
        "結論に影響しない内部詳細",
        "網羅的な列挙",
        "競合",
        "待ち",
        "障害後の挙動",
        "判断に関係する場合は残します",
        "定型的な見出しを並べません",
        "入力にない前提を作りません",
    ):
        assert phrase in rules


def test_writer_sets_one_atomic_support_boundary_in_every_policy_arm():
    instruction = (PACK / "facets/instructions/japanese-write.md").read_text(
        encoding="utf-8"
    )
    writer = (PACK / "facets/personas/japanese-writer.md").read_text(encoding="utf-8")
    rules = (PACK / "facets/policies/japanese-writing-rules-v2.md").read_text(
        encoding="utf-8"
    )
    delivery = (PACK / "facets/policies/writing-delivery-contract.md").read_text(
        encoding="utf-8"
    )

    atomic_boundary = (
        "サポート返信で個人情報や業務データを含み得るときは、ファイル本体やデータ行は"
        "送らないでくださいと読み手に明示し、同じ段落で、代わりにヘッダーまたは列名だけを"
        "知らせ、氏名やメールアドレスなど不要な識別情報をマスクするよう案内します。"
    )
    assert atomic_boundary in writer.replace("\n", "")
    assert "必要性が明示されない限り" not in writer
    screenshot_boundary = (
        "エラーの確認が必要なサポート返信では、スクリーンショットを送らせず、秘密情報と"
        "不要な識別情報を除いたエラー文をテキストで共有するよう依頼します。"
    )
    assert screenshot_boundary in writer.replace("\n", "").replace("  ", "")

    common = instruction + writer
    arms = {
        "raw": common,
        "framework": common + delivery,
        "language": common + rules,
        "combined": common + delivery + rules,
    }
    assert all(atomic_boundary in prompt.replace("\n", "")
               for prompt in arms.values())
    for phrase in ("個人情報や業務データ", "ファイル本体やデータ行"):
        assert phrase not in rules


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
        "japanese-writing-ambiguity": (
            "会議後に共有する旨が伝えられました。"
        ),
        "japanese-writing-incident-delivery": (
            "INC-JP-17についてお知らせします。\n\n"
            "2026年8月9日14:10 JSTに、注文APIの一部でエラーを検知しました。"
            "新規注文の一部に影響しています。14:32 JSTに再起動を実施しました。\n\n"
            "原因と復旧見込みは調査中です。次回は15:30 JSTに状況を更新します。"
        ),
        "japanese-writing-internal-register": (
            "まだ確認できていません。20分後に確認し、確認後にコメントします。"
        ),
        "japanese-writing-meaningful-negation-contrast": (
            "障害は解消していません。一方、暫定回避策は利用できます。"
            "恒久対応は明日実施します。"
        ),
        "japanese-writing-no-workflow-meta": (
            "OPS-JP-META-1について、2026年8月12日14:00に会議室Bで運用会議を開きます。"
            "議題はリリース手順の確認です。"
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
        "japanese-writing-support-data-minimization": (
            "CSVファイル本体は送らないでください。データ行も送らないでください。"
            "ヘッダーまたは列名だけを共有し、氏名とメールアドレスはマスクしてください。"
            "スクリーンショットは送らないでください。秘密情報と不要な識別情報を"
            "除いたエラー文をテキストで共有してください。"
        ),
        "japanese-writing-technical-operation": (
            "write-throughはキャッシュへの書き込みと同時に保存先へ同期するため、"
            "整合性を保ちやすい一方で書き込みが遅くなります。write-backは先に"
            "キャッシュへ書き込み、後で保存先へ反映するため高速ですが、反映前の"
            "障害で失うリスクがあります。"
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
    assert '--secure-provider-config "$PWD/.rig/provider-pins.json"' in command
    assert "--goal-stdin" in command
    assert '--goal-stdin < "$PWD/.rig/japanese-goal.txt"' in command
    assert '--goal "' not in command
    assert '"schema_version": 1' in command
    assert "machine 固有の path や digest は同梱しません" in command
    for relative in ("skills/engine/SKILL.md", "skills/engine/PACKS.md"):
        catalog = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "domain:japanese-writing" in catalog
