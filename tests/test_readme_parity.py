"""README.ja.md keeps the §11 and §14 subsections README.md has (#582).

The English README is the one that grows; the Japanese one is where a reader who follows
`README.md`'s pointer to it learns which features exist. These subsections went missing from
it wholesale, so this pins their headings — in both files, and in the order English has them
— rather than comparing every heading, which the two READMEs legitimately do not share.
"""

import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: (English heading, Japanese heading). Each pair names the same subsection.
PAIRED_HEADINGS = [
    ("### Flow visibility", "### Flow visibility"),
    ("### `queue go`'s completion summary", "### `queue go` の完了サマリ"),
    ("### Context metering (`/rig:go context`)", "### Context metering（`/rig:go context`）"),
    ("### CLI session reuse (`--reuse-session`, #326, opt-in)",
     "### CLI セッション再利用（`--reuse-session`・#326・opt-in）"),
]


def _lines(name):
    return (REPO_ROOT / name).read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize("english,japanese", PAIRED_HEADINGS)
def test_each_subsection_heading_is_present_exactly_once_in_both_readmes(english, japanese):
    assert _lines("README.md").count(english) == 1, f"README.md: {english!r}"
    assert _lines("README.ja.md").count(japanese) == 1, f"README.ja.md: {japanese!r}"


def test_the_subsections_appear_in_the_same_order_in_both_readmes():
    def order(name, headings):
        lines = _lines(name)
        return sorted(headings, key=lines.index)

    english = [e for e, _ in PAIRED_HEADINGS]
    japanese = [j for _, j in PAIRED_HEADINGS]
    by_en = order("README.md", english)
    by_ja = order("README.ja.md", japanese)
    assert [PAIRED_HEADINGS[english.index(h)][1] for h in by_en] == by_ja
