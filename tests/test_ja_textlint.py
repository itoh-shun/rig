"""The ja-textlint sensor: what it checks, what it refuses to claim, and the three states.

The policy this pins is `skills/engine/facets/policies/japanese-textlint-rules`. Its
load-bearing claim is a boundary: rules decided by characters and dictionaries are
`error`, rules that approximate part-of-speech are `warning` and never move the exit
code. Several tests assert that the sensor does **not** report something — a rule that
silently grew a capability would make the shipped ratio a lie.
"""

import json
import os
import pathlib
import subprocess
import sys
import unicodedata

import pytest

from rig_workbench import ja_textlint as jt

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "scripts" / "ja_textlint.py"
MANIFESTS = REPO_ROOT / "skills" / "engine" / "manifests"
TEMPLATE = MANIFESTS / "ja-textlint.template.json"
SCHEMA = MANIFESTS / "ja-textlint.schema.json"


def _lint(text: str, config: dict | None = None, rules: list[str] | None = None) -> list[dict]:
    settings = jt.Settings(config)
    if rules:
        settings.enabled = {r: jt.DEFAULT_SEVERITY[r] for r in rules}
    return jt.lint_text(text, settings, "t.md")


def _rules(findings: list[dict]) -> list[str]:
    return [f["rule"] for f in findings]


def _run(tmp_path, config: dict | None, files: dict[str, str], extra=()):
    art = tmp_path / "art"
    art.mkdir(exist_ok=True)
    for name, body in files.items():
        (art / name).write_text(body, encoding="utf-8")
    argv = []
    if config is not None:
        cpath = tmp_path / "ja-textlint.json"
        cpath.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        argv += ["--config", str(cpath)]
    report = tmp_path / "report.json"
    argv += ["--report", str(report), *extra, str(art)]
    code = jt.main(argv)
    return code, json.loads(report.read_text(encoding="utf-8"))


# ── 検出：文字と辞書で決まるもの（error） ─────────────────────────────────────
def test_a_long_sentence_is_measured_without_code_spans():
    long = "あ" * 101 + "。"
    assert _rules(_lint(long + "\n", rules=["sentence-length"])) == ["sentence-length"]
    coded = "`" + "x" * 200 + "` を含む短い文です。\n"
    assert _lint(coded, rules=["sentence-length"]) == []


def test_too_many_commas_in_one_sentence():
    assert _rules(_lint("これは、読点が、四つ、以上、あります。\n", rules=["max-ten"])) == ["max-ten"]
    assert _lint("これは、読点が、三つ、あります。\n", rules=["max-ten"]) == []


def test_double_negative_fixed_forms_but_not_nakereba_naranai():
    assert _rules(_lint("できないことはない。\n", rules=["no-double-negative-ja"])) == ["no-double-negative-ja"]
    assert _lint("省略しなければならない。\n", rules=["no-double-negative-ja"]) == []
    # 形式名詞を漢字で書いても同じ二重否定。前後比較の下書きですり抜けた形。
    assert _rules(_lint("壊れることがない訳ではありません。\n", rules=["no-double-negative-ja"])) == ["no-double-negative-ja"]


def test_redundant_expression_dictionary_reports_the_span_and_a_reason():
    f = _lint("この機能を利用することができます。\n", rules=["ja-no-redundant-expression"])
    assert len(f) == 1 and f[0]["text"] == "することができます"
    assert f[0]["column"] == 8
    # 「約」の後ろが漢数字や「半分」でも同じ重複。前後比較の下書きですり抜けた形。
    f = _lint("時間を約半分ほどに短縮した。約三割程度です。\n", rules=["ja-no-redundant-expression"])
    assert [x["text"] for x in f] == ["約半分ほど", "約三割程度"]


def test_prh_terms_come_only_from_the_declaration():
    text = "Javascript と github で書く。\n"
    assert _lint(text) == []  # 宣言が無ければ用語規則は無い
    cfg = {"terms": [{"pattern": "Javascript", "expected": "JavaScript"},
                     {"pattern": "github", "expected": "GitHub"}]}
    f = _lint(text, cfg, rules=None)
    assert [x["fix"] for x in f if x["rule"] == "prh"] == ["JavaScript", "GitHub"]


def test_prh_regex_term_is_case_sensitive_and_skips_the_expected_form():
    cfg = {"terms": [{"pattern": "[Gg]it[Hh]ub", "expected": "GitHub", "regex": True}]}
    f = _lint("GitHub と Github と github。\n", cfg)
    assert [x["text"] for x in f if x["rule"] == "prh"] == ["Github", "github"]


def test_paragraph_without_a_period_and_a_paragraph_with_an_ascii_period():
    f = _lint("この段落は句点で終わっていません\n\nこの段落はピリオドです.\n", rules=["ja-no-mixed-period"])
    assert [(x["line"], "ピリオド" in x["message"]) for x in f] == [(1, False), (3, True)]


def test_unmatched_pair_is_per_paragraph():
    f = _lint("この文には（閉じ括弧がありません。\n\n「開きだけ。\n\n（正しい）です。\n", rules=["no-unmatched-pair"])
    assert [x["line"] for x in f] == [1, 3]


# ── 検出：近似（warning、exit code を動かさない） ───────────────────────────
@pytest.mark.parametrize("rule", [
    "no-doubled-joshi", "no-dropping-the-ra", "no-dropped-i", "no-mix-dearu-desumasu",
    "ja-hiragana-keishikimeishi", "ja-no-successive-word", "ja-unnatural-alphabet",
])
def test_approximate_rules_default_to_warning(rule):
    assert jt.DEFAULT_SEVERITY[rule] == "warning"


def test_a_warning_alone_leaves_exit_zero_unless_strict(tmp_path):
    code, report = _run(tmp_path, None, {"a.md": "映画を見れた。\n"})
    assert report["summary"] == {"errors": 0, "warnings": 1, "files": 1}
    assert code == 0
    code, _ = _run(tmp_path, None, {"a.md": "映画を見れた。\n"}, extra=("--strict",))
    assert code == 1


def test_doubled_joshi_counts_low_confidence_particles_for_the_interval_only():
    # 「まで」「の」を助詞と数えないと「が…が」が隣接になる。textlint（kuromoji）は数える。
    assert _lint("検索結果が表示されるまでの待ち時間が長い。\n", rules=["no-doubled-joshi"]) == []
    f = _lint("材料不足で代替素材で製品を作った。\n", rules=["no-doubled-joshi"])
    assert _rules(f) == ["no-doubled-joshi"]
    # 「〜ますが、」の接続助詞は、後ろの格助詞「が」の相手にならない。
    assert _lint("利用できますが、結果は古い。\n", rules=["no-doubled-joshi"]) == []


def test_dropping_ra_does_not_fire_on_the_conditional():
    assert _rules(_lint("映画を見れた。\n", rules=["no-dropping-the-ra"])) == ["no-dropping-the-ra"]
    assert _lint("映画を見れば分かる。\n", rules=["no-dropping-the-ra"]) == []


def test_dropped_i_does_not_fire_after_a_kanji_or_an_a_row_kana():
    """「見てる」と「建てる」は表層で分けられない。分けられないものは報告しない。"""
    assert _rules(_lint("いま作業をしてる。\n", rules=["no-dropped-i"])) == ["no-dropped-i"]
    assert _lint("家を建てる。紙を捨てる。花を愛でる。\n", rules=["no-dropped-i"]) == []


def test_register_mix_reports_the_minority_and_leaves_a_pure_document_alone():
    body = "敬体です。\n\n敬体です。\n\n常体である。\n"
    f = _lint(body, rules=["no-mix-dearu-desumasu"])
    assert [x["line"] for x in f] == [5]
    assert _lint("常体だ。\n\n常体である。\n", rules=["no-mix-dearu-desumasu"]) == []


def test_register_check_skips_lists_and_headings():
    body = "# 見出しである\n\n- 箇条書きだ\n\n本文です。\n"
    assert _lint(body, rules=["no-mix-dearu-desumasu"]) == []


def test_successive_word_does_not_fire_on_the_particle_before_dekiru():
    """「自動でできる」は助詞「で」＋「できる」。rig 自身の docs で最初に出た偽陽性。"""
    assert _lint("レビューも自動でできるので便利です。\n", rules=["ja-no-successive-word"]) == []
    assert _rules(_lint("会議でで決めた。\n", rules=["ja-no-successive-word"])) == ["ja-no-successive-word"]


def test_unnatural_alphabet_is_lowercase_beside_hiragana_only():
    assert _rules(_lint("こnにちは。\n", rules=["ja-unnatural-alphabet"])) == ["ja-unnatural-alphabet"]
    assert _lint("提案Aと主張Bの領分。\n", rules=["ja-unnatural-alphabet"]) == []


# ── 空白：既定 auto は文書内の揺れだけを見る（textlint からの意図した逸脱） ────
def test_spacing_auto_is_silent_on_a_consistent_document_and_reports_the_minority():
    spaced = "これは Markdown の説明です。\n\nこれは HTML の説明です。\n"
    assert _lint(spaced, rules=["ja-space-between-half-and-full-width"]) == []
    mixed = spaced + "\nこれはCSSの説明です。\n"
    f = _lint(mixed, rules=["ja-space-between-half-and-full-width"])
    assert [x["line"] for x in f] == [5, 5]


def test_spacing_never_and_always_are_explicit():
    cfg = {"rules": {"ja-space-between-half-and-full-width": {"space": "never"}}}
    assert len(_lint("これは Markdown の説明です。\n", cfg)) == 2
    cfg = {"rules": {"ja-space-between-half-and-full-width": {"space": "always"}}}
    assert len(_lint("これはMarkdownの説明です。\n", cfg)) == 2


def test_digits_beside_kanji_never_vote_on_spacing_style():
    text = "2026年9月7日 14時05分に API の応答が遅延した。\n"
    assert _lint(text, rules=["ja-space-between-half-and-full-width"]) == []


# ── Markdown の構造 ───────────────────────────────────────────────────────────
def test_code_fences_front_matter_and_tables_are_outside_sentence_rules():
    text = (
        "---\ntitle: x\n---\n\n```\n" + "あ" * 200 + "。\n```\n\n"
        "| 列 | " + "あ" * 200 + "。 |\n|---|---|\n"
    )
    assert _lint(text, rules=["sentence-length", "ja-no-mixed-period"]) == []


def test_character_rules_still_see_table_rows():
    f = _lint("| 列 | ﾃｷｽﾄ |\n|---|---|\n", rules=["no-hankaku-kana"])
    assert _rules(f) == ["no-hankaku-kana"] and f[0]["line"] == 1


def test_emphasis_markers_do_not_hide_the_period():
    assert _lint("**強調で終わる。**\n", rules=["ja-no-mixed-period"]) == []


def test_columns_are_one_based_and_survive_masking():
    f = _lint("- `code` のあとにﾃｷｽﾄ。\n", rules=["no-hankaku-kana"])
    assert (f[0]["line"], f[0]["column"]) == (1, 14)


def test_english_paragraphs_are_left_alone():
    text = "This is an English paragraph that is well over one hundred characters long and " \
           "has, several, commas, in it, and it ends with a period.\n"
    assert _lint(text) == []


# ── 設定：黙って抜けない ─────────────────────────────────────────────────────
def test_an_unknown_config_key_is_unchecked_not_a_pass(tmp_path):
    code, report = _run(tmp_path, {"rulez": {}}, {"a.md": "できないことはない。\n"})
    assert (code, report["status"]) == (2, "unchecked")
    assert "rulez" in report["reason"]


def test_an_unknown_rule_name_is_unchecked(tmp_path):
    code, report = _run(tmp_path, {"rules": {"sentence-lenght": False}}, {"a.md": "。\n"})
    assert (code, report["status"]) == (2, "unchecked")


def test_a_rule_can_be_disabled_or_promoted(tmp_path):
    body = {"a.md": "できないことはない。\n"}
    code, _ = _run(tmp_path, {"rules": {"no-double-negative-ja": False}}, body)
    assert code == 0
    code, report = _run(tmp_path, {"rules": {"no-double-negative-ja": "warning"}}, body)
    assert code == 0 and report["summary"]["warnings"] == 1
    code, _ = _run(tmp_path, {"rules": {"no-dropping-the-ra": "error"}}, {"a.md": "見れた。\n"})
    assert code == 1


def test_ignore_patterns_drop_findings_by_their_text(tmp_path):
    body = {"a.md": "株式会社東京電力の担当者。\n"}
    cfg = {"presets": ["technical"], "rules": {"max-kanji-continuous-len": "error"}}
    assert _run(tmp_path, cfg, body)[0] == 1
    cfg["ignore"] = ["東京電力"]
    assert _run(tmp_path, cfg, body)[0] == 0


def test_the_template_validates_against_the_schema_and_has_no_placeholder():
    data = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert set(data) <= set(schema["properties"])
    assert schema["additionalProperties"] is False
    settings = jt.Settings(data)  # 雛形はそのまま読める
    assert settings.paths and "prh" in settings.enabled
    assert set(data["rules"]) <= set(jt.RULES)


def test_every_rule_has_a_preset_or_is_prh_and_a_default_severity():
    in_presets = {r for rules in jt.PRESETS.values() for r in rules}
    assert in_presets | {"prh"} == set(jt.RULES) == set(jt.DEFAULT_SEVERITY)


# ── 状態：3つ、どれも合格ではない ────────────────────────────────────────────
def test_no_targets_is_unchecked_unless_if_configured(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    report = tmp_path / "r.json"
    assert jt.main(["--report", str(report)]) == 2
    assert json.loads(report.read_text(encoding="utf-8"))["status"] == "unchecked"
    assert jt.main(["--report", str(report), "--if-configured"]) == 0
    assert json.loads(report.read_text(encoding="utf-8"))["status"] == "not-configured"


def test_config_paths_are_the_targets_when_no_argument_is_given(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("できないことはない。\n", encoding="utf-8")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "ja-textlint.json").write_text(
        json.dumps({"paths": ["docs/"]}), encoding="utf-8")
    report = tmp_path / "r.json"
    assert jt.main(["--report", str(report)]) == 1
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["artifacts"] == [os.path.join("docs", "a.md")]
    assert data["config_sha256"]


def test_an_explicit_missing_config_is_unchecked(tmp_path):
    (tmp_path / "a.md").write_text("。\n", encoding="utf-8")
    report = tmp_path / "r.json"
    code = jt.main(["--config", str(tmp_path / "nope.json"), "--report", str(report), str(tmp_path / "a.md")])
    assert code == 2


def test_broken_json_and_non_utf8_are_unchecked(tmp_path):
    (tmp_path / "a.md").write_text("。\n", encoding="utf-8")
    cfg = tmp_path / "c.json"
    cfg.write_text("{", encoding="utf-8")
    report = tmp_path / "r.json"
    assert jt.main(["--config", str(cfg), "--report", str(report), str(tmp_path / "a.md")]) == 2
    bad = tmp_path / "b.md"
    bad.write_bytes("日本語".encode("shift_jis"))
    assert jt.main(["--report", str(report), str(bad)]) == 2
    assert "UTF-8" in json.loads(report.read_text(encoding="utf-8"))["reason"]


def test_the_report_file_is_never_scanned_as_an_artifact(tmp_path):
    art = tmp_path / "art"
    art.mkdir()
    (art / "a.md").write_text("問題ない。\n", encoding="utf-8")
    report = art / "report.json"
    report.write_text(json.dumps({"findings": [{"text": "できないことはない"}]}), encoding="utf-8")
    assert jt.main(["--report", str(report), str(art)]) == 0


def test_symlinks_are_skipped_and_named(tmp_path):
    art = tmp_path / "art"
    art.mkdir()
    (tmp_path / "outside.md").write_text("できないことはない。\n", encoding="utf-8")
    os.symlink(tmp_path / "outside.md", art / "link.md")
    (art / "a.md").write_text("問題ない。\n", encoding="utf-8")
    report = tmp_path / "r.json"
    assert jt.main(["--report", str(report), str(art)]) == 0
    data = json.loads(report.read_text(encoding="utf-8"))
    assert [s["why"] for s in data["skipped"]] == ["symlink"]


def test_stdin_is_a_first_class_artifact(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(LAUNCHER), "--json", "-"],
        input="できないことはない。\n", capture_output=True, text=True, cwd=tmp_path, timeout=60, check=False,
    )
    assert proc.returncode == 1, proc.stderr
    data = json.loads(proc.stdout)
    assert data["artifacts"] == ["-"]
    assert data["findings"][0]["file"] == jt.STDIN_NAME


def test_the_cli_subcommand_is_routed(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "rig_workbench.cli", "ja-lint", "--list-rules"],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=60, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "sentence-length" in proc.stdout and "[hiragana]" in proc.stdout


def test_nfd_is_detected_and_nfc_is_not():
    text = "データベースをバックアップします。\n"
    assert _lint(text, rules=["no-nfd"]) == []
    found = _lint(unicodedata.normalize("NFD", text), rules=["no-nfd"])
    assert set(_rules(found)) == {"no-nfd"} and len(found) == 4  # デ・ベ・バ・プ の一字ごと


def test_bom_is_not_a_zero_width_finding_but_a_later_one_is():
    assert _lint("\ufeff問題ない。\n", rules=["no-zero-width-spaces"]) == []
    assert _rules(_lint("問\u200b題。\n", rules=["no-zero-width-spaces"])) == ["no-zero-width-spaces"]
