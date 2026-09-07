"""What the design-constraints sensor actually catches, measured against a blind corpus.

`tests/fixtures/design-constraints/` was written by an author who had the policy,
the schema, and the template — and not the sensor, which did not exist yet. The
answer key is therefore an expectation derived from the spec, not a transcript of
what the implementation happens to do. That separation is the whole point: a
measuring instrument scored against a key its own author wrote measures nothing.

The numbers below are pinned deliberately. If a change moves one, that is not a
broken test — it is the shipped ratio going stale. Re-measure, update the number
here **and** in `policies/design-constraint-rules`, and say which way it moved.
"""

import json
import pathlib

import pytest

from rig_workbench import design_constraints as dc

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "design-constraints"
POLICY = (
    pathlib.Path(__file__).resolve().parent.parent
    / "skills" / "engine" / "facets" / "policies" / "design-constraint-rules.md"
)

# 2026-09-07 の実測（4-way レビュー後の再測）。seed 11/12・attack 12/14。
# 採点は行とクラスの完全一致のみ——値の部分一致による予備判定は置かない。
# 取りこぼしは3件で、いずれも
# 「ソースに無く描画結果にだけ現れる」か「値としての形を持たない」もの:
#   typography-spec.md  書体名を CSS 宣言の外（使用トークン表）に置いた
#   theme-accent.jsx    16進色を文字列連結で分割した
#   upload-status.jsx   禁止表現を JSX のテキストノードと式に割った
EXPECTED_HITS = {"seed": 11, "attack": 12}
EXPECTED_TOTAL = {"seed": 12, "attack": 14}
KNOWN_MISSES = {
    ("artifacts/typography-spec.md", 12),
    ("artifacts/theme-accent.jsx", 3),
    ("artifacts/upload-status.jsx", 9),
}


@pytest.fixture(scope="module")
def key() -> dict:
    return json.loads((FIXTURES / "answer_key.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def declared() -> dc.Declared:
    return dc.Declared(dc.load_constraints(str(FIXTURES / "constraints.json")))


def _scan(entry: dict, declared: dc.Declared) -> list[dict]:
    return dc.scan_file(str(FIXTURES / entry["path"]), declared)


def _hit(expected: dict, found: list[dict]) -> bool:
    """行とクラスが一致したときだけ hit とする。

    値の部分一致を予備の判定にしていたが、実測ではその枝が一度も発火しないうえ、
    `_line_of` を定数に潰しても禁止表現の大半が hit のまま残った——**行番号の主張が
    守りになっていなかった**。予備を外すと、行がずれた瞬間に落ちる。
    """
    return any(
        v["line"] == expected["line"] and v["class"] == expected["class"] for v in found
    )


def test_the_corpus_and_its_answer_key_describe_the_same_files(key):
    on_disk = {f"artifacts/{p.name}" for p in (FIXTURES / "artifacts").iterdir()}
    assert {a["path"] for a in key["artifacts"]} == on_disk


def test_the_answer_key_points_at_lines_that_exist(key):
    for a in key["artifacts"]:
        text = (FIXTURES / a["path"]).read_text(encoding="utf-8").split("\n")
        for v in a["expected_violations"]:
            start, end = v["line"], v.get("line_end", v["line"])
            needle = v.get("line_fragment", v["token_or_value"])
            assert needle in "\n".join(text[start - 1:end]), (a["path"], start, needle)


def test_an_honest_artefact_produces_nothing(key, declared):
    """偽陽性 0。ここが緑でなくなると、正直に書いた人が罰される。"""
    noise = {
        a["path"]: _scan(a, declared) for a in key["artifacts"] if a["kind"] == "clean"
    }
    assert {p: v for p, v in noise.items() if v} == {}


def test_untokenized_prose_stays_outside_the_detection_class(key, declared):
    """ポリシーが「捕れない」と宣言した唯一のクラス。

    ここが赤になったら、センサーが黙って能力を増やしたか偶然拾ったかで、
    どちらでも出荷している比率が嘘になる。
    """
    leaked = {
        a["path"]: _scan(a, declared) for a in key["artifacts"] if a["kind"] == "class-4"
    }
    assert {p: v for p, v in leaked.items() if v} == {}


@pytest.mark.parametrize("kind", ["seed", "attack"])
def test_the_measured_detection_rate(key, declared, kind):
    hits = 0
    total = 0
    missed = set()
    for a in key["artifacts"]:
        if a["kind"] != kind:
            continue
        found = _scan(a, declared)
        for expected in a["expected_violations"]:
            total += 1
            if _hit(expected, found):
                hits += 1
            else:
                missed.add((a["path"], expected["line"]))
    assert total == EXPECTED_TOTAL[kind]
    assert hits == EXPECTED_HITS[kind]
    assert missed <= KNOWN_MISSES, f"新しい取りこぼし: {missed - KNOWN_MISSES}"


def test_the_shipped_ratio_is_written_where_a_reader_will_find_it():
    """比率をテストにだけ書いて policy に書かないと、読む人には届かない。"""
    text = POLICY.read_text(encoding="utf-8")
    assert f"{EXPECTED_HITS['seed'] + EXPECTED_HITS['attack']}/" \
           f"{EXPECTED_TOTAL['seed'] + EXPECTED_TOTAL['attack']}" in text


@pytest.mark.parametrize(
    "name,expected",
    [("constraints.placeholder.json", "unchecked"),
     ("constraints.broken.json", "unchecked"),
     ("constraints.schema-invalid.json", "unchecked"),
     # 親が追加。綴り間違いで規則が丸ごと消え、checked / 違反 0 件で緑になっていた。
     ("constraints.typo-key.json", "unchecked")],
)
def test_a_declaration_that_cannot_be_read_is_never_a_pass(tmp_path, name, expected):
    art = tmp_path / "art"
    art.mkdir()
    (art / "a.md").write_text("色は #FF0000。\n", encoding="utf-8")
    report = tmp_path / "r.json"
    code = dc.main(["--if-configured", "--constraints", str(FIXTURES / "unchecked" / name),
                    "--report", str(report), str(art)])
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == expected and code == 2
    assert payload["violations"] == []
