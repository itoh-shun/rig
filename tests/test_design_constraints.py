"""The design-constraints sensor: what it checks, and what it refuses to claim.

The policy this pins is `skills/engine/facets/policies/design-constraint-rules`.
Its load-bearing claim is not "constraints are enforced" but a *detection class*:
raw values, unknown token names, unknown components, and prohibited expressions
are checkable; untokenized prose is structurally outside and is left to a
reviewer. Several tests below exist to keep that boundary honest — they assert
that the sensor does **not** detect something, because a sensor that silently
grew a fourth capability would make the shipped ratio a lie.
"""

import json
import os
import pathlib
import random
import subprocess
import sys
import time
import unicodedata

import pytest

from rig_workbench import design_constraints as dc

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "scripts" / "check_design_constraints.py"
MANIFESTS = REPO_ROOT / "skills" / "engine" / "manifests"
TEMPLATE = MANIFESTS / "design-constraints.template.json"
SCHEMA = MANIFESTS / "design-constraints.schema.json"

FILLED = {
    # 説明文にわざと `[要記入]` を含める。生テキスト走査への回帰はこの1行が支えている。
    "_readme": "値を埋めるまで [要記入] が残る",
    "version": 1,
    "tokens": {
        "color": {"brand": "#0A84FF", "surface": "#FFFFFF"},
        "spacing": {"md": "16px"},
        "border": {"hairline": "1px solid #CCCCCC"},
        "font": {"body": "Inter"},
    },
    "components": ["Button", "Card"],
    "prohibited": [{"pattern": "こちらをクリック", "why": "リンク先が分からない (WCAG 2.4.4)"}],
}


def _write(tmp_path: pathlib.Path, constraints: dict | None, files: dict[str, str]):
    art = tmp_path / "art"
    art.mkdir(exist_ok=True)
    for name, body in files.items():
        (art / name).write_text(body, encoding="utf-8")
    cpath = tmp_path / "constraints.json"
    if constraints is not None:
        cpath.write_text(json.dumps(constraints, ensure_ascii=False), encoding="utf-8")
    return cpath, art


def _run(tmp_path, constraints, files, extra=()):
    cpath, art = _write(tmp_path, constraints, files)
    report = tmp_path / "report.json"
    argv = ["--constraints", str(cpath), "--report", str(report), *extra, str(art)]
    code = dc.main(argv)
    return code, json.loads(report.read_text(encoding="utf-8"))


# ── 正規化：攻撃者が表記を変えるだけで逃げられないこと ────────────────────────
@pytest.mark.parametrize(
    "written",
    ["#0A84FF", "#0a84ff", "#0A84FFFF", "rgb(10, 132, 255)", "rgba(10,132,255,1)",
     "rgb(10 132 255)"],
)
def test_a_declared_colour_is_still_declared_however_it_is_written(tmp_path, written):
    code, report = _run(tmp_path, FILLED, {"a.md": f"色は {written} を使う。\n"})
    assert report["status"] == "checked"
    assert report["violations"] == [], f"{written} が宣言済みと認識されなかった"
    assert code == 0


def test_the_three_digit_shorthand_expands(tmp_path):
    code, report = _run(tmp_path, FILLED, {"a.md": "背景は #FFF。\n"})
    assert report["violations"] == [] and code == 0


@pytest.mark.parametrize("written", ["16px", "16.0px"])
def test_a_declared_length_is_still_declared_however_it_is_written(tmp_path, written):
    _, report = _run(tmp_path, FILLED, {"a.md": f"余白は {written}。\n"})
    assert report["violations"] == []


def test_a_composite_token_declares_both_its_colour_and_its_length(tmp_path):
    """`1px solid #CCCCCC` は色と長さの両方を宣言する。

    片方だけ登録すると、宣言済みの値が違反として上がる。実際にそうなっていた。
    """
    _, report = _run(tmp_path, FILLED, {"a.md": "境界は 1px、色は #CCC。\n"})
    assert report["violations"] == []


# ── 検出：3つのクラス ─────────────────────────────────────────────────────────
def test_an_undeclared_colour_is_a_raw_value(tmp_path):
    code, report = _run(tmp_path, FILLED, {"a.md": "主色は #FF0000。\n"})
    assert code == 1
    assert [v["class"] for v in report["violations"]] == ["raw-value"]
    assert report["violations"][0]["line"] == 1


def test_a_token_name_that_does_not_exist_is_reported(tmp_path):
    _, report = _run(tmp_path, FILLED, {"a.md": "token(color.brand-secondary) を使う。\n"})
    assert [v["class"] for v in report["violations"]] == ["unknown-token"]


def test_the_css_variable_spelling_of_a_token_resolves_through_its_group(tmp_path):
    _, report = _run(tmp_path, FILLED, {"a.css": "a { color: var(--color-brand); }\n"})
    assert report["violations"] == []


def test_a_css_variable_that_does_not_exist_is_reported(tmp_path):
    _, report = _run(tmp_path, FILLED, {"a.css": "a { margin: var(--spacing-xl); }\n"})
    assert [v["class"] for v in report["violations"]] == ["unknown-token"]


def test_a_component_outside_the_inventory_is_reported(tmp_path):
    _, report = _run(tmp_path, FILLED, {"a.md": "<Modal> を開く。\n"})
    assert [v["class"] for v in report["violations"]] == ["unknown-component"]


def test_a_prohibited_expression_is_reported_with_its_reason(tmp_path):
    _, report = _run(tmp_path, FILLED, {"a.md": "ボタンは「こちらをクリック」。\n"})
    assert [v["class"] for v in report["violations"]] == ["prohibited-expression"]
    assert "2.4.4" in report["violations"][0]["detail"]


# ── 主張しないこと：この4つは「捕れない」を固定する ──────────────────────────
def test_untokenized_prose_is_not_detected(tmp_path):
    """検出クラスの外。ポリシーが「捕れない」と宣言している唯一のクラス。

    ここが緑になったら、センサーが黙って能力を増やしたか、偶然拾ったかのどちらか。
    どちらでも出荷している比率が嘘になるので、失敗させる。
    """
    _, report = _run(tmp_path, FILLED, {"a.md": "見出しはブランドの青で、少し大きめにする。\n"})
    assert report["violations"] == []


def test_a_mention_is_reported_the_same_as_a_use(tmp_path):
    """センサーは向きを読まない。過去の値への言及も未一致として上げる。

    これは偽陽性ではなく設計。用法と言及の判定は reviewer の仕事で、
    センサーに「賢く無視」させると、無視すべきでないものを誰も見なくなる。
    """
    _, report = _run(tmp_path, FILLED, {"a.md": "以前の #FF0000 から移行済み。\n"})
    assert [v["class"] for v in report["violations"]] == ["raw-value"]


def test_a_font_named_only_in_prose_is_not_detected(tmp_path):
    """フォントは `font-family:` 宣言からしか読めない。散文中の書体名は読めない。"""
    _, report = _run(tmp_path, FILLED, {"a.md": "本文は Helvetica で組む。\n"})
    assert report["violations"] == []


def test_a_font_in_a_declaration_is_detected(tmp_path):
    _, report = _run(tmp_path, FILLED, {"a.css": "body { font-family: Helvetica; }\n"})
    assert [v["class"] for v in report["violations"]] == ["raw-value"]


# ── 偽陽性：正直な成果物を落とさない ─────────────────────────────────────────
def test_markdown_headings_and_anchors_are_not_colours(tmp_path):
    body = "# タイトル\n\n## 節\n\n詳細は [設定](#settings) と [FAQ](#faq-1) を参照。\n"
    _, report = _run(tmp_path, FILLED, {"a.md": body})
    assert report["violations"] == []


def test_the_token_table_the_policy_asks_for_does_not_trip_the_sensor(tmp_path):
    body = (
        "## 使用トークン\n"
        "| トークン | 値 |\n|---|---|\n"
        "| color.brand | #0A84FF |\n| spacing.md | 16px |\n\n"
        "主ボタンは <Button>、色は token(color.brand)、余白は token(spacing.md)。\n"
    )
    _, report = _run(tmp_path, FILLED, {"a.md": body})
    assert report["violations"] == []


# ── 三状態 ────────────────────────────────────────────────────────────────────
def test_a_declaration_that_is_still_the_blank_template_is_unchecked_not_passed(tmp_path):
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    code, report = _run(tmp_path, template, {"a.md": "何か\n"})
    assert code == 2
    assert report["status"] == "unchecked"
    assert "tokens.color.brand-primary" in report["reason"]


def test_the_readme_prose_quoting_the_placeholder_does_not_block_a_filled_file(tmp_path):
    """`_readme` は制約ではなく説明文で、雛形では本文中に `[要記入]` を引用している。

    生テキストを走査すると、値を全部埋めた利用者が説明文のせいで永久に未検査になる。
    """
    filled = dict(FILLED)
    filled["_readme"] = json.loads(TEMPLATE.read_text(encoding="utf-8"))["_readme"]
    assert dc.UNFILLED in filled["_readme"]
    code, report = _run(tmp_path, filled, {"a.md": "token(color.brand)\n"})
    assert report["status"] == "checked" and code == 0


def test_broken_json_is_unchecked(tmp_path):
    art = tmp_path / "art"
    art.mkdir()
    (art / "a.md").write_text("x\n", encoding="utf-8")
    cpath = tmp_path / "c.json"
    cpath.write_text("{ not json", encoding="utf-8")
    report = tmp_path / "r.json"
    code = dc.main(["--constraints", str(cpath), "--report", str(report), str(art)])
    assert code == 2
    assert json.loads(report.read_text(encoding="utf-8"))["status"] == "unchecked"


def test_no_declaration_at_all_is_not_configured_and_does_not_fail(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    art = tmp_path / "art"
    art.mkdir()
    (art / "a.md").write_text("色は #FF0000。\n", encoding="utf-8")
    report = tmp_path / "r.json"
    code = dc.main(["--if-configured", "--report", str(report), str(art)])
    assert code == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "not-configured"
    assert payload["violations"] == []


def test_no_declaration_without_the_flag_is_unchecked(tmp_path, monkeypatch):
    """既定の手動実行では、宣言が無いことは合格にならない（policy 規則 4）。"""
    monkeypatch.chdir(tmp_path)
    art = tmp_path / "art"
    art.mkdir()
    (art / "a.md").write_text("x\n", encoding="utf-8")
    report = tmp_path / "r.json"
    assert dc.main(["--report", str(report), str(art)]) == 2
    assert json.loads(report.read_text(encoding="utf-8"))["status"] == "unchecked"


def test_an_explicit_constraints_path_that_is_missing_is_a_typo_not_an_opt_out(tmp_path):
    art = tmp_path / "art"
    art.mkdir()
    (art / "a.md").write_text("x\n", encoding="utf-8")
    report = tmp_path / "r.json"
    code = dc.main(["--if-configured", "--constraints", str(tmp_path / "nope.json"),
                    "--report", str(report), str(art)])
    assert code == 2
    assert json.loads(report.read_text(encoding="utf-8"))["status"] == "unchecked"


# ── 報告そのもの ──────────────────────────────────────────────────────────────
def test_the_report_does_not_scan_itself(tmp_path):
    """報告は成果物ではない。走査すると前回の違反値を今回の違反として拾う。"""
    cpath, art = _write(tmp_path, FILLED, {"a.md": "色は #FF0000。\n"})
    report = art / "report.json"          # わざと成果物ディレクトリの中に置く
    args = ["--constraints", str(cpath), "--report", str(report), str(art)]
    assert dc.main(args) == 1
    first = json.loads(report.read_text(encoding="utf-8"))["violations"]
    assert dc.main(args) == 1
    second = json.loads(report.read_text(encoding="utf-8"))["violations"]
    assert first == second, "2回目が自分の報告を読んで違反を増やした"


def test_the_report_records_which_declaration_was_measured(tmp_path):
    _, report = _run(tmp_path, FILLED, {"a.md": "x\n"})
    assert len(report["constraints_sha256"]) == 64
    assert "検出クラスの外" in report["scope"]


def test_every_status_writes_a_report(tmp_path, monkeypatch):
    """orchestrate の checks 実行系は stdout を捨てる。報告の本体はファイルである。"""
    monkeypatch.chdir(tmp_path)
    art = tmp_path / "art"
    art.mkdir()
    (art / "a.md").write_text("色は #FF0000。\n", encoding="utf-8")
    for extra, expected in ((["--if-configured"], "not-configured"), ([], "unchecked")):
        report = tmp_path / f"r-{expected}.json"
        dc.main([*extra, "--report", str(report), str(art)])
        assert json.loads(report.read_text(encoding="utf-8"))["status"] == expected


# ── 出荷物 ────────────────────────────────────────────────────────────────────
def test_the_schema_and_the_template_ship_together():
    assert SCHEMA.is_file() and TEMPLATE.is_file()
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    assert set(schema["required"]) <= set(template)
    assert set(template) <= set(schema["properties"]), "雛形にスキーマ外のキーがある"


def test_the_shipped_template_carries_no_palette_of_ours():
    """rig は雛形しか出荷しない。値はプロジェクトが所有する（policy 規則 2）。"""
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    for group, entries in template["tokens"].items():
        for name, value in entries.items():
            assert value == dc.UNFILLED, f"tokens.{group}.{name} に実値が入っている"


def test_the_packaged_data_includes_the_schema_and_template():
    """`manifests/*.md` だけを拾っていると、規則が指す形の定義が届かない。"""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"manifests/*.json",' in pyproject


def test_the_launcher_runs_from_a_checkout(tmp_path):
    art = tmp_path / "art"
    art.mkdir()
    (art / "a.md").write_text("x\n", encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(LAUNCHER), "--if-configured", str(art)],
        capture_output=True, text=True, cwd=tmp_path,
    )
    assert r.returncode == 0
    assert "未設定" in r.stdout


# ── レビューで出た欠陥の回帰 ────────────────────────────────────────────────
# 以下はすべて、4-way レビューが実際に再現させた欠陥に対応する。散文で塞いだものは
# 1つも無い——「直したつもり」を残さないため、全部が入力から確かめている。

def test_a_misspelled_section_name_is_never_a_silent_pass(tmp_path):
    """`prohibited` を `prohibitted` と綴ると規則が丸ごと消え、緑になっていた。

    宣言はあるのに守られず、しかもゲートが合格を返す——このセンサーが潰すはずの状態そのもの。
    """
    broken = dict(FILLED)
    broken["prohibitted"] = broken.pop("prohibited")
    code, report = _run(tmp_path, broken, {"a.md": "ボタンは「こちらをクリック」。\n"})
    assert code == 2
    assert report["status"] == "unchecked"
    assert "prohibitted" in report["reason"]


def test_a_misspelled_rule_key_is_also_unchecked(tmp_path):
    broken = dict(FILLED)
    broken["prohibited"] = [{"pattern": "x", "why": "y", "regexp": True}]
    code, report = _run(tmp_path, broken, {"a.md": "x\n"})
    assert code == 2 and "regexp" in report["reason"]


def test_a_constraints_file_that_is_not_utf8_is_unchecked_and_still_reports(tmp_path):
    """デコード失敗を捕っておらず、traceback で落ちて報告が書かれなかった。"""
    art = tmp_path / "art"
    art.mkdir()
    (art / "a.md").write_text("x\n", encoding="utf-8")
    cpath = tmp_path / "c.json"
    cpath.write_bytes(json.dumps(FILLED, ensure_ascii=False).encode("cp932"))
    report = tmp_path / "r.json"
    assert dc.main(["--constraints", str(cpath), "--report", str(report), str(art)]) == 2
    assert report.is_file(), "報告ファイルが書かれていない"
    assert json.loads(report.read_text(encoding="utf-8"))["status"] == "unchecked"


def test_a_byte_order_mark_does_not_make_a_file_permanently_unchecked(tmp_path):
    art = tmp_path / "art"
    art.mkdir()
    (art / "a.md").write_text("token(color.brand)\n", encoding="utf-8")
    cpath = tmp_path / "c.json"
    cpath.write_bytes(b"\xef\xbb\xbf" + json.dumps(FILLED, ensure_ascii=False).encode("utf-8"))
    report = tmp_path / "r.json"
    assert dc.main(["--constraints", str(cpath), "--report", str(report), str(art)]) == 0


@pytest.mark.parametrize("written", ["background: white", "color: #FFFFFF", "color: #fff"])
def test_a_colour_declared_by_name_is_recognised_however_the_artefact_spells_it(tmp_path, written):
    """宣言側に正規化が当たっておらず、`white` の宣言がフォント扱いになっていた。

    その結果、自分で宣言した色が違反として上がっていた。
    """
    declared = {"version": 1, "tokens": {"color": {"surface": "white"}}}
    _, report = _run(tmp_path, declared, {"a.css": f"a {{ {written}; }}\n"})
    assert report["violations"] == []


def test_a_declaration_written_in_full_width_still_resolves(tmp_path):
    declared = {"version": 1, "tokens": {"color": {"brand": "＃0A84FF"}}}
    _, report = _run(tmp_path, declared, {"a.css": "a { color: #0a84ff; }\n"})
    assert report["violations"] == []


@pytest.mark.parametrize("text", ["fixes #123", "closes #1234"])
def test_a_short_issue_reference_is_not_a_colour(tmp_path, text):
    """数字だけの3桁・4桁は CSS 宣言の中でだけ色と見なす。`#123` は毎日のコミット文に出る。"""
    _, report = _run(tmp_path, FILLED, {"a.md": f"{text}\n"})
    assert report["violations"] == []


def test_a_six_digit_all_number_hex_is_a_colour_even_in_prose(tmp_path):
    """使用トークン表の `| navy | #003366 |` を落とすほうが、Issue 番号の誤検出より害が大きい。

    6桁を宣言の中に限っていたとき、policy 規則 6 が「読める」と言った表の値が落ちていた。
    """
    _, report = _run(tmp_path, FILLED, {"a.md": "| navy | #003366 |\n"})
    assert [v["class"] for v in report["violations"]] == ["raw-value"]


def test_a_short_hex_with_letters_is_still_a_colour_in_prose(tmp_path):
    _, report = _run(tmp_path, FILLED, {"a.md": "主色は #f00 を使う。\n"})
    assert [v["class"] for v in report["violations"]] == ["raw-value"]


def test_an_all_digit_hex_inside_a_declaration_is_a_colour(tmp_path):
    _, report = _run(tmp_path, FILLED, {"a.css": "a { color: #123456; }\n"})
    assert [v["class"] for v in report["violations"]] == ["raw-value"]


def test_the_camel_case_spelling_of_a_css_property_is_the_same_property(tmp_path):
    """`style={{ fontFamily: "Comic Sans MS" }}` が丸ごとすり抜けていた。"""
    _, report = _run(tmp_path, FILLED, {"a.jsx": 'const s = { fontFamily: "Comic Sans MS" };\n'})
    assert [v["class"] for v in report["violations"]] == ["raw-value"]


@pytest.mark.parametrize(
    "line",
    ["const m: Map<String, Int> = new Map();",
     "function f<T>(x: T): T { return x; }",
     "type P = Array<Item>;",
     "const pick = <T,>(xs: T[]) => xs[0];"],
)
def test_a_type_argument_is_not_a_component(tmp_path, line):
    """TS の型引数を JSX 要素と読み違えると、TS プロジェクト全体が偽陽性で埋まる。"""
    _, report = _run(tmp_path, FILLED, {"a.tsx": line + "\n"})
    assert report["violations"] == []


def test_a_plain_ts_file_is_never_checked_for_components(tmp_path):
    """`.ts` に JSX は書けない。`<Foo>bar` は必ず型アサーションである。"""
    _, report = _run(tmp_path, FILLED, {"a.ts": "let x = <Foo>bar;\n"})
    assert report["violations"] == []


def test_a_symlinked_artefact_is_unchecked(tmp_path):
    """`docs/design/x.json -> ~/.config/…` の中身が報告に載っていた。"""
    art = tmp_path / "art"
    art.mkdir()
    (tmp_path / "outside.md").write_text("色は #DEADBE。\n", encoding="utf-8")
    (art / "link.md").symlink_to(tmp_path / "outside.md")
    report = tmp_path / "r.json"
    cpath = tmp_path / "c.json"
    cpath.write_text(json.dumps(FILLED, ensure_ascii=False), encoding="utf-8")
    (art / "real.md").write_text("token(color.brand)\n", encoding="utf-8")
    code = dc.main(["--constraints", str(cpath), "--report", str(report), str(art)])
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert code == 0
    assert payload["violations"] == [], "シンボリックリンクの先を読んでいる"
    assert [s["why"] for s in payload["skipped"]] == ["symlink"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root は権限で弾かれない")
def test_a_directory_that_cannot_be_read_is_unchecked_not_clean(tmp_path):
    """飛ばすと、中を一度も見ていないのに『違反 0 件』になる。"""
    art = tmp_path / "art"
    (art / "sub").mkdir(parents=True)
    (art / "ok.md").write_text("x\n", encoding="utf-8")
    (art / "sub" / "hidden.md").write_text("色は #FF0000。\n", encoding="utf-8")
    (art / "sub").chmod(0o000)
    try:
        cpath = tmp_path / "c.json"
        cpath.write_text(json.dumps(FILLED, ensure_ascii=False), encoding="utf-8")
        report = tmp_path / "r.json"
        assert dc.main(["--constraints", str(cpath), "--report", str(report), str(art)]) == 2
        assert json.loads(report.read_text(encoding="utf-8"))["status"] == "unchecked"
    finally:
        (art / "sub").chmod(0o755)


def test_a_catastrophic_pattern_is_bounded_by_the_wall_clock(tmp_path):
    """破滅的バックトラックは1回の `re.search` が返ってこない。

    プロセス内で経過時間を測っても止められないので、別プロセスに出して打ち切る。
    **返ってこないゲートは、通らないのではなく何も守っていない。**
    """
    declared = dict(FILLED)
    declared["prohibited"] = [{"pattern": "^(a+)+$", "why": "破滅的", "regex": True}]
    started = time.monotonic()
    code, report = _run(tmp_path, declared, {"a.md": "a" * 40 + "b\n"})
    elapsed = time.monotonic() - started
    assert code == 2
    assert report["status"] == "unchecked"
    assert elapsed < dc.REGEX_TIME_BUDGET * 4, f"打ち切れていない ({elapsed:.1f}s)"


def test_a_regular_expression_sees_the_same_normalised_text_as_a_literal(tmp_path):
    """生テキストに当てていたため、ゼロ幅の回避が正規表現指定にだけ効かなかった。

    同じ禁止表現が、書き方によって検出されたりされなかったりする状態だった。
    """
    declared = dict(FILLED)
    declared["prohibited"] = [{"pattern": "こちらをクリック", "why": "x", "regex": True}]
    _, report = _run(tmp_path, declared, {"a.md": "ボタンは「こちらを​クリック」。\n"})
    assert [v["class"] for v in report["violations"]] == ["prohibited-expression"]


def test_the_report_records_which_checks_the_declaration_left_out(tmp_path):
    """policy の「省いたことを報告に書く」を、人の記憶ではなく機械で満たす。"""
    minimal = {"version": 1, "tokens": {"color": {"brand": "#0A84FF"}}}
    _, report = _run(tmp_path, minimal, {"a.md": "x\n"})
    assert report["not_declared"] == ["components", "prohibited"]
    _, full = _run(tmp_path, FILLED, {"a.md": "x\n"})
    assert full["not_declared"] == []


def test_an_empty_component_inventory_permits_nothing(tmp_path):
    """空配列は「検査しない」ではなく「1つも許可しない」（policy「やらないこと」）。"""
    declared = dict(FILLED)
    declared["components"] = []
    _, report = _run(tmp_path, declared, {"a.jsx": "<Button />\n"})
    assert [v["class"] for v in report["violations"]] == ["unknown-component"]


def test_omitting_the_inventory_turns_the_check_off(tmp_path):
    declared = {k: v for k, v in FILLED.items() if k != "components"}
    _, report = _run(tmp_path, declared, {"a.jsx": "<Whatever />\n"})
    assert report["violations"] == []


def test_the_verdict_contract_carries_the_constraint_section():
    """契約に節を足したなら、節が消えたことに気づける場所が要る。"""
    contract = (
        REPO_ROOT / "skills" / "engine" / "facets" / "output-contracts" / "design-verdict.md"
    ).read_text(encoding="utf-8")
    assert "制約 所見:" in contract
    assert "状態: <checked|unchecked|not-configured>" in contract
    for word in ("checked", "unchecked", "not-configured"):
        assert word in contract


def test_the_sensor_is_reachable_as_a_subcommand():
    """instruction が指す起動経路が、実際に存在すること。"""
    cli = (REPO_ROOT / "rig_workbench" / "cli.py").read_text(encoding="utf-8")
    assert 'if sub == "design-constraints":' in cli
    vet = (
        REPO_ROOT / "skills" / "engine" / "facets" / "instructions" / "design-vet.md"
    ).read_text(encoding="utf-8")
    assert "rig-wb design-constraints" in vet


# ── finding-verifier が反証した箇所の回帰 ──────────────────────────────────
# 「直した」と書いた主張を、別の主体が入力で崩した。以下はその入力そのもの。

@pytest.mark.parametrize(
    "declared,artefact",
    [({"border": {"focus": "2px solid red"}}, "a { border: 2px solid red; }"),
     ({"type": {"body": "16px/1.5 Inter, sans-serif"}},
      "b { font: 16px/1.5 Inter, sans-serif; }"),
     ({"color": {"surface": "white"}}, "c { background: white; }"),
     ({"font": {"body": "Noto Sans JP"}}, 'd { font-family: "Noto Sans JP"; }')],
)
def test_a_composite_declaration_declares_every_part_of_itself(tmp_path, declared, artefact):
    """宣言側が成果物側と同じ抽出器を通っていなかった。

    `2px solid red` の `red` と `16px/1.5 Inter, …` の `Inter` が宣言から落ち、
    それを使った成果物が、**満たしているはずの制約に違反している**と報告されていた。
    """
    _, report = _run(tmp_path, {"version": 1, "tokens": declared}, {"a.css": artefact + "\n"})
    assert report["violations"] == []


@pytest.mark.parametrize("key,value", [("case_sensitive", "false"), ("regex", "true"),
                                       ("regex", 1), ("case_sensitive", None)])
def test_a_boolean_rule_flag_that_is_not_a_boolean_is_unchecked(tmp_path, key, value):
    """`"case_sensitive": "false"` は真になり、規則が意図と逆に働いていた。

    宣言と挙動が食い違ったまま `checked` が返るのは、未知キーの素通りと同じ穴である。
    """
    declared = dict(FILLED)
    declared["prohibited"] = [{"pattern": "Click Here", "why": "x", key: value}]
    code, report = _run(tmp_path, declared, {"a.md": "Click here for details.\n"})
    assert code == 2 and report["status"] == "unchecked"
    assert key in report["reason"]


def test_a_generic_constraint_is_not_a_component(tmp_path):
    """`<T extends unknown>` は識別子の直後ではないので、直前の文字だけでは分けられない。"""
    _, report = _run(
        tmp_path, FILLED, {"a.tsx": "const identity = <T extends unknown>(x: T) => x;\n"}
    )
    assert report["violations"] == []


@pytest.mark.parametrize(
    "line,expected",
    [("a { color: #DE​ADBE; }", ["raw-value"]),
     ("token(color.​nope)", ["unknown-token"])],
)
def test_a_zero_width_character_does_not_hide_a_reference(tmp_path, line, expected):
    """ゼロ幅の除去が禁止表現の経路にしか無く、値とトークン参照は素通りしていた。

    参照が見えなくなるのは、違反が「無い」のではなく「検査されていない」状態である。
    """
    _, report = _run(tmp_path, FILLED, {"a.css": line + "\n"})
    assert [v["class"] for v in report["violations"]] == expected


def test_an_unexpected_exception_still_leaves_a_report(tmp_path, monkeypatch):
    """報告を書かずに traceback で終わると、『検査していない』ことすら残らない。"""
    declared = dict(FILLED)
    declared["prohibited"] = [{"pattern": "x+", "why": "y", "regex": True}]
    monkeypatch.setattr(sys, "executable", None)
    code, report = _run(tmp_path, declared, {"a.md": "xxx\n"})
    assert code == 2
    assert report["status"] == "unchecked"
    assert "想定外の例外" in report["reason"]


# ── verifier 2周目が反証した箇所の回帰 ────────────────────────────────────
# 1周目で直したはずのゼロ幅対策が、列挙にしていたせいで別の文字で破られた。
# 「列挙は追随できない」は、この節の存在理由そのものである。

@pytest.mark.parametrize(
    "invisible,name",
    [("­", "SOFT HYPHEN"), ("​", "ZWSP"), ("‌", "ZWNJ"),
     ("‎", "LRM"), ("⁠", "WORD JOINER"), ("﻿", "BOM")],
)
def test_no_format_character_can_hide_a_value(tmp_path, invisible, name):
    """書式制御文字（カテゴリ `Cf`）を1つ挟むだけで、すべて消えて緑になっていた。

    最初はゼロ幅5文字を列挙して直したつもりでいた。U+00AD で同じ穴が開いた。
    """
    body = f"a {{ color: #FF{invisible}0000; }}\n"
    code, report = _run(tmp_path, FILLED, {"a.css": body})
    assert code == 1, f"{name} で色が消えた"
    assert [v["class"] for v in report["violations"]] == ["raw-value"]


@pytest.mark.parametrize("invisible", ["­", "​"])
def test_a_case_sensitive_rule_gets_the_same_normalisation(tmp_path, invisible):
    """`case_sensitive: true` だけが生テキストに当たっており、正規化が届いていなかった。

    同じ禁止表現が、指定の仕方で検出されたりされなかったりする状態だった。
    """
    declared = dict(FILLED)
    declared["prohibited"] = [{"pattern": "Click Here", "why": "x", "case_sensitive": True}]
    _, report = _run(tmp_path, declared, {"a.md": f"Click{invisible} Here\n"})
    assert [v["class"] for v in report["violations"]] == ["prohibited-expression"]


def test_a_case_sensitive_rule_still_distinguishes_case(tmp_path):
    """正規化を通しても、`case_sensitive: true` の意味は残っていること。"""
    declared = dict(FILLED)
    declared["prohibited"] = [{"pattern": "Click Here", "why": "x", "case_sensitive": True}]
    _, report = _run(tmp_path, declared, {"a.md": "click here\n"})
    assert report["violations"] == []


@pytest.mark.parametrize(
    "value,expected_font",
    [("Black Han Sans, sans-serif", "black han sans"),
     ("Coral Pro", "coral pro"),
     ("Noto Sans JP", "noto sans jp")],
)
def test_a_typeface_whose_name_contains_a_colour_word_is_still_a_typeface(
    tmp_path, value, expected_font
):
    """合成した宣言文が嘘をつく側で崩れていた。

    `Black Han Sans, sans-serif` から `black` を色として拾い、宣言した書体を捨てていた。
    宣言した書体が違反になり、同時に**誰も宣言していない `#000000` が黙って通る**。
    """
    colors, lengths, font = dc.classify_declared(value)
    assert font == expected_font
    assert colors == [] and lengths == []


def test_a_declared_typeface_with_a_colour_word_does_not_declare_that_colour(tmp_path):
    declared = {"version": 1, "tokens": {"font": {"display": "Black Han Sans, sans-serif"}}}
    _, report = _run(tmp_path, declared,
                     {"a.css": 'h1 { font-family: "Black Han Sans", sans-serif; }\n'
                               "p { color: #000000; }\n"})
    assert [v["value"] for v in report["violations"]] == ["#000000"]


@pytest.mark.parametrize(
    "markup",
    ['<div style="color: red">x</div>', '<p style="background: dodgerblue">y</p>'],
)
def test_a_declaration_inside_a_style_attribute_is_a_declaration(tmp_path, markup):
    """`RE_DECL` が直前に `^;{,` を要求しており、属性内の先頭宣言に当たらなかった。

    policy は「CSS 宣言の中でだけ読む」と書いていて、実装のほうが狭かった。
    """
    _, report = _run(tmp_path, FILLED, {"a.html": markup + "\n"})
    assert [v["class"] for v in report["violations"]] == ["raw-value"]


def test_a_boolean_version_is_not_version_one(tmp_path):
    """`True == 1` なので、`"version": true` が通っていた。"""
    declared = dict(FILLED)
    declared["version"] = True
    code, report = _run(tmp_path, declared, {"a.md": "x\n"})
    assert code == 2 and "version" in report["reason"]


def test_an_empty_inventory_is_declared_not_omitted(tmp_path):
    """空配列は「1つも許可しない」という宣言であって、省略ではない（policy 規則）。"""
    declared = dict(FILLED)
    declared["components"] = []
    _, report = _run(tmp_path, declared, {"a.md": "x\n"})
    assert "components" not in report["not_declared"]


def test_a_report_that_cannot_be_written_is_not_a_pass(tmp_path):
    """報告が残せないなら、何が起きたか追跡できない。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("", encoding="utf-8")
    art = tmp_path / "art"
    art.mkdir()
    (art / "a.md").write_text("x\n", encoding="utf-8")
    cpath = tmp_path / "c.json"
    cpath.write_text(json.dumps(FILLED, ensure_ascii=False), encoding="utf-8")
    code = dc.main(["--constraints", str(cpath), "--report", str(blocker / "r.json"), str(art)])
    assert code == 2


# ── verifier 3周目が反証した箇所の回帰 ────────────────────────────────────
# 「列挙は追随できない」と書きながらカテゴリ1個の列挙に置き換えていた。
# **カテゴリ列挙も列挙である。** 3周連続で同じ穴が開いたので、Unicode が定めた
# `Default_Ignorable_Code_Point` を使うようにした。

@pytest.mark.parametrize(
    "invisible,name,category",
    [("­", "SOFT HYPHEN", "Cf"), ("​", "ZWSP", "Cf"),
     ("ㅤ", "HANGUL FILLER", "Lo"), ("️", "VARIATION SELECTOR-16", "Mn"),
     ("ᅟ", "HANGUL CHOSEONG FILLER", "Lo"), ("ﾠ", "HALFWIDTH HANGUL FILLER", "Lo"),
     ("͏", "COMBINING GRAPHEME JOINER", "Mn"), ("᠎", "MONGOLIAN VOWEL SEPARATOR", "Cf")],
)
def test_no_ignorable_code_point_can_hide_a_value(tmp_path, invisible, name, category):
    """`Cf` だけを落としていたとき、`Lo` の HANGUL FILLER と `Mn` の異体字セレクタが通った。

    どちらも描画されない。カテゴリでは「見えない」を言い当てられない。
    """
    assert unicodedata.category(invisible) == category, f"{name} の分類が変わった"
    code, report = _run(tmp_path, FILLED, {"a.css": f"a {{ color: #FF{invisible}0000; }}\n"})
    assert code == 1, f"{name} ({category}) で色が消えた"
    assert [v["class"] for v in report["violations"]] == ["raw-value"]


@pytest.mark.parametrize("invisible", ["ㅤ", "️", "­"])
def test_no_ignorable_code_point_can_hide_a_prohibited_phrase(tmp_path, invisible):
    _, report = _run(tmp_path, FILLED, {"a.md": f"「こちらを{invisible}クリック」\n"})
    assert [v["class"] for v in report["violations"]] == ["prohibited-expression"]


def test_a_decomposed_prohibited_phrase_still_matches(tmp_path):
    """NFKC を1文字ずつ当てていたため、NFD で分解された語が合成されなかった。

    `fold` は文字列全体、`_flatten` は1文字ずつ——**正規化が2種類あった**。
    """
    declared = dict(FILLED)
    declared["prohibited"] = [{"pattern": "café", "why": "x"}]
    body = unicodedata.normalize("NFD", "ここで café と書く\n")
    _, report = _run(tmp_path, declared, {"a.md": body})
    assert [v["class"] for v in report["violations"]] == ["prohibited-expression"]


def test_the_line_number_survives_a_flood_of_invisible_characters(tmp_path):
    body = "​" * 200 + "\n" + "ㅤ" * 200 + "\n" + "「こちらを­クリック」\n"
    _, report = _run(tmp_path, FILLED, {"a.md": body})
    assert [v["line"] for v in report["violations"]] == [3]


def test_a_font_shorthand_with_a_size_is_not_read_as_a_colour(tmp_path):
    """「長さがあるか」では分けられない——`700 24px/1.2 Black Han Sans` も長さを含む。

    枠線ショートハンドの線種キーワードで分ける。
    """
    colors, lengths, font = dc.classify_declared("700 24px/1.2 Black Han Sans, sans-serif")
    assert font == "black han sans"
    assert colors == [] and lengths == ["24px"]


@pytest.mark.parametrize(
    "value,expected",
    # `2px solid red` は書体としても登録される。プロパティ名の無い値で枠線と書体を
    # 語彙で分けようとしたら実在書体 `Solid Grotesk` が宣言から丸ごと落ちたので、
    # **曖昧なら書体側にも入れる**ことにした。書体名の過剰宣言はその名前1つにしか
    # 波及しないが、過少宣言は宣言した値そのものを違反として上げる。
    [("2px solid red", (["#ff0000"], ["2px"], "solid red")),
     ("1px solid #CCC", (["#cccccc"], ["1px"], None)),
     ("2px red", (["#ff0000"], ["2px"], None)),
     ("16px/1.5 Inter, sans-serif", ([], ["16px"], "inter"))],
)
def test_the_shorthand_shapes_each_declare_what_they_contain(value, expected):
    assert dc.classify_declared(value) == expected


def test_a_percentage_alpha_is_the_same_colour_as_a_decimal_one(tmp_path):
    """`/ 50%` を不透明として扱っており、`, 0.5` と同じ色が一致しなかった。"""
    assert dc.colors_in("color: rgb(255 0 0 / 50%);") == \
           dc.colors_in("color: rgba(255,0,0,0.5);")


# ── 4周目の検証で崩れた3点 ────────────────────────────────────────────────
@pytest.mark.parametrize(
    "half,full",
    [("ﾀﾞｳﾝﾛｰﾄﾞ", "ダウンロード"), ("ﾊﾟｽﾜｰﾄﾞ", "パスワード"), ("ｶﾞｲﾄﾞ", "ガイド")],
)
def test_a_halfwidth_voiced_kana_is_the_same_word_as_its_fullwidth_spelling(
    tmp_path, half, full,
):
    """半角カナの濁点で禁止表現が全経路すり抜けていた。

    まとまりの境界を**正規化前**の `category` で決めていたのが原因。U+FF9E は正規化前
    `Lm` で、`Mn` になるのは NFKC の後。だから基底に合流せず `ﾀﾞ` が合成されなかった。
    policy 冒頭の「全角と半角を同じ参照として読みます」が嘘になっていた。
    """
    assert dc.fold(half) == dc.fold(full)
    constraints = {"version": 1, "tokens": {"color": {"brand": "#0A84FF"}},
                   "prohibited": [{"pattern": full, "why": "用語統一"}]}
    code, report = _run(tmp_path, constraints, {"a.md": f"資料を{half}してください\n"})
    assert code == 1
    assert [v["class"] for v in report["violations"]] == ["prohibited-expression"]


@pytest.mark.parametrize(
    "text",
    ["ﾀﾞｳﾝﾛｰﾄﾞ", "ﾊﾟｽ", "ダウンロード", "㈱ﬁ①", "ｶﾞ", "ﾞ", "ﾞﾞ", "áb", "",
     "ダ\u200bウン", "Ａ Ｂ", "ﾞあ",
     # ハングルの jamo は `Lo`＝starter。まとまり単位の正規化では**永久に**合成されず、
     # この行を書いていなかったから「不動点を縛った」と書けてしまった。
     "한글", "베트남", "가", "ㄱㅏ", "Tiếng Việt", "한\n글"],
)
def test_normalisation_has_already_absorbed_nfkc(text):
    """`fold(x) == fold(NFKC(x))`。**この不動点が3周目・4周目の退行を許した穴を塞ぐ。**

    「見えない文字の表をもう1つ広げる」ではなく「正規化が NFKC の不動点である」を
    直接縛る。半角濁点もハングルも、この等式が破れていた形にすぎない。
    """
    assert dc.fold(text) == dc.fold(unicodedata.normalize("NFKC", text))


@pytest.mark.parametrize(
    "text", ["한글", "베트남", "Tiếng Việt", "café", "가나다"],
)
def test_a_decomposed_word_is_the_same_word_as_its_composed_spelling(text):
    """NFD で分解して書いた禁止語が、合成表記の宣言と一致すること。"""
    assert dc.fold(unicodedata.normalize("NFD", text)) == dc.fold(text)


def test_the_invariant_holds_across_the_whole_code_point_space():
    """不動点を**手で選んだ文字列ではなく乱択**で確かめる。

    4周目はこの性質を「縛った」と書きながら、実体は日本語とラテンだけの12文字列で、
    ハングルを1つ足せば崩れた。反例は乱択でしか出てこない——選んだ文字列は、
    自分が直したケースの写しにしかならない。
    """
    rnd = random.Random(20260907)
    for _ in range(6000):
        text = "".join(chr(rnd.randint(1, 0x10FFFF)) for _ in range(rnd.randint(1, 6)))
        assert dc.fold(text) == dc.fold(unicodedata.normalize("NFKC", text)), repr(text)


@pytest.mark.parametrize(
    "written",
    ["ﾀ\u200bﾞｳﾝﾛｰﾄ\u200bﾞ", "ﾀ\u00adﾞｳﾝﾛｰﾄﾞ", "ダ\u200b\u3099ウンロード"[:1] + "ダウンロード"[1:]],
)
def test_an_invisible_character_between_a_base_and_its_mark_does_not_block_composition(
    tmp_path, written,
):
    """**捨ててから合成する。** 逆順だと表示の変わらない1文字で合成が壊れる。

    不可視文字は表の中にあり実際に捨てられていたのに、捨てる前に合成の境界を決めて
    いたため `ﾀ<ZWSP>ﾞ` が `ダ` にならず、禁止表現が exit 0 で素通りしていた。
    表の有限性の問題ではなく、**順序**の問題。
    """
    constraints = {"version": 1, "tokens": {"color": {"brand": "#0A84FF"}},
                   "prohibited": [{"pattern": "ダウンロード", "why": "「取得」と書く"}]}
    code, report = _run(tmp_path, constraints, {"a.md": f"資料を{written}してください\n"})
    assert code == 1, report["violations"]
    assert [v["class"] for v in report["violations"]] == ["prohibited-expression"]


def test_normalising_line_by_line_is_normalising_the_whole_text():
    """行ごとの NFKC ＝ 本文全体の NFKC。行番号を残すための分割が意味を変えないこと。

    この等式が正規化を「表に依存しない」ものにしている。改行は合成にも分解にも
    関与しないので成り立つが、成り立たなくなれば行分割そのものが誤りになる。
    """
    rnd = random.Random(20260907)
    texts = ["한글\n가나", unicodedata.normalize("NFD", "Tiếng Việt") + "\nﾀﾞ",
             "a\nﾞ", "㈱\nﬁ", "café\n" + unicodedata.normalize("NFD", "café"),
             "\n\n", "ｶﾞ\nｶﾞ", ""]
    texts += ["".join(chr(rnd.randint(1, 0xFFFF)) for _ in range(40)) for _ in range(500)]
    for t in texts:
        whole = unicodedata.normalize("NFKC", t)
        by_line = "\n".join(unicodedata.normalize("NFKC", ln) for ln in t.split("\n"))
        assert whole == by_line, repr(t[:40])


@pytest.mark.parametrize(
    "value",
    ["2px solid red", "red 2px solid", "red solid 2px",
     "solid red 2px", "2px red solid", "solid 2px red"],
)
def test_a_border_shorthand_declares_its_colour_in_any_order(value):
    """CSS の枠線ショートハンドは**順不同**。末尾だけを見て4通りで色を落としていた。

    落ちる方向が悪い——宣言した色が宣言集合から消え、それを使った成果物が違反になる。
    """
    colors, lengths, _ = dc.classify_declared(value)
    assert colors == ["#ff0000"] and lengths == ["2px"]


@pytest.mark.parametrize(
    "value",
    ["16px Display Black Regular", "700 16px Display Black Regular",
     "1px double Midnight Blue Text"],
)
def test_a_long_value_does_not_declare_a_colour_from_a_typeface_name(value):
    """語数の上限が無いと、書体名に混ざった色語が色として宣言される。

    宣言された `#000000` は**その色のあらゆる使用**を黙って通す。書体名の過剰宣言と
    違って波及範囲が広いので、色だけは枠線ショートハンドの形に閉じ込める。
    語数上限を外す変異が 168/168 緑で通ったため、この形を直接張る。
    """
    colors, _, _ = dc.classify_declared(value)
    assert colors == []


@pytest.mark.parametrize(
    "value",
    ["16px/1.2 Black", "16px Black, sans-serif", "1.5rem/2 Navy", "12px Silver, serif"],
)
def test_a_font_shorthand_marker_keeps_a_typeface_name_from_becoming_a_colour(value):
    """`/`（サイズ／行送り）と `,`（フォールバック）は書体指定の目印。

    語数だけで閉じ込めると `16px/1.2 Black` の `Black` が色になる。この2つの条件を
    外す変異が 171/171 緑で通ったため、語数では除外されない形を直接張る。
    """
    colors, _, _ = dc.classify_declared(value)
    assert colors == []


@pytest.mark.parametrize(
    "keyword",
    ["solid", "dashed", "dotted", "double", "groove", "ridge", "inset", "outset", "hidden"],
)
def test_a_typeface_whose_name_contains_a_border_keyword_is_still_declared(
    tmp_path, keyword,
):
    """`Solid Grotesk` は実在の書体で、線種キーワード表はそれを宣言から落とした。

    9語すべてを張るのは、表を `{"solid"}` に縮める変異が **123/123 緑**で通ったから。
    守っていない不変条件は、次の修正が黙って壊す。
    """
    face = f"{keyword.capitalize()} Grotesk"
    assert dc.classify_declared(face) == ([], [], face.lower())
    constraints = {"version": 1, "tokens": {"font": {"display": face}}}
    code, report = _run(tmp_path, constraints, {"a.css": f".t {{ font-family: {face}; }}\n"})
    assert code == 0, report["violations"]


@pytest.mark.parametrize(
    "keyword",
    ["solid", "dashed", "dotted", "double", "groove", "ridge", "inset", "outset", "hidden"],
)
def test_a_border_shorthand_still_declares_its_colour_and_its_length(keyword):
    """書体側に寄せても、枠線ショートハンドの色と長さは落とさない。"""
    colors, lengths, _ = dc.classify_declared(f"2px {keyword} red")
    assert colors == ["#ff0000"] and lengths == ["2px"]


def test_a_font_shorthand_does_not_declare_a_colour_that_is_part_of_a_typeface_name():
    """`Black Han Sans` の `black` を色として宣言すると、`#000000` が黙って通る。"""
    colors, _, family = dc.classify_declared("700 24px/1.2 Black Han Sans, sans-serif")
    assert colors == [] and family == "black han sans"


@pytest.mark.parametrize(
    "extra,expected",
    [({"components": [], "prohibited": []}, []),
     ({"components": []}, ["prohibited"]),
     ({"prohibited": []}, ["components"]),
     ({}, ["components", "prohibited"])],
)
def test_an_empty_section_is_declared_and_an_absent_one_is_not(tmp_path, extra, expected):
    """`[]` は「1つも許可しない／禁止しない」という宣言。省略だけが未宣言。

    `components` は `is not None`、`prohibited` は `bool()` で読んでいたため、
    同じ `[]` が別の意味になり、報告の `not_declared` が嘘をついていた。
    """
    constraints = {"version": 1, "tokens": {"color": {"brand": "#0A84FF"}}, **extra}
    _, report = _run(tmp_path, constraints, {"a.md": "x\n"})
    assert report["not_declared"] == expected
