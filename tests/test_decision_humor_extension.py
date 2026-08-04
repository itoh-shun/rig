import hashlib
import json
import pathlib
import shutil

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PACK = REPO_ROOT / "packs/domain/decision-humor"
RECIPES = {"coin", "duck", "magi", "pre-mortem", "roast", "sage"}


def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("RIG_HOME", str(REPO_ROOT))
    monkeypatch.setenv("RIG_USER_HOME", str(tmp_path / "user-home"))
    monkeypatch.delenv("RIG_ORG_HOME", raising=False)


def test_decision_humor_is_opt_in_and_manifest_is_exact(monkeypatch, tmp_path):
    from rig_workbench.packs.manifest import parse_frontmatter_subset
    from rig_workbench.packs.resolver import resolve_asset
    from rig_workbench.packs.validation import validate_pack

    _isolated(monkeypatch, tmp_path)
    for name in RECIPES:
        assert not (REPO_ROOT / "skills/rig/recipes" / f"{name}.md").exists()
        assert not (REPO_ROOT / "commands" / f"{name}.md").exists()
        assert resolve_asset("recipe", name, project=tmp_path) is None
        assert resolve_asset("command", name, project=tmp_path) is None
    manifest = validate_pack(PACK)
    assert manifest["dependencies"] == []
    assert {pathlib.PurePosixPath(item).stem for item in manifest["assets"]["recipe"]} == RECIPES
    assert len(manifest["assets"]["eval-case"]) == 10
    for name in ("magi", "sage"):
        recipe = parse_frontmatter_subset(PACK / "recipes" / f"{name}.md")
        assert recipe["no_orchestrate"] in (True, "true")


def test_alias_install_resolves_every_owned_asset_records_trust_and_removes(
        monkeypatch, tmp_path):
    from rig_workbench.orchestrate import config as orchestrate_config
    from rig_workbench.orchestrate.recipes import resolve_plan_json, resolve_recipe
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.model import ASSET_DIRS, PROMPT_KINDS
    from rig_workbench.packs.remover import remove_pack
    from rig_workbench.packs.resolver import resolve_asset

    _isolated(monkeypatch, tmp_path)
    project = tmp_path / "project"
    trust = tmp_path / "pack-trust.json"
    monkeypatch.setenv("RIG_ALLOW_PROJECT_PACKS", "1")
    monkeypatch.setenv("RIG_PACK_TRUST_STORE", str(trust))
    monkeypatch.setattr(orchestrate_config, "INVOCATION_CWD", project)
    monkeypatch.setattr(orchestrate_config, "PROJECT_RECIPES", project / ".rig/recipes")
    result = install_pack("domain:decision-humor", scope="project", project=project,
                          allow_unverified=True)
    assert result.manifest["id"] == "decision-humor"
    for kind, paths in result.manifest["assets"].items():
        if kind not in PROMPT_KINDS:
            continue
        prefix = pathlib.PurePosixPath(ASSET_DIRS[kind])
        for relative in paths:
            name = str(pathlib.PurePosixPath(relative).relative_to(prefix).with_suffix(""))
            resolved = resolve_asset(kind, name, project=project)
            assert resolved is not None, f"unresolved {kind}:{name}"
            assert resolved.pack_id == "decision-humor"
    recipe_path = resolve_recipe("coin")
    assert resolve_plan_json(recipe_path)["n_steps"] == 1
    assert trust.exists() and "decision-humor" in trust.read_text(encoding="utf-8")

    _target, removed = remove_pack("decision-humor", scope="project", project=project,
                                   yes=True)
    assert removed
    for name in RECIPES:
        assert resolve_asset("recipe", name, project=project) is None


def test_all_four_domain_packs_coexist_and_same_tier_collision_fails(monkeypatch, tmp_path):
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.resolver import resolve_asset
    from rig_workbench.packs.validation import validate_tiered_collection

    _isolated(monkeypatch, tmp_path)
    project = tmp_path / "project"
    for pack_id in ("sales", "sns-x", "video-storytelling", "decision-humor"):
        install_pack(f"domain:{pack_id}", scope="project", project=project,
                     allow_unverified=True)
    for kind, name in (("recipe", "deal-review"), ("recipe", "sns-x-post"),
                       ("recipe", "movie"), ("recipe", "magi")):
        assert resolve_asset(kind, name, project=project) is not None

    duplicate = project / ".rig/packs/collision"
    shutil.copytree(project / ".rig/packs/decision-humor", duplicate)
    manifest = duplicate / "pack.yaml"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace(
        "decision-humor", "decision-humor-copy"
    ), encoding="utf-8")
    compatibility = duplicate / "compatibility.yaml"
    compatibility.write_text(compatibility.read_text(encoding="utf-8").replace(
        '"pack_id":"decision-humor"', '"pack_id":"decision-humor-copy"'
    ), encoding="utf-8")
    with pytest.raises(PackError, match="same-tier asset collision"):
        validate_tiered_collection([
            ("project", project / ".rig/packs/decision-humor"),
            ("project", duplicate),
        ])


def test_unknown_gate_and_no_orchestrate_fail_closed(monkeypatch, tmp_path, capsys):
    from rig_workbench.orchestrate import commands
    from rig_workbench.orchestrate.runstate import compute_next, gate_outcome, new_state
    from rig_workbench.packs.manifest import canonical, digest, read_json_yaml
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.validation import validate_pack

    step = {"id": "vote", "instruction": "x", "gate": "custom-vote",
            "checks": [], "max_retries": 1}
    state = new_state("custom", [step], None)
    assert compute_next(state)[0] == "BLOCKED"
    assert state["stopped"]["kind"] == "BLOCKED"
    assert state["step_state"]["vote"]["retries"] == 0
    assert gate_outcome(step, state["step_state"]["vote"]) == "unsupported"

    monkeypatch.setattr(commands, "resolve_recipe", lambda _name: PACK / "recipes/magi.md")
    commands.cmd_plan(["magi", "--json"])
    manual_plan = json.loads(capsys.readouterr().out)
    assert manual_plan["execution"]["structurally_valid"] is True
    assert manual_plan["execution"]["orchestratable"] is False
    assert manual_plan["execution"]["manual_only"] is True

    for entrypoint in (commands.cmd_init, commands.cmd_run):
        with pytest.raises(SystemExit) as stopped:
            entrypoint(["magi"])
        assert stopped.value.code == 2
        assert "no_orchestrate" in capsys.readouterr().out

    copied = tmp_path / "decision-humor"
    shutil.copytree(PACK, copied)
    recipe = copied / "recipes/magi.md"
    recipe.write_text(recipe.read_text(encoding="utf-8").replace(
        "no_orchestrate: true\n", ""
    ), encoding="utf-8")
    _raw, manifest = read_json_yaml(copied / "pack.yaml")
    manifest["hashes"]["recipes/magi.md"] = digest(recipe)
    (copied / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    with pytest.raises(PackError, match="unsupported executable gate"):
        validate_pack(copied)


def test_ten_eval_cases_have_real_provenance_and_runnable_markdown_checks():
    from rig_workbench.eval.cases import canonical_json
    from rig_workbench.eval.runner import _check
    from rig_workbench.packs.manifest import read_json_yaml

    outputs = {
        "magi-majority-structure": "## MAGI 合議結果\n議題: MIGRATION-42\n判定: 可決\n集計: 可決 2 / 否決 1\nMELCHIOR — dry-run成功\nBALTHASAR\nCASPER\n次アクション",
        "magi-insufficient-evidence": "## MAGI 合議結果\n議題: MIGRATION-UNKNOWN\n判定: 審議継続\n不足情報: rollback evidence\n次アクション: 情報確認",
        "roast-person-attack-refusal": "根拠:\n1. src/auth.py:12\n2. src/auth.py:18\n3. src/auth.py:24\n判定: REJECT\n確信度: 高\n",
        "coin-low-stakes-choice": "## rig coin\n議題: LOCAL-COUNT-NAME\nトリアージ: 可逆 ✓ / 被害半径 小 ✓ / どちらでも実害小 ✓\n確定: count",
        "coin-high-stakes-refusal": "## rig coin → magi 案件\n議題: PROD-CUSTOMER-DROP\nコインで決めるべきではない。\n$rig --recipe magi",
        "duck-question-only": "fixture を読み込んだ直後の値は何？",
        "premortem-report-structure": "## rig pre-mortem: 事前検死\n対象: DB-MIGRATION-7\n総合リスク: 高\n### 失敗モード（可能性×影響の高い順）\n#### [R1] lock\n- ガードレール: canary\n### 最も安く効く 1 手\n- test",
        "sage-grounded-answer": "《告》解析完了\n《解》API-500-REGIONはREGION不足\n確度: 高\n根拠:\n- src/api.py:42",
        "sage-insufficient-evidence": "《告》解析不能\n《解答不能》VERSION-3-BREAKING 不足: changelog",
        "sage-evolved-manual-structure": "《演算完了》CACHE-CHOICE-A\n《予測》帰結\n《提案》Redis\n根拠:\n- evidence/cache-bench.txt:18\n- src/cache.py:44",
    }
    cases = sorted((PACK / "evals/cases").glob("*/case.json"))
    assert len(cases) == 10
    for path in cases:
        _raw, case = read_json_yaml(path)
        assert case["status"] == "approved" and case["repeat"] == 3
        assert case["provenance"]["source_commit"] == (
            "26cc81beaeb9ff35aaa5c9449a9800d789b01fa1"
        )
        assert case["provenance"]["captured_at"] == "2026-08-05T06:00:00+09:00"
        assert set(case["provenance"]["source_hashes"]) == {"task.json"}
        assert case["provenance"]["source_hashes"]["task.json"] == hashlib.sha256(
            canonical_json(case["target_inputs"]).encode()
        ).hexdigest()
        results = [_check(spec, outputs[case["id"]], 0)
                   for spec in case["deterministic_checks"]]
        assert all(item["status"] == "pass" for item in results), results


def test_active_core_has_no_decision_humor_or_party_assets():
    assert not (REPO_ROOT / "skills/rig/facets/personas/content-risk-reviewer.md").exists()
    assert not (REPO_ROOT / "commands/party.md").exists()
    paths = [REPO_ROOT / "README.md", REPO_ROOT / "README.ja.md"]
    for directory in (REPO_ROOT / "commands", REPO_ROOT / "skills/rig", REPO_ROOT / "web"):
        paths.extend(path for path in directory.rglob("*")
                     if path.suffix in {".md", ".html"}
                     and "history" not in path.parts)
    text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    for legacy in ("/rig:magi", "/rig:roast", "/rig:coin", "/rig:duck",
                   "/rig:pre-mortem", "/rig:sage", "/rig:party",
                   "content-risk-reviewer", "sage_notifications"):
        assert legacy not in text
