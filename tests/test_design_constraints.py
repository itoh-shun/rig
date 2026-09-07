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
    # **変異した定数自身と比べない。** `REGEX_TIME_BUDGET = 600.0` の変異が
    # 601 秒かけて緑のまま通っていた（打ち切りの絶対値を張っていなかった）。
    assert elapsed < 20.0, f"打ち切れていない ({elapsed:.1f}s)"


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


def test_a_font_shorthand_with_a_size_still_declares_its_typeface(tmp_path):
    """書体は落とさない。`700 24px/1.2 Black Han Sans` は長さも書体も宣言する。

    以前はここで「色ではない」ことも主張していたが、その判別（線種キーワード表）は
    実在書体 `Solid Grotesk` に破れた。いまは色も宣言し、報告に内訳が載る。
    """
    colors, lengths, font = dc.classify_declared("700 24px/1.2 Black Han Sans, sans-serif")
    assert font == "black han sans"
    assert lengths == ["24px"] and "#000000" in colors


@pytest.mark.parametrize(
    "value,expected",
    # `2px solid red` は書体としても登録される。プロパティ名の無い値で枠線と書体を
    # 語彙で分けようとしたら実在書体 `Solid Grotesk` が宣言から丸ごと落ちたので、
    # **曖昧なら書体側にも入れる**ことにした。書体名の過剰宣言はその名前1つにしか
    # 波及しないが、過少宣言は宣言した値そのものを違反として上げる。色についても
    # 同じ理由で推測をやめ、過剰宣言は報告の `composite_declarations` で可視にする。
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
     "İ x", "İİİ", "ǅ", "ﬄ",
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
    "value,colour",
    [("16px Display Black Regular", "#000000"), ("16px/1.2 Black", "#000000"),
     # `Midnight Blue` は2語なので `blue` として読まれる（`midnightblue` ではない）。
     ("1px double Midnight Blue Text", "#0000ff"), ("12px Silver, serif", "#c0c0c0"),
     ("0 1px 2px black", "#000000"), ("inset 0 1px 0 white", "#ffffff"),
     ("0 0 4px navy", "#000080"), ("1.5rem/2 Navy", "#000080")],
)
def test_a_value_containing_a_length_declares_every_colour_word_in_it(value, colour):
    """**方向が反転しています。** 以前は書体名に混ざった色語を色にしないよう絞っていた。

    裸の値から CSS プロパティを推測する判別器は3代続けて実値に破れた——線種キーワード
    表は `Solid Grotesk` に、末尾語は `red 2px solid` に、語数は `0 1px 2px black`
    （`box-shadow` の宣言）に。次は `inset 0 1px 0 white` で破れる。**推測をやめる。**

    代償は `16px Display Black` が `#000000` を宣言すること。過剰宣言はその色の
    あらゆる使用を通すので危険だが、**黙って**起きなければレビュアが裁ける——報告の
    `composite_declarations` にどのトークンが何を宣言したかが載る。過少宣言（宣言した
    影が違反として上がる）には同等の救済が無い。
    """
    colors, _, _ = dc.classify_declared(value)
    assert colour in colors


def test_a_value_without_a_length_is_still_only_a_typeface():
    """長さが無ければ色語は読まない。`Solid Grotesk` も `Black Han Sans` も書体。"""
    for face in ("Solid Grotesk", "Black Han Sans", "Display Black"):
        colors, lengths, family = dc.classify_declared(face)
        assert colors == [] and lengths == [] and family == face.lower()


def test_a_declared_shadow_is_not_reported_as_a_raw_value(tmp_path):
    """`box-shadow: 0 1px 2px black` を宣言して逐語で使うと違反になっていた。

    語数上限3で切っていたため4語の影の宣言だけが色を落とした。成果物側は `shadow` を
    色プロパティとして読むので、**宣言した値そのもの**が違反として上がる。
    """
    constraints = {"version": 1, "tokens": {
        "color": {"brand": "#1F6FEB"}, "elevation": {"card": "0 1px 2px black"},
        "spacing": {"sm": "8px"}}}
    code, report = _run(tmp_path, constraints,
                        {"card.css": ".card {\n  box-shadow: 0 1px 2px black;\n}\n"})
    assert code == 0, report["violations"]


def test_the_report_says_which_token_declared_which_colour(tmp_path):
    """過剰宣言を**黙って**起こさないための記録。レビュアはこれを読んで裁く。"""
    constraints = {"version": 1, "tokens": {
        "color": {"brand": "#0A84FF"}, "font": {"display": "16px Display Black"}}}
    _, report = _run(tmp_path, constraints, {"a.md": "x\n"})
    entry = [c for c in report["composite_declarations"] if c["token"] == "font.display"]
    assert entry and "#000000" in entry[0]["declared"]["colors"]
    assert entry[0]["declared"]["font"] == "display black"


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


def test_a_font_shorthand_still_declares_its_typeface(tmp_path):
    """書体は落とさない。色も宣言するが、それは報告に載って可視になる。"""
    value = "700 24px/1.2 Black Han Sans, sans-serif"
    colors, _, family = dc.classify_declared(value)
    assert family == "black han sans" and "#000000" in colors
    _, report = _run(tmp_path, {"version": 1, "tokens": {"font": {"h1": value}}},
                     {"a.md": "x\n"})
    assert [c["token"] for c in report["composite_declarations"]] == ["font.h1"]


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


# ── 6周目の検証で崩れた点 ────────────────────────────────────────────────
def test_a_regex_rule_survives_a_line_wrap_like_a_literal_one(tmp_path):
    """`regex: true` のときだけ折り返しで検出が消えていた。

    正規表現には空白を残した本文しか渡しておらず、リテラルが持つ「空白を落とした
    第2パス」が無かった。policy「禁止表現は本文全体に対して照合します（行単位では
    ありません）」が、**指定方法によって成り立ったり成り立たなかったり**していた。
    """
    constraints = {"version": 1, "tokens": {"color": {"brand": "#0A84FF"}}, "prohibited": [
        {"pattern": "ボタンを(押|クリック)してください", "why": "操作を指示しない", "regex": True},
        {"pattern": "エラーが発生しました", "why": "定型文"}]}
    body = "続けるにはボタンを押\nしてくださいと表示する。\nここでエラーが発生\nしましたと出る。\n"
    code, report = _run(tmp_path, constraints, {"copy.md": body})
    assert code == 1
    assert len(report["violations"]) == 2, report["violations"]


def test_a_file_too_large_for_the_regex_budget_is_unchecked(tmp_path, monkeypatch):
    """上限を超えたら合格ではなく未検査。

    上限そのものを大きくする変異は、実ファイルで張ると**失敗ではなく低速化**になり、
    打ち切りの絶対値を張らなかったときと同じ見落としになる。上限は monkeypatch で
    小さくして**挙動**を張り、定数の桁は別に張る。
    """
    monkeypatch.setattr(dc, "REGEX_MAX_CHARS", 100)
    constraints = {"version": 1, "tokens": {"color": {"brand": "#0A84FF"}},
                   "prohibited": [{"pattern": "だめ", "why": "x", "regex": True}]}
    code, report = _run(tmp_path, constraints, {"big.md": "あ" * 200})
    assert code == 2 and report["status"] == "unchecked"


def test_the_regex_limits_are_small_enough_to_be_limits():
    """定数の桁を直接張る。10億文字・600秒は上限ではない。"""
    assert dc.REGEX_MAX_CHARS <= 1_000_000
    assert dc.REGEX_TIME_BUDGET <= 30.0


def test_the_position_map_points_at_the_line_each_character_came_from():
    """位置対応を**直接**張る。改行の対応先を1つずらす変異が 191/191 緑で通った。

    折り返しの筋書きで張ろうとしたが、一致が改行そのものから始まらない限り差が出ない
    ——**到達しにくい経路を筋書きで張ろうとすると、張れていないことに気づけない。**
    """
    text = "あ\nい\nう"
    out, idx = dc._flatten(text, drop_all_space=False)
    assert len(out) == len(idx)
    # 各出力文字の対応先は、その文字が来た行の先頭（改行は直前の行に属する）。
    starts = [0, 2, 4]
    assert [dc._line_of(i, starts) for i in idx] == [1, 1, 2, 2, 3]


def test_a_character_that_lowercases_to_two_characters_does_not_derail_the_scan(tmp_path):
    """`"İ".lower()` は2文字。1文字追加を前提にすると出力と位置対応がずれる。

    ずれた結果は IndexError で未検査になる——黙って通る経路ではないが、`İ` を書いた
    だけで検査そのものが消える。
    """
    out, idx = dc._flatten("İ x", False)
    assert len(out) == len(idx)
    constraints = {"version": 1, "tokens": {"color": {"brand": "#0A84FF"}},
                   "prohibited": [{"pattern": "だめ", "why": "x"}]}
    code, report = _run(tmp_path, constraints, {"a.md": "İ" * 40 + "\nだめです\n"})
    assert code == 1, report
    assert report["violations"][0]["line"] == 2


def test_a_prohibited_phrase_split_across_a_wrap_reports_the_line_it_starts_on(tmp_path):
    """折り返しで割れた禁止表現の行番号。改行の位置対応を潰す変異が無防備だった。"""
    constraints = {"version": 1, "tokens": {"color": {"brand": "#0A84FF"}},
                   "prohibited": [{"pattern": "こちらをクリック", "why": "操作を指示しない"}]}
    body = "案内文です。\n続けるにはこちらを\nクリックしてください。\n"
    code, report = _run(tmp_path, constraints, {"copy.md": body})
    assert code == 1
    assert report["violations"][0]["line"] == 2, report["violations"]


# ── 7周目の検証で崩れた点（いずれも成果物側の抽出器・最初から在った）──────────
def test_a_jsx_style_object_declares_every_property_not_just_the_first(tmp_path):
    """カンマ区切りの2つ目以降が1つ目の値に飲まれ、**書き方だけで検出が消えていた**。

    1行1プロパティに割ると検出されるのに、1行にまとめると違反0件。ポリシーが最も
    強く否定している形（書き方で検出力が変わる）。
    """
    body = ('export const Card = () => (\n'
            '  <div style={{ fontFamily: "Inter", color: "crimson", borderColor: "teal" }}>\n'
            '    hello\n  </div>\n);\n')
    constraints = {"version": 1, "tokens": {
        "color": {"brand": "#0A84FF"}, "font": {"body": "Inter"}}}
    code, report = _run(tmp_path, constraints, {"Card.jsx": body})
    assert code == 1
    assert sorted(v["value"] for v in report["violations"]) == ["#008080", "#dc143c"]


@pytest.mark.parametrize(
    "value,keeps",
    [("color: rgba(0, 0, 0, 0.5)", "rgba(0, 0, 0, 0.5)"),
     ("font-family: Inter, sans-serif", "Inter, sans-serif"),
     ("box-shadow: 0 1px 2px black, inset 0 0 1px white",
      "0 1px 2px black, inset 0 0 1px white"),
     ("transition: color 1s, background 2s", "color 1s, background 2s")],
)
def test_a_comma_inside_a_value_is_not_a_declaration_boundary(value, keeps):
    """`,` は値の一部のことも次の宣言の区切りのこともある。後ろに `名前:` が続く
    ときだけ切る——切りすぎると `rgba(0, 0, 0, .5)` の色が読めなくなる。"""
    assert dc.RE_DECL.findall(value)[0][1] == keeps


@pytest.mark.parametrize(
    "declared,written",
    [("Georgia", "font: 12px/30px Georgia, serif;"),
     ("Georgia", "font: 12px/1.5rem Georgia;"),
     ("Inter", "font: 16px/1.5 Inter, sans-serif;"),
     ("Black Han Sans", "font: 700 24px/1.2 Black Han Sans, sans-serif;")],
)
def test_a_line_height_with_a_unit_does_not_eat_into_the_typeface(
    tmp_path, declared, written,
):
    """行送りが単位の手前で切れ、書体が `px georgia` になっていた。

    **宣言した書体を正しく使った成果物が違反になる**方向。既存テストが `16px/1.5`
    （単位なし）だけだったので偶然通っていた。
    """
    constraints = {"version": 1, "tokens": {
        "font": {"body": declared}, "space": {"a": "12px", "b": "30px", "c": "1.5rem",
                                              "d": "16px", "e": "24px"}}}
    code, report = _run(tmp_path, constraints, {"card.css": f".c {{ {written} }}\n"})
    assert code == 0, report["violations"]


@pytest.mark.parametrize(
    "written,expected",
    [("padding: .5rem;", ["0.5rem"]), ("margin: -2px;", ["-2px"]),
     ("padding: 0.5rem;", ["0.5rem"]), ("width: 1.5rem;", ["1.5rem"]),
     ("m: -0.5rem;", ["-0.5rem"]), ("a: var(--gap-2px);", []), ("c: #0a84ff;", []),
     # 識別子の中の小数から幻の長さを拾わない（lookbehind の `.` が支えている）。
     ("x: a1.5rem;", []), ("y: v1.2px;", []), ("z: $2px;", [])],
)
def test_a_length_is_read_however_its_number_is_written(written, expected):
    """`.5rem` と `-2px` が読めないと、書き方を変えるだけで長さの検査が消える。

    `1.5rem` の中の `5rem`、`--gap-2px` の中の `2px`、16進の中の数字は読まない。
    """
    assert dc.lengths_in(written) == expected


def test_a_leading_dot_length_matches_a_declaration_written_with_a_zero(tmp_path):
    """`.5rem` は `0.5rem` の宣言に一致する（数として畳んでから突き合わせる）。"""
    constraints = {"version": 1, "tokens": {"spacing": {"sm": "0.5rem"}}}
    code, report = _run(tmp_path, constraints, {"a.css": ".c { padding: .5rem; }\n"})
    assert code == 0, report["violations"]


# ── 8周目の検証で崩れた点 ────────────────────────────────────────────────
@pytest.mark.parametrize(
    "name,body",
    [("quoted.jsx",
      '<div style={{ "fontFamily": "Papyrus", "color": "crimson" }} />\n'),
     ("single.jsx",
      "<div style={{ 'fontFamily': 'Papyrus', 'color': 'crimson' }} />\n"),
     ("theme.json",
      '{"color": "crimson", "fontFamily": "Papyrus"}\n'),
     ("plain.jsx",
      '<div style={{ fontFamily: "Papyrus", color: "crimson" }} />\n')],
)
def test_a_quoted_property_name_is_still_a_declaration(tmp_path, name, body):
    """**JSON のキーは常に引用符付き**なので、囲みを読まないと `.json` 成果物が
    名前付き色・3桁16進・書体に対して丸ごと盲目になっていた。

    JSX の style も、引用符を付けるだけで同じ穴が開いた——7周目に直した穴が
    書き方ひとつで戻る形。囲みの有無で答えが変わってはならない。
    """
    constraints = {"version": 1, "tokens": {
        "color": {"brand": "#0A84FF"}, "font": {"body": "Inter"}}}
    code, report = _run(tmp_path, constraints, {name: body})
    assert code == 1, report
    found = {(v["class"], v["value"]) for v in report["violations"]}
    assert ("raw-value", "#dc143c") in found
    assert ("raw-value", "papyrus") in found


@pytest.mark.parametrize(
    "written",
    ["font: 12px/150% Georgia, serif;", "font: 12px/30px Georgia;",
     "font: 12px/1.5rem Georgia;", "font: 12px/2em Georgia;"],
)
def test_every_line_height_unit_is_eaten_by_the_size_slot(written):
    """行送りの単位から `%` を落とす変異が 224/224 緑で通った。

    実装は正しかったがテストが `%` を張っていなかった——変異体では書体が
    `% georgia` になる。単位ごとに張る。
    """
    assert dc.fonts_in(written) == ["georgia"]


def test_a_regex_that_matches_at_the_very_end_reports_instead_of_crashing(tmp_path):
    """末尾のゼロ幅一致が本文長と同じ位置を返し、IndexError で未検査になっていた。

    検出できているのに「想定外の例外」で終わるのは、最も紛らわしい失敗の形。
    """
    constraints = {"version": 1, "tokens": {"color": {"brand": "#0A84FF"}},
                   "prohibited": [{"pattern": "[ ]*$", "why": "行末の空白", "regex": True}]}
    code, report = _run(tmp_path, constraints, {"a.md": "本文です\n"})
    assert report["status"] == "checked", report["reason"]
    assert code == 1 and report["violations"][0]["class"] == "prohibited-expression"


def test_a_data_uri_truncates_the_declaration_as_documented(tmp_path):
    """既知の取りこぼし。`;` で値が切れるので、データ URI の後ろの色は読めない。

    ポリシーに書いてある挙動を張る——**書いてある取りこぼしと、気づいていない
    取りこぼしは違う。** 後者に変わったらこのテストが落ちる。
    """
    constraints = {"version": 1, "tokens": {"color": {"brand": "#0A84FF"}}}
    body = ('.a { background: url("data:image/svg+xml;base64,AAA=") crimson; }\n'
            '.b { background: url("data:image/svg+xml,AAA") crimson; }\n')
    code, report = _run(tmp_path, constraints, {"u.css": body})
    assert code == 1
    assert [v["line"] for v in report["violations"]] == [2]
