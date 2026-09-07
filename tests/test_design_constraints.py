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
import subprocess
import sys
import time

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
