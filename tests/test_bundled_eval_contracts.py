from __future__ import annotations

import hashlib
import pathlib

import pytest

from rig_workbench.eval.cases import EvalCaseError, canonical_json, validate_case
from rig_workbench.eval.runner import _check, run_case
from rig_workbench.packs.manifest import read_json_yaml
from rig_workbench.packs.model import PackError
from rig_workbench.packs.tester import compose_case_prompt


ROOT = pathlib.Path(__file__).parents[1]
PACKS = ROOT / "packs" / "domain"
SOURCE_COMMITS = {
    "decision-humor": "26cc81beaeb9ff35aaa5c9449a9800d789b01fa1",
    "sales": "b86b3a2e8ddc6c3ad79e1c3a68ffb45d8b0b0d71",
    "video-storytelling": "3baa91c68263065cd086b9f624f815f857e22c01",
}


def fixture(case_id: str, kind: str) -> str:
    clean = kind == "clean"
    values = {
        "coin-high-stakes-refusal": "PROD-AUDIT-DROP" if clean else "PROD-CUSTOMER-DROP",
        "coin-low-stakes-choice": "LOCAL-FIXTURE-NAME" if clean else "LOCAL-COUNT-NAME",
        "duck-question-only": "cache" if clean else "fixture",
        "magi-insufficient-evidence": "AUTH-UNKNOWN" if clean else "MIGRATION-UNKNOWN",
        "magi-majority-structure": "CACHE-17" if clean else "MIGRATION-42",
        "premortem-report-structure": "QUEUE-ROLLOUT-9" if clean else "DB-MIGRATION-7",
        "sage-evolved-manual-structure": "QUEUE-CHOICE-B" if clean else "CACHE-CHOICE-A",
        "sage-grounded-answer": "WORKER-BOOT-QUEUE" if clean else "API-500-REGION",
        "sage-insufficient-evidence": "SDK-5-DEPRECATED" if clean else "VERSION-3-BREAKING",
        "movie-storyboard-grounding": "DIFFVIEW-30" if clean else "LOGVIEW-45",
        "scenario-draft-grounding": "SCENARIO-DIFF-30" if clean else "SCENARIO-LOG-30",
    }
    if case_id == "coin-high-stakes-refusal":
        return f"## rig coin → magi 案件\n議題: {values[case_id]}\nコインで決めるべきではない。→ `$rig --recipe magi {values[case_id]}`"
    if case_id == "coin-low-stakes-choice":
        return f"## rig coin\n議題: {values[case_id]}\nトリアージ: 可逆 ✓ / 被害半径 小 ✓ / どちらでも実害小 ✓\n確定: sample。可逆だからまず動こう。"
    if case_id == "duck-question-only":
        return f"{values[case_id]}を読み込んだ直後の値は何？"
    if case_id == "magi-insufficient-evidence":
        missing = "負荷試験" if clean else "rollback検証"
        return f"## MAGI 合議結果\n議題: {values[case_id]}\n判定: 審議継続\n不足情報: {missing}\n次アクション: 不足情報を確認"
    if case_id == "magi-majority-structure":
        evidence = "p95を実測" if clean else "dry-run成功"
        return f"## MAGI 合議結果\n議題: {values[case_id]}\n判定: 可決\n集計: 可決 2 / 条件付 0 / 否決 1\nMELCHIOR — {evidence}\nBALTHASAR\nCASPER\n次アクション: 進行"
    if case_id == "premortem-report-structure":
        return f"## rig pre-mortem: 事前検死\n対象: {values[case_id]}\n総合リスク: 高\n### 失敗モード（可能性×影響の高い順）\n#### [R1] 移行が停止した\n- ガードレール: canaryで停止する\n### 最も安く効く 1 手\n- dry-run"
    if case_id == "roast-person-attack-refusal":
        anchors = ("src/cache.py:9", "src/cache.py:15", "src/cache.py:21") if clean else ("src/auth.py:12", "src/auth.py:18", "src/auth.py:24")
        return "根拠:\n" + "\n".join(f"{i}. コードの問題 — `{anchor}`" for i, anchor in enumerate(anchors, 1)) + "\n判定: REJECT\n確信度: 高\n"
    if case_id == "sage-evolved-manual-structure":
        anchors = ("evidence/queue-bench.txt:11", "docs/ops.md:27") if clean else ("evidence/cache-bench.txt:18", "src/cache.py:44")
        return f"《告》\n《解》{values[case_id]}を比較する。\n根拠:\n- {anchors[0]}\n- {anchors[1]}\n《演算完了》検証した仮説: 2 件（並列）\n《予測》両案の帰結\n《提案》最適解と次善案"
    if case_id == "sage-grounded-answer":
        anchor = "src/jobs.py:31" if clean else "src/api.py:42"
        return f"《告》\n《解》{values[case_id]}は必須環境変数の欠落。\n確度: 高\n根拠:\n- {anchor}"
    if case_id == "sage-insufficient-evidence":
        source = "release notes" if clean else "changelog"
        return f"《告》\n《解答不能》{values[case_id]} 不足: {source}またはdiff"
    if case_id == "deal-review-structure":
        detail = "決裁者を次回確認する" if clean else "営業が8月8日までに比較表を送る"
        return "## 商談レビュー結果\n総合評価: B\n| 観点 | 判定 | ひとことで |\n|---|---|---|\n| ヒアリング | ○ | 事実あり |\n| ニーズ把握 | ○ | 課題あり |\n| 提案 | △ | 根拠不足 |\n| クロージング | △ | 確認必要 |\n| ネクストアクション | ○ | 具体的 |\n### 次回の具体アクション（優先順）\n1. " + detail + "\n### 情報不足（記録に足りず評価できなかった点）\n- 効果の実績"
    if case_id == "sales-enablement-structure":
        product = "TraceBoard" if clean else "ReleaseGuard"
        source = "README" if clean else "README / CHANGELOG"
        return f"# 営業資料: {product}\n## ヘッドライン\n確認済み機能を紹介\n## こんな課題ありませんか（ターゲットの痛み・3点）\n- 手作業\n| 機能（実在） | だから何が嬉しいか（ベネフィット） | 出所 |\n|---|---|---|\n| 絞り込み | 探しやすい | {source} |\n## 次の一歩（CTA）\n- [要記入: 連絡先]\n# 荷電スクリプト: {product}\n## 1. オープニング（〜15秒）\n確認済み機能をご紹介します。\n## 5. 反論処理（よくある反論 → 切り返し）\n| 反論 | 切り返し |\n|---|---|\n| 不要 | 資料のみ送付 |\n## 6. クロージング（next action）\n[要記入: 候補日]"
    if case_id == "movie-storyboard-grounding":
        marker = values[case_id]
        cmd, observed = ("diffview before.json after.json --tsv changes.tsv", "wrote 7 changes to changes.tsv") if clean else ("logview events.json --type error --csv out.csv", "wrote 18 rows to out.csv")
        return f"ログライン: {marker}の実機能を見せる\n### シーン表\n| 1 | screen | `{cmd}` → `{observed}` |\n### CTA\nREADMEを確認\n### ソース対応表\n- screen → READMEとobserved output"
    if case_id == "release-movie-changelog-grounding":
        product, version, cmd, observed = ("DiffView", "v1.3", "diffview before.json after.json --tsv changes.tsv", "wrote 7 changes to changes.tsv") if clean else ("LogView", "v2.4", "logview events.json --type error --csv out.csv", "wrote 18 rows to out.csv")
        return f"## リリースムービー台本: {product} {version}\nログライン: 出荷済み機能を見せる\n### シーン表\n| 1 | 5s | screen | `{cmd}` → `{observed}` |\n### CTA（最終カード）\nCHANGELOGを確認\n### ソース対応表（誇張防止）\n- screen → CHANGELOG"
    if case_id == "scenario-draft-grounding":
        cmd, observed = ("diffview before.json after.json", "7 changes") if clean else ("logview events.json --date 2026-08-01", "matched 12 events")
        return f"## シナリオ: {values[case_id]}（explainer / 尺 30秒 / 観客 開発者）\nログライン: 実出力を見せる\n感情の弧: 課題→転換→CTA\n### ビートシート\n| # | 尺 | ビート | 画面 | テロップ | VO | source（実機能） |\n| 1 | 3s | hook | `{cmd}` | 結果 | 実行する | `{observed}` |\n### 目玉（1つ）: 実出力\n### CTA: READMEを確認"
    if case_id == "scenario-vet-rejects-invention":
        metric = "24時間" if clean else "80%"
        return f"観点: video-content-safety\n根拠:\n- 冒頭: フックが弱い\n- 数値: {metric}にsourceがない\n- CTA: 2つあり競合\n修正条件:\n- 数値を削除しCTAを1つにする\n判定: REJECT\n確信度: high"
    raise AssertionError(case_id)


def iter_cases():
    for pack_id in SOURCE_COMMITS:
        pack = PACKS / pack_id
        _raw, manifest = read_json_yaml(pack / "pack.yaml")
        for relative in manifest["assets"]["eval-case"]:
            _case_raw, case = read_json_yaml(pack / relative)
            yield pack_id, pack, manifest, case


def test_all_bundled_cases_are_composed_distinct_and_recomputable():
    count = 0
    covered: dict[str, set[str]] = {pack: set() for pack in SOURCE_COMMITS}
    for pack_id, pack, manifest, case in iter_cases():
        count += 1
        validate_case(case)
        assert case["provenance"]["source_commit"] == SOURCE_COMMITS[pack_id]
        assert case["provenance"]["source_hashes"] == {
            "task.json": hashlib.sha256(canonical_json(case["target_inputs"]).encode()).hexdigest()
        }
        assert case["target_expectations"] == case["deterministic_checks"]
        assert case["target_expectations"] != case["clean_expectations"]
        prompt = compose_case_prompt(pack, manifest, case, project=ROOT)
        assert all(surface in prompt for surface in case["prompt_composition"])
        covered[pack_id].update(case["prompt_composition"])
    assert count == 16
    for pack_id in SOURCE_COMMITS:
        _raw, manifest = read_json_yaml(PACKS / pack_id / "pack.yaml")
        expected = {f"{entry['kind']}:{entry['target']}" for entry in manifest["entrypoints"]}
        assert expected <= covered[pack_id]


def test_contract_fixtures_pass_their_lane_and_fail_the_inverse_lane():
    for _pack_id, _pack, _manifest, case in iter_cases():
        target = fixture(case["id"], "target")
        clean = fixture(case["id"], "clean")
        assert all(_check(spec, target, 0)["status"] == "pass" for spec in case["target_expectations"]), case["id"]
        assert all(_check(spec, clean, 0)["status"] == "pass" for spec in case["clean_expectations"]), case["id"]
        assert any(_check(spec, clean, 0)["status"] == "fail" for spec in case["target_expectations"]), case["id"]
        assert any(_check(spec, target, 0)["status"] == "fail" for spec in case["clean_expectations"]), case["id"]


def test_schema_rejects_identical_lane_expectations():
    case = next(case for _id, _pack, _manifest, case in iter_cases())
    case["clean_expectations"] = list(case["target_expectations"])
    with pytest.raises(EvalCaseError, match="must be distinct"):
        validate_case(case)


def test_contract_composition_alias_resolves_owned_and_rejects_unknown():
    pack = PACKS / "decision-humor"
    _raw, manifest = read_json_yaml(pack / "pack.yaml")
    case = next(case for _id, _pack, _manifest, case in iter_cases()
                if case["id"] == "premortem-report-structure")
    prompt = compose_case_prompt(pack, manifest, case, project=ROOT)
    assert "--- contract:premortem-report (owner=decision-humor) ---" in prompt
    case["prompt_composition"][-1] = "contract:not-owned"
    with pytest.raises(PackError, match="dependency is unavailable"):
        compose_case_prompt(pack, manifest, case, project=ROOT)


def test_runner_selects_target_and_clean_expectations(monkeypatch, tmp_path):
    case = next(case for _id, _pack, _manifest, case in iter_cases()
                if case["id"] == "scenario-vet-rejects-invention")
    monkeypatch.setenv("RIG_EVAL_ATTESTATION_KEY", "bundled-case-test-attestation-key-32-bytes")

    def execute(**kwargs):
        return 0, fixture(case["id"], kwargs["kind"]), "", None

    monkeypatch.setattr("rig_workbench.eval.runner._execute", execute)
    _path, result = run_case(
        case, repo=ROOT, provider="codex", model="fixture-only", repeat=3,
        phase="current", result_root=tmp_path,
    )
    assert all(row["outcome"] == "pass" for row in [*result["target"], *result["clean"]])
    assert {check["spec"] for check in result["target"][0]["checks"]} == set(case["target_expectations"])
    assert {check["spec"] for check in result["clean"][0]["checks"]} == set(case["clean_expectations"])
