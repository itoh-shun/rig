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
def test_a_long_sentence_counts_code_but_not_urls():
    """textlint と同じ数え方。インラインコードは読む文字なので数え、URL やリンク先は表示されないので数えない。"""
    long = "あ" * 101 + "。"
    assert _rules(_lint(long + "\n", rules=["sentence-length"])) == ["sentence-length"]
    coded = "`" + "x" * 200 + "` を含む文です。\n"
    assert _rules(_lint(coded, rules=["sentence-length"])) == ["sentence-length"]
    linked = "[短い](https://example.com/" + "x" * 200 + ") を含む文です。\n"
    assert _lint(linked, rules=["sentence-length"]) == []


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
    assert report["summary"] == {"errors": 0, "warnings": 1, "files": 1, "suppressed": 0,
                                 "unclosed_disable": 0, "unclosed_fence": 0, "fixed": 0}
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


# ── 本家との突き合わせで直したもの ───────────────────────────────────────────
def test_wo_is_never_a_doubled_particle_and_commas_widen_the_interval():
    """textlint は格助詞「を」の重なりを例外にし、読点と括弧を距離に数える。"""
    assert _lint("AIを使った開発を支援するツールです。\n", rules=["no-doubled-joshi"]) == []
    assert _lint("私は、彼は好きだ。\n", rules=["no-doubled-joshi"]) == []
    assert _rules(_lint("結果が古い場合があります。\n", rules=["no-doubled-joshi"])) == ["no-doubled-joshi"]
    # 「こと」の「と」は語の一部。距離に数えると「が…が」が離れて見える。
    assert _rules(_lint("停止すると、インデックスが壊れることがあります。\n", rules=["no-doubled-joshi"])) == ["no-doubled-joshi"]


def test_commas_between_nouns_are_a_list_not_a_pause():
    """textlint の非厳密モード：「A、B、C」の並列の読点は数えない。strict で数える。"""
    text = "基準は、build が成功する、lint が0件、レビューで REJECT がない、といったものです。\n"
    assert _lint(text, rules=["max-ten"]) == []
    assert _rules(_lint(text, {"presets": ["technical"], "rules": {"max-ten": {"strict": True}}})) == ["max-ten"]


def test_kanji_run_limit_is_the_presets_six():
    assert _lint("情報処理技術。\n", rules=["max-kanji-continuous-len"]) == []
    assert _rules(_lint("個人情報保護法改正案。\n", rules=["max-kanji-continuous-len"])) == ["max-kanji-continuous-len"]


def test_a_lazily_continued_list_item_is_one_paragraph():
    """Markdown の lazy continuation。別段落にすると一文が二つに割れて長さが半分に見える。"""
    text = "- " + "あ" * 60 + "\n" + "い" * 60 + "。\n"
    assert _rules(_lint(text, rules=["sentence-length"])) == ["sentence-length"]
    assert _lint("- （開き\n  閉じ）。\n", rules=["no-unmatched-pair"]) == []


def test_fukushi_uses_the_upstream_dictionary_and_guards_compounds():
    assert [f["fix"] for f in _lint("最も予め設定する。\n", rules=["ja-hiragana-fukushi"])] == ["もっとも", "あらかじめ"]
    assert _lint("変更に例えば全ての土台を正しく。\n", rules=["ja-hiragana-fukushi"]) == []


def test_hojodoushi_no_longer_reads_nai_as_an_auxiliary():
    assert _lint("時間が無い。結果が有る。\n", rules=["ja-hiragana-hojodoushi"]) == []
    f = _lint("お願い致します。確認して下さい。\n", rules=["ja-hiragana-hojodoushi"])
    assert [x["fix"] for x in f] == ["いたし", "ください"]
    assert jt.apply_fixes("お願い致します。\n", f)[0] == "お願いいたします。\n"


def test_keishikimeishi_covers_hou_goto_tabi():
    f = _lint("無い方がよい。マージ毎に走る。する度に増える。\n", rules=["ja-hiragana-keishikimeishi"])
    assert [x["fix"] for x in f] == ["ほう", "ごと", "たび"]


def test_three_more_spacing_rules():
    assert _rules(_lint("人間 対 生成。\n", rules=["ja-no-space-between-full-width"])) == ["ja-no-space-between-full-width"] * 2
    assert _lint("ウェブ ブラウザ。\n", rules=["ja-no-space-between-full-width"]) == []
    assert _rules(_lint("反証 / 裏づけ。\n", rules=["ja-no-space-around-slash"])) == ["ja-no-space-around-slash"]
    assert _lint("a / b\n", rules=["ja-no-space-around-slash"]) == []
    mixed = "`a` を呼ぶ。`b`を呼ぶ。`c`を呼ぶ。\n"
    assert [f["line"] for f in _lint(mixed, rules=["ja-space-around-code"])] == [1]
    assert _lint("`a` を呼ぶ。`b` を呼ぶ。\n", rules=["ja-space-around-code"]) == []


def test_dropped_i_leaves_the_copula_negative_alone():
    assert _lint("それはわけでない。つもりでない。\n", rules=["no-dropped-i"]) == []


# ── 抑制コメントと --fix ─────────────────────────────────────────────────────
def test_disable_comments_follow_textlint_filter_rule_comments():
    body = (
        "<!-- textlint-disable no-exclamation-question-mark -->\n本当ですか？\n<!-- textlint-enable -->\n"
        "本当ですか？\n"
        "すごい！ <!-- ja-lint-disable-line -->\n"
        "<!-- textlint-disable-next-line no-exclamation-question-mark -->\nすごい！\n"
    )
    f = _lint(body, rules=["no-exclamation-question-mark"])
    assert [x["line"] for x in f] == [4]


def test_suppressed_findings_are_counted_in_the_report(tmp_path):
    body = {"a.md": "<!-- textlint-disable -->\nできないことはない。\n"}
    code, report = _run(tmp_path, None, body)
    assert code == 0 and report["summary"]["suppressed"] == 1 and report["findings"] == []


def test_a_marker_inside_a_code_span_is_documentation_not_a_directive():
    """`<!-- textlint-disable -->` と書いただけの行が、そこから下の検査を止めない。"""
    body = (
        "| japanese-lint | `<!-- textlint-disable -->` あり。 |\n"
        "本当ですか？\n"
        "すごい！ ``<!-- textlint-disable -->`` と二重の backtick でも同じ。\n"
        "本当ですか？\n"
    )
    assert jt.suppressions(body) == []
    assert [f["line"] for f in _lint(body, rules=["no-exclamation-question-mark"])] == [2, 3, 4]


def test_a_marker_inside_a_fenced_block_is_documentation_not_a_directive():
    body = (
        "```markdown\n<!-- textlint-disable -->\n```\n"
        "本当ですか？\n"
        "~~~\n<!-- textlint-disable -->\n~~~\n"
        "すごい！\n"
    )
    assert jt.suppressions(body) == []
    assert [f["line"] for f in _lint(body, rules=["no-exclamation-question-mark"])] == [4, 8]


def test_a_real_marker_still_suppresses_from_outside_a_code_span():
    body = (
        "`--fix` の話。\n"
        "<!-- textlint-disable no-exclamation-question-mark -->\n"
        "すごい！\n"
        "<!-- textlint-enable -->\n"
        "すごい！\n"
    )
    assert jt.suppressions(body) == [(2, 4, {"no-exclamation-question-mark"})]
    assert [f["line"] for f in _lint(body, rules=["no-exclamation-question-mark"])] == [5]


def test_an_unclosed_disable_still_suppresses_but_says_so(tmp_path):
    """本家と同じく末尾まで効かせる。ただし黙って効かせない——報告に1行出て、--strict では error。"""
    unclosed: list[int] = []
    assert jt.suppressions("<!-- textlint-disable -->\nすごい！\n", unclosed) == [(1, 3, None)]
    assert unclosed == [1]

    body = {"a.md": "# 見出し\n\n<!-- textlint-disable -->\nできないことはない。\n"}
    code, report = _run(tmp_path, None, body)
    assert code == 0
    assert report["summary"]["unclosed_disable"] == 1
    assert [u["line"] for u in report["unclosed_disable"]] == [3]
    assert report["unclosed_disable"][0]["file"].endswith("a.md")
    assert _run(tmp_path, None, body, extra=("--strict",))[0] == 1


def test_a_closed_disable_is_not_reported_as_unclosed(tmp_path):
    body = {"a.md": "<!-- textlint-disable -->\nできないことはない。\n<!-- textlint-enable -->\n"}
    code, report = _run(tmp_path, None, body)
    assert code == 0 and report["summary"]["unclosed_disable"] == 0
    assert _run(tmp_path, None, body, extra=("--strict",))[0] == 0


def test_the_shipped_engine_docs_document_the_marker_without_switching_the_linter_off():
    """SKILL.md が §2 の表の1行から下を黙って検査しなくなっていた歴史を固定する。

    `skills/engine/*.md` にはマーカーの書き方を説明する行がある（BRICKS.md の
    japanese-lint 行など）。それはコードスパンの中なので、抑制を1件も生まない。
    本物のマーカーを1つ足して閉じ忘れれば、そこから file の末尾まで同じことが起きる。
    だから閉じていない disable が1つも無いことも、ここで押さえる。"""
    engine = REPO_ROOT / "skills" / "engine"
    documented: list[str] = []
    for md in sorted(engine.glob("*.md")):
        lines = md.read_text(encoding="utf-8").split("\n")
        masked = jt._mask_code(md.read_text(encoding="utf-8")).split("\n")
        unclosed: list[int] = []
        starts = {a for a, _b, _n in jt.suppressions(md.read_text(encoding="utf-8"), unclosed)}
        assert unclosed == [], (
            f"{md.name}: 閉じていない <!-- textlint-disable --> が {unclosed} 行目にある。"
            "そこから file の末尾まで検査が止まる——閉じるか、行単位の抑制にすること")
        for no, raw in enumerate(lines, start=1):
            if jt.RE_DISABLE.search(raw) and not jt.RE_DISABLE.search(masked[no - 1]):
                documented.append(f"{md.name}:{no}")
                assert no not in starts and no + 1 not in starts, (
                    f"{md.name}:{no}: コードスパンの中のマーカーが抑制を作っている")
    assert any(d.startswith("BRICKS.md:") for d in documented), (
        "BRICKS.md が japanese-lint 行でマーカーの書き方を説明しているはず: " + repr(documented))


def test_a_four_backtick_fence_still_closes_on_the_inner_three_backticks():
    """段落分けの `RE_FENCE` は、マスク用の `RE_MASK_FENCE` とは別の規則のまま。

    片方の名前をもう片方に付け直すと、この入力が丸ごとコードになり 3 行目の所見が消える。
    `parse_document` は開いた印を ``` 3 文字に丸めるので、```` の中の ``` で閉じる。"""
    body = "````\n```\nすごい！\n````\n"
    f = _lint(body, rules=["no-exclamation-question-mark"])
    assert [(x["line"], x["column"]) for x in f] == [(3, 4)]


def test_the_report_says_on_stdout_which_disable_was_never_closed(tmp_path, capsys):
    """閉じていない disable は報告に1行出る。数だけ JSON に入れて黙るのでは足りない。"""
    body = {"a.md": "# 見出し\n\n<!-- textlint-disable -->\nできないことはない。\n"}
    code, _ = _run(tmp_path, None, body)
    out = capsys.readouterr().out
    assert code == 0
    note = [ln for ln in out.splitlines() if ln.startswith("注記:")]
    assert len(note) == 1, out
    assert "a.md:3:" in note[0] and "textlint-disable" in note[0]


def test_masking_a_fence_keeps_later_line_numbers_put():
    """`_mask_code` は行を落とさず空白に潰す。潰し方を変えると以降の行番号がずれる。"""
    body = ("```\n<!-- textlint-disable X -->\n```\n"
            "<!-- textlint-disable X -->\n本文。\n<!-- textlint-enable -->\n")
    assert jt.suppressions(body) == [(4, 6, {"X"})]


def test_an_indented_fence_is_masked_the_way_the_parser_reads_it():
    """字下げしたフェンスもコード。`parse_document` がそう読む以上、マスクも同じに読む。

    リストの中にフェンスを字下げして書く形は shipped の
    `facets/instructions/persona-gen.md` にある。マスクだけ字下げを認めないと、そこに
    書いたマーカーが指示として生き、下の全部を抑制した。"""
    body = "- 項目\n\n    ```\n    <!-- textlint-disable -->\n    ```\n\nすごい！\n"
    assert jt.suppressions(body) == []
    assert [f["line"] for f in _lint(body, rules=["no-exclamation-question-mark"])] == [7]


def test_a_nested_fence_does_not_close_on_the_shorter_inner_one():
    """```` は ``` では閉じない（CommonMark と同じ：同じ文字で同じ長さ以上だけが閉じる）。

    開いた印の長さを捨てていたので、外側が ```` のとき内側の ``` で閉じたことになり、
    コードブロックの中のマーカーが指示として生き返っていた。この run が直した defect が
    入れ子のフェンスでだけ残っていた形。`parse_document` 経由ではなくマスクを直接見る。"""
    body = "````markdown\n```\n<!-- textlint-disable -->\n```\n````\n本当ですか？\n"
    assert jt.suppressions(body) == []
    assert [f["line"] for f in _lint(body, rules=["no-exclamation-question-mark"])] == [6]


def test_a_marker_in_a_four_space_block_without_a_fence_is_still_read_as_a_directive():
    """残る限界を名前で残す：フェンスの無い 4 スペース字下げは本文として読む。

    字下げだけでコードと決めると、リストの継続行（同じ字下げ）に置いた本物のマーカーが
    黙って効かなくなる。害の向きを選んでいる。囲むならフェンスを使うこと。"""
    body = "段落。\n\n    <!-- textlint-disable -->\n\nすごい！\n"
    unclosed: list[int] = []
    assert jt.suppressions(body, unclosed) == [(3, 6, None)]
    assert unclosed == [3]
    assert _lint(body, rules=["no-exclamation-question-mark"]) == []


def test_an_unclosed_fence_is_announced_because_it_swallows_the_markers_below(tmp_path, capsys):
    """閉じていないフェンスは、そこから末尾までのマーカーを黙って読まなくする。

    error にはしない（本文はコードとして読まれているだけで、誤りとは限らない）。
    黙らせないために報告に1行出す。"""
    body = "```\ncode\n\n<!-- ja-lint-disable-line X -->\nすごい！\n"
    fences: list[int] = []
    assert jt.suppressions(body, None, fences) == []
    assert fences == [1]

    code, report = _run(tmp_path, None, {"a.md": body})
    out = capsys.readouterr().out
    assert report["summary"]["unclosed_fence"] == 1
    assert [u["line"] for u in report["unclosed_fence"]] == [1]
    note = [ln for ln in out.splitlines() if ln.startswith("注記:") and "フェンス" in ln]
    assert len(note) == 1 and "a.md:1:" in note[0], out
    # 閉じていないフェンスの下は、段落分けもマスクもコードとして読む——だから ！ すら
    # 報告されない。exit code は動かさず、注記だけが「読んでいない」と言う。
    assert code == 0 and report["summary"]["errors"] == 0
    assert _run(tmp_path, None, {"b.md": body}, extra=("--strict",))[0] == 0


def test_fix_applies_only_mechanical_replacements_and_leaves_the_rest(tmp_path):
    art = tmp_path / "art"
    art.mkdir()
    doc = art / "a.md"
    doc.write_text("ﾃｷｽﾄを`code`で確認して下さい。Ｖ２です。できないことはない。\n", encoding="utf-8")
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"presets": ["technical", "hiragana", "style"]}), encoding="utf-8")
    report = tmp_path / "r.json"
    code = jt.main(["--config", str(cfg), "--fix", "--report", str(report), str(art)])
    assert code == 1  # 二重否定は機械では直さない
    assert doc.read_text(encoding="utf-8") == "テキストを`code`で確認してください。V2です。できないことはない。\n"
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["summary"]["fixed"] == 3
    assert [f["rule"] for f in data["findings"] if f["severity"] == "error"] == ["no-double-negative-ja"]


def test_fix_never_touches_text_that_moved():
    """所見の位置の文字が text と違えば触らない。ずれたまま置き換えるより残すほうが安全。"""
    src = "ﾃｷｽﾄです。\n"
    findings = _lint(src, rules=["no-hankaku-kana"])
    findings[0]["column"] = 3
    fixed, n = jt.apply_fixes(src, findings)
    assert (fixed, n) == (src, 0)


# ── 変更行だけを見る（gate と hook の土台） ───────────────────────────────────
def _scratch_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@test.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "old.md").write_text("元からある。できないことはない。\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)
    return repo


def test_added_lines_are_read_from_a_unified_diff():
    diff = ("diff --git a/a.md b/a.md\n--- a/a.md\n+++ b/a.md\n@@ -1,2 +1,3 @@\n 一\n+二\n 三\n"
            "@@ -10 +11,2 @@\n+十一\n+十二\n"
            "diff --git a/b.md b/b.md\n--- /dev/null\n+++ b/b.md\n@@ -0,0 +1 @@\n+新\n")
    assert jt.added_lines_from_diff(diff) == {"a.md": {2, 11, 12}, "b.md": {1}}


def test_staged_mode_lints_only_the_added_japanese_lines(tmp_path):
    repo = _scratch_repo(tmp_path)
    (repo / "old.md").write_text("元からある。できないことはない。\n追記は問題ない。\n", encoding="utf-8")
    (repo / "new.md").write_text("まず最初に確認する。\n", encoding="utf-8")
    (repo / "en.md").write_text("English only, can't not be fine.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    files, findings = jt.lint_changed(str(repo), jt.Settings(None), staged=True)
    assert files == ["new.md", "old.md"]          # en.md adds no Japanese; old.md's error is pre-existing
    assert [(f["file"], f["line"], f["rule"]) for f in findings] == [("new.md", 1, "ja-no-redundant-expression")]


def test_changed_mode_includes_untracked_japanese_files(tmp_path):
    repo = _scratch_repo(tmp_path)
    (repo / "draft.md").write_text("この機能は利用することができます。\n", encoding="utf-8")
    files, findings = jt.lint_changed(str(repo), jt.Settings(None), base="HEAD")
    assert files == ["draft.md"] and _rules(findings) == ["ja-no-redundant-expression"]


def test_staged_cli_with_nothing_japanese_is_a_pass_not_unchecked(tmp_path, monkeypatch):
    repo = _scratch_repo(tmp_path)
    (repo / "code.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    report = tmp_path / "r.json"
    assert jt.main(["--staged", "--report", str(report)]) == 0
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["status"] == "checked" and data["artifacts"] == [] and data["diff"]["mode"] == "staged"


def test_commit_and_conversation_presets_drop_the_document_shaped_rules():
    assert "ja-no-mixed-period" not in jt.PRESETS["commit"]
    assert "no-exclamation-question-mark" in jt.PRESETS["commit"]
    assert {"ja-no-mixed-period", "no-exclamation-question-mark"}.isdisjoint(jt.PRESETS["conversation"])
    assert _lint("件名だけの行\n", {"presets": ["commit"]}) == []
    assert _lint("本当ですか？\n", {"presets": ["conversation"]}) == []


# ── AI 臭の名指しブラックリスト（助言専用・gate にならない） ─────────────────
def _smell(text, config=None):
    cfg = {"presets": ["ai-smell"]}
    if config:
        cfg.update(config)
    return _lint(text, cfg)


def test_the_blacklist_reports_over_sprinkling_not_the_word_itself():
    """カタログ自身が「一律禁止にしない＝見るのはカテゴリの撒きすぎ」と書いている。"""
    assert _smell("この設計は本質的です。\n") == []
    f = _smell("多角的な視点が不可欠です。\n")
    assert [x["text"] for x in f] == ["多角的", "不可欠"]
    assert "この段落に 2 件" in f[0]["message"]
    # 段落が違えば密度ではない。
    assert _smell("多角的に見ます。\n\n設定が不可欠です。\n") == []


def test_categories_with_a_named_replacement_fire_on_a_single_hit():
    f = _smell("本稿では扱いません。\n")
    assert [x["text"] for x in f] == ["本稿"] and "書き換えの対象です" in f[0]["message"]
    assert [x["text"] for x in _smell("ケースバイケースです。\n")] == ["ケースバイケース"]


def test_the_blacklist_is_opt_in_and_silent_on_honest_prose():
    assert "ja-ai-smell-phrases" not in jt.PRESETS["technical"]
    assert "ja-ai-smell-phrases" not in {r for p in jt.DEFAULT_PRESETS for r in jt.PRESETS[p]}
    honest = ("再構築には数分から数十分かかります。データ量によって変わります。\n\n"
              "停止するとインデックスが壊れることがあります。再度コマンドを実行してください。\n")
    assert _smell(honest) == []


def test_the_blacklist_cannot_be_promoted_to_an_error():
    """§6-3: AI 臭の代理指標を gate にすると所見は減るのに人の判定が悪化する。"""
    assert jt.DEFAULT_SEVERITY["ja-ai-smell-phrases"] == "warning"
    assert "ja-ai-smell-phrases" in jt.ADVISORY_ONLY
    for value in ("error", {"severity": "error"}):
        with pytest.raises(jt.Unchecked, match="助言専用"):
            jt.Settings({"presets": ["ai-smell"], "rules": {"ja-ai-smell-phrases": value}})
    # 無効化と warning の明示は通る（黙らせる自由は残す）。
    assert jt.Settings({"presets": ["ai-smell"], "rules": {"ja-ai-smell-phrases": False}}).enabled == {}


def test_strict_does_not_count_advisory_warnings(tmp_path):
    code, report = _run(tmp_path, {"presets": ["ai-smell"]},
                        {"a.md": "多角的な視点が不可欠です。\n"}, extra=("--strict",))
    assert report["summary"]["warnings"] == 2
    assert code == 0, "助言専用の規則は --strict でも exit code を動かさない"
    # 近似規則は従来どおり --strict で数える。
    code, _ = _run(tmp_path, {"presets": ["technical"]}, {"a.md": "映画を見れた。\n"}, extra=("--strict",))
    assert code == 1


def test_allow_lets_a_project_keep_a_word():
    cfg = {"rules": {"ja-ai-smell-phrases": {"allow": ["不可欠"]}}}
    assert _smell("多角的な視点が不可欠です。\n", cfg) == []
