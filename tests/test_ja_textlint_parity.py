"""How far the stdlib sensor agrees with the real textlint-ja, on the same corpus.

`tests/fixtures/ja-textlint/upstream-textlint.json` is what textlint 15.8.0 with
preset-ja-technical-writing, preset-ja-spacing, the three ja-hiragana rules and prh
reported on the fixture corpus (versions inside the file). The sensor cannot run
textlint — that is the point of it — so the snapshot is the spec it is measured
against, rule by rule, line-exact.

Three numbers are pinned for the rules both sides ship: what upstream reported, what
the sensor reported, and the overlap. The gaps are not noise; each direction is named
in `policies/japanese-textlint-rules` as a deliberate departure or a known limit
(majority-based 敬体/常体 instead of a fixed preference, superset dictionaries,
`auto` spacing). A change that moves a number is a change to that contract: re-measure,
and update the policy and this file together.
"""

import collections
import json
import pathlib

import pytest

from rig_workbench import ja_textlint as jt

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "ja-textlint"

# 2026-09-09: 30 shared rules on 31 files. Upstream reported 77 findings on those rules,
# the sensor 85, and 57 are the same (file, line). Recall against upstream 0.74,
# precision against upstream 0.67 — the 28 sensor-only findings are all in the
# superset rules listed in PER_RULE below, none in a rule that claims exactness.
EXPECTED_SHARED = {"upstream": 77, "mine": 85, "both": 57}

# (upstream, mine, both) per shared rule. A rule with both == upstream == mine agrees exactly.
PER_RULE = {
    "ja-hiragana-fukushi": (3, 3, 3),
    "ja-hiragana-hojodoushi": (2, 5, 2),        # 出来る・ご連絡頂き は本家に無い（意図した上位集合）
    "ja-hiragana-keishikimeishi": (4, 4, 4),
    "ja-no-abusage": (2, 4, 2),                 # 汚名挽回・的を得る は本家辞書に無い
    "ja-no-mixed-period": (3, 2, 2),            # 「:」で終わる段落を本家は報告し、ここでは免除
    "ja-no-redundant-expression": (2, 5, 2),    # まず最初に・各〜ごと・約〜ほど は本家辞書に無い
    "ja-no-space-around-parentheses": (1, 1, 1),
    "ja-no-space-around-slash": (1, 1, 1),
    "ja-no-space-between-full-width": (2, 2, 2),
    "ja-no-successive-word": (3, 2, 2),         # 一つ一つ を本家は報告し、ここでは畳語として許す
    "ja-no-weak-phrase": (2, 3, 2),             # 気がします は本家辞書に無い
    "ja-space-around-code": (8, 1, 1),          # 本家は never 固定、ここは auto で少数派だけ
    "ja-space-between-half-and-full-width": (9, 2, 2),  # 同上
    "ja-unnatural-alphabet": (1, 1, 0),         # 本家は kuromoji の未知語で、ここは小文字一字。位置が違う
    "max-kanji-continuous-len": (2, 2, 2),
    "max-ten": (3, 3, 3),
    "no-double-negative-ja": (3, 5, 3),         # ないわけではありません・なくはない は本家の活用に無い
    "no-doubled-conjunction": (2, 2, 2),
    "no-doubled-conjunctive-particle-ga": (1, 1, 1),
    "no-doubled-joshi": (4, 8, 4),              # 本家が読まない見出し・clean 文書の「が…が」を含む
    "no-dropping-the-ra": (3, 3, 3),
    "no-exclamation-question-mark": (2, 2, 2),
    "no-hankaku-kana": (1, 1, 1),
    "no-invalid-control-character": (1, 1, 1),
    "no-mix-dearu-desumasu": (4, 13, 1),        # 本家は本文=ですます・箇条書き=である の固定、ここは多数派
    "no-nfd": (1, 1, 1),
    "no-unmatched-pair": (2, 2, 2),
    "no-zero-width-spaces": (1, 1, 1),
    "prh": (2, 2, 2),
    "sentence-length": (2, 2, 2),
}
UPSTREAM_ONLY_RULES = {"arabic-kanji-numbers"}   # 漢数字/算用数字。捕れないと policy に書いてある
SENSOR_ONLY_RULES = {                            # 本家に無い、または本家がこのコーパスで沈黙した規則
    "ja-nakaguro-or-halfwidth-space-between-katakana", "ja-no-orthographic-variants",
    "no-dropped-i", "no-zenkaku-alnum",
}


@pytest.fixture(scope="module")
def tallies():
    snap = json.loads((FIXTURES / "upstream-textlint.json").read_text(encoding="utf-8"))
    data, _ = jt.load_config(str(FIXTURES / "config.json"), True)
    settings = jt.Settings(data)
    up: dict[str, set] = collections.defaultdict(set)
    mine: dict[str, set] = collections.defaultdict(set)
    for rel, msgs in snap["files"].items():
        for m in msgs:
            up[m["rule"]].add((rel, m["line"]))
        for f in jt.lint_text(jt.read_source(str(FIXTURES / rel)), settings, rel):
            mine[f["rule"]].add((rel, f["line"]))
    return up, mine


def test_the_snapshot_names_its_versions():
    snap = json.loads((FIXTURES / "upstream-textlint.json").read_text(encoding="utf-8"))
    assert snap["versions"]["textlint"] and snap["versions"]["textlint-rule-preset-ja-technical-writing"]
    assert len(snap["files"]) == 31


def test_per_rule_agreement_is_pinned(tallies):
    up, mine = tallies
    got = {r: (len(up[r]), len(mine[r]), len(up[r] & mine[r]))
           for r in set(up) | set(mine) if r in jt.RULES and up[r]}
    assert got == PER_RULE, {r: (got.get(r), PER_RULE.get(r)) for r in set(got) | set(PER_RULE) if got.get(r) != PER_RULE.get(r)}


def test_shared_totals_are_pinned(tallies):
    up, mine = tallies
    shared = [r for r in PER_RULE]
    totals = {
        "upstream": sum(len(up[r]) for r in shared),
        "mine": sum(len(mine[r]) for r in shared),
        "both": sum(len(up[r] & mine[r]) for r in shared),
    }
    assert totals == EXPECTED_SHARED


def test_rules_only_one_side_ships_are_the_declared_ones(tallies):
    up, mine = tallies
    assert {r for r in up if r not in jt.RULES} == UPSTREAM_ONLY_RULES
    assert {r for r in mine if r in jt.RULES and not up[r]} == SENSOR_ONLY_RULES


def test_exact_rules_agree_exactly(tallies):
    """Rules the policy calls 'decided by characters' must match upstream line for line."""
    up, mine = tallies
    for r in ("no-hankaku-kana", "no-nfd", "no-zero-width-spaces", "no-invalid-control-character",
              "no-exclamation-question-mark", "no-unmatched-pair", "prh", "sentence-length",
              "max-ten", "max-kanji-continuous-len", "no-doubled-conjunction",
              "no-doubled-conjunctive-particle-ga", "no-dropping-the-ra"):
        assert up[r] == mine[r], (r, up[r] ^ mine[r])
