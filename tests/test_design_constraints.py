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
import pathlib
import subprocess
import sys

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
